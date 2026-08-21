#!/usr/bin/env python
"""CLI Driver for the 24-point ground-truth classification (goal Part 0).

Usage:
    .venv/bin/python scripts/run_gt_classification.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

import typer
from jaladhar.analysis.gt_classification import run_classification

app = typer.Typer(add_completion=False)


@app.command()
def run() -> None:
    """Classify all 24 ground-truth points and write the report + manifest."""
    run_classification(REPO / "configs/analysis.yaml")


if __name__ == "__main__":
    app()