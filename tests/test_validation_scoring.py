"""Tests for Validation Scoring Harness.

Enforces:
- V1: Realized contingency state verified against hand-computed analytical values.
- V2: Independent observables named before writing checks.
- V5: Red-under-mutation demonstrated and documented for every invariant check.
- V7: Check scope stated beside claimed scope.
- Hand-computed synthetic test cases:
  - Benchmark 10x10 case: 20 pred, 15 obs, 12 overlap -> hits=12, misses=3, false_alarms=8,
    CSI = 12/23 = 0.5217, POD = 0.8, FAR = 0.4.
- Degenerate cases:
  - Nothing predicted: hits=0, misses>0 -> CSI=0.0, POD=0.0, FAR=NaN.
  - Everything predicted: hits=obs, misses=0 -> POD=1.0, CSI=obs/total.
  - Nothing observed: hits=0, misses=0 -> POD=NaN, CSI=0.0 (if FA>0) or NaN (if FA=0).
  - Clean dry domain (nothing pred, nothing obs): CSI is NaN (assert NOT silently returning 0.0).
- Permanent water body exclusion:
  - Excludes permanently wet tanks/quarries from CSI/POD/FAR.
  - Reports both masked and unmasked scores.
- Temporal integrity:
  - comparison_timestamp is mandatory with no default; omitting it raises TypeError.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pytest

from src.jaladhar.validation.scoring import (
    score_extent,
    score_threshold_curve,
)


# ---------------------------------------------------------------------------
# Invariant 1: Hand-Computed Benchmark Synthetic Case
# ---------------------------------------------------------------------------


def test_hand_computed_synthetic_case() -> None:
    """Invariant: 10x10 grid with 20 pred, 15 obs, 12 overlap yields exact analytical scores.

    Hand calculation:
      - 12 cells (0..11): pred > 0.1, obs = True -> Hits = 12
      - 8 cells (12..19): pred > 0.1, obs = False -> False Alarms = 8
      - 3 cells (20..22): pred <= 0.1, obs = True -> Misses = 3
      - 77 cells (23..99): pred <= 0.1, obs = False -> Correct Negatives = 77
      Total = 100 cells.

    Expected:
      Hits = 12, Misses = 3, False Alarms = 8, Correct Negatives = 77
      CSI = 12 / (12 + 3 + 8) = 12 / 23 = 0.5217391304...
      POD = 12 / (12 + 3) = 12 / 15 = 0.8000000000...
      FAR = 8 / (12 + 8) = 8 / 20 = 0.4000000000...
    """
    grid_shape = (10, 10)
    pred_depth = np.zeros(grid_shape, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)

    # 12 hits
    pred_depth.flat[0:12] = 0.25
    obs_flooded.flat[0:12] = True

    # 8 false alarms
    pred_depth.flat[12:20] = 0.25
    obs_flooded.flat[12:20] = False

    # 3 misses
    pred_depth.flat[20:23] = 0.05  # below threshold 0.1
    obs_flooded.flat[20:23] = True

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=timezone.utc)
    res = score_extent(
        predicted_depth=pred_depth,
        observed_flooded=obs_flooded,
        comparison_timestamp=ts,
        threshold_m=0.1,
    )

    t = res.unmasked
    assert t.hits == 12
    assert t.misses == 3
    assert t.false_alarms == 8
    assert t.correct_negatives == 77
    assert t.total_samples == 100

    assert pytest.approx(t.csi, rel=1e-6) == 12.0 / 23.0
    assert round(t.csi, 4) == 0.5217
    assert pytest.approx(t.pod, rel=1e-6) == 0.8
    assert pytest.approx(t.far, rel=1e-6) == 0.4


# ---------------------------------------------------------------------------
# Invariant 2: Degenerate Cases Handling
# ---------------------------------------------------------------------------


def test_degenerate_nothing_predicted() -> None:
    """Invariant: When nothing is predicted, Hits=0, Misses=obs_count, FA=0 -> CSI=0, POD=0, FAR=NaN."""
    grid_shape = (10, 10)
    pred_depth = np.zeros(grid_shape, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)
    obs_flooded.flat[0:15] = True  # 15 observed flooded

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=timezone.utc)
    res = score_extent(
        predicted_depth=pred_depth,
        observed_flooded=obs_flooded,
        comparison_timestamp=ts,
        threshold_m=0.1,
    )

    t = res.unmasked
    assert t.hits == 0
    assert t.misses == 15
    assert t.false_alarms == 0
    assert t.correct_negatives == 85

    assert t.csi == 0.0
    assert t.pod == 0.0
    assert math.isnan(t.far)  # FAR is 0/0 -> NaN


def test_degenerate_everything_predicted() -> None:
    """Invariant: When everything is predicted, Misses=0 -> POD=1.0, CSI=obs/total."""
    grid_shape = (10, 10)
    pred_depth = np.full(grid_shape, 1.0, dtype=np.float32)  # all predicted flooded
    obs_flooded = np.zeros(grid_shape, dtype=bool)
    obs_flooded.flat[0:20] = True  # 20 observed flooded

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=timezone.utc)
    res = score_extent(
        predicted_depth=pred_depth,
        observed_flooded=obs_flooded,
        comparison_timestamp=ts,
        threshold_m=0.1,
    )

    t = res.unmasked
    assert t.hits == 20
    assert t.misses == 0
    assert t.false_alarms == 80
    assert t.correct_negatives == 0

    assert t.pod == 1.0
    assert pytest.approx(t.csi, rel=1e-6) == 20.0 / 100.0  # 0.20
    assert pytest.approx(t.far, rel=1e-6) == 80.0 / 100.0  # 0.80


def test_degenerate_nothing_observed_and_clean_dry_domain() -> None:
    """Invariant: When nothing is observed and nothing is predicted, CSI/POD are undefined (NaN).

    Observable: Clean dry domain (all zeros) returns math.isnan(csi) is True.
    It MUST NOT silently return 0.0.
    """
    grid_shape = (10, 10)
    pred_depth = np.zeros(grid_shape, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=timezone.utc)
    res = score_extent(
        predicted_depth=pred_depth,
        observed_flooded=obs_flooded,
        comparison_timestamp=ts,
        threshold_m=0.1,
    )

    t = res.unmasked
    assert t.hits == 0
    assert t.misses == 0
    assert t.false_alarms == 0
    assert t.correct_negatives == 100

    # CSI denom is hits + misses + false_alarms == 0 -> undefined (NaN)
    assert math.isnan(t.csi), "CSI on 0 events must be NaN, not silently 0.0"
    assert math.isnan(t.pod), "POD on 0 observed events must be NaN"
    assert math.isnan(t.far), "FAR on 0 predicted events must be NaN"


# ---------------------------------------------------------------------------
# Invariant 3: Permanent Water Body Exclusion
# ---------------------------------------------------------------------------


def test_permanent_water_exclusion() -> None:
    """Invariant: Permanently wet water bodies must be excluded from masked scores.

    Observable:
      Without mask: 12 hits, 3 misses, 8 FA -> CSI = 12/23 = 0.5217
      With mask (2 permanent water cells containing hits):
        Masked Hits = 10, Misses = 3, FA = 8 -> CSI = 10 / (10 + 3 + 8) = 10 / 21 = 0.4762
        Excluded cell count = 2
    """
    grid_shape = (10, 10)
    pred_depth = np.zeros(grid_shape, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)

    # 12 hits
    pred_depth.flat[0:12] = 0.5
    obs_flooded.flat[0:12] = True

    # 8 false alarms
    pred_depth.flat[12:20] = 0.5
    obs_flooded.flat[12:20] = False

    # 3 misses
    pred_depth.flat[20:23] = 0.0
    obs_flooded.flat[20:23] = True

    # Permanent water body covering cells 0 and 1
    water_mask = np.zeros(grid_shape, dtype=bool)
    water_mask.flat[0:2] = True

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=timezone.utc)
    res = score_extent(
        predicted_depth=pred_depth,
        observed_flooded=obs_flooded,
        comparison_timestamp=ts,
        threshold_m=0.1,
        permanent_water_mask=water_mask,
    )

    assert res.is_masked is True
    assert res.excluded_water_cells == 2

    # Unmasked (all 100 cells)
    assert res.unmasked.hits == 12
    assert pytest.approx(res.unmasked.csi, rel=1e-6) == 12.0 / 23.0

    # Masked (98 cells)
    assert res.masked is not None
    assert res.masked.hits == 10
    assert res.masked.misses == 3
    assert res.masked.false_alarms == 8
    assert res.masked.correct_negatives == 77
    assert res.masked.total_samples == 98
    assert pytest.approx(res.masked.csi, rel=1e-6) == 10.0 / 21.0
    assert round(res.masked.csi, 4) == 0.4762


# ---------------------------------------------------------------------------
# Invariant 4: Comparison Timestamp Required (No Default)
# ---------------------------------------------------------------------------


def test_comparison_timestamp_is_mandatory() -> None:
    """Invariant: comparison_timestamp is a required argument with no default.

    Observable: Calling score_extent without comparison_timestamp or with None raises TypeError.
    """
    pred_depth = np.zeros((5, 5), dtype=np.float32)
    obs_flooded = np.zeros((5, 5), dtype=bool)

    # Calling without comparison_timestamp raises TypeError (missing positional argument)
    with pytest.raises(TypeError):
        score_extent(pred_depth, obs_flooded)  # type: ignore

    # Passing None explicitly raises TypeError
    with pytest.raises(TypeError, match="comparison_timestamp is a required datetime"):
        score_extent(pred_depth, obs_flooded, comparison_timestamp=None)  # type: ignore


# ---------------------------------------------------------------------------
# Invariant 5: Threshold Sweep Monotonicity
# ---------------------------------------------------------------------------


def test_score_threshold_curve_sweep() -> None:
    """Invariant: score_threshold_curve sweeps across depth thresholds.

    Observable: Predicted flood count is monotonically non-increasing with threshold.
    """
    grid_shape = (20, 20)
    # Depths ranging from 0.0 to 1.0 m
    np.random.seed(42)
    pred_depth = np.linspace(0.0, 1.0, 400, dtype=np.float32).reshape(grid_shape)
    obs_flooded = pred_depth > 0.3

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=timezone.utc)
    thresholds = [0.1, 0.2, 0.3, 0.4, 0.5, 0.8]
    curve = score_threshold_curve(
        predicted_depth=pred_depth,
        observed_flooded=obs_flooded,
        comparison_timestamp=ts,
        thresholds_m=thresholds,
    )

    assert len(curve) == len(thresholds)
    pred_counts = [res.unmasked.hits + res.unmasked.false_alarms for res in curve]
    for i in range(1, len(pred_counts)):
        assert pred_counts[i] <= pred_counts[i - 1], "Predicted count must decrease with threshold"
