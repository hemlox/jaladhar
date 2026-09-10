"""for each grid cell over that interval. Cumulative sources convert to incremental"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import numpy as np


class NowcastUnavailableError(ValueError):
    """Base error for unavailable or contract-invalid nowcast input."""

    def __init__(self, message: str, *, axis: str = "N-3 DWR/rainfall nowcast access") -> None:
        super().__init__(message)
        self.axis = axis


class NowcastFetchError(NowcastUnavailableError):
    """The configured nowcast source could not be fetched or supplied."""


class NowcastStaleError(NowcastUnavailableError):
    """The source validity window cannot cover the requested nowcast."""


class NowcastDistrictAbsentError(NowcastUnavailableError):
    """The source response has no unambiguous Bengaluru Urban feature."""


class ForcingMode(StrEnum):

    HISTORICAL = "historical"
    NOWCAST = "nowcast"

    @classmethod
    def from_str(cls, val: str) -> ForcingMode:
        norm = val.strip().lower()
        for mode in cls:
            if mode.value == norm:
                return mode
        raise ValueError(
            f"Invalid forcing mode: '{val}'. Must be exactly one of "
            f"{[m.value for m in cls]}. Blended/combined modes are forbidden."
        )


@dataclass(frozen=True)
class RainfallInterval:
    """native_cell_ids: 2D numpy array (height, width) indicating native source cell ID."""

    timestamp: datetime
    interval_minutes: float
    rainfall_grid_mm: np.ndarray
    native_cell_ids: np.ndarray
    distinct_native_cells: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rainfall_grid_mm.ndim != 2:
            raise ValueError(
                f"rainfall_grid_mm must be 2D, got shape {self.rainfall_grid_mm.shape}"
            )
        if self.native_cell_ids.shape != self.rainfall_grid_mm.shape:
            raise ValueError(
                f"native_cell_ids shape {self.native_cell_ids.shape} != "
                f"rainfall_grid_mm shape {self.rainfall_grid_mm.shape}"
            )
        if np.any(self.rainfall_grid_mm < 0):
            raise ValueError("Negative rainfall depth encountered in incremental grid.")

    @property
    def shape(self) -> tuple[int, int]:
        return self.rainfall_grid_mm.shape

    @property
    def mean_depth_mm(self) -> float:
        return float(np.mean(self.rainfall_grid_mm))

    @property
    def max_depth_mm(self) -> float:
        return float(np.max(self.rainfall_grid_mm))

    @property
    def max_intensity_mm_hr(self) -> float:
        duration_hr = self.interval_minutes / 60.0
        return float(np.max(self.rainfall_grid_mm) / duration_hr) if duration_hr > 0 else 0.0

    def volume_m3(self, cell_area_m2: float) -> float:
        return float(np.sum(self.rainfall_grid_mm) * 1e-3 * cell_area_m2)


@dataclass
class RainfallEvent:

    mode: ForcingMode
    intervals: list[RainfallInterval]
    cell_resolution_m: float
    source_name: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.intervals:
            raise ValueError("RainfallEvent must contain at least one interval.")
        first_shape = self.intervals[0].shape
        for i, iv in enumerate(self.intervals):
            if iv.shape != first_shape:
                raise ValueError(
                    f"Interval {i} shape {iv.shape} != first interval shape {first_shape}"
                )

    @property
    def cell_area_m2(self) -> float:
        return self.cell_resolution_m * self.cell_resolution_m

    @property
    def num_intervals(self) -> int:
        return len(self.intervals)

    @property
    def total_duration_hours(self) -> float:
        return sum(iv.interval_minutes for iv in self.intervals) / 60.0

    @property
    def total_volume_m3(self) -> float:
        return sum(iv.volume_m3(self.cell_area_m2) for iv in self.intervals)

    @property
    def cumulative_depth_grid_mm(self) -> np.ndarray:
        acc = np.zeros(self.intervals[0].shape, dtype=np.float64)
        for iv in self.intervals:
            acc += iv.rainfall_grid_mm
        return acc.astype(np.float32)

    @property
    def areal_mean_total_mm(self) -> float:
        return float(np.mean(self.cumulative_depth_grid_mm))

    @property
    def peak_hourly_rate_mm_hr(self) -> float:
        """Peak intensity observed in any cell at any interval (mm/hr)."""
        if not self.intervals:
            return 0.0
        return max(iv.max_intensity_mm_hr for iv in self.intervals)

    @property
    def distinct_native_cells(self) -> int:
        return self.intervals[0].distinct_native_cells

    def verify_mass_conservation(self, rtol: float = 1e-5, atol: float = 1e-4) -> bool:
        """Assert that total domain volume equals sum of cell depths * area."""
        direct_vol = float(np.sum(self.cumulative_depth_grid_mm) * 1e-3 * self.cell_area_m2)
        reported_vol = self.total_volume_m3
        diff = abs(direct_vol - reported_vol)
        if diff > atol + rtol * max(abs(direct_vol), abs(reported_vol)):
            raise AssertionError(
                f"Mass conservation check failed: direct integral {direct_vol:.4f} m^3 "
                f"!= reported volume {reported_vol:.4f} m^3 (diff: {diff:.4e})"
            )
        return True


def interval_depth_mm_to_rate_m_s(depth_mm: float, interval_minutes: float) -> float:

    duration = float(interval_minutes)
    if not np.isfinite(duration) or duration <= 0.0:
        raise ValueError("interval_minutes must be finite and positive")
    depth = float(depth_mm)
    if not np.isfinite(depth) or depth < 0.0:
        raise ValueError("depth_mm must be finite and non-negative")
    return depth * (60.0 / duration) / 3600.0 / 1000.0


def assert_rate_conversion_parameterised(
    event: RainfallEvent, native_rates_m_s: Sequence[np.ndarray]
) -> bool:
    """Assert realized consumer rates integrate back to each source interval depth."""

    if len(native_rates_m_s) != event.num_intervals:
        raise AssertionError("consumer rate conversion count does not match event intervals")
    for number, (interval, native_rates) in enumerate(
        zip(event.intervals, native_rates_m_s, strict=True), start=1
    ):
        rates = np.asarray(native_rates, dtype=np.float64)
        if rates.shape != (interval.distinct_native_cells,):
            raise AssertionError(
                f"consumer rate conversion {number} has shape {rates.shape}, expected "
                f"({interval.distinct_native_cells},)"
            )
        ids = np.asarray(interval.native_cell_ids)
        if ids.size == 0 or ids.min() < -1 or ids.max() >= interval.distinct_native_cells:
            raise AssertionError(f"consumer rate conversion {number} has invalid native-cell IDs")
        covered = ids >= 0
        if set(int(value) for value in np.unique(ids[covered])) != set(
            range(interval.distinct_native_cells)
        ):
            raise AssertionError(
                f"consumer rate conversion {number} does not realize every declared native cell"
            )
        if np.any(np.asarray(interval.rainfall_grid_mm)[~covered] != 0.0):
            raise AssertionError(
                f"consumer rate conversion {number} has rainfall outside native coverage"
            )
        realized_depth_mm = np.zeros(interval.shape, dtype=np.float64)
        realized_depth_mm[covered] = rates[ids[covered]] * interval.interval_minutes * 60.0 * 1000.0
        if not np.allclose(
            realized_depth_mm,
            np.asarray(interval.rainfall_grid_mm, dtype=np.float64),
            rtol=1e-6,
            atol=1e-7,
        ):
            raise AssertionError(
                f"consumer rate conversion {number} does not integrate to the realized "
                f"{interval.interval_minutes}-minute source depth"
            )
    return True


def _aware_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise AssertionError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise AssertionError(f"{field} must be timezone-aware UTC")
    return value.astimezone(UTC)


def assert_nowcast_metadata_contract(event: RainfallEvent) -> bool:
    """Enforce the frozen nowcast producer/consumer seam on a realized event."""

    if event.mode is not ForcingMode.NOWCAST:
        raise AssertionError(f"expected nowcast event, got {event.mode!r}")
    required = {
        "product_id",
        "issue_time",
        "valid_until",
        "update_time",
        "fetch_url",
        "district_name",
        "imd_category_code",
        "intensity_mapping",
        "truncated_at_valid_until",
        "source_response",
        "lead_times_minutes",
        "grid_shape",
        "grid_crs",
        "cell_resolution_m",
    }
    missing = sorted(required.difference(event.metadata))
    if missing:
        raise AssertionError(f"nowcast provenance missing keys: {missing}")
    if event.metadata["product_id"] not in {
        "IMD_WFS_NowcastWarningDistrict",
        "IMD_WFS_NowcastWarningStation",
    }:
        raise AssertionError("nowcast product_id is not a frozen IMD product enum")
    if (
        event.metadata["product_id"] == "IMD_WFS_NowcastWarningDistrict"
        and event.metadata["district_name"] != "BANGLORE URBAN"
    ):
        raise AssertionError("district nowcast is not BANGLORE URBAN")
    if event.metadata["imd_category_code"] not in {"cat2", "cat7", "cat12"}:
        raise AssertionError("nowcast category has no frozen intensity mapping")
    mapping = event.metadata["intensity_mapping"]
    if not isinstance(mapping, dict) or not all(
        key in mapping for key in ("light", "moderate", "heavy", "basis")
    ):
        raise AssertionError("nowcast intensity_mapping is incomplete")
    if type(event.metadata["truncated_at_valid_until"]) is not bool:
        raise AssertionError("nowcast truncated_at_valid_until must be boolean")
    fetch_url = event.metadata["fetch_url"]
    if not isinstance(fetch_url, str) or urlparse(fetch_url).scheme not in {"http", "https"}:
        raise AssertionError("nowcast fetch_url must be a non-empty HTTP(S) URL")
    update_time = event.metadata["update_time"]
    if not isinstance(update_time, str):
        raise AssertionError("nowcast update_time must preserve the source timestamp string")
    try:
        parsed_update = datetime.fromisoformat(update_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssertionError("nowcast update_time is not ISO-8601") from exc
    _aware_utc(parsed_update, "event.metadata.update_time")
    source_response = event.metadata["source_response"]
    required_source = {
        "path",
        "sha256",
        "fetch_time",
        "http_status",
        "cache_decision",
        "cache_dir",
        "poll_log_path",
        "courtesy_interval_seconds",
        "freshness_seconds",
    }
    if not isinstance(source_response, dict) or not required_source.issubset(source_response):
        raise AssertionError("nowcast source_response provenance envelope is incomplete")
    if not isinstance(source_response["sha256"], str) or len(source_response["sha256"]) != 64:
        raise AssertionError("nowcast source_response sha256 is invalid")
    fetch_time = datetime.fromisoformat(str(source_response["fetch_time"]).replace("Z", "+00:00"))
    _aware_utc(fetch_time, "event.metadata.source_response.fetch_time")
    if type(source_response["http_status"]) is not int:
        raise AssertionError("nowcast source_response http_status must be integer")
    issue_time = _aware_utc(event.metadata["issue_time"], "event.metadata.issue_time")
    valid_until = _aware_utc(event.metadata["valid_until"], "event.metadata.valid_until")
    if valid_until < issue_time:
        raise AssertionError("nowcast valid_until precedes issue_time")
    if event.metadata["grid_shape"] != [3421, 3515]:
        raise AssertionError("nowcast grid shape is not the frozen 3421x3515 identity")
    if event.metadata["grid_crs"] != "EPSG:32643":
        raise AssertionError("nowcast grid CRS is not EPSG:32643")
    if float(event.metadata["cell_resolution_m"]) != 10.0:
        raise AssertionError("nowcast grid resolution is not 10 m")
    leads = event.metadata["lead_times_minutes"]
    if not isinstance(leads, list) or len(leads) != event.num_intervals:
        raise AssertionError("lead-time provenance length does not match intervals")
    expected_shape = (3421, 3515)
    for interval, lead in zip(event.intervals, leads, strict=True):
        timestamp = _aware_utc(interval.timestamp, "interval.timestamp")
        if interval.shape != expected_shape:
            raise AssertionError("nowcast interval shape is not the frozen grid identity")
        expected_lead = (timestamp - issue_time).total_seconds() / 60.0
        if abs(float(lead) - expected_lead) > 1e-6:
            raise AssertionError("interval lead-time provenance does not match timestamps")
        interval_end = timestamp + timedelta(minutes=interval.interval_minutes)
        if interval_end > valid_until + timedelta(microseconds=1):
            raise AssertionError("nowcast interval extends beyond source valid_until")
        ids = np.asarray(interval.native_cell_ids)
        covered = ids >= 0
        if ids.min() < -1 or ids.max() >= interval.distinct_native_cells:
            raise AssertionError("nowcast native-cell IDs are outside sentinel/declared range")
        if set(int(value) for value in np.unique(ids[covered])) != set(
            range(interval.distinct_native_cells)
        ):
            raise AssertionError("nowcast native-cell count does not match realized IDs")
        if np.any(np.asarray(interval.rainfall_grid_mm)[~covered] != 0.0):
            raise AssertionError("nowcast rainfall exists outside district native-cell coverage")
        for native_id in range(interval.distinct_native_cells):
            values = np.asarray(interval.rainfall_grid_mm)[ids == native_id]
            if values.size == 0 or not np.all(values == values[0]):
                raise AssertionError("one nowcast native-cell ID maps to non-uniform rainfall")
        for key in (
            "product_id",
            "issue_time",
            "valid_until",
            "update_time",
            "fetch_url",
            "district_name",
            "imd_category_code",
            "source_response",
        ):
            if interval.metadata.get(key) != event.metadata.get(key):
                raise AssertionError(f"interval nowcast metadata {key} differs from event")
    event.verify_mass_conservation()
    return True


class RainfallAdapter(ABC):

    @property
    @abstractmethod
    def mode(self) -> ForcingMode: ...

    @abstractmethod
    def get_forcing(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> RainfallEvent: ...
