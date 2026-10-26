"""Activity locations from OpenStreetMap (Phase 2, step 2).

Reads the saved OSM extract (dataset osm_extract) and keeps the features whose tags match the
category rules in config/activity_categories.yaml: schools, workplaces, markets, malls and
terminals. Then:

  1. Each feature gets one representative point: the node itself, a point inside a building or
     area outline, or the middle of an open way.
  2. Only features whose point lies inside the EXACT study boundary are kept. The buffered clip
     polygon is only for the road network.
  3. Duplicates are merged, within a category:
     - a feature lying inside a mapped place outline is part of that place;
     - a land-use area is dropped when a place or part lies inside it;
     - the same name closer than location_dedup_distance_m is one place mapped twice;
     - an unnamed building closer than that to a location is one more building of it.
     Every merge is recorded.

Writes the activity_locations placeholder (data/placeholder/locations.csv) in the plan's
location schema, with zone and road fields empty until zoning and snapping (Phase 2, steps 3-4).
Also writes a provenance file beside it and data/processed/locations.geojson to view in QGIS.

    python -m sumo_track.locations.extract_pois
"""

import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yaml
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, mapping
from shapely.ops import transform
from shapely.prepared import prep

from sumo_track.config import REPO_ROOT, Config
from sumo_track.network.boundary import load_study_boundary

RULES_FILE = REPO_ROOT / "config" / "activity_categories.yaml"
GEOJSON_FILE = REPO_ROOT / "data" / "processed" / "locations.geojson"
LEVELS = ("place", "part", "area")
COLUMNS = ["location_id", "category", "subtype", "name", "lat", "lon", "source_type", "osm_ids",
           "area_m2", "zone_id", "edge_id", "lane_pos_m", "snap_distance_m", "flag"]

_to_metres = Transformer.from_crs("EPSG:4326", "EPSG:32651", always_xy=True).transform
_to_lonlat = Transformer.from_crs("EPSG:32651", "EPSG:4326", always_xy=True).transform


@dataclass
class Feature:
    osm_id: str                 # "node/123" or "way/456"
    category: str
    subtype: str                # the matching tag, e.g. "amenity=school"
    level: str
    name: str
    geom: object                # shapely geometry in metres (UTM 51N)
    merged: list = field(default_factory=list)   # osm ids merged into this one

    @property
    def point(self):
        if isinstance(self.geom, Polygon):
            return self.geom.representative_point()
        if isinstance(self.geom, LineString):
            return self.geom.interpolate(0.5, normalized=True)
        return self.geom

    @property
    def area_m2(self):
        return self.geom.area if isinstance(self.geom, Polygon) else 0.0


# ---------------------------------------------------------------------------------- rules
def load_rules(path=RULES_FILE):
    """{category: {"rules": [(key, values or None for any, level)], "exclude": regex or None}},
    in file order (the order decides which category wins)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules = {}
    for category, spec in data.items():
        parsed = []
        for r in spec["rules"]:
            if r["level"] not in LEVELS:
                raise ValueError(f"{path.name}: {category}: level must be one of {LEVELS}, not {r['level']}")
            values = None if r["values"] == "*" else set(r["values"])
            parsed.append((r["key"], values, r["level"]))
        exclude = spec.get("exclude_name")
        rules[category] = {"rules": parsed, "exclude": re.compile(exclude, re.I) if exclude else None}
    return rules


def classify(tags, rules):
    """(category, subtype, level) of the first matching rule, or None."""
    for category, spec in rules.items():
        for key, values, level in spec["rules"]:
            value = tags.get(key)
            if value is not None and (values is None or value in values):
                if spec["exclude"] and spec["exclude"].search(tags.get("name", "")):
                    return None
                return category, f"{key}={value}", level
    return None


# -------------------------------------------------------------------------------- reading
def read_osm(path, rules):
    """Matching features, plus the number of matching relations (not assembled: none matched in
    the 2026-10-16 data; a warning shows if a later extract has some)."""
    nodes, features, relations = {}, [], 0
    for _, el in ET.iterparse(path):
        if el.tag not in ("node", "way", "relation"):
            continue
        tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
        if el.tag == "node":
            lonlat = (float(el.get("lon")), float(el.get("lat")))
            nodes[el.get("id")] = lonlat
            match = classify(tags, rules) if tags else None
            if match:
                features.append(Feature(f"node/{el.get('id')}", *match, tags.get("name", ""),
                                        Point(_to_metres(*lonlat))))
        elif el.tag == "way":
            match = classify(tags, rules) if tags else None
            if match:
                refs = [nd.get("ref") for nd in el.findall("nd")]
                pts = [_to_metres(*nodes[r]) for r in refs if r in nodes]
                if len(pts) >= 2:
                    closed = refs[0] == refs[-1] and len(pts) >= 4
                    geom = Polygon(pts) if closed else LineString(pts)
                    if closed and not geom.is_valid:
                        geom = geom.buffer(0)          # repairs self-touching outlines
                    features.append(Feature(f"way/{el.get('id')}", *match, tags.get("name", ""), geom))
        elif tags.get("type") == "multipolygon" and classify(tags, rules):
            relations += 1
        el.clear()
    return features, relations


# ---------------------------------------------------------------------------- duplicates
def norm_name(name):
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def dedupe(features, distance_m):
    """Merge duplicates within each category (see the module docstring). Returns the kept
    features and a list of merges (kept id, dropped id, reason)."""
    kept_all, merges = [], []
    by_category = defaultdict(list)
    for f in features:
        by_category[f.category].append(f)
    for feats in by_category.values():
        # 1. a place or part inside a bigger place outline belongs to that place
        outlines = sorted((f for f in feats if f.level == "place" and isinstance(f.geom, Polygon)),
                          key=lambda f: -f.area_m2)
        kept = []
        for f in feats:
            container = None if f.level == "area" else next(
                (o for o in outlines if o is not f and o.area_m2 > f.area_m2 and o.geom.contains(f.point)), None)
            if container:
                container.merged.append(f.osm_id)
                merges.append((container.osm_id, f.osm_id, "inside"))
            else:
                kept.append(f)
        # 2. a land-use area with a place or part inside is represented by those
        specific = [f for f in kept if f.level != "area"]
        areas_with_content = {id(a) for a in kept if a.level == "area"
                              and any(a.geom.contains(s.point) for s in specific)}
        for a in kept:
            if id(a) in areas_with_content:
                merges.append(("-", a.osm_id, "area has places inside"))
        kept = [f for f in kept if id(f) not in areas_with_content]
        # 3. same name nearby: one place mapped twice; keep the one with the larger outline
        # 4. an unnamed building near a location is one more building of it (e.g. a school's
        #    other buildings when its campus is not mapped)
        kept.sort(key=lambda f: -f.area_m2)
        result = []
        for f in kept:
            near = [k for k in result if k.point.distance(f.point) <= distance_m]
            twin = next((k for k in near if f.name and norm_name(k.name) == norm_name(f.name)), None)
            reason = "same name nearby"
            if twin is None and not f.name and f.level == "part" and near:
                twin, reason = near[0], "unnamed building nearby"
            if twin:
                twin.merged.append(f.osm_id)
                merges.append((twin.osm_id, f.osm_id, reason))
            else:
                result.append(f)
        kept_all.extend(result)
    return kept_all, merges


# ----------------------------------------------------------------------------------- main
def sha256_text(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main():
    config = Config.load()
    osm_path = config.dataset_path("osm_extract")
    out_path = REPO_ROOT / config.datasets["activity_locations"].placeholder
    distance = float(config.param("location_dedup_distance_m"))
    rules = load_rules()
    boundary, _ = load_study_boundary(config)
    city = prep(transform(_to_metres, boundary))

    features, relations = read_osm(osm_path, rules)
    inside = [f for f in features if city.contains(f.point)]
    kept, merges = dedupe(inside, distance)
    if relations:
        print(f"WARNING: {relations} multipolygon relations match a category but are not assembled")

    order = list(rules)
    kept.sort(key=lambda f: (order.index(f.category), norm_name(f.name) or "~", f.osm_id))
    rows = []
    for i, f in enumerate(kept, start=1):
        lon, lat = _to_lonlat(f.point.x, f.point.y)
        rows.append({"location_id": f"LOC_{i:04d}", "category": f.category, "subtype": f.subtype,
                     "name": f.name, "lat": round(lat, 7), "lon": round(lon, 7), "source_type": "osm",
                     "osm_ids": ";".join([f.osm_id] + f.merged), "area_m2": round(f.area_m2),
                     "zone_id": "", "edge_id": "", "lane_pos_m": "", "snap_distance_m": "", "flag": ""})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    GEOJSON_FILE.parent.mkdir(parents=True, exist_ok=True)
    GEOJSON_FILE.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {k: r[k] for k in ("location_id", "category", "subtype", "name", "area_m2")},
         "geometry": mapping(Point(r["lon"], r["lat"]))} for r in rows]}) + "\n", encoding="utf-8")

    found = Counter(f.category for f in features)
    in_city = Counter(f.category for f in inside)
    final = Counter(r["category"] for r in rows)
    osm_prov = osm_path.with_name(osm_path.name + ".provenance.json")
    provenance = {
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "osm_extract": config.datasets["osm_extract"].active_source,
        "osm_sha256": hashlib.sha256(osm_path.read_bytes()).hexdigest(),
        "osm_data_timestamp": (json.loads(osm_prov.read_text(encoding="utf-8")).get("osm_data_timestamp")
                               if osm_prov.is_file() else None),
        "rules_file": "config/activity_categories.yaml", "rules_sha256": sha256_text(RULES_FILE),
        "boundary": config.datasets["study_boundary"].active_source,
        "dedup_distance_m": distance,
        "counts": {c: {"matched": found[c], "inside_boundary": in_city[c], "locations": final[c]} for c in order},
        "unassembled_relations": relations,
        "merges": [{"kept": k, "dropped": d, "reason": why} for k, d, why in merges],
    }
    prov_path = out_path.with_name(out_path.stem + ".provenance.json")
    prov_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

    print(f"Activity locations from {config.datasets['osm_extract'].active_source} "
          f"(OSM data as of {provenance['osm_data_timestamp']}), inside the study boundary")
    print(f"  {'category':10s} {'matched':>8s} {'in city':>8s} {'locations':>10s} {'named':>6s}")
    for c in order:
        named = sum(1 for r in rows if r["category"] == c and r["name"])
        print(f"  {c:10s} {found[c]:8d} {in_city[c]:8d} {final[c]:10d} {named:6d}")
    print(f"  {len(merges)} duplicates merged: " + ", ".join(
        f"{n} {why}" for why, n in Counter(m[2] for m in merges).most_common()))
    for path in (out_path, prov_path, GEOJSON_FILE):
        print(f"  Wrote {path.relative_to(REPO_ROOT).as_posix()}")


if __name__ == "__main__":
    main()
