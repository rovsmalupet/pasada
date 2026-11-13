"""The group's video traffic counts, and the simulation's counts at the same spot (manuscript
Sec. 4.5: the GEH statistic against the observed counts).

Each video (data/traffic_counts/<folder>/, from the group's counting pipeline) counts every vehicle
crossing a line across the road, with its class (car, bus, motorcycle or truck) and its direction
(toward or away from the camera). settings.COUNT_SITES says where each camera stood, on which road,
which way "away" is and which videos were taken there. A video's clock time is in its file name
(VID_<date>_<hhmmss>), not checked against a clock.

The simulation counts at the same spot with an instant induction loop on every lane of the road
there, both ways (write_loops). compare() then sets the simulated and video counts side by side for
each video's 30 minutes, per direction and class, in vehicles per hour, with the GEH statistic
(GEH < 5 is a good match).

Classes: the camera's jeepney labels are unchecked model guesses, and it counts a jeepney as a car,
bus or truck. So the simulation's jeepneys, buses and trucks are set against the camera's buses and
trucks together ("large"). Bicycles are not counted.

    python counts.py            # the counts of each video, per direction and class
    python counts.py --check    # background traffic only (no commuters): SUMO around each video's
                                #   30 minutes, then the comparison (a few minutes)
"""

import argparse
import csv
import json
import math
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import sumolib

import settings as S

CLASSES = ["car", "motorcycle", "large"]
CLASS_OF = {"car": "car", "motorcycle": "motorcycle", "bus": "large", "truck": "large", "jeepney": "large"}
BEARING = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}
OPPOSITE = {d: next(o for o, b in BEARING.items() if b == (a + 180) % 360) for d, a in BEARING.items()}
CHECK_WARMUP_S = 3600              # --check: SUMO starts this long before each video, on empty roads


def videos():
    """Every video of COUNT_SITES: its site, folder, start (s after midnight), length (s), and its
    vehicles per hour {direction: {type: rate}} per camera type (car, motorcycle, bus, truck) and
    {direction: {class: rate}} per class compared, from its vehicle_events.csv."""
    out = []
    for site, s in S.COUNT_SITES.items():
        away, toward = s["away"], OPPOSITE[s["away"]]
        for folder in s["videos"]:
            path = S.TRAFFIC_COUNTS / folder
            video = json.loads((path / "metadata/run_metadata.json").read_text(encoding="utf-8"))["video"]
            hh, mm, ss = re.search(r"_\d{8}_(\d\d)(\d\d)(\d\d)", video["filename"]).groups()
            length = float(video["duration_seconds"])
            n = defaultdict(Counter)
            with open(path / "vehicle_events.csv", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    n[{"away_from_camera": away, "toward_camera": toward}[row["direction"]]][row["vehicle_type"]] += 1
            types = {way: {t: n[way][t] * 3600 / length for t in ("car", "motorcycle", "bus", "truck")}
                     for way in (away, toward)}
            out.append({"site": site, "folder": folder, "start_s": int(hh) * 3600 + int(mm) * 60 + int(ss),
                        "length_s": length, "types": types,
                        "flows": {way: {c: sum(r for t, r in rates.items() if CLASS_OF[t] == c) for c in CLASSES}
                                  for way, rates in types.items()}})
    return out


def site_edges(net):
    """{site: {direction: edge}}: the site's road at its spot, the edge each way."""
    out = {}
    for site, s in S.COUNT_SITES.items():
        x, y = net.convertLonLat2XY(*s["lonlat"])
        near = [(e, d) for e, d in net.getNeighboringEdges(x, y, 30) if e.getName() == s["road"]]
        if not near:
            raise SystemExit(f"COUNT_SITES['{site}']: no {s['road']} within 30 m of {s['lonlat']}")
        out[site] = {}
        for way in (s["away"], OPPOSITE[s["away"]]):
            def off(edge):                   # degrees between the edge's heading and `way`
                (x0, y0), (x1, y1) = edge.getShape()[0], edge.getShape()[-1]
                heading = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360
                return abs((heading - BEARING[way] + 180) % 360 - 180)
            edge, _ = min(((e, d) for e, d in near if off(e) < 60), key=lambda ed: ed[1], default=(None, 0))
            if edge is None:
                raise SystemExit(f"COUNT_SITES['{site}']: {s['road']} does not run {way} there")
            out[site][way] = edge
    return out


def write_loops(net, path, output):
    """SUMO additional file `path`: an instant induction loop across every lane vehicles may use,
    halfway along each site edge, writing every vehicle passing to `output`."""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           "<!-- Generated by counts.py: where the simulation counts traffic, as the group's videos did. -->",
           "<additional>"]
    for site, ways in site_edges(net).items():
        for way, edge in ways.items():
            for lane in edge.getLanes():
                if any(lane.allows(c) for c in ("passenger", "motorcycle", "bus", "truck")):
                    xml.append(f'    <instantInductionLoop id="{site}|{way}|{lane.getIndex()}" lane="{lane.getID()}" '
                               f'pos="{edge.getLength() / 2:.1f}" file="{Path(output).as_posix()}"/>')
    xml.append("</additional>")
    Path(path).write_text("\n".join(xml) + "\n", encoding="utf-8")


def tally(output):
    """{(site, direction): [(time, class)]}: each vehicle passing a loop of `output`, once."""
    seen, out = set(), defaultdict(list)
    for _, el in ET.iterparse(output):
        if el.tag == "instantOut" and el.get("state") == "enter":
            site, way, _ = el.get("id").split("|")
            if el.get("type") in CLASS_OF and (el.get("vehID"), site, way) not in seen:
                seen.add((el.get("vehID"), site, way))
                out[(site, way)].append((float(el.get("time")), CLASS_OF[el.get("type")]))
        el.clear()
    return out


def geh(m, c):
    """GEH of hourly flows m (simulated) and c (counted)."""
    return math.sqrt(2 * (m - c) ** 2 / (m + c)) if m + c else 0.0


def compare(output, until, folders=None):
    """The simulated counts of `output` against each video the run covered (until: the time it
    reached; folders: only these videos), printed and returned for summary.json."""
    passed = tally(output)
    print("\n================ traffic counts: simulated vs video (vehicles per hour) ================")
    rows, cells, good = [], 0, 0
    for v in videos():
        if folders is not None and v["folder"] not in folders:
            continue
        end = v["start_s"] + v["length_s"]
        if until < end:
            print(f"  {v['site']} {clock(v['start_s'])}: the run stopped before this video's 30 minutes ended")
            continue
        for way, flows in v["flows"].items():
            sim = Counter(c for t, c in passed[(v["site"], way)] if v["start_s"] <= t < end)
            row = {"site": v["site"], "video": v["folder"], "from": clock(v["start_s"]), "to": clock(end),
                   "direction": way, "simulated": {}, "video_count": {}, "geh": {}}
            line = f"  {v['site']} {clock(v['start_s'])}-{clock(end)} {way:2s}"
            for c in CLASSES + ["all"]:
                m = (sum(sim.values()) if c == "all" else sim[c]) * 3600 / v["length_s"]
                o = sum(flows.values()) if c == "all" else flows[c]
                g = geh(m, o)
                row["simulated"][c], row["video_count"][c], row["geh"][c] = round(m), round(o), round(g, 1)
                line += f"  {c} {m:5.0f} vs {o:5.0f} (GEH {g:4.1f})"
                if c != "all":
                    cells, good = cells + 1, good + (g < 5)
            print(line)
            rows.append(row)
    if cells:
        print(f"  GEH < 5 in {good} of {cells} direction-class cells ({100 * good / cells:.0f}%; "
              "the manuscript's target: 85%)")
    return {"rows": rows, "cells": cells, "geh_below_5": good}


def clock(seconds):
    s = int(round(seconds))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}"


def check():
    """Background traffic only: SUMO from CHECK_WARMUP_S before each video to its end, the jeepneys
    and background of generated/, then compare()."""
    from make_background import trips_file           # not at the top: make_background imports this file
    routes = [S.GENERATED / "jeepney.rou.xml", *(trips_file(v) for v in S.BACKGROUND_TYPES)]
    missing = [p.name for p in routes if not p.is_file()]
    if missing:
        raise SystemExit(f"Missing generated/{', generated/'.join(missing)}: run python build.py first")
    net = sumolib.net.readNet(str(S.NET_FILE))
    work = Path(tempfile.mkdtemp(prefix="counts_"))
    runs = []
    for i, v in enumerate(videos()):              # one SUMO per video, all at once
        output = work / f"loops_{i}.xml"
        write_loops(net, work / f"loops_{i}.add.xml", output)
        begin, end = max(0, v["start_s"] - CHECK_WARMUP_S), math.ceil(v["start_s"] + v["length_s"])
        cmd = [sumolib.checkBinary("sumo"), "-n", str(S.NET_FILE),
               "-a", f"{S.GENERATED / 'vtypes.add.xml'},{work / f'loops_{i}.add.xml'}",
               "-r", ",".join(str(r) for r in routes), "-b", str(begin), "-e", str(end), "--step-length", "1",
               "--lateral-resolution", str(S.LATERAL_RESOLUTION), "--time-to-teleport", str(S.TIME_TO_TELEPORT),
               "--ignore-route-errors", "true",
               "--collision.action", "warn", "--collision.check-junctions", "true", "--seed", str(S.SEED),
               "--no-step-log", "true", "--no-warnings", "true", "--duration-log.statistics", "true"]
        print(f"SUMO {clock(begin)}-{clock(end)} for {v['site']} {clock(v['start_s'])} ...", flush=True)
        runs.append((subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True), output, v))
    done = []
    for proc, output, v in runs:
        log = proc.communicate()[0]
        stats = [l.strip() for l in log.splitlines() if re.match(r"\s*(Inserted|Running|Waiting|Teleports|Duration)", l)]
        print(f"  {clock(v['start_s'])}: " + (" | ".join(stats) if proc.returncode == 0 else
                                             f"SUMO failed (exit {proc.returncode}): no counts"))
        if proc.returncode == 0:
            done.append((output, v))
    # one comparison over every run's loops: each video's window lies in its own run
    merged = work / "loops.xml"
    with open(merged, "w", encoding="utf-8") as f:
        f.write("<instantE1>\n")
        for output, _ in done:
            for _, el in ET.iterparse(output):
                if el.tag == "instantOut":
                    f.write(ET.tostring(el, encoding="unicode").strip() + "\n")
                el.clear()
        f.write("</instantE1>\n")
    compare(merged, S.END_S, {v["folder"] for _, v in done})
    print(f"(SUMO's files: {work})")


def main():
    parser = argparse.ArgumentParser(description="The group's traffic counts, and the simulation's against them.")
    parser.add_argument("--check", action="store_true", help="background only: SUMO around each video, then compare")
    if parser.parse_args().check:
        check()
        return
    for v in videos():
        print(f"{v['site']}, {v['folder']}: {clock(v['start_s'])}-{clock(v['start_s'] + v['length_s'])}")
        for way, flows in v["flows"].items():
            print(f"  {way:2s} " + "  ".join(f"{c} {flows[c]:5.0f}/h" for c in CLASSES)
                  + f"  all {sum(flows.values()):5.0f}/h")


if __name__ == "__main__":
    main()
