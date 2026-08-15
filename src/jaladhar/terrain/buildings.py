"""Building footprints for the JALADHAR terrain pipeline: OSM union Microsoft ML.

Two sources, combined:

  - OSM `building=*` via `osmnx` — typically better-attributed in Bengaluru's
    dense core.
  - Microsoft Global ML Building Footprints (ODbL) — fills gaps OSM doesn't
    cover, mainly the peri-urban fringe. `configs/domain_bengaluru.yaml`'s
    `buildings.microsoft_footprints` names exactly ONE zoom-9 quadkey,
    "123303312", verified during Phase 1 planning to be the single tile that
    covers the entire BBMP extent (~88 MB gzipped).

⚠ Despite the `.csv.gz` extension, Microsoft's per-quadkey files are
newline-delimited GeoJSON (one `{"type": "Feature", ...}` object per line),
NOT comma-separated rows — confirmed by downloading and inspecting a real
byte-range sample before writing this parser, rather than guessing from the
filename. `pd.read_csv` would silently mis-parse this. `properties.height`
and `properties.confidence` use Microsoft's sentinel `-1.0` for "not
computed" — never trusted as a real height here; the blockage height always
comes from config (`buildings.blockage_height_m`), matching the plan's
"raise cells by a CONFIGURED height" (not a per-building measured one, which
we don't reliably have).

STREAMED, never fully loaded: the gz file is read line-by-line with a cheap
bbox pre-filter before any shapely geometry is even constructed for a
candidate outside it — CLAUDE.md, host RAM is the binding constraint on this
machine, and loading an ~88 MB-gzipped (larger decompressed) national-tile
file whole risks repeating the OOM history already logged this project.

Two treatments, both implemented behind `buildings.treatment` in config —
neither is fabricated: `blockage` raises building cells by a configured
height so water routes around them; `porosity` instead scales down a cell's
storage/conveyance without full blocking. Both `building_mask` and
`building_height_delta` are always written (independent of `treatment`) —
THIS module's output does not change with `treatment` at all. It is
`conditioning.py` that picks a burn strategy at ITS config-read time from
the same key (writing a THIRD layer, `building_conveyance_factor`, for the
porosity side — see that module's BURN PRECEDENCE docstring section), so
switching treatments never requires re-running this module.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
import rasterio
import typer
from rasterio.features import rasterize
from shapely.geometry import box, shape

from jaladhar.terrain.fetch import download_file
from jaladhar.terrain.grid import build_grid, load_boundary, load_config, run_stage

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


class BuildingFetchError(Exception):
    """An OSM or Microsoft building fetch failed or returned nothing usable.

    Per CLAUDE.md rule 1: stop and report, never fall back to a synthetic
    or empty building layer silently.
    """


def fetch_osm_buildings(query_polygon_wgs84: Any, timeout_s: int = 180) -> gpd.GeoDataFrame:
    """Fetch building=* footprints within a WGS84 polygon via Overpass."""
    ox.settings.requests_timeout = timeout_s
    ox.settings.log_console = False
    try:
        gdf = ox.features_from_polygon(query_polygon_wgs84, tags={"building": True})
    except Exception as e:
        raise BuildingFetchError(
            f"Overpass building=* fetch failed: {type(e).__name__}: {e}"
        ) from e
    if gdf.empty:
        raise BuildingFetchError("Overpass returned zero building=* features for the BBMP polygon")
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if gdf.empty:
        raise BuildingFetchError("Overpass returned features, but none were Polygon/MultiPolygon")
    return gdf


def resolve_ms_tile_url(cfg: dict[str, Any], repo_root: Path) -> str:
    """Download (or reuse a cached) Microsoft index CSV, return the one quadkey's URL.

    The INDEX itself (dataset-links.csv, one row per country-quadkey pair,
    tens of thousands of rows but each row is short) is small enough to load
    fully — it is the per-quadkey DATA files that are large and must stream.
    """
    b_cfg = cfg["buildings"]["microsoft_footprints"]
    raw_dir = repo_root / cfg["paths"]["raw_microsoft_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    index_path = raw_dir / "dataset-links.csv"

    status, _ = download_file(b_cfg["index_url"], index_path, timeout=60)
    df = pd.read_csv(index_path)
    match = df[(df["Location"] == b_cfg["location"]) & (df["QuadKey"] == int(b_cfg["quadkey"]))]
    if match.empty:
        raise BuildingFetchError(
            f"quadkey {b_cfg['quadkey']} not found for Location={b_cfg['location']} "
            f"in the Microsoft index ({index_path}, fetch status={status}) — the index "
            "may have been restructured since Phase 1 planning verified this quadkey"
        )
    return str(match.iloc[0]["Url"])


def stream_ms_footprints_in_polygon(
    gz_path: Path, query_polygon_wgs84: Any
) -> list[dict[str, Any]]:
    """Line-by-line GeoJSONL parse, filtered to the query polygon as we go.

    Two-stage filter per candidate: a cheap bounds-tuple comparison against
    the polygon's bbox BEFORE constructing any shapely geometry (rejects
    the vast majority of a zoom-9 tile's footprints — the tile covers a much
    larger area than the true BBMP polygon), then a precise `.intersects()`
    check for bbox survivors. Never loads the file as a single json/DataFrame
    object; each line is parsed, checked, and discarded independently.
    """
    qminx, qminy, qmaxx, qmaxy = query_polygon_wgs84.bounds
    kept: list[dict[str, Any]] = []
    n_lines = 0
    n_bbox_survivors = 0
    with gzip.open(gz_path, "rt") as f:
        for line in f:
            n_lines += 1
            line = line.strip()
            if not line:
                continue
            try:
                feat = json.loads(line)
            except json.JSONDecodeError:
                continue  # a malformed line is skipped, not fatal to the whole stream
            coords_flat = _flatten_coords(feat["geometry"]["coordinates"])
            if not coords_flat:
                continue
            lons = coords_flat[0::2]
            lats = coords_flat[1::2]
            fminx, fmaxx = min(lons), max(lons)
            fminy, fmaxy = min(lats), max(lats)
            if fmaxx < qminx or fminx > qmaxx or fmaxy < qminy or fminy > qmaxy:
                continue  # cheap bbox reject — no shapely object built
            n_bbox_survivors += 1
            geom = shape(feat["geometry"])
            if not geom.intersects(query_polygon_wgs84):
                continue
            props = feat.get("properties", {})
            kept.append(
                {
                    "geometry": geom,
                    "ms_height": props.get("height", -1.0),
                    "ms_confidence": props.get("confidence", -1.0),
                }
            )
    return kept, n_lines, n_bbox_survivors


def _flatten_coords(coords: Any) -> list[float]:
    """Flatten a GeoJSON coordinate array (any nesting depth) to [x0,y0,x1,y1,...]."""
    out: list[float] = []
    stack = [coords]
    while stack:
        c = stack.pop()
        if isinstance(c, (int, float)):
            continue  # a lone number never reaches here in valid GeoJSON coords
        if len(c) >= 2 and all(isinstance(v, (int, float)) for v in c[:2]) and len(c) == 2:
            out.append(c[0])
            out.append(c[1])
        else:
            stack.extend(c)
    return out


def dedup_ms_against_osm(
    ms_gdf: gpd.GeoDataFrame, osm_gdf: gpd.GeoDataFrame, predicate: str
) -> gpd.GeoDataFrame:
    """Drop Microsoft footprints that intersect any OSM building.

    OSM preferred where both exist (typically better attributed in the
    core); Microsoft kept only where it fills a gap. `gpd.sjoin` with
    `how="left"` + drop-matched is the standard spatial-dedup idiom and
    avoids an O(n*m) Python-level double loop over tens of thousands of
    polygons on each side.
    """
    osm_geoms_only = osm_gdf[["geometry"]].reset_index(drop=True)
    joined = gpd.sjoin(ms_gdf, osm_geoms_only, how="left", predicate=predicate)
    unmatched = joined[joined["index_right"].isna()].drop(columns=["index_right"])
    return ms_gdf.loc[unmatched.index]


def build_buildings(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    """Fetch OSM+Microsoft buildings, dedup, rasterize mask + height-delta."""
    grid, grid_diag = build_grid(cfg, repo_root)
    b_cfg = cfg["buildings"]

    boundary_wgs84 = load_boundary(cfg, repo_root)
    boundary_valid = boundary_wgs84.copy()
    boundary_valid["geometry"] = boundary_valid.make_valid()
    union_wgs84 = boundary_valid.union_all()
    query_box = box(*union_wgs84.bounds)

    osm_gdf = fetch_osm_buildings(query_box)
    osm_gdf = gpd.clip(osm_gdf, union_wgs84)
    osm_gdf = osm_gdf[~osm_gdf.geometry.is_empty & osm_gdf.geometry.notna()]
    if osm_gdf.empty:
        raise BuildingFetchError("zero OSM buildings survived clipping to the BBMP polygon")
    n_osm = len(osm_gdf)

    ms_url = resolve_ms_tile_url(cfg, repo_root)
    quadkey = b_cfg["microsoft_footprints"]["quadkey"]
    ms_dest = repo_root / cfg["paths"]["raw_microsoft_dir"] / f"quadkey_{quadkey}.geojsonl.gz"
    status, size = download_file(ms_url, ms_dest, timeout=600)
    ms_rows, n_lines, n_bbox_survivors = stream_ms_footprints_in_polygon(ms_dest, union_wgs84)
    if not ms_rows:
        raise BuildingFetchError(
            f"streamed {n_lines} lines from the Microsoft quadkey file ({ms_dest}, "
            f"{size} bytes, download status={status}) but zero footprints intersected "
            "the BBMP polygon — suspect a coordinate order or quadkey mismatch"
        )
    ms_gdf = gpd.GeoDataFrame(ms_rows, crs="EPSG:4326")
    n_ms_in_polygon = len(ms_gdf)

    ms_gdf_kept = dedup_ms_against_osm(ms_gdf, osm_gdf, cfg["buildings"]["dedup_predicate"])
    n_ms_kept = len(ms_gdf_kept)
    n_ms_dropped_dup = n_ms_in_polygon - n_ms_kept

    osm_gdf = osm_gdf.assign(source="osm")[["geometry", "source"]]
    ms_gdf_kept = ms_gdf_kept.assign(source="microsoft")[["geometry", "source"]]
    combined = pd.concat([osm_gdf, ms_gdf_kept], ignore_index=True)
    combined = gpd.GeoDataFrame(combined, crs="EPSG:4326").to_crs(grid.crs)

    mask = rasterize(
        [(geom, 1) for geom in combined.geometry],
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0,
        all_touched=False,  # a building footprint is an AREA — centroid-style
        # "is this cell inside the polygon" rasterization is
        # the correct semantics here, unlike roads.py's line
        # burn where all_touched=True is required for
        # connectivity. all_touched=True on a dense polygon
        # layer would systematically overstate footprint area
        # by roughly a half-cell border on every building.
        dtype="uint8",
    )
    treatment = b_cfg["treatment"]
    blockage_height_m = float(b_cfg["blockage_height_m"])
    height_delta = np.where(mask == 1, np.float32(blockage_height_m), np.float32(0.0)).astype(
        np.float32
    )

    interim_dir = repo_root / cfg["paths"]["interim_terrain_dir"]
    interim_dir.mkdir(parents=True, exist_ok=True)

    vector_path = interim_dir / "buildings.gpkg"
    combined.to_file(vector_path, driver="GPKG")

    mask_path = interim_dir / "building_mask.tif"
    with rasterio.open(mask_path, "w", **grid.profile(dtype="uint8", nodata=0)) as dst:
        dst.write(mask, 1)

    delta_path = interim_dir / "building_height_delta.tif"
    with rasterio.open(delta_path, "w", **grid.profile(dtype="float32", nodata=0.0)) as dst:
        dst.write(height_delta, 1)

    n_building_cells = int(mask.sum())

    return {
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "treatment": treatment,
        "blockage_height_m": blockage_height_m,
        "porosity_conveyance_factor": float(b_cfg["porosity_conveyance_factor"]),
        "osm": {"n_fetched_and_clipped": n_osm},
        "microsoft": {
            "quadkey": b_cfg["microsoft_footprints"]["quadkey"],
            "source_url": ms_url,
            "download_status": status,
            "download_bytes": size,
            "n_lines_streamed": n_lines,
            "n_bbox_survivors": n_bbox_survivors,
            "n_intersecting_bbmp_polygon": n_ms_in_polygon,
            "n_dropped_as_duplicate_of_osm": n_ms_dropped_dup,
            "n_kept_gap_fill": n_ms_kept,
        },
        "n_buildings_total": len(combined),
        "n_building_cells": n_building_cells,
        "building_cell_fraction": n_building_cells / (grid.width * grid.height),
        "vector_path": str(vector_path.relative_to(repo_root)),
        "mask_path": str(mask_path.relative_to(repo_root)),
        "height_delta_path": str(delta_path.relative_to(repo_root)),
    }


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_buildings", help="Directory to write the manifest into"
    ),
) -> None:
    """Fetch OSM + Microsoft ML buildings, dedup, rasterize mask + height-delta."""
    cfg = load_config(config)
    try:
        result = run_stage("phase1_terrain_buildings", build_buildings, cfg, config, REPO, out)
    except BuildingFetchError as e:
        typer.echo(f"\nFATAL: {e}")
        raise typer.Exit(code=1) from e

    typer.echo(f"OSM buildings (clipped to BBMP): {result['osm']['n_fetched_and_clipped']:,}")
    ms = result["microsoft"]
    typer.echo(
        f"Microsoft quadkey {ms['quadkey']}: streamed {ms['n_lines_streamed']:,} lines, "
        f"{ms['n_intersecting_bbmp_polygon']:,} in BBMP polygon, "
        f"{ms['n_dropped_as_duplicate_of_osm']:,} dropped as OSM duplicates, "
        f"{ms['n_kept_gap_fill']:,} kept as gap-fill"
    )
    typer.echo(
        f"total buildings: {result['n_buildings_total']:,}   "
        f"building cells: {result['n_building_cells']:,} "
        f"({100 * result['building_cell_fraction']:.2f}% of domain)"
    )
    typer.echo(f"treatment: {result['treatment']}")
    typer.echo(
        f"wrote {result['mask_path']}, {result['height_delta_path']}, {result['vector_path']}"
    )
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
