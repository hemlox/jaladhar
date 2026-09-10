"""missing) so re-running this after a partial failure resumes rather than
-> conditioning -> derived -> [this module] assemble + QC
PROMPT.md §6.1's acceptance text (elevation, building mask, Manning's n,
directly, not recomputed, per `derived.py`'s scope note). All copied or
question B measured that the untiled canonical grid uses 989 MiB of 8188
the manifest (empty, one whole-domain "tile" covering the full extent)
rather than fabricating a tiling scheme this session never used."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
import typer

from jaladhar.terrain.buildings import BuildingFetchError, build_buildings
from jaladhar.terrain.conditioning import ConditioningError, build_conditioning
from jaladhar.terrain.depressions import DepressionError
from jaladhar.terrain.derived import DerivedLayerError, build_derived
from jaladhar.terrain.drains import DrainFetchError, build_drains
from jaladhar.terrain.fetch import DEMSourceError, fetch_dem
from jaladhar.terrain.grid import Grid, build_grid, build_grid_result, load_config, run_stage
from jaladhar.terrain.roads import RoadFetchError, build_roads
from jaladhar.terrain.roughness import RoughnessFetchError, build_roughness
from jaladhar.terrain.water import WaterFetchError, build_water

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


class BuildError(Exception):
    """Per CLAUDE.md rule 1: stop and report."""


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


# (source path relative to repo root, final stack filename, "copy" straight
STACK_LAYERS: list[tuple[str, str, str]] = [
    ("data/interim/terrain/dem_conditioned_postbreach.tif", "elevation.tif", "crop"),
    ("data/interim/terrain/building_mask.tif", "building_mask.tif", "copy"),
    ("data/interim/terrain/building_height_delta.tif", "building_height_delta.tif", "copy"),
    (
        "data/interim/terrain/building_conveyance_factor.tif",
        "building_conveyance_factor.tif",
        "crop",
    ),
    ("data/interim/terrain/manning_n.tif", "manning_n.tif", "copy"),
    ("data/interim/terrain/drain_capacity.tif", "drain_capacity.tif", "copy"),
    ("data/interim/terrain/distance_to_drain.tif", "distance_to_drain.tif", "copy"),
    ("data/interim/terrain/road_segment_id.tif", "road_segment_id.tif", "copy"),
    ("data/interim/terrain/slope.tif", "slope.tif", "copy"),
    ("data/interim/terrain/flow_accumulation.tif", "flow_accumulation.tif", "copy"),
    ("data/interim/terrain/basin_class.tif", "basin_class.tif", "copy"),
]

BUFFERED_STACK_LAYERS: list[tuple[str, str, str]] = [
    ("data/interim/terrain/dem_conditioned_postbreach.tif", "elevation.tif", "copy"),
    ("data/interim/terrain/building_mask_buffered.tif", "building_mask.tif", "copy"),
    (
        "data/interim/terrain/building_height_delta_buffered.tif",
        "building_height_delta.tif",
        "copy",
    ),
    (
        "data/interim/terrain/building_conveyance_factor.tif",
        "building_conveyance_factor.tif",
        "copy",
    ),
    ("data/interim/terrain/manning_n_buffered.tif", "manning_n.tif", "copy"),
    ("data/interim/terrain/drain_capacity_buffered.tif", "drain_capacity.tif", "copy"),
    ("data/interim/terrain/distance_to_drain_buffered.tif", "distance_to_drain.tif", "copy"),
    ("data/interim/terrain/road_segment_id_buffered.tif", "road_segment_id.tif", "copy"),
    ("data/interim/terrain/slope_buffered.tif", "slope.tif", "copy"),
    ("data/interim/terrain/flow_accumulation_buffered.tif", "flow_accumulation.tif", "copy"),
    ("data/interim/terrain/basin_class_buffered.tif", "basin_class.tif", "copy"),
]


def assemble_stack(cfg: dict[str, Any], grid: Grid, repo_root: Path) -> dict[str, Any]:
    buffer_m = float(cfg["dem"]["buffer_m"])
    buffered_grid = grid.buffered(buffer_m)
    buf_cells = round(buffer_m / grid.resolution)

    processed_dir = repo_root / cfg["paths"]["processed_dir"]
    processed_dir.mkdir(parents=True, exist_ok=True)
    buffered_dir = processed_dir / "buffered"
    buffered_dir.mkdir(parents=True, exist_ok=True)

    canonical_reports: list[dict[str, Any]] = []
    for src_rel, dst_name, mode in STACK_LAYERS:
        src_path = repo_root / src_rel
        if not src_path.exists():
            raise BuildError(f"missing stage output: {src_path} (dst would be {dst_name})")
        dst_path = processed_dir / dst_name

        with rasterio.open(src_path) as src:
            if mode == "crop":
                arr = src.read(1)
                cropped = arr[
                    buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width
                ]
                profile = src.profile.copy()
                profile.update(width=grid.width, height=grid.height, transform=grid.transform)
                with rasterio.open(dst_path, "w", **profile) as dst:
                    dst.write(cropped, 1)
            else:
                shutil.copy2(src_path, dst_path)

        with rasterio.open(dst_path) as check:
            grid.assert_aligned(check)
            arr = check.read(1)
            nodata = check.nodata
            interior_nan = bool(np.isnan(arr).any())
            n_nodata = int((arr == nodata).sum()) if nodata is not None else 0

        canonical_reports.append(
            {
                "name": dst_name,
                "source": src_rel,
                "mode": mode,
                "dtype": str(arr.dtype),
                "contains_nan": interior_nan,
                "n_nodata_cells": n_nodata,
                "min": float(np.nanmin(arr)),
                "max": float(np.nanmax(arr)),
            }
        )

    buffered_reports: list[dict[str, Any]] = []
    for src_rel, dst_name, _mode in BUFFERED_STACK_LAYERS:
        src_path = repo_root / src_rel
        if not src_path.exists():
            raise BuildError(f"missing buffered stage output: {src_path} (dst would be {dst_name})")
        dst_path = buffered_dir / dst_name

        shutil.copy2(src_path, dst_path)

        with rasterio.open(dst_path) as check:
            buffered_grid.assert_aligned(check)
            arr = check.read(1)
            nodata = check.nodata
            interior_nan = bool(np.isnan(arr).any())
            n_nodata = int((arr == nodata).sum()) if nodata is not None else 0

        buffered_reports.append(
            {
                "name": dst_name,
                "source": src_rel,
                "construction": "built_over_buffered_extent",
                "dtype": str(arr.dtype),
                "contains_nan": interior_nan,
                "n_nodata_cells": n_nodata,
                "min": float(np.nanmin(arr)),
                "max": float(np.nanmax(arr)),
            }
        )

    return {
        "processed_dir": str(processed_dir.relative_to(repo_root)),
        "buffered_dir": str(buffered_dir.relative_to(repo_root)),
        "construction_mode": "built_over_buffered_extent_no_padding",
        "layers": canonical_reports,
        "buffered_layers": buffered_reports,
    }


def write_qc_figures(cfg: dict[str, Any], repo_root: Path) -> list[str]:
    qc_dir = repo_root / cfg["paths"]["qc_dir"]
    qc_dir.mkdir(parents=True, exist_ok=True)
    processed_dir = repo_root / cfg["paths"]["processed_dir"]

    written: list[str] = []
    layer_files = sorted(processed_dir.glob("*.tif"))
    n = len(layer_files)
    ncols = 3
    nrows = -(-n // ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 4.5 * nrows))
    axes_flat = np.array(axes).reshape(-1)

    for i, layer_path in enumerate(layer_files):
        with rasterio.open(layer_path) as ds:
            arr = ds.read(1)
            nodata = ds.nodata
        display = np.ma.masked_equal(arr, nodata) if nodata is not None else arr

        single_fig, single_ax = plt.subplots(figsize=(8, 7))
        im = single_ax.imshow(display, cmap="viridis")
        single_ax.set_title(layer_path.stem)
        plt.colorbar(im, ax=single_ax, shrink=0.8)
        single_path = qc_dir / f"{layer_path.stem}.png"
        single_fig.savefig(single_path, dpi=100, bbox_inches="tight")
        plt.close(single_fig)
        written.append(str(single_path.relative_to(repo_root)))

        im2 = axes_flat[i].imshow(display, cmap="viridis")
        axes_flat[i].set_title(layer_path.stem, fontsize=9)
        axes_flat[i].axis("off")
        plt.colorbar(im2, ax=axes_flat[i], shrink=0.7)

    for j in range(n, len(axes_flat)):
        axes_flat[j].axis("off")

    fig.suptitle("JALADHAR terrain stack — whole-city mosaic (eyeball for seams/voids)")
    mosaic_path = qc_dir / "mosaic_all_layers.png"
    fig.savefig(mosaic_path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    written.append(str(mosaic_path.relative_to(repo_root)))
    return written


def run_all_stages(cfg: dict[str, Any], config_path: Path, repo_root: Path) -> None:
    """re-running after a partial failure skips whatever's already cached/
    stage's artifact on disk but writes no manifest, so a later stage
    (`conditioning.py` reading `dem_is_dtm` from `fetch.py`'s manifest) could"""
    typer.echo("=== grid ===")
    run_stage(
        "phase1_terrain_grid",
        build_grid_result,
        cfg,
        config_path,
        repo_root,
        repo_root / "runs" / "terrain_grid",
    )
    typer.echo("=== fetch (DEM) ===")
    run_stage(
        "phase1_terrain_fetch_dem",
        fetch_dem,
        cfg,
        config_path,
        repo_root,
        repo_root / "runs" / "terrain_dem",
    )
    typer.echo("=== water ===")
    run_stage(
        "phase1_terrain_water",
        build_water,
        cfg,
        config_path,
        repo_root,
        repo_root / "runs" / "terrain_water",
    )
    typer.echo("=== roads ===")
    run_stage(
        "phase1_terrain_roads",
        build_roads,
        cfg,
        config_path,
        repo_root,
        repo_root / "runs" / "terrain_roads",
    )
    typer.echo("=== buildings ===")
    run_stage(
        "phase1_terrain_buildings",
        build_buildings,
        cfg,
        config_path,
        repo_root,
        repo_root / "runs" / "terrain_buildings",
    )
    typer.echo("=== roughness ===")
    run_stage(
        "phase1_terrain_roughness",
        build_roughness,
        cfg,
        config_path,
        repo_root,
        repo_root / "runs" / "terrain_roughness",
    )
    typer.echo("=== drains ===")
    run_stage(
        "phase1_terrain_drains",
        build_drains,
        cfg,
        config_path,
        repo_root,
        repo_root / "runs" / "terrain_drains",
    )
    typer.echo("=== conditioning ===")
    run_stage(
        "phase1_terrain_conditioning",
        build_conditioning,
        cfg,
        config_path,
        repo_root,
        repo_root / "runs" / "terrain_conditioning",
    )
    typer.echo("=== derived ===")
    run_stage(
        "phase1_terrain_derived",
        build_derived,
        cfg,
        config_path,
        repo_root,
        repo_root / "runs" / "terrain_derived",
    )


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    skip_stages: bool = typer.Option(
        False,
        "--skip-stages",
        help="Skip re-running upstream stages; assemble from whatever already exists "
        "in data/interim/ (for iterating on assembly/QC alone without re-fetching).",
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_build", help="Directory to write the manifest into"
    ),
) -> None:
    t0 = time.perf_counter()
    cfg = load_config(config)

    # that hit a missing upstream artifact tracebacked unhandled instead of
    try:
        if not skip_stages:
            run_all_stages(cfg, config, REPO)
        grid, grid_diag = build_grid(cfg, REPO)
        stack_result = assemble_stack(cfg, grid, REPO)
    except (
        DEMSourceError,
        RoadFetchError,
        BuildingFetchError,
        RoughnessFetchError,
        DrainFetchError,
        WaterFetchError,
        DepressionError,
        ValueError,
        ConditioningError,
        DerivedLayerError,
        BuildError,
    ) as e:
        typer.echo(f"\nFATAL: {type(e).__name__}: {e}")
        raise typer.Exit(code=1) from e

    qc_files = write_qc_figures(cfg, REPO)
    wall_clock = time.perf_counter() - t0

    typer.echo(
        f"\nassembled {len(stack_result['layers'])} layers into {stack_result['processed_dir']}"
    )
    for layer in stack_result["layers"]:
        typer.echo(
            f"  {layer['name']:28} dtype={layer['dtype']:8} "
            f"nan={layer['contains_nan']}  nodata_cells={layer['n_nodata_cells']:,}  "
            f"range=[{layer['min']:.3f}, {layer['max']:.3f}]"
        )
    typer.echo(f"wrote {len(qc_files)} QC figures to {REPO / cfg['paths']['qc_dir']}")

    manifest = {
        "stage": "phase1_terrain_build",
        "git_sha": git_sha(),
        "config_path": str(config),
        "wall_clock_sec": wall_clock,
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "tiling": "UNTILED — deliberate, benchmarked deviation from PROMPT.md §6's "
        "literal 'tiled' text; see OPEN-ITEMS.md question B (989/8188 MiB VRAM "
        "at canonical resolution, full-city forward simulation needs no tiling)",
        "tile_index": [
            {
                "tile_id": "whole_domain",
                "bounds": list(grid.bounds),
                "width": grid.width,
                "height": grid.height,
            }
        ],
        "stack": stack_result,
        "qc_figures": qc_files,
    }
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    typer.echo(f"\nwrote {manifest_path}")


if __name__ == "__main__":
    app()
