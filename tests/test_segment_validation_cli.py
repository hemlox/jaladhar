"""Regression checks for the segment-validation command-line report printer."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jaladhar_segment_validation_cli", REPO / "scripts/run_segment_validation.py"
)
assert SPEC is not None and SPEC.loader is not None
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


def test_positive_unlabelled_metrics_render_without_crashing() -> None:
    """PU null metrics must stay visible as N/A rather than becoming a CLI error.

    Observable: the report's deliberate ``None`` values render as ``N/A``;
    changing the helper back to direct numeric formatting raises TypeError.
    Scope: the output conversion boundary, independent of a Phase 3 rerun.
    """
    assert CLI.format_metric(None) == "N/A"
    assert CLI.format_metric(float("nan")) == "N/A"
    assert CLI.format_metric(0.125, places=2) == "0.12"
