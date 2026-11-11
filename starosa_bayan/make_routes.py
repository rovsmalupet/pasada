"""Jeepney lines as settings.JEEPNEY_LINES describes them, both directions.

Each line follows one or more roads: a road is the edges with its name (matched ignoring
punctuation) that are of LINE_ROAD_TYPES (trunk, primary, secondary) and that jeepneys may use.
For each road, road_paths finds its two far ends and routes a jeepney from one to the other, both
ways. The routing prefers the road itself (every metre off it costs OFF_HIGHWAY_PENALTY metres),
so the route follows it and only leaves it where the network forces a short detour.

A line drives its first road end to end, then joins each next road at the edge nearest by road
(the shortest drive, at most CONNECT_M) and drives it to its end. Its reverse drives the same
roads backwards: it leaves each road where the next one is nearest and drives the last one end to
end. Lines are named by their key and the compass direction from start to end, e.g. N1_S,
RIZ-FRS_S. Copied from ../starosa_prototype (ported there from ../sumo_track_prototype), where
every line was one road.

Writes generated/jeepney_routes.json and generated/jeepney.rou.xml (routes + one flow per line).

    python make_routes.py
"""

import heapq
import json
import math
import re

import sumolib
from sumolib import geomhelper

import settings as S

OPPOSITE = {"N": "S", "NE": "SW", "E": "W", "SE": "NW", "S": "N", "SW": "NE", "W": "E", "NW": "SE"}
CONNECT_M = 2000.0                 # longest drive from one road of a line to the next


def norm(name):
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def can_drive(a, b, vclass):
    """Can vclass drive from the end of edge a onto edge b?"""
    return b.allows(vclass) and any(c.getFromLane().allows(vclass) and c.getToLane().allows(vclass)
                                    for c in a.getOutgoing().get(b, []))


def midpoint(edge):
    shape = edge.getShape()
    return geomhelper.positionAtShapeOffset(shape, geomhelper.polyLength(shape) / 2)


def far_ends(edges):
    """The two points of the road farthest apart (edge midpoints)."""
    mids = [midpoint(e) for e in edges]
    _, i, j = max((math.dist(a, b), i, j) for i, a in enumerate(mids) for j, b in enumerate(mids) if i < j)
    return mids[i], mids[j]


def end_edges(edges, p_from, p_to):
    """First and last road edge for travel from p_from towards p_to: the edge starting
    nearest p_from that heads towards p_to, and the edge ending nearest p_to that comes from
    the p_from side."""
    def d(node, p):
        return math.dist(node.getCoord(), p)
    first = min((e for e in edges if d(e.getToNode(), p_to) < d(e.getFromNode(), p_to)),
                key=lambda e: d(e.getFromNode(), p_from))
    last = min((e for e in edges if d(e.getFromNode(), p_from) < d(e.getToNode(), p_from)),
               key=lambda e: d(e.getToNode(), p_to))
    return first, last


def preferred_path(start, end, preferred, vclass, penalty):
    """Cheapest route from edge start to edge end, where edges outside `preferred` cost
    `penalty` times their length."""
    best, prev, counter = {start: 0.0}, {}, 0
    heap = [(0.0, counter, start)]
    while heap:
        cost, _, edge = heapq.heappop(heap)
        if edge == end:
            break
        if cost > best[edge]:
            continue
        for nxt in edge.getOutgoing():
            if not can_drive(edge, nxt, vclass):
                continue
            c = cost + nxt.getLength() * (1.0 if nxt in preferred else penalty)
            if c < best.get(nxt, math.inf):
                best[nxt], prev[nxt] = c, edge
                counter += 1
                heapq.heappush(heap, (c, counter, nxt))
    if end not in best:
        return None
    path = [end]
    while path[-1] != start:
        path.append(prev[path[-1]])
    return path[::-1]


def compass(p_from, p_to):
    """'NE', 'SW', ... for travel from p_from to p_to (network y points north)."""
    angle = math.degrees(math.atan2(p_to[0] - p_from[0], p_to[1] - p_from[1])) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][round(angle / 45) % 8]


def road_paths(net, name):
    """({compass direction: route} driving the whole road each way, the road's edges)."""
    edges = [e for e in net.getEdges() if norm(e.getName()) == norm(name)
             and e.getType() in S.LINE_ROAD_TYPES and e.allows(S.JEEPNEY_VCLASS)]
    if len(edges) < 2:
        raise SystemExit(f"No road '{name}' of {', '.join(S.LINE_ROAD_TYPES)} with 2+ edges {S.JEEPNEY_VCLASS} may use")
    a, b = far_ends(edges)
    paths = {}
    for p_from, p_to in ((a, b), (b, a)):
        first, last = end_edges(edges, p_from, p_to)
        path = preferred_path(first, last, set(edges), S.JEEPNEY_VCLASS, S.OFF_HIGHWAY_PENALTY)
        if path is None:
            raise SystemExit(f"No {S.JEEPNEY_VCLASS} route along {name} from {first.getID()} to {last.getID()}")
        paths[compass(p_from, p_to)] = path
    return paths, set(edges)


def nearest_by_road(start, targets, forward):
    """(the edge of targets nearest by road, the edges driven in between, in driving order):
    driving on from edge start (forward) or driving to it (backward), at most CONNECT_M.
    (None, None) if none is that near."""
    targets = set(targets)
    if start in targets:
        return start, []
    best, prev, counter = {start: 0.0}, {}, 0
    heap = [(0.0, counter, start)]
    while heap:
        cost, _, edge = heapq.heappop(heap)
        if cost > best[edge]:
            continue
        if edge in targets:
            between, e = [], prev[edge]
            while e != start:
                between.append(e)
                e = prev[e]
            return edge, between[::-1] if forward else between
        if forward:
            steps = [n for n in edge.getOutgoing() if can_drive(edge, n, S.JEEPNEY_VCLASS)]
        else:
            steps = [n for n in edge.getIncoming() if can_drive(n, edge, S.JEEPNEY_VCLASS)]
        for nxt in steps:
            c = cost + nxt.getLength()
            if c <= CONNECT_M and c < best.get(nxt, math.inf):
                best[nxt], prev[nxt] = c, edge
                counter += 1
                heapq.heappush(heap, (c, counter, nxt))
    return None, None


def line_route(paths, spec, first_whole):
    """The route of a line driving the roads of spec ([(road, direction)], in driving order).
    first_whole: the first road end to end, each next joined where nearest; otherwise the last
    road end to end, each earlier one left where the next is nearest."""
    if first_whole:
        route = list(paths[spec[0][0]][spec[0][1]])
        for road, d in spec[1:]:
            nxt = paths[road][d]
            target, between = nearest_by_road(route[-1], nxt, forward=True)
            if target is None:
                raise SystemExit(f"{road} ({d}) is not within {CONNECT_M:.0f} m by road of {spec[0][0]}")
            k = nxt.index(target)
            route += between + nxt[k + 1 if target == route[-1] else k:]
    else:
        route = list(paths[spec[-1][0]][spec[-1][1]])
        for road, d in reversed(spec[:-1]):
            prv = paths[road][d]
            target, between = nearest_by_road(route[0], prv, forward=False)
            if target is None:
                raise SystemExit(f"{spec[-1][0]} is not within {CONNECT_M:.0f} m by road of {road} ({d})")
            j = prv.index(target)
            route = prv[:j if target == route[0] else j + 1] + between + route
    for a, b in zip(route, route[1:]):
        if not can_drive(a, b, S.JEEPNEY_VCLASS):
            raise SystemExit(f"Broken route for {spec}: {a.getID()} -> {b.getID()}")
    return route


def main():
    S.GENERATED.mkdir(exist_ok=True)
    if not S.NET_FILE.is_file():
        raise SystemExit(f"No network at {S.NET_FILE}: build it first (python make_map.py)")
    net = sumolib.net.readNet(str(S.NET_FILE))
    roads = {road for spec in S.JEEPNEY_LINES.values() for road, _ in spec}
    paths, road_edges = {}, {}
    for road in sorted(roads):
        paths[road], road_edges[road] = road_paths(net, road)
        print(f"{road}: directions {', '.join(paths[road])}")
    lines = {}
    for key, spec in S.JEEPNEY_LINES.items():
        for road, d in spec:
            if d not in paths[road]:
                raise SystemExit(f"JEEPNEY_LINES['{key}']: {road} runs {' or '.join(paths[road])}, not {d}")
        back = [(road, OPPOSITE[d]) for road, d in reversed(spec)]
        for drive, first_whole in ((spec, True), (back, False)):
            route = line_route(paths, drive, first_whole)
            start, end = route[0].getFromNode().getCoord(), route[-1].getToNode().getCoord()
            line = f"{key}_{compass(start, end)}"
            if line in lines:
                raise SystemExit(f"Two lines named {line}")
            on_roads = set().union(*(road_edges[road] for road, _ in drive))
            length = sum(e.getLength() for e in route)
            on = sum(e.getLength() for e in route if e in on_roads)
            lon0, lat0 = net.convertXY2LonLat(*start)
            lon1, lat1 = net.convertXY2LonLat(*end)
            lines[line] = {
                "key": key, "roads": [road for road, _ in drive], "highway": " + ".join(road for road, _ in drive),
                "edges": [e.getID() for e in route],
                "length_m": round(length, 1), "on_highway_share": round(on / length, 3),
                "freeflow_s": round(sum(e.getLength() / min(e.getSpeed(), S.JEEPNEY["maxSpeed"]) for e in route)),
                "from_lat_lon": [round(lat0, 5), round(lon0, 5)], "to_lat_lon": [round(lat1, 5), round(lon1, 5)],
            }
            print(f"{line:11s} {' -> '.join(f'{r} {d}' for r, d in drive)}: {len(route)} edges, {length / 1000:.2f} km, "
                  f"{100 * on / length:.0f}% on its roads, free-flow {lines[line]['freeflow_s'] / 60:.1f} min, "
                  f"from {lat0:.5f},{lon0:.5f} to {lat1:.5f},{lon1:.5f}")

    (S.GENERATED / "jeepney_routes.json").write_text(json.dumps(lines, indent=2) + "\n", encoding="utf-8")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           "<!-- Generated by make_routes.py. Do not edit: rerun it. -->", "<routes>"]
    xml += [f'    <route id="r_{line}" edges="{" ".join(r["edges"])}"/>' for line, r in lines.items()]
    xml += [f'    <flow id="{line}" type="jeepney" route="r_{line}" line="{line}" begin="0" '
            f'end="{S.END_S}" period="{S.JEEPNEY_HEADWAY_S}" departLane="best"/>' for line in lines]
    xml.append("</routes>")
    (S.GENERATED / "jeepney.rou.xml").write_text("\n".join(xml) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} lines to generated/jeepney_routes.json and generated/jeepney.rou.xml")


if __name__ == "__main__":
    main()
