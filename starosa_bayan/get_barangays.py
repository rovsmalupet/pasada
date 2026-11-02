"""Santa Rosa's 18 barangay boundaries (ADM4) from the official COD-AB dataset on HDX.

The source is OCHA's Philippines COD-AB (NAMRIA and PSA; CC BY-IGO), the dataset the city
boundary in ../population_extraction/ comes from. Its archive is about 0.9 GB, so this reads
only what it needs, with HTTP range requests (population_extraction's RemoteZip):
  * phl_admin4.dbf: which records are Santa Rosa's (adm3_pcode PH0403428),
  * phl_admin4.shx: where those records are in phl_admin4.shp,
  * phl_admin4.shp: streamed from its start and decompressed only until the last of them
    (about 26 MB of download).
Writes data/santa_rosa_barangays.geojson (one feature per barangay, the source's own
attributes) and data/santa_rosa_barangays.provenance.json.

    python get_barangays.py
"""

import json
import struct
import sys
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
POPULATION = HERE.parent / "population_extraction"
sys.path.insert(0, str(POPULATION))
from obtain_boundary import RemoteZip   # noqa: E402

CITY_PCODE = "PH0403428"                 # City of Santa Rosa, Laguna
OUT = HERE / "data" / "santa_rosa_barangays.geojson"


def main():
    meta = json.loads((POPULATION / "boundaries" / "cod-ab-phl_hdx_metadata.json").read_text())["result"]
    res = next(x for x in meta["resources"] if x["format"] == "SHP")
    url, size = res["url"], res["size"]

    def get(start, end):
        req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
        with urllib.request.urlopen(req, timeout=300) as r:
            if r.status != 206:
                raise RuntimeError("the server did not honour the byte range")
            return r.read()

    with zipfile.ZipFile(RemoteZip(url, size)) as z:
        info = {i.filename: i for i in z.infolist()}

    def data_start(i):
        h = get(i.header_offset, i.header_offset + 29)
        n, m = struct.unpack("<HH", h[26:30])
        return i.header_offset + 30 + n + m

    def member(name):
        i = info[name]
        s = data_start(i)
        raw = get(s, s + i.compress_size - 1)
        return zlib.decompress(raw, -15) if i.compress_type == zipfile.ZIP_DEFLATED else raw

    # which records are Santa Rosa's
    dbf = member("phl_admin4.dbf")
    nrec, hlen, rlen = struct.unpack("<IHH", dbf[4:12])
    fields, off, col = {}, 32, 1
    while dbf[off] != 0x0D:
        name, length = dbf[off:off + 11].split(b"\0")[0].decode(), dbf[off + 16]
        fields[name] = (col, length)
        col, off = col + length, off + 32
    records = {}
    for k in range(nrec):
        rec = dbf[hlen + k * rlen: hlen + (k + 1) * rlen]
        row = {f: rec[c:c + n].decode("utf-8", "replace").strip() for f, (c, n) in fields.items()}
        if row["adm3_pcode"] == CITY_PCODE:
            records[k] = {f: v for f, v in row.items() if v}
    print(f"{len(records)} barangays with adm3_pcode {CITY_PCODE} (of {nrec} in the country)")

    # where they are in the .shp, then stream it until the last one
    shx = member("phl_admin4.shx")
    where = {k: struct.unpack(">ii", shx[100 + 8 * k: 108 + 8 * k]) for k in records}   # 16-bit words
    need = max((o + n) * 2 + 8 for o, n in where.values())
    i = info["phl_admin4.shp"]
    s = data_start(i)
    req = urllib.request.Request(url, headers={"Range": f"bytes={s}-{s + i.compress_size - 1}"})
    inflate, shp, got = zlib.decompressobj(-15), bytearray(), 0
    with urllib.request.urlopen(req, timeout=600) as r:
        while len(shp) < need:
            chunk = r.read(1 << 20)
            if not chunk:
                raise RuntimeError("the .shp ended before the last record")
            got += len(chunk)
            shp += inflate.decompress(chunk)
    print(f"Downloaded {got / 1e6:.1f} MB of the compressed .shp")

    features = []
    for k, (o, n) in sorted(where.items()):
        content = bytes(shp[o * 2 + 8: o * 2 + 8 + n * 2])
        nparts, npts = struct.unpack("<ii", content[36:44])
        parts = list(struct.unpack(f"<{nparts}i", content[44:44 + 4 * nparts])) + [npts]
        xy = struct.unpack(f"<{2 * npts}d", content[44 + 4 * nparts: 44 + 4 * nparts + 16 * npts])
        points = list(zip(xy[0::2], xy[1::2]))
        rings = [points[parts[j]:parts[j + 1]] for j in range(nparts)]
        area = lambda r: sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(r, r[1:])) / 2
        outer = unary_union([Polygon(r) for r in rings if area(r) < 0])       # outer rings run clockwise
        holes = [Polygon(r) for r in rings if area(r) >= 0]
        geom = outer.difference(unary_union(holes)) if holes else outer
        features.append({"type": "Feature", "properties": records[k], "geometry": mapping(geom)})

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")
    provenance = {
        "source": "OCHA Philippines COD-AB (cod-ab-phl) on HDX, from NAMRIA and PSA",
        "license": "CC BY-IGO 3.0: attribute NAMRIA, PSA and OCHA/HDX",
        "resource": res["name"], "resource_url": url, "resource_size": size,
        "member": "phl_admin4.shp/.shx/.dbf", "selected": f"adm3_pcode == {CITY_PCODE}",
        "valid_on": sorted({f["properties"].get("valid_on") for f in features}),
        "version": sorted({f["properties"].get("version") for f in features}),
        "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "made_by": "get_barangays.py",
    }
    OUT.with_suffix(".provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT.relative_to(HERE)} ({len(features)} barangays) and its provenance")


if __name__ == "__main__":
    main()
