"""The manuscript's evaluation measures (Sec. 4.5) that come from the simulation, over every day the
pipeline has run so far. Each day belongs to "baseline" or to the scenario it ran under (its run's
summary.json), and every measure is given per group of days.

From each day's files in the demand folder (the outcomes name the run folder they come from):
  * commuter behaviour (Sec. 4.5.4): mode share, overall and by income group and vehicle access;
    trip time and length per mode; how many short trips were walked or biked; the hard-constraint
    violation rate; the daily habit change (Delta_t); the disrupted mode's weight under a scenario
    (C(T)); switch_rate(k); the departure-time adaptation effect;
  * the network (Sec. 4.5.5): the jeepney wait per line and period against half the headway; dwell
    time, predicted from who boarded and alighted against simulated; mean jeepney occupancy per line
    at peak and off-peak hours; the traffic counts against the group's videos (GEH, counts.py: only
    days run with background);
  * running the simulation (Sec. 4.5.6): SUMO's run time, teleports and collisions per day.
Not here: invalid_rate and mean_regenerations (the LLM's retries, in the LLM team's logs) and the
reflection grounding rate (coded by the researchers).

With several demand folders (e.g. the same days under different seeds), the main numbers are also
given as mean and standard deviation across them (Sec. 4.5.3). Each folder gets a measures.json.

    python measures.py                    # the demand folder of settings.DEMAND
    python measures.py --demand A B C     # several demand folders: each, then the mean and SD across
"""

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

import settings as S
from convert_demand import MODES, clock

PEAKS = {name for name, _, _ in S.PERIODS if name.endswith("peak")}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def mean(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def period(seconds):
    hour = int(seconds // 3600) % 24
    return next(name for name, a, b in S.PERIODS if a <= hour < b)


def load_days(demand):
    """Every day of `demand` with outcomes, in order: its files, its run (if still there) and group."""
    days, n = [], 1
    while (demand / f"outcomes_day_{n}.json").is_file():
        outcomes = load(demand / f"outcomes_day_{n}.json")
        run_dir = S.RUNS / outcomes["made_by"].rsplit("run ", 1)[1]
        summary = load(run_dir / "summary.json") if (run_dir / "summary.json").is_file() else None
        scenario = (summary or {}).get("scenario")
        choices = {t["trip_id"]: (a, t) for a in load(demand / f"choices_day_{n}.json")["agents"] for t in a["trips"]}
        reflection = demand / f"reflection_day_{n}.json"
        days.append(SimpleNamespace(
            n=n, run_dir=run_dir if summary else None, summary=summary or {}, scenario=scenario,
            group=scenario["name"] if scenario else "baseline" if summary else "unknown (run folder gone)",
            trips=[(a["agent_id"], t) for a in outcomes["agents"] for t in a["trips"]], choices=choices,
            reflection={a["agent_id"]: a["habit_update"] for a in load(reflection)["agents"]} if reflection.is_file() else None))
        n += 1
    return days


def grouped(days):
    """[(group, its days)] in order of first appearance."""
    groups = defaultdict(list)
    for d in days:
        groups[d.group].append(d)
    return list(groups.items())


def shares(counter):
    total = sum(counter.values())
    return {m: round(counter[m] / total, 3) for m in MODES} | {"trips": total} if total else {"trips": 0}


def fmt_shares(s):
    return "  ".join(f"{m} {100 * s[m]:4.1f}%" for m in MODES if m in s) + f"  ({s['trips']} trips)"


# ------------------------------------------------------------------- Sec. 4.5.4 behaviour
def modal_split(days, agents):
    print("\n================ mode share (Sec. 4.5.4.1) ================")
    out = {}
    for group, ds in grouped(days):
        overall, income, access, short = Counter(), defaultdict(Counter), defaultdict(Counter), Counter()
        times, lengths = defaultdict(list), defaultdict(list)
        for d in ds:
            for aid, t in d.trips:
                m, who = t["realized_mode"], d.choices[t["trip_id"]][0]
                decile = agents[aid]["per_capita_income_decile"]
                overall[m] += 1
                income[next((g for g, (a, b) in S.INCOME_GROUPS.items() if a <= decile <= b), "unknown")][m] += 1
                access["car" if who["has_car"] else "bike, no car" if who["has_bike"] else "neither"][m] += 1
                lengths[m].append(t["distance_m"])
                if t["completed"]:
                    times[m].append(t["actual_total_time_s"])
                if t["distance_m"] < S.SHORT_TRIP_M:
                    short[m in ("walk", "bike")] += 1
        out[group] = {
            "days": [d.n for d in ds], "overall": shares(overall),
            "by_income": {g: shares(income[g]) for g in [*S.INCOME_GROUPS, "unknown"] if income[g]},
            "by_access": {g: shares(access[g]) for g in ("car", "bike, no car", "neither") if access[g]},
            "per_mode": {m: {"trips": overall[m], "mean_time_min": mean([x / 60 for x in times[m]]),
                             "mean_length_km": mean([x / 1000 for x in lengths[m]])} for m in MODES if overall[m]},
            "short_trips": {"under_m": S.SHORT_TRIP_M, "trips": sum(short.values()),
                            "walked_or_biked": round(short[True] / sum(short.values()), 3) if short else None}}
        o = out[group]
        print(f"  {group} (days {', '.join(map(str, o['days']))}): {fmt_shares(o['overall'])}")
        for kind in ("by_income", "by_access"):
            for g, s in o[kind].items():
                print(f"      {g:18s} {fmt_shares(s)}")
        print("      mean trip: " + "; ".join(f"{m} {v['mean_time_min'] or 0:.1f} min, {v['mean_length_km']:.2f} km"
                                            for m, v in o["per_mode"].items()))
        if short:
            print(f"      trips under {S.SHORT_TRIP_M:.0f} m: {sum(short.values())}, walked or biked: "
                  f"{100 * o['short_trips']['walked_or_biked']:.0f}%")
    return out


def violations(days):
    """Trips made in a way the options were meant to rule out (Sec. 4.5.4.2): expected 0."""
    print("\n================ hard-constraint violations (Sec. 4.5.4.2) ================")
    out = {}
    for group, ds in grouped(days):
        why = Counter()
        total = sum(len(d.trips) for d in ds)
        for d in ds:
            for _, t in d.trips:
                who, trip = d.choices[t["trip_id"]]
                m, dist = t["realized_mode"], t["distance_m"]
                reason = ("car without a car" if m == "car" and not who["has_car"] else
                          "bike without a bike" if m == "bike" and not who["has_bike"] else
                          "bike over BIKE_TRIP_LIMIT" if m == "bike" and dist > S.BIKE_TRIP_LIMIT else
                          "walk over WALK_TRIP_LIMIT" if m == "walk" and dist > S.WALK_TRIP_LIMIT else
                          "not one of the trip's options" if m not in {o["mode"] for o in trip["options"]} else None)
                if reason:
                    why[reason] += 1
        out[group] = {"trips": total, "violations": sum(why.values()), "rate": round(sum(why.values()) / total, 4) if total else None,
                      "why": dict(why)}
        print(f"  {group}: {sum(why.values())} of {total} trips" + (f" ({', '.join(f'{k} {v}' for k, v in why.items())})" if why else ""))
    return out


def weight_history(demand, days):
    """{(agent, trip_key): [w_0, w_1, ...]}: each recurring trip's mode weights before day 1 (w_0) and
    after each day's reflection, as far as the reflections go."""
    hist = {(a["agent_id"], t["trip_key"]): [t["mode_preference_weights"]]
            for a in load(demand / "habits.json")["agents"] for t in a["recurring_trips"]}
    for d in days:
        if d.reflection is None:
            break
        for (aid, key), ws in hist.items():
            ws.append(d.reflection.get(aid, {}).get(key, ws[-1]))
    return hist


def l1(a, b):
    return sum(abs(a.get(m, 0.0) - b.get(m, 0.0)) for m in set(a) | set(b))


def primary(ws):
    return max(ws, key=ws.get)


def habits(demand, days):
    print("\n================ habit adaptation (Sec. 4.5.4.3) ================")
    hist = weight_history(demand, days)
    reflected = len(next(iter(hist.values()))) - 1 if hist else 0
    out = {"days_reflected": reflected}
    # daily change: mean over every recurring trip, day by day, then per group
    delta = {d.n: [l1(ws[d.n], ws[d.n - 1]) for ws in hist.values()] for d in days[:reflected]}
    out["delta"] = {}
    for group, ds in grouped(days[:reflected]):
        per_day = {d.n: mean(delta[d.n]) for d in ds}
        out["delta"][group] = {"mean": mean([x for d in ds for x in delta[d.n]]), "per_day": per_day,
                               "max": max(x for d in ds for x in delta[d.n])}
        print(f"  Delta_t, {group}: mean {out['delta'][group]['mean']}, largest {out['delta'][group]['max']:.2f}; "
              "per day " + ", ".join(f"day {n} {v}" for n, v in per_day.items()))
    # C(T): each run of consecutive days under a scenario, against the weights going into its first day
    out["C"] = []
    blocks, start = [], 0
    for i in range(1, reflected + 1):
        if i == reflected or days[i].group != days[start].group:
            if days[start].scenario:
                blocks.append(days[start:i])
            start = i
    for block in blocks:
        mode = block[0].scenario.get("disrupted_mode")
        if not mode:
            continue
        t0 = block[0].n
        hit = {aid for d in block for aid, t in d.trips if t["disrupted"]}
        series = {k: [ws[n].get(mode, 0.0) for n in range(t0 - 1, block[-1].n + 1)]
                  for k, ws in hist.items() if ws[t0 - 1].get(mode, 0.0) > 0}
        c = {"scenario": block[0].group, "mode": mode, "days": [d.n for d in block], "trips_followed": len(series)}
        for who, keys in (("all", list(series)), ("disrupted_agents", [k for k in series if k[0] in hit])):
            c[who] = {"trips": len(keys),
                      "C_T": {d.n: mean([series[k][d.n - t0 + 1] - series[k][0] for k in keys]) for d in block},
                      "never_rising": round(sum(all(b <= a + 1e-9 for a, b in zip(series[k], series[k][1:])) for k in keys)
                                            / len(keys), 3) if keys else None}
        out["C"].append(c)
        print(f"  C(T), {mode} weight under {c['scenario']} from day {t0}, {len(series)} recurring trips with {mode} "
              f"weight > 0: " + ", ".join(f"day {n} {v}" for n, v in c["all"]["C_T"].items())
              + f"; never rising {c['all']['never_rising']}")
        print(f"      of agents with a disrupted trip ({c['disrupted_agents']['trips']} trips): "
              + ", ".join(f"day {n} {v}" for n, v in c["disrupted_agents"]["C_T"].items()))
    # switch_rate(k): after k disrupted days in a row, did the agent's primary mode of a recurring trip change?
    streak, num, den = Counter(), Counter(), Counter()
    by_agent = defaultdict(list)
    for k, ws in hist.items():
        by_agent[k[0]].append(ws)
    for d in days[:reflected]:
        hit = {aid for aid, t in d.trips if t["disrupted"]}
        for aid, trips in by_agent.items():
            streak[aid] = streak[aid] + 1 if aid in hit else 0
            if streak[aid]:
                den[streak[aid]] += 1
                num[streak[aid]] += any(primary(ws[d.n]) != primary(ws[d.n - 1]) for ws in trips)
    out["switch_rate"] = {k: {"agents": den[k], "switched": num[k], "rate": round(num[k] / den[k], 3)} for k in sorted(den)}
    print("  switch_rate(k): " + (", ".join(f"k={k} {v['switched']}/{v['agents']}" for k, v in out["switch_rate"].items())
                                  or "no agent had a disrupted trip"))
    return out


def departures(days):
    """Sec. 4.5.4.4: how the planned departure of the same trip (same agent, origin and destination)
    moved from one day to the next, after a disrupted trip and after an undisrupted one."""
    print("\n================ departure-time adaptation (Sec. 4.5.4.4) ================")
    def plan(d):
        out = defaultdict(list)                       # (agent, from, to) -> [(depart min, disrupted)]
        for aid, t in d.trips:
            trip = d.choices[t["trip_id"]][1]
            h, m = map(int, trip["depart"].split(":"))
            out[(aid, trip["from"], trip["to"])].append((h * 60 + m, bool(t["disrupted"])))
        return out
    shifts = {True: [], False: []}
    for a, b in zip(days, days[1:]):
        before, after = plan(a), plan(b)
        for key, trips in before.items():
            for (dep, hit), (dep_next, _) in zip(trips, after.get(key, [])):
                shifts[hit].append(dep_next - dep)
    out = {"affected": {"trips": len(shifts[True]), "mean_shift_min": mean(shifts[True])},
           "unaffected": {"trips": len(shifts[False]), "mean_shift_min": mean(shifts[False])}}
    out["adaptation_effect_min"] = (round(out["affected"]["mean_shift_min"] - out["unaffected"]["mean_shift_min"], 2)
                                    if shifts[True] and shifts[False] else None)
    print(f"  next day's departure, after a disrupted trip: {out['affected']['mean_shift_min']} min "
          f"({len(shifts[True])} trips); after an undisrupted one: {out['unaffected']['mean_shift_min']} min "
          f"({len(shifts[False])}); adaptation effect {out['adaptation_effect_min']} min (negative: earlier)")
    return out


# --------------------------------------------------------------------- Sec. 4.5.5 network
def events(d, kinds):
    if d.run_dir and (d.run_dir / "events.jsonl").is_file():
        with open(d.run_dir / "events.jsonl", encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                if e["event"] in kinds:
                    yield e


def waits(days):
    print("\n================ jeepney wait vs half the headway (Sec. 4.5.5.3) ================")
    out = {}
    for group, ds in grouped(days):
        cells = defaultdict(lambda: {"waits": [], "headway_s": None})
        for d in ds:
            headways = (d.scenario or {}).get("jeepney_headway_s", {})
            for _, t in d.trips:
                if t["realized_mode"] == "jeepney" and t.get("actual_waiting_time_s") is not None and t["route_id"]:
                    cell = cells[(t["route_id"], period(t["actual_departure_s"] + t.get("actual_access_time_s", 0)))]
                    cell["waits"].append(t["actual_waiting_time_s"])
                    cell["headway_s"] = headways.get(t["route_id"].rsplit("_", 1)[0], S.JEEPNEY_HEADWAY_S)
        out[group] = {f"{line} {p}": {"trips": len(c["waits"]), "mean_wait_min": mean([w / 60 for w in c["waits"]]),
                                      "half_headway_min": c["headway_s"] / 120,
                                      "ratio": round(sum(c["waits"]) / len(c["waits"]) / (c["headway_s"] / 2), 2)}
                      for (line, p), c in sorted(cells.items())}
        print(f"  {group}:")
        for k, v in out[group].items():
            print(f"      {k:24s} {v['trips']:4d} trips  mean wait {v['mean_wait_min']:5.1f} min  half headway "
                  f"{v['half_headway_min']:5.1f} min  ratio {v['ratio']:.2f}")
    return out


def dwell(days):
    print("\n================ dwell time (Sec. 4.5.5.4) ================")
    per_person = S.JEEPNEY["boardingDuration"]       # SUMO takes as long to let a person off as on
    out = {}
    for group, ds in grouped(days):
        rows = [(len(e["boarded"]) + len(e["alighted"]), e["dwell_s"])
                for d in ds for e in events(d, {"stop"}) if e["dwell_s"] is not None]
        if not rows:
            continue
        n, sim = [r[0] for r in rows], [r[1] for r in rows]
        r = statistics.correlation(n, sim) if len(set(n)) > 1 and len(set(sim)) > 1 else None
        out[group] = {"stops": len(rows), "mean_predicted_s": mean([x * per_person for x in n]),
                      "mean_simulated_s": mean(sim), "at_minimum": round(sum(s <= S.DWELL_S for s in sim) / len(sim), 3),
                      "r_passengers_dwell": round(r, 3) if r is not None else None}
        o = out[group]
        print(f"  {group}: {o['stops']} stops, predicted {o['mean_predicted_s']} s, simulated {o['mean_simulated_s']} s "
              f"({100 * o['at_minimum']:.0f}% at the {S.DWELL_S:g} s minimum); r(passengers, dwell) {o['r_passengers_dwell']}")
    return out


def occupancy(days):
    print("\n================ jeepney occupancy, peak vs off-peak (Sec. 4.5.5.5) ================")
    out = {}
    for group, ds in grouped(days):
        hourly = defaultdict(lambda: defaultdict(list))  # line -> hour -> persons aboard, each sample
        for d in ds:
            for e in events(d, {"occupancy"}):
                hourly[e["line"]][int(e["t"] // 3600) % 24].append(e["persons"])
        if not hourly:
            continue
        out[group] = {}
        for line, hours in sorted(hourly.items()):
            peak = [x for h, xs in hours.items() if period(h * 3600) in PEAKS for x in xs]
            off = [x for h, xs in hours.items() if period(h * 3600) not in PEAKS for x in xs]
            out[group][line] = {"peak": mean(peak), "off_peak": mean(off),
                                "peak_above_off_peak": bool(peak and off and mean(peak) > mean(off)),
                                "per_hour": {h: mean(xs) for h, xs in sorted(hours.items())}}
        print(f"  {group} (mean persons aboard, peak vs off-peak): "
              + "; ".join(f"{line} {v['peak'] or 0:.2f} vs {v['off_peak'] or 0:.2f}" + ("" if v["peak_above_off_peak"] else " (not above)")
                          for line, v in out[group].items()))
    return out


def traffic_counts(days):
    print("\n================ traffic counts vs the videos, GEH (Sec. 4.5.5.2) ================")
    out = {}
    for group, ds in grouped(days):
        rows = [dict(r, day=d.n) for d in ds for r in d.summary.get("counts", {}).get("rows", [])]
        cells = [(r["day"], r["from"], r["direction"], c, g) for r in rows for c, g in r["geh"].items() if c != "all"]
        if not cells:
            print(f"  {group}: no day run with background traffic")
            continue
        out[group] = {"cells": len(cells), "geh_below_5": round(sum(g < 5 for *_, g in cells) / len(cells), 3), "rows": rows}
        print(f"  {group}: GEH < 5 in {sum(g < 5 for *_, g in cells)} of {len(cells)} direction-class cells "
              f"({100 * out[group]['geh_below_5']:.0f}%; the target: 85%)")
    return out


# ---------------------------------------------------------------------- Sec. 4.5.6 running
def running(days):
    print("\n================ running the simulation (Sec. 4.5.6) ================")
    out = {}
    for d in days:
        s = d.summary
        out[d.n] = {"group": d.group, "run": d.run_dir.name if d.run_dir else None, "background": s.get("background"),
                    "seed": s.get("seed"), "run_time_s": s.get("run_time_s"), "ended": clock(s["ended_s"]) if "ended_s" in s else None,
                    "teleports": s.get("teleports"), "collisions": s.get("collisions")}
        o = out[d.n]
        print(f"  day {d.n} ({d.group}): run {o['run']}, background {o['background']}, seed {o['seed']}, "
              f"SUMO {o['run_time_s']} s, until {o['ended']}, teleports {o['teleports']}, collisions {o['collisions']}")
    return out


# --------------------------------------------------------------------------------- main
def measure(demand):
    days = load_days(demand)
    if not days:
        raise SystemExit(f"No outcomes_day_1.json in {demand}: run python pipeline.py first")
    agents = {a["agent_id"]: a for a in load(demand / "agents.json")["agents"]}
    print(f"\n######## {demand}: days 1-{len(days)} ("
          + ", ".join(f"{g}: {', '.join(str(d.n) for d in ds)}" for g, ds in grouped(days)) + ")")
    out = {"made_by": "measures.py", "demand": str(demand), "days": {d.n: d.group for d in days},
           "mode_share": modal_split(days, agents), "violations": violations(days), "habits": habits(demand, days),
           "departures": departures(days), "jeepney_wait": waits(days), "dwell": dwell(days),
           "occupancy": occupancy(days), "traffic_counts": traffic_counts(days), "running": running(days)}
    (demand / "measures.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n(all of it in {S.rel(demand / 'measures.json')})")
    return out


def headline(m):
    """The main numbers of one folder's measures, flat: {name: value}."""
    out = {}
    for g, v in m["mode_share"].items():
        out.update({f"{g}: share {mode}": v["overall"].get(mode) for mode in MODES})
    for g, v in m["violations"].items():
        out[f"{g}: violation rate"] = v["rate"]
    for g, v in m["habits"]["delta"].items():
        out[f"{g}: mean Delta_t"] = v["mean"]
    for c in m["habits"]["C"]:
        last = list(c["all"]["C_T"].items())[-1] if c["all"]["C_T"] else None
        if last:
            out[f"{c['scenario']}: C(T) {c['mode']} by day {last[0]}"] = last[1]
    for k, v in m["habits"]["switch_rate"].items():
        out[f"switch_rate({k})"] = v["rate"]
    out["adaptation effect (min)"] = m["departures"]["adaptation_effect_min"]
    for g, cells in m["jeepney_wait"].items():
        ratios = [c["ratio"] for c in cells.values()]
        out[f"{g}: wait / half headway (mean over lines and periods)"] = mean(ratios)
    for g, v in m["dwell"].items():
        out[f"{g}: dwell simulated (s)"] = v["mean_simulated_s"]
    for g, lines in m["occupancy"].items():
        out[f"{g}: occupancy peak"] = mean([v["peak"] for v in lines.values() if v["peak"] is not None])
        out[f"{g}: occupancy off-peak"] = mean([v["off_peak"] for v in lines.values() if v["off_peak"] is not None])
    for g, v in m["traffic_counts"].items():
        out[f"{g}: share GEH < 5"] = v["geh_below_5"]
    return out


def main():
    parser = argparse.ArgumentParser(description="The manuscript's Sec. 4.5 measures from the simulation's files.")
    parser.add_argument("--demand", nargs="+", default=[str(S.DEMAND)], help="demand folder(s), e.g. one per seed")
    folders = [Path(p).resolve() for p in parser.parse_args().demand]
    results = [headline(measure(f)) for f in folders]
    if len(results) > 1:
        print(f"\n################ across {len(results)} demand folders: mean (SD) ################")
        for key in dict.fromkeys(k for r in results for k in r):
            xs = [r[key] for r in results if r.get(key) is not None]
            if xs:
                sd = f"{statistics.stdev(xs):.3f}" if len(xs) > 1 else "-"
                print(f"  {key:60s} {statistics.mean(xs):8.3f} ({sd})  n={len(xs)}")


if __name__ == "__main__":
    main()
