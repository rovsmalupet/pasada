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
import hashlib
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
    # saved progress counts only for this exact input file (same contents): never answers to an old one
    input_sha = hashlib.sha256(inp.read_bytes()).hexdigest()
    saved = json.loads(partial_path.read_text(encoding="utf-8")) if partial_path.is_file() else {}
    # ...and only for answers made with the same prompt version (a prompt change answers everyone again)
    same = saved.get("input_sha256") == input_sha and saved.get("prompt_version") == C.PROMPT_VERSION
    done = saved.get("answers", {}) if same else {}
    if saved and not same:
        print(f"  (saved progress in {partial_path.name} was for another input file or prompt version: not used)", flush=True)
    # run_sim's targeted redo: agents whose schedule could be made keep it; only the others are asked
    keep_path = logs / f"{out.stem}.keep.json"
    if keep_path.is_file():
        kept = json.loads(keep_path.read_text(encoding="utf-8"))
        if kept.get("prompt_version") == C.PROMPT_VERSION:
            ids = {agent_id_of(e) for e in entries}
            done.update({a: r for a, r in kept["answers"].items() if a in ids and a not in done})
            print(f"  kept {len(kept['answers'])} agents' answers from before ({kept.get('why', '')})", flush=True)
        keep_path.unlink()
    print(f"\n=== {task}{f' day {day}' if day else ''}: {len(entries)} agents, {len(done)} already answered "
          f"({inp.relative_to(C.ABM_DIR)} -> {out.relative_to(C.ABM_DIR)})", flush=True)

    # every agent still to answer, with its prompt (built once: the same prompt on every try)
    queue = []
    for entry in entries:
        aid = agent_id_of(entry)
        if aid in done:
            continue
        prompt, max_tokens = build(entry, head)
        if show_prompt:
            print(SYSTEM + "\n\n" + (prompt or "(no prompt: answered without the model)"))
            return True
        if prompt is None:                        # nothing for the model to decide
            done[aid] = convert("", entry, head)[0]
            continue
        queue.append({"aid": aid, "entry": entry, "prompt": prompt, "max_tokens": max_tokens, "try": 0})
    partial_path.write_text(json.dumps({"input_sha256": input_sha, "prompt_version": C.PROMPT_VERSION, "answers": done}, ensure_ascii=False), encoding="utf-8")

    failed, first_try, asked, total = [], 0, len(queue), len(queue)
    queue.sort(key=lambda q: len(q["prompt"]))     # similar lengths together: less padding
    while queue:
        batch, queue = queue[:C.BATCH_SIZE], queue[C.BATCH_SIZE:]
        M.load(model_name)
        start = time.time()
        texts = M.ask_batch(SYSTEM, [q["prompt"] for q in batch], max(q["max_tokens"] for q in batch))
        seconds = round(time.time() - start, 1)
        from . import tasks as T
        for q, text in zip(batch, texts):
            q["try"] += 1
            T.NOTES.clear()
            result, problems = convert(text, q["entry"], head)
            notes = list(T.NOTES)
            with attempts_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"agent_id": q["aid"], "task": task, "day": day, "model": model_name,
                                    "prompt_version": C.PROMPT_VERSION, "try": q["try"], "batch_size": len(batch),
                                    "batch_seconds": seconds, "passes": result is not None,
                                    "problems": problems, "simulation_notes": notes, "prompt": q["prompt"], "output": text,
                                    "time": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
            print(f"{task} | {q['aid']} | try {q['try']}/{C.MAX_TRIES} | "
                  f"{'passes' if result else 'FAILS: ' + '; '.join(problems)[:150]}", flush=True)
            if result is not None:
                done[q["aid"]] = result
                first_try += q["try"] == 1
            elif q["try"] < C.MAX_TRIES:
                queue.append(q)                   # regenerated in a later batch
            else:
                failed.append(q["aid"])
        partial_path.write_text(json.dumps({"input_sha256": input_sha, "prompt_version": C.PROMPT_VERSION, "answers": done}, ensure_ascii=False), encoding="utf-8")
        print(f"  batch of {len(batch)} in {seconds}s | {total - len(queue) - len(failed)} of {total} answered",
              flush=True)

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
