"""Turn a demand file into SUMO input: the bridge between the LLM and the simulation.

The demand file (demand/day_1.json; format in demand/README.md) is written by the LLM, or by
llm_stub.py until it is connected. It says who goes where, when and by which mode, and holds no
transport values: as the thesis plan requires (Sec. 4.3.1), this code works out every route,
position and time.

  * TRIPS by sumo_track's fixed rule (Plan Phase 5, step 1): a trip whenever two consecutive
    schedule entries are at different places, leaving when the earlier entry ends.
  * PLACES: activity locations (ACTIVITY_LOCATIONS, sumo_track's OSM placeholder) snapped to the
    nearest walkable edge within LOCATION_SNAP_M; homes (demand/agents.json, from
    make_agents.py) to the nearest home street within HOME_SNAP_M, at its middle.
  * LEGS, by mode:
      - jeepney: of the lines serving the trip, the one with the shortest total walk. Boarding
        at the roadside spot with the SHORTEST WALK from the start (SUMO's pedestrian router),
        so people wait where their street meets the main road; alighting at the one with the
        shortest walk to the end, at least MIN_RIDE further along the line (a line runs one
        way). Both walks at most MAX_WALK,
      - walk: SUMO's pedestrian route, at most WALK_TRIP_LIMIT,
      - bike or car: the agent's own (has_bike / has_car in agents.json), which must be where
        the trip starts: it stays where it was last parked, at first at home. It stands on the
        nearest edge within PARK_SNAP_M of each place that it may use and people may walk on
        (most destinations are on service roads or footways, closed to cars). A car at home is
        at the house: no walk there. Otherwise the commuter walks between door and vehicle (at
        most MAX_WALK). A bike ride is at most BIKE_TRIP_LIMIT. A car trip waits CAR_ACCESS_S at
        the car before driving and CAR_PARKING_S after parking.
    Each leg gets a free-flow estimate of its time (est_s).
  * CHECKS: a file that breaks the format stops the conversion with a list of what is wrong. A
    trip that cannot be made as written (no line serves it, too far to walk, no car, the car is
    elsewhere) is reported, and that agent's whole day is left out.

Writes commuters.rou.xml (one <person> per trip, with params agent_id, trip_id, trip_type, mode
and est_s, and just before each car or bike trip its vehicle, depart="triggered": it leaves when
they get in; sorted by departure) and trips.json (the trips, readable). run.py converts the demand
file into each run's folder; run this to check a file on its own.

    python convert_demand.py [DEMAND] [--out DIR]   (default settings.DEMAND_FILE, generated/)
"""

import argparse
import csv
import json
import math
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import sumolib
import traci
from sumolib import geomhelper

import settings as S
from make_routes import norm

MODES = ("jeepney", "walk", "bike", "car")
VCLASS = {"bike": "bicycle", "car": "passenger"}      # vType id (= mode) -> SUMO vehicle class
LINE_COLORS = {"N1": ["1,0.3,0.3", "1,0.6,0.2"], "RIZ": ["0.3,0.6,1", "0.3,0.9,0.4"]}
MODE_COLORS = {"walk": "1,1,1", "bike": "0,1,0", "car": "1,0.2,1"}   # commuter cars: magenta, not grey
CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")    # HH:MM within the day
TOP_KEYS = {"day", "made_by", "agents"}
ENTRY_KEYS = {"location_id", "activity", "start", "end", "mode", "reasoning"}
PED_SPEED = 1.39      # m/s: SUMO's default walking speed (desiredMaxSpeed; TraCI cannot read it)
_router_speed = None  # m/s: the speed SUMO's pedestrian router assumes (PED_SPEED x persontrip.walkfactor)


# ---------------------------------------------------------------- network, places, walking
def kerb_edges(net, edges):
    """Route edges where a commuter can stand and a jeepney can stop: some lane allows
    pedestrians (a sidewalk, or a road lane people may walk on), some lane allows the jeepney,
    and the edge is long enough to stop KERB_MARGIN from both ends. Not the first edge (a
    jeepney enters there already moving, ~3 m short of a stop at KERB_MARGIN: too close to
    brake, so nobody waiting there was ever served) and not the last (its trip ends there).
    Returns [(edge, route offset of the edge's start)]."""
    result, offset = [], 0.0
    for i, edge_id in enumerate(edges):
        edge = net.getEdge(edge_id)
        lanes = edge.getLanes()
        if (any(l.allows("pedestrian") for l in lanes) and any(l.allows(S.JEEPNEY_VCLASS) for l in lanes)
                and 0 < i < len(edges) - 1 and edge.getLength() >= 2 * S.KERB_MARGIN + 5):
            result.append((edge, offset))
        offset += edge.getLength()
    return result


def kerb_spots(kerb):
    """KERB_MARGIN in from each end of every kerb edge. People reach an edge at one of its
    ends, so the spot with the shortest walk on an edge is always one of these two.
    Returns [(edge_id, pos, route_offset, xy)]."""
    spots = []
    for edge, start in kerb:
        shape = edge.getShape()
        scale = geomhelper.polyLength(shape) / edge.getLength()   # shape vs edge length
        for pos in (S.KERB_MARGIN, edge.getLength() - S.KERB_MARGIN):
            spots.append((edge.getID(), round(pos, 1), start + pos,
                          geomhelper.positionAtShapeOffset(shape, pos * scale)))
    return spots


class SpotGrid:
    """Spots bucketed in MAX_WALK-sized cells: every spot within MAX_WALK of a point lies in
    the 3x3 cells around it."""

    def __init__(self, spots):
        self.cells = defaultdict(list)
        for s in spots:
            self.cells[self.cell(s[3])].append(s)

    @staticmethod
    def cell(xy):
        return int(xy[0] // S.MAX_WALK), int(xy[1] // S.MAX_WALK)

    def near(self, xy):
        cx, cy = self.cell(xy)
        found = [s for dx in (-1, 0, 1) for dy in (-1, 0, 1) for s in self.cells.get((cx + dx, cy + dy), ())]
        return sorted((math.dist(xy, s[3]), s) for s in found if math.dist(xy, s[3]) <= S.MAX_WALK)


def walk(from_edge, from_pos, to_edge, to_pos):
    """(length, free-flow time) of SUMO's own pedestrian route, or None if there is none.
    findIntermodalRoute (SUMO 1.27) counts the whole first edge in a stage's length wherever the
    walk starts on it, and gives a walk along one edge the whole edge and no time. Its time is
    right, so the length comes from the time; along one edge, from the positions."""
    global _router_speed
    if from_edge == to_edge:
        length = abs(to_pos - from_pos)          # people walk either way along an edge
        return length, length / PED_SPEED
    stages = traci.simulation.findIntermodalRoute(from_edge, to_edge, departPos=from_pos, arrivalPos=to_pos)
    if not stages:
        return None
    if _router_speed is None:
        _router_speed = PED_SPEED * float(traci.simulation.getOption("persontrip.walkfactor"))
    length = sum(s.travelTime for s in stages) * _router_speed
    return length, length / PED_SPEED


def nearest_on_foot(place, grid, toward):
    """The spot with the shortest walk between place (edge, pos, xy) and the line: walking from
    place to the spot if toward, else from the spot to place. (edge_id, pos, route_offset,
    walk_m, walk_s), or None if none is within MAX_WALK on foot."""
    edge_id, pos, xy = place
    best = None
    for dist, (s_edge, s_pos, s_offset, _) in grid.near(xy):
        # nearest first; a walk is never shorter than the straight line (give or take a few
        # metres: people walk beside the centre lines measured here), so stop when none can win
        if dist - 10.0 > (best[3] if best else S.MAX_WALK):
            break
        w = walk(edge_id, pos, s_edge, s_pos) if toward else walk(s_edge, s_pos, edge_id, pos)
        if w is not None and w[0] <= S.MAX_WALK and (best is None or w[0] < best[3]):
            best = (s_edge, s_pos, s_offset, *w)
    return best


def place_on(edge, xy):
    """(edge_id, pos, xy) of the point on edge closest to xy; pos stays on the edge's shortest
    lane (a sidewalk can be a bit shorter than the road)."""
    shape = edge.getShape()
    offset = geomhelper.polygonOffsetWithMinimumDistanceToPoint(xy, shape)
    shortest = min(l.getLength() for l in edge.getLanes())
    pos = min(max(offset * edge.getLength() / geomhelper.polyLength(shape), 0.0), shortest - 0.1)
    return edge.getID(), round(pos, 1), geomhelper.positionAtShapeOffset(shape, offset)


def nearest_place(net, xy, vclasses, radius):
    """(place_on the nearest edge within radius that all vclasses may use, its distance), or None."""
    near = [(d, e) for e, d in net.getNeighboringEdges(*xy, r=radius) if all(e.allows(c) for c in vclasses)]
    if not near:
        return None
    dist, edge = min(near, key=lambda de: de[0])
    return place_on(edge, xy), dist


def home_streets(net, exclude):
    """{edge_id: (edge_id, pos, xy)} at the middle of every street people can live on: walkable,
    up to MAX_STREET_SPEED, at least 10 m, and not in exclude (the lines and main roads)."""
    streets = {}
    for edge in net.getEdges():
        if (edge.getID() in exclude or not edge.allows("pedestrian")
                or edge.getSpeed() > S.MAX_STREET_SPEED or edge.getLength() < 10):
            continue
        shape = edge.getShape()
        xy = geomhelper.positionAtShapeOffset(shape, geomhelper.polyLength(shape) / 2)
        streets[edge.getID()] = (edge.getID(), round(edge.getLength() / 2, 1), xy)
    return streets


def street_exclusions(net, lines):
    """Edges nobody lives on: the lines' edges and every edge of the main roads (HIGHWAYS)."""
    highway_names = {norm(name) for name in S.HIGHWAYS.values()}
    on_lines = {e for r in lines.values() for e in r["edges"]}
    return on_lines | {e.getID() for e in net.getEdges() if norm(e.getName()) in highway_names}


def in_osm_box(net, lon, lat):
    """Network x, y of a WGS84 point inside OSM_BBOX, or None. Outside it the network holds only
    the far ends of roads crossing the box, so people and places there have no streets to use."""
    lon0, lat0, lon1, lat1 = S.OSM_BBOX
    return net.convertLonLat2XY(lon, lat) if lon0 <= lon <= lon1 and lat0 <= lat <= lat1 else None


def load_destinations(net):
    """Activity locations inside OSM_BBOX snapped to the nearest walkable edge within
    LOCATION_SNAP_M, at the closest position on it. Returns ({location_id: {place, category,
    name, location_id, lat, lon, snap_m}}, dropped Counter by category)."""
    kept, dropped = {}, Counter()
    with open(S.ACTIVITY_LOCATIONS, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lat, lon = float(row["lat"]), float(row["lon"])
            xy = in_osm_box(net, lon, lat)
            if xy is None:
                continue
            found = nearest_place(net, xy, ["pedestrian"], S.LOCATION_SNAP_M)
            if found is None:
                dropped[row["category"]] += 1
                continue
            place, dist = found
            kept[row["location_id"]] = {"place": place, "category": row["category"], "name": row["name"],
                                        "location_id": row["location_id"], "lat": lat, "lon": lon, "snap_m": dist}
    return kept, dropped


def load_lines():
    path = S.GENERATED / "jeepney_routes.json"
    if not path.is_file():
        raise SystemExit("No jeepney lines yet: run python make_routes.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def load_agents():
    """{agent_id: agent} from demand/agents.json (make_agents.py)."""
    if not S.AGENTS_FILE.is_file():
        raise SystemExit(f"No agents yet ({S.AGENTS_FILE.relative_to(S.HERE)}): run python make_agents.py")
    return {a["agent_id"]: a for a in json.loads(S.AGENTS_FILE.read_text(encoding="utf-8"))["agents"]}


# ------------------------------------------------------------------------------- legs
class Planner:
    """What a trip's leg needs: the network, the lines, the places, and SUMO as a router. Start
    SUMO with `with planner:` before asking for legs."""

    def __init__(self):
        self.net = sumolib.net.readNet(str(S.NET_FILE))
        self.lines = load_lines()
        self.vtypes = S.GENERATED / "vtypes.add.xml"
        if not self.vtypes.is_file():
            raise SystemExit("No vehicle types yet: run python make_vtypes.py first")
        self.streets = home_streets(self.net, street_exclusions(self.net, self.lines))
        self.grids = {line: SpotGrid(kerb_spots(kerb_edges(self.net, r["edges"]))) for line, r in self.lines.items()}
        self.line_speed = {line: r["length_m"] / r["freeflow_s"] for line, r in self.lines.items()}   # m/s, free flow
        self.locations, self.dropped = load_destinations(self.net)
        self._spots, self._walks, self._drives = {}, {}, {}

    def __enter__(self):
        traci.start([sumolib.checkBinary("sumo"), "-n", str(S.NET_FILE), "-a", str(self.vtypes),
                     "--no-step-log", "true", "--no-warnings", "true"])
        return self

    def __exit__(self, *exc):
        traci.close()

    def home_place(self, home):
        """The home street nearest a home's lat/lon (within HOME_SNAP_M), at its middle; or None."""
        xy = self.net.convertLonLat2XY(home["lon"], home["lat"])
        near = [(d, e.getID()) for e, d in self.net.getNeighboringEdges(*xy, r=S.HOME_SNAP_M) if e.getID() in self.streets]
        return self.streets[min(near)[1]] if near else None

    def spot(self, line, place, toward):
        key = (line, place[0], place[1], toward)
        if key not in self._spots:
            self._spots[key] = nearest_on_foot(place, self.grids[line], toward)
        return self._spots[key]

    def on_foot(self, a, b):
        """Cached walk() between two places."""
        key = (a[0], a[1], b[0], b[1])
        if key not in self._walks:
            self._walks[key] = walk(a[0], a[1], b[0], b[1])
        return self._walks[key]

    def leg(self, mode, a, b, at_house=(False, False)):
        """The leg from place a to place b by mode: (leg dict, None), or (None, why not).
        at_house: whether a and b are the agent's home (a car is at the house there)."""
        if mode == "jeepney":
            return self.jeepney(a, b)
        if mode == "walk":
            return self.walk_trip(a, b)
        key = (mode, a[0], a[1], b[0], b[1], at_house)
        if key not in self._drives:
            self._drives[key] = self.vehicle_trip(a, b, mode, at_house)
        leg, why = self._drives[key]
        if leg and mode == "bike" and leg["length_m"] > S.BIKE_TRIP_LIMIT:
            return None, f"the ride is {leg['length_m']:.0f} m, over BIKE_TRIP_LIMIT ({S.BIKE_TRIP_LIMIT:.0f} m)"
        return leg, why

    def jeepney(self, a, b):
        """Of the lines serving a -> b, the one with the shortest total walk."""
        best = None
        for line in sorted(self.lines):
            board = self.spot(line, a, toward=True)
            alight = self.spot(line, b, toward=False) if board else None
            if alight is None or alight[2] - board[2] < S.MIN_RIDE:
                continue
            if best is None or board[3] + alight[3] < best["walk_m"]:
                ride_m = alight[2] - board[2]
                est = board[4] + S.JEEPNEY_HEADWAY_S / 2 + ride_m / self.line_speed[line] + alight[4]
                best = {"line": line, "board": board, "alight": alight, "walk_m": board[3] + alight[3],
                        "ride_m": ride_m, "est_s": est}
        if best is None:
            return None, (f"no line runs from within {S.MAX_WALK:.0f} m on foot of the start to within "
                          f"{S.MAX_WALK:.0f} m of the end, with at least {S.MIN_RIDE:.0f} m of ride")
        return best, None

    def walk_trip(self, a, b):
        straight = math.dist(a[2], b[2])
        if straight > S.WALK_TRIP_LIMIT:                      # a walk is never shorter
            return None, f"{straight:.0f} m apart in a straight line, over WALK_TRIP_LIMIT ({S.WALK_TRIP_LIMIT:.0f} m)"
        w = self.on_foot(a, b)
        if w is None:
            return None, "no walking route"
        if w[0] > S.WALK_TRIP_LIMIT:
            return None, f"the walk is {w[0]:.0f} m, over WALK_TRIP_LIMIT ({S.WALK_TRIP_LIMIT:.0f} m)"
        return {"walk_m": w[0], "est_s": w[1]}, None

    def vehicle_trip(self, a, b, mode, at_house):
        """A car or bike leg. The vehicle stands on the nearest edge to each place that it may
        use and people may walk on; a car is at the house (no walk there), otherwise the
        commuter walks between door and vehicle."""
        classes = (VCLASS[mode], "pedestrian")
        start = nearest_place(self.net, a[2], classes, S.PARK_SNAP_M)
        end = nearest_place(self.net, b[2], classes, S.PARK_SNAP_M)
        if start is None or end is None:
            return None, f"no road for a {mode} within PARK_SNAP_M ({S.PARK_SNAP_M:.0f} m) of the {'start' if start is None else 'end'}"
        p0, p1 = start[0], end[0]
        if p0[0] == p1[0]:
            return None, f"too short for a {mode}: it would start and end on the same edge"
        walk_a = not (mode == "car" and at_house[0])
        walk_b = not (mode == "car" and at_house[1])
        w0 = self.on_foot(a, p0) if walk_a else (0.0, 0.0)
        w1 = self.on_foot(p1, b) if walk_b else (0.0, 0.0)
        if w0 is None or w1 is None or max(w0[0], w1[0]) > S.MAX_WALK:
            return None, f"the {mode} would be over MAX_WALK ({S.MAX_WALK:.0f} m) on foot from the door"
        try:
            route = traci.simulation.findRoute(p0[0], p1[0], vType=mode)
        except traci.TraCIException:
            route = None
        if not route or not route.edges:
            return None, f"no {mode} route"
        # route.length runs from the start of the first edge to the end of the last
        length = route.length - p0[1] - (self.net.getEdge(p1[0]).getLength() - p1[1])
        waits = S.CAR_ACCESS_S + S.CAR_PARKING_S if mode == "car" else 0.0
        return {"from": p0, "to": p1, "edges": route.edges, "length_m": length,
                "walk_m": (w0[0] if walk_a else None, w1[0] if walk_b else None),
                "est_s": w0[1] + route.travelTime + waits + w1[1]}, None


# --------------------------------------------------------------------- the demand file
def seconds(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def clock(s):
    return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}"


def home_id(agent_id):
    return f"HOME_{agent_id}"


def check_format(demand, agents, location_ids):
    """Everything in a demand file that breaks the format (demand/README.md), as messages."""
    if not isinstance(demand, dict):
        return ["the file must be a JSON object"]
    errors = [f"top level: unknown key {k!r}" for k in sorted(set(demand) - TOP_KEYS)]
    if not isinstance(demand.get("day"), int) or isinstance(demand.get("day"), bool) or demand["day"] < 1:
        errors.append('top level: "day" must be a whole number from 1')
    if not isinstance(demand.get("agents"), list):
        return errors + ['top level: "agents" must be a list']
    seen = set()
    for i, agent in enumerate(demand["agents"]):
        if not isinstance(agent, dict):
            errors.append(f"agents[{i}]: must be an object")
            continue
        aid = agent.get("agent_id")
        errors += [f"agents[{i}]: unknown key {k!r}" for k in sorted(set(agent) - {"agent_id", "schedule"})]
        if aid not in agents:
            errors.append(f"agents[{i}]: agent_id {aid!r} is not in agents.json")
            continue
        if aid in seen:
            errors.append(f"{aid}: appears twice")
        seen.add(aid)
        schedule = agent.get("schedule")
        if not isinstance(schedule, list) or not schedule or not all(isinstance(e, dict) for e in schedule):
            errors.append(f'{aid}: "schedule" must be a non-empty list of objects')
            continue
        home, prev_end = home_id(aid), None
        for j, entry in enumerate(schedule):
            where = f"{aid} schedule[{j}]"
            errors += [f"{where}: unknown key {k!r}" for k in sorted(set(entry) - ENTRY_KEYS)]
            loc = entry.get("location_id")
            if loc != home and loc not in location_ids:
                errors.append(f"{where}: location_id {loc!r} is neither {home} nor a location in agents.json")
            if j == 0 and loc != home:
                errors.append(f"{where}: the day must start at home ({home})")
            moved = j > 0 and loc != schedule[j - 1].get("location_id")
            if moved and entry.get("mode") not in MODES:
                errors.append(f'{where}: "mode" must be one of {", ".join(MODES)}: how they get here')
            if not moved and "mode" in entry:
                errors.append(f'{where}: "mode" belongs only on an entry at a different place from the one before')
            end = entry.get("end")
            if end is None:
                if j < len(schedule) - 1:
                    errors.append(f'{where}: "end" (HH:MM) is needed on every entry but the last')
            elif not isinstance(end, str) or not CLOCK.match(end):
                errors.append(f'{where}: "end" must be HH:MM between 00:00 and 23:59, not {end!r}')
            else:
                if prev_end is not None and seconds(end) <= prev_end:
                    errors.append(f'{where}: "end" {end} is not after the previous entry\'s end')
                prev_end = seconds(end)
            if "start" in entry and (not isinstance(entry["start"], str) or not CLOCK.match(entry["start"])):
                errors.append(f'{where}: "start" must be HH:MM between 00:00 and 23:59, not {entry["start"]!r}')
            for key in ("activity", "reasoning"):
                if key in entry and not isinstance(entry[key], str):
                    errors.append(f'{where}: "{key}" must be text')
    return errors


def plan_day(demand, agents, planner):
    """The trips of every agent whose whole day can be made as written:
    ([trip, sorted by departure], {agent_id: why the day was left out})."""
    day, trips, problems = demand["day"], [], {}
    for agent in demand["agents"]:
        aid, info = agent["agent_id"], agents[agent["agent_id"]]
        home = planner.home_place(info["home"])
        if home is None:
            problems[aid] = f"no home street within HOME_SNAP_M ({S.HOME_SNAP_M:.0f} m) of the home"
            continue

        def place(loc):
            return home if loc == home_id(aid) else planner.locations[loc]["place"]

        def activity(entry):
            loc = entry["location_id"]
            return entry.get("activity") or ("home" if loc == home_id(aid) else planner.locations[loc]["category"])

        schedule, parked, mine, problem, k = agent["schedule"], {m: home_id(aid) for m in VCLASS}, [], None, 0
        for prev, entry in zip(schedule, schedule[1:]):
            a, b = prev["location_id"], entry["location_id"]
            if a == b:
                continue
            k += 1
            mode, trip_id = entry["mode"], f"{aid}_D{day:02d}_T{k:02d}"
            what = f"{trip_id} ({a} -> {b} by {mode}, {prev['end']})"
            if mode in VCLASS and not info[f"has_{mode}"]:
                problem = f"{what}: has no {mode} (has_{mode} is false in agents.json)"
                break
            if mode in VCLASS and parked[mode] != a:
                problem = f"{what}: the {mode} is at {parked[mode]}"
                break
            leg, why = planner.leg(mode, place(a), place(b), at_house=(a == home_id(aid), b == home_id(aid)))
            if leg is None:
                problem = f"{what}: {why}"
                break
            if mode in VCLASS:
                parked[mode] = b
            label = leg["line"] if mode == "jeepney" else mode
            mine.append({"id": f"{trip_id}_{label}", "agent": aid, "trip_id": trip_id, "day": day, "mode": mode,
                         "trip_type": f"{activity(prev)}_to_{activity(entry)}", "depart": seconds(prev["end"]),
                         "from_id": a, "to_id": b, "from": place(a), "to": place(b), "leg": leg,
                         "to_name": "home" if b == home_id(aid) else planner.locations[b]["name"] or "(unnamed)"})
        if problem:
            problems[aid] = problem
        else:
            trips += mine
    trips.sort(key=lambda t: (t["depart"], t["id"]))
    return trips, problems


# ------------------------------------------------------------------------------ output
def comment_text(s):
    return s.replace("--", "-")   # "--" may not appear inside an XML comment


def leg_text(mode, leg):
    if mode == "jeepney":
        return f'{leg["line"]}: walk {leg["board"][3]:.0f} m, ride {leg["ride_m"]:.0f} m, walk {leg["alight"][3]:.0f} m'
    if mode == "walk":
        return f'walk {leg["walk_m"]:.0f} m'
    walks = [f"{w:.0f} m" for w in leg["walk_m"] if w is not None]
    return f'{mode} {leg["length_m"]:.0f} m, walk{"s" if len(walks) > 1 else ""} {" + ".join(walks)}'


def walk_xml(a, b):
    return f'        <walk from="{a[0]}" to="{b[0]}" arrivalPos="{b[1]}"/>'


def trip_xml(trip, lines):
    """The <person> of one trip, preceded by its own vehicle for a car or bike trip."""
    a, b, leg, mode, pid = trip["from"], trip["to"], trip["leg"], trip["mode"], trip["id"]
    params = [f'        <param key="{k}" value="{v}"/>' for k, v in
              (("agent_id", trip["agent"]), ("trip_id", trip["trip_id"]), ("trip_type", trip["trip_type"]),
               ("mode", mode), ("est_s", round(leg["est_s"])))]
    head = (f'    <!-- {trip["trip_id"]} ({trip["trip_type"]}) at {clock(trip["depart"])}: {trip["from_id"]} -> '
            f'{trip["to_id"]} {comment_text(trip["to_name"])}; {leg_text(mode, leg)}; '
            f'estimate {leg["est_s"] / 60:.1f} min -->')
    if mode == "jeepney":
        prefix = leg["line"].split("_")[0]
        color = LINE_COLORS.get(prefix, ["1,1,1", "1,1,1"])[sorted(lines).index(leg["line"]) % 2]
        board, alight = leg["board"], leg["alight"]
        return [head, f'    <person id="{pid}" depart="{trip["depart"]}" departPos="{a[1]}" color="{color}">', *params,
                walk_xml(a, board),
                f'        <ride from="{board[0]}" to="{alight[0]}" lines="{leg["line"]}" arrivalPos="{alight[1]}"/>',
                walk_xml(alight, b), "    </person>"]
    if mode == "walk":
        return [head, f'    <person id="{pid}" depart="{trip["depart"]}" departPos="{a[1]}" color="{MODE_COLORS["walk"]}">',
                *params, walk_xml(a, b), "    </person>"]
    p0, p1, veh = leg["from"], leg["to"], f"{pid}_veh"
    walk_a, walk_b = (w is not None for w in leg["walk_m"])   # a car at the house: no walk there
    xml = [head,
           f'    <vehicle id="{veh}" type="{mode}" depart="triggered" departPos="{p0[1]}" departSpeed="0" '
           f'arrivalPos="{p1[1]}" color="{MODE_COLORS[mode]}">',
           f'        <route edges="{" ".join(leg["edges"])}"/>', "    </vehicle>",
           f'    <person id="{pid}" depart="{trip["depart"]}" departPos="{(a if walk_a else p0)[1]}" '
           f'color="{MODE_COLORS[mode]}">', *params]
    if walk_a:
        xml.append(walk_xml(a, p0))
    if mode == "car":
        xml.append(f'        <stop edge="{p0[0]}" endPos="{p0[1]}" duration="{S.CAR_ACCESS_S:.0f}" actType="getting the car"/>')
    xml.append(f'        <ride from="{p0[0]}" to="{p1[0]}" lines="{veh}" arrivalPos="{p1[1]}"/>')
    if mode == "car":
        xml.append(f'        <stop edge="{p1[0]}" endPos="{p1[1]}" duration="{S.CAR_PARKING_S:.0f}" actType="parking"/>')
    if walk_b:
        xml.append(walk_xml(p1, b))
    return xml + ["    </person>"]


def trip_record(trip):
    """A trip in the shape of the plan's trip record (Part D.4), with its estimate."""
    leg = trip["leg"]
    return {"trip_id": trip["trip_id"], "agent_id": trip["agent"], "day": trip["day"],
            "origin_location_id": trip["from_id"], "destination_location_id": trip["to_id"],
            "departure": clock(trip["depart"]), "departure_time_s": trip["depart"], "trip_type": trip["trip_type"],
            "mode": trip["mode"], "route_id": leg.get("line"), "estimated_total_time_s": round(leg["est_s"]),
            "sumo_person_id": trip["id"]}


def convert(demand_path, out_dir):
    """Check the demand file and write commuters.rou.xml and trips.json into out_dir (with a
    copy of the demand file when out_dir is a run folder). Returns the commuters.rou.xml path."""
    demand_path, out_dir = Path(demand_path), Path(out_dir)
    if not demand_path.is_file():
        raise SystemExit(f"No demand file at {demand_path}: write one (format in demand/README.md), "
                         "or run python llm_stub.py for a stand-in")
    try:
        demand = json.loads(demand_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{demand_path.name} is not valid JSON: {e}")
    agents = load_agents()
    planner = Planner()
    errors = check_format(demand, agents, set(planner.locations))
    if errors:
        more = f"\n  ... and {len(errors) - 30} more" if len(errors) > 30 else ""
        raise SystemExit(f"{demand_path.name} does not follow the demand format (demand/README.md):\n  "
                         + "\n  ".join(errors[:30]) + more)
    with planner:
        trips, problems = plan_day(demand, agents, planner)

    lines = planner.lines
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           f"<!-- Generated by convert_demand.py from {demand_path.name} (day {demand['day']}, made by "
           f"{comment_text(str(demand.get('made_by', 'unknown')))}). Do not edit: change the demand file.",
           "     One <person> per trip (params: agent_id, trip_id, trip_type, mode, est_s = free-flow estimate):",
           "       * jeepney: walk to the roadside, flag a jeepney on the line, ride, walk on. The first",
           "         walk's arrivalPos is the boarding spot and the ride's arrivalPos the alighting",
           "         spot: run.py reads both from here,",
           "       * walk: one walk,",
           "       * bike or car: the commuter's own vehicle (listed just before them, depart=triggered:",
           "         it leaves when they get in). A bike rider walks to it and from it to the door; a",
           "         car is at the house (no walk there) and its driver walks only elsewhere. A car",
           "         trip also waits at the car before driving and after parking.",
           "     Sorted by depart: SUMO drops out-of-order entries. -->",
           "<routes>"]
    for trip in trips:
        xml += trip_xml(trip, lines)
    xml.append("</routes>")
    out_dir.mkdir(parents=True, exist_ok=True)
    rou = out_dir / "commuters.rou.xml"
    rou.write_text("\n".join(xml) + "\n", encoding="utf-8")
    (out_dir / "trips.json").write_text(json.dumps([trip_record(t) for t in trips], indent=2) + "\n", encoding="utf-8")
    if out_dir.resolve() != demand_path.parent.resolve() and out_dir.resolve() != S.GENERATED.resolve():
        shutil.copy2(demand_path, out_dir / demand_path.name)     # a run keeps the demand it ran

    n_agents = len(demand["agents"])
    per_mode = Counter(t["mode"] for t in trips)
    print(f"Demand file {demand_path.name} (day {demand['day']}, made by {demand.get('made_by', 'unknown')}): "
          f"{n_agents} agents, {n_agents - len(problems)} kept, {len(trips)} trips ("
          + ", ".join(f"{m} {per_mode[m]}" for m in MODES) + ")")
    jeep = Counter(t["leg"]["line"] for t in trips if t["mode"] == "jeepney")
    if jeep:
        print("  jeepney trips by line: " + ", ".join(f"{k} {v}" for k, v in sorted(jeep.items())))
    if problems:
        print(f"  {len(problems)} agents left out, the whole day (a trip cannot be made as written):")
        for aid, why in sorted(problems.items()):
            print(f"    {aid}: {why}")
    print(f"Wrote {rou} and trips.json")
    return rou


def main():
    parser = argparse.ArgumentParser(description="Check a demand file and turn it into SUMO input.")
    parser.add_argument("demand", nargs="?", default=str(S.DEMAND_FILE), help="the demand file (JSON)")
    parser.add_argument("--out", default=str(S.GENERATED), help="folder for commuters.rou.xml and trips.json")
    args = parser.parse_args()
    convert(args.demand, args.out)


if __name__ == "__main__":
    main()
