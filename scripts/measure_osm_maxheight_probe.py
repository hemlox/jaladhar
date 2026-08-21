"""Probe Overpass API response sizes for the Bengaluru maxheight measurement.

Counts only — no full fetch. Decides how heavy the subsequent full-data
queries can be. Logged to data/raw/osm/probe_counts.json.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "raw" / "osm" / "probe_counts.json"

DEM_BBOX = (12.827, 77.458, 13.148, 77.787)  # south, west, north, east
DIST_BBOX = (12.658, 77.325, 13.235, 77.837)  # Bengaluru Urban district


def probe(label: str, query: str, timeout: int = 110) -> dict:
    url = "https://overpass-api.de/api/interpreter"
    data = urllib.parse.urlencode({"data": query}).encode()
    t0 = time.time()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "jaladhar-underpass-audit/0.1 (research; contact: repo)",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except Exception as e:  # noqa: BLE001
        return {"label": label, "error": str(e), "wall_s": round(time.time() - t0, 1)}
    try:
        doc = json.loads(raw)
        counts = {}
        for el in doc.get("elements", []):
            if el.get("type") == "count":
                counts = el["tags"]
        return {"label": label, "counts": counts, "wall_s": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        return {
            "label": label,
            "error": str(e),
            "wall_s": round(time.time() - t0, 1),
            "raw_head": raw[:200].decode(errors="replace"),
        }


def bbox_q(clause: str, bbox: tuple) -> str:
    s, w, n, e = bbox
    return f"[out:json][timeout:105];{clause}({s},{w},{n},{e});out count;"


QUERIES = {
    "maxheight_ways_dem": bbox_q('way["maxheight"]', DEM_BBOX),
    "maxheight_phys_ways_dem": bbox_q('way["maxheight:physical"]', DEM_BBOX),
    "maxheight_signed_ways_dem": bbox_q('way["maxheight:signed"]', DEM_BBOX),
    "maxheight_ways_dist": bbox_q('way["maxheight"]', DIST_BBOX),
    "maxheight_phys_ways_dist": bbox_q('way["maxheight:physical"]', DIST_BBOX),
    "maxheight_signed_ways_dist": bbox_q('way["maxheight:signed"]', DIST_BBOX),
    "maxheight_nodes_dem": bbox_q('node["maxheight"]', DEM_BBOX),
    "maxheight_relations_dem": bbox_q('relation["maxheight"]', DEM_BBOX),
    "highway_ways_dem": bbox_q('way["highway"]', DEM_BBOX),
    "highway_ways_dist": bbox_q('way["highway"]', DIST_BBOX),
    "tunnel_highway_dem": bbox_q('way["highway"]["tunnel"]', DEM_BBOX),
    "tunnel_highway_dist": bbox_q('way["highway"]["tunnel"]', DIST_BBOX),
    "covered_highway_dem": bbox_q('way["highway"]["covered"]', DEM_BBOX),
    "layer_neg_highway_dem": bbox_q('way["highway"]["layer"="-1"]', DEM_BBOX),
    "bridge_highway_dem": bbox_q('way["highway"]["bridge"]', DEM_BBOX),
}

results = []
for label, query in QUERIES.items():
    res = probe(label, query)
    results.append(res)
    print(f"{label:32s} {res.get('counts', res.get('error', '?'))}  ({res.get('wall_s')}s)")
    time.sleep(2)

OUT.write_text(json.dumps(results, indent=2))
print(f"\nwrote {OUT}")