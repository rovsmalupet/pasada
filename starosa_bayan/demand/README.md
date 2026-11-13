# The LLM ↔ simulation hand-over: every file, who writes it, and what is checked

This folder holds every file passed between the LLM and the simulation for a simulated day,
following the manuscript's workflow (Sec. 4.3): five LLM tasks, with logic and SUMO between them.
`../pipeline.py` runs the whole sequence:

| Step | Who | Gets | Writes | Format |
|---|---|---|---|---|
| agents | logic (`make_agents.py`) | the LLM team's base agents (`../data/base_agents.json`) | `agents.json` | below |
| **persona** | **LLM**, once | `llm_inputs/persona_input.json` | `personas.json` | `persona.schema.json` |
| **habits** | **LLM**, once | `llm_inputs/habits_input.json` | `habits.json` | `habits.schema.json` |
| **schedule** | **LLM**, every day | `llm_inputs/schedule_input_day_N.json` | `schedule_day_N.json` | `day.schema.json` |
| choices | logic (`make_choices.py`) | the schedule | `choices_day_N.json` | below |
| **modes** | **LLM**, every day | `llm_inputs/modes_input_day_N.json` | `modes_day_N.json` | `modes.schema.json` |
| simulate | SUMO (`run.py`) | the schedule and the modes | `outcomes_day_N.json` | below |
| **reflection** | **LLM**, every day | `llm_inputs/reflection_input_day_N.json` | `reflection_day_N.json` | `reflection.schema.json` |

**The LLM never sets a transport value** (Sec. 4.3.1). Every distance, time, cost and outcome
comes from the network, SUMO's router and the simulation. The LLM chooses among them and
explains why.

## Running it

From the folder above, with the venv active (made as in `../README.md`, Setup):

```powershell
python pipeline.py --day 1 --llm stub     # the whole day, with llm_stub.py standing in for the LLM
python pipeline.py --day 1                # with the LLM: stops at the first answer that is missing
python pipeline.py --day 2                # the next day (needs day 1's reflection)
python pipeline.py --day 1 --redo modes   # modes and every later step of day 1 again
```

**How the LLM plugs in.** At each LLM step the pipeline writes the task's input file in
`llm_inputs/`: exactly what the manuscript says that prompt is given, one agent per line. Then:
1. Without `--llm stub`, it stops and names the file to write.
2. The LLM team's code reads the input file. It calls the model once per agent: put the agent's
   line in the prompt, with the task's `rules` and the behavioural knowledge base. It writes the
   answer file in the format below. Each schema's `$defs/agent` is one agent's answer, and local
   model servers (Ollama, vLLM, LM Studio) can force the model's output into that JSON schema.
3. Running the pipeline again checks the answer and goes on.

**Every answer is checked** (`../llm_tasks.py`) by the manuscript's objective validation
criteria that logic can check. If anything fails, the pipeline stops and lists every problem by
agent, e.g.

```
demand\habits.json fails 2 check(s) (llm_tasks.py); regenerate those agents, then run again:
  A000033 recurring_trips[0]: weights add up to 1.300, not 1
  A000040 Mon: the day's recurring trips end at LOC_0241, not at home
```

Regenerate those agents' answers and run again (Sec. 4.3.9.1). Behavioural quality (does the
persona suit the agent?) is not checked here: that is the rubric evaluation of Sec. 4.3.9.2.

`--redo` never deletes: it moves the files into `old/<date_time>/` (not committed).

## The agents and places (`agents.json`, logic)

```json
{"agent_id": "A000150", "home_id": "HOME_A000150", "home": {"lat": 14.302927, "lon": 121.11},
 "barangay": "Pooc", "age_group": "35 - 39", "sex": "Female", "commuter_type": "employed",
 "employment_status": "employed", "school_status": "unknown", "occupation_category": "Service and Sales Workers",
 "per_capita_income_decile": 3, "household_income_group": "Third Decile", "household_car_access": false,
 "household_bicycle_access": true, "household_motorcycle_access": true}
```

- **The base agents are the LLM team's** (`PASADA-llm/base_commuter_agents`). `make_agents.py`
  copies their file into `../data/base_agents.json` with its provenance whenever it changes.
- It keeps only agents living in the study area's 9 barangays, and not under 5.
  `settings.SAMPLE_AGENTS` of them are drawn at random (100 now; 0 = all 402).
- Each gets a home inside their own barangay: a street drawn in proportion to the people living
  near it (the population grid). `home_id` is the home's place id in every file.
- `locations` lists the 756 activity locations: `location_id`, `category`, `name`, `barangay`,
  lat/lon. The categories are workplace, school, market, mall and terminal, and also healthcare,
  services, worship, food, recreation and shop, which no activity uses yet.
- **Modes are jeepney, walk, bike and car** (manuscript Sec. 4.5.1). Household motorcycle access
  is passed on, but no motorcycle mode exists.

## 1. Persona (LLM, once)

**Given** (`persona_input.json`): `rules`, and per agent the attributes above, without the home's
coordinates.

**Answer** (`personas.json`):

```json
{"made_by": "model, prompt version", "agents": [
  {"agent_id": "A000150", "persona": {
     "description": "Female, aged 35 - 39, living in Pooc; employed (Service and Sales Workers); ...",
     "private_car_access": false, "bicycle_access": true,
     "walking_predisposition": "High", "cycling_predisposition": "Moderate",
     "sensitivities": {"safety": "Moderate", "cost": "High", "comfort": "Moderate", "accessibility": "Moderate",
                       "availability": "Moderate", "efficiency": "High", "reliability": "High"},
     "general_transport_preference": "Prefers jeepneys for longer trips and walking for short ones."}}
]}
```

**Checked:**
- every agent is answered, once;
- every field is present, with no extra ones;
- levels are `Low`, `Moderate` or `High`;
- `private_car_access` only with `household_car_access` and an age group from 20
  (`CAR_DRIVER_MIN_AGE`);
- `bicycle_access` only with `household_bicycle_access`.

From then on, the persona's access decides whether the agent is offered a car or bike.

## 2. Habits (LLM, once)

**Given** (`habits_input.json`): per agent, the agent with persona, `usable_modes`, and
`activity_locations`. These are the nearest locations of each category (up to 8) that one of
the agent's modes can reach and come back from:

```json
{"location_id": "LOC_0021", "category": "mall", "name": "Tram Plaza", "barangay": "Balibago",
 "distance_m": 600, "reachable_by": ["jeepney", "walk", "bike"]}
```

`distance_m` is a straight line from home. The real distances come with the options (step
"choices").

**Answer** (`habits.json`):

```json
{"agent_id": "A000150",
 "frequent_locations": [{"location_id": "LOC_0001", "activity": "out_of_area"}],
 "recurring_trips": [
   {"trip_key": "R1", "origin": "HOME_A000150", "destination": "LOC_0001", "purpose": "out_of_area",
    "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "usual_departure": "07:00",
    "mode_preference_weights": {"jeepney": 0.6, "walk": 0.0, "bike": 0.4}},
   {"trip_key": "R2", "origin": "LOC_0001", "destination": "HOME_A000150", "purpose": "home",
    "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "usual_departure": "17:00",
    "mode_preference_weights": {"jeepney": 0.6, "walk": 0.0, "bike": 0.4}}]}
```

| Activity | At locations of category |
|---|---|
| `work` | workplace, school, market, mall, terminal (teachers, vendors and shop staff too) |
| `school` | school |
| `shopping` | market, mall |
| `leisure` | mall |
| `out_of_area` | terminal: leaves the study area by bus and comes back the same way |
| `home` | the agent's own `home_id` (purpose of a trip home) |

**Checked:**
- every location is one of the agent's `activity_locations`, and its activity fits the category;
- `origin` and `destination` are home or a frequent location, and differ;
- `purpose` is the activity at the destination;
- `trip_key` values are unique;
- `days` are taken from `Mon` to `Sun`; `usual_departure` is `HH:MM`;
- the weights cover exactly the agent's `usable_modes`, each from 0 to 1, adding up to 1 (±0.01);
- each day's trips, in order of departure, start at home, go on from where the one before
  ended, and end at home;
- at least one trip, unless the agent was offered no location.

## 3. Schedule (LLM, every day)

**Given** (`schedule_input_day_N.json`):
- `day`, `weekday` (day 1 is a Monday) and `simulation_context`;
- per agent: the agent with persona, `frequent_locations` (with name and category),
  `recurring_trips` without the weights (Sec. 4.3.5: modes come later), and `travel_experience`
  (the last 3 days' reflections).

**Answer** (`schedule_day_N.json`):

```json
{"day": 1, "made_by": "model, prompt version", "agents": [
  {"agent_id": "A000150", "schedule": [
     {"location_id": "HOME_A000150", "activity": "home", "end": "07:01"},
     {"location_id": "LOC_0001", "activity": "out_of_area", "end": "17:04", "reasoning": "usual workday"},
     {"location_id": "HOME_A000150", "activity": "home"}]}
]}
```

A schedule is the agent's day in order. Each entry is a **stay at one place**:

| Field | Needed | Meaning |
|---|---|---|
| `location_id` | yes | The agent's `home_id` or a `location_id` from `agents.json` |
| `end` | on every entry but the last | `HH:MM`: when they leave. **The trip to the next entry departs then** |
| `activity` | no | `home` at home; elsewhere an activity that fits the place (table above) |
| `start` | no | Planned arrival, for reading only: people arrive when their trip ends |
| `reasoning` | no | Why, in a sentence |

- A trip is made whenever two consecutive entries are at different places, leaving at the
  earlier entry's `end`. Trips are numbered per agent: `A000150_D01_T01`, `A000150_D01_T02`, ...
  (D = day, T = trip).
- The day starts and ends at home, and the `end` times increase. An agent staying home has one
  entry.

**Checked:**
- every agent appears, once;
- places exist; the day starts and ends at home; the `end` times increase;
- no mode is given;
- activities fit their places;
- no `start` is before the previous entry's `end`.

## Choices (logic, every day)

`make_choices.py` lists every way each trip can be made (`choices_day_N.json`). The modes input
carries the same options:

```json
{"trip_id": "A000150_D01_T01", "from": "HOME_A000150", "to": "LOC_0001", "to_name": "Alabang Jeepney Terminal",
 "trip_type": "home_to_out_of_area", "depart": "07:01", "options": [
   {"mode": "jeepney", "route_id": "RIZ-N1_N", "distance_m": 1713, "access_walk_m": 125, "in_vehicle_m": 1560,
    "egress_walk_m": 28, "estimated_total_time_s": 429, "access_time_s": 90, "waiting_time_s": 150,
    "in_vehicle_time_s": 169, "egress_time_s": 20, "cost_php": 14.0},
   {"mode": "bike", "distance_m": 2157, "access_walk_m": 0, "in_vehicle_m": 2157, "egress_walk_m": 0,
    "estimated_total_time_s": 403, "access_time_s": 0, "waiting_time_s": 0, "in_vehicle_time_s": 403,
    "egress_time_s": 0, "cost_php": 0.0}]}
```

| Option | When it is listed |
|---|---|
| `jeepney` | One per line (`route_id`) passing within `LINE_WALK` (10 km, a test) on foot of both ends, with at least 500 m of ride |
| `walk` | The walking route is 20 to 1,500 m |
| `bike` | The agent has a bike (persona), and the ride is at most 5,000 m |
| `car` | The agent has a car (persona) |

| Field | Meaning |
|---|---|
| `distance_m` | Door to door, in metres |
| `estimated_total_time_s` | Door to door, in seconds, **on empty roads**. Except for a walk, the sum of the four parts below |
| `access_time_s` | Walking to the jeepney stop or the parked bike or car, plus 60 s getting the car |
| `waiting_time_s` | Jeepney only: half the time between jeepneys (one every 5 min) |
| `in_vehicle_time_s` | Riding the jeepney or bike, or driving, at the speed limits |
| `egress_time_s` | Walking on to the door, plus 120 s parking the car |
| `access_walk_m`, `in_vehicle_m`, `egress_walk_m` | The same parts as distances |
| `cost_php` | Jeepney: ₱14 up to 4 km, then ₱2 per km started (traditional jeepney, from 28 September 2026, no discounts). Car: ₱6 per km (fuel). Walk and bike: 0 |

Speed limits are 40 km/h on national roads and 30 km/h on city streets (RA 4136 and JMC
2018-001), unless OSM tags a road otherwise.

If a trip has no option, the pipeline stops: that agent's schedule must change (`--redo schedule`).

## 4. Modes (LLM, every day)

**Given** (`modes_input_day_N.json`): per agent with trips, the agent with persona,
`recurring_trips` with the current weights, `travel_experience`, today's `schedule`, and `trips`
with their options (above).

**Answer** (`modes_day_N.json`):

```json
{"day": 1, "made_by": "model, prompt version", "agents": [
  {"agent_id": "A000150", "selections": [
     {"trip_id": "A000150_D01_T01", "mode": "jeepney", "route_id": "RIZ-N1_N", "reasoning": "cheap, and quick enough"},
     {"trip_id": "A000150_D01_T02", "mode": "jeepney", "route_id": "RIZ-N1_S"}]}
]}
```

`route_id` goes with a jeepney only. `reasoning` is optional.

**Checked:**
- one choice per trip, for trips that exist;
- the choice is one of that trip's options;
- **a bike or car only where it is**: at home at first, then where it was last left (someone
  who walks to work cannot drive home);
- no choice that arrives after the next trip leaves when another option would arrive in time
  (Sec. 4.3.6).

## Outcomes (SUMO, every day)

`run.py` simulates the day and writes `outcomes_day_N.json`: one record per trip that departed,
grouped by agent (the plan's trip outcome, Part D.4).

```json
{"trip_id": "A000150_D01_T01", "realized_mode": "jeepney", "route_id": "RIZ-N1_N",
 "departure": "07:01", "arrival": "07:11", "actual_departure_s": 25260, "arrival_time_s": 25913,
 "actual_total_time_s": 653, "estimated_total_time_s": 429, "delay_ratio": 1.52,
 "actual_access_time_s": 99, "actual_waiting_time_s": 256, "actual_in_vehicle_time_s": 276,
 "actual_egress_time_s": 22, "full_pass_bys_experienced": 0, "distance_m": 1713, "cost_php": 14.0,
 "disrupted": false, "disruption_reason": null, "completed": true}
```

| Field | Meaning |
|---|---|
| `departure`, `arrival` | When the trip left and arrived, `HH:MM`. `actual_departure_s` and `arrival_time_s` are the same in seconds of the day |
| `actual_total_time_s` | Door to door, as simulated |
| `estimated_total_time_s` | The estimate of the option chosen (on empty roads) |
| `delay_ratio` | `actual_total_time_s / estimated_total_time_s` |
| `actual_access_time_s`, `actual_waiting_time_s`, `actual_in_vehicle_time_s`, `actual_egress_time_s` | Jeepney only: the same parts as the options. Access ends where they flagged the jeepney, which can be before the planned spot. Other modes have only `actual_waiting_time_s`, 0 |
| `full_pass_bys_experienced` | Full jeepneys of the line that came by while the commuter waited (or walked along the route) and could not take them |
| `distance_m`, `cost_php` | Those of the option chosen |
| `disrupted`, `disruption_reason` | The trip took at least twice its estimate **and** at least 10 min longer, or had not arrived when the run ended (`DISRUPTION_RATIO`, `DISRUPTION_MIN_S`, stand-ins). `null` for a trip still underway when a run was stopped early |
| `completed` | Whether it arrived before the run ended |

- The file starts with `whole_day` (false when the run stopped early, e.g. with `--end`),
  `run_ended` and `trips_not_departed`.
- An agent whose day could not be simulated as chosen has `left_out` with the reason, and no
  trips.
- Times match SUMO's own `tripinfo.xml` to the second; this was checked on all 9,942 trips of a
  whole day.

## 5. Reflection (LLM, every day)

**Given** (`reflection_input_day_N.json`):
- `day`, `weekday`, `simulation_context`, `whole_day`;
- per agent: the agent with persona, `recurring_trips` with the current weights,
  `travel_experience`, today's `schedule`, and `trips`. Each trip has its planned `from`, `to`,
  `to_name`, `trip_type`, `depart` and its `outcome` (above, `null` if it did not depart).

**Answer** (`reflection_day_N.json`):

```json
{"day": 1, "made_by": "model, prompt version", "agents": [
  {"agent_id": "A000150",
   "travel_experience": "Day 1: the jeepney to the terminal took 11 min against 7 expected; I waited 4 min.",
   "habit_update": {"R1": {"jeepney": 0.5, "walk": 0.0, "bike": 0.5}}},
  {"agent_id": "A000164", "travel_experience": "Day 1: an ordinary day.", "habit_update": {}}
]}
```

**Checked:**
- every agent asked about is answered, once;
- `travel_experience` is text;
- `habit_update` keys are the agent's `trip_key`s, `{}` for no change;
- the new weights follow the habits' rules, and **no weight changes by more than 0.2 in a day**
  (`MAX_WEIGHT_CHANGE`, a stand-in for the manuscript's daily-change limit).

## How the agents' state carries over

No answer file is ever changed afterwards (Sec. 4.3.4).
- **The travel experience on day N** is the `travel_experience` of the reflections of days 1 to
  N−1. Prompts get the last 3 (`EXPERIENCE_DAYS`, a stand-in for the context window the
  manuscript leaves to Chapter 5).
- **The mode weights on day N** are `habits.json`'s, with each reflection's `habit_update` of days
  1 to N−1 applied in order.

That is why day N needs day N−1's reflection.

## How a trip is simulated

| Mode | In SUMO |
|---|---|
| jeepney | Walk to the roadside spot on the chosen line, flag a jeepney of that line, ride, walk to the door |
| walk | Walk the shortest route |
| bike | Walk to the bike on the nearest road a bike may use (usually a few metres), ride, walk to the door |
| car | The car is at the house: drive off (after 60 s getting the car), park near the activity (120 s), walk to the door. The reverse on the way home |

Each run's folder (`../runs/<date_time>/`) keeps:
- the files it used;
- the SUMO file made from them (`commuters.rou.xml`);
- the trips with their estimates (`trips.json`);
- the outcomes;
- `run.log`, with every event the controller logged.

## One file instead of two (for hand-written tests)

A schedule may carry the modes itself, as `"mode"` on every entry at a different place from the
one before (how they get there). Then no modes file is needed, and the jeepney line is the one
with the shortest walk: `python run.py --demand my_day.json`. This skips the pipeline and its
checks.
