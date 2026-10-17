"""Run the prototype: jeepneys on the two highways, random commuters, background traffic.

The flag-down controller of starosa_prototype/jeepney_controller.py, extended to several lines:
  * the commuter registry comes from generated/commuters.rou.xml: each person's line (the
    ride's lines), boarding spot (where the walk before the ride ends) and alighting spot,
  * a commuter standing at the roadside flags the NEAREST jeepney of THEIR line approaching
    within LOOKAHEAD_M that has a free seat (seats promised to other flaggers count as taken)
    and can still brake in time,
  * a commuter still walking flags one too, once they walk along their line's route: the
    jeepney stops beside them and their walk ends there,
  * the boarding AND alighting stops are booked together (SUMO boards nobody otherwise),
  * one jeepney per commuter; if it passes them, or they board another, its stops are
    cancelled and the next one is flagged,
  * a full jeepney passes by.

Each run writes runs/<date_time>/: the SUMO config, gui settings, tripinfo.xml, collisions.xml
and summary.json, so runs never overwrite each other.

    python run.py                    # sumo-gui, press play
    python run.py --nogui            # headless: event log and summary
    python run.py --no-background    # without background cars and motorcycles (much faster)
    python run.py --meter            # also measure motorcycle filtering (slower)
    python run.py --end 1800         # simulated seconds (default settings.END_S)
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime

import sumolib
import traci
import traci.constants as tc

import settings as S
import traffic_meter
from make_background import trips_file
from make_commuters import kerb_edges


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


def write_run_files(run_dir, end, background, lines, net):
    routes = [S.GENERATED / "jeepney.rou.xml", S.GENERATED / "commuters.rou.xml"]
    if background:
        routes += [trips_file(v) for v in S.BACKGROUND_PER_HOUR]
    missing = [p.name for p in routes + [S.GENERATED / "vtypes.add.xml"] if not p.is_file()]
    if missing:
        raise SystemExit(f"Missing generated/{', generated/'.join(missing)}: run python build.py first")
    cfg = run_dir / "run.sumocfg"
    cfg.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="{S.NET_FILE}"/>
        <additional-files value="{S.GENERATED / 'vtypes.add.xml'}"/>
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
    </output>
    <report><no-step-log value="true"/></report>
</configuration>
""", encoding="utf-8")

    # open sumo-gui over both highways, with the sublane strips drawn and people enlarged
    xy = [p for r in lines.values() for e in r["edges"] for p in net.getEdge(e).getShape()]
    (nx0, ny0), (nx1, ny1) = net.getBBoxXY()
    x0, x1 = min(p[0] for p in xy), max(p[0] for p in xy)
    y0, y1 = min(p[1] for p in xy), max(p[1] for p in xy)
    zoom = 90 * min((nx1 - nx0) / max(x1 - x0, 1), (ny1 - ny0) / max(y1 - y0, 1))
    gui = run_dir / "gui-settings.xml"
    gui.write_text(f"""<viewsettings>
    <scheme name="sumo_track_prototype">
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
    parser = argparse.ArgumentParser(description="Run the sumo_track prototype.")
    parser.add_argument("--nogui", action="store_true", help="run headless")
    parser.add_argument("--no-background", action="store_true", help="leave out background traffic")
    parser.add_argument("--meter", action="store_true", help="measure motorcycle filtering (slower)")
    parser.add_argument("--end", type=int, default=S.END_S, help="simulated seconds")
    args = parser.parse_args()

    net = sumolib.net.readNet(str(S.NET_FILE))
    lines = json.loads((S.GENERATED / "jeepney_routes.json").read_text(encoding="utf-8"))
    commuters = load_commuters(S.GENERATED / "commuters.rou.xml", set(lines))
    for c in commuters.values():
        c["board_lane"] = stop_lane(net, c["board_edge"])
        c["alight_lane"] = stop_lane(net, c["alight_edge"])
    # where a commuter walking along their line's route may flag a jeepney (edge -> length)
    kerb = {line: {e.getID(): e.getLength() for e, _ in kerb_edges(net, r["edges"])} for line, r in lines.items()}

    run_dir = S.RUNS / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir.mkdir(parents=True)
    cfg, gui_settings = write_run_files(run_dir, args.end, not args.no_background, lines, net)
    print(f"Run folder: {run_dir.relative_to(S.HERE).as_posix()} | {len(commuters)} commuters on "
          f"{len(lines)} lines | background {'off' if args.no_background else f'x{S.BACKGROUND_SCALE:g}'}")
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
    jeepneys = {line: set() for line in lines}       # line -> its jeepneys on the network
    teleports, collisions = 0, set()
    promised = Counter()

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
        while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < args.end:
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
            for v in traci.simulation.getArrivedIDList():
                for vs in jeepneys.values():
                    vs.discard(v)
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
                    continue
                waiting.pop(pid)
                riding[pid] = veh
                times[pid].setdefault("kerb", t)
                times[pid].update(board=t, veh=veh)
                log(t, f"BOARDED  {pid} -> {veh} ({traci.vehicle.getPersonNumber(veh)}/{seats(veh)} aboard)")
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
                release_stop(stop_users, veh, *alight_stop(commuters[pid]), pid, cancel=False)
                times[pid]["alight"] = t
                log(t, f"ALIGHTED {pid} from {veh}")
            for pid in traci.simulation.getArrivedPersonIDList():
                if pid in times:
                    times[pid]["arrive"] = t
                    log(t, f"ARRIVED  {pid}")

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
                flagged = flag(pid, c, board_stop(c), t)
                if flagged:
                    log(t, f"FLAGDOWN {pid} flags {flagged[0]} ({flagged[1]:.0f} m away)")
    except traci.FatalTraCIError:
        closed = True
        print("\nsumo-gui was closed - stopping early.")

    summary = summarise(commuters, times, lines, teleports, len(collisions))
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if meter:
        print(meter.summary())
    if not closed:
        if not args.nogui:
            print("\nSimulation over - close the sumo-gui window to finish.")
        traci.close()


def summarise(commuters, times, lines, teleports, collisions):
    print("\n================ commuters ================")
    print(f"  {'line':8s} {'commuters':>9s} {'boarded':>8s} {'arrived':>8s} {'mean wait':>10s} {'mean ride':>10s}")
    out = {"lines": {}, "teleports": teleports, "collisions": collisions}
    for line in lines:
        mine = [p for p in commuters if commuters[p]["line"] == line]
        waits = [times[p]["board"] - times[p]["kerb"] for p in mine if "board" in times[p]]
        rides = [times[p]["alight"] - times[p]["board"] for p in mine if "alight" in times[p]]
        arrived = sum("arrive" in times[p] for p in mine)
        out["lines"][line] = {"commuters": len(mine), "boarded": len(waits), "arrived": arrived,
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


if __name__ == "__main__":
    main()
