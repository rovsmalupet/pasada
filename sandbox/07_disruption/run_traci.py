"""Execute the day, inject a disruption, and record trip outcomes.

Phase 8 in miniature: run -> observe -> compare against the Phase 4
estimate -> decide whether the trip was disrupted.
"""
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci
import traci.constants as tc
import sumolib

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- params.yaml stand-ins ------------------------------------------------
LOOKAHEAD_M = 150.0
BOARDING_TIME_PER_PAX = 3.0
ALIGHTING_TIME_PER_PAX = 2.0
DISRUPTION_THRESHOLD = 1.30          # actual/estimated above this = disrupted

# ---- the scenario condition (Phase 10 would load this from a YAML) --------
DISRUPTION = {"edge": "BC", "from_s": 900, "to_s": 2400,
              "reduced_speed": 2.0, "reason": "congestion_incident"}

ALTS = json.load(open(os.path.join(HERE, "alternatives.json")))
REGISTRY = {
    aid: {"line": a["route_id"],
          "board_edge": a["boarding"]["edge_id"], "board_pos": a["boarding"]["pos_m"],
          "alight_edge": a["alighting"]["edge_id"], "alight_pos": a["alighting"]["pos_m"]}
    for aid, a in ALTS.items()
}

net = sumolib.net.readNet(os.path.join(HERE, "dis.net.xml"))
BY_EDGE = defaultdict(list)
for aid, r in REGISTRY.items():
    BY_EDGE[r["board_edge"]].append(aid)


def bus_lane_index(edge_id, vclass="bus"):
    for lane in net.getEdge(edge_id).getLanes():
        if lane.allows(vclass):
            return lane.getIndex()
    return 0


#traci.start(["sumo", "-c", os.path.join(HERE, "dis.sumocfg"), "--no-step-log"])
traci.start(["sumo-gui", "-c", os.path.join(HERE, "dis.sumocfg"), "--no-step-log",
             "--start", "--delay", "30"])
requested, last_state = set(), {}
exposed = set()          # agents who were riding while the disruption was live
normal_speed = None
t = 0

while traci.simulation.getMinExpectedNumber() > 0:
    traci.simulationStep()
    t += 1

    # ---- scenario condition ------------------------------------------------
    if t == DISRUPTION["from_s"]:
        normal_speed = traci.edge.getLastStepMeanSpeed(DISRUPTION["edge"])
        for lane in net.getEdge(DISRUPTION["edge"]).getLanes():
            traci.lane.setMaxSpeed(lane.getID(), DISRUPTION["reduced_speed"])
        print(f"t={t:>5}s  DISRUPTION ON   edge={DISRUPTION['edge']} "
              f"speed -> {DISRUPTION['reduced_speed']} m/s  ({DISRUPTION['reason']})")
    if t == DISRUPTION["to_s"]:
        for lane in net.getEdge(DISRUPTION["edge"]).getLanes():
            traci.lane.setMaxSpeed(lane.getID(), net.getEdge(DISRUPTION["edge"]).getSpeed())
        print(f"t={t:>5}s  DISRUPTION OFF  edge={DISRUPTION['edge']}")

    # ---- commuter state ----------------------------------------------------
    waiting, riding = set(), {}
    for pid in traci.person.getIDList():
        stage, veh = traci.person.getStage(pid).type, traci.person.getVehicle(pid)
        if stage == tc.STAGE_DRIVING and veh == "":
            waiting.add(pid); state = "waiting_to_flag"
        elif veh:
            riding[pid] = veh; state = "riding"
            if DISRUPTION["from_s"] <= t < DISRUPTION["to_s"]:
                if traci.vehicle.getRoadID(veh) == DISRUPTION["edge"]:
                    exposed.add(pid)
        elif stage == tc.STAGE_WALKING:
            state = "walking"
        else:
            state = f"stage_{stage}"
        if last_state.get(pid) != state:
            print(f"t={t:>5}s  {pid:<12} -> {state}")
            last_state[pid] = state

    # ---- controller --------------------------------------------------------
    for vid in traci.vehicle.getIDList():
        edge = traci.vehicle.getRoadID(vid)
        if edge.startswith(":"):
            continue
        line, pos = traci.vehicle.getLine(vid), traci.vehicle.getLanePosition(vid)
        occ, cap = traci.vehicle.getPersonNumber(vid), traci.vehicle.getPersonCapacity(vid)

        stops = defaultdict(lambda: [0, 0])
        for aid, r in REGISTRY.items():
            if r["line"] != line:
                continue
            if aid in waiting and edge == r["board_edge"] \
                    and 0 < r["board_pos"] - pos <= LOOKAHEAD_M:
                stops[r["board_pos"]][0] += 1
            if riding.get(aid) == vid and edge == r["alight_edge"] \
                    and 0 < r["alight_pos"] - pos <= LOOKAHEAD_M:
                stops[r["alight_pos"]][1] += 1

        for stop_pos, (n_board, n_alight) in sorted(stops.items()):
            key = (vid, edge, stop_pos)
            if key in requested:
                continue
            seats_free = cap - occ
            if n_alight == 0 and seats_free <= 0:
                print(f"t={t:>5}s  full_pass_by {vid} @ {stop_pos}")
                requested.add(key); continue
            n_board = min(n_board, seats_free)
            dwell = n_board * BOARDING_TIME_PER_PAX + n_alight * ALIGHTING_TIME_PER_PAX
            traci.vehicle.setStop(vid, edge, pos=stop_pos,
                                  laneIndex=bus_lane_index(edge), duration=dwell)
            requested.add(key)

traci.close()

# ---- Phase 8: build the trip outcome records ------------------------------
root = ET.parse(os.path.join(HERE, "tripinfo.xml")).getroot()
outcomes = {}
for p in root.findall("personinfo"):
    aid = p.get("id")
    stages = list(p)
    access, ride, egress = stages[0], stages[1], stages[2]
    est = ALTS[aid]["estimated_total_time_s"]
    actual = float(p.get("duration"))
    ratio = actual / est
    disrupted = ratio > DISRUPTION_THRESHOLD
    outcomes[aid] = {
        "trip_id": aid,
        "realized_mode": "jeepney",
        "route_id": ride.get("vehicle"),
        "actual_departure_s": float(p.get("depart")),
        "arrival_time_s": float(p.get("depart")) + actual,
        "actual_total_time_s": actual,
        "actual_waiting_time_s": float(ride.get("waitingTime")),
        "actual_access_time_s": float(access.get("duration")),
        "actual_in_vehicle_time_s": float(ride.get("duration")),
        "actual_egress_time_s": float(egress.get("duration")),
        "distance_m": ALTS[aid]["distance_m"],
        "cost_php": ALTS[aid]["cost_php"],
        "estimated_total_time_s": est,
        "delay_ratio": round(ratio, 3),
        "disrupted": disrupted,
        "disruption_reason": DISRUPTION["reason"] if aid in exposed else None,
        "full_pass_bys_experienced": 0,
        "completed": True,
    }

json.dump(outcomes, open(os.path.join(HERE, "trip_outcomes.json"), "w"), indent=2)

print("\n" + "=" * 78)
print(f"{'trip':13} {'est':>7} {'actual':>8} {'in-veh':>8} {'ratio':>7} "
      f"{'disrupted':>10}  reason")
print("-" * 78)
for aid, o in outcomes.items():
    print(f"{aid:13} {o['estimated_total_time_s']:7.1f} {o['actual_total_time_s']:8.1f} "
          f"{o['actual_in_vehicle_time_s']:8.1f} {o['delay_ratio']:7.3f} "
          f"{str(o['disrupted']):>10}  {o['disruption_reason'] or '-'}")
