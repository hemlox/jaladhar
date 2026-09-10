"""- V1: Realized contingency state verified against hand-computed analytical values.
- V2: Independent observables named before writing checks.
- V5: Red-under-mutation demonstrated and documented for every invariant check.
- V7: Check scope stated beside claimed scope.
- Hand-computed synthetic test cases:
- Nothing observed: hits=0, misses=0 -> POD=NaN, CSI=0.0 (if FA>0) or NaN (if FA=0).
- Clean dry domain (nothing pred, nothing obs): CSI is NaN (assert NOT silently returning 0.0)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest

from src.jaladhar.validation.scoring import (
    score_extent,
    score_threshold_curve,
)

# Invariant 1: Hand-Computed Benchmark Synthetic Case


def test_hand_computed_synthetic_case() -> None:
    """Invariant: 10x10 grid with 20 pred, 15 obs, 12 overlap yields exact analytical scores."""
    grid_shape = (10, 10)
    pred_depth = np.zeros(grid_shape, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)

    pred_depth.flat[0:12] = 0.25
    obs_flooded.flat[0:12] = True

    pred_depth.flat[12:20] = 0.25
    obs_flooded.flat[12:20] = False

    pred_depth.flat[20:23] = 0.05
    obs_flooded.flat[20:23] = True

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=UTC)
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


# Invariant 2: Degenerate Cases Handling


def test_degenerate_nothing_predicted() -> None:
    """Invariant: When nothing is predicted, Hits=0, Misses=obs_count, FA=0 -> CSI=0, POD=0, FAR=NaN."""  # noqa: E501
    grid_shape = (10, 10)
    pred_depth = np.zeros(grid_shape, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)
    obs_flooded.flat[0:15] = True  # 15 observed flooded

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=UTC)
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
    assert math.isnan(t.far)


def test_degenerate_everything_predicted() -> None:
    """Invariant: When everything is predicted, Misses=0 -> POD=1.0, CSI=obs/total."""
    grid_shape = (10, 10)
    pred_depth = np.full(grid_shape, 1.0, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)
    obs_flooded.flat[0:20] = True  # 20 observed flooded

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=UTC)
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
    assert pytest.approx(t.csi, rel=1e-6) == 20.0 / 100.0
    assert pytest.approx(t.far, rel=1e-6) == 80.0 / 100.0


def test_degenerate_nothing_observed_and_clean_dry_domain() -> None:
    """Invariant: When nothing is observed and nothing is predicted, CSI/POD are undefined (NaN)."""
    grid_shape = (10, 10)
    pred_depth = np.zeros(grid_shape, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=UTC)
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

    assert math.isnan(t.csi), "CSI on 0 events must be NaN, not silently 0.0"
    assert math.isnan(t.pod), "POD on 0 observed events must be NaN"
    assert math.isnan(t.far), "FAR on 0 predicted events must be NaN"


# Invariant 3: Permanent Water Body Exclusion


def test_permanent_water_exclusion() -> None:
    """Invariant: Permanently wet water bodies must be excluded from masked scores."""
    grid_shape = (10, 10)
    pred_depth = np.zeros(grid_shape, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)

    pred_depth.flat[0:12] = 0.5
    obs_flooded.flat[0:12] = True

    pred_depth.flat[12:20] = 0.5
    obs_flooded.flat[12:20] = False

    pred_depth.flat[20:23] = 0.0
    obs_flooded.flat[20:23] = True

    water_mask = np.zeros(grid_shape, dtype=bool)
    water_mask.flat[0:2] = True

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=UTC)
    res = score_extent(
        predicted_depth=pred_depth,
        observed_flooded=obs_flooded,
        comparison_timestamp=ts,
        threshold_m=0.1,
        permanent_water_mask=water_mask,
    )

    assert res.is_masked is True
    assert res.excluded_water_cells == 2

    assert res.unmasked.hits == 12
    assert pytest.approx(res.unmasked.csi, rel=1e-6) == 12.0 / 23.0

    assert res.masked is not None
    assert res.masked.hits == 10
    assert res.masked.misses == 3
    assert res.masked.false_alarms == 8
    assert res.masked.correct_negatives == 77
    assert res.masked.total_samples == 98
    assert pytest.approx(res.masked.csi, rel=1e-6) == 10.0 / 21.0
    assert round(res.masked.csi, 4) == 0.4762


# Invariant 4: Comparison Timestamp Required (No Default)


def test_comparison_timestamp_is_mandatory() -> None:
    """Invariant: comparison_timestamp is a required argument with no default."""
    pred_depth = np.zeros((5, 5), dtype=np.float32)
    obs_flooded = np.zeros((5, 5), dtype=bool)

    with pytest.raises(TypeError):
        score_extent(pred_depth, obs_flooded)  # type: ignore

    with pytest.raises(TypeError, match="comparison_timestamp is a required datetime"):
        score_extent(pred_depth, obs_flooded, comparison_timestamp=None)  # type: ignore


# Invariant 5: Threshold Sweep Monotonicity


def test_score_threshold_curve_sweep() -> None:
    """Invariant: score_threshold_curve sweeps across depth thresholds."""
    grid_shape = (20, 20)
    np.random.seed(42)
    pred_depth = np.linspace(0.0, 1.0, 400, dtype=np.float32).reshape(grid_shape)
    obs_flooded = pred_depth > 0.3

    ts = datetime(2022, 9, 5, 0, 40, tzinfo=UTC)
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
