"""Core Interfaces and Data Models for Rainfall Forcing.

Fixed interface per architectural design:
    (timestamp, cell, mm_in_interval)

A forcing adapter returns, for a given time window, an INCREMENTAL depth in mm
for each grid cell over that interval. Cumulative sources convert to incremental
inside their adapter, never at the call site.

THREE MODES, AND THEY ARE NEVER BLENDED:
  - historical -> GPM IMERG (Sept 2022 event)
  - nowcast    -> live gauge telemetry (KSNDMC)
  - forecast   -> Open-Meteo

A single run uses exactly one mode. Blending or combining modes is structurally
prohibited by design.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np


class ForcingMode(str, Enum):
    """Mutually exclusive forcing modes. Blending is strictly prohibited."""

    HISTORICAL = "historical"
    NOWCAST = "nowcast"
    FORECAST = "forecast"

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
    """Incremental rainfall depth across the 2D spatial grid for a specific time interval.

    Attributes:
        timestamp: Start time of the interval in UTC.
        interval_minutes: Duration of the interval in minutes.
        rainfall_grid_mm: 2D numpy array (height, width) with incremental depth in mm.
        native_cell_ids: 2D numpy array (height, width) indicating native source cell ID.
        distinct_native_cells: Number of distinct native cells covering the grid.
        metadata: Additional diagnostic info.
    """

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
        """Volume of water deposited over the domain in this interval (m^3)."""
        # mm * 1e-3 = meters depth
        return float(np.sum(self.rainfall_grid_mm) * 1e-3 * cell_area_m2)


@dataclass
class RainfallEvent:
    """Full time-series of incremental rainfall intervals over an event duration."""

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
        """Integral of the rainfall field over domain and duration (m^3)."""
        return sum(iv.volume_m3(self.cell_area_m2) for iv in self.intervals)

    @property
    def cumulative_depth_grid_mm(self) -> np.ndarray:
        """Total accumulated depth per grid cell (mm)."""
        acc = np.zeros(self.intervals[0].shape, dtype=np.float64)
        for iv in self.intervals:
            acc += iv.rainfall_grid_mm
        return acc.astype(np.float32)

    @property
    def areal_mean_total_mm(self) -> float:
        """Areal-mean accumulated depth over the domain (mm)."""
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


class RainfallAdapter(ABC):
    """Abstract Base Class for mode-specific rainfall adapters."""

    @property
    @abstractmethod
    def mode(self) -> ForcingMode:
        """Return the forcing mode handled by this adapter."""
        ...

    @abstractmethod
    def get_forcing(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> RainfallEvent:
        """Generate the RainfallEvent with incremental rainfall on canonical grid."""
        ...
