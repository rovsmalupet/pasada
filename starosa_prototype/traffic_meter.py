"""
traffic_meter.py — how motorcycles, cars and jeepneys share the road.

The filter meter of sandbox 11 (sandbox/11_motorcycles/run.py), ported to the
real Santa Rosa network. jeepney_controller.py calls step() once per
simulation step and prints summary() at the end. It only observes - it never
changes what a vehicle does.

What it counts:
  * MOTORCYCLE passes: a motorcycle draws level with a car, jeepney or bicycle
    while SHARING ITS LANE (the sublane model lets them ride side by side in
    one lane), then gets ahead of it. Recorded: the side it passed on (kerb or
    left), the tightest sideways clearance, which strips of its own lane it
    rode in, and whether the vehicle it passed was queued/crawling
    ("filtering through traffic") or was a jeepney stopped for passengers.
  * CAR passes of a jeepney stopped for passengers (sandbox 10): how far the
    car went past the left edge of the jeepney's lane. On a one-lane-each-way
    street that edge is the centre line.
  * HEALTH: teleports and collisions.

Sandbox 11's road ran straight along x, so a vehicle's y coordinate WAS its
sideways position. Santa Rosa's roads curve, so every pass is measured in the
frame of the vehicle being passed: its heading gives ahead/behind, its left
normal gives left/right. Over the few metres of a pass any road is straight
enough for that.
"""
import math
import xml.etree.ElementTree as ET
from collections import defaultdict

import traci
import traci.constants as tc

# ======================= CONFIG =======================
SLOW_MPS = 2.0         # a vehicle below this is "in traffic" (queued/crawling)
SAME_HEADING = 60.0    # deg; bigger heading differences = crossing/oncoming traffic
BESIDE_M = 4.0         # max sideways distance between centres to count as "beside"
SHOW_EXAMPLES = 12     # how many motorcycle passes of a loading jeepney to print
# ======================================================

SUBSCRIBED = [tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED, tc.VAR_STOPSTATE,
              tc.VAR_LANE_ID, tc.VAR_LANEPOSITION_LAT]
CELL = 2 * BESIDE_M + 10.0   # neighbour-search grid (m); covers any vehicle beside


def kind_of(veh, jeepney_type):
    if traci.vehicle.getTypeID(veh) == jeepney_type:
        return "jeepney"
    vclass = traci.vehicle.getVehicleClass(veh)
    return vclass if vclass in ("motorcycle", "bicycle") else "car"


def heading_gap(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


class TrafficMeter:
    def __init__(self, strip_width, jeepney_type="jeepney", log=None):
        self.strip_w = strip_width          # = lateral-resolution of the run
        self.jeepney_type = jeepney_type
        self.log = log or (lambda t, msg: print(f"[{t:7.1f}s] {msg}"))
        self.info = {}                      # veh -> (kind, length, width)
        self.lane_w = {}                    # lane id -> width (cache)
        self.lane_n = {}                    # edge id -> number of lanes (cache)
        self.pairs = {}                     # (passer, passed) -> pass in progress
        self.moto_passes, self.car_passes = [], []
        self.teleports, self.collisions = 0, set()
        self.peak, self.printed = 0, 0

    # ------------------------------------------------------------ geometry
    def _lane_width(self, lane):
        if lane not in self.lane_w:
            self.lane_w[lane] = traci.lane.getWidth(lane)
        return self.lane_w[lane]

    def _leftmost(self, lane):
        """Is `lane` the leftmost lane of its edge? (None inside junctions)"""
        if lane.startswith(":"):
            return None
        edge, index = lane.rsplit("_", 1)
        if edge not in self.lane_n:
            self.lane_n[edge] = traci.edge.getLaneNumber(edge)
        return int(index) == self.lane_n[edge] - 1

    @staticmethod
    def _relative(p, o):
        """Position of p's front in o's frame: (metres ahead of o's front,
        metres to o's left). SUMO angles: 0 = north, clockwise."""
        a = math.radians(o[tc.VAR_ANGLE])
        hx, hy = math.sin(a), math.cos(a)
        dx = p[tc.VAR_POSITION][0] - o[tc.VAR_POSITION][0]
        dy = p[tc.VAR_POSITION][1] - o[tc.VAR_POSITION][1]
        return dx * hx + dy * hy, -dx * hy + dy * hx

    def _strips(self, s, width):
        """Strips of its OWN lane a vehicle covers, 0 = kerb-side strip."""
        lane_w = self._lane_width(s[tc.VAR_LANE_ID])
        off = lane_w / 2.0 + s[tc.VAR_LANEPOSITION_LAT]   # from the lane's right edge
        n = max(1, int(round(lane_w / self.strip_w)))
        lo = int((off - width / 2.0 + 1e-6) // self.strip_w)
        hi = int((off + width / 2.0 - 1e-6) // self.strip_w)
        return max(lo, 0), min(hi, n - 1), n

    # ---------------------------------------------------------------- step
    def step(self, t):
        for v in traci.simulation.getDepartedIDList():
            self.info[v] = (kind_of(v, self.jeepney_type),
                            traci.vehicle.getLength(v), traci.vehicle.getWidth(v))
            traci.vehicle.subscribe(v, SUBSCRIBED)
        self.teleports += traci.simulation.getStartingTeleportNumber()
        for c in traci.simulation.getCollisions():
            # collision.action=warn keeps both vehicles going, so the same
            # crash is reported every step they overlap - count it once
            self.collisions.add((c.collider, c.victim))

        state = {v: s for v, s in traci.vehicle.getAllSubscriptionResults().items()
                 if v in self.info}
        self.peak = max(self.peak, len(state))

        grid = defaultdict(list)
        for v, s in state.items():
            x, y = s[tc.VAR_POSITION]
            grid[(int(x // CELL), int(y // CELL))].append(v)

        seen = set()
        for o, os_ in state.items():
            okind = self.info[o][0]
            if okind == "motorcycle":
                continue                      # motorcycles passing motorcycles: not counted
            x, y = os_[tc.VAR_POSITION]
            cx, cy = int(x // CELL), int(y // CELL)
            for gx in (cx - 1, cx, cx + 1):
                for gy in (cy - 1, cy, cy + 1):
                    for p in grid.get((gx, gy), ()):
                        pkind = self.info[p][0]
                        if p == o or not (pkind == "motorcycle"
                                          or (pkind == "car" and okind == "jeepney")):
                            continue
                        if self._update(p, o, state, t):
                            seen.add((p, o))

        # pairs no longer side by side: did the passer end up ahead?
        for key in [k for k in self.pairs if k not in seen]:
            p, o = key
            pair = self.pairs.pop(key)
            if p not in state or o not in state:
                continue
            ahead, _ = self._relative(state[p], state[o])
            if ahead - self.info[p][1] >= 0:          # passer's rear past o's front
                self._finish(p, o, pair, t)

    def _update(self, p, o, state, t):
        """Track p beside o. Returns True if they are side by side now."""
        ps, os_ = state[p], state[o]
        if heading_gap(ps[tc.VAR_ANGLE], os_[tc.VAR_ANGLE]) > SAME_HEADING:
            return False
        pkind, plen, pw = self.info[p]
        okind, olen, ow = self.info[o]
        ahead, left = self._relative(ps, os_)
        if not (-olen < ahead < plen) or abs(left) > BESIDE_M:
            return False
        if ps[tc.VAR_LANE_ID] == os_[tc.VAR_LANE_ID]:
            # same lane: SUMO's own sideways offsets are exact, even on a curve
            # (the heading frame above drifts ~0.1 m along a 7 m jeepney there)
            left = ps[tc.VAR_LANEPOSITION_LAT] - os_[tc.VAR_LANEPOSITION_LAT]

        # o's own lane, measured from o's centre (positive = to o's left)
        o_lane_w = self._lane_width(os_[tc.VAR_LANE_ID])
        lane_left = o_lane_w / 2.0 - os_[tc.VAR_LANEPOSITION_LAT]
        lane_right = -o_lane_w / 2.0 - os_[tc.VAR_LANEPOSITION_LAT]
        shared = min(left + pw / 2.0, lane_left) - max(left - pw / 2.0, lane_right) > 0.05
        clear = abs(left) - (pw + ow) / 2.0

        pair = self.pairs.setdefault((p, o), {
            "kind": okind, "clear": 1e9, "shared": False, "slow": False,
            "loading": False, "past_left": 0.0, "fully_out": False,
            "leftmost": self._leftmost(os_[tc.VAR_LANE_ID])})
        if clear < pair["clear"]:                     # remember the tightest moment
            pair.update(clear=clear, side="kerb" if left < 0 else "left",
                        strips=self._strips(ps, pw))
        pair["shared"] |= shared
        pair["slow"] |= os_[tc.VAR_SPEED] < SLOW_MPS
        pair["loading"] |= okind == "jeepney" and bool(os_[tc.VAR_STOPSTATE] & 1)
        pair["past_left"] = max(pair["past_left"], left + pw / 2.0 - lane_left)
        pair["fully_out"] |= left - pw / 2.0 > lane_left
        return True

    def _finish(self, p, o, pair, t):
        pair.update(passer=p, passed=o, t=t)
        if self.info[p][0] != "motorcycle":
            self.car_passes.append(pair)
            return
        self.moto_passes.append(pair)
        if pair["loading"] and self.printed < SHOW_EXAMPLES:
            self.printed += 1
            lo, hi, n = pair["strips"]
            self.log(t, f"MOTO     {p} passed {o} while it loaded passengers, on its "
                        f"{pair['side']} side, strips {lo + 1}-{hi + 1} of {n}, "
                        f"clearance {pair['clear']:.2f} m")

    # ------------------------------------------------------------- summary
    def summary(self):
        out = ["\n================ traffic summary ================",
               f"peak {self.peak} vehicles on the network"]

        filt = [p for p in self.moto_passes if p["shared"]]
        other_lane = len(self.moto_passes) - len(filt)
        out.append(f"\nMOTORCYCLES  passing a car, jeepney or bicycle while sharing "
                   f"its lane: {len(filt)}")
        for label, grp in (("in TRAFFIC (it was queued/crawling)",
                            [p for p in filt if p["slow"]]),
                           ("in free flow (it was moving)",
                            [p for p in filt if not p["slow"]])):
            out.append(f"  {label:38s} {len(grp):5d}")
            for side, edge_strip in (("kerb", lambda s: s[0] <= 1),
                                     ("left", lambda s: s[1] >= s[2] - 2)):
                g = [p for p in grp if p["side"] == side]
                if not g:
                    continue
                in_strips = sum(1 for p in g if edge_strip(p["strips"]))
                out.append(f"    on the {side.upper():4s} side {len(g):5d}   min clearance "
                           f"mean {sum(p['clear'] for p in g) / len(g):.2f} m   "
                           f"riding in the 2 {side} strips: {in_strips} "
                           f"({100.0 * in_strips / len(g):.0f}%)")
        loading = [p for p in filt if p["loading"]]
        if loading:
            kerb = sum(1 for p in loading if p["side"] == "kerb")
            out.append(f"  of these, past a jeepney LOADING passengers: {len(loading)} "
                       f"(kerb side {kerb}, left side {len(loading) - kerb})")
        out.append(f"  (+{other_lane} ordinary passes from a separate lane, e.g. on the "
                   f"two-lane trunk road - not filtering)")

        stopped = [p for p in self.car_passes if p["loading"]]
        out.append(f"\nCARS         passing a jeepney stopped for passengers: {len(stopped)}"
                   f"   (passing a moving jeepney: {len(self.car_passes) - len(stopped)})")
        for label, grp in (("one lane each way - left edge = centre line",
                            [p for p in stopped if p["leftmost"]]),
                           ("multi-lane - left edge = lane line",
                            [p for p in stopped if p["leftmost"] is False])):
            if grp:
                past = [max(0.0, p["past_left"]) for p in grp]
                fully = sum(1 for p in grp if p["fully_out"])
                out.append(f"  {label:44s} {len(grp):4d}   past the left edge by mean "
                           f"{sum(past) / len(past):.2f} m, max {max(past):.2f} m; "
                           f"fully out of the lane: {fully}")

        out.append(f"\nHEALTH       teleports: {self.teleports}   "
                   f"collisions: {len(self.collisions)}   (both should be 0)")
        return "\n".join(out)


def travel_times(tripinfo_path):
    """Mean trip time per flow (vehicle ids 'flow.N'), from SUMO's tripinfo.
    Single vehicles (car1, bike1) are left out. Call after traci.close() -
    SUMO only finishes writing the file then."""
    acc = defaultdict(lambda: [None, 0, 0.0, 0.0, 0.0])
    for ti in ET.parse(tripinfo_path).getroot().iter("tripinfo"):
        if "." not in ti.get("id") or float(ti.get("arrival")) < 0:
            continue                                  # single vehicle / unfinished
        a = acc[ti.get("id").rsplit(".", 1)[0]]
        a[0] = ti.get("vType")
        a[1] += 1
        a[2] += float(ti.get("duration"))
        a[3] += float(ti.get("timeLoss"))
        a[4] += float(ti.get("waitingTime"))
    lines = ["\nTRAVEL TIME  per flow, finished trips only (from tripinfo.xml):",
             f"  {'flow':8s} {'vType':11s} {'trips':>5s} {'mean time':>10s} "
             f"{'time lost':>10s} {'stopped':>8s}"]
    for flow, (vtype, n, d, lost, wait) in sorted(acc.items()):
        lines.append(f"  {flow:8s} {vtype:11s} {n:5d} {d / n:9.0f}s {lost / n:9.0f}s "
                     f"{wait / n:7.0f}s")
    return "\n".join(lines)
