# starosa_bayan — the multi-line prototype on 14 barangays of Santa Rosa

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

**The study area is 14 barangays: all of Santa Rosa north-east of SLEX except Malitlit**, which
SLEX cuts through. They are Tagapo, Pooc, Dila, Labas, Sinalhan, Macabling, Market Area,
Caingin, Malusak, Ibaba, Kanluran, Aplaya, Balibago and Dita: 22.8 km², about 313,000 people
on the population grid (`settings.AREA_BARANGAYS`).

- The boundaries are official: COD-AB from NAMRIA and PSA, via OCHA/HDX, CC BY-IGO
  (`get_barangays.py` → `data/santa_rosa_barangays.geojson`).
- **Homes and destinations are only inside the area.** The roads are every OSM way touching
  it, kept whole, so roads crossing its edge stick out a little, as in a Web Wizard box.
- SLEX and its ramps are left out.
- **Speed limits** follow RA 4136 and JMC 2018-001 for cars: 40 km/h on trunk and primary roads
  (national roads, "through streets"), 30 km/h on the others ("city and municipal streets"),
  unless OSM tags a road (`typemap.typ.xml`).
- **Jeepney lines** (`settings.JEEPNEY_LINES`), each in both directions:

  | Line | Route |
  |---|---|
  | `N1` | National Highway 1 |
  | `RIZ-FRS` | Rizal Boulevard, then on to Felix Reyes Street |
  | `RIZ-N1` | Rizal Boulevard, then back onto National Highway 1 |
  | `JPRS` | J. P. Rizal Street |

  No line runs on Arcillas Boulevard or the Biñan–Santa Rosa Access Road (removed by choice),
  nor on F. Gomez, Zavalla or Arambulo Street (shorter than a 500 m ride).

To rebuild the map (only needed if the area or the OSM data changes):

```powershell
python get_barangays.py   # the 18 barangay boundaries (about 26 MB of download)
python make_map.py        # data/area.geojson, bayan.osm (from ../sumo_track/data/raw/), bayan.net.xml
python build.py           # jeepney lines, vehicle types, background traffic
```

`bayan.net.xml` (67 MB) is not committed. Rebuild it from the committed `bayan.osm` with
`netconvert -c netconvert.netccfg`. `make_map.py` also needs
`../sumo_track/data/raw/santa_rosa_buffered.osm`, sumo_track's local download, which is not on this branch.

## Setup

This branch holds only this folder and what it reads from outside it:

| Path | Why |
|---|---|
| `../sumo_track/data/placeholder/locations.csv` | The activity locations, with its provenance file; `../sumo_track/config/activity_categories.yaml` has the rules that made it |
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
```

The simulation alone, on the day's files:

```powershell
python run.py                      # sumo-gui on day 1's schedule and modes; press play
python run.py --nogui              # headless: event log and summary
python run.py --no-background      # without background cars and motorcycles (much faster)
python run.py --end 36000          # stop at 10:00 (simulated seconds; default a whole day)
python run.py --demand FILE --modes FILE   # another day's schedule and modes
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
| `run.py` | Runs SUMO with the flag-down controller and writes `runs/<date_time>/`, with every trip's outcome |
| `get_barangays.py` | Santa Rosa's 18 official barangay boundaries → `data/santa_rosa_barangays.geojson` and its provenance |
| `make_map.py` | The study area (`data/area.geojson`), its OSM extract (`bayan.osm`) and network (`bayan.net.xml`) |
| `netconvert.netccfg`, `typemap.typ.xml` | How `bayan.net.xml` is built from `bayan.osm` (bicycles allowed on trunk roads, urban speed limits, no SLEX) |
| `build.py` | Runs `make_routes.py`, `make_vtypes.py` and `make_background.py` |
| `make_routes.py` | The jeepney lines of `JEEPNEY_LINES`, each way: one or more roads in a row → `generated/` |
| `make_vtypes.py` | Jeepney, car, motorcycle and bike types → `generated/` |
| `make_background.py` | Random background car and motorcycle trips over the day → `generated/` |
| `traffic_meter.py` | Optional meter of how motorcycles, cars and jeepneys share the road (`run.py --meter`) |
| `demand/` | The hand-over: `README.md`, the JSON schemas, the agents, the LLM's inputs and answers, options and outcomes per day |
| `data/` | The barangay boundaries, the study area, and the base agents (`base_agents.json`, copied from PASADA-llm, with provenance) |

`generated/`, `runs/`, `demand/old/` and `bayan.net.xml` are not committed.
