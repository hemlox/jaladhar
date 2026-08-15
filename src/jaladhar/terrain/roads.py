"""Road centrelines and road-segment IDs for the JALADHAR terrain pipeline.

ONE OSM/Overpass fetch produces TWO outputs that must come from the SAME
query, never two separate ones — if the burn geometry and the ID raster came
from different queries, the two ID sets could silently diverge and the join
that §12's per-road-segment reporting depends on breaks with no exception:

  (a) centreline geometry (a GeoPackage), handed off to `conditioning.py`
      (not built this session) for burning into the DEM as shallow channels;
  (b) a `road_segment_id` int32 raster on the CANONICAL (unbuffered) Grid,
      with a sidecar CSV mapping each dense integer ID back to the real OSM
      way ID plus useful tags (highway class, name).

Per-road-segment status ("closure", "routing around flooded segments",
§12) is a TOPOLOGICAL claim we can defend — unlike per-square-metre depth at
10 m resolution, which we cannot (§14.1). This module is what makes that
topology exist as data.

Scope: drivable network only (`configs/domain_bengaluru.yaml` `roads.
highway_types`) — footway/path/steps/cycleway/pedestrian/bridleway/corridor
are not vehicle-routable and are excluded, matching §12's routing/closure
use case rather than every pedestrian path in the city.

Dense re-indexing, not raw OSM IDs: OSM way IDs are 64-bit and can exceed
int32 range, but the plan specifies an int32 raster. Segment IDs here are a
contiguous 1..N sequence assigned by SORTING on the real OSM way ID first —
deterministic and reproducible run-to-run — with the OSM ID preserved in the
sidecar table (invariant 11: unique, and round-trips to a real OSM feature).
"""

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

from jaladhar.terrain.grid import Grid, build_grid, load_boundary, load_config, run_stage

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


class RoadFetchError(Exception):
    """Overpass/OSM fetch failed or returned nothing usable.

    Per CLAUDE.md rule 1: the caller stops and reports this. It must never
    be caught and silently papered over with an empty/synthetic road layer.
    """


def fetch_osm_roads(
    query_polygon_wgs84: Any, highway_types: list[str], timeout_s: int
) -> gpd.GeoDataFrame:
    """Fetch highway=* features within a WGS84 polygon via Overpass.

    Returns a GeoDataFrame in WGS84 (osmnx's native output CRS), filtered to
    `highway_types` and to LineString/MultiLineString geometry (a `highway`
    tag can appear on an area-mapped polygon too — e.g. a pedestrian plaza —
    which is not a centreline and is out of scope here).
    """
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
    """Dense, deterministic int32 `segment_id` (1..N), sorted by OSM way ID.

    osmnx exposes the OSM element ID via the GeoDataFrame index for
    `features_from_polygon` results (a (element_type, osmid) MultiIndex).
    Sorting by that raw ID before assigning 1..N makes the mapping
    reproducible across runs even if Overpass ever returns features in a
    different order.
    """
    gdf = gdf.reset_index()
    id_col = "osmid" if "osmid" in gdf.columns else "id"
    gdf = gdf.sort_values(id_col, kind="stable").reset_index(drop=True)
    gdf["segment_id"] = np.arange(1, len(gdf) + 1, dtype=np.int32)
    gdf["osm_id"] = gdf[id_col].astype("int64")
    return gdf


def rasterize_segment_ids(gdf_proj: gpd.GeoDataFrame, grid: Grid) -> np.ndarray:
    """Burn `segment_id` onto the canonical Grid. 0 = no road (nodata).

    `all_touched=True` gives a connected line of cells along each
    centreline's path (standard for line-burning; a `False` setting can
    leave gaps at shallow-angle crossings, which would break the burn
    conditioning.py performs later and any connectivity-dependent use of
    this raster, e.g. tracing a segment along the network).

    Shapes are rasterized in ASCENDING segment_id order, so at the rare
    pixel where two segments' lines cross in exactly the same cell, the
    higher segment_id deterministically wins (rasterize's last-shape-wins
    semantics) — reproducible, not an artifact of dict/iteration order.
    """
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
    """Fetch, ID, and rasterize the drivable road network. Returns manifest fields."""
    grid, grid_diag = build_grid(cfg, repo_root)
    r_cfg = cfg["roads"]

    boundary_wgs84 = load_boundary(cfg, repo_root)
    boundary_valid = boundary_wgs84.copy()
    boundary_valid["geometry"] = boundary_valid.make_valid()
    union_wgs84 = boundary_valid.union_all()
    # Query the BOUNDING BOX (cheap, simple for Overpass to evaluate — a
    # multi-hundred-vertex ward-union polygon in the query itself risks the
    # kind of slow/504 Overpass response Phase 0 saw on a different broad
    # query), then clip results to the true ward-union polygon afterward.
    query_box = box(*union_wgs84.bounds)

    gdf = fetch_osm_roads(query_box, r_cfg["highway_types"], r_cfg["overpass_timeout_s"])
    n_fetched = len(gdf)

    gdf = gpd.clip(gdf, union_wgs84)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    if gdf.empty:
        raise RoadFetchError(
            f"{n_fetched} highway features fetched in the bbox, but zero survived "
            "clipping to the true BBMP polygon — suspect a bbox/polygon CRS mismatch"
        )
    n_clipped = len(gdf)

    gdf = assign_segment_ids(gdf)
    gdf_proj = gdf.to_crs(grid.crs)

    id_raster = rasterize_segment_ids(gdf_proj, grid)
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

    raster_path = interim_dir / "road_segment_id.tif"
    profile = grid.profile(dtype="int32", nodata=0, compress="deflate")
    with rasterio.open(raster_path, "w", **profile) as dst:
        dst.write(id_raster, 1)

    n_ids = int(id_raster.max())
    n_unique_raster_ids = len(np.unique(id_raster)) - (1 if 0 in id_raster else 0)
    ids_are_unique = gdf_out["segment_id"].is_unique
    osm_ids_are_unique = gdf_out["osm_id"].is_unique

    return {
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "highway_types": r_cfg["highway_types"],
        "n_features_fetched_bbox": n_fetched,
        "n_features_after_clip": n_clipped,
        "n_segments": len(gdf_out),
        "segment_id_range": [1, n_ids],
        "segment_ids_unique": bool(ids_are_unique),
        "osm_ids_unique": bool(osm_ids_are_unique),
        "n_unique_ids_in_raster": n_unique_raster_ids,
        "vector_path": str(vector_path.relative_to(repo_root)),
        "lookup_path": str(lookup_path.relative_to(repo_root)),
        "raster_path": str(raster_path.relative_to(repo_root)),
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
    """Fetch OSM road centrelines, assign segment IDs, rasterize onto the canonical grid."""
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
