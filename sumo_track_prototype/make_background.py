"""Background traffic: random car and motorcycle trips across the whole network.

SUMO's randomTrips.py picks random origins and destinations at a constant rate
(BACKGROUND_PER_HOUR x BACKGROUND_SCALE) from 0 to END_S. SUMO routes each trip when it departs
and drops trips that have no route. No peak profile. Moved here from sumo_track on 2026-10-17.

Writes generated/background_car.trips.xml and generated/background_motorcycle.trips.xml.

    python make_background.py
"""

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
    for i, (vtype_id, per_hour) in enumerate(S.BACKGROUND_PER_HOUR.items()):
        rate = per_hour * S.BACKGROUND_SCALE
        print(f"Generating {vtype_id} trips: {rate:.0f}/h for 0-{S.END_S} s (seed {S.SEED + i})")
        subprocess.run(
            [sys.executable, random_trips, "-n", str(S.NET_FILE), "-o", str(trips_file(vtype_id)),
             "-b", "0", "-e", str(S.END_S), "--insertion-rate", str(rate),
             "--edge-permission", EDGE_CLASS[vtype_id], "--prefix", f"{vtype_id}_",
             "--trip-attributes", f'type="{vtype_id}" departLane="best"',
             "--min-distance", str(S.BACKGROUND_MIN_TRIP_M),
             "--remove-loops", "--no-validate", "--seed", str(S.SEED + i)],
            check=True, capture_output=True)
        print(f"  Wrote generated/{trips_file(vtype_id).name}")


if __name__ == "__main__":
    main()
