"""Tests for Rainfall Forcing Layer and Adapters.

Enforces:
- V1: Realized raster / grid state verified against disk and canonical Grid.
- V2: Independent observables named before writing checks.
- V5: Red-under-mutation demonstrated and documented for every invariant check.
- V7: Check scope stated beside claimed scope.
- Mass conservation: integral of produced rainfall field over domain and event
  equals total volume reported by adapter to float tolerance.
- Mode exclusivity: exactly one mode per run, blending/combining modes is structurally forbidden.
- Honesty constraint: 16 distinct IMERG cells cover the canonical grid (not 9).
- KSNDMC geolocation integrity: HOBLINAME join within bbox matches >= 235/266;
  forbidden join (RAINGAUGE -> LocationID) is 100% rejected.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import requests
import yaml

from src.jaladhar.forcing.imerg import ImergHistoricalAdapter, compute_imerg_grid_mapping
from src.jaladhar.forcing.interface import (
    ForcingMode,
    RainfallEvent,
    RainfallInterval,
)
from src.jaladhar.forcing.ksndmc import KsndmcNowcastAdapter, load_and_locate_gauges
from src.jaladhar.forcing.open_meteo import OpenMeteoForecastAdapter
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Invariant 1: Mode Exclusivity (Structural prohibition on blending)
# ---------------------------------------------------------------------------


def test_forcing_mode_exclusivity() -> None:
    """Invariant: exactly three modes exist (historical, nowcast, forecast).

    Blending or combined modes are structurally forbidden and must raise ValueError.
    Observable: ForcingMode.from_str('blended') raises ValueError.
    """
    assert ForcingMode.from_str("historical") == ForcingMode.HISTORICAL
    assert ForcingMode.from_str("nowcast") == ForcingMode.NOWCAST
    assert ForcingMode.from_str("forecast") == ForcingMode.FORECAST

    forbidden_modes = ["blended", "combined", "hybrid", "all", "historical+forecast"]
    for bad in forbidden_modes:
        with pytest.raises(ValueError, match="Invalid forcing mode"):
            ForcingMode.from_str(bad)


# ---------------------------------------------------------------------------
# Invariant 2: Synthetic Event Mass Conservation
# ---------------------------------------------------------------------------


def test_synthetic_event_mass_conservation() -> None:
    """Invariant: domain integral over event equals reported total volume to float tolerance.

    Observable: direct sum(depth * area) matches event.total_volume_m3 within 1e-4 relative tolerance.
    """
    h, w = 100, 100
    res = 10.0  # 10 m resolution -> 100 m^2 per cell
    native_ids = np.zeros((h, w), dtype=np.int32)
    t0 = datetime(2022, 9, 5, 0, 0, tzinfo=timezone.utc)

    # 3 intervals with known rainfall depths
    grid1 = np.full((h, w), 5.0, dtype=np.float32)  # 5 mm
    grid2 = np.full((h, w), 10.0, dtype=np.float32)  # 10 mm
    grid3 = np.full((h, w), 2.5, dtype=np.float32)  # 2.5 mm

    iv1 = RainfallInterval(t0, 30.0, grid1, native_ids, distinct_native_cells=1)
    iv2 = RainfallInterval(t0, 30.0, grid2, native_ids, distinct_native_cells=1)
    iv3 = RainfallInterval(t0, 30.0, grid3, native_ids, distinct_native_cells=1)

    event = RainfallEvent(
        mode=ForcingMode.HISTORICAL,
        intervals=[iv1, iv2, iv3],
        cell_resolution_m=res,
        source_name="synthetic_test",
    )

    # Expected: (5 + 10 + 2.5) mm = 17.5 mm total depth across 10,000 cells
    # Total volume = 17.5 * 1e-3 m * (100 * 100 * 100 m^2) = 17,500 m^3
    assert event.num_intervals == 3
    assert event.total_duration_hours == 1.5
    assert pytest.approx(event.areal_mean_total_mm, rel=1e-6) == 17.5
    assert pytest.approx(event.total_volume_m3, rel=1e-6) == 17500.0
    assert event.verify_mass_conservation() is True


# ---------------------------------------------------------------------------
# Invariant 3: IMERG Adapter Native Cell Coverage & Independent Targets
# ---------------------------------------------------------------------------


def test_imerg_native_cell_coverage(domain_grid: Grid) -> None:
    """Invariant: canonical 10 m grid intersects exactly 16 distinct IMERG cells (4x4 block).

    Honesty constraint: downstream code cannot assume fine detail when source resolution is 0.1 deg.
    Observable: compute_imerg_grid_mapping returns distinct_cells_count == 16.
    """
    _, _, native_ids, n_distinct = compute_imerg_grid_mapping(domain_grid)
    assert n_distinct == 16, f"Expected 16 distinct IMERG cells covering domain, got {n_distinct}"
    assert native_ids.shape == (domain_grid.height, domain_grid.width)
    assert len(np.unique(native_ids)) == 16


def test_imerg_historical_adapter_sept5_event() -> None:
    """Invariant: IMERG adapter loads 5 Sept 2022 and satisfies mass conservation.

    Target metrics recorded in OPEN-ITEMS.md item 8:
    - 24-hour event on 2022-09-05 across 48 granules
    - Peak cell intensity: 14.47 mm/hr (14.5 mm/hr) at 14:30 UTC (20:00 IST)
    - Domain areal mean: 37.30 mm (domain grid) / 39.15 mm (BBMP raw bbox)
    - Mass conservation verified to float tolerance
    """
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
    st = datetime(2022, 9, 5, 0, 0, tzinfo=timezone.utc)
    et = datetime(2022, 9, 5, 23, 30, tzinfo=timezone.utc)

    event = adapter.get_forcing(st, et)

    assert event.mode == ForcingMode.HISTORICAL
    assert event.num_intervals == 48
    assert event.total_duration_hours == 24.0
    assert event.distinct_native_cells == 16

    # Peak hourly rate reproduction (14.47 mm/hr ≈ 14.5 mm/hr)
    assert pytest.approx(event.peak_hourly_rate_mm_hr, abs=0.1) == 14.47

    # Domain areal mean reproduction (37.30 mm over full 3421x3515 canonical domain)
    assert pytest.approx(event.areal_mean_total_mm, abs=0.1) == 37.30

    # Mass conservation
    assert event.verify_mass_conservation() is True
    assert event.total_volume_m3 > 44_000_000.0  # ~44.85 M m^3


# ---------------------------------------------------------------------------
# Invariant 4: Open-Meteo Forecast Adapter Round-Trip & Semantics
# ---------------------------------------------------------------------------


def test_open_meteo_forecast_adapter_mock() -> None:
    """Invariant: forecast adapter maps hourly precipitation (mm) to incremental grid.

    Observable: 3 forecast hours with [0.0, 5.0, 10.0] mm produce 3 intervals of 60 min each.
    """
    _require_boundary()
    mock_payload = {
        "latitude": 12.97,
        "longitude": 77.59,
        "elevation": 910.0,
        "hourly": {
            "time": [
                "2026-08-17T00:00",
                "2026-08-17T01:00",
                "2026-08-17T02:00",
            ],
            "precipitation": [0.0, 5.0, 10.0],
            "precipitation_probability": [0, 40, 80],
        },
    }
    adapter = OpenMeteoForecastAdapter(REPO / "configs/forcing.yaml", raw_response=mock_payload)
    event = adapter.get_forcing()

    assert event.mode == ForcingMode.FORECAST
    assert event.num_intervals == 3
    assert event.total_duration_hours == 3.0
    assert event.distinct_native_cells == 1
    assert pytest.approx(event.areal_mean_total_mm, rel=1e-6) == 15.0
    assert pytest.approx(event.peak_hourly_rate_mm_hr, rel=1e-6) == 10.0
    assert event.verify_mass_conservation() is True


def test_open_meteo_forecast_adapter_live() -> None:
    """Invariant: live Open-Meteo fetch succeeds, adheres to interval semantics, and verifies mass."""
    _require_boundary()
    adapter = OpenMeteoForecastAdapter(REPO / "configs/forcing.yaml")
    try:
        event = adapter.get_forcing()
    except requests.RequestException as exc:
        pytest.skip(f"BLOCKED: live Open-Meteo endpoint unavailable: {exc}")

    assert event.mode == ForcingMode.FORECAST
    assert event.num_intervals >= 24  # at least 1 day
    assert event.total_duration_hours >= 24.0
    assert event.distinct_native_cells == 1
    assert event.verify_mass_conservation() is True


# ---------------------------------------------------------------------------
# Invariant 5: KSNDMC Geolocation Integrity & Cumulative Conversion
# ---------------------------------------------------------------------------


def test_ksndmc_geolocation_and_forbidden_join_check() -> None:
    """Invariant: live HOBLINAME -> KML TM_RainGauge_LocationName join yields >= 235/266 matches.

    Forbidden join check: RAINGAUGE -> KGISTM_RainGauge_LocationID produces 100% station name mismatch.
    """
    kml_path = REPO / "data/raw/ksndmc/stations/rain_gauges.kml"
    rain_jsons = [
        REPO / f"data/raw/ksndmc/getCurrentRainData_d0{i}_2026-08-14.json"
        for i in range(1, 4)
    ]
    _require_artifacts(kml_path, *rain_jsons, stage="KSNDMC geolocation audit")
    bbox = [77.0, 12.3, 78.0, 13.6]
    name_to_coords, stats = load_and_locate_gauges(kml_path, bbox)

    # Baseline 266 gauges across d01, d02, d03
    d01_raw = open(REPO / "data/raw/ksndmc/getCurrentRainData_d01_2026-08-14.json").read()
    d02_raw = open(REPO / "data/raw/ksndmc/getCurrentRainData_d02_2026-08-14.json").read()
    d03_raw = open(REPO / "data/raw/ksndmc/getCurrentRainData_d03_2026-08-14.json").read()

    d01 = json.loads(json.loads(d01_raw))
    d02 = json.loads(json.loads(d02_raw))
    d03 = json.loads(json.loads(d03_raw))
    live_gauges = {int(r["RAINGAUGE"]): r for r in (d01 + d02 + d03)}

    matched = 0
    for gid, r in live_gauges.items():
        h = str(r.get("HOBLINAME", "")).strip().lower()
        if h in name_to_coords:
            matched += 1

    # Realized match count against 235/266
    assert matched >= 235, f"Expected at least 235 matched gauges, got {matched}/{len(live_gauges)}"

    # Check forbidden join disagreement
    import geopandas as gpd

    gdf_kml = gpd.read_file(kml_path, driver="KML")
    disagreements = 0
    forbidden_matches = 0
    for gid, r in live_gauges.items():
        kml_rows = gdf_kml[gdf_kml["KGISTM_RainGauge_LocationID"] == float(gid)]
        if len(kml_rows) > 0:
            forbidden_matches += 1
            live_h = str(r.get("HOBLINAME", "")).strip().lower()
            kml_h = str(kml_rows.iloc[0]["TM_RainGauge_LocationName"]).strip().lower()
            if live_h != kml_h:
                disagreements += 1

    assert forbidden_matches == 178
    assert disagreements == forbidden_matches  # 100% disagreement


def test_ksndmc_nowcast_adapter_execution() -> None:
    """Invariant: KSNDMC nowcast adapter converts cumulative series to incremental intervals."""
    _require_boundary()
    _require_artifacts(
        REPO / "data/raw/ksndmc/stations/rain_gauges.kml",
        stage="KSNDMC nowcast",
    )
    adapter = KsndmcNowcastAdapter(REPO / "configs/forcing.yaml")
    st = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    et = datetime(2026, 8, 16, 22, 0, tzinfo=timezone.utc)

    event = adapter.get_forcing(st, et)

    assert event.mode == ForcingMode.NOWCAST
    assert event.num_intervals > 0
    assert event.distinct_native_cells >= 235
    assert event.verify_mass_conservation() is True
    # All increments must be non-negative
    for iv in event.intervals:
        assert np.all(iv.rainfall_grid_mm >= 0.0)
