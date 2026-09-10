#!/usr/bin/env python
"""CLI driver for the retained per-road-segment validation gate rescore.

The report covers BBMP, eligible ground-truth, road-class/arterial-trunk subsets,
the adjudication verdict, and the manifest lifecycle. Retired SAR and density
products are not loaded or reported.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

import typer  # noqa: E402
import yaml  # noqa: E402

from jaladhar.provenance import RunManifest  # noqa: E402
from jaladhar.validation.segment_validation import run_segment_validation_gate  # noqa: E402

app = typer.Typer(add_completion=False)


def format_metric(value: Any, places: int = 4) -> str:
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
    output_dir: Path = typer.Option(
        REPO / "runs/segment_validation/baseline",
        help="Distinct directory for the validation report and manifests",
    ),
    val_config: Path = typer.Option(
        REPO / "configs/validation.yaml", help="Validation configuration YAML path"
    ),
    mc_draws: int = typer.Option(
        10000, help="Number of Monte Carlo draws for mandatory null model floor estimation"
    ),
    seed: int = typer.Option(42, help="Random seed for reproducible Monte Carlo draws"),
) -> None:
    typer.echo("=================================================================")
    typer.echo("PHASE 3 PER-ROAD-SEGMENT FLOOD STATUS VALIDATION RESCORE")
    typer.echo("=================================================================")
    typer.echo(f"Runs Directory: {runs_dir}")
    typer.echo(f"Validation Config: {val_config}")
    typer.echo(f"Monte Carlo Draws for Null Model: {mc_draws:,} (seed={seed})")

    with val_config.open() as handle:
        validation_cfg: dict[str, Any] = yaml.safe_load(handle)
    lifecycle = RunManifest(
        output_dir / "segment_validation_manifest.json",
        stage="phase3_segment_validation_rescore",
        repo_root=REPO,
        resolved_config={
            "validation": validation_cfg,
            "monte_carlo_draws": mc_draws,
            "seed": seed,
            "phase3_run_dir": str(runs_dir),
            "output_dir": str(output_dir),
        },
        config_paths={"validation": str(val_config)},
        fields={"input_phase3_manifest": str(runs_dir / "manifest.json")},
    )
    lifecycle.start()
    try:
        report = run_segment_validation_gate(
            phase3_run_dir=runs_dir,
            output_dir=output_dir,
            val_config_path=val_config,
            n_mc_draws=mc_draws,
            seed=seed,
        )
        lifecycle.complete(
            {
                "report_path": str(output_dir / "segment_validation_report.json"),
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

    typer.echo("\n" + "-" * 70)
    typer.echo("HEADLINE RESULTS AGAINST MANDATORY NULL FLOOR")
    typer.echo("-" * 70)
    typer.echo(
        f"1. BBMP Flood-Prone Segments (Obs={bbmp['observed_flooded_segments']}, Pred={bbmp['predicted_flooded_segments']}):\n"  # noqa: E501
        f"   - Achieved: TP={bbmp['hits']}, POD={format_metric(bbmp['pod'])}, "
        f"FAR={format_metric(bbmp['far'])}, CSI={format_metric(bbmp['csi'], 6)}\n"
        f"   - Null Model: Mean CSI={format_metric(bbmp.get('null_csi_mean'), 6)} "
        f"(95% CI: [{format_metric(bbmp.get('null_csi_95ci', [None, None])[0], 6)}, "
        f"{format_metric(bbmp.get('null_csi_95ci', [None, None])[1], 6)}])\n"
        f"   - Lift Ratio over Random Null: {format_metric(bbmp.get('lift_ratio_csi'), 2)}x "
        f"(POD lift: {format_metric(bbmp.get('lift_ratio_pod'), 2)}x)"
    )
    typer.echo(
        f"2. Ground-Truth Verified Points (Obs={gt['observed_flooded_segments']}, Pred={gt['predicted_flooded_segments']}):\n"  # noqa: E501
        f"   - Achieved: TP={gt['hits']}, POD={format_metric(gt['pod'])}, "
        f"FAR={format_metric(gt['far'])}, CSI={format_metric(gt['csi'], 6)}\n"
        f"   - Null Model: Mean CSI={format_metric(gt.get('null_csi_mean'), 6)} "
        f"(95% CI: [{format_metric(gt.get('null_csi_95ci', [None, None])[0], 6)}, "
        f"{format_metric(gt.get('null_csi_95ci', [None, None])[1], 6)}])\n"
        f"   - Lift Ratio over Random Null: {format_metric(gt.get('lift_ratio_csi'), 2)}x "
        f"(POD lift: {format_metric(gt.get('lift_ratio_pod'), 2)}x)"
    )
    typer.echo(
        "\nSegment-level lift: "
        f"{format_metric(bbmp.get('lift_ratio_csi'), 2)}x (BBMP) / "
        f"{format_metric(gt.get('lift_ratio_csi'), 2)}x (eligible GT)"
    )

    art = report["stratification"]["arterial_trunk_alone"]
    typer.echo("\n" + "-" * 70)
    typer.echo("ARTERIAL / TRUNK SEGMENTS ALONE (Key Routing Links)")
    typer.echo("-" * 70)
    art_b = art["bbmp_event_max"]
    typer.echo(
        f"Arterial Total Segments: {art['total_arterial_segments']}\n"
        f"BBMP on Arterials: TP={art_b['hits']}, POD={format_metric(art_b['pod'])}, "
        f"CSI={format_metric(art_b['csi'], 6)}, "
        f"Lift={format_metric(art_b.get('lift_ratio_csi'), 2)}x"
    )

    trunk = report["trunk_signal_evaluation"]
    trunk_score = trunk["trunk_alone"]
    typer.echo(
        f"Trunk Segments: TP={trunk_score['hits']}, POD={format_metric(trunk_score['pod'])}, "
        f"Lift={format_metric(trunk_score.get('lift_ratio_pod'), 2)}x"
    )

    v = report["verdict_for_adjudication"]
    typer.echo("\n" + "=" * 70)
    typer.echo(f"VERDICT: {v['status']}")
    typer.echo("=" * 70)
    for f_line in v["findings"]:
        typer.echo(f"  * {f_line}")
    typer.echo(f"\nReport written to: {output_dir / 'segment_validation_report.json'}")
    typer.echo(f"Manifest written: {output_dir / 'segment_validation_manifest.json'}")


if __name__ == "__main__":
    app()
