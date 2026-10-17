"""Look at the network with random dummy cars driving everywhere (no jeepneys, no commuters).

A viewing aid: it shows whether roads connect, since randomTrips.py --validate drops trips that
cannot reach their destination. Moved here from sumo_track (network/preview.py) on 2026-10-17.
Files go to runs/preview_<date_time>/.

    python preview_network.py            # sumo-gui, press play
    python preview_network.py --nogui    # headless: how many arrived
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime

import sumolib

import settings as S

PREVIEW_END = 900       # s of departures
TRIP_PERIOD = 0.5       # s between departures: 2 new cars per second


def main():
    parser = argparse.ArgumentParser(description="Random dummy cars on the network.")
    parser.add_argument("--nogui", action="store_true", help="run headless instead of sumo-gui")
    args = parser.parse_args()

    out = S.RUNS / f"preview_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
    out.mkdir(parents=True)
    trips, routes = out / "random.trips.xml", out / "random.rou.xml"
    print(f"Generating random car trips (one every {TRIP_PERIOD} s for {PREVIEW_END} s); "
          "validating the routes takes several minutes")
    random_trips = os.path.join(os.environ["SUMO_HOME"], "tools", "randomTrips.py")
    subprocess.run([sys.executable, random_trips, "-n", str(S.NET_FILE), "-o", str(trips), "-r", str(routes),
                    "-b", "0", "-e", str(PREVIEW_END), "-p", str(TRIP_PERIOD), "--vehicle-class", "passenger",
                    "--validate", "--remove-loops", "--min-distance", "300", "--seed", str(S.SEED)],
                   check=True, capture_output=True)
    n = sum(1 for _ in sumolib.xml.parse_fast(str(routes), "vehicle", ["id"]))
    print(f"  {n} routable trips")
    cmd = [sumolib.checkBinary("sumo" if args.nogui else "sumo-gui"), "-n", str(S.NET_FILE), "-r", str(routes),
           "--seed", str(S.SEED), "--tripinfo-output", str(out / "tripinfo.xml"), "--no-step-log", "true",
           "--ignore-route-errors", "true"]
    if args.nogui:
        result = subprocess.run(cmd, capture_output=True, text=True)
        arrived = sum(1 for _ in sumolib.xml.parse_fast(str(out / "tripinfo.xml"), "tripinfo", ["id"]))
        print(f"  {arrived}/{n} cars arrived, {result.stderr.count('Teleporting vehicle')} teleports")
    else:
        print("  sumo-gui is opening: press the green Run (play) button")
        subprocess.run(cmd + ["--start", "false"])


if __name__ == "__main__":
    main()
