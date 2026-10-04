"""Phase 4: build the candidate alternatives the LLM will choose between.

For each trip, produce one record per FEASIBLE mode - walk, bike, car, and
one per serving jeepney route. Infeasible modes are removed entirely and
never shown to the LLM (manuscript 4.4.1 / 4.3.6).

Estimates use the UNLOADED (free-flow) network for a consistent baseline.
"""
import json
import os
import sys

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci
import sumolib
from sumolib import geomhelper

HERE = os.path.dirname(os.path.abspath(__file__))

# ===== params.yaml stand-ins (every one status: placeholder) ===============
P = {
    "pedestrian_speed":           1.34,    # m/s
    "walking_distance_limit":     1500.0,  # m   beyond this, walking infeasible
    "walking_tolerance_to_route":  300.0,  # m   max walk to reach a jeepney route
    "bike_speed":                 5.56,    # m/s
    "bike_distance_limit":        5000.0,  # m
    "car_access_time":            120.0,   # s   getting to and starting the car
    "car_parking_time":           180.0,   # s   finding parking at destination
    "car_operating_cost_per_km":    8.50,  # PHP/km
    "headway_s":                  300.0,   # s   per route per period
    "dwell_allowance_s":            6.0,   # s
    "fare_base_php":               13.00,  # PHP
    "fare_base_km":                 4.0,   # km
    "fare_per_km_php":              1.80,  # PHP/km beyond base
}

# ===== placeholder jeepney routes (Phase 3 would produce these) ============
JEEPNEY_ROUTES = {
    "JEEP_R01_EB": ["A1B1", "B1C1", "C1D1"],            # row 1, west -> east
    "JEEP_R02_NB": ["B0B1", "B1B2", "B2B3"],            # column B, south -> north
    "JEEP_R03_EB": ["A1B1", "B1C1", "C1C2", "C2C3"],    # row 1, then north at C
}

net = sumolib.net.readNet(os.path.join(HERE, "city.net.xml"))


# ----------------------------------------------------------------- helpers
def snap(x, y, vclass="pedestrian", radius=60.0):
    """Nearest edge allowing vclass. Returns (edge_id, pos_m, dist_m)."""
    cands = [(e, d) for e, d in net.getNeighboringEdges(x, y, radius)
             if e.allows(vclass)]
    if not cands:
        return None
    e, d = min(cands, key=lambda t: t[1])
    off, _ = geomhelper.polygonOffsetAndDistanceToPoint((x, y), e.getShape())
    return e.getID(), off, d


def nearest_on_route(x, y, route_edges):
    """Closest point on a jeepney route to (x, y).

    Returns (edge_id, pos_m, dist_to_route_m, offset_along_whole_route_m).
    That last value is what lets us check boarding comes BEFORE alighting.
    """
    best = None
    cumulative = 0.0
    for eid in route_edges:
        e = net.getEdge(eid)
        off, dist = geomhelper.polygonOffsetAndDistanceToPoint((x, y), e.getShape())
        if best is None or dist < best[2]:
            best = (eid, off, dist, cumulative + off)
        cumulative += e.getLength()
    return best


def route_segment_time(route_edges, board_eid, board_pos, alight_eid, alight_pos):
    """Free-flow seconds and metres between two points along a route."""
    i, j = route_edges.index(board_eid), route_edges.index(alight_eid)
    t = d = 0.0
    for k in range(i, j + 1):
        e = net.getEdge(route_edges[k])
        start = board_pos if k == i else 0.0
        end = alight_pos if k == j else e.getLength()
        seg = max(0.0, end - start)
        t += seg / e.getSpeed()
        d += seg
    return t, d


def fare(distance_m):
    km = distance_m / 1000.0
    return round(P["fare_base_php"]
                 + max(0.0, km - P["fare_base_km"]) * P["fare_per_km_php"], 2)


# ------------------------------------------------------------------- modes
def walk_alt(trip, o_edge, d_edge, o_pos=0.0, d_pos=0.0):
    # NOTE: sumolib's getShortestPath(vClass="pedestrian") returns None on a
    # network with walkingareas, so pedestrian routing must go through TraCI.
    stages = traci.simulation.findIntermodalRoute(
        o_edge, d_edge, modes="", pType="pedestrian",
        departPos=o_pos, arrivalPos=d_pos)
    if not stages:
        return None, "no pedestrian route"
    s = stages[0]
    if s.length > P["walking_distance_limit"]:
        return None, "distance %.0f m > walking_distance_limit" % s.length
    # Take the DISTANCE from the router but compute the TIME ourselves.
    # The router's travelTime implies 1.6-1.7 m/s regardless of the speed we
    # pass it; the manuscript specifies time = distance / pedestrian_speed.
    walk_s = s.length / P["pedestrian_speed"]
    return {
        "mode": "walk", "route_id": None, "feasible": True,
        "distance_m": round(s.length, 1), "cost_php": 0.0,
        "access_time_s": 0.0, "waiting_time_s": 0.0,
        "in_vehicle_time_s": round(walk_s, 1),
        "dwell_time_s": 0.0, "egress_time_s": 0.0,
        "estimated_total_time_s": round(walk_s, 1),
    }, None


def bike_alt(trip, o_edge, d_edge):
    if not trip["has_bike"]:
        return None, "agent has no bike"
    r = traci.simulation.findRoute(o_edge, d_edge, vType="bike")
    if not r.edges:
        return None, "no bicycle route"
    if r.length > P["bike_distance_limit"]:
        return None, "distance %.0f m > bike_distance_limit" % r.length
    return {
        "mode": "bike", "route_id": None, "feasible": True,
        "distance_m": round(r.length, 1), "cost_php": 0.0,
        "access_time_s": 0.0, "waiting_time_s": 0.0,
        "in_vehicle_time_s": round(r.travelTime, 1),
        "dwell_time_s": 0.0, "egress_time_s": 0.0,
        "estimated_total_time_s": round(r.travelTime, 1),
    }, None


def car_alt(trip, o_edge, d_edge):
    if not trip["has_car"]:
        return None, "agent has no car access"
    r = traci.simulation.findRoute(o_edge, d_edge, vType="car")
    if not r.edges:
        return None, "no passenger route"
    total = P["car_access_time"] + r.travelTime + P["car_parking_time"]
    return {
        "mode": "car", "route_id": None, "feasible": True,
        "distance_m": round(r.length, 1),
        "cost_php": round(r.length / 1000.0 * P["car_operating_cost_per_km"], 2),
        "access_time_s": P["car_access_time"], "waiting_time_s": 0.0,
        "in_vehicle_time_s": round(r.travelTime, 1),
        "dwell_time_s": 0.0, "egress_time_s": P["car_parking_time"],
        "estimated_total_time_s": round(total, 1),
    }, None


def jeepney_alts(trip):
    """One alternative per serving route. Rejects wrong-direction routes."""
    out, rejected = [], []
    ox, oy = trip["origin_xy"]
    dx, dy = trip["dest_xy"]
    for route_id, edges in JEEPNEY_ROUTES.items():
        b_eid, b_pos, b_dist, b_off = nearest_on_route(ox, oy, edges)
        a_eid, a_pos, a_dist, a_off = nearest_on_route(dx, dy, edges)

        if b_dist > P["walking_tolerance_to_route"]:
            rejected.append((route_id, "origin %.0f m from route" % b_dist))
            continue
        if a_dist > P["walking_tolerance_to_route"]:
            rejected.append((route_id, "destination %.0f m from route" % a_dist))
            continue
        if b_off >= a_off:
            rejected.append((route_id, "alighting point is not after boarding point"))
            continue

        in_veh, dist = route_segment_time(edges, b_eid, b_pos, a_eid, a_pos)
        access = b_dist / P["pedestrian_speed"]
        wait = P["headway_s"] / 2.0
        egress = a_dist / P["pedestrian_speed"]
        total = access + wait + in_veh + P["dwell_allowance_s"] + egress
        out.append({
            "mode": "jeepney", "route_id": route_id, "feasible": True,
            "distance_m": round(dist, 1), "cost_php": fare(dist),
            "access_time_s": round(access, 1), "waiting_time_s": wait,
            "in_vehicle_time_s": round(in_veh, 1),
            "dwell_time_s": P["dwell_allowance_s"],
            "egress_time_s": round(egress, 1),
            "estimated_total_time_s": round(total, 1),
            "boarding": {"edge_id": b_eid, "pos_m": round(b_pos, 1)},
            "alighting": {"edge_id": a_eid, "pos_m": round(a_pos, 1)},
        })
    return out, rejected


def build(trip):
    """All feasible alternatives for one trip, plus why each mode was excluded."""
    o = snap(*trip["origin_xy"])
    d = snap(*trip["dest_xy"])
    if o is None or d is None:
        return {"trip_id": trip["trip_id"], "alternatives": [],
                "excluded": [["all", "origin or destination could not be snapped"]]}

    alts, excluded = [], []
    a, why = walk_alt(trip, o[0], d[0], o_pos=o[1], d_pos=d[1])
    alts.append(a) if a else excluded.append(["walk", why])
    for fn, name in ((bike_alt, "bike"), (car_alt, "car")):
        a, why = fn(trip, o[0], d[0])
        if a:
            alts.append(a)
        else:
            excluded.append([name, why])

    j, rej = jeepney_alts(trip)
    alts.extend(j)
    excluded.extend([["jeepney/" + r, w] for r, w in rej])

    for a in alts:
        a["trip_id"] = trip["trip_id"]
    return {"trip_id": trip["trip_id"], "alternatives": alts, "excluded": excluded}


# -------------------------------------------------------------------- main
TRIPS = [
    # short hop - walking should be feasible
    {"trip_id": "T1_short", "origin_xy": (60, 300), "dest_xy": (500, 300),
     "has_car": True, "has_bike": True},
    # long crosstown, no car access - car must be absent
    {"trip_id": "T2_nocar", "origin_xy": (60, 300), "dest_xy": (880, 880),
     "has_car": False, "has_bike": True},
    # served by TWO jeepney routes (R01 and R03 share the row-1 corridor)
    {"trip_id": "T3_two_routes", "origin_xy": (60, 300), "dest_xy": (580, 300),
     "has_car": True, "has_bike": True},
    # far from every jeepney route - jeepney must be absent
    {"trip_id": "T4_nojeep", "origin_xy": (880, 880), "dest_xy": (880, 620),
     "has_car": True, "has_bike": True},
    # wrong direction along the route - board after alight
    {"trip_id": "T5_wrongdir", "origin_xy": (580, 300), "dest_xy": (60, 300),
     "has_car": False, "has_bike": False},
]


def main():
    traci.start(["sumo", "-c", os.path.join(HERE, "city.sumocfg"), "--no-step-log"])
    results = {t["trip_id"]: build(t) for t in TRIPS}
    traci.close()

    json.dump(results, open(os.path.join(HERE, "alternatives.json"), "w"), indent=2)

    for tid, r in results.items():
        print("\n" + "=" * 72)
        print(tid)
        print("-" * 72)
        if r["alternatives"]:
            print("  %-9s %-13s %9s %9s %9s" %
                  ("mode", "route", "dist_m", "cost_PHP", "total_s"))
            for a in sorted(r["alternatives"], key=lambda x: x["estimated_total_time_s"]):
                print("  %-9s %-13s %9.0f %9.2f %9.0f" %
                      (a["mode"], a["route_id"] or "-", a["distance_m"],
                       a["cost_php"], a["estimated_total_time_s"]))
        else:
            print("  (no feasible alternatives)")
        for name, why in r["excluded"]:
            print("  EXCLUDED %-18s %s" % (name, why))


if __name__ == "__main__":
    main()
