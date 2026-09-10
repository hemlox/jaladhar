"""DEM would pool mass at every unresolved pit instead of routing it.
accumulation correct upstream areas at the true domain edge (invariant 10);
SCOPE NOTE: distance-to-drain is NOT computed here despite being named
ACCEPTANCE list (elevation, building mask, Manning's n, drain sink
would be undocumented scope creep, not a requirement — `build.py`'s"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import typer
import whitebox

from jaladhar.terrain.grid import Grid, atomic_output_path, build_grid, load_config, run_stage

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


class DerivedLayerError(Exception):
    """Per CLAUDE.md rule 1: stop and report."""


def _require(path: Path, produced_by: str) -> Path:
    if not path.exists():
        raise DerivedLayerError(f"required input {path} does not exist — run `{produced_by}` first")
    return path


def _crop_to_canonical(buffered_arr: np.ndarray, buf_cells: int, grid: Grid) -> np.ndarray:
    return buffered_arr[buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width]


def _run_whitebox_and_load_float32(
    wbt: whitebox.WhiteboxTools, tool_name: str, kwargs: dict[str, Any], output_path: Path
) -> tuple[np.ndarray, str, float | None]:
    """the DEM's nodata across every derived layer would be the same
    declared-vs-realized mismatch conditioning.py's burn-step nodata fix"""
    tool = getattr(wbt, tool_name)
    with atomic_output_path(output_path) as tmp:
        ret = tool(output=str(tmp.resolve()), **kwargs)
        if ret != 0:
            raise DerivedLayerError(f"whitebox.{tool_name} returned nonzero exit code {ret}")
        with rasterio.open(tmp) as src:
            arr = src.read(1)
            original_dtype = src.dtypes[0]
            profile = src.profile.copy()
        nodata_value = profile["nodata"]
        if original_dtype != "float32":
            arr = arr.astype(np.float32)
            nodata_value = float(np.float32(nodata_value)) if nodata_value is not None else None
            profile.update(dtype="float32", nodata=nodata_value, compress="deflate")
            with rasterio.open(tmp, "w", **profile) as dst:
                dst.write(arr, 1)
    return arr, original_dtype, nodata_value


def build_derived(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    grid, grid_diag = build_grid(cfg, repo_root)
    buffer_m = float(cfg["dem"]["buffer_m"])
    buffered_grid = grid.buffered(buffer_m)
    buf_cells = round(buffer_m / grid.resolution)

    interim_terrain = repo_root / cfg["paths"]["interim_terrain_dir"]
    conditioned_path = _require(
        interim_terrain / "dem_conditioned_postbreach.tif", "conditioning.py"
    )

    with rasterio.open(conditioned_path) as src:
        buffered_grid.assert_aligned(src)

    wbt = whitebox.WhiteboxTools()
    wbt.verbose = False

    slope_path_buffered = interim_terrain / "slope_buffered.tif"
    slope_arr, slope_dtype_before, slope_nodata = _run_whitebox_and_load_float32(
        wbt,
        "slope",
        {"dem": str(conditioned_path.resolve()), "units": "degrees"},
        slope_path_buffered,
    )

    flowacc_path_buffered = interim_terrain / "flow_accumulation_buffered.tif"
    flowacc_arr, flowacc_dtype_before, flowacc_nodata = _run_whitebox_and_load_float32(
        wbt,
        "d8_flow_accumulation",
        {"i": str(conditioned_path.resolve()), "out_type": "cells"},
        flowacc_path_buffered,
    )

    # Invariant 9, checked on the BUFFERED result — that is the actual
    max_idx_buffered = np.unravel_index(np.argmax(flowacc_arr), flowacc_arr.shape)
    h, w = flowacc_arr.shape
    max_at_edge = max_idx_buffered[0] in (0, h - 1) or max_idx_buffered[1] in (0, w - 1)

    with rasterio.open(conditioned_path) as src:
        profile_template = src.profile.copy()
    profile_template.update(
        width=grid.width, height=grid.height, transform=grid.transform, dtype="float32"
    )

    slope_canonical = _crop_to_canonical(slope_arr, buf_cells, grid)
    flowacc_canonical = _crop_to_canonical(flowacc_arr, buf_cells, grid)

    slope_profile = {**profile_template, "nodata": slope_nodata}
    flowacc_profile = {**profile_template, "nodata": flowacc_nodata}

    slope_final_path = interim_terrain / "slope.tif"
    with atomic_output_path(slope_final_path) as tmp:
        with rasterio.open(tmp, "w", **slope_profile) as dst:
            dst.write(slope_canonical, 1)

    flowacc_final_path = interim_terrain / "flow_accumulation.tif"
    with atomic_output_path(flowacc_final_path) as tmp:
        with rasterio.open(tmp, "w", **flowacc_profile) as dst:
            dst.write(flowacc_canonical, 1)

    return {
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "buffer_m": buffer_m,
        "slope_units": "degrees",
        "flow_accumulation_out_type": "cells",
        "flow_accumulation_algorithm": "D8",
        "slope_dtype_corrected_from": (
            None if slope_dtype_before == "float32" else slope_dtype_before
        ),
        "flowacc_dtype_corrected_from": (
            None if flowacc_dtype_before == "float32" else flowacc_dtype_before
        ),
        "slope_nodata": slope_nodata,
        "flowacc_nodata": flowacc_nodata,
        "max_flow_accumulation_buffered_cells": float(flowacc_arr.max()),
        "max_flow_accumulation_idx_buffered": [int(max_idx_buffered[0]), int(max_idx_buffered[1])],
        "max_flow_accumulation_at_buffered_edge": bool(max_at_edge),
        "max_flow_accumulation_canonical_cells": float(flowacc_canonical.max()),
        "slope_min_deg": float(slope_canonical.min()),
        "slope_max_deg": float(slope_canonical.max()),
        "slope_mean_deg": float(slope_canonical.mean()),
        "slope_path": str(slope_final_path.relative_to(repo_root)),
        "flow_accumulation_path": str(flowacc_final_path.relative_to(repo_root)),
    }


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_derived", help="Directory to write the manifest into"
    ),
) -> None:
    cfg = load_config(config)
    try:
        result = run_stage("phase1_terrain_derived", build_derived, cfg, config, REPO, out)
    except DerivedLayerError as e:
        typer.echo(f"\nFATAL: {e}")
        raise typer.Exit(code=1) from e

    typer.echo(
        f"slope: [{result['slope_min_deg']:.2f}, {result['slope_max_deg']:.2f}] deg, "
        f"mean {result['slope_mean_deg']:.2f} deg"
    )
    typer.echo(
        f"flow accumulation (D8, cells): max={result['max_flow_accumulation_buffered_cells']:,.0f} "
        f"at buffered-domain idx {result['max_flow_accumulation_idx_buffered']}  "
        f"AT EDGE: {result['max_flow_accumulation_at_buffered_edge']}"
    )
    typer.echo(f"wrote {result['slope_path']}, {result['flow_accumulation_path']}")
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
