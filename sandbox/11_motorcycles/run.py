"""Motorcycles filter through traffic on the kerb side AND the centre-line side.

Builds on sandbox 10 (jeepneys stop at the kerb, cars squeeze past them).
New here: motorcycles, and a signalised junction whose red light queues the
main road every cycle.

Three jobs:
  1. FLAG-DOWN CONTROLLER - unchanged from sandbox 10.
  2. FILTER METER - every time a motorcycle passes a car or jeepney, record
     which SIDE it passed on (kerb or centre line), how much sideways
     clearance it had, and whether the vehicle it passed was crawling or
     stopped (= "filtering through traffic") or moving normally.
  3. TRAVEL TIMES - cars vs motorcycles on the same road, from tripinfo.xml.

The main road runs straight along x, so a vehicle's y coordinate IS its
sideways position: side and clearance come straight from geometry.

Usage:
    python run.py              # sumo-gui (starts paused - press play)
    python run.py --nogui      # headless: summary only
    python run.py --verbose    # also print every commuter event
"""
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
LOOKAHEAD_M = 200.0        # a kerbside commuter flags jeepneys within this range
DWELL_S = 12.0             # minimum stop length
SLOW_MPS = 2.0             # a vehicle below this is "in traffic" (queued/crawling)
SHOW_FILTER_EXAMPLES = 12  # how many motorcycle filter passes to print
# ===========================================================================

VERBOSE = "--verbose" in sys.argv[1:]
net = sumolib.net.readNet(NET)


# ---------------------------------------------------------------- geometry
def lane_y(lane_id):
    return net.getLane(lane_id).getShape()[0][1]


LANE_W = net.getLane("AC_1").getWidth()
CENTRE_Y = (lane_y("AC_1") + lane_y("CA_1")) / 2.0
KERB_Y = {"EB": lane_y("AC_1") - LANE_W / 2.0, "WB": lane_y("CA_1") + LANE_W / 2.0}


def direction_of(type_id, line):
    """EB / WB on the main road; None for cross-street traffic."""
    if line:
        return "EB" if line.endswith("EB") else "WB"
    if type_id.endswith("_eb"):
        return "EB"
    if type_id.endswith("_wb"):
        return "WB"
    return None


def kind_of(type_id):
    if type_id == "jeepney":
        return "jeepney"
    return "moto" if type_id.startswith("moto") else "car"


def encroachment(direction, y, width):
    """Metres the vehicle's LEFT side is past the centre line (0 if not)."""
    if direction == "EB":
        return max(0.0, (y + width / 2.0) - CENTRE_Y)
    return max(0.0, CENTRE_Y - (y - width / 2.0))


def kerb_gap(direction, y, width):
    if direction == "EB":
        return (y - width / 2.0) - KERB_Y["EB"]
    return KERB_Y["WB"] - (y + width / 2.0)


STRIP_W = 0.4                                   # = lateral-resolution in sim.sumocfg
N_STRIPS = int(round(LANE_W / STRIP_W))         # 9 strips on a 3.6 m lane


def strips(direction, y, width):
    """Which 0.4 m strips of its OWN lane a vehicle covers, counted from the
    kerb: 0 = rightmost (kerb) strip, N_STRIPS-1 = leftmost (centre line)."""
    off = (y - KERB_Y["EB"]) if direction == "EB" else (KERB_Y["WB"] - y)
    lo = int((off - width / 2.0 + 1e-6) // STRIP_W)
    hi = int((off + width / 2.0 - 1e-6) // STRIP_W)
    return max(lo, 0), min(hi, N_STRIPS - 1)


def side_by_side(direction, xa, la, xb, lb):
    """Do vehicle a (front at xa, length la) and b overlap lengthwise?"""
    if direction == "EB":
        return xa > xb - lb and xa - la < xb
    return xa < xb + lb and xa + la > xb


def ahead(direction, xa, la, xb):
    return (xa - la >= xb) if direction == "EB" else (xa + la <= xb)


def side_of(direction, ya, yb):
    """Which side of b is a on? Right = kerb side."""
    if direction == "EB":
        return "kerb side" if ya < yb else "centre-line side"
    return "kerb side" if ya > yb else "centre-line side"


# --------------------------------------------------------------- registry
def load_commuters():
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
        try:
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
    print("Main road: %.1f m lanes, centre line y=%.1f | %d commuters"
          % (LANE_W, CENTRE_Y, len(commuters)))

    if gui:
        traci.start(["sumo-gui", "-c", CFG, "-g", GUI_SETTINGS])
        print("sumo-gui is open: press the green Run (play) button.")
    else:
        traci.start(["sumo", "-c", CFG])
    end = traci.simulation.getEndTime()

    info = {}                     # vehicle -> (kind, direction, length, width)
    waiting, assigned, riding = dict(commuters), {}, {}
    stop_users, full_logged = {}, set()
    times = {pid: {} for pid in commuters}

    moto_pairs = {}               # (moto, other) -> pass in progress
    moto_passes = []              # finished passes
    car_pairs, car_overtakes = {}, []
    teleports = collisions = peak = 0
    printed = 0

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
                info[v] = (kind_of(tid), direction_of(tid, traci.vehicle.getLine(v)),
                           traci.vehicle.getLength(v), traci.vehicle.getWidth(v))
                traci.vehicle.subscribe(v, [tc.VAR_POSITION, tc.VAR_SPEED,
                                            tc.VAR_STOPSTATE])
            teleports += traci.simulation.getStartingTeleportNumber()
            collisions += traci.simulation.getCollidingVehiclesNumber()
            state = traci.vehicle.getAllSubscriptionResults()
            peak = max(peak, len(state))

            main_road = [v for v in state if info[v][1] is not None]
            jeeps = [v for v in main_road if info[v][0] == "jeepney"]
            motos = [v for v in main_road if info[v][0] == "moto"]
            others = [v for v in main_road if info[v][0] != "moto"]

            # ---------------- 1. commuters (same as sandbox 10) -------------
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
                times[pid].update(board=t)
                if VERBOSE:
                    log(t, "BOARDED   %s -> %s" % (pid, veh))
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
                if VERBOSE:
                    log(t, "ALIGHTED  %s from %s" % (pid, veh))
            for pid in traci.simulation.getArrivedPersonIDList():
                if pid in times:
                    times[pid]["arrive"] = t

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
                approaching = []
                for veh in jeeps:
                    if traci.vehicle.getLine(veh) != c["line"]:
                        continue
                    d = distance_ahead(veh, *board(c))
                    if 0 <= d <= LOOKAHEAD_M:
                        approaching.append((d, veh))
                for d, veh in sorted(approaching):
                    if (traci.vehicle.getPersonNumber(veh) + promised[veh]
                            >= traci.vehicle.getPersonCapacity(veh)):
                        continue
                    speed = state[veh][tc.VAR_SPEED]
                    brake = (speed * traci.vehicle.getTau(veh)
                             + speed ** 2 / (2 * traci.vehicle.getDecel(veh)))
                    if d <= brake + 2.0:
                        continue
                    try:
                        reserve_stop(stop_users, veh, *board(c), pid=pid)
                        reserve_stop(stop_users, veh, *alight(c), pid=pid)
                    except traci.TraCIException:
                        release_stop(stop_users, veh, *board(c), pid=pid, cancel=True)
                        continue
                    assigned[pid] = veh
                    promised[veh] += 1
                    if VERBOSE:
                        log(t, "FLAGDOWN  %s flags %s (%.0f m away)" % (pid, veh, d))
                    break

            # ---------------- 2. motorcycle filter meter --------------------
            for m in motos:
                mdir, _, mlen, mw = info[m][1], None, info[m][2], info[m][3]
                mx, my = state[m][tc.VAR_POSITION]
                for o in others:
                    if info[o][1] != mdir:
                        continue
                    ox, oy = state[o][tc.VAR_POSITION]
                    olen, ow = info[o][2], info[o][3]
                    key = (m, o)
                    if side_by_side(mdir, mx, mlen, ox, olen):
                        p = moto_pairs.setdefault(key, {
                            "t0": t, "dir": mdir, "kind": info[o][0], "side": None,
                            "clear": 1e9, "slow": False, "cross": 0.0})
                        p["side"] = side_of(mdir, my, oy)
                        gap = abs(my - oy) - (mw + ow) / 2.0
                        if gap < p["clear"]:          # remember the tightest moment
                            p["clear"] = gap
                            p["strips"] = strips(mdir, my, mw)
                        p["slow"] = p["slow"] or state[o][tc.VAR_SPEED] < SLOW_MPS
                        p["cross"] = max(p["cross"], encroachment(mdir, my, mw))
                    elif key in moto_pairs:
                        p = moto_pairs.pop(key)
                        if ahead(mdir, mx, mlen, ox):
                            p.update(moto=m, other=o, t1=t)
                            moto_passes.append(p)
                            if p["slow"] and printed < SHOW_FILTER_EXAMPLES:
                                printed += 1
                                log(t, "FILTER    %-11s passed queued %-7s %-12s on the "
                                       "%-16s strips %d-%d  clearance %.2f m%s"
                                    % (m, p["kind"], o, p["side"], p["strips"][0] + 1,
                                       p["strips"][1] + 1, p["clear"],
                                       "  (crossed centre line by %.1f m)" % p["cross"]
                                       if p["cross"] > 0.05 else ""))
                                if printed == SHOW_FILTER_EXAMPLES:
                                    print("           ... (further filter passes counted "
                                          "in the summary, not printed)")
            for key in [k for k in moto_pairs if k[0] not in state or k[1] not in state]:
                moto_pairs.pop(key)

            # ---------------- 3. cars passing stopped jeepneys (sandbox 10) --
            for j in jeeps:
                if not state[j][tc.VAR_STOPSTATE] & 1:
                    continue
                jx = state[j][tc.VAR_POSITION][0]
                jdir, jlen = info[j][1], info[j][2]
                for car in others:
                    if info[car][0] != "car" or info[car][1] != jdir:
                        continue
                    cx, cy = state[car][tc.VAR_POSITION]
                    key = (car, j)
                    if side_by_side(jdir, cx, info[car][2], jx, jlen):
                        p = car_pairs.setdefault(key, {"enc": 0.0})
                        p["enc"] = max(p["enc"], encroachment(jdir, cy, info[car][3]))
                    elif key in car_pairs and ahead(jdir, cx, info[car][2], jx):
                        car_overtakes.append(car_pairs.pop(key))
            for key in [k for k in car_pairs if k[0] not in state or k[1] not in state]:
                car_pairs.pop(key)
    except traci.FatalTraCIError:
        closed = True
        print("\nsumo-gui was closed - stopping early.")

    if not closed:
        if gui:
            print("Simulation over - close the sumo-gui window to see the summary.")
        traci.close()
    summary(commuters, times, moto_passes, car_overtakes, teleports, collisions, peak)


def travel_times():
    """Mean time on the main road per vehicle kind, from SUMO's tripinfo."""
    path = os.path.join(HERE, "tripinfo.xml")
    if not os.path.exists(path):
        return {}
    acc = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for ti in ET.parse(path).getroot().iter("tripinfo"):
        vt = ti.get("vType")
        if not (vt.endswith("_eb") or vt.endswith("_wb")):
            continue
        k = "motorcycle" if vt.startswith("moto") else "car"
        a = acc[k]
        a[0] += 1
        a[1] += float(ti.get("duration"))
        a[2] += float(ti.get("timeLoss"))
        a[3] += float(ti.get("waitingTime"))
    return {k: (n, d / n, tl / n, w / n) for k, (n, d, tl, w) in acc.items() if n}


def summary(commuters, times, passes, car_overtakes, teleports, collisions, peak):
    print("\n" + "=" * 74)
    boarded = sum("board" in times[p] for p in commuters)
    arrived = sum("arrive" in times[p] for p in commuters)
    print("COMMUTERS    %d/%d boarded, %d/%d reached their destination   "
          "(peak %d vehicles on the road)" % (boarded, len(commuters), arrived,
                                               len(commuters), peak))

    print("\nMOTORCYCLES  passing a car or jeepney:")
    for label, grp in (("in TRAFFIC (it was queued/crawling)", [p for p in passes if p["slow"]]),
                       ("in free flow (it was moving)", [p for p in passes if not p["slow"]])):
        if not grp:
            print("             %-38s none" % label)
            continue
        kerb = [p for p in grp if p["side"] == "kerb side"]
        cl = [p for p in grp if p["side"] == "centre-line side"]
        crossed = sum(1 for p in cl if p["cross"] > 0.05)
        # "edge strips" = the two strips at that side of the lane
        at_kerb = sum(1 for p in kerb if p["strips"][0] <= 1)
        at_line = sum(1 for p in cl if p["strips"][1] >= N_STRIPS - 2)
        print("             %-38s %4d passes" % (label, len(grp)))
        if kerb:
            print("               kerb-side passes riding in the 2 KERB strips:        "
                  "%4d of %d (%.0f%%)" % (at_kerb, len(kerb), 100.0 * at_kerb / len(kerb)))
        if cl:
            print("               centre-side passes riding in the 2 CENTRE-LINE strips: "
                  "%4d of %d (%.0f%%)" % (at_line, len(cl), 100.0 * at_line / len(cl)))
        print("               on the KERB side         %4d  (min clearance mean %.2f m)"
              % (len(kerb), sum(p["clear"] for p in kerb) / len(kerb) if kerb else 0))
        print("               on the CENTRE-LINE side  %4d  (min clearance mean %.2f m), "
              "%d of them partly across the line"
              % (len(cl), sum(p["clear"] for p in cl) / len(cl) if cl else 0, crossed))

    tt = travel_times()
    if tt:
        print("\nTRAVEL TIME  main road, end to end (from tripinfo.xml):")
        print("             %-11s %6s %10s %10s %14s" % ("", "trips", "mean time",
                                                         "time lost", "stopped/waiting"))
        for k in ("car", "motorcycle"):
            if k in tt:
                n, d, tl, wt = tt[k]
                print("             %-11s %6d %9.0f s %9.0f s %13.0f s" % (k, n, d, tl, wt))

    if car_overtakes:
        enc = [o["enc"] for o in car_overtakes]
        print("\nCARS         passing a stopped jeepney: %d, crossed centre line by "
              "mean %.2f m (sandbox 10 behaviour)" % (len(enc), sum(enc) / len(enc)))

    print("\nHEALTH       teleports: %d   collisions: %d   (both should be 0)"
          % (teleports, collisions))
    print("=" * 74)


if __name__ == "__main__":
    main()
