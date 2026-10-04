"""Phase 4 estimation, packaged as a module the pipeline can call.

Same logic as example 08, but importable and driven by an agent record
instead of a hardcoded trip list.
"""
import os
import sys

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci
import sumolib
from sumolib import geomhelper

HERE = os.path.dirname(os.path.abspath(__file__))

P = {
    "pedestrian_speed":           1.34,
    "walking_distance_limit":     1500.0,
    "walking_tolerance_to_route":  300.0,
    "bike_distance_limit":        5000.0,
    "car_access_time":            120.0,
    "car_parking_time":           180.0,
    "car_operating_cost_per_km":    8.50,
    "headway_s":                  300.0,
    "dwell_allowance_s":            6.0,
    "fare_base_php":               13.00,
    "fare_base_km":                 4.0,
    "fare_per_km_php":              1.80,
}

JEEPNEY_ROUTES = {
    "JEEP_R01_EB": ["A1B1", "B1C1", "C1D1"],
    "JEEP_R01_WB": ["D1C1", "C1B1", "B1A1"],
    "JEEP_R02_NB": ["B0B1", "B1B2", "B2B3"],
    "JEEP_R02_SB": ["B3B2", "B2B1", "B1B0"],
}

net = sumolib.net.readNet(os.path.join(HERE, "city.net.xml"))


def snap(x, y, vclass="pedestrian", radius=60.0):
    cands = [(e, d) for e, d in net.getNeighboringEdges(x, y, radius)
             if e.allows(vclass)]
    if not cands:
        return None
    e, d = min(cands, key=lambda t: t[1])
    off, _ = geomhelper.polygonOffsetAndDistanceToPoint((x, y), e.getShape())
    return e.getID(), off, d


def nearest_on_route(x, y, route_edges):
    best, cumulative = None, 0.0
    for eid in route_edges:
        e = net.getEdge(eid)
        off, dist = geomhelper.polygonOffsetAndDistanceToPoint((x, y), e.getShape())
        if best is None or dist < best[2]:
            best = (eid, off, dist, cumulative + off)
        cumulative += e.getLength()
    return best


def route_segment(route_edges, b_eid, b_pos, a_eid, a_pos):
    i, j = route_edges.index(b_eid), route_edges.index(a_eid)
    t = d = 0.0
    for k in range(i, j + 1):
        e = net.getEdge(route_edges[k])
        start = b_pos if k == i else 0.0
        end = a_pos if k == j else e.getLength()
        seg = max(0.0, end - start)
        t += seg / e.getSpeed()
        d += seg
    return t, d


def fare(distance_m):
    km = distance_m / 1000.0
    return round(P["fare_base_php"]
                 + max(0.0, km - P["fare_base_km"]) * P["fare_per_km_php"], 2)


def alternatives_for(trip, agent):
    """Every feasible alternative for one trip. Infeasible modes are removed."""
    o = snap(*trip["origin_xy"])
    d = snap(*trip["dest_xy"])
    if o is None or d is None:
        return [], [["all", "origin or destination could not be snapped"]]

    alts, excluded = [], []
    base = {"trip_id": trip["trip_id"], "feasible": True}

    # --- walk: distance from the router, TIME computed ourselves -----------
    stages = traci.simulation.findIntermodalRoute(
        o[0], d[0], modes="", pType="pedestrian", departPos=o[1], arrivalPos=d[1])
    if not stages:
        excluded.append(["walk", "no pedestrian route"])
    elif stages[0].length > P["walking_distance_limit"]:
        excluded.append(["walk", "distance %.0f m > walking_distance_limit"
                         % stages[0].length])
    else:
        secs = stages[0].length / P["pedestrian_speed"]
        alts.append(dict(base, mode="walk", route_id=None,
                         distance_m=round(stages[0].length, 1), cost_php=0.0,
                         access_time_s=0.0, waiting_time_s=0.0,
                         in_vehicle_time_s=round(secs, 1), dwell_time_s=0.0,
                         egress_time_s=0.0, estimated_total_time_s=round(secs, 1)))

    # --- bike ---------------------------------------------------------------
    if not agent["has_bike"]:
        excluded.append(["bike", "agent has no bike"])
    else:
        r = traci.simulation.findRoute(o[0], d[0], vType="bike")
        if not r.edges:
            excluded.append(["bike", "no bicycle route"])
        elif r.length > P["bike_distance_limit"]:
            excluded.append(["bike", "beyond bike_distance_limit"])
        else:
            alts.append(dict(base, mode="bike", route_id=None,
                             distance_m=round(r.length, 1), cost_php=0.0,
                             access_time_s=0.0, waiting_time_s=0.0,
                             in_vehicle_time_s=round(r.travelTime, 1),
                             dwell_time_s=0.0, egress_time_s=0.0,
                             estimated_total_time_s=round(r.travelTime, 1)))

    # --- car ----------------------------------------------------------------
    if not agent["has_car"]:
        excluded.append(["car", "agent has no car access"])
    else:
        r = traci.simulation.findRoute(o[0], d[0], vType="car")
        if not r.edges:
            excluded.append(["car", "no passenger route"])
        else:
            total = P["car_access_time"] + r.travelTime + P["car_parking_time"]
            alts.append(dict(base, mode="car", route_id=None,
                             distance_m=round(r.length, 1),
                             cost_php=round(r.length / 1000.0
                                            * P["car_operating_cost_per_km"], 2),
                             access_time_s=P["car_access_time"], waiting_time_s=0.0,
                             in_vehicle_time_s=round(r.travelTime, 1),
                             dwell_time_s=0.0, egress_time_s=P["car_parking_time"],
                             estimated_total_time_s=round(total, 1)))

    # --- jeepney: one alternative per serving route --------------------------
    ox, oy = trip["origin_xy"]
    dx, dy = trip["dest_xy"]
    for route_id, edges in JEEPNEY_ROUTES.items():
        b_eid, b_pos, b_dist, b_off = nearest_on_route(ox, oy, edges)
        a_eid, a_pos, a_dist, a_off = nearest_on_route(dx, dy, edges)
        if b_dist > P["walking_tolerance_to_route"]:
            excluded.append(["jeepney/" + route_id, "origin %.0f m from route" % b_dist])
            continue
        if a_dist > P["walking_tolerance_to_route"]:
            excluded.append(["jeepney/" + route_id,
                             "destination %.0f m from route" % a_dist])
            continue
        if b_off >= a_off:
            excluded.append(["jeepney/" + route_id, "wrong direction"])
            continue
        in_veh, dist = route_segment(edges, b_eid, b_pos, a_eid, a_pos)
        access = b_dist / P["pedestrian_speed"]
        wait = P["headway_s"] / 2.0
        egress = a_dist / P["pedestrian_speed"]
        alts.append(dict(base, mode="jeepney", route_id=route_id,
                         distance_m=round(dist, 1), cost_php=fare(dist),
                         access_time_s=round(access, 1), waiting_time_s=wait,
                         in_vehicle_time_s=round(in_veh, 1),
                         dwell_time_s=P["dwell_allowance_s"],
                         egress_time_s=round(egress, 1),
                         estimated_total_time_s=round(
                             access + wait + in_veh + P["dwell_allowance_s"] + egress, 1),
                         boarding={"edge_id": b_eid, "pos_m": round(b_pos, 1)},
                         alighting={"edge_id": a_eid, "pos_m": round(a_pos, 1)}))

    return alts, excluded
