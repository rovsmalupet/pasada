"""Run the multi-line setup: jeepneys on the main roads, commuters, background traffic.

The commuters come from the day's demand (format in demand/README.md): the schedule
(settings.SCHEDULE_FILE) and the option chosen for each trip (settings.MODES_FILE), both written
by the LLM, or by llm_stub.py until then. Each run first converts them with convert_demand.py into
the run's folder, so a run keeps the demand it ran (copies of both files, commuters.rou.xml and
trips.json).

Copied from ../starosa_prototype/run.py on 2026-11-03 (ported there from ../sumo_track_prototype).
It is the flag-down controller of starosa_prototype's jeepney_controller.py, extended to several
lines:
  * the commuter registry comes from the converted commuters.rou.xml, one SUMO person per trip:
    each jeepney trip's line (the ride's lines), boarding spot (where the walk before the ride
    ends) and alighting spot. Trips on foot, by bike or by car need no controller: SUMO moves
    them, and run.py only records when they leave and arrive, and compares that with the
    trip's free-flow estimate (param est_s),
  * a commuter standing at the roadside flags the NEAREST jeepney of THEIR line approaching
    within LOOKAHEAD_M that has a free seat (seats promised to other flaggers count as taken)
    and can still brake in time,
  * a commuter still walking flags one too, once they walk along their line's route: the
    jeepney stops beside them and their walk ends there,
  * the boarding AND alighting stops are booked together (SUMO boards nobody otherwise),
  * one jeepney per commuter; if it passes them, or they board another, its stops are
    cancelled and the next one is flagged,
  * a full jeepney passes by.

The run covers a day (END_S) and stops early once every commuter is home. Each run writes
runs/<date_time>/: the demand (above), the SUMO config, gui settings, tripinfo.xml,
collisions.xml, stops.xml (SUMO's record of every vehicle stop), summary.json, events.jsonl and
outcomes_day_<n>.json, so runs never overwrite each other.

events.jsonl is the event log of the manuscript (Sec. 4.4.2.1-2): one JSON object per line, in
time order, each with "t" (simulated seconds) and "event":
  * jeepneys: stop (with until, dwell_s and who boarded and alighted), boarding, alighting,
    full_pass_by, and occupancy (every OCCUPANCY_SAMPLE_S, each jeepney on the road). The
    passengers a jeepney comes in with from outside (make_background.py, background traffic only)
    board where it starts and get off at the stop run.py books for them: background true;
  * commuters: depart, waiting (at the roadside), flagdown, missed, vehicle_depart and
    vehicle_arrive (their own car or bike), arrived.
Changes SUMO made (departing, arriving, stopping, boarding, alighting) are stamped with the step
they happened in, as in tripinfo.xml and the outcomes; TraCI reports them one step later. The
controller's own decisions (flagdown, missed, full_pass_by) and the samples carry the time it
made them.

outcomes_day_<n>.json is what the LLM reflects on at the end of the day (Plan B.3 step 7, the trip
outcome of Part D.4): one record per trip that departed, grouped by agent, with what happened
against the estimate the trip was chosen by (actual times, the jeepney's parts, full jeepneys that
passed the commuter by, whether it was disrupted: DISRUPTION_RATIO, DISRUPTION_MIN_S), and the
agents whose day was left out, with why.

    python run.py                    # sumo-gui, press play
    python run.py --nogui            # headless: event log and summary
    python run.py --no-background    # without background cars, motorcycles and jeepney riders (much faster)
    python run.py --meter            # also measure motorcycle filtering (slower)
    python run.py --end 36000        # stop at 10:00 (simulated seconds; default settings.END_S)
    python run.py --demand FILE      # another schedule (default settings.SCHEDULE_FILE), or one file with modes
    python run.py --modes FILE       # another modes file (default settings.MODES_FILE)
    python run.py --additional FILE  # also load this SUMO additional file (repeatable), e.g. speed changes
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import sumolib
import traci
import traci.constants as tc

import settings as S
import traffic_meter
from make_background import LOADED_JEEPNEYS, trips_file
from convert_demand import MODES, clock, convert, kerb_edges


def log(t, msg):
    print(f"[{t:7.1f}s] {msg}")


# ------------------------------------------------------------------------------ setup
def stop_lane(net, edge_id):
    """Index of the kerb-side lane a jeepney may use. Lane 0 can be a sidewalk, where
    setStop fails."""
    for lane in net.getEdge(edge_id).getLanes():
        if lane.allows(S.JEEPNEY_VCLASS):
            return lane.getIndex()
    raise ValueError(f"no {S.JEEPNEY_VCLASS} lane on edge {edge_id}")


def load_commuters(path, lines):
    """One entry per person whose plan has a ride on one of `lines`."""
    commuters = {}
    for person in ET.parse(path).getroot().iter("person"):
        stages = list(person)
        for i, ride in enumerate(stages):
            if ride.tag == "ride" and ride.get("lines") in lines and i > 0:
                walk = stages[i - 1]
                commuters[person.get("id")] = {
                    "line": ride.get("lines"),
                    "board_edge": walk.get("to"), "board_pos": float(walk.get("arrivalPos")),
                    "alight_edge": ride.get("to"), "alight_pos": float(ride.get("arrivalPos"))}
    return commuters


def load_trips(path):
    """{person id: {agent_id, trip_id, trip_type, mode, est_s}} of every commuter trip, from the
    params convert_demand.py writes on each person."""
    trips = {}
    for person in ET.parse(path).getroot().iter("person"):
        p = {param.get("key"): param.get("value") for param in person.iter("param")}
        trips[person.get("id")] = {**p, "est_s": float(p["est_s"]), "depart_plan": float(person.get("depart"))}
    return trips


def load_riders(path, net):
    """{person id: {veh, alight_edge, alight_pos, alight_lane}}: the passengers each jeepney comes
    in with (make_background.py), aboard from its start (depart="triggered")."""
    riders = {}
    for person in ET.parse(path).getroot().iter("person"):
        ride = person.find("ride")
        riders[person.get("id")] = {"veh": ride.get("lines"), "alight_edge": ride.get("to"),
                                    "alight_pos": float(ride.get("arrivalPos")), "alight_lane": stop_lane(net, ride.get("to"))}
    return riders


def write_run_files(run_dir, end, background, lines, net, commuters_file, extra=()):
    # with the background, the jeepneys come in with passengers (make_background.py)
    routes = [LOADED_JEEPNEYS if background else S.GENERATED / "jeepney.rou.xml", commuters_file]
    if background:
        routes += [trips_file(v) for v in S.BACKGROUND_PER_HOUR]
    missing = [p.name for p in routes + [S.GENERATED / "vtypes.add.xml"] if not p.is_file()]
    if missing:
        raise SystemExit(f"Missing generated/{', generated/'.join(missing)}: run python build.py first")
    additional = [S.GENERATED / "vtypes.add.xml", *extra]
    cfg = run_dir / "run.sumocfg"
    cfg.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="{S.NET_FILE}"/>
        <additional-files value="{','.join(str(a) for a in additional)}"/>
        <route-files value="{','.join(str(r) for r in routes)}"/>
    </input>
    <time><begin value="0"/><end value="{end}"/><step-length value="1"/></time>
    <processing>
        <lateral-resolution value="{S.LATERAL_RESOLUTION}"/>
        <ignore-route-errors value="true"/>
        <collision.action value="warn"/>
        <collision.check-junctions value="true"/>
    </processing>
    <random_number><seed value="{S.SEED}"/></random_number>
    <output>
        <tripinfo-output value="tripinfo.xml"/>
        <tripinfo-output.write-unfinished value="true"/>
        <collision-output value="collisions.xml"/>
        <stop-output value="stops.xml"/>
        <stop-output.write-unfinished value="true"/>
    </output>
    <report><no-step-log value="true"/></report>
</configuration>
""", encoding="utf-8")

    # open sumo-gui over both roads, with the sublane strips drawn and people enlarged
    xy = [p for r in lines.values() for e in r["edges"] for p in net.getEdge(e).getShape()]
    (nx0, ny0), (nx1, ny1) = net.getBBoxXY()
    x0, x1 = min(p[0] for p in xy), max(p[0] for p in xy)
    y0, y1 = min(p[1] for p in xy), max(p[1] for p in xy)
    zoom = 90 * min((nx1 - nx0) / max(x1 - x0, 1), (ny1 - ny0) / max(y1 - y0, 1))
    gui = run_dir / "gui-settings.xml"
    gui.write_text(f"""<viewsettings>
    <scheme name="starosa_bayan">
        <edges laneShowBorders="1" showSublanes="1"/>
        <vehicles vehicleQuality="2" showBlinker="1"/>
        <persons person_exaggeration="4" person_minSize="2"/>
    </scheme>
    <delay value="20"/>
    <viewport zoom="{zoom:.0f}" x="{(x0 + x1) / 2:.1f}" y="{(y0 + y1) / 2:.1f}"/>
</viewsettings>
""", encoding="utf-8")
    return cfg, gui


# ---------------------------------------------------------------------- stop bookkeeping
def seats(veh):
    return traci.vehicletype.getPersonCapacity(traci.vehicle.getTypeID(veh))


def distance_ahead(veh, edge, pos, lane):
    """Metres veh still drives to reach edge@pos; negative or invalid once it is past or
    the edge is not ahead on its route."""
    return traci.vehicle.getDrivingDistance(veh, edge, pos, lane)


def reserve_stop(stop_users, veh, edge, pos, lane, pid):
    key = (veh, edge, pos)
    if key not in stop_users:
        traci.vehicle.setStop(veh, edge, pos=pos, laneIndex=lane, duration=S.DWELL_S)
        stop_users[key] = set()
    stop_users[key].add(pid)


def release_stop(stop_users, veh, edge, pos, lane, pid, cancel):
    """pid no longer needs that stop; with cancel=True the stop is deleted from the jeepney
    unless someone else still needs it (re-sending a not-yet-reached stop with duration 0
    deletes it)."""
    key = (veh, edge, pos)
    users = stop_users.get(key)
    if users is None:
        return
    users.discard(pid)
    if users:
        return
    del stop_users[key]
    if cancel:
        try:
            traci.vehicle.setStop(veh, edge, pos=pos, laneIndex=lane, duration=0)
        except traci.TraCIException:
            pass   # jeepney already gone


# -------------------------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description="Run the starosa_bayan multi-line setup.")
    parser.add_argument("--nogui", action="store_true", help="run headless")
    parser.add_argument("--no-background", action="store_true", help="leave out background traffic")
    parser.add_argument("--meter", action="store_true", help="measure motorcycle filtering (slower)")
    parser.add_argument("--end", type=int, default=S.END_S, help="simulated seconds")
    parser.add_argument("--demand", default=str(S.SCHEDULE_FILE),
                        help="the schedule (step 1), or one file with the modes in it (JSON)")
    parser.add_argument("--modes", default=str(S.MODES_FILE), help="the modes file (step 3)")
    parser.add_argument("--additional", action="append", default=[],
                        help="also load this SUMO additional file (repeatable), e.g. speed changes")
    args = parser.parse_args()
    extra = [Path(a).resolve() for a in args.additional]
    missing = [str(a) for a in extra if not a.is_file()]
    if missing:
        raise SystemExit(f"No additional file at {', '.join(missing)}")

    run_dir = S.RUNS / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir.mkdir(parents=True)
    try:
        # the demand -> this run's SUMO persons
        commuters_file, day, left_out = convert(args.demand, run_dir, args.modes)
    except SystemExit:
        if not any(run_dir.iterdir()):
            run_dir.rmdir()                                 # no empty run folders for a bad file
        raise
    records = json.loads((run_dir / "trips.json").read_text(encoding="utf-8"))
    net = sumolib.net.readNet(str(S.NET_FILE))
    lines = json.loads((S.GENERATED / "jeepney_routes.json").read_text(encoding="utf-8"))
    commuters = load_commuters(commuters_file, set(lines))
    trip_info = load_trips(commuters_file)
    for c in commuters.values():
        c["board_lane"] = stop_lane(net, c["board_edge"])
        c["alight_lane"] = stop_lane(net, c["alight_edge"])
    # where a commuter walking along their line's route may flag a jeepney (edge -> length)
    kerb = {line: {e.getID(): e.getLength() for e, _ in kerb_edges(net, r["edges"])} for line, r in lines.items()}

    cfg, gui_settings = write_run_files(run_dir, args.end, not args.no_background, lines, net, commuters_file, extra)
    riders = {} if args.no_background else load_riders(LOADED_JEEPNEYS, net)
    riders_of = defaultdict(list)                    # jeepney -> the riders it comes in with
    for pid, r in riders.items():
        riders_of[r["veh"]].append(pid)
    aboard = {}                                      # rider -> jeepney, until they get off
    rider_count = Counter()
    per_mode = Counter(t["mode"] for t in trip_info.values())
    agents = {t["agent_id"] for t in trip_info.values()}
    print(f"Run folder: {run_dir.relative_to(S.HERE).as_posix()} | {len(agents)} commuters, {len(trip_info)} "
          "trips (" + ", ".join(f"{m} {per_mode[m]}" for m in MODES) + f"), {len(lines)} jeepney lines | "
          "background " + ("off" if args.no_background else f"x{S.BACKGROUND_SCALE:g}") + f" | until {args.end} s")
    if args.nogui:
        traci.start([sumolib.checkBinary("sumo"), "-c", str(cfg)])
    else:
        traci.start([sumolib.checkBinary("sumo-gui"), "-c", str(cfg), "-g", str(gui_settings)])
        print("sumo-gui is open: press the green Run (play) button.")
    meter = traffic_meter.TrafficMeter(S.LATERAL_RESOLUTION, log=log) if args.meter else None
    reach = float(traci.simulation.getOption("ride.stop-tolerance"))   # SUMO boards within this

    waiting, assigned, riding = dict(commuters), {}, {}
    stop_users, full_logged = {}, set()
    times = {pid: {} for pid in commuters}
    trips = {i: {} for i in trip_info}               # every commuter trip: depart and arrive time
    jeepneys = {line: set() for line in lines}       # line -> its jeepneys on the network
    teleports, collisions = 0, set()
    promised = Counter()
    done = 0                                         # commuter trips arrived
    t = 0.0                                          # the time the run has reached
    events = []                                      # the event log (events.jsonl)
    stopped = {}                                     # jeepney -> its stop under way (a "stop" event)
    line_of = {}                                     # jeepney -> its line

    def event(time, kind, **fields):
        events.append({"t": int(time), "event": kind, **fields})

    def board_stop(c):
        return c["board_edge"], c["board_pos"], c["board_lane"]

    def alight_stop(c):
        return c["alight_edge"], c["alight_pos"], c["alight_lane"]

    def walking_spot(pid, c):
        """(edge, pos, lane) to stop at for a commuter still walking, if they walk along their
        line's route: the nearest position KERB_MARGIN clear of the edge ends, if within reach."""
        if traci.person.getStage(pid).type != tc.STAGE_WALKING:
            return None
        edge = traci.person.getRoadID(pid)
        if edge not in kerb[c["line"]]:
            return None
        pos = traci.person.getLanePosition(pid)
        stop = min(max(pos, S.KERB_MARGIN), kerb[c["line"]][edge] - S.KERB_MARGIN)
        if abs(stop - pos) > reach - 1.0:
            return None
        return edge, round(stop, 1), stop_lane(net, edge)

    def flag(pid, c, board, t):
        """Book the nearest suitable jeepney of pid's line for board. (veh, distance) or None."""
        approaching = []
        for veh in jeepneys[c["line"]]:
            dist = distance_ahead(veh, *board)
            if 0 <= dist <= S.LOOKAHEAD_M:
                approaching.append((dist, veh))
        for dist, veh in sorted(approaching):
            if distance_ahead(veh, *alight_stop(c)) <= dist:
                continue                       # the drop-off must lie further along its route
            if traci.vehicle.getPersonNumber(veh) + promised[veh] >= seats(veh):
                if (veh, pid) not in full_logged:
                    full_logged.add((veh, pid))
                    log(t, f"FULL     {veh} passes {pid}")
                    event(t, "full_pass_by", vehicle=veh, person=pid, edge=board[0], pos=board[1],
                          aboard=traci.vehicle.getPersonNumber(veh), promised=promised[veh])
                continue
            speed = traci.vehicle.getSpeed(veh)
            if dist <= speed * traci.vehicle.getTau(veh) + speed ** 2 / (2 * traci.vehicle.getDecel(veh)):
                continue                       # too close to brake
            try:
                reserve_stop(stop_users, veh, *board, pid)
            except traci.TraCIException:
                continue
            try:
                reserve_stop(stop_users, veh, *alight_stop(c), pid)
            except traci.TraCIException:
                release_stop(stop_users, veh, *board, pid, cancel=True)
                continue
            assigned[pid] = veh
            promised[veh] += 1
            return veh, dist
        return None

    closed = False
    try:
        # stop at --end, or once every commuter trip has arrived (checked after a whole step)
        while (traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < args.end
               and done < len(trips)):
            traci.simulationStep()
            t = traci.simulation.getTime()
            if meter:
                meter.step(t)
            teleports += traci.simulation.getStartingTeleportNumber()
            collisions.update((c.collider, c.victim) for c in traci.simulation.getCollisions())
            for v in traci.simulation.getDepartedIDList():
                line = traci.vehicle.getLine(v)
                if line in jeepneys:
                    jeepneys[line].add(v)
                    line_of[v] = line
                    if riders_of.get(v):                              # it comes in with riders: book their stops
                        edge, pos = traci.vehicle.getRoadID(v), round(traci.vehicle.getLanePosition(v), 1)
                        for pid in riders_of[v]:
                            r = riders[pid]
                            try:
                                reserve_stop(stop_users, v, r["alight_edge"], r["alight_pos"], r["alight_lane"], pid)
                            except traci.TraCIException:
                                rider_count["no stop booked"] += 1
                            aboard[pid] = v
                            rider_count["came in"] += 1
                            event(t - 1, "boarding", vehicle=v, person=pid, edge=edge, pos=pos, aboard=len(riders_of[v]),
                                  background=True)
                elif v.endswith("_veh") and v[:-4] in trips:          # a commuter's own car or bike
                    event(t - 1, "vehicle_depart", vehicle=v, person=v[:-4])
            # jeepneys stop only where run.py booked a stop, so ask SUMO only while one is booked.
            # Before anyone's getting off: a rider whose ride ends there gets off the same step
            for v in traci.simulation.getStopStartingVehiclesIDList() if stop_users else ():
                if v in line_of:
                    stopped[v] = {"t": int(t - 1), "event": "stop", "vehicle": v, "line": line_of[v],
                                  "edge": traci.vehicle.getRoadID(v), "pos": round(traci.vehicle.getLanePosition(v), 1),
                                  "until": None, "dwell_s": None, "boarded": [], "alighted": []}
            for i in traci.simulation.getDepartedPersonIDList():
                if i in trips:
                    trips[i]["depart"] = t
                    event(t - 1, "depart", person=i, mode=trip_info[i]["mode"])
            for v in traci.simulation.getArrivedIDList():
                for vs in jeepneys.values():
                    vs.discard(v)
                if v.endswith("_veh") and v[:-4] in trips:
                    event(t - 1, "vehicle_arrive", vehicle=v, person=v[:-4])
            for i in traci.simulation.getArrivedPersonIDList():
                if i in trips:
                    trips[i]["arrive"] = t
                    done += 1
                    event(t - 1, "arrived", person=i)
                    if trip_info[i]["mode"] != "jeepney":
                        log(t, f"ARRIVED  {i} ({trip_info[i]['mode']}, {(t - trips[i]['depart']) / 60:.1f} min)")
                elif i in aboard:                                     # a rider got off: their trip ends there
                    veh, r = aboard.pop(i), riders[i]
                    release_stop(stop_users, veh, r["alight_edge"], r["alight_pos"], r["alight_lane"], i, cancel=False)
                    event(t - 1, "alighting", vehicle=veh, person=i, edge=r["alight_edge"], pos=r["alight_pos"],
                          background=True)
                    rider_count["got off"] += 1
                    if veh in stopped:
                        stopped[veh]["alighted"].append(i)
            persons = set(traci.person.getIDList())

            # --- 1. person state: at the roadside, boarded, alighted, arrived ---
            for pid in list(waiting):
                if pid not in persons:
                    continue
                c = waiting[pid]
                veh = traci.person.getVehicle(pid)
                if not veh:
                    if "kerb" not in times[pid] and traci.person.getStage(pid).type == tc.STAGE_DRIVING:
                        times[pid]["kerb"] = t
                        log(t, f"WAITING  {pid} at {c['board_edge']}@{c['board_pos']}")
                        event(t - 1, "waiting", person=pid, line=c["line"], edge=c["board_edge"], pos=c["board_pos"])
                    continue
                waiting.pop(pid)
                riding[pid] = veh
                times[pid].setdefault("kerb", t)
                times[pid].update(board=t, veh=veh)
                aboard = traci.vehicle.getPersonNumber(veh)
                log(t, f"BOARDED  {pid} -> {veh} ({aboard}/{seats(veh)} aboard)")
                event(t - 1, "boarding", vehicle=veh, person=pid, edge=c["board_edge"], pos=c["board_pos"], aboard=aboard)
                if veh in stopped:
                    stopped[veh]["boarded"].append(pid)
                flagged = assigned.pop(pid, None)
                if flagged == veh:
                    release_stop(stop_users, veh, *board_stop(c), pid, cancel=False)
                else:   # got on a jeepney stopped here for someone else
                    if flagged is not None:
                        release_stop(stop_users, flagged, *board_stop(c), pid, cancel=True)
                        release_stop(stop_users, flagged, *alight_stop(c), pid, cancel=True)
                    try:
                        reserve_stop(stop_users, veh, *alight_stop(c), pid)
                    except traci.TraCIException:
                        pass
            for pid in list(riding):
                if pid in persons and traci.person.getVehicle(pid):
                    continue
                veh = riding.pop(pid)
                c = commuters[pid]
                release_stop(stop_users, veh, *alight_stop(c), pid, cancel=False)
                times[pid]["alight"] = t
                log(t, f"ALIGHTED {pid} from {veh}")
                event(t - 1, "alighting", vehicle=veh, person=pid, edge=c["alight_edge"], pos=c["alight_pos"])
                if veh in stopped:
                    stopped[veh]["alighted"].append(pid)
            for pid in traci.simulation.getArrivedPersonIDList():
                if pid in times:
                    times[pid]["arrive"] = t
                    log(t, f"ARRIVED  {pid}")
            for v in traci.simulation.getStopEndingVehiclesIDList() if stopped else ():   # after boarding: it happens during the stop
                if v in stopped:
                    stop = stopped.pop(v)
                    stop.update(until=int(t - 1), dwell_s=int(t - 1) - stop["t"])
                    events.append(stop)

            # --- 2. flag-downs ---
            promised = Counter(assigned.values())
            for pid, c in waiting.items():
                if "kerb" not in times[pid]:
                    spot = walking_spot(pid, c) if pid in persons else None
                    flagged = flag(pid, c, spot, t) if spot else None
                    if flagged is None:
                        continue
                    veh, dist = flagged
                    traci.person.removeStage(pid, 0)          # stop walking: wait here
                    c["board_edge"], c["board_pos"], c["board_lane"] = spot
                    times[pid]["kerb"] = t
                    log(t, f"FLAGDOWN {pid} flags {veh} while walking ({dist:.0f} m away)")
                    event(t - 1, "waiting", person=pid, line=c["line"], edge=spot[0], pos=spot[1])
                    event(t, "flagdown", person=pid, vehicle=veh, edge=spot[0], pos=spot[1], distance_m=round(dist),
                          walking=True)
                    continue
                if pid in assigned:
                    veh = assigned[pid]
                    if veh in jeepneys[c["line"]] and distance_ahead(veh, *board_stop(c)) >= 0:
                        continue                               # still on its way
                    del assigned[pid]
                    promised[veh] -= 1
                    release_stop(stop_users, veh, *board_stop(c), pid, cancel=False)
                    release_stop(stop_users, veh, *alight_stop(c), pid, cancel=True)
                    log(t, f"MISSED   {veh} passed {pid}; flagging the next one")
                    event(t, "missed", person=pid, vehicle=veh)
                flagged = flag(pid, c, board_stop(c), t)
                if flagged:
                    log(t, f"FLAGDOWN {pid} flags {flagged[0]} ({flagged[1]:.0f} m away)")
                    event(t, "flagdown", person=pid, vehicle=flagged[0], edge=c["board_edge"], pos=c["board_pos"],
                          distance_m=round(flagged[1]), walking=False)

            # --- 3. occupancy samples ---
            if int(t) % S.OCCUPANCY_SAMPLE_S == 0:
                for line, vs in jeepneys.items():
                    for v in sorted(vs):
                        event(t, "occupancy", vehicle=v, line=line, persons=traci.vehicle.getPersonNumber(v),
                              edge=traci.vehicle.getRoadID(v), pos=round(traci.vehicle.getLanePosition(v), 1))
    except traci.FatalTraCIError:
        closed = True
        print("\nsumo-gui was closed - stopping early.")
    if done == len(trips) and not closed:
        log(traci.simulation.getTime(), "every commuter is home: stopping")
    events += stopped.values()                       # stops still under way: until null
    events.sort(key=lambda e: e["t"])                # stable: same-second events keep their order
    with open(run_dir / "events.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in events)

    summary = summarise(commuters, times, lines, teleports, len(collisions))
    summary["events"] = dict(Counter(e["event"] for e in events))
    if riders:
        rider_count["still aboard"] = len(aboard)
        summary["riders"] = dict(rider_count)
        print(f"  jeepney riders (background): {rider_count['came in']} came in aboard, {rider_count['got off']} got off, "
              f"{len(aboard)} still aboard when the run stopped, {rider_count['no stop booked']} stops not booked")
    summary["modes"] = summarise_modes(trip_info, trips, t)
    passes = Counter(pid for _, pid in full_logged)          # full jeepneys that passed each commuter
    summary["outcomes"] = write_outcomes(run_dir, day, records, trips, times, passes, left_out, t,
                                         whole_day=done == len(trips) or t >= S.END_S)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if meter:
        print(meter.summary())
    if not closed:
        if not args.nogui:
            print("\nSimulation over - close the sumo-gui window to finish.")
        traci.close()


def summarise(commuters, times, lines, teleports, collisions):
    print("\n================ jeepney trips ================")
    print(f"  {'line':8s} {'trips':>9s} {'boarded':>8s} {'arrived':>8s} {'mean wait':>10s} {'mean ride':>10s}")
    out = {"lines": {}, "teleports": teleports, "collisions": collisions}
    for line in lines:
        mine = [p for p in commuters if commuters[p]["line"] == line]
        waits = [times[p]["board"] - times[p]["kerb"] for p in mine if "board" in times[p]]
        rides = [times[p]["alight"] - times[p]["board"] for p in mine if "alight" in times[p]]
        arrived = sum("arrive" in times[p] for p in mine)
        out["lines"][line] = {"trips": len(mine), "boarded": len(waits), "arrived": arrived,
                              "mean_wait_s": round(sum(waits) / len(waits)) if waits else None,
                              "mean_ride_s": round(sum(rides) / len(rides)) if rides else None}
        fmt = lambda xs: f"{sum(xs) / len(xs) / 60:.1f} min" if xs else "-"
        print(f"  {line:8s} {len(mine):9d} {len(waits):8d} {arrived:8d} {fmt(waits):>10s} {fmt(rides):>10s}")
    total = len(commuters)
    boarded = sum(v["boarded"] for v in out["lines"].values())
    arrived = sum(v["arrived"] for v in out["lines"].values())
    print(f"  all      {total:9d} {boarded:8d} {arrived:8d}")
    print(f"  teleports: {teleports}   collisions: {collisions}  (SUMO warns and lets both go on)")
    return out


def summarise_modes(trip_info, trips, until):
    """Every commuter trip, door to door, against its free-flow estimate; and commuters whose
    next trip was due (by the demand file) before the one before had arrived. Only next trips
    due before `until`, the time the run reached, count: later ones were never due."""
    print("\n================ all commuter trips, door to door ================")
    print(f"  {'mode':8s} {'trips':>6s} {'arrived':>8s} {'mean trip':>10s} {'longest':>10s} {'vs estimate':>12s}")
    out = {}
    for mode in MODES:
        mine = [i for i, info in trip_info.items() if info["mode"] == mode]
        done = [i for i in mine if "arrive" in trips[i]]
        durations = [trips[i]["arrive"] - trips[i]["depart"] for i in done]
        ratios = [d / max(trip_info[i]["est_s"], 1) for i, d in zip(done, durations)]
        out[mode] = {"trips": len(mine), "arrived": len(done),
                     "mean_trip_s": round(sum(durations) / len(durations)) if durations else None,
                     "mean_vs_estimate": round(sum(ratios) / len(ratios), 2) if ratios else None}
        mean = f"{sum(durations) / len(durations) / 60:.1f} min" if durations else "-"
        longest = f"{max(durations) / 60:.1f} min" if durations else "-"
        ratio = f"x{sum(ratios) / len(ratios):.2f}" if ratios else "-"
        print(f"  {mode:8s} {len(mine):6d} {len(done):8d} {mean:>10s} {longest:>10s} {ratio:>12s}")
    by_agent = defaultdict(list)
    for i, info in trip_info.items():
        by_agent[info["agent_id"]].append(i)
    late = []
    for agent, ids in sorted(by_agent.items()):
        ids.sort(key=lambda i: trip_info[i]["depart_plan"])
        if any(trip_info[b]["depart_plan"] < until and trips[a].get("arrive", float("inf")) > trip_info[b]["depart_plan"]
               for a, b in zip(ids, ids[1:])):
            late.append(agent)
    out["next_trip_due_before_arrival"] = late
    print(f"  commuters whose next trip was due before the one before had arrived: {len(late)}"
          + (f" ({', '.join(late[:20])}{', ...' if len(late) > 20 else ''})" if late else ""))
    return out


OUTCOME_RULES = [
    "One record per trip that departed: what happened, against the estimate the trip was chosen by "
    "(estimated_total_time_s, on empty roads). Times in seconds; departure and arrival also as HH:MM.",
    "delay_ratio is actual_total_time_s / estimated_total_time_s. Jeepney trips also have their actual parts, "
    "as in the options: access (walking to where they flagged the jeepney), waiting, in-vehicle, egress.",
    "full_pass_bys_experienced: full jeepneys of the line that came by while the commuter waited, or walked "
    "along the route, and could not take them.",
    f"disrupted: the trip took at least {S.DISRUPTION_RATIO:g}x its estimate and at least "
    f"{S.DISRUPTION_MIN_S / 60:g} min longer, or had not arrived when the run ended (disruption_reason says which). "
    "It is null for a trip still underway when a run stopped before the day was over (whole_day false).",
    "distance_m and cost_php are those of the chosen option.",
    "An agent with left_out had their whole day left out before the run, because a trip could not be made "
    "as chosen: they made no trips.",
]


def outcome(rec, trip, stamps, passes, until, whole_day):
    """One trip's outcome record (the plan's trip outcome, Part D.4) from its trips.json record,
    its depart and arrive times, and for a jeepney trip its times at the roadside, boarding and
    alighting (stamps). A trip still underway when a run stopped early is neither disrupted nor
    not: disrupted is null."""
    # TraCI lists who departed or arrived after the step they did it in, so every time it gave is
    # one step (1 s) later than SUMO's own (tripinfo.xml). Shifted back here; spans are unchanged
    depart, arrive = trip["depart"] - 1, trip["arrive"] - 1 if "arrive" in trip else None
    est = max(rec["estimated_total_time_s"], 1)
    total = round(arrive - depart) if arrive is not None else None
    disrupted = True
    if total is None and whole_day:
        reason = f"had not arrived when the run ended ({clock(until)})"
    elif total is None:
        disrupted, reason = None, f"still underway when the run stopped ({clock(until)})"
    elif total >= est * S.DISRUPTION_RATIO and total - est >= S.DISRUPTION_MIN_S:
        reason = f"took {total / est:.1f}x the estimate ({(total - est) / 60:.0f} min longer)"
    else:
        disrupted, reason = False, None
    t = {k: v - 1 for k, v in {"depart": trip["depart"], **stamps}.items() if k != "veh"}

    def span(a, b):
        return round(t[b] - t[a]) if a in t and b in t else None

    out = {"trip_id": rec["trip_id"], "realized_mode": rec["mode"], "route_id": rec["route_id"],
           "departure": clock(depart), "arrival": clock(arrive) if arrive is not None else None,
           "actual_departure_s": round(depart), "arrival_time_s": round(arrive) if arrive is not None else None,
           "actual_total_time_s": total, "estimated_total_time_s": rec["estimated_total_time_s"],
           "delay_ratio": round(total / est, 2) if total is not None else None}
    if rec["mode"] == "jeepney":
        out.update(actual_access_time_s=span("depart", "kerb"), actual_waiting_time_s=span("kerb", "board"),
                   actual_in_vehicle_time_s=span("board", "alight"), actual_egress_time_s=span("alight", "arrive"))
    else:
        out["actual_waiting_time_s"] = 0
    out.update(full_pass_bys_experienced=passes[rec["sumo_person_id"]], distance_m=rec["distance_m"],
               cost_php=rec["cost_php"], disrupted=disrupted, disruption_reason=reason,
               completed=arrive is not None)
    return out


def write_outcomes(run_dir, day, records, trips, times, passes, left_out, until, whole_day):
    """outcomes_day_<day>.json in the run folder: the outcome of every trip that departed, grouped
    by agent, and the agents whose day was left out. Prints and returns its totals."""
    by_agent = defaultdict(list)
    for rec in records:
        pid = rec["sumo_person_id"]
        if "depart" in trips[pid]:
            by_agent[rec["agent_id"]].append(outcome(rec, trips[pid], times.get(pid, {}), passes, until, whole_day))
    agents = [({"agent_id": a}, by_agent[a]) for a in sorted(by_agent)]
    agents += [({"agent_id": a, "left_out": why}, []) for a, why in sorted(left_out.items())]
    agents.sort(key=lambda x: x[0]["agent_id"])
    done = [o for _, outs in agents for o in outs]
    head = {"day": day, "made_by": f"run.py, run {run_dir.name}",
            "about": "What happened on each trip of the day, for the end-of-day reflection.", "rules": OUTCOME_RULES,
            "whole_day": whole_day, "run_ended": clock(until),
            "trips_not_departed": len(records) - len(done)}

    def agent_text(who, outs):          # one trip per line, as in the choices file
        body = ",\n            ".join(json.dumps(o, ensure_ascii=False) for o in outs)
        return (f'    {json.dumps(who, ensure_ascii=False)[:-1]}, "trips": ['
                + (f"\n            {body}\n        " if outs else "") + "]}")

    path = run_dir / f"outcomes_day_{day}.json"
    path.write_text("{\n" + ",\n".join(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in head.items())
                    + ',\n  "agents": [\n' + ",\n".join(agent_text(w, o) for w, o in agents) + "\n  ]\n}\n",
                    encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))   # check it is valid JSON

    disrupted = Counter(o["realized_mode"] for o in done if o["disrupted"])
    out = {"file": path.name, "trips": len(done), "completed": sum(o["completed"] for o in done),
           "disrupted": dict(disrupted), "full_pass_bys": sum(o["full_pass_bys_experienced"] for o in done),
           "agents_left_out": len(left_out), "whole_day": whole_day}
    print(f"\n================ trip outcomes ({path.name}) ================")
    print(f"  {len(done)} trips departed, {out['completed']} arrived; disrupted: {sum(disrupted.values())}"
          + (" (" + ", ".join(f"{m} {disrupted[m]}" for m in MODES if disrupted[m]) + ")" if disrupted else "")
          + f"; full jeepneys passing someone by: {out['full_pass_bys']}; agents left out: {len(left_out)}")
    if not whole_day:
        print(f"  the run stopped at {clock(until)}, before the day was over: {len(done) - out['completed']} trips "
              f"still underway (disrupted: null), {len(records) - len(done)} not departed (no record)")
    return out


if __name__ == "__main__":
    main()
