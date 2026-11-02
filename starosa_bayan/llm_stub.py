"""Stand-in for the LLM: writes the demand file demand/day_1.json from demand/agents.json, in the
format the LLM will use (demand/README.md). Its choices are placeholders, not behaviour:
  * one activity per agent at a random location, with a start time and stay drawn from
    ACTIVITY_TIMES for its category,
  * one mode both ways: of the modes the agent can use (a car or bike only if they have one),
    the one with the most of its MODE_SHARE still to fill, if it is feasible both ways to some
    location (convert_demand.py's rules; locations tried in random order). Otherwise the next
    mode. An agent with no feasible mode to any location stays home,
  * they leave home the free-flow estimate before the start, so they arrive on time if the
    roads are free.
Unlike a real LLM it sees the feasibility rules, so its file always converts in full.

Refuses to overwrite the demand file unless --force: it may be the LLM's.

    python llm_stub.py [--force]
"""

import argparse
import json
import random
from collections import Counter

import settings as S
from convert_demand import MODES, VCLASS, Planner, clock, home_id, load_agents

ACTIVITY = {"workplace": "work"}          # activity name per location category; others keep the category


def quotas(n):
    """Agents per mode: MODE_SHARE of n, the rounding remainder to the largest leftovers."""
    total = sum(S.MODE_SHARE.values())
    exact = {m: n * S.MODE_SHARE.get(m, 0) / total for m in MODES}
    q = {m: int(exact[m]) for m in MODES}
    for m in sorted(MODES, key=lambda m: exact[m] - q[m], reverse=True)[:n - sum(q.values())]:
        q[m] += 1
    return q


def main():
    parser = argparse.ArgumentParser(description="Write a stand-in demand file, as the LLM will.")
    parser.add_argument("--force", action="store_true", help="overwrite the demand file")
    args = parser.parse_args()
    if S.DEMAND_FILE.is_file() and not args.force:
        raise SystemExit(f"{S.DEMAND_FILE.relative_to(S.HERE)} exists (it may be the LLM's). "
                         "Use --force to replace it.")

    rng = random.Random(S.SEED)
    agents = load_agents()
    planner = Planner()
    location_ids = sorted(planner.locations)
    quota, chosen, schedules = quotas(len(agents)), Counter(), {}
    order = sorted(agents)
    rng.shuffle(order)
    with planner:
        for aid in order:
            info, home = agents[aid], planner.home_place(agents[aid]["home"])
            plan = None
            usable = [m for m in MODES if m not in VCLASS or info[f"has_{m}"]]
            rng.shuffle(usable)
            usable.sort(key=lambda m: chosen[m] - quota[m])       # most share left first; ties stay random
            for mode in usable if home else []:
                for loc in rng.sample(location_ids, len(location_ids)):
                    dest = planner.locations[loc]["place"]
                    out, _ = planner.leg(mode, home, dest, at_house=(True, False))
                    back, _ = planner.leg(mode, dest, home, at_house=(False, True)) if out else (None, None)
                    if back:
                        plan = mode, loc, out
                        break
                if plan:
                    break
            if plan is None:
                schedules[aid] = [{"location_id": home_id(aid), "activity": "home"}]
                continue
            mode, loc, out = plan
            chosen[mode] += 1
            category = planner.locations[loc]["category"]
            first, last, shortest, longest = S.ACTIVITY_TIMES[category]
            start = round(rng.uniform(first, last) * 60) * 60
            end = start + round(rng.uniform(shortest, longest) * 60) * 60
            leave = (start - out["est_s"]) // 60 * 60
            schedules[aid] = [
                {"location_id": home_id(aid), "activity": "home", "end": clock(leave)},
                {"location_id": loc, "activity": ACTIVITY.get(category, category), "start": clock(start),
                 "end": clock(end), "mode": mode, "reasoning": f"llm_stub: {mode} is feasible both ways"},
                {"location_id": home_id(aid), "activity": "home", "mode": mode}]

    made_by = f"llm_stub.py (stand-in for the LLM), seed {S.SEED}"
    agents_text = []
    for aid in sorted(schedules):   # one schedule entry per line: easy to read and to diff
        entries = ",\n        ".join(json.dumps(e, ensure_ascii=False) for e in schedules[aid])
        agents_text.append(f'    {{"agent_id": "{aid}", "schedule": [\n        {entries}\n    ]}}')
    S.DEMAND_FILE.parent.mkdir(exist_ok=True)
    S.DEMAND_FILE.write_text('{\n  "day": 1,\n' f'  "made_by": {json.dumps(made_by)},\n  "agents": [\n'
                             + ",\n".join(agents_text) + "\n  ]\n}\n", encoding="utf-8")
    json.loads(S.DEMAND_FILE.read_text(encoding="utf-8"))   # check it is valid JSON
    home_all_day = sum(len(s) == 1 for s in schedules.values())
    print(f"{len(schedules)} agents: " + ", ".join(f"{m} {chosen[m]} (aim {quota[m]})" for m in MODES)
          + f", home all day {home_all_day}")
    print(f"Wrote {S.DEMAND_FILE.relative_to(S.HERE)}")


if __name__ == "__main__":
    main()
