"""Background traffic: random car and motorcycle trips across the whole network.

SUMO's randomTrips.py picks random origins and destinations from 0 to END_S, at
BACKGROUND_PER_HOUR x BACKGROUND_SCALE x BACKGROUND_PROFILE[hour] in each hour (randomTrips
spreads a list of rates over equal slices of the run). SUMO routes each trip when it departs
and drops trips that have no route. Ported from ../sumo_track_prototype (which has no profile).

Writes generated/background_car.trips.xml and generated/background_motorcycle.trips.xml.

    python make_background.py
"""

import math
import os
import subprocess
import sys

import settings as S

EDGE_CLASS = {"car": "passenger", "motorcycle": "motorcycle"}   # vType id -> SUMO vClass


def trips_file(vtype_id):
    return S.GENERATED / f"background_{vtype_id}.trips.xml"


def main():
    S.GENERATED.mkdir(exist_ok=True)
    random_trips = os.path.join(os.environ["SUMO_HOME"], "tools", "randomTrips.py")
    hours = math.ceil(S.END_S / 3600)
    for i, (vtype_id, per_hour) in enumerate(S.BACKGROUND_PER_HOUR.items()):
        rates = [per_hour * S.BACKGROUND_SCALE * S.BACKGROUND_PROFILE[h % 24] for h in range(hours)]
        print(f"Generating {vtype_id} trips: {min(rates):.0f}-{max(rates):.0f}/h over {hours} h, "
              f"{sum(rates):,.0f} in all (seed {S.SEED + i})")
        subprocess.run(
            [sys.executable, random_trips, "-n", str(S.NET_FILE), "-o", str(trips_file(vtype_id)),
             "-b", "0", "-e", str(hours * 3600), "--insertion-rate", *(f"{r:.1f}" for r in rates),
             "--edge-permission", EDGE_CLASS[vtype_id], "--prefix", f"{vtype_id}_",
             "--trip-attributes", f'type="{vtype_id}" departLane="best"',
             "--min-distance", str(S.BACKGROUND_MIN_TRIP_M),
             "--remove-loops", "--no-validate", "--seed", str(S.SEED + i)],
            check=True, capture_output=True)
        print(f"  Wrote generated/{trips_file(vtype_id).name}")


if __name__ == "__main__":
    main()
