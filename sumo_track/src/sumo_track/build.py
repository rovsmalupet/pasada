"""Rebuild everything the simulation needs, in order (Plan D.1 rule 8).

    python -m sumo_track.build --all                 # every step
    python -m sumo_track.build --from route_match    # that step and every one after it

The OSM download reuses the saved copy in data/raw/ (run sumo_track.network.osm_download
--refresh yourself to fetch newer OSM data). Each step is also a module you can run alone.
"""

import argparse
import subprocess
import sys
import time

from sumo_track.config import REPO_ROOT

STEPS = [
    ("clip_polygon", "sumo_track.network.clip_polygon"),
    ("osm_download", "sumo_track.network.osm_download"),
    ("locations", "sumo_track.locations.extract_pois"),
    ("network", "sumo_track.network.build"),
    ("route_match", "sumo_track.jeepney.route_match"),
    ("vtypes", "sumo_track.jeepney.vtypes"),
    ("flows", "sumo_track.jeepney.flows"),
]


def main() -> None:
    names = [name for name, _ in STEPS]
    parser = argparse.ArgumentParser(description="Rebuild the SUMO track's derived files.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="run every step")
    group.add_argument("--from", dest="start", choices=names, help="run this step and all later ones")
    args = parser.parse_args()

    for name, module in STEPS[names.index(args.start) if args.start else 0:]:
        print(f"\n=== {name}: python -m {module}")
        started = time.time()
        if subprocess.run([sys.executable, "-m", module], cwd=REPO_ROOT).returncode != 0:
            raise SystemExit(f"Step '{name}' failed; fix it and rerun with --from {name}")
        print(f"=== {name} done in {time.time() - started:.0f} s")
    print("\nAll steps done. (No runner yet: the Phase 8 pipeline comes later. To see the network with "
          "traffic and commuters, use ../sumo_track_prototype.)")


if __name__ == "__main__":
    main()
