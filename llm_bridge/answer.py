"""Answer the simulation's LLM tasks: read each input file in starosa_bayan/demand/llm_inputs/ that
has no answer yet, ask the model once per agent with the evaluated prompt, regenerate an answer
that fails a check (up to MAX_TRIES), and write the answer file pipeline.py is waiting for.

    python -m llm_bridge.answer                    # every input file without an answer
    python -m llm_bridge.answer --task persona     # only this task (day tasks: every day missing)
    python -m llm_bridge.answer --model qwen       # another candidate model (default: llama)
    python -m llm_bridge.answer --show-prompt      # print the first prompt and stop (no model)

Progress is saved after every agent (demand/llm_logs/), so a stopped run goes on where it was.
Every try, with its prompt, answer and problems, goes to demand/llm_logs/<answer>.attempts.jsonl
(the regeneration rate of Sec. 4.3.9 comes from it).
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime

from . import config as C
from .prompts import SYSTEM
from .tasks import TASKS, agent_id_of

ORDER = ["persona", "habits", "schedule", "modes", "reflection"]


def demand():
    return C.ABM_DIR / "demand"


def answer_path(task, day):
    if task == "persona":
        return demand() / "personas.json"
    if task == "habits":
        return demand() / "habits.json"
    return demand() / f"{task}_day_{day}.json"


def pending(only=None):
    """(task, day, input path, answer path) for every input file without its answer, in order."""
    found = []
    for path in (demand() / "llm_inputs").glob("*_input*.json"):
        m = re.fullmatch(r"(\w+?)_input(?:_day_(\d+))?", path.stem)
        if not m or m.group(1) not in TASKS:
            continue
        task, day = m.group(1), int(m.group(2)) if m.group(2) else None
        if only and task != only:
            continue
        out = answer_path(task, day)
        if not out.is_file():
            found.append((task, day, path, out))
    return sorted(found, key=lambda f: (f[1] or 0, ORDER.index(f[0])))


def write(path, head, agents):
    """The simulation's own layout (llm_tasks.write): {head..., "agents": [one agent per line]}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{\n" + "".join(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},\n" for k, v in head.items())
                    + '  "agents": [\n' + ",\n".join(f"    {json.dumps(a, ensure_ascii=False)}" for a in agents)
                    + "\n  ]\n}\n", encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))


def run_task(task, day, inp, out, model_name, show_prompt=False):
    from . import model as M
    build, convert, _ = TASKS[task]
    data = json.loads(inp.read_text(encoding="utf-8"))
    head = {k: v for k, v in data.items() if k != "agents"}
    entries = data["agents"]
    logs = demand() / "llm_logs"
    logs.mkdir(exist_ok=True)
    partial_path = logs / f"{out.stem}.partial.json"
    attempts_path = logs / f"{out.stem}.attempts.jsonl"
    done = json.loads(partial_path.read_text(encoding="utf-8")) if partial_path.is_file() else {}
    print(f"\n=== {task}{f' day {day}' if day else ''}: {len(entries)} agents, {len(done)} already answered "
          f"({inp.relative_to(C.ABM_DIR)} -> {out.relative_to(C.ABM_DIR)})", flush=True)

    failed, first_try, tries_total, asked = [], 0, 0, 0
    for n, entry in enumerate(entries, 1):
        aid = agent_id_of(entry)
        if aid in done:
            continue
        prompt, max_tokens = build(entry, head)
        if show_prompt:
            print(SYSTEM + "\n\n" + (prompt or "(no prompt: answered without the model)"))
            return True
        if prompt is None:                        # nothing for the model to decide
            result, problems = convert("", entry, head)
            done[aid] = result
            partial_path.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
            continue
        asked += 1
        M.load(model_name)
        result = None
        for attempt in range(1, C.MAX_TRIES + 1):
            start = time.time()
            text = M.ask(SYSTEM, prompt, max_tokens)
            result, problems = convert(text, entry, head)
            tries_total += 1
            with attempts_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"agent_id": aid, "task": task, "day": day, "model": model_name,
                                    "prompt_version": C.PROMPT_VERSION, "try": attempt,
                                    "seconds": round(time.time() - start, 1), "passes": result is not None,
                                    "problems": problems, "prompt": prompt, "output": text,
                                    "time": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
            print(f"{task} | {aid} ({n}/{len(entries)}) | try {attempt}/{C.MAX_TRIES} | "
                  f"{'passes' if result else 'FAILS: ' + '; '.join(problems)[:150]} | {time.time() - start:.1f}s",
                  flush=True)
            if result is not None:
                first_try += attempt == 1
                break
        if result is None:
            failed.append(aid)
            continue
        done[aid] = result
        partial_path.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")

    if failed:
        print(f"\n{task}: {len(failed)} agent(s) failed all {C.MAX_TRIES} tries: {', '.join(failed)}\n"
              f"Run the same command again to try them again (the others are kept).", flush=True)
        return False
    answer_head = {"day": day} if day else {}
    answer_head["made_by"] = f"{C.MODELS[model_name]['repo']} (4-bit), prompt {C.PROMPT_VERSION}, llm_bridge"
    write(out, answer_head, [done[agent_id_of(e)] for e in entries])
    partial_path.unlink(missing_ok=True)
    print(f"{task}: wrote {out.relative_to(C.ABM_DIR)} | {len(entries)} agents"
          + (f" | asked this run: {asked}, passed on the first try: {first_try}" if asked else ""), flush=True)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=ORDER, help="only this task")
    ap.add_argument("--model", default=C.DEFAULT_MODEL, choices=sorted(C.MODELS))
    ap.add_argument("--show-prompt", action="store_true", help="print the first prompt and stop")
    args = ap.parse_args(argv)
    todo = pending(args.task)
    if not todo:
        print("Nothing to answer: every input file in demand/llm_inputs/ has its answer.")
        return 0
    for task, day, inp, out in todo:
        if not run_task(task, day, inp, out, args.model, args.show_prompt):
            return 1
        if args.show_prompt:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
