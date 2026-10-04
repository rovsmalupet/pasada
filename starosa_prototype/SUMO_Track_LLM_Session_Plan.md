# SUMO Track — LLM Session Plan

**Thesis:** *Designing an LLM-Enhanced Agent-Based Transportation Simulation Framework for Adaptive Commuter Decision-Making*
**Group:** Dompor, Fuentes, Orcullo · **Adviser:** Briane Paul V. Samson, PhD · De La Salle University, BS Computer Science
**My assignment:** the SUMO track (Manuscript Section 4.4) · **Status:** proposal approved, implementation starting this term

---

## How to use this document

1. Paste **Part A (Session Starter Prompt)** at the start of every new LLM session. Attach this whole file, or paste it below the prompt.
2. Work on **one phase per session** (Part E). Tell the LLM which phase you're on.
3. At the end of each session, ask the LLM to update `PROGRESS.md` in the repo (template in Part H) and paste that file into the next session.
4. Whenever a real dataset arrives from the data collection team, follow **Part F (Data Swap Guide)**.

---

## Part A — Session Starter Prompt (copy-paste)

```
You are helping me implement the SUMO part of our undergraduate thesis. The full
context is in the attached plan (SUMO_Track_LLM_Session_Plan.md). Read Parts B–D
before doing anything.

Today I am working on: PHASE <number> — <name>.
Current progress: <paste PROGRESS.md or say "starting fresh">.
My SUMO version: <output of `sumo --version`>. OS: <Windows/macOS/Linux>.

Rules for this session:
1. Stay inside the SUMO track (Section 4.4). Do not build LLM prompts, persona
   generation, or evaluation metrics. For anything LLM-side, use the stub
   described in Phase 10.
2. Never hardcode an assumed value in code. Every assumption goes in
   config/params.yaml with a value, unit, source, and status.
3. Never invent facts about Santa Rosa (routes, fares, headways, counts). If a
   value is unknown, add it as status: placeholder and tell me.
4. Every input file must be loaded through the data manifest, so placeholder data
   can be swapped for real data without changing code.
5. All times are in seconds. Step length is 1 s. A day runs from t=0 to t=86400.
6. If you are not sure a SUMO/TraCI function or option exists in my version, say
   so and give me a quick way to check. Do not guess silently.
7. For each task, give me: the code, how to run it, and how to tell if it worked
   (the acceptance criteria from the plan).
```

---

## Part B — Thesis Context (what the LLM must know)

### B.1 What the study is
An agent-based transportation simulation of **Santa Rosa City, Laguna**, built in **SUMO**, where commuter decisions are made by a **locally hosted open-weight LLM**. The focus is **jeepney-based mobility**. Commuters have habits that change over **multiple simulated days** based on their travel experiences.

**Research objectives**
1. Design an LLM prompt that includes factors influencing commuter travel behavior and mode choice.
2. Design an agent-based transportation simulation framework that uses LLMs for commuter decisions. ← **my part is the simulation half of this**
3. Evaluate the framework by checking whether its outputs are consistent with available demographic and transportation data.

### B.2 Scope that affects the SUMO track
- **Modes:** jeepney, walking, bicycle, private car. **Buses and tricycles are excluded** (a stated scope limitation).
- **Jeepneys** are the only public transport. They run on **fixed routes** and **never reroute**. There are **no fixed stops**: commuters flag them anywhere along the route.
- **Synthetic commuters only.** No human participants. There are three commuter types: employed, student, and other non-working.
- **Data sources:** PSA census, the Meta 2020 high-resolution population density raster, OpenStreetMap, field observation of jeepneys, and traffic counts from agencies or the field.
- **Evaluation site for traffic counts:** the Paseo de Santa Rosa intersection.

### B.3 Full framework (so the SUMO track fits correctly)

```
INITIALIZATION (Day 1 only)
  Base Commuter Agent (logic)  →  Persona (LLM)  →  Habitual Travel Patterns (LLM)
  Empty Travel Experience

DAILY LOOP (repeat for N days)
  1. Day Schedule Generation ............ LLM         (not mine)
  2. Trip extraction .................... logic       ← MINE
  3. Candidate mode preparation ......... logic       ← MINE
  4. Mode Selection ..................... LLM         (not mine; I supply its input)
  5. SUMO input conversion .............. logic       ← MINE
  6. Execution (SUMO + TraCI) ........... simulation  ← MINE
  7. Outcome recording + disruption ..... logic       ← MINE
  8. End-of-Day Reflection + habit update  LLM        (not mine; I supply its input)
  9. Day advance
```

**Core design principle (Sec. 4.3.1):** the LLM does behavioral judgment only. **Programmed logic and SUMO handle all routing, travel times, costs, feasibility, and movement.** The LLM must never invent or change a transportation value.

**Time handling:** SUMO step length is 1 s (`--step-length 1`). Each simulated day is a **separate 86,400-second SUMO run**. The LLM is **not** called every tick. It is called before the run (schedule, mode selection) and after the run (reflection). Clock time converts as `t = 3600·hh + 60·mm + ss`.

### B.4 Validation strategy (important)
- **My track is validated on its own with scripted test agents** (hardcoded schedules and modes) **before** any LLM is connected (Sec. 4.4.3.3).
- The LLM tasks are validated separately by the rest of the team (Sec. 4.3.9).
- The two halves are only integrated after **both** pass.

---

## Part C — SUMO Track Scope (Manuscript Section 4.4)

### C.1 In scope (what I build)
| # | Component | Manuscript |
|---|---|---|
| 1 | Road network generation (OSM Web Wizard, then custom `netconvert`) | 4.4.1 |
| 2 | Network verification script (`sumolib`) and manual fixes (`netedit`) | 4.4.1 |
| 3 | POI / activity location extraction (`polyconvert`) and filtering | 4.4.1 |
| 4 | Snapping locations to edges; residential edge pool for home origins | 4.4.1 |
| 5 | Jeepney route matching to network edges | 4.4.1 |
| 6 | Routing and mode-specific travel-time estimation module | 4.4.1 |
| 7 | Demand file (`.rou.xml`) structure and builder | 4.4.1 |
| 8 | Agent definitions: commuter (person), jeepney, car, bike, background traffic | 4.4.2 |
| 9 | TraCI jeepney controller (conditional stops, dwell, capacity skip, event log) | 4.4.2 |
| 10 | Commuter state tracking (Table 4.10) | 4.4.2 |
| 11 | Five-step execution pipeline | 4.4.3 |
| 12 | Output recording and run configuration logging | 4.4.3 |
| 13 | Scripted validation before LLM integration | 4.4.3 |

### C.2 Out of scope (belongs to groupmates)
- Collecting data: PSA, raster processing, field observation, traffic counts (Sec. 4.2). **I consume their outputs.**
- LLM prompts, candidate LLM evaluation, and behavioral rubric (Sec. 4.3).
- Integrated evaluation metrics (Sec. 4.5). **I only make sure the needed data is recorded.**

### C.3 Things I depend on (and my placeholder until they arrive)
| Dependency | From | Placeholder I use meanwhile |
|---|---|---|
| Study area boundary | Data team (4.2.1) | Santa Rosa City administrative polygon (PSA or OSM relation), optionally narrowed to selected barangays later |
| Cleaned OSM extract and mode-accessibility rules | Data team (4.2.4) | Raw OSM download, SUMO default type maps |
| Activity location inventory | Data team (4.2.3) | Raw `polyconvert` output, lightly filtered |
| Population-weighted origin zones | Data team (4.2.2) | Equal weights per residential edge |
| Jeepney routes, headways, capacity, dwell, fare | Data team (4.2.5) | 1–2 hand-traced routes; values from `params.yaml`, all marked placeholder |
| Traffic counts | Data team (4.2.6) | `randomTrips.py` background demand with a peak-shaped profile |
| Mode Selection output | LLM team (4.3.6) | Scripted JSON in the agreed schema |
| Reflection input format | LLM team (4.3.7) | Draft trip-outcome schema (Part D.4) to confirm with them |

---

## Part D — Design Rules and Data Contracts

### D.1 Modifiability rules (so real data can drop in later)
1. **Config over code.** Every numeric assumption lives in `config/params.yaml`.
2. **Every parameter carries provenance:**
   ```yaml
   jeepney_capacity:
     value: 20
     unit: passengers
     status: placeholder      # placeholder | assumed | literature | official | observed
     source: "TBD – field observation (Sec. 4.2.5)"
     notes: ""
   ```
3. **A data manifest chooses the source for each dataset.** Code never uses file paths directly.
   ```yaml
   # config/data_manifest.yaml
   study_boundary:     {active: placeholder, placeholder: data/placeholder/boundary.geojson, real: data/real/boundary.geojson}   # exact polygon; buffered version derived at build time
   osm_extract:        {active: placeholder, placeholder: data/raw/santarosa_bbox.osm,     real: data/real/santarosa_clean.osm}
   activity_locations: {active: placeholder, placeholder: data/placeholder/locations.csv,   real: data/real/locations.csv}
   origin_zones:       {active: placeholder, placeholder: data/placeholder/origin_zones.csv, real: data/real/origin_zones.csv}
   jeepney_routes:     {active: placeholder, placeholder: data/placeholder/jeepney_routes.json, real: data/real/jeepney_routes.json}
   traffic_counts:     {active: none,        placeholder: null, real: data/real/traffic_counts.csv}
   ```
4. **Every loader validates against a JSON schema** (in `schemas/`). A new real file that doesn't match the schema should fail loudly, not quietly break something downstream.
5. **Derived files are regenerated by scripts, never hand-edited.** The exception is `netedit` fixes, which are saved as a patch file (`config/net_patches.xml`, or netedit-exported edge/connection files) and re-applied by the build script.
6. **Caches are keyed by a hash** of the network plus relevant parameters. The manuscript requires recalculating estimates whenever the network or parameters change.
7. **Placeholder data is labeled.** Every record has `source_type`. Every run logs which datasets were placeholder. A run using any placeholder data is tagged `dev` and must not be used for thesis results.
8. **One command rebuilds everything:** `python -m sumo_track.build --all`.

### D.2 Suggested repository layout
```
sumo_track/
├── config/
│   ├── params.yaml              # all assumptions with provenance
│   ├── data_manifest.yaml       # placeholder vs real switch
│   ├── netconvert.netccfg       # saved netconvert options (reproducible network)
│   ├── polyconvert_typemap.xml  # POI categories to keep
│   ├── net_patches/             # manual netedit corrections
│   └── scenarios/
│       ├── baseline.yaml
│       ├── congestion.yaml
│       └── reduced_jeepney.yaml
├── data/
│   ├── raw/                     # untouched downloads
│   ├── placeholder/             # my stand-in data
│   ├── real/                    # filled when data collection finishes
│   └── processed/               # generated; safe to delete and rebuild
├── schemas/                     # JSON schemas (Part D.4)
├── src/sumo_track/
│   ├── config.py                # loads params + manifest
│   ├── network/                 # build.py, verify.py
│   ├── locations/               # extract_pois.py, snap.py, residential_pool.py
│   ├── jeepney/                 # route_match.py, vtypes.py, flows.py, controller.py
│   ├── estimation/              # feasibility.py, walk.py, bike.py, car.py, jeepney.py
│   ├── demand/                  # trip_extraction.py, converter.py, background.py
│   ├── execution/               # runner.py, state_tracker.py, recorder.py, disruption.py
│   ├── pipeline.py              # the five-step pipeline
│   └── llm_stub.py              # fake Mode Selection for testing (Phase 10)
├── tests/
│   ├── scripted_agents/         # hand-authored schedules and modes
│   └── test_*.py
├── runs/<run_id>/               # outputs + run_config.json
├── PROGRESS.md
└── README.md
```

### D.3 Tools
- **SUMO** (`sumo`, `sumo-gui`, `netconvert`, `netedit`, `polyconvert`, `duarouter`), plus tools: `osmWebWizard.py`, `randomTrips.py`, `tracemapper.py` (for GPS/route-trace matching).
- **OSM clipping:** `osmium-tool` (preferred) or `osmconvert`, with the Geofabrik Philippines extract.
- **Python:** `traci` (or `libsumo` for faster headless runs), `sumolib`, `pyyaml`, `jsonschema`, `pandas`, `lxml`. Optional: `geopandas` and `shapely` for boundary clipping.
- **QGIS** for checking geometry against satellite imagery (named in the manuscript).

### D.4 Data contracts (DRAFTS — confirm field names with the LLM team)

**Placed activity location** (Sec. 4.4.1 output)
```json
{
  "location_id": "LOC_0001",
  "category": "school",
  "lat": 14.3, "lon": 121.1,
  "source_type": "osm | supplemented | placeholder",
  "zone_id": "BRGY_XXXX",
  "edge_id": "123456#0",
  "lane_pos_m": 42.5,
  "snap_distance_m": 18.2,
  "flag": null
}
```

**Trip record** (created in pipeline step 1, extended in later steps)
```json
{
  "trip_id": "A0001_D03_T02",
  "agent_id": "A0001",
  "day": 3,
  "origin_location_id": "HOME_A0001",
  "destination_location_id": "LOC_0001",
  "departure_time_s": 25200,
  "trip_purpose": "education",
  "trip_type": "home_to_school",
  "alternatives": [],
  "selection": null,
  "outcome": null
}
```

**Candidate alternative** (field names follow Sec. 4.3.6)
```json
{
  "trip_id": "A0001_D03_T02",
  "mode": "jeepney",
  "route_id": "JEEP_R01_NB",
  "feasible": true,
  "distance_m": 3400,
  "cost_php": 0.0,
  "estimated_total_time_s": 1380,
  "access_time_s": 240,
  "waiting_time_s": 300,
  "in_vehicle_time_s": 660,
  "dwell_time_s": 60,
  "egress_time_s": 120,
  "boarding": {"edge_id": "…", "pos_m": 12.0},
  "alighting": {"edge_id": "…", "pos_m": 88.0}
}
```
`cost_php` for jeepney is computed from the fare parameters. `boarding` and `alighting` are only present for jeepney. Walking, bike, and car records omit fields that don't apply or set them to 0.

**Mode Selection output** (produced by the LLM team, consumed by my converter)
```json
{
  "agent_id": "A0001",
  "day": 3,
  "selections": [
    {"trip_id": "A0001_D03_T02", "mode": "jeepney", "route_id": "JEEP_R01_NB", "reasoning": "…"}
  ]
}
```

**Trip outcome** (my output, feeds End-of-Day Reflection and evaluation)
```json
{
  "trip_id": "A0001_D03_T02",
  "realized_mode": "jeepney",
  "route_id": "JEEP_R01_NB",
  "actual_departure_s": 25200,
  "arrival_time_s": 26820,
  "actual_total_time_s": 1620,
  "actual_waiting_time_s": 480,
  "distance_m": 3400,
  "cost_php": 0.0,
  "estimated_total_time_s": 1380,
  "delay_ratio": 1.17,
  "disrupted": false,
  "disruption_reason": null,
  "full_pass_bys_experienced": 1,
  "completed": true
}
```

**Jeepney event log** (one JSON line per event)
```json
{"t": 25540, "vehicle_id": "JEEP_R01_NB.12", "event": "stop|boarding|alighting|full_pass_by", "edge_id": "…", "pos_m": 12.0, "person_ids": ["A0001"]}
```
Also record periodic occupancy samples: `{"t": …, "vehicle_id": …, "occupancy": 14}`.

**Commuter state transition log**
```json
{"t": 25200, "agent_id": "A0001", "trip_id": "A0001_D03_T02", "from": "at_origin", "to": "walking_to_boarding"}
```

### D.5 Commuter states (Manuscript Table 4.10)
| State | Applies to | Ends when |
|---|---|---|
| `at_origin` | All modes | Scheduled departure time is reached |
| `walking_to_boarding` | Jeepney | Agent reaches computed boarding position |
| `waiting_to_flag` | Jeepney | A jeepney on the selected route stops at the agent's position with available capacity |
| `riding` | Jeepney | Jeepney stops at the computed alighting position |
| `alighting` | Jeepney | Agent has left the vehicle during the dwell period |
| `walking_to_destination` | Jeepney, walking | Agent reaches destination |
| `traveling` | Car, bike | Vehicle reaches destination edge |
| `arrived` | All modes | Terminal; trip record closed with actual travel time |

### D.6 Parameter checklist for `params.yaml`
**Every value below starts as `status: placeholder`.** None of these are real Santa Rosa figures. Fill them in from data collection, literature, or preliminary runs.

| Parameter | Unit | Where it gets finalized |
|---|---|---|
| `step_length` | s | Fixed at 1 (manuscript) |
| `pedestrian_speed` | m/s | Literature / SUMO default |
| `walking_distance_limit` | m | Literature / behavior factor matrix |
| `walking_tolerance_to_route` | m | Literature / behavior factor matrix |
| `bike_speed`, `bike_distance_limit` | m/s, m | Literature |
| `car_access_time`, `car_parking_time` | s | Assumption |
| `car_operating_cost_per_km` | PHP/km | Assumption / literature |
| `jeepney_capacity` | passengers | Field observation |
| `jeepney_max_speed`, `jeepney_length`, accel/decel | m/s, m, m/s² | Assumption |
| `boarding_time_per_pax`, `alighting_time_per_pax` | s | Field observation / literature |
| `headway[route][period]` | s | Field observation |
| `fare_base`, `fare_base_km`, `fare_per_km` | PHP, km, PHP/km | Official LTFRB fare matrix (verify the current one) |
| `time_periods` (AM peak, midday, PM peak, off-peak) | clock ranges | Group decision |
| `boundary_buffer_m` | m | Implementation; must keep cross-border jeepney route ends intact |
| `snap_max_distance` | m | Implementation |
| `route_match_max_gap` | m | Implementation |
| `controller_polling_interval` | s | Tuned in implementation |
| `controller_lookahead_distance` | m | Tuned in implementation |
| `disruption_threshold` | ratio or s | Preliminary runs |
| `background_demand_scale` | factor | Calibration / preliminary runs |
| `random_seed` | int | Per run |

---

## Part E — Implementation Phases

Each phase lists: **Goal · Manuscript refs · Inputs · Tasks · Outputs · Done when**.

### Phase 0 — Environment setup
- **Goal:** a working SUMO and Python environment with the repo skeleton.
- **Tasks**
  - Install SUMO. Set `SUMO_HOME`. Confirm `sumo --version`, and that `import traci, sumolib` works.
  - Create the repo layout (Part D.2), `params.yaml` (all placeholders), `data_manifest.yaml`, and `PROGRESS.md`.
  - Write `config.py`: loads params and manifest, resolves active dataset paths, and warns whenever a placeholder is active.
  - Write a "hello SUMO" script: open a tiny network in TraCI, step 100 times, close.
- **Done when:** the hello script runs headless and in `sumo-gui`, and the config loader prints which datasets are placeholder.

### Phase 1 — Road network (Santa Rosa only, not a bounding box)
- **Refs:** 4.2.1, 4.2.4, 4.4.1 (Road Network Generation; Network Verification)
- **Inputs:** Santa Rosa administrative boundary; regional OSM extract.
- **Why this matters:** OSM Web Wizard can only give a rectangle, which pulls in Cabuyao, Biñan, and Santa Rosa's neighbors. The study area is defined by a real boundary (Sec. 4.2.1), so the network has to be clipped to a polygon, not a box.
- **Tasks**
  1. **Get the boundary polygon.**
     - Preferred for the thesis: PSA administrative boundary files for Santa Rosa City (and its barangays), since the manuscript already cites PSA for barangay boundaries and codes.
     - Alternative sources: the OSM administrative relation for Santa Rosa City (`admin_level=6`, exported from overpass turbo or `polygons.openstreetmap.fr`), GADM, or HDX.
     - Store it as `data/*/boundary.geojson`, plus a `.poly` version for the clipping tools. Register it in the manifest as `study_boundary`.
  2. **Build the buffered clip polygon.** Apply `boundary_buffer_m` (a parameter, placeholder around 500–1000 m) to the boundary. **Clip the network with the buffered polygon, not the exact boundary.** Jeepney routes and roads run across the city line toward Cabuyao, Biñan, and the SLEX exits, and a strict cut breaks route continuity and creates dead ends at the edges. The exact boundary is applied later, when selecting origins, destinations, and agents (Phase 2), which is what actually defines the study area.
  3. **Download and clip the OSM data.** Get the Philippines or CALABARZON extract from Geofabrik, then:
     ```bash
     # osmium (preferred)
     osmium extract -p santa_rosa_buffered.poly philippines-latest.osm.pbf \
       -s complete_ways -o santa_rosa.osm.pbf
     osmium cat santa_rosa.osm.pbf -o santa_rosa.osm

     # or osmconvert
     osmconvert philippines-latest.osm.pbf -B=santa_rosa_buffered.poly \
       --complete-ways -o=santa_rosa.osm
     ```
     `complete_ways` is required. Without it, roads crossing the polygon are cut mid-way and the geometry breaks.
  4. **Optional first pass with OSM Web Wizard.** Run `osmWebWizard.py` over the area once to get a quick working network and confirm the study area and source data agree, as the manuscript describes. **Treat it as a throwaway reference only.** The network used in the study comes from the clipped extract in step 3.
  5. **Fallback if the clip is done inside netconvert instead.** `--keep-edges.in-geo-boundary "lon,lat,lon,lat,..."` drops everything outside a shape (check the exact syntax with `netconvert --help | grep boundary`). This works, but the polygon has to be simplified to a manageable point count, so clipping the OSM file in step 3 gives cleaner results for a complex city outline. Whichever you use, record the choice in the run config.
  6. **Main pass:** write `config/netconvert.netccfg` for the clipped `.osm`. Options to consider (check each exists in your version with `netconvert --help`):
     - pedestrians: `--sidewalks.guess`, `--crossings.guess`
     - bikes: `--bikelanes.guess`
     - geometry: `--junctions.join`, `--geometry.remove`, `--roundabouts.guess`, `--ramps.guess`
     - signals: `--tls.guess-signals`, `--tls.join`
     - cleanup: `--remove-edges.isolated`, `--keep-edges.components 1`
     - metadata: `--output.street-names`, `--osm.all-attributes` (keeps OSM tags for later checks)
     - type map: `osmNetconvert.typ.xml` plus a custom override file for mode-accessibility rules
  7. `network/build.py`: clips the OSM extract with the buffered polygon, runs netconvert from the saved config, then applies `net_patches/`. One command, fully reproducible.
  8. `network/verify.py` (sumolib) should check:
     - the network's bounding geometry stays inside the buffered polygon, and every barangay in the study area has road coverage
     - edges orphaned by the clip are removed (`--keep-edges.components 1`) and the count is reported, since a large number means the buffer is too tight
     - isolated or unreachable edges (connectivity test from a central edge, for each vClass: passenger, bicycle, pedestrian, and the jeepney vClass)
     - junctions with missing lane-to-lane connections
     - suspicious one-way roads (compare against OSM `oneway` tags)
     - major roads with unexpected lane counts
     - edges that should allow pedestrians or bicycles but don't
     - writes a report (CSV + summary)
  9. Fix failures in `netedit`, using satellite imagery or QGIS when needed. Save the fixes as patches. Repeat until the report is clean.
- **Outputs:** `data/processed/santa_rosa.osm` (clipped), `santa_rosa_buffered.poly`, `data/processed/santarosa.net.xml`, `netconvert.netccfg`, the verification report, and `net_patches/`.
- **Done when:** the verification report has zero failures; the network covers Santa Rosa and only a thin buffer beyond it, with no whole neighboring city included; and everything rebuilds from one command.

### Phase 2 — Activity locations and home origins
- **Refs:** 4.2.3, 4.4.1 (POI Extraction; Snapping)
- **Inputs:** OSM extract, network, category list, placeholder origin zones.
- **Tasks**
  1. `polyconvert` with a type map that keeps the relevant amenity, shop, building, and landuse tags (schools, colleges, workplaces, markets, malls, terminals, residential landuse).
  2. `locations/extract_pois.py`: clip to the **exact** study boundary (not the buffered one, since this is where the study area is actually enforced), keep only needed categories, remove duplicates, output CSV/JSON in the location schema with `source_type`. It should also accept supplemented locations and append them.
  3. `locations/snap.py`: use `net.convertLonLat2XY`, then `net.getNeighboringEdges(x, y, r)`. Keep only edges that allow pedestrians. Choose the closest one and compute the lane position. Anything beyond `snap_max_distance` gets flagged, not silently matched. **Terminals** go through the same process and stay fixed points.
  4. `locations/residential_pool.py`: filter edges by residential road types (`highway.residential`, `highway.living_street`) or edges inside residential landuse polygons. Group by zone, attach population weights (equal for now), output CSV.
  5. Add a sampler function: pick a home edge and position for an agent given its zone. This is used by agent initialization.
- **Outputs:** `placed_locations.json`, `residential_edge_pool.csv`, and a flag report.
- **Done when:** all non-flagged locations sit on pedestrian-accessible edges inside the boundary, and every zone has at least one residential edge (or is flagged for boundary review).

### Phase 3 — Jeepney routes and vehicle type
- **Refs:** 4.2.5, 4.4.1 (route matching), 4.4.2 (Jeepney Agents)
- **Inputs:** placeholder route traces (1–2 routes, each direction as a separate route).
- **Tasks**
  1. **Placeholder route format** (same as the future real format):
     ```json
     {"route_id": "JEEP_R01_NB", "name": "…", "direction": "NB",
      "points": [{"seq": 1, "lat": …, "lon": …, "source": "placeholder"}],
      "terminals": ["LOC_T01", "LOC_T02"],
      "boarding_refs": []}
     ```
  2. `jeepney/route_match.py`: convert points into a continuous edge sequence. Try `sumolib.route.mapTrace` or `tools/route/tracemapper.py`, then fill gaps with shortest paths. Check for gaps, wrong direction, and disconnected segments, and flag any problems.
  3. `jeepney/vtypes.py`: define the jeepney `vType` using capacity, length, speed, accel/decel, and per-passenger boarding time, all from params. Decide on the vClass (for example `bus`). Then **check that every edge on the matched routes allows that vClass.** Also define car and bike vTypes.
  4. `jeepney/flows.py`: create one `<flow>` per route, with separate insertion periods for peak and off-peak based on `headway[route][period]`.
- **Outputs:** `jeepney_routes.matched.json`, route XML elements, vType definitions, and flow definitions.
- **Done when:** in `sumo-gui`, jeepneys (with no passengers yet) run each route end to end with no teleports and the expected spacing between vehicles.

### Phase 4 — Estimation module (candidate alternatives)
- **Refs:** 4.4.1 (Routing and Mode-Specific Travel-Time Estimation), 4.3.6
- **Inputs:** network, placed locations, matched jeepney routes, params, and a trip record plus the agent's access flags (car, bike).
- **Tasks**
  1. `estimation/feasibility.py`: filter modes by agent access, distance limits, and whether a valid route exists. Excluded modes are never shown to the LLM.
  2. **Walking:** shortest pedestrian path. Time = distance / pedestrian speed. No wait, no dwell, zero cost. Only feasible within `walking_distance_limit`.
  3. **Bike:** route over bicycle-permitted edges. Time is based on bike speed. Zero wait and cost unless a scenario says otherwise. Only offered if the agent has a bike and the trip is within the bike distance limit.
  4. **Car:** fastest route over passenger edges. Total time = access time + travel + parking. Cost = distance × operating cost. Only offered if the agent has car access.
  5. **Jeepney:**
     - Find routes that pass within `walking_tolerance_to_route` of **both** origin and destination, with the boarding point **before** the alighting point in the route's direction.
     - Use a **nearest-point function** to find the closest usable boarding and alighting positions (edge + pos) on the route.
     - access = walk time to boarding point · wait = headway / 2 for that period · in-vehicle = travel time along the route segment · dwell allowance = from params (same assumptions the controller uses) · egress = walk time from alighting point · cost = fare function of riding distance
     - Create **one alternative per serving route**. If no route serves the trip, remove jeepney.
  6. Estimates use an **unloaded (free-flow) network**, for a consistent baseline.
  7. **Cache** by (origin, destination, mode, route_id, departure period), keyed by the network and params hash.
  8. Use `sumolib` routing offline (for example `net.getShortestPath(from, to, vClass=…)`; check the signature in your version). Alternatively, use a TraCI `findRoute` / `findIntermodalRoute` call on a loaded network.
- **Outputs:** a list of alternative JSON records per trip (schema D.4).
- **Done when:** unit tests pass for:
  - a short trip, where walking is feasible
  - a long trip with no car access, where car is absent
  - a trip with no nearby jeepney route, where jeepney is absent
  - an origin and destination in the wrong order along a route, where that route is rejected
  - a trip served by two routes, which gives two jeepney alternatives

  Also: every total time equals the sum of its components.

### Phase 5 — Trip extraction and demand file builder
- **Refs:** 4.4.1 (Demand File Structure), 4.4.3 (pipeline steps 1 and 3)
- **Tasks**
  1. `demand/trip_extraction.py`: **fixed rule:** a trip is created whenever two consecutive schedule entries are at different locations. It departs at the earlier entry's time. Each trip gets `trip_type`, which links it to the same travel pattern on other days.
  2. `demand/converter.py`: turn a mode selection into `.rou.xml` elements:
     - **walk** → `<person>` with one `<walk>` stage
     - **bike / car** → `<vehicle>` of the matching vType, with the agent and trip IDs stored as `<param>` elements
     - **jeepney** → `<person>` with `<walk>` to the boarding position, then `<ride>` (lines = the selected route's line), then `<walk>` from the alighting position to the destination. Register the boarding and alighting positions in the controller registry (Phase 7).
  3. Assemble the day's file: vTypes + jeepney routes and flows + background flows + all converted trips, **sorted by departure time** (SUMO requires this).
  4. Write **3–5 hand-authored scripted demand files** for testing, using the same structure (see Phase 9).
- **Outputs:** `runs/<run_id>/day_<n>.rou.xml`.
- **Done when:** a generated file loads in SUMO without errors or warnings about unsorted departures, and each mode's person or vehicle appears and moves correctly.

> **Prototype early (Phase 5–7 risk):** SUMO's built-in person boarding normally expects bus stops or specific stop conditions. The manuscript requires **boarding at arbitrary roadside positions**, controlled by our own logic. Build a tiny test first: 1 jeepney and 1 person waiting at an arbitrary position on an edge. Use `traci.vehicle.setStop(vehID, edgeID, pos=…, duration=…)` and see whether SUMO boards the person natively when the stop range covers their position and the `lines` attribute matches. If native boarding doesn't work reliably, fall back to custom transfer logic, such as replacing the person's stages through `traci.person` stage functions. **Document which approach works in your SUMO version before building more on top of it.**

### Phase 6 — Background traffic
- **Refs:** 4.2.6, 4.4.2 (Background Traffic Agents), 4.5.3
- **Tasks**
  - **If counts exist:** create flows on the counted corridors with hourly rates matching the observed profile.
  - **If not (current placeholder):** use `randomTrips.py`, pass the output through `duarouter`, and shape departure times into morning and evening peaks. Label it as a scenario assumption.
  - Add a `background_demand_scale` factor and a per-segment multiplier for the **congestion scenario**.
  - Add edge-based output (`<edgeData>`) covering the count locations, especially the Paseo de Santa Rosa approaches.
- **Done when:** peak-hour congestion is visible in `sumo-gui`, and scaling the factor clearly changes travel times on selected edges.

### Phase 7 — TraCI jeepney controller and commuter state tracking
- **Refs:** 4.4.2 (Jeepney Agents; Commuter Agents; Table 4.10)
- **Controller cycle (runs every `controller_polling_interval`):**
  1. Maintain a **registry** of waiting commuters (with their flag position and route) and onboard passengers (with their alighting position ahead).
  2. For each jeepney in service, look ahead `controller_lookahead_distance` along its route for waiting commuters on the same route or upcoming alighting positions.
  3. No match: do nothing, the jeepney keeps going.
  4. Match: **stop at that exact position** (arbitrary-position stop). If the jeepney is **full and only boarders are waiting**, **skip the stop** and log a `full_pass_by`. The commuter keeps waiting for the next one.
  5. While stopped, transfer the matched persons. Dwell = n_board × t_board + n_alight × t_alight.
  6. Log every stop, boarding, alighting, and full pass-by (schema D.4), and sample occupancy periodically.
- **Rules:** jeepneys **never reroute**. Only stop when someone flags or someone needs to get off.
- **Commuter state tracker (`execution/state_tracker.py`):** each step, or on relevant events, read each person's stage and position with `traci.person.*` (current stage, road, position, vehicle). Map these to the Table 4.10 states and log timestamped transitions. For car and bike trips, track the vehicle (waiting → traveling → arrived).
- **Parameter tuning:** try a few polling interval and lookahead values. Pick values where jeepneys can physically brake in time and runtime stays reasonable. Record the values in the run config.
- **Done when:** in a single-jeepney test:
  - the jeepney passes positions with no waiting commuters without stopping
  - it stops exactly at a flagging commuter
  - the dwell time grows with the number of passengers
  - a full jeepney passes a waiting commuter and a later one picks them up
  - every event appears in the log

### Phase 8 — Execution pipeline and output recording
- **Refs:** 4.4.3 (Execution Pipeline; Data Recording)
- **`pipeline.py` implements the five steps:**
  1. Trip extraction
  2. Candidate mode preparation (Phase 4)
  3. SUMO input conversion (Phase 5)
  4. Execution: one 86,400-s run through TraCI, with the controller active and scenario conditions applied
  5. Outcome recording and **disruption determination**. Rebuild door-to-door time from state transitions and cross-check it with tripinfo. Get waiting time from the jeepney log. Mark a trip `disrupted` if actual time exceeds the estimate by `disruption_threshold`, or if a scenario condition affected it directly.
- **SUMO outputs to enable:** `--tripinfo-output` (with `--tripinfo-output.write-unfinished`), `<edgeData>` on count segments, and optionally `--stop-output` and `--vehroute-output`. Check the option names with `sumo --help`.
- **Recorder:** parse the XML outputs and store everything **keyed by run / agent / day / trip**. Start with JSON lines or SQLite.
- **`run_config.json`** for every run must include: SUMO version, network hash, all params (with status), manifest state (placeholder vs real), walking speed, access times, cost and fare parameters, disruption threshold, controller interval and lookahead, routing method, scenario, random seed, and prompt version / LLM config (filled later by the LLM team).
- **Multi-day support:** a day runner that loops days, carrying the stored trip records forward. The LLM-side state is handled by the stub for now.
- **Done when:** one scripted day produces complete trip outcome records, a jeepney event log, tripinfo and edgeData outputs, and a run config. The same seed gives identical results when re-run.

### Phase 9 — Scripted validation before LLM integration
- **Refs:** 4.4.3 (Simulation Validation Before LLM Integration)
- **Scripted agents:** a small fixed set (for example 5–15) with hardcoded schedules, modes, and routes covering every mode. Include:
  - a pure walk trip
  - a bike trip and a car trip
  - a jeepney trip with a normal boarding
  - a jeepney trip where the first jeepney is full (force this with low capacity or many boarders)
  - two agents boarding the same jeepney at different positions
  - a deliberately delayed trip (for example, a closed or slowed edge)
- **The five checks from the manuscript**, each with its own test and pass criterion (you define the exact thresholds and report them):

| Check | What to verify |
|---|---|
| Environment connectivity | Origins and destinations are reachable; jeepney routes are continuous; scripted OD pairs return valid alternatives; boarding and alighting positions are within walking tolerance |
| Jeepney operation | Follows route; stops only where someone is registered; dwell applied; full jeepney passes; every event logged |
| Commuter movement | Every mode completes its full Table 4.10 state sequence, including walk → wait → ride → walk for jeepney |
| Disruption detection | The delayed trip is flagged; normal trips are not |
| Record completeness | Every trip has all fields needed by the reflection input and by the Sec. 4.5 evaluation |

- **Output:** a validation report (test cases, number of agents, pass criteria, results). This goes into the thesis results or appendix.
- **Done when:** all five checks pass. **This is the gate before LLM integration.**

### Phase 10 — Integration readiness (my side only)
- **Tasks**
  - `llm_stub.py`: a fake Mode Selection that returns the **agreed JSON schema** (for example, it picks the fastest alternative, or the habit-weighted one). This lets the full daily loop run end to end without the LLM.
  - JSON schema validators for every handoff (alternatives out, mode selection in, trip outcomes out).
  - **Retry and fallback hooks** (Sec. 4.5.6): if a Mode Selection output is still invalid after `k_max` attempts, assign the agent's highest-weight **feasible** mode and **flag the agent-day**.
  - Add a scenario loader for **baseline**, **congestion** (raise background flow rates on chosen segments), and **reduced jeepney availability** (lengthen headways on a corridor). Disruptions start only after a configured number of baseline days.
  - Support multiple random seeds per scenario.
  - Write an **interface doc** for the LLM team: what I send, what I expect back, and example files.
- **Done when:** a multi-day run with the stub works for all three scenarios and several seeds, and the LLM team has confirmed the schemas.

### Phase 11 — Scale and performance check (before real runs)
- Time one simulated day at increasing agent counts. Consider `libsumo` for speed when the GUI isn't needed.
- Profile the estimation module and confirm caching works.
- Report the practical agent limit to the group. The manuscript says sample size is set by computational feasibility tests.

---

## Part F — Data Swap Guide (when real data arrives)

| Real dataset arrives | Steps | Rebuild |
|---|---|---|
| Final study boundary (selected barangays) | Put the file in `data/real/`, switch the manifest to `real`, re-clip with the buffer | Phases 1 → 2 → 3 → 4 cache |
| Cleaned OSM + mode-accessibility rules | Replace the extract; turn rules into type-map overrides | Phase 1 (re-check patches still apply), then everything after it |
| Activity location inventory | Must match the location schema; switch manifest | Phase 2 snapping, Phase 4 cache |
| Origin zones with population weights | Switch manifest | Phase 2 residential pool |
| Jeepney routes (field-observed) | Must match the route format with `source` per point; switch manifest | Phase 3 matching, then Phase 4 cache; re-run Phase 9 jeepney checks |
| Headway, capacity, dwell, fare | Update `params.yaml`, set `status: observed` or `official`, fill `source` | Phase 4 cache; re-run Phase 9 |
| Traffic counts | Add file; switch background mode from `random` to `counts` | Phase 6; set up edgeData on counted edges |

**After any swap:**
1. Run `python -m sumo_track.build --all`.
2. Run the full Phase 9 validation suite again.
3. Check that the run config shows no remaining placeholders for anything used in thesis results.
4. Update `PROGRESS.md` and the data documentation matrix (owned by the data team, but tell them what changed).

---

## Part G — Open Decisions to Raise With the Group or Adviser
- Exact SUMO version to use for the whole thesis (pin it).
- Jeepney vClass choice and whether some narrow roads need permission overrides.
- Time-period definitions (AM peak, midday, PM peak, off-peak).
- Pass thresholds for each Phase 9 check.
- Disruption threshold definition (ratio vs. absolute seconds).
- Whether car and bike trips may reroute during a run (the manuscript leaves this open).
- Final JSON field names with the LLM team (Part D.4 is a draft).
- Where the data store lives (files vs. SQLite) and who owns it.
- Manuscript fixes in the SUMO chapter: the "stored in seconds ?" broken citation, the states table cited as Table 4.8 (it's 4.10), and feasibility filters / walking tolerance cited to Sec. 4.2.6 (which is traffic demand).

---

## Part H — `PROGRESS.md` Template

```markdown
# SUMO Track Progress
Last updated: YYYY-MM-DD
SUMO version: x.y.z

## Phase status
| Phase | Status | Notes |
|---|---|---|
| 0 Setup | ☐ / ◐ / ☑ | |
| 1 Network | | |
| 2 Locations | | |
| 3 Jeepney routes | | |
| 4 Estimation | | |
| 5 Demand builder | | |
| 6 Background traffic | | |
| 7 Controller + states | | |
| 8 Pipeline + recording | | |
| 9 Scripted validation | | |
| 10 Integration readiness | | |
| 11 Scale check | | |

## Datasets currently active
| Dataset | placeholder / real | Date switched |
|---|---|---|

## Decisions made
- YYYY-MM-DD: …

## Known issues / blockers
- …

## Next session goal
- …
```
