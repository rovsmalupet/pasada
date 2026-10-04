"""Capacity, full pass-bys, grouped boarding and dwell scaling.

Phase 7 controller, closer to the manuscript:
  - group waiting commuters by flagging position
  - dwell = n_board * t_board + n_alight * t_alight
  - if FULL and only boarders wait -> skip the stop, log full_pass_by
"""
import os
import sys
from collections import defaultdict

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci
import traci.constants as tc
import sumolib

HERE = os.path.dirname(os.path.abspath(__file__))
SUMO_BINARY = "sumo-gui"

# ---- params.yaml stand-ins -------------------------------------------------
POLLING_INTERVAL = 1
LOOKAHEAD_M = 150.0
BOARDING_TIME_PER_PAX = 3.0      # s   (matches vType boardingDuration)
ALIGHTING_TIME_PER_PAX = 2.0     # s

REGISTRY = {
    "c1": {"line": "JEEP_R01_EB", "board_edge": "AB", "board_pos": 200.0,
           "alight_edge": "BC", "alight_pos": 300.0},
    "c2": {"line": "JEEP_R01_EB", "board_edge": "AB", "board_pos": 200.0,
           "alight_edge": "BC", "alight_pos": 300.0},
    "c3": {"line": "JEEP_R01_EB", "board_edge": "AB", "board_pos": 200.0,
           "alight_edge": "BC", "alight_pos": 300.0},
    "c4": {"line": "JEEP_R01_EB", "board_edge": "AB", "board_pos": 200.0,
           "alight_edge": "BC", "alight_pos": 300.0},
    "c5": {"line": "JEEP_R01_EB", "board_edge": "AB", "board_pos": 420.0,
           "alight_edge": "BC", "alight_pos": 450.0},
}

net = sumolib.net.readNet(os.path.join(HERE, "cap.net.xml"))


def bus_lane_index(edge_id, vclass="bus"):
    for lane in net.getEdge(edge_id).getLanes():
        if lane.allows(vclass):
            return lane.getIndex()
    return 0


traci.start([SUMO_BINARY, "-c", os.path.join(HERE, "cap.sumocfg"), "--no-step-log"])

events, requested, last_state = [], set(), {}


def log(t, event, **kw):
    events.append({"t": t, "event": event, **kw})
    print(f"t={t:>5}s  {event:<15} " + "  ".join(f"{k}={v}" for k, v in kw.items()))


t = 0
while traci.simulation.getMinExpectedNumber() > 0:
    traci.simulationStep()
    t += 1
    if t % POLLING_INTERVAL != 0:
        continue

    # ---- 1. commuter state ------------------------------------------------
    waiting, riding = {}, {}
    for pid in traci.person.getIDList():
        stage = traci.person.getStage(pid).type
        veh = traci.person.getVehicle(pid)
        if stage == tc.STAGE_DRIVING and veh == "":
            waiting[pid] = True
            state = "waiting_to_flag"
        elif veh:
            riding[pid] = veh
            state = "riding"
        elif stage == tc.STAGE_WALKING:
            state = "walking"
        else:
            state = f"stage_{stage}"
        if last_state.get(pid) != state:
            log(t, "state_change", agent=pid, to=state, vehicle=veh or "-")
            last_state[pid] = state

    # ---- 2. controller ----------------------------------------------------
    for vid in traci.vehicle.getIDList():
        line, edge = traci.vehicle.getLine(vid), traci.vehicle.getRoadID(vid)
        if edge.startswith(":"):
            continue
        pos = traci.personCapacitypersonCapacityvehicle.getLanePosition(vid)
        occ = traci.vehicle.getPersonNumber(vid)
        cap = traci.vehicle.getPersonCapacity(vid)

        # group everyone who wants this vehicle to stop, BY POSITION
        boarders = defaultdict(list)
        alighters = defaultdict(list)
        for agent_id, r in REGISTRY.items():
            if r["line"] != line:
                continue
            if agent_id in waiting and edge == r["board_edge"]:
                if 0 < r["board_pos"] - pos <= LOOKAHEAD_M:
                    boarders[r["board_pos"]].append(agent_id)
            if riding.get(agent_id) == vid and edge == r["alight_edge"]:
                if 0 < r["alight_pos"] - pos <= LOOKAHEAD_M:
                    alighters[r["alight_pos"]].append(agent_id)

        for stop_pos in sorted(set(boarders) | set(alighters)):
            key = (vid, edge, stop_pos)
            if key in requested:
                continue
            n_board = len(boarders.get(stop_pos, []))
            n_alight = len(alighters.get(stop_pos, []))
            seats_free = cap - occ

            # FULL and nobody getting off here -> do not stop
            if n_alight == 0 and seats_free <= 0:
                log(t, "full_pass_by", vehicle=vid, pos=stop_pos,
                    occupancy=f"{occ}/{cap}", left_behind=",".join(boarders[stop_pos]))
                requested.add(key)
                continue

            dwell = n_board * BOARDING_TIME_PER_PAX + n_alight * ALIGHTING_TIME_PER_PAX
            traci.vehicle.setStop(vid, edge, pos=stop_pos,
                                  laneIndex=bus_lane_index(edge), duration=dwell)
            requested.add(key)
            log(t, "stop_requested", vehicle=vid, pos=stop_pos,
                board=n_board, alight=n_alight, dwell_s=dwell,
                seats_free=seats_free)

traci.close()
print(f"\nSimulation ended at t={t} s.  {len(events)} events logged.")
