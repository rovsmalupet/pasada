"""Stand-in for the LLM, in the formats the LLM will use (demand/README.md), for both of its steps
of a day. Its choices are placeholders, not behaviour.

  schedule (step 1) -> settings.SCHEDULE_FILE, from demand/agents.json: who goes where and when,
    no modes.
      * one activity per agent at a random location, with a start time and stay drawn from
        ACTIVITY_TIMES for its category,
      * the location is one the agent can reach and leave by some mode: the mode with the most
        MODE_SHARE left of those it can use, if it is feasible both ways (convert_demand.py's
        rules; locations tried in random order), otherwise the next. Nobody reachable: home all
        day. Unlike a real LLM it sees the feasibility rules, so every trip has an option,
      * they leave home that mode's free-flow estimate before the start.
  modes (step 3) -> settings.MODES_FILE, from the choices file (make_choices.py, step 2): an
    option per trip.
      * one mode for all of an agent's trips (so a car or bike comes home): of the modes offered
        on every trip, the one with the most MODE_SHARE left; the fastest jeepney line,
      * if no mode is offered on every trip, the fastest option per trip, on foot or by jeepney
        if there is one.

Refuses to overwrite a file unless --force: it may be the LLM's.

    python llm_stub.py schedule [--force]
    python llm_stub.py modes [--force]
"""

import argparse
import json
import random
from collections import Counter

import settings as S
from convert_demand import MODES, VCLASS, Planner, clock, home_id, load_agents, load_json

ACTIVITY = {"workplace": "work"}          # activity name per location category; others keep the category
MADE_BY = f"llm_stub.py (stand-in for the LLM), seed {S.SEED}"


def quotas(n):
    """Agents per mode: MODE_SHARE of n, the rounding remainder to the largest leftovers."""
    total = sum(S.MODE_SHARE.values())
    exact = {m: n * S.MODE_SHARE.get(m, 0) / total for m in MODES}
    q = {m: int(exact[m]) for m in MODES}
    for m in sorted(MODES, key=lambda m: exact[m] - q[m], reverse=True)[:n - sum(q.values())]:
        q[m] += 1
    return q


def write(path, top, key, items, label):
    """{top..., key: [{label: id, ...one entry per line...}]}: easy to read and to diff."""
    blocks = []
    for item_id, entries in items:
        body = ",\n        ".join(json.dumps(e, ensure_ascii=False) for e in entries)
        blocks.append(f'    {{"agent_id": "{item_id}", "{label}": [\n        {body}\n    ]}}')
    path.parent.mkdir(exist_ok=True)
    path.write_text("{\n" + "".join(f"  {json.dumps(k)}: {json.dumps(v)},\n" for k, v in top.items())
                    + f'  "{key}": [\n' + ",\n".join(blocks) + "\n  ]\n}\n", encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))   # check it is valid JSON


def schedules():
    rng = random.Random(S.SEED)
    agents = load_agents()
    planner = Planner()
    location_ids = sorted(planner.locations)
    quota, chosen, plans = quotas(len(agents)), Counter(), {}
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
                plans[aid] = [{"location_id": home_id(aid), "activity": "home"}]
                continue
            mode, loc, out = plan
            chosen[mode] += 1
            category = planner.locations[loc]["category"]
            first, last, shortest, longest = S.ACTIVITY_TIMES[category]
            start = round(rng.uniform(first, last) * 60) * 60
            end = start + round(rng.uniform(shortest, longest) * 60) * 60
            leave = (start - out["est_s"]) // 60 * 60
            plans[aid] = [
                {"location_id": home_id(aid), "activity": "home", "end": clock(leave)},
                {"location_id": loc, "activity": ACTIVITY.get(category, category), "start": clock(start),
                 "end": clock(end), "reasoning": "llm_stub: a random place of this kind that can be reached"},
                {"location_id": home_id(aid), "activity": "home"}]
    write(S.SCHEDULE_FILE, {"day": 1, "made_by": MADE_BY}, "agents", sorted(plans.items()), "schedule")
    home_all_day = sum(len(p) == 1 for p in plans.values())
    print(f"{len(plans)} agents, home all day {home_all_day}. Places chosen for "
          + ", ".join(f"{m} {chosen[m]} (aim {quota[m]})" for m in MODES) + " (the modes are chosen in step 3)")
    print(f"Wrote {S.SCHEDULE_FILE.relative_to(S.HERE)}")


def modes():
    choices = load_json(S.CHOICES_FILE, "run python make_choices.py first")
    rng = random.Random(S.SEED)
    travellers = [a for a in choices["agents"] if a["trips"]]
    quota, chosen, picks, split = quotas(len(travellers)), Counter(), {}, 0
    order = sorted(travellers, key=lambda a: a["agent_id"])
    rng.shuffle(order)
    for agent in order:
        trips = agent["trips"]
        common = set(MODES)
        for trip in trips:
            common &= {o["mode"] for o in trip["options"]}
        usable = sorted(common)
        rng.shuffle(usable)
        usable.sort(key=lambda m: chosen[m] - quota[m])           # most share left first; ties stay random
        selections = []
        if usable:
            mode = usable[0]
            chosen[mode] += 1
            for trip in trips:
                best = min((o for o in trip["options"] if o["mode"] == mode), key=lambda o: o["estimated_total_time_s"])
                selections.append(selection(trip, best, f"llm_stub: {mode} on every trip"))
        else:
            split += 1
            for trip in trips:
                on_foot = [o for o in trip["options"] if o["mode"] not in VCLASS] or trip["options"]
                if on_foot:
                    best = min(on_foot, key=lambda o: o["estimated_total_time_s"])
                    selections.append(selection(trip, best, "llm_stub: no mode fits every trip; the fastest option"))
        picks[agent["agent_id"]] = selections
    write(S.MODES_FILE, {"day": choices["day"], "made_by": MADE_BY}, "agents", sorted(picks.items()), "selections")
    print(f"{len(picks)} agents with trips: " + ", ".join(f"{m} {chosen[m]} (aim {quota[m]})" for m in MODES)
          + f"; {split} with no mode offered on every trip (fastest option per trip)")
    print(f"Wrote {S.MODES_FILE.relative_to(S.HERE)}")


def selection(trip, option, reasoning):
    pick = {"trip_id": trip["trip_id"], "mode": option["mode"]}
    if option["mode"] == "jeepney":
        pick["route_id"] = option["route_id"]
    return {**pick, "reasoning": reasoning}


def main():
    parser = argparse.ArgumentParser(description="Stand in for the LLM: write a schedule or choose modes.")
    parser.add_argument("step", choices=["schedule", "modes"], help="schedule: step 1; modes: step 3")
    parser.add_argument("--force", action="store_true", help="overwrite the file")
    args = parser.parse_args()
    path = S.SCHEDULE_FILE if args.step == "schedule" else S.MODES_FILE
    if path.is_file() and not args.force:
        raise SystemExit(f"{path.relative_to(S.HERE)} exists (it may be the LLM's). Use --force to replace it.")
    schedules() if args.step == "schedule" else modes()


if __name__ == "__main__":
    main()
