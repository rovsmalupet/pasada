"""Jeepney lines as settings.JEEPNEY_LINES describes them, both directions.

Each line follows one or more roads: a road is the edges with its name (matched ignoring
punctuation) that are of LINE_ROAD_TYPES (trunk, primary, secondary) and that jeepneys may use.
For each road, road_paths finds its two far ends and routes a jeepney from one to the other, both
ways. The routing prefers the road itself (every metre off it costs OFF_HIGHWAY_PENALTY metres),
so the route follows it and only leaves it where the network forces a short detour. A road's two
directions (the compass names in JEEPNEY_LINES) are those of its part inside the study area, so
they stay put when the approach roads (make_map.py) make it longer outside.

A line drives its first road end to end, then joins each next road at the edge nearest by road
(the shortest drive, at most CONNECT_M) and drives it to its end. Its reverse drives the same
roads backwards: it leaves each road where the next one is nearest and drives the last one end to
end. A line whose roads start or end less than LINE_OUTSIDE_M outside the area gets a lead-in or
lead-out: the shortest drive along main roads (APPROACH_HIGHWAYS) from or to a point that far
out, so its jeepneys come in from outside, already moving, and leave the area again. Lines are
named by their key and the compass direction of their part inside the area, e.g. N1_S, RIZ-FRS_S.
Copied from ../starosa_prototype (ported there from ../sumo_track_prototype), where every line
was one road.

Writes generated/jeepney_routes.json and generated/jeepney.rou.xml: the routes, and one jeepney
per line every JEEPNEY_HEADWAY_S over the day. Each is its own vehicle, named as a flow would name
it (<line>.<n>), so make_background.py can put passengers aboard it.

    python make_routes.py
"""

import heapq
import json
import math
import re

import sumolib
from shapely.geometry import Point, Polygon, shape
from shapely.ops import unary_union
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


def study_area(net):
    """The study area (AREA_FILE) in network coordinates, metres."""
    geom = shape(json.loads(S.AREA_FILE.read_text(encoding="utf-8"))["geometry"])
    parts = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    return unary_union([Polygon([net.convertLonLat2XY(*c[:2]) for c in p.exterior.coords]) for p in parts])


def road_paths(net, name, area):
    """({compass direction: route} driving the whole road each way, the road's edges). The
    directions are those of the road's part inside area (all of it if under 2 edges are inside)."""
    edges = [e for e in net.getEdges() if norm(e.getName()) == norm(name)
             and e.getType() in S.LINE_ROAD_TYPES and e.allows(S.JEEPNEY_VCLASS)]
    if len(edges) < 2:
        raise SystemExit(f"No road '{name}' of {', '.join(S.LINE_ROAD_TYPES)} with 2+ edges {S.JEEPNEY_VCLASS} may use")
    inside = [e for e in edges if area.contains(Point(midpoint(e)))]
    a, b = far_ends(inside if len(inside) >= 2 else edges)
    # the whole road's ends: its edges furthest along the line from a to b, either way
    along = sorted((midpoint(e) for e in edges), key=lambda p: (p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1]))
    paths = {}
    for p_from, p_to, direction in ((along[0], along[-1], compass(a, b)), (along[-1], along[0], compass(b, a))):
        first, last = end_edges(edges, p_from, p_to)
        path = preferred_path(first, last, set(edges), S.JEEPNEY_VCLASS, S.OFF_HIGHWAY_PENALTY)
        if path is None:
            raise SystemExit(f"No {S.JEEPNEY_VCLASS} route along {name} from {first.getID()} to {last.getID()}")
        paths[direction] = path
    return paths, set(edges)


def lead(area, edge, forward):
    """The shortest drive along main roads (APPROACH_HIGHWAYS) a jeepney may take from edge out to
    a point LINE_OUTSIDE_M outside area (forward: a lead-out, in driving order, without edge), or
    from such a point to edge (a lead-in); [] if there is none within LEAD_MAX_M."""
    main = {f"highway.{h}" for h in S.APPROACH_HIGHWAYS}
    best, prev, counter = {edge: 0.0}, {}, 0
    heap = [(0.0, counter, edge)]
    while heap:
        cost, _, e = heapq.heappop(heap)
        if cost > best[e]:
            continue
        node = e.getToNode() if forward else e.getFromNode()
        if e is not edge and area.distance(Point(node.getCoord())) >= S.LINE_OUTSIDE_M:
            chain = [e]
            while chain[-1] is not edge:
                chain.append(prev[chain[-1]])
            return chain[::-1][1:] if forward else chain[:-1]
        if forward:
            steps = [n for n in e.getOutgoing() if can_drive(e, n, S.JEEPNEY_VCLASS)]
        else:
            steps = [n for n in e.getIncoming() if can_drive(n, e, S.JEEPNEY_VCLASS)]
        for nxt in steps:
            c = cost + nxt.getLength()
            if nxt.getType() in main and c <= S.LEAD_MAX_M and c < best.get(nxt, math.inf):
                best[nxt], prev[nxt] = c, e
                counter += 1
                heapq.heappush(heap, (c, counter, nxt))
    return []


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
    area = study_area(net)
    paths, road_edges = {}, {}
    for road in sorted(roads):
        paths[road], road_edges[road] = road_paths(net, road, area)
        print(f"{road}: directions {', '.join(paths[road])}")
    lines = {}
    for key, spec in S.JEEPNEY_LINES.items():
        for road, d in spec:
            if d not in paths[road]:
                raise SystemExit(f"JEEPNEY_LINES['{key}']: {road} runs {' or '.join(paths[road])}, not {d}")
        back = [(road, OPPOSITE[d]) for road, d in reversed(spec)]
        for drive, first_whole in ((spec, True), (back, False)):
            service = line_route(paths, drive, first_whole)
            # in from outside and out again: a lead where the roads end too close to the area
            out_by = [area.distance(Point(p)) for p in (service[0].getFromNode().getCoord(), service[-1].getToNode().getCoord())]
            lead_in = lead(area, service[0], forward=False) if out_by[0] < S.LINE_OUTSIDE_M else []
            lead_out = lead(area, service[-1], forward=True) if out_by[1] < S.LINE_OUTSIDE_M else []
            route = lead_in + service + lead_out
            for a, b in zip(route, route[1:]):
                if not can_drive(a, b, S.JEEPNEY_VCLASS):
                    raise SystemExit(f"Broken route for {drive}: {a.getID()} -> {b.getID()}")
            inside = [e for e in route if area.contains(Point(midpoint(e)))]
            line = f"{key}_{compass(inside[0].getFromNode().getCoord(), inside[-1].getToNode().getCoord())}"
            if line in lines:
                raise SystemExit(f"Two lines named {line}")
            on_roads = set().union(*(road_edges[road] for road, _ in drive))
            length = sum(e.getLength() for e in route)
            on = sum(e.getLength() for e in service if e in on_roads) / sum(e.getLength() for e in service)
            start, end = route[0].getFromNode().getCoord(), route[-1].getToNode().getCoord()
            lon0, lat0 = net.convertXY2LonLat(*start)
            lon1, lat1 = net.convertXY2LonLat(*end)
            lines[line] = {
                "key": key, "roads": [road for road, _ in drive], "highway": " + ".join(road for road, _ in drive),
                "edges": [e.getID() for e in route],
                "length_m": round(length, 1), "inside_m": round(sum(e.getLength() for e in inside), 1),
                "lead_in_m": round(sum(e.getLength() for e in lead_in), 1),
                "lead_out_m": round(sum(e.getLength() for e in lead_out), 1), "on_highway_share": round(on, 3),
                "freeflow_s": round(sum(e.getLength() / min(e.getSpeed(), S.JEEPNEY["maxSpeed"]) for e in route)),
                "from_lat_lon": [round(lat0, 5), round(lon0, 5)], "to_lat_lon": [round(lat1, 5), round(lon1, 5)],
                "starts_outside_m": round(area.distance(Point(start))), "ends_outside_m": round(area.distance(Point(end))),
            }
            r = lines[line]
            print(f"{line:11s} {' -> '.join(f'{rd} {d}' for rd, d in drive)}: {len(route)} edges, {length / 1000:.2f} km "
                  f"({r['inside_m'] / 1000:.2f} km inside the area; lead-in {r['lead_in_m']:.0f} m, lead-out "
                  f"{r['lead_out_m']:.0f} m), starts {r['starts_outside_m']} m and ends {r['ends_outside_m']} m outside, "
                  f"{100 * on:.0f}% on its roads, free-flow {r['freeflow_s'] / 60:.1f} min")
            if min(r["starts_outside_m"], r["ends_outside_m"]) < S.LINE_OUTSIDE_M:
                print(f"  note: no main road leads {S.LINE_OUTSIDE_M:.0f} m out of the area at its "
                      + ("start" if r["starts_outside_m"] < S.LINE_OUTSIDE_M else "end"))

    (S.GENERATED / "jeepney_routes.json").write_text(json.dumps(lines, indent=2) + "\n", encoding="utf-8")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           "<!-- Generated by make_routes.py. Do not edit: rerun it. One jeepney per line every "
           f"JEEPNEY_HEADWAY_S ({S.JEEPNEY_HEADWAY_S} s), named as a flow would name it. -->", "<routes>"]
    xml += [f'    <route id="r_{line}" edges="{" ".join(r["edges"])}"/>' for line, r in lines.items()]
    departures = sorted((k * S.JEEPNEY_HEADWAY_S, line, k) for line in lines
                        for k in range(math.ceil(S.END_S / S.JEEPNEY_HEADWAY_S)))
    xml += [f'    <vehicle id="{line}.{k}" type="jeepney" route="r_{line}" line="{line}" depart="{depart}" '
            'departLane="best"/>' for depart, line, k in departures]
    xml.append("</routes>")
    (S.GENERATED / "jeepney.rou.xml").write_text("\n".join(xml) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} lines and {len(departures)} jeepneys to generated/jeepney_routes.json and "
          "generated/jeepney.rou.xml")


if __name__ == "__main__":
    main()
