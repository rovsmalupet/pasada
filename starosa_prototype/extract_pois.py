"""
extract_pois.py — pull categorized activity locations out of a raw OSM extract.

Usage:
    python extract_pois.py santarosa.osm pois_raw.csv

Reads OSM nodes and ways, matches them against a tag->category map covering
the activity categories from Section 4.2.3 (schools, workplaces, malls,
markets, terminals, recreation), and writes one row per matched location.

For ways (building outlines etc.), the POI coordinate is approximated as the
centroid of the way's corner nodes, since a way has no single point.
"""
import sys
import csv
import xml.etree.ElementTree as ET
from collections import Counter

# tag key -> { tag value : category }.  "*" means "any value counts".
TAG_CATEGORY_MAP = {
    "amenity": {
        "school": "school",
        "university": "school",
        "college": "school",
        "marketplace": "market",
        "bus_station": "terminal",
        "community_centre": "recreation",
    },
    "shop": {
        "mall": "mall_commercial",
        "supermarket": "mall_commercial",
        "convenience": "market",
    },
    "office": {"*": "workplace"},
    "landuse": {
        "commercial": "workplace",
        "industrial": "workplace",
    },
    "leisure": {"*": "recreation"},
    "public_transport": {"station": "terminal"},
    "highway": {"bus_stop": "terminal"},
    "building": {"commercial": "mall_commercial"},
}


def categorize(tags):
    for key, value in tags.items():
        mapping = TAG_CATEGORY_MAP.get(key)
        if not mapping:
            continue
        if value in mapping:
            return mapping[value]
        if "*" in mapping:
            return mapping["*"]
    return None


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    osm_file, out_file = sys.argv[1], sys.argv[2]

    tree = ET.parse(osm_file)
    root = tree.getroot()

    # index all node coordinates first (needed to compute way centroids)
    node_coords = {}
    for node in root.findall("node"):
        node_coords[node.get("id")] = (
            float(node.get("lat")),
            float(node.get("lon")),
        )

    rows = []
    counts = Counter()
    seen_ids = set()

    def tags_of(elem):
        return {t.get("k"): t.get("v") for t in elem.findall("tag")}

    # --- point features: nodes with a matching tag ---
    for node in root.findall("node"):
        tags = tags_of(node)
        if not tags:
            continue
        category = categorize(tags)
        if category is None:
            continue
        osm_id = f"node/{node.get('id')}"
        if osm_id in seen_ids:
            continue
        seen_ids.add(osm_id)
        lat, lon = float(node.get("lat")), float(node.get("lon"))
        name = tags.get("name", f"Unnamed {category}")
        rows.append([osm_id, name, category, lat, lon, "osm"])
        counts[category] += 1

    # --- area features: ways with a matching tag, use centroid of corners ---
    for way in root.findall("way"):
        tags = tags_of(way)
        if not tags:
            continue
        category = categorize(tags)
        if category is None:
            continue
        refs = [nd.get("ref") for nd in way.findall("nd")]
        coords = [node_coords[r] for r in refs if r in node_coords]
        if not coords:
            continue
        lat = sum(c[0] for c in coords) / len(coords)
        lon = sum(c[1] for c in coords) / len(coords)
        osm_id = f"way/{way.get('id')}"
        if osm_id in seen_ids:
            continue
        seen_ids.add(osm_id)
        name = tags.get("name", f"Unnamed {category}")
        rows.append([osm_id, name, category, lat, lon, "osm"])
        counts[category] += 1

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "category", "lat", "lon", "source"])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} POIs to {out_file}\n")
    for cat, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"{cat:18s}: {n}")
    print("-" * 20)
    print(f"{'TOTAL':18s}: {sum(counts.values())}")

    if not rows:
        print("\nNo POIs found. Your study box may be too small / residential-only,")
        print("or the .osm file may be trimmed. Try widening the box.")


if __name__ == "__main__":
    main()
