"""Slope and flow accumulation for the JALADHAR terrain pipeline.

Both computed from the CONDITIONED (post-breach) DEM `conditioning.py`
wrote — burned structure and a pit-free surface both matter here: slope on
an unconditioned DEM would miss the 10 m street/drain structure the whole
point of conditioning is to add, and flow accumulation on an un-breached
DEM would pool mass at every unresolved pit instead of routing it.

Computed on the BUFFERED grid (matching the margin `fetch.py`/
`conditioning.py` already carry), then CROPPED to the canonical grid for
the final stack layer — the buffer's entire purpose is giving flow
accumulation correct upstream areas at the true domain edge (invariant 10);
cropping only after that computation is what actually uses the buffer for
something, rather than fetching it and never spending it.

SCOPE NOTE: distance-to-drain is NOT computed here despite being named
alongside slope/flow-accumulation in an earlier planning pass. Checked
directly against PROMPT.md before writing this module: §6.1's Phase 1
ACCEPTANCE list (elevation, building mask, Manning's n, drain sink
capacity, road segment IDs) does not include it at all; it appears only in
§10 as a Phase-5 surrogate input channel, and `drains.py` already produces
a correctly-gridded, canonical-CRS distance-to-drain raster computed from
real waterway geometry. Recomputing a second, DIFFERENT distance-to-drain
here (e.g. flow-path distance rather than drains.py's straight-line EDT)
would be undocumented scope creep, not a requirement — `build.py`'s
assembly step reuses drains.py's existing raster directly.

Both whitebox tools operate on DEM input directly (no separate flow-
pointer step needed for `d8_flow_accumulation`) and, like
`conditioning.py`'s breach step, write float64 regardless of a float32
input — same fix applied here for the same reason (PROMPT.md §7: float32
throughout; float64 isn't needed and wastes memory at these grid sizes).
"""

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
    """A required upstream artefact is missing, or a whitebox tool failed.

    Per CLAUDE.md rule 1: stop and report.
    """


def _require(path: Path, produced_by: str) -> Path:
    if not path.exists():
        raise DerivedLayerError(f"required input {path} does not exist — run `{produced_by}` first")
    return path


def _crop_to_canonical(buffered_arr: np.ndarray, buf_cells: int, grid: Grid) -> np.ndarray:
    """Extract the canonical-grid window from a buffered-grid array.

    Inverse of `conditioning.py`'s `_embed_canonical_into_buffered` — the
    offset is exactly `buf_cells` by the same `Grid.buffered()` guarantee.
    """
    return buffered_arr[buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width]


def _run_whitebox_and_load_float32(
    wbt: whitebox.WhiteboxTools, tool_name: str, kwargs: dict[str, Any], output_path: Path
) -> tuple[np.ndarray, str, float | None]:
    """Run a whitebox tool, load its output, re-cast to float32 if needed.

    Returns (array, original_dtype_before_any_correction, nodata_value) so
    the caller can record whether a dtype correction happened AND use this
    layer's own actual nodata value. whitebox tools are not guaranteed to
    reuse the input DEM's nodata sentinel — sharing one profile built from
    the DEM's nodata across every derived layer would be the same
    declared-vs-realized mismatch conditioning.py's burn-step nodata fix
    addresses, just one module over.
    """
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
            # Cast the sentinel the same way the pixel data was cast — a
            # nodata value that doesn't round-trip exactly through float32
            # would leave the declared nodata and the actual (now-float32)
            # nodata pixels mismatched by the same margin this dtype fix
            # corrects for ordinary data.
            nodata_value = float(np.float32(nodata_value)) if nodata_value is not None else None
            profile.update(dtype="float32", nodata=nodata_value, compress="deflate")
            with rasterio.open(tmp, "w", **profile) as dst:
                dst.write(arr, 1)
    return arr, original_dtype, nodata_value


def build_derived(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    """Compute slope + flow accumulation from the conditioned DEM, crop to canonical."""
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
    # hydrological computation domain; the true outlet can legitimately sit
    # at the edge of the BUFFER, and an anomalous INTERIOR maximum there
    # (not explained by real confluence) means an unresolved sink is still
    # trapping flow despite conditioning.py's breach step.
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

    # Each layer writes with ITS OWN actual nodata value (from whitebox's
    # own output for that tool), never the conditioned DEM's -9999 — see
    # _run_whitebox_and_load_float32's docstring.
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
    """Compute slope + D8 flow accumulation from the conditioned DEM."""
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
