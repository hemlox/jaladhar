"""- V1: Realized raster / grid state verified against disk and canonical Grid.
- V2: Independent observables named before writing checks.
- V5: Red-under-mutation demonstrated and documented for every invariant check.
- V7: Check scope stated beside claimed scope.
- Mass conservation: integral of produced rainfall field over domain and event"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from src.jaladhar.forcing.imerg import ImergHistoricalAdapter, compute_imerg_grid_mapping
from src.jaladhar.forcing.interface import (
    ForcingMode,
    RainfallEvent,
    RainfallInterval,
)
from src.jaladhar.terrain.grid import Grid, build_grid

REPO = Path(__file__).resolve().parents[1]


def _require_artifacts(*paths: Path, stage: str) -> None:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"BLOCKED: {stage} requires missing artifact(s): {missing}")


def _require_boundary() -> None:
    with open(REPO / "configs/domain_bengaluru.yaml") as f:
        cfg = yaml.safe_load(f)
    _require_artifacts(REPO / cfg["boundary"]["path"], stage="canonical domain grid")


@pytest.fixture(scope="session")
def domain_grid() -> Grid:
    _require_boundary()
    with open(REPO / "configs/domain_bengaluru.yaml") as f:
        cfg = yaml.safe_load(f)
    grid, _ = build_grid(cfg, REPO)
    return grid


@pytest.fixture(scope="session")
def forcing_config() -> dict[str, Any]:
    with open(REPO / "configs/forcing.yaml") as f:
        return yaml.safe_load(f)


# Invariant 1: Mode Exclusivity (Structural prohibition on blending)


def test_forcing_mode_exclusivity() -> None:
    """Invariant: retained modes are historical IMERG and IMD nowcast only."""
    assert ForcingMode.from_str("historical") == ForcingMode.HISTORICAL
    assert ForcingMode.from_str("nowcast") == ForcingMode.NOWCAST
    with pytest.raises(ValueError, match="Invalid forcing mode"):
        ForcingMode.from_str("forecast")

    forbidden_modes = ["blended", "combined", "hybrid", "all", "historical+forecast"]
    for bad in forbidden_modes:
        with pytest.raises(ValueError, match="Invalid forcing mode"):
            ForcingMode.from_str(bad)


# Invariant 2: Synthetic Event Mass Conservation


def test_synthetic_event_mass_conservation() -> None:
    """Invariant: domain integral over event equals reported total volume to float tolerance."""
    h, w = 100, 100
    res = 10.0
    native_ids = np.zeros((h, w), dtype=np.int32)
    t0 = datetime(2022, 9, 5, 0, 0, tzinfo=UTC)

    grid1 = np.full((h, w), 5.0, dtype=np.float32)
    grid2 = np.full((h, w), 10.0, dtype=np.float32)
    grid3 = np.full((h, w), 2.5, dtype=np.float32)

    iv1 = RainfallInterval(t0, 30.0, grid1, native_ids, distinct_native_cells=1)
    iv2 = RainfallInterval(t0, 30.0, grid2, native_ids, distinct_native_cells=1)
    iv3 = RainfallInterval(t0, 30.0, grid3, native_ids, distinct_native_cells=1)

    event = RainfallEvent(
        mode=ForcingMode.HISTORICAL,
        intervals=[iv1, iv2, iv3],
        cell_resolution_m=res,
        source_name="synthetic_test",
    )

    assert event.num_intervals == 3
    assert event.total_duration_hours == 1.5
    assert pytest.approx(event.areal_mean_total_mm, rel=1e-6) == 17.5
    assert pytest.approx(event.total_volume_m3, rel=1e-6) == 17500.0
    assert event.verify_mass_conservation() is True


# Invariant 3: IMERG Adapter Native Cell Coverage & Independent Targets


def test_imerg_native_cell_coverage(domain_grid: Grid) -> None:
    """Invariant: canonical 10 m grid intersects exactly 16 distinct IMERG cells (4x4 block).
    Honesty constraint: downstream code cannot assume fine detail when source resolution is 0.1 deg.
    """
    _, _, native_ids, n_distinct = compute_imerg_grid_mapping(domain_grid)
    assert n_distinct == 16, f"Expected 16 distinct IMERG cells covering domain, got {n_distinct}"
    assert native_ids.shape == (domain_grid.height, domain_grid.width)
    assert len(np.unique(native_ids)) == 16


def test_imerg_historical_adapter_sept5_event() -> None:
    """Invariant: IMERG adapter loads 5 Sept 2022 and satisfies mass conservation.
    - Mass conservation verified to float tolerance"""
    _require_boundary()
    with open(REPO / "configs/forcing.yaml") as f:
        forcing_cfg = yaml.safe_load(f)
    imerg_dir = REPO / forcing_cfg["historical"]["granules_dir"]
    granules = sorted(imerg_dir.glob("*.20220905-*.HDF5")) if imerg_dir.exists() else []
    if len(granules) != 48:
        pytest.skip(
            "BLOCKED: IMERG 2022-09-05 replay requires 48 complete HDF5 granules; "
            f"found {len(granules)} in {imerg_dir}"
        )
    adapter = ImergHistoricalAdapter(REPO / "configs/forcing.yaml")
    st = datetime(2022, 9, 5, 0, 0, tzinfo=UTC)
    et = datetime(2022, 9, 5, 23, 30, tzinfo=UTC)

    event = adapter.get_forcing(st, et)

    assert event.mode == ForcingMode.HISTORICAL
    assert event.num_intervals == 48
    assert event.total_duration_hours == 24.0
    assert event.distinct_native_cells == 16

    assert pytest.approx(event.peak_hourly_rate_mm_hr, abs=0.1) == 14.47

    assert pytest.approx(event.areal_mean_total_mm, abs=0.1) == 37.30

    # Mass conservation
    assert event.verify_mass_conservation() is True
    assert event.total_volume_m3 > 44_000_000.0
