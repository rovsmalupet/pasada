"""
make_route.py — build a jeepney route between two edges you picked in netedit.

Usage:
    python make_route.py santarosa.net.xml ROUTE_START_EDGE ROUTE_END_EDGE

Prints:
  1. the ordered edge list (paste into demand.rou.xml)
  2. a suggested boarding position  (midway along the route)
  3. a suggested alighting position (near the end of the route)

This is a baby version of the map-matching + nearest-point machinery of
Section 4.4.1.5: for the prototype we let SUMO's own shortest path define the
route instead of matching GPS points.
"""
import sys
import sumolib

def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    net_file, start_id, end_id = sys.argv[1], sys.argv[2], sys.argv[3]
    net = sumolib.net.readNet(net_file)

    try:
        start = net.getEdge(start_id)
        end = net.getEdge(end_id)
    except KeyError as e:
        print(f"Edge not found in network: {e}")
        print("Edge IDs are case-sensitive; copy them exactly from netedit.")
        sys.exit(1)

    # shortest path for a normal road vehicle (jeepneys drive as vClass passenger)
    path, _ = net.getShortestPath(start, end, vClass="passenger")
    if path is None:
        print("No drivable path from start to end in that direction.")
        print("Tip: one-way streets matter. Try swapping the edges, or pick the")
        print("opposite-direction twins (same number, with/without leading '-').")
        sys.exit(1)

    edge_ids = [e.getID() for e in path]
    total_len = sum(e.getLength() for e in path)
    # `cost` from getShortestPath is route length (meters), not time -
    # compute real free-flow travel time from each edge's length/speed.
    total_time = sum(e.getLength() / e.getSpeed() for e in path)

    print("=" * 60)
    print(f"Route found: {len(edge_ids)} edges, {total_len:.0f} m, "
          f"~{total_time:.0f} s free-flow")
    print("=" * 60)
    print("\n1) PASTE THIS EDGE LIST into demand.rou.xml (route edges=...):\n")
    print(" ".join(edge_ids))

    # boarding: the edge closest to the route's halfway point
    walked = 0.0
    board_edge, board_pos = edge_ids[0], 5.0
    for e in path:
        if walked + e.getLength() >= total_len / 2:
            board_edge = e.getID()
            board_pos = round(min(max(total_len / 2 - walked, 5.0),
                                  e.getLength() - 1), 1)
            break
        walked += e.getLength()

    # alighting: middle of the second-to-last edge (so the jeepney can still
    # pull over before its route ends)
    alight_e = path[-2] if len(path) > 1 else path[-1]
    alight_edge = alight_e.getID()
    alight_pos = round(alight_e.getLength() / 2, 1)

    print("\n2) SUGGESTED BOARDING POSITION (fill into demand + controller):")
    print(f"   BOARD_EDGE  = {board_edge}")
    print(f"   BOARD_POS   = {board_pos}")
    print("\n3) SUGGESTED ALIGHTING POSITION:")
    print(f"   ALIGHT_EDGE = {alight_edge}")
    print(f"   ALIGHT_POS  = {alight_pos}")
    print("\nDone. Next: fill demand.rou.xml (Step 4 of the README).")

if __name__ == "__main__":
    main()
