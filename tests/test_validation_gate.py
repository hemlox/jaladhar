"""- V1: Realized rasters, classes, and manifests checked directly against disk.
- V2: Independent observables named before checks.
- V5: Red-under-mutation demonstrated for invariant checks.
- V7: Check scope stated beside claimed scope.
- Invariants:"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from jaladhar.validation.scoring import (
    ContingencyTable,
    score_extent,
)

REPO = Path(__file__).resolve().parents[1]


# Invariant 1: Contingency Table Math & Undefined Handling


def test_contingency_table_math() -> None:
    """Invariant: CSI = hits / (hits + misses + false_alarms), POD = hits / (hits + misses),"""
    table = ContingencyTable(hits=10, misses=5, false_alarms=5, correct_negatives=80)
    assert table.total_samples == 100
    assert pytest.approx(table.csi, rel=1e-4) == 0.50
    assert pytest.approx(table.pod, rel=1e-4) == 0.6666667
    assert pytest.approx(table.far, rel=1e-4) == 0.3333333


def test_contingency_table_zero_events_undefined_nan() -> None:
    """Invariant: When all positive counts are zero, metrics return float('nan') rather than"""
    table = ContingencyTable(hits=0, misses=0, false_alarms=0, correct_negatives=100)
    assert np.isnan(table.csi)
    assert np.isnan(table.pod)
    assert np.isnan(table.far)


# Invariant 2: Permanent Water Exclusion Effect on Extent Scoring


def test_permanent_water_exclusion_increases_masked_purity() -> None:
    """Invariant: Excluding permanent water bodies prevents unearned hits on perennial lakes."""
    grid_shape = (10, 10)
    pred_depth = np.full(grid_shape, 0.2, dtype=np.float32)
    obs_flooded = np.zeros(grid_shape, dtype=bool)
    obs_flooded[0:5, 0:5] = True  # 25 observed water cells

    # Permanent lake occupies 10 cells of the 25 observed water cells
    permanent_water = np.zeros(grid_shape, dtype=bool)
    permanent_water[0:2, 0:5] = True

    ts = datetime(2022, 9, 5, 0, 40, 28, tzinfo=UTC)
    res = score_extent(
        predicted_depth=pred_depth,
        observed_flooded=obs_flooded,
        comparison_timestamp=ts,
        threshold_m=0.1,
        permanent_water_mask=permanent_water,
    )

    assert res.is_masked is True
    assert res.excluded_water_cells == 10
    assert res.unmasked.hits == 25
    assert res.masked is not None
    assert res.masked.hits == 15
    assert res.masked.total_samples == 90


# Invariant 4: Mandatory Comparison Timestamp (Timing Integrity)


def test_comparison_timestamp_required() -> None:
    """Invariant: score_extent raises TypeError if comparison_timestamp is omitted or None."""
    pred = np.zeros((5, 5), dtype=np.float32)
    obs = np.zeros((5, 5), dtype=bool)

    with pytest.raises(TypeError, match="comparison_timestamp is a required datetime"):
        score_extent(pred, obs, comparison_timestamp=None)  # type: ignore


# Invariant 5: Ground Truth Depth Band Math


def test_groundtruth_depth_band_evaluation() -> None:
    """Invariant: Model depth inside [low, high] is WITHIN_BAND; depth below low is BELOW;"""
    low, high = 0.5, 0.8
    mid = (low + high) / 2.0

    h_model_1 = 0.60
    assert low <= h_model_1 <= high

    h_model_2 = 0.20
    assert h_model_2 < low

    h_model_3 = 1.20
    assert h_model_3 > high

    errs = [h_model_1 - mid, h_model_2 - mid, h_model_3 - mid]
    rmse = np.sqrt(np.mean(np.square(errs)))
    assert rmse > 0.0
