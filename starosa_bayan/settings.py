"""Every stand-in value and path of the multi-line setup (run.py), in one place.

This folder is a PROTOTYPE: these values are for seeing how things behave, not thesis
assumptions. The thesis ABM is ../sumo_track, which keeps its values in config/params.yaml with
a source and status for each. Copied from ../starosa_prototype on 2026-11-03: same values except
the map's own, marked MAP.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"     # rebuilt by build.py; safe to delete
RUNS = HERE / "runs"               # one folder per run
# the demand: who goes where, when and how (format in demand/README.md). Not generated: kept as is
DEMAND = HERE / "demand"
AGENTS_FILE = DEMAND / "agents.json"     # what the LLM is given: agents (homes, car, bike) and places
# one day, in three steps (thesis plan B.3, daily loop steps 1, 3 and 4):
SCHEDULE_FILE = DEMAND / "schedule_day_1.json"   # 1. the LLM: where and when (no modes)
CHOICES_FILE = DEMAND / "choices_day_1.json"     # 2. make_choices.py: each trip's options with their numbers
MODES_FILE = DEMAND / "modes_day_1.json"         # 3. the LLM: an option per trip; run.py reads 1 and 3

# MAP: the study area, the 14 barangays of Santa Rosa entirely north-east of SLEX (all of that side
# but Malitlit, which SLEX cuts through), chosen by the user on 2026-11-03. make_map.py joins them
# into AREA_FILE, cuts OSM_SOURCE to the roads touching it (OSM_FILE) and builds NET_FILE from it
# (netconvert.netccfg: sidewalks, oncoming-lane overtaking). After a rebuild, rerun build.py.
AREA_BARANGAYS = [                 # adm4_pcode in BARANGAYS_FILE (get_barangays.py)
    "PH0403428023",                # Tagapo
    "PH0403428016",                # Pook (Pooc)
    "PH0403428004",                # Dila
    "PH0403428008",                # Labas
    "PH0403428022",                # Sinalhan
    "PH0403428009",                # Macabling
    "PH0403428012",                # Market Area (Pob.)
    "PH0403428003",                # Caingin
    "PH0403428011",                # Malusak (Pob.)
    "PH0403428007",                # Ibaba
    "PH0403428015",                # Kanluran (Pob.)
    "PH0403428001",                # Aplaya
    "PH0403428002",                # Balibago
    "PH0403428005",                # Dita
]
BARANGAYS_FILE = HERE / "data" / "santa_rosa_barangays.geojson"
AREA_FILE = HERE / "data" / "area.geojson"
OSM_SOURCE = HERE.parent / "sumo_track" / "data" / "raw" / "santa_rosa_buffered.osm"   # Overpass, 2026-10-16
OSM_FILE = HERE / "bayan.osm"
NET_FILE = HERE / "bayan.net.xml"

SEED = 42
END_S = 86400                      # simulated seconds per run: a whole day, as in sumo_track (day_length_s);
                                   # run.py stops early once every commuter is home, or at --end
LATERAL_RESOLUTION = 0.4           # m: sublane strips (sandbox 11)

# --- jeepney lines ------------------------------------------------------------------------------
# Main roads: nobody lives on them (convert_demand.street_exclusions).
LINE_ROAD_TYPES = ("highway.trunk", "highway.primary", "highway.secondary")
# MAP: the lines, chosen by the user (2026-11-03): jeepneys on the main roads in both directions, but
# none on Arcillas Boulevard or the Binan-Santa Rosa Access Road; Rizal Boulevard's jeepneys go on
# either to Felix Reyes Street or back onto National Highway 1. F. Gomez, Zavalla and Arambulo
# Streets are under MIN_RIDE. Each line: the roads it follows in order, each with the compass
# direction of travel on it (make_routes.py prints each road's two). The first road is driven end
# to end; each next one is joined where it is nearest and driven to its end. make_routes.py also
# makes the reverse line: the same roads backwards, the last one end to end.
JEEPNEY_LINES = {
    "N1": [("National Highway 1", "SE")],
    "JPRS": [("J. P. Rizal Street", "E")],
    "RIZ-FRS": [("Rizal Boulevard", "S"), ("Felix Reyes Street", "SW")],
    "RIZ-N1": [("Rizal Boulevard", "S"), ("National Highway 1", "SE")],
}
OFF_HIGHWAY_PENALTY = 10.0         # routing: a metre off the road costs as much as this many on it
JEEPNEY_VCLASS = "bus"
JEEPNEY_HEADWAY_S = 300            # one jeepney per line every 5 min
JEEPNEY = dict(length=7.0, width=2.1, maxSpeed=16.67, accel=1.0, decel=3.0, personCapacity=22,
               boardingDuration=2.0, latAlignment="right", minGapLat=0.4)

# --- commuters ---------------------------------------------------------------------------------
N_COMMUTERS = 5000                 # agents made by make_agents.py: 10,000 trips with llm_stub.py's
                                   # home-activity-home days, as in starosa_prototype
MAX_WALK = 400.0                   # m: longest walk home -> roadside, roadside -> destination, or door <-> vehicle
MIN_RIDE = 500.0                   # m: shortest jeepney ride worth taking
MAX_STREET_SPEED = 14.0            # m/s: homes and destinations only on streets up to ~50 km/h
KERB_MARGIN = 10.0                 # m: jeepneys stop at least this far from either end of an edge
# where commuters live and go (make_agents.py, convert_demand.py): homes and destinations only
# inside AREA_FILE. Outside it the network has only the ends of roads crossing the edge
POPULATION_GRID = HERE.parent / "population_extraction" / "output" / "Santa_Rosa_Population_Density_2020.csv"
ACTIVITY_LOCATIONS = HERE.parent / "sumo_track" / "data" / "placeholder" / "locations.csv"
HOME_SNAP_M = 100.0                # m: a grid cell's people go to the nearest home street within this
LOCATION_SNAP_M = 200.0            # m: an activity location goes to the nearest walkable edge within this
CAR_ACCESS = 0.3                   # make_agents.py: share of agents with a car they can use
BIKE_ACCESS = 0.3                  # make_agents.py: share with a bike
# llm_stub.py's choices (stand-ins for the LLM): NOT a modal split, only enough of each mode to
# watch in a test; each agent gets a mode they can use, the one with the most share left
MODE_SHARE = {"jeepney": 0.4, "walk": 0.2, "bike": 0.2, "car": 0.2}
# feasibility, checked by convert_demand.py for every trip
WALK_TRIP_LIMIT = 1500.0           # m: longest walk all the way to the destination
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
# llm_stub.py's day plans: home -> activity -> home (stand-ins until the LLM writes schedules)
# category: (earliest start, latest start, shortest stay, longest stay), in hours of the day
ACTIVITY_TIMES = {
    "school": (7.0, 8.0, 6.0, 9.0),
    "workplace": (7.0, 9.0, 8.0, 10.0),
    "market": (6.0, 11.0, 0.5, 1.5),
    "mall": (10.0, 18.0, 1.0, 3.0),
    "terminal": (5.0, 9.0, 8.0, 11.0),   # leaves the area from the terminal and comes back the same way
}

# --- flag-down controller ----------------------------------------------------------------------
DWELL_S = 15.0                     # minimum stop length
LOOKAHEAD_M = 200.0                # a waiting commuter flags jeepneys within this distance

# --- background traffic: random trips across the whole network ---------------------------------
# MAP: sumo_track_prototype's 1,800/h per type on the whole city, scaled by car-lane length
# (628.2 km here, printed by make_map.py, vs 1,996.5 km there; starosa_prototype: 145.6 km, 131/h),
# so x1 is equally dense in every folder.
BACKGROUND_PER_HOUR = {"car": 566, "motorcycle": 566}
BACKGROUND_SCALE = 10.0            # as dense as sumo_track_prototype's x10
# share of that rate in each hour of the day (1.0 = the peak). A stand-in shape with morning and
# evening peaks until traffic counts (Sec. 4.2.6) give a real one.
BACKGROUND_PROFILE = [0.10, 0.05, 0.05, 0.05, 0.10, 0.30, 0.70, 1.00, 1.00, 0.80, 0.60, 0.60,
                      0.60, 0.60, 0.60, 0.70, 0.90, 1.00, 1.00, 0.80, 0.60, 0.40, 0.30, 0.20]
BACKGROUND_MIN_TRIP_M = 300.0
CAR = dict(latAlignment="nice", minGapLat=0.4)                    # rest: SUMO defaults
MOTORCYCLE = dict(width=0.75, latAlignment="arbitrary", minGapLat=0.15)
BICYCLE = {}                                                      # commuters' bikes: SUMO defaults
