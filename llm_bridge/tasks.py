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
PERSONA_FIELDS = ["behavioral_description", "vehicle_ownership", "bicycle_access", "walking_tolerance",
                  "cycling_tolerance", "sensitivities", "general_transport_preference"]     # abm10 (v0.5)
INVERT = {"High": "Low", "Low": "High", "Moderate": "Moderate"}   # walking/cycling tolerance <-> the simulation's sensitivity


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
NOTES = []                         # what the simulation side decided for an answer (logged by answer.py)
LAST_TRY = False                   # set by answer.py on an agent's last try (see modes_answer)


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


def _coords(aid, lid):
    if "coords" not in _CACHE:
        data = _json("agents.json") or {}
        c = {l["location_id"]: (l["lat"], l["lon"]) for l in data.get("locations", [])}
        c.update({a["home_id"]: (a["home"]["lat"], a["home"]["lon"]) for a in data.get("agents", []) if a.get("home")})
        _CACHE["coords"] = c
    return _CACHE["coords"].get(lid)


def _km(a, b):
    import math
    if a is None or b is None:
        return 0.0
    f1, f2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((f2 - f1) / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def resolve(aid, kind, activity, from_lid, places):
    """INTEGRATION (v0.3-abm7): which place a kind means for this trip, decided on the simulation
    side: the place of that kind NEAREST TO WHERE THE AGENT IS COMING FROM (straight line), among
    the places the agent can reach and come back from (`places`, the habits step's options). If
    there is none of that kind, the nearest such place where the same activity can happen.
    Returns (location, note) or (None, None)."""
    here = _coords(aid, from_lid)
    same = [l for l in places if l["category"] == kind]
    if same:
        return min(same, key=lambda l: _km(here, _coords(aid, l["location_id"]))), None
    alike = [l for l in places if l["category"] in C.ACTIVITIES.get(activity, ())]
    if not alike:
        return None, None
    l = min(alike, key=lambda x: _km(here, _coords(aid, x["location_id"])))
    return l, f"{aid}: no reachable {kind}; {activity} at the nearest reachable {l['category']} instead"


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
            shown["current_school_level"] = level            # abm10 (v0.5): the level enrolled in now
    return shown


def prompt_persona(p):
    """The simulation's persona (personas.json) in the evaluated prompts' fields."""
    def walking(kind):
        if f"{kind}_sensitivity" in p:
            return p[f"{kind}_sensitivity"]
        # a stand-in persona (llm_stub.py) has predispositions: the opposite scale
        return {"High": "Low", "Low": "High"}.get(p.get(f"{kind}_predisposition"), "Moderate")
    # abm10 (v0.5): walking/cycling shown as tolerances; personas.json keeps the simulation's sensitivities
    return {"behavioral_description": p["description"], "vehicle_ownership": p["private_car_access"],
            "bicycle_access": p["bicycle_access"], "walking_tolerance": INVERT.get(walking("walking")),
            "cycling_tolerance": INVERT.get(walking("cycling")), "sensitivities": p["sensitivities"],
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
    levels = {**sens, "walking_tolerance": p.get("walking_tolerance"), "cycling_tolerance": p.get("cycling_tolerance")}
    wrong = [f"{k}={v!r}" for k, v in levels.items() if k in (*C.SENSITIVITIES, "walking_tolerance",
                                                               "cycling_tolerance") and v not in C.LEVELS]
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
    # abm11: on the last try, an impossible car/bicycle access is set to false (logged), not failed
    vehicle_problems = [x for x in problems if x.startswith(("car access", "bicycle access"))]
    if LAST_TRY and vehicle_problems:
        for x in vehicle_problems:
            problems.remove(x)
            NOTES.append(f"{entry.get('agent_id', '?')}: {x} on the last try: set to false")
        if any(x.startswith("car access") for x in vehicle_problems):
            p["vehicle_ownership"] = False
        if any(x.startswith("bicycle access") for x in vehicle_problems):
            p["bicycle_access"] = False
    if "general_transport_preference" in p and p["general_transport_preference"] not in C.TO_ABM_MODE:
        problems.append(f"preference {p['general_transport_preference']!r} is not one of the four modes")
    if "behavioral_description" in p and not (isinstance(p["behavioral_description"], str)
                                              and p["behavioral_description"].strip()):
        problems.append("behavioral_description is not text")
    if problems:
        return None, problems
    persona = {"description": p["behavioral_description"].strip(), "private_car_access": p["vehicle_ownership"],
               "bicycle_access": p["bicycle_access"], C.WALKING_FIELD: INVERT[p["walking_tolerance"]],
               C.CYCLING_FIELD: INVERT[p["cycling_tolerance"]],
               "sensitivities": {k: sens[k] for k in C.SENSITIVITIES},
               "general_transport_preference": p["general_transport_preference"]}
    return {"agent_id": entry["agent_id"], "persona": persona}, []


# --------------------------------------------------------------------- 2. habits
def all_kinds():
    """Every kind of place in the study area (the categories the activities can happen at)."""
    return sorted({c for cats in C.ACTIVITIES.values() for c in cats})


def habits_locations(entry):
    """INTEGRATION (v0.3-abm6): the place options are home and every kind of place in the study
    area, with no distance and no reachability: the agent decides what kind of place it goes to,
    the simulation decides which one (the nearest it can reach) and how (team decision 2026-11-11)."""
    return [{"place": HOME}] + [{"place": k} for k in all_kinds()]


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
    kinds = set(all_kinds())
    problems = []

    def kind_of(place):
        w = place_word(place)
        return HOME if w in (HOME, home.lower()) else w

    # 1. what the model said: the kind of each place, and what it does there
    words = {}                                 # kind -> words for the activity (roles, trip purposes)
    for f in h.get("frequent_locations") or []:
        if isinstance(f, dict):
            k = kind_of(f.get("place", f.get("location_id")))
            if k != HOME:
                words.setdefault(k, []).append(f.get("role"))
    raw_trips = []
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
        o, d = kind_of(t["origin"]), kind_of(t["destination"])
        for k in (o, d):
            if k != HOME and k not in kinds:
                problems.append(f"{tid}: unknown place {k!r} (use one of the place options)")
        if d != HOME:
            words.setdefault(d, []).append(t["trip_purpose"])
        if o != HOME:
            words.setdefault(o, [])
        raw_trips.append((tid, o, d, t))
    unknown = [k for k in words if k not in kinds]
    for k in unknown:
        if not any(k in p for p in problems):
            problems.append(f"unknown place {k!r} (use one of the place options)")
    if problems:
        return None, problems

    # 2. where, decided by the simulation side (v0.3-abm7): trips in order of departure; a kind's place
    # is the one nearest to the origin of the first trip that goes there, and stays that place
    activity_of_kind = {k: activity_for(w, k, agent) for k, w in words.items()}
    place_of_kind = {}

    def place(k, from_lid):
        if k == HOME or k in place_of_kind:
            return
        l, note = resolve(aid, k, activity_of_kind[k], from_lid, entry["activity_locations"])
        if l is None:
            problems.append(f"no place for {activity_of_kind[k]} ({k} or similar) that this agent can reach")
            return
        place_of_kind[k] = l
        if note:
            NOTES.append(note)

    for tid, o, d, t in sorted(raw_trips, key=lambda r: clock(r[3]["usual_departure_time"])):
        place(o, home)                             # an origin met first: from home
        if o == HOME or o in place_of_kind:
            place(d, home if o == HOME else place_of_kind[o]["location_id"])
    for k in activity_of_kind:                     # a frequent place with no trip: from home
        place(k, home)
    if problems:
        return None, problems

    def to_id(k):
        return home if k == HOME else place_of_kind[k]["location_id"]

    activity = {}
    for k, l in place_of_kind.items():
        activity.setdefault(l["location_id"], activity_of_kind[k])
    out_trips, keys = [], set()
    for tid, o, d, t in raw_trips:
        if tid in keys:
            problems.append(f"{tid}: trip_id used twice")
        keys.add(tid)
        origin, destination = to_id(o), to_id(d)
        if origin == destination:
            problems.append(f"{tid}: origin and destination are the same place")
        days = [day_of(x) for x in t["travel_days"]] if isinstance(t["travel_days"], list) else [None]
        if not days or None in days or len(set(days)) != len(days):
            problems.append(f"{tid}: travel_days must be different days of the week")
        dep = clock(t["usual_departure_time"])
        if not HHMM.match(dep):
            problems.append(f"{tid} time {t['usual_departure_time']!r} is not HH:MM")
        weights, more = weight_problems(tid, t["mode_preference_weights"], entry["usable_modes"])
        problems += more
        out_trips.append({"trip_key": tid, "origin": origin, "destination": destination,
                          "purpose": None, "days": days, "usual_departure": dep,
                          "mode_preference_weights": weights})
    if problems:
        return None, problems
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
    return [{"place": k} for k in all_kinds() if k not in frequent]   # v0.3-abm6: no distances


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
    frequent = {f["category"]: f["location_id"] for f in entry["frequent_locations"]}   # the habit's places
    today, prev, problems = {}, home, []
    for i, it in enumerate(items):
        w = place_word(it.get("place", it.get("location_id")))
        if w in (HOME, home.lower()):
            lid = home
        elif w in frequent:
            lid = frequent[w]
        elif w in today:
            lid = today[w]
        elif w in all_kinds():                 # another kind of place: nearest to where they are (v0.3-abm7)
            act = activity_for([it.get("trip_purpose"), it.get("activity")], w, agent)
            l, note = resolve(aid, w, act, prev, offered_places(aid))
            if l is None:
                problems.append(f"entry {i + 1}: no place for {act} ({w} or similar) this agent can reach")
                lid = None
            else:
                lid = today[w] = l["location_id"]
                activity.setdefault(lid, act)
                if note:
                    NOTES.append(note)
        else:
            problems.append(f"entry {i + 1}: {it.get('place')!r} is not home or one of the listed places")
            lid = None
        it["location_id"] = lid
        if lid is not None:
            prev = lid
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


def usable_options(entry):
    """Each trip's options without a bicycle or car the agent cannot have where that trip starts,
    whatever it chose before (e.g. the bicycle on a trip from work when it could not have ridden it
    there). A vehicle stays offered when some choice of the earlier trips brings it there (then the
    prompt's vehicle rule says to use it on the way). Decided with the user, 2026-11-13: the LLM is
    only offered what the agent can actually use."""
    trips, home = entry["trips"], entry["agent"]["home_id"]
    opts = [best_options(t) for t in trips]
    start = tuple(sorted({m_: home for m_ in C.VEHICLE_MODES}.items()))
    memo = {}

    def step(i, parked, mode):
        where = dict(parked)
        if mode in C.VEHICLE_MODES:
            if where[mode] != trips[i]["from"]:
                return None
            where[mode] = trips[i]["to"]
        return tuple(sorted(where.items()))

    def can_finish(i, parked):
        if i == len(trips):
            return parked == start          # every bicycle/car back home at the end of the day
        if (i, parked) not in memo:
            memo[(i, parked)] = any((n := step(i, parked, o["mode"])) is not None and can_finish(i + 1, n)
                                    for o in opts[i])
        return memo[(i, parked)]

    if not can_finish(0, start):
        return opts             # no way at all (a trip offers only an unreachable vehicle): see modes_answer
    keep, frontier = [set() for _ in trips], {start}
    for i in range(len(trips)):
        nxt = set()
        for parked in frontier:
            for o in opts[i]:
                n = step(i, parked, o["mode"])
                if n is not None and can_finish(i + 1, n):
                    keep[i].add(o["mode"])
                    nxt.add(n)
        frontier = nxt
    return [[o for o in opts[i] if o["mode"] in keep[i]] for i in range(len(trips))]


def candidates_of(entry):
    out = []
    for t, usable in zip(entry["trips"], usable_options(entry)):
        modes = []
        for o in usable:
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


def vehicle_rule_possible(trips, home):
    """True when at least one choice of modes (from each trip's options) keeps every bicycle and car
    trip starting where that vehicle was left. When none does (e.g. a trip whose only option is the
    bicycle, from a place the bicycle cannot have reached), no answer could pass the vehicle rule."""
    options = [[o["mode"] for o in best_options(t)] for t in trips]
    seen = set()

    start = tuple(sorted({m_: home for m_ in C.VEHICLE_MODES}.items()))

    def ok(i, parked):
        if i == len(trips):
            return parked == start          # every bicycle/car back home at the end of the day
        if (i, parked) in seen:
            return False
        seen.add((i, parked))
        where = dict(parked)
        for mode in options[i]:
            if mode in C.VEHICLE_MODES:
                if where[mode] != trips[i]["from"]:
                    continue
                if ok(i + 1, tuple(sorted({**where, mode: trips[i]["to"]}.items()))):
                    return True
            elif ok(i + 1, parked):
                return True
        return False

    return ok(0, start)


def modes_answer(text, entry, head):
    aid, home = entry["agent"]["agent_id"], entry["agent"]["home_id"]
    enforce_vehicle = vehicle_rule_possible(entry["trips"], home)
    if not enforce_vehicle:      # logged; the simulation's own options made the rule impossible for this day
        NOTES.append(f"{aid}: vehicle rule not applied: no choice of modes keeps the bicycle/car where each trip "
                     f"starts (a trip offers only a vehicle the agent cannot have there)")
    elif LAST_TRY:               # last resort so one agent cannot stop the run; logged with the answer
        enforce_vehicle = False
        NOTES.append(f"{aid}: vehicle rule not applied on the last try (every earlier try broke it)")
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
    trips, usable = entry["trips"], usable_options(entry)
    for i, t in enumerate(trips):
        d = by_trip.get(t["trip_id"])
        if d is None:
            problems.append(f"{t['trip_id']}: no decision")
            continue
        options = {o["mode"]: o for o in usable[i]}         # only what the agent can use (usable_options)
        chosen = C.TO_ABM_MODE.get(str(d.get("selected_mode", "")).strip(" <>").lower())
        if chosen not in options:
            problems.append(f"{t['trip_id']}: {d.get('selected_mode')!r} is not one of the options "
                            f"{[C.TO_PROMPT_MODE[x] for x in options]}")
            continue
        before = dict(parked)
        if chosen in C.VEHICLE_MODES:
            if enforce_vehicle and parked[chosen] != t["from"]:
                problems.append(f"{t['trip_id']}: the {C.TO_PROMPT_MODE[chosen]} is at {parked[chosen]}, "
                                f"not at {t['from']}")
            parked[chosen] = t["to"]
        if i + 1 < len(trips):
            due = minutes(trips[i + 1]["depart"]) * 60 - minutes(t["depart"]) * 60
            fits = [o for o in options.values() if o["estimated_total_time_s"] <= due
                    and (o["mode"] not in C.VEHICLE_MODES or not enforce_vehicle or before[o["mode"]] == t["from"])]
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
    if not enforce_vehicle and not problems:
        # the rule could not be asked of the LLM, but the simulation still needs each bicycle/car trip to
        # start where the vehicle is: such a choice becomes the fastest other option of that trip (logged)
        where = {m_: home for m_ in C.VEHICLE_MODES}
        for i, (t, sel) in enumerate(zip(trips, selections)):
            mode = sel["mode"]
            if mode in C.VEHICLE_MODES and where[mode] != t["from"]:
                others = [o for o in best_options(t) if o["mode"] not in C.VEHICLE_MODES or where[o["mode"]] == t["from"]]
                if others:
                    pick = min(others, key=lambda o: o["estimated_total_time_s"])
                    sel.clear()
                    sel.update({"trip_id": t["trip_id"], "mode": pick["mode"],
                                "reasoning": f"(simulation) the {C.TO_PROMPT_MODE[mode]} was not here; "
                                             f"took the {C.TO_PROMPT_MODE[pick['mode']]} instead"})
                    if pick["mode"] == "jeepney":
                        sel["route_id"] = pick["route_id"]
                    NOTES.append(f"{t['trip_id']}: {C.TO_PROMPT_MODE[mode]} not at {t['from']}; "
                                 f"replaced by {C.TO_PROMPT_MODE[pick['mode']]}")
                    mode = pick["mode"]
            if mode in C.VEHICLE_MODES:
                where[mode] = t["to"]
    if enforce_vehicle and not problems:
        for vehicle, where in parked.items():
            if where != home:                # 2026-11-13, decided with the user: no vehicle left out overnight
                problems.append(f"the {C.TO_PROMPT_MODE[vehicle]} is left at {where} at the end of the day; "
                                f"ride it back home")
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


def limit_change(old, new, limit):
    """new weights moved toward the answer but no weight by more than limit, still adding up to 1."""
    d = {m: max(-limit, -old[m], min(limit, new[m] - old[m])) for m in old}
    up = sum(v for v in d.values() if v > 0)
    down = -sum(v for v in d.values() if v < 0)
    if up > down:
        d = {m: (v * down / up if v > 0 else v) for m, v in d.items()}
    elif down > up:
        d = {m: (v * up / down if v < 0 else v) for m, v in d.items()}
    out = {m: round(old[m] + d[m], 3) for m in old}
    top = max(out, key=out.get)
    out[top] = round(out[top] + 1 - sum(out.values()), 3)
    return out


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
        if too_much and LAST_TRY:
            # last resort so one agent cannot stop the run: the same direction of change, kept to the
            # daily limit (logged). E.g. jeepney 1.0 -> 0.0 and car 0.0 -> 1.0 becomes 0.8 and 0.2.
            w = limit_change(current[key], w, C.MAX_WEIGHT_CHANGE)
            NOTES.append(f"{key}: changes cut to {C.MAX_WEIGHT_CHANGE} on the last try ({', '.join(too_much)})")
            updates[key] = w
        elif too_much:
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
