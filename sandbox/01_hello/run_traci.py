"""Hello SUMO via TraCI.

TraCI lets Python step the simulation one second at a time and read/change
state while it runs. Everything in Phase 7 (jeepney controller, commuter
state tracker) is built on exactly this loop.
"""
import os
import sys

# SUMO ships its Python tools inside SUMO_HOME; add them to the import path.
if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci

HERE = os.path.dirname(os.path.abspath(__file__))

# Swap "sumo" for "sumo-gui" to watch it. Everything else stays the same.
SUMO_BINARY = "sumo-gui"

traci.start([SUMO_BINARY, "-c", os.path.join(HERE, "hello.sumocfg")])

step = 0
while traci.simulation.getMinExpectedNumber() > 0:   # stop when no vehicles remain
    traci.simulationStep()                            # advance exactly 1 second
    step += 1

    if step % 20 == 0:
        for vid in traci.vehicle.getIDList():
            edge = traci.vehicle.getRoadID(vid)       # which edge it is on
            pos = traci.vehicle.getLanePosition(vid)  # metres along that edge
            speed = traci.vehicle.getSpeed(vid)
            print(f"t={step:>4}s  {vid}  edge={edge:<4} pos={pos:7.1f} m  v={speed:5.2f} m/s")

traci.close()
print(f"\nSimulation ended at t={step} s.")
