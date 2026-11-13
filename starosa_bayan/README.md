# starosa_bayan — the multi-line prototype on 9 barangays of Santa Rosa

A copy of `../starosa_prototype/`'s multi-line setup (`run.py`), made on 2026-11-03 to run the
same simulation on a bigger, better-defined map. It now runs **a whole simulated day from the
LLM to SUMO and back**, following the manuscript's workflow (Sec. 4.3):

1. The LLM team's base commuter agents get homes in their own barangays (`make_agents.py`).
2. The LLM writes each agent's **persona** and **habitual travel patterns**, once.
3. Every day:
   1. the LLM writes the **schedule**;
   2. the simulation lists every way each trip can be made, with distance, time and cost
      (`make_choices.py`);
   3. the LLM **chooses a mode** per trip;
   4. SUMO simulates the day with the jeepney flag-down controller (`run.py`) and records every
      trip's **outcome**;
   5. the LLM **reflects**, which can change the agent's mode weights for the next day.

`pipeline.py` runs these steps in order. At each LLM step it writes the input file and checks
the answer. `llm_stub.py` stands in for the LLM until it is connected. The hand-over for the
LLM team (every file, format and check) is in **`demand/README.md`**.

This is a prototype: every value in `settings.py` is a stand-in, not a thesis assumption.

## The map

**The study area is 9 barangays**, chosen by the group on 2026-11-10: Tagapo, Pooc, Labas,
Macabling, Market Area, Malusak, Ibaba, Kanluran and Balibago. They are 13.58 km², with about
214,000 people on the population grid (`settings.AREA_BARANGAYS`). Before that, the area was
the 14 barangays north-east of SLEX, which also had Dila, Sinalhan, Caingin, Aplaya and Dita.

- The boundaries are official: COD-AB from NAMRIA and PSA, via OCHA/HDX, CC BY-IGO
  (`get_barangays.py` → `data/santa_rosa_barangays.geojson`).
- **Homes and destinations are only inside the area.** The places are
  `data/locations_9_barangays.csv`: sumo_track's OSM activity locations cut to the 9 barangays.
  The roads are every OSM way touching the area, kept whole, so roads crossing its edge stick
  out a little, as in a Web Wizard box.
- **Approach roads:** the main roads leading into the area continue 500 m outside it and end
  there (`APPROACH_M`), without their side streets. Jeepneys and through traffic come in at those
  road ends instead of appearing at the area's edge.
- SLEX and its ramps are left out.
- **Speed limits** follow RA 4136 and JMC 2018-001 for cars: 40 km/h on trunk and primary roads
  (national roads, "through streets"), 30 km/h on the others ("city and municipal streets"),
  unless OSM tags a road (`typemap.typ.xml`).
- **Jeepney lines** (`settings.JEEPNEY_LINES`), each in both directions. These are the routes
  jeepneys actually run in the area, from a group member who lives there. Their frequency is
  still a stand-in: one jeepney every 5 minutes on each line (`JEEPNEY_HEADWAY_S`).

  | Line | Route |
  |---|---|
  | `N1` | National Highway 1 |
  | `RIZ-FRS` | Rizal Boulevard, then on to Felix Reyes Street |
  | `RIZ-N1` | Rizal Boulevard, then back onto National Highway 1 |

  No line runs on Arcillas Boulevard or the Biñan–Santa Rosa Access Road (removed by choice),
  nor on F. Gomez, Zavalla or Arambulo Street (shorter than a 500 m ride). The `JPRS` line
  (J. P. Rizal Street) was dropped with the 9 barangays: that street lies in Dita.

  Every line starts and ends at those road ends, about 500 m outside the area: jeepneys enter
  already moving and leave it again. Where a line's roads stop at the edge, it is led in and out
  along the main road outside: Rizal Boulevard's lines along National Highway 1 to the north, and
  Felix Reyes Street's along the Santa Rosa–Tagaytay Road.
- **Background traffic** (`make_background.py`; every number a stand-in except the counted traffic):
  - counted traffic: cars, motorcycles, buses and trucks driving the whole of National Highway 1,
    each way, at the rates Matthew's Balibago videos counted (07:10 and 14:10; other hours follow
    `BACKGROUND_PROFILE` from the nearest video), so the simulation's camera there sees what the
    video saw (`COUNT_SITES`, `counts.py`). **Still being calibrated:** it comes on top of the
    stand-ins below, and with background traffic on, the area still jams in the morning;
  - random car and motorcycle trips inside the area (`BACKGROUND_PER_HOUR` × `BACKGROUND_SCALE`);
  - traffic coming in and going out at the main road ends (`GATEWAYS`): National Highway 1 north
    and south, the Santa Rosa–Tagaytay Road and the Biñan–Santa Rosa Access Road. Each has its
    own rate per hour in each direction, as a camera there would count it; for now more go out
    than come in. Half of what comes in drives straight through (`GATEWAY_THROUGH_SHARE`); the
    rest of what goes out starts inside the area;
  - jeepneys come in with passengers aboard, about half full at the peak (`JEEPNEY_ENTRY_LOAD`).
    They get off at random roadside spots inside the area, or ride on out of it. They are not
    agents: no LLM, no outcome; they only take seats;
  - all of it follows one daily shape with morning and evening peaks (`BACKGROUND_PROFILE`).
    `--no-background` leaves all of it out.

To rebuild the map (only needed if the area or the OSM data changes):

```powershell
python get_barangays.py   # the 18 barangay boundaries (about 26 MB of download)
python make_map.py        # data/area.geojson, bayan.osm (from ../sumo_track/data/raw/), bayan.net.xml
python build.py           # jeepney lines, vehicle types, background traffic
```

`bayan.net.xml` (37 MB) is not committed. Rebuild it from the committed `bayan.osm` with
`netconvert -c netconvert.netccfg`. `make_map.py` also needs
`../sumo_track/data/raw/santa_rosa_buffered.osm`, sumo_track's local download, which is not on this branch.

## Setup

This branch holds only this folder and what it reads from outside it:

| Path | Why |
|---|---|
| `../sumo_track/data/placeholder/locations.csv` | All of Santa Rosa's activity locations, with its provenance file; `../sumo_track/config/activity_categories.yaml` has the rules that made it. Not read at run time: `data/locations_9_barangays.csv` is its rows inside the 9 barangays, and its provenance gives this file's sha256 |
| `../population_extraction/output/Santa_Rosa_Population_Density_2020.csv` | People per ~30 m cell, for the homes, with its summary file and `../population_extraction/README.md` |
| `../population_extraction/obtain_boundary.py` | Used by `get_barangays.py` |

You need SUMO 1.27.0, with `SUMO_HOME` set and `%SUMO_HOME%\tools` in `PYTHONPATH` (for `traci` and
`sumolib`), and Python 3.11 or newer (tested on 3.13). Once, from inside this folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
netconvert -c netconvert.netccfg    # bayan.net.xml, not committed
```

## Running

With the venv active, from inside this folder:

```powershell
python build.py                              # once: generated/ (lines, vehicle types, background)
python pipeline.py --day 1 --llm stub        # a whole day, the stand-in answering for the LLM (about 3 min)
python pipeline.py --day 2 --llm stub        # the next day: yesterday's reflections carry over
python pipeline.py --day 1                   # with the LLM: stops at each answer missing (demand/README.md)
python pipeline.py --day 1 --redo modes      # a step and every later one again (old files go to demand/old/)
python pipeline.py --day 1 --background      # with background cars and motorcycles (much slower)
python pipeline.py --day 3 --llm stub --scenario reduced_jeepney   # a day under a scenario (below)
```

The simulation alone, on the day's files:

```powershell
python run.py                      # sumo-gui on day 1's schedule and modes; press play
python run.py --nogui              # headless: event log and summary
python run.py --no-background      # without background cars and motorcycles (much faster)
python run.py --end 36000          # stop at 10:00 (simulated seconds; default a whole day)
python run.py --demand FILE --modes FILE   # another day's schedule and modes
python run.py --additional FILE    # also load a SUMO additional file, e.g. speed changes
python run.py --scenario reduced_jeepney   # fewer jeepneys on Rizal Boulevard (settings.SCENARIOS)
```

**Scenarios** (`settings.SCENARIOS`, manuscript Sec. 4.5.3): `reduced_jeepney` runs Rizal
Boulevard's two lines (`RIZ-FRS`, `RIZ-N1`) every 30 minutes instead of every 5. A scenario changes
only the SUMO run of the days it is given on. The trip options keep the usual estimates and no
prompt mentions it, so the commuters find out by travelling. The run's summary shows, per line, the
mean and longest wait and the most commuters waiting at the roadside at once.

Each run's folder holds the trip outcomes (`outcomes_day_<n>.json`) and the event log
(`events.jsonl`): every jeepney stop, boarding, alighting and full pass-by, each commuter's
state changes, and each jeepney's passengers every minute, as the manuscript's Sec. 4.4.2 asks.

**The measures** of the manuscript's evaluation (Sec. 4.5) that come from the simulation, over every
day run so far, baseline days and scenario days apart: mode share (overall, by income and by vehicle
access), trip time and length per mode, violations, the daily habit change, C(T), switch_rate(k),
the departure-time adaptation effect, the jeepney wait against half the headway, dwell time,
occupancy at peak and off-peak hours, the traffic counts against the videos (GEH) and the run time.
The LLM's retry rates (the LLM team's logs) and the reflection grounding rate (coded by hand) are
not here.

```powershell
python measures.py                       # demand/: prints the tables, writes demand/measures.json
python measures.py --demand A B C        # several demand folders (e.g. one per seed): mean and SD across them
```

**Traffic counts:** `counts.py` reads the group's videos (`settings.COUNT_SITES`: where each camera
stood, on which road, which way is "away"). Every run counts the vehicles passing the same spot
(`counts.xml`) and, with background traffic, prints them against the videos with GEH.

```powershell
python counts.py            # each video's vehicles per hour, per direction and class
python counts.py --check    # background only: SUMO around each video's 30 minutes, then the comparison
```

To check the simulation itself with scripted test agents (manuscript Sec. 4.4.3.3; see
`validation/README.md`):

```powershell
python validation/validate.py      # the five checks, PASS or FAIL (about 1.5 min)
```

## Files

| File | What it does |
|---|---|
| `settings.py` | Every stand-in value and path; the map's own are marked `MAP` |
| `pipeline.py` | A simulated day from the LLM to SUMO and back: every step in order, checking each LLM answer |
| `llm_tasks.py` | The five LLM tasks: what each prompt is given (`demand/llm_inputs/`), how its answer is checked, and the agents' state from day to day |
| `llm_stub.py` | Stand-in for the LLM: answers each task's input file with rules of thumb |
| `make_agents.py` | The LLM team's base agents in the area, a sample, homes in their barangays → `demand/agents.json` |
| `make_choices.py` | Every way each trip of a schedule can be made (distance, time and its parts, cost) |
| `convert_demand.py` | Checks a schedule and modes, and turns them into SUMO persons and vehicles |
| `run.py` | Runs SUMO with the flag-down controller and writes `runs/<date_time>/`, with every trip's outcome and the event log |
| `get_barangays.py` | Santa Rosa's 18 official barangay boundaries → `data/santa_rosa_barangays.geojson` and its provenance |
| `make_map.py` | The study area (`data/area.geojson`), its OSM extract (`bayan.osm`) and network (`bayan.net.xml`) |
| `netconvert.netccfg`, `typemap.typ.xml` | How `bayan.net.xml` is built from `bayan.osm` (bicycles allowed on trunk roads, urban speed limits, no SLEX) |
| `build.py` | Runs `make_routes.py`, `make_vtypes.py` and `make_background.py` |
| `make_routes.py` | The jeepney lines of `JEEPNEY_LINES`, each way: one or more roads in a row → `generated/` |
| `make_vtypes.py` | Jeepney, car, motorcycle and bike types → `generated/` |
| `make_background.py` | Random background car and motorcycle trips over the day → `generated/` |
| `traffic_meter.py` | Optional meter of how motorcycles, cars and jeepneys share the road (`run.py --meter`) |
| `counts.py` | The group's video traffic counts (`data/traffic_counts/`), and the simulation's at the same spot, with GEH |
| `measures.py` | The manuscript's evaluation measures (Sec. 4.5) from the days run so far → `demand/measures.json` |
| `demand/` | The hand-over: `README.md`, the JSON schemas, the agents, the LLM's inputs and answers, options and outcomes per day |
| `validation/` | The scripted test agents (`cases/`, from `make_cases.py`), the five checks (`validate.py`) and the latest result (`report.md`) |
| `data/` | The barangay boundaries, the study area, the activity locations (`locations_9_barangays.csv`) and the base agents (`base_agents.json`, copied from PASADA-llm), each with provenance |

`generated/`, `runs/`, `demand/old/` and `bayan.net.xml` are not committed.
