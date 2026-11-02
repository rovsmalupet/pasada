"""The map: the study area (AREA_BARANGAYS), its OSM extract and its SUMO network.

  1. AREA: the union of the barangays in AREA_BARANGAYS (data/santa_rosa_barangays.geojson, from
     get_barangays.py) -> data/area.geojson. Homes and destinations are kept inside it.
  2. OSM: from OSM_SOURCE (sumo_track's download of the whole city), every way with at least
     one node inside the area, whole, with all its nodes, and the turn restrictions among them
     -> bayan.osm. Like the OSM Web Wizard's box, roads crossing the edge are kept whole.
  3. NETWORK: netconvert -c netconvert.netccfg -> bayan.net.xml (SLEX and its ramps removed).
     It prints the car-lane length, which sets BACKGROUND_PER_HOUR in settings.py.

Rerun build.py after this: edge IDs and lengths change.

    python make_map.py
"""

import json
import subprocess
import xml.etree.ElementTree as ET

import shapely
import sumolib
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

import settings as S


def make_area():
    features = json.loads(S.BARANGAYS_FILE.read_text(encoding="utf-8"))["features"]
    chosen = [f for f in features if f["properties"]["adm4_pcode"] in S.AREA_BARANGAYS]
    missing = set(S.AREA_BARANGAYS) - {f["properties"]["adm4_pcode"] for f in chosen}
    if missing:
        raise SystemExit(f"Not in {S.BARANGAYS_FILE.name}: {sorted(missing)}")
    area = unary_union([shape(f["geometry"]) for f in chosen])
    names = sorted(f["properties"]["adm4_name"] for f in chosen)
    S.AREA_FILE.write_text(json.dumps({"type": "Feature", "properties": {
        "barangays": names, "pcodes": sorted(S.AREA_BARANGAYS), "source": S.BARANGAYS_FILE.name},
        "geometry": mapping(area)}, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Area: {len(chosen)} barangays, {area.geom_type} -> {S.AREA_FILE.relative_to(S.HERE)}")
    return area


def cut_osm(area):
    root = ET.parse(S.OSM_SOURCE).getroot()
    ids, lons, lats = [], [], []
    for n in root.iter("node"):
        ids.append(n.get("id"))
        lons.append(float(n.get("lon")))
        lats.append(float(n.get("lat")))
    inside = {i for i, ok in zip(ids, shapely.contains_xy(area, lons, lats)) if ok}
    ways, needed = [], set()
    for w in root.iter("way"):
        refs = [nd.get("ref") for nd in w.iter("nd")]
        if any(r in inside for r in refs):
            ways.append(w)
            needed.update(refs)
    kept_ways = {w.get("id") for w in ways}
    relations = []
    for r in root.iter("relation"):
        tags = {t.get("k"): t.get("v") for t in r.iter("tag")}
        members = list(r.iter("member"))
        if tags.get("type") == "restriction" and all(
                (m.get("ref") in kept_ways) if m.get("type") == "way" else (m.get("ref") in needed)
                for m in members if m.get("type") in ("way", "node")):
            relations.append(r)
    out = ET.Element("osm", root.attrib)
    out.extend([n for n in root.iter("node") if n.get("id") in needed] + ways + relations)
    ET.ElementTree(out).write(S.OSM_FILE, encoding="UTF-8", xml_declaration=True)
    print(f"OSM: {len(needed):,} nodes ({len(inside):,} inside the area), {len(ways):,} ways, "
          f"{len(relations)} turn restrictions -> {S.OSM_FILE.name} ({S.OSM_FILE.stat().st_size / 1e6:.1f} MB)")


def build_network():
    result = subprocess.run([sumolib.checkBinary("netconvert"), "-c", "netconvert.netccfg"], cwd=S.HERE,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"netconvert failed:\n{result.stderr[-3000:]}")
    warnings = [line for line in result.stderr.splitlines() if line.startswith("Warning")]
    net = sumolib.net.readNet(str(S.NET_FILE))
    edges = [e for e in net.getEdges() if e.getFunction() != "internal"]
    car_km = sum(l.getLength() for e in edges for l in e.getLanes() if l.allows("passenger")) / 1000
    print(f"Network: {len(edges):,} edges, {len(net.getNodes()):,} junctions, {car_km:.1f} km of car lanes "
          f"-> {S.NET_FILE.name} ({len(warnings)} netconvert warnings)")
    print(f"Background at x1 as dense as sumo_track_prototype's 1,800/h per type on 1,996.5 km: "
          f"BACKGROUND_PER_HOUR = {1800 * car_km / 1996.5:.0f} per type")


def main():
    area = make_area()
    cut_osm(area)
    build_network()
    print("Next: python build.py")


if __name__ == "__main__":
    main()
