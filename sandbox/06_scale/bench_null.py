import os, sys, time
sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))
import traci
HERE = os.path.dirname(os.path.abspath(__file__))
traci.start(["sumo", "-c", os.path.join(HERE, "bench.sumocfg"), "--no-step-log"])
t0 = time.perf_counter()
for _ in range(600):
    traci.simulationStep()
e = time.perf_counter() - t0
traci.close()
print(f"null (no controller)      {e:8.1f} {e/600*1000:9.2f} ms/step   {e/600*86400/60:8.1f} min/day")
