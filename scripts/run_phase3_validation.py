#!/usr/bin/env python
"""CLI Driver for Phase 3 Validation Gate.

Runs end-to-end event replay and scoring for the September 2022 Bengaluru flood.
Usage:
    .venv/bin/python scripts/run_phase3_validation.py --help
    .venv/bin/python scripts/run_phase3_validation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root and src to sys.path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

import typer  # noqa: E402

from jaladhar.validation.event_replay import run_phase3_gate  # noqa: E402

app = typer.Typer(add_completion=False)


@app.command()
def main(
    solver_config: Path = typer.Option(REPO / "configs/solver.yaml", help="Solver config path"),
    forcing_config: Path = typer.Option(REPO / "configs/forcing.yaml", help="Forcing config path"),
    val_config: Path = typer.Option(
        REPO / "configs/validation.yaml", help="Validation config path"
    ),
    compute_config: Path = typer.Option(REPO / "configs/compute.yaml", help="Compute config path"),
    out: Path = typer.Option(REPO / "runs/phase3_validation", help="Output directory"),
    start_time: str = typer.Option("2022-09-04T00:00:00Z", help="Start time ISO UTC"),
    end_time: str = typer.Option("2022-09-05T23:30:00Z", help="End time ISO UTC"),
    sar_time: str = typer.Option("2022-09-05T00:40:28Z", help="SAR pass time ISO UTC"),
) -> None:
    """Execute Phase 3 validation gate end to end and produce uncalibrated baseline report."""
    typer.echo("Executing Phase 3 Validation Gate...")
    run_phase3_gate(
        solver_cfg_path=solver_config,
        forcing_cfg_path=forcing_config,
        val_cfg_path=val_config,
        compute_cfg_path=compute_config,
        out_dir=out,
        event_start_iso=start_time,
        event_end_iso=end_time,
        sar_instant_iso=sar_time,
    )

    typer.echo("\n" + "=" * 70)
    typer.echo("PHASE 3 UNCALIBRATED BASELINE VALIDATION COMPLETE")
    typer.echo("=" * 70)
    typer.echo(f"Results Manifest: {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
