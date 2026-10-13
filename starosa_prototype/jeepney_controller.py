"""
jeepney_controller.py — Milestone D: flag-down jeepney control via TraCI,
for many commuters and many jeepneys.

A baby version of the controller in Section 4.4.2.2:
  * builds its commuter registry straight from the route files listed in
    run.sumocfg: every <person> with a <ride lines="JPN1"> is a commuter whose
    boarding point is where the walk before the ride ends (to + arrivalPos)
    and whose alighting point is the ride's own to + arrivalPos. The demand
    files and the controller therefore can never disagree
    (make_passengers.py writes passengers.rou.xml; demand.rou.xml keeps the
    hand-authored commuter0),
  * once a commuter is standing at the kerb, flags down the NEAREST JPN1
    jeepney approaching within LOOKAHEAD metres that still has a free seat
    (seats already promised to other flagging commuters count as taken),
  * commands vehicle.setStop for BOTH the boarding stop AND the alighting
    stop at once (arbitrary-position stops, NOT named bus stops).
    SUMO silently refuses to ever board a person onto a vehicle unless a
    stop already exists at the ride's alighting edge too - scheduling only
    the boarding stop deadlocks forever (confirmed empirically against SUMO
    1.27.0; not documented clearly). That's why both are commanded together
    here instead of adding the alighting stop after boarding is detected.
  * flags only ONE jeepney per commuter - with a jeepney every minute, every
    vehicle behind the first would otherwise pull over for nobody. If that
    jeepney drives past without them, or they hop onto a different one, its
    now-useless stops are cancelled and the next jeepney is flagged instead,
  * stops the jeepney at the kerb side of its lane while it loads/unloads,
    so cars behind can overtake it (or, with PULL_OVER, fully off the lane),
  * passes by (FULL) when no seat is left,
  * logs every event with a timestamp and prints a per-commuter summary,
  * runs traffic_meter.py alongside (observe only): how motorcycles filter
    past cars and loading jeepneys, how far cars swing out to pass a stopped
    jeepney, collisions/teleports, and travel time per flow.

Usage:
    python make_passengers.py              # (re)generate the random commuters
    python jeepney_controller.py           # sumo-gui, press play
    python jeepney_controller.py --nogui   # headless: event log + summary only
"""
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter

import sumolib
import traci
import traci.constants as tc

import traffic_meter

# ======================= CONFIG =======================
SUMO_CFG  = "run.sumocfg"      # its route files ARE the commuter registry
NET_FILE  = "santarosa.net.xml"
GUI_SETTINGS = "gui-settings.xml"   # zoom, person size, 100 ms step delay
LINE      = "JPN1"
DWELL     = 15.0     # seconds per stop (fixed for the prototype)
LOOKAHEAD = 200.0    # a waiting commuter flags jeepneys within this distance (m)
# False (default): a jeepney stops IN its lane, tucked against the kerb side
# (sublane model + latAlignment="right"), and cars get past it through the
# oncoming lane when that is clear (network built with opposites.guess).
# True: it pulls fully OUT of the traffic lane (SUMO "parking" stop) - no car
# is ever held up, but sumo-gui draws the parked jeepney beside the road.
PULL_OVER = False
# Seat capacity is read from each jeepney's vType (personCapacity), so it
# can no longer drift out of sync with demand.rou.xml.
# ======================================================


def passenger_lane_index(net, edge_id):
    """First lane on `edge_id` that a passenger-class vehicle may use.
    traci.vehicle.setStop defaults to laneIndex=0, which is often the
    sidewalk or a bike lane on edges built with --sidewalks.guess /
    --bikelanes.guess - stopping there raises a TraCIException."""
    for lane in net.getEdge(edge_id).getLanes():
        if lane.allows("passenger"):
            return lane.getIndex()
    raise ValueError(f"No passenger-legal lane found on edge {edge_id}")


def log(t, msg):
    print(f"[{t:7.1f}s] {msg}")


def route_files(sumocfg):
    """Route files listed in the .sumocfg (paths there are relative to it)."""
    value = ET.parse(sumocfg).getroot().find("input/route-files").get("value")
    base = os.path.dirname(os.path.abspath(sumocfg))
    return [os.path.join(base, f.strip()) for f in value.split(",") if f.strip()]


def load_commuters(files, line):
    """Registry: one entry per <person> whose plan contains a ride on `line`."""
    commuters = {}
    for path in files:
        for person in ET.parse(path).getroot().iter("person"):
            stages = list(person)
            for i, ride in enumerate(stages):
                if ride.tag != "ride" or line not in ride.get("lines", "").split():
                    continue
                walk = stages[i - 1] if i > 0 else None
                if (walk is None or walk.tag != "walk"
                        or walk.get("arrivalPos") is None
                        or ride.get("arrivalPos") is None):
                    print(f"WARNING: {person.get('id')} in {path} skipped - its "
                          f"ride needs a <walk arrivalPos=...> right before it "
                          f"and an arrivalPos of its own")
                    continue
                commuters[person.get("id")] = {
                    "person_id":   person.get("id"),
                    "board_edge":  walk.get("to"),
                    "board_pos":   float(walk.get("arrivalPos")),
                    "alight_edge": ride.get("to"),
                    "alight_pos":  float(ride.get("arrivalPos")),
                }
    return commuters


def seats(veh):
    return traci.vehicletype.getPersonCapacity(traci.vehicle.getTypeID(veh))


def distance_ahead(veh, edge, pos, lane):
    """Metres `veh` still has to drive to reach edge@pos; negative once it
    is past. While a jeepney is pulled over at the kerb (a parking stop),
    getDrivingDistance returns INVALID for every target (confirmed against
    SUMO 1.27.0) - that made a jeepney loading one commuter look as if it had
    already passed the next one - so measure along its route by hand then."""
    if not traci.vehicle.isStoppedParking(veh):
        return traci.vehicle.getDrivingDistance(veh, edge, pos, lane)
    route = traci.vehicle.getRoute(veh)
    idx = traci.vehicle.getRouteIndex(veh)
    if edge not in route[idx:]:
        return -1.0
    dist = pos - traci.vehicle.getLanePosition(veh)
    for e in route[idx:route.index(edge, idx)]:
        dist += traci.lane.getLength(f"{e}_0")
    return dist


# ---- stop bookkeeping: several commuters may share one physical stop ----

STOP_FLAGS = tc.STOP_PARKING if PULL_OVER else tc.STOP_DEFAULT

def reserve_stop(stop_users, veh, edge, pos, lane, pid):
    """Make `veh` stop at edge@pos on behalf of `pid`."""
    key = (veh, edge, pos)
    if key not in stop_users:
        traci.vehicle.setStop(veh, edge, pos=pos, laneIndex=lane, duration=DWELL,
                              flags=STOP_FLAGS)
        stop_users[key] = set()
    stop_users[key].add(pid)


def release_stop(stop_users, veh, edge, pos, lane, pid, cancel):
    """`pid` no longer needs that stop. With cancel=True the stop is removed
    from the jeepney too, unless another commuter still needs it. Only cancel
    stops still AHEAD of the vehicle: re-sending a stop it has already passed
    would schedule a brand-new one."""
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
            # re-sending an existing, not-yet-reached stop with duration 0
            # deletes it
            traci.vehicle.setStop(veh, edge, pos=pos, laneIndex=lane, duration=0,
                                  flags=STOP_FLAGS)
        except traci.TraCIException:
            pass   # jeepney already left the network


def main():
    gui = "--nogui" not in sys.argv[1:]
    # all file names above are relative to this folder - make the script work
    # no matter which directory it is launched from (e.g. VS Code's terminal)
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    net = sumolib.net.readNet(NET_FILE)
    commuters = load_commuters(route_files(SUMO_CFG), LINE)
    for c in commuters.values():
        c["board_lane"] = passenger_lane_index(net, c["board_edge"])
        c["alight_lane"] = passenger_lane_index(net, c["alight_edge"])
    print(f"Registry: {len(commuters)} commuters riding {LINE}")

    if gui:
        traci.start(["sumo-gui", "-c", SUMO_CFG, "-g", GUI_SETTINGS])
        print("sumo-gui is open: press the green Run (play) button to start.")
    else:
        traci.start(["sumo", "-c", SUMO_CFG])
    end = traci.simulation.getEndTime()   # -1 if the config sets no end
    meter = traffic_meter.TrafficMeter(
        float(traci.simulation.getOption("lateral-resolution")), log=log)

    waiting = dict(commuters)   # not yet on board
    assigned = {}               # waiting pid -> the one jeepney flagged for them
    riding = {}                 # pid -> jeepney they are on
    stop_users = {}             # (veh, edge, pos) -> pids needing that stop
    full_logged = set()         # (veh, pid) FULL events already printed
    times = {pid: {} for pid in commuters}   # kerb/board/alight/arrive, jeepney
    jeepneys = set()            # JPN1 vehicles currently on the network

    def board_stop(c):
        return c["board_edge"], c["board_pos"], c["board_lane"]

    def alight_stop(c):
        return c["alight_edge"], c["alight_pos"], c["alight_lane"]

    closed = False   # did the user shut the sumo-gui window mid-run?
    try:
        while traci.simulation.getMinExpectedNumber() > 0:
            if 0 <= end <= traci.simulation.getTime():
                break
            traci.simulationStep()
            t = traci.simulation.getTime()
            meter.step(t)
            persons = set(traci.person.getIDList())
            # check each vehicle's line once, when it enters: asking every
            # vehicle every second costs one TraCI round trip each, and the
            # motorcycles double the number of vehicles
            jeepneys.update(v for v in traci.simulation.getDepartedIDList()
                            if traci.vehicle.getLine(v) == LINE)
            jeepneys.difference_update(traci.simulation.getArrivedIDList())

            # --- 1. watch person state: kerb arrival, boarding, alighting ---
            for pid in list(waiting):
                if pid not in persons:
                    continue   # not departed from home yet
                c = waiting[pid]
                veh = traci.person.getVehicle(pid)
                if not veh:
                    # walking is OVER once SUMO moves the person on to the ride
                    # stage (STAGE_DRIVING) with no vehicle yet - they now stand at
                    # the kerb. Flagging while they still walk would make the
                    # jeepney pull over, dwell, and leave before they arrive.
                    if ("kerb" not in times[pid]
                            and traci.person.getStage(pid).type == tc.STAGE_DRIVING):
                        times[pid]["kerb"] = t
                        log(t, f"WAITING  {pid} at {c['board_edge']}@{c['board_pos']}")
                    continue
                waiting.pop(pid)
                riding[pid] = veh
                times[pid].setdefault("kerb", t)
                times[pid].update(board=t, veh=veh)
                log(t, f"BOARDED  {pid} -> {veh} "
                       f"({traci.vehicle.getPersonNumber(veh)}/{seats(veh)} aboard)")
                flagged = assigned.pop(pid, None)
                if flagged == veh:
                    release_stop(stop_users, veh, *board_stop(c), pid, cancel=False)
                else:
                    # hopped onto a jeepney that pulled over right here for
                    # someone else - the one they flagged need not stop any more
                    if flagged is not None:
                        release_stop(stop_users, flagged, *board_stop(c), pid, cancel=True)
                        release_stop(stop_users, flagged, *alight_stop(c), pid, cancel=True)
                    try:
                        reserve_stop(stop_users, veh, *alight_stop(c), pid)
                    except traci.TraCIException:
                        pass   # SUMO only let them on because veh stops on that edge

            for pid in list(riding):
                if pid in persons and traci.person.getVehicle(pid):
                    continue
                veh = riding.pop(pid)
                release_stop(stop_users, veh, *alight_stop(commuters[pid]), pid,
                             cancel=False)
                times[pid]["alight"] = t
                where = traci.person.getRoadID(pid) if pid in persons else "(arrived)"
                log(t, f"ALIGHTED {pid} from {veh} at {where}")

            for pid in traci.simulation.getArrivedPersonIDList():
                if pid in times:
                    times[pid]["arrive"] = t
                    log(t, f"ARRIVED  {pid} reached destination")

            # --- 2. flag-downs: rules 2-4 of Section 4.4.2.2 ---
            promised = Counter(assigned.values())   # seats held for waiting commuters
            for pid, c in waiting.items():
                if "kerb" not in times[pid]:
                    continue   # still walking to the roadside

                if pid in assigned:
                    veh = assigned[pid]
                    if (veh in jeepneys
                            and distance_ahead(veh, *board_stop(c)) >= 0):
                        continue   # flagged jeepney still on its way
                    # it drove past (or left the network) without them
                    del assigned[pid]
                    promised[veh] -= 1
                    release_stop(stop_users, veh, *board_stop(c), pid, cancel=False)
                    release_stop(stop_users, veh, *alight_stop(c), pid, cancel=True)
                    log(t, f"MISSED   {veh} passed {pid}; flagging the next one")

                # approaching jeepneys that can see this commuter, nearest first
                approaching = []
                for veh in jeepneys:
                    dist = distance_ahead(veh, *board_stop(c))
                    if 0 <= dist <= LOOKAHEAD:
                        approaching.append((dist, veh))

                for dist, veh in sorted(approaching):
                    # the alighting spot must lie further along this jeepney's route
                    if distance_ahead(veh, *alight_stop(c)) <= dist:
                        continue
                    if traci.vehicle.getPersonNumber(veh) + promised[veh] >= seats(veh):
                        if (veh, pid) not in full_logged:
                            full_logged.add((veh, pid))
                            log(t, f"FULL     {veh} passes {pid}")
                        continue
                    # can it still stop in time? Asking anyway is refused and
                    # makes SUMO print a scary "Error: ... too close to brake"
                    speed = traci.vehicle.getSpeed(veh)
                    brake_gap = (speed * traci.vehicle.getTau(veh)
                                 + speed ** 2 / (2 * traci.vehicle.getDecel(veh)))
                    if dist <= brake_gap:
                        continue
                    try:
                        reserve_stop(stop_users, veh, *board_stop(c), pid)
                    except traci.TraCIException:
                        continue   # too close to brake - try the next jeepney
                    try:
                        reserve_stop(stop_users, veh, *alight_stop(c), pid)
                    except traci.TraCIException:
                        release_stop(stop_users, veh, *board_stop(c), pid, cancel=True)
                        continue
                    assigned[pid] = veh
                    promised[veh] += 1
                    log(t, f"FLAGDOWN {pid} flags {veh} ({dist:.0f} m away): stop at "
                           f"{c['board_edge']}@{c['board_pos']}, drop off at "
                           f"{c['alight_edge']}@{c['alight_pos']}")
                    break
    except traci.FatalTraCIError:
        closed = True
        print("\nsumo-gui was closed - stopping early.")

    # summaries first: with sumo-gui, traci.close() blocks until the window is shut
    print_summary(commuters, times)
    print(meter.summary())
    if not closed:
        if gui:
            print("\nSimulation over - close the sumo-gui window to see travel times.")
        traci.close()
    # SUMO only finishes writing tripinfo.xml once it has shut down
    tripinfo = ET.parse(SUMO_CFG).getroot().find("output/tripinfo-output").get("value")
    try:
        print(traffic_meter.travel_times(tripinfo))
    except (OSError, ET.ParseError):
        print(f"\n({tripinfo} is missing or incomplete - travel times skipped)")
    print("Check tripinfo.xml for SUMO's own door-to-door times.")


def print_summary(commuters, times):
    def fmt(x):
        return f"{x:.0f}" if x is not None else "-"

    print("\n================ commuter summary ================")
    print(f"{'person':10s} {'jeepney':10s} {'at kerb':>8s} {'wait':>6s} "
          f"{'ride':>6s} {'arrived':>8s}")
    waits = []
    for pid in commuters:
        tm = times[pid]
        wait = tm["board"] - tm["kerb"] if "board" in tm else None
        ride = tm["alight"] - tm["board"] if "alight" in tm else None
        if wait is not None:
            waits.append(wait)
        print(f"{pid:10s} {tm.get('veh', '-'):10s} {fmt(tm.get('kerb')):>8s} "
              f"{fmt(wait):>6s} {fmt(ride):>6s} {fmt(tm.get('arrive')):>8s}")
    arrived = sum("arrive" in times[p] for p in commuters)
    print(f"\n{len(waits)}/{len(commuters)} boarded, "
          f"{arrived}/{len(commuters)} reached their destination")
    if waits:
        print(f"kerbside wait: mean {sum(waits) / len(waits):.0f} s, "
              f"max {max(waits):.0f} s")


if __name__ == "__main__":
    main()
