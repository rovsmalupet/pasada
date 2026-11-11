"""Background traffic: cars and motorcycles that are not commuters (no LLM), and the passengers
jeepneys bring in from outside the study area. Every rate is a stand-in in settings.py until the
group's video counts replace it.

  * inside trips: random trips between edges inside the study area, BACKGROUND_PER_HOUR x
    BACKGROUND_SCALE per type at the peak;
  * gateway trips: at each GATEWAYS road end, its IN vehicles come in from outside:
    GATEWAY_THROUGH_SHARE of them drive through to another gateway (picked in proportion to its OUT
    traffic), the rest end at a random edge inside. Its OUT vehicles leave there: the through
    traffic expected from the other gateways, and the rest from a random edge inside. All come in
    and go out at the road's cut end (make_map.py), APPROACH_M outside the area;
  both by the hour, scaled by BACKGROUND_PROFILE and spread evenly over the hour. Origins and
  destinations inside are drawn at random, every edge the type may use equally likely, at least
  BACKGROUND_MIN_TRIP_M apart in a straight line. SUMO routes each trip when it departs and drops
  trips that have no route. Until 2026-11-12 this was SUMO's randomTrips.py over the whole map;
  ported from ../sumo_track_prototype (which has no profile).
  * jeepney riders: every jeepney of generated/jeepney.rou.xml comes in with passengers aboard:
    each seat is taken with probability JEEPNEY_ENTRY_LOAD x BACKGROUND_PROFILE[hour]. A rider
    gets off at a random roadside spot of the line inside the area, or (RIDER_THROUGH_SHARE) at
    its last spot, past the area; run.py books their stops. SUMO puts a person with
    depart="triggered" aboard when its jeepney departs, so the riders are written right after
    their jeepney, into a copy of the jeepney file.

run.py loads these unless --no-background (then generated/jeepney.rou.xml, without riders).

Writes generated/background_car.trips.xml, generated/background_motorcycle.trips.xml and
generated/jeepney_loaded.rou.xml.

    python make_background.py
"""

import json
import math
import random
import xml.etree.ElementTree as ET
from collections import Counter

import sumolib
from shapely.geometry import Point

import settings as S
from convert_demand import kerb_edges, kerb_spots
from make_routes import OPPOSITE, midpoint, road_paths, study_area

EDGE_CLASS = {"car": "passenger", "motorcycle": "motorcycle"}   # vType id -> SUMO vClass
LOADED_JEEPNEYS = S.GENERATED / "jeepney_loaded.rou.xml"        # the jeepneys with their riders


def trips_file(vtype_id):
    return S.GENERATED / f"background_{vtype_id}.trips.xml"


def hourly(peak):
    """peak x BACKGROUND_PROFILE, hour by hour over the run."""
    return [peak * S.BACKGROUND_PROFILE[h % 24] for h in range(math.ceil(S.END_S / 3600))]


def spread(rates, rng):
    """Departure times, rates[h] per hour, evenly spaced (what is left of an hour's last gap
    carries over to the next, so fractions add up)."""
    times, carry = [], rng.random()
    for h, rate in enumerate(rates):
        if rate <= 0:
            carry = 0.0
            continue
        gap = 3600.0 / rate
        t = carry * gap
        while t < 3600.0:
            times.append(h * 3600 + t)
            t += gap
        carry = (t - 3600.0) / gap
    return times


class Ends:
    """Where trips of one vClass may start and end inside the area: random edges at least
    BACKGROUND_MIN_TRIP_M apart."""

    def __init__(self, net, area, vclass):
        inside = [e for e in net.getEdges() if e.getFunction() != "internal" and e.allows(vclass)
                  and area.contains(Point(midpoint(e)))]
        self.sources = [e for e in inside if any(n.allows(vclass) for n in e.getOutgoing())]
        self.sinks = [e for e in inside if any(p.allows(vclass) for p in e.getIncoming())]

    def pair(self, rng, origin=None, destination=None):
        """(origin, destination): the given ones, the others drawn at random."""
        for _ in range(50):
            a = origin or rng.choice(self.sources)
            b = destination or rng.choice(self.sinks)
            if a != b and math.dist(midpoint(a), midpoint(b)) >= S.BACKGROUND_MIN_TRIP_M:
                return a, b
        raise SystemExit(f"No background trip found from {origin and origin.getID()} to {destination and destination.getID()}")


def gateways(net, area):
    """{name: (entry edge, exit edge)}: where each GATEWAYS road comes in from outside."""
    out = {}
    for name, (road, direction, *_) in S.GATEWAYS.items():
        paths, _ = road_paths(net, road, area)
        if direction not in paths:
            raise SystemExit(f"GATEWAYS['{name}']: {road} runs {' or '.join(paths)}, not {direction}")
        entry, exit_ = paths[direction][0], paths[OPPOSITE[direction]][-1]
        out[name] = (entry, exit_)
        print(f"  gateway {name}: in on {entry.getID()}, {area.distance(Point(entry.getFromNode().getCoord())):.0f} m "
              f"outside; out on {exit_.getID()}")
    return out


def write_trips(net, area, gates, vtype_id, rng):
    ends = Ends(net, area, EDGE_CLASS[vtype_id])
    trips, kinds = [], Counter()                     # (depart, from, to, coming in from outside)
    for t in spread(hourly(S.BACKGROUND_PER_HOUR[vtype_id] * S.BACKGROUND_SCALE), rng):
        trips.append((t, *ends.pair(rng), False))
        kinds["inside"] += 1
    peak_in = {name: S.GATEWAYS[name][2][vtype_id] for name in gates}
    peak_out = {name: S.GATEWAYS[name][3][vtype_id] for name in gates}
    others = {name: [g for g in gates if g != name] for name in gates}
    for name, (entry, exit_) in gates.items():
        for t in spread(hourly(peak_in[name]), rng):
            if others[name] and rng.random() < S.GATEWAY_THROUGH_SHARE:
                to = gates[rng.choices(others[name], weights=[peak_out[g] for g in others[name]])[0]][1]
                kinds["through"] += 1
            else:
                to = ends.pair(rng, origin=entry)[1]
                kinds["in"] += 1
            trips.append((t, entry, to, True))
        # going out here: the through traffic expected from the other gateways, the rest from inside
        through = sum(peak_in[g] * S.GATEWAY_THROUGH_SHARE * peak_out[name] / sum(peak_out[k] for k in others[g])
                      for g in others[name])
        for t in spread(hourly(max(0.0, peak_out[name] - through)), rng):
            trips.append((t, ends.pair(rng, destination=exit_)[0], exit_, False))
            kinds["out"] += 1
    trips.sort(key=lambda x: x[0])
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           f"<!-- Generated by make_background.py. Do not edit: rerun it. {vtype_id}: {kinds['inside']} inside trips, "
           f"{kinds['in']} coming in at a gateway and ending inside, {kinds['through']} through, {kinds['out']} leaving. -->",
           "<routes>"]
    for i, (t, a, b, entering) in enumerate(trips):
        speed = ' departSpeed="max"' if entering else ""    # coming in from outside: already moving
        xml.append(f'    <trip id="{vtype_id}_{i}" type="{vtype_id}" depart="{t:.2f}" from="{a.getID()}" '
                   f'to="{b.getID()}" departLane="best"{speed}/>')
    xml.append("</routes>")
    trips_file(vtype_id).write_text("\n".join(xml) + "\n", encoding="utf-8")
    peak_h = max(range(24), key=lambda h: S.BACKGROUND_PROFILE[h])
    print(f"  {vtype_id}: {len(trips):,} trips ({', '.join(f'{k} {v:,}' for k, v in kinds.items())}); "
          f"{sum(1 for x in trips if int(x[0] // 3600) == peak_h):,} start in the peak hour -> generated/{trips_file(vtype_id).name}")


def write_riders(net, area, rng):
    """generated/jeepney_loaded.rou.xml: the jeepneys, each followed by the riders it comes in with."""
    lines = json.loads((S.GENERATED / "jeepney_routes.json").read_text(encoding="utf-8"))
    capacity = int(S.JEEPNEY["personCapacity"])
    spots, last = {}, {}
    for line, r in lines.items():
        all_spots = kerb_spots(kerb_edges(net, r["edges"]))
        spots[line] = [s for s in all_spots if area.contains(Point(s[3]))]
        last[line] = max(all_spots, key=lambda s: s[2])
    root = ET.parse(S.GENERATED / "jeepney.rou.xml").getroot()
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           "<!-- Generated by make_background.py from jeepney.rou.xml. Do not edit: rerun it. Each jeepney is "
           "followed by the passengers it comes in with (depart=\"triggered\": aboard when it departs). -->", "<routes>"]
    xml += [f'    <route id="{r.get("id")}" edges="{r.get("edges")}"/>' for r in root.iter("route")]
    riders, through, per_hour = 0, 0, Counter()
    for v in root.iter("vehicle"):
        xml.append("    " + ET.tostring(v, encoding="unicode").strip())
        depart, line = float(v.get("depart")), v.get("line")
        p = S.JEEPNEY_ENTRY_LOAD * S.BACKGROUND_PROFILE[int(depart // 3600) % 24]
        for i in range(sum(rng.random() < p for _ in range(capacity))):
            off_past = rng.random() < S.RIDER_THROUGH_SHARE
            edge, pos = (last[line] if off_past else rng.choice(spots[line]))[:2]
            xml.append(f'    <person id="{v.get("id")}_r{i}" depart="triggered"><ride to="{edge}" arrivalPos="{pos}" '
                       f'lines="{v.get("id")}"/></person>')
            riders += 1
            through += off_past
            per_hour[int(depart // 3600)] += 1
    xml.append("</routes>")
    LOADED_JEEPNEYS.write_text("\n".join(xml) + "\n", encoding="utf-8")
    jeepneys = len(root.findall("vehicle"))
    peak_h = max(range(24), key=lambda h: S.BACKGROUND_PROFILE[h])
    print(f"  jeepney riders: {riders:,} on {jeepneys:,} jeepneys ({riders / jeepneys:.1f} each; "
          f"{per_hour[peak_h] / (3600 / S.JEEPNEY_HEADWAY_S * len(lines)):.1f} in the peak hour), {through:,} riding on "
          f"out of the area -> generated/{LOADED_JEEPNEYS.name}")


def main():
    S.GENERATED.mkdir(exist_ok=True)
    net = sumolib.net.readNet(str(S.NET_FILE))
    area = study_area(net)
    rng = random.Random(S.SEED)
    gates = gateways(net, area)
    for vtype_id in S.BACKGROUND_PER_HOUR:
        write_trips(net, area, gates, vtype_id, rng)
    write_riders(net, area, rng)


if __name__ == "__main__":
    main()
