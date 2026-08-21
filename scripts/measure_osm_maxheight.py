from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely.geometry

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw" / "osm"
REGISTER = REPO / "data" / "interim" / "terrain" / "unrepresentative_underpass_register.csv"
OUT_JSON = RAW / "maxheight_coverage.json"

DEM_BBOX = (12.827, 77.458, 13.148, 77.787)  # south, west, north, east
DIST_BBOX = (12.658, 77.325, 13.235, 77.837)
OVERLAP_M = 25.0  # join radius for register segments

UA = {"User-Agent": "jaladhar-underpass-audit/0.1 (research)", "Accept": "*/*"}


def overpass(query: str, timeout: int = 115, tries: int = 5) -> dict:
    import urllib.parse
    import urllib.request

    url = "https://overpass-api.de/api/interpreter"
    data = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, data=data, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(6 * (i + 1))
    raise RuntimeError(f"overpass failed after {tries} tries: {last}")


def count_q(clause: str, bbox: tuple) -> str:
    s, w, n, e = bbox
    return f"[out:json][timeout:110];{clause}({s},{w},{n},{e});out count;"


def geom_q(clauses: list[str], bbox: tuple) -> str:
    s, w, n, e = bbox
    inner = "".join(f"{c}({s},{w},{n},{e});" for c in clauses)
    return f"[out:json][timeout:110];({inner});out tags geom;"


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def way_geometries(elements: list[dict]) -> gpd.GeoDataFrame:
    rows = []
    for el in elements:
        if el.get("type") != "way":
            continue
        coords = [(nd["lon"], nd["lat"]) for nd in el.get("geometry", [])]
        if len(coords) < 2:
            continue
        rows.append(
            {
                "osm_id": el["id"],
                "tags": json.dumps(el.get("tags", {})),
                "geometry": shapely.geometry.LineString(coords),
            }
        )
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    if not gdf.empty:
        gdf["length_m"] = gdf.to_crs("EPSG:32643").geometry.length
    return gdf


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "jaladhar.measure-osm-maxheight.v1",
        "git_sha": git_sha(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "running",
        "aois": {
            "dem_bbox": DEM_BBOX,
            "district_bbox": DIST_BBOX,
            "note": (
                "bboxes are (south, west, north, east); district bbox exceeds the "
                "district polygon and is used only for counts"
            ),
        },
    }
    OUT_JSON.write_text(json.dumps(manifest, indent=2))

    counts = {}
    for label, clause, bbox in [
        ("maxheight_ways_dem", 'way["maxheight"]', DEM_BBOX),
        ("maxheight_ways_dist", 'way["maxheight"]', DIST_BBOX),
        ("maxheight_phys_ways_dem", 'way["maxheight:physical"]', DEM_BBOX),
        ("maxheight_phys_ways_dist", 'way["maxheight:physical"]', DIST_BBOX),
        ("maxheight_signed_ways_dem", 'way["maxheight:signed"]', DEM_BBOX),
        ("maxheight_signed_ways_dist", 'way["maxheight:signed"]', DIST_BBOX),
        ("maxheight_nodes_dem", 'node["maxheight"]', DEM_BBOX),
        ("maxheight_phys_nodes_dem", 'node["maxheight:physical"]', DEM_BBOX),
        ("maxheight_signed_nodes_dem", 'node["maxheight:signed"]', DEM_BBOX),
        ("maxheight_nodes_dist", 'node["maxheight"]', DIST_BBOX),
        ("highway_ways_dem", 'way["highway"]', DEM_BBOX),
        ("highway_ways_dist", 'way["highway"]', DIST_BBOX),
        ("tunnel_highway_dem", 'way["highway"]["tunnel"]', DEM_BBOX),
        ("tunnel_highway_dist", 'way["highway"]["tunnel"]', DIST_BBOX),
        ("layer_neg_highway_dem", 'way["highway"]["layer"="-1"]', DEM_BBOX),
        ("bridge_highway_dem", 'way["highway"]["bridge"]', DEM_BBOX),
        ("covered_highway_dem", 'way["highway"]["covered"]', DEM_BBOX),
    ]:
        doc = overpass(count_q(clause, bbox))
        c = {}
        for el in doc.get("elements", []):
            if el.get("type") == "count":
                c = {k: int(v) for k, v in el["tags"].items()}
        counts[label] = c
        print(f"{label:28s} {c}")
        time.sleep(2)

    raw_maxheight_ways = overpass(
        geom_q(
            ['way["maxheight"]', 'way["maxheight:physical"]', 'way["maxheight:signed"]'],
            DEM_BBOX,
        )
    )
    raw_maxheight_nodes = overpass(
        geom_q(
            [
                'node["maxheight"]',
                'node["maxheight:physical"]',
                'node["maxheight:signed"]',
            ],
            DEM_BBOX,
        )
    )
    raw_tunnel = overpass(geom_q(['way["highway"]["tunnel"]'], DEM_BBOX))

    (RAW / "overpass_maxheight_ways.json").write_text(json.dumps(raw_maxheight_ways))
    (RAW / "overpass_maxheight_nodes.json").write_text(json.dumps(raw_maxheight_nodes))
    (RAW / "overpass_tunnel_ways.json").write_text(json.dumps(raw_tunnel))
    print("raw responses saved")

    mh_ways = way_geometries(raw_maxheight_ways["elements"])
    tunnel_ways = way_geometries(raw_tunnel["elements"])

    reg = pd.read_csv(REGISTER)
    reg_gdf = gpd.GeoDataFrame(
        reg, geometry=gpd.points_from_xy(reg["lon"], reg["lat"]), crs="EPSG:4326"
    )

    def join_count(gdf_a: gpd.GeoDataFrame, gdf_b: gpd.GeoDataFrame) -> int:
        if gdf_a.empty or gdf_b.empty:
            return 0
        a = gdf_a.to_crs("EPSG:32643").geometry
        b = gdf_b.to_crs("EPSG:32643").geometry
        hits = a.buffer(OVERLAP_M).intersects(b)
        return int(hits.sum())

    register_hits = join_count(reg_gdf, mh_ways)
    tunnel_hits = join_count(tunnel_ways, mh_ways)

    tag_keys = ["maxheight", "maxheight:physical", "maxheight:signed"]
    tag_series = mh_ways["tags"].apply(json.loads)
    values = []
    for t in tag_series:
        for k in tag_keys:
            v = t.get(k)
            if v:
                values.append(v)
    units = {"metres": 0, "feet": 0, "numeric_bare": 0, "other": 0}
    for v in values:
        vs = str(v).strip().lower()
        if vs.endswith(" m"):
            units["metres"] += 1
        elif vs.endswith("'"):
            units["feet"] += 1
        elif vs.replace(".", "", 1).isdigit():
            units["numeric_bare"] += 1
        else:
            units["other"] += 1

    n_highway_dem = counts["highway_ways_dem"]["ways"]
    n_highway_dist = counts["highway_ways_dist"]["ways"]
    n_mh_dem = (
        counts["maxheight_ways_dem"]["ways"]
        + counts["maxheight_phys_ways_dem"]["ways"]
        + counts["maxheight_signed_ways_dem"]["ways"]
    )

    stats = {
        "dem_bbox": {
            "highway_ways": n_highway_dem,
            "maxheight_any_ways": n_mh_dem,
            "maxheight_ways": counts["maxheight_ways_dem"]["ways"],
            "maxheight_physical_ways": counts["maxheight_phys_ways_dem"]["ways"],
            "maxheight_signed_ways": counts["maxheight_signed_ways_dem"]["ways"],
            "maxheight_any_nodes": counts["maxheight_nodes_dem"]["nodes"],
            "fraction_of_highway_ways_with_maxheight": round(n_mh_dem / n_highway_dem, 6),
            "tunnel_highway_ways": counts["tunnel_highway_dem"]["ways"],
            "layer_neg1_highway_ways": counts["layer_neg_highway_dem"]["ways"],
            "bridge_highway_ways": counts["bridge_highway_dem"]["ways"],
            "covered_highway_ways": counts["covered_highway_dem"]["ways"],
        },
        "district_bbox": {
            "highway_ways": n_highway_dist,
            "maxheight_any_ways": (
                counts["maxheight_ways_dist"]["ways"]
                + counts["maxheight_phys_ways_dist"]["ways"]
                + counts["maxheight_signed_ways_dist"]["ways"]
            ),
            "maxheight_ways": counts["maxheight_ways_dist"]["ways"],
            "maxheight_physical_ways": counts["maxheight_phys_ways_dist"]["ways"],
            "maxheight_signed_ways": counts["maxheight_signed_ways_dist"]["ways"],
            "maxheight_any_nodes": counts["maxheight_nodes_dist"]["nodes"],
            "fraction_of_highway_ways_with_maxheight": round(
                (
                    counts["maxheight_ways_dist"]["ways"]
                    + counts["maxheight_phys_ways_dist"]["ways"]
                    + counts["maxheight_signed_ways_dist"]["ways"]
                )
                / n_highway_dist,
                6,
            ),
            "tunnel_highway_ways": counts["tunnel_highway_dist"]["ways"],
        },
        "underpass_register": {
            "n_segments": len(reg),
            "n_with_maxheight_way_within_25m": register_hits,
            "fraction": round(register_hits / len(reg), 6),
        },
        "tunnel_crossing": {
            "n_tunnel_ways_dem_bbox": len(tunnel_ways),
            "n_tunnel_ways_with_maxheight_within_25m": tunnel_hits,
        },
        "maxheight_values": {"n_values": len(values), "units": units},
        "join_radius_m": OVERLAP_M,
    }
    print(json.dumps(stats, indent=2))

    manifest.update(
        {
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "completed",
            "counts": counts,
            "stats": stats,
        }
    )
    OUT_JSON.write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {OUT_JSON}")


if __name__ == "__main__":
    main()