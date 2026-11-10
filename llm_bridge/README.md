# llm_bridge: the LLM side of the simulation

`starosa_bayan/pipeline.py` runs a simulated day and stops at each LLM task, after writing what
the prompt is given (`starosa_bayan/demand/llm_inputs/`). This package answers those files with
**the prompts evaluated in PASADA-llm** (notebook `commuter_prompts_v4_server.ipynb`, prompt
v0.3), on the GPU server, and writes the answer files the pipeline is waiting for.

```
laptop (SUMO)                              GitHub (this branch)                server (GPU)
pipeline.py writes llm_inputs/X.json  ──push──►                ──pull──►  answer.py asks the model
pipeline.py goes on                   ◄──pull──                ◄──push──  writes demand/X.json
```

## Files

| File | What it does |
|---|---|
| `answer.py` | Answers every input file without an answer: one prompt per agent, batched, regenerating a failed answer up to 3 times (Sec. 4.3.9.1), saving progress after every agent |
| `tasks.py` | Per task: the simulation's input → the evaluated prompt, and the model's answer → checks → the simulation's answer format |
| `prompts.py` | The five prompts, copied from the notebook; the few changes the simulation needs are marked `INTEGRATION` |
| `kb.py`, `model.py`, `config.py` | Knowledge-base retrieval, model loading (4-bit, as evaluated) and settings, from the notebook |
| `run_sim.py` | Everything on the server: the pipeline day by day, answering each LLM task when it stops |
| `report.py` | A readable Excel of everything so far (also while running): progress, personas, habits, each day's schedule, modes, outcomes and reflections, and failures |
| `serve.sh` | Server loop for the git hand-over: every minute pull, answer, push |
| `laptop_loop.py` | Laptop loop: run the pipeline, push its inputs, wait for the answers, go on |

## Running it: everything on the GPU server (the usual way)

`run_sim.py` runs the pipeline day by day and answers each LLM task as soon as it stops for one,
with the model kept loaded. No git in between. In tmux, so a dropped connection does not stop it.

**Once:**
```bash
git clone -b llm-integration https://github.com/dlsudatasci/PASADA-abm.git ~/abm   # or: cd ~/abm && git pull
~/thesis-env/bin/pip install eclipse-sumo==1.27.0 sumolib==1.27.0 traci==1.27.0 shapely pyproj rtree
cd ~/abm/starosa_bayan
~/thesis-env/bin/netconvert -c netconvert.netccfg        # bayan.net.xml (not committed)
SUMO_HOME=$(~/thesis-env/bin/python -c "import sumo; print(sumo.SUMO_HOME)") ~/thesis-env/bin/python build.py
```

**Each run:**
```bash
cd ~/abm
tmux new -d -s sim "~/thesis-env/bin/python -u -m llm_bridge.run_sim --days 1 --first-args '--redo agents --agents 10' > ~/sim_log.txt 2>&1"
tail -f ~/sim_log.txt
```
- `--days 1-3`: several days (a day already done is kept).
- `--first-args "--redo agents --agents 10"`: start fresh with 10 agents (moves old files to `demand/old/`).
- `--model qwen`: another candidate model. `BATCH_SIZE=16` (before the command): more agents per GPU call.
- SUMO runs headless. To watch a day, copy its `starosa_bayan/runs/<date_time>/` folder to a laptop with
  SUMO and open it in sumo-gui.

By hand: `~/thesis-env/bin/python -m llm_bridge.answer` answers whatever the pipeline is waiting for
(`--show-prompt` prints the first prompt without the model).

## Batching

`answer.py` sends `BATCH_SIZE` agents (default 8) to the model in one generate call: the prompts
are padded on the left with an attention mask, so the padding is ignored, and each answer is
sampled on its own with the evaluated settings. A failed answer goes into a later batch for its
next try. Answers are equivalent to one-at-a-time generation, not bit-identical (GPU arithmetic
differs slightly with batch size).

## The other way: SUMO on a laptop, the LLM on the server (git hand-over)

Slower (each hand-off waits up to about a minute each way), but sumo-gui can be watched live.

- Server: `tmux new -d -s bridge "bash ~/abm/llm_bridge/serve.sh > ~/bridge_log.txt 2>&1"`
- Laptop, from `starosa_bayan` with its venv active:
  `python ..\llm_bridge\laptop_loop.py --day 1 --pipeline-args "--redo agents --agents 10"`

## Logs (for the methodology)

`starosa_bayan/demand/llm_logs/<answer>.attempts.jsonl`: every try, with the agent, model, prompt
version, prompt, raw answer, problems found and seconds. The regeneration rate and the time per
answer of Sec. 4.3.9 come from these.

## What differs from the evaluated prompts (prompt v0.3-abm5)

Every change is marked `INTEGRATION` in `prompts.py`. They are only what the simulation needs:

1. **Habits:** the location options are the simulation's (with straight-line `distance_m` from
   home and `reachable_by`); the roles use the simulation's activity names (work, school,
   shopping, leisure, out_of_area); and one line says each day's trips must start and end at home
   and follow on from each other.
   From v0.3-abm2 (2026-11-10, after the first 10-agent run): an example of such a day, and two
   lines asking to copy location ids exactly and not to make a trip to the same place.
   These are the three failures the first run regenerated most.
   From v0.3-abm3 (same day, after 3 of 10 agents failed every try): each outing is asked for
   as a round trip from home (chains of several stops failed most), and every prompt shows
   the agent's home as `HOME` instead of `HOME_A000150` (the model made up location ids from
   the agent's number, e.g. `LOC_000150`); the bridge maps `HOME` back.
2b. **Schedule, from v0.3-abm4:** besides the frequent locations, the prompt lists up to 3 other
   named places per category (the nearest the agent can reach and leave, from the habits step's
   options), and the schedule may use them "when your day calls for it". The evaluated prompt
   allowed only frequent locations; the manuscript (4.3.5) asks for habits to guide the schedule
   "while still allowing reasonable day-to-day variations".
2. **Schedule:** each place has an `end_time` (when the agent leaves it): the simulation starts a
   trip at the end of a stay. One line asks for enough time between places for the trip.
3. **Mode:** the options are the simulation's computed ones (times, distance, fare). Where several
   jeepney lines serve a trip, the fastest line is the jeepney option (the line is a route, not a
   mode). Two lines state the simulation's rules: a bike or car is used from where it was left,
   and an option that arrives after the next trip leaves is not picked when another would not.
4. **Reflection:** the trip outcomes are SUMO's; one line states the daily limit of 0.2 per weight.

**Since v0.3-abm5 (team meeting, 2026-11-10): places by category, never by id.** Every prompt
names places only by their category (`home`, `workplace`, `market`, `shop`, `healthcare`, ...).
For each agent and category, the bridge uses the agent's nearest place of that category (straight
line from home) among the places it can reach and leave (the habits step's options), so a category
is always the same place for that agent. Work may be at any category (`settings.ACTIVITIES`, which
the bridge reads, so both sides share one list); `personal` (errands, check-ups, church) is a new
activity at healthcare, services and worship places. The schedule may use any listed category.

Turning one format into the other is only renaming (mode, day and field names). Where the model's
words for an activity do not fit the place's category, the category's own activity is used (a
market is `shopping`). It never changes a choice the model made: an answer that breaks a rule is
regenerated, not repaired.

The persona's walking and cycling fields keep the evaluated prompt's meaning and names,
`walking_sensitivity` and `cycling_sensitivity` (High = it discourages them), as in the
manuscript (Sec. 4.3.2). `llm_tasks.py`, `llm_stub.py`, `demand/persona.schema.json` and
`demand/README.md` use these names on this branch.
