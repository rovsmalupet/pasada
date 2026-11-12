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
            # the same problem when the pipeline kept an old choices file and went on to modes anyway
            empty = trips_without_options(day) if (stuck or (wait and "modes_input" in wait.group(1))) else []
            if empty and redos.get(day, 0) >= 2:
                # last resort, simulation side: the agent skips the stop it cannot get to
                drop_unmakeable(day, empty)
                first = ["--redo", "choices"]
                redos[day] = 0
                continue
            if empty:
                # a trip no mode can make (e.g. between two places, not from home): ask ONLY those agents
                # for a new day; every other agent keeps its schedule (answer.py reads the .keep file)
                redos[day] = redos.get(day, 0) + 1
                bad = sorted(set(re.findall(r"(A\d+)_D\d+_T\d+", " ".join(empty))))
                done = C.ABM_DIR / "demand" / f"schedule_day_{day}.json"
                keep = {e["agent_id"]: e for e in json.loads(done.read_text(encoding="utf-8"))["agents"]
                        if e.get("agent_id") not in bad}
                logs = C.ABM_DIR / "demand" / "llm_logs"
                logs.mkdir(exist_ok=True)
                (logs / f"schedule_day_{day}.keep.json").write_text(json.dumps(
                    {"prompt_version": C.PROMPT_VERSION, "why": f"no mode for {stuck.group(1)}", "answers": keep},
                    ensure_ascii=False), encoding="utf-8")
                print(f"\n>>> day {day}: a new schedule only for {', '.join(bad)} (a trip no mode can make); "
                      f"the other {len(keep)} agents keep theirs (try {redos[day]} of 2, then the stop is skipped)",
                      flush=True)
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


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def trips_without_options(day):
    """Trip ids of the day's choices file with no way to make them (empty options)."""
    path = C.ABM_DIR / "demand" / f"choices_day_{day}.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return sorted({d["trip_id"] for d in _walk(data) if "trip_id" in d and "options" in d and not d["options"]})


def drop_unmakeable(day, trip_ids):
    """LAST RESORT (simulation side), after the LLM was asked twice: the agent skips a stop it has no way
    to get to (its stay is removed and the stays around it join up), so the day can be simulated.
    Logged in demand/llm_logs/simulation_adjustments.jsonl."""
    path = C.ABM_DIR / "demand" / f"schedule_day_{day}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    log = C.ABM_DIR / "demand" / "llm_logs" / "simulation_adjustments.jsonl"
    for agent in data["agents"]:
        if not any(t.startswith(agent["agent_id"] + "_") for t in trip_ids):
            continue
        stays, drop = agent["schedule"], set()
        # trip n is the n-th move between two different places; skip the stop a failing trip goes TO
        # (the trip out of a skipped stop then disappears with it); never skip home
        moves = [i for i in range(1, len(stays)) if stays[i]["location_id"] != stays[i - 1]["location_id"]]
        failing = sorted(int(m.group(1)) for t in trip_ids if t.startswith(agent["agent_id"] + "_")
                         if (m := re.search(r"_T(\d+)$", t)))
        for n in failing:
            if n - 1 >= len(moves):
                continue
            i = moves[n - 1]
            if i - 1 in drop:                       # the trip out of a stop already skipped
                continue
            j = i if not stays[i]["location_id"].startswith("HOME") else i - 1
            if not stays[j]["location_id"].startswith("HOME"):
                drop.add(j)
        merged = []
        for i, stay in enumerate(stays):
            if i in drop:
                continue
            if merged and merged[-1]["location_id"] == stay["location_id"]:
                if "end" in stay:
                    merged[-1]["end"] = stay["end"]
                else:
                    merged[-1].pop("end", None)
            else:
                merged.append(dict(stay))
        removed = [stays[i]["location_id"] for i in sorted(drop)]
        agent["schedule"] = merged
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"day": day, "agent_id": agent["agent_id"], "trips": [t for t in trip_ids
                                if t.startswith(agent["agent_id"] + "_")], "skipped_stays": removed,
                                "why": "no mode can make these trips; the LLM was asked twice"}) + "\n")
        print(f">>> day {day}: {agent['agent_id']} skips {', '.join(removed)} (no way to get there; "
              f"logged in simulation_adjustments.jsonl)", flush=True)
    head = {k: v for k, v in data.items() if k != "agents"}
    A.write(path, head, data["agents"])


if __name__ == "__main__":
    sys.exit(main())
