"""The laptop side of the hand-over: run the simulation's pipeline for a day, and whenever it waits
for the LLM, push its input file, wait for the server's answer (llm_bridge/serve.sh), pull it,
and go on. Stops when the day is done or when something needs a person.

From starosa_bayan, with its venv active:
    python ..\\llm_bridge\\laptop_loop.py --day 1
    python ..\\llm_bridge\\laptop_loop.py --day 1 --pipeline-args "--agents 10"
"""

import argparse
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ABM = Path(__file__).resolve().parent.parent / "starosa_bayan"
REPO = ABM.parent


def git(*args, check=True):
    result = subprocess.run(["git", *args], cwd=REPO, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{result.stdout}{result.stderr}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, default=1)
    ap.add_argument("--pipeline-args", default="", help='extra arguments for pipeline.py, e.g. "--agents 10"')
    ap.add_argument("--poll", type=int, default=30, help="seconds between checks for the server's answer")
    args = ap.parse_args()
    extra = shlex.split(args.pipeline_args)
    while True:
        print(f"\n>>> python pipeline.py --day {args.day} {' '.join(extra)}", flush=True)
        run = subprocess.run([sys.executable, "pipeline.py", "--day", str(args.day), *extra], cwd=ABM,
                             text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        print(run.stdout, flush=True)
        extra = [a for a in extra if not a.startswith("--redo")]   # a redo only once
        wait = re.search(r"Waiting for the LLM: answer (\S+) in (\S+)", run.stdout)
        if run.returncode == 0:
            git("add", "starosa_bayan/demand")
            if git("diff", "--cached", "--quiet", check=False).returncode:
                git("commit", "-m", f"Simulation day {args.day} done")
                git("pull", "--rebase", "--autostash")
                git("push")
            print(f"Day {args.day} is done and pushed.")
            return 0
        if not wait:
            print("The pipeline stopped for another reason (above). Fix it, then run this again.")
            return 1
        answer = ABM / wait.group(2)
        git("add", "starosa_bayan/demand")
        git("commit", "-m", f"Simulation: input for the LLM ({wait.group(1)})", check=False)
        git("pull", "--rebase", "--autostash")
        git("push")
        print(f"Pushed {wait.group(1)}. Waiting for the server's answer ({wait.group(2)})...", flush=True)
        waited = 0
        while not answer.is_file():
            time.sleep(args.poll)
            waited += args.poll
            git("pull", "--rebase", "--autostash", "-q", check=False)
            if waited % 300 < args.poll:
                print(f"  still waiting ({waited // 60} min)", flush=True)
        print(f"Got {wait.group(2)}.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
