"""Keep every simulation run: one folder per run, never overwritten by another run.

    starosa_bayan/llm_runs/run_001_20261112_2045_10agents/
        demand/        the run's agents, LLM answers, SUMO outcomes and every try's log (llm_logs/)
        report.xlsx    the readable report of that run
        run_info.json  when, prompt version, model settings, simulation settings, git commit

run_sim does this by itself:
  * at the end of every run_sim command, the current run's folder is brought up to date
    (the same run again = the same folder; days 2-3 of a run are added to that run's folder);
  * before a fresh start (--redo agents), the old run is saved one last time and a new run
    folder is started, with empty LLM logs.

By hand:
    python -m llm_bridge.archive                       # bring the current run's folder up to date
    python -m llm_bridge.archive --note "MAX_WALK 1500 test"
"""

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime

from . import config as C

ROOT = C.ABM_DIR / "llm_runs"
CURRENT = ROOT / "current_run.txt"
SKIP = shutil.ignore_patterns("old", "*.schema.json", "README.md", "*.partial.json")


def demand():
    return C.ABM_DIR / "demand"


def n_agents():
    try:
        return len(json.loads((demand() / "agents.json").read_text(encoding="utf-8"))["agents"])
    except Exception:
        return 0


def new_name():
    ROOT.mkdir(exist_ok=True)
    numbers = [int(m.group(1)) for p in ROOT.iterdir() if (m := re.match(r"run_(\d+)_", p.name))]
    return f"run_{max(numbers, default=0) + 1:03d}_{datetime.now():%Y%m%d_%H%M}_{n_agents()}agents"


def settings_used():
    text = (C.ABM_DIR / "settings.py").read_text(encoding="utf-8")
    found = {}
    for name in ["MAX_WALK", "MIN_RIDE", "WALK_TRIP_LIMIT", "BIKE_TRIP_LIMIT", "SAMPLE_AGENTS",
                 "JEEPNEY_HEADWAY_S", "END_S"]:
        m = re.search(rf"^{name}\s*=\s*([^\s#]+)", text, re.M)
        if m:
            found[name] = m.group(1)
    return found


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=C.ABM_DIR, text=True,
                              capture_output=True).stdout.strip()
    except Exception:
        return ""


def save(note="", new_run=False):
    """Copy demand/ and a fresh report into the current run's folder (made if there is none).
    new_run=True: afterwards, start a new run folder and empty the LLM logs for the new run."""
    if not (demand() / "agents.json").is_file():
        print("archive: no run to save yet (demand/agents.json is missing)")
        name = None
    else:
        ROOT.mkdir(exist_ok=True)
        name = CURRENT.read_text(encoding="utf-8").strip() if CURRENT.is_file() else ""
        if not name or not (ROOT / name).is_dir():
            name = new_name()
        dest = ROOT / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(dest / "demand", ignore_errors=True)
        shutil.copytree(demand(), dest / "demand", ignore=SKIP)
        try:
            from . import report
            report.main(["--out", str(dest / "report.xlsx")])
        except Exception as error:                          # the report is a convenience; the data is saved
            print(f"archive: report not made ({type(error).__name__}: {error})")
        info_path = dest / "run_info.json"
        info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {"notes": []}
        if note:
            info["notes"].append(f"{datetime.now():%Y-%m-%d %H:%M} {note}")
        info.update({"run": name, "last_saved": datetime.now().isoformat(timespec="seconds"),
                     "agents": n_agents(),
                     "days_done": sorted(int(m.group(1)) for p in demand().glob("reflection_day_*.json")
                                         if (m := re.match(r"reflection_day_(\d+)", p.stem))),
                     "prompt_version": C.PROMPT_VERSION, "max_tries": C.MAX_TRIES, "batch_size": C.BATCH_SIZE,
                     "simulation_settings": settings_used(), "git_commit": git_commit()})
        info_path.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        CURRENT.write_text(name, encoding="utf-8")
        print(f"archive: saved this run in {dest.relative_to(C.ABM_DIR.parent)}", flush=True)
    if new_run:
        shutil.rmtree(demand() / "llm_logs", ignore_errors=True)   # the old run's logs are in its folder
        (demand() / "llm_logs").mkdir(parents=True, exist_ok=True)
        ROOT.mkdir(exist_ok=True)
        CURRENT.write_text("", encoding="utf-8")                  # the next save makes a new folder
        print("archive: a new run starts (its own folder is made when it first saves)", flush=True)
    return name


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--note", default="", help="a line about this run, kept in run_info.json")
    args = ap.parse_args(argv)
    save(args.note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
