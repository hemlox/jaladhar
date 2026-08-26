"""WF-1 drain-graph build pipeline (orchestrator-owned).

Runs the full literal-pin composite end to end on CPU:

    loader.load_reaches -> stitch.stitch_components -> capacity.assign_capacity
    -> graph_io.finalize_manifest -> graph_io.write_candidate (publish gated)

Rule 6: the run manifest is created by graph_io.init_manifest at start
(status "running") and updated in place by finalize/write_candidate.
Rule 7: every module resolves its own config keys with aggregated errors.

Expected honest end state (pre-registered, spec.md ACCEPTANCE RULE):
status="stopped_owner_adjudication" while component_count_post_stitch > 1 and/or
capacity_status == "blocked_missing_design_intensity"; candidate artefacts live
only under runs/drain_graph_build/ - nothing is placed at data/processed/.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from jaladhar.drainage import capacity, graph_io, loader, stitch

app = typer.Typer(help="WF-1 drain-graph build pipeline (CPU-only).")


def _repo(p: str | Path) -> Path:
    base = Path(__file__).resolve().parents[3]
    p = Path(p)
    return p if p.is_absolute() else base / p


@app.command()
def run(
    config: str = typer.Option("configs/drainage.yaml", "--config"),
) -> None:
    t0 = time.perf_counter()
    cfg = stitch.resolve_config(config)
    outputs = cfg["outputs"]
    run_dir = _repo(outputs["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = graph_io.init_manifest(str(run_dir), cfg)

    loader.bind_config(cfg)
    reaches, loader_diag = loader.load_reaches(
        str(_repo(cfg["inputs"]["kml_primary"])),
        str(_repo(cfg["inputs"]["kml_secondary"])),
        kml_tertiary_report_only=str(_repo(cfg["inputs"]["kml_tertiary_report_only"])),
    )
    manifest["loader_diagnostics"] = {
        k: v for k, v in loader_diag.items() if isinstance(v, (int, float, str, bool, type(None)))
    }

    result = stitch.stitch_components(reaches, cfg)

    pointer_path = run_dir / "derived_pntr_d8.tif"
    elevation_path = _repo(cfg["inputs"]["elevation_surface"])
    nodes, edges, cap_metrics = capacity.assign_capacity(
        result.nodes,
        result.edges,
        str(pointer_path),
        str(elevation_path),
        cfg,
    )

    manifest = graph_io.finalize_manifest(
        manifest,
        nodes=nodes,
        edges=edges,
        stitch_metrics=result.metrics,
        capacity_metrics=cap_metrics,
    )

    written = graph_io.write_candidate(nodes, edges, manifest, str(run_dir), publish=False)

    fraction = float(manifest.get("synthesised_fraction_of_total_length", float("nan")))
    post = (
        int(manifest.get("component_count_pre_post", {}).get("post", 0))
        if isinstance(manifest.get("component_count_pre_post"), dict)
        else int(manifest.get("component_count_post_stitch", -1))
    )
    pre = int(manifest.get("component_count_pre_stitch", -1))
    status = str(manifest.get("status"))
    reasons = manifest.get("reasons", [])

    print(
        "[pipeline] RULE-1 HEADLINE synthesised_fraction_of_total_length "
        f"= {fraction:.6f} ({fraction * 100:.2f}% of 767.3 km observed)"
    )
    print(f"[pipeline] components pre_stitch={pre} post_stitch={post}")
    print(f"[pipeline] status={status} reasons={reasons}")
    print(f"[pipeline] artefacts={sorted(written) if isinstance(written, dict) else written}")
    print(f"[pipeline] wall_clock={time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    app()
