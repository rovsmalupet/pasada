"""Phase 2: activity locations from OSM (category rules and duplicate merging).

    python -m pytest tests          (from the sumo_track folder, with .venv active)
"""

import pytest
from shapely.geometry import Point, box

from sumo_track.locations.extract_pois import Feature, classify, dedupe, load_rules

RULES = load_rules()


def test_the_first_matching_category_wins():
    # a terminal inside a mall building is a terminal: terminal comes first in the rules file
    assert classify({"amenity": "bus_station", "shop": "mall"}, RULES)[0] == "terminal"
    assert classify({"office": "anything"}, RULES) == ("workplace", "office=anything", "place")
    assert classify({"amenity": "restaurant"}, RULES) is None


def test_tricycle_terminals_are_excluded():
    assert classify({"public_transport": "station", "name": "Malitlit Tricycle Terminal"}, RULES) is None
    assert classify({"public_transport": "station", "name": "Tagaytay Jeepney Terminal"}, RULES)[0] == "terminal"


def test_a_bad_level_in_the_rules_file_fails(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("school:\n  rules:\n    - {key: amenity, values: [school], level: campus}\n")
    with pytest.raises(ValueError, match="level"):
        load_rules(path)


def feature(osm_id, geom, level="place", name="", category="school"):
    return Feature(osm_id, category, "test=yes", level, name, geom)


def test_a_building_inside_a_campus_belongs_to_the_campus():
    campus = feature("way/1", box(0, 0, 200, 200), name="Dita Elementary School")
    building = feature("way/2", box(50, 50, 60, 60), level="part")
    kept, merges = dedupe([campus, building], 100)
    assert kept == [campus] and campus.merged == ["way/2"]
    assert merges == [("way/1", "way/2", "inside")]


def test_a_landuse_area_is_dropped_only_when_something_is_mapped_inside_it():
    estate = feature("way/1", box(0, 0, 1000, 1000), level="area", category="workplace")
    empty_estate = feature("way/2", box(5000, 0, 6000, 1000), level="area", category="workplace")
    factory = feature("way/3", box(100, 100, 150, 150), level="part", name="Factory", category="workplace")
    kept, _ = dedupe([estate, empty_estate, factory], 100)
    assert {f.osm_id for f in kept} == {"way/2", "way/3"}


def test_same_name_is_merged_only_when_close():
    a = feature("node/1", Point(0, 0), name="Puregold", category="market")
    b = feature("node/2", Point(60, 0), name="PUREGOLD", category="market")
    c = feature("node/3", Point(5000, 0), name="Puregold", category="market")
    kept, _ = dedupe([a, b, c], 100)
    assert len(kept) == 2


def test_only_unnamed_buildings_are_merged_by_distance():
    school = feature("way/1", box(0, 0, 50, 50), name="Canossa School")
    unnamed_building = feature("way/2", box(70, 0, 80, 10), level="part")
    named_place = feature("node/3", Point(80, 0), name="Another School")
    kept, merges = dedupe([school, unnamed_building, named_place], 100)
    assert {f.osm_id for f in kept} == {"way/1", "node/3"}
    assert ("way/1", "way/2", "unnamed building nearby") in merges


def test_categories_never_merge_with_each_other():
    mall = feature("way/1", box(0, 0, 300, 300), name="SM City", category="mall")
    supermarket = feature("node/2", Point(100, 100), name="SM Supermarket", category="market")
    kept, _ = dedupe([mall, supermarket], 100)
    assert len(kept) == 2
