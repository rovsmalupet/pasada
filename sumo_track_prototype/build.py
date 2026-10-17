"""Regenerate everything in generated/, in order. Needs the network built in ../sumo_track.

    python build.py
"""

import subprocess
import sys
import time

import settings as S

STEPS = ["make_routes.py", "make_vtypes.py", "make_commuters.py", "make_background.py"]


def main():
    if not S.NET_FILE.is_file():
        raise SystemExit(f"No network at {S.NET_FILE}: build it in ../sumo_track first "
                         "(python -m sumo_track.build --all)")
    for step in STEPS:
        print(f"\n=== {step}")
        started = time.time()
        if subprocess.run([sys.executable, step], cwd=S.HERE).returncode != 0:
            raise SystemExit(f"{step} failed")
        print(f"=== {step} done in {time.time() - started:.0f} s")
    print("\nAll done. Run it: python run.py")


if __name__ == "__main__":
    main()
