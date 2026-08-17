"""DEM conditioning for the JALADHAR terrain pipeline — depression-aware D4 conditioning.

A 30 m-native DEM cannot resolve a 10 m street or rajakaluve. We fix this with
VECTOR-DRIVEN STRUCTURE, not interpolation: burn known road centrelines,
building footprints, and drain channels from OSM/Microsoft vectors into the
elevation surface.

Step order:
  1. Read the buffered DEM `fetch.py` wrote (already resampled to 10 m).
  2. Burn OSM road centrelines DOWN (shallow channels).
  3. Burn OSM waterway=drain|ditch|stream DOWN (deeper than roads — rajakaluves).
  4. Burn buildings UP as blockages (if dem_is_dtm) outside conveyance channels.
  5. Write `dem_conditioned_prebreach.tif`.
  6. Extract candidate depressions (>= 4 cells = 400 m2) and classify on external
     evidence (STORAGE, QUARRY, LANDFILL, UNCERTAIN) via `depressions.py`.
  7. Apply D4 Priority-Flood and Capped Hybrid Breach & Fill OUTSIDE retained basins:
     - Retained basin interiors are NEVER modified.
     - Carve along cost surface (waterway -> road -> bare ground -> BUILDING: FORBIDDEN)
       up to cut_max = 0.50 m.
     - Fill pits minimally up to fill_max = 0.25 m (buildings forbidden).
     - Residuals outside bounds are enumerated in the register.
  8. Write `dem_conditioned_postbreach.tif`.

CONNECTIVITY:
Declared as D4 (cardinal 4-connected 5-point stencil matching ACC solver).
"""

from __future__ import annotations

import heapq
import json
import logging
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import scipy.ndimage as ndi
import typer
from rasterio.features import rasterize

from jaladhar.terrain.depressions import (
    CLASS_LANDFILL,
    CLASS_QUARRY,
    CLASS_STORAGE,
    CLASS_UNCERTAIN,
    build_depressions,
)
from jaladhar.terrain.grid import (
    Grid,
    atomic_output_path,
    build_grid,
    load_config,
    run_stage,
)

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]
log = logging.getLogger(__name__)

CONNECTIVITY_D4 = "D4"
CUT_MAX_M = 0.50
FILL_MAX_M = 0.25


class ConditioningError(Exception):
    """A required upstream artifact is missing or unusable."""


def _require(path: Path, produced_by: str) -> Path:
    if not path.exists():
        raise ConditioningError(f"required input {path} does not exist — run `{produced_by}` first")
    return path


def rasterize_burn_mask(vector_path: Path, buffered_grid: Grid) -> np.ndarray:
    """Rasterize a vector file's geometry onto the buffered grid. 1 = burn here."""
    gdf = gpd.read_file(vector_path)
    if gdf.empty:
        raise ConditioningError(f"{vector_path} contains zero features")
    mask = rasterize(
        [(geom, 1) for geom in gdf.geometry],
        out_shape=(buffered_grid.height, buffered_grid.width),
        transform=buffered_grid.transform,
        fill=0,
        all_touched=True,
        dtype="uint8",
    )
    return mask


def count_pits(elevation: np.ndarray, valid_mask: np.ndarray) -> int:
    """Count 8-connected local minima strictly inside the valid interior."""
    h, w = elevation.shape
    is_pit = np.ones((h, w), dtype=bool)
    is_pit[0, :] = is_pit[-1, :] = is_pit[:, 0] = is_pit[:, -1] = False
    is_pit &= valid_mask

    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            neighbour = np.roll(np.roll(elevation, -dy, axis=0), -dx, axis=1)
            neighbour_valid = np.roll(np.roll(valid_mask, -dy, axis=0), -dx, axis=1)
            is_pit &= neighbour_valid & (neighbour > elevation)
    return int(is_pit.sum())


def count_pits_d4(elevation: np.ndarray, valid_mask: np.ndarray) -> int:
    """Count 4-connected (cardinal-only) local minima strictly inside the valid interior."""
    h, w = elevation.shape
    is_pit = np.ones((h, w), dtype=bool)
    is_pit[0, :] = is_pit[-1, :] = is_pit[:, 0] = is_pit[:, -1] = False
    is_pit &= valid_mask

    for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        neighbour = np.roll(np.roll(elevation, -dy, axis=0), -dx, axis=1)
        neighbour_valid = np.roll(np.roll(valid_mask, -dy, axis=0), -dx, axis=1)
        is_pit &= neighbour_valid & (neighbour > elevation)
    return int(is_pit.sum())


def _find_d4_pits(elevation: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    """Return a boolean mask of D4 pits (cardinal-only local minima)."""
    h, w = elevation.shape
    is_pit = np.ones((h, w), dtype=bool)
    is_pit[0, :] = is_pit[-1, :] = is_pit[:, 0] = is_pit[:, -1] = False
    is_pit &= valid_mask

    for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        neighbour = np.roll(np.roll(elevation, -dy, axis=0), -dx, axis=1)
        neighbour_valid = np.roll(np.roll(valid_mask, -dy, axis=0), -dx, axis=1)
        is_pit &= neighbour_valid & (neighbour > elevation)
    return is_pit


def d4_capped_hybrid_conditioning(
    dem: np.ndarray,
    valid_mask: np.ndarray,
    retained_basin_mask: np.ndarray,
    cost_surface: np.ndarray,
    building_mask: np.ndarray,
    cut_max: float = CUT_MAX_M,
    fill_max: float = FILL_MAX_M,
) -> tuple[np.ndarray, dict[str, Any]]:
    """D4 Priority Flood and Capped Hybrid resolution outside retained basins.

    Properties guaranteed:
    1. Retained basin interiors are NEVER modified.
    2. Building cells are FORBIDDEN from being lowered or modified.
    3. Maximum cut <= cut_max (0.50 m) along cost surface.
    4. Maximum fill <= fill_max (0.25 m) on bare ground / non-building.
    5. Monotone cardinal spillway routing from domain boundary & retained basins.
    """
    working_dem = dem.copy()
    original_dem = dem.copy()
    h, w = dem.shape

    # Step 1: Diagonal staircase conversion along cost surface (carve <= cut_max)
    DIAGONALS = [
        ((-1, -1), [(-1, 0), (0, -1)]),  # NW -> North, West
        ((-1, 1), [(-1, 0), (0, 1)]),   # NE -> North, East
        ((1, -1), [(1, 0), (0, -1)]),   # SW -> South, West
        ((1, 1), [(1, 0), (0, 1)]),     # SE -> South, East
    ]
    CARDINAL = [(-1, 0), (1, 0), (0, -1), (0, 1)]

    pits_pre = _find_d4_pits(working_dem, valid_mask) & ~retained_basin_mask
    pit_rows, pit_cols = np.where(pits_pre)
    n_staircase = 0

    for r, c in zip(pit_rows, pit_cols):
        p_elev = working_dem[r, c]
        best_d_elev = p_elev
        best_m = None
        best_cost = 999

        for (ddy, ddx), intermediates in DIAGONALS:
            dr, dc = r + ddy, c + ddx
            if 0 <= dr < h and 0 <= dc < w and valid_mask[dr, dc]:
                d_elev = working_dem[dr, dc]
                if d_elev < p_elev:
                    for mdy, mdx in intermediates:
                        mr, mc = r + mdy, c + mdx
                        if 0 <= mr < h and 0 <= mc < w and valid_mask[mr, mc]:
                            if cost_surface[mr, mc] < 999:  # not building
                                m_cost = cost_surface[mr, mc]
                                target_z = 0.5 * (p_elev + d_elev)
                                cur_z = working_dem[mr, mc]
                                req_cut = cur_z - target_z
                                if 0 < req_cut <= cut_max and (original_dem[mr, mc] - target_z <= cut_max):
                                    if m_cost < best_cost or (m_cost == best_cost and d_elev < best_d_elev):
                                        best_cost = m_cost
                                        best_d_elev = d_elev
                                        best_m = (mr, mc, target_z)

        if best_m is not None:
            mr, mc, tz = best_m
            working_dem[mr, mc] = np.float32(tz)
            n_staircase += 1

    # Step 2: D4 Priority Flood from domain boundary & retained basins
    visited = np.zeros((h, w), dtype=bool)
    pq: list[tuple[float, int, int]] = []

    # Outer border cells
    for r in (0, h - 1):
        for c in range(w):
            if valid_mask[r, c] and not visited[r, c]:
                visited[r, c] = True
                heapq.heappush(pq, (float(working_dem[r, c]), r, c))

    for c in (0, w - 1):
        for r in range(h):
            if valid_mask[r, c] and not visited[r, c]:
                visited[r, c] = True
                heapq.heappush(pq, (float(working_dem[r, c]), r, c))

    # Retained basins (treated as established sinks)
    r_rows, r_cols = np.where(retained_basin_mask & valid_mask)
    for r, c in zip(r_rows, r_cols):
        if not visited[r, c]:
            visited[r, c] = True
            heapq.heappush(pq, (float(working_dem[r, c]), r, c))

    n_filled = 0
    n_residuals = 0

    while pq:
        z_spill, r, c = heapq.heappop(pq)

        for dy, dx in CARDINAL:
            nr, nc = r + dy, c + dx
            if 0 <= nr < h and 0 <= nc < w and valid_mask[nr, nc]:
                if not visited[nr, nc]:
                    visited[nr, nc] = True
                    z_orig = float(working_dem[nr, nc])

                    if z_orig >= z_spill:
                        # Naturally drains
                        heapq.heappush(pq, (z_orig, nr, nc))
                    else:
                        if building_mask[nr, nc]:
                            # Building cells forbidden to modify
                            heapq.heappush(pq, (z_orig, nr, nc))
                        else:
                            delta = z_spill - z_orig
                            if delta <= fill_max and (working_dem[nr, nc] + delta - original_dem[nr, nc] <= fill_max):
                                working_dem[nr, nc] = np.float32(z_spill)
                                n_filled += 1
                                heapq.heappush(pq, (z_spill, nr, nc))
                            else:
                                # Residual pit
                                n_residuals += 1
                                heapq.heappush(pq, (z_orig, nr, nc))

    # Compute diagnostics
    diff = working_dem - original_dem
    cuts = -diff[diff < -1e-4]
    fills = diff[diff > 1e-4]

    modified_mask = np.abs(diff) > 1e-4
    n_modified = int(modified_mask.sum())
    max_cut = float(cuts.max()) if len(cuts) > 0 else 0.0
    max_fill = float(fills.max()) if len(fills) > 0 else 0.0
    mean_cut = float(cuts.mean()) if len(cuts) > 0 else 0.0
    mean_fill = float(fills.mean()) if len(fills) > 0 else 0.0

    building_modified = int((modified_mask & building_mask).sum())
    basin_modified = int((modified_mask & retained_basin_mask).sum())

    assert building_modified == 0, f"Building cells were modified: {building_modified}"
    assert basin_modified == 0, f"Retained basin interiors were modified: {basin_modified}"
    assert max_cut <= cut_max + 1e-4, f"max_cut {max_cut} exceeded cut_max {cut_max}"
    assert max_fill <= fill_max + 1e-4, f"max_fill {max_fill} exceeded fill_max {fill_max}"

    diag = {
        "n_cells_modified": n_modified,
        "n_cells_cut": len(cuts),
        "n_cells_filled": len(fills),
        "max_cut_m": max_cut,
        "max_fill_m": max_fill,
        "mean_cut_m": mean_cut,
        "mean_fill_m": mean_fill,
        "n_staircase_carved": n_staircase,
        "n_priority_filled": n_filled,
        "n_residuals_enumerated": n_residuals,
        "building_cells_modified": building_modified,
        "retained_basin_cells_modified": basin_modified,
    }

    return working_dem, diag


def build_conditioning(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    """Burn roads/buildings/waterways into DEM, apply depression-aware D4 conditioning."""
    grid, grid_diag = build_grid(cfg, repo_root)
    buffer_m = float(cfg["dem"]["buffer_m"])
    buffered_grid = grid.buffered(buffer_m)
    buf_cells = round(buffer_m / grid.resolution)

    dem_path = _require(
        repo_root / cfg["paths"]["interim_dem_dir"] / "dem_10m_buffered.tif", "fetch.py"
    )
    dem_manifest_path = _require(repo_root / "runs" / "terrain_dem" / "manifest.json", "fetch.py")
    dem_manifest = json.loads(dem_manifest_path.read_text())
    dem_is_dtm = bool(dem_manifest["dem_is_dtm"])
    dem_source = dem_manifest["dem_source"]
    nodata = float(dem_manifest["nodata_value"])

    treatment = cfg["buildings"]["treatment"]
    porosity_factor = float(cfg["buildings"]["porosity_conveyance_factor"])

    interim_terrain = repo_root / cfg["paths"]["interim_terrain_dir"]
    roads_path = _require(interim_terrain / "roads_centrelines.gpkg", "roads.py")
    waterways_path = _require(interim_terrain / "waterways.gpkg", "drains.py")
    building_mask_path = _require(interim_terrain / "building_mask_buffered.tif", "buildings.py")
    building_delta_path = _require(interim_terrain / "building_height_delta_buffered.tif", "buildings.py")

    with rasterio.open(dem_path) as src:
        buffered_grid.assert_aligned(src)
        original = src.read(1)
        profile = src.profile.copy()

    valid_mask = original != nodata

    road_depth = float(cfg["conditioning"]["road_burn_depth_operating_point_m"])
    drain_depth = float(cfg["conditioning"]["drain_burn_depth_operating_point_m"])

    road_mask = rasterize_burn_mask(roads_path, buffered_grid)
    waterway_mask = rasterize_burn_mask(waterways_path, buffered_grid)

    # Precedence: waterway > road > building
    lowering = np.zeros_like(original)
    lowering = np.where(road_mask == 1, road_depth, lowering)
    lowering = np.where(waterway_mask == 1, drain_depth, lowering)
    lowering = np.where(valid_mask, lowering, 0.0)
    conditioned = original - lowering

    n_road_burned = int(((road_mask == 1) & valid_mask).sum())
    n_waterway_burned = int(((waterway_mask == 1) & valid_mask).sum())

    n_building_cells = 0
    n_conveyance_reduced_cells = 0
    conveyance_factor = np.ones_like(original, dtype=np.float32)
    building_override = np.zeros_like(valid_mask)

    if dem_is_dtm:
        with rasterio.open(building_mask_path) as src:
            buffered_grid.assert_aligned(src)
            building_mask_buf = src.read(1)
        with rasterio.open(building_delta_path) as src:
            buffered_grid.assert_aligned(src)
            building_delta_buf = src.read(1)

        valid_building = (building_mask_buf == 1) & valid_mask
        claimed_by_conveyance = (road_mask == 1) | (waterway_mask == 1)
        contested = valid_building & claimed_by_conveyance
        uncontested = valid_building & ~claimed_by_conveyance

        if treatment == "blockage":
            building_override = uncontested
            conditioned = np.where(building_override, original + building_delta_buf, conditioned)

        reduced = contested if treatment == "blockage" else valid_building
        conveyance_factor = np.where(reduced, np.float32(porosity_factor), conveyance_factor)
        n_building_cells = int(building_override.sum())
        n_conveyance_reduced_cells = int(reduced.sum())

    conveyance_factor = np.where(valid_mask, conveyance_factor, np.float32(nodata))
    conditioned = np.where(valid_mask, conditioned, nodata)

    interim_terrain.mkdir(parents=True, exist_ok=True)
    prebreach_path = interim_terrain / "dem_conditioned_prebreach.tif"
    profile.update(dtype="float32", nodata=nodata, compress="deflate")
    with atomic_output_path(prebreach_path) as tmp:
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(conditioned.astype(np.float32), 1)

    conveyance_path = interim_terrain / "building_conveyance_factor.tif"
    with atomic_output_path(conveyance_path) as tmp:
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(conveyance_factor.astype(np.float32), 1)

    # ── Depression-Aware D4 Conditioning ──────────────────────────────
    # 1. Run / load depression classification and register
    register_csv_path = interim_terrain / "retained_basins.csv"
    class_raster_buf_path = interim_terrain / "basin_class_buffered.tif"
    if not register_csv_path.exists() or not class_raster_buf_path.exists():
        build_depressions(cfg, repo_root)

    with rasterio.open(class_raster_buf_path) as src:
        buffered_grid.assert_aligned(src)
        basin_class_buf = src.read(1)

    # Retained basins mask (all registered candidates: Storage, Quarry, Landfill, Uncertain)
    retained_basin_mask = (basin_class_buf > 0) & valid_mask

    # Full building mask (ALL building cells, regardless of conveyance override)
    with rasterio.open(building_mask_path) as src:
        buffered_grid.assert_aligned(src)
        raw_b_mask = src.read(1)
    full_building_mask = (raw_b_mask == 1) & valid_mask

    # Cost surface for carving outside retained basins:
    # waterway (1) -> road (2) -> bare ground (3) -> building (999, strictly forbidden)
    cost_surface = np.full_like(conditioned, 3, dtype=np.int32)
    cost_surface[road_mask == 1] = 2
    cost_surface[waterway_mask == 1] = 1
    cost_surface[full_building_mask] = 999  # STRICTLY FORBIDDEN

    post_breach, d4_diag = d4_capped_hybrid_conditioning(
        dem=conditioned,
        valid_mask=valid_mask,
        retained_basin_mask=retained_basin_mask,
        cost_surface=cost_surface,
        building_mask=full_building_mask,
        cut_max=CUT_MAX_M,
        fill_max=FILL_MAX_M,
    )

    # 2. Enumerate remaining residuals into register and class raster
    d4_pits_mask = _find_d4_pits(post_breach, valid_mask)
    residual_pits = d4_pits_mask & ~retained_basin_mask
    n_residuals = int(residual_pits.sum())

    if n_residuals > 0:
        basin_class_buf[residual_pits] = CLASS_UNCERTAIN
        retained_basin_mask = (basin_class_buf > 0) & valid_mask

        # Re-save updated basin_class_buffered.tif
        profile_class_buf = buffered_grid.profile(dtype="uint8", nodata=0)
        with atomic_output_path(class_raster_buf_path) as tmp:
            with rasterio.open(tmp, "w", **profile_class_buf) as dst:
                dst.write(basin_class_buf.astype(np.uint8), 1)

        # Re-save updated canonical basin_class.tif
        class_raster_canon = basin_class_buf[
            buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width
        ]
        profile_class_canon = grid.profile(dtype="uint8", nodata=0)
        class_raster_canon_path = interim_terrain / "basin_class.tif"
        with atomic_output_path(class_raster_canon_path) as tmp:
            with rasterio.open(tmp, "w", **profile_class_canon) as dst:
                dst.write(class_raster_canon.astype(np.uint8), 1)

    postbreach_path = interim_terrain / "dem_conditioned_postbreach.tif"
    with atomic_output_path(postbreach_path) as tmp:
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(post_breach.astype(np.float32), 1)

    # Pits inventory
    n_pits_pre = count_pits(conditioned, valid_mask)
    n_pits_post_d8 = count_pits(post_breach, valid_mask)
    n_d4_pits_pre = count_pits_d4(conditioned, valid_mask)
    n_d4_pits_post = count_pits_d4(post_breach, valid_mask)

    d4_pits_final = _find_d4_pits(post_breach, valid_mask)
    pits_inside_retained = int((d4_pits_final & retained_basin_mask).sum())
    pits_outside_retained = int((d4_pits_final & ~retained_basin_mask).sum())

    diff = post_breach - conditioned
    n_cells_changed = int((np.abs(diff) > 1e-4).sum())
    n_cells_raised = int((diff > 1e-4).sum())
    volume_changed_m3 = float(np.abs(diff[valid_mask]).sum() * grid.resolution * grid.resolution)

    return {
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "buffer_m": buffer_m,
        "buffered_grid": buffered_grid.to_manifest_dict(),
        "dem_source": dem_source,
        "dem_is_dtm": dem_is_dtm,
        "connectivity": CONNECTIVITY_D4,
        "conditioning_method": "depression_aware_d4_capped_hybrid",
        "cut_max_m": CUT_MAX_M,
        "fill_max_m": FILL_MAX_M,
        "road_burn_depth_m": road_depth,
        "drain_burn_depth_m": drain_depth,
        "n_road_burned_cells": n_road_burned,
        "n_waterway_burned_cells": n_waterway_burned,
        "n_building_override_cells": n_building_cells,
        "buildings_treatment": treatment,
        "buildings_treatment_applied": treatment if dem_is_dtm else None,
        "porosity_conveyance_factor": porosity_factor,
        "n_building_conveyance_reduced_cells": n_conveyance_reduced_cells,
        "building_conveyance_factor_path": str(conveyance_path.relative_to(repo_root)),
        "n_pits_pre_breach": n_pits_pre,
        "n_pits_post_d8_breach": n_pits_post_d8,
        "n_d4_pits_pre_conditioning": n_d4_pits_pre,
        "n_d4_pits_post_conditioning": n_d4_pits_post,
        "pits_inside_retained_basins": pits_inside_retained,
        "pits_outside_retained_basins": pits_outside_retained,
        "d4_capped_hybrid_diagnostics": d4_diag,
        "n_cells_changed_by_breach": n_cells_changed,
        "n_cells_RAISED_by_breach": n_cells_raised,
        "volume_changed_m3": volume_changed_m3,
        "prebreach_path": str(prebreach_path.relative_to(repo_root)),
        "postbreach_path": str(postbreach_path.relative_to(repo_root)),
        "deviations": [
            "Bounded filling up to 0.25m deviates from PROMPT.md §6.1 prefer-breaching "
            "to prevent destructive 22m trench carving on bare ground while preserving "
            "burned conveyance channels."
        ],
    }


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_conditioning", help="Directory to write the manifest into"
    ),
) -> None:
    """Burn roads/buildings/waterways into DEM, apply depression-aware D4 conditioning."""
    cfg = load_config(config)
    try:
        result = run_stage(
            "phase1_terrain_conditioning", build_conditioning, cfg, config, REPO, out
        )
    except ConditioningError as e:
        typer.echo(f"\nFATAL: {e}")
        raise typer.Exit(code=1) from e

    typer.echo(f"DEM source: {result['dem_source']}  dem_is_dtm={result['dem_is_dtm']}")
    typer.echo(f"Connectivity: {result['connectivity']}  Method: {result['conditioning_method']}")
    typer.echo(
        f"burned: roads={result['n_road_burned_cells']:,} cells @ "
        f"{result['road_burn_depth_m']}m   waterways={result['n_waterway_burned_cells']:,} "
        f"cells @ {result['drain_burn_depth_m']}m   "
        f"buildings={result['n_building_override_cells']:,} cells"
    )
    d4 = result["d4_capped_hybrid_diagnostics"]
    typer.echo(
        f"D4 conditioning: {d4['n_cells_modified']:,} cells modified "
        f"(carved: {d4['n_cells_cut']:,} cells, max cut {d4['max_cut_m']:.3f}m | "
        f"filled: {d4['n_cells_filled']:,} cells, max fill {d4['max_fill_m']:.3f}m)"
    )
    typer.echo(
        f"D4 pits: {result['n_d4_pits_pre_conditioning']:,} -> {result['n_d4_pits_post_conditioning']:,} "
        f"(inside retained basins: {result['pits_inside_retained_basins']:,}, "
        f"outside retained: {result['pits_outside_retained_basins']:,})"
    )
    typer.echo(f"wrote {result['prebreach_path']}, {result['postbreach_path']}")
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
