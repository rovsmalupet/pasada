"""A simulated day from the LLM to SUMO and back: the manuscript's workflow (Sec. 4.3, the
initialization and daily simulation figure; thesis plan B.3), with every file in demand/.

  at the start, once:
    agents      logic  make_agents.py: base agents living in the area, with homes  -> agents.json
    persona     LLM    llm_inputs/persona_input.json                               -> personas.json
    habits      LLM    llm_inputs/habits_input.json                                -> habits.json
  day N:
    schedule    LLM    llm_inputs/schedule_input_day_N.json                        -> schedule_day_N.json
    choices     logic  make_choices.py: every way each trip can be made           -> choices_day_N.json
    modes       LLM    llm_inputs/modes_input_day_N.json                           -> modes_day_N.json
    simulate    SUMO   run.py: the day, with the flag-down controller              -> outcomes_day_N.json
    reflection  LLM    llm_inputs/reflection_input_day_N.json                      -> reflection_day_N.json

A step whose file exists is checked and kept, so the pipeline can stop and go on. At an LLM step it
writes the task's input file (llm_tasks.py: what the prompt is given), then
  * --llm stub: llm_stub.py answers, and the pipeline goes on;
  * --llm files (the default): it stops until the answer file is there (written by the LLM team's
    code), and goes on when run again.
Every answer is checked (llm_tasks.py); a wrong one stops the pipeline with the list of problems, to
be regenerated (manuscript Sec. 4.3.9.1). Day N needs day N-1's reflection: the agents' travel
experience and mode weights follow from it. --redo moves a step's file and every later one into
demand/old/<date_time>/, never deleting them.

    python pipeline.py --day 1 --llm stub        # the whole day with the stand-in
    python pipeline.py --day 1                   # stops at the first LLM answer missing
    python pipeline.py --day 1 --redo modes      # modes, and every later step of day 1, again
    --agents N (first run: how many base agents, default settings.SAMPLE_AGENTS), --background
    (background traffic; default: none, much faster), --end SECONDS (stop the simulation early),
    --scenario NAME (simulate the day under a scenario of settings.SCENARIOS, e.g. reduced_jeepney)
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime

import settings as S
import llm_stub
from convert_demand import load_json
from llm_tasks import TASKS, answer_path, input_path, recurring, state

STEPS = ["agents", "persona", "habits", "schedule", "choices", "modes", "simulate", "reflection"]
INIT = STEPS[:3]


def output(step, day):
    if step == "agents":
        return S.AGENTS_FILE
    if step in TASKS:
        return answer_path(step, day if TASKS[step][2] else None)
    return S.day_file({"choices": "choices", "simulate": "outcomes"}[step], day)


def run(*cmd, log=None):
    """Run one of this folder's scripts with this Python and return what it printed (also into
    log, if given); stop the pipeline if it fails."""
    result = subprocess.run([sys.executable, *cmd], cwd=S.HERE, text=True, encoding="utf-8", errors="replace",
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if log:
        log.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed:\n" + "\n".join(result.stdout.splitlines()[-15:])
                         + (f"\n(all of it in {log})" if log else ""))
    return result.stdout


def redo(step, day):
    """Move the files of `step` and every later step (of this day and later days) aside."""
    old = S.DEMAND / "old" / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    later = STEPS[STEPS.index(step):]
    paths = [output(s, day) for s in later if s in INIT]
    first = 1 if step in INIT else day
    paths += [p for p in S.DEMAND.glob("*_day_*.json") if int(re.search(r"_day_(\d+)", p.stem).group(1)) > first
              or (int(re.search(r"_day_(\d+)", p.stem).group(1)) == first and
                  any(p == output(s, first) for s in later if s not in INIT))]
    moved = [p for p in paths if p.is_file()]
    for p in moved:
        old.mkdir(parents=True, exist_ok=True)
        shutil.move(p, old / p.name)
    print(f"Moved {len(moved)} file(s) to {S.rel(old)}: {', '.join(p.name for p in moved) or '-'}")


def llm_step(task, day, llm):
    daily = TASKS[task][2]
    d = day if daily else None
    write_input, check = TASKS[task][:2]
    path, given = answer_path(task, d), input_path(task, d)
    st = state(d)
    if not path.is_file() or not given.is_file():
        write_input(st, day) if daily else write_input(st)
        print(f"  wrote {S.rel(given)} (what the prompt is given)")
    if not path.is_file():
        if llm != "stub":
            raise SystemExit(f"\nWaiting for the LLM: answer {S.rel(given)} in {S.rel(path)} "
                             "(format: demand/README.md), then run this again.")
        llm_stub.answer(task, d)
    answer = load_json(path, "")
    errors = check(answer, st, day) if daily else check(answer, st)
    if errors:
        more = f"\n  ... and {len(errors) - 40} more" if len(errors) > 40 else ""
        raise SystemExit(f"\n{S.rel(path)} fails {len(errors)} check(s) (llm_tasks.py); regenerate those "
                         f"agents, then run again:\n  " + "\n  ".join(errors[:40]) + more)
    print(f"  {S.rel(path)}: {len(answer['agents'])} agents, every check passed")


def simulate(day, args):
    cmd = ["run.py", "--nogui", "--demand", str(S.day_file("schedule", day)), "--modes", str(S.day_file("modes", day))]
    cmd += [] if args.background else ["--no-background"]
    cmd += ["--end", str(args.end)] if args.end else []
    cmd += ["--scenario", args.scenario] if args.scenario else []
    log = S.RUNS / "pipeline_last_run.log"
    S.RUNS.mkdir(exist_ok=True)
    out = run(*cmd, log=log)
    run_dir = S.HERE / re.search(r"Run folder: (\S+)", out).group(1)
    shutil.move(log, run_dir / "run.log")
    shutil.copy2(run_dir / f"outcomes_day_{day}.json", S.day_file("outcomes", day))
    print("  " + "\n  ".join(out[out.index("================ trip outcomes"):].strip().splitlines()))
    print(f"  run folder {S.rel(run_dir)} (run.log: everything SUMO and the controller said)")


def choices(day):
    out = run("make_choices.py", str(S.day_file("schedule", day)), "--out", str(S.day_file("choices", day)))
    print("  " + out.strip().replace("\n", "\n  "))
    empty = [t["trip_id"] for a in load_json(S.day_file("choices", day), "")["agents"] for t in a["trips"]
             if not t["options"]]
    if empty:
        raise SystemExit(f"\n{len(empty)} trip(s) cannot be made by any mode the agent has: {', '.join(empty[:20])}. The "
                         f"schedule must change for those agents: python pipeline.py --day {day} --redo schedule")


def report(day, llm):
    outcomes = load_json(S.day_file("outcomes", day), "")
    trips = [o for a in outcomes["agents"] for o in a["trips"]]
    refl = load_json(S.day_file("reflection", day), "")["agents"]
    modes = Counter(o["realized_mode"] for o in trips)
    st = state(day)
    changed = sum(any(w != t["mode_preference_weights"] for t in recurring(st, a["agent_id"])
                      for k, w in a["habit_update"].items() if k == t["trip_key"]) for a in refl)
    print(f"\nDay {day} done: {len(refl)} agents, {len(trips)} trips (" + ", ".join(f"{m} {n}" for m, n in modes.most_common())
          + f"), {sum(o['completed'] for o in trips)} arrived, {sum(bool(o['disrupted']) for o in trips)} disrupted; "
          f"{changed} agents changed their mode weights. "
          f"Next: python pipeline.py --day {day + 1}" + (" --llm stub" if llm == "stub" else ""))


def main():
    parser = argparse.ArgumentParser(description="Run a simulated day from the LLM to SUMO and back.")
    parser.add_argument("--day", type=int, default=1)
    parser.add_argument("--llm", choices=["files", "stub"], default="files",
                        help="who answers the LLM tasks: files (the LLM team's, default) or stub (llm_stub.py)")
    parser.add_argument("--redo", choices=STEPS, help="do this step and every later one again")
    parser.add_argument("--agents", type=int, default=S.SAMPLE_AGENTS, help="first run: base agents to take (0: all)")
    parser.add_argument("--background", action="store_true", help="simulate with background traffic")
    parser.add_argument("--end", type=int, help="stop the simulation at this many seconds into the day")
    parser.add_argument("--scenario", choices=sorted(S.SCENARIOS),
                        help="simulate this day under a scenario of settings.SCENARIOS (no prompt is told)")
    args = parser.parse_args()
    if args.redo:
        redo(args.redo, args.day)

    for step in STEPS:
        path = output(step, args.day)
        kind = "LLM" if step in TASKS else "SUMO" if step == "simulate" else "logic"
        print(f"\n=== {step} ({kind}) -> {S.rel(path)}")
        started = time.time()
        if step in TASKS:
            llm_step(step, args.day, args.llm)
        elif path.is_file():
            print("  kept")
            continue
        elif step == "agents":
            print("  " + run("make_agents.py", str(args.agents)).strip().replace("\n", "\n  "))
        elif step == "choices":
            choices(args.day)
        else:
            simulate(args.day, args)
        print(f"  ({time.time() - started:.0f} s)")
    report(args.day, args.llm)


if __name__ == "__main__":
    main()
