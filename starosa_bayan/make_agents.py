"""The agents and places the LLM is given: demand/agents.json.

  * AGENTS: the LLM team's base commuter agents (../../PASADA-llm/base_commuter_agents: census-based
    age group, sex, barangay, employment, school, occupation, income decile, household car, bicycle
    and motorcycle access; made by logic, never by the LLM). Copied into BASE_AGENTS_FILE with
    their provenance whenever the source changes, so this folder keeps the agents it used.
    Kept: those living in the study area's barangays (AREA_BARANGAYS), except NOT_TRAVELLING age
    groups; SAMPLE_AGENTS of them drawn at random (seeded), or all with 0.
    Each gets a HOME in their own barangay: a home street drawn in proportion to its people.
    POPULATION_GRID's people per ~30 m cell (Meta 2020) inside the barangay go to the nearest home
    street within HOME_SNAP_M (a walkable street up to MAX_STREET_SPEED, not on a line or main
    road). The home is the street's middle, given as lat/lon.
  * LOCATIONS: the activity locations they can go to (ACTIVITY_LOCATIONS inside the study area
    that snap to a walkable edge), with id, category, name, barangay and lat/lon.
Whether an agent may use the household's car or bicycle is the persona's (llm_tasks.py); until
there is one, convert_demand.load_agents uses the household's access.

Everything after refers to these agents, so this refuses to overwrite agents.json unless --force
(then start the pipeline again: python pipeline.py --redo persona).

    python make_agents.py [N] [--force]          (default settings.SAMPLE_AGENTS; 0 = all)
"""

import argparse
import csv
import hashlib
import json
import random
import shutil
from collections import Counter, defaultdict
from datetime import date

import sumolib
from shapely.geometry import Point, shape
from shapely.prepared import prep

import settings as S
from convert_demand import home_id, home_streets, in_area, load_destinations, load_lines, street_exclusions

# base agent fields given on to the LLM tasks (the rest are source labels, kept in BASE_AGENTS_FILE)
BASE_FIELDS = ("age_group", "sex", "commuter_type", "employment_status", "school_status", "occupation_category",
               "per_capita_income_decile", "household_income_group", "household_car_access",
               "household_bicycle_access", "household_motorcycle_access")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def base_agents():
    """The base agents, copied from BASE_AGENTS_SOURCE first if it is there and has changed."""
    provenance = S.BASE_AGENTS_FILE.with_suffix(".provenance.json")
    source = S.BASE_AGENTS_SOURCE
    if source.is_file() and (not S.BASE_AGENTS_FILE.is_file() or sha256(source) != sha256(S.BASE_AGENTS_FILE)):
        shutil.copy2(source, S.BASE_AGENTS_FILE)
        meta_path = source.with_name("generation_metadata.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        provenance.write_text(json.dumps({
            "source": "PASADA-llm/base_commuter_agents/output/base_agents.json (the LLM team's generator)",
            "copied_on": date.today().isoformat(), "sha256": sha256(S.BASE_AGENTS_FILE),
            "generation": {k: meta.get(k) for k in ("generation_timestamp_utc", "random_seed",
                                                    "actual_generated_population_size")}}, indent=2) + "\n",
            encoding="utf-8")
        print(f"Copied the base agents from {source} into {S.rel(S.BASE_AGENTS_FILE)}")
    if not S.BASE_AGENTS_FILE.is_file():
        raise SystemExit(f"No base agents: neither {source} (run PASADA-llm's generate_base_agents.py) "
                         f"nor {S.rel(S.BASE_AGENTS_FILE)}")
    return json.loads(S.BASE_AGENTS_FILE.read_text(encoding="utf-8"))


def barangay_name(name):
    """A barangay's name as the base agents give it (the census's), from either source."""
    name = name.replace(" (Pob.)", "")
    return S.BARANGAY_ALIASES.get(name, name)


def area_barangays():
    """{name: prepared polygon} of the study area's barangays (AREA_BARANGAYS)."""
    features = json.loads(S.BARANGAYS_FILE.read_text(encoding="utf-8"))["features"]
    return {barangay_name(f["properties"]["adm4_name"]): prep(shape(f["geometry"]))
            for f in features if f["properties"]["adm4_pcode"] in S.AREA_BARANGAYS}


def which(barangays, lon, lat):
    point = Point(lon, lat)
    return next((name for name, poly in barangays.items() if poly.contains(point)), None)


def load_population(net, streets, barangays):
    """People per home street, by barangay: each grid cell's people go to the nearest home street
    within HOME_SNAP_M. Returns ({barangay: {edge_id: people}}, report)."""
    weights = defaultdict(lambda: defaultdict(float))
    report = Counter()
    with open(S.POPULATION_GRID, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            n, lon, lat = float(row["phl_general_2020"]), float(row["longitude"]), float(row["latitude"])
            xy = in_area(net, lon, lat) if n > 0 else None
            brgy = which(barangays, lon, lat) if xy else None
            if brgy is None:
                continue
            report["people"] += n
            near = [(d, e.getID()) for e, d in net.getNeighboringEdges(*xy, r=S.HOME_SNAP_M) if e.getID() in streets]
            if near:
                weights[brgy][min(near)[1]] += n
            else:
                report["dropped"] += n
    return weights, report


def main():
    parser = argparse.ArgumentParser(description="Make the agents and places the LLM is given.")
    parser.add_argument("n", nargs="?", type=int, default=S.SAMPLE_AGENTS, help="number of agents (0: all)")
    parser.add_argument("--force", action="store_true", help="overwrite demand/agents.json")
    args = parser.parse_args()
    if S.AGENTS_FILE.is_file() and not args.force:
        raise SystemExit(f"{S.rel(S.AGENTS_FILE)} exists, and everything after refers to its agents. "
                         "Use --force to replace it, then start again: python pipeline.py --redo persona")

    rng = random.Random(S.SEED)
    base = base_agents()
    barangays = area_barangays()
    in_area_ = [a for a in base if barangay_name(a["residential_barangay"]) in barangays]
    travelling = [a for a in in_area_ if a["age_group"] not in S.NOT_TRAVELLING]
    chosen = sorted(rng.sample(travelling, args.n) if 0 < args.n < len(travelling) else travelling,
                    key=lambda a: a["agent_id"])
    print(f"Base agents: {len(base)}; {len(in_area_)} live in the study area's {len(barangays)} barangays, "
          f"{len(travelling)} of them not {'/'.join(S.NOT_TRAVELLING)}; {len(chosen)} taken")

    net = sumolib.net.readNet(str(S.NET_FILE))
    streets = home_streets(net, street_exclusions(net, load_lines()))
    weights, pop = load_population(net, streets, barangays)
    print(f"Population grid: {pop['people']:,.0f} people in the study area's barangays, {pop['dropped']:,.0f} with "
          f"no home street within {S.HOME_SNAP_M:.0f} m")

    agents = []
    for a in chosen:
        brgy = barangay_name(a["residential_barangay"])
        ids = sorted(weights[brgy])
        if not ids:
            raise SystemExit(f"{brgy}: no home street with people from the population grid")
        street =streets[rng.choices(ids, weights=[weights[brgy][e] for e in ids])[0]]
        lon, lat = net.convertXY2LonLat(*street[2])
        agents.append({"agent_id": a["agent_id"], "home_id": home_id(a["agent_id"]),
                       "home": {"lat": round(lat, 6), "lon": round(lon, 6)}, "barangay": a["residential_barangay"],
                       **{k: a[k] for k in BASE_FIELDS}})
    locations, dropped = load_destinations(net)
    places = [{"location_id": loc["location_id"], "category": loc["category"], "name": loc["name"],
               "barangay": which(barangays, loc["lon"], loc["lat"]), "lat": loc["lat"], "lon": loc["lon"]}
              for loc in sorted(locations.values(), key=lambda l: l["location_id"])]

    made_by = f"make_agents.py, seed {S.SEED}, from {S.rel(S.BASE_AGENTS_FILE)}"
    about = ("Base commuter agents (the LLM team's, census-based) living in the study area, each with a home in "
             "their barangay, and the activity locations they can go to. Everything after refers to agent_id, "
             "home_id and location_id.")

    def block(items):   # one agent or location per line: readable, and compact enough for an LLM prompt
        return ",\n    ".join(json.dumps(x, ensure_ascii=False) for x in items)

    S.AGENTS_FILE.parent.mkdir(exist_ok=True)
    S.AGENTS_FILE.write_text(
        "{\n" f'  "made_by": {json.dumps(made_by)},\n  "about": {json.dumps(about)},\n'
        f'  "agents": [\n    {block(agents)}\n  ],\n  "locations": [\n    {block(places)}\n  ]\n' "}\n",
        encoding="utf-8")
    json.loads(S.AGENTS_FILE.read_text(encoding="utf-8"))   # check it is valid JSON
    print(f"{len(agents)} agents (" + ", ".join(f"{k} {v}" for k, v in sorted(Counter(a['commuter_type'] for a in agents).items()))
          + f"; household car {sum(a['household_car_access'] for a in agents)}, bicycle "
          f"{sum(a['household_bicycle_access'] for a in agents)}) and {len(places)} locations ("
          + ", ".join(f"{k} {v}" for k, v in sorted(Counter(p["category"] for p in places).items()))
          + f"; {sum(dropped.values())} dropped: no walkable edge within {S.LOCATION_SNAP_M:.0f} m)")
    print(f"Wrote {S.rel(S.AGENTS_FILE)}")


if __name__ == "__main__":
    main()
