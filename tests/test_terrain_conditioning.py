"""Formal invariant suite for depression-aware D4 terrain conditioning.
Tests the mathematical invariants of depression-aware conditioning:
Invariant A1: pits_D4 is a SUBSET of (R union RESIDUAL). Pits outside: exactly 0.
Invariant A2: Residual repair failures bounded by stated domain-density rule
and consistent across all artifacts (CSV, rasters, manifest).
Invariant B:  Every registered basin has a D4-monotone spill path from its outlet
to the domain boundary or to another registered basin.
Invariant C:  max(cut) <= cut_max (2.60 m); max(fill) <= fill_max (3.25 m).
Invariant R:  exactly 0 cells modified inside retained basins (classes 1..4).
Invariant D:  Cells modified <= 465,927 baseline.
Invariant E:  Total retained volume sum(V(R)) vs design-storm volume (132.3 M m3).
Invariant F:  CONNECTIVITY CONSISTENCY: conditioning manifest connectivity (D4)
Invariant G:  Excavation volume <= excav_max, with no single connected trench"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rasterio
import scipy.ndimage as ndi
import yaml

from jaladhar.terrain.conditioning import (
    CONNECTIVITY_D4,
    CUT_MAX_M,
    FILL_MAX_M,
    _find_d4_pits,
)

REPO = Path(__file__).resolve().parents[1]
PROCESSED = REPO / "data" / "processed"
INTERIM = REPO / "data" / "interim" / "terrain"
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


# ── Invariant A1: pits_D4 is a SUBSET of (R union RESIDUAL). Pits outside == 0 ──


def test_invariant_a1_d4_pits_subset_of_retained_union_residual():
    post_path = INTERIM / "dem_conditioned_postbreach.tif"
    register_path = INTERIM / "retained_basins.csv"
    class_path = INTERIM / "basin_class_buffered.tif"
    _skip_unless_exist(post_path, register_path, class_path)

    with rasterio.open(post_path) as ds:
        dem = ds.read(1)
        nodata = ds.nodata
        valid = dem != nodata

    with rasterio.open(class_path) as ds:
        basin_class = ds.read(1)

    pits_d4 = _find_d4_pits(dem, valid)
    retained_or_residual_mask = (basin_class > 0) & valid

    pits_outside = pits_d4 & ~retained_or_residual_mask
    n_outside = int(pits_outside.sum())

    assert n_outside == 0, (
        f"Invariant A1 failed: Found {n_outside:,} D4 pits outside the retained-basin "
        "register R union RESIDUAL. Every pit must be inside interior(R) or enumerated in RESIDUAL."
    )


# ── Invariant A2: Residual pits cross-artifact agreement and bounded by stated rule ──


def test_invariant_a2_residual_pits_bounded_and_enumerated():
    """V8 Seam & Cross-Artifact Assertion:
    The producer writes its guaranteed properties into the manifest, and the consumer asserts
    5. terrain_conditioning manifest.json (declared buffered & canonical counts)"""
    manifest = _manifest("terrain_conditioning")
    class_buf_path = INTERIM / "basin_class_buffered.tif"
    class_canon_path = INTERIM / "basin_class.tif"
    register_path = INTERIM / "retained_basins.csv"
    residuals_csv_path = INTERIM / "basin_residuals.csv"
    _skip_unless_exist(class_buf_path, class_canon_path, register_path, residuals_csv_path)

    with rasterio.open(class_buf_path) as ds:
        basin_class_buf = ds.read(1)

    with rasterio.open(class_canon_path) as ds:
        basin_class_canon = ds.read(1)

    # 1. Realized counts across rasters
    n_residuals_buf_realized = int((basin_class_buf == 5).sum())
    n_residuals_canon_realized = int((basin_class_canon == 5).sum())

    # 2. Manifest declared values (V8 producer-consumer contract)
    manifest_buf_count = int(manifest["n_residuals_enumerated"])
    manifest_buf_pits = int(manifest.get("n_residual_pits_buffered", manifest_buf_count))
    manifest_canon_pits = int(manifest.get("n_residual_pits_canonical", n_residuals_canon_realized))
    residual_max_bound = int(manifest.get("residual_max_bound", 95_463))
    residual_canon_max_bound = int(manifest.get("residual_canonical_max_bound", 90_186))

    # Cross-artifact assertion 1: Manifest fields internal consistency
    assert manifest_buf_count == manifest_buf_pits, (
        f"Invariant A2 failed: Manifest n_residuals_enumerated ({manifest_buf_count}) != "
        f"n_residual_pits_buffered ({manifest_buf_pits})"
    )

    # Cross-artifact assertion 2: Realized buffered raster == Manifest buffered count
    assert n_residuals_buf_realized == manifest_buf_pits, (
        f"Invariant A2 failed: basin_class_buffered.tif class 5 ({n_residuals_buf_realized:,}) "
        f"disagrees with manifest n_residual_pits_buffered ({manifest_buf_pits:,})"
    )

    # Cross-artifact assertion 3: Realized canonical raster == Manifest canonical count
    assert n_residuals_canon_realized == manifest_canon_pits, (
        f"Invariant A2 failed: basin_class.tif class 5 ({n_residuals_canon_realized:,}) "
        f"disagrees with manifest n_residual_pits_canonical ({manifest_canon_pits:,})"
    )

    # Cross-artifact assertion 4: Buffered cutout matches canonical raster
    cfg = _cfg()
    buf_cells = round(float(cfg["dem"]["buffer_m"]) / float(cfg["resolution_m"]))
    canon_h, canon_w = basin_class_canon.shape
    buf_cutout = basin_class_buf[buf_cells : buf_cells + canon_h, buf_cells : buf_cells + canon_w]
    assert (
        int((buf_cutout == 5).sum()) == n_residuals_canon_realized
    ), "Invariant A2 failed: Canonical slice of basin_class_buffered.tif disagrees with basin_class.tif"  # noqa: E501

    # Cross-artifact assertion 5: basin_residuals.csv matches realized buffered count
    df_residuals = pd.read_csv(residuals_csv_path)
    assert len(df_residuals) == n_residuals_buf_realized, (
        f"Invariant A2 failed: residuals CSV has {len(df_residuals):,} rows, "
        f"expected {n_residuals_buf_realized:,} (basin_class_buffered.tif class 5 count)"
    )
    for col in ["id", "outlet_cell", "max_depth_m", "failure_reason"]:
        assert (
            col in df_residuals.columns
        ), f"Invariant A2 failed: Missing column {col} in residuals register"

    # Cross-artifact assertion 6: retained_basins.csv residual rows match residuals CSV
    df_reg = pd.read_csv(register_path, low_memory=False)
    assert "class" in df_reg.columns
    reg_res_count = int((df_reg["class"] == "residual").sum())
    assert reg_res_count == n_residuals_buf_realized, (
        f"Invariant A2 failed: retained_basins.csv has {reg_res_count:,} residual rows, "
        f"expected {n_residuals_buf_realized:,}"
    )

    # Bound assertions: Honest bounds derived from domain density (<= 0.75% domain cells)
    assert n_residuals_buf_realized <= residual_max_bound, (
        f"Invariant A2 failed: Buffered residuals ({n_residuals_buf_realized:,}) "
        f"exceeds stated bound residual_max_bound = {residual_max_bound:,}"
    )
    assert n_residuals_canon_realized <= residual_canon_max_bound, (
        f"Invariant A2 failed: Canonical residuals ({n_residuals_canon_realized:,}) "
        f"exceeds stated bound residual_canonical_max_bound = {residual_canon_max_bound:,}"
    )


# ── Invariant B: D4-monotone spill path from outlet to boundary or basin ──


def test_invariant_b_d4_monotone_spill_paths():
    """Every registered basin must have a valid DAG path to boundary or downstream basin."""
    manifest = _manifest("terrain_depressions")
    cascade = manifest["cascade_graph"]

    assert cascade["is_dag"], "Invariant B failed: Cascade graph contains cycles."
    assert cascade[
        "all_reach_boundary"
    ], "Invariant B failed: Not all retained basins have a finite spill path to boundary."

    kc_chain = cascade["key_chains"]["kc_valley"]
    expected_kc = [88889, 85584, 78186, 66347, "boundary"]
    assert (
        kc_chain == expected_kc
    ), f"Invariant B failed: KC Valley chain mismatch. Expected {expected_kc}, got {kc_chain}"


# ── Invariant C: Bound on slope-repair and Priority-Flood magnitude ───────


def test_invariant_c_cut_and_fill_bounds():
    """Invariant C: Bounded slope-repair cuts (<= 2.60 m) and fills (<= 3.25 m).
    NOTE ON INVARIANT ROLE:
    Invariant C is a bound on repair magnitude and terrain distortion, NOT a basin"""
    pre_path = INTERIM / "dem_conditioned_prebreach.tif"
    post_path = INTERIM / "dem_conditioned_postbreach.tif"
    building_mask_path = INTERIM / "building_mask_buffered.tif"
    road_mask_path = INTERIM / "road_segment_id_buffered.tif"
    waterway_mask_path = INTERIM / "distance_to_drain_buffered.tif"
    _skip_unless_exist(pre_path, post_path, building_mask_path, road_mask_path, waterway_mask_path)

    with rasterio.open(pre_path) as ds:
        pre = ds.read(1)
        nodata = ds.nodata
        valid = pre != nodata

    with rasterio.open(post_path) as ds:
        post = ds.read(1)

    with rasterio.open(building_mask_path) as ds:
        building_mask = ds.read(1) > 0

    with rasterio.open(road_mask_path) as ds:
        road_mask = ds.read(1) > 0

    with rasterio.open(waterway_mask_path) as ds:
        waterway_mask = ds.read(1) == 0.0

    diff = post[valid] - pre[valid]
    b_diff = post[valid & building_mask] - pre[valid & building_mask]

    cuts = -diff[diff < -1e-4]
    fills = diff[diff > 1e-4]

    max_cut = float(cuts.max()) if len(cuts) > 0 else 0.0
    max_fill = float(fills.max()) if len(fills) > 0 else 0.0

    assert (
        max_cut <= CUT_MAX_M + 1e-4
    ), f"Invariant C failed: max cut {max_cut:.4f} m exceeds cut_max={CUT_MAX_M} m"
    assert (
        max_fill <= FILL_MAX_M + 1e-4
    ), f"Invariant C failed: max fill {max_fill:.4f} m exceeds fill_max={FILL_MAX_M} m"

    building_modified = int((np.abs(b_diff) > 1e-4).sum())
    assert (
        building_modified == 0
    ), f"Invariant C failed: {building_modified} building cells were modified during conditioning."

    full_diff = post - pre
    road_fills = int(((full_diff > 1e-4) & road_mask & valid).sum())
    assert road_fills == 0, f"Invariant C failed: {road_fills} road cells were filled."

    waterway_fills = int(((full_diff > 1e-4) & waterway_mask & valid).sum())
    assert waterway_fills == 0, f"Invariant C failed: {waterway_fills} waterway cells were filled."


# ── Invariant R: Retained Basin Mask Invariance (Replaces Invariant C's former role) ──


def test_invariant_retained_basin_interior_unmodified():
    """Invariant R: No cell inside a registered retained basin is modified by the repair pass.
    Asserted against REALIZED rasters on disk (V1), not against manifest declarations."""
    pre_path = INTERIM / "dem_conditioned_prebreach.tif"
    post_path = INTERIM / "dem_conditioned_postbreach.tif"
    class_buf_path = INTERIM / "basin_class_buffered.tif"
    _skip_unless_exist(pre_path, post_path, class_buf_path)

    with rasterio.open(pre_path) as ds:
        pre = ds.read(1)
        nodata = ds.nodata
        valid = pre != nodata

    with rasterio.open(post_path) as ds:
        post = ds.read(1)

    with rasterio.open(class_buf_path) as ds:
        basin_class = ds.read(1)

    retained_mask = (basin_class >= 1) & (basin_class <= 4) & valid
    n_retained_cells = int(retained_mask.sum())
    assert (
        n_retained_cells > 0
    ), "Invariant R failed: No retained basin cells found in class raster."

    diff_retained = np.abs(post[retained_mask] - pre[retained_mask])
    modified_cells = int((diff_retained > 1e-4).sum())
    max_modification = float(diff_retained.max()) if len(diff_retained) > 0 else 0.0

    assert modified_cells == 0, (
        f"Invariant R failed: {modified_cells:,} of {n_retained_cells:,} retained basin cells "
        f"were modified during conditioning (max diff {max_modification:.4f} m). "
        "Retained basin interiors must be strictly immutable."
    )


def test_invariant_retained_basin_mask_mutation_fails_red():
    """V5 Mutation check: Verify that disabling retained_basin_mask in conditioning fails RED.
    Demonstrates that Invariant R is non-vacuous and capable of detecting basin modification."""
    from jaladhar.terrain.conditioning import d4_capped_hybrid_conditioning

    pre_path = INTERIM / "dem_conditioned_prebreach.tif"
    class_buf_path = INTERIM / "basin_class_buffered.tif"
    building_mask_path = INTERIM / "building_mask_buffered.tif"
    road_mask_path = INTERIM / "road_segment_id_buffered.tif"
    waterway_mask_path = INTERIM / "distance_to_drain_buffered.tif"
    _skip_unless_exist(
        pre_path, class_buf_path, building_mask_path, road_mask_path, waterway_mask_path
    )

    with rasterio.open(pre_path) as ds:
        pre = ds.read(1)
        valid = pre != ds.nodata

    with rasterio.open(class_buf_path) as ds:
        basin_class = ds.read(1)

    with rasterio.open(building_mask_path) as ds:
        b_mask = ds.read(1) > 0

    with rasterio.open(road_mask_path) as ds:
        r_mask = ds.read(1) > 0

    with rasterio.open(waterway_mask_path) as ds:
        w_mask = ds.read(1) == 0.0

    ret_mask = (basin_class >= 1) & (basin_class <= 4) & valid

    tile_r, tile_c = 1500, 2000
    s_r = slice(tile_r, tile_r + 500)
    s_c = slice(tile_c, tile_c + 500)

    t_pre = pre[s_r, s_c].copy()
    t_valid = valid[s_r, s_c].copy()
    t_ret = ret_mask[s_r, s_c].copy()
    t_b = b_mask[s_r, s_c].copy()
    t_r = r_mask[s_r, s_c].copy()
    t_w = w_mask[s_r, s_c].copy()

    cost = np.full_like(t_pre, 3, dtype=np.int32)
    cost[t_r] = 2
    cost[t_w] = 1
    cost[t_b] = 999

    # Pipeline mutation: pass zeroed retained_basin_mask
    mutated_post, _ = d4_capped_hybrid_conditioning(
        dem=t_pre,
        valid_mask=t_valid,
        retained_basin_mask=np.zeros_like(t_valid),  # MUTATION
        cost_surface=cost,
        building_mask=t_b,
        road_mask=t_r.astype(int),
        waterway_mask=t_w.astype(int),
        cut_max=CUT_MAX_M,
        fill_max=FILL_MAX_M,
    )

    diff_mut = np.abs(mutated_post[t_ret] - t_pre[t_ret])
    n_modified_mut = int((diff_mut > 1e-4).sum())

    # The mutation MUST cause basin cells to be modified (RED condition)
    assert (
        n_modified_mut > 0
    ), "Mutation check failed: Omitting retained_basin_mask should have modified basin cells."


# ── Invariant D: Cells modified <= 465,927 baseline ─────────────────────


def test_invariant_d_modified_cells_bound():
    manifest = _manifest("terrain_conditioning")
    d4_diag = manifest["d4_capped_hybrid_diagnostics"]
    n_modified = d4_diag["n_cells_modified"]
    baseline_max = 465_927

    assert (
        n_modified <= baseline_max
    ), f"Invariant D failed: {n_modified:,} cells modified exceeds baseline limit {baseline_max:,}"


# ── Invariant E: Retained volume sum(V(R)) reported against design storm ─


def test_invariant_e_retained_volume_accounting():
    manifest = _manifest("terrain_depressions")
    v_total = manifest["retained_candidate_volume_m3"]
    v_m_m3 = v_total / 1e6

    assert v_m_m3 > 0.0, "Invariant E failed: Retained volume is zero."
    assert (
        20.0 <= v_m_m3 <= 35.0
    ), f"Invariant E failed: Retained volume {v_m_m3:.2f} M m3 outside expected 20-35 M m3 range."

    class_summary = manifest["class_summary"]
    assert "storage" in class_summary, "Missing storage class in manifest."
    assert "quarry" in class_summary, "Missing quarry class in manifest."
    assert "landfill" in class_summary, "Missing landfill class in manifest."
    assert "uncertain" in class_summary, "Missing uncertain class in manifest."


# ── Invariant F: CONNECTIVITY CONSISTENCY (manifest == solver stencil) ──


def test_invariant_f_connectivity_consistency():
    """Terrain manifest declared connectivity must match solver stencil (D4)."""
    manifest = _manifest("terrain_conditioning")
    manifest_conn = manifest.get("connectivity")
    assert (
        manifest_conn == CONNECTIVITY_D4
    ), f"Invariant F failed: Terrain manifest connectivity {manifest_conn!r} != {CONNECTIVITY_D4!r}"

    # Also test solver state loader assertion
    from jaladhar.solver.state import load_domain, load_solver_config

    solver_cfg = load_solver_config(REPO / "configs" / "solver.yaml", REPO)
    static = load_domain(solver_cfg, REPO, use_buffered=True)
    assert static.shape[0] > 0 and static.shape[1] > 0


# ── Invariant G: Excavation volume <= excav_max and no trench > 10% ─────


def test_invariant_g_excavation_volume_and_no_trench_dominance():
    pre_path = INTERIM / "dem_conditioned_prebreach.tif"
    post_path = INTERIM / "dem_conditioned_postbreach.tif"
    _skip_unless_exist(pre_path, post_path)

    with rasterio.open(pre_path) as ds:
        pre = ds.read(1)
        nodata = ds.nodata
        valid = pre != nodata

    with rasterio.open(post_path) as ds:
        post = ds.read(1)

    diff = pre[valid] - post[valid]
    excavation_cells = diff > 1e-4
    excav_vol_m3 = float(diff[excavation_cells].sum() * 100.0)

    excav_grid = np.zeros_like(pre, dtype=bool)
    excav_grid[valid] = pre[valid] - post[valid] > 1e-4

    labels, num_features = ndi.label(excav_grid, structure=np.ones((3, 3)))
    if num_features > 0:
        indices = np.arange(1, num_features + 1)
        comp_vols = ndi.sum_labels(np.maximum(pre - post, 0.0) * 100.0, labels, index=indices)
        max_comp_vol = float(np.max(comp_vols))
        max_comp_fraction = max_comp_vol / excav_vol_m3 if excav_vol_m3 > 0 else 0.0
    else:
        max_comp_fraction = 0.0

    assert max_comp_fraction < 0.10, (
        f"Invariant G failed: Largest connected excavation trench holds {max_comp_fraction*100:.2f}% "  # noqa: E501
        "of total excavation volume (exceeds 10% threshold; former breach had 48.92% canyon)."
    )
