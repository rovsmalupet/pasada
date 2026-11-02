# Demand files: what the LLM writes, and what the simulation gives it

A simulated day takes three steps (thesis plan B.3, daily loop steps 1, 3 and 4). The LLM writes
the schedule, the simulation lists the options for every trip with their numbers, and the LLM
picks one per trip:

| Step | File | Written by | What it is |
|---|---|---|---|
| 0 | `agents.json` | `make_agents.py` | **What the LLM is given about people and places.** Each agent's `agent_id`, `home_id`, home (lat/lon), `has_car`, `has_bike`; every activity location (`location_id`, category, name, lat/lon) |
| 1 | `schedule_day_1.json` | the LLM (now `llm_stub.py schedule`) | **Where and when.** Each agent's day, with no modes |
| 2 | `choices_day_1.json` | `make_choices.py` | **The options.** Every way each trip can be made, with distance, time, waiting, walking and cost |
| 3 | `modes_day_1.json` | the LLM (now `llm_stub.py modes`) | **The choice.** One option per trip, with a reason |

`run.py` reads steps 1 and 3. The LLM never sets a transport value (Sec. 4.3.1): every number in
step 2 comes from the network and SUMO's router, and the simulation checks every choice.

```powershell
python llm_stub.py schedule    # step 1 (stand-in; --force to overwrite)
python make_choices.py         # step 2
python llm_stub.py modes       # step 3 (stand-in; --force to overwrite)
python convert_demand.py       # check steps 1 + 3 without running them
python run.py                  # simulate them
```

## Step 1: the schedule

```json
{
  "day": 1,
  "made_by": "which model / prompt wrote this",
  "agents": [
    {"agent_id": "A0001", "schedule": [
        {"location_id": "HOME_A0001", "activity": "home", "end": "07:12"},
        {"location_id": "LOC_0042", "activity": "work", "start": "07:30", "end": "16:45",
         "reasoning": "starts work at 7:30"},
        {"location_id": "HOME_A0001", "activity": "home"}
    ]},
    {"agent_id": "A0002", "schedule": [
        {"location_id": "HOME_A0002", "activity": "home"}
    ]}
  ]
}
```

A schedule is the agent's day in order. Each entry is a **stay at one place**:

| Field | Needed | Meaning |
|---|---|---|
| `location_id` | yes | The agent's own `home_id` (`HOME_<agent_id>`), or a `location_id` from `agents.json` |
| `end` | on every entry but the last | `HH:MM`: when they leave this place. **The trip to the next entry departs then** |
| `activity` | no | What they do there (`home`, `work`, `school`, `market`, ...). Used in the trip type, e.g. `home_to_work` |
| `start` | no | Planned start, for reading only: people arrive when their trip ends |
| `reasoning` | no | Why, in a sentence. Kept for reading |

- **A trip** is made whenever two consecutive entries are at different places. It leaves at the
  earlier entry's `end`. Trips are numbered per agent: `A0001_D01_T01`, `A0001_D01_T02`, ...
  (D = day, T = trip). This is the fixed rule of the thesis plan, Phase 5.
- The day starts at the agent's home, and the `end` times increase. An agent who stays home all
  day has one entry and no trips. Agents left out of the file make no trips.

## Step 2: the options (made by the simulation)

```json
{"agent_id": "A0001", "has_car": true, "has_bike": true, "trips": [
    {"trip_id": "A0001_D01_T01", "from": "HOME_A0001", "to": "LOC_0167",
     "to_name": "Santa Rosa Hospital & Medical Center", "trip_type": "home_to_work", "depart": "07:44",
     "options": [
        {"mode": "jeepney", "route_id": "RIZ-FRS_S", "distance_m": 1482, "access_walk_m": 313,
         "in_vehicle_m": 847, "egress_walk_m": 323, "estimated_total_time_s": 692, "access_time_s": 225,
         "waiting_time_s": 150, "in_vehicle_time_s": 85, "egress_time_s": 232, "cost_php": 13.0},
        {"mode": "bike", "distance_m": 1693, ..., "estimated_total_time_s": 321, "cost_php": 0.0},
        {"mode": "car", "distance_m": 1667, ..., "estimated_total_time_s": 292, "cost_php": 9.71}
     ]}
]}
```

The file starts with `rules`: the same notes as below, in short. There is one option per way a trip
can be made, and only ways that work are listed:

| Option | When it is listed |
|---|---|
| `jeepney` | One per line (`route_id`) that passes within 400 m on foot of both ends, with at least 500 m of ride |
| `walk` | The walking route is at most 1,500 m |
| `bike` | The agent has a bike, and the ride is at most 5,000 m |
| `car` | The agent has a car |

| Field | Meaning |
|---|---|
| `distance_m` | Door to door, in metres |
| `estimated_total_time_s` | Door to door, in seconds, **on empty roads**. For everything but a walk it is the sum of the four parts below |
| `access_time_s` | Walking to the jeepney stop or the parked bike or car, plus 60 s getting the car |
| `waiting_time_s` | Jeepney only: half the time between jeepneys (they come every 5 min) |
| `in_vehicle_time_s` | Riding the jeepney or bike, or driving |
| `egress_time_s` | Walking on to the door, plus 120 s parking the car |
| `access_walk_m`, `in_vehicle_m`, `egress_walk_m` | The same parts as distances |
| `cost_php` | Jeepney: the fare (₱14 up to 4 km, then ₱2 per km started: the traditional jeepney fare from 28 September 2026, without discounts). Car: ₱6 per km (fuel only). Walk and bike: 0 |
| `route_id` | Jeepney only: the line, e.g. `RIZ-FRS_S` (Rizal Boulevard then Felix Reyes Street, south-bound) |

**One rule the options cannot show:** a bike or car trip needs the vehicle where the trip starts.
It is at home at the start of the day, then wherever it was last left. Someone who walks to work
can't drive home, even though `car` is listed for the trip home.

The fare, car cost, walking limits and jeepney frequency are stand-ins in `../settings.py`, to be
replaced with real data.

## Step 3: the choice

```json
{
  "day": 1,
  "made_by": "which model / prompt wrote this",
  "agents": [
    {"agent_id": "A0001", "selections": [
        {"trip_id": "A0001_D01_T01", "mode": "jeepney", "route_id": "RIZ-FRS_S",
         "reasoning": "cheapest, and the hospital is near the line"},
        {"trip_id": "A0001_D01_T02", "mode": "jeepney", "route_id": "RIZ-FRS_N"}
    ]}
  ]
}
```

| Field | Needed | Meaning |
|---|---|---|
| `trip_id` | yes | A trip from the options file |
| `mode` | yes | `jeepney`, `walk`, `bike` or `car` |
| `route_id` | with `jeepney` only | Which line: one of that trip's jeepney options |
| `reasoning` | no | Why, in a sentence. Kept for reading |

`modes.schema.json` gives this format as a JSON Schema.

## What the simulation checks

1. **The format.** Unknown fields (a typo such as `"mdoe"`), unknown places or lines, bad times,
   or times out of order **stop the conversion**. So does a trip with no choice, or a choice for
   a trip that isn't in the schedule. You get a list of every problem, e.g.
   ```
   modes A0003 selections[0]: unknown key 'mdoe'
   modes file: no choice for trip A0001_D01_T02
   ```
2. **Whether each chosen trip can be made.** If one can't, the **agent's whole day is left out**
   and the reason is printed; every other agent still runs. This happens when the agent has no
   car or bike, when the vehicle is elsewhere, when the line doesn't serve the trip, or when the
   walk or ride is too long. Choosing from the options avoids all of these except the vehicle
   rule.

## How a trip is simulated

| Mode | In SUMO |
|---|---|
| jeepney | Walk to the roadside spot on the chosen line, flag a jeepney of that line, ride, walk to the door |
| walk | Walk the shortest route |
| bike | Walk to the bike on the nearest road a bike may use (usually a few metres), ride, walk to the door |
| car | The car is at the house: drive off (after 60 s getting the car), park near the activity (120 s), walk to the door. The reverse on the way home |

## One file instead of two

A schedule may also carry the modes itself: `"mode"` on every entry at a different place from the
one before (how they get there). Then no modes file is needed and the line is the one with the
shortest walk, e.g. `python run.py --demand my_day.json`. This is handy for hand-written test days.

Each run copies the files it used into its folder (`runs/<date_time>/`), with the SUMO file made
from them (`commuters.rou.xml`) and the trips with their estimates (`trips.json`).
