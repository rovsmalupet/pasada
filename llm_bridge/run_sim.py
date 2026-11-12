"""Everything on one machine (the GPU server): run starosa_bayan's pipeline day by day, and answer
each LLM task as soon as it stops for one, with the model kept loaded in between. No git, no
waiting. Run it in tmux so it survives a dropped connection:

    cd ~/abm && ~/thesis-env/bin/python -m llm_bridge.run_sim --days 1 --first-args "--redo agents --agents 10"
    cd ~/abm && ~/thesis-env/bin/python -m llm_bridge.run_sim --days 1-3          # go on, days 1 to 3
    --model qwen (another candidate), BATCH_SIZE=16 (environment: agents answered together)

A day already done is kept (the pipeline keeps every finished step). Every run is kept in its own
folder, starosa_bayan/llm_runs/run_NNN_<date>_<N>agents/ (archive.py): brought up to date at the end of
each command, and a new one started by --redo agents, so a run never overwrites another. Stops at the first problem
that needs a person (printed), e.g. a trip no mode can make.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time

from . import answer as A
from . import archive
from . import config as C


def sumo_env():
    """SUMO from pip (eclipse-sumo) when SUMO_HOME is not set: its binaries and tools."""
    env = dict(os.environ)
    if not env.get("SUMO_HOME"):
        try:
            import sumo
            env["SUMO_HOME"] = sumo.SUMO_HOME
        except ImportError:
            raise SystemExit("SUMO not found: pip install eclipse-sumo==1.27.0 (or set SUMO_HOME)")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def days_of(text):
    if "-" in text:
        a, b = text.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(d) for d in text.split(",")]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", default="1", help="e.g. 1, 1-3 or 2,3")
    ap.add_argument("--model", default=C.DEFAULT_MODEL, choices=sorted(C.MODELS))
    ap.add_argument("--first-args", default="", help='pipeline.py arguments for the first call only, e.g. '
                                                    '"--redo agents --agents 10"')
    ap.add_argument("--pipeline-args", default="", help='pipeline.py arguments for every call, e.g. "--background"')
    ap.add_argument("--note", default="", help="a line about this run, kept in its llm_runs/ folder")
    args = ap.parse_args(argv)
    first = shlex.split(args.first_args)
    fresh = "--redo" in first and first[first.index("--redo") + 1:first.index("--redo") + 2] == ["agents"]
    if fresh:
        archive.save("saved before a fresh start (--redo agents)", new_run=True)   # the old run is kept
    try:
        return simulate(args, first)
    finally:
        archive.save(args.note)            # this run's folder in llm_runs/, up to date (also after a stop)


def simulate(args, first):
    env = sumo_env()
    every = shlex.split(args.pipeline_args)
    started = time.time()
    redos = {}
    for day in days_of(args.days):
        while True:
            cmd = [sys.executable, "pipeline.py", "--day", str(day), *first, *every]
            first = []
            print(f"\n>>> {' '.join(cmd[1:])}", flush=True)
            run = subprocess.run(cmd, cwd=C.ABM_DIR, env=env, text=True, encoding="utf-8", errors="replace",
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            print(run.stdout, flush=True)
            if run.returncode == 0:
                print(f"=== day {day} done ({(time.time() - started) / 60:.0f} min so far)", flush=True)
                break
            wait = re.search(r"Waiting for the LLM: answer (\S+) in (\S+)", run.stdout)
            stuck = re.search(r"cannot be made by any mode the agent has: (.+?)\. The schedule must change", run.stdout)
            if not wait and stuck and redos.get(day, 0) < 3:
                # a trip no mode can make (e.g. between two places, not from home): ask ONLY those agents
                # for a new day; every other agent keeps its schedule (answer.py reads the .keep file)
                redos[day] = redos.get(day, 0) + 1
                bad = sorted(set(re.findall(r"(A\d+)_D\d+_T\d+", stuck.group(1))))
                done = C.ABM_DIR / "demand" / f"schedule_day_{day}.json"
                keep = {e["agent_id"]: e for e in json.loads(done.read_text(encoding="utf-8"))["agents"]
                        if e.get("agent_id") not in bad}
                logs = C.ABM_DIR / "demand" / "llm_logs"
                logs.mkdir(exist_ok=True)
                (logs / f"schedule_day_{day}.keep.json").write_text(json.dumps(
                    {"prompt_version": C.PROMPT_VERSION, "why": f"no mode for {stuck.group(1)}", "answers": keep},
                    ensure_ascii=False), encoding="utf-8")
                print(f"\n>>> day {day}: a new schedule only for {', '.join(bad)} (a trip no mode can make); "
                      f"the other {len(keep)} agents keep theirs (redo {redos[day]} of 3)", flush=True)
                first = ["--redo", "schedule"]
                continue
            if not wait:
                print("The pipeline stopped for a reason the LLM cannot fix (above).")
                return 1
            # answer only the file the pipeline is waiting for (an old input file may lie around)
            inp, out = C.ABM_DIR / wait.group(1), C.ABM_DIR / wait.group(2)
            todo = [t for t in A.pending() if t[2].resolve() == inp.resolve()]
            if not todo:
                print(f"The pipeline waits for {wait.group(2)}, but it is not a task the bridge knows: see above.")
                return 1
            task, d, inp, out = todo[0]
            if not A.run_task(task, d, inp, out, args.model):
                print(f"{task}: some agents failed every try (above). Run the same command again to retry them.")
                return 1
    print(f"\nAll done in {(time.time() - started) / 60:.0f} min.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
