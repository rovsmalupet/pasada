"""The agents and places the LLM is given to write a demand file: demand/agents.json.

  * AGENTS (the plan's base commuter agents, made by logic, not by the LLM): N, seeded. Each
    lives on a home street drawn in proportion to its people: POPULATION_GRID's people per
    ~30 m cell (Meta 2020, the data team's extraction) go to the nearest home street within
    HOME_SNAP_M, a walkable street up to MAX_STREET_SPEED that is not on a line or a main road.
    The home is the street's middle, given as lat/lon. Each has a car with chance CAR_ACCESS
    and a bike with chance BIKE_ACCESS (stand-ins until the LLM team's personas).
  * LOCATIONS: the activity locations they can go to (ACTIVITY_LOCATIONS inside OSM_BBOX that
    snap to a walkable edge), with id, category, name and lat/lon.
Only inside OSM_BBOX, the Web Wizard's download box: outside it the network has only the far
ends of roads crossing the box, not the streets people live on.

A demand file refers to these agents, so this refuses to overwrite agents.json unless --force
(then rewrite the demand file too).

    python make_agents.py [N] [--force]          (default settings.N_COMMUTERS)
"""

import argparse
import csv
import json
import random
from collections import Counter, defaultdict

import sumolib

import settings as S
from convert_demand import home_id, home_streets, in_osm_box, load_destinations, load_lines, street_exclusions


def load_population(net, streets):
    """People per home street: each grid cell's people go to the nearest home street within
    HOME_SNAP_M. Returns ({edge_id: people}, report)."""
    weights = defaultdict(float)
    cells = people = dropped = 0.0
    with open(S.POPULATION_GRID, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            n = float(row["phl_general_2020"])
            xy = in_osm_box(net, float(row["longitude"]), float(row["latitude"])) if n > 0 else None
            if xy is None:
                continue
            cells += 1
            people += n
            near = [(d, e.getID()) for e, d in net.getNeighboringEdges(*xy, r=S.HOME_SNAP_M) if e.getID() in streets]
            if near:
                weights[min(near)[1]] += n
            else:
                dropped += n
    return dict(weights), {"cells": int(cells), "people": people, "dropped": dropped}


def main():
    parser = argparse.ArgumentParser(description="Make the agents and places the LLM is given.")
    parser.add_argument("n", nargs="?", type=int, default=S.N_COMMUTERS, help="number of agents")
    parser.add_argument("--force", action="store_true", help="overwrite demand/agents.json")
    args = parser.parse_args()
    if S.AGENTS_FILE.is_file() and not args.force:
        raise SystemExit(f"{S.AGENTS_FILE.relative_to(S.HERE)} exists, and a demand file refers to its agents. "
                         "Use --force to replace it, then rewrite the demand file.")

    rng = random.Random(S.SEED)
    net = sumolib.net.readNet(str(S.NET_FILE))
    streets = home_streets(net, street_exclusions(net, load_lines()))
    weights, pop = load_population(net, streets)
    home_ids = sorted(weights)
    home_w = [weights[e] for e in home_ids]
    print(f"Population grid: {pop['cells']} cells with people in OSM_BBOX, {pop['people']:,.0f} people; "
          f"{pop['people'] - pop['dropped']:,.0f} on {len(home_ids)} home streets, {pop['dropped']:,.0f} "
          f"dropped (no home street within {S.HOME_SNAP_M:.0f} m)")
    locations, dropped = load_destinations(net)

    agents = []
    for i in range(args.n):
        street = streets[rng.choices(home_ids, weights=home_w)[0]]
        lon, lat = net.convertXY2LonLat(*street[2])
        aid = f"A{i + 1:04d}"
        agents.append({"agent_id": aid, "home_id": home_id(aid), "home": {"lat": round(lat, 6), "lon": round(lon, 6)},
                       "has_car": rng.random() < S.CAR_ACCESS, "has_bike": rng.random() < S.BIKE_ACCESS})
    places = [{"location_id": loc["location_id"], "category": loc["category"], "name": loc["name"],
               "lat": loc["lat"], "lon": loc["lon"]} for loc in sorted(locations.values(), key=lambda l: l["location_id"])]

    made_by = f"make_agents.py, seed {S.SEED}"
    about = ("Base agents (homes from the population grid; car and bike are stand-ins) and the activity "
             "locations they can go to. A demand file (README.md) refers to agent_id, home_id and location_id.")

    def block(items):   # one agent or location per line: readable, and compact enough for an LLM prompt
        return ",\n    ".join(json.dumps(x, ensure_ascii=False) for x in items)

    S.AGENTS_FILE.parent.mkdir(exist_ok=True)
    S.AGENTS_FILE.write_text(
        "{\n" f'  "made_by": {json.dumps(made_by)},\n  "about": {json.dumps(about)},\n'
        f'  "agents": [\n    {block(agents)}\n  ],\n  "locations": [\n    {block(places)}\n  ]\n' "}\n",
        encoding="utf-8")
    json.loads(S.AGENTS_FILE.read_text(encoding="utf-8"))   # check it is valid JSON
    cars, bikes = sum(a["has_car"] for a in agents), sum(a["has_bike"] for a in agents)
    print(f"{len(agents)} agents ({cars} with a car, {bikes} with a bike) and {len(places)} locations ("
          + ", ".join(f"{k} {v}" for k, v in sorted(Counter(p["category"] for p in places).items()))
          + f"; {sum(dropped.values())} dropped: no walkable edge within {S.LOCATION_SNAP_M:.0f} m)")
    print(f"Wrote {S.AGENTS_FILE.relative_to(S.HERE)}")


if __name__ == "__main__":
    main()
