#!/usr/bin/env python
"""CLI Driver for Per-Road-Segment Validation Gate Rescore.

PROMPT.md §8 & CLAUDE.md:
- Re-scores Phase 3 validation gate on per-road-segment flood status.
- Evaluates against three independent label sets: BBMP (399 pts), Ground Truth (24 pts),
  and Sentinel-1 change-detection SAR reference (flood - pre).
- Computes mandatory Monte Carlo null models (>= 5,000 draws), stratification by density
  and road class, and arterial/trunk subsets.
- Enforces CLAUDE.md Rules 1-7 and Verification Rules V1-V8.

Usage:
    .venv/bin/python scripts/run_segment_validation.py
    .venv/bin/python scripts/run_segment_validation.py --help
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

# Add project root and src to sys.path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

import typer
import yaml

from jaladhar.provenance import RunManifest
from jaladhar.validation.segment_validation import run_segment_validation_gate

app = typer.Typer(add_completion=False)


def format_metric(value: Any, places: int = 4) -> str:
    """Render an optional report metric without turning an honest null into a CLI crash."""
    if value is None:
        return "N/A"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return f"{numeric:.{places}f}" if math.isfinite(numeric) else "N/A"


@app.command()
def main(
    runs_dir: Path = typer.Option(
        REPO / "runs/phase3_validation",
        help="Phase 3 validation runs directory containing depth rasters",
    ),
    val_config: Path = typer.Option(
        REPO / "configs/validation.yaml", help="Validation configuration YAML path"
    ),
    mc_draws: int = typer.Option(
        10000, help="Number of Monte Carlo draws for mandatory null model floor estimation"
    ),
    seed: int = typer.Option(42, help="Random seed for reproducible Monte Carlo draws"),
) -> None:
    """Execute per-road-segment validation rescore and produce audit report."""
    typer.echo("=================================================================")
    typer.echo("PHASE 3 PER-ROAD-SEGMENT FLOOD STATUS VALIDATION RESCORE")
    typer.echo("=================================================================")
    typer.echo(f"Runs Directory: {runs_dir}")
    typer.echo(f"Validation Config: {val_config}")
    typer.echo(f"Monte Carlo Draws for Null Model: {mc_draws:,} (seed={seed})")

    with val_config.open() as handle:
        validation_cfg: dict[str, Any] = yaml.safe_load(handle)
    lifecycle = RunManifest(
        runs_dir / "segment_validation_manifest.json",
        stage="phase3_segment_validation_rescore",
        repo_root=REPO,
        resolved_config={
            "validation": validation_cfg,
            "monte_carlo_draws": mc_draws,
            "seed": seed,
            "phase3_runs_dir": str(runs_dir),
        },
        config_paths={"validation": str(val_config)},
        fields={"input_phase3_manifest": str(runs_dir / "manifest.json")},
    )
    lifecycle.start()
    try:
        report = run_segment_validation_gate(
            runs_dir=runs_dir,
            val_config_path=val_config,
            n_mc_draws=mc_draws,
            seed=seed,
        )
        lifecycle.complete(
            {
                "report_path": str(runs_dir / "segment_validation_report.json"),
                "headline_results": report["headline_results"],
                "verdict": report["verdict_for_adjudication"],
            }
        )
    except BaseException as exc:
        lifecycle.fail(exc)
        raise

    h = report["headline_results"]
    bbmp = h["bbmp_validation"]
    gt = h["groundtruth_validation_date_and_snap_eligible"]
    sar = h["sentinel1_change_validation"]

    typer.echo("\n" + "-" * 70)
    typer.echo("HEADLINE RESULTS AGAINST MANDATORY NULL FLOOR")
    typer.echo("-" * 70)
    typer.echo(
        f"1. BBMP Flood-Prone Segments (Obs={bbmp['observed_flooded_segments']}, Pred={bbmp['predicted_flooded_segments']}):\n"
        f"   - Achieved: TP={bbmp['hits']}, POD={format_metric(bbmp['pod'])}, "
        f"FAR={format_metric(bbmp['far'])}, CSI={format_metric(bbmp['csi'], 6)}\n"
        f"   - Null Model: Mean CSI={format_metric(bbmp.get('null_csi_mean'), 6)} "
        f"(95% CI: [{format_metric(bbmp.get('null_csi_95ci', [None, None])[0], 6)}, "
        f"{format_metric(bbmp.get('null_csi_95ci', [None, None])[1], 6)}])\n"
        f"   - Lift Ratio over Random Null: {format_metric(bbmp.get('lift_ratio_csi'), 2)}x "
        f"(POD lift: {format_metric(bbmp.get('lift_ratio_pod'), 2)}x)"
    )
    typer.echo(
        f"2. Ground-Truth Verified Points (Obs={gt['observed_flooded_segments']}, Pred={gt['predicted_flooded_segments']}):\n"
        f"   - Achieved: TP={gt['hits']}, POD={format_metric(gt['pod'])}, "
        f"FAR={format_metric(gt['far'])}, CSI={format_metric(gt['csi'], 6)}\n"
        f"   - Null Model: Mean CSI={format_metric(gt.get('null_csi_mean'), 6)} "
        f"(95% CI: [{format_metric(gt.get('null_csi_95ci', [None, None])[0], 6)}, "
        f"{format_metric(gt.get('null_csi_95ci', [None, None])[1], 6)}])\n"
        f"   - Lift Ratio over Random Null: {format_metric(gt.get('lift_ratio_csi'), 2)}x "
        f"(POD lift: {format_metric(gt.get('lift_ratio_pod'), 2)}x)"
    )
    typer.echo(
        f"3. Sentinel-1 Change-Detection Reference (Obs={sar['observed_flooded_segments']}, Pred={sar['predicted_flooded_segments']}):\n"
        f"   - Achieved: TP={sar['hits']}, POD={format_metric(sar['pod'])}, "
        f"FAR={format_metric(sar['far'])}, CSI={format_metric(sar['csi'], 6)}\n"
        f"   - Null Model: Mean CSI={format_metric(sar.get('null_csi_mean'), 6)} "
        f"(95% CI: [{format_metric(sar.get('null_csi_95ci', [None, None])[0], 6)}, "
        f"{format_metric(sar.get('null_csi_95ci', [None, None])[1], 6)}])\n"
        f"   - Lift Ratio over Random Null: {format_metric(sar.get('lift_ratio_csi'), 2)}x "
        f"(POD lift: {format_metric(sar.get('lift_ratio_pod'), 2)}x)"
    )
    typer.echo(
        "\nSegment-level lift: "
        f"{format_metric(bbmp.get('lift_ratio_csi'), 2)}x (BBMP) / "
        f"{format_metric(gt.get('lift_ratio_csi'), 2)}x (eligible GT) / "
        f"{format_metric(sar.get('lift_ratio_csi'), 2)}x (SAR)"
    )

    art = report["stratification"]["arterial_trunk_alone"]
    typer.echo("\n" + "-" * 70)
    typer.echo("ARTERIAL / TRUNK SEGMENTS ALONE (Key Routing Links)")
    typer.echo("-" * 70)
    art_b = art["bbmp_event_max"]
    art_s = art["sar_change_instant"]
    typer.echo(
        f"Arterial Total Segments: {art['total_arterial_segments']}\n"
        f"BBMP on Arterials: TP={art_b['hits']}, POD={format_metric(art_b['pod'])}, "
        f"CSI={format_metric(art_b['csi'], 6)}, Lift={format_metric(art_b.get('lift_ratio_csi'), 2)}x\n"
        f"SAR on Arterials:  TP={art_s['hits']}, POD={format_metric(art_s['pod'])}, "
        f"CSI={format_metric(art_s['csi'], 6)}, Lift={format_metric(art_s.get('lift_ratio_csi'), 2)}x"
    )

    v = report["verdict_for_adjudication"]
    typer.echo("\n" + "=" * 70)
    typer.echo(f"VERDICT: {v['status']}")
    typer.echo("=" * 70)
    for f_line in v["findings"]:
        typer.echo(f"  * {f_line}")
    typer.echo(f"\nReport written to: {runs_dir / 'segment_validation_report.json'}")
    typer.echo(f"Manifest written: {runs_dir / 'segment_validation_manifest.json'}")


if __name__ == "__main__":
    app()
