"""Jeepney lines along the two main highways, both directions.

For each highway in settings.HIGHWAYS: take the network edges carrying its name, find its two
far ends, and route a jeepney from one end to the other and back. The routing prefers the
highway itself (every metre off it costs OFF_HIGHWAY_PENALTY metres), so the route follows the
highway and only leaves it where the network forces a short detour. Lines are named by the
compass direction they travel, e.g. N1_SE.

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
    """The two points of the highway farthest apart (edge midpoints)."""
    mids = [midpoint(e) for e in edges]
    _, i, j = max((math.dist(a, b), i, j) for i, a in enumerate(mids) for j, b in enumerate(mids) if i < j)
    return mids[i], mids[j]


def end_edges(edges, p_from, p_to):
    """First and last highway edge for travel from p_from towards p_to: the edge starting
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


def main():
    S.GENERATED.mkdir(exist_ok=True)
    if not S.NET_FILE.is_file():
        raise SystemExit(f"No network at {S.NET_FILE}: build it in ../sumo_track first "
                         "(python -m sumo_track.build --all)")
    net = sumolib.net.readNet(str(S.NET_FILE))
    lines = {}
    for prefix, name in S.HIGHWAYS.items():
        edges = [e for e in net.getEdges() if norm(e.getName()) == norm(name) and e.allows(S.JEEPNEY_VCLASS)]
        if not edges:
            raise SystemExit(f"No edges named '{name}' that allow {S.JEEPNEY_VCLASS}")
        a, b = far_ends(edges)
        for p_from, p_to in ((a, b), (b, a)):
            first, last = end_edges(edges, p_from, p_to)
            path = preferred_path(first, last, set(edges), S.JEEPNEY_VCLASS, S.OFF_HIGHWAY_PENALTY)
            if path is None:
                raise SystemExit(f"No {S.JEEPNEY_VCLASS} route along {name} from {first.getID()} to {last.getID()}")
            line = f"{prefix}_{compass(p_from, p_to)}"
            length = sum(e.getLength() for e in path)
            on_highway = sum(e.getLength() for e in path if e in set(edges))
            lon0, lat0 = net.convertXY2LonLat(*first.getFromNode().getCoord())
            lon1, lat1 = net.convertXY2LonLat(*last.getToNode().getCoord())
            lines[line] = {
                "highway": name, "edges": [e.getID() for e in path],
                "length_m": round(length, 1), "on_highway_share": round(on_highway / length, 3),
                "freeflow_s": round(sum(e.getLength() / min(e.getSpeed(), S.JEEPNEY["maxSpeed"]) for e in path)),
                "from_lat_lon": [round(lat0, 5), round(lon0, 5)], "to_lat_lon": [round(lat1, 5), round(lon1, 5)],
            }
            print(f"{line:7s} {name}: {len(path)} edges, {length / 1000:.2f} km, "
                  f"{100 * on_highway / length:.0f}% on the highway, free-flow "
                  f"{lines[line]['freeflow_s'] / 60:.1f} min, from {lat0:.5f},{lon0:.5f} to {lat1:.5f},{lon1:.5f}")

    (S.GENERATED / "jeepney_routes.json").write_text(json.dumps(lines, indent=2) + "\n", encoding="utf-8")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           "<!-- Generated by make_routes.py. Do not edit: rerun it. -->", "<routes>"]
    xml += [f'    <route id="r_{line}" edges="{" ".join(r["edges"])}"/>' for line, r in lines.items()]
    xml += [f'    <flow id="{line}" type="jeepney" route="r_{line}" line="{line}" begin="0" '
            f'end="{S.END_S}" period="{S.JEEPNEY_HEADWAY_S}" departLane="best"/>' for line in lines]
    xml.append("</routes>")
    (S.GENERATED / "jeepney.rou.xml").write_text("\n".join(xml) + "\n", encoding="utf-8")
    print("Wrote generated/jeepney_routes.json and generated/jeepney.rou.xml")


if __name__ == "__main__":
    main()
