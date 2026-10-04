# Section 4.4 Prototype — Full Simplified Build Guide

This guide walks through a **miniature version of the entire Section 4.4**
(Transportation Simulation Development) of the thesis: environment, agents,
pipeline, and pre-LLM validation. Every step below maps to a subsection of
4.4 — the mapping is shown in each step's header — so finishing this guide
means you have touched every part of the methodology once, at small scale.

**Scope of the prototype (deliberately tiny):**
- one small study box in Santa Rosa (~2×2 km)
- one jeepney route, one direction
- a handful of real POIs extracted from OpenStreetMap
- a small residential edge pool for home origins
- 2–3 commuters (walk / jeepney flag-down), light background cars
- a minimal free-flow estimator and a scripted validation pass

No LLM anywhere — exactly like Section 4.4.3.3 requires. The LLM connects
only after all of this works with known, hand-authored inputs.

**Files in this kit:**

| File | Used in | What it is |
|---|---|---|
| `netconvert.netccfg` | Step 2 | Custom network conversion config (4.4.1.1 stage two) |
| `extract_pois.py` | Step 3 | Pulls categorized activity locations from the OSM file (4.4.1.3) |
| `snap_pois.py` | Step 4 | Matches each POI to a walkable network edge (4.4.1.4) |
| `make_route.py` | Step 6 | Builds the jeepney route edge list + boarding/alighting positions (4.4.1.5) |
| `demand.rou.xml` | Step 8 | Template demand file: vTypes, jeepney flow, commuters, background cars (4.4.1.6, 4.4.2) |
| `run.sumocfg` | Step 8+ | SUMO config with tripinfo output enabled (4.4.3.2) |
| `make_passengers.py` | Step 9 | Generates N random commuters → `passengers.rou.xml` (4.4.1.6) |
| `jeepney_controller.py` | Step 9 | TraCI flag-down controller (4.4.2.2) |

**Milestones:**
- **A** — network runs (end of Step 2)
- **B** — real locations on the network (end of Step 5)
- **C** — commuter rides a jeepney at a fixed stop (end of Step 8)
- **D** — flag-down via TraCI + event log (end of Step 9)
- **E** — scripted validation checklist passes (end of Step 11) ← *prototype done*

---

## Step 0 — Install (30 min)

1. Install SUMO ≥ 1.19: https://eclipse.dev/sumo/ (Windows installer /
   `brew install sumo` / `sudo apt install sumo sumo-tools`).
2. Set the `SUMO_HOME` environment variable to the install folder (the
   installer usually does this on Windows; on Linux it's typically
   `/usr/share/sumo`). Test: `echo %SUMO_HOME%` (Windows) or `echo $SUMO_HOME`.
3. `pip install sumolib traci`
4. Sanity check: type `sumo-gui` in a terminal — an empty window should open.

## Step 1 — Get the study area  *(= 4.4.1.1, stage one)*

1. Run the OSM Web Wizard:
   ```
   python "%SUMO_HOME%/tools/osmWebWizard.py"     (Windows)
   python "$SUMO_HOME/tools/osmWebWizard.py"      (Linux/Mac)
   ```
2. In the browser map, search "Santa Rosa, Laguna", click **Select Area**,
   and draw a SMALL box (~2×2 km) around an area with a main road, some
   residential streets, and at least one school or mall — e.g. the Balibago
   area or a stretch of the old national highway. Small = every bug visible.
3. Enable **Pedestrians** and **Bicycles**, click **Generate Scenario**.
4. sumo-gui opens with random traffic — press play ▶ to confirm it runs.
5. From the output folder (printed in the terminal), copy `osm_bbox.osm.xml`
   into your prototype folder and rename it **`santarosa.osm`**. This raw
   extract feeds Steps 2 and 3.

## Step 2 — Build the network your own way  *(= 4.4.1.1 stage two + 4.4.1.2)* → **Milestone A**

The wizard used defaults; the thesis requires a reproducible custom config.

```
netconvert -c netconvert.netccfg
```

This produces `santarosa.net.xml` with guessed sidewalks, crossings, and bike
lanes, joined junctions, and isolated fragments removed. The `.netccfg` file
IS your saved configuration — keep it in git.

**Mini-verification (4.4.1.2, simplified):** open the network:

```
netedit santarosa.net.xml
```

Zoom around your main road and 2–3 intersections; compare against Google
satellite view. If a connection is obviously wrong, fix it in netedit and note
what you changed — that note is the seed of your correction log. A full
scripted verification comes later at thesis scale; for the prototype, a visual
pass is enough.

## Step 3 — Extract POIs  *(= 4.4.1.3)*

```
python extract_pois.py santarosa.osm pois_raw.csv
```

The script scans OSM nodes and building outlines for schools, malls, markets,
terminals, workplaces, and recreation spots (the categories of Section 4.2.3),
and writes one row per location with its category and coordinates. It prints
a category count summary. If it finds very few, your box is too
residential — widen it slightly and redo Steps 1–2, or accept it and note the
limitation (your methodology's fallback procedure allows manually adding known
landmarks; just append rows to the CSV with `source=manual`).

## Step 4 — Snap POIs to the network  *(= 4.4.1.4, activity locations)*

```
python snap_pois.py santarosa.net.xml pois_raw.csv pois_snapped.csv
```

Each POI is matched to the nearest pedestrian-permitted edge within 100–300 m
and gets an `edge_id` + `position` (meters along the edge). Anything with no
walkable edge nearby is kept but marked `UNMATCHED` for manual review — the
same flag-don't-silently-match rule as the methodology.

`pois_snapped.csv` is your prototype's **processed activity location
inventory** — the file the LLM prompts will later be restricted to.

## Step 5 — Home origins: the residential edge pool  *(= 4.4.1.4, home origins)* → **Milestone B**

At thesis scale this is population-weighted sampling per barangay. Prototype
version: just find a few residential edges by hand.

1. In netedit, find 2–3 small residential streets in different parts of the
   box (OSM type `residential` or `living_street` — netedit shows the type
   when you click an edge).
2. Write their edge IDs into a tiny `home_origins.csv` you make yourself:

   ```
   agent_id,home_edge
   commuter0,<edge id 1>
   commuter1,<edge id 2>
   commuter2,<edge id 3>
   ```

That's the whole residential pool for now. (Automating it is a ~20-line
sumolib filter — a good "next step" after Milestone E.)

**Milestone B reached:** you now have real activity locations and home origins
sitting on valid network edges — the "environment composition" of 4.4.1.

## Step 6 — Jeepney route  *(= 4.4.1.5, route matching)*

At thesis scale you map-match GPS traces of a real route. Prototype version:
let SUMO's shortest path define the route along a main road you know jeepneys
actually use.

1. In netedit, note the edge IDs at the two ends of that main road
   (`ROUTE_START`, `ROUTE_END`) — same direction of travel.
2. ```
   python make_route.py santarosa.net.xml ROUTE_START ROUTE_END
   ```
   It prints the ordered **edge list** plus a suggested **boarding position**
   (midway) and **alighting position** (near the end). Write all three down.

If it says no path exists, one-way directions are the usual culprit — try the
opposite-direction twin edges (same number with/without a leading `-`).

## Step 7 — Free-flow estimates, by hand  *(= 4.4.1.5, estimation module — simplest possible version)*

The real estimation module is a Python package; the prototype version is a
five-minute calculation that proves you understand the formula. For ONE
commuter trip (home → school), compute on paper or in a notebook:

- **walk estimate** = walking distance ÷ 1.2 m/s (SUMO's default walk speed)
- **jeepney estimate** = four legs:
  - access walk: distance from home edge to the boarding position ÷ 1.2
  - wait: headway ÷ 2 → with the prototype's 300 s headway, that's **150 s**
  - ride: for each route edge between boarding and alighting,
    edge length ÷ min(speed limit, 16.7 m/s), summed
    (edge lengths and speeds: click edges in netedit, or
    `sumolib.net.readNet(...).getEdge(id).getLength()` / `.getSpeed()`)
  - egress walk: distance from alighting position to destination ÷ 1.2
- **jeepney cost** = ₱13 base fare for the first 4 km + ₱1.80/km after
  (LTFRB reference values — replace with current ones when you do 4.2.5
  properly)

Write these numbers down — Step 11 compares them against what the simulation
actually produces, which is exactly how disruption detection (4.4.3.1) works.

## Step 8 — Demand file + fixed-stop run  *(= 4.4.1.6 + 4.4.2.1/4.4.2.3/4.4.2.4)* → **Milestone C**

Open `demand.rou.xml` and fill every `>>>PLACEHOLDER<<<`:

- the route **edge list** from Step 6,
- **BOARD/ALIGHT edge + pos** from Step 6,
- **HOME_EDGE** from your `home_origins.csv` (Step 5),
- **DEST_EDGE** = an `edge_id` from `pois_snapped.csv` (Step 4) — e.g. a
  school. Your commuter now travels to a real mapped destination.

The template already contains all four agent types of Table 4.7:

- `jeepney` vType (capacity 16, boarding delay) + a **flow** every 300 s
  carrying line `JPN1` — the jeepney agents (4.4.2.2),
- one **person** with walk → ride JPN1 → walk stages — the commuter agent
  (4.4.2.1),
- a `car` vType and a thin background **flow** — background traffic (4.4.2.4).
  (Private car/bike commuter trips (4.4.2.3) are just a `<vehicle>` element —
  add one as an extra exercise if you like.)

For Milestone C the flow contains one temporary fixed `<stop>` at the boarding
position, so boarding works without any code. Run:

```
sumo-gui -c run.sumocfg
```

Watch the commuter walk from home, wait, board, ride, alight, and walk to the
school. Right-click the person → "track" to follow. If boarding fails, the
walk's `arrivalPos` and the stop's position must be identical numbers on the
same edge.

**Add a second commuter now:** duplicate the `<person>` block with a different
`home_edge` and a different POI destination and a slightly later `depart`.
Two commuters, two real destinations — the smallest possible "population."

## Step 9 — Flag-down via TraCI  *(= 4.4.2.2, the controller)* → **Milestone D**

1. In `demand.rou.xml`, **delete the `<stop .../>` line** inside the flow —
   now no jeepney stops on its own.
2. Generate the random commuters (20 by default, seed 42):
   ```
   python make_passengers.py            # or: python make_passengers.py 30 7
   ```
   Each gets a random home and destination street near the route; boarding
   and alighting are the nearest kerbside points on the route, and both walk
   legs are checked with SUMO's pedestrian router. Output:
   `passengers.rou.xml`, already listed in `run.sumocfg`.
3. ```
   python jeepney_controller.py          # sumo-gui, press play
   python jeepney_controller.py --nogui  # headless: event log + summary
   ```

No registry to fill in by hand any more: the controller reads every
`<person>` with a `<ride lines="JPN1">` from the route files in
`run.sumocfg` (boarding = end of the walk before the ride, alighting = the
ride's `arrivalPos`), so `commuter0` plus the 20 generated commuters are all
served.

The controller implements the four rules of 4.4.2.2 in miniature: it keeps a
registry of waiting/onboard commuters, flags the nearest jeepney approaching
within 200 m (only one per commuter — with a jeepney every 60 s, every vehicle
behind it would otherwise stop for nobody), commands `vehicle.setStop` at the
exact flagged position, skips when full, re-flags the next jeepney if the
first drives past, and prints a timestamped event log (WAITING / FLAGDOWN /
BOARDED / ALIGHTED / ARRIVED / FULL / MISSED) plus a per-commuter wait/ride
summary — a baby version of the jeepney operation event log of 4.4.3.2.

**Jeepneys stop at the kerb, cars go around them.** Three settings work
together:
- `run.sumocfg` turns on the **sublane model** (`lateral-resolution 0.8`), so
  vehicles have a sideways position inside their lane;
- the jeepney vType has `latAlignment="right"`, so it drives and stops tucked
  against the kerb side of its lane (0.7 m right of centre on a 3.2 m lane);
- `netconvert.netccfg` has `opposites.guess`, so the network knows which
  lanes face each other and cars can overtake a stopped jeepney through the
  oncoming lane when it is clear (the lane alone is too narrow for a car to
  squeeze past a jeepney).

Same 21 commuters, with the original light traffic (one car every 40 s, one
direction only), cars departing in the first 1500 s — mean time stopped:
41 s with a jeepney halting mid-lane and no overtaking; 41 s with sublanes
only (no room to pass); 18 s with sublanes + oncoming-lane overtaking (35 cars
overtook, no collisions). `PULL_OVER = True` in the controller CONFIG instead
makes jeepneys leave the lane entirely (SUMO "parking" stop): 16 s, but
sumo-gui then draws the parked jeepney beside the road.

**Capacity check (rule 4):** temporarily set `personCapacity="2"` in the
jeepney vType (the controller reads capacity from the vType) — busy jeepneys
should pass commuters by (`FULL` in the log) and the next one picks them up.

## Step 10 — Recording  *(= 4.4.3.2, simplified)*

`run.sumocfg` already writes `tripinfo.xml` — SUMO's per-vehicle/per-person
trip output with duration, waiting time, and time loss. After a run, open it
and find your commuters' entries. Together with the controller's printed event
log, you now have two of the three output groups of 4.4.3.2 (the third,
edge-level counts, only matters once you compare against real traffic counts).

Prototype-level reproducibility: keep `netconvert.netccfg`, the demand files
(`demand.rou.xml`, `passengers.rou.xml` — or just the `make_passengers.py`
seed that regenerates it), and the controller CONFIG values in git. That's the run-configuration record
of 4.4.3.2 at small scale.

## Step 11 — Scripted validation pass  *(= 4.4.3.3)* → **Milestone E, prototype done**

Your Step 8–9 setup already *is* a scripted validation: every schedule, mode,
origin, destination, and departure time was hand-authored, no LLM anywhere.
Now run the actual checklist from 4.4.3.3 against it:

- [ ] **Environment connectivity** — every home edge, POI edge, and route edge
      resolved and was reachable (Steps 4–6 succeeded; no UNMATCHED entries
      used as destinations).
- [ ] **Jeepney operation** — jeepneys followed the route, stopped only at
      registered positions, dwelled during exchange, and passed commuters when
      full (Step 9 + capacity check).
- [ ] **Commuter movement** — each commuter completed the full state sequence
      walk → wait → ride → alight → walk (watched in the GUI, confirmed by
      BOARDED/ALIGHTED log lines).
- [ ] **Disruption detection (manual version)** — compare each commuter's
      actual duration in `tripinfo.xml` against your Step 7 hand estimate.
      Then raise the background flows (`probability="0.067"` →
      `probability="0.1"` or more, on both `bg` and `bgon`) and re-run:
      the actual time should now exceed the estimate noticeably. That gap
      crossing a threshold = a disrupted trip. (At thesis scale a script does
      this comparison; here you do it by eye.)
- [ ] **Record completeness** — every commuter appears in both `tripinfo.xml`
      and the controller event log with boarding and alighting events.

All five checked ⇒ you have exercised every mechanism Section 4.4 describes,
end to end, with known inputs. Everything remaining at thesis scale is
*more of the same*: more POIs, a real GPS-traced route, automated scripts for
the by-hand steps (5, 7, 11), and eventually the LLM replacing your
hand-authored schedules and mode choices.

## After Milestone E — scaling checklist (maps prototype → thesis)

| Prototype shortcut | Thesis-scale replacement | 4.4 ref |
|---|---|---|
| Visual netedit spot-check | sumolib verification script + correction log | 4.4.1.2 |
| Hand-picked residential edges | Population-weighted residential pool script | 4.4.1.4 |
| Shortest-path "jeepney route" | GPS/field-traced route + map-matching script | 4.4.1.5 |
| Paper estimate for one trip | Estimation module with caching, all modes, per serving route | 4.4.1.5 |
| Hand-filled demand.rou.xml | Demand-file generator script (pipeline conversion step) | 4.4.1.6 |
| 2–3 commuters | Sampled synthetic population | 4.2.2 |
| Eyeball disruption check | Threshold comparison in the outcome recorder | 4.4.3.1 |
| Print-to-terminal event log | Structured CSV/JSON-lines log keyed by run/agent/day/trip | 4.4.3.2 |

## Troubleshooting quick hits

- **"Edge not known"** → edge IDs are case-sensitive and often start with `-`
  (direction). Copy them exactly from netedit.
- **Jeepney disappears at route start** → route edges not connected in that
  direction; rerun `make_route.py`, it validates the path.
- **Person teleports with a warning** → walk origin/destination not on a
  pedestrian-permitted edge; pick a smaller street, and confirm the network
  was built with `--sidewalks.guess` (our netccfg does this).
- **`extract_pois.py` finds 0 POIs** → box too small/residential, or the
  `.osm` is trimmed; widen the box or re-export from openstreetmap.org.
- **All POIs UNMATCHED** → you passed the `.osm` instead of the `.net.xml`
  to `snap_pois.py`, or sidewalks weren't generated.
- **Nothing moves** → read the terminal; SUMO prints XML errors with line
  numbers.
