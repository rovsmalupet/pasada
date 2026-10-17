"""Every stand-in value and path the prototype uses, in one place.

This folder is a PROTOTYPE: these values are for seeing how things behave, not thesis
assumptions. The thesis ABM is ../sumo_track, which keeps its values in config/params.yaml with
a source and status for each.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"     # rebuilt by build.py; safe to delete
RUNS = HERE / "runs"               # one folder per run

# The road network is the final ABM's: build it in ../sumo_track first
# (python -m sumo_track.build --all). If it is rebuilt, rerun build.py here: edge ids change.
NET_FILE = HERE.parent / "sumo_track" / "data" / "processed" / "santarosa.net.xml"

SEED = 42
END_S = 3600                       # simulated seconds per run
LATERAL_RESOLUTION = 0.4           # m: sublane strips (sandbox 11)

# --- jeepney lines: one per highway and direction of travel -----------------------------------
HIGHWAYS = {                       # line prefix -> road name in OSM (matched ignoring punctuation)
    "N1": "National Highway 1",
    "TGY": "Santa Rosa-Tagaytay Road",
}
OFF_HIGHWAY_PENALTY = 10.0         # routing: a metre off the highway costs as much as this many on it
JEEPNEY_VCLASS = "bus"
JEEPNEY_HEADWAY_S = 300            # one jeepney per line every 5 min
JEEPNEY = dict(length=7.0, width=2.1, maxSpeed=16.67, accel=1.0, decel=3.0, personCapacity=22,
               boardingDuration=2.0, latAlignment="right", minGapLat=0.4)

# --- commuters ---------------------------------------------------------------------------------
N_COMMUTERS = 100
DEPART_WINDOW = (60, 1800)         # s: commuters leave home at random times in this window
MAX_WALK = 400.0                   # m: longest walk home -> roadside, or roadside -> destination
MIN_RIDE = 500.0                   # m: shortest jeepney ride worth taking
MAX_STREET_SPEED = 14.0            # m/s: homes and destinations only on streets up to ~50 km/h
KERB_MARGIN = 10.0                 # m: jeepneys stop at least this far from either end of an edge

# --- flag-down controller ----------------------------------------------------------------------
DWELL_S = 15.0                     # minimum stop length
LOOKAHEAD_M = 200.0                # a waiting commuter flags jeepneys within this distance

# --- background traffic: random trips across the whole network ---------------------------------
BACKGROUND_PER_HOUR = {"car": 1800, "motorcycle": 1800}
BACKGROUND_SCALE = 10.0            # x10 (2026-10-17): 1x looked too empty in sumo-gui
BACKGROUND_MIN_TRIP_M = 300.0
CAR = dict(latAlignment="nice", minGapLat=0.4)                    # rest: SUMO defaults
MOTORCYCLE = dict(width=0.75, latAlignment="arbitrary", minGapLat=0.15)
