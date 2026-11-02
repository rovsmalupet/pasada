"""Regenerate everything in generated/ for run.py, in order. Needs the map (make_map.py: the
study area and the network).

The demand files in demand/ are not rebuilt: the LLM's files refer to those agents and trips.
Only a missing file is made: agents.json (make_agents.py), the schedule (llm_stub.py schedule),
the choices (make_choices.py) and the modes (llm_stub.py modes). run.py converts the schedule and
the modes at every run.

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
    for path, step in ((S.AGENTS_FILE, ["make_agents.py"]), (S.SCHEDULE_FILE, ["llm_stub.py", "schedule"]),
                       (S.CHOICES_FILE, ["make_choices.py"]), (S.MODES_FILE, ["llm_stub.py", "modes"])):
        if path.is_file():
            print(f"\n{path.relative_to(S.HERE)} kept as it is")
        else:
            run(*step)
    print("\nAll done. Check the demand: python convert_demand.py; run it: python run.py")


if __name__ == "__main__":
    main()
