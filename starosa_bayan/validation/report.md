# Simulation validation: PASS

Scripted test agents (manuscript Sec. 4.4.3.3), run `runs/2026-11-12_033608`, 2026-11-12 03:36, 79 s. Made by `python validation/validate.py`.

Test day: 32 agents, 38 trips, no background traffic (`validation/cases/`, from `make_cases.py`).

| # | Check | Result |
|---|---|---|
| 1 | Environment connectivity | PASS |
| 2 | Jeepney operation | PASS |
| 3 | Commuter movement | PASS |
| 4 | Disruption detection | PASS |
| 5 | Record completeness | PASS |

## 1. Environment connectivity: PASS

- PASS: 756 activity locations on a walkable edge within LOCATION_SNAP_M (200 m); 0 dropped
- PASS: every activity location reachable on foot from LOC_0421 (the most central) and back
- PASS: 32 test agents' homes on a home street within HOME_SNAP_M and reachable on foot both ways
- PASS: 100 demand/ agents' homes on a home street within HOME_SNAP_M and reachable on foot both ways
- PASS: line N1_N: 94 edges, 6.43 km, continuous for bus; 47 edges people can wait on
- PASS: line N1_S: 94 edges, 6.47 km, continuous for bus; 47 edges people can wait on
- PASS: line RIZ-FRS_N: 124 edges, 7.62 km, continuous for bus; 70 edges people can wait on
- PASS: line RIZ-FRS_S: 119 edges, 7.53 km, continuous for bus; 66 edges people can wait on
- PASS: line RIZ-N1_N: 93 edges, 6.60 km, continuous for bus; 55 edges people can wait on
- PASS: line RIZ-N1_S: 94 edges, 6.66 km, continuous for bus; 55 edges people can wait on
- PASS: all 38 scripted trips offered their mode (and line) by make_choices.py
- PASS: 31 jeepney trips board and alight on their line's edges, 10 m clear of the ends

## 2. Jeepney operation: PASS

- PASS: 224 jeepneys finished their whole route (N1_N 38, N1_S 38, RIZ-FRS_N 37, RIZ-FRS_S 37, RIZ-N1_N 37, RIZ-N1_S 37): from the line's first edge to its last, never rerouted, driven length within 1 m of the line's edges and junctions (N1_N 7363 m, N1_S 7367 m, RIZ-FRS_N 9016 m, RIZ-FRS_S 8871 m, RIZ-N1_N 7642 m, RIZ-N1_S 7697 m)
- PASS: 16 jeepney stops (SUMO's stops.xml), every one with someone getting on or off
- PASS: every stop lasts at least DWELL_S (15 s) and boardingDuration (2 s) per person boarding; the busiest: 22 on, 0 off, 44 s
- PASS: no jeepney ever carried more than personCapacity (22): at most 22 aboard
- PASS: capacity case: the 22 agents at one spot of RIZ-FRS_N all boarded one jeepney (RIZ-FRS_N.0), 22 aboard: full
- PASS: capacity case: the full jeepney passed the 3 agents waiting further on without stopping, logged as full_pass_by (and counted in their outcomes); a later one took them: V_CAP_P1: passed by RIZ-FRS_N.0 at 312 s, boarded RIZ-FRS_N.1 at 595 s; V_CAP_P2: passed by RIZ-FRS_N.0 at 312 s, boarded RIZ-FRS_N.1 at 596 s; V_CAP_P3: passed by RIZ-FRS_N.0 at 312 s, boarded RIZ-FRS_N.1 at 598 s
- PASS: event log: 16 stop events for SUMO's 16 stops, each with its times (within 1 s) and the people who got on and off
- PASS: event log: all 62 boardings and alightings happened during a stop of that jeepney, at the person's spot

## 3. Commuter movement: PASS

- PASS: all 38 trips (bike 2, car 3, jeepney 31, walk 2) went through their mode's states in order and arrived: walk: depart > arrived; jeepney: depart > waiting > boarding > alighting > arrived; bike: depart > vehicle_depart > vehicle_arrive > arrived; car: depart > vehicle_depart > vehicle_arrive > arrived
- PASS: every jeepney rider flagged the jeepney they took

## 4. Disruption detection: PASS

- PASS: the delayed trip's route still runs over the 1 slowed edges (506 m at 0.52 m/s)
- PASS: V_DELAY_D01_T01 (delayed, slowed on purpose) is disrupted: 1630 s against an estimate of 501 s; took 3.3x the estimate (19 min longer)
- PASS: the 12 trips expected normal (line, walk, bike, car cases) are not disrupted: delay ratios 0.87-1.60

## 5. Record completeness: PASS

- PASS: 38 trip records (trips.json), one per scripted trip, with every field (trip_id, agent_id, day, origin_location_id, destination_location_id, departure, departure_time_s, trip_type, mode, route_id, distance_m, estimated_total_time_s, cost_php, sumo_person_id)
- PASS: every scripted trip has a completed outcome with every field the reflection and the evaluation need (jeepney trips also their four parts)
- PASS: outcome times add up: arrival - departure = total, and a jeepney trip's access + waiting + in-vehicle + egress = total within 2 s
