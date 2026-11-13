"""Check the simulation with the scripted test agents, before the LLM is connected (manuscript
Sec. 4.4.3.3, "Simulation Validation Before LLM Integration"): its five checks, each PASS or FAIL
with the numbers behind it.

Runs the test day of validation/cases/ (make_cases.py) through the same steps as a real day, with
the scripted modes in place of the LLM's answers:
  candidate preparation   make_choices.py on the schedule: every scripted choice must be offered;
  conversion, execution   run.py --nogui --no-background, with the delayed case's stretch slowed
                          by a variable speed sign (cases.json: slow_lanes, slow_speed_ms);
  records                 trips.json, outcomes_day_1.json, events.jsonl, and SUMO's stops.xml and
                          tripinfo.xml.
The checks:
  1 environment connectivity  every activity location and home (the test agents' and demand/'s)
                              lies on a walkable edge, reachable on foot both ways from a central
                              place; every line is a continuous route jeepneys may drive; every
                              scripted trip is offered its mode and line, and jeepney riders board
                              and alight on their line, KERB_MARGIN clear of the edge ends
  2 jeepney operation         jeepneys drive their whole route, never rerouted; they stop only where
                              someone gets on or off, for at least DWELL_S and boardingDuration per
                              person; never carry more than personCapacity; a full one passes the
                              people waiting further on, who take a later one; the event log
                              matches SUMO's own record of every stop
  3 commuter movement         every trip goes through its mode's states in order and arrives
  4 disruption detection      the slowed trip is disrupted; the trips expected normal are not
  5 record completeness       every trip has its trip record and an outcome with every field

Writes validation_report.md into the run folder and validation/report.md; exits 1 if a check fails.

    python validation/validate.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import sumolib

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
os.environ["STAROSA_BAYAN_DEMAND"] = str(CASES)      # before settings: the test agents' folder
sys.path.insert(0, str(HERE.parent))

import settings as S                                                    # noqa: E402
from convert_demand import Planner, kerb_edges, load_agents, walk     # noqa: E402
from make_routes import can_drive                                       # noqa: E402

TOL_S = 1                  # s: SUMO's own times against the event log's
ROUTE_TOL_M = 1.0          # m: a jeepney's driven length against its line's
PARTS_TOL_S = 2            # s: a jeepney trip's parts against its door-to-door time
RECORD_FIELDS = ("trip_id", "agent_id", "day", "origin_location_id", "destination_location_id", "departure",
                 "departure_time_s", "trip_type", "mode", "route_id", "distance_m", "estimated_total_time_s",
                 "cost_php", "sumo_person_id")
OUTCOME_FIELDS = ("trip_id", "realized_mode", "route_id", "departure", "arrival", "actual_departure_s",
                  "arrival_time_s", "actual_total_time_s", "estimated_total_time_s", "delay_ratio",
                  "actual_waiting_time_s", "full_pass_bys_experienced", "distance_m", "cost_php", "disrupted",
                  "disruption_reason", "completed")
JEEPNEY_PARTS = ("actual_access_time_s", "actual_waiting_time_s", "actual_in_vehicle_time_s", "actual_egress_time_s")
STATES = {"walk": ["depart", "arrived"], "jeepney": ["depart", "waiting", "boarding", "alighting", "arrived"],
          "bike": ["depart", "vehicle_depart", "vehicle_arrive", "arrived"],
          "car": ["depart", "vehicle_depart", "vehicle_arrive", "arrived"]}
STATE_EVENTS = {e for seq in STATES.values() for e in seq}


class Check:
    def __init__(self, number, name):
        self.number, self.name, self.items = number, name, []

    def item(self, ok, text):
        self.items.append((bool(ok), text))

    @property
    def ok(self):
        return all(ok for ok, _ in self.items)


def listed(xs, n=5):
    xs = list(xs)
    return ", ".join(map(str, xs[:n])) + (f" ... ({len(xs)} in all)" if len(xs) > n else "")


def run(cmd, log):
    """Run one of the folder's scripts with the test agents' demand; stop if it fails."""
    result = subprocess.run([sys.executable, *cmd], cwd=S.HERE, text=True, encoding="utf-8", errors="replace",
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=os.environ.copy())
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed:\n" + "\n".join(result.stdout.splitlines()[-15:]) + f"\n(all of it in {log})")
    return result.stdout


# ------------------------------------------------------------------------------ check 1, static
def check_network(check, cases):
    """Places and homes reachable on foot; lines continuous. Uses SUMO's router, like the converter."""
    demand_agents = S.HERE / "demand" / "agents.json"
    real = json.loads(demand_agents.read_text(encoding="utf-8"))["agents"] if demand_agents.is_file() else []
    with Planner() as p:
        check.item(not sum(p.dropped.values()),
                   f"{len(p.locations)} activity locations on a walkable edge within LOCATION_SNAP_M "
                   f"({S.LOCATION_SNAP_M:.0f} m); {sum(p.dropped.values())} dropped")
        xs = [loc["place"][2] for loc in p.locations.values()]
        mid = (sum(x for x, _ in xs) / len(xs), sum(y for _, y in xs) / len(xs))
        ref_id, ref = min(((lid, loc["place"]) for lid, loc in p.locations.items()),
                          key=lambda x: (x[1][2][0] - mid[0]) ** 2 + (x[1][2][1] - mid[1]) ** 2)

        def unreachable(places):
            return [k for k, pl in places if walk(ref[0], ref[1], pl[0], pl[1]) is None
                    or walk(pl[0], pl[1], ref[0], ref[1]) is None]

        bad = unreachable((lid, loc["place"]) for lid, loc in p.locations.items())
        check.item(not bad, f"every activity location reachable on foot from {ref_id} (the most central) and back"
                   + (f"; not: {listed(bad)}" if bad else ""))
        test_agents = load_agents()
        for label, agents in (("test agents'", list(test_agents.values())), ("demand/ agents'", real)):
            homes = [(a["agent_id"], p.home_place(a["home"])) for a in agents]
            missing = [aid for aid, h in homes if h is None]
            bad = unreachable((aid, h) for aid, h in homes if h is not None)
            check.item(agents and not missing and not bad,
                       f"{len(agents)} {label} homes on a home street within HOME_SNAP_M and reachable on foot both ways"
                       + (f"; no street: {listed(missing)}" if missing else "") + (f"; unreachable: {listed(bad)}" if bad else ""))
        for line, r in sorted(p.lines.items()):
            edges = [p.net.getEdge(e) for e in r["edges"]]
            closed = [e.getID() for e in edges if not e.allows(S.JEEPNEY_VCLASS)]
            gaps = [f"{a.getID()}->{b.getID()}" for a, b in zip(edges, edges[1:]) if not can_drive(a, b, S.JEEPNEY_VCLASS)]
            kerb = kerb_edges(p.net, r["edges"])
            check.item(not closed and not gaps and kerb,
                       f"line {line}: {len(edges)} edges, {r['length_m'] / 1000:.2f} km, continuous for {S.JEEPNEY_VCLASS}; "
                       f"{len(kerb)} edges people can wait on" + (f"; closed: {listed(closed)}" if closed else "")
                       + (f"; no connection: {listed(gaps)}" if gaps else ""))
        kerbs = {line: {e.getID(): e.getLength() for e, _ in kerb_edges(p.net, r["edges"])} for line, r in p.lines.items()}
    return kerbs


# ------------------------------------------------------------------------------- the outputs
def read_persons(path):
    """{person id: {params, board/alight (edge, pos) for a jeepney, route edges for an own vehicle}}."""
    persons, routes = {}, {}
    root = ET.parse(path).getroot()
    for v in root.iter("vehicle"):
        routes[v.get("id")] = v.find("route").get("edges").split()
    for person in root.iter("person"):
        info = {prm.get("key"): prm.get("value") for prm in person.iter("param")}
        stages = [s for s in person if s.tag in ("walk", "ride", "stop")]
        rides = [i for i, s in enumerate(stages) if s.tag == "ride"]
        if info["mode"] == "jeepney":
            ride = stages[rides[0]]
            info.update(line=ride.get("lines"), board=(stages[rides[0] - 1].get("to"), float(stages[rides[0] - 1].get("arrivalPos"))),
                        alight=(ride.get("to"), float(ride.get("arrivalPos"))))
        elif info["mode"] in ("car", "bike"):
            info["route"] = routes.get(f"{person.get('id')}_veh", [])
        persons[person.get("id")] = info
    return persons


def read_stops(path):
    """SUMO's record of every jeepney stop: [{vehicle, edge, pos, started, ended, on, off}]."""
    stops = []
    for s in ET.parse(path).getroot().iter("stopinfo"):
        if s.get("type") != "jeepney":
            continue
        stops.append({"vehicle": s.get("id"), "edge": s.get("lane").rsplit("_", 1)[0], "pos": float(s.get("pos")),
                      "started": float(s.get("started")), "ended": float(s.get("ended", -1)),
                      "on": int(s.get("loadedPersons", 0)), "off": int(s.get("unloadedPersons", 0))})
    return stops


# ------------------------------------------------------------------------------- checks 1 to 5
def check_offers(check, cases, choices, persons, kerbs):
    offered = {t["trip_id"]: t["options"] for a in choices["agents"] for t in a["trips"]}
    missing = []
    for trip_id, want in cases["trips"].items():
        opts = offered.get(trip_id, [])
        if not any(o["mode"] == want["mode"] and o.get("route_id") == want.get("route_id") for o in opts):
            missing.append(f"{trip_id} ({want['mode']} {want.get('route_id') or ''})".strip())
    check.item(not missing, f"all {len(cases['trips'])} scripted trips offered their mode (and line) by make_choices.py"
               + (f"; not: {listed(missing)}" if missing else ""))
    jeep = {pid: info for pid, info in persons.items() if info["mode"] == "jeepney"}
    off_kerb = []
    for pid, info in jeep.items():
        for edge, pos in (info["board"], info["alight"]):
            length = kerbs[info["line"]].get(edge)
            if length is None or not S.KERB_MARGIN - 0.1 <= pos <= length - S.KERB_MARGIN + 0.1:
                off_kerb.append(f"{pid} at {edge}@{pos}")
    check.item(jeep and not off_kerb, f"{len(jeep)} jeepney trips board and alight on their line's edges, "
               f"{S.KERB_MARGIN:.0f} m clear of the ends" + (f"; not: {listed(off_kerb)}" if off_kerb else ""))


def junction_length(net, a, b):
    """(shortest, longest) metres through the junction from edge a into edge b: its internal
    lanes, over the lane connections there are."""
    lengths = []
    for conn in net.getEdge(a).getConnections(net.getEdge(b)):
        length, via = 0.0, conn.getViaLaneID()
        while via:
            lane = net.getLane(via)
            length += lane.getLength()
            via = next((c.getViaLaneID() for c in lane.getOutgoing() if c.getViaLaneID()), None)
        lengths.append(length)
    return min(lengths, default=0.0), max(lengths, default=0.0)


def check_jeepneys(check, cases, lines, events, stops, tripinfo, outcomes, persons):
    capacity = int(S.JEEPNEY["personCapacity"])
    boarding_s = float(S.JEEPNEY["boardingDuration"])
    # route following: SUMO's routeLength counts the junctions too, and starts at the jeepney's front
    net = sumolib.net.readNet(str(S.NET_FILE), withInternal=True)
    expected = {}                      # line -> (shortest, longest) driven length, by the lanes it may take
    for line, r in lines.items():
        through = [junction_length(net, a, b) for a, b in zip(r["edges"], r["edges"][1:])]
        lanes = [[l.getLength() for l in net.getEdge(e).getLanes() if l.allows(S.JEEPNEY_VCLASS)] for e in r["edges"]]
        start = min(float(S.JEEPNEY["length"]), min(lanes[0]))   # its front, unless the first edge is shorter
        expected[line] = tuple(sum(f(x) for x in lanes) + sum(f(j) for j in through) - start for f in (min, max))
    done = [ti for ti in tripinfo if ti.get("vType") == "jeepney" and float(ti.get("arrival", -1)) >= 0]
    off = []
    for ti in done:
        line = ti.get("id").rsplit(".", 1)[0]
        edges, (lo, hi) = lines[line]["edges"], expected[line]
        if (not lo - ROUTE_TOL_M <= float(ti.get("routeLength")) <= hi + ROUTE_TOL_M or ti.get("rerouteNo", "0") != "0"
                or ti.get("departLane").rsplit("_", 1)[0] != edges[0] or ti.get("arrivalLane").rsplit("_", 1)[0] != edges[-1]):
            off.append(f"{ti.get('id')} {float(ti.get('routeLength')):.0f} m")
    per_line = Counter(ti.get("id").rsplit(".", 1)[0] for ti in done)
    check.item(done and not off and set(per_line) == set(lines),
               f"{len(done)} jeepneys finished their whole route (" + ", ".join(f"{l} {n}" for l, n in sorted(per_line.items()))
               + f"): from the line's first edge to its last, never rerouted, driven length within {ROUTE_TOL_M:g} m "
               "of the line's edges and junctions, by the lanes it may take (" + ", ".join(
                   f"{l} {lo:.0f}" + (f"-{hi:.0f}" if hi - lo >= 0.5 else "") + " m" for l, (lo, hi) in sorted(expected.items())) + ")"
               + (f"; not: {listed(off)}" if off else ""))
    # stops: only for someone, long enough
    empty = [f"{s['vehicle']} at {s['edge']} {s['started']:.0f} s" for s in stops if s["on"] + s["off"] == 0]
    check.item(stops and not empty, f"{len(stops)} jeepney stops (SUMO's stops.xml), every one with someone getting on or off"
               + (f"; not: {listed(empty)}" if empty else ""))
    short = [f"{s['vehicle']} {s['ended'] - s['started']:.0f} s for {s['on']} on, {s['off']} off" for s in stops
             if s["ended"] >= 0 and s["ended"] - s["started"] < max(S.DWELL_S, boarding_s * s["on"]) - TOL_S]
    longest = max(stops, key=lambda s: s["on"], default=None)
    check.item(stops and not short, f"every stop lasts at least DWELL_S ({S.DWELL_S:g} s) and boardingDuration "
               f"({boarding_s:g} s) per person boarding" + (f"; the busiest: {longest['on']} on, {longest['off']} off, "
                                                            f"{longest['ended'] - longest['started']:.0f} s" if longest else "")
               + (f"; too short: {listed(short)}" if short else ""))
    # capacity
    aboard = [e["aboard"] for e in events if e["event"] == "boarding"] + [e["persons"] for e in events if e["event"] == "occupancy"]
    check.item(aboard and max(aboard) <= capacity, f"no jeepney ever carried more than personCapacity ({capacity}): "
               f"at most {max(aboard, default=0)} aboard")
    cap = cases["cases"]["capacity"]
    by_person = defaultdict(list)
    for e in events:
        if "person" in e:
            by_person[e["person"]].append(e)
    pid_of = {info["agent_id"]: pid for pid, info in persons.items()}
    group_vehicles = {e["vehicle"] for a in cap["group"] for e in by_person[pid_of.get(a)] if e["event"] == "boarding"}
    full = next(iter(group_vehicles)) if len(group_vehicles) == 1 else None
    full_peak = max((e["aboard"] for e in events if e["event"] == "boarding" and e["vehicle"] == full), default=0)
    check.item(full and full_peak == capacity, f"capacity case: the {len(cap['group'])} agents at one spot of {cap['line']} all "
               f"boarded one jeepney ({full or ', '.join(sorted(group_vehicles))}), {full_peak} aboard: full")
    passed = []
    for a in cap["probes"]:
        pid = pid_of.get(a)
        evs = by_person[pid]
        pass_t = [e["t"] for e in evs if e["event"] == "full_pass_by" and e["vehicle"] == full]
        rode = [e for e in evs if e["event"] == "boarding"]
        stopped_there = [s for s in stops if s["vehicle"] == full and s["edge"] == persons[pid]["board"][0]]
        outcome = outcomes.get(persons[pid]["trip_id"], {})
        ok = (pass_t and rode and rode[0]["vehicle"] != full and rode[0]["t"] > pass_t[0] and not stopped_there
              and outcome.get("completed") and outcome.get("full_pass_bys_experienced", 0) >= 1)
        passed.append((ok, f"{a}: passed by {full} at {pass_t[0] if pass_t else '-'} s, boarded "
                           f"{rode[0]['vehicle'] if rode else '-'} at {rode[0]['t'] if rode else '-'} s"))
    check.item(passed and all(ok for ok, _ in passed),
               f"capacity case: the full jeepney passed the {len(cap['probes'])} agents waiting further on without "
               "stopping, logged as full_pass_by (and counted in their outcomes); a later one took them: "
               + "; ".join(text for _, text in passed))
    # the event log against SUMO's record
    logged = [e for e in events if e["event"] == "stop"]
    unmatched = [f"{s['vehicle']} {s['started']:.0f} s" for s in stops if not any(
        e["vehicle"] == s["vehicle"] and e["edge"] == s["edge"] and abs(e["t"] - s["started"]) <= TOL_S
        and (e["until"] is None and s["ended"] < 0 or e["until"] is not None and abs(e["until"] - s["ended"]) <= TOL_S)
        and len(e["boarded"]) == s["on"] and len(e["alighted"]) == s["off"] for e in logged)]
    check.item(len(logged) == len(stops) and not unmatched,
               f"event log: {len(logged)} stop events for SUMO's {len(stops)} stops, each with its times (within {TOL_S} s) "
               "and the people who got on and off" + (f"; not matched: {listed(unmatched)}" if unmatched else ""))
    outside = []
    for e in events:
        if e["event"] in ("boarding", "alighting"):
            key = "boarded" if e["event"] == "boarding" else "alighted"
            if not any(s["vehicle"] == e["vehicle"] and s["edge"] == e["edge"] and e["person"] in s[key] for s in logged):
                outside.append(f"{e['person']} {e['event']} {e['vehicle']}")
    n = sum(e["event"] in ("boarding", "alighting") for e in events)
    check.item(n and not outside, f"event log: all {n} boardings and alightings happened during a stop of that "
               "jeepney, at the person's spot" + (f"; not: {listed(outside)}" if outside else ""))


def check_movement(check, events, persons):
    by_person = defaultdict(list)
    for e in events:
        if e.get("person") in persons and e["event"] in STATE_EVENTS:
            by_person[e["person"]].append(e)
    wrong, modes = [], Counter()
    for pid, info in sorted(persons.items()):
        seq = [e["event"] for e in by_person[pid]]
        times = [e["t"] for e in by_person[pid]]
        modes[info["mode"]] += 1
        if seq != STATES[info["mode"]] or times != sorted(times):
            wrong.append(f"{pid}: {' > '.join(seq) or 'nothing'}")
    flagged = {e["person"] for e in events if e["event"] == "flagdown"}
    no_flag = [pid for pid, info in persons.items() if info["mode"] == "jeepney" and pid not in flagged]
    check.item(persons and not wrong, f"all {len(persons)} trips (" + ", ".join(f"{m} {n}" for m, n in sorted(modes.items()))
               + ") went through their mode's states in order and arrived: " + "; ".join(f"{m}: {' > '.join(s)}" for m, s in STATES.items())
               + (f". Not: {listed(wrong)}" if wrong else ""))
    check.item(not no_flag, "every jeepney rider flagged the jeepney they took" + (f"; not: {listed(no_flag)}" if no_flag else ""))


def check_disruption(check, cases, outcomes, persons):
    delayed = cases["cases"]["delayed"]
    route = next((info["route"] for info in persons.values() if info["trip_id"] == delayed["trip_id"]), [])
    check.item(set(delayed["slow_edges"]) <= set(route),
               f"the delayed trip's route still runs over the {len(delayed['slow_edges'])} slowed edges "
               f"({delayed['slow_length_m']} m at {delayed['slow_speed_ms']} m/s)"
               + ("" if set(delayed["slow_edges"]) <= set(route) else ": it changed, rerun make_cases.py"))
    for trip_id, want in sorted(cases["trips"].items()):
        if want["expect_disrupted"] is None:
            continue
        o = outcomes.get(trip_id)
        if want["expect_disrupted"]:
            check.item(o and o["disrupted"] is True,
                       f"{trip_id} ({want['case']}, slowed on purpose) is disrupted: "
                       + (f"{o['actual_total_time_s']} s against an estimate of {o['estimated_total_time_s']} s; {o['disruption_reason']}"
                          if o else "no outcome"))
    normal = [(t, outcomes.get(t)) for t, w in sorted(cases["trips"].items()) if w["expect_disrupted"] is False]
    wrong = [t for t, o in normal if not o or o["disrupted"] is not False]
    ratios = [o["delay_ratio"] for _, o in normal if o and o["delay_ratio"] is not None]
    check.item(normal and not wrong, f"the {len(normal)} trips expected normal (line, walk, bike, car cases) are not disrupted"
               + (f": delay ratios {min(ratios):.2f}-{max(ratios):.2f}" if ratios else "") + (f"; disrupted: {listed(wrong)}" if wrong else ""))


def check_records(check, cases, records, outcomes):
    by_trip = {r["trip_id"]: r for r in records}
    missing = [t for t in cases["trips"] if t not in by_trip]
    incomplete = [t for t, r in by_trip.items() if any(k not in r for k in RECORD_FIELDS)]
    check.item(not missing and not incomplete, f"{len(by_trip)} trip records (trips.json), one per scripted trip, with "
               f"every field ({', '.join(RECORD_FIELDS)})" + (f"; missing: {listed(missing)}" if missing else "")
               + (f"; incomplete: {listed(incomplete)}" if incomplete else ""))
    gaps, sums = [], []
    for trip_id in cases["trips"]:
        o = outcomes.get(trip_id)
        if o is None:
            gaps.append(f"{trip_id}: no outcome")
            continue
        jeep = o["realized_mode"] == "jeepney"
        need = OUTCOME_FIELDS + (JEEPNEY_PARTS if jeep else ())
        nullable = {"disruption_reason"} | (set() if jeep else {"route_id"})
        empty = [k for k in need if k not in o or (o[k] is None and k not in nullable)]
        if empty or not o["completed"]:
            gaps.append(f"{trip_id}: {', '.join(empty) or 'not completed'}")
        if jeep and not empty:
            parts = sum(o[k] for k in JEEPNEY_PARTS)
            if abs(parts - o["actual_total_time_s"]) > PARTS_TOL_S:
                sums.append(f"{trip_id}: parts {parts} s, total {o['actual_total_time_s']} s")
        if not empty and o["arrival_time_s"] - o["actual_departure_s"] != o["actual_total_time_s"]:
            sums.append(f"{trip_id}: arrival - departure != total")
    check.item(not gaps, f"every scripted trip has a completed outcome with every field the reflection and the "
               "evaluation need (jeepney trips also their four parts)" + (f"; not: {listed(gaps)}" if gaps else ""))
    check.item(not sums, f"outcome times add up: arrival - departure = total, and a jeepney trip's access + waiting + "
               f"in-vehicle + egress = total within {PARTS_TOL_S} s" + (f"; not: {listed(sums)}" if sums else ""))


# ----------------------------------------------------------------------------------- report
def report(checks, run_dir, cases, took):
    passed = all(c.ok for c in checks)
    lines = [f"# Simulation validation: {'PASS' if passed else 'FAIL'}", "",
             f"Scripted test agents (manuscript Sec. 4.4.3.3), run `{run_dir.relative_to(S.HERE).as_posix()}`, "
             f"{datetime.now():%Y-%m-%d %H:%M}, {took:.0f} s. Made by `python validation/validate.py`.", "",
             f"Test day: {len({t['agent_id'] for t in cases['trips'].values()})} agents, {len(cases['trips'])} trips, "
             "no background traffic (`validation/cases/`, from `make_cases.py`).", "",
             "| # | Check | Result |", "|---|---|---|"]
    lines += [f"| {c.number} | {c.name} | {'PASS' if c.ok else 'FAIL'} |" for c in checks]
    for c in checks:
        lines += ["", f"## {c.number}. {c.name}: {'PASS' if c.ok else 'FAIL'}", ""]
        lines += [f"- {'PASS' if ok else 'FAIL'}: {text}" for ok, text in c.items]
    text = "\n".join(lines) + "\n"
    (run_dir / "validation_report.md").write_text(text, encoding="utf-8")
    (HERE / "report.md").write_text(text, encoding="utf-8")
    return passed


def main():
    started = time.time()
    cases = json.loads((CASES / "cases.json").read_text(encoding="utf-8"))
    checks = [Check(1, "Environment connectivity"), Check(2, "Jeepney operation"), Check(3, "Commuter movement"),
              Check(4, "Disruption detection"), Check(5, "Record completeness")]
    print("1. Places, homes and lines ...")
    kerbs = check_network(checks[0], cases)

    print("2. Options (make_choices.py) ...")
    S.RUNS.mkdir(exist_ok=True)
    work = S.RUNS / f"validation_{datetime.now():%Y-%m-%d_%H%M%S}"
    work.mkdir()
    choices_path = work / "choices_day_1.json"
    run(["make_choices.py", str(CASES / "schedule_day_1.json"), "--out", str(choices_path)], work / "make_choices.log")
    delayed = cases["cases"]["delayed"]
    slow = work / "slow.add.xml"
    slow.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!-- validation/validate.py: the delayed case, slowed on purpose -->\n'
                    f'<additional>\n    <variableSpeedSign id="validation_delay" lanes="{" ".join(delayed["slow_lanes"])}">\n'
                    f'        <step time="0" speed="{delayed["slow_speed_ms"]}"/>\n    </variableSpeedSign>\n</additional>\n',
                    encoding="utf-8")

    print("3. The test day in SUMO (run.py) ...")
    out = run(["run.py", "--nogui", "--no-background", "--demand", str(CASES / "schedule_day_1.json"),
               "--modes", str(CASES / "modes_day_1.json"), "--additional", str(slow)], work / "run.log")
    run_dir = S.HERE / re.search(r"Run folder: (\S+)", out).group(1)
    for f in work.iterdir():                                   # keep everything in the run's folder
        shutil.move(str(f), run_dir / f.name)
    work.rmdir()

    print("4. Checks ...")
    persons = read_persons(run_dir / "commuters.rou.xml")
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    stops = read_stops(run_dir / "stops.xml")
    tripinfo = list(ET.parse(run_dir / "tripinfo.xml").getroot().iter("tripinfo"))
    outcomes = {o["trip_id"]: o for a in json.loads((run_dir / "outcomes_day_1.json").read_text(encoding="utf-8"))["agents"]
                for o in a["trips"]}
    records = json.loads((run_dir / "trips.json").read_text(encoding="utf-8"))
    choices = json.loads((run_dir / "choices_day_1.json").read_text(encoding="utf-8"))
    lines = json.loads((S.GENERATED / "jeepney_routes.json").read_text(encoding="utf-8"))

    check_offers(checks[0], cases, choices, persons, kerbs)
    check_jeepneys(checks[1], cases, lines, events, stops, tripinfo, outcomes, persons)
    check_movement(checks[2], events, persons)
    check_disruption(checks[3], cases, outcomes, persons)
    check_records(checks[4], cases, records, outcomes)

    passed = report(checks, run_dir, cases, time.time() - started)
    print()
    for c in checks:
        print(f"{c.number}. {c.name}: {'PASS' if c.ok else 'FAIL'}")
        for ok, text in c.items:
            if not ok:
                print(f"     FAIL: {text}")
    print(f"\n{'PASS' if passed else 'FAIL'} ({time.time() - started:.0f} s). Report: validation/report.md and "
          f"{run_dir.relative_to(S.HERE).as_posix()}/validation_report.md")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
