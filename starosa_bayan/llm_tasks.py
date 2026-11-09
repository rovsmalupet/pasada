"""The LLM's tasks (manuscript Sec. 4.3): what each prompt is given, how its answer is checked, and
what the answers add up to. pipeline.py writes each task's input file (demand/llm_inputs/); the
LLM, or llm_stub.py until it is connected, answers in demand/ (every format in demand/README.md).
The answer is checked by the criteria of the manuscript's validation tables that logic can check
(format, completeness, places, times, access, weights); the rest, such as whether a persona suits
its agent, is the behavioural evaluation of Sec. 4.3.9.

  At the start (initialization), once:
    persona     (Sec. 4.3.2)  llm_inputs/persona_input.json  -> personas.json
    habits      (Sec. 4.3.3)  llm_inputs/habits_input.json   -> habits.json
  Every day N:
    schedule    (Sec. 4.3.5)  llm_inputs/schedule_input_day_N.json   -> schedule_day_N.json
                then make_choices.py -> choices_day_N.json (the options, by logic)
    modes       (Sec. 4.3.6)  llm_inputs/modes_input_day_N.json      -> modes_day_N.json
                then run.py -> outcomes_day_N.json (SUMO)
    reflection  (Sec. 4.3.7)  llm_inputs/reflection_input_day_N.json -> reflection_day_N.json

No answer is changed afterwards: the agents' changing state follows from them (Sec. 4.3.4).
  * The travel experience on day N is the reflections of days 1 to N-1 (a prompt gets the last
    EXPERIENCE_DAYS).
  * The mode preference weights on day N are habits.json's, with each reflection's habit_update
    of days 1 to N-1 applied in order.
"""

import copy
import json
import math
from collections import defaultdict
from types import SimpleNamespace

import settings as S
from convert_demand import (CLOCK, MODES, VCLASS, Planner, apply_modes, check_format, home_id, load_agents,
                            load_json, load_lines, may_drive, seconds, trips_of)

# walking/cycling_sensitivity (2026-11-10, was *_predisposition): the names and meaning of the
# evaluated persona prompt (PASADA-llm, manuscript Sec. 4.3.2): High = walking or cycling discourages them.
PERSONA_KEYS = {"description", "private_car_access", "bicycle_access", "walking_sensitivity",
                "cycling_sensitivity", "sensitivities", "general_transport_preference"}
HABIT_KEYS = {"agent_id", "frequent_locations", "recurring_trips"}
RECURRING_KEYS = {"trip_key", "origin", "destination", "purpose", "days", "usual_departure",
                  "mode_preference_weights"}
REFLECTION_KEYS = {"agent_id", "travel_experience", "habit_update"}
ANSWER_TOP_KEYS = {"day", "made_by", "agents"}

PERSONA_RULES = [
    "Write one persona per agent: its behavioural identity for every later task. The agent's attributes are "
    "fixed (census-based) and must not be contradicted.",
    "private_car_access, bicycle_access: true or false. A car only if household_car_access is true and the age "
    f"group starts at {S.CAR_DRIVER_MIN_AGE} or more; a bicycle only if household_bicycle_access is true.",
    f"walking_sensitivity, cycling_sensitivity (High: walking or cycling discourages them), and each of sensitivities ({', '.join(S.SENSITIVITIES)}): "
    f"one of {', '.join(S.LEVELS)}.",
    "description and general_transport_preference: text.",
]
HABITS_RULES = [
    "Give each agent's recurring travel: frequent_locations (location_id from the agent's activity_locations, "
    "with the activity done there) and recurring_trips.",
    "activity: " + "; ".join(f"{a} at {', '.join(c)}" for a, c in S.ACTIVITIES.items()) + ". At home: home.",
    "A recurring trip: trip_key (unique per agent, e.g. R1), origin and destination (the agent's home_id or a "
    "frequent location), purpose (the activity at the destination, home for the agent's home), days (of "
    f"{', '.join(S.WEEKDAYS)}), usual_departure (HH:MM) and mode_preference_weights.",
    "mode_preference_weights: one weight from 0 to 1 per mode in the agent's usable_modes, adding up to 1.",
    "On each day the trips, in order of departure, must start at home, go on from where the one before "
    "ended, and end at home.",
    "At least one recurring trip, unless the agent has no activity_locations. reachable_by lists the modes "
    "that can make the trip there and back (the vehicle comes home).",
]
SCHEDULE_RULES = [
    "Write today's schedule of each agent (format: demand/README.md, step 1): the places of the day in order, "
    "from home and back home, each with location_id, activity and end (HH:MM, when they leave; none on the last "
    "entry). An agent staying home has one entry.",
    "A trip is made whenever two entries in a row are at different places; it leaves at the earlier entry's end.",
    "Follow the recurring trips that fall on today, with day-to-day variation where the travel experience or "
    "the simulation context calls for it. No modes here: they are chosen later, from computed options.",
    "activity: home at home; elsewhere one of " + ", ".join(S.ACTIVITIES) + ", fitting the location's category.",
    "start (optional): planned arrival, not before the previous entry's end.",
]
MODES_RULES = [
    "Choose one option per trip from the trip's options (format: demand/README.md, step 3): trip_id, mode, "
    "route_id for a jeepney, reasoning. Never change a computed value.",
    "Times are estimates on empty roads, in seconds; distances in metres; cost_php is the fare or the car's "
    "running cost.",
    "A bike or car trip needs the vehicle where the trip starts: at home at first, then where it was last left.",
    "Prefer an option that arrives before the next trip leaves: one that does not is refused when another would.",
]
REFLECTION_RULES = [
    "Reflect on each agent's day (manuscript Sec. 4.3.7): travel_experience, a short text of only what is "
    "likely to change future travel (disruptions, unexpected conditions, notable outcomes), supported by the "
    "trip outcomes; an ordinary day can say so.",
    "habit_update: {trip_key: new mode_preference_weights} for the recurring trips whose weights the "
    "experience justifies changing; {} for none. Same rules as the habits' weights, and no weight may change "
    f"by more than {S.MAX_WEIGHT_CHANGE:g} in a day.",
    "Outcome fields (demand/README.md, step 4): actual times against the estimate the option was chosen by, "
    "waiting, full jeepneys that passed the commuter by, disrupted and why.",
]


# ------------------------------------------------------------------------------ files
def input_path(task, day=None):
    return S.LLM_INPUTS / (f"{task}_input.json" if day is None else f"{task}_input_day_{day}.json")


def answer_path(task, day=None):
    return {"persona": S.PERSONAS_FILE, "habits": S.HABITS_FILE}.get(task) or S.day_file(task, day)


def write(path, head, agents):
    """{head..., "agents": [one agent per line]}: readable, easy to diff, valid JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{\n" + "".join(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},\n" for k, v in head.items())
                    + '  "agents": [\n' + ",\n".join(f"    {json.dumps(a, ensure_ascii=False)}" for a in agents)
                    + "\n  ]\n}\n", encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))   # check it is valid JSON


def weekday(day):
    return S.WEEKDAYS[(S.WEEKDAYS.index(S.FIRST_WEEKDAY) + day - 1) % 7]


def commuter(agent, persona=None):
    """An agent as the prompts get it: the base attributes, home and barangay (and the persona)."""
    c = {k: v for k, v in agent.items() if k not in ("home", "has_car", "has_bike")}
    return {**c, "persona": persona} if persona else c


def usable_modes(agent):
    return [m for m in MODES if m not in VCLASS or agent[f"has_{m}"]]


def state(day=None):
    """What the tasks need, from the files: agents (with the persona's access), locations, personas,
    habits (the weights as of day N) and travel experience (the reflections before day N)."""
    st = SimpleNamespace(agents=load_agents(), personas={}, habits={}, experience=defaultdict(list))
    st.locations = {l["location_id"]: l for l in json.loads(S.AGENTS_FILE.read_text(encoding="utf-8"))["locations"]}
    if S.PERSONAS_FILE.is_file():
        st.personas = {a["agent_id"]: a["persona"] for a in load_json(S.PERSONAS_FILE, "")["agents"]}
    if S.HABITS_FILE.is_file():
        st.habits = copy.deepcopy({a["agent_id"]: a for a in load_json(S.HABITS_FILE, "")["agents"]})
    for d in range(1, day or 1):
        path = S.day_file("reflection", d)
        if not path.is_file():
            raise SystemExit(f"Day {d} has no reflection ({S.rel(path)}): finish day {d} first")
        for a in load_json(path, "")["agents"]:
            st.experience[a["agent_id"]].append({"day": d, "reflection": a["travel_experience"]})
            for t in st.habits[a["agent_id"]]["recurring_trips"]:
                if t["trip_key"] in a["habit_update"]:
                    t["mode_preference_weights"] = a["habit_update"][t["trip_key"]]
    return st


def recurring(st, aid, weights=True):
    trips = st.habits.get(aid, {}).get("recurring_trips", [])
    return trips if weights else [{k: v for k, v in t.items() if k != "mode_preference_weights"} for t in trips]


def frequent(st, aid):
    """The agent's frequent locations, with each location's name and category."""
    return [{**f, "name": st.locations[f["location_id"]]["name"], "category": st.locations[f["location_id"]]["category"]}
            for f in st.habits.get(aid, {}).get("frequent_locations", [])]


def experience(st, aid):
    return st.experience[aid][-S.EXPERIENCE_DAYS:]


# ------------------------------------------------------------------------------ checks
def check_answer(answer, expected, keys=None):
    """The answer's agents by id, and what is wrong with the list: not an object, an agent missing,
    twice or unknown, unknown keys (keys: an agent entry's allowed keys)."""
    if not isinstance(answer, dict) or not isinstance(answer.get("agents"), list):
        return {}, ['the answer must be an object with "agents": a list']
    errors = [f"top level: unknown key {k!r}" for k in sorted(set(answer) - ANSWER_TOP_KEYS)]
    found = {}
    for i, a in enumerate(answer["agents"]):
        aid = a.get("agent_id") if isinstance(a, dict) else None
        if aid not in expected:
            errors.append(f"agents[{i}]: agent_id {aid!r} is not one of the agents asked about")
        elif aid in found:
            errors.append(f"{aid}: appears twice")
        else:
            found[aid] = a
            if keys:
                errors += [f"{aid}: unknown key {k!r}" for k in sorted(set(a) - keys)]
                errors += [f"{aid}: missing {k!r}" for k in sorted(keys - set(a))]
    errors += [f"{aid}: missing from the answer" for aid in sorted(set(expected) - set(found))]
    return found, errors


def is_text(x):
    return isinstance(x, str) and x.strip() != ""


def check_weights(w, modes, where):
    """Mode preference weights: one per usable mode, each 0 to 1, adding up to 1."""
    if not isinstance(w, dict):
        return [f"{where}: mode_preference_weights must be an object"]
    errors = []
    if set(w) != set(modes):
        errors.append(f"{where}: weights must be for exactly the usable modes {', '.join(modes)}, not {', '.join(w)}")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in w.values()):
        errors.append(f"{where}: every weight must be a number from 0 to 1")
    elif abs(sum(w.values()) - 1) > S.WEIGHT_SUM_TOLERANCE:
        errors.append(f"{where}: weights add up to {sum(w.values()):.3f}, not 1")
    return errors


# ----------------------------------------------------------------------- 1. persona
def persona_input(st):
    head = {"task": "persona", "about": "Write a persona for each Base Commuter Agent (manuscript Sec. 4.3.2). The "
            "behavioural knowledge base is the LLM team's, added in the prompt.", "rules": PERSONA_RULES,
            "answer": f"{S.rel(S.PERSONAS_FILE)} (format: demand/README.md)"}
    write(input_path("persona"), head, [commuter(st.agents[a]) for a in sorted(st.agents)])


def check_personas(answer, st):
    found, errors = check_answer(answer, st.agents, {"agent_id", "persona"})
    for aid, a in found.items():
        p, agent = a.get("persona"), st.agents[aid]
        if not isinstance(p, dict):
            errors.append(f"{aid}: persona must be an object")
            continue
        errors += [f"{aid} persona: unknown key {k!r}" for k in sorted(set(p) - PERSONA_KEYS)]
        errors += [f"{aid} persona: missing {k!r}" for k in sorted(PERSONA_KEYS - set(p))]
        for k in ("description", "general_transport_preference"):
            if k in p and not is_text(p[k]):
                errors.append(f"{aid} persona: {k} must be text")
        for k in ("private_car_access", "bicycle_access"):
            if k in p and not isinstance(p[k], bool):
                errors.append(f"{aid} persona: {k} must be true or false")
        for k in ("walking_sensitivity", "cycling_sensitivity"):
            if k in p and p[k] not in S.LEVELS:
                errors.append(f"{aid} persona: {k} must be one of {', '.join(S.LEVELS)}")
        sens = p.get("sensitivities")
        if not isinstance(sens, dict) or set(sens) != set(S.SENSITIVITIES) or not all(v in S.LEVELS for v in sens.values()):
            errors.append(f"{aid} persona: sensitivities must give each of {', '.join(S.SENSITIVITIES)} one of "
                          f"{', '.join(S.LEVELS)}")
        if p.get("private_car_access") is True and not (agent["household_car_access"] and may_drive(agent)):
            errors.append(f"{aid} persona: private_car_access needs household_car_access and an age group from "
                          f"{S.CAR_DRIVER_MIN_AGE} (is {agent['age_group']}, household car "
                          f"{agent['household_car_access']})")
        if p.get("bicycle_access") is True and not agent["household_bicycle_access"]:
            errors.append(f"{aid} persona: bicycle_access needs household_bicycle_access")
    return errors


# ------------------------------------------------------------------------ 2. habits
def reachable_locations(st):
    """{agent_id: [location]}: per category, the LOCATIONS_PER_CATEGORY nearest locations (in a
    straight line from home) that one of the agent's usable modes can reach and come back from,
    with reachable_by and distance_m. Uses the same legs as make_choices.py."""
    planner = Planner()
    result = {}
    with planner:
        for aid in sorted(st.agents):
            agent = st.agents[aid]
            home = planner.home_place(agent["home"])
            modes, found = usable_modes(agent), []
            for category in sorted({c for cats in S.ACTIVITIES.values() for c in cats}):
                near = sorted((math.dist(home[2], loc["place"][2]), lid) for lid, loc in planner.locations.items()
                              if loc["category"] == category) if home else []
                kept = 0
                for dist, lid in near[:3 * S.LOCATIONS_PER_CATEGORY]:
                    place = planner.locations[lid]["place"]
                    by = [m for m in modes if planner.leg(m, home, place, at_house=(True, False))[0]
                          and planner.leg(m, place, home, at_house=(False, True))[0]]
                    if by:
                        loc = st.locations[lid]
                        found.append({"location_id": lid, "category": category, "name": loc["name"],
                                      "barangay": loc["barangay"], "distance_m": int(round(dist, -1)), "reachable_by": by})
                        kept += 1
                        if kept == S.LOCATIONS_PER_CATEGORY:
                            break
            result[aid] = found
    return result


def habits_input(st):
    places = reachable_locations(st)
    head = {"task": "habits", "about": "Give each Commuter Agent's Habitual Travel Patterns (manuscript Sec. 4.3.3).",
            "rules": HABITS_RULES, "weekdays": list(S.WEEKDAYS),
            "answer": f"{S.rel(S.HABITS_FILE)} (format: demand/README.md)"}
    write(input_path("habits"), head,
          [{"agent": commuter(st.agents[a], st.personas[a]), "usable_modes": usable_modes(st.agents[a]),
            "activity_locations": places[a]} for a in sorted(st.agents)])


def check_habits(answer, st):
    offered = {a["agent"]["agent_id"]: {l["location_id"]: l for l in a["activity_locations"]}
               for a in load_json(input_path("habits"), "")["agents"]}
    found, errors = check_answer(answer, st.agents, HABIT_KEYS)
    for aid, a in found.items():
        home, modes = home_id(aid), usable_modes(st.agents[aid])
        activity = {}
        for i, f in enumerate(a.get("frequent_locations") if isinstance(a.get("frequent_locations"), list) else []):
            where = f"{aid} frequent_locations[{i}]"
            lid = f.get("location_id") if isinstance(f, dict) else None
            if not isinstance(f, dict) or set(f) != {"location_id", "activity"} or not all(map(is_text, f.values())):
                errors.append(f"{where}: must be {{location_id, activity}}, both text")
            elif lid not in offered[aid]:
                errors.append(f"{where}: {lid!r} is not one of the agent's activity_locations")
            elif lid in activity:
                errors.append(f"{where}: {lid} is listed twice")
            elif offered[aid][lid]["category"] not in S.ACTIVITIES.get(f["activity"], ()):
                errors.append(f"{where}: activity {f['activity']!r} does not fit a {offered[aid][lid]['category']} "
                              f"(allowed: {', '.join(k for k, c in S.ACTIVITIES.items() if offered[aid][lid]['category'] in c)})")
            else:
                activity[lid] = f["activity"]
        if not isinstance(a.get("frequent_locations"), list):
            errors.append(f"{aid}: frequent_locations must be a list")
        trips = a.get("recurring_trips")
        if not isinstance(trips, list):
            errors.append(f"{aid}: recurring_trips must be a list")
            continue
        if not trips and offered[aid]:
            errors.append(f"{aid}: needs at least one recurring trip")
        keys, valid = set(), []
        for i, t in enumerate(trips):
            where = f"{aid} recurring_trips[{i}]"
            if not isinstance(t, dict) or set(t) != RECURRING_KEYS:
                errors.append(f"{where}: must have exactly {', '.join(sorted(RECURRING_KEYS))}")
                continue
            ok = True
            if not is_text(t["trip_key"]) or t["trip_key"] in keys:
                errors.append(f"{where}: trip_key must be text, unique for the agent")
                ok = False
            else:
                keys.add(t["trip_key"])
            for end in ("origin", "destination"):
                if t[end] != home and (not is_text(t[end]) or t[end] not in activity):
                    errors.append(f"{where}: {end} {t[end]!r} is neither {home} nor a frequent location")
                    ok = False
            if ok and t["origin"] == t["destination"]:
                errors.append(f"{where}: origin and destination are the same")
                ok = False
            purpose = ("home" if t["destination"] == home else activity.get(t["destination"])) if ok else None
            if purpose is not None and t["purpose"] != purpose:
                errors.append(f"{where}: purpose must be the activity at the destination, {purpose!r}")
            days = t["days"]
            if (not isinstance(days, list) or not days or not all(d in S.WEEKDAYS for d in days)
                    or len(set(days)) != len(days)):
                errors.append(f"{where}: days must be a non-empty list of different days of {', '.join(S.WEEKDAYS)}")
                ok = False
            if not isinstance(t["usual_departure"], str) or not CLOCK.match(t["usual_departure"]):
                errors.append(f"{where}: usual_departure must be HH:MM")
                ok = False
            errors += check_weights(t["mode_preference_weights"], modes, where)
            if ok:
                valid.append(t)
        for day in S.WEEKDAYS:                       # each day's trips: a round from home back home
            todays = sorted((t for t in valid if day in t["days"]), key=lambda t: t["usual_departure"])
            if not todays:
                continue
            at, last = home, None
            for t in todays:
                if t["usual_departure"] == last:
                    errors.append(f"{aid} {day}: {t['trip_key']} leaves at {last}, the same time as the trip before")
                    break
                if t["origin"] != at:
                    errors.append(f"{aid} {day}: {t['trip_key']} leaves {t['origin']} at {t['usual_departure']}, but the "
                                  f"agent is at {at} then")
                    break
                at, last = t["destination"], t["usual_departure"]
            else:
                if at != home:
                    errors.append(f"{aid} {day}: the day's recurring trips end at {at}, not at home")
    return errors


# ---------------------------------------------------------------------- 3. schedule
def schedule_input(st, day):
    head = {"task": "schedule", "day": day, "weekday": weekday(day), "simulation_context": S.SIMULATION_CONTEXT,
            "about": "Write each Commuter Agent's Daily Schedule for today (manuscript Sec. 4.3.5).",
            "rules": SCHEDULE_RULES,
            "answer": f"{S.rel(S.day_file('schedule', day))} (format: demand/README.md)"}
    write(input_path("schedule", day), head,
          [{"agent": commuter(st.agents[a], st.personas[a]), "frequent_locations": frequent(st, a),
            "recurring_trips": recurring(st, a, weights=False), "travel_experience": experience(st, a)}
           for a in sorted(st.agents)])


def check_schedule(answer, st, day):
    errors = check_format(answer, st.agents, set(st.locations), with_modes=False)
    if errors:
        return errors
    if answer["day"] != day:
        errors.append(f'top level: "day" is {answer["day"]}, not {day}')
    _, more = check_answer(answer, st.agents)
    errors += more
    for agent in answer["agents"]:
        aid, prev_end = agent["agent_id"], None
        if agent["schedule"][-1]["location_id"] != home_id(aid):
            errors.append(f"{aid}: the day ends at {agent['schedule'][-1]['location_id']}, not at home: the last entry "
                          f"must be at {home_id(aid)}")
        for j, e in enumerate(agent["schedule"]):
            where, act = f"{aid} schedule[{j}]", e.get("activity")
            at_home = e["location_id"] == home_id(aid)
            if act is not None:
                if at_home and act != "home":
                    errors.append(f"{where}: the activity at home is home")
                elif not at_home and st.locations[e["location_id"]]["category"] not in S.ACTIVITIES.get(act, ()):
                    errors.append(f"{where}: activity {act!r} does not fit a {st.locations[e['location_id']]['category']}")
            if "start" in e and prev_end is not None and seconds(e["start"]) < prev_end:
                errors.append(f"{where}: starts at {e['start']}, before the previous entry ends")
            if "start" in e and "end" in e and seconds(e["start"]) > seconds(e["end"]):
                errors.append(f"{where}: starts after it ends")
            prev_end = seconds(e["end"]) if "end" in e else prev_end
    return errors


# ------------------------------------------------------------------------- 4. modes
def modes_input(st, day):
    schedule = {a["agent_id"]: a["schedule"] for a in load_json(S.day_file("schedule", day), "")["agents"]}
    choices = {a["agent_id"]: a["trips"] for a in load_json(S.day_file("choices", day), "")["agents"]}
    head = {"task": "modes", "day": day, "weekday": weekday(day), "simulation_context": S.SIMULATION_CONTEXT,
            "about": "Choose a mode for every trip of each Commuter Agent's Daily Schedule, from the options the "
                     "simulation computed (manuscript Sec. 4.3.6).", "rules": MODES_RULES,
            "answer": f"{S.rel(S.day_file('modes', day))} (format: demand/README.md)"}
    write(input_path("modes", day), head,
          [{"agent": commuter(st.agents[a], st.personas[a]), "recurring_trips": recurring(st, a),
            "travel_experience": experience(st, a), "schedule": schedule[a], "trips": choices[a]}
           for a in sorted(choices) if choices[a]])


def check_modes(answer, st, day):
    """apply_modes' format checks, then each agent's choices: an option of the trip, the vehicle
    where the trip starts, and not arriving after the next trip leaves while another option would."""
    schedule = load_json(S.day_file("schedule", day), "")
    choices = {a["agent_id"]: a["trips"] for a in load_json(S.day_file("choices", day), "")["agents"]}
    _, errors = apply_modes(copy.deepcopy(schedule), answer, load_lines())
    if errors:
        return errors
    picked = {s["trip_id"]: s for a in answer["agents"] for s in a["selections"]}
    for aid, trips in sorted(choices.items()):
        parked = {m: home_id(aid) for m in VCLASS}
        for i, trip in enumerate(trips):
            sel, where, before = picked[trip["trip_id"]], f"{aid} {trip['trip_id']}", dict(parked)
            option = next((o for o in trip["options"] if o["mode"] == sel["mode"]
                           and o.get("route_id") == sel.get("route_id")), None)
            if option is None:
                errors.append(f"{where}: {sel['mode']} {sel.get('route_id', '')} is not one of the trip's options".replace("  ", " "))
                continue
            if sel["mode"] in VCLASS:
                if parked[sel["mode"]] != trip["from"]:
                    errors.append(f"{where}: the {sel['mode']} is at {parked[sel['mode']]}, not at {trip['from']}")
                parked[sel["mode"]] = trip["to"]
            if i + 1 < len(trips):
                due = seconds(trips[i + 1]["depart"]) - seconds(trip["depart"])
                fits = [o for o in trip["options"] if o["estimated_total_time_s"] <= due
                        and (o["mode"] not in VCLASS or before[o["mode"]] == trip["from"])]
                if option["estimated_total_time_s"] > due and fits:
                    best = min(fits, key=lambda o: o["estimated_total_time_s"])
                    errors.append(f"{where}: arrives after the next trip leaves ({option['estimated_total_time_s']} s for "
                                  f"{due} s), while {best['mode']} {best.get('route_id', '')} would not".replace("  ", " "))
    return errors


# -------------------------------------------------------------------- 5. reflection
def reflection_input(st, day):
    schedule = {a["agent_id"]: a["schedule"] for a in load_json(S.day_file("schedule", day), "")["agents"]}
    choices = {a["agent_id"]: a["trips"] for a in load_json(S.day_file("choices", day), "")["agents"]}
    outcomes = load_json(S.day_file("outcomes", day), "")
    by_trip = {o["trip_id"]: o for a in outcomes["agents"] for o in a["trips"]}
    left_out = {a["agent_id"]: a["left_out"] for a in outcomes["agents"] if "left_out" in a}
    head = {"task": "reflection", "day": day, "weekday": weekday(day), "simulation_context": S.SIMULATION_CONTEXT,
            "about": "Reflect on each Commuter Agent's day, and update their habits if the experience justifies it "
                     "(manuscript Sec. 4.3.7).", "rules": REFLECTION_RULES, "whole_day": outcomes["whole_day"],
            "answer": f"{S.rel(S.day_file('reflection', day))} (format: demand/README.md)"}
    agents = []
    for aid in sorted(schedule):
        trips = [{k: t[k] for k in ("trip_id", "from", "to", "to_name", "trip_type", "depart")}
                 | {"outcome": by_trip.get(t["trip_id"])} for t in choices.get(aid, [])]
        entry = {"agent": commuter(st.agents[aid], st.personas[aid]), "recurring_trips": recurring(st, aid),
                 "travel_experience": experience(st, aid), "schedule": schedule[aid], "trips": trips}
        if aid in left_out:
            entry["left_out"] = left_out[aid]
        agents.append(entry)
    write(input_path("reflection", day), head, agents)


def check_reflection(answer, st, day):
    asked = {a["agent"]["agent_id"] for a in load_json(input_path("reflection", day), "")["agents"]}
    found, errors = check_answer(answer, asked, REFLECTION_KEYS)
    if answer.get("day") != day:
        errors.append(f'top level: "day" must be {day}')
    for aid, a in found.items():
        if not is_text(a.get("travel_experience")):
            errors.append(f"{aid}: travel_experience must be text")
        update = a.get("habit_update")
        if not isinstance(update, dict):
            errors.append(f"{aid}: habit_update must be an object ({{}} for no change)")
            continue
        current = {t["trip_key"]: t["mode_preference_weights"] for t in recurring(st, aid)}
        for key, w in update.items():
            where = f"{aid} habit_update[{key!r}]"
            if key not in current:
                errors.append(f"{where}: not one of the agent's recurring trips")
                continue
            more = check_weights(w, usable_modes(st.agents[aid]), where)
            errors += more
            if not more:
                errors += [f"{where}: {m} changes by {abs(w[m] - current[key][m]):.2f}, over MAX_WEIGHT_CHANGE "
                           f"({S.MAX_WEIGHT_CHANGE:g})" for m in w if abs(w[m] - current[key][m]) > S.MAX_WEIGHT_CHANGE + 1e-9]
    return errors


TASKS = {   # task: (input writer, answer check, day task)
    "persona": (persona_input, check_personas, False),
    "habits": (habits_input, check_habits, False),
    "schedule": (schedule_input, check_schedule, True),
    "modes": (modes_input, check_modes, True),
    "reflection": (reflection_input, check_reflection, True),
}
