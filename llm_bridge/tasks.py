"""The five tasks between the simulation's files and the evaluated prompts.

For each task: `prompt(entry, head)` turns one agent's line of the simulation's input file
(starosa_bayan/demand/llm_inputs/) into the evaluated prompt, and `answer(text, entry, head)`
checks the model's answer and turns it into that agent's line of the simulation's answer file
(format: starosa_bayan/demand/README.md). An answer with problems is regenerated (answer.py).

The checks are the evaluation notebook's (Part 4.4) plus the simulation's own (llm_tasks.py), so
an answer that passes here also passes the pipeline. Turning one format into the other is only
renaming (mode names, day names, field names) and filling what follows from the answer itself;
it never changes a choice the model made.
"""

import re

from . import config as C
from . import kb
from .prompts import (mode_prompt, parse_json, persona_prompt, reflection_prompt, schedule_prompt,
                      habits_prompt)

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PERSONA_FIELDS = ["behavioral_description", "vehicle_ownership", "bicycle_access", "walking_sensitivity",
                  "cycling_sensitivity", "sensitivities", "general_transport_preference"]


# ------------------------------------------------------------------------ helpers
def home_id(aid):
    return f"HOME_{aid}"


# INTEGRATION (v0.3-abm5, team meeting 2026-11-10): the prompts never show a location id. The model
# names a place by its category ("home", "healthcare", "market", ...), and the bridge uses the
# agent's NEAREST place of that category (straight line from home) among those it can reach and
# leave by one of its modes (the places llm_tasks.py offers at the habits step). The same category
# is always the same place for an agent, so "work at healthcare" is the same clinic every day.
HOME = "home"
_CACHE = {}


def _json(name):
    import json
    path = C.ABM_DIR / "demand" / name
    if path not in _CACHE:
        _CACHE[path] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    return _CACHE[path]


def category_of(lid):
    """A location's category, from agents.json (every activity location of the run)."""
    if "categories" not in _CACHE:
        data = _json("agents.json") or {}
        _CACHE["categories"] = {l["location_id"]: l["category"] for l in data.get("locations", [])}
    return _CACHE["categories"].get(lid, "place")


def offered_places(aid):
    """The agent's reachable places, nearest first (habits_input.json's activity_locations)."""
    if "offered" not in _CACHE:
        data = _json("llm_inputs/habits_input.json") or {"agents": []}
        _CACHE["offered"] = {a["agent"]["agent_id"]: sorted(a["activity_locations"],
                                                            key=lambda l: l.get("distance_m") or 0)
                             for a in data["agents"]}
    return _CACHE["offered"].get(aid, [])


def nearest_by_category(places):
    out = {}
    for l in sorted(places, key=lambda l: l.get("distance_m") or 0):
        out.setdefault(l["category"], l)
    return out


def shown(lid, home):
    return HOME if lid == home else category_of(lid)


def place_word(value):
    return str(value or "").strip().lower().replace(" ", "_")


def age_from(age_group):
    m = re.match(r"\s*(\d+)", str(age_group))
    return int(m.group(1)) if m else 0


def clock(value):
    """'7:05', '07:05' or '07:05:00' as '07:05'; anything else as it was (and then it fails)."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*", str(value))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else str(value)


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def shown_agent(agent):
    """The agent as the evaluated prompts show it (notebook 2.2), from the simulation's agent."""
    shown = {"age_group": agent["age_group"], "sex": agent["sex"],
             "residential_barangay": agent.get("barangay", agent.get("residential_barangay")),
             "commuter_type": agent["commuter_type"], "employment_status": agent["employment_status"],
             "school_status": agent["school_status"], "household_income_group": agent["household_income_group"],
             "per_capita_income_decile": agent["per_capita_income_decile"],
             "household_car_access": agent["household_car_access"],
             "household_bicycle_access": agent["household_bicycle_access"]}
    if agent.get("occupation_category"):
        shown["occupation_category"] = agent["occupation_category"]
    if agent.get("school_status") == "attending_school":
        level = agent.get("current_education_level") or agent.get("education_level_eligibility")
        if level:
            shown["education_level"] = level
    return shown


def prompt_persona(p):
    """The simulation's persona (personas.json) in the evaluated prompts' fields."""
    def walking(kind):
        if f"{kind}_sensitivity" in p:
            return p[f"{kind}_sensitivity"]
        # a stand-in persona (llm_stub.py) has predispositions: the opposite scale
        return {"High": "Low", "Low": "High"}.get(p.get(f"{kind}_predisposition"), "Moderate")
    return {"behavioral_description": p["description"], "vehicle_ownership": p["private_car_access"],
            "bicycle_access": p["bicycle_access"], "walking_sensitivity": walking("walking"),
            "cycling_sensitivity": walking("cycling"), "sensitivities": p["sensitivities"],
            "general_transport_preference": p["general_transport_preference"]}


def commuter_of(agent):
    """notebook build_commuter: the shown attributes plus the persona."""
    return {**shown_agent(agent), "persona": prompt_persona(agent["persona"])}


def prompt_weights(w):
    return {C.TO_PROMPT_MODE[m]: v for m, v in w.items()}


def prompt_trips(recurring, home, weights=True):
    """The simulation's recurring trips in the evaluated prompts' fields."""
    out = []
    for t in recurring:
        row = {"trip_id": t["trip_key"], "origin": shown(t["origin"], home),
               "destination": shown(t["destination"], home),
               "trip_purpose": t["purpose"], "travel_days": [C.DAY_NAMES[d] for d in t["days"]],
               "usual_departure_time": t["usual_departure"]}
        if weights and "mode_preference_weights" in t:
            row["mode_preference_weights"] = prompt_weights(t["mode_preference_weights"])
        out.append(row)
    return out


def prompt_experience(experience):
    return [{"simulation_day": e["day"], "reflection": e["reflection"]} for e in experience]


def weight_problems(label, weights, usable):
    """Weights in the prompts' mode names -> (weights in the simulation's names, problems)."""
    if not isinstance(weights, dict) or not weights:
        return None, [f"{label} has no weights"]
    problems, out = [], {}
    for m, v in weights.items():
        abm = C.TO_ABM_MODE.get(str(m).strip().lower())
        if abm not in usable:
            problems.append(f"{label} uses a mode not available: {m}")
        elif not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1:
            problems.append(f"{label} has a weight that is not a number from 0 to 1")
        else:
            out[abm] = float(v)
    if problems:
        return None, problems
    if abs(sum(out.values()) - 1) > C.WEIGHT_SUM_TOLERANCE:
        return None, [f"{label} weights add up to {sum(out.values()):.2f}"]
    return {m: out.get(m, 0.0) for m in usable}, []      # a mode left out has weight 0


def parsed(text):
    try:
        data = parse_json(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# -------------------------------------------------------------------- 1. persona
def persona_prompt_of(entry, head):
    return persona_prompt(shown_agent(entry), kb.findings_text(kb.retrieve(entry))), C.MAX_NEW_TOKENS


def persona_answer(text, entry, head):
    p = parsed(text)
    if p is None:
        return None, ["not valid JSON"]
    problems = []
    missing = [f for f in PERSONA_FIELDS if f not in p]
    if missing:
        problems.append("missing " + ", ".join(missing))
    sens = p.get("sensitivities") if isinstance(p.get("sensitivities"), dict) else {}
    if [k for k in C.SENSITIVITIES if k not in sens]:
        problems.append("missing sensitivities " + ", ".join(k for k in C.SENSITIVITIES if k not in sens))
    levels = {**sens, "walking_sensitivity": p.get("walking_sensitivity"),
              "cycling_sensitivity": p.get("cycling_sensitivity")}
    wrong = [f"{k}={v!r}" for k, v in levels.items() if k in (*C.SENSITIVITIES, "walking_sensitivity",
                                                               "cycling_sensitivity") and v not in C.LEVELS]
    if wrong:
        problems.append("not Low/Moderate/High: " + ", ".join(wrong))
    for field in ("vehicle_ownership", "bicycle_access"):
        if field in p and not isinstance(p[field], bool):
            problems.append(f"{field} is not true or false")
    if p.get("vehicle_ownership") is True and not entry["household_car_access"]:
        problems.append("car access without a household car")
    if p.get("vehicle_ownership") is True and age_from(entry["age_group"]) < C.CAR_DRIVER_MIN_AGE:
        problems.append(f"car access below age {C.CAR_DRIVER_MIN_AGE} (simulation rule)")
    if p.get("bicycle_access") is True and not entry["household_bicycle_access"]:
        problems.append("bicycle access without a household bicycle")
    if "general_transport_preference" in p and p["general_transport_preference"] not in C.TO_ABM_MODE:
        problems.append(f"preference {p['general_transport_preference']!r} is not one of the four modes")
    if "behavioral_description" in p and not (isinstance(p["behavioral_description"], str)
                                              and p["behavioral_description"].strip()):
        problems.append("behavioral_description is not text")
    if problems:
        return None, problems
    persona = {"description": p["behavioral_description"].strip(), "private_car_access": p["vehicle_ownership"],
               "bicycle_access": p["bicycle_access"], C.WALKING_FIELD: p["walking_sensitivity"],
               C.CYCLING_FIELD: p["cycling_sensitivity"],
               "sensitivities": {k: sens[k] for k in C.SENSITIVITIES},
               "general_transport_preference": p["general_transport_preference"]}
    return {"agent_id": entry["agent_id"], "persona": persona}, []


# --------------------------------------------------------------------- 2. habits
def habits_locations(entry):
    """The place options: home, then each category the agent can reach, with its nearest place."""
    near = nearest_by_category(entry["activity_locations"])
    return [{"place": HOME}] + [{"place": cat, "nearest_distance_m": l.get("distance_m"),
                                 "reachable_by": [C.TO_PROMPT_MODE[m] for m in l.get("reachable_by", [])]}
                                for cat, l in sorted(near.items(), key=lambda kv: kv[1].get("distance_m") or 0)]


def habits_prompt_of(entry, head):
    if not entry["activity_locations"]:
        return None, None                      # nowhere to go: no trips (answered without the model)
    modes = [C.TO_PROMPT_MODE[m] for m in entry["usable_modes"]]
    return habits_prompt(commuter_of(entry["agent"]), habits_locations(entry), modes), C.MAX_NEW_TOKENS_LONG


ACTIVITY_WORDS = {"work": "work", "job": "work", "office": "work", "duty": "work", "shift": "work",
                  "school": "school", "class": "school", "study": "school",
                  "shopping": "shopping", "shop": "shopping", "market": "shopping", "groceries": "shopping",
                  "grocery": "shopping", "buy": "shopping",
                  "leisure": "leisure", "recreation": "leisure", "mall": "leisure", "eat": "leisure",
                  "food": "leisure", "restaurant": "leisure", "dine": "leisure", "park": "leisure",
                  "personal": "personal", "errand": "personal", "health": "personal", "hospital": "personal",
                  "clinic": "personal", "doctor": "personal", "checkup": "personal", "check_up": "personal",
                  "pharmacy": "personal", "church": "personal", "mass": "personal", "worship": "personal",
                  "bank": "personal", "service": "personal",
                  "out_of_area": "out_of_area", "out of area": "out_of_area", "terminal": "out_of_area",
                  "travel": "out_of_area"}


def default_activity(category):
    """A category's own activity: the first one (besides work) that happens at it, else work."""
    for act, cats in C.ACTIVITIES.items():
        if act != "work" and category in cats:
            return act
    return "work"


def activity_for(words, category, agent):
    """The simulation's activity name for a place, from what the model called it (its role, or the
    purpose of a trip there). Only renaming: if nothing it said fits the place's category, the
    category's own activity (a school is 'school' for a student, 'work' for anyone else)."""
    for w in words:
        w = str(w or "").strip().lower().replace("-", "_")
        for key, act in ACTIVITY_WORDS.items():
            if w == key or key in w:
                if category in C.ACTIVITIES[act]:
                    return act
                break
    if category == "school" and agent["commuter_type"] != "student":
        return "work"
    return default_activity(category)


def day_of(name):
    d = str(name).strip()[:3].title()
    return d if d in C.WEEKDAYS else None


def habits_answer(text, entry, head):
    agent, home = entry["agent"], entry["agent"]["home_id"]
    aid = agent["agent_id"]
    if not entry["activity_locations"]:
        return {"agent_id": aid, "frequent_locations": [], "recurring_trips": []}, []
    h = parsed(text)
    if h is None:
        return None, ["not valid JSON"]
    trips = h.get("recurring_trips")
    if not isinstance(trips, list) or not trips:
        return None, ["no recurring trips"]
    near = nearest_by_category(entry["activity_locations"])
    offered = {l["location_id"]: l for l in near.values()}

    def to_id(place):                          # a category -> the agent's nearest place of it
        w = place_word(place)
        return home if w in (HOME, home.lower()) else near[w]["location_id"] if w in near else None

    problems = []
    roles = {}
    for f in h.get("frequent_locations") or []:
        if not isinstance(f, dict):
            continue
        lid = to_id(f.get("place", f.get("location_id")))
        if lid is None:
            problems.append(f"unknown place {f.get('place', f.get('location_id'))!r} (use one of the place options)")
        elif lid != home:
            roles.setdefault(lid, []).append(f.get("role"))
    out_trips, used, keys = [], {}, set()
    for i, t in enumerate(trips):
        if not isinstance(t, dict):
            problems.append(f"trip {i + 1} is not an object")
            continue
        tid = str(t.get("trip_id") or f"R{i + 1}")
        missing = [f for f in ("origin", "destination", "trip_purpose", "travel_days", "usual_departure_time",
                               "mode_preference_weights") if f not in t]
        if missing:
            problems.append(f"{tid} missing " + ", ".join(missing))
            continue
        if tid in keys:
            problems.append(f"{tid}: trip_id used twice")
        keys.add(tid)
        for end in ("origin", "destination"):
            lid = to_id(t[end])
            if lid is None:
                problems.append(f"{tid}: unknown place {t[end]!r} (use one of the place options)")
            t[end] = lid
        if t["origin"] == t["destination"]:
            problems.append(f"{tid}: origin and destination are the same")
        if t["destination"] in offered:
            used.setdefault(t["destination"], []).append(t["trip_purpose"])
        if t["origin"] in offered:
            used.setdefault(t["origin"], [])
        days = [day_of(d) for d in t["travel_days"]] if isinstance(t["travel_days"], list) else [None]
        if not days or None in days or len(set(days)) != len(days):
            problems.append(f"{tid}: travel_days must be different days of the week")
        dep = clock(t["usual_departure_time"])
        if not HHMM.match(dep):
            problems.append(f"{tid} time {t['usual_departure_time']!r} is not HH:MM")
        weights, more = weight_problems(tid, t["mode_preference_weights"], entry["usable_modes"])
        problems += more
        out_trips.append({"trip_key": tid, "origin": t["origin"], "destination": t["destination"],
                          "purpose": None, "days": days, "usual_departure": dep,
                          "mode_preference_weights": weights})
    if problems:
        return None, problems
    activity = {lid: activity_for(roles.get(lid, []) + used.get(lid, []), offered[lid]["category"], agent)
                for lid in list(roles) + [u for u in used if u not in roles]}
    for t in out_trips:
        t["purpose"] = "home" if t["destination"] == home else activity[t["destination"]]
    for day in C.WEEKDAYS:                     # each day's trips: a round from home back home
        todays = sorted((t for t in out_trips if day in t["days"]), key=lambda t: t["usual_departure"])
        at, last = home, None
        for t in todays:
            if t["usual_departure"] == last:
                problems.append(f"{day}: {t['trip_key']} leaves at the same time as the trip before")
                break
            if t["origin"] != at:
                problems.append(f"{day}: {t['trip_key']} leaves {t['origin']} at {t['usual_departure']}, "
                                f"but you are at {at} then")
                break
            at, last = t["destination"], t["usual_departure"]
        else:
            if todays and at != home:
                problems.append(f"{day}: the day's trips end at {at}, not at home")
    if problems:
        return None, problems
    frequent = [{"location_id": lid, "activity": act} for lid, act in activity.items()]
    return {"agent_id": aid, "frequent_locations": frequent, "recurring_trips": out_trips}, []


# ------------------------------------------------------------------- 3. schedule
def schedule_habits(entry):
    a = entry["agent"]
    frequent = [{"place": HOME, "role": "home"}] + \
               [{"place": f["category"], "role": f["activity"]} for f in entry["frequent_locations"]]
    return {"frequent_places": frequent,
            "recurring_trips": prompt_trips(entry["recurring_trips"], a["home_id"], weights=False)}


def schedule_places(entry):
    """category -> the place it means for this agent: its frequent places first, then the nearest
    reachable place of every other category."""
    places = {f["category"]: f["location_id"] for f in entry["frequent_locations"]}
    for cat, l in nearest_by_category(offered_places(entry["agent"]["agent_id"])).items():
        places.setdefault(cat, l["location_id"])
    return places




def other_places(entry):
    """INTEGRATION (v0.3-abm4/5): the categories besides the frequent ones that the schedule may
    use, each with its nearest reachable place's distance (habits_input.json)."""
    frequent = {f["category"] for f in entry["frequent_locations"]}
    return [{"place": cat, "nearest_distance_m": l.get("distance_m")}
            for cat, l in sorted(nearest_by_category(offered_places(entry["agent"]["agent_id"])).items(),
                                 key=lambda kv: kv[1].get("distance_m") or 0) if cat not in frequent]


def schedule_prompt_of(entry, head):
    context = {"simulation_day": head["day"], "day_of_week": C.DAY_NAMES[head["weekday"]],
               "conditions": head["simulation_context"]}
    return (schedule_prompt(commuter_of(entry["agent"]), schedule_habits(entry),
                            prompt_experience(entry["travel_experience"]), context, other_places(entry)),
            C.MAX_NEW_TOKENS)


def schedule_answer(text, entry, head):
    agent = entry["agent"]
    aid, home = agent["agent_id"], agent["home_id"]
    s = parsed(text)
    if s is None:
        return None, ["not valid JSON"]
    items = s.get("schedule")
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        return None, ["no schedule"]
    activity = {f["location_id"]: f["activity"] for f in entry["frequent_locations"]}
    places = schedule_places(entry)
    problems = []
    for i, it in enumerate(items):
        w = place_word(it.get("place", it.get("location_id")))
        lid = home if w in (HOME, home.lower()) else places.get(w)
        it["location_id"] = lid
        if lid is None:
            problems.append(f"entry {i + 1}: {it.get('place')!r} is not home or one of the listed places")
        elif lid != home and lid not in activity:          # a category besides the frequent ones
            activity[lid] = activity_for([it.get("trip_purpose"), it.get("activity")], w, agent)
        for key in ("start_time", "end_time"):
            if key in it and it[key] not in (None, "") and not HHMM.match(clock(it[key])):
                problems.append(f"entry {i + 1}: {key} {it[key]!r} is not HH:MM")
    if problems:
        return None, problems
    if items[0]["location_id"] != home:
        problems.append("the day does not start at home")
    if items[-1]["location_id"] != home:
        problems.append("the day does not end at home")
    stays = []                                 # consecutive entries at one place are one stay
    for it in items:
        start = clock(it["start_time"]) if it.get("start_time") else None
        end = clock(it["end_time"]) if it.get("end_time") else None
        if stays and stays[-1]["location_id"] == it["location_id"]:
            stays[-1]["end"] = end
            continue
        stays.append({"location_id": it["location_id"], "start": start, "end": end,
                      "reasoning": it.get("activity") if isinstance(it.get("activity"), str) else None})
    prev_end = None
    for j, st in enumerate(stays):
        last = j == len(stays) - 1
        if not last and not st["end"]:
            problems.append(f"{st['location_id']} has no end_time (when you leave it)")
            continue
        if st["start"] and prev_end is not None and minutes(st["start"]) < prev_end:
            problems.append(f"{st['location_id']} starts at {st['start']}, before you left the place before")
        if not last:
            if prev_end is not None and minutes(st["end"]) <= prev_end:
                problems.append(f"{st['location_id']}: end_time {st['end']} is not after the previous end_time")
            if st["start"] and minutes(st["start"]) > minutes(st["end"]):
                problems.append(f"{st['location_id']}: starts after it ends")
            prev_end = minutes(st["end"])
    if problems:
        return None, problems
    schedule = []
    for j, st in enumerate(stays):
        e = {"location_id": st["location_id"],
             "activity": "home" if st["location_id"] == home else activity[st["location_id"]]}
        if j > 0 and st["start"]:
            e["start"] = st["start"]
        if j < len(stays) - 1:
            e["end"] = st["end"]
        if st["reasoning"]:
            e["reasoning"] = st["reasoning"].strip()
        schedule.append(e)
    if len(schedule) == 1:                     # stays home all day: one entry, no end
        schedule[0].pop("end", None)
    return {"agent_id": aid, "schedule": schedule}, []


# ---------------------------------------------------------------------- 4. modes
OPTION_FIELDS = ("estimated_total_time_s", "access_time_s", "waiting_time_s", "in_vehicle_time_s",
                 "egress_time_s")


def best_options(trip):
    """One option per mode: for the jeepney, the fastest line (the line is a route, not a mode)."""
    best = {}
    for o in trip["options"]:
        if o["mode"] not in best or o["estimated_total_time_s"] < best[o["mode"]]["estimated_total_time_s"]:
            best[o["mode"]] = o
    return [best[m] for m in ("jeepney", "walk", "bike", "car") if m in best]


def candidates_of(entry):
    out = []
    for t in entry["trips"]:
        modes = []
        for o in best_options(t):
            row = {"mode": C.TO_PROMPT_MODE[o["mode"]]}
            row.update({k: o[k] for k in OPTION_FIELDS if k in o})
            row["distance_km"] = round(o["distance_m"] / 1000, 2)
            row["cost_php"] = o["cost_php"]
            modes.append(row)
        # INTEGRATION: the trip's planned departure (the simulation's), not the next start time
        home = entry["agent"]["home_id"]
        out.append({"trip_id": t["trip_id"], "origin": shown(t["from"], home), "destination": shown(t["to"], home),
                    "destination_name": t.get("to_name"), "departure_time": t["depart"],
                    "trip_purpose": t.get("trip_type", ""), "candidate_modes": modes})
    return out


def names_of(entry):
    names = {t["to"]: t.get("to_name") for t in entry.get("trips", [])}
    names[entry["agent"]["home_id"]] = "home"
    return names


def prompt_schedule(entry):
    names, home = names_of(entry), entry["agent"]["home_id"]
    return [{k: v for k, v in {"place": shown(e["location_id"], home), "name": names.get(e["location_id"]),
                               "activity": e.get("activity"), "start_time": e.get("start"),
                               "end_time": e.get("end")}.items() if v is not None}
            for e in entry["schedule"]]


def modes_prompt_of(entry, head):
    habits = {"recurring_trips": prompt_trips(entry["recurring_trips"], entry["agent"]["home_id"])}
    return (mode_prompt(commuter_of(entry["agent"]), habits, prompt_experience(entry["travel_experience"]),
                        {"simulation_day": head["day"], "schedule": prompt_schedule(entry)},
                        candidates_of(entry)), C.MAX_NEW_TOKENS_LONG)


def modes_answer(text, entry, head):
    aid, home = entry["agent"]["agent_id"], entry["agent"]["home_id"]
    m = parsed(text)
    if m is None:
        return None, ["not valid JSON"]
    decisions = m.get("decisions")
    if not isinstance(decisions, list):
        return None, ["no decisions"]
    by_trip, problems = {}, []
    for d in decisions:
        if isinstance(d, dict) and d.get("trip_id") in by_trip:
            problems.append(f"{d['trip_id']}: decided twice")
        elif isinstance(d, dict):
            by_trip[d.get("trip_id")] = d
    selections, parked = [], {m_: home for m_ in C.VEHICLE_MODES}
    trips = entry["trips"]
    for i, t in enumerate(trips):
        d = by_trip.get(t["trip_id"])
        if d is None:
            problems.append(f"{t['trip_id']}: no decision")
            continue
        options = {o["mode"]: o for o in best_options(t)}
        chosen = C.TO_ABM_MODE.get(str(d.get("selected_mode", "")).strip(" <>").lower())
        if chosen not in options:
            problems.append(f"{t['trip_id']}: {d.get('selected_mode')!r} is not one of the options "
                            f"{[C.TO_PROMPT_MODE[x] for x in options]}")
            continue
        before = dict(parked)
        if chosen in C.VEHICLE_MODES:
            if parked[chosen] != t["from"]:
                problems.append(f"{t['trip_id']}: the {C.TO_PROMPT_MODE[chosen]} is at {parked[chosen]}, "
                                f"not at {t['from']}")
            parked[chosen] = t["to"]
        if i + 1 < len(trips):
            due = minutes(trips[i + 1]["depart"]) * 60 - minutes(t["depart"]) * 60
            fits = [o for o in options.values() if o["estimated_total_time_s"] <= due
                    and (o["mode"] not in C.VEHICLE_MODES or before[o["mode"]] == t["from"])]
            if options[chosen]["estimated_total_time_s"] > due and fits:
                problems.append(f"{t['trip_id']}: {C.TO_PROMPT_MODE[chosen]} arrives after your next trip leaves, "
                                f"while {C.TO_PROMPT_MODE[min(fits, key=lambda o: o['estimated_total_time_s'])['mode']]} "
                                f"would not")
        sel = {"trip_id": t["trip_id"], "mode": chosen}
        if chosen == "jeepney":
            sel["route_id"] = options["jeepney"]["route_id"]
        if isinstance(d.get("reasoning"), str) and d["reasoning"].strip():
            sel["reasoning"] = d["reasoning"].strip()
        selections.append(sel)
    if problems:
        return None, problems
    return {"agent_id": aid, "selections": selections}, []


# ----------------------------------------------------------------- 5. reflection
def sim_outputs_of(entry):
    rows = []
    for t in entry["trips"]:
        o = t.get("outcome")
        home = entry["agent"]["home_id"]
        row = {"trip_id": t["trip_id"], "origin": shown(t["from"], home), "destination": shown(t["to"], home),
               "destination_name": t.get("to_name"), "scheduled_time": t["depart"]}
        if o is None:
            rows.append({**row, "events": ["this trip did not start before the simulation ended"]})
            continue
        events = []
        if o.get("full_pass_bys_experienced"):
            events.append(f"{o['full_pass_bys_experienced']} full jeepney(s) passed by and could not take you")
        if o.get("disrupted"):
            events.append(f"disrupted: {o.get('disruption_reason') or 'took much longer than expected'}")
        if o.get("completed") is False:
            events.append("you had not arrived when the simulation ended")
        row.update({"selected_mode": C.TO_PROMPT_MODE.get(o.get("realized_mode"), o.get("realized_mode")),
                    "departure": o.get("departure"), "arrival": o.get("arrival"),
                    "estimated_total_time_s": o.get("estimated_total_time_s"),
                    "actual_total_time_s": o.get("actual_total_time_s")})
        if o.get("estimated_total_time_s") is not None and o.get("actual_total_time_s") is not None:
            row["delay_s"] = o["actual_total_time_s"] - o["estimated_total_time_s"]
        if o.get("realized_mode") == "jeepney":
            row["waiting_time_s"] = o.get("actual_waiting_time_s")
        row["cost_php"] = o.get("cost_php")
        row["events"] = events
        rows.append(row)
    if entry.get("left_out"):
        rows.append({"events": [f"your planned day could not be simulated: {entry['left_out']}"]})
    return rows


def reflection_prompt_of(entry, head):
    habits = {"recurring_trips": prompt_trips(entry["recurring_trips"], entry["agent"]["home_id"])}
    if not habits["recurring_trips"]:
        habits["recurring_trips"] = []
    return (reflection_prompt(commuter_of(entry["agent"]), habits, prompt_experience(entry["travel_experience"]),
                              {"simulation_day": head["day"], "schedule": prompt_schedule(entry)},
                              sim_outputs_of(entry)), C.MAX_NEW_TOKENS_LONG)


def reflection_answer(text, entry, head):
    aid = entry["agent"]["agent_id"]
    r = parsed(text)
    if r is None:
        return None, ["not valid JSON"]
    exp = r.get("travel_experience_entry")
    reflection = exp.get("reflection") if isinstance(exp, dict) else None
    if not isinstance(reflection, str) or not reflection.strip():
        return None, ["no reflection text"]
    current = {t["trip_key"]: t["mode_preference_weights"] for t in entry["recurring_trips"]}
    updates, problems = {}, []
    for u in r.get("updated_mode_preference_weights") or []:
        if not isinstance(u, dict):
            problems.append("an update is not an object")
            continue
        key = u.get("trip_id")
        if key not in current:
            problems.append(f"{key!r} is not one of your recurring trips")
            continue
        usable = list(current[key])
        w, more = weight_problems(str(key), u.get("mode_preference_weights"), usable)
        problems += more
        if w is None:
            continue
        changed = [m for m in usable if abs(w[m] - current[key][m]) > 1e-9]
        too_much = [f"{C.TO_PROMPT_MODE[m]} by {abs(w[m] - current[key][m]):.2f}" for m in usable
                    if abs(w[m] - current[key][m]) > C.MAX_WEIGHT_CHANGE + 1e-9]
        if too_much:
            problems.append(f"{key}: changes more than {C.MAX_WEIGHT_CHANGE} in a day ({', '.join(too_much)})")
        elif changed:
            updates[key] = w
    if problems:
        return None, problems
    return {"agent_id": aid, "travel_experience": reflection.strip(), "habit_update": updates}, []


TASKS = {   # task: (prompt builder, answer converter, is a day task)
    "persona": (persona_prompt_of, persona_answer, False),
    "habits": (habits_prompt_of, habits_answer, False),
    "schedule": (schedule_prompt_of, schedule_answer, True),
    "modes": (modes_prompt_of, modes_answer, True),
    "reflection": (reflection_prompt_of, reflection_answer, True),
}


def agent_id_of(entry):
    return entry["agent"]["agent_id"] if "agent" in entry else entry["agent_id"]
