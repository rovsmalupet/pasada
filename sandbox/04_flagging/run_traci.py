"""EXPERIMENT: can a commuter flag a jeepney at an ARBITRARY roadside
position, with no bus stop defined anywhere?

This is the Phase 5-7 risk the plan says to prototype early. Prototype of
the Phase 7 controller loop.
"""
import os
import sys

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci
import traci.constants as tc
import sumolib

HERE = os.path.dirname(os.path.abspath(__file__))
SUMO_BINARY = "sumo"

# ---- params.yaml stand-ins -------------------------------------------------
POLLING_INTERVAL = 1        # controller_polling_interval (s)
LOOKAHEAD_M = 150.0         # controller_lookahead_distance (m)
DWELL_S = 10.0              # dwell allowance (s)

# ---- registry: filled by Phase 5's converter in the real system ------------
REGISTRY = {
    "commuter_1": {
        "line":        "JEEP_R01_EB",
        "board_edge":  "AB", "board_pos":  320.0,
        "alight_edge": "BC", "alight_pos": 300.0,
    },
}

net = sumolib.net.readNet(os.path.join(HERE, "flag.net.xml"))


def bus_lane_index(edge_id, vclass="bus"):
    """Lane a jeepney may stop on. NOT 0 - the sidewalk took index 0."""
    for lane in net.getEdge(edge_id).getLanes():
        if lane.allows(vclass):
            return lane.getIndex()
    return 0


traci.start([SUMO_BINARY, "-c", os.path.join(HERE, "flag.sumocfg"), "--no-step-log"])

events = []
requested = set()          # (vehicle, edge, pos) stops already asked for
last_state = {}            # agent -> last logged state


def log(t, event, **kw):
    events.append({"t": t, "event": event, **kw})
    print(f"t={t:>5}s  {event:<15} " + "  ".join(f"{k}={v}" for k, v in kw.items()))


t = 0
while traci.simulation.getMinExpectedNumber() > 0:
    traci.simulationStep()
    t += 1
    if t % POLLING_INTERVAL != 0:
        continue

    # ---- 1. read commuter state ------------------------------------------
    waiting, riding = {}, {}
    for pid in traci.person.getIDList():
        stage = traci.person.getStage(pid).type
        veh = traci.person.getVehicle(pid)
        edge = traci.person.getRoadID(pid)
        pos = traci.person.getLanePosition(pid)

        if stage == tc.STAGE_DRIVING and veh == "":
            waiting[pid] = (edge, pos)                 # at the roadside, arm out
            state = "waiting_to_flag"
        elif veh:
            riding[pid] = veh
            state = "riding"
        elif stage == tc.STAGE_WALKING:
            state = "walking"
        else:
            state = f"stage_{stage}"

        if last_state.get(pid) != state:
            log(t, "state_change", agent=pid, to=state,
                edge=edge, pos=round(pos, 1), vehicle=veh or "-")
            last_state[pid] = state

    # ---- 2. controller: decide which jeepneys must stop -------------------
    for vid in traci.vehicle.getIDList():
        line = traci.vehicle.getLine(vid)
        edge = traci.vehicle.getRoadID(vid)
        if edge.startswith(":"):
            continue
        pos = traci.vehicle.getLanePosition(vid)
        occ = traci.vehicle.getPersonNumber(vid)
        cap = traci.vehicle.getPersonCapacity(vid)

        for agent_id, r in REGISTRY.items():
            if r["line"] != line:
                continue

            # BOARD: someone is waiting ahead on this edge
            if agent_id in waiting and edge == r["board_edge"]:
                gap = r["board_pos"] - pos
                key = (vid, r["board_edge"], r["board_pos"])
                if 0 < gap <= LOOKAHEAD_M and key not in requested:
                    if occ >= cap:
                        log(t, "full_pass_by", vehicle=vid, agent=agent_id,
                            occupancy=f"{occ}/{cap}", edge=edge)
                        continue
                    traci.vehicle.setStop(vid, r["board_edge"], pos=r["board_pos"],
                                          laneIndex=bus_lane_index(r["board_edge"]),
                                          duration=DWELL_S)
                    requested.add(key)
                    log(t, "stop_requested", vehicle=vid, agent=agent_id,
                        purpose="board", pos=r["board_pos"], gap_m=round(gap, 1))

            # ALIGHT: a passenger aboard needs the next stop
            if riding.get(agent_id) == vid and edge == r["alight_edge"]:
                gap = r["alight_pos"] - pos
                key = (vid, r["alight_edge"], r["alight_pos"])
                if 0 < gap <= LOOKAHEAD_M and key not in requested:
                    traci.vehicle.setStop(vid, r["alight_edge"], pos=r["alight_pos"],
                                          laneIndex=bus_lane_index(r["alight_edge"]),
                                          duration=DWELL_S)
                    requested.add(key)
                    log(t, "stop_requested", vehicle=vid, agent=agent_id,
                        purpose="alight", pos=r["alight_pos"], gap_m=round(gap, 1))

traci.close()
print(f"\nSimulation ended at t={t} s.  {len(events)} events logged.")
