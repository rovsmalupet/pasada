"""Phase 11 scale check: how expensive is the jeepney controller really?

Three designs, same work, measured on the same 600-second run:
  A  naive       - per-vehicle TraCI getters + scan every registry entry
  B  subscribe   - TraCI subscriptions (one batched fetch per step)
  C  subscribe+  - subscriptions AND a registry indexed by edge
"""
import os
import sys
import time
import random
from collections import defaultdict

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci
import traci.constants as tc
import sumolib

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "bench.sumocfg")
STEPS = 600
N_COMMUTERS = 2000

net = sumolib.net.readNet(os.path.join(HERE, "grid.net.xml"))
edge_ids = [e.getID() for e in net.getEdges() if not e.getID().startswith(":")]

random.seed(42)
REGISTRY = {}
for i in range(N_COMMUTERS):
    e = random.choice(edge_ids)
    REGISTRY[f"c{i}"] = {"board_edge": e,
                         "board_pos": random.uniform(10, net.getEdge(e).getLength() - 10)}

# registry indexed by edge - the whole point of design C
BY_EDGE = defaultdict(list)
for aid, r in REGISTRY.items():
    BY_EDGE[r["board_edge"]].append((aid, r["board_pos"]))

LOOKAHEAD = 150.0


def run(mode):
    traci.start(["sumo", "-c", CFG, "--no-step-log"])
    subscribed = set()
    peak = 0
    hits = 0
    t0 = time.perf_counter()

    for _ in range(STEPS):
        traci.simulationStep()

        if mode == "A":
            ids = traci.vehicle.getIDList()
            peak = max(peak, len(ids))
            for vid in ids:
                edge = traci.vehicle.getRoadID(vid)
                pos = traci.vehicle.getLanePosition(vid)
                traci.vehicle.getPersonNumber(vid)
                traci.vehicle.getPersonCapacity(vid)
                if edge.startswith(":"):
                    continue
                for aid, r in REGISTRY.items():            # scan EVERYONE
                    if r["board_edge"] == edge and 0 < r["board_pos"] - pos <= LOOKAHEAD:
                        hits += 1
        else:
            for vid in traci.simulation.getDepartedIDList():
                traci.vehicle.subscribe(vid, [tc.VAR_ROAD_ID, tc.VAR_LANEPOSITION,
                                              tc.VAR_PERSON_NUMBER, tc.VAR_PERSON_CAPACITY])
                subscribed.add(vid)
            res = traci.vehicle.getAllSubscriptionResults()   # ONE fetch
            peak = max(peak, len(res))
            for vid, d in res.items():
                edge = d[tc.VAR_ROAD_ID]
                pos = d[tc.VAR_LANEPOSITION]
                if edge.startswith(":"):
                    continue
                if mode == "B":
                    for aid, r in REGISTRY.items():        # still scans everyone
                        if r["board_edge"] == edge and 0 < r["board_pos"] - pos <= LOOKAHEAD:
                            hits += 1
                else:                                       # C: O(1) edge lookup
                    for aid, bpos in BY_EDGE.get(edge, ()):
                        if 0 < bpos - pos <= LOOKAHEAD:
                            hits += 1

    elapsed = time.perf_counter() - t0
    traci.close()
    return elapsed, peak, hits


print(f"grid: {len(edge_ids)} edges | registry: {N_COMMUTERS} waiting commuters | "
      f"{STEPS} steps\n")
print(f"{'design':26} {'wall s':>8} {'ms/step':>9} {'peak veh':>9} {'86400 s run':>14}")
print("-" * 70)
for mode, name in [("A", "A  naive"),
                   ("B", "B  subscriptions"),
                   ("C", "C  subscriptions + index")]:
    elapsed, peak, hits = run(mode)
    per_step = elapsed / STEPS
    projected = per_step * 86400 / 60
    print(f"{name:26} {elapsed:8.1f} {per_step*1000:9.2f} {peak:9d} {projected:11.1f} min")
