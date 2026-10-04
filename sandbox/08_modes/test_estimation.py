"""The Phase 4 acceptance criteria, as executable tests.

Straight from the plan:
  - a short trip, where walking is feasible
  - a long trip with no car access, where car is absent
  - a trip with no nearby jeepney route, where jeepney is absent
  - origin and destination in the wrong order along a route -> route rejected
  - a trip served by two routes -> two jeepney alternatives
  - every total time equals the sum of its components
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ALTS = json.load(open(os.path.join(HERE, "alternatives.json")))

FAILURES = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAILURES.append(name)
    print("[%s] %s%s" % (status, name, ("  - " + detail) if detail else ""))


def modes(trip_id):
    return [a["mode"] for a in ALTS[trip_id]["alternatives"]]


def excluded_reasons(trip_id):
    return {name: why for name, why in ALTS[trip_id]["excluded"]}


print("Phase 4 acceptance criteria")
print("=" * 64)

# 1 - short trip: walking is feasible
check("short trip offers walking",
      "walk" in modes("T1_short"),
      "modes: %s" % ", ".join(modes("T1_short")))

# 2 - long trip, no car access: car absent
check("no car access -> car excluded",
      "car" not in modes("T2_nocar"),
      excluded_reasons("T2_nocar").get("car", ""))

# 2b - and that trip is too long to walk
check("beyond walking limit -> walk excluded",
      "walk" not in modes("T2_nocar"),
      excluded_reasons("T2_nocar").get("walk", ""))

# 3 - no nearby jeepney route: jeepney absent
check("no serving route -> jeepney excluded",
      "jeepney" not in modes("T4_nojeep"),
      "modes: %s" % ", ".join(modes("T4_nojeep")))

# 4 - wrong direction along the route: that route is rejected
wrong = excluded_reasons("T5_wrongdir")
check("wrong direction -> route rejected",
      all("not after boarding" in wrong.get("jeepney/" + r, "")
          for r in ["JEEP_R01_EB", "JEEP_R02_NB", "JEEP_R03_EB"]),
      "all three routes rejected on ordering")

# 5 - trip served by two routes: two jeepney alternatives
jeep_routes = [a["route_id"] for a in ALTS["T3_two_routes"]["alternatives"]
               if a["mode"] == "jeepney"]
check("two serving routes -> two alternatives",
      len(jeep_routes) == 2,
      "routes: %s" % ", ".join(jeep_routes))

# 6 - every total equals the sum of its components
bad = []
for tid, r in ALTS.items():
    for a in r["alternatives"]:
        parts = (a["access_time_s"] + a["waiting_time_s"] + a["in_vehicle_time_s"]
                 + a["dwell_time_s"] + a["egress_time_s"])
        if abs(parts - a["estimated_total_time_s"]) > 0.2:
            bad.append("%s/%s: %.1f != %.1f"
                       % (tid, a["mode"], parts, a["estimated_total_time_s"]))
check("total time == sum of components",
      not bad,
      "; ".join(bad) if bad else "checked every alternative")

# 7 - an agent is never offered a mode it has no access to
leaks = []
for tid, r in ALTS.items():
    trip_has_bike = "bike" not in excluded_reasons(tid).get("bike", "agent has no bike")
    for a in r["alternatives"]:
        if a["mode"] == "car" and "no car access" in excluded_reasons(tid).get("car", ""):
            leaks.append(tid)
check("no mode offered without access", not leaks, "; ".join(leaks) or "none leaked")

print("=" * 64)
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all checks passed")
