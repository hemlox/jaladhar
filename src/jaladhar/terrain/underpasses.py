"""Extraction and DEM profile diagnostics for underpass, tunnel, and covered segments.

CLAUDE.md Non-negotiable 1 & 5:
- Identifies locations where the DEM is structurally unrepresentative.
- Measures the DEM profile behavior along the segment (sag, crest, runthrough).
- STOPS at the boundary: NO invented depths, synthetic inverts, or fabricated sags.

Clearance requery (`requery-clearance` command, goal Part 1):
- Fetches maxheight / maxheight:physical / maxheight:signed tags for the
  osm_ids already in the register, plus the bridge structure above each
  segment (bridge/layer/width tags of the nearest bridge=yes way).
- Writes a NEW csv (`underpass_register_clearance.csv`); never touches
  `unrepresentative_underpass_register.csv` (that file's value is that it
  contains zero invented depths).
- Reports coverage honestly: how many of the register's osm_ids carry a
  usable clearance tag.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
import pyproj
import rasterio
import requests
import typer
from shapely.geometry import LineString, Point, box

from jaladhar.terrain.grid import load_config

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

# NOTE: overpass.osm.ch is Switzerland-only (verified: 0 elements for
# Bengaluru way ids) — never put it before a global mirror.
OVERAPSS_ENDPOINTS = [
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
UA_HEADER = {"User-Agent": "jaladhar-underpass-clearance/1.0 (SIH 2026; repo owner)"}

CLEARANCE_TAG_KEYS = ["maxheight", "maxheight:physical", "maxheight:signed"]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def parse_height_tag(value: str) -> float | None:
    """Parse an OSM height tag to metres. Handles '3.5', '3.5 m', '3.5m', '11\'6"'.

    Returns None when the value is not a parseable linear dimension
    (e.g. 'default', 'below_default', 'no_sign', '5.5 m (truck)', '~3.5').
    """
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    # OSM documents maxheight values as either a plain number (metres),
    # "x m", or feet-inches like 11'6" or 11'6\".
    ftin = re.match(r"^\s*(\d+)\s*['′]\s*(\d+)?\s*(?:''|\"|″|in)?\s*$", v)
    if ftin:
        feet = int(ftin.group(1))
        inches = int(ftin.group(2) or 0)
        return feet * 0.3048 + inches * 0.0254
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(?:m|meters?|metres?)?\s*$", v)
    if m:
        return float(m.group(1))
    return None


def _overpass_post(q: str, timeout_s: int, max_retries: int = 6) -> dict[str, Any]:
    """POST an Overpass query with retry/backoff on 429/504 and endpoint fallback."""
    import time as _time

    last_err: Exception | None = None
    for attempt in range(max_retries):
        endpoint = OVERAPSS_ENDPOINTS[attempt % len(OVERAPSS_ENDPOINTS)]
        try:
            r = requests.post(endpoint, data={"data": q}, timeout=timeout_s + 60, headers=UA_HEADER)
            if r.status_code in (429, 504):
                raise requests.HTTPError(f"{r.status_code}", response=r)
            r.raise_for_status()
            return r.json()
        except (requests.HTTPError, requests.ConnectionError) as e:
            last_err = e
            wait = 10.0 * (2**attempt)
            _time.sleep(wait)
    raise RuntimeError(f"Overpass query failed after {max_retries} retries: {last_err}")


def fetch_osm_tags_by_id(
    osm_ids: list[str], timeout_s: int = 240, batch_size: int = 400
) -> dict[str, dict[str, str]]:
    """Fetch full tag sets for specific way IDs from Overpass, in batches."""
    results: dict[str, dict[str, str]] = {}
    for i in range(0, len(osm_ids), batch_size):
        batch = osm_ids[i : i + batch_size]
        q = f"[out:json][timeout:{timeout_s}];way(id:{','.join(batch)});out tags;"
        payload = _overpass_post(q, timeout_s)
        for el in payload.get("elements", []):
            results[str(el["id"])] = el.get("tags", {})
        typer.echo(
            f"  batch {i // batch_size + 1}/"
            f"{(len(osm_ids) + batch_size - 1) // batch_size}: {len(results)} ids so far"
        )
    return results


def fetch_bridge_ways(
    query_box_wgs84: Any, union_wgs84: Any, timeout_s: int = 600
) -> gpd.GeoDataFrame:
    """Fetch all bridge=yes ways over the domain (the structures ABOVE underpasses)."""
    q = (
        f"[out:json][timeout:{timeout_s}];"
        f"way['bridge']({query_box_wgs84.bounds[1]},{query_box_wgs84.bounds[0]},"
        f"{query_box_wgs84.bounds[3]},{query_box_wgs84.bounds[2]});out tags geom;"
    )
    payload = _overpass_post(q, timeout_s)
    elements = payload.get("elements", [])
    rows = []
    for el in elements:
        if "geometry" not in el:
            continue
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if len(coords) < 2:
            continue
        rows.append(
            {"osm_id": str(el["id"]), "tags": el.get("tags", {}), "geometry": LineString(coords)}
        )
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    gdf = gpd.clip(gdf, union_wgs84)
    return gdf


@app.command()
def requery_clearance(
    config: Path = typer.Option(REPO / "configs/domain_bengaluru.yaml", help="Domain config"),
    out_dir: Path = typer.Option(
        REPO / "data/interim/terrain", help="Output directory (new files only)"
    ),
) -> None:
    """Re-query OSM for clearance tags on the register's osm_ids; write a NEW csv."""
    t0 = time.perf_counter()
    register_csv = out_dir / "unrepresentative_underpass_register.csv"
    if not register_csv.exists():
        raise FileNotFoundError(f"register not found: {register_csv}")

    register = pd.read_csv(register_csv, low_memory=False)
    osm_ids = register["osm_id"].astype(str).unique().tolist()
    typer.echo(f"Register: {len(register)} rows, {len(osm_ids)} unique osm_ids")

    manifest = {
        "stage": "underpass_clearance_requery",
        "status": "running",
        "git_sha": git_sha(),
        "start_time_iso": datetime.now(UTC).isoformat(),
        "n_register_rows": int(len(register)),
        "n_unique_osm_ids": len(osm_ids),
    }
    runs_dir = REPO / "runs" / "underpass_clearance"
    runs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = runs_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        tags = fetch_osm_tags_by_id(osm_ids)
        typer.echo(f"Overpass returned tags for {len(tags)} of {len(osm_ids)} ids")

        cfg = load_config(config)
        boundary_path = REPO / cfg["boundary"]["path"]
        gdf_boundary = gpd.read_file(boundary_path)
        union_wgs84 = (
            gpd.GeoDataFrame(geometry=[gdf_boundary.union_all()], crs=gdf_boundary.crs)
            .to_crs("EPSG:4326")
            .geometry.iloc[0]
        )
        query_box = box(*union_wgs84.bounds)

        typer.echo("Fetching bridge=yes ways (structures above underpasses)...")
        bridges = fetch_bridge_ways(query_box, union_wgs84)
        bridges_proj = bridges.to_crs(cfg["crs"])
        typer.echo(f"Bridges fetched: {len(bridges_proj)}")
        bridges_gpkg = out_dir / "underpass_bridges.gpkg"
        if len(bridges_proj):
            bridges_proj.to_file(bridges_gpkg, driver="GPKG")
            typer.echo(f"Wrote {bridges_gpkg}")
        else:
            typer.echo("WARNING: zero bridges fetched; continuing without bridge overlay")

        reg_gdf = gpd.read_file(out_dir / "unrepresentative_underpass_register.gpkg")

        # nearest bridge per register segment via spatial join (distance
        # matrix is reg(2534) x bridges(2545) = 6.4M pairwise ops; element-wise
        # GeoSeries.distance would silently align by index and be wrong)
        if len(bridges_proj):
            sj = gpd.sjoin_nearest(
                reg_gdf[["osm_id", "geometry"]],
                bridges_proj[["tags", "geometry"]],
                how="left",
                max_distance=5000,
                distance_col="bridge_dist_m",
            )
            sj = sj.sort_values("bridge_dist_m").drop_duplicates("osm_id", keep="first")
            nb = sj.set_index("osm_id")[["tags", "bridge_dist_m"]]
        else:
            nb = None

        rows = []
        for _, row in register.iterrows():
            osm_id = str(row["osm_id"])
            tg = tags.get(osm_id, {})
            clearance_values = {k: tg.get(k) for k in CLEARANCE_TAG_KEYS}
            parsed = {k: parse_height_tag(v) for k, v in clearance_values.items()}
            usable = {k: v for k, v in parsed.items() if v is not None and v > 0}
            primary_key = next((k for k in CLEARANCE_TAG_KEYS if k in usable), None)
            primary_value = usable.get(primary_key) if primary_key else None

            if nb is not None and osm_id in nb.index:
                nb_tags = nb.loc[osm_id]["tags"] or {}
                nb_dist = float(nb.loc[osm_id]["bridge_dist_m"])
                nb_osm = str(nb_tags.get("id", ""))
            else:
                nb_tags, nb_dist, nb_osm = {}, np.nan, ""

            rows.append(
                {
                    "osm_id": osm_id,
                    "name": row.get("name", ""),
                    "highway": row.get("highway", ""),
                    "maxheight": clearance_values["maxheight"],
                    "maxheight_physical": clearance_values["maxheight:physical"],
                    "maxheight_signed": clearance_values["maxheight:signed"],
                    "clearance_m_parsed": round(primary_value, 3) if primary_value else np.nan,
                    "clearance_tag_source": primary_key if primary_key else "",
                    "clearance_parse_status": (
                        "OK"
                        if primary_key
                        else (
                            "MISSING_TAG" if not any(clearance_values.values()) else "UNPARSEABLE"
                        )
                    ),
                    "nearest_bridge_osm_id": nb_osm,
                    "nearest_bridge_dist_m": (
                        round(nb_dist, 1) if not np.isnan(nb_dist) else np.nan
                    ),
                    "bridge_tag": nb_tags.get("bridge", ""),
                    "bridge_layer": nb_tags.get("layer", ""),
                    "bridge_width_m": nb_tags.get("width", ""),
                    "bridge_maxheight": nb_tags.get("maxheight", ""),
                    "structure_max_z_m": row.get("z_max_m", np.nan),
                }
            )

        out_df = pd.DataFrame(rows)
        out_csv = out_dir / "underpass_register_clearance.csv"
        out_df.to_csv(out_csv, index=False)

        n_usable = int(out_df["clearance_m_parsed"].notna().sum())
        wall_clock = time.perf_counter() - t0
        manifest.update(
            {
                "status": "completed",
                "end_time_iso": datetime.now(UTC).isoformat(),
                "wall_clock_sec": round(wall_clock, 2),
                "overpass_tags_returned": len(tags),
                "n_bridges_fetched": int(len(bridges_proj)),
                "n_with_usable_clearance": n_usable,
                "n_with_usable_clearance_pct": round(100.0 * n_usable / len(osm_ids), 2),
                "clearance_status_breakdown": (
                    out_df["clearance_parse_status"].value_counts().to_dict()
                ),
                "output_csv": str(out_csv.relative_to(REPO)),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))

        typer.echo(
            f"Clearance coverage: {n_usable} of {len(osm_ids)} unique ids "
            f"({100.0 * n_usable / len(osm_ids):.1f}%)"
        )
        typer.echo(f"Wrote {out_csv}")
    except Exception:
        manifest["status"] = "failed"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise


@app.command()
def fetch_bridges(
    config: Path = typer.Option(REPO / "configs/domain_bengaluru.yaml", help="Domain config"),
    out_dir: Path = typer.Option(
        REPO / "data/interim/terrain", help="Output directory (new files only)"
    ),
) -> None:
    """Fetch bridge=yes ways over the domain; write NEW `underpass_bridges.gpkg`."""
    t0 = time.perf_counter()
    cfg = load_config(config)
    boundary_path = REPO / cfg["boundary"]["path"]
    gdf_boundary = gpd.read_file(boundary_path)
    union_wgs84 = (
        gpd.GeoDataFrame(geometry=[gdf_boundary.union_all()], crs=gdf_boundary.crs)
        .to_crs("EPSG:4326")
        .geometry.iloc[0]
    )
    query_box = box(*union_wgs84.bounds)

    manifest = {
        "stage": "underpass_bridges_fetch",
        "status": "running",
        "git_sha": git_sha(),
        "start_time_iso": datetime.now(UTC).isoformat(),
    }
    runs_dir = REPO / "runs" / "underpass_clearance"
    runs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = runs_dir / "bridge_fetch_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        bridges = fetch_bridge_ways(query_box, union_wgs84)
        bridges_proj = bridges.to_crs(cfg["crs"])
        out_gpkg = out_dir / "underpass_bridges.gpkg"
        bridges_proj.to_file(out_gpkg, driver="GPKG")
        wall_clock = time.perf_counter() - t0
        manifest.update(
            {
                "status": "completed",
                "end_time_iso": datetime.now(UTC).isoformat(),
                "wall_clock_sec": round(wall_clock, 2),
                "n_bridges_fetched": int(len(bridges_proj)),
                "crs": str(cfg["crs"]),
                "output_gpkg": str(out_gpkg.relative_to(REPO)),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))
        typer.echo(f"Bridges fetched: {len(bridges_proj)} -> {out_gpkg}")
    except Exception:
        manifest["status"] = "failed"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise


def fetch_underpass_segments(
    query_box_wgs84: Any, union_wgs84: Any, timeout_s: int = 180
) -> gpd.GeoDataFrame:
    """Fetch road and transport features with tunnel, layer, covered, or culvert tags from OSM."""
    ox.settings.requests_timeout = timeout_s
    ox.settings.log_console = False

    queries = [
        ("tunnel", {"tunnel": True}),
        ("negative_layer", {"layer": ["-1", "-2", "-3", "-4", "-5"]}),
        ("covered", {"covered": "yes"}),
        ("culvert", {"man_made": "culvert", "waterway": "culvert", "culvert": True}),
    ]

    results = []
    keep_cols = [
        "source_query_tag",
        "highway",
        "name",
        "tunnel",
        "layer",
        "covered",
        "culvert",
        "waterway",
        "geometry",
    ]

    for tag_name, tag_dict in queries:
        try:
            gdf = ox.features_from_polygon(query_box_wgs84, tags=tag_dict)
            if not gdf.empty:
                gdf = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])].copy()
                gdf["source_query_tag"] = tag_name
                results.append(gdf)
        except Exception as e:
            typer.echo(f"Warning: query for {tag_name} failed: {e}")

    if not results:
        raise RuntimeError("No features fetched from OSM Overpass")

    combined_list = []
    for gdf in results:
        gdf = gdf.reset_index()
        for c in keep_cols:
            if c in gdf.columns and c != "geometry":
                gdf[c] = gdf[c].astype(str)
            elif c not in gdf.columns and c != "geometry":
                gdf[c] = ""
        id_col = (
            "osmid" if "osmid" in gdf.columns else ("id" if "id" in gdf.columns else "element_id")
        )
        if id_col in gdf.columns:
            gdf["osm_id"] = gdf[id_col].astype(str)
        else:
            gdf["osm_id"] = [f"feat_{i}" for i in range(len(gdf))]
        combined_list.append(gdf[["osm_id"] + keep_cols])

    combined_all = pd.concat(combined_list, ignore_index=True)
    combined_gdf = gpd.GeoDataFrame(combined_all, geometry="geometry", crs="EPSG:4326")

    # Clip to BBMP boundary polygon
    combined_gdf = gpd.clip(combined_gdf, union_wgs84)
    combined_gdf = combined_gdf[~combined_gdf.geometry.is_empty & combined_gdf.geometry.notna()]
    combined_gdf = combined_gdf.drop_duplicates(subset=["osm_id"]).copy().reset_index(drop=True)
    return combined_gdf


def analyze_dem_behavior(
    combined_proj: gpd.GeoDataFrame,
    dem_path: Path,
    gt_df: pd.DataFrame,
    bbmp_gdf_pts: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Evaluate DEM profiles along underpass segments and classify behavior."""
    with rasterio.open(dem_path) as elev_src:
        elev_arr = elev_src.read(1)
        elev_bounds = elev_src.bounds
        dx = elev_src.res[0]

    trans_to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
    trans_to_wgs = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)

    gt_points = []
    for _, row in gt_df.iterrows():
        ux, uy = trans_to_utm.transform(row["lon"], row["lat"])
        gt_points.append(
            {
                "id": row["id"],
                "name": row["location_name"],
                "geometry": Point(ux, uy),
            }
        )
    gt_gdf = gpd.GeoDataFrame(gt_points, crs="EPSG:32643")

    def get_elev_at(x: float, y: float) -> float:
        col = int(round((x - elev_bounds.left) / dx))
        row = int(round((elev_bounds.top - y) / dx))
        if 0 <= row < elev_arr.shape[0] and 0 <= col < elev_arr.shape[1]:
            return float(elev_arr[row, col])
        return np.nan

    segment_analysis = []
    for _idx, row in combined_proj.iterrows():
        geom = row.geometry
        length = geom.length
        if length < 5.0:
            continue

        n_pts = max(3, int(np.ceil(length / 5.0)))
        distances = np.linspace(0, length, n_pts)
        elevs = []
        for d in distances:
            pt = geom.interpolate(d)
            elevs.append(get_elev_at(pt.x, pt.y))

        elevs = np.array(elevs)
        valid_elevs = elevs[~np.isnan(elevs)]
        if len(valid_elevs) < 2:
            continue

        z_start = valid_elevs[0]
        z_end = valid_elevs[-1]
        z_min = float(np.min(valid_elevs))
        z_max = float(np.max(valid_elevs))

        # Linear chord between endpoints
        chord = np.linspace(z_start, z_end, len(valid_elevs))
        max_dip = float(np.max(chord - valid_elevs))
        max_crest = float(np.max(valid_elevs - chord))
        dip_below_endpoints = float(min(z_start, z_end) - z_min)

        if max_dip > 0.3:
            dem_behavior = "SAG_DETECTED"
        elif max_crest > 0.3:
            dem_behavior = "CREST_OR_DECK_VISIBLE"
        elif abs(z_start - z_end) > 0.5 and max_dip <= 0.1:
            dem_behavior = "MONOTONIC_SLOPE"
        else:
            dem_behavior = "FLAT_RUNTHROUGH"

        # Nearest GT & BBMP points
        nearest_gt_dist = float(gt_gdf.distance(geom).min())
        nearest_gt_idx = int(gt_gdf.distance(geom).argmin())
        nearest_gt_id = gt_gdf.iloc[nearest_gt_idx]["id"]
        nearest_gt_name = gt_gdf.iloc[nearest_gt_idx]["name"]

        nearest_bbmp_dist = float(bbmp_gdf_pts.distance(geom).min())
        nearest_bbmp_idx = int(bbmp_gdf_pts.distance(geom).argmin())
        nearest_bbmp_name = bbmp_gdf_pts.iloc[nearest_bbmp_idx]["name"]

        c_lon, c_lat = trans_to_wgs.transform(geom.centroid.x, geom.centroid.y)

        segment_analysis.append(
            {
                "osm_id": str(row["osm_id"]),
                "name": str(row.get("name", "")),
                "highway": str(row.get("highway", "")),
                "tunnel": str(row.get("tunnel", "")),
                "layer": str(row.get("layer", "")),
                "covered": str(row.get("covered", "")),
                "culvert": str(row.get("culvert", "")),
                "source_tag": str(row.get("source_query_tag", "")),
                "length_m": round(length, 2),
                "lat": round(c_lat, 6),
                "lon": round(c_lon, 6),
                "z_start_m": round(z_start, 3),
                "z_end_m": round(z_end, 3),
                "z_min_m": round(z_min, 3),
                "z_max_m": round(z_max, 3),
                "max_dip_m": round(max_dip, 3),
                "max_crest_m": round(max_crest, 3),
                "dip_below_endpoints_m": round(dip_below_endpoints, 3),
                "dem_behavior": dem_behavior,
                "nearest_gt_id": nearest_gt_id,
                "nearest_gt_name": nearest_gt_name,
                "nearest_gt_dist_m": round(nearest_gt_dist, 2),
                "nearest_bbmp_name": nearest_bbmp_name,
                "nearest_bbmp_dist_m": round(nearest_bbmp_dist, 2),
                "geometry": geom,
            }
        )

    return gpd.GeoDataFrame(segment_analysis, crs="EPSG:32643")


@app.command()
def main(
    config: Path = typer.Option(REPO / "configs/domain_bengaluru.yaml", help="Domain config"),
    out_dir: Path = typer.Option(
        REPO / "data/interim/terrain", help="Output directory for register"
    ),
) -> None:
    """Extract underpasses, quantify DEM profile sag/crest behavior, and write register."""
    cfg = load_config(config)
    boundary_path = REPO / cfg["boundary"]["path"]
    dem_path = REPO / cfg["paths"]["elevation_raster"]
    gt_path = REPO / "data/raw/groundtruth/sept2022_points.csv"
    bbmp_dir = REPO / "data/raw/bbmp"

    gdf_boundary = gpd.read_file(boundary_path)
    union_wgs84 = (
        gpd.GeoDataFrame(geometry=[gdf_boundary.union_all()], crs=gdf_boundary.crs)
        .to_crs("EPSG:4326")
        .geometry.iloc[0]
    )
    query_box = box(*union_wgs84.bounds)

    typer.echo("Fetching underpass features from OSM...")
    raw_underpasses = fetch_underpass_segments(query_box, union_wgs84)
    underpasses_proj = raw_underpasses.to_crs(cfg["crs"])

    gt_df = pd.read_csv(gt_path)
    trans_to_utm = pyproj.Transformer.from_crs("EPSG:4326", cfg["crs"], always_xy=True)
    bbmp_pts = []
    for kml_p in sorted(bbmp_dir.glob("*.kml")):
        for _, r in gpd.read_file(kml_p).iterrows():
            if r.geometry.geom_type == "Point":
                ux, uy = trans_to_utm.transform(r.geometry.x, r.geometry.y)
                bbmp_pts.append(
                    {
                        "name": str(r.get("Name", kml_p.stem)),
                        "geometry": Point(ux, uy),
                    }
                )
    bbmp_gdf = gpd.GeoDataFrame(bbmp_pts, crs=cfg["crs"])

    typer.echo("Analyzing DEM elevation profile behavior...")
    register = analyze_dem_behavior(underpasses_proj, dem_path, gt_df, bbmp_gdf)

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_p = out_dir / "unrepresentative_underpass_register.csv"
    gpkg_p = out_dir / "unrepresentative_underpass_register.gpkg"
    register.drop(columns=["geometry"]).to_csv(csv_p, index=False)
    register.to_file(gpkg_p, driver="GPKG")

    typer.echo(f"Analyzed {len(register)} segments.")
    typer.echo(f"Wrote register: {csv_p} and {gpkg_p}")


if __name__ == "__main__":
    app()
