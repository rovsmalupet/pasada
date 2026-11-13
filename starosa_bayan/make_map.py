"""The map: the study area (AREA_BARANGAYS), its OSM extract and its SUMO network.

  1. AREA: the union of the barangays in AREA_BARANGAYS (data/santa_rosa_barangays.geojson, from
     get_barangays.py) -> data/area.geojson. Homes and destinations are kept inside it.
  2. OSM: from OSM_SOURCE (sumo_track's download of the whole city), every way with at least
     one node inside the area, whole, with all its nodes, and the turn restrictions among them
     -> bayan.osm. Like the OSM Web Wizard's box, side streets crossing the edge are kept whole.
     The main roads (APPROACH_HIGHWAYS) are kept up to APPROACH_M outside the area and cut there,
     at a new node, so jeepneys and through traffic come in from that far out: those touching the
     area, and those outside it joined to them through main roads (the approach roads, without
     their side streets).
  3. NETWORK: netconvert -c netconvert.netccfg -> bayan.net.xml (SLEX and its ramps removed).
     It prints the car-lane length, which sets BACKGROUND_PER_HOUR in settings.py.

Rerun build.py after this: edge IDs and lengths change.

    python make_map.py
"""

import json
import math
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict

import numpy as np
import shapely
import sumolib
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union

import settings as S
from make_routes import midpoint, study_area


def make_area():
    features = json.loads(S.BARANGAYS_FILE.read_text(encoding="utf-8"))["features"]
    chosen = [f for f in features if f["properties"]["adm4_pcode"] in S.AREA_BARANGAYS]
    missing = set(S.AREA_BARANGAYS) - {f["properties"]["adm4_pcode"] for f in chosen}
    if missing:
        raise SystemExit(f"Not in {S.BARANGAYS_FILE.name}: {sorted(missing)}")
    area = unary_union([shape(f["geometry"]) for f in chosen])
    names = sorted(f["properties"]["adm4_name"] for f in chosen)
    S.AREA_FILE.write_text(json.dumps({"type": "Feature", "properties": {
        "barangays": names, "pcodes": sorted(S.AREA_BARANGAYS), "source": S.BARANGAYS_FILE.name},
        "geometry": mapping(area)}, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Area: {len(chosen)} barangays, {area.geom_type} -> {S.AREA_FILE.relative_to(S.HERE)}")
    return area


NEW_ID = 10 ** 15        # ids for the nodes and way pieces make_map.py adds (OSM's are about 1e10)


def cut_osm(area):
    root = ET.parse(S.OSM_SOURCE).getroot()
    ids, lons, lats = [], [], []
    for n in root.iter("node"):
        ids.append(n.get("id"))
        lons.append(float(n.get("lon")))
        lats.append(float(n.get("lat")))
    inside = {i for i, ok in zip(ids, shapely.contains_xy(area, lons, lats)) if ok}
    lonlat = dict(zip(ids, zip(lons, lats)))
    # metres east and north of the area's centre (equirectangular: fine over a few km)
    lon0, lat0 = area.centroid.x, area.centroid.y
    scale = np.array([111320.0 * math.cos(math.radians(lat0)), 110540.0])
    area_m = shapely.transform(area, lambda c: (c - [lon0, lat0]) * scale)
    xy = (np.column_stack([lons, lats]) - [lon0, lat0]) * scale
    away = dict(zip(ids, shapely.distance(area_m, shapely.points(xy))))     # 0 inside the area
    added, cuts = [], {}                                                     # new nodes; (a, b) -> node id

    def cut_node(a, b):
        """A new node where the segment from a (within APPROACH_M) to b (beyond) is APPROACH_M out."""
        if (a, b) not in cuts:
            f = (S.APPROACH_M - away[a]) / (away[b] - away[a])
            lon, lat = (pa + f * (pb - pa) for pa, pb in zip(lonlat[a], lonlat[b]))
            node_id = str(NEW_ID + len(added))
            added.append(ET.Element("node", id=node_id, lat=f"{lat:.7f}", lon=f"{lon:.7f}", version="1"))
            cuts[(a, b)] = node_id
        return cuts[(a, b)]

    def pieces(refs):
        """The runs of a way's nodes within APPROACH_M of the area, each ended at a new node where it
        leaves that distance."""
        runs, run = [], []
        for prev, r in zip([None] + refs, refs):
            if away[r] <= S.APPROACH_M:
                if not run and prev is not None:
                    run.append(cut_node(r, prev))              # coming back from beyond
                run.append(r)
            elif run:
                run.append(cut_node(prev, r))                  # leaving: cut here
                runs.append(run)
                run = []
        return [run for run in runs + [run] if len(run) >= 2]

    ways, needed, main = [], set(), []
    for w in root.iter("way"):
        refs = [nd.get("ref") for nd in w.iter("nd")]
        if any(t.get("k") == "highway" and t.get("v") in S.APPROACH_HIGHWAYS for t in w.iter("tag")):
            main += [(w, run, k) for k, run in enumerate(pieces(refs))]
        elif any(r in inside for r in refs):
            ways.append(w)
            needed.update(refs)
    # the main-road pieces touching the area, and those reached from them through main roads
    by_node = defaultdict(list)
    for i, (_, run, _) in enumerate(main):
        for r in run:
            by_node[r].append(i)
    todo = [i for i, (_, run, _) in enumerate(main) if any(r in inside for r in run)]
    kept = set(todo)
    while todo:
        for r in main[todo.pop()][1]:
            for j in by_node[r]:
                if j not in kept:
                    kept.add(j)
                    todo.append(j)
    approach = trimmed = 0
    for i in sorted(kept):
        w, run, k = main[i]
        piece = ET.Element("way", {**w.attrib, "id": w.get("id") if k == 0 else str(NEW_ID + len(added) + i)})
        piece.extend([ET.Element("nd", ref=r) for r in run] + [t for t in w if t.tag == "tag"])
        ways.append(piece)
        needed.update(r for r in run if int(r) < NEW_ID)
        approach += not any(r in inside for r in run)
        trimmed += len(run) != len(list(w.iter("nd")))
    kept_ways = {w.get("id") for w in ways}
    relations = []
    for r in root.iter("relation"):
        tags = {t.get("k"): t.get("v") for t in r.iter("tag")}
        members = list(r.iter("member"))
        if tags.get("type") == "restriction" and all(
                (m.get("ref") in kept_ways) if m.get("type") == "way" else (m.get("ref") in needed)
                for m in members if m.get("type") in ("way", "node")):
            relations.append(r)
    out = ET.Element("osm", root.attrib)
    out.extend([n for n in root.iter("node") if n.get("id") in needed] + added + ways + relations)
    ET.ElementTree(out).write(S.OSM_FILE, encoding="UTF-8", xml_declaration=True)
    print(f"OSM: {len(needed) + len(added):,} nodes ({len(inside):,} inside the area), {len(ways):,} ways "
          f"({approach} of them approach roads outside; main roads cut {S.APPROACH_M:.0f} m outside the area: "
          f"{trimmed} cut, {len(added)} new end nodes), {len(relations)} turn restrictions -> {S.OSM_FILE.name} "
          f"({S.OSM_FILE.stat().st_size / 1e6:.1f} MB)")


def build_network():
    result = subprocess.run([sumolib.checkBinary("netconvert"), "-c", "netconvert.netccfg"], cwd=S.HERE,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"netconvert failed:\n{result.stderr[-3000:]}")
    warnings = [line for line in result.stderr.splitlines() if line.startswith("Warning")]
    net = sumolib.net.readNet(str(S.NET_FILE))
    edges = [e for e in net.getEdges() if e.getFunction() != "internal"]
    area = study_area(net)
    lanes = [(e, l) for e in edges for l in e.getLanes() if l.allows("passenger")]
    car_km = sum(l.getLength() for _, l in lanes) / 1000
    inside_km = sum(l.getLength() for e, l in lanes if area.contains(Point(midpoint(e)))) / 1000
    print(f"Network: {len(edges):,} edges, {len(net.getNodes()):,} junctions, {car_km:.1f} km of car lanes "
          f"({inside_km:.1f} km inside the area) -> {S.NET_FILE.name} ({len(warnings)} netconvert warnings)")
    print(f"Inside trips at x1 as dense as sumo_track_prototype's 1,800/h per type on 1,996.5 km: "
          f"BACKGROUND_PER_HOUR = {1800 * inside_km / 1996.5:.0f} per type")


def main():
    area = make_area()
    cut_osm(area)
    build_network()
    print("Next: python build.py")


if __name__ == "__main__":
    main()
