"""Random commuters who walk to a highway, ride a jeepney along it, and walk on.

For each commuter (seeded, so the same N and SEED give the same people):
  * a LINE at random (each highway in settings.HIGHWAYS, both directions),
  * a HOME: the middle of a random walkable street within MAX_WALK (straight line) of the line,
  * a DESTINATION: another such street whose roadside spot lies at least MIN_RIDE further along
    the line (a line runs one way),
  * the BOARDING spot: the roadside spot on the line with the SHORTEST WALK from home, measured
    with SUMO's own pedestrian router, so people wait where their street meets the highway
    (the nearest-on-foot rule of starosa_prototype),
  * the ALIGHTING spot: likewise, the shortest walk to the destination,
  * a random departure time in DEPART_WINDOW.
Both walks are at most MAX_WALK.

Writes generated/commuters.rou.xml; run.py reads each commuter's line and spots from it.

    python make_commuters.py [N]          (default settings.N_COMMUTERS)
"""

import json
import math
import random
import sys
from collections import defaultdict

import sumolib
import traci
from sumolib import geomhelper

import settings as S
from make_routes import norm

LINE_COLORS = {"N1": ["1,0.3,0.3", "1,0.6,0.2"], "TGY": ["0.3,0.6,1", "0.3,0.9,0.4"]}


def kerb_edges(net, edges):
    """Route edges where a commuter can stand and a jeepney can stop: some lane allows
    pedestrians (a sidewalk, or a road lane people may walk on), some lane allows the jeepney,
    and the edge is long enough to stop KERB_MARGIN from both ends. Not the last edge (the
    jeepney's trip ends there). Returns [(edge, route offset of the edge's start)]."""
    result, offset = [], 0.0
    for i, edge_id in enumerate(edges):
        edge = net.getEdge(edge_id)
        lanes = edge.getLanes()
        if (any(l.allows("pedestrian") for l in lanes) and any(l.allows(S.JEEPNEY_VCLASS) for l in lanes)
                and i < len(edges) - 1 and edge.getLength() >= 2 * S.KERB_MARGIN + 5):
            result.append((edge, offset))
        offset += edge.getLength()
    return result


def kerb_spots(kerb):
    """KERB_MARGIN in from each end of every kerb edge. People reach an edge at one of its
    ends, so the spot with the shortest walk on an edge is always one of these two.
    Returns [(edge_id, pos, route_offset, xy)]."""
    spots = []
    for edge, start in kerb:
        shape = edge.getShape()
        scale = geomhelper.polyLength(shape) / edge.getLength()   # shape vs edge length
        for pos in (S.KERB_MARGIN, edge.getLength() - S.KERB_MARGIN):
            spots.append((edge.getID(), round(pos, 1), start + pos,
                          geomhelper.positionAtShapeOffset(shape, pos * scale)))
    return spots


class SpotGrid:
    """Spots bucketed in MAX_WALK-sized cells: every spot within MAX_WALK of a point lies in
    the 3x3 cells around it."""

    def __init__(self, spots):
        self.cells = defaultdict(list)
        for s in spots:
            self.cells[self.cell(s[3])].append(s)

    @staticmethod
    def cell(xy):
        return int(xy[0] // S.MAX_WALK), int(xy[1] // S.MAX_WALK)

    def near(self, xy):
        cx, cy = self.cell(xy)
        found = [s for dx in (-1, 0, 1) for dy in (-1, 0, 1) for s in self.cells.get((cx + dx, cy + dy), ())]
        return sorted((math.dist(xy, s[3]), s) for s in found if math.dist(xy, s[3]) <= S.MAX_WALK)


def walk_length(from_edge, from_pos, to_edge, to_pos):
    """Length of SUMO's own pedestrian route, or None if there is none."""
    stages = traci.simulation.findIntermodalRoute(from_edge, to_edge, departPos=from_pos, arrivalPos=to_pos)
    return sum(s.length for s in stages) if stages else None


def nearest_on_foot(place, grid):
    """The spot with the shortest walk from place (edge, pos, xy):
    (edge_id, pos, route_offset, walk_m), or None if none is within MAX_WALK on foot."""
    edge_id, pos, xy = place
    best = None
    for dist, (s_edge, s_pos, s_offset, _) in grid.near(xy):
        # nearest first; a walk is never shorter than the straight line (give or take a few
        # metres: people walk beside the centre lines measured here), so stop when none can win
        if dist - 10.0 > (best[3] if best else S.MAX_WALK):
            break
        walk = walk_length(edge_id, pos, s_edge, s_pos)
        if walk is not None and walk <= S.MAX_WALK and (best is None or walk < best[3]):
            best = (s_edge, s_pos, s_offset, walk)
    return best


def candidate_places(net, grid, exclude):
    """(edge, pos, xy) at the middle of every walkable street within MAX_WALK of a spot."""
    places = []
    for edge in net.getEdges():
        if (edge.getID() in exclude or not edge.allows("pedestrian")
                or edge.getSpeed() > S.MAX_STREET_SPEED or edge.getLength() < 10):
            continue
        shape = edge.getShape()
        xy = geomhelper.positionAtShapeOffset(shape, geomhelper.polyLength(shape) / 2)
        if grid.near(xy):
            places.append((edge.getID(), round(edge.getLength() / 2, 1), xy))
    return places


def load_lines():
    path = S.GENERATED / "jeepney_routes.json"
    if not path.is_file():
        raise SystemExit("No jeepney lines yet: run python make_routes.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else S.N_COMMUTERS
    rng = random.Random(S.SEED)
    net = sumolib.net.readNet(str(S.NET_FILE))
    lines = load_lines()
    highway_names = {norm(name) for name in S.HIGHWAYS.values()}
    on_lines = {e for r in lines.values() for e in r["edges"]}
    exclude = on_lines | {e.getID() for e in net.getEdges() if norm(e.getName()) in highway_names}

    grids, places = {}, {}
    for line, r in lines.items():
        kerb = kerb_edges(net, r["edges"])
        grids[line] = SpotGrid(kerb_spots(kerb))
        places[line] = candidate_places(net, grids[line], exclude)
        print(f"{line}: {len(kerb)} roadside edges, {len(places[line])} streets within {S.MAX_WALK:.0f} m")

    # SUMO with only the network loaded, used purely as a pedestrian router
    traci.start([sumolib.checkBinary("sumo"), "-n", str(S.NET_FILE), "--no-step-log", "true",
                 "--no-warnings", "true"])
    cache, commuters = {}, []
    try:
        def spot(line, place):
            key = (line, place[0])
            if key not in cache:
                cache[key] = nearest_on_foot(place, grids[line])
            return cache[key]

        attempts = 0
        while len(commuters) < n:
            attempts += 1
            if attempts > 200 * n:
                raise SystemExit(f"Only placed {len(commuters)}/{n} commuters; try a larger MAX_WALK "
                                 "or a smaller MIN_RIDE in settings.py")
            line = rng.choice(sorted(lines))
            home, dest = rng.choice(places[line]), rng.choice(places[line])
            board, alight = spot(line, home), spot(line, dest)
            if board is None or alight is None or alight[2] - board[2] < S.MIN_RIDE:
                continue
            # the alighting spot was chosen walking FROM the destination; check the walk the
            # commuter really makes, from the roadside to the destination
            egress = walk_length(alight[0], alight[1], dest[0], dest[1])
            if egress is None or egress > S.MAX_WALK:
                continue
            commuters.append({"line": line, "home": home, "board": board, "alight": alight,
                              "dest": dest, "access_m": board[3], "ride_m": alight[2] - board[2],
                              "egress_m": egress})
    finally:
        traci.close()

    departs = sorted(rng.uniform(*S.DEPART_WINDOW) for _ in commuters)
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           f"<!-- Generated by make_commuters.py ({len(commuters)} commuters, seed {S.SEED}). Do not edit:",
           "     rerun it. Each person walks home -> roadside, flags a jeepney on their line, rides, walks on.",
           "     The first walk's arrivalPos is the boarding spot and the ride's arrivalPos the alighting",
           "     spot: run.py reads both from here. Sorted by depart: SUMO drops out-of-order entries. -->",
           "<routes>"]
    for i, (c, depart) in enumerate(zip(commuters, departs)):
        prefix = c["line"].split("_")[0]
        color = LINE_COLORS.get(prefix, ["1,1,1", "1,1,1"])[sorted(lines).index(c["line"]) % 2]
        xml += [f'    <!-- walk {c["access_m"]:.0f} m, ride {c["ride_m"]:.0f} m, walk {c["egress_m"]:.0f} m -->',
                f'    <person id="c{i:03d}_{c["line"]}" depart="{depart:.0f}" departPos="{c["home"][1]}" color="{color}">',
                f'        <walk from="{c["home"][0]}" to="{c["board"][0]}" arrivalPos="{c["board"][1]}"/>',
                f'        <ride from="{c["board"][0]}" to="{c["alight"][0]}" lines="{c["line"]}" arrivalPos="{c["alight"][1]}"/>',
                f'        <walk from="{c["alight"][0]}" to="{c["dest"][0]}" arrivalPos="{c["dest"][1]}"/>',
                "    </person>"]
    xml.append("</routes>")
    (S.GENERATED / "commuters.rou.xml").write_text("\n".join(xml) + "\n", encoding="utf-8")

    per_line = defaultdict(int)
    for c in commuters:
        per_line[c["line"]] += 1
    walks = [c["access_m"] for c in commuters] + [c["egress_m"] for c in commuters]
    rides = [c["ride_m"] for c in commuters]
    print(f"\n{len(commuters)} commuters ({attempts} draws): " + ", ".join(f"{k} {v}" for k, v in sorted(per_line.items())))
    print(f"  walks: mean {sum(walks) / len(walks):.0f} m, max {max(walks):.0f} m; "
          f"rides: mean {sum(rides) / len(rides) / 1000:.1f} km, max {max(rides) / 1000:.1f} km")
    print("Wrote generated/commuters.rou.xml")


if __name__ == "__main__":
    main()
