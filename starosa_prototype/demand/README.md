# Demand files: what the LLM writes, and what the simulation reads

The simulation's commuters come from one readable file per day, **`day_1.json`**. The LLM
writes it. Until the LLM is connected, `llm_stub.py` writes a stand-in in the same format.
`run.py` reads it at the start of every run.

The file says **who goes where, when, and by which mode**. It holds no transport values. The
code works out every route, boarding spot, travel time and parking place, and checks that
each trip can be made (thesis Sec. 4.3.1: the LLM never sets a transport value).

## Files in this folder

| File | Written by | What it is |
|---|---|---|
| `agents.json` | `make_agents.py` (code) | **What the LLM is given.** Each agent's `agent_id`, `home_id`, home location (lat/lon, from the population grid), and whether they have a car or bike. Also every activity location they can go to: `location_id`, category, name, lat/lon |
| `day_1.json` | the LLM (now `llm_stub.py`) | **The demand file.** Each agent's schedule for the day |
| `dummy_day.json` | by hand | A small example: 10 agents with one case each (every mode, lunch on foot while the car stays at work, a stop on the way home, a mixed-mode day, a day at home). Run it with `run.py --demand demand\dummy_day.json` |
| `day.schema.json` | by hand | The format as a JSON Schema, for checking or constraining the LLM's output |

## Format

```json
{
  "day": 1,
  "made_by": "which model / prompt wrote this",
  "agents": [
    {"agent_id": "A0001", "schedule": [
        {"location_id": "HOME_A0001", "activity": "home", "end": "07:12"},
        {"location_id": "LOC_0042", "activity": "work", "start": "07:30", "end": "16:45",
         "mode": "jeepney", "reasoning": "cheapest way to work"},
        {"location_id": "HOME_A0001", "activity": "home", "mode": "jeepney"}
    ]},
    {"agent_id": "A0002", "schedule": [
        {"location_id": "HOME_A0002", "activity": "home"}
    ]}
  ]
}
```

A schedule is the agent's day in order. Each entry is one place:

| Field | Needed | Meaning |
|---|---|---|
| `location_id` | yes | The agent's own `home_id` (`HOME_<agent_id>`), or a `location_id` from `agents.json` |
| `end` | on every entry but the last | `HH:MM`: when they leave this place. **The trip to the next entry departs at this time** |
| `mode` | on every entry at a different place from the one before | How they get **here**: `jeepney`, `walk`, `bike` or `car` |
| `activity` | no | What they do there (`home`, `work`, `school`, `market`, ...). Used in the trip type, e.g. `home_to_work` |
| `start` | no | Planned start, for reading only. The simulation ignores it: people arrive when the trip ends |
| `reasoning` | no | Why, in a sentence. Kept for reading |

- A trip is made whenever two consecutive entries are at different places. It leaves at the
  earlier entry's `end`. (This is the fixed rule of the thesis plan, Phase 5.)
- An agent who stays home all day has one entry and no trips. Agents left out of the file
  make no trips.
- The day must start at the agent's home, and the `end` times must increase.

## What the code checks

`convert_demand.py` checks two things.

1. **The format.** Unknown fields (a typo such as `"mdoe"`), unknown places, bad times, a
   missing or extra `mode`, or times out of order **stop the conversion**. You get a list of
   every problem, for example:
   ```
   A0004 schedule[1]: unknown key 'mdoe'
   A0002 schedule[1]: location_id 'LOC_9999' is neither HOME_A0002 nor a location in agents.json
   ```
2. **Whether each trip can be made.** If one can't, the **agent's whole day is left out** and
   the reason is printed. Every other agent still runs. A trip can't be made when:
   - **jeepney:** no line passes within 400 m on foot of both ends with at least 500 m of ride
     in the line's direction;
   - **walk:** the walking route is over 1,500 m;
   - **bike or car:**
     - the agent has none (`has_car` / `has_bike` is false in `agents.json`), or
     - the vehicle is somewhere else: it stays where it was last parked, at first at home.
       So someone who walks to work can't drive home;
     - also for bikes: the ride is over 5,000 m.

   The limits are stand-ins in `settings.py`.

## How a trip is simulated

| Mode | In SUMO |
|---|---|
| jeepney | Walk to the roadside spot with the shortest walk, flag a jeepney of the line, ride, walk to the door |
| walk | Walk the shortest route |
| bike | Walk to the bike on the nearest road a bike may use (usually a few metres), ride, walk to the door |
| car | The car is at the house: drive straight off from home (after 60 s getting the car), park near the activity (120 s), walk to the door. The reverse on the way home |

## Check a file, run it

```powershell
..\sumo_track\.venv\Scripts\python.exe convert_demand.py demand\day_1.json   # check it and write generated\commuters.rou.xml + trips.json
..\sumo_track\.venv\Scripts\python.exe run.py --demand demand\day_1.json     # simulate it (the default file)
```

Each run copies the demand file into its folder (`runs/<date_time>/`), with the SUMO file
made from it (`commuters.rou.xml`) and the trips with their free-flow estimates
(`trips.json`). So you can always see what a run simulated.
