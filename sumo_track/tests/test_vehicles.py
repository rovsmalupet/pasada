"""Phase 3 / 6: jeepney route matching, vehicle types, jeepney flows.

    python -m pytest tests          (from the sumo_track folder, with .venv active)
"""

import subprocess
import xml.etree.ElementTree as ET

import pytest
import sumolib

from sumo_track.config import Config
from sumo_track.jeepney.flows import flows_xml
from sumo_track.jeepney.route_match import (bridge_gaps, connected, drop_standstill,
                                             load_observations, trip_name)
from sumo_track.jeepney.vtypes import vtypes_xml

GPX = """<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1"><trk><trkseg>
<trkpt lat="14.25" lon="121.06"><time>2026-10-14T00:17:37Z</time></trkpt>
<trkpt lat="14.26" lon="121.07"><time>2026-10-14T00:18:37Z</time></trkpt>
</trkseg></trk></gpx>"""
STOPS_HEADER = ("trip,event,snapped_latitude,snapped_longitude,approx_gps_time_ph,"
                "headcount_after,boarded,alighted,notes\n")


@pytest.fixture(scope="module")
def grid(tmp_path_factory):
    """A 3x3 grid of 100 m two-way streets, built with SUMO's netgenerate."""
    path = tmp_path_factory.mktemp("net") / "grid.net.xml"
    subprocess.run([sumolib.checkBinary("netgenerate"), "--grid", "--grid.number", "3",
                    "--grid.length", "100", "-o", str(path)], check=True, capture_output=True)
    return sumolib.net.readNet(str(path))


def test_standstill_points_are_dropped():
    xy = [(0, 0), (0.3, 0), (0.6, 0), (5, 0), (5, 0.2), (12, 0)]
    assert drop_standstill(xy, 1.0) == [(0, 0), (5, 0), (12, 0)]


def test_trip_name_comes_from_the_file_name(tmp_path):
    assert trip_name(tmp_path / "Paseo_to_Balibago_2026-10-14_annotated.gpx") == "Paseo_to_Balibago"


def test_a_gap_is_bridged_with_the_shortest_path_and_reported(grid):
    a, b = grid.getEdge("A0A1"), grid.getEdge("B1B2")          # not directly connected
    assert not connected(a, b, "passenger")
    route, gaps = bridge_gaps(grid, [a, b], "passenger", max_gap_m=300)
    assert all(connected(x, y, "passenger") for x, y in zip(route, route[1:]))
    assert route[0] == a and route[-1] == b
    # A0A1 ends at node A1 (0, 100); B1B2 starts at node B1 (100, 100)
    assert len(gaps) == 1 and gaps[0]["air_gap_m"] == pytest.approx(100, rel=0.01)


def test_a_gap_beyond_the_limit_stops_the_build(grid):
    with pytest.raises(ValueError, match="exceeds"):
        bridge_gaps(grid, [grid.getEdge("A0A1"), grid.getEdge("B1B2")], "passenger", max_gap_m=50)


def test_every_trip_needs_its_stops(tmp_path):
    (tmp_path / "Paseo_to_Balibago_2026-10-14_annotated.gpx").write_text(GPX)
    (tmp_path / "stops.csv").write_text(STOPS_HEADER + "Other_trip,Start,14.25,121.06,,,,,\n")
    with pytest.raises(ValueError, match="no stops"):
        load_observations(tmp_path)


def test_stops_are_read_with_a_byte_order_mark(tmp_path):
    (tmp_path / "Paseo_to_Balibago_2026-10-14_annotated.gpx").write_text(GPX)
    (tmp_path / "stops.csv").write_text("﻿" + STOPS_HEADER +
                                        "Paseo_to_Balibago,Start,14.25,121.06,,12,,,\n", encoding="utf-8")
    trips, _ = load_observations(tmp_path)
    assert trips[0]["trip"] == "Paseo_to_Balibago" and len(trips[0]["points"]) == 2


def test_vtypes_take_their_values_from_params():
    config = Config.load()
    jeepney = ET.fromstring(vtypes_xml(config)).find("vType[@id='jeepney']")
    assert jeepney.get("personCapacity") == str(config.param("jeepney_capacity"))
    assert jeepney.get("vClass") == config.param("jeepney_vclass")


def test_each_route_gets_a_flow_whose_line_is_the_route():
    routes = [{"route_id": "JEEP_A", "edges": ["e1", "e2"]}, {"route_id": "JEEP_B", "edges": ["e3"]}]
    root = ET.fromstring(flows_xml(routes, 300, 86400))
    flows = root.findall("flow")
    assert [f.get("line") for f in flows] == ["JEEP_A", "JEEP_B"]
    assert {f.get("period") for f in flows} == {"300"} and {f.get("end") for f in flows} == {"86400"}
    assert root.find("route[@id='r_JEEP_A']").get("edges") == "e1 e2"
