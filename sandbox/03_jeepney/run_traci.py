"""Watch jeepneys run a fixed route, and sample occupancy.

Previews the Phase 7 controller loop: every N seconds, look at every
jeepney in service and record where it is and how full it is. No
passengers exist yet - that is step 4.
"""
import os
import sys

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci

HERE = os.path.dirname(os.path.abspath(__file__))
SUMO_BINARY = "sumo-gui"                  # "sumo-gui" to watch

POLLING_INTERVAL = 30                 # params.yaml: controller_polling_interval (s)

traci.start([SUMO_BINARY, "-c", os.path.join(HERE, "jeep.sumocfg"), "--no-step-log"])

seen = set()
t = 0
while traci.simulation.getMinExpectedNumber() > 0:
    traci.simulationStep()
    t += 1

    # a jeepney entering service
    for vid in traci.simulation.getDepartedIDList():
        line = traci.vehicle.getLine(vid)
        cap = traci.vehicle.getPersonCapacity(vid)
        seen.add(vid)
        print(f"t={t:>5}s  ENTER SERVICE  {vid:<22} line={line:<12} capacity={cap}")

    if t % POLLING_INTERVAL == 0 and t <= 180:
        for vid in traci.vehicle.getIDList():
            edge = traci.vehicle.getRoadID(vid)
            if edge.startswith(":"):          # inside a junction - skip this tick
                continue
            pos = traci.vehicle.getLanePosition(vid)
            occ = traci.vehicle.getPersonNumber(vid)
            cap = traci.vehicle.getPersonCapacity(vid)
            print(f"t={t:>5}s    {vid:<22} edge={edge:<3} pos={pos:6.1f} m  "
                  f"occupancy={occ}/{cap}")

traci.close()
print(f"\n{len(seen)} jeepneys ran. Simulation ended at t={t} s.")
