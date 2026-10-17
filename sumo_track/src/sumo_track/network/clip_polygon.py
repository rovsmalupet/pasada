"""Buffered clip polygon for the road network (Phase 1, step 2).

The network is clipped with the study boundary GROWN by boundary_buffer_m, not with the exact
boundary: roads and jeepney routes run across the city line, and a strict cut breaks route
continuity and leaves dead ends at the edge. The exact boundary is applied later, when origins,
destinations and agents are chosen (Phase 2); that is what defines the study area.

Reads the active study_boundary from the data manifest and boundary_buffer_m from params.yaml,
grows the boundary in metres (UTM zone 51N), checks the result, and writes two derived files:

    data/processed/santa_rosa_buffered.geojson   to check in QGIS
    data/processed/santa_rosa_buffered.poly      clip polygon for osmium / osmconvert

    python -m sumo_track.network.clip_polygon
"""

import json

from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform

from sumo_track.config import REPO_ROOT, Config
from sumo_track.network.boundary import area_km2, load_study_boundary

METRIC_CRS = "EPSG:32651"  # UTM zone 51N: metres for Santa Rosa (netconvert picks the same zone)
OUT_DIR = REPO_ROOT / "data" / "processed"
OUT_NAME = "santa_rosa_buffered"

_to_metres = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True).transform
_to_lonlat = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True).transform


def buffer_m(shape, distance_m: float):
    """Grow a WGS84 longitude/latitude (Multi)Polygon by distance_m metres."""
    return transform(_to_lonlat, transform(_to_metres, shape).buffer(distance_m))


def min_gap_m(inner, outer) -> float:
    """Smallest distance in metres between inner and the outline of outer, which contains it."""
    outer_m = transform(_to_metres, outer)
    rings = [p.exterior for p in getattr(outer_m, "geoms", [outer_m])]
    return min(ring.distance(transform(_to_metres, inner)) for ring in rings)


def poly_text(name: str, shape) -> str:
    """Osmosis .poly format, as read by osmium and osmconvert: one section per outer ring,
    '!'-prefixed sections for holes, longitude before latitude, each section closed by END."""
    lines = [name]
    for i, part in enumerate(getattr(shape, "geoms", [shape]), start=1):
        rings = [(str(i), part.exterior)]
        rings += [(f"!{i}_{j}", hole) for j, hole in enumerate(part.interiors, start=1)]
        for label, ring in rings:
            lines.append(label)
            lines += [f"    {lon:.7f}    {lat:.7f}" for lon, lat in ring.coords]
            lines.append("END")
    lines.append("END")
    return "\n".join(lines) + "\n"


def main() -> None:
    config = Config.load()
    dataset = config.datasets["study_boundary"]
    boundary, props = load_study_boundary(config)
    distance = float(config.param("boundary_buffer_m"))
    clip = buffer_m(boundary, distance)

    # the clip polygon must hold the whole boundary with (almost) the full buffer all round;
    # buffer() draws arcs as short chords, so the gap comes out a little short (997.5 m of 1000)
    gap = min_gap_m(boundary, clip)
    if not clip.covers(boundary) or gap < 0.99 * distance:
        raise SystemExit(f"Clip polygon check failed: covers boundary {clip.covers(boundary)}, "
                         f"smallest gap {gap:.1f} m for a {distance:.0f} m buffer")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    feature = {
        "type": "Feature",
        "properties": {
            "name": f"{OUT_NAME} (derived file - rebuild with python -m sumo_track.network.clip_polygon)",
            "boundary_dataset": f"study_boundary ({dataset.active}): {dataset.active_source}",
            "boundary_name": props.get("adm3_name") or props.get("name"),
            "boundary_buffer_m": distance,
            "metric_crs": METRIC_CRS,
            "area_km2": round(area_km2(clip), 3),
        },
        "geometry": mapping(clip),
    }
    geojson_path = OUT_DIR / f"{OUT_NAME}.geojson"
    poly_path = OUT_DIR / f"{OUT_NAME}.poly"
    geojson_path.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}) + "\n",
                            encoding="utf-8")
    poly_path.write_text(poly_text(OUT_NAME, clip), encoding="utf-8")

    parts = list(getattr(clip, "geoms", [clip]))
    min_lon, min_lat, max_lon, max_lat = clip.bounds
    print(f"Boundary: study_boundary ({dataset.active}) {dataset.active_source}")
    print(f"  {props.get('adm3_name') or props.get('name')}: {area_km2(boundary):.2f} km2")
    print(f"Clip polygon: boundary + {distance:.0f} m  ({METRIC_CRS})")
    print(f"  {len(parts)} polygon(s), {sum(len(p.exterior.coords) for p in parts)} points, "
          f"{area_km2(clip):.2f} km2")
    print(f"  Extent: lon {min_lon:.4f} to {max_lon:.4f}, lat {min_lat:.4f} to {max_lat:.4f}")
    print(f"  Check: covers the whole boundary; smallest gap to its edge {gap:.1f} m")
    for path in (geojson_path, poly_path):
        print(f"  Wrote {path.relative_to(REPO_ROOT).as_posix()}")


if __name__ == "__main__":
    main()
