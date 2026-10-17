"""Build the Santa Rosa road network (Phase 1, step 4).

Runs netconvert with the saved options in config/netconvert.netccfg on the OSM data the data
manifest makes active (osm_extract), keeping only edges inside the buffered clip polygon
(python -m sumo_track.network.clip_polygon). The OSM download holds whole ways, and some run
tens of km beyond the polygon; --keep-edges.in-geo-boundary cuts those off. Writes:

    data/processed/santarosa.net.xml                 the SUMO network
    data/processed/santarosa.netconvert.log          everything netconvert printed
    data/processed/santarosa.net.xml.build.json      inputs, versions, checksums, counts

    python -m sumo_track.network.build

Manual netedit fixes (config/net_patches/, step 1.6) are not applied yet; there are none.
"""

import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone

import sumolib
from shapely.geometry import shape

from sumo_track.config import REPO_ROOT, Config
from sumo_track.network.clip_polygon import OUT_DIR as CLIP_DIR, OUT_NAME as CLIP_NAME

NETCCFG = REPO_ROOT / "config" / "netconvert.netccfg"
OUT_DIR = REPO_ROOT / "data" / "processed"
NET_FILE = OUT_DIR / "santarosa.net.xml"
LOG_FILE = OUT_DIR / "santarosa.netconvert.log"
BUILD_FILE = OUT_DIR / "santarosa.net.xml.build.json"


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def warning_summary(log: str) -> Counter:
    """netconvert warnings counted by kind (ids and numbers blanked to %). netconvert prints
    only the first few of each kind, then 'Warning: N total messages of type: ...'; use N."""
    def kind(text):
        return re.sub(r"\d+(\.\d+)?", "%", re.sub(r"'[^']*'", "'%'", text.strip()))

    printed, totals = Counter(), {}
    for line in log.splitlines():
        if not line.startswith("Warning:"):
            continue
        total = re.match(r"Warning: (\d+) total messages of type: (.*)", line)
        if total:
            totals[kind(total.group(2))] = int(total.group(1))
        else:
            printed[kind(line[len("Warning:"):])] += 1
    return Counter({**printed, **totals})


def clip_polygon():
    path = CLIP_DIR / f"{CLIP_NAME}.geojson"
    if not path.is_file():
        raise SystemExit(f"{path.relative_to(REPO_ROOT).as_posix()} is missing: "
                         "run python -m sumo_track.network.clip_polygon first")
    return shape(json.loads(path.read_text(encoding="utf-8"))["features"][0]["geometry"])


def network_stats(net_path) -> dict:
    net = sumolib.net.readNet(str(net_path))
    edges = net.getEdges()   # normal road edges (no internal, crossing or walking-area edges)
    lon_lat = [net.convertXY2LonLat(x, y) for e in edges for x, y in e.getShape()]

    def km(vclass):
        return round(sum(e.getLength() for e in edges if e.allows(vclass)) / 1000, 1)

    return {
        "edges": len(edges),
        "junctions": len(net.getNodes()),
        "traffic_lights": len(net.getTrafficLights()),
        "edges_by_type": dict(Counter(e.getType() for e in edges).most_common()),
        "km_open_to": {v: km(v) for v in ("passenger", "bus", "motorcycle", "bicycle", "pedestrian")},
        "extent_lon_lat": [round(min(p[0] for p in lon_lat), 4), round(min(p[1] for p in lon_lat), 4),
                           round(max(p[0] for p in lon_lat), 4), round(max(p[1] for p in lon_lat), 4)],
    }


def main() -> None:
    config = Config.load()
    osm_path = config.dataset_path("osm_extract")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    binary = sumolib.checkBinary("netconvert")
    version = subprocess.run([binary, "--version"], capture_output=True, text=True).stdout.splitlines()[0]

    print(f"Building {NET_FILE.relative_to(REPO_ROOT).as_posix()} from "
          f"{osm_path.relative_to(REPO_ROOT).as_posix()} ({version}); this takes a minute or two")
    clip = clip_polygon()
    boundary_arg = ",".join(f"{lon:.7f},{lat:.7f}" for lon, lat in clip.exterior.coords)
    started = datetime.now(timezone.utc)
    result = subprocess.run(
        [binary, "-c", str(NETCCFG), "--osm-files", str(osm_path), "--output-file", str(NET_FILE),
         "--keep-edges.in-geo-boundary", boundary_arg],
        capture_output=True, text=True)
    log = result.stdout + result.stderr
    LOG_FILE.write_text(log, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"netconvert failed (exit {result.returncode}); see {LOG_FILE.name}")

    warnings = warning_summary(log)
    stats = network_stats(NET_FILE)
    osm_provenance = osm_path.with_name(osm_path.name + ".provenance.json")
    build = {
        "built_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "netconvert": version,
        "options_file": "config/netconvert.netccfg",
        "kept_edges_in": f"data/processed/{CLIP_NAME}.geojson ({len(clip.exterior.coords)} points)",
        "options_sha256": sha256(NETCCFG),
        "typemap_sha256": sha256(REPO_ROOT / "config" / "osm_typemap.typ.xml"),
        "osm_extract": config.datasets["osm_extract"].active_source,
        "osm_sha256": sha256(osm_path),
        "osm_data_timestamp": (json.loads(osm_provenance.read_text(encoding="utf-8"))
                               .get("osm_data_timestamp") if osm_provenance.is_file() else None),
        "net_sha256": sha256(NET_FILE),
        "warnings": sum(warnings.values()),
        "warnings_by_kind": dict(warnings.most_common()),
        **stats,
    }
    BUILD_FILE.write_text(json.dumps(build, indent=2) + "\n", encoding="utf-8")

    print(f"  {stats['edges']:,} edges, {stats['junctions']:,} junctions, "
          f"{stats['traffic_lights']} traffic lights")
    print("  km open to: " + ", ".join(f"{k} {v}" for k, v in stats["km_open_to"].items()))
    e = stats["extent_lon_lat"]
    c = [round(v, 4) for v in clip.bounds]
    print(f"  Extent: lon {e[0]} to {e[2]}, lat {e[1]} to {e[3]}  "
          f"(clip polygon: lon {c[0]} to {c[2]}, lat {c[1]} to {c[3]})")
    print(f"  {build['warnings']:,} netconvert warnings; most common:")
    for kind, n in warnings.most_common(5):
        print(f"    {n:6,}  {kind[:110]}")
    for path in (NET_FILE, LOG_FILE, BUILD_FILE):
        print(f"  Wrote {path.relative_to(REPO_ROOT).as_posix()}")


if __name__ == "__main__":
    main()
