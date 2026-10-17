"""OpenStreetMap data inside the clip polygon (Phase 1, step 3).

Asks Overpass for every OSM node, way and relation inside data/processed/santa_rosa_buffered.geojson
(python -m sumo_track.network.clip_polygon), with all the nodes of every way that reaches into it,
so roads crossing the polygon edge stay whole. Saves the untouched response in data/raw/, plus a
provenance file beside it (query, OSM data timestamp, size, checksum) for the methods write-up.
Builds always read the saved copy; downloading again gives newer OSM data, so only refresh on
purpose.

    python -m sumo_track.network.osm_download            # use the saved download (downloads if missing)
    python -m sumo_track.network.osm_download --refresh  # download a fresh copy from Overpass

OSM data is licensed under ODbL: credit "© OpenStreetMap contributors" wherever it is used.
"""

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

from shapely.geometry import shape

from sumo_track.config import REPO_ROOT, Config
from sumo_track.network.clip_polygon import OUT_DIR as PROCESSED_DIR, OUT_NAME

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "pasada-sumo-thesis/0.1 (De La Salle University undergraduate thesis)"
QUERY_TIMEOUT_S = 600
DOWNLOAD_ATTEMPTS = 4
RETRY_WAIT_S = 30  # multiplied by the attempt number: 30 s, 60 s, 90 s

CLIP_GEOJSON = PROCESSED_DIR / f"{OUT_NAME}.geojson"


def provenance_path(osm_path):
    return osm_path.with_name(osm_path.name + ".provenance.json")


def build_query(clip) -> str:
    """Overpass QL: nodes and ways inside the polygon, ways completed with all their nodes, and
    the relations that use them (turn restrictions, multipolygons) WITHOUT their other members.
    Completing relations too (nwr + recurse, the first version) pulled in the whole of every
    province boundary and long-distance bus route touching the city - roads down to Mindanao."""
    if clip.geom_type != "Polygon" or clip.interiors:
        raise ValueError("Overpass poly: filter needs one polygon without holes")
    # poly: wants "lat lon lat lon ...", without repeating the first point at the end
    coords = list(clip.exterior.coords)[:-1]
    poly = " ".join(f"{lat:.7f} {lon:.7f}" for lon, lat in coords)
    return (f'[out:xml][timeout:{QUERY_TIMEOUT_S}];\n'
            f'(node(poly:"{poly}"); way(poly:"{poly}"););\n'
            f'(._;>;);\n'
            f'(._; rel(bn); rel(bw););\n'
            f'out meta;\n')


def download(query: str, osm_path) -> None:
    """Run the query and save the response as-is, retrying while Overpass is busy."""
    body = urllib.parse.urlencode({"data": query}).encode()
    request = urllib.request.Request(OVERPASS_URL, data=body, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        print(f"Downloading OSM data from {OVERPASS_URL} (attempt {attempt}/{DOWNLOAD_ATTEMPTS}); "
              f"this can take a few minutes")
        try:
            with urllib.request.urlopen(request, timeout=QUERY_TIMEOUT_S + 60) as response:
                raw = response.read()
            # a busy server can answer HTTP 200 with an HTML error page, or with OSM XML that
            # ends in a <remark> about a runtime error - check the content, not the status code
            head = raw[:2000].decode("utf-8", "replace")
            if "<osm" not in head:
                raise ValueError("got an error page instead of OSM data")
            remark = re.search(rb"<remark>(.*?)</remark>", raw[-5000:], re.S)
            if remark:
                raise ValueError(f"Overpass reported: {remark.group(1).decode('utf-8', 'replace').strip()}")
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            print(f"  failed: {e}")
            if attempt < DOWNLOAD_ATTEMPTS:
                wait = RETRY_WAIT_S * attempt
                print(f"  Overpass is a shared server and is often busy; retrying in {wait} s")
                time.sleep(wait)
            continue
        osm_path.parent.mkdir(parents=True, exist_ok=True)
        osm_path.write_bytes(raw)
        print(f"  saved {len(raw) / 1e6:.1f} MB to {osm_path.relative_to(REPO_ROOT).as_posix()}")
        return
    raise SystemExit("Overpass did not answer after several tries. Wait a few minutes and run again.")


def summarize(osm_path) -> dict:
    """Counts and checks on the saved file: every way must have all its nodes."""
    root = ET.parse(osm_path).getroot()
    meta = root.find("meta")
    nodes = {n.get("id") for n in root.iter("node")}
    lons = [float(n.get("lon")) for n in root.iter("node")]
    lats = [float(n.get("lat")) for n in root.iter("node")]
    ways = list(root.iter("way"))
    missing = sum(1 for w in ways for nd in w.iter("nd") if nd.get("ref") not in nodes)
    highways = Counter(t.get("v") for w in ways for t in w.iter("tag") if t.get("k") == "highway")
    return {
        "osm_data_timestamp": meta.get("osm_base") if meta is not None else None,
        "nodes": len(nodes),
        "ways": len(ways),
        "relations": sum(1 for _ in root.iter("relation")),
        "highway_ways": sum(highways.values()),
        "highway_ways_by_type": dict(highways.most_common()),
        "way_nodes_missing": missing,
        "node_extent_lon_lat": [round(min(lons), 4), round(min(lats), 4),
                                round(max(lons), 4), round(max(lats), 4)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Download OSM data inside the clip polygon from Overpass.")
    parser.add_argument("--refresh", action="store_true", help="download a fresh copy even if one is saved")
    args = parser.parse_args()

    config = Config.load()
    osm_path = config.dataset_path("osm_extract", must_exist=False)
    if not CLIP_GEOJSON.is_file():
        raise SystemExit(f"{CLIP_GEOJSON.relative_to(REPO_ROOT).as_posix()} is missing: "
                         "run python -m sumo_track.network.clip_polygon first")
    clip_feature = json.loads(CLIP_GEOJSON.read_text(encoding="utf-8"))["features"][0]
    query = build_query(shape(clip_feature["geometry"]))

    if args.refresh or not osm_path.exists():
        started = datetime.now(timezone.utc)
        download(query, osm_path)
        raw = osm_path.read_bytes()
        stats = summarize(osm_path)
        provenance = {
            "source": "OpenStreetMap via the Overpass API",
            "overpass_url": OVERPASS_URL,
            "downloaded_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "license": "ODbL 1.0, (c) OpenStreetMap contributors",
            "clip_polygon": clip_feature["properties"],
            "query": query,
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            **stats,
        }
        provenance_path(osm_path).write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    else:
        print(f"Using saved download {osm_path.relative_to(REPO_ROOT).as_posix()} (use --refresh to download again)")
        provenance = json.loads(provenance_path(osm_path).read_text(encoding="utf-8"))
        stats = summarize(osm_path)

    print(f"OSM data as of {stats['osm_data_timestamp']} (downloaded {provenance['downloaded_utc']})")
    print(f"  {stats['nodes']:,} nodes, {stats['ways']:,} ways, {stats['relations']:,} relations; "
          f"{stats['highway_ways']:,} ways tagged highway")
    print(f"  Top road types: " + ", ".join(f"{k} {v}" for k, v in list(stats['highway_ways_by_type'].items())[:6]))
    print(f"  Node extent: lon {stats['node_extent_lon_lat'][0]} to {stats['node_extent_lon_lat'][2]}, "
          f"lat {stats['node_extent_lon_lat'][1]} to {stats['node_extent_lon_lat'][3]} "
          f"(ways crossing the polygon edge reach a little beyond it)")
    print(f"  Check: way nodes missing from the file: {stats['way_nodes_missing']} (should be 0)")
    if stats["way_nodes_missing"]:
        raise SystemExit("Some ways are incomplete - the download is not usable as it is")


if __name__ == "__main__":
    main()
