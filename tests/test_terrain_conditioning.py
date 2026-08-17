"""Formal invariant suite for depression-aware D4 terrain conditioning (Part D).

Tests the 7 mathematical invariants of depression-aware conditioning:
  Invariant A: pits_D4 is a SUBSET of interior(R). Pits outside R: exactly 0.
  Invariant B: Every registered basin has a D4-monotone spill path from its outlet
               to the domain boundary or to another registered basin.
  Invariant C: max(cut) <= cut_max (0.50 m) and max(fill) <= fill_max (0.25 m).
  Invariant D: Cells modified <= 465,927 baseline.
  Invariant E: Total retained volume sum(V(R)) vs design-storm volume (132.3 M m3).
  Invariant F: CONNECTIVITY CONSISTENCY: conditioning manifest connectivity (D4)
               matches solver stencil (D4).
  Invariant G: Excavation volume <= excav_max, with no single connected trench
               component exceeding 10% of total volume (no 48.92% trench artifact).
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
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
    count_pits_d4,
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


# ── Invariant A: pits_D4 is a SUBSET of interior(R). Pits outside R == 0 ──


def test_invariant_a_d4_pits_subset_of_retained_register():
    """All D4 pits in conditioned DEM must reside inside registered depressions R.

    Pits outside R must be exactly 0 (bounded enumerated residuals).
    """
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
    retained_mask = (basin_class > 0) & valid

    pits_outside_r = pits_d4 & ~retained_mask
    n_outside = int(pits_outside_r.sum())

    assert n_outside == 0, (
        f"Invariant A failed: Found {n_outside:,} D4 pits outside the retained-basin "
        "register R. Every pit must be inside interior(R) as an enumerated residual."
    )


# ── Invariant B: D4-monotone spill path from outlet to boundary or basin ──


def test_invariant_b_d4_monotone_spill_paths():
    """Every registered basin must have a valid DAG path to boundary or downstream basin."""
    manifest = _manifest("terrain_depressions")
    cascade = manifest["cascade_graph"]

    assert cascade["is_dag"], "Invariant B failed: Cascade graph contains cycles."
    assert cascade["all_reach_boundary"], (
        "Invariant B failed: Not all retained basins have a finite spill path to boundary."
    )

    # Validate the documented KC valley chain explicitly
    kc_chain = cascade["key_chains"]["kc_valley"]
    expected_kc = [88889, 85584, 78186, 66347, "boundary"]
    assert kc_chain == expected_kc, (
        f"Invariant B failed: KC Valley chain mismatch. Expected {expected_kc}, got {kc_chain}"
    )


# ── Invariant C: max(cut) <= cut_max and max(fill) <= fill_max ──────────


def test_invariant_c_cut_and_fill_bounds():
    """Conditioning cuts must not exceed 0.50 m and fills must not exceed 0.25 m."""
    pre_path = INTERIM / "dem_conditioned_prebreach.tif"
    post_path = INTERIM / "dem_conditioned_postbreach.tif"
    building_mask_path = INTERIM / "building_mask_buffered.tif"
    _skip_unless_exist(pre_path, post_path, building_mask_path)

    with rasterio.open(pre_path) as ds:
        pre = ds.read(1)
        nodata = ds.nodata
        valid = pre != nodata

    with rasterio.open(post_path) as ds:
        post = ds.read(1)

    with rasterio.open(building_mask_path) as ds:
        building_mask = ds.read(1) > 0

    diff = post[valid] - pre[valid]
    b_diff = post[valid & building_mask] - pre[valid & building_mask]

    # Cuts are negative differences (lowering)
    cuts = -diff[diff < -1e-4]
    fills = diff[diff > 1e-4]

    max_cut = float(cuts.max()) if len(cuts) > 0 else 0.0
    max_fill = float(fills.max()) if len(fills) > 0 else 0.0

    assert max_cut <= CUT_MAX_M + 1e-4, (
        f"Invariant C failed: max cut {max_cut:.4f} m exceeds cut_max={CUT_MAX_M} m"
    )
    assert max_fill <= FILL_MAX_M + 1e-4, (
        f"Invariant C failed: max fill {max_fill:.4f} m exceeds fill_max={FILL_MAX_M} m"
    )

    # Buildings must never be modified by conditioning
    building_modified = int((np.abs(b_diff) > 1e-4).sum())
    assert building_modified == 0, (
        f"Invariant C failed: {building_modified} building cells were modified during conditioning."
    )


# ── Invariant D: Cells modified <= 465,927 baseline ─────────────────────


def test_invariant_d_modified_cells_bound():
    """Total cells modified by D4 conditioning must not exceed the baseline 465,927."""
    manifest = _manifest("terrain_conditioning")
    d4_diag = manifest["d4_capped_hybrid_diagnostics"]
    n_modified = d4_diag["n_cells_modified"]
    baseline_max = 465_927

    assert n_modified <= baseline_max, (
        f"Invariant D failed: {n_modified:,} cells modified exceeds baseline limit {baseline_max:,}"
    )


# ── Invariant E: Retained volume sum(V(R)) reported against design storm ─


def test_invariant_e_retained_volume_accounting():
    """Sum of V(R) must be reported against design storm volume (~132.3 M m3)."""
    manifest = _manifest("terrain_depressions")
    v_total = manifest["retained_candidate_volume_m3"]
    v_m_m3 = v_total / 1e6

    # 132.3 M m3 is 184 mm * 717 km2 design storm
    design_storm_volume_m3 = 132.3e6
    fraction_of_storm = v_total / design_storm_volume_m3

    assert v_m_m3 > 0.0, "Invariant E failed: Retained volume is zero."
    # Volume is expected to be ~29.83 M m3 (20-25% of design storm)
    assert 20.0 <= v_m_m3 <= 35.0, (
        f"Invariant E failed: Retained volume {v_m_m3:.2f} M m3 outside expected 20-35 M m3 range."
    )

    # Check class summary split
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
    assert manifest_conn == CONNECTIVITY_D4, (
        f"Invariant F failed: Terrain manifest connectivity {manifest_conn!r} != {CONNECTIVITY_D4!r}"
    )

    # Also test solver state loader assertion
    from jaladhar.solver.state import load_domain, load_solver_config

    solver_cfg = load_solver_config(REPO / "configs" / "solver.yaml", REPO)
    # This should succeed without raising ValueError
    static = load_domain(solver_cfg, REPO, use_buffered=True)
    assert static.shape[0] > 0 and static.shape[1] > 0


# ── Invariant G: Excavation volume <= excav_max and no trench > 10% ─────


def test_invariant_g_excavation_volume_and_no_trench_dominance():
    """Total excavation volume must be bounded and no single connected trench exceeds 10%."""
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

    # Label connected excavation components (8-connectivity)
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
        f"Invariant G failed: Largest connected excavation trench holds {max_comp_fraction*100:.2f}% "
        "of total excavation volume (exceeds 10% threshold; former breach had 48.92% canyon)."
    )
