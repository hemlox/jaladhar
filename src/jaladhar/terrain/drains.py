"""Drain-capacity PRIOR for the JALADHAR terrain pipeline.

Bengaluru's actual stormwater drain network is NOT open data (PROMPT.md
§14.2 — the largest single source of error, and a data-availability
problem, not a modelling one). Everything this module writes is an
EXPLICIT, DOCUMENTED, ARBITRARY starting point for Phase 3 gradient-descent
calibration against observed flooding, never a measurement — see
`configs/domain_bengaluru.yaml`'s `drains:` section, which spells out why
setting a documented model-parameter starting point is what PROMPT.md
§6.1 item 5 itself asks for ("a per-cell removal rate capped by an
ASSUMED capacity"), not a CLAUDE.md rule 1 violation (that rule prohibits
fabricating and presenting DATA/observations as real, which this is not).

Two ingredients, one real, one arbitrary:

  - REAL: distance to the nearest OSM waterway=drain|ditch|stream feature
    (the primary rajakaluve channels, per §6.1) — genuine geometry, not
    invented.
  - ARBITRARY (documented as such throughout): a uniform baseline capacity
    and an exponential decay length, both explicit Phase-3 calibration
    targets. The config's `baseline_capacity_by_landuse_mm_per_hr` is
    intentionally the SAME value across every class — differentiating them
    without a source to justify the differences would imply false
    precision we don't have. If a future session obtains real per-land-use
    evidence, this module's flat-value shortcut (see `_baseline_value`)
    needs replacing with an actual landuse classification pass, mirroring
    `roughness.py`'s landuse fetch.

Distance computed via a raster Euclidean distance transform (`scipy.ndimage.
distance_transform_edt`), not a per-cell geometric query against the line
network — the latter would mean ~12M individual distance evaluations against
a linear feature set, drastically more expensive than one EDT pass over a
rasterized drain mask.

Also writes the waterway centreline GEOMETRY (`waterways.gpkg`), not just
the derived distance/capacity rasters — `conditioning.py` (not built this
session) needs the actual lines to burn as deeper channels (§6.1 item 3),
and re-querying Overpass a second time for the identical
waterway=drain|ditch|stream tags this module already fetched would be a
redundant fetch, not an independence requirement: unlike the roads/
buildings/roughness/drains siblings (deliberately independent of EACH
OTHER), conditioning.py is the next pipeline stage and is SUPPOSED to
consume all four siblings' outputs directly, per the plan's dependency
graph — reusing drains.py's already-fetched geometry here is the same
"one fetch, multiple downstream outputs" principle `roads.py` already
uses for its own centreline/ID-raster split.
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
from scipy.ndimage import distance_transform_edt
from shapely.geometry import box

from jaladhar.terrain.grid import build_grid, load_boundary, load_config, run_stage

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


class DrainFetchError(Exception):
    """The OSM waterway fetch failed or returned nothing usable.

    Per CLAUDE.md rule 1: stop and report — never fall back to a uniform
    (distance-blind) capacity raster silently. A city this size genuinely
    having zero mapped drain/ditch/stream features would itself be a real
    and reportable finding, not something to paper over.
    """


def fetch_waterways(
    query_polygon_wgs84: Any,
    union_wgs84: Any,
    waterway_tags: list[str],
    grid_crs: Any,
    timeout_s: int,
) -> gpd.GeoDataFrame:
    """OSM waterway=drain|ditch|stream lines within the BBMP polygon."""
    ox.settings.requests_timeout = timeout_s
    ox.settings.log_console = False
    try:
        gdf = ox.features_from_polygon(query_polygon_wgs84, tags={"waterway": waterway_tags})
    except Exception as e:
        raise DrainFetchError(f"Overpass waterway=* fetch failed: {type(e).__name__}: {e}") from e
    if gdf.empty:
        raise DrainFetchError(f"Overpass returned zero waterway={waterway_tags} features")

    gdf = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    if gdf.empty:
        raise DrainFetchError("features found, but none were LineString/MultiLineString")

    gdf = gpd.clip(gdf, union_wgs84)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    if gdf.empty:
        raise DrainFetchError("zero waterway features survived clipping to the BBMP polygon")
    return gdf.to_crs(grid_crs)


def _baseline_value(by_landuse: dict[str, float]) -> float:
    """The single uniform baseline value — see module docstring for why.

    Asserts every configured class truly is equal, so a future edit that
    differentiates the config WITHOUT updating this module's logic fails
    loudly here rather than silently keeping the flat-value shortcut.
    """
    values = set(by_landuse.values())
    if len(values) != 1:
        raise ValueError(
            f"drains.baseline_capacity_by_landuse_mm_per_hr is no longer uniform "
            f"({by_landuse}) — this module's flat-value shortcut is now stale; "
            "implement a real landuse classification pass (mirroring roughness.py) "
            "before using per-class values."
        )
    return values.pop()


def build_drains(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    """Fetch waterways over the buffered domain, compute distance-to-nearest, write capacity rasters."""
    grid, grid_diag = build_grid(cfg, repo_root)
    buffer_m = float(cfg["dem"]["buffer_m"])
    buffered_grid = grid.buffered(buffer_m)
    buf_cells = round(buffer_m / grid.resolution)
    d_cfg = cfg["drains"]

    # Compute bounding box of the BUFFERED domain in WGS84
    buffered_box_proj = box(*buffered_grid.bounds)
    buffered_box_wgs84 = (
        gpd.GeoSeries([buffered_box_proj], crs=grid.crs).to_crs("EPSG:4326").iloc[0]
    )
    query_box = box(*buffered_box_wgs84.bounds)

    gdf_proj = fetch_waterways(
        query_box, buffered_box_wgs84, d_cfg["waterway_tags"], grid.crs, d_cfg["overpass_timeout_s"]
    )
    n_features = len(gdf_proj)

    # Rasterize on the BUFFERED grid
    drain_mask_buffered = rasterize(
        [(geom, 1) for geom in gdf_proj.geometry],
        out_shape=(buffered_grid.height, buffered_grid.width),
        transform=buffered_grid.transform,
        fill=0,
        all_touched=True,  # a line feature — same connectivity reasoning as roads.py
        dtype="uint8",
    )
    n_drain_cells_buffered = int(drain_mask_buffered.sum())
    if n_drain_cells_buffered == 0:
        raise DrainFetchError(
            f"{n_features} waterway features fetched, but the rasterized mask has zero "
            "drain cells — suspect a rasterize/CRS mismatch"
        )

    # distance_transform_edt on the BUFFERED grid
    distance_m_buffered = distance_transform_edt(
        drain_mask_buffered == 0, sampling=(grid.resolution, grid.resolution)
    ).astype(np.float32)

    baseline = _baseline_value(d_cfg["baseline_capacity_by_landuse_mm_per_hr"])
    decay_m = float(d_cfg["distance_decay"]["decay_m"])
    if d_cfg["distance_decay"]["functional_form"] != "exponential":
        raise ValueError(
            f"drains.distance_decay.functional_form="
            f"{d_cfg['distance_decay']['functional_form']!r} not implemented "
            "(only 'exponential' is)"
        )
    capacity_buffered = (baseline * np.exp(-distance_m_buffered / decay_m)).astype(np.float32)

    # Crop to canonical grid
    distance_m_canonical = distance_m_buffered[
        buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width
    ]
    capacity_canonical = capacity_buffered[
        buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width
    ]
    n_drain_cells_canonical = int((drain_mask_buffered[buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width] == 1).sum())

    interim_dir = repo_root / cfg["paths"]["interim_terrain_dir"]
    interim_dir.mkdir(parents=True, exist_ok=True)

    waterways_path = interim_dir / "waterways.gpkg"
    gdf_out = gdf_proj.copy()
    keep_cols = [c for c in ["waterway", "name", "geometry"] if c in gdf_out.columns]
    gdf_out = gdf_out[keep_cols]
    if "name" in gdf_out.columns:
        gdf_out["name"] = gdf_out["name"].astype(str)
    gdf_out.to_file(waterways_path, driver="GPKG")

    # Write buffered rasters
    buffered_dist_path = interim_dir / "distance_to_drain_buffered.tif"
    with rasterio.open(buffered_dist_path, "w", **buffered_grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(distance_m_buffered, 1)

    buffered_capacity_path = interim_dir / "drain_capacity_buffered.tif"
    with rasterio.open(buffered_capacity_path, "w", **buffered_grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(capacity_buffered, 1)

    # Write canonical rasters
    dist_path = interim_dir / "distance_to_drain.tif"
    with rasterio.open(dist_path, "w", **grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(distance_m_canonical, 1)

    capacity_path = interim_dir / "drain_capacity.tif"
    with rasterio.open(capacity_path, "w", **grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(capacity_canonical, 1)

    return {
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "buffered_grid": buffered_grid.to_manifest_dict(),
        "waterway_tags": d_cfg["waterway_tags"],
        "n_features": n_features,
        "n_drain_cells": n_drain_cells_canonical,
        "n_drain_cells_buffered": n_drain_cells_buffered,
        "assumed_uncalibrated": bool(d_cfg["assumed_uncalibrated"]),
        "baseline_capacity_mm_per_hr": baseline,
        "distance_decay_m": decay_m,
        "distance_m_min": float(distance_m_canonical.min()),
        "distance_m_max": float(distance_m_canonical.max()),
        "distance_m_mean": float(distance_m_canonical.mean()),
        "capacity_min_mm_per_hr": float(capacity_canonical.min()),
        "capacity_max_mm_per_hr": float(capacity_canonical.max()),
        "waterways_vector_path": str(waterways_path.relative_to(repo_root)),
        "distance_path": str(dist_path.relative_to(repo_root)),
        "buffered_distance_path": str(buffered_dist_path.relative_to(repo_root)),
        "capacity_path": str(capacity_path.relative_to(repo_root)),
        "buffered_capacity_path": str(buffered_capacity_path.relative_to(repo_root)),
        "distance_to_drain_path": str(dist_path.relative_to(repo_root)),
        "drain_capacity_path": str(capacity_path.relative_to(repo_root)),
    }


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_drains", help="Directory to write the manifest into"
    ),
) -> None:
    """Fetch waterways, compute a distance-decayed drain-capacity PRIOR (not a measurement)."""
    cfg = load_config(config)
    try:
        result = run_stage("phase1_terrain_drains", build_drains, cfg, config, REPO, out)
    except (DrainFetchError, ValueError) as e:
        typer.echo(f"\nFATAL: {e}")
        raise typer.Exit(code=1) from e

    typer.echo(
        f"waterway features: {result['n_features']}   drain cells: {result['n_drain_cells']:,}"
    )
    typer.echo(
        f"distance to nearest drain: min={result['distance_m_min']:.1f}m "
        f"mean={result['distance_m_mean']:.1f}m max={result['distance_m_max']:.1f}m"
    )
    typer.echo(
        f"capacity prior [ASSUMED_UNCALIBRATED={result['assumed_uncalibrated']}]: "
        f"baseline={result['baseline_capacity_mm_per_hr']:.1f} mm/hr  "
        f"decay={result['distance_decay_m']:.0f}m  "
        f"range=[{result['capacity_min_mm_per_hr']:.3f}, "
        f"{result['capacity_max_mm_per_hr']:.3f}] mm/hr"
    )
    typer.echo(
        f"wrote {result['waterways_vector_path']}, {result['distance_to_drain_path']}, "
        f"{result['drain_capacity_path']}"
    )
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
