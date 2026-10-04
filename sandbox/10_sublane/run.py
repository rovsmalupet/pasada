"""Jeepneys stop at the kerb; cars squeeze past using part of the oncoming lane.

Two jobs:
  1. FLAG-DOWN CONTROLLER (same logic as sumo_prototype/jeepney_controller.py
     and sandbox 04/05/09): a commuter standing at the kerb flags the nearest
     approaching jeepney with a free seat. Its boarding AND alighting stops
     are booked together (SUMO will not board anyone otherwise - sandbox 09).
  2. OVERTAKE METER: every time a car passes a jeepney, record how far the
     car crossed the centre line. The road is straight along the x-axis, so
     a vehicle's y coordinate IS its sideways position - no SUMO bookkeeping
     needed to tell "partly in the oncoming lane" from "fully in it".

Usage:
    python run.py            # sumo-gui (starts paused - press play)
    python run.py --nogui    # headless: event log + summary only
"""
import bisect
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import sumolib
import traci
import traci.constants as tc

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "sim.sumocfg")
NET = os.path.join(HERE, "road.net.xml")
ROUTES = os.path.join(HERE, "demand.rou.xml")
GUI_SETTINGS = os.path.join(HERE, "gui-settings.xml")

# ===== params.yaml stand-ins ===============================================
LOOKAHEAD_M = 200.0      # a kerbside commuter flags jeepneys within this range
DWELL_S = 12.0           # minimum stop length; boardingDuration can extend it
PRINT_MOVING_OVERTAKES = False   # True = also log cars passing MOVING jeepneys
# ===========================================================================

net = sumolib.net.readNet(NET)


# ---------------------------------------------------------------- geometry
def lane_y(lane_id):
    return net.getLane(lane_id).getShape()[0][1]


ROAD_LANE = {"AB": "AB_1", "BA": "BA_1"}          # the non-sidewalk lanes
LANE_W = net.getLane("AB_1").getWidth()
CENTRE_Y = (lane_y("AB_1") + lane_y("BA_1")) / 2.0   # the centre line
KERB_Y = {"EB": lane_y("AB_1") - LANE_W / 2.0,       # outer edge of each lane
          "WB": lane_y("BA_1") + LANE_W / 2.0}


def direction_of(type_id, line):
    if line:
        return "EB" if line.endswith("EB") else "WB"
    return "EB" if type_id.endswith("eb") else "WB"


def encroachment(direction, y, width):
    """Metres the vehicle's LEFT side is past the centre line (0 if not).
    EB drives toward +x so its left is +y; WB drives toward -x, left is -y."""
    if direction == "EB":
        return max(0.0, (y + width / 2.0) - CENTRE_Y)
    return max(0.0, CENTRE_Y - (y - width / 2.0))


def fully_across(direction, y, width):
    """True if even the vehicle's RIGHT side is past the centre line."""
    if direction == "EB":
        return (y - width / 2.0) > CENTRE_Y
    return (y + width / 2.0) < CENTRE_Y


def kerb_gap(direction, y, width):
    """Metres between the vehicle's right side and the outer edge of its lane."""
    if direction == "EB":
        return (y - width / 2.0) - KERB_Y["EB"]
    return KERB_Y["WB"] - (y + width / 2.0)


def side_by_side(direction, xc, lc, xj, lj):
    """Do car (front at xc, length lc) and jeepney overlap lengthwise?"""
    if direction == "EB":
        return xc > xj - lj and xc - lc < xj
    return xc < xj + lj and xc + lc > xj


def car_ahead(direction, xc, lc, xj):
    return (xc - lc >= xj) if direction == "EB" else (xc + lc <= xj)


# --------------------------------------------------------------- registry
def load_commuters():
    """Boarding point = end of the walk before the ride; alighting = the ride's."""
    commuters = {}
    for person in ET.parse(ROUTES).getroot().iter("person"):
        stages = list(person)
        for i, ride in enumerate(stages):
            if ride.tag != "ride":
                continue
            walk = stages[i - 1]
            commuters[person.get("id")] = {
                "line": ride.get("lines"),
                "board_edge": walk.get("to"), "board_pos": float(walk.get("arrivalPos")),
                "alight_edge": ride.get("to"), "alight_pos": float(ride.get("arrivalPos")),
            }
    return commuters


def road_lane_index(edge_id):
    for lane in net.getEdge(edge_id).getLanes():
        if lane.allows("bus"):
            return lane.getIndex()
    raise ValueError("no bus lane on " + edge_id)


def log(t, msg):
    print("[%6.0f s] %s" % (t, msg))


# ------------------------------------------------- stop bookkeeping (shared)
def reserve_stop(stop_users, veh, edge, pos, pid):
    key = (veh, edge, pos)
    if key not in stop_users:
        traci.vehicle.setStop(veh, edge, pos=pos, laneIndex=road_lane_index(edge),
                              duration=DWELL_S)
        stop_users[key] = set()
    stop_users[key].add(pid)


def release_stop(stop_users, veh, edge, pos, pid, cancel):
    key = (veh, edge, pos)
    users = stop_users.get(key)
    if users is None:
        return
    users.discard(pid)
    if users:
        return
    del stop_users[key]
    if cancel:
        try:   # re-sending a not-yet-reached stop with duration 0 deletes it
            traci.vehicle.setStop(veh, edge, pos=pos,
                                  laneIndex=road_lane_index(edge), duration=0)
        except traci.TraCIException:
            pass


def distance_ahead(veh, edge, pos):
    return traci.vehicle.getDrivingDistance(veh, edge, pos, road_lane_index(edge))


# ===================================================================== main
def main():
    gui = "--nogui" not in sys.argv[1:]
    commuters = load_commuters()
    print("Registry: %d commuters | lane %.1f m | centre line y=%.2f | "
          "lateral resolution 0.8 m (%d sublanes per lane)"
          % (len(commuters), LANE_W, CENTRE_Y, round(LANE_W / 0.8)))

    if gui:
        traci.start(["sumo-gui", "-c", CFG, "-g", GUI_SETTINGS])
        print("sumo-gui is open: press the green Run (play) button.")
    else:
        traci.start(["sumo", "-c", CFG])
    end = traci.simulation.getEndTime()

    vtype = {}      # vehicle -> (type, direction, length, width)
    waiting, assigned, riding = dict(commuters), {}, {}
    stop_users, full_logged = {}, set()
    times = {pid: {} for pid in commuters}

    pairs = {}                    # (car, jeep) -> overtake in progress
    overtakes = []                # finished ones
    kerb_samples = []             # stopped jeepney's distance from the kerb
    teleports = collisions = 0
    peak_vehicles = 0
    last_stopped = {}             # car -> last time it stood still
    across_since = {}             # car -> when it crossed the centre line
    episodes = []                 # how long each crossing lasted (s)

    def board(c):
        return c["board_edge"], c["board_pos"]

    def alight(c):
        return c["alight_edge"], c["alight_pos"]

    closed = False
    try:
        while traci.simulation.getMinExpectedNumber() > 0:
            if 0 <= end <= traci.simulation.getTime():
                break
            traci.simulationStep()
            t = traci.simulation.getTime()

            for v in traci.simulation.getDepartedIDList():
                tid = traci.vehicle.getTypeID(v)
                line = traci.vehicle.getLine(v)
                vtype[v] = (tid, direction_of(tid, line),
                            traci.vehicle.getLength(v), traci.vehicle.getWidth(v))
                traci.vehicle.subscribe(v, [tc.VAR_POSITION, tc.VAR_STOPSTATE,
                                            tc.VAR_SPEED])
            teleports += traci.simulation.getStartingTeleportNumber()
            collisions += traci.simulation.getCollidingVehiclesNumber()

            state = traci.vehicle.getAllSubscriptionResults()
            peak_vehicles = max(peak_vehicles, len(state))
            jeeps = [v for v in state if vtype[v][0] == "jeepney"]
            cars = [v for v in state if vtype[v][0] != "jeepney"]

            # ---------------- 1. commuter state ------------------------------
            persons = set(traci.person.getIDList())
            for pid in list(waiting):
                if pid not in persons:
                    continue
                c = waiting[pid]
                veh = traci.person.getVehicle(pid)
                if not veh:
                    if ("kerb" not in times[pid]
                            and traci.person.getStage(pid).type == tc.STAGE_DRIVING):
                        times[pid]["kerb"] = t
                    continue
                waiting.pop(pid)
                riding[pid] = veh
                times[pid].setdefault("kerb", t)
                times[pid].update(board=t, veh=veh)
                log(t, "BOARDED   %s -> %s (%d aboard)"
                    % (pid, veh, traci.vehicle.getPersonNumber(veh)))
                flagged = assigned.pop(pid, None)
                if flagged == veh:
                    release_stop(stop_users, veh, *board(c), pid=pid, cancel=False)
                else:
                    if flagged is not None:
                        release_stop(stop_users, flagged, *board(c), pid=pid, cancel=True)
                        release_stop(stop_users, flagged, *alight(c), pid=pid, cancel=True)
                    try:
                        reserve_stop(stop_users, veh, *alight(c), pid=pid)
                    except traci.TraCIException:
                        pass

            for pid in list(riding):
                if pid in persons and traci.person.getVehicle(pid):
                    continue
                veh = riding.pop(pid)
                release_stop(stop_users, veh, *alight(commuters[pid]), pid=pid, cancel=False)
                times[pid]["alight"] = t
                log(t, "ALIGHTED  %s from %s" % (pid, veh))
            for pid in traci.simulation.getArrivedPersonIDList():
                if pid in times:
                    times[pid]["arrive"] = t

            # ---------------- 2. flag-downs ---------------------------------
            promised = Counter(assigned.values())
            for pid, c in waiting.items():
                if "kerb" not in times[pid]:
                    continue
                if pid in assigned:
                    veh = assigned[pid]
                    if veh in state and distance_ahead(veh, *board(c)) >= 0:
                        continue
                    del assigned[pid]
                    promised[veh] -= 1
                    release_stop(stop_users, veh, *board(c), pid=pid, cancel=False)
                    release_stop(stop_users, veh, *alight(c), pid=pid, cancel=True)
                    log(t, "MISSED    %s passed %s - flagging the next one" % (veh, pid))

                approaching = []
                for veh in jeeps:
                    if traci.vehicle.getLine(veh) != c["line"]:
                        continue
                    d = distance_ahead(veh, *board(c))
                    if 0 <= d <= LOOKAHEAD_M:
                        approaching.append((d, veh))
                for d, veh in sorted(approaching):
                    cap = traci.vehicle.getPersonCapacity(veh)
                    if traci.vehicle.getPersonNumber(veh) + promised[veh] >= cap:
                        if (veh, pid) not in full_logged:
                            full_logged.add((veh, pid))
                            log(t, "FULL      %s passes %s" % (veh, pid))
                        continue
                    speed = traci.vehicle.getSpeed(veh)
                    brake = (speed * traci.vehicle.getTau(veh)
                             + speed ** 2 / (2 * traci.vehicle.getDecel(veh)))
                    if d <= brake + 2.0:
                        continue
                    try:
                        reserve_stop(stop_users, veh, *board(c), pid=pid)
                        reserve_stop(stop_users, veh, *alight(c), pid=pid)
                    except traci.TraCIException as exc:
                        release_stop(stop_users, veh, *board(c), pid=pid, cancel=True)
                        log(t, "could not book %s for %s: %s" % (veh, pid, exc))
                        continue
                    assigned[pid] = veh
                    promised[veh] += 1
                    log(t, "FLAGDOWN  %s flags %s (%.0f m away)" % (pid, veh, d))
                    break

            # ---------------- 3. overtake meter -----------------------------
            xs = {"EB": sorted(state[v][tc.VAR_POSITION][0] for v in state
                               if vtype[v][1] == "EB"),
                  "WB": sorted(state[v][tc.VAR_POSITION][0] for v in state
                               if vtype[v][1] == "WB")}

            def oncoming_gap(direction, x):
                """Metres to the nearest vehicle coming the other way."""
                if direction == "EB":
                    i = bisect.bisect_right(xs["WB"], x)
                    return xs["WB"][i] - x if i < len(xs["WB"]) else 1e9
                i = bisect.bisect_left(xs["EB"], x)
                return x - xs["EB"][i - 1] if i > 0 else 1e9

            for j in jeeps:
                jx, jy = state[j][tc.VAR_POSITION]
                jdir, jlen, jw = vtype[j][1], vtype[j][2], vtype[j][3]
                stopped = bool(state[j][tc.VAR_STOPSTATE] & 1)
                if stopped:
                    kerb_samples.append(kerb_gap(jdir, jy, jw))
                for car in cars:
                    cdir = vtype[car][1]
                    if cdir != jdir:
                        continue
                    cx, cy = state[car][tc.VAR_POSITION]
                    clen, cw = vtype[car][2], vtype[car][3]
                    key = (car, j)
                    if side_by_side(cdir, cx, clen, jx, jlen):
                        p = pairs.setdefault(key, {
                            "t0": t, "max_enc": 0.0, "fully": False, "stopped": False,
                            "dir": cdir,
                            # stood still in the 15 s before drawing level = it
                            # had been stuck behind the jeepney, waiting for a gap
                            "queued": t - last_stopped.get(car, -1e9) <= 15,
                            "oncoming": 1e9})
                        p["max_enc"] = max(p["max_enc"], encroachment(cdir, cy, cw))
                        p["oncoming"] = min(p["oncoming"], oncoming_gap(cdir, cx))
                        p["fully"] = p["fully"] or fully_across(cdir, cy, cw)
                        p["stopped"] = p["stopped"] or stopped
                    elif key in pairs:
                        p = pairs.pop(key)
                        if car_ahead(cdir, cx, clen, jx):
                            p.update(car=car, jeep=j, t1=t)
                            overtakes.append(p)
                            if p["stopped"] or PRINT_MOVING_OVERTAKES:
                                log(t, "OVERTAKE  %-12s passed %-14s %s  crossed centre "
                                       "line by %.1f m of %.1f m%s"
                                    % (car, j, "(stopped)" if p["stopped"] else "(moving) ",
                                       p["max_enc"], LANE_W,
                                       "  <- FULLY in oncoming lane" if p["fully"] else ""))
            for key in [k for k in pairs if k[0] not in state or k[1] not in state]:
                pairs.pop(key)

            # how long does each car stay across the centre line? A long
            # stretch would mean cars cruise in the oncoming lane - bad.
            for car in cars:
                if state[car][tc.VAR_SPEED] < 0.5:
                    last_stopped[car] = t
                cy = state[car][tc.VAR_POSITION][1]
                if encroachment(vtype[car][1], cy, vtype[car][3]) > 0.3:
                    across_since.setdefault(car, t)
                elif car in across_since:
                    episodes.append(t - across_since.pop(car))
            for car in [c for c in across_since if c not in state]:
                episodes.append(t - across_since.pop(car))
    except traci.FatalTraCIError:
        closed = True
        print("\nsumo-gui was closed - stopping early.")

    summary(commuters, times, overtakes, kerb_samples, teleports, collisions,
            peak_vehicles, episodes)
    if not closed:
        if gui:
            print("Simulation over - close the sumo-gui window to exit.")
        traci.close()


def summary(commuters, times, overtakes, kerb_samples, teleports, collisions, peak,
            episodes):
    print("\n" + "=" * 72)
    boarded = [p for p in commuters if "board" in times[p]]
    arrived = [p for p in commuters if "arrive" in times[p]]
    waits = [times[p]["board"] - times[p]["kerb"] for p in boarded]
    print("COMMUTERS  %d/%d boarded, %d/%d reached their destination"
          % (len(boarded), len(commuters), len(arrived), len(commuters)))
    if waits:
        print("           kerbside wait: mean %.0f s, max %.0f s"
              % (sum(waits) / len(waits), max(waits)))

    print("\nJEEPNEYS   distance from the kerb while stopped (right side to lane edge):")
    if kerb_samples:
        print("           mean %.2f m, max %.2f m   (0 = touching the kerb)"
              % (sum(kerb_samples) / len(kerb_samples), max(kerb_samples)))

    print("\nOVERTAKES  cars passing a jeepney (peak %d vehicles on the road)" % peak)
    for label, subset in (("of a STOPPED jeepney", [o for o in overtakes if o["stopped"]]),
                          ("of a moving jeepney", [o for o in overtakes if not o["stopped"]])):
        if not subset:
            print("           %-22s none" % label)
            continue
        enc = [o["max_enc"] for o in subset]
        partial = sum(1 for o in subset if not o["fully"] and o["max_enc"] > 0)
        within = sum(1 for o in subset if o["max_enc"] == 0)
        fully = sum(1 for o in subset if o["fully"])
        print("           %-22s %4d   partly across centre: %d  |  fully in oncoming "
              "lane: %d  |  stayed in own lane: %d"
              % (label, len(subset), partial, fully, within))
        print("           %-22s       centre-line crossing: mean %.2f m, max %.2f m "
              "(lane is %.1f m = %.0f%% max)"
              % ("", sum(enc) / len(enc), max(enc), LANE_W, 100 * max(enc) / LANE_W))

    stopped = [o for o in overtakes if o["stopped"]]
    if stopped:
        print("\nWHY FULLY? passing a stopped jeepney, split by whether the car had")
        print("           been stuck behind it first:")
        for label, grp in (("had been queuing", [o for o in stopped if o["queued"]]),
                           ("rolled straight past", [o for o in stopped if not o["queued"]])):
            if grp:
                f = sum(1 for o in grp if o["fully"])
                print("           %-22s %3d overtakes, %3d fully across (%.0f%%)"
                      % (label, len(grp), f, 100.0 * f / len(grp)))
        print("           ...and by the nearest ONCOMING vehicle while side by side:")
        for label, grp in (("oncoming within 100 m", [o for o in stopped if o["oncoming"] < 100]),
                           ("oncoming lane clear", [o for o in stopped if o["oncoming"] >= 100])):
            if grp:
                f = sum(1 for o in grp if o["fully"])
                enc = [o["max_enc"] for o in grp]
                print("           %-22s %3d overtakes, %3d fully across (%.0f%%), "
                      "mean crossing %.2f m"
                      % (label, len(grp), f, 100.0 * f / len(grp), sum(enc) / len(enc)))

    if episodes:
        episodes.sort()
        print("\nRETURNING  each time a car crossed the centre line, how long it stayed:")
        print("           %d crossings, median %.0f s, longest %.0f s"
              % (len(episodes), episodes[len(episodes) // 2], episodes[-1]))
    print("\nHEALTH     teleports: %d   collisions: %d   (both should be 0)"
          % (teleports, collisions))
    print("=" * 72)


if __name__ == "__main__":
    main()
