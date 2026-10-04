"""Verify a jeepney route against the network BEFORE running.

Phase 3 task: "check that every edge on the matched routes allows that
vClass", plus route continuity. sumolib reads the .net.xml offline - no
simulation needed. This is the seed of network/verify.py.
"""
import os
import sys

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import sumolib

HERE = os.path.dirname(os.path.abspath(__file__))
net = sumolib.net.readNet(os.path.join(HERE, "jeep.net.xml"))

ROUTES = {
    "JEEP_R01_EB": ["AB", "BC"],
    "JEEP_R01_WB": ["CB", "BA"],
    "BROKEN_DEMO": ["AB", "BA"],   # deliberately invalid: no connection B->B
}
VCLASS = "bus"

for route_id, edge_ids in ROUTES.items():
    problems = []
    total_m = 0.0

    for eid in edge_ids:
        edge = net.getEdge(eid)
        total_m += edge.getLength()
        if not edge.allows(VCLASS):
            problems.append(f"edge {eid} does not allow vClass '{VCLASS}'")

    # continuity: each edge must actually connect to the next one
    for a, b in zip(edge_ids, edge_ids[1:]):
        if net.getEdge(b) not in net.getEdge(a).getOutgoing():
            problems.append(f"no connection from {a} to {b}")

    status = "FAIL" if problems else "OK"
    print(f"[{status:4}] {route_id:14} {len(edge_ids)} edges, {total_m:7.1f} m")
    for p in problems:
        print(f"         - {p}")
