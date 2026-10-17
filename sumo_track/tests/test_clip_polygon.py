"""Phase 1, step 2: the buffered clip polygon and the study boundary loader.

    python -m pytest tests          (from the sumo_track folder, with .venv active)
"""

import hashlib
import json

import pytest
from shapely.geometry import Polygon, box

from sumo_track.config import Config, Dataset
from sumo_track.network.boundary import load_study_boundary
from sumo_track.network.clip_polygon import buffer_m, min_gap_m, poly_text
from sumo_track.network.osm_download import build_query

# a ~1 km square in Santa Rosa (lon/lat), well inside UTM zone 51N
SQUARE = box(121.09, 14.27, 121.10, 14.28)


def test_buffer_grows_the_boundary_by_the_distance_all_round():
    clip = buffer_m(SQUARE, 500)
    assert clip.covers(SQUARE)
    assert min_gap_m(SQUARE, clip) == pytest.approx(500, rel=0.01)


def test_poly_text_has_one_section_per_ring_and_holes_marked():
    with_hole = Polygon(SQUARE.exterior.coords,
                        [box(121.094, 14.274, 121.096, 14.276).exterior.coords])
    lines = poly_text("test", with_hole).splitlines()
    assert lines[0] == "test"
    assert lines[1] == "1" and "!1_1" in lines
    assert lines[-2:] == ["END", "END"]
    lon, lat = map(float, lines[2].split())     # longitude first
    assert 121 < lon < 122 and 14 < lat < 15


def test_overpass_query_lists_latitude_first_without_repeating_the_first_point():
    query = build_query(SQUARE)
    poly = query.split('poly:"')[1].split('"')[0].split()
    pairs = list(zip(map(float, poly[0::2]), map(float, poly[1::2])))
    assert len(pairs) == len(SQUARE.exterior.coords) - 1
    assert all(14 < lat < 15 and 121 < lon < 122 for lat, lon in pairs)
    assert "(._;>;);" in query       # ways come with all their nodes
    assert "nwr(" not in query       # relations must not be completed (they span provinces)


def _config_for(path):
    dataset = Dataset(name="study_boundary", active="real", placeholder=None, real=str(path))
    return Config(params={}, datasets={"study_boundary": dataset})


def _write_boundary(tmp_path, geometry=None, crs=None, sidecar_sha=None, crlf=False):
    data = {"type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"name": "test"},
                          "geometry": geometry or json.loads(json.dumps(SQUARE.__geo_interface__))}]}
    if crs:
        data["crs"] = {"type": "name", "properties": {"name": crs}}
    text = json.dumps(data, indent=1) + "\n"
    path = tmp_path / "boundary.geojson"
    path.write_bytes(text.replace("\n", "\r\n").encode() if crlf else text.encode())
    if sidecar_sha:
        sha = hashlib.sha256(text.encode()).hexdigest() if sidecar_sha == "match" else sidecar_sha
        (tmp_path / "boundary.metadata.json").write_text(json.dumps({"selected_boundary_sha256": sha}))
    return path


def test_loader_accepts_a_wgs84_polygon_and_a_crlf_checkout_of_a_verified_file(tmp_path):
    path = _write_boundary(tmp_path, crs="urn:ogc:def:crs:OGC:1.3:CRS84", sidecar_sha="match", crlf=True)
    boundary, props = load_study_boundary(_config_for(path))
    assert boundary.equals(SQUARE) and props["name"] == "test"


def test_loader_rejects_a_file_changed_after_verification(tmp_path):
    path = _write_boundary(tmp_path, sidecar_sha="0" * 64)
    with pytest.raises(ValueError, match="checksum"):
        load_study_boundary(_config_for(path))


def test_loader_rejects_a_projected_crs_and_a_non_polygon(tmp_path):
    with pytest.raises(ValueError, match="WGS84"):
        load_study_boundary(_config_for(_write_boundary(tmp_path, crs="EPSG:32651")))
    line = {"type": "LineString", "coordinates": [[121.09, 14.27], [121.10, 14.28]]}
    with pytest.raises(ValueError, match="polygon"):
        load_study_boundary(_config_for(_write_boundary(tmp_path, geometry=line)))
