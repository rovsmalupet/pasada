"""Stand-in for the LLM, for all five of its tasks (llm_tasks.py). It reads each task's input file
(demand/llm_inputs/), exactly what a prompt gets, and answers in the LLM's format
(demand/README.md), so the pipeline runs end to end before the LLM is connected. Its answers are
rules of thumb, not behaviour:

  persona     car: the household's, from CAR_DRIVER_MIN_AGE; bicycle: the household's. Sensitivities
              (attributes, walking, cycling) from the income decile, age and role.
  habits      employed: work Mon-Fri (some also Sat), some of them out of the area from a terminal;
              students: school Mon-Fri; others: shopping on 2 or 3 days a week. Work or school at
              any location of that kind the agent is offered; shopping at one of the 3 nearest.
              There and back. Weights from the distance, the persona and which modes reach the place.
  schedule    today's recurring trips, each leaving within 10 min of its usual time; 10 min earlier
              from home after a day whose travel experience reported delays or full jeepneys.
  modes       per trip, the option with the highest weight among those arriving before the next
              trip leaves (the fastest line for a jeepney). A car or bike taken from home is kept
              for the day's trips, so it comes back.
  reflection  notes disrupted and slow trips and full jeepneys, and moves up to 0.1 of those trips'
              weight from that mode to the others.

    python llm_stub.py TASK [--day N]     (TASK: persona, habits, schedule, modes, reflection)
"""

import argparse
import math
import random

import settings as S
from convert_demand import VCLASS, age_from, clock, load_json, seconds
from llm_tasks import TASKS, answer_path, input_path, write

MADE_BY = "llm_stub.py (stand-in for the LLM)"
# habits: purpose -> (earliest start, latest start, shortest stay, longest stay), in hours of the day
TIMES = {"work": (7.0, 9.0, 8.0, 10.0), "out_of_area": (5.0, 8.0, 9.0, 11.0), "school": (7.0, 8.0, 6.0, 9.0),
         "shopping": (7.0, 11.0, 0.5, 1.5)}
LEAVE_BEFORE_S = 1800        # habits: leave home this long before the activity starts
OUT_OF_AREA_SHARE = 0.15     # habits: employed agents who work outside the area (by bus from a terminal)
SLOW = 1.5                   # reflection: a trip this many times its estimate is worth noting
SHIFT = 0.1                  # reflection: weight moved away from a mode after a bad trip


def rng_for(*parts):
    return random.Random("-".join(map(str, (S.SEED, *parts))))   # str seeds are stable across runs


def normalised(scores):
    """Weights adding up to exactly 1, rounded to 2 decimals (the rounding left on the largest)."""
    total = sum(scores.values())
    w = {m: round(s / total, 2) for m, s in scores.items()}
    top = max(w, key=w.get)
    w[top] = round(w[top] + 1 - sum(w.values()), 2)
    return w


# ----------------------------------------------------------------------------- tasks
def persona(a):
    age, decile, kind = age_from(a["age_group"]), a["per_capita_income_decile"], a["commuter_type"]
    car = a["household_car_access"] and age >= S.CAR_DRIVER_MIN_AGE
    bike = a["household_bicycle_access"]
    sens = {"safety": "High" if age >= 60 or age < 15 else "Moderate",
            "cost": "High" if decile <= 3 else "Moderate" if decile <= 7 else "Low",
            "comfort": "High" if decile >= 8 or age >= 60 else "Moderate",
            "accessibility": "High" if age >= 60 else "Moderate",
            "availability": "Moderate",
            "efficiency": {"employed": "High", "student": "Moderate"}.get(kind, "Low"),
            "reliability": "High" if kind in ("employed", "student") else "Moderate"}
    role = {"employed": f"employed ({a['occupation_category'] or 'occupation unknown'})", "student": "a student"}
    prefers = "the household car" if car and decile >= 6 else "jeepneys for longer trips and walking for short ones"
    return {"description": f"{a['sex']}, aged {a['age_group']}, living in {a['barangay']}; "
                           f"{role.get(kind, 'not working')}; per capita income decile {decile} of 10.",
            "private_car_access": car, "bicycle_access": bike,
            # sensitivity: High = it discourages them (was a predisposition, High = willing; values flipped)
            "walking_sensitivity": "High" if age >= 65 else "Low" if decile <= 5 else "Moderate",
            "cycling_sensitivity": "Moderate" if bike and 10 <= age < 60 else "High",
            "sensitivities": sens, "general_transport_preference": f"Prefers {prefers}."}


def weights_for(loc, modes, p):
    """Weights for a trip to loc: only modes that reach it, scored by distance and persona."""
    d, sens = loc["distance_m"], p["sensitivities"]
    level = {"High": 1.3, "Moderate": 1.0, "Low": 0.5}
    tolerance = {"High": 0.5, "Moderate": 1.0, "Low": 1.3}   # walking/cycling sensitivity: High = less of it
    score = {}
    for m in modes:
        if m not in loc["reachable_by"]:
            score[m] = 0.0
        elif m == "walk":
            score[m] = (1.0 if d < 800 else 0.5 if d < 1500 else 0.1) * tolerance[p["walking_sensitivity"]]
        elif m == "jeepney":
            score[m] = (0.3 if d < 800 else 1.0) * (1.2 if sens["cost"] == "High" else 1.0)
        elif m == "bike":
            score[m] = (0.8 if d < 4000 else 0.2) * tolerance[p["cycling_sensitivity"]]
        else:
            score[m] = {"Low": 1.5, "Moderate": 1.0, "High": 0.5}[sens["cost"]] * (1.3 if sens["comfort"] == "High" else 1.0)
    return normalised(score)


def habits(entry):
    a, places = entry["agent"], entry["activity_locations"]
    aid, home, kind = a["agent_id"], a["home_id"], a["commuter_type"]
    rng = rng_for("habits", aid)
    if kind == "employed":
        purpose = "out_of_area" if rng.random() < OUT_OF_AREA_SHARE else "work"
        days = list(S.WEEKDAYS[:5]) + (["Sat"] if rng.random() < 0.3 else [])
    elif kind == "student":
        purpose, days = "school", list(S.WEEKDAYS[:5])
    else:
        purpose, days = "shopping", sorted(rng.sample(S.WEEKDAYS, rng.randint(2, 3)), key=S.WEEKDAYS.index)
    kinds = {"work": ("workplace",), "out_of_area": ("terminal",)}.get(purpose, S.ACTIVITIES[purpose])
    pool = [l for l in places if l["category"] in kinds][:3 if purpose == "shopping" else None]
    if not pool and places:                      # nothing of that kind within reach: shopping, else anything
        purpose = "shopping" if any(l["category"] in S.ACTIVITIES["shopping"] for l in places) else "work"
        pool = [l for l in places if l["category"] in S.ACTIVITIES[purpose]][:3]
    if not pool:
        return {"agent_id": aid, "frequent_locations": [], "recurring_trips": []}
    loc = rng.choice(pool)
    first, last, shortest, longest = TIMES.get(purpose, TIMES["shopping"])
    start = round(rng.uniform(first, last) * 4) * 900                 # quarter hours
    back = start + round(rng.uniform(shortest, longest) * 4) * 900
    w = weights_for(loc, entry["usable_modes"], a["persona"])
    trips = [{"trip_key": "R1", "origin": home, "destination": loc["location_id"], "purpose": purpose, "days": days,
              "usual_departure": clock(max(start - LEAVE_BEFORE_S, 0)), "mode_preference_weights": w},
             {"trip_key": "R2", "origin": loc["location_id"], "destination": home, "purpose": "home", "days": days,
              "usual_departure": clock(min(back, 23 * 3600)), "mode_preference_weights": w}]
    return {"agent_id": aid, "frequent_locations": [{"location_id": loc["location_id"], "activity": purpose}],
            "recurring_trips": trips}


def schedule(entry, today):
    a, home = entry["agent"], entry["agent"]["home_id"]
    rng = rng_for("schedule", a["agent_id"], today)
    todays = sorted((t for t in entry["recurring_trips"] if today in t["days"]), key=lambda t: t["usual_departure"])
    if not todays:
        return [{"location_id": home, "activity": "home", "reasoning": "llm_stub: no recurring trip today"}]
    last = entry["travel_experience"][-1]["reflection"] if entry["travel_experience"] else ""
    earlier = 600 if any(w in last for w in ("longer than the", "full jeepney", "disrupted")) else 0
    leave, prev = [], -60
    for t in todays:
        s = seconds(t["usual_departure"]) + rng.randint(-10, 10) * 60 - (earlier if t["origin"] == home else 0)
        prev = min(max(s, prev + 60, 0), 86340)
        leave.append(prev)
    plan = [{"location_id": home, "activity": "home", "end": clock(leave[0])}]
    for i, t in enumerate(todays):
        e = {"location_id": t["destination"], "activity": t["purpose"]}
        if i + 1 < len(todays):
            e["end"] = clock(leave[i + 1])
        plan.append({**e, "reasoning": f"llm_stub: recurring trip {t['trip_key']}, usually at {t['usual_departure']}"
                                       + (", 10 min early after yesterday" if earlier and t["origin"] == home else "")})
    return plan


def modes(entry):
    trips, home = entry["trips"], entry["agent"]["home_id"]
    weights = {(t["origin"], t["destination"]): t["mode_preference_weights"] for t in entry["recurring_trips"]}
    for_day = {m for m in VCLASS if all(any(o["mode"] == m for o in t["options"]) for t in trips)}
    vehicle, picks = None, []
    for i, trip in enumerate(trips):
        due = seconds(trips[i + 1]["depart"]) - seconds(trip["depart"]) if i + 1 < len(trips) else math.inf
        w = weights.get((trip["from"], trip["to"]), {})
        options = [o for o in trip["options"] if o["mode"] not in VCLASS or o["mode"] == vehicle
                   or (vehicle is None and i == 0 and trip["from"] == home and o["mode"] in for_day)]
        if vehicle:
            options = [o for o in options if o["mode"] == vehicle]
        if not options:
            options = trip["options"]                 # cannot be made by the rules: the checks will say so
        best = max(options, key=lambda o: (o["estimated_total_time_s"] <= due, w.get(o["mode"], 0),
                                           -o["estimated_total_time_s"]))
        if i == 0 and best["mode"] in VCLASS:
            vehicle = best["mode"]
        pick = {"trip_id": trip["trip_id"], "mode": best["mode"]}
        if best["mode"] == "jeepney":
            pick["route_id"] = best["route_id"]
        why = (f"llm_stub: highest preference ({best['mode']} {w.get(best['mode'], 0):.2f}) among the options arriving "
               f"in time; about {round(best['estimated_total_time_s'] / 60)} min, PHP {best['cost_php']:g}")
        picks.append({**pick, "reasoning": why})
    return picks


def reflection(entry, day):
    notes, bad = [], set()
    for t in entry["trips"]:
        o = t["outcome"]
        if o is None:
            continue
        where = f"the {o['realized_mode']} trip to {t['to_name']}"
        if o["disrupted"]:
            notes.append(f"{where} was disrupted: it {o['disruption_reason']}")
        elif o["delay_ratio"] is not None and o["delay_ratio"] >= SLOW:
            notes.append(f"{where} took {round(o['actual_total_time_s'] / 60)} min, longer than the "
                         f"{round(o['estimated_total_time_s'] / 60)} min expected")
        if o["full_pass_bys_experienced"]:
            notes.append(f"{o['full_pass_bys_experienced']} full jeepney(s) passed me on {where}")
        if o["disrupted"] or o["full_pass_bys_experienced"] or (o["delay_ratio"] or 0) >= SLOW:
            bad.add((t["from"], t["to"], o["realized_mode"]))
    if "left_out" in entry:
        text = f"Day {day}: my day could not be made as planned ({entry['left_out']})."
    elif not entry["trips"]:
        text = f"Day {day}: stayed home."
    elif not notes:
        text = f"Day {day}: an ordinary day; my {len(entry['trips'])} trip(s) went about as expected."
    else:
        text = f"Day {day}: " + "; ".join(notes) + ". I will rely less on that mode for these trips."
    update = {}
    for t in entry["recurring_trips"]:
        w = t["mode_preference_weights"]
        for frm, to, mode in bad:
            others = [m for m in w if m != mode and w[m] > 0]
            if (t["origin"], t["destination"]) == (frm, to) and w.get(mode, 0) > 0 and others:
                shift = min(SHIFT, w[mode])
                update[t["trip_key"]] = normalised({m: w[m] - shift if m == mode else w[m] + shift / len(others)
                                                    if m in others else w[m] for m in w})
    return {"travel_experience": text, "habit_update": update}


# ------------------------------------------------------------------------------ main
def answer(task, day=None):
    """Answer the task's input file in its answer file."""
    bundle = load_json(input_path(task, day), "run python pipeline.py, which writes it")
    agents = bundle["agents"]
    if task == "persona":
        out = [{"agent_id": a["agent_id"], "persona": persona(a)} for a in agents]
    elif task == "habits":
        out = [habits(e) for e in agents]
    elif task == "schedule":
        out = [{"agent_id": e["agent"]["agent_id"], "schedule": schedule(e, bundle["weekday"])} for e in agents]
    elif task == "modes":
        out = [{"agent_id": e["agent"]["agent_id"], "selections": modes(e)} for e in agents]
    else:
        out = [{"agent_id": e["agent"]["agent_id"], **reflection(e, day)} for e in agents]
    write(answer_path(task, day), ({"day": day} if day else {}) | {"made_by": MADE_BY}, out)
    print(f"llm_stub.py answered {len(out)} agents' {task} in {S.rel(answer_path(task, day))}")


def main():
    parser = argparse.ArgumentParser(description="Stand in for the LLM: answer one task's input file.")
    parser.add_argument("task", choices=list(TASKS))
    parser.add_argument("--day", type=int, default=1, help="the day (schedule, modes, reflection)")
    args = parser.parse_args()
    answer(args.task, args.day if TASKS[args.task][2] else None)


if __name__ == "__main__":
    main()
