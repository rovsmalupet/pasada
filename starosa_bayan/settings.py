"""Every stand-in value and path of the multi-line setup (run.py), in one place.

This folder is a PROTOTYPE: these values are for seeing how things behave, not thesis
assumptions. The thesis ABM is ../sumo_track, which keeps its values in config/params.yaml with
a source and status for each. Copied from ../starosa_prototype on 2026-11-03: same values except
the map's own, marked MAP.
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"     # rebuilt by build.py; safe to delete
RUNS = HERE / "runs"               # one folder per run
# the demand: who goes where, when and how (format in demand/README.md). Not generated: kept as is.
# STAROSA_BAYAN_DEMAND points it elsewhere, e.g. ../llm_test/demand for a test with a real LLM
DEMAND = Path(os.environ.get("STAROSA_BAYAN_DEMAND") or HERE / "demand").resolve()


def rel(path):
    """A path as printed: relative to this folder (with ../ if it is outside)."""
    return Path(os.path.relpath(path, HERE)).as_posix()
AGENTS_FILE = DEMAND / "agents.json"     # make_agents.py: the agents (base attributes, homes) and places
# the LLM's answers, once at the start (pipeline.py; the format of every file in demand/README.md):
PERSONAS_FILE = DEMAND / "personas.json"         # persona of each agent (manuscript Sec. 4.3.2)
HABITS_FILE = DEMAND / "habits.json"             # habitual travel patterns, with the first mode weights
LLM_INPUTS = DEMAND / "llm_inputs"               # what each LLM task is given (llm_tasks.py)


def day_file(kind, day):
    """demand/<kind>_day_<day>.json: schedule, choices, modes, outcomes or reflection."""
    return DEMAND / f"{kind}_day_{day}.json"


# day 1, the default of run.py, make_choices.py and convert_demand.py:
SCHEDULE_FILE = day_file("schedule", 1)          # the LLM: where and when (no modes)
CHOICES_FILE = day_file("choices", 1)            # make_choices.py: each trip's options with their numbers
MODES_FILE = day_file("modes", 1)                # the LLM: an option per trip; run.py reads the schedule and these

# MAP: the study area, 9 barangays of Santa Rosa chosen by the group on 2026-11-10 (13.58 km²,
# about 214,000 people); until then the 14 entirely north-east of SLEX (2026-11-03).
# make_map.py joins them into AREA_FILE, cuts OSM_SOURCE to the roads touching it and their
# approach roads (OSM_FILE) and builds NET_FILE from it (netconvert.netccfg: sidewalks,
# oncoming-lane overtaking). After a rebuild, rerun build.py.
AREA_BARANGAYS = [                 # adm4_pcode in BARANGAYS_FILE (get_barangays.py)
    "PH0403428023",                # Tagapo
    "PH0403428016",                # Pook (Pooc)
    "PH0403428008",                # Labas
    "PH0403428009",                # Macabling
    "PH0403428012",                # Market Area (Pob.)
    "PH0403428011",                # Malusak (Pob.)
    "PH0403428007",                # Ibaba
    "PH0403428015",                # Kanluran (Pob.)
    "PH0403428002",                # Balibago
]
BARANGAYS_FILE = HERE / "data" / "santa_rosa_barangays.geojson"
AREA_FILE = HERE / "data" / "area.geojson"
OSM_SOURCE = HERE.parent / "sumo_track" / "data" / "raw" / "santa_rosa_buffered.osm"   # Overpass, 2026-10-16
OSM_FILE = HERE / "bayan.osm"
NET_FILE = HERE / "bayan.net.xml"
# MAP: approach roads, so jeepneys and through traffic come in from outside instead of appearing at
# the area's edge (the user, 2026-11-12). The map keeps the main roads (APPROACH_HIGHWAYS) up to
# APPROACH_M outside the area, joined to it through main roads, and cuts them there: their ends are
# where jeepneys and gateway traffic come in and go out. 1,000 m at first; 500 m by the user's
# choice ("1km is too long"), the same day.
APPROACH_M = 500.0
APPROACH_HIGHWAYS = ("trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link")

SEED = 42
END_S = 86400                      # simulated seconds per run: a whole day, as in sumo_track (day_length_s);
                                   # run.py stops early once every commuter is home, or at --end
LATERAL_RESOLUTION = 0.4           # m: sublane strips (sandbox 11)
TIME_TO_TELEPORT = -1              # s a vehicle may stand still before SUMO moves it on; -1: never. SUMO 1.27
                                   # crashed (segmentation fault) when taking a stuck vehicle off the road
                                   # with these sublanes: 3 of 3 midday runs with the counted traffic
                                   # (2026-11-14), also with --time-to-teleport.remove; none with -1

# --- jeepney lines ------------------------------------------------------------------------------
# Main roads: nobody lives on them (convert_demand.street_exclusions).
LINE_ROAD_TYPES = ("highway.trunk", "highway.primary", "highway.secondary")
# MAP: the lines, chosen by the user (2026-11-03), who lives in the area: the jeepney routes that
# actually run there (confirmed 2026-11-12). Jeepneys on the main roads in both directions, but
# none on Arcillas Boulevard or the Binan-Santa Rosa Access Road; Rizal Boulevard's jeepneys go on
# either to Felix Reyes Street or back onto National Highway 1. F. Gomez, Zavalla and Arambulo
# Streets are under MIN_RIDE. Line JPRS (J. P. Rizal Street, "E") went with the 9 barangays
# (2026-11-10): the street lies wholly in Dita. Each line: the roads it follows in order, each with
# the compass direction of travel on it inside the area (make_routes.py prints each road's two).
# The first road is driven end to end; each next one is joined where it is nearest and driven to
# its end. make_routes.py also makes the reverse line: the same roads backwards, the last one end
# to end.
JEEPNEY_LINES = {
    "N1": [("National Highway 1", "S")],             # "SE" with the 14 barangays (it reached Dita)
    "RIZ-FRS": [("Rizal Boulevard", "S"), ("Felix Reyes Street", "SW")],
    "RIZ-N1": [("Rizal Boulevard", "S"), ("National Highway 1", "S")],
}
# jeepneys come in from outside the area and leave it again (the user, 2026-11-12): a line whose
# roads start or end closer than this outside the area is led in or out along main roads. Just
# short of APPROACH_M, so every line starts and ends where the main roads are cut
LINE_OUTSIDE_M = APPROACH_M - 25.0
LEAD_MAX_M = 3000.0                # m: longest lead-in or lead-out looked for
OFF_HIGHWAY_PENALTY = 10.0         # routing: a metre off the road costs as much as this many on it
JEEPNEY_VCLASS = "bus"
JEEPNEY_HEADWAY_S = 300            # one jeepney per line every 5 min (a stand-in: the routes are real, the gap is not)
JEEPNEY = dict(length=7.0, width=2.1, maxSpeed=16.67, accel=1.0, decel=3.0, personCapacity=22,
               boardingDuration=2.0, latAlignment="right", minGapLat=0.4)

# --- commuters ---------------------------------------------------------------------------------
# the LLM team's base commuter agents (census-based: age group, sex, barangay, employment, school,
# occupation, income decile, household car, bicycle and motorcycle access). make_agents.py copies
# the file into BASE_AGENTS_FILE with its provenance whenever the source changes, so this folder
# keeps the agents it used.
BASE_AGENTS_SOURCE = HERE.parent.parent / "PASADA-llm" / "base_commuter_agents" / "output" / "base_agents.json"
BASE_AGENTS_FILE = HERE / "data" / "base_agents.json"
BARANGAY_ALIASES = {"Pook": "Pooc"}  # BARANGAYS_FILE's name (without " (Pob.)") -> the census name the agents use
SAMPLE_AGENTS = 100                # make_agents.py: this many of the base agents living in the study area,
                                   # drawn at random (seeded); 0 = all of them (811 of the 1,000 of 2026-10-13)
NOT_TRAVELLING = ("Under 5",)      # age groups left out: small children do not travel on their own
CAR_DRIVER_MIN_AGE = 20            # a car only for age groups from this age (the 15-19 group mixes minors
                                   # and adults; a non-professional licence is from 17)
MAX_WALK = 400.0                  # m: longest walk between door and car or bike
LINE_WALK = 10000.0               # m: longest walk to the jeepney line and from it (a test since 2026-11-13,
                                  #    the user's: 400 m, MAX_WALK, before; no tricycles yet)
MIN_RIDE = 500.0                   # m: shortest jeepney ride worth taking
MAX_STREET_SPEED = 14.0            # m/s: homes and destinations only on streets up to ~50 km/h
KERB_MARGIN = 10.0                 # m: jeepneys stop at least this far from either end of an edge
# where commuters live and go (make_agents.py, convert_demand.py): homes and destinations only
# inside AREA_FILE. Outside it the network has only the ends of roads crossing the edge
POPULATION_GRID = HERE.parent / "population_extraction" / "output" / "Santa_Rosa_Population_Density_2020.csv"
# sumo_track's OSM placeholder (data/placeholder/locations.csv) cut to the 9 barangays, with a barangay column
ACTIVITY_LOCATIONS = HERE / "data" / "locations_9_barangays.csv"
HOME_SNAP_M = 100.0                # m: a grid cell's people go to the nearest home street within this
LOCATION_SNAP_M = 200.0            # m: an activity location goes to the nearest walkable edge within this
# feasibility, checked by convert_demand.py for every trip
WALK_TRIP_LIMIT = 1500.0           # m: longest walk all the way to the destination
MIN_TRIP_M = 20.0                  # m: places closer than this on foot are the same place: no trip between them
BIKE_TRIP_LIMIT = 5000.0           # m: longest bike ride
PARK_SNAP_M = 200.0                # m: a car or bike is parked on the nearest edge within this of the home or
                                   #    destination that it may use and people may walk on. A car is at the
                                   #    house (no walk there); otherwise they walk to it
CAR_ACCESS_S = 60.0                # s: at the car before driving off (sumo_track Plan D.6: car_access_time)
CAR_PARKING_S = 120.0              # s: parking before walking on (car_parking_time)
# costs shown with each option (make_choices.py); stand-ins to confirm with the data team (Sec. 4.2.5)
JEEPNEY_FARE = dict(min_php=14.0, min_km=4.0, per_km_php=2.0)    # traditional jeepney, LTFRB/DOTr fare from
                                                                 # 2026-09-28 (news reports, checked 2026-11-03): the
                                                                 # minimum for the first 4 km, then per km. No 20%
                                                                 # student/senior/PWD discount; modern jeepneys: 17 + 2.40
CAR_COST_PHP_PER_KM = 6.0          # fuel only: about PHP 60 per litre at 10 km per litre

# --- the LLM's tasks (llm_tasks.py, pipeline.py; manuscript Sec. 4.3) --------------------------
# what people do at a place -> the location categories it can be at (the agent's home: "home")
ACTIVITIES = {
    "work": ("workplace", "school", "market", "mall", "terminal"),   # teachers, vendors and shop staff too
    "school": ("school",),
    "shopping": ("market", "mall"),
    "leisure": ("mall",),
    "out_of_area": ("terminal",),  # leaves the study area by bus from a terminal and comes back the same way
}
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
FIRST_WEEKDAY = "Mon"              # day 1 of a simulation
SIMULATION_CONTEXT = "An ordinary day: no rain, no transport strike, no holiday, schools open."
LEVELS = ("Low", "Moderate", "High")                   # persona sensitivities and predispositions
SENSITIVITIES = ("safety", "cost", "comfort", "accessibility", "availability", "efficiency", "reliability")
LOCATIONS_PER_CATEGORY = 8         # habits: the nearest locations of each category an agent can reach
EXPERIENCE_DAYS = 3                # travel experience entries (last days) given to a prompt: a stand-in
                                   # for the context window the manuscript leaves to Chapter 5
WEIGHT_SUM_TOLERANCE = 0.01        # mode preference weights must add up to 1 within this
MAX_WEIGHT_CHANGE = 0.2            # reflection: largest change of one mode's weight in one day (stand-in)

# --- flag-down controller ----------------------------------------------------------------------
DWELL_S = 15.0                     # minimum stop length
LOOKAHEAD_M = 200.0                # a waiting commuter flags jeepneys within this distance
OCCUPANCY_SAMPLE_S = 60            # run.py's event log: each jeepney's passengers, every this many seconds

# --- scenarios (manuscript Sec. 4.5.3): run.py / pipeline.py --scenario NAME ---------------------
# A scenario changes only the SUMO run of the days it is given on. The options keep the usual
# estimates and no prompt names it: the commuters find out by travelling.
#   jeepney_headway_s: {line: headway} for lines (both ways) running less often than
#   JEEPNEY_HEADWAY_S. A multiple of it: the jeepneys leaving at its multiples still run, with
#   their riders; the others are taken off.
#   disrupted_mode: the mode it makes worse, whose weight measures.py follows (C(T), Sec. 4.5.4.3).
SCENARIOS = {
    # Rizal Boulevard's two lines every 30 min instead of 5 (a stand-in level: long enough for a
    # typical 6-10 min jeepney trip to pass the disruption rule below)
    "reduced_jeepney": {"jeepney_headway_s": {"RIZ-FRS": 1800, "RIZ-N1": 1800}, "disrupted_mode": "jeepney"},
}

# --- measures (measures.py: the manuscript's Sec. 4.5 from the simulation's files) ---------------
# Stand-ins until the group sets them. Periods of the day: hours [from, to).
PERIODS = [("night", 0, 6), ("morning peak", 6, 9), ("midday", 9, 16), ("evening peak", 16, 19), ("night", 19, 24)]
INCOME_GROUPS = {"low (deciles 1-3)": (1, 3), "middle (4-7)": (4, 7), "high (8-10)": (8, 10)}   # per capita decile
SHORT_TRIP_M = 1000.0              # m: Sec. 4.5.4.1's "minimum motorized-trip distance": shorter trips
                                   #    should mostly be walked or biked

# --- trip outcomes (run.py -> runs/<date_time>/outcomes_day_<n>.json, Plan Part D.4) -------------
# A trip is disrupted when it took at least DISRUPTION_RATIO times its estimate AND at least
# DISRUPTION_MIN_S longer, or had not arrived when the run ended. A stand-in for the plan's
# disruption_threshold (D.6: set from preliminary runs). The estimates are for empty roads and
# optimistic, so the ratio alone would mark many ordinary jeepney trips: on a whole day without
# background (runs/2026-11-03_233050, 9,942 trips, old speed limits) jeepney trips took 1.52x their
# estimate at the median, and this rule marked 321 trips, all by jeepney (3%).
DISRUPTION_RATIO = 2.0
DISRUPTION_MIN_S = 600.0

# --- background traffic (make_background.py): not commuters, no LLM ------------------------------
# Every number here is a stand-in until the group's video counts on the jeepney lines replace it.
# 1. Inside trips: random trips between edges inside the study area.
# MAP: sumo_track_prototype's 1,800/h per type on the whole city, scaled by car-lane length
# (395.4 km inside the area, printed by make_map.py, vs 1,996.5 km there; the 14 barangays:
# 628.2 km, 566/h; starosa_prototype: 145.6 km, 131/h), so x1 is equally dense in every folder.
# Until 2026-11-12: 373, from the whole 9-barangay network's 413.3 km.
BACKGROUND_PER_HOUR = {"car": 357, "motorcycle": 357}
BACKGROUND_SCALE = 10.0            # as dense as sumo_track_prototype's x10
# share of each hourly rate in each hour of the day (1.0 = the peak), for all background traffic
# and the jeepney riders. A stand-in shape with morning and evening peaks.
BACKGROUND_PROFILE = [0.10, 0.05, 0.05, 0.05, 0.10, 0.30, 0.70, 1.00, 1.00, 0.80, 0.60, 0.60,
                      0.60, 0.60, 0.60, 0.70, 0.90, 1.00, 1.00, 0.80, 0.60, 0.40, 0.30, 0.20]
BACKGROUND_MIN_TRIP_M = 300.0
# 2. Gateways: MAP, the main roads bringing traffic into and out of the area (the user,
# 2026-11-12: more of it on the main road at both ends of the area, and more leaving). Each: the
# road and its direction of travel INTO the area (as make_routes.py names them), then the vehicles
# per hour at the peak coming IN there and going OUT there, per type: what a camera at that road
# counts in each direction. Traffic comes in and goes out at the road's cut end (APPROACH_M out).
# GATEWAY_THROUGH_SHARE of those coming in drive on through to another gateway (picked in
# proportion to its OUT traffic); the rest end inside the area. Those going out that are not
# through traffic start inside the area.
GATEWAYS = {
    "N1 north": ("National Highway 1", "S", {"car": 600, "motorcycle": 600}, {"car": 900, "motorcycle": 900}),
    "N1 south": ("National Highway 1", "N", {"car": 600, "motorcycle": 600}, {"car": 900, "motorcycle": 900}),
    "Tagaytay Road": ("Santa Rosa-Tagaytay Road", "NE", {"car": 200, "motorcycle": 200}, {"car": 300, "motorcycle": 300}),
    "Access Road": ("Biñan - Santa Rosa Access Road", "E", {"car": 200, "motorcycle": 200}, {"car": 300, "motorcycle": 300}),
}
GATEWAY_THROUGH_SHARE = 0.5
# 3. Jeepney riders: jeepneys come in from outside with passengers aboard (the user, 2026-11-12:
# more at the peak). Each seat is taken at entry with probability JEEPNEY_ENTRY_LOAD x
# BACKGROUND_PROFILE[hour] (so about half full at the peak). A rider gets off at a random roadside
# spot of the line inside the area, or (RIDER_THROUGH_SHARE) rides on out of it.
JEEPNEY_ENTRY_LOAD = 0.5
RIDER_THROUGH_SHARE = 0.3

# --- traffic counts (counts.py): the group's videos, against the simulation at the same spot ------
TRAFFIC_COUNTS = HERE / "data" / "traffic_counts"   # one folder per video, from the counting pipeline
# Each site: where the camera stood (lon, lat), the road it filmed, which way "away from the camera"
# is, and its videos (folders of TRAFFIC_COUNTS). Balibago N1: Matthew's two 30-min videos on
# National Highway 1, just north of its junctions with Rizal Boulevard and Felix Reyes Street (pin
# and direction: the user, 2026-11-14). Counted automatically; not yet checked by hand.
COUNT_SITES = {
    "Balibago N1": dict(lonlat=(121.105583, 14.296889), road="National Highway 1", away="N",
                        videos=["traffic_count_Balibago_morning_Nov14", "traffic_count_Balibago_midday_Nov13"]),
}
# 4. Counted traffic (make_background.py): through trips on each count site's road, from its end
# behind the camera to the end ahead (the road's GATEWAYS, by direction), at the rates the site's
# videos counted per type, so the camera sees what the videos saw. In the hour of a video, its
# rates; in other hours, the nearest video's x BACKGROUND_PROFILE[hour] / BACKGROUND_PROFILE[its
# hour]. The camera counts a jeepney as a car, bus or truck, so the simulation's own jeepneys passing
# it are taken off the buses and trucks.
BACKGROUND_TYPES = ["car", "motorcycle", "bus", "truck"]   # one trip file each (make_background.py)
CAR = dict(latAlignment="nice", minGapLat=0.4)                    # rest: SUMO defaults
MOTORCYCLE = dict(width=0.75, latAlignment="arbitrary", minGapLat=0.15)
BICYCLE = {}                                                      # commuters' bikes: SUMO defaults
BUS = dict(latAlignment="right", minGapLat=0.4)                   # counted traffic only (below)
TRUCK = dict(latAlignment="right", minGapLat=0.4)
