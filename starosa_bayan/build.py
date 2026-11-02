"""Regenerate everything in generated/ for run.py, in order. Needs the map (make_map.py: the
study area and the network).

The demand (demand/agents.json and demand/day_1.json) is not rebuilt: the LLM's demand file
refers to those agents. Only if a file is missing does this make it (make_agents.py, then
llm_stub.py for a stand-in demand file). run.py converts the demand file at every run.

    python build.py
"""

import subprocess
import sys
import time

import settings as S

STEPS = ["make_routes.py", "make_vtypes.py", "make_background.py"]


def run(step):
    print(f"\n=== {step}")
    started = time.time()
    if subprocess.run([sys.executable, step], cwd=S.HERE).returncode != 0:
        raise SystemExit(f"{step} failed")
    print(f"=== {step} done in {time.time() - started:.0f} s")


def main():
    if not S.NET_FILE.is_file():
        raise SystemExit(f"No network at {S.NET_FILE}: build the map first (python make_map.py)")
    if not S.AREA_FILE.is_file():
        raise SystemExit("No study area yet: python make_map.py")
    for step in STEPS:
        run(step)
    for path, step in ((S.AGENTS_FILE, "make_agents.py"), (S.DEMAND_FILE, "llm_stub.py")):
        if path.is_file():
            print(f"\n{path.relative_to(S.HERE)} kept as it is")
        else:
            run(step)
    print("\nAll done. Check the demand file: python convert_demand.py; run it: python run.py")


if __name__ == "__main__":
    main()
