"""Validation Scoring Harness for Predicted Flood Extent vs Observations.

Computes contingency table metrics:
    CSI (Critical Success Index / Threat Score) = hits / (hits + misses + false_alarms)
    POD (Probability of Detection / Hit Rate)   = hits / (hits + misses)
    FAR (False Alarm Ratio)                     = false_alarms / (hits + false_alarms)

Predicted flooded = depth > threshold.
Threshold is a parameterized sweep; no hardcoded cut is baked in.

PERMANENT WATER BODY EXCLUSION:
Permanently-wet water bodies (lakes, tanks, quarries) match SAR water detection
regardless of storm flooding. The harness takes an optional permanent water mask,
excludes those cells, and reports metrics both WITH and WITHOUT exclusion (or
explicitly flags UNMASKED when absent).

TIMING INTEGRITY:
The comparison timestamp is a REQUIRED argument with NO DEFAULT, preventing
silent comparison against the wrong temporal instant (e.g. event max vs SAR epoch).
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import typer

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


@dataclass(frozen=True)
class ContingencyTable:
    """Standard 2x2 contingency table for binary event verification."""

    hits: int
    misses: int
    false_alarms: int
    correct_negatives: int

    @property
    def total_samples(self) -> int:
        return self.hits + self.misses + self.false_alarms + self.correct_negatives

    @property
    def csi(self) -> float:
        """Critical Success Index (Threat Score).

        Returns float('nan') if hits + misses + false_alarms == 0 (undefined).
        Never silently returns 0.0 when undefined.
        """
        denom = self.hits + self.misses + self.false_alarms
        if denom == 0:
            return float("nan")
        return float(self.hits / denom)

    @property
    def pod(self) -> float:
        """Probability of Detection (Hit Rate).

        Returns float('nan') if hits + misses == 0 (no observed events).
        """
        denom = self.hits + self.misses
        if denom == 0:
            return float("nan")
        return float(self.hits / denom)

    @property
    def far(self) -> float:
        """False Alarm Ratio.

        Returns float('nan') if hits + false_alarms == 0 (no predicted events).
        """
        denom = self.hits + self.false_alarms
        if denom == 0:
            return float("nan")
        return float(self.false_alarms / denom)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "false_alarms": self.false_alarms,
            "correct_negatives": self.correct_negatives,
            "total_samples": self.total_samples,
            "csi": None if math.isnan(self.csi) else round(self.csi, 6),
            "pod": None if math.isnan(self.pod) else round(self.pod, 6),
            "far": None if math.isnan(self.far) else round(self.far, 6),
        }


@dataclass(frozen=True)
class ValidationScoreResult:
    """Scoring result at a specific depth threshold, with and without permanent water exclusion."""

    comparison_timestamp: datetime
    threshold_m: float
    unmasked: ContingencyTable
    masked: ContingencyTable | None
    excluded_water_cells: int
    is_masked: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparison_timestamp_iso": self.comparison_timestamp.isoformat(),
            "threshold_m": self.threshold_m,
            "is_masked": self.is_masked,
            "excluded_water_cells": self.excluded_water_cells,
            "unmasked_scores": self.unmasked.to_dict(),
            "masked_scores": self.masked.to_dict() if self.masked is not None else "UNMASKED",
        }


def compute_contingency_counts(
    pred_binary: np.ndarray,
    obs_binary: np.ndarray,
    valid_mask: np.ndarray | None = None,
) -> ContingencyTable:
    """Compute hits, misses, false alarms, and correct negatives over valid cells."""
    if pred_binary.shape != obs_binary.shape:
        raise ValueError(
            f"Shape mismatch: predicted {pred_binary.shape} != observed {obs_binary.shape}"
        )

    if valid_mask is not None:
        if valid_mask.shape != pred_binary.shape:
            raise ValueError(
                f"Valid mask shape {valid_mask.shape} != pred shape {pred_binary.shape}"
            )
        p = pred_binary[valid_mask]
        o = obs_binary[valid_mask]
    else:
        p = pred_binary
        o = obs_binary

    hits = int(np.sum(p & o))
    misses = int(np.sum((~p) & o))
    false_alarms = int(np.sum(p & (~o)))
    correct_negatives = int(np.sum((~p) & (~o)))

    return ContingencyTable(
        hits=hits,
        misses=misses,
        false_alarms=false_alarms,
        correct_negatives=correct_negatives,
    )


def score_extent(
    predicted_depth: np.ndarray,
    observed_flooded: np.ndarray,
    comparison_timestamp: datetime,
    threshold_m: float = 0.1,
    permanent_water_mask: np.ndarray | None = None,
) -> ValidationScoreResult:
    """Score predicted depth against observed binary flooding at a specific instant.

    Args:
        predicted_depth: 2D array of water depths in meters.
        observed_flooded: 2D boolean array (True = observed flooded).
        comparison_timestamp: Explicit instant for comparison (no default).
        threshold_m: Depth threshold above which a cell is classified as flooded.
        permanent_water_mask: 2D boolean array (True = permanent water, excluded from masked evaluation).

    Returns:
        ValidationScoreResult containing unmasked and masked contingency tables.
    """
    if comparison_timestamp is None or not isinstance(comparison_timestamp, datetime):
        raise TypeError(
            "comparison_timestamp is a required datetime argument with no default. "
            "Timing integrity prohibits comparing against an unspecified instant."
        )

    pred_flooded = predicted_depth > threshold_m
    obs_flooded = observed_flooded.astype(bool)

    # 1. Unmasked evaluation (all cells)
    unmasked_table = compute_contingency_counts(pred_flooded, obs_flooded, valid_mask=None)

    # 2. Masked evaluation (excluding permanent water bodies)
    masked_table: ContingencyTable | None = None
    excluded_count = 0
    is_masked = permanent_water_mask is not None

    if is_masked:
        # valid_mask is True for NON-permanent water cells
        valid_mask = ~permanent_water_mask
        excluded_count = int(np.sum(permanent_water_mask))
        masked_table = compute_contingency_counts(
            pred_flooded, obs_flooded, valid_mask=valid_mask
        )

    return ValidationScoreResult(
        comparison_timestamp=comparison_timestamp,
        threshold_m=threshold_m,
        unmasked=unmasked_table,
        masked=masked_table,
        excluded_water_cells=excluded_count,
        is_masked=is_masked,
    )


def score_threshold_curve(
    predicted_depth: np.ndarray,
    observed_flooded: np.ndarray,
    comparison_timestamp: datetime,
    thresholds_m: list[float] | np.ndarray = (0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0),
    permanent_water_mask: np.ndarray | None = None,
) -> list[ValidationScoreResult]:
    """Evaluate validation scores across a sweep of depth thresholds."""
    results: list[ValidationScoreResult] = []
    for th in thresholds_m:
        res = score_extent(
            predicted_depth=predicted_depth,
            observed_flooded=observed_flooded,
            comparison_timestamp=comparison_timestamp,
            threshold_m=float(th),
            permanent_water_mask=permanent_water_mask,
        )
        results.append(res)
    return results


@app.command()
def main(
    out: Path = typer.Option(REPO / "runs/validation_scoring", help="Output directory"),
) -> None:
    """Run validation scoring demonstration on synthetic scenarios and write manifest."""
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"

    t0 = time.perf_counter()
    manifest: dict[str, Any] = {
        "stage": "validation_scoring_harness",
        "status": "running",
        "git_sha": git_sha(),
        "start_time_iso": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        # Benchmark synthetic case from specification:
        # 10x10 grid with 20 predicted flooded, 15 observed flooded, 12 overlap.
        # hits=12, misses=3, false_alarms=8 -> CSI=12/23=0.5217, POD=0.8, FAR=0.4
        grid_shape = (10, 10)
        pred_depth = np.zeros(grid_shape, dtype=np.float32)
        obs_flooded = np.zeros(grid_shape, dtype=bool)

        # 12 overlapping cells (0 to 11)
        pred_depth.flat[0:12] = 0.2  # > 0.1 m threshold
        obs_flooded.flat[0:12] = True

        # 8 false alarm cells (12 to 19)
        pred_depth.flat[12:20] = 0.2
        obs_flooded.flat[12:20] = False

        # 3 miss cells (20 to 22)
        pred_depth.flat[20:23] = 0.0
        obs_flooded.flat[20:23] = True

        # Add permanent water mask on cells 0 and 1 (2 cells)
        water_mask = np.zeros(grid_shape, dtype=bool)
        water_mask.flat[0:2] = True

        ts = datetime(2022, 9, 5, 0, 40, tzinfo=timezone.utc)  # 06:10 IST SAR pass
        result = score_extent(
            predicted_depth=pred_depth,
            observed_flooded=obs_flooded,
            comparison_timestamp=ts,
            threshold_m=0.1,
            permanent_water_mask=water_mask,
        )

        curve = score_threshold_curve(
            predicted_depth=pred_depth,
            observed_flooded=obs_flooded,
            comparison_timestamp=ts,
            thresholds_m=[0.05, 0.1, 0.15, 0.25],
            permanent_water_mask=water_mask,
        )

        wall_clock = time.perf_counter() - t0
        manifest.update(
            {
                "status": "completed",
                "wall_clock_sec": wall_clock,
                "benchmark_case": result.to_dict(),
                "threshold_curve": [c.to_dict() for c in curve],
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))

        typer.echo("Validation Scoring Harness Demonstration:")
        typer.echo(f"  Comparison Instant: {ts.isoformat()} (06:10 IST SAR flood epoch)")
        typer.echo(f"  Unmasked: Hits={result.unmasked.hits}, Misses={result.unmasked.misses}, FalseAlarms={result.unmasked.false_alarms}")
        typer.echo(f"    CSI: {result.unmasked.csi:.4f} (Expected: 0.5217)")
        typer.echo(f"    POD: {result.unmasked.pod:.4f} (Expected: 0.8000)")
        typer.echo(f"    FAR: {result.unmasked.far:.4f} (Expected: 0.4000)")
        if result.masked is not None:
            typer.echo(f"  Masked ({result.excluded_water_cells} permanent water cells excluded):")
            typer.echo(f"    Hits={result.masked.hits}, Misses={result.masked.misses}, FalseAlarms={result.masked.false_alarms}")
            typer.echo(f"    CSI: {result.masked.csi:.4f}, POD: {result.masked.pod:.4f}, FAR: {result.masked.far:.4f}")
        typer.echo(f"\nWrote manifest to {manifest_path}")

    except Exception as e:
        manifest.update(
            {
                "status": "failed",
                "error": str(e),
                "wall_clock_sec": time.perf_counter() - t0,
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise


if __name__ == "__main__":
    app()
