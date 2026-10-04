"""Track a walking person's state through TraCI.

This is a miniature of the Phase 7 commuter state tracker (Table 4.10):
read each person's stage every second, map it to one of our own state
names, and log only the CHANGES.
"""
import os
import sys

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci
import traci.constants as tc

HERE = os.path.dirname(os.path.abspath(__file__))
SUMO_BINARY = "sumo-gui"          # "sumo-gui" to watch

# SUMO's stage numbers -> the state vocabulary from OUR manuscript (Table 4.10)
STAGE_NAMES = {
    tc.STAGE_WAITING_FOR_DEPART: "at_origin",
    tc.STAGE_WALKING:            "walking_to_destination",
    tc.STAGE_DRIVING:            "riding",
    tc.STAGE_WAITING:            "waiting_to_flag",
    tc.STAGE_ACCESS:             "access",
    tc.STAGE_TRIP:               "planning",
}

traci.start([SUMO_BINARY, "-c", os.path.join(HERE, "walk.sumocfg")])

last_state = {}               # agent_id -> last state we logged
transitions = []              # our own event log

t = 0
while traci.simulation.getMinExpectedNumber() > 0:
    traci.simulationStep()
    t += 1

    for pid in traci.person.getIDList():
        stage = traci.person.getStage(pid)
        state = STAGE_NAMES.get(stage.type, f"unknown({stage.type})")
        edge = traci.person.getRoadID(pid)
        pos = traci.person.getLanePosition(pid)

        if last_state.get(pid) != state:                 # only log changes
            transitions.append({"t": t, "agent_id": pid, "from": last_state.get(pid),
                                "to": state, "edge_id": edge, "pos_m": round(pos, 1)})
            print(f"t={t:>4}s  {pid}: {last_state.get(pid)} -> {state}"
                  f"   (edge={edge}, pos={pos:.1f} m)")
            last_state[pid] = state

        if t % 150 == 0:
            print(f"t={t:>4}s  {pid} walking   edge={edge:<4} pos={pos:6.1f} m"
                  f"  v={traci.person.getSpeed(pid):.2f} m/s")

traci.close()

# When the person finishes, they vanish from getIDList() - that IS the arrival.
transitions.append({"t": t, "agent_id": "ped0", "from": last_state.get("ped0"),
                    "to": "arrived", "edge_id": None, "pos_m": None})
print(f"t={t:>4}s  ped0: {last_state.get('ped0')} -> arrived")
print(f"\nSimulation ended at t={t} s.  {len(transitions)} state transitions logged.")
