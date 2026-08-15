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
    """Fetch waterways, compute distance-to-nearest, write the capacity prior raster."""
    grid, grid_diag = build_grid(cfg, repo_root)
    d_cfg = cfg["drains"]

    boundary_wgs84 = load_boundary(cfg, repo_root)
    boundary_valid = boundary_wgs84.copy()
    boundary_valid["geometry"] = boundary_valid.make_valid()
    union_wgs84 = boundary_valid.union_all()
    query_box = box(*union_wgs84.bounds)

    gdf_proj = fetch_waterways(
        query_box, union_wgs84, d_cfg["waterway_tags"], grid.crs, d_cfg["overpass_timeout_s"]
    )
    n_features = len(gdf_proj)

    drain_mask = rasterize(
        [(geom, 1) for geom in gdf_proj.geometry],
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0,
        all_touched=True,  # a line feature — same connectivity reasoning as roads.py
        dtype="uint8",
    )
    n_drain_cells = int(drain_mask.sum())
    if n_drain_cells == 0:
        raise DrainFetchError(
            f"{n_features} waterway features fetched, but the rasterized mask has zero "
            "drain cells — suspect a rasterize/CRS mismatch"
        )

    # distance_transform_edt measures distance to the nearest ZERO in a binary
    # array (in pixel units by default); pass `sampling` so it returns metres
    # directly, and invert the mask since drain cells are our "target" (True
    # == not-yet-a-drain, per EDT's own semantics of measuring away from
    # False/target pixels — see scipy docs: distances are to the nearest
    # background (0) pixel of the INPUT, so we feed it the drain mask's
    # logical inverse to measure distance TO drain cells, not away from them).
    distance_m = distance_transform_edt(
        drain_mask == 0, sampling=(grid.resolution, grid.resolution)
    ).astype(np.float32)

    baseline = _baseline_value(d_cfg["baseline_capacity_by_landuse_mm_per_hr"])
    decay_m = float(d_cfg["distance_decay"]["decay_m"])
    if d_cfg["distance_decay"]["functional_form"] != "exponential":
        raise ValueError(
            f"drains.distance_decay.functional_form="
            f"{d_cfg['distance_decay']['functional_form']!r} not implemented "
            "(only 'exponential' is)"
        )
    capacity = (baseline * np.exp(-distance_m / decay_m)).astype(np.float32)

    interim_dir = repo_root / cfg["paths"]["interim_terrain_dir"]
    interim_dir.mkdir(parents=True, exist_ok=True)

    waterways_path = interim_dir / "waterways.gpkg"
    gdf_out = gdf_proj.copy()
    keep_cols = [c for c in ["waterway", "name", "geometry"] if c in gdf_out.columns]
    gdf_out = gdf_out[keep_cols]
    if "name" in gdf_out.columns:
        gdf_out["name"] = gdf_out["name"].astype(str)
    gdf_out.to_file(waterways_path, driver="GPKG")

    dist_path = interim_dir / "distance_to_drain.tif"
    with rasterio.open(dist_path, "w", **grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(distance_m, 1)

    capacity_path = interim_dir / "drain_capacity.tif"
    with rasterio.open(capacity_path, "w", **grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(capacity, 1)

    return {
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "waterway_tags": d_cfg["waterway_tags"],
        "n_features": n_features,
        "n_drain_cells": n_drain_cells,
        "assumed_uncalibrated": bool(d_cfg["assumed_uncalibrated"]),
        "baseline_capacity_mm_per_hr": baseline,
        "distance_decay_m": decay_m,
        "distance_m_min": float(distance_m.min()),
        "distance_m_max": float(distance_m.max()),
        "distance_m_mean": float(distance_m.mean()),
        "capacity_min_mm_per_hr": float(capacity.min()),
        "capacity_max_mm_per_hr": float(capacity.max()),
        "waterways_vector_path": str(waterways_path.relative_to(repo_root)),
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
