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
| `answer.py` | Answers every input file without an answer: one prompt per agent, regenerating a failed answer up to 3 times (Sec. 4.3.9.1), saving progress after every agent |
| `tasks.py` | Per task: the simulation's input → the evaluated prompt, and the model's answer → checks → the simulation's answer format |
| `prompts.py` | The five prompts, copied from the notebook; the few changes the simulation needs are marked `INTEGRATION` |
| `kb.py`, `model.py`, `config.py` | Knowledge-base retrieval, model loading (4-bit, as evaluated) and settings, from the notebook |
| `serve.sh` | Server loop: every minute pull, answer, push |
| `laptop_loop.py` | Laptop loop: run the pipeline, push its inputs, wait for the answers, go on |

## Running it

**Server, once:**
```bash
git clone -b llm-integration https://github.com/dlsudatasci/PASADA-abm.git ~/abm
~/thesis-env/bin/pip install sentence-transformers pandas   # if not there yet
```
It uses the models in `~/thesis_llm/models` and the knowledge base
`~/thesis_llm/inputs/knowledge_base_factors_v1.csv` (change with `THESIS_LLM` or `KB_FILE`).

**Server, each time:**
```bash
tmux new -d -s bridge "bash ~/abm/llm_bridge/serve.sh > ~/bridge_log.txt 2>&1"
tail -f ~/bridge_log.txt
```
By hand instead: `cd ~/abm && ~/thesis-env/bin/python -m llm_bridge.answer` (add `--model qwen`
for another model, `--show-prompt` to print the first prompt without the model).

**Laptop**, from `starosa_bayan` with its venv active:
```powershell
python ..\llm_bridge\laptop_loop.py --day 1 --pipeline-args "--redo agents --agents 10"   # first test: 10 agents, fresh
python ..\llm_bridge\laptop_loop.py --day 1        # day 1 again after a stop
python ..\llm_bridge\laptop_loop.py --day 2        # the next day
```
`--redo agents` moves the stand-in's files (from `--llm stub` runs) into `demand/old/`, so the
model answers from the start.

## Logs (for the methodology)

`starosa_bayan/demand/llm_logs/<answer>.attempts.jsonl`: every try, with the agent, model, prompt
version, prompt, raw answer, problems found and seconds. The regeneration rate and the time per
answer of Sec. 4.3.9 come from these.

## What differs from the evaluated prompts (prompt v0.3-abm1)

Every change is marked `INTEGRATION` in `prompts.py`. They are only what the simulation needs:

1. **Habits:** the location options are the simulation's (with straight-line `distance_m` from
   home and `reachable_by`); the roles use the simulation's activity names (work, school,
   shopping, leisure, out_of_area); and one line says each day's trips must start and end at home
   and follow on from each other.
2. **Schedule:** each place has an `end_time` (when the agent leaves it): the simulation starts a
   trip at the end of a stay. One line asks for enough time between places for the trip.
3. **Mode:** the options are the simulation's computed ones (times, distance, fare). Where several
   jeepney lines serve a trip, the fastest line is the jeepney option (the line is a route, not a
   mode). Two lines state the simulation's rules: a bike or car is used from where it was left,
   and an option that arrives after the next trip leaves is not picked when another would not.
4. **Reflection:** the trip outcomes are SUMO's; one line states the daily limit of 0.2 per weight.

Turning one format into the other is only renaming (mode, day and field names). Where the model's
words for an activity do not fit the place's category, the category's own activity is used (a
market is `shopping`). It never changes a choice the model made: an answer that breaks a rule is
regenerated, not repaired.

The persona's walking and cycling fields keep the evaluated prompt's meaning and names,
`walking_sensitivity` and `cycling_sensitivity` (High = it discourages them), as in the
manuscript (Sec. 4.3.2). `llm_tasks.py`, `llm_stub.py`, `demand/persona.schema.json` and
`demand/README.md` use these names on this branch.
