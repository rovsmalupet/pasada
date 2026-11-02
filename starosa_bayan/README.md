# starosa_bayan — the multi-line prototype on 14 barangays of Santa Rosa

A copy of `../starosa_prototype/`'s multi-line setup (`run.py`), made on 2026-11-03 to run the
same simulation on a bigger, better-defined map. The setup itself is unchanged:

- jeepney lines along the map's main roads, both directions, with the TraCI flag-down
  controller;
- commuters from a readable demand file (`demand/day_1.json`; format in `demand/README.md`).
  The LLM will write it; until then `llm_stub.py` writes a stand-in;
- homes from the population grid (`../population_extraction/`) and destinations at the
  activity locations (`../sumo_track/data/placeholder/locations.csv`);
- walk, bike, car and jeepney trips, background cars and motorcycles, a whole day per run,
  and one output folder per run (`runs/<date_time>/`).

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
python build.py           # lines, vTypes, background; agents and demand if missing
```

`bayan.net.xml` (67 MB) is not committed. Rebuild it from the committed `bayan.osm` with
`netconvert -c netconvert.netccfg`. `make_map.py` also needs
`../sumo_track/data/raw/santa_rosa_buffered.osm`, sumo_track's local download.

## Running

Use the venv's Python (`..\sumo_track\.venv\Scripts\python.exe`), from inside this folder:

```powershell
python run.py                      # sumo-gui; press play
python run.py --nogui              # headless: event log and summary
python run.py --no-background      # without background cars and motorcycles (much faster)
python run.py --end 36000          # stop at 10:00 (simulated seconds; default a whole day)
python run.py --demand FILE        # another demand file (default demand/day_1.json)
python make_agents.py --force      # new agents (then also: python llm_stub.py --force)
python llm_stub.py --force         # a new stand-in demand file
python convert_demand.py [FILE]    # check a demand file without running it
```

## Files

| File | What it does |
|---|---|
| `settings.py` | Every stand-in value and path; the map's own are marked `MAP` |
| `get_barangays.py` | Santa Rosa's 18 official barangay boundaries → `data/santa_rosa_barangays.geojson` and its provenance |
| `make_map.py` | The study area (`data/area.geojson`), its OSM extract (`bayan.osm`) and network (`bayan.net.xml`) |
| `netconvert.netccfg`, `typemap.typ.xml` | How `bayan.net.xml` is built from `bayan.osm` (same as `starosa_prototype`, minus SLEX) |
| `build.py` | Runs `make_routes.py`, `make_vtypes.py` and `make_background.py`; makes the demand files if missing |
| `make_routes.py` | The jeepney lines of `JEEPNEY_LINES`, each way: one or more roads in a row → `generated/` |
| `make_vtypes.py` | Jeepney, car, motorcycle and bike types → `generated/` |
| `make_background.py` | Random background car and motorcycle trips over the day → `generated/` |
| `make_agents.py` | Agents (homes from the population grid, car and bike access) and the activity locations → `demand/agents.json` |
| `llm_stub.py` | Stand-in for the LLM → `demand/day_1.json` |
| `convert_demand.py` | Checks a demand file and turns it into SUMO persons and vehicles |
| `run.py` | Converts the demand file, runs SUMO with the flag-down controller, writes `runs/<date_time>/` |
| `traffic_meter.py` | Optional meter of how motorcycles, cars and jeepneys share the road (`run.py --meter`) |
| `demand/` | The demand file format (`README.md`, `day.schema.json`) and the demand files |
| `data/` | The barangay boundaries and the study area |

`generated/`, `runs/` and `bayan.net.xml` are not committed.
