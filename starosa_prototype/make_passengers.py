"""
make_passengers.py — generate N random jeepney commuters for the prototype.

A baby version of the demand-file generator of Section 4.4.1.6. For every
passenger it picks:
  * a HOME: the middle of a random walkable street near the jeepney route,
  * a DESTINATION: another random walkable street further down the route,
  * the BOARDING point: the kerbside spot on route_JPN1 nearest to home,
  * the ALIGHTING point: the kerbside spot on route_JPN1 nearest to the
    destination (the "nearest-point function" of Section 4.4.1.5),
  * a random departure time.
Both walk legs are checked with SUMO's own pedestrian router (and must be at
most MAX_WALK long), so the generated file always loads cleanly.

Output: passengers.rou.xml (listed in run.sumocfg). jeepney_controller.py
builds its flag-down registry from that file, so nothing else needs editing.

Usage:
    python make_passengers.py [N] [SEED]        (defaults: N=20, SEED=42)
Same seed -> same passengers, so runs stay reproducible.
"""
import colorsys
import os
import random
import sys
import xml.etree.ElementTree as ET

import sumolib
import traci
from sumolib import geomhelper

# ======================= CONFIG =======================
NET_FILE      = "santarosa.net.xml"
DEMAND_FILE   = "demand.rou.xml"     # route_JPN1 is read from here
OUT_FILE      = "passengers.rou.xml"
ROUTE_ID      = "route_JPN1"
LINE          = "JPN1"
DEPART_WINDOW = (30, 900)   # commuters leave home uniformly in this window (s)
MAX_WALK      = 400.0       # longest walk home->kerb or kerb->destination (m)
MIN_RIDE      = 300.0       # shortest jeepney ride worth taking (m)
KERB_MARGIN   = 10.0        # keep stops this far from either end of an edge (m)
MAX_STREET_SPEED = 14.0     # homes/destinations only on streets <= ~50 km/h
# ======================================================


def read_route(demand_file, route_id):
    for route in ET.parse(demand_file).getroot().iter("route"):
        if route.get("id") == route_id:
            return route.get("edges").split()
    sys.exit(f"route '{route_id}' not found in {demand_file}")


def kerb_edges(net, route):
    """Route edges where a commuter can stand and a jeepney can pull over:
    a sidewalk (pedestrian-only lane) beside a lane jeepneys may use, long
    enough to keep stops KERB_MARGIN from both ends. This skips the trunk-road
    part of the route (no sidewalk - pedestrians there share the traffic
    lanes) and the last edge (the jeepney's trip ends on it).
    Returns [(edge, route offset of the edge's start)]."""
    result, offset = [], 0.0
    for i, edge_id in enumerate(route):
        edge = net.getEdge(edge_id)
        lanes = edge.getLanes()
        has_sidewalk = any(l.allows("pedestrian") and not l.allows("passenger")
                           for l in lanes)
        has_road = any(l.allows("passenger") for l in lanes)
        if (has_sidewalk and has_road and i < len(route) - 1
                and edge.getLength() >= 2 * KERB_MARGIN + 5):
            result.append((edge, offset))
        offset += edge.getLength()
    return result


def nearest_kerb_point(xy, kerb):
    """Nearest-point function: the kerbside route spot closest to xy.
    Returns (edge_id, pos, route_offset, straight_line_distance)."""
    best = None
    for edge, start in kerb:
        shape = edge.getShape()
        offset, dist = geomhelper.polygonOffsetAndDistanceToPoint(xy, shape)
        # shape length and edge length can differ slightly - rescale
        pos = offset * edge.getLength() / geomhelper.polyLength(shape)
        pos = round(min(max(pos, KERB_MARGIN), edge.getLength() - KERB_MARGIN), 1)
        if best is None or dist < best[3]:
            best = (edge.getID(), pos, start + pos, dist)
    return best


def candidate_places(net, route, kerb):
    """Every walkable, non-highway street near the route (its midpoint),
    paired with its nearest kerb point on the route."""
    on_route = set(route)
    places = []
    for edge in net.getEdges():
        if (edge.getID() in on_route or not edge.allows("pedestrian")
                or edge.getSpeed() > MAX_STREET_SPEED or edge.getLength() < 10):
            continue
        shape = edge.getShape()
        xy = geomhelper.positionAtShapeOffset(shape, geomhelper.polyLength(shape) / 2)
        kerb_point = nearest_kerb_point(xy, kerb)
        # a walk is never shorter than the straight line - cheap pre-filter
        if kerb_point[3] <= MAX_WALK:
            places.append({"edge": edge.getID(),
                           "pos": round(edge.getLength() / 2, 1),
                           "kerb": kerb_point})
    return places


def walk_length(from_edge, from_pos, to_edge, to_pos):
    """Length of SUMO's own pedestrian route, or None if there is none."""
    stages = traci.simulation.findIntermodalRoute(
        from_edge, to_edge, departPos=from_pos, arrivalPos=to_pos)
    if not stages:
        return None
    return sum(s.length for s in stages)


def sample_passengers(n, rng, places):
    passengers = []
    attempts = 0
    while len(passengers) < n:
        attempts += 1
        if attempts > 500 * n:
            sys.exit(f"Only placed {len(passengers)}/{n} passengers - "
                     "try a larger MAX_WALK or a smaller MIN_RIDE.")
        home = rng.choice(places)
        board_edge, board_pos, board_offset, _ = home["kerb"]
        # destination must be far enough DOWNSTREAM - the line runs one way
        dests = [p for p in places if p["kerb"][2] - board_offset >= MIN_RIDE]
        if not dests:
            continue
        dest = rng.choice(dests)
        alight_edge, alight_pos, alight_offset, _ = dest["kerb"]

        access = walk_length(home["edge"], home["pos"], board_edge, board_pos)
        if access is None or access > MAX_WALK:
            continue
        egress = walk_length(alight_edge, alight_pos, dest["edge"], dest["pos"])
        if egress is None or egress > MAX_WALK:
            continue
        passengers.append({
            "home_edge": home["edge"], "home_pos": home["pos"],
            "board_edge": board_edge, "board_pos": board_pos,
            "alight_edge": alight_edge, "alight_pos": alight_pos,
            "dest_edge": dest["edge"], "dest_pos": dest["pos"],
            "access": access, "ride": alight_offset - board_offset,
            "egress": egress,
        })
    return passengers


def write_passengers(path, passengers, departs, seed, rng):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<!-- {path} — {len(passengers)} random {LINE} commuters, generated by',
        f'     make_passengers.py (seed {seed}). Do not edit by hand: re-run the script.',
        '     Plan per person: walk home -> kerb, flag down a jeepney, ride, walk -> destination.',
        '     The first walk\'s arrivalPos IS the boarding spot and the ride\'s arrivalPos',
        '     IS the alighting spot - jeepney_controller.py reads both from this file.',
        '     Persons are sorted by depart: SUMO drops out-of-order entries. -->',
        '<routes>',
    ]
    for i, (p, depart) in enumerate(zip(passengers, departs)):
        r, g, b = colorsys.hsv_to_rgb(rng.random(), 0.85, 1.0)
        lines += [
            f'    <!-- walk {p["access"]:.0f} m, ride {p["ride"]:.0f} m, '
            f'walk {p["egress"]:.0f} m -->',
            f'    <person id="pax{i:02d}" depart="{depart:.0f}" '
            f'departPos="{p["home_pos"]}" color="{r:.2f},{g:.2f},{b:.2f}">',
            f'        <walk from="{p["home_edge"]}" to="{p["board_edge"]}" '
            f'arrivalPos="{p["board_pos"]}"/>',
            f'        <ride from="{p["board_edge"]}" to="{p["alight_edge"]}" '
            f'lines="{LINE}" arrivalPos="{p["alight_pos"]}"/>',
            f'        <walk from="{p["alight_edge"]}" to="{p["dest_edge"]}" '
            f'arrivalPos="{p["dest_pos"]}"/>',
            '    </person>',
        ]
    lines.append('</routes>')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    rng = random.Random(seed)
    # file names above are relative to this folder, wherever we're run from
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    net = sumolib.net.readNet(NET_FILE)
    route = read_route(DEMAND_FILE, ROUTE_ID)
    kerb = kerb_edges(net, route)
    places = candidate_places(net, route, kerb)
    print(f"{len(kerb)} kerbside route edges, "
          f"{len(places)} candidate homes/destinations within {MAX_WALK:.0f} m")

    # SUMO with only the network loaded, used purely as a pedestrian router
    traci.start(["sumo", "-n", NET_FILE, "--no-step-log", "true",
                 "--no-warnings", "true"])
    try:
        passengers = sample_passengers(n, rng, places)
    finally:
        traci.close()

    departs = sorted(rng.uniform(*DEPART_WINDOW) for _ in passengers)
    write_passengers(OUT_FILE, passengers, departs, seed, rng)

    print(f"\n{'id':6s} {'depart':>6s}  {'board at':22s} {'alight at':22s} "
          f"{'walk':>5s} {'ride':>5s} {'walk':>5s}")
    for i, (p, depart) in enumerate(zip(passengers, departs)):
        print(f"pax{i:02d} {depart:6.0f}  "
              f"{p['board_edge'] + '@' + str(p['board_pos']):22s} "
              f"{p['alight_edge'] + '@' + str(p['alight_pos']):22s} "
              f"{p['access']:5.0f} {p['ride']:5.0f} {p['egress']:5.0f}")
    print(f"\nWrote {len(passengers)} passengers to {OUT_FILE}.")


if __name__ == "__main__":
    main()
