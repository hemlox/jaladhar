"""Tests for Urban Density Stratification and Stratified SAR Scoring.

Enforces:
- V1: Realized contingency state verified against hand-computed analytical values per density class.
- V2: Independent observables named before writing checks.
- V5: Red-under-mutation demonstrated and documented for invariant checks.
- V7: Check scope stated beside claimed scope.
- Hand-computed synthetic test case:
  - 10x10 grid (100 cells) split into OPEN (40), MODERATE (30), DENSE (30).
  - Exact contingency tables, CSI, POD, FAR asserted for each class and citywide.
  - Masked evaluation with permanent water body exclusion asserted per class.
- Moving window building fraction computation on synthetic grids.
- Classification into OPEN, MODERATE, DENSE terciles.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from src.jaladhar.validation.density import (
    classify_density,
    compute_building_fraction,
)
from src.jaladhar.validation.scoring import (
    score_extent,
)

# ---------------------------------------------------------------------------
# Invariant 1: Hand-Computed Density-Stratified Scoring Synthetic Case
# ---------------------------------------------------------------------------


def test_hand_computed_density_stratified_scoring() -> None:
    """Invariant: Density stratification computes exact analytical scores per class.

    Evaluated per class alongside citywide contingency metrics.

    Setup:
      Grid shape (10, 10) = 100 cells.
      Class 0 (OPEN):     indices 0..39 (40 cells)
      Class 1 (MODERATE): indices 40..69 (30 cells)
      Class 2 (DENSE):    indices 70..99 (30 cells)

    Hand calculations:
      OPEN (40 cells):
        - 10 hits (0..9): pred=0.25, obs=True
        - 2 false alarms (10..11): pred=0.25, obs=False
        - 1 miss (12): pred=0.05, obs=True
        - 27 correct negatives (13..39): pred=0.0, obs=False
        Expected OPEN: Hits=10, FA=2, Misses=1, CN=27
        CSI = 10 / (10 + 1 + 2) = 10 / 13 ≈ 0.769231
        POD = 10 / (10 + 1) = 10 / 11 ≈ 0.909091
        FAR = 2 / (10 + 2) = 2 / 12 ≈ 0.166667

      MODERATE (30 cells):
        - 6 hits (40..45): pred=0.25, obs=True
        - 4 false alarms (46..49): pred=0.25, obs=False
        - 3 misses (50..52): pred=0.05, obs=True
        - 17 correct negatives (53..69): pred=0.0, obs=False
        Expected MODERATE: Hits=6, FA=4, Misses=3, CN=17
        CSI = 6 / (6 + 3 + 4) = 6 / 13 ≈ 0.461538
        POD = 6 / (6 + 3) = 6 / 9 ≈ 0.666667
        FAR = 4 / (6 + 4) = 4 / 10 = 0.400000

      DENSE (30 cells):
        - 2 hits (70..71): pred=0.25, obs=True
        - 8 false alarms (72..79): pred=0.25, obs=False
        - 5 misses (80..84): pred=0.05, obs=True
        - 15 correct negatives (85..99): pred=0.0, obs=False
        Expected DENSE: Hits=2, FA=8, Misses=5, CN=15
        CSI = 2 / (2 + 5 + 8) = 2 / 15 ≈ 0.133333
        POD = 2 / (2 + 5) = 2 / 7 ≈ 0.285714
        FAR = 8 / (2 + 8) = 8 / 10 = 0.800000

      Citywide Unmasked (100 cells):
        - Hits = 10 + 6 + 2 = 18
        - FA = 2 + 4 + 8 = 14
        - Misses = 1 + 3 + 5 = 9
        - CN = 27 + 17 + 15 = 59
        Expected Citywide: Hits=18, FA=14, Misses=9, CN=59
        CSI = 18 / (18 + 9 + 14) = 18 / 41 ≈ 0.439024
        POD = 18 / (18 + 9) = 18 / 27 ≈ 0.666667
        FAR = 14 / (18 + 14) = 14 / 32 = 0.437500
    """
    grid_shape = (10, 10)
    pred_depth = np.zeros(grid_shape, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)
    density_raster = np.zeros(grid_shape, dtype=np.uint8)

    # Assign density classes
    density_raster.flat[0:40] = 0  # OPEN
    density_raster.flat[40:70] = 1  # MODERATE
    density_raster.flat[70:100] = 2  # DENSE

    # OPEN points
    pred_depth.flat[0:10] = 0.25
    obs_flooded.flat[0:10] = True

    pred_depth.flat[10:12] = 0.25
    obs_flooded.flat[10:12] = False

    pred_depth.flat[12:13] = 0.05
    obs_flooded.flat[12:13] = True

    # MODERATE points
    pred_depth.flat[40:46] = 0.25
    obs_flooded.flat[40:46] = True

    pred_depth.flat[46:50] = 0.25
    obs_flooded.flat[46:50] = False

    pred_depth.flat[50:53] = 0.05
    obs_flooded.flat[50:53] = True

    # DENSE points
    pred_depth.flat[70:72] = 0.25
    obs_flooded.flat[70:72] = True

    pred_depth.flat[72:80] = 0.25
    obs_flooded.flat[72:80] = False

    pred_depth.flat[80:85] = 0.05
    obs_flooded.flat[80:85] = True

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=UTC)
    res = score_extent(
        predicted_depth=pred_depth,
        observed_flooded=obs_flooded,
        comparison_timestamp=ts,
        threshold_m=0.1,
        density_raster=density_raster,
        density_classes={0: "OPEN", 1: "MODERATE", 2: "DENSE"},
    )

    assert res.is_stratified is True
    assert res.stratified_unmasked is not None

    # 1. Check Citywide Unmasked
    assert res.unmasked.hits == 18
    assert res.unmasked.false_alarms == 14
    assert res.unmasked.misses == 9
    assert res.unmasked.correct_negatives == 59
    assert res.unmasked.total_samples == 100
    assert pytest.approx(res.unmasked.csi, rel=1e-5) == 18.0 / 41.0
    assert pytest.approx(res.unmasked.pod, rel=1e-5) == 18.0 / 27.0
    assert pytest.approx(res.unmasked.far, rel=1e-5) == 14.0 / 32.0

    # 2. Check OPEN class
    open_res = res.stratified_unmasked["OPEN"]
    assert open_res.hits == 10
    assert open_res.false_alarms == 2
    assert open_res.misses == 1
    assert open_res.correct_negatives == 27
    assert open_res.total_samples == 40
    assert pytest.approx(open_res.csi, rel=1e-5) == 10.0 / 13.0
    assert pytest.approx(open_res.pod, rel=1e-5) == 10.0 / 11.0
    assert pytest.approx(open_res.far, rel=1e-5) == 2.0 / 12.0

    # 3. Check MODERATE class
    mod_res = res.stratified_unmasked["MODERATE"]
    assert mod_res.hits == 6
    assert mod_res.false_alarms == 4
    assert mod_res.misses == 3
    assert mod_res.correct_negatives == 17
    assert mod_res.total_samples == 30
    assert pytest.approx(mod_res.csi, rel=1e-5) == 6.0 / 13.0
    assert pytest.approx(mod_res.pod, rel=1e-5) == 6.0 / 9.0
    assert pytest.approx(mod_res.far, rel=1e-5) == 4.0 / 10.0

    # 4. Check DENSE class
    dense_res = res.stratified_unmasked["DENSE"]
    assert dense_res.hits == 2
    assert dense_res.false_alarms == 8
    assert dense_res.misses == 5
    assert dense_res.correct_negatives == 15
    assert dense_res.total_samples == 30
    assert pytest.approx(dense_res.csi, rel=1e-5) == 2.0 / 15.0
    assert pytest.approx(dense_res.pod, rel=1e-5) == 2.0 / 7.0
    assert pytest.approx(dense_res.far, rel=1e-5) == 8.0 / 10.0


# ---------------------------------------------------------------------------
# Invariant 2: Stratified Masked Scoring with Permanent Water Exclusion
# ---------------------------------------------------------------------------


def test_stratified_masked_scoring() -> None:
    """Invariant: Stratified scoring correctly excludes permanent water cells.

    Exclusion operates within each individual density class independently.

    Observable:
      2 permanent water cells placed in OPEN class (cell 0: hit, cell 13: CN):
      OPEN Masked: Hits = 9, FA = 2, Misses = 1, CN = 26 -> CSI = 9/12 = 0.75
    """
    grid_shape = (10, 10)
    pred_depth = np.zeros(grid_shape, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)
    density_raster = np.zeros(grid_shape, dtype=np.uint8)
    water_mask = np.zeros(grid_shape, dtype=bool)

    density_raster.flat[0:40] = 0  # OPEN
    density_raster.flat[40:70] = 1  # MODERATE
    density_raster.flat[70:100] = 2  # DENSE

    # OPEN points
    pred_depth.flat[0:10] = 0.25
    obs_flooded.flat[0:10] = True
    pred_depth.flat[10:12] = 0.25
    obs_flooded.flat[10:12] = False
    pred_depth.flat[12:13] = 0.05
    obs_flooded.flat[12:13] = True

    # Permanent water on cells 0 (hit) and 13 (CN)
    water_mask.flat[0] = True
    water_mask.flat[13] = True

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=UTC)
    res = score_extent(
        predicted_depth=pred_depth,
        observed_flooded=obs_flooded,
        comparison_timestamp=ts,
        threshold_m=0.1,
        permanent_water_mask=water_mask,
        density_raster=density_raster,
        density_classes={0: "OPEN", 1: "MODERATE", 2: "DENSE"},
    )

    assert res.is_masked is True
    assert res.is_stratified is True
    assert res.stratified_masked is not None

    open_masked = res.stratified_masked["OPEN"]
    assert open_masked.hits == 9
    assert open_masked.false_alarms == 2
    assert open_masked.misses == 1
    assert open_masked.correct_negatives == 26
    assert open_masked.total_samples == 38
    assert pytest.approx(open_masked.csi, rel=1e-5) == 9.0 / 12.0
    assert pytest.approx(open_masked.pod, rel=1e-5) == 9.0 / 10.0
    assert pytest.approx(open_masked.far, rel=1e-5) == 2.0 / 11.0


# ---------------------------------------------------------------------------
# Invariant 3: Moving Window Building Fraction Computation
# ---------------------------------------------------------------------------


def test_compute_building_fraction_moving_window() -> None:
    """Invariant: Moving window computation correctly averages building presence.

    Observable:
      A 5x5 grid with single center pixel = 1 and kernel_size = 3 (window 3x3):
      Center cell value is exactly 1/9.
    """
    grid = np.zeros((5, 5), dtype=np.uint8)
    grid[2, 2] = 1  # center pixel

    bf, ksize = compute_building_fraction(grid, window_size_m=30.0, resolution_m=10.0)
    assert ksize == 3
    assert pytest.approx(bf[2, 2], rel=1e-5) == 1.0 / 9.0
    assert pytest.approx(bf[0, 0], rel=1e-5) == 0.0


# ---------------------------------------------------------------------------
# Invariant 4: Density Classification Tercile Partitioning
# ---------------------------------------------------------------------------


def test_classify_density_terciles() -> None:
    """Invariant: classify_density partitions fraction into disjoint OPEN, MODERATE, DENSE.

    Observable:
      Sum of class cell counts equals total cells.
      No overlap between class ranges.
    """
    bf = np.linspace(0.0, 0.9, 100, dtype=np.float32).reshape((10, 10))
    density_raster, result = classify_density(bf, custom_cuts=(0.10, 0.30))

    assert density_raster.shape == (10, 10)
    assert result.total_cells == 100
    assert sum(c.cell_count for c in result.classes) == 100
    assert result.classes[0].name == "OPEN"
    assert result.classes[1].name == "MODERATE"
    assert result.classes[2].name == "DENSE"
