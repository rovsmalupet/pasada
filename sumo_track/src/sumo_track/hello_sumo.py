"""Smoke test: build a tiny grid network, drive a few cars through it with TraCI, and close.

Checks that SUMO, TraCI, sumolib, and sumo_track.config work together.

    python -m sumo_track.hello_sumo          # headless (sumo)
    python -m sumo_track.hello_sumo --gui    # watch it in sumo-gui
"""

import argparse
import subprocess

import sumolib
import traci

from sumo_track.config import REPO_ROOT, Config

# Test fixture only, not a model assumption, so these stay out of params.yaml.
GRID_SIZE = 3  # junctions per side (A0 .. C2)
GRID_EDGE_LENGTH = 100  # m
ROUTE_FROM, ROUTE_TO = "A0B0", "B2C2"  # corner to opposite corner
NUM_VEHICLES = 3
DEPART_INTERVAL = 10  # s

OUT_DIR = REPO_ROOT / "data" / "processed" / "hello"
NET_FILE = OUT_DIR / "grid.net.xml"


def build_grid() -> None:
    """Regenerate the test grid with netgenerate (derived file, never hand-edited)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sumolib.checkBinary("netgenerate"),
            "--grid",
            "--grid.number", str(GRID_SIZE),
            "--grid.length", str(GRID_EDGE_LENGTH),
            "--output-file", str(NET_FILE),
        ],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test for SUMO + TraCI.")
    parser.add_argument("--gui", action="store_true", help="run in sumo-gui instead of headless sumo")
    parser.add_argument("--steps", type=int, default=100, help="simulation steps to run (default: 100)")
    parser.add_argument("--delay", type=int, default=100, help="sumo-gui delay per step in ms (default: 100)")
    args = parser.parse_args()

    config = Config.load()
    build_grid()

    cmd = [
        sumolib.checkBinary("sumo-gui" if args.gui else "sumo"),
        "--net-file", str(NET_FILE),
        "--step-length", str(config.param("step_length")),
        "--seed", str(config.param("random_seed")),
        "--no-step-log", "true",
    ]
    if args.gui:
        # --start: don't wait for the play button; --quit-on-end: close the window when TraCI closes
        cmd += ["--start", "--quit-on-end", "--delay", str(args.delay)]

    traci.start(cmd)
    try:
        api_version, sumo_version = traci.getVersion()
        print(f"Connected to {sumo_version} (TraCI API {api_version})")

        route = traci.simulation.findRoute(ROUTE_FROM, ROUTE_TO).edges
        traci.route.add("hello_route", route)
        print(f"Route {ROUTE_FROM} -> {ROUTE_TO}: {' '.join(route)}")
        for i in range(NUM_VEHICLES):
            traci.vehicle.add(f"hello_{i}", "hello_route", depart=str(i * DEPART_INTERVAL))

        departed = arrived = 0
        for _ in range(args.steps):
            traci.simulationStep()
            departed += traci.simulation.getDepartedNumber()
            arrived += traci.simulation.getArrivedNumber()
            t = traci.simulation.getTime()
            if int(t) % 10 == 0:
                print(f"  t={t:5.0f} s   on network: {traci.vehicle.getIDCount()}   arrived so far: {arrived}")

        print(f"Done: {args.steps} steps, t={t:.0f} s, {departed} departed, {arrived} arrived.")
    finally:
        traci.close()


if __name__ == "__main__":
    main()
