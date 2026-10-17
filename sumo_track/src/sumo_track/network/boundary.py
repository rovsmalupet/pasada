"""Santa Rosa City boundary: the placeholder from OpenStreetMap, and the loader for whichever
boundary the data manifest makes active.

Placeholder: downloads the city's administrative boundary relation from Overpass once, keeps the
untouched response in data/raw/, and converts it into the GeoJSON polygon registered in the data
manifest as study_boundary (placeholder). Converting the same saved response always gives the
same file.

    python -m sumo_track.network.boundary            # use the saved download (downloads if missing)
    python -m sumo_track.network.boundary --refresh  # download a fresh copy from Overpass

Other modules read the active boundary (placeholder or real) with load_study_boundary().

OSM data is licensed under ODbL: credit "© OpenStreetMap contributors" wherever it is used.
"""

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from pyproj import Geod
from shapely.geometry import LineString, mapping
from shapely.geometry import shape as from_geojson
from shapely.ops import polygonize_full, unary_union

from sumo_track.config import REPO_ROOT, Config

OSM_RELATION_ID = 1335521  # Santa Rosa City, Laguna (boundary=administrative, admin_level=6)
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "pasada-sumo-thesis/0.1 (De La Salle University undergraduate thesis)"
DOWNLOAD_ATTEMPTS = 4
RETRY_WAIT_S = 20  # multiplied by the attempt number: 20 s, 40 s, 60 s

RAW_FILE = REPO_ROOT / "data" / "raw" / f"osm_boundary_relation_{OSM_RELATION_ID}.json"


def download() -> None:
    """Fetch the relation with full member geometry and save the response as-is."""
    query = f"[out:json][timeout:90];rel({OSM_RELATION_ID});out meta geom;"
    body = urllib.parse.urlencode({"data": query}).encode()
    request = urllib.request.Request(OVERPASS_URL, data=body, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        print(f"Downloading relation {OSM_RELATION_ID} from {OVERPASS_URL} (attempt {attempt}/{DOWNLOAD_ATTEMPTS})")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read()
            # A busy server can answer HTTP 200 with an HTML error page, or with JSON carrying a
            # "remark" about a runtime error, so check the content, not just the status code.
            data = json.loads(raw)
            if "remark" in data:
                raise ValueError(f"Overpass reported: {data['remark']}")
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            reason = "got an error page instead of data" if isinstance(e, json.JSONDecodeError) else str(e)
            print(f"  failed: {reason}")
            if attempt < DOWNLOAD_ATTEMPTS:
                wait = RETRY_WAIT_S * attempt
                print(f"  Overpass is a shared server and is often busy; retrying in {wait} s")
                time.sleep(wait)
            continue
        RAW_FILE.parent.mkdir(parents=True, exist_ok=True)
        RAW_FILE.write_bytes(raw)
        print(f"  saved {len(raw):,} bytes to {RAW_FILE.relative_to(REPO_ROOT).as_posix()}")
        return
    raise SystemExit("Overpass did not answer after several tries. Wait a few minutes and run again.")


def build_polygon(relation: dict):
    """Join the relation's border ways into one closed (Multi)Polygon."""
    roles = {m["role"] for m in relation["members"] if m["type"] == "way"}
    if roles != {"outer"}:
        raise ValueError(f"Expected only 'outer' border ways, found roles {sorted(roles)}")
    ways = [
        LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
        for m in relation["members"]
        if m["type"] == "way"
    ]
    polygons, cuts, dangles, invalid = polygonize_full(unary_union(ways))
    if polygons.is_empty or not (cuts.is_empty and dangles.is_empty and invalid.is_empty):
        raise ValueError("The border ways do not form a closed outline (a segment is missing or broken in OSM)")
    shape = unary_union(list(polygons.geoms))
    if not shape.is_valid:
        raise ValueError("The assembled boundary polygon is not valid")
    return shape, len(ways)


def area_km2(shape) -> float:
    """True area on the WGS84 ellipsoid, not the distorted area in degrees."""
    area_m2, _ = Geod(ellps="WGS84").geometry_area_perimeter(shape)
    return abs(area_m2) / 1e6


# GeoJSON names for WGS84 longitude/latitude (GeoJSON's default when "crs" is absent)
_WGS84_NAMES = {"urn:ogc:def:crs:OGC:1.3:CRS84", "urn:ogc:def:crs:OGC::CRS84",
                "EPSG:4326", "urn:ogc:def:crs:EPSG::4326"}


def _check_sidecar_checksum(path, raw: bytes) -> None:
    """If the boundary ships with a provenance sidecar (<name>.metadata.json, as the data
    team's PSA/NAMRIA boundary does), the file must still match the checksum recorded there."""
    sidecar = path.with_name(path.stem + ".metadata.json")
    if not sidecar.is_file():
        return
    expected = json.loads(sidecar.read_text(encoding="utf-8")).get("selected_boundary_sha256")
    if not expected:
        return
    # git on Windows (core.autocrlf) checks text files out with CRLF line endings; the
    # checksum was taken over the original LF file
    actual = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    if actual != expected:
        raise ValueError(f"{path.name} no longer matches the checksum in {sidecar.name}: "
                         "the boundary file was changed after it was verified")


def load_study_boundary(config):
    """The active study boundary (data manifest: study_boundary) as a shapely (Multi)Polygon
    in WGS84 longitude/latitude, plus the feature's properties.

    Fails loudly unless the file holds exactly one valid polygon feature in WGS84, and, when a
    provenance sidecar exists, still matches its checksum."""
    path = config.dataset_path("study_boundary")
    raw = path.read_bytes()
    _check_sidecar_checksum(path, raw)
    data = json.loads(raw)

    crs = (data.get("crs") or {}).get("properties", {}).get("name")
    if crs is not None and crs not in _WGS84_NAMES:
        raise ValueError(f"{path.name}: expected WGS84 longitude/latitude, found CRS {crs}")
    features = data["features"] if data.get("type") == "FeatureCollection" else [data]
    if len(features) != 1:
        raise ValueError(f"{path.name}: expected one boundary feature, found {len(features)}")
    boundary = from_geojson(features[0]["geometry"])
    if boundary.geom_type not in ("Polygon", "MultiPolygon") or boundary.is_empty:
        raise ValueError(f"{path.name}: expected a polygon, found {boundary.geom_type}")
    if not boundary.is_valid:
        raise ValueError(f"{path.name}: the boundary polygon is not valid")
    min_lon, min_lat, max_lon, max_lat = boundary.bounds
    if not (-180 <= min_lon <= max_lon <= 180 and -90 <= min_lat <= max_lat <= 90):
        raise ValueError(f"{path.name}: coordinates are not longitude/latitude {boundary.bounds}")
    return boundary, features[0].get("properties") or {}


def convert(output_path) -> None:
    data = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    relation = data["elements"][0]
    tags = relation["tags"]
    if tags.get("boundary") != "administrative" or tags.get("admin_level") != "6":
        raise ValueError(f"Relation {OSM_RELATION_ID} is not a city/municipality boundary: {tags}")

    shape, n_ways = build_polygon(relation)
    area = area_km2(shape)
    osm_timestamp = data["osm3s"]["timestamp_osm_base"]

    feature = {
        "type": "Feature",
        "properties": {
            "name": tags.get("official_name", tags["name"]),
            "psgc": tags.get("ref"),
            "source": f"OpenStreetMap relation {OSM_RELATION_ID}, version {relation['version']}",
            "source_type": "osm",
            "osm_relation_last_edited": relation["timestamp"],
            "osm_data_timestamp": osm_timestamp,
            "license": "ODbL, (c) OpenStreetMap contributors",
            "area_km2": round(area, 3),
        },
        "geometry": mapping(shape),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}) + "\n", encoding="utf-8")

    parts = list(getattr(shape, "geoms", [shape]))
    min_lon, min_lat, max_lon, max_lat = shape.bounds
    print(f"Boundary: {feature['properties']['name']} (PSGC {tags.get('ref')})")
    print(f"  OSM relation {OSM_RELATION_ID}, version {relation['version']}, last edited {relation['timestamp']}")
    print(f"  OSM data as of: {osm_timestamp}")
    print(f"  {n_ways} border ways -> {len(parts)} polygon(s), {sum(len(p.exterior.coords) for p in parts)} points")
    print(f"  Area: {area:.2f} km2")
    print(f"  Extent: lon {min_lon:.4f} to {max_lon:.4f}, lat {min_lat:.4f} to {max_lat:.4f}")
    print(f"  Wrote {output_path.relative_to(REPO_ROOT).as_posix()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Santa Rosa study boundary from OpenStreetMap.")
    parser.add_argument("--refresh", action="store_true", help="download a fresh copy even if one is saved")
    args = parser.parse_args()

    config = Config.load()
    # This script produces the placeholder boundary; the real one (PSA/NAMRIA) is built separately.
    output_path = REPO_ROOT / config.datasets["study_boundary"].placeholder

    if args.refresh or not RAW_FILE.exists():
        download()
    else:
        print(f"Using saved download {RAW_FILE.relative_to(REPO_ROOT).as_posix()} (use --refresh to download again)")
    convert(output_path)


if __name__ == "__main__":
    main()
