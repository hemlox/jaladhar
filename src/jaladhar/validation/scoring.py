from __future__ import annotations

import json
import math
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
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

    hits: int
    misses: int
    false_alarms: int
    correct_negatives: int

    @property
    def total_samples(self) -> int:
        return self.hits + self.misses + self.false_alarms + self.correct_negatives

    @property
    def csi(self) -> float:
        denom = self.hits + self.misses + self.false_alarms
        if denom == 0:
            return float("nan")
        return float(self.hits / denom)

    @property
    def pod(self) -> float:
        """Returns float('nan') if hits + misses == 0 (no observed events)."""
        denom = self.hits + self.misses
        if denom == 0:
            return float("nan")
        return float(self.hits / denom)

    @property
    def far(self) -> float:
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

    comparison_timestamp: datetime
    threshold_m: float
    unmasked: ContingencyTable
    masked: ContingencyTable | None
    excluded_water_cells: int
    is_masked: bool
    stratified_unmasked: dict[str, ContingencyTable] | None = None
    stratified_masked: dict[str, ContingencyTable] | None = None
    is_stratified: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "comparison_timestamp_iso": self.comparison_timestamp.isoformat(),
            "threshold_m": self.threshold_m,
            "is_masked": self.is_masked,
            "is_stratified": self.is_stratified,
            "excluded_water_cells": self.excluded_water_cells,
            "unmasked_scores": self.unmasked.to_dict(),
            "masked_scores": self.masked.to_dict() if self.masked is not None else "UNMASKED",
        }
        if self.stratified_unmasked is not None:
            d["stratified_unmasked"] = {k: v.to_dict() for k, v in self.stratified_unmasked.items()}
        if self.stratified_masked is not None:
            d["stratified_masked"] = {k: v.to_dict() for k, v in self.stratified_masked.items()}
        return d


def compute_contingency_counts(
    pred_binary: np.ndarray,
    obs_binary: np.ndarray,
    valid_mask: np.ndarray | None = None,
) -> ContingencyTable:
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
    density_raster: np.ndarray | None = None,
    density_classes: dict[str, int] | dict[int, str] | list[str] | None = None,
) -> ValidationScoreResult:
    """Score predicted depth against observed binary flooding at a specific instant.
    observed_flooded: 2D boolean array (True = observed flooded)."""
    if comparison_timestamp is None or not isinstance(comparison_timestamp, datetime):
        raise TypeError(
            "comparison_timestamp is a required datetime argument with no default. "
            "Timing integrity prohibits comparing against an unspecified instant."
        )

    pred_flooded = predicted_depth > threshold_m
    obs_flooded = observed_flooded.astype(bool)

    unmasked_table = compute_contingency_counts(pred_flooded, obs_flooded, valid_mask=None)

    masked_table: ContingencyTable | None = None
    excluded_count = 0
    is_masked = permanent_water_mask is not None

    if is_masked:
        valid_mask = ~permanent_water_mask
        excluded_count = int(np.sum(permanent_water_mask))
        masked_table = compute_contingency_counts(pred_flooded, obs_flooded, valid_mask=valid_mask)

    stratified_unmasked: dict[str, ContingencyTable] | None = None
    stratified_masked: dict[str, ContingencyTable] | None = None
    is_stratified = density_raster is not None

    if is_stratified and density_raster is not None:
        if density_raster.shape != pred_flooded.shape:
            raise ValueError(
                f"Density raster shape {density_raster.shape} != pred shape {pred_flooded.shape}"
            )
        class_mapping: dict[str, int] = {}
        if density_classes is None:
            unique_ids = np.unique(density_raster)
            default_names = {0: "OPEN", 1: "MODERATE", 2: "DENSE"}
            for uid in unique_ids:
                name = default_names.get(int(uid), f"CLASS_{uid}")
                class_mapping[name] = int(uid)
        elif isinstance(density_classes, list):
            for idx, name in enumerate(density_classes):
                class_mapping[name] = idx
        elif isinstance(density_classes, dict):
            for k, v in density_classes.items():
                if isinstance(k, str) and isinstance(v, int):
                    class_mapping[k] = v
                elif isinstance(k, int) and isinstance(v, str):
                    class_mapping[v] = k

        stratified_unmasked = {}
        stratified_masked = {} if is_masked else None

        for class_name, class_id in class_mapping.items():
            c_mask = density_raster == class_id
            stratified_unmasked[class_name] = compute_contingency_counts(
                pred_flooded, obs_flooded, valid_mask=c_mask
            )
            if is_masked and stratified_masked is not None:
                c_valid_mask = c_mask & (~permanent_water_mask)
                stratified_masked[class_name] = compute_contingency_counts(
                    pred_flooded, obs_flooded, valid_mask=c_valid_mask
                )

    return ValidationScoreResult(
        comparison_timestamp=comparison_timestamp,
        threshold_m=threshold_m,
        unmasked=unmasked_table,
        masked=masked_table,
        excluded_water_cells=excluded_count,
        is_masked=is_masked,
        stratified_unmasked=stratified_unmasked,
        stratified_masked=stratified_masked,
        is_stratified=is_stratified,
    )


def score_threshold_curve(
    predicted_depth: np.ndarray,
    observed_flooded: np.ndarray,
    comparison_timestamp: datetime,
    thresholds_m: list[float] | np.ndarray = (0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0),
    permanent_water_mask: np.ndarray | None = None,
    density_raster: np.ndarray | None = None,
    density_classes: dict[str, int] | dict[int, str] | list[str] | None = None,
) -> list[ValidationScoreResult]:
    results: list[ValidationScoreResult] = []
    for th in thresholds_m:
        res = score_extent(
            predicted_depth=predicted_depth,
            observed_flooded=observed_flooded,
            comparison_timestamp=comparison_timestamp,
            threshold_m=float(th),
            permanent_water_mask=permanent_water_mask,
            density_raster=density_raster,
            density_classes=density_classes,
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
        "start_time_iso": datetime.now(UTC).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        # Benchmark synthetic case from specification:
        # 10x10 grid with 20 predicted flooded, 15 observed flooded, 12 overlap.
        grid_shape = (10, 10)
        pred_depth = np.zeros(grid_shape, dtype=np.float32)
        obs_flooded = np.zeros(grid_shape, dtype=bool)

        pred_depth.flat[0:12] = 0.2
        obs_flooded.flat[0:12] = True

        pred_depth.flat[12:20] = 0.2
        obs_flooded.flat[12:20] = False

        pred_depth.flat[20:23] = 0.0
        obs_flooded.flat[20:23] = True

        water_mask = np.zeros(grid_shape, dtype=bool)
        water_mask.flat[0:2] = True

        ts = datetime(2022, 9, 5, 0, 40, tzinfo=UTC)
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
        u = result.unmasked
        typer.echo(f"  Unmasked: Hits={u.hits}, Misses={u.misses}, FalseAlarms={u.false_alarms}")
        typer.echo(f"    CSI: {u.csi:.4f} (Expected: 0.5217)")
        typer.echo(f"    POD: {u.pod:.4f} (Expected: 0.8000)")
        typer.echo(f"    FAR: {u.far:.4f} (Expected: 0.4000)")
        if result.masked is not None:
            m = result.masked
            typer.echo(f"  Masked ({result.excluded_water_cells} permanent water cells excluded):")
            typer.echo(f"    Hits={m.hits}, Misses={m.misses}, FalseAlarms={m.false_alarms}")
            typer.echo(f"    CSI: {m.csi:.4f}, POD: {m.pod:.4f}, FAR: {m.far:.4f}")
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
