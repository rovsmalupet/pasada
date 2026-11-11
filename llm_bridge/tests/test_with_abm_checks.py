"""Run the bridge on the simulation's real input files with a stand-in model, then check every
answer file with the simulation's OWN checks (starosa_bayan/llm_tasks.py)."""
import json, os, random, re, shutil, sys, tempfile
from pathlib import Path

SRC_ABM = Path(sys.argv[1])            # a copy of starosa_bayan (py files + demand/)
BRIDGE = Path(sys.argv[2])             # the folder that holds llm_bridge/
work = Path(tempfile.mkdtemp())
abm = work / "starosa_bayan"
shutil.copytree(SRC_ABM, abm)
locs = SRC_ABM.parent / "sumo_track" / "data" / "placeholder" / "locations.csv"   # settings.ACTIVITIES reads it
(work / "sumo_track" / "data" / "placeholder").mkdir(parents=True)
shutil.copy(locs, work / "sumo_track" / "data" / "placeholder" / "locations.csv")
shutil.copytree(BRIDGE / "llm_bridge", work / "llm_bridge")
# what she renames on the llm-integration branch: the persona's two fields
t = (abm / "llm_tasks.py").read_text()
pass
pass
os.environ["ABM_DIR"] = str(abm)
sys.path[:0] = [str(work), str(abm)]

import llm_bridge.kb as kb
kb.retrieve = lambda agent, k=None: [{"id": "M01", "statement": "Safety is ranked first.", "source": "Mayo 2020",
                                      "population": "Cebu"}]
import llm_bridge.model as M
from llm_bridge import answer as A
rng = random.Random(1)
CALLS = {"n": 0, "bad_first": 0}
BROKEN = {}

def block(prompt, marker):
    i = prompt.index(marker) + len(marker)
    return json.JSONDecoder().raw_decode(prompt[i:].lstrip())[0]

def fake(system, prompt, max_new_tokens=0):
    CALLS["n"] += 1
    BROKEN.setdefault(prompt, 0)
    if CALLS["n"] % 7 == 0 and BROKEN[prompt] < 2:  # now and then a broken answer (at most twice per prompt)
        BROKEN[prompt] += 1
        CALLS["bad_first"] += 1
        return "Sure! Here is the persona: {not json"
    if prompt.startswith("Sample attributes"):
        a = block(prompt, "population:\n")
        old = int(re.match(r"\d+", a["age_group"]).group()) >= 20 if re.match(r"\d+", a["age_group"]) else False
        return json.dumps({"behavioral_description": "A commuter.", "vehicle_ownership": a["household_car_access"] and old,
                           "bicycle_access": a["household_bicycle_access"], "walking_sensitivity": "Moderate",
                           "cycling_sensitivity": "High", "sensitivities": {k: "High" for k in
                           ["safety", "cost", "comfort", "accessibility", "availability", "efficiency", "reliability"]},
                           "general_transport_preference": "jeepney"})
    if "Describe your usual weekly travel routine" in prompt:
        opts = block(prompt, "place options:\n")
        modes = re.search(r"Your available transport modes are: (.*?)\. Use only", prompt).group(1).split(", ")
        dest = opts[1 + rng.randrange(len(opts) - 1)]["place"]
        role = rng.choice(["work", "shopping", "leisure", "personal", "errand at the bank", "school"])
        w = {m: round(1 / len(modes), 4) for m in modes}
        w[modes[0]] = round(1 - sum(v for m, v in w.items() if m != modes[0]), 4)
        return json.dumps({"frequent_locations": [{"place": "home", "role": "home"}, {"place": dest, "role": role}],
                           "recurring_trips": [
                               {"trip_id": "R1", "origin": "home", "destination": dest, "trip_purpose": role,
                                "travel_days": ["Monday", "Tuesday", "Wednesday"], "usual_departure_time": "7:30",
                                "mode_preference_weights": w},
                               {"trip_id": "R2", "origin": dest, "destination": "Home", "trip_purpose": "home",
                                "travel_days": ["Monday", "Tuesday", "Wednesday"], "usual_departure_time": "17:00",
                                "mode_preference_weights": w}]})
    if "Write in broad strokes what you are doing" in prompt:
        h = block(prompt, "Your usual travel routine:\n")
        others = [f["place"] for f in h["frequent_places"][1:]]
        if "Other kinds of place you could go to today" in prompt and rng.random() < 0.4:
            listed = block(prompt, "Other kinds of place you could go to today:\n")
            if listed:
                others = [listed[0]["place"]]
                CALLS["other_place"] = CALLS.get("other_place", 0) + 1
        if not others or rng.random() < 0.15:
            return json.dumps({"simulation_day": 1, "schedule": [{"start_time": "00:00", "activity": "Rest at home.",
                                                                  "place": "home", "trip_purpose": "home"}]})
        return json.dumps({"simulation_day": 1, "schedule": [
            {"start_time": "06:00", "end_time": "07:30", "activity": "Wake up.", "place": "home", "trip_purpose": "home"},
            {"start_time": "08:00", "end_time": "12:00", "activity": "Work.", "place": others[0], "trip_purpose": "work"},
            {"start_time": "12:00", "end_time": "17:00", "activity": "More work.", "place": others[0], "trip_purpose": "work"},
            {"start_time": "17:40", "activity": "Rest.", "place": "home", "trip_purpose": "home"}]})
    if "Your trips today and the options for each trip are:" in prompt:
        cands = block(prompt, "Your trips today and the options for each trip are:\n")
        decisions = []
        for c in cands:
            modes = [m["mode"] for m in c["candidate_modes"]]
            pick = next((m for m in ("jeepney", "walking") if m in modes), modes[0])
            decisions.append({"trip_id": c["trip_id"], "reasoning": "It is cheap.", "selected_mode": pick})
        return json.dumps({"simulation_day": 1, "decisions": decisions})
    if "The day is over. Reflect" in prompt:
        h = block(prompt, "Your usual travel routine and current mode preference weights:\n")
        ups = []
        for t in h["recurring_trips"]:
            w = dict(t["mode_preference_weights"])
            if len(w) >= 2 and rng.random() < 0.5:
                a, b = list(w)[:2]
                d = min(0.1, w[b])
                w[a], w[b] = round(w[a] + d, 4), round(w[b] - d, 4)
            ups.append({"trip_id": t["trip_id"], "mode_preference_weights": w})
        return json.dumps({"travel_experience_entry": {"simulation_day": 1, "reflection": "An ordinary day."},
                           "updated_mode_preference_weights": ups})
    raise AssertionError("unknown prompt: " + prompt[:200])

M.load = lambda name: None
M.ask = fake
M.ask_batch = lambda system, users, n=0: [fake(system, u) for u in users]
M.CURRENT = "llama"

import llm_tasks as LT
LT.load_lines = lambda: None
LT.apply_modes = lambda schedule, answer, lines: (None, [])     # needs the network files; the rest of check_modes runs

def run_and_check(task, day, check):
    out = A.answer_path(task, day)
    keep = out.with_suffix(".orig")
    shutil.move(out, keep)
    inp = abm / "demand" / "llm_inputs" / (f"{task}_input.json" if day is None else f"{task}_input_day_{day}.json")
    for attempt in range(10):          # like the real runner: run again to retry the agents that failed
        if A.run_task(task, day, inp, out, "llama"):
            break
    else:
        raise AssertionError(f"{task} {day} did not finish")
    ans = json.loads(out.read_text())
    errors = check(ans)
    print(f"--> {task} day {day}: {len(ans['agents'])} agents, simulation's own check: "
          f"{'PASSES' if not errors else 'FAILS'}")
    for e in errors[:15]:
        print("     ", e)
    assert not errors
    return keep, out

# persona: checked against the agents (the new personas)
keep, out = run_and_check("persona", None, lambda a: LT.check_personas(a, LT.state()))
p = json.loads(out.read_text())["agents"][0]["persona"]
assert "walking_sensitivity" in p and "description" in p, p
shutil.move(keep, out)                      # the later inputs were built from the stand-in personas
for task in ("habits",):
    keep, out = run_and_check(task, None, lambda a: LT.check_habits(a, LT.state()))
    shutil.move(keep, out)
for day in (1, 2):
    keep, out = run_and_check("schedule", day, lambda a, d=day: LT.check_schedule(a, LT.state(d), d)); shutil.move(keep, out)
    keep, out = run_and_check("modes", day, lambda a, d=day: LT.check_modes(a, LT.state(d), d)); shutil.move(keep, out)
    keep, out = run_and_check("reflection", day, lambda a, d=day: LT.check_reflection(a, LT.state(d), d)); shutil.move(keep, out)
logs = list((abm / "demand" / "llm_logs").glob("*.attempts.jsonl"))
rows = [json.loads(l) for f in logs for l in f.read_text().splitlines()]
print(f"\nmodel calls: {CALLS['n']} (broken on purpose: {CALLS['bad_first']}); logged tries: {len(rows)}; "
      f"regenerated: {sum(1 for r in rows if r['try'] > 1)}")
assert not list((abm / "demand" / "llm_logs").glob("*.partial.json"))
print("schedules that used a non-frequent place:", CALLS.get("other_place", 0))
print("ALL OK")
