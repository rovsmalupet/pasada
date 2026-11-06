"""The options the LLM chooses from: every way each trip of the schedule can be made, with its
numbers (thesis plan B.3, daily loop step 3 "candidate mode preparation"; Plan Phase 4).

Reads the schedule (step 1, settings.SCHEDULE_FILE: who goes where and when, no modes) and writes
the choices file (step 2, settings.CHOICES_FILE). For each trip (the fixed rule: two consecutive
schedule entries at different places), one option per way it can be made:
  * jeepney: one per line serving it (at most MAX_WALK on foot at each end, at least MIN_RIDE of
    ride in the line's direction),
  * walk: if the walking route is at most WALK_TRIP_LIMIT,
  * bike, car: if the agent has one (agents.json); a bike ride at most BIKE_TRIP_LIMIT.
Each option has its distance, its estimated time on empty roads with the parts (walking to it,
waiting, riding or driving, walking on) and its cost (convert_demand.alternative). Ways a trip
cannot be made are left out: the LLM never sees them (Plan Phase 4). Only this code sets the
values; the LLM only chooses (Sec. 4.3.1), writing the modes file (step 3, format in
demand/README.md).

One rule the options cannot show: a bike or car trip needs the vehicle where the trip starts (at
home at first, then where it was last parked), so one trip's choice limits the next. The file
states it, and the converter checks it.

    python make_choices.py [SCHEDULE] [--out FILE]   (defaults: settings.SCHEDULE_FILE, CHOICES_FILE)
"""

import argparse
import json
from collections import Counter
from pathlib import Path

import settings as S
from convert_demand import MODES, VCLASS, Planner, alternative, check_format, home_id, load_agents, load_json, trips_of

RULES = [
    "Choose one option per trip: its mode, and for a jeepney its route_id (the line).",
    "Times are estimates on empty roads, in seconds. estimated_total_time_s is the sum of access (walking to "
    "the jeepney or vehicle, and getting the car), waiting (for a jeepney), in-vehicle and egress (parking, "
    "walking on) times; a walk has only the total.",
    "Distances are in metres; access_walk_m and egress_walk_m are the walks at each end. cost_php is the jeepney "
    "fare or the car's running cost.",
    "A bike or car trip needs the vehicle where the trip starts: at home at the start of the day, then where it "
    "was last left. Someone who walks to work cannot drive home.",
]


def main():
    parser = argparse.ArgumentParser(description="List the options for every trip of a schedule.")
    parser.add_argument("schedule", nargs="?", default=str(S.SCHEDULE_FILE), help="the schedule (step 1, JSON)")
    parser.add_argument("--out", default=str(S.CHOICES_FILE), help="the choices file to write")
    args = parser.parse_args()
    schedule_path, out = Path(args.schedule), Path(args.out)
    schedule = load_json(schedule_path, "write one (format in demand/README.md), or run python pipeline.py")
    agents = load_agents()
    planner = Planner()
    errors = check_format(schedule, agents, set(planner.locations), with_modes=False)
    if errors:
        more = f"\n  ... and {len(errors) - 30} more" if len(errors) > 30 else ""
        raise SystemExit(f"{schedule_path.name} does not follow the schedule format (demand/README.md):\n  "
                         + "\n  ".join(errors[:30]) + more)

    day, blocks, offered, trips_n, without = schedule["day"], [], Counter(), 0, []
    with planner:
        for agent in schedule["agents"]:
            aid, info = agent["agent_id"], agents[agent["agent_id"]]
            home = planner.home_place(info["home"])

            def place(loc):
                return home if loc == home_id(aid) else planner.locations[loc]["place"]

            def activity(entry):
                loc = entry["location_id"]
                return entry.get("activity") or ("home" if loc == home_id(aid) else planner.locations[loc]["category"])

            trips = []
            for trip_id, prev, entry in trips_of(agent, day):
                a, b = prev["location_id"], entry["location_id"]
                options = []
                if home is not None:
                    pa, pb, at_house = place(a), place(b), (a == home_id(aid), b == home_id(aid))
                    options += [alternative("jeepney", leg) for leg in planner.jeepney_options(pa, pb)]
                    for mode in ("walk", "bike", "car"):
                        if mode in VCLASS and not info[f"has_{mode}"]:
                            continue
                        leg, _ = planner.leg(mode, pa, pb, at_house)
                        if leg:
                            options.append(alternative(mode, leg))
                trips_n += 1
                offered.update({o["mode"] for o in options})
                if not options:
                    without.append(trip_id)
                head = {"trip_id": trip_id, "from": a, "to": b,
                        "to_name": "home" if b == home_id(aid) else planner.locations[b]["name"] or "(unnamed)",
                        "trip_type": f"{activity(prev)}_to_{activity(entry)}", "depart": prev["end"]}
                trips.append((head, options))
            blocks.append(({"agent_id": aid, "has_car": info["has_car"], "has_bike": info["has_bike"]}, trips))

    def trip_text(head, options):        # one trip, one option per line: readable, compact for a prompt
        inner = ",\n                ".join(json.dumps(o, ensure_ascii=False) for o in options)
        return (json.dumps(head, ensure_ascii=False)[:-1] + ', "options": ['
                + (f"\n                {inner}\n            " if options else "") + "]}")

    agents_text = []
    for who, trips in blocks:
        body = ",\n            ".join(trip_text(h, o) for h, o in trips)
        agents_text.append(f'    {json.dumps(who, ensure_ascii=False)[:-1]}, "trips": [\n            {body}\n        ]}}')
    head = {"day": day, "made_by": f"make_choices.py from {schedule_path.name}",
            "about": "Every way each trip of the schedule can be made, with its numbers. Choose one per trip "
                     "in the modes file (demand/README.md).", "rules": RULES}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{\n" + ",\n".join(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in head.items())
                   + ',\n  "agents": [\n' + ",\n".join(agents_text) + "\n  ]\n}\n", encoding="utf-8")
    json.loads(out.read_text(encoding="utf-8"))   # check it is valid JSON

    n_options = sum(len(o) for _, trips in blocks for _, o in trips)
    print(f"{schedule_path.name}: {len(blocks)} agents, {trips_n} trips, {n_options} options "
          f"({n_options / max(trips_n, 1):.1f} per trip). Trips offering each mode: "
          + ", ".join(f"{m} {offered[m]}" for m in MODES))
    if without:
        print(f"  {len(without)} trips have no option (the LLM must change those days): {', '.join(without[:10])}"
              + (" ..." if len(without) > 10 else ""))
    print(f"Wrote {out.relative_to(S.HERE) if out.is_relative_to(S.HERE) else out}")


if __name__ == "__main__":
    main()
