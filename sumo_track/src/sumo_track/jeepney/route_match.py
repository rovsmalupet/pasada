"""Jeepney routes from observed GPS traces, matched to the road network (Phase 3, steps 1-2).

Reads the active jeepney_routes dataset from the data manifest (one GPX track per observed trip
and a CSV of the stops seen on each trip) and turns every trip into a continuous sequence of
network edges the jeepney vehicle class may drive:

  1. GPS fixes closer than STANDSTILL_M to the previous one are dropped (the jeepney standing).
  2. sumolib's mapTrace matches the fixes to edges within route_match_radius_m.
  3. Where consecutive matched edges are not connected, the trace and the network disagree
     (a turn the network lacks, a road it does not have). Gaps up to route_match_max_gap_m are
     bridged with the shortest legal path and LISTED in the report: each is a candidate network
     fix (Phase 1, step 6). A larger gap, or no legal path, stops the build.
  4. The observed stops are placed on the matched route, in trip order.

Writes data/processed/jeepney_routes.matched.json and prints a report.

    python -m sumo_track.jeepney.route_match
"""

import csv
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone

import sumolib
from sumolib import geomhelper
from sumolib.route import mapTrace

from sumo_track.config import REPO_ROOT, Config
from sumo_track.network.build import BUILD_FILE, NET_FILE, OUT_DIR

OUT_FILE = OUT_DIR / "jeepney_routes.matched.json"
GPX_NS = {"g": "http://www.topografix.com/GPX/1/1"}
STANDSTILL_M = 1.0   # data cleaning, not a model value: GPS jitter while the jeepney stands still
STOP_COLUMNS = {"trip", "event", "snapped_latitude", "snapped_longitude", "approx_gps_time_ph",
                "headcount_after", "boarded", "alighted", "notes"}


# ------------------------------------------------------------------------------- input
def read_gpx(path):
    """[(lon, lat, time)] of the single track in a GPX file, in recorded order."""
    points = []
    for p in ET.parse(path).getroot().iterfind(".//g:trk/g:trkseg/g:trkpt", GPX_NS):
        when = p.find("g:time", GPX_NS)
        if when is None:
            raise ValueError(f"{path.name}: a track point has no <time>")
        points.append((float(p.get("lon")), float(p.get("lat")),
                       datetime.fromisoformat(when.text.replace("Z", "+00:00"))))
    if len(points) < 2:
        raise ValueError(f"{path.name}: fewer than 2 track points")
    if any(b[2] < a[2] for a, b in zip(points, points[1:])):
        raise ValueError(f"{path.name}: track point times go backwards")
    return points


def drop_standstill(xy, min_step_m=STANDSTILL_M):
    """Keep a point only if it is at least min_step_m from the last kept one."""
    kept = [xy[0]]
    for p in xy[1:]:
        if math.dist(p, kept[-1]) >= min_step_m:
            kept.append(p)
    return kept


def trip_name(gpx_path) -> str:
    """'Paseo_to_Balibago_2026-10-14_annotated.gpx' -> 'Paseo_to_Balibago'."""
    m = re.match(r"(.+?)_\d{4}-\d{2}-\d{2}", gpx_path.stem)
    if not m:
        raise ValueError(f"{gpx_path.name}: expected <trip>_<YYYY-MM-DD>_... as the file name")
    return m.group(1)


def load_observations(folder):
    """One record per observed trip, with its GPS points and its stops from the CSV."""
    gpx_files = sorted(folder.glob("*.gpx"))
    stop_files = sorted(folder.glob("*stops*.csv"))
    if not gpx_files:
        raise ValueError(f"{folder}: no .gpx files")
    if len(stop_files) != 1:
        raise ValueError(f"{folder}: expected one *stops*.csv, found {len(stop_files)}")
    stops = defaultdict(list)
    with open(stop_files[0], encoding="utf-8-sig", newline="") as f:   # the CSV starts with a BOM
        reader = csv.DictReader(f)
        missing = STOP_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{stop_files[0].name}: missing columns {sorted(missing)}")
        for row in reader:
            stops[row["trip"]].append(row)

    trips = []
    for path in gpx_files:
        name = trip_name(path)
        if name not in stops:
            raise ValueError(f"{path.name}: no stops for trip '{name}' in {stop_files[0].name}")
        trips.append({"trip": name, "gpx": path, "points": read_gpx(path), "stops": stops.pop(name)})
    if stops:
        raise ValueError(f"{stop_files[0].name}: stops for trips with no GPX track: {sorted(stops)}")
    return trips, stop_files[0]


# ---------------------------------------------------------------------------- matching
def connected(a, b, vclass) -> bool:
    """Can a vehicle of vclass drive from the end of edge a straight onto edge b?"""
    return any(c.getFromLane().allows(vclass) and c.getToLane().allows(vclass)
               for c in a.getOutgoing().get(b, []))


def bridge_gaps(net, edges, vclass, max_gap_m):
    """Join every unconnected pair of consecutive edges with the shortest legal path.
    Returns the continuous edge list and one record per bridged gap."""
    route, gaps = [edges[0]], []
    for b in edges[1:]:
        a = route[-1]
        if connected(a, b, vclass):
            route.append(b)
            continue
        end, start = a.getToNode().getCoord(), b.getFromNode().getCoord()
        air = math.dist(end, start)
        # a network without a geo-projection (e.g. a generated test grid) reports x/y instead
        lon, lat = net.convertXY2LonLat(*end) if net.hasGeoProj() else end
        where = f"{a.getID()} -> {b.getID()} at {lat:.5f},{lon:.5f}"
        if air > max_gap_m:
            raise ValueError(f"gap of {air:.0f} m ({where}) exceeds route_match_max_gap_m")
        path, _ = net.getShortestPath(a, b, vClass=vclass)
        if path is None:
            raise ValueError(f"no {vclass} path across the gap {where}")
        middle = list(path[1:-1])
        gaps.append({"after_edge": a.getID(), "before_edge": b.getID(),
                     "lat": round(lat, 6), "lon": round(lon, 6), "air_gap_m": round(air, 1),
                     "bridge_edges": [e.getID() for e in middle],
                     "bridge_length_m": round(sum(e.getLength() for e in middle), 1)})
        route.extend(middle + [b])
    return route, gaps


def gps_fit(xy, route, net, radius_m):
    """How far the GPS points lie from the matched route, and where they leave it."""
    shapes = [e.getShape() for e in route]
    dist = [min(geomhelper.distancePointToPolygon(p, s) for s in shapes) for p in xy]
    runs, current = [], None
    for i, d in enumerate(dist):        # consecutive stretches of points beyond the radius
        if d > radius_m:
            if current is None:
                current = {"first_point": i, "last_point": i, "max_m": d}
            current["last_point"], current["max_m"] = i, max(current["max_m"], d)
        elif current is not None:
            runs.append(current)
            current = None
    if current is not None:
        runs.append(current)
    for r in runs:
        lon, lat = net.convertXY2LonLat(*xy[r["first_point"]])
        r.update(lat=round(lat, 6), lon=round(lon, 6), max_m=round(r["max_m"], 1))
    return {"mean_m": round(sum(dist) / len(dist), 1), "max_m": round(max(dist), 1),
            "points_beyond_radius": sum(1 for d in dist if d > radius_m), "stretches": runs}


def place_stops(net, route, stops):
    """Each observed stop on the matched route: (edge, pos), searching forward from the
    previous stop, so a route that passes the same place twice keeps the trip order."""
    offsets, total = [], 0.0
    for e in route:
        offsets.append(total)
        total += e.getLength()
    placed, first = [], 0
    for row in stops:
        x, y = net.convertLonLat2XY(float(row["snapped_longitude"]), float(row["snapped_latitude"]))
        best = None
        for i in range(first, len(route)):
            shape = route[i].getShape()
            offset, d = geomhelper.polygonOffsetAndDistanceToPoint((x, y), shape)
            if best is None or d < best[0] - 1e-6:
                pos = offset * route[i].getLength() / max(geomhelper.polyLength(shape), 1e-9)
                best = (d, i, pos)
        d, i, pos = best
        first = i
        placed.append({"event": row["event"], "time_ph": row["approx_gps_time_ph"],
                       "headcount_after": row["headcount_after"], "boarded": row["boarded"],
                       "alighted": row["alighted"], "notes": row["notes"],
                       "edge": route[i].getID(), "pos_m": round(pos, 1),
                       "route_offset_m": round(offsets[i] + pos, 1), "distance_m": round(d, 1)})
    return placed


def match_trip(net, obs, vclass, radius_m, max_gap_m, max_speed):
    points = obs["points"]
    xy = drop_standstill([net.convertLonLat2XY(lon, lat) for lon, lat, _ in points])
    matched = mapTrace(xy, net, radius_m, fillGaps=max_gap_m, vClass=vclass)
    if not matched:
        raise ValueError(f"{obs['gpx'].name}: no edges matched")
    route, gaps = bridge_gaps(net, matched, vclass, max_gap_m)

    not_allowed = [e.getID() for e in route if not e.allows(vclass)]
    breaks = [(a.getID(), b.getID()) for a, b in zip(route, route[1:]) if not connected(a, b, vclass)]
    if not_allowed or breaks:
        raise ValueError(f"{obs['gpx'].name}: route still invalid: edges not allowing {vclass} "
                         f"{not_allowed}, unconnected {breaks}")

    gps_length = sum(math.dist(a, b) for a, b in zip(xy, xy[1:]))
    route_length = sum(e.getLength() for e in route)
    duration = (points[-1][2] - points[0][2]).total_seconds()
    return {
        "route_id": f"JEEP_{obs['trip']}",
        "trip": obs["trip"],
        "gpx_file": obs["gpx"].name,
        "gpx_sha256": hashlib.sha256(obs["gpx"].read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "observed_start_utc": points[0][2].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observed_duration_s": round(duration),
        "gps_points": len(points),
        "gps_points_moving": len(xy),
        "gps_length_m": round(gps_length, 1),
        "route_length_m": round(route_length, 1),
        "length_ratio": round(route_length / gps_length, 3),
        "freeflow_time_s": round(sum(e.getLength() / min(e.getSpeed(), max_speed) for e in route)),
        "edges": [e.getID() for e in route],
        "gaps_bridged": gaps,
        "gps_fit": gps_fit(xy, route, net, radius_m),
        "stops": place_stops(net, route, obs["stops"]),
    }


# -------------------------------------------------------------------------------- main
def main() -> None:
    config = Config.load()
    folder = config.dataset_path("jeepney_routes")
    if not NET_FILE.is_file():
        raise SystemExit("No network yet: run python -m sumo_track.network.build first")
    vclass = config.param("jeepney_vclass")
    radius = float(config.param("route_match_radius_m"))
    max_gap = float(config.param("route_match_max_gap_m"))
    max_speed = float(config.param("jeepney_max_speed"))

    trips, stop_file = load_observations(folder)
    print(f"Matching {len(trips)} observed jeepney trips to {NET_FILE.relative_to(REPO_ROOT).as_posix()} "
          f"(vClass {vclass}, radius {radius:.0f} m)")
    net = sumolib.net.readNet(str(NET_FILE))
    try:
        routes = [match_trip(net, obs, vclass, radius, max_gap, max_speed) for obs in trips]
    except ValueError as e:
        raise SystemExit(f"Route matching failed: {e}")

    build = json.loads(BUILD_FILE.read_text(encoding="utf-8")) if BUILD_FILE.is_file() else {}
    OUT_FILE.write_text(json.dumps({
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"jeepney_routes ({config.datasets['jeepney_routes'].active}): "
                  f"{config.datasets['jeepney_routes'].active_source}",
        "stops_file": stop_file.name,
        "net_sha256": build.get("net_sha256"),
        "vclass": vclass, "radius_m": radius, "max_gap_m": max_gap,
        "routes": routes,
    }, indent=2) + "\n", encoding="utf-8")

    for r in routes:
        fit = r["gps_fit"]
        print(f"\n{r['route_id']}  ({r['gpx_file']})")
        print(f"  {len(r['edges'])} edges, {r['route_length_m'] / 1000:.2f} km "
              f"(GPS track {r['gps_length_m'] / 1000:.2f} km, ratio {r['length_ratio']:.2f})")
        print(f"  time: observed {r['observed_duration_s'] / 60:.1f} min, free-flow on the route "
              f"{r['freeflow_time_s'] / 60:.1f} min")
        print(f"  GPS to route: mean {fit['mean_m']} m, max {fit['max_m']} m, "
              f"{fit['points_beyond_radius']} points beyond {radius:.0f} m")
        for s in fit["stretches"]:
            print(f"    off the route: GPS points {s['first_point']}-{s['last_point']} from "
                  f"{s['lat']},{s['lon']}, up to {s['max_m']} m away")
        print(f"  {len(r['gaps_bridged'])} gaps bridged (network fixes to check, step 1.6):")
        for g in r["gaps_bridged"]:
            print(f"    {g['lat']},{g['lon']}: {g['after_edge']} -> {g['before_edge']}, "
                  f"{g['air_gap_m']:.0f} m apart, bridged with a {g['bridge_length_m']:.0f} m detour")
        print(f"  {len(r['stops'])} observed stops placed, farthest "
              f"{max(s['distance_m'] for s in r['stops'])} m from the route")
    print(f"\nWrote {OUT_FILE.relative_to(REPO_ROOT).as_posix()}")


if __name__ == "__main__":
    main()
