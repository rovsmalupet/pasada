# Santa Rosa Jeepney Prototype — Step-by-Step Guide

Goal: in one sitting, get a small piece of Santa Rosa running in SUMO with
**one jeepney route, one flag-down commuter (walk → wait → ride → walk), and a
few background cars** — a miniature of your whole Section 4.4.

You will finish with 3 milestones:

- **Milestone A** — Santa Rosa network runs in sumo-gui with random traffic.
- **Milestone B** — A jeepney circulates a fixed route and a commuter boards it
  at a *fixed* stop (no TraCI yet).
- **Milestone C** — The TraCI controller makes the jeepney stop *because* the
  commuter is waiting there (flag-down), the core trick of your thesis.

Files in this kit:

| File | What it is |
|---|---|
| `netconvert.netccfg` | Your custom network conversion config (Sec 4.4.1.1 stage two) |
| `make_route.py` | Helper: builds a jeepney route (edge list) between two edges you pick |
| `demand.rou.xml` | Template demand file: jeepney type, flow, one commuter, background cars |
| `run.sumocfg` | SUMO config tying network + demand together |
| `jeepney_controller.py` | Milestone C: TraCI flag-down controller |

---

## Step 0 — Install (30 min)

1. Install SUMO ≥ 1.19: https://eclipse.dev/sumo/ (Windows installer / `brew install sumo` / `sudo apt install sumo sumo-tools`).
2. Set the `SUMO_HOME` environment variable to the install folder (the installer
   usually does this on Windows; on Linux it's typically `/usr/share/sumo`).
   Test in a terminal: `echo %SUMO_HOME%` (Windows) or `echo $SUMO_HOME`.
3. `pip install sumolib traci`
4. Sanity check: type `sumo-gui` in a terminal. An empty window should open.

## Step 1 — Milestone A: get Santa Rosa (30–45 min)

1. Run the wizard:
   ```
   python "%SUMO_HOME%/tools/osmWebWizard.py"        (Windows)
   python "$SUMO_HOME/tools/osmWebWizard.py"         (Linux/Mac)
   ```
2. A browser map opens. Search "Santa Rosa, Laguna", then click **Select Area**
   and draw a SMALL box — start with roughly the Balibago area or the stretch of
   the old national highway (Rizal Blvd / national road corridor). Small = maybe
   2 km × 2 km. Do NOT grab the whole city; a small network makes every bug
   visible.
3. In the right panel: enable **Pedestrians** and **Bicycles**, leave cars on,
   click **Generate Scenario**.
4. sumo-gui opens with random traffic running. Press the play ▶ button.
   **That's Milestone A.** Note the output folder it created (shown in the
   terminal, usually `~/Sumo/yyyy-mm-dd-hh-mm-ss/`). Inside is `osm_bbox.osm.xml`
   — that's your raw OpenStreetMap extract. Copy it into this prototype folder
   and rename it `santarosa.osm`.

## Step 2 — Rebuild the network your own way (20 min)

The wizard used default options. Re-run the conversion with your own saved
config (this is literally your proposal's Section 4.4.1.1 second stage):

```
netconvert -c netconvert.netccfg
```

This produces `santarosa.net.xml`. Open it to inspect:

```
netedit santarosa.net.xml
```

In netedit, zoom around. Right-click any road → you'll see its **edge ID**
(something like `123456#1` or `-123456#0`). You need FOUR edge IDs — write them
down:

- `HOME_EDGE` — a small residential street (your commuter's home)
- `ROUTE_START` — one end of a main road you want the jeepney to serve
- `ROUTE_END` — the other end of that main road (same direction of travel!)
- `DEST_EDGE` — a street near a mall/school/market (the commuter's destination)

Tip: pick HOME_EDGE and DEST_EDGE within ~200 m walking of the main road, so
the walk legs are short.

> POIs for the prototype: you don't need polyconvert yet. "A street near the
> mall" IS your activity location for now. Real POI extraction is a later step.

## Step 3 — Build the jeepney route (10 min)

```
python make_route.py santarosa.net.xml ROUTE_START ROUTE_END
```

(replace with your actual edge IDs). The script prints:

- the **edge list** for the jeepney route,
- a suggested **boarding edge + position** (midway along the route),
- a suggested **alighting edge + position** (near the end).

Paste the edge list into `demand.rou.xml` where marked, and note the
boarding/alighting values.

## Step 4 — Milestone B: fixed-stop run (30 min)

Open `demand.rou.xml` and fill in every `>>>` placeholder using the values from
Steps 2–3. The template already contains:

- a `jeepney` vehicle type (capacity 16, boarding delay),
- a jeepney **flow** every 5 minutes carrying line `JPN1`, with ONE fixed
  `<stop>` at the boarding position (temporary — deleted in Milestone C),
- one **person** whose plan is walk → ride line JPN1 → walk,
- a light background car flow.

Run it:

```
sumo-gui -c run.sumocfg
```

Press play. Watch for: a yellow-ish jeepney leaves the route start, halts at the
stop, your commuter (a small figure) walks from home, waits, boards, rides, and
walks to the destination. Use right-click → "track" on the person to follow
them. **That's Milestone B** — full multi-stage jeepney commuting, the same
person-stage structure your Section 4.4.2.1 describes.

If the person never boards: the vehicle's stop position and the walk's
`arrivalPos` must be on the SAME edge and within a few meters of each other —
recheck the numbers.

## Step 5 — Milestone C: flag-down via TraCI (1–2 hours)

1. In `demand.rou.xml`, DELETE the `<stop .../>` line inside the flow's route
   (the comment marks it). Now no jeepney ever stops on its own.
2. Fill in the CONFIG block at the top of `jeepney_controller.py` with the same
   boarding/alighting edge+pos values.
3. Run the controller (it launches sumo-gui itself):

   ```
   python jeepney_controller.py
   ```

What you should see: the commuter walks to the roadside and waits. The
controller detects a JPN1 jeepney whose route passes that position, commands
`vehicle.setStop` there → the jeepney pulls over ONLY because someone is
flagging it. Once the person is aboard, the controller registers the alighting
stop; the jeepney pulls over again, the person gets off and walks to the
destination. The terminal prints a timestamped event log (stop commanded,
boarded, alighted) — a baby version of your Section 4.4.2.2 event log.

**That's Milestone C — the prototype is done.** Everything else in Section 4.4
(POI extraction, snapping scripts, the estimation module, scripted validation)
is scaling this up, not new concepts.

## What to try next (in order)

1. Add a second commuter at a different roadside position — confirm the same
   jeepney serves both flag-downs.
2. Fill the jeepney: set `personCapacity="2"` and add 3 commuters — confirm the
   controller's capacity skip leaves the third waiting for the next jeepney.
3. Replace the hand-picked boarding position with a computed one: nearest point
   on the route to HOME_EDGE (this is the "nearest-point function" of Section
   4.4.1.5 — ~15 lines with sumolib).
4. Wrap a `randomTrips.py` background flow around it and watch congestion delay
   the jeepney — the seed of your disruption detection.

## Troubleshooting quick hits

- **"Edge not known"** → edge IDs are case-sensitive and often start with `-`
  (direction). Copy them exactly from netedit.
- **Jeepney disappears at route start** → your ROUTE_START/ROUTE_END edges are
  not connected in that direction; rerun `make_route.py`, it validates the path.
- **Person teleports with a warning** → walk origin/destination not on a
  pedestrian-permitted edge; pick a smaller residential street, or check the
  network was built with `--sidewalks.guess` (our netccfg does this).
- **Nothing moves** → check the terminal for XML errors; SUMO prints the line
  number.
