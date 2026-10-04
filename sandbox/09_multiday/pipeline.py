"""The five-step execution pipeline, looped over several days.

  1. Trip extraction .............. logic
  2. Candidate mode preparation ... logic  (estimation.py)
  3. Mode Selection ............... LLM     (llm_stub.py)
  4. SUMO input conversion ........ logic
  5. Execution + outcome recording  simulation

Day N's outcomes update the habit weights that shape day N+1's choices.
"""
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci
import traci.constants as tc

import estimation
import llm_stub

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")

# ===== params.yaml stand-ins ==============================================
N_DAYS = 3
DAY_LENGTH_S = 10800           # compressed day for the sandbox (real: 86400)
HEADWAY_S = 300
LOOKAHEAD_M = 150.0
BOARDING_TIME_PER_PAX = 3.0
ALIGHTING_TIME_PER_PAX = 2.0
DISRUPTION_THRESHOLD = 1.30
RANDOM_SEED = 42

# A scenario condition, active from day 2 onwards (Phase 10: disruptions
# start only after a configured number of baseline days).
SCENARIO = {"name": "congestion", "first_day": 2, "edge": "B1C1",
            "from_s": 1500, "to_s": 4000, "reduced_speed": 2.0,
            "reason": "corridor_congestion"}

# Each agent's daily schedule: (clock time, location key).
# Trip extraction rule: a trip whenever two consecutive entries differ.
SCHEDULE = [(1800, "home"), (5400, "work"), (9000, "home")]

AGENTS = json.load(open(os.path.join(HERE, "agents.json")))


def log(msg):
    print(msg)


# ---------------------------------------------------------- step 1
def extract_trips(agent_id, agent, day):
    """A trip exists whenever consecutive schedule entries are at different
    locations. It departs at the earlier entry's time."""
    trips = []
    for i, (t, loc) in enumerate(SCHEDULE[:-1]):
        nxt_t, nxt_loc = SCHEDULE[i + 1]
        if loc == nxt_loc:
            continue
        trips.append({
            "trip_id": "%s_D%02d_T%02d" % (agent_id, day, len(trips) + 1),
            "agent_id": agent_id, "day": day,
            "origin_xy": agent["%s_xy" % loc],
            "dest_xy": agent["%s_xy" % nxt_loc],
            "departure_time_s": t,
            "trip_type": "%s_to_%s" % (loc, nxt_loc),
        })
    return trips


# ---------------------------------------------------------- step 4
def to_route_xml(day, plans, path):
    """Turn selections into a .rou.xml. Departures MUST be sorted."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<routes>"]
    for rid, edges in estimation.JEEPNEY_ROUTES.items():
        lines.append('    <route id="r_%s" edges="%s"/>' % (rid, " ".join(edges)))
    for rid in estimation.JEEPNEY_ROUTES:
        lines.append('    <flow id="%s" type="jeepney" route="r_%s" line="%s" '
                     'begin="0" end="%d" period="%d"/>'
                     % (rid, rid, rid, DAY_LENGTH_S, HEADWAY_S))

    entries = []
    for p in sorted(plans, key=lambda x: x["trip"]["departure_time_s"]):
        trip, alt, depart = p["trip"], p["alt"], p["trip"]["departure_time_s"]
        aid, tid = trip["agent_id"], trip["trip_id"]
        o = estimation.snap(*trip["origin_xy"])
        d = estimation.snap(*trip["dest_xy"])
        if alt["mode"] == "walk":
            entries.append((depart,
                '    <person id="%s" type="pedestrian" depart="%d" departPos="%.1f">\n'
                '        <walk from="%s" to="%s" arrivalPos="%.1f"/>\n'
                '        <param key="trip_id" value="%s"/>\n'
                '    </person>' % (tid, depart, o[1], o[0], d[0], d[1], tid)))
        elif alt["mode"] in ("bike", "car"):
            vtype = "bike" if alt["mode"] == "bike" else "car"
            entries.append((depart,
                '    <trip id="%s" type="%s" depart="%d" from="%s" to="%s" '
                'departPos="%.1f" arrivalPos="%.1f">\n'
                '        <param key="agent_id" value="%s"/>\n'
                '        <param key="trip_id" value="%s"/>\n'
                '    </trip>' % (tid, vtype, depart, o[0], d[0], o[1], d[1], aid, tid)))
        else:  # jeepney: walk -> ride -> walk
            b, a = alt["boarding"], alt["alighting"]
            entries.append((depart,
                '    <person id="%s" type="pedestrian" depart="%d" departPos="%.1f">\n'
                '        <walk from="%s" to="%s" arrivalPos="%.1f"/>\n'
                '        <ride from="%s" to="%s" lines="%s" arrivalPos="%.1f"/>\n'
                '        <walk from="%s" to="%s" arrivalPos="%.1f"/>\n'
                '        <param key="trip_id" value="%s"/>\n'
                '    </person>'
                % (tid, depart, o[1], o[0], b["edge_id"], b["pos_m"],
                   b["edge_id"], a["edge_id"], alt["route_id"], a["pos_m"],
                   a["edge_id"], d[0], d[1], tid)))

    lines.extend(e[1] for e in sorted(entries, key=lambda x: x[0]))
    lines.append("</routes>")
    open(path, "w").write("\n".join(lines) + "\n")


def write_cfg(day, day_dir):
    cfg = os.path.join(day_dir, "day.sumocfg")
    open(cfg, "w").write(
        '<?xml version="1.0" encoding="UTF-8"?>\n<configuration>\n'
        '    <input>\n'
        '        <net-file value="%s"/>\n'
        '        <additional-files value="%s"/>\n'
        '        <route-files value="day.rou.xml"/>\n'
        '    </input>\n'
        '    <time><begin value="0"/><end value="%d"/><step-length value="1"/></time>\n'
        '    <output>\n'
        '        <tripinfo-output value="tripinfo.xml"/>\n'
        '        <tripinfo-output.write-unfinished value="true"/>\n'
        '        <stop-output value="stops.xml"/>\n'
        '    </output>\n'
        '    <report><no-step-log value="true"/><no-warnings value="true"/></report>\n'
        '</configuration>\n'
        % (os.path.join(HERE, "city.net.xml"),
           os.path.join(HERE, "vtypes.add.xml"), DAY_LENGTH_S))
    return cfg


# ---------------------------------------------------------- step 5
def run_day(day, cfg, registry):
    """One SUMO run with the jeepney controller active."""
    traci.start(["sumo", "-c", cfg, "--no-step-log", "--seed", str(RANDOM_SEED)])

    # Offsets along each ROUTE, so lookahead can span edges. A commuter
    # standing 12 m past a junction is unreachable if you only look at the
    # jeepney's current edge - it is already going too fast to brake.
    route_cum = {}
    for rid, edges in estimation.JEEPNEY_ROUTES.items():
        cum, total = [], 0.0
        for eid in edges:
            cum.append(total)
            total += estimation.net.getEdge(eid).getLength()
        route_cum[rid] = dict(zip(edges, cum))

    def route_offset(line, edge, pos):
        """Distance from the start of the route, or None if edge not on it."""
        base = route_cum.get(line, {}).get(edge)
        return None if base is None else base + pos

    for tid, r in registry.items():
        r["board_offset"] = route_offset(r["line"], r["board_edge"], r["board_pos"])
        r["alight_offset"] = route_offset(r["line"], r["alight_edge"], r["alight_pos"])

    by_line = defaultdict(list)
    for tid, r in registry.items():
        by_line[r["line"]].append(tid)

    requested, exposed, pass_bys = set(), set(), defaultdict(int)
    disruption_on = (day >= SCENARIO["first_day"])
    t = 0
    while traci.simulation.getMinExpectedNumber() > 0 and t < DAY_LENGTH_S:
        traci.simulationStep()
        t += 1

        if disruption_on and t == SCENARIO["from_s"]:
            for lane in estimation.net.getEdge(SCENARIO["edge"]).getLanes():
                traci.lane.setMaxSpeed(lane.getID(), SCENARIO["reduced_speed"])
            log("    t=%-5d scenario ON  %s @ %s" % (t, SCENARIO["name"], SCENARIO["edge"]))
        if disruption_on and t == SCENARIO["to_s"]:
            for lane in estimation.net.getEdge(SCENARIO["edge"]).getLanes():
                traci.lane.setMaxSpeed(lane.getID(),
                                       estimation.net.getEdge(SCENARIO["edge"]).getSpeed())
            log("    t=%-5d scenario OFF" % t)

        waiting, riding = set(), {}
        for pid in traci.person.getIDList():
            stage, veh = traci.person.getStage(pid).type, traci.person.getVehicle(pid)
            if stage == tc.STAGE_DRIVING and veh == "":
                waiting.add(pid)
            elif veh:
                riding[pid] = veh
                if disruption_on and SCENARIO["from_s"] <= t < SCENARIO["to_s"]:
                    if traci.vehicle.getRoadID(veh) == SCENARIO["edge"]:
                        exposed.add(pid)

        for vid in traci.vehicle.getIDList():
            line = traci.vehicle.getLine(vid)
            if not line:
                continue
            edge = traci.vehicle.getRoadID(vid)
            if edge.startswith(":"):
                continue
            pos = traci.vehicle.getLanePosition(vid)
            occ, cap = traci.vehicle.getPersonNumber(vid), traci.vehicle.getPersonCapacity(vid)

            # A jeepney physically cannot stop closer than its braking
            # distance. Below this, it must pass and let the next one take
            # the commuter - exactly what a real driver does.
            v = traci.vehicle.getSpeed(vid)
            brake_m = v * v / (2.0 * max(traci.vehicle.getDecel(vid), 0.1)) + 5.0

            here = route_offset(line, edge, pos)
            if here is None:
                continue

            def in_range(target_offset):
                if target_offset is None:
                    return False
                return brake_m < (target_offset - here) <= LOOKAHEAD_M

            # keyed by (edge, pos) now, because a stop may be on a LATER edge
            stops = defaultdict(lambda: [0, 0, []])
            for tid in by_line.get(line, ()):
                r = registry[tid]
                if tid in waiting and in_range(r["board_offset"]):
                    s = stops[(r["board_edge"], r["board_pos"])]
                    s[0] += 1
                    s[2].append(tid)
                if riding.get(tid) == vid and in_range(r["alight_offset"]):
                    stops[(r["alight_edge"], r["alight_pos"])][1] += 1

            for (stop_edge, stop_pos), (n_board, n_alight, who) in sorted(stops.items()):
                key = (vid, stop_edge, stop_pos)
                if key in requested:
                    continue
                seats = cap - occ
                if n_alight == 0 and seats <= 0:
                    for tid in who:
                        pass_bys[tid] += 1
                    requested.add(key)
                    continue
                n_board = min(n_board, seats)
                dwell = n_board * BOARDING_TIME_PER_PAX + n_alight * ALIGHTING_TIME_PER_PAX
                try:
                    traci.vehicle.setStop(vid, stop_edge, pos=stop_pos,
                                          laneIndex=_bus_lane(stop_edge), duration=dwell)
                    requested.add(key)
                except traci.TraCIException as exc:
                    # could not be scheduled (e.g. braking) - let the next
                    # jeepney handle it rather than killing the run
                    log("    t=%-5d could not stop %s @ %s/%.0f: %s"
                        % (t, vid, stop_edge, stop_pos, exc))
                    continue

                # CRITICAL: SUMO only boards a person if the vehicle ALREADY
                # has a stop scheduled at their alighting edge (unless that
                # edge happens to be the end of the route). So the alighting
                # stop must be registered NOW, not after they board.
                for tid in who[:n_board]:
                    r = registry[tid]
                    akey = (vid, r["alight_edge"], r["alight_pos"])
                    if akey in requested:
                        continue
                    try:
                        traci.vehicle.setStop(
                            vid, r["alight_edge"], pos=r["alight_pos"],
                            laneIndex=_bus_lane(r["alight_edge"]),
                            duration=ALIGHTING_TIME_PER_PAX)
                        requested.add(akey)
                    except traci.TraCIException as exc:
                        log("    t=%-5d could not pre-book alight %s @ %s/%.0f: %s"
                            % (t, vid, r["alight_edge"], r["alight_pos"], exc))
    traci.close()
    return exposed, pass_bys


def _bus_lane(edge_id, vclass="bus"):
    for lane in estimation.net.getEdge(edge_id).getLanes():
        if lane.allows(vclass):
            return lane.getIndex()
    return 0


def record_outcomes(day, day_dir, plans, exposed, pass_bys):
    """Parse SUMO output into trip outcome records (Part D.4)."""
    root = ET.parse(os.path.join(day_dir, "tripinfo.xml")).getroot()
    by_id = {}
    for p in root.findall("personinfo"):
        by_id[p.get("id")] = ("person", p)
    for v in root.findall("tripinfo"):
        by_id[v.get("id")] = ("vehicle", v)

    outcomes = []
    for pl in plans:
        tid, alt = pl["trip"]["trip_id"], pl["alt"]
        est = alt["estimated_total_time_s"]
        kind_rec = by_id.get(tid)
        if kind_rec is None:
            outcomes.append({"trip_id": tid, "agent_id": pl["trip"]["agent_id"],
                             "day": day, "realized_mode": alt["mode"],
                             "completed": False, "delay_ratio": None,
                             "disrupted": True, "disruption_reason": "did_not_complete",
                             "estimated_total_time_s": est})
            continue
        kind, rec = kind_rec
        actual = float(rec.get("duration"))
        # SUMO only simulates the driving. The access and parking time in the
        # estimate are real time the agent spends, so add them back or the
        # comparison is apples-to-oranges.
        if alt["mode"] == "car":
            actual += estimation.P["car_access_time"] + estimation.P["car_parking_time"]
        if kind == "person":
            stages = list(rec)
            ride = next((s for s in stages if s.tag == "ride"), None)
            wait = float(ride.get("waitingTime")) if ride is not None else 0.0
            in_veh = float(ride.get("duration")) if ride is not None else actual
        else:
            wait, in_veh = 0.0, actual
        ratio = actual / est if est else None
        outcomes.append({
            "trip_id": tid, "agent_id": pl["trip"]["agent_id"], "day": day,
            "realized_mode": alt["mode"], "route_id": alt.get("route_id"),
            "actual_departure_s": float(rec.get("depart")),
            "actual_total_time_s": actual,
            "actual_waiting_time_s": wait,
            "actual_in_vehicle_time_s": in_veh,
            "distance_m": alt["distance_m"], "cost_php": alt["cost_php"],
            "estimated_total_time_s": est,
            "delay_ratio": round(ratio, 3),
            "disrupted": bool(ratio > DISRUPTION_THRESHOLD) or tid in exposed,
            "disrupted_by_ratio": bool(ratio > DISRUPTION_THRESHOLD),
            "disrupted_by_condition": tid in exposed,
            "disruption_reason": SCENARIO["reason"] if tid in exposed else None,
            "full_pass_bys_experienced": pass_bys.get(tid, 0),
            "completed": True,
        })
    return outcomes


# ---------------------------------------------------------------- the loop
def main():
    if os.path.isdir(RUNS):
        shutil.rmtree(RUNS)
    os.makedirs(RUNS)

    habits = {aid: dict(a["habit_weights"]) for aid, a in AGENTS.items()}
    flagged_agent_days = []
    all_outcomes = []

    for day in range(1, N_DAYS + 1):
        day_dir = os.path.join(RUNS, "day_%02d" % day)
        os.makedirs(day_dir)
        log("\n" + "=" * 74)
        log("DAY %d   scenario: %s"
            % (day, SCENARIO["name"] if day >= SCENARIO["first_day"] else "baseline"))
        log("=" * 74)

        # steps 1-3 need the routing SUMO instance
        traci.start(["sumo", "-c", os.path.join(HERE, "routing.sumocfg"), "--no-step-log"])
        plans, registry = [], {}
        for aid, agent in AGENTS.items():
            trips = extract_trips(aid, agent, day)                    # step 1
            with_alts = {}
            for trip in trips:                                        # step 2
                alts, _ = estimation.alternatives_for(trip, agent)
                with_alts[trip["trip_id"]] = alts
            sel, fell_back = llm_stub.select_with_fallback(            # step 3
                aid, day, with_alts, habits[aid], log,
                misbehave_on=(2,) if aid == "A002" else (),
                persist_on=(3,) if aid == "A003" else ())
            if fell_back:
                flagged_agent_days.append((aid, day))
            chosen = {s["trip_id"]: s for s in sel["selections"]}
            for trip in trips:
                s = chosen.get(trip["trip_id"])
                if not s:
                    continue
                alt = next(a for a in with_alts[trip["trip_id"]]
                           if a["mode"] == s["mode"]
                           and a.get("route_id") == s.get("route_id"))
                plans.append({"trip": trip, "alt": alt})
                if alt["mode"] == "jeepney":
                    registry[trip["trip_id"]] = {
                        "line": alt["route_id"],
                        "board_edge": alt["boarding"]["edge_id"],
                        "board_pos": alt["boarding"]["pos_m"],
                        "alight_edge": alt["alighting"]["edge_id"],
                        "alight_pos": alt["alighting"]["pos_m"]}
        traci.close()

        for pl in plans:
            log("  %-16s %-8s %-12s est %5.0f s"
                % (pl["trip"]["trip_id"], pl["alt"]["mode"],
                   pl["alt"].get("route_id") or "-",
                   pl["alt"]["estimated_total_time_s"]))

        to_route_xml(day, plans, os.path.join(day_dir, "day.rou.xml"))  # step 4
        cfg = write_cfg(day, day_dir)
        cwd = os.getcwd()
        os.chdir(day_dir)
        exposed, pass_bys = run_day(day, "day.sumocfg", registry)       # step 5
        os.chdir(cwd)

        outcomes = record_outcomes(day, day_dir, plans, exposed, pass_bys)
        json.dump(outcomes, open(os.path.join(day_dir, "trip_outcomes.json"), "w"),
                  indent=2)
        all_outcomes.extend(outcomes)

        log("  %-16s %-8s %8s %8s %7s %s"
            % ("trip", "mode", "est", "actual", "ratio", "disrupted"))
        for o in outcomes:
            log("  %-16s %-8s %8.0f %8.0f %7s %s"
                % (o["trip_id"], o["realized_mode"], o["estimated_total_time_s"],
                   o.get("actual_total_time_s") or -1,
                   o["delay_ratio"] if o["delay_ratio"] else "-",
                   "YES" if o["disrupted"] else ""))

        # end-of-day reflection -> habits for tomorrow
        for aid in AGENTS:
            mine = [o for o in outcomes if o["agent_id"] == aid and o["completed"]]
            if mine:
                habits[aid] = llm_stub.update_habits(habits[aid], mine)
        log("  habits after day %d:" % day)
        for aid, h in habits.items():
            log("    %s  %s" % (aid, {k: v for k, v in h.items() if v > 0}))

    json.dump({"outcomes": all_outcomes,
               "flagged_agent_days": flagged_agent_days,
               "final_habits": habits},
              open(os.path.join(RUNS, "summary.json"), "w"), indent=2)
    log("\n" + "=" * 74)
    log("%d days, %d trip outcomes, %d flagged agent-days"
        % (N_DAYS, len(all_outcomes), len(flagged_agent_days)))
    if flagged_agent_days:
        log("flagged: %s" % ", ".join("%s/day%d" % x for x in flagged_agent_days))


if __name__ == "__main__":
    main()
