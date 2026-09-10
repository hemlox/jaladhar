"""Scope: drivable network only (`configs/domain_bengaluru.yaml` `roads.
sidecar table (invariant 11: unique, and round-trips to a real OSM feature)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import osmnx as ox
import rasterio
import typer
from rasterio.features import rasterize
from shapely.geometry import box

from jaladhar.terrain.grid import Grid, build_grid, load_config, run_stage

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


class RoadFetchError(Exception):
    """Per CLAUDE.md rule 1: the caller stops and reports this. It must never
    be caught and silently papered over with an empty/synthetic road layer."""


def fetch_osm_roads(
    query_polygon_wgs84: Any, highway_types: list[str], timeout_s: int
) -> gpd.GeoDataFrame:
    """which is not a centreline and is out of scope here)."""
    ox.settings.requests_timeout = timeout_s
    ox.settings.log_console = False
    try:
        gdf = ox.features_from_polygon(query_polygon_wgs84, tags={"highway": True})
    except Exception as e:
        raise RoadFetchError(f"Overpass highway=* fetch failed: {type(e).__name__}: {e}") from e

    if gdf.empty:
        raise RoadFetchError("Overpass returned zero highway=* features for the BBMP polygon")

    gdf = gdf[gdf["highway"].isin(highway_types)]
    gdf = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    if gdf.empty:
        raise RoadFetchError(
            f"Overpass returned features, but none matched highway_types={highway_types} "
            "and LineString geometry"
        )
    return gdf


def assign_segment_ids(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.reset_index()
    id_col = "osmid" if "osmid" in gdf.columns else "id"
    gdf = gdf.sort_values(id_col, kind="stable").reset_index(drop=True)
    gdf["segment_id"] = np.arange(1, len(gdf) + 1, dtype=np.int32)
    gdf["osm_id"] = gdf[id_col].astype("int64")
    return gdf


def rasterize_segment_ids(gdf_proj: gpd.GeoDataFrame, grid: Grid) -> np.ndarray:
    shapes = list(
        zip(
            gdf_proj.sort_values("segment_id")["geometry"],
            gdf_proj.sort_values("segment_id")["segment_id"],
            strict=True,
        )
    )
    arr = rasterize(
        shapes,
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0,
        all_touched=True,
        dtype="int32",
    )
    return arr


def build_roads(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    """Fetch, ID, and rasterize the drivable road network over the buffered domain. Returns manifest
    fields."""  # noqa: E501
    grid, grid_diag = build_grid(cfg, repo_root)
    buffer_m = float(cfg["dem"]["buffer_m"])
    buffered_grid = grid.buffered(buffer_m)
    buf_cells = round(buffer_m / grid.resolution)
    r_cfg = cfg["roads"]

    buffered_box_proj = box(*buffered_grid.bounds)
    buffered_box_wgs84 = (
        gpd.GeoSeries([buffered_box_proj], crs=grid.crs).to_crs("EPSG:4326").iloc[0]
    )
    query_box = box(*buffered_box_wgs84.bounds)

    gdf = fetch_osm_roads(query_box, r_cfg["highway_types"], r_cfg["overpass_timeout_s"])
    n_fetched = len(gdf)

    gdf = gpd.clip(gdf, buffered_box_wgs84)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    if gdf.empty:
        raise RoadFetchError(
            f"{n_fetched} highway features fetched in the bbox, but zero survived "
            "clipping to the buffered domain — suspect a bbox/polygon CRS mismatch"
        )
    n_clipped = len(gdf)

    gdf = assign_segment_ids(gdf)
    gdf_proj = gdf.to_crs(grid.crs)

    id_raster_buffered = rasterize_segment_ids(gdf_proj, buffered_grid)
    buffered_grid.assert_aligned(
        {
            "transform": buffered_grid.transform,
            "width": buffered_grid.width,
            "height": buffered_grid.height,
            "crs": buffered_grid.crs,
        }
    )

    id_raster_canonical = id_raster_buffered[
        buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width
    ]
    grid.assert_aligned(
        {"transform": grid.transform, "width": grid.width, "height": grid.height, "crs": grid.crs}
    )

    interim_dir = repo_root / cfg["paths"]["interim_terrain_dir"]
    interim_dir.mkdir(parents=True, exist_ok=True)

    vector_path = interim_dir / "roads_centrelines.gpkg"
    keep_cols = ["segment_id", "osm_id", "highway", "name", "geometry"]
    gdf_out = gdf_proj[[c for c in keep_cols if c in gdf_proj.columns]].copy()
    gdf_out["name"] = gdf_out["name"].astype(str) if "name" in gdf_out.columns else None
    gdf_out.to_file(vector_path, driver="GPKG")

    lookup_path = interim_dir / "roads_segment_lookup.csv"
    lookup_cols = [c for c in ["segment_id", "osm_id", "highway", "name"] if c in gdf_out.columns]
    gdf_out[lookup_cols].to_csv(lookup_path, index=False)

    buffered_raster_path = interim_dir / "road_segment_id_buffered.tif"
    buffered_profile = buffered_grid.profile(dtype="int32", nodata=0, compress="deflate")
    with rasterio.open(buffered_raster_path, "w", **buffered_profile) as dst:
        dst.write(id_raster_buffered, 1)

    raster_path = interim_dir / "road_segment_id.tif"
    profile = grid.profile(dtype="int32", nodata=0, compress="deflate")
    with rasterio.open(raster_path, "w", **profile) as dst:
        dst.write(id_raster_canonical, 1)

    n_ids = int(id_raster_buffered.max())
    n_unique_raster_ids = len(np.unique(id_raster_canonical)) - (
        1 if 0 in id_raster_canonical else 0
    )
    n_unique_buffered_ids = len(np.unique(id_raster_buffered)) - (
        1 if 0 in id_raster_buffered else 0
    )
    ids_are_unique = gdf_out["segment_id"].is_unique
    osm_ids_are_unique = gdf_out["osm_id"].is_unique

    return {
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "buffered_grid": buffered_grid.to_manifest_dict(),
        "highway_types": r_cfg["highway_types"],
        "n_features_fetched_bbox": n_fetched,
        "n_features_after_clip": n_clipped,
        "n_segments": len(gdf_out),
        "segment_id_range": [1, n_ids],
        "segment_ids_unique": bool(ids_are_unique),
        "osm_ids_unique": bool(osm_ids_are_unique),
        "n_unique_ids_in_raster": n_unique_raster_ids,
        "n_unique_ids_in_buffered_raster": n_unique_buffered_ids,
        "vector_path": str(vector_path.relative_to(repo_root)),
        "lookup_path": str(lookup_path.relative_to(repo_root)),
        "raster_path": str(raster_path.relative_to(repo_root)),
        "buffered_raster_path": str(buffered_raster_path.relative_to(repo_root)),
    }


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_roads", help="Directory to write the manifest into"
    ),
) -> None:
    cfg = load_config(config)
    try:
        result = run_stage("phase1_terrain_roads", build_roads, cfg, config, REPO, out)
    except RoadFetchError as e:
        typer.echo(f"\nFATAL: {e}")
        raise typer.Exit(code=1) from e

    typer.echo(f"fetched {result['n_features_fetched_bbox']} features in bbox")
    typer.echo(f"kept {result['n_features_after_clip']} after clipping to BBMP polygon")
    typer.echo(f"segments: {result['n_segments']}  id range: {result['segment_id_range']}")
    typer.echo(
        f"segment_ids unique: {result['segment_ids_unique']}   "
        f"osm_ids unique: {result['osm_ids_unique']}"
    )
    typer.echo(f"wrote {result['raster_path']}, {result['vector_path']}, {result['lookup_path']}")
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
