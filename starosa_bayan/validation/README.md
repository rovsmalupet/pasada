# Simulation validation with scripted test agents

The check of the simulation on its own, before the LLM is connected (manuscript Sec. 4.4.3.3).
A fixed set of test agents, whose schedules and modes are written by a script, not an LLM, run
through the same steps as a real day: candidate preparation (`make_choices.py`), conversion and
SUMO (`run.py`), and the trip records. Because every input is known, anything wrong points to
the simulation.

```powershell
python validation/validate.py      # about 1.5 min; PASS or FAIL per check, exit code 1 on a FAIL
python validation/make_cases.py    # only after the map, the lines or the places change
```

The latest result is in `report.md`. Each run also writes `validation_report.md`, with
everything it used, into its run folder (`runs/<date_time>/`).

## The test agents (`cases/`, made by `make_cases.py`)

| Case | Agents | What it tests |
|---|---|---|
| capacity | 22 + 3 | 22 people (one jeepney's seats) wait at one spot of the longest line before its first jeepney comes, so they all board it. 3 more wait further on: the full jeepney must pass them, and a later one take them |
| line | 3 | One rider per line, out on one direction and back on the other: all 6 lines carry someone |
| walk, bike, car | 1 each | A trip there and back by that mode |
| delayed | 1 | A car trip whose route is slowed on purpose (a variable speed sign): it must come out disrupted |

The files are the day's demand in the usual formats (`agents.json`, `schedule_day_1.json`,
`modes_day_1.json`; see `../demand/README.md`). `cases.json` says what each case is for and
what is expected of each trip. There is no background traffic, so the cases run at night, which
keeps the run short.

## The checks

| # | Check | Passes when |
|---|---|---|
| 1 | Environment connectivity | Every activity location and home lies on a walkable edge and is reachable on foot both ways from the most central place. Every line is a continuous route jeepneys may drive. Every scripted trip is offered its mode and line. Jeepney riders board and alight on their line, `KERB_MARGIN` clear of the edge ends |
| 2 | Jeepney operation | Jeepneys drive their whole route (first edge to last, the right length) and are never rerouted. They stop only where someone gets on or off, for at least `DWELL_S` and `boardingDuration` per person boarding. None carries more than `personCapacity`. The capacity case fills one jeepney, which passes the people waiting further on (a `full_pass_by` in the log and in their outcomes) and a later one takes them. The event log (`events.jsonl`) matches SUMO's own record of every stop (`stops.xml`) |
| 3 | Commuter movement | Every trip goes through its mode's states in order and arrives: walk: depart, arrived; jeepney: depart, waiting, boarding, alighting, arrived; bike or car: depart, vehicle departs, vehicle arrives, arrived |
| 4 | Disruption detection | The slowed trip is disrupted; the line, walk, bike and car trips are not |
| 5 | Record completeness | Every trip has its trip record (`trips.json`) and a completed outcome with every field the reflection and the evaluation need. Times add up |
