"""Phase 4 in miniature: build the candidate alternative for a jeepney trip.

Runs OFFLINE on an unloaded (free-flow) network, exactly as the manuscript
requires, so every agent gets a consistent baseline. Writes the alternative
in the Part D.4 schema; run_traci.py compares outcomes against it.
"""
import json
import os
import sys

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import sumolib

HERE = os.path.dirname(os.path.abspath(__file__))
net = sumolib.net.readNet(os.path.join(HERE, "dis.net.xml"))

# ---- params.yaml stand-ins ------------------------------------------------
PEDESTRIAN_SPEED = 1.34        # m/s
HEADWAY_S = 300.0              # s   headway[JEEP_R01_EB][peak]
DWELL_ALLOWANCE_S = 6.0        # s   same assumption the controller uses
FARE_BASE_PHP = 13.0           # PHP  LTFRB base fare      (placeholder!)
FARE_BASE_KM = 4.0             # km   distance covered by base fare
FARE_PER_KM_PHP = 1.80         # PHP/km beyond the base    (placeholder!)

ROUTE_EDGES = ["AB", "BC", "CD"]


def freeflow_time(edge_id, from_pos, to_pos):
    """Seconds to cover [from_pos, to_pos] on an empty edge."""
    e = net.getEdge(edge_id)
    return (to_pos - from_pos) / e.getSpeed()


def segment(board_edge, board_pos, alight_edge, alight_pos):
    """Free-flow time and distance along the route between two points."""
    i, j = ROUTE_EDGES.index(board_edge), ROUTE_EDGES.index(alight_edge)
    if i > j or (i == j and board_pos >= alight_pos):
        raise ValueError("alighting point is not after boarding point")
    t = d = 0.0
    for k in range(i, j + 1):
        eid = net.getEdge(ROUTE_EDGES[k])
        start = board_pos if k == i else 0.0
        end = alight_pos if k == j else eid.getLength()
        t += freeflow_time(ROUTE_EDGES[k], start, end)
        d += end - start
    return t, d


def fare(distance_m):
    km = distance_m / 1000.0
    extra = max(0.0, km - FARE_BASE_KM)
    return round(FARE_BASE_PHP + extra * FARE_PER_KM_PHP, 2)


def build_alternative(trip_id, walk_m_access, board, alight, walk_m_egress):
    in_veh_s, dist_m = segment(*board, *alight)
    access_s = walk_m_access / PEDESTRIAN_SPEED
    wait_s = HEADWAY_S / 2.0                      # expected wait = headway/2
    egress_s = walk_m_egress / PEDESTRIAN_SPEED
    total = access_s + wait_s + in_veh_s + DWELL_ALLOWANCE_S + egress_s
    return {
        "trip_id": trip_id,
        "mode": "jeepney",
        "route_id": "JEEP_R01_EB",
        "feasible": True,
        "distance_m": round(dist_m, 1),
        "cost_php": fare(dist_m),
        "estimated_total_time_s": round(total, 1),
        "access_time_s": round(access_s, 1),
        "waiting_time_s": round(wait_s, 1),
        "in_vehicle_time_s": round(in_veh_s, 1),
        "dwell_time_s": DWELL_ALLOWANCE_S,
        "egress_time_s": round(egress_s, 1),
        "boarding": {"edge_id": board[0], "pos_m": board[1]},
        "alighting": {"edge_id": alight[0], "pos_m": alight[1]},
    }


alts = {}
for trip_id in ["c_normal", "c_disrupted"]:
    alts[trip_id] = build_alternative(
        trip_id,
        walk_m_access=50.0,                 # 150 m -> 200 m on AB
        board=("AB", 200.0),
        alight=("CD", 300.0),
        walk_m_egress=100.0,                # 300 m -> 400 m on CD
    )

out = os.path.join(HERE, "alternatives.json")
json.dump(alts, open(out, "w"), indent=2)

a = alts["c_normal"]
print("Free-flow estimate for both trips (identical by construction):")
for k in ["distance_m", "cost_php", "access_time_s", "waiting_time_s",
          "in_vehicle_time_s", "dwell_time_s", "egress_time_s",
          "estimated_total_time_s"]:
    print(f"  {k:24} {a[k]}")
check = (a["access_time_s"] + a["waiting_time_s"] + a["in_vehicle_time_s"]
         + a["dwell_time_s"] + a["egress_time_s"])
print(f"\n  components sum to {check:.1f} s -> matches total: "
      f"{abs(check - a['estimated_total_time_s']) < 0.2}")
