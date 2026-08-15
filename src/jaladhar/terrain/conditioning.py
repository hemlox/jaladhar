"""DEM conditioning for the JALADHAR terrain pipeline — the important module.

A 30 m-native DEM cannot resolve a 10 m street. We fix this with
VECTOR-DRIVEN STRUCTURE, not interpolation: burn known road centrelines,
building footprints, and drain channels from OSM/Microsoft vectors into the
elevation surface. The fine structure comes from vector data, not from
inventing elevation (PROMPT.md §6.1) — say exactly that in the docs and in
the pitch.

Exact step order, do not reorder:
  1. Read the buffered DEM `fetch.py` wrote (already resampled to 10 m).
  2. Burn OSM road centrelines DOWN (shallow channels).
  3. Burn OSM waterway=drain|ditch|stream DOWN (deeper than roads — the
     primary rajakaluve channels).
  4. Burn buildings UP as blockages — ONLY IF `dem_is_dtm` (fetch.py's
     manifest flag), AND only on cells neither the road nor the waterway
     burn already claimed. GLO-30 is a Digital SURFACE model with
     buildings already baked into its elevation; burning them again would
     double-count every building's height. The DTM/DSM branch is read from
     the actual manifest fetch.py wrote, never hardcoded. Being LAST in
     this list is computation order, not precedence — see BURN PRECEDENCE
     below; conflating the two is exactly what produced the bug that
     section replaced.
  5. Breach depressions — see "WHICH WHITEBOX TOOL" below, this is not a
     minor implementation detail.

WHICH WHITEBOX TOOL, AND WHY IT MATTERS
----------------------------------------
`whitebox.breach_depressions_least_cost` — the function named in earlier
planning for this module — was empirically tested against a synthetic pit
on real DEM data before being used here, and REJECTED: even with its
`fill=False` argument, it raised the pit cell itself by several metres
(929.97 -> 936.78 m in the test) while also lowering other cells. It is a
breach+fill HYBRID by design (matching the title of the Lindsay (2016)
paper it implements, "hybrid breaching-filling sink removal"); `fill=False`
only suppresses an ADDITIONAL final fill pass for depressions the hybrid
step didn't resolve, not the raising that can happen during the hybrid
step itself.

`whitebox.breach_depressions` (the plain, non-hybrid function, also
Lindsay 2016) was tested identically and behaves as the plan requires: the
synthetic pit cell was completely UNCHANGED, zero cells were raised, and
256 cells were lowered carving an actual monotonic channel out of the
depression. `fill_pits=False` (its own default) leaves any depression a
breach path cannot resolve as a genuine, honestly-reported pit rather than
silently raising it — exactly "prefer breaching over filling... it
preserves flow paths" (PROMPT.md §6.1 item 3).

THE PRE-BREACH / POST-BREACH ARTEFACT
--------------------------------------
Both the burned-but-unbreached raster and the final breached raster are
written to disk. A later invariant check ("post-breach <= pre-breach
everywhere it changed") MUST compare these two specifically — comparing
the final output against the ORIGINAL fetched DEM would be wrong and would
falsely fail, because step 3 legitimately RAISES building cells above the
original grade whenever `dem_is_dtm` is true.

BURN PRECEDENCE — waterway > road > building, EXPLICIT AND NOT A SIDE
EFFECT OF STATEMENT ORDER
-----------------------------------------------------------------------
Where more than one of {road, waterway, building} would touch the same
cell, precedence is decided explicitly, never by which burn happens to be
written last in the code:

  1. WATERWAY wins first. Even where a building has physically encroached
     on a rajakaluve — a real, common problem in this city, not a
     hypothetical (PROMPT.md §14.2) — the model represents that as the
     channel being CONSTRICTED or OVERTOPPED by the encroachment, not as
     the channel ceasing to exist. Backfilling a drain to building height
     because a building sits on it would erase the one flow path most
     likely to matter during a flood.
  2. ROAD wins second — over building, never over waterway. Buildings
     routinely front directly onto a road's centreline; at 10 m
     resolution with an all_touched line burn, real road/building overlap
     is common (~12% of road cells on a real run), not an edge case. A
     road cell must stay a conveyance path — a building sitting at the
     property line next to a street does not mean the STREET is
     structurally blocked.
  3. BUILDING applies ONLY on a cell neither of the above claims. Still an
     ABSOLUTE override computed from the ORIGINAL grade (`original +
     height`), same as before — the only change is WHICH cells it is
     allowed to claim.

This is a deliberate reversal of an earlier, undocumented version of this
module, in which building — burned last in COMPUTATION order — always won
on overlap ("last writer wins"). That was never a stated design decision;
it was an artefact of statement order that happened to look like one,
caught by a Phase 1 review pass. Never re-derive "who wins" from which
np.where() call happens to run last — the code below encodes precedence
explicitly (an independent `claimed_by_conveyance` mask for the building
step) precisely so a future reordering of these steps cannot silently
change which layer wins.

Road and waterway are combined between EACH OTHER as a plain sequential
override too, but the DIRECTION is fixed independent of the two burn
DEPTHS: waterway's assignment runs after road's and unconditionally
overwrites it on overlap. This is deliberate — do not "simplify" it back
to `np.maximum(road_depth, drain_depth)`; that reads as equivalent only
because `drain_depth` currently happens to exceed `road_depth` in config,
and would silently invert precedence the moment that configured
relationship ever changed. "depth below natural grade" is also not
physically additive across two features occupying the same cell, which is
why this is an override, never a sum.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
import typer
import whitebox
from rasterio.features import rasterize

from jaladhar.terrain.grid import Grid, atomic_output_path, build_grid, load_config, run_stage

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


class ConditioningError(Exception):
    """A required upstream artefact is missing or unusable.

    Per CLAUDE.md rule 1: stop and report — this module must never
    substitute a placeholder for a genuinely missing sibling-module output.
    """


def _require(path: Path, produced_by: str) -> Path:
    if not path.exists():
        raise ConditioningError(f"required input {path} does not exist — run `{produced_by}` first")
    return path


def _embed_canonical_into_buffered(
    canonical_arr: np.ndarray, buf_cells: int, buffered_shape: tuple[int, int], fill_value: Any
) -> np.ndarray:
    """Pad a canonical-grid array into the larger buffered-grid lattice.

    The offset is EXACTLY `buf_cells` on every side by construction of
    `Grid.buffered()` (same lattice, widened by an exact multiple of the
    resolution) — not a heuristic alignment, a guaranteed one.
    """
    out = np.full(buffered_shape, fill_value, dtype=canonical_arr.dtype)
    h, w = canonical_arr.shape
    out[buf_cells : buf_cells + h, buf_cells : buf_cells + w] = canonical_arr
    return out


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
        all_touched=True,  # line burns need connected 1-cell-wide paths, same
        # reasoning as roads.py/drains.py's own rasterization
        dtype="uint8",
    )
    return mask


def count_pits(elevation: np.ndarray, valid_mask: np.ndarray) -> int:
    """Count 8-connected local minima strictly inside the valid interior.

    A pit: every one of its 8 neighbours is strictly higher, AND all 8
    neighbours are themselves valid (non-nodata) cells — the raster border
    and any cell adjacent to nodata are excluded, since "surrounded by
    higher ground" is undefined there, not falsely counted either way.
    """
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


def build_conditioning(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    """Burn roads/buildings/waterways, breach depressions, write pre/post artefacts."""
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
    if treatment not in ("blockage", "porosity"):
        raise ConditioningError(
            f"buildings.treatment={treatment!r} not implemented in conditioning.py "
            "(only 'blockage' and 'porosity' are)"
        )
    porosity_factor = float(cfg["buildings"]["porosity_conveyance_factor"])

    interim_terrain = repo_root / cfg["paths"]["interim_terrain_dir"]
    roads_path = _require(interim_terrain / "roads_centrelines.gpkg", "roads.py")
    waterways_path = _require(interim_terrain / "waterways.gpkg", "drains.py")
    building_mask_path = _require(interim_terrain / "building_mask.tif", "buildings.py")
    building_delta_path = _require(interim_terrain / "building_height_delta.tif", "buildings.py")

    with rasterio.open(dem_path) as src:
        buffered_grid.assert_aligned(src)
        original = src.read(1)
        profile = src.profile.copy()

    valid_mask = original != nodata

    road_depth = float(cfg["conditioning"]["road_burn_depth_operating_point_m"])
    drain_depth = float(cfg["conditioning"]["drain_burn_depth_operating_point_m"])

    road_mask = rasterize_burn_mask(roads_path, buffered_grid)
    waterway_mask = rasterize_burn_mask(waterways_path, buffered_grid)

    # BURN PRECEDENCE (module docstring): waterway's assignment runs after
    # road's and unconditionally overwrites it on overlap — explicit
    # precedence, not np.maximum(road_depth, drain_depth). The maximum
    # would happen to read as equivalent only because drain_depth exceeds
    # road_depth in the current config, and would silently invert
    # precedence the moment that configured relationship ever changed.
    lowering = np.zeros_like(original)
    lowering = np.where(road_mask == 1, road_depth, lowering)
    lowering = np.where(waterway_mask == 1, drain_depth, lowering)
    # A burn mask is pure geometry (rasterized centrelines) and knows
    # nothing about DEM validity — where a line crosses a nodata cell
    # (buffer edge, mosaic seam), a naive subtraction turns a sentinel like
    # -9999 into -9999.15, which no longer reads as nodata to anything
    # downstream checking `== nodata`, even though the file's header still
    # declares that nodata value.
    lowering = np.where(valid_mask, lowering, 0.0)
    conditioned = original - lowering

    n_road_burned = int(((road_mask == 1) & valid_mask).sum())
    n_waterway_burned = int(((waterway_mask == 1) & valid_mask).sum())

    n_building_cells = 0
    n_conveyance_reduced_cells = 0
    conveyance_factor = np.ones_like(original, dtype=np.float32)
    if dem_is_dtm:
        with rasterio.open(building_mask_path) as src:
            canonical_mask = src.read(1)
        with rasterio.open(building_delta_path) as src:
            canonical_delta = src.read(1)
        building_mask_buf = _embed_canonical_into_buffered(
            canonical_mask, buf_cells, original.shape, np.uint8(0)
        )
        building_delta_buf = _embed_canonical_into_buffered(
            canonical_delta, buf_cells, original.shape, np.float32(0.0)
        )
        # Same nodata reasoning as the lowering mask above: a building
        # footprint is independent geometry and can rasterize onto a
        # nodata DEM cell; an absolute override (original + delta)
        # computed from a nodata sentinel is not a real elevation and must
        # not be written as one.
        valid_building = (building_mask_buf == 1) & valid_mask

        # BURN PRECEDENCE (module docstring): a building claims a cell for
        # ELEVATION purposes only if NEITHER the road NOR the waterway
        # burn already claims it — waterway and road are conveyance and
        # must stay passable, REGARDLESS of buildings.treatment. A
        # "contested" cell (building AND road/waterway) never gets its
        # elevation raised, under either treatment.
        claimed_by_conveyance = (road_mask == 1) | (waterway_mask == 1)
        contested = valid_building & claimed_by_conveyance
        uncontested = valid_building & ~claimed_by_conveyance

        if treatment == "blockage":
            building_override = uncontested
            conditioned = np.where(building_override, original + building_delta_buf, conditioned)
        else:  # "porosity" — validated above, no other value reaches here
            # Buildings never raise elevation under porosity treatment —
            # not just on contested cells — so every valid building cell's
            # effect lives in conveyance_factor instead of conditioned.
            building_override = np.zeros_like(valid_building)

        # D4: a contested cell's building is otherwise INVISIBLE to Phase 2
        # once elevation stops representing it — the building still
        # physically exists and still constricts real flow through that
        # cell. Recorded here as a genuine separate conveyance-reduction
        # layer (never folded into elevation, never silently dropped):
        # `porosity_conveyance_factor` is a documented, arbitrary Phase 3
        # calibration starting point (config comment), same status as
        # drains.py's baseline capacity.
        #   - blockage treatment: ONLY contested cells get this — an
        #     uncontested building cell is already fully represented via
        #     the elevation raise above; recording a reduction there too
        #     would double-represent the same physical effect twice.
        #   - porosity treatment: EVERY valid building cell gets this,
        #     contested or not, since elevation never represents any of
        #     them under this treatment.
        reduced = contested if treatment == "blockage" else valid_building
        conveyance_factor = np.where(reduced, np.float32(porosity_factor), conveyance_factor)
        n_building_cells = int(building_override.sum())
        n_conveyance_reduced_cells = int(reduced.sum())

    conveyance_factor = np.where(valid_mask, conveyance_factor, np.float32(nodata))

    # Final backstop, independent of which path above touched a cell: any
    # cell that was nodata in the source DEM must still read as nodata here.
    # Without this, dem_conditioned_prebreach.tif would declare a nodata
    # value in its profile while some pixels tagged nodata hold ordinary-
    # looking (corrupted) floats — a declared-vs-realized mismatch.
    conditioned = np.where(valid_mask, conditioned, nodata)

    interim_terrain.mkdir(parents=True, exist_ok=True)
    prebreach_path = interim_terrain / "dem_conditioned_prebreach.tif"
    profile.update(dtype="float32", nodata=nodata, compress="deflate")
    with atomic_output_path(prebreach_path) as tmp:
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(conditioned.astype(np.float32), 1)

    # D4: genuine separate conveyance-reduction layer, on the same buffered
    # grid as elevation (build.py crops it to canonical the same way it
    # crops elevation). Independent of breaching — a building's conveyance
    # effect on a cell has nothing to do with whether that cell was ever a
    # hydrological pit, so there is no pre/post-breach distinction for this
    # layer.
    conveyance_path = interim_terrain / "building_conveyance_factor.tif"
    with atomic_output_path(conveyance_path) as tmp:
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(conveyance_factor.astype(np.float32), 1)

    n_pits_pre = count_pits(conditioned, valid_mask)

    wbt = whitebox.WhiteboxTools()
    wbt.verbose = False
    postbreach_path = interim_terrain / "dem_conditioned_postbreach.tif"
    with atomic_output_path(postbreach_path) as tmp:
        ret = wbt.breach_depressions(
            dem=str(prebreach_path.resolve()),
            output=str(tmp.resolve()),
            fill_pits=False,
        )
        if ret != 0:
            raise ConditioningError(f"whitebox breach_depressions returned nonzero exit code {ret}")

        with rasterio.open(tmp) as src:
            buffered_grid.assert_aligned(src)
            post_breach = src.read(1)
            post_dtype = src.dtypes[0]

        # whitebox's breach_depressions writes float64 regardless of the
        # float32 input (observed on this run: prebreach float32 in,
        # postbreach float64 out) — inconsistent with every other layer in
        # this pipeline and with PROMPT.md SS7's float32 convention (float64
        # "will not fit in 8 GB at useful grid sizes and isn't needed").
        # Elevations in the ~700-1000 m range lose nothing meaningful going
        # back to float32 (~7 significant digits, far finer than metre-scale
        # terrain data needs). Re-write the tmp file (still pre-rename, so a
        # crash mid-correction still can't leave a bad file at the final
        # path) — derived.py reads the file directly, not this function's
        # return value.
        if post_dtype != "float32":
            post_breach = post_breach.astype(np.float32)
            fixed_profile = profile.copy()
            fixed_profile.update(dtype="float32", nodata=nodata, compress="deflate")
            with rasterio.open(tmp, "w", **fixed_profile) as dst:
                dst.write(post_breach, 1)

    n_pits_post = count_pits(post_breach, valid_mask)
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
        "road_burn_depth_m": road_depth,
        "drain_burn_depth_m": drain_depth,
        "n_road_burned_cells": n_road_burned,
        "n_waterway_burned_cells": n_waterway_burned,
        "n_building_override_cells": n_building_cells,
        "buildings_treatment": treatment,
        # Distinct from "buildings_treatment" above: that's the CONFIGURED
        # value regardless of whether it ran. When dem_is_dtm is False the
        # entire building block above is skipped — treatment was never
        # actually applied to anything — so this is None rather than
        # silently implying blockage/porosity logic executed. The zero
        # counts (n_building_override_cells, n_building_conveyance_reduced_
        # cells) already disclose this, but a null here closes it in one
        # field instead of requiring a reader to cross-reference counts.
        "buildings_treatment_applied": treatment if dem_is_dtm else None,
        "porosity_conveyance_factor": porosity_factor,
        "n_building_conveyance_reduced_cells": n_conveyance_reduced_cells,
        "building_conveyance_factor_path": str(conveyance_path.relative_to(repo_root)),
        "breach_tool": "whitebox.breach_depressions (NOT least_cost — see module docstring)",
        "breach_fill_pits": False,
        "postbreach_dtype_corrected_from": (
            None if post_dtype == "float32" else post_dtype
        ),  # whitebox wrote float64 on this run; re-cast to float32 to match
        # every other layer — see the fix above.
        "n_pits_pre_breach": n_pits_pre,
        "n_pits_post_breach": n_pits_post,
        "pits_decreased": n_pits_post < n_pits_pre,
        "n_cells_changed_by_breach": n_cells_changed,
        "n_cells_RAISED_by_breach": n_cells_raised,
        "breach_is_pure_lowering": n_cells_raised == 0,
        "volume_changed_m3": volume_changed_m3,
        "prebreach_path": str(prebreach_path.relative_to(repo_root)),
        "postbreach_path": str(postbreach_path.relative_to(repo_root)),
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
    """Burn roads/buildings/waterways into the DEM, breach depressions."""
    cfg = load_config(config)
    try:
        result = run_stage(
            "phase1_terrain_conditioning", build_conditioning, cfg, config, REPO, out
        )
    except ConditioningError as e:
        typer.echo(f"\nFATAL: {e}")
        raise typer.Exit(code=1) from e

    typer.echo(f"DEM source: {result['dem_source']}  dem_is_dtm={result['dem_is_dtm']}")
    typer.echo(
        f"burned: roads={result['n_road_burned_cells']:,} cells @ "
        f"{result['road_burn_depth_m']}m   waterways={result['n_waterway_burned_cells']:,} "
        f"cells @ {result['drain_burn_depth_m']}m   "
        f"buildings={result['n_building_override_cells']:,} cells (treatment="
        f"{result['buildings_treatment']})"
    )
    typer.echo(
        f"building conveyance reduction: {result['n_building_conveyance_reduced_cells']:,} "
        f"cells @ factor={result['porosity_conveyance_factor']} -> "
        f"{result['building_conveyance_factor_path']}"
    )
    typer.echo(f"breach tool: {result['breach_tool']}")
    typer.echo(
        f"pits: {result['n_pits_pre_breach']:,} -> {result['n_pits_post_breach']:,}  "
        f"(decreased: {result['pits_decreased']})"
    )
    typer.echo(
        f"breach changed {result['n_cells_changed_by_breach']:,} cells, "
        f"{result['n_cells_RAISED_by_breach']:,} RAISED "
        f"(pure lowering: {result['breach_is_pure_lowering']}), "
        f"volume {result['volume_changed_m3']:,.0f} m^3"
    )
    typer.echo(f"wrote {result['prebreach_path']}, {result['postbreach_path']}")
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
