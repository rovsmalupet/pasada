"""Regenerate everything in generated/ for run.py, in order. Needs the map (make_map.py: the
study area and the network).

The demand in demand/ is not touched: the LLM's files refer to those agents and trips. It comes from
the pipeline (python pipeline.py: agents, the LLM's answers, options, the simulation).

    python build.py
"""

import subprocess
import sys
import time

import settings as S

STEPS = ["make_routes.py", "make_vtypes.py", "make_background.py"]


def run(step, *args):
    print(f"\n=== {' '.join((step, *args))}")
    started = time.time()
    if subprocess.run([sys.executable, step, *args], cwd=S.HERE).returncode != 0:
        raise SystemExit(f"{step} failed")
    print(f"=== {step} done in {time.time() - started:.0f} s")


def main():
    if not S.NET_FILE.is_file():
        raise SystemExit(f"No network at {S.NET_FILE}: build the map first (python make_map.py)")
    if not S.AREA_FILE.is_file():
        raise SystemExit("No study area yet: python make_map.py")
    for step in STEPS:
        run(step)
    print("\nAll done. Next, a day from the LLM to SUMO: python pipeline.py --day 1 --llm stub")


if __name__ == "__main__":
    main()
