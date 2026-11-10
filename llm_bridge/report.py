"""A readable Excel view of the simulation's LLM files, any time (also while a run is going):

    cd ~/abm && ~/thesis-env/bin/python -m llm_bridge.report            # -> ~/abm/report.xlsx
    cd ~/abm && ~/thesis-env/bin/python -m llm_bridge.report --summary  # just the progress, in the terminal

Tabs: progress (per task: agents answered, first-try passes, regenerations), personas, habits,
schedule / modes / outcomes / reflection of each day, and failures (every failed try and why).
Places are shown by category and name, not by id. Only reads files; changes nothing.
Copy it to a laptop with:  scp -P 32092 simulacra@103.231.240.148:~/abm/report.xlsx .
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from . import config as C

ORDER = ["persona", "habits", "schedule", "modes", "reflection"]


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def demand():
    return C.ABM_DIR / "demand"


def place_namer():
    agents = load(demand() / "agents.json") or {}
    locs = {l["location_id"]: l for l in agents.get("locations", [])}

    def name(lid):
        if lid is None:
            return ""
        if str(lid).startswith("HOME_"):
            return "home"
        l = locs.get(lid)
        return f"{l['category']}: {l.get('name') or '(no name)'}" if l else str(lid)
    return name, {a["agent_id"]: a for a in agents.get("agents", [])}


def attempts():
    rows = []
    for f in sorted((demand() / "llm_logs").glob("*.attempts.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def progress_rows():
    """One row per LLM task file: how far it is."""
    tries = defaultdict(list)
    for r in attempts():
        tries[(r["task"], r.get("day"))].append(r)
    rows = []
    for inp in sorted((demand() / "llm_inputs").glob("*_input*.json")):
        m = re.fullmatch(r"(\w+?)_input(?:_day_(\d+))?", inp.stem)
        if not m or m.group(1) not in ORDER:
            continue
        task, day = m.group(1), int(m.group(2)) if m.group(2) else None
        n_agents = len((load(inp) or {}).get("agents", []))
        answer = demand() / ("personas.json" if task == "persona" else "habits.json" if task == "habits"
                             else f"{task}_day_{day}.json")
        done = len((load(answer) or {}).get("agents", [])) if answer.is_file() else None
        if done is None:
            partial = load(demand() / "llm_logs" / f"{answer.stem}.partial.json") or {}
            done = len(partial.get("answers", partial if "answers" not in partial and "input_sha256" not in partial else {}))
        t = tries[(task, day)]
        agents_tried = {r["agent_id"] for r in t}
        first = sum(1 for r in t if r["try"] == 1 and r["passes"])
        rows.append({"task": task, "day": day or "", "agents": n_agents, "answered": done,
                     "status": "done" if answer.is_file() else ("running / stopped" if t else "waiting"),
                     "first_try_passes": first, "agents_asked": len(agents_tried),
                     "first_try_rate": f"{first / len(agents_tried):.0%}" if agents_tried else "",
                     "regenerations": sum(1 for r in t if r["try"] > 1),
                     "failed_all_tries": len({r["agent_id"] for r in t if r["try"] == C.MAX_TRIES and not r["passes"]}),
                     "last_update": max((r["time"] for r in t), default="")})
    order = {t: i for i, t in enumerate(ORDER)}
    return sorted(rows, key=lambda r: (r["day"] or 0, order[r["task"]]))


def sheets():
    name, agents = place_namer()
    out = {"progress": progress_rows()}

    personas = (load(demand() / "personas.json") or {}).get("agents", [])
    out["personas"] = [{
        "agent_id": p["agent_id"], "age": agents.get(p["agent_id"], {}).get("age_group", ""),
        "sex": agents.get(p["agent_id"], {}).get("sex", ""),
        "type": agents.get(p["agent_id"], {}).get("commuter_type", ""),
        "decile": agents.get(p["agent_id"], {}).get("per_capita_income_decile", ""),
        "car": p["persona"].get("private_car_access"), "bike": p["persona"].get("bicycle_access"),
        **{k: v for k, v in p["persona"].items() if k.endswith("sensitivity") or k.endswith("predisposition")},
        **{f"sens_{k}": v for k, v in p["persona"].get("sensitivities", {}).items()},
        "preference": p["persona"].get("general_transport_preference"),
        "description": p["persona"].get("description")} for p in personas]

    habits = (load(demand() / "habits.json") or {}).get("agents", [])
    out["habits"] = [{
        "agent_id": h["agent_id"], "trip": t["trip_key"], "from": name(t["origin"]), "to": name(t["destination"]),
        "purpose": t["purpose"], "days": ", ".join(t["days"]), "departs": t["usual_departure"],
        **{f"w_{m}": w for m, w in t["mode_preference_weights"].items()}}
        for h in habits for t in h["recurring_trips"]]

    days = sorted({int(m.group(1)) for f in demand().glob("*_day_*.json")
                   if (m := re.search(r"_day_(\d+)", f.stem))})
    for d in days:
        sched = (load(demand() / f"schedule_day_{d}.json") or {}).get("agents", [])
        out[f"schedule_day_{d}"] = [{
            "agent_id": a["agent_id"], "#": i + 1, "place": name(e["location_id"]), "activity": e.get("activity"),
            "arrive": e.get("start", ""), "leave": e.get("end", ""), "what": e.get("reasoning", "")}
            for a in sched for i, e in enumerate(a["schedule"])]
        modes = (load(demand() / f"modes_day_{d}.json") or {}).get("agents", [])
        choices = {t["trip_id"]: t for a in (load(demand() / f"choices_day_{d}.json") or {}).get("agents", [])
                   for t in a.get("trips", [])}
        rows = []
        for a in modes:
            for s in a["selections"]:
                t = choices.get(s["trip_id"], {})
                opts = {o["mode"]: o for o in t.get("options", [])}
                rows.append({"agent_id": a["agent_id"], "trip": s["trip_id"], "departs": t.get("depart", ""),
                             "from": name(t.get("from")), "to": name(t.get("to")), "chosen": s["mode"],
                             "line": s.get("route_id", ""), "reasoning": s.get("reasoning", ""),
                             **{f"{m}_min": round(o["estimated_total_time_s"] / 60, 1) for m, o in opts.items()},
                             **{f"{m}_php": o.get("cost_php") for m, o in opts.items()}})
        out[f"modes_day_{d}"] = rows
        outcomes = (load(demand() / f"outcomes_day_{d}.json") or {}).get("agents", [])
        def minutes(s):
            return round(s / 60, 1) if isinstance(s, (int, float)) else ""
        out[f"outcomes_day_{d}"] = [{
            "agent_id": a["agent_id"], "trip": t.get("trip_id"), "mode": t.get("realized_mode"),
            "line": t.get("route_id") or "", "left": t.get("departure"), "arrived": t.get("arrival"),
            "estimated_min": minutes(t.get("estimated_total_time_s")), "actual_min": minutes(t.get("actual_total_time_s")),
            "delay_ratio": t.get("delay_ratio"), "waited_min": minutes(t.get("actual_waiting_time_s")),
            "full_jeepneys_passed": t.get("full_pass_bys_experienced"),
            "distance_km": round(t["distance_m"] / 1000, 2) if t.get("distance_m") else "",
            "cost_php": t.get("cost_php"), "disrupted": t.get("disrupted"),
            "why": t.get("disruption_reason") or "", "completed": t.get("completed")}
            for a in outcomes for t in a.get("trips", [])]
        refl = (load(demand() / f"reflection_day_{d}.json") or {}).get("agents", [])
        out[f"reflection_day_{d}"] = [{
            "agent_id": r["agent_id"], "experience": r["travel_experience"],
            "habit_update": json.dumps(r["habit_update"]) if r["habit_update"] else "(no change)"} for r in refl]

    out["failures"] = [{"time": r["time"], "task": r["task"], "day": r.get("day") or "", "agent_id": r["agent_id"],
                        "try": r["try"], "problems": "; ".join(r["problems"])}
                       for r in attempts() if not r["passes"]]
    return {k: v for k, v in out.items() if v or k == "progress"}


def write_xlsx(data, path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in data.items():
        ws = wb.create_sheet(title[:31])
        cols = list(dict.fromkeys(k for r in rows for k in r)) or ["(nothing yet)"]
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) if not isinstance(r.get(c), (list, dict)) else json.dumps(r.get(c)) for c in cols])
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="2F5496")
        ws.freeze_panes = "B2"
        for i, c in enumerate(cols, 1):
            longest = max([len(str(c))] + [len(str(r.get(c) or "")) for r in rows[:200]])
            width = min(max(10, longest + 2), 70)
            ws.column_dimensions[ws.cell(1, i).column_letter].width = width
            if width == 70:
                for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    row[0].alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--summary", action="store_true", help="print the progress only")
    ap.add_argument("--out", default=str(C.ABM_DIR.parent / "report.xlsx"))
    args = ap.parse_args(argv)
    rows = progress_rows()
    print(f"{'task':<11}{'day':>4}{'answered':>12}{'first try':>11}{'regen':>7}{'failed':>8}  status")
    for r in rows:
        print(f"{r['task']:<11}{str(r['day']):>4}{r['answered']:>6} of {r['agents']:<4}{r['first_try_rate']:>10}"
              f"{r['regenerations']:>7}{r['failed_all_tries']:>8}  {r['status']}")
    if not args.summary:
        write_xlsx(sheets(), args.out)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
