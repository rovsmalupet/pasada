"""The five prompts, as evaluated (PASADA-llm notebook, prompt version v0.3, Part 2).

Everything here is copied from the notebook word for word, except the lines marked
INTEGRATION: the few things the simulation needs that the evaluation set did not
(PROMPT_VERSION v0.3-abm1 in config.py). Each is listed in llm_bridge/README.md.
"""

import json

SYSTEM = ("You are a highly specialized sociologist and economist with extensive, "
          "evidence-based knowledge of Filipino cultural, behavioral, and economic patterns. "
          "When given descriptions of specific individuals living in the Philippines, you must "
          "provide detailed, realistic, and unbiased sociological and economic insights that "
          "accurately reflect contemporary social and economic dynamics. "
          "Ensure every output is strictly valid JSON.")

JSON_ONLY = ("Do not include any explanations, only provide a RFC8259 compliant JSON "
             "response following this format without deviation.\n")


def J(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False)


def parse_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("the answer contains no JSON object")
    return json.loads(text[start:end + 1])


JEEPNEY_CONTEXT = """About jeepneys in Santa Rosa City, Laguna:
- Jeepneys run along fixed routes but not on fixed schedules; they circulate continuously along their route.
- There are few designated stops. Passengers usually flag a jeepney down and get off anywhere along its route, so they often walk to and from the route.
- Jeepneys are usually slow. They stop often to let passengers on and off, slow down to look for waiting passengers, and may stay parked to wait for more riders before moving on. Because of this, a jeepney trip usually takes longer than the same trip by car, and the time can vary from day to day.
- Waiting time also varies. Drivers may wait at terminals or busy areas until the jeepney fills up, and a full jeepney cannot take more passengers.
- During the morning and evening rush hours, public transport is often overcrowded, with long queues at terminals and longer waiting and travel times.
- Many commuters avoid jeepneys when a trip needs a long wait for a transfer at peak time.
- Traditional jeepneys are small, holding only about 12 to 14 passengers, with a low ceiling, so passengers must bend to get in, move, and get out.
- Traditional jeepneys are generally not air-conditioned, which makes trips uncomfortable in hot weather.
- Passengers see jeepneys as familiar, easy to use, and cheap, but also as noisy, dirty, dangerous, and uncomfortable.
- Jeepneys are generally accepted for short trips but avoided for long trips, where air-conditioned modes are preferred.
- Many passengers avoid riding jeepneys alone at night.
- Jeepneys are one of the cheapest motorized options and are widely used for daily commuting.
- Boarding and alighting along the road exposes passengers to traffic and road-accident risk.
- A jeepney only covers its own route, so a trip may need walking at either end or a transfer to another jeepney.
- The exact fare, travel time, and waiting time for each trip are given separately and take precedence over this general description.
"""

# ------------------------------------------------------------------- 1. persona
LMH = '"Low, Moderate, or High"'

# v0.5 / abm10 (round 2, 2026-11-13): the same text as the evaluation notebook v0.5
PERSONA_FORMAT = (
    '{"behavioral_description": "realistic one paragraph description of this commuter",\n'
    ' "vehicle_ownership": true or false,\n'
    ' "bicycle_access": true or false,\n'
    f' "walking_tolerance": {LMH},\n'
    f' "cycling_tolerance": {LMH},\n'
    f' "sensitivities": {{"safety": {LMH}, "cost": {LMH}, "comfort": {LMH},\n'
    f'   "accessibility": {LMH}, "availability": {LMH}, "efficiency": {LMH},\n'
    f'   "reliability": {LMH}}},\n'
    ' "general_transport_preference": "jeepney, walking, bicycle, or private car"}')

PERSONA_DEFINITIONS = (
    "Definitions:\n"
    "- per_capita_income_decile: where this person's household income per person ranks in the "
    "population, from 1 to 10. 1 to 3 is lower income (the poorest 30%), 4 to 7 is middle income, "
    "and 8 to 10 is higher income (the richest 30%). For example, 7 means richer than about 60% of "
    "the population, and 2 means poorer than about 80%.\n"
    "- current_school_level (students only): the level this person is enrolled in now. For example, "
    "bachelors means they are taking a bachelor's degree now, not that they finished one.\n"
    "- vehicle_ownership and bicycle_access: whether this person can personally use a "
    "private car or a bicycle for their own trips. This can not be true if the household "
    "has no access to one.\n"
    "- walking_tolerance and cycling_tolerance: how far this commuter is willing to walk or cycle. "
    "High means they walk or cycle long distances without minding; Low means they avoid it beyond "
    "short distances.\n"
    "- sensitivities: how much each attribute matters to this commuter when choosing how to travel. "
    "High means it matters a lot; Low means it barely matters. safety: the risk of accidents, crime, or "
    "travelling at night. cost: the price of a trip. comfort: crowding, heat, noise, and cramped seats. "
    "accessibility: how far they walk to and from where a ride starts or ends. availability: having enough "
    "rides to choose from. efficiency: short travel times. reliability: travel and waiting times that stay "
    "the same from day to day. For example, a commuter on a tight budget has cost: High, and a commuter "
    "who does not mind crowded, hot rides has comfort: Low.\n")


def persona_prompt(shown_agent, findings):
    return ("Sample attributes from the Santa Rosa City, Laguna population:\n"
            + J(shown_agent) + "\n"
            "Imagine a realistic commuter with these attributes. "
            "Describe their travel-related characteristics.\n"
            "Findings from studies of Filipino commuters. Use them as background on how "
            "people like this commuter tend to travel, not as fixed rules, and do not let "
            "them contradict the attributes above:\n"
            + findings + "\n"
            + JEEPNEY_CONTEXT
            + PERSONA_DEFINITIONS
            + JSON_ONLY
            + PERSONA_FORMAT + "\nThe JSON Response:\n")


# -------------------------------------------------------------------- 2. habits
def habits_format(modes):
    weights = ", ".join(f'"{m}": number from 0 to 1' for m in modes)
    return (
        # INTEGRATION (v0.3-abm5): places are named by category, never by an id
        '{"frequent_locations": [{"place": "home or a place category from the place options", '
        # INTEGRATION: the simulation's activity names instead of "home, work, school, market, or
        # another activity role" (each place's activity must fit its category)
        '"role": "home, work, school, shopping, leisure, personal, or out_of_area"}, ...],\n'
        ' "recurring_trips": [{"trip_id": "R1", "origin": "home or a place category", '
        '"destination": "home or a place category", "trip_purpose": "short purpose such as work or shopping", '
        '"travel_days": ["Monday", "Tuesday", ...], "usual_departure_time": "HH:MM", '
        f'"mode_preference_weights": {{{weights}}}}}, ...]}}')


def habits_prompt(commuter, locations, modes):
    return ("You are:\n" + J(commuter) + "\n"
            "You live in Santa Rosa City, Laguna. Describe your usual weekly travel routine "
            "before the simulation starts: the places you go to regularly and the recurring "
            "trips that connect them.\n"
            + JEEPNEY_CONTEXT +
            # INTEGRATION (v0.3-abm5): places are categories; the simulation uses the nearest one
            "Every place must be one of the place options and can not be anything else: \"home\" "
            "is your home, and every other option is a kind of place in Santa Rosa.\n"
            "You can work at any kind of place (for example an office, a shop, a market, a school, a "
            "healthcare place, or a mall), go to school at a school, shop at a market, shop or mall, "
            "spend leisure time at a mall, recreation or food place, and do personal errands at "
            "healthcare, services or worship places.\n"
            "place options:\n" + J(locations) + "\n"
            "For each recurring trip, give the origin, destination, trip purpose, the days "
            "you usually make the trip, your usual departure time in 24-hour HH:MM, and how "
            "strongly you tend toward each transport mode available to you.\n"
            f"Your available transport modes are: {', '.join(modes)}. Use only these modes. "
            "The mode preference weights of each trip must add up to 1.\n"
            "Include at least one recurring trip, and include the trips that return you home.\n"
            # INTEGRATION: the simulation follows each day's trips in order, so they must chain;
            # and the rules its checks found broken most often (v0.3-abm2/3, 2026-11-10)
            # abm10 (v0.5): outings can chain several places before going home
            "An outing can be a round trip from home, for example home to workplace at 07:00 and "
            "workplace to home at 17:00, Monday to Friday. It can also visit several places before going "
            "home, for example home to mall at 14:00, mall to market at 17:00, and market to home at "
            "18:30, on Saturdays.\n"
            "On each travel day, your trips in order of departure time must start at home, leave "
            "from where the trip before ended, and end at home.\n"
            # INTEGRATION abm8: Llama overlapped two outings on one day (A000933, 2026-11-11)
            "Outings on the same day must not overlap: come back home from one outing before you "
            "leave home for the next, or go straight from one place to the next. "
            "Give each trip on a day a different departure time.\n"
            "Write every place exactly as it is written in the place options. Each trip goes "
            "from one place to a different place.\n"
            + JSON_ONLY
            + habits_format(modes) + "\nThe JSON Response:\n")


# ------------------------------------------------------------------ 3. schedule
SCHEDULE_FORMAT = (
    '{"simulation_day": the simulation day number,\n'
    ' "schedule": [{"start_time": "HH:MM", '
    # INTEGRATION: the simulation needs when each stay ends (the trip to the next place leaves then)
    '"end_time": "HH:MM when you leave this place (leave it out for the last entry)", '
    '"activity": "a one sentence description of what you start doing at that time", '
    # INTEGRATION (v0.3-abm4): other listed places allowed too (manuscript 4.3.5: day-to-day variation)
    '"place": "home, the kind of one of your frequent places, or one of the other kinds of place listed", '
    '"trip_purpose": "the purpose of going to this location"}, ...]}')


def schedule_prompt(commuter, habits, travel_experience, context, other_places=None):
    experience = J(travel_experience) if travel_experience else "No travel experience recorded yet."
    return ("You are:\n" + J(commuter) + "\n"
            "Your usual travel routine:\n" + J(habits) + "\n"
            "Your significant travel experiences from previous days:\n" + experience + "\n"
            "Today:\n" + J(context) + "\n"
            # INTEGRATION (v0.3-abm4): places besides the frequent ones
            + ("Other kinds of place you could go to today:\n" + J(other_places) + "\n"
               if other_places else "")
            + "Write in broad strokes what you are doing during the day. Start the day at home "
            "and end the day at home. Follow your usual routine for this day of the week, but "
            "you may vary it reasonably because of your previous travel experiences or "
            "today's conditions.\n"
            # INTEGRATION (v0.3-abm4): usually the frequent locations, but not only them
            "Only include tasks that occur at a specific place: usually one of your frequent "
            "places, or, when your day calls for it, one of the other kinds of place listed above. "
            "Do not include any transportation or commuting tasks (for "
            "example, do not include actions like \"riding a jeepney\" or \"walking to work\").\n"
            "List the activities in chronological order with 24-hour times, and do not let "
            "activities overlap.\n"
            # INTEGRATION: the end time of each stay, and travel time between places
            "Give each place an end_time, when you leave it. Leave enough time between leaving "
            "one place and starting at the next for the trip there.\n"
            + JSON_ONLY
            + SCHEDULE_FORMAT + "\nThe JSON Response for today:\n")


# --------------------------------------------------------------------- 4. modes
def mode_format(candidates):
    items = ",".join(
        f'{{"trip_id": "{c["trip_id"]}", '
        '"reasoning": "a one sentence reasoning for your decision", '
        f'"selected_mode": "<{"/".join(m["mode"] for m in c["candidate_modes"])}>"}}'
        for c in candidates)
    return '{"simulation_day": the simulation day number, "decisions": [' + items + ']}'


def mode_prompt(commuter, habits, travel_experience, schedule, candidates):
    experience = J(travel_experience) if travel_experience else "No travel experience recorded yet."
    return ("You are:\n" + J(commuter) + "\n"
            "You live in Santa Rosa City, Laguna.\n"
            + JEEPNEY_CONTEXT +
            "Your usual travel routine and how strongly you tend toward each mode:\n"
            + J(habits) + "\n"
            "Your significant travel experiences from previous days:\n" + experience + "\n"
            "Your schedule today:\n" + J(schedule) + "\n"
            "Your trips today and the options for each trip are:\n" + J(candidates) + "\n"
            "All times, distances, and costs above were computed for you. Do not change them "
            "and do not estimate your own.\n"
            "For each trip, write one personal sentence explaining your choice, then pick the "
            "mode. Compare the total time of each option with the time you have in your "
            "schedule, and weigh it against cost, safety, comfort, and accessibility according "
            "to your sensitivities, your walking and cycling tolerance, your usual habits, and your previous experiences.\n"
            "Pick exactly one mode for every trip, and only from the options listed for that "
            "trip.\n"
            # INTEGRATION: the simulation's vehicle rule and its on-time rule (llm_tasks.py)
            "A bicycle or private car trip needs the vehicle where the trip starts: at home at "
            "first, then wherever you last left it. If you leave it somewhere, use it again to "
            "come back: by the end of the day it must be back home.\n"
            "If an option would arrive after your next trip leaves while another option would "
            "arrive in time, do not pick the late one.\n"
            "Return exactly one JSON object and nothing else:\n"
            + mode_format(candidates) + "\n\nNow, your JSON response:")


# ---------------------------------------------------------------- 5. reflection
def reflection_format(habits):
    items = ",".join(
        f'{{"trip_id": "{t["trip_id"]}", "mode_preference_weights": {{'
        + ", ".join(f'"{m}": number from 0 to 1' for m in t["mode_preference_weights"])
        + '}}'
        for t in habits["recurring_trips"])
    return ('{"travel_experience_entry": {"simulation_day": the simulation day number, '
            '"reflection": "concise reflection on today\'s significant travel experiences and '
            'how they may influence your future travel"},\n'
            ' "updated_mode_preference_weights": [' + items + ']}')


def reflection_prompt(commuter, habits, travel_experience, schedule, sim_outputs):
    experience = J(travel_experience) if travel_experience else "No travel experience recorded yet."
    return ("You play the role of the person:\n" + J(commuter) + "\n\n"
            "-----TASK INSTRUCTION-----\n"
            "The day is over. Reflect on today's travel and decide whether your usual "
            "transport habits should change.\n\n"
            "-----OUTPUT FORMAT-----\n"
            + reflection_format(habits) + "\n\n"
            "-----REFLECTION GUIDELINES-----\n"
            "- Compare what you planned with what actually happened on each trip.\n"
            "- Keep only major disruptions, unexpected conditions, or notable outcomes that "
            "are likely to influence your future travel. Leave out routine or insignificant "
            "events. If nothing significant happened, say so briefly.\n"
            "- Do not fabricate events or conditions. Base the reflection only on today's "
            "recorded trip outcomes.\n\n"
            "-----HABIT UPDATE GUIDELINES-----\n"
            "- Return the mode preference weights for every recurring trip.\n"
            "- Change weights only when today's experience together with your previous travel "
            "experiences justifies it. If no change is justified, return the current weights "
            "unchanged.\n"
            "- Use only the modes already listed for each trip. The weights of each trip must "
            "add up to 1.\n"
            # INTEGRATION: the simulation's daily limit (settings.MAX_WEIGHT_CHANGE)
            "- No weight may change by more than 0.2 in one day.\n"
            "- Do not change locations, trip purposes, travel days, or departure times.\n\n"
            "-----ABOUT JEEPNEYS-----\n"
            + JEEPNEY_CONTEXT + "\n"
            "-----TODAY'S CONTEXT-----\n"
            "Your usual travel routine and current mode preference weights:\n"
            + J(habits) + "\n"
            "Your significant travel experiences from previous days:\n" + experience + "\n"
            "Your schedule today:\n" + J(schedule) + "\n"
            "What actually happened on today's trips:\n" + J(sim_outputs) + "\n\n"
            "Now generate the JSON output:")
