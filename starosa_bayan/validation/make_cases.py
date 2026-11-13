"""Scripted test agents: a fixed day for checking the simulation before the LLM is connected
(manuscript Sec. 4.4.3.3, "Simulation Validation Before LLM Integration").

Finds homes and places on the current map for a fixed set of cases, and writes them as a day's
demand that no LLM touches, into validation/cases/:
  agents.json          the test agents: a home and what the household owns (no persona)
  schedule_day_1.json  where and when (the schedule format of demand/README.md)
  modes_day_1.json     the mode, and for a jeepney the line, of every trip (the modes format)
  cases.json           what each case tests and what validate.py expects of each trip
The files are committed, so every validation runs the same agents. Rerun this only when the map,
the lines or the places change, then python validation/validate.py.

The cases. Without background traffic the hour does not matter, so they run at night and the run
is short:
  capacity   CAPACITY agents (one jeepney's seats) wait at one spot of the longest line from
             00:00, before its first jeepney gets there: all of them board it, and it leaves full.
             PROBES agents wait further along: the full jeepney must pass them, and a later one
             take them.
  line       one agent per JEEPNEY_LINES key: out on one direction at 02:00, back on the other at
             03:00, so every line carries someone.
  walk, bike, car   one agent each, there at 02:00 and back at 03:00.
  delayed    a car trip at 02:00 whose route validate.py slows down on purpose (a variable speed
             sign): it must come out disrupted, while the car case, the same kind of trip, must not.

    python validation/make_cases.py
"""

import json
import math
import os
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
os.environ["STAROSA_BAYAN_DEMAND"] = str(CASES)      # before settings: the test agents' folder
sys.path.insert(0, str(HERE.parent))

import settings as S                                   # noqa: E402
from convert_demand import Planner, home_id, in_area   # noqa: E402

MADE_BY = "validation/make_cases.py (scripted test agents, no LLM)"
AGE = "30 - 34"            # every test agent: an adult, who may drive (CAR_DRIVER_MIN_AGE)
CAPACITY = int(S.JEEPNEY["personCapacity"])
PROBES = 3
GROUP_SPOT_M = 2000.0      # capacity: the group's spot at least this far along the line, so they wait
                           # there before its first jeepney (leaving the start at 0 s) arrives
GAP_M = 600.0              # capacity: the probes' spot at least this far on, and the drop-off after it
SHORT_WALK_M = 150.0       # capacity: walks to the line at most this long, so everyone is there in time
LINE_RIDE_M = 1000.0       # line cases: at least this much ride each way
WALK_M = (500.0, 1200.0)   # walk case: the walk's length
BIKE_M = (1500.0, 4000.0)  # bike case: the ride's length
CAR_M = (2000.0, 5000.0)   # car and delayed cases: the drive's length
SLOW_LENGTH_M = 300.0      # delayed case: slow at least this much of its route, away from both ends ...
SLOW_MARGIN = 1.5          # ... enough for this many times the delay the disruption rule needs
TRIES = 60                 # homes tried per case


def home_candidates(p, rng):
    """[(home lat/lon, home place)] on the home streets inside the study area, shuffled. The home
    is given as lat/lon, like a real agent's, and placed as convert_demand does (home_place)."""
    out = []
    for edge_id in sorted(p.streets):
        lon, lat = p.net.convertXY2LonLat(*p.streets[edge_id][2])
        if in_area(p.net, lon, lat) is None:
            continue
        home = {"lat": round(lat, 6), "lon": round(lon, 6)}
        place = p.home_place(home)
        if place:
            out.append((home, place))
    rng.shuffle(out)
    return out


def nearest_first(origin, locations, lo, hi):
    """Locations lo..hi metres from origin in a straight line, nearest first."""
    near = sorted((math.dist(origin[2], place[2]), lid, place) for lid, place in locations)
    return [(lid, place) for d, lid, place in near if lo <= d <= hi]


def find_walk(p, homes, locations):
    for home, h in homes[:TRIES]:
        for lid, l in nearest_first(h, locations, 0.6 * WALK_M[0], WALK_M[1])[:10]:
            out, _ = p.leg("walk", h, l)
            back, _ = p.leg("walk", l, h)
            if out and back and WALK_M[0] <= out["walk_m"] <= WALK_M[1] and back["walk_m"] <= WALK_M[1]:
                return home, lid, out, back
    raise SystemExit(f"No walk case: no home within {WALK_M[0]:.0f}-{WALK_M[1]:.0f} m on foot of a place")


def find_vehicle(p, mode, span, homes, locations):
    """A home and place a mode can make there and back, the drive in span."""
    for home, h in homes[:TRIES]:
        for lid, l in nearest_first(h, locations, 0.6 * span[0], span[1])[:10]:
            out, _ = p.leg(mode, h, l, at_house=(True, False))
            if not out or not span[0] <= out["length_m"] <= span[1]:
                continue
            back, _ = p.leg(mode, l, h, at_house=(False, True))
            if back:
                return home, lid, out, back
    raise SystemExit(f"No {mode} case: no home with a place {span[0]:.0f}-{span[1]:.0f} m away by {mode}")


def spots(p, line, places, toward, max_walk=S.MAX_WALK):
    """[(spot, key, place)] of the places with a spot on line within max_walk on foot (a spot is
    (edge, pos, route offset, walk_m, walk_s)), by route offset."""
    out = []
    for key, place in places:
        if not any(d <= max_walk for d, _ in p.grids[line].near(place[2])):
            continue                                   # no spot within max_walk even in a straight line
        s = p.spot(line, place, toward)
        if s and s[3] <= max_walk:
            out.append((s, key, place))
    return sorted(out, key=lambda x: x[0][2])


def find_line(p, out_line, back_line, homes, locations):
    """A home and place: out on out_line and back on back_line, at least LINE_RIDE_M each way."""
    boards = spots(p, out_line, [(i, h) for i, (_, h) in enumerate(homes[:400])], toward=True)
    alights = spots(p, out_line, locations, toward=False)
    for b, i, h in boards:
        for a, lid, l in reversed(alights):
            if a[2] - b[2] < LINE_RIDE_M:
                break
            out, _ = p.leg("jeepney", h, l, line=out_line)
            back, _ = p.leg("jeepney", l, h, line=back_line)
            if out and back and back["ride_m"] >= LINE_RIDE_M:
                return homes[i][0], lid, out, back
    raise SystemExit(f"No line case for {out_line} and {back_line}")


def find_capacity(p, line, homes, locations):
    """The group's home, the probes' home and their shared drop-off on line: the group's spot at
    least GROUP_SPOT_M along it, the probes' at least GAP_M after, the drop-off GAP_M after that."""
    short = spots(p, line, [(i, h) for i, (_, h) in enumerate(homes)], toward=True, max_walk=SHORT_WALK_M)
    alights = spots(p, line, locations, toward=False)
    for g, gi, gh in short:
        if g[2] < GROUP_SPOT_M:
            continue
        for q, qi, qh in short:
            if q[2] < g[2] + GAP_M or q[0] == g[0]:
                continue
            for a, lid, l in alights:
                if a[2] < q[2] + GAP_M:
                    continue
                leg_g, _ = p.leg("jeepney", gh, l, line=line)
                leg_q, _ = p.leg("jeepney", qh, l, line=line)
                if leg_g and leg_q and leg_g["board"][:2] == g[:2] and leg_q["board"][:2] == q[:2]:
                    return homes[gi][0], homes[qi][0], lid, leg_g, leg_q
    raise SystemExit(f"No capacity case on {line}")


def slow_stretch(p, route, avoid):
    """The run of route edges nearest its middle, at least SLOW_LENGTH_M long, none at either end
    or in avoid: [edge ids], or None."""
    inner = route[1:-1]
    best = None
    for i in range(len(inner)):
        run, length = [], 0.0
        for e in inner[i:]:
            if e in avoid:
                break
            run.append(e)
            length += p.net.getEdge(e).getLength()
            if length >= SLOW_LENGTH_M:
                off_middle = abs(i + len(run) / 2 - len(inner) / 2)
                if best is None or off_middle < best[0]:
                    best = (off_middle, list(run))
                break
    return best[1] if best else None


def trip_id(agent_id, k):
    return f"{agent_id}_D01_T{k:02d}"


def main():
    rng = random.Random(S.SEED)
    with Planner() as p:
        homes = home_candidates(p, rng)
        locations = sorted((lid, loc["place"]) for lid, loc in p.locations.items())
        print(f"{len(homes)} home streets inside the area, {len(locations)} places, {len(p.lines)} lines")
        agents, schedules, selections, trips, cases = [], [], [], {}, {}

        def add(agent_id, case, home, legs, plan, car=False, bike=False):
            """One test agent: plan is [(location_id, end)], from home; legs the trips' (mode, leg)."""
            agents.append({"agent_id": agent_id, "case": case, "home": home, "age_group": AGE,
                           "household_car_access": car, "household_bicycle_access": bike})
            schedules.append({"agent_id": agent_id, "schedule": [
                {"location_id": loc, **({"end": end} if end else {})} for loc, end in plan]})
            chosen = []
            for k, (mode, leg, expect) in enumerate(legs, 1):
                sel = {"trip_id": trip_id(agent_id, k), "mode": mode}
                if mode == "jeepney":
                    sel["route_id"] = leg["line"]
                chosen.append(sel)
                trips[sel["trip_id"]] = {"case": case, "agent_id": agent_id, **{k2: v for k2, v in sel.items() if k2 != "trip_id"},
                                         "expect_disrupted": expect}
            selections.append({"agent_id": agent_id, "selections": chosen})

        # capacity: the longest line
        line = max(p.lines, key=lambda k: p.lines[k]["length_m"])
        g_home, q_home, drop, leg_g, leg_q = find_capacity(p, line, homes, locations)
        group = [f"V_CAP_G{i:02d}" for i in range(1, CAPACITY + 1)]
        probes = [f"V_CAP_P{i}" for i in range(1, PROBES + 1)]
        for aid in group:
            add(aid, "capacity", g_home, [("jeepney", leg_g, None)], [(home_id(aid), "00:00"), (drop, None)])
        for aid in probes:
            add(aid, "capacity", q_home, [("jeepney", leg_q, None)], [(home_id(aid), "00:00"), (drop, None)])
        cases["capacity"] = {
            "line": line, "group": group, "probes": probes, "drop_off": drop,
            "group_spot": {"edge": leg_g["board"][0], "pos": leg_g["board"][1], "route_offset_m": round(leg_g["board"][2]),
                           "walk_m": round(leg_g["board"][3])},
            "probe_spot": {"edge": leg_q["board"][0], "pos": leg_q["board"][1], "route_offset_m": round(leg_q["board"][2]),
                           "walk_m": round(leg_q["board"][3])},
            "drop_off_spot": {"edge": leg_g["alight"][0], "pos": leg_g["alight"][1],
                              "route_offset_m": round(leg_g["alight"][2])}}
        print(f"capacity: {line}, {CAPACITY} board at {round(leg_g['board'][2])} m, {PROBES} wait at "
              f"{round(leg_q['board'][2])} m, all off at {round(leg_g['alight'][2])} m ({drop})")

        # one agent per line key: out on one direction, back on the other
        cases["line"] = {}
        for key in S.JEEPNEY_LINES:
            out_line, back_line = sorted(l for l in p.lines if p.lines[l]["key"] == key)
            home, lid, out, back = find_line(p, out_line, back_line, homes, locations)
            aid = f"V_LINE_{key}"
            add(aid, "line", home, [("jeepney", out, False), ("jeepney", back, False)],
                [(home_id(aid), "02:00"), (lid, "03:00"), (home_id(aid), None)])
            cases["line"][key] = {"agent_id": aid, "out": out_line, "back": back_line, "place": lid,
                                  "ride_m": [round(out["ride_m"]), round(back["ride_m"])]}
            print(f"line {key}: {aid} {out_line} {out['ride_m']:.0f} m out, {back_line} {back['ride_m']:.0f} m back ({lid})")

        # walk, bike, car: there and back; delayed: a car trip, one way
        home, lid, out, back = find_walk(p, homes, locations)
        add("V_WALK", "walk", home, [("walk", out, False), ("walk", back, False)],
            [(home_id("V_WALK"), "02:00"), (lid, "03:00"), (home_id("V_WALK"), None)])
        cases["walk"] = {"agent_id": "V_WALK", "place": lid, "walk_m": [round(out["walk_m"]), round(back["walk_m"])]}
        print(f"walk: {out['walk_m']:.0f} m out, {back['walk_m']:.0f} m back ({lid})")
        line_edges = {e for r in p.lines.values() for e in r["edges"]}
        rng.shuffle(homes)
        home, lid, out, back = find_vehicle(p, "bike", BIKE_M, homes, locations)
        add("V_BIKE", "bike", home, [("bike", out, False), ("bike", back, False)],
            [(home_id("V_BIKE"), "02:00"), (lid, "03:00"), (home_id("V_BIKE"), None)], bike=True)
        cases["bike"] = {"agent_id": "V_BIKE", "place": lid, "ride_m": [round(out["length_m"]), round(back["length_m"])]}
        print(f"bike: {out['length_m']:.0f} m out, {back['length_m']:.0f} m back ({lid})")
        bike_edges = set(out["edges"]) | set(back["edges"])
        rng.shuffle(homes)
        home, lid, out, back = find_vehicle(p, "car", CAR_M, homes, locations)
        add("V_CAR", "car", home, [("car", out, False), ("car", back, False)],
            [(home_id("V_CAR"), "02:00"), (lid, "03:00"), (home_id("V_CAR"), None)], car=True)
        cases["car"] = {"agent_id": "V_CAR", "place": lid, "drive_m": [round(out["length_m"]), round(back["length_m"])]}
        print(f"car: {out['length_m']:.0f} m out, {back['length_m']:.0f} m back ({lid})")
        others = line_edges | bike_edges | set(out["edges"]) | set(back["edges"])
        rng.shuffle(homes)
        for home, h in homes[:TRIES]:
            try:
                d_home, d_lid, d_out, _ = find_vehicle(p, "car", CAR_M, [(home, h)], locations)
            except SystemExit:
                continue
            stretch = slow_stretch(p, d_out["edges"], others)   # nobody else drives there
            if stretch:
                break
        else:
            raise SystemExit("No delayed case: no car trip with a stretch to slow away from the other cases")
        add("V_DELAY", "delayed", d_home, [("car", d_out, True)], [(home_id("V_DELAY"), "02:00"), (d_lid, None)], car=True)
        edges = [p.net.getEdge(e) for e in stretch]
        length = sum(e.getLength() for e in edges)
        normal_s = sum(e.getLength() / e.getSpeed() for e in edges)
        needed = max(S.DISRUPTION_MIN_S, (S.DISRUPTION_RATIO - 1) * d_out["est_s"]) * SLOW_MARGIN
        speed = math.floor(length / (normal_s + needed) * 100) / 100
        cases["delayed"] = {
            "agent_id": "V_DELAY", "trip_id": trip_id("V_DELAY", 1), "place": d_lid, "drive_m": round(d_out["length_m"]),
            "estimate_s": round(d_out["est_s"]), "slow_edges": stretch,
            "slow_lanes": [l.getID() for e in edges for l in e.getLanes() if l.allows("passenger")],
            "slow_length_m": round(length), "slow_speed_ms": speed,
            "added_s": round(length / speed - normal_s)}
        print(f"delayed: {d_out['length_m']:.0f} m by car, estimate {d_out['est_s']:.0f} s; {len(stretch)} edges "
              f"({length:.0f} m) slowed to {speed} m/s, about {length / speed - normal_s:.0f} s added")

    CASES.mkdir(exist_ok=True)

    def block(items):   # one agent per line, as in demand/agents.json
        return ",\n    ".join(json.dumps(x, ensure_ascii=False) for x in items)

    def write(name, head, key, items):
        path = CASES / name
        path.write_text("{\n" + "".join(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},\n" for k, v in head.items())
                        + f'  "{key}": [\n    {block(items)}\n  ]\n}}\n', encoding="utf-8")
        json.loads(path.read_text(encoding="utf-8"))   # check it is valid JSON

    write("agents.json", {"made_by": MADE_BY, "about": "The scripted test agents: a home each, and what the "
                          "household owns. No persona: convert_demand uses the household's access."}, "agents", agents)
    write("schedule_day_1.json", {"day": 1, "made_by": MADE_BY}, "agents", schedules)
    write("modes_day_1.json", {"day": 1, "made_by": MADE_BY}, "agents", selections)
    (CASES / "cases.json").write_text(json.dumps({
        "made_by": MADE_BY,
        "about": "What each test case is for, and what validate.py expects of each trip (expect_disrupted: true, "
                 "false, or null when not checked).",
        "network": {"edges": len([e for e in p.net.getEdges() if e.getFunction() != "internal"]),
                    "lines": {l: r["length_m"] for l, r in sorted(p.lines.items())}},
        "cases": cases, "trips": trips}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(agents)} agents and {len(trips)} trips to {CASES.relative_to(HERE.parent).as_posix()}/")


if __name__ == "__main__":
    main()
