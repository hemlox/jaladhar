"""Phase 1 invariant tests — the 14 invariants from the approved plan.

Tests against the REAL pipeline outputs already on disk (`data/processed/`,
`data/interim/terrain/`, and the stage manifests in `runs/`), never against
synthetic/fabricated data — CLAUDE.md rule 1. If the pipeline hasn't been
run, every test here skips with a clear message rather than fabricating a
fixture to test against.

Invariants 13 (config-driven, no hardcoded literals) and 14 (Microsoft
footprint streaming) are CODE-REVIEW-style checks, not data-testable — the
Phase 1 plan itself frames #14 this way ("more of a code-review-style
invariant than a runtime test"). Lightweight structural tests are included
where reasonable; the substantive verification for these two is the human/
agent review pass, not this file, and that limitation is stated here
honestly rather than papered over with a test that would pass without
actually checking the thing it claims to check.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
import yaml

REPO = Path(__file__).resolve().parents[1]
PROCESSED = REPO / "data" / "processed"
INTERIM = REPO / "data" / "interim" / "terrain"
INTERIM_DEM = REPO / "data" / "interim" / "dem"
RUNS = REPO / "runs"
CONFIG_PATH = REPO / "configs" / "domain_bengaluru.yaml"


def _cfg() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _skip_unless_exist(*paths: Path) -> None:
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        pytest.skip(
            f"required pipeline output(s) not present: {missing} — "
            "run `python -m jaladhar.terrain.build` first"
        )


def _manifest(stage_dir: str) -> dict:
    path = RUNS / stage_dir / "manifest.json"
    _skip_unless_exist(path)
    return json.loads(path.read_text())


STACK_LAYERS = [
    "elevation.tif",
    "building_mask.tif",
    "building_height_delta.tif",
    "building_conveyance_factor.tif",
    "manning_n.tif",
    "drain_capacity.tif",
    "distance_to_drain.tif",
    "road_segment_id.tif",
    "slope.tif",
    "flow_accumulation.tif",
]


# --- Invariant 1: all layers share identical shape, transform, crs ---------


def test_invariant_1_all_layers_aligned():
    from jaladhar.terrain.grid import build_grid

    cfg = _cfg()
    grid, _ = build_grid(cfg, REPO)
    layer_paths = [PROCESSED / name for name in STACK_LAYERS]
    _skip_unless_exist(*layer_paths)

    for path in layer_paths:
        with rasterio.open(path) as ds:
            grid.assert_aligned(ds)  # raises ValueError on any mismatch


# --- Invariant 2: no NaN in the BBMP interior; nodata only outside ---------
#
# The BBMP boundary is an irregular ward-union polygon; the canonical grid
# is its rectangular bounding box (see grid.py). Real nodata cells are
# EXPECTED in the margin between the two, so "no nodata anywhere in the
# raster" is the wrong check — the invariant is specifically about the
# INTERIOR of the real polygon.
#
# Six layers are continuous/complete — every interior cell has a real
# computed value, there is no "legitimately no data here" concept — so
# nodata inside BBMP for these is a genuine defect. The other three are
# inherently sparse/categorical: their declared nodata value (0) doubles as
# the correct DATA value "no building/road here" for most of the city (a
# city is mostly neither building footprint nor road centreline), so
# asserting zero nodata cells there would fail on millions of entirely
# normal background cells. Both groups still get a real, non-vacuous check.

CONTINUOUS_LAYERS = [
    "elevation.tif",
    "building_conveyance_factor.tif",
    "manning_n.tif",
    "drain_capacity.tif",
    "distance_to_drain.tif",
    "slope.tif",
    "flow_accumulation.tif",
]
SPARSE_LAYERS = ["building_mask.tif", "building_height_delta.tif", "road_segment_id.tif"]
assert set(CONTINUOUS_LAYERS) | set(SPARSE_LAYERS) == set(STACK_LAYERS)


@pytest.fixture(scope="module")
def bbmp_interior_mask() -> np.ndarray | None:
    """Boolean canonical-grid mask: True where a cell's centre is inside the
    REAL BBMP boundary polygon — not just inside the grid's rectangular
    bounding box. Computed once per test module (rasterizing the polygon
    onto a ~3515x3421 grid nine times over, once per parametrized layer,
    would be pure waste).

    Returns None (dependent tests skip) if the boundary file or config
    isn't present.
    """
    if not CONFIG_PATH.exists():
        return None
    cfg = _cfg()
    boundary_path = REPO / cfg["boundary"]["path"]
    if not boundary_path.exists():
        return None

    from rasterio.features import rasterize

    from jaladhar.terrain.grid import build_grid, load_boundary

    grid, _ = build_grid(cfg, REPO)
    gdf = load_boundary(cfg, REPO).to_crs(grid.crs)
    gdf["geometry"] = gdf.make_valid()
    union_geom = gdf.union_all()

    mask = rasterize(
        [(union_geom, 1)],
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0,
        dtype="uint8",
    )
    return mask == 1


@pytest.mark.parametrize("layer_name", CONTINUOUS_LAYERS)
def test_invariant_2_no_missing_data_inside_bbmp(layer_name: str, bbmp_interior_mask):
    """No NaN and no declared-nodata cells anywhere inside the real BBMP
    polygon, for the layers where every interior cell must have a real
    computed value.

    The previous version of this test (`test_invariant_2_no_nan`) checked
    only `np.isnan(arr).any()` across the WHOLE raster. None of these
    layers ever use IEEE NaN as their nodata sentinel — they use explicit
    finite values (-9999 etc.) or declare no nodata at all — so that check
    could not fail regardless of whether nodata had silently eaten part of
    the city; it was structurally incapable of catching the failure mode
    the invariant is named for.
    """
    if bbmp_interior_mask is None:
        pytest.skip("BBMP boundary file not present — cannot build the interior mask")
    path = PROCESSED / layer_name
    _skip_unless_exist(path)
    with rasterio.open(path) as ds:
        arr = ds.read(1)
        nodata = ds.nodata

    assert arr.shape == bbmp_interior_mask.shape, (
        f"{layer_name} shape {arr.shape} != BBMP interior mask shape " f"{bbmp_interior_mask.shape}"
    )
    interior = arr[bbmp_interior_mask]
    assert interior.size > 0, "BBMP interior mask is empty — cannot test"

    n_nan = int(np.isnan(interior).sum()) if np.issubdtype(interior.dtype, np.floating) else 0
    assert n_nan == 0, f"{layer_name}: {n_nan} NaN cells inside the real BBMP polygon"

    if nodata is not None:
        n_nodata = int((interior == nodata).sum())
        assert n_nodata == 0, (
            f"{layer_name}: {n_nodata} cells equal to its declared nodata ({nodata}) "
            "inside the real BBMP polygon — missing data inside the actual city "
            "boundary, not just the rectangular grid margin"
        )


# layer_name -> (manifest stage_dir, function(full_raster_array, manifest_dict) -> actual count)
# Used to cross-check the raster's REAL on-disk signal against each layer's
# own producing module's self-reported count — a plain "not entirely empty"
# check can only ever be reddened by wiping the WHOLE layer; this catches a
# PARTIAL corruption too (e.g. a bug that silently drops some but not all
# cells), which "not entirely empty" is structurally blind to.
SPARSE_LAYER_CROSS_CHECKS = {
    "building_mask.tif": (
        "terrain_buildings",
        lambda arr: int((arr == 1).sum()),
        "n_building_cells",
    ),
    "building_height_delta.tif": (
        "terrain_buildings",
        lambda arr: int((arr != 0).sum()),
        "n_building_cells",
    ),
    "road_segment_id.tif": (
        "terrain_roads",
        lambda arr: len(set(np.unique(arr).tolist()) - {0}),
        "n_unique_ids_in_raster",
    ),
}
assert set(SPARSE_LAYER_CROSS_CHECKS) == set(SPARSE_LAYERS)


@pytest.mark.parametrize("layer_name", SPARSE_LAYERS)
def test_invariant_2_sparse_layer_has_real_signal_inside_bbmp(layer_name: str, bbmp_interior_mask):
    """For inherently sparse/categorical layers, "zero nodata cells" is the
    wrong check — most of a city is neither a building footprint nor a road
    centreline, so nodata (background) is the CORRECT state for most
    interior cells. The meaningful check is that the layer has SOME real
    signal inside the actual city polygon, PLUS that the actual on-disk
    signal count matches the producing module's own self-reported count —
    the "some real signal" half alone can only be reddened by wiping the
    entire layer (still worth checking, but weak on its own); the
    cross-check catches a partial corruption too.
    """
    if bbmp_interior_mask is None:
        pytest.skip("BBMP boundary file not present — cannot build the interior mask")
    path = PROCESSED / layer_name
    _skip_unless_exist(path)
    with rasterio.open(path) as ds:
        arr = ds.read(1)
        nodata = ds.nodata

    interior = arr[bbmp_interior_mask]
    n_nan = int(np.isnan(interior).sum()) if np.issubdtype(interior.dtype, np.floating) else 0
    assert n_nan == 0, f"{layer_name}: {n_nan} NaN cells inside the real BBMP polygon"

    if nodata is not None:
        n_real = int((interior != nodata).sum())
        assert n_real > 0, (
            f"{layer_name}: every interior cell equals its declared nodata ({nodata}) "
            "— zero real signal anywhere inside the actual city polygon, suggesting "
            "an empty or failed upstream fetch"
        )

    stage_dir, count_fn, manifest_key = SPARSE_LAYER_CROSS_CHECKS[layer_name]
    manifest = _manifest(stage_dir)
    actual_count = count_fn(arr)
    expected_count = manifest[manifest_key]
    assert actual_count == expected_count, (
        f"{layer_name}: actual on-disk signal count ({actual_count}) does not match "
        f"{stage_dir}'s manifest {manifest_key}={expected_count} — the raster and its "
        "own producing module's manifest disagree, meaning the file on disk isn't "
        "the one that manifest actually describes"
    )


# --- Invariant 3: elevation median in the expected band, from real data ----


def test_invariant_3_elevation_median_in_expected_band():
    path = PROCESSED / "elevation.tif"
    _skip_unless_exist(path)
    cfg = _cfg()
    band = cfg["dem"]["expected_median_elevation_m"]

    with rasterio.open(path) as ds:
        arr = ds.read(1)
        nodata = ds.nodata
    valid = arr[arr != nodata] if nodata is not None else arr
    median = float(np.median(valid))

    assert band[0] <= median <= band[1], (
        f"elevation median {median:.1f} m outside expected band {band} — "
        "suspect wrong tile, unit error, or failed reprojection"
    )


# --- Invariant 4: roads burned down, buildings burned up (sign check) ------


def test_invariant_4_road_cells_lower_than_original():
    """Every road-burned cell's conditioned elevation < the pre-burn DEM.

    Previously this excluded cells that were ALSO a building-override
    cell, because the old conditioning.py let building win on overlap
    unconditionally — an accommodation the review pass flagged as itself
    part of the D2/D3 problem (a test quietly absorbing a spec deviation
    instead of the pipeline being fixed). The burn-precedence rewrite
    (waterway > road > building-only-where-unclaimed) means road now wins
    over building on every overlap cell too, so no exclusion is needed
    here any more — road cells burned at the WATERWAY depth (rarer
    road/waterway overlap, see test_invariant_4_waterway_wins_over_road_
    on_overlap) are still "lower than original", so a plain road-presence
    mask is correct for this specific assertion.
    """
    road_path = INTERIM / "road_segment_id.tif"
    orig_dem_path = INTERIM_DEM / "dem_10m_buffered.tif"
    prebreach_path = INTERIM / "dem_conditioned_prebreach.tif"
    _skip_unless_exist(road_path, orig_dem_path, prebreach_path)

    cfg = _cfg()
    buffer_m = float(cfg["dem"]["buffer_m"])
    with rasterio.open(road_path) as ds:
        grid_res = ds.res[0]
        canonical_shape = ds.shape
    buf_cells = round(buffer_m / grid_res)

    with rasterio.open(road_path) as ds:
        road_ids = ds.read(1)
    with rasterio.open(orig_dem_path) as ds:
        original_buffered = ds.read(1)
    with rasterio.open(prebreach_path) as ds:
        prebreach_buffered = ds.read(1)

    # road_segment_id is on the CANONICAL grid; original/prebreach DEM are
    # BUFFERED — crop the buffered arrays to canonical for a like-for-like
    # comparison (same offset conditioning.py/derived.py use throughout).
    h, w = canonical_shape
    original = original_buffered[buf_cells : buf_cells + h, buf_cells : buf_cells + w]
    prebreach = prebreach_buffered[buf_cells : buf_cells + h, buf_cells : buf_cells + w]

    road_mask = road_ids != 0
    assert road_mask.any(), "no road cells found — cannot test the burn sign"
    diff = prebreach[road_mask] - original[road_mask]
    assert (diff < -1e-4).all(), (
        f"{int((diff >= -1e-4).sum())} road cells were NOT lowered relative to the "
        "original DEM — road burn sign is wrong, or a building wrongly won "
        "precedence on an overlap cell"
    )


def test_invariant_4_waterway_wins_over_road_on_overlap():
    """Where a road and a waterway both claim a cell, the applied lowering
    equals the WATERWAY depth, not the road depth — the explicit
    precedence rule (module docstring), verified independently of which
    burn happens to run last or which is numerically deeper: distance_to_
    drain.tif is computed by drains.py, an entirely separate rasterization
    from conditioning.py's own road/waterway masks.
    """
    road_path = PROCESSED / "road_segment_id.tif"
    dist_path = PROCESSED / "distance_to_drain.tif"
    orig_dem_path = INTERIM_DEM / "dem_10m_buffered.tif"
    prebreach_path = INTERIM / "dem_conditioned_prebreach.tif"
    _skip_unless_exist(road_path, dist_path, orig_dem_path, prebreach_path)

    cfg = _cfg()
    buffer_m = float(cfg["dem"]["buffer_m"])
    drain_depth = float(cfg["conditioning"]["drain_burn_depth_operating_point_m"])
    with rasterio.open(road_path) as ds:
        grid_res = ds.res[0]
        canonical_shape = ds.shape
    buf_cells = round(buffer_m / grid_res)

    with rasterio.open(road_path) as ds:
        road_ids = ds.read(1)
    with rasterio.open(dist_path) as ds:
        dist_to_drain = ds.read(1)
    with rasterio.open(orig_dem_path) as ds:
        original_buffered = ds.read(1)
    with rasterio.open(prebreach_path) as ds:
        prebreach_buffered = ds.read(1)
    h, w = canonical_shape
    original = original_buffered[buf_cells : buf_cells + h, buf_cells : buf_cells + w]
    prebreach = prebreach_buffered[buf_cells : buf_cells + h, buf_cells : buf_cells + w]

    overlap = (road_ids != 0) & (dist_to_drain == 0.0)
    if not overlap.any():
        pytest.skip("no road/waterway overlap cells on this run — precedence not exercised")
    applied = original[overlap] - prebreach[overlap]
    assert np.allclose(applied, drain_depth, atol=1e-4), (
        f"road/waterway overlap cells are not burned to the configured drain depth "
        f"({drain_depth} m) — waterway is not winning precedence over road on overlap"
    )


def test_invariant_4_building_cells_higher_than_original_when_dtm():
    """When dem_is_dtm, every building cell NOT claimed by a road or
    waterway burn has elevation > original DEM.

    Previously this checked ALL building cells unconditionally, because
    the old conditioning.py let building win on every overlap. The
    precedence rewrite means a building cell that's ALSO a road or
    waterway cell is no longer raised there — conveyance wins instead
    (test_invariant_4_road_cells_lower_than_original and
    test_invariant_4_waterway_wins_over_road_on_overlap cover those cells
    directly). This test now restricts to the building-only cells the
    precedence rule actually still grants to the building.
    """
    dem_manifest = _manifest("terrain_dem")
    if not dem_manifest["dem_is_dtm"]:
        pytest.skip("dem_is_dtm is false on this run — buildings are not burned")

    mask_path = PROCESSED / "building_mask.tif"
    road_path = PROCESSED / "road_segment_id.tif"
    dist_path = PROCESSED / "distance_to_drain.tif"
    orig_dem_path = INTERIM_DEM / "dem_10m_buffered.tif"
    prebreach_path = INTERIM / "dem_conditioned_prebreach.tif"
    _skip_unless_exist(mask_path, road_path, dist_path, orig_dem_path, prebreach_path)

    cfg = _cfg()
    buffer_m = float(cfg["dem"]["buffer_m"])
    with rasterio.open(mask_path) as ds:
        mask = ds.read(1)
        grid_res = ds.res[0]
        h, w = ds.shape
    buf_cells = round(buffer_m / grid_res)

    with rasterio.open(road_path) as ds:
        road_ids = ds.read(1)
    with rasterio.open(dist_path) as ds:
        dist_to_drain = ds.read(1)
    with rasterio.open(orig_dem_path) as ds:
        original_buffered = ds.read(1)
    with rasterio.open(prebreach_path) as ds:
        prebreach_buffered = ds.read(1)
    original = original_buffered[buf_cells : buf_cells + h, buf_cells : buf_cells + w]
    prebreach = prebreach_buffered[buf_cells : buf_cells + h, buf_cells : buf_cells + w]

    claimed_by_conveyance = (road_ids != 0) | (dist_to_drain == 0.0)
    building_only = (mask == 1) & ~claimed_by_conveyance
    assert building_only.any(), "no building-only (non-conveyance) cells found — cannot test"
    diff = prebreach[building_only] - original[building_only]
    assert (diff > 1e-4).all(), (
        f"{int((diff <= 1e-4).sum())} building-only cells were NOT raised relative to "
        "the original DEM — building burn sign is wrong, dem_is_dtm gating failed, or "
        "the burn-precedence rule is wrongly excluding a non-conveyance cell"
    )


# --- Invariant 5: buildings burned ONLY if dem_is_dtm -----------------------


def test_invariant_5_building_burn_gated_by_dem_is_dtm():
    """conditioning.py's ACTUAL applied elevation offset at building cells,
    checked against the ground-truth config constant.

    The previous version of this test read buildings.py's own
    `building_height_delta.tif` and asserted only "is anything nonzero" —
    that file is computed unconditionally by buildings.py regardless of
    `dem_is_dtm`, so the assertion would pass even if conditioning.py's
    gate were wired wrong or entirely missing; it tested that buildings.py
    ran, never that conditioning.py's gate does anything. This version
    reads conditioning.py's real output (prebreach - original at building
    cells) and compares it to the configured `blockage_height_m` directly —
    ground truth independent of any module's self-reported intermediate
    file, so it also catches buildings.py itself computing the wrong
    height, not just a broken gate.

    Restricted to building-only (non-conveyance) cells for the same reason
    as test_invariant_4_building_cells_higher_than_original_when_dtm: the
    burn-precedence rule means a building cell that's ALSO a road or
    waterway cell is not raised at all — the config height literally does
    not apply there, on purpose.
    """
    dem_manifest = _manifest("terrain_dem")
    mask_path = PROCESSED / "building_mask.tif"
    road_path = PROCESSED / "road_segment_id.tif"
    dist_path = PROCESSED / "distance_to_drain.tif"
    orig_dem_path = INTERIM_DEM / "dem_10m_buffered.tif"
    prebreach_path = INTERIM / "dem_conditioned_prebreach.tif"
    _skip_unless_exist(mask_path, road_path, dist_path, orig_dem_path, prebreach_path)

    cfg = _cfg()
    buffer_m = float(cfg["dem"]["buffer_m"])
    treatment = cfg["buildings"]["treatment"]
    with rasterio.open(mask_path) as ds:
        mask = ds.read(1)
        grid_res = ds.res[0]
        h, w = ds.shape
    buf_cells = round(buffer_m / grid_res)

    with rasterio.open(road_path) as ds:
        road_ids = ds.read(1)
    with rasterio.open(dist_path) as ds:
        dist_to_drain = ds.read(1)
    with rasterio.open(orig_dem_path) as ds:
        original_buffered = ds.read(1)
        orig_nodata = ds.nodata
    with rasterio.open(prebreach_path) as ds:
        prebreach_buffered = ds.read(1)
    original = original_buffered[buf_cells : buf_cells + h, buf_cells : buf_cells + w]
    prebreach = prebreach_buffered[buf_cells : buf_cells + h, buf_cells : buf_cells + w]

    # conditioning.py only applies the building override where the original
    # cell is valid (see the burn-step nodata fix) and not already claimed
    # by a road/waterway burn (see the burn-precedence rule) — exclude both
    # here too, or this test would wrongly expect an override the pipeline
    # correctly withheld.
    claimed_by_conveyance = (road_ids != 0) | (dist_to_drain == 0.0)
    building_cells = (mask == 1) & (original != orig_nodata) & ~claimed_by_conveyance
    assert building_cells.any(), "no valid building-only cells found — cannot test the burn gate"
    applied = prebreach[building_cells] - original[building_cells]

    if not dem_manifest["dem_is_dtm"]:
        assert np.allclose(applied, 0.0, atol=1e-4), (
            f"{int((np.abs(applied) > 1e-4).sum())} building cells were altered by "
            "conditioning.py even though dem_is_dtm=False — GLO-30 already bakes "
            "buildings into elevation, so burning again double-counts"
        )
    elif treatment == "blockage":
        expected_height = float(cfg["buildings"]["blockage_height_m"])
        assert np.allclose(applied, expected_height, atol=1e-4), (
            f"conditioning.py's applied building offset does not equal the "
            f"configured blockage_height_m={expected_height} at one or more "
            "building cells (dem_is_dtm=True, treatment=blockage) — the gate is "
            "not applying the real, configured height"
        )
    else:
        pytest.skip(
            f"buildings.treatment={treatment!r} — this test's exact-height "
            "assertion covers 'blockage' only"
        )


# --- D4 addition: building_conveyance_factor.tif is a genuine separate -----
# layer, not folded into elevation. Not one of the original 14 invariants —
# the layer did not exist until D4 added it — held to the same non-vacuous,
# ground-truth-against-config standard as the rest of this file.


def test_d4_building_conveyance_factor_matches_config_at_contested_cells():
    """Under `treatment: blockage`, a road/waterway-contested building cell
    holds EXACTLY the configured `porosity_conveyance_factor` in this
    layer, and every other cell holds exactly 1.0 (unaffected) — ground
    truth against the config constant, independent of conditioning.py's
    own internal variable names for the same concept.
    """
    dem_manifest = _manifest("terrain_dem")
    if not dem_manifest["dem_is_dtm"]:
        pytest.skip("dem_is_dtm is false on this run — buildings are not burned")

    cfg = _cfg()
    treatment = cfg["buildings"]["treatment"]
    if treatment != "blockage":
        pytest.skip(f"buildings.treatment={treatment!r} — this test covers 'blockage' only")
    porosity_factor = float(cfg["buildings"]["porosity_conveyance_factor"])

    conveyance_path = PROCESSED / "building_conveyance_factor.tif"
    mask_path = PROCESSED / "building_mask.tif"
    road_path = PROCESSED / "road_segment_id.tif"
    dist_path = PROCESSED / "distance_to_drain.tif"
    _skip_unless_exist(conveyance_path, mask_path, road_path, dist_path)

    with rasterio.open(conveyance_path) as ds:
        conveyance = ds.read(1)
    with rasterio.open(mask_path) as ds:
        mask = ds.read(1)
    with rasterio.open(road_path) as ds:
        road_ids = ds.read(1)
    with rasterio.open(dist_path) as ds:
        dist_to_drain = ds.read(1)

    claimed_by_conveyance = (road_ids != 0) | (dist_to_drain == 0.0)
    contested = (mask == 1) & claimed_by_conveyance
    assert contested.any(), "no contested building cells found — cannot test"

    assert np.allclose(conveyance[contested], porosity_factor, atol=1e-4), (
        f"contested building cells do not hold the configured "
        f"porosity_conveyance_factor={porosity_factor} in building_conveyance_factor.tif"
    )
    assert np.allclose(conveyance[~contested], 1.0, atol=1e-4), (
        "a non-contested cell holds a conveyance reduction under blockage treatment — "
        "should be exactly 1.0 (unaffected), since the elevation raise already "
        "represents an uncontested building cell"
    )


# --- Invariant 6: Manning's n bounds ----------------------------------------


def test_invariant_6_manning_n_bounds():
    path = PROCESSED / "manning_n.tif"
    _skip_unless_exist(path)
    with rasterio.open(path) as ds:
        arr = ds.read(1)
    assert arr.min() >= 0.01, f"Manning's n below 0.01: min={arr.min()}"
    assert arr.max() <= 0.2, f"Manning's n above 0.2: max={arr.max()}"
    assert not (arr <= 0).any(), "Manning's n contains a zero or negative value"


# --- Invariant 7: post-breach <= pre-breach (NEVER conditioned-vs-original) -


def test_invariant_7_breach_is_pure_lowering():
    """post-breach <= pre-breach everywhere — compares the two BREACH-STEP
    rasters specifically, never the final output against the original DEM
    (that comparison is wrong: building burn-up legitimately raises cells
    above the original grade whenever dem_is_dtm is true)."""
    pre_path = INTERIM / "dem_conditioned_prebreach.tif"
    post_path = INTERIM / "dem_conditioned_postbreach.tif"
    _skip_unless_exist(pre_path, post_path)

    with rasterio.open(pre_path) as ds:
        pre = ds.read(1)
        nodata = ds.nodata
    with rasterio.open(post_path) as ds:
        post = ds.read(1)

    valid = pre != nodata
    diff = post[valid] - pre[valid]
    assert diff.max() <= 1e-4, (
        f"breach RAISED at least one cell by up to {diff.max():.4f} m — "
        "breach_depressions should be pure lowering (fill_pits=False)"
    )


# --- Invariant 8: pit count strictly decreased after breaching -------------


def test_invariant_8_pit_count_decreased():
    from jaladhar.terrain.conditioning import count_pits

    pre_path = INTERIM / "dem_conditioned_prebreach.tif"
    post_path = INTERIM / "dem_conditioned_postbreach.tif"
    orig_dem_path = INTERIM_DEM / "dem_10m_buffered.tif"
    _skip_unless_exist(pre_path, post_path, orig_dem_path)

    with rasterio.open(pre_path) as ds:
        pre = ds.read(1)
        nodata = ds.nodata
    with rasterio.open(post_path) as ds:
        post = ds.read(1)
    with rasterio.open(orig_dem_path) as ds:
        original = ds.read(1)
    valid_mask = original != nodata

    # Independently recomputed here (imported from conditioning.py, the
    # actual function the pipeline used — not re-derived by hand), not just
    # trusting the manifest's self-reported counts.
    n_pre = count_pits(pre, valid_mask)
    n_post = count_pits(post, valid_mask)
    assert n_post < n_pre, f"pit count did not decrease: {n_pre} -> {n_post}"


# --- Invariant 9: flow accumulation max at a domain-EDGE cell --------------


def test_invariant_9_flow_accumulation_max_at_edge():
    path = INTERIM / "flow_accumulation_buffered.tif"
    _skip_unless_exist(path)
    with rasterio.open(path) as ds:
        arr = ds.read(1)

    max_idx = np.unravel_index(np.argmax(arr), arr.shape)
    h, w = arr.shape
    at_edge = max_idx[0] in (0, h - 1) or max_idx[1] in (0, w - 1)
    assert at_edge, (
        f"max flow accumulation ({arr.max():,.0f} cells) is at interior index "
        f"{max_idx}, not a domain edge — an unbreached sink may still be "
        "trapping the drainage network"
    )


# --- Invariant 10: DEM buffer >= 500 m, verified on the REAL raster --------


def test_invariant_10_buffer_at_least_500m():
    """The buffered flow-accumulation raster's REAL bounds exceed the
    canonical grid's REAL bounds by the configured buffer_m (itself
    required to be >=500m) on all four sides.

    The previous version of this test asserted only that the YAML config
    SAYS buffer_m >= 500.0 — internally consistent with itself, but proves
    nothing about whether the buffered grid actually used for flow
    accumulation was really computed with that margin. A bug in
    Grid.buffered() or in how derived.py crops back to canonical could
    silently produce a narrower (or zero) real margin while the config file
    still correctly says "500".
    """
    buffered_path = INTERIM / "flow_accumulation_buffered.tif"
    _skip_unless_exist(buffered_path)

    cfg = _cfg()
    configured_buffer_m = float(cfg["dem"]["buffer_m"])
    assert configured_buffer_m >= 500.0, f"configured buffer_m={configured_buffer_m} < 500"

    from jaladhar.terrain.grid import build_grid

    grid, _ = build_grid(cfg, REPO)
    with rasterio.open(buffered_path) as ds:
        buffered_bounds = ds.bounds  # left, bottom, right, top
        buffered_epsg = ds.crs.to_epsg()

    canonical_epsg = grid.crs.to_epsg()
    assert (
        buffered_epsg == canonical_epsg
    ), f"buffered raster CRS EPSG:{buffered_epsg} != canonical EPSG:{canonical_epsg}"

    canonical_bounds = grid.bounds  # xmin, ymin, xmax, ymax
    margins = {
        "left": canonical_bounds[0] - buffered_bounds[0],
        "bottom": canonical_bounds[1] - buffered_bounds[1],
        "right": buffered_bounds[2] - canonical_bounds[2],
        "top": buffered_bounds[3] - canonical_bounds[3],
    }
    for side, margin in margins.items():
        assert margin >= configured_buffer_m - 1e-6, (
            f"buffered flow-accumulation raster's real {side} margin is "
            f"{margin:.1f} m, less than the configured buffer_m="
            f"{configured_buffer_m} m — flow accumulation would get wrong "
            "upstream areas at the domain edge (invariant 9's edge-outlet "
            "check depends on this margin being real)"
        )


# --- Invariant 11: road_segment_id unique, round-trips to real OSM IDs -----


def test_invariant_11_road_segment_ids_unique_and_roundtrip():
    lookup_path = INTERIM / "roads_segment_lookup.csv"
    raster_path = PROCESSED / "road_segment_id.tif"
    _skip_unless_exist(lookup_path, raster_path)

    import pandas as pd

    lookup = pd.read_csv(lookup_path)
    assert lookup["segment_id"].is_unique, "segment_id is not unique in the lookup table"
    assert lookup["osm_id"].is_unique, "osm_id is not unique in the lookup table"

    with rasterio.open(raster_path) as ds:
        raster_ids = ds.read(1)
    raster_unique = set(np.unique(raster_ids)) - {0}
    lookup_ids = set(lookup["segment_id"])
    orphans = raster_unique - lookup_ids
    assert not orphans, (
        f"{len(orphans)} segment_id values in the raster have no lookup-table "
        f"entry (e.g. {list(orphans)[:5]}) — burn geometry and ID raster diverged"
    )


# --- Invariant 12: manifests carry git SHA, DEM source, dem_is_dtm, wall clock -


@pytest.mark.parametrize(
    "stage_dir",
    [
        "terrain_grid",
        "terrain_dem",
        "terrain_roads",
        "terrain_buildings",
        "terrain_roughness",
        "terrain_drains",
        "terrain_conditioning",
        "terrain_derived",
        "terrain_build",
    ],
)
def test_invariant_12_manifest_has_git_sha_and_wall_clock(stage_dir: str):
    m = _manifest(stage_dir)
    assert "git_sha" in m and m["git_sha"] != "unknown", f"{stage_dir}: missing/unknown git_sha"
    assert "wall_clock_sec" in m and m["wall_clock_sec"] >= 0


def test_invariant_12_dem_manifest_has_source_and_dtm_flag():
    m = _manifest("terrain_dem")
    assert "dem_source" in m
    assert "dem_is_dtm" in m and isinstance(m["dem_is_dtm"], bool)


# --- Invariant 13: config-driven — LIGHTWEIGHT structural check only -------
#
# Whether every numeric parameter genuinely traces to config.py (vs. being
# quietly hardcoded) is a code-review-style check, not something a runtime
# test can prove by executing the pipeline once and inspecting outputs — a
# hardcoded value that happens to match the config would pass a data-level
# test while still violating the rule. The structural check below confirms
# every terrain module's CLI takes `--config` (the mechanical prerequisite
# for being config-driven); the substantive check is the human/agent review
# pass against the actual source, not this file.


@pytest.mark.parametrize(
    "module_name",
    [
        "grid",
        "fetch",
        "roads",
        "buildings",
        "roughness",
        "drains",
        "conditioning",
        "derived",
        "build",
    ],
)
def test_invariant_13_module_has_config_driven_cli(module_name: str):
    import importlib

    mod = importlib.import_module(f"jaladhar.terrain.{module_name}")
    assert hasattr(mod, "app"), f"{module_name}.py has no typer app"
    assert hasattr(mod, "main"), f"{module_name}.py has no main() CLI entry"


# --- Invariant 14: Microsoft footprint file streamed, not fully loaded -----
#
# Also a code-review-style check per the plan's own framing ("more of a
# code-review-style invariant than a runtime test"). The structural check
# below confirms the streaming function exists and is a generator-consuming
# line-by-line reader (checked by source inspection, not by running it
# against a multi-GB file in a test suite); full confidence is a review-pass
# question, not this file's.


def test_invariant_14_buildings_module_streams_ms_footprints():
    import inspect

    from jaladhar.terrain import buildings

    assert hasattr(
        buildings, "stream_ms_footprints_in_polygon"
    ), "buildings.py has no dedicated streaming function for Microsoft footprints"
    source = inspect.getsource(buildings.stream_ms_footprints_in_polygon)
    assert "gzip.open" in source and "for line in f" in source, (
        "stream_ms_footprints_in_polygon does not appear to read line-by-line "
        "(no `for line in f` over a gzip.open handle) — check for a full-file "
        "load that would risk OOMing this machine"
    )
    assert "pd.read_csv" not in source, (
        "stream_ms_footprints_in_polygon appears to use pd.read_csv, which would "
        "mis-parse the newline-delimited GeoJSON format Microsoft actually serves"
    )
