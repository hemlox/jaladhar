#!/usr/bin/env python
"""WF-2 round 3 / milestone 2, owner ruling D-GT 2026-08-26: FINAL edge-mode
attribution of the 24 GT points onto the REALIZED coupled-resmoke surcharge
events.

What this script does (and does not):
  - reads ``runs/wf2_coupled_resmoke/products/surcharge_events.csv`` READ-ONLY
    (485 rows [F]); its producing run manifest's ``gt_attribution`` block was
    produced under the superseded node mode and is NEVER reconciled by
    rewriting it;
  - cross-checks the resmoke manifest's ``version_pins.graph_fingerprint``
    against a FRESH production :func:`load_drain_graph` load — a mismatch
    means the events were produced on a different graph than the one whose
    classes classify the responsible nodes here, and the run REFUSES;
  - classifies RESPONSIBLE nodes ONLY through the IMPORTED M1 classifier path
    (``jaladhar.coupling.router.DrainGraph.component_class`` over the
    consumer-side v2-lake-boundary seeds of
    ``jaladhar.drainage.terminal``) — nothing is recomputed locally;
  - runs :func:`jaladhar.coupling.diagnostics.attribute_ground_truth_edges`
    at the CONFIGURED radius (``diagnostics.attribution_radius_m``, chosen by
    the pre-registered anti-tuning rule recorded in runs/wf2_gt_edges/) and
    reports the match rate honestly AS A FINDING — the low expected rate at
    ~40 m is the ruling's correct outcome, never tuned upward;
  - writes ``runs/wf2_gt_edge_attribution/{attribution.json,manifest.json}``;
    the manifest follows rule 6 verbatim (status "running" AT START with git
    sha + input sha256s, updated IN PLACE on completion/failure).

Usage:
    .venv/bin/python scripts/wf2_gt_edge_attribution_eval.py
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import typer

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.diagnostics import (
    attribute_ground_truth_edges,
    load_drain_edges_geoms,
    read_surcharge_events_csv,
)
from jaladhar.coupling.router import load_drain_graph

REPO = Path(__file__).resolve().parents[1]

# Read-only inputs (this script owns NOTHING outside runs/wf2_gt_edge_attribution/)
COUPLING_YAML = REPO / "configs/coupling.yaml"
RESMOKE_MANIFEST = REPO / "runs/wf2_coupled_resmoke/manifest.json"
REALIZED_EVENTS_CSV = REPO / "runs/wf2_coupled_resmoke/products/surcharge_events.csv"

OUT_DIR = REPO / "runs/wf2_gt_edge_attribution"


class EvalRefusal(RuntimeError):
    """Hard stop: a declared input or cross-check contradicts realized state."""


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        )
        return bool(out.strip())
    except Exception:
        return True


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _input_sha_block(cfg) -> dict[str, str]:
    paths = [
        COUPLING_YAML,
        RESMOKE_MANIFEST,
        REALIZED_EVENTS_CSV,
        Path(cfg.graph.gpkg),
        Path(cfg.graph.adjacency),
        Path(cfg.graph.manifest),
        Path(cfg.graph.terminal_definition),
        Path(cfg.diagnostics.ground_truth_manifest),
    ]
    return {str(p.relative_to(REPO)): sha256_file(p) for p in paths}


def run(
    config: Path = COUPLING_YAML,
    out_dir: Path = OUT_DIR,
) -> dict:
    t0 = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    cfg = resolve_config(config, REPO)
    if cfg.diagnostics.attribution_mode != "edge":
        raise EvalRefusal(
            f"diagnostics.attribution_mode={cfg.diagnostics.attribution_mode!r} — this eval "
            "implements the D-GT edge-mode ruling; running it under 'node' would misreport"
        )

    # --- rule 6: MANIFEST AT START -------------------------------------------
    manifest: dict = {
        "stage": "wf2_gt_edge_attribution",
        "purpose": (
            "D-GT ruling evaluation: attribute the 24 GT points to nearest drain EDGES on "
            "the REALIZED resmoke surcharge events; responsible node = edge.to_node; "
            "classes via the imported M1 classifier path only."
        ),
        "status": "running",
        "git_sha": git_sha(),
        "git_dirty": git_dirty(),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "start_time_iso": datetime.now(UTC).isoformat(),
        "inputs_sha256": _input_sha_block(cfg),
        "config_resolved": {
            "attribution_mode": cfg.diagnostics.attribution_mode,
            "attribution_radius_m": cfg.diagnostics.attribution_radius_m,
            "attribution_radius_basis": cfg.diagnostics.attribution_radius_basis,
            "suspicious_deadend_share": cfg.diagnostics.suspicious_deadend_share,
            "expected_counts": {
                "nodes": cfg.graph.expected_counts.nodes,
                "edges": cfg.graph.expected_counts.edges,
            },
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        rows = read_surcharge_events_csv(REALIZED_EVENTS_CSV)
        manifest["realized_events"] = {
            "path": str(REALIZED_EVENTS_CSV.relative_to(REPO)),
            "n_rows": len(rows),
            "note": "READ-ONLY input; the producing manifest's superseded node-mode "
            "gt_attribution block is never rewritten",
        }

        # --- fresh production load: the ONLY class source --------------------
        graph = load_drain_graph(cfg, REPO)
        term_echo = (graph.manifest or {}).get("terminal_seed_resolution") or {}

        # --- V8 cross-checks against the producing run -----------------------
        resmoke = json.loads(RESMOKE_MANIFEST.read_text())
        recorded_fp = (resmoke.get("version_pins") or {}).get("graph_fingerprint")
        fp_status = (
            "match"
            if recorded_fp is not None and str(recorded_fp) == str(graph.graph_fingerprint)
            else "MISMATCH"
        )
        if fp_status != "match":
            raise EvalRefusal(
                f"resmoke manifest graph_fingerprint {recorded_fp!r} != fresh production "
                f"fingerprint {graph.graph_fingerprint!r} — events were not produced on the "
                "graph being scored; refusing rather than joining across graphs"
            )
        ledger_returned = resmoke.get("surcharge_returned_m3")
        csv_sum = math.fsum(r["total_returned_m3"] for r in rows)
        ledger_status = (
            "match"
            if isinstance(ledger_returned, (int, float))
            and math.isclose(csv_sum, float(ledger_returned), rel_tol=1e-9, abs_tol=0.0)
            else "MISMATCH"
        )
        if ledger_status != "match":
            raise EvalRefusal(
                f"surcharge_events.csv sum {csv_sum!r} != resmoke manifest "
                f"surcharge_returned_m3 {ledger_returned!r} — artefact/manifest drift, refusing"
            )
        # C3 (round 3): the producing run also recorded HOW MANY event rows it
        # flushed; the artefact must carry exactly that many. (Residual limitation
        # stated beside: this is a count+sum check only.)
        rows_declared = resmoke.get("surcharge_events_rows")
        rows_status = (
            "match"
            if isinstance(rows_declared, int)
            and not isinstance(rows_declared, bool)
            and rows_declared == len(rows)
            else "MISMATCH"
        )
        if rows_status != "match":
            raise EvalRefusal(
                f"surcharge_events.csv has {len(rows)} rows but the producing run manifest "
                f"recorded surcharge_events_rows={rows_declared!r} — row-count drift, refusing"
            )
        manifest["cross_checks"] = {
            "graph_fingerprint": {
                "resmoke_recorded": recorded_fp,
                "fresh_production_load": graph.graph_fingerprint,
                "status": fp_status,
            },
            "ledger_total_returned_m3": {
                "csv_fsum": csv_sum,
                "resmoke_manifest": ledger_returned,
                "status": ledger_status,
            },
            "surcharge_events_rows": {
                "csv_n_rows": len(rows),
                "producing_run_manifest": rows_declared,
                "status": rows_status,
                "residual_limitation": (
                    "row-count + total-sum equality does NOT prove content identity: a "
                    "content-preserving same-sum mutation of individual row fields would "
                    "pass both checks. Undetectable without per-row hashes serialized at "
                    "write time (the §11.1 schema carries none) — stated limitation, not a "
                    "guarantee"
                ),
            },
        }
        manifest["class_source"] = {
            "path": "jaladhar.coupling.router.DrainGraph.component_class via production "
            "load_drain_graph (M1 terminal module; NOT recomputed locally)",
            "terminal_definition_version": term_echo.get("definition_version"),
            "terminal_counts_by_rule": term_echo.get("counts_by_rule"),
            "directed_split_live": {
                "outfall_terminating": graph.component_class.count("outfall_terminating"),
                "dead_end": graph.component_class.count("dead_end"),
            },
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))

        # --- edge-mode attribution at the configured radius ------------------
        geom_by_id, _from_by_id, to_by_id, edge_crs = load_drain_edges_geoms(Path(cfg.graph.gpkg))
        attr = attribute_ground_truth_edges(
            rows,
            Path(cfg.diagnostics.ground_truth_manifest),
            radius_m=float(cfg.diagnostics.attribution_radius_m),
            edge_geoms=geom_by_id,
            responsible_node_of_edge=to_by_id,
            expected_edge_count=int(cfg.graph.expected_counts.edges),
            graph=graph,
            suspicious_deadend_share=float(cfg.diagnostics.suspicious_deadend_share),
            radius_basis=str(cfg.diagnostics.attribution_radius_basis),
            edge_crs=edge_crs or None,
        )

        # split matched points by RESPONSIBLE-node class; each responsible node's
        # returned volume counted ONCE (labels name every basis explicitly)
        split: dict[str, dict] = {
            cls: {"nodes": 0, "points": 0, "m3": 0.0} for cls in ("outfall_terminating", "dead_end")
        }
        seen: set[int] = set()
        vol_by_node = {r["node_id"]: r["total_returned_m3"] for r in rows}
        for p in attr["points"]:
            if not p["matched"]:
                continue
            cls = p["responsible_node_class"]
            bucket = split.setdefault(cls, {"nodes": 0, "points": 0, "m3": 0.0})
            bucket["points"] += 1
            nid = int(p["responsible_node_id"])
            if nid not in seen:
                seen.add(nid)
                bucket["nodes"] += 1
                bucket["m3"] += vol_by_node[nid]

        n_matched = attr["n_gt_matched"]
        finding = (
            f"HONEST FINDING: {n_matched}/{attr['n_gt_points']} GT points are attributed at "
            f"the configured r*={cfg.diagnostics.attribution_radius_m:g} m. This low rate is "
            "the EXPECTED outcome of the ruling's anti-tuning contract (measured min "
            "GT-to-edge distance 39.7 m [F], runs/wf2_gt_edges/); it is reported as a "
            "finding about the drain network's coverage relative to the Sept-2022 flood "
            "points, NEVER tuned away."
        )

        attribution_out = {
            **attr,
            "matched_split_by_responsible_node_class": split,
            "split_labels": {
                "nodes": "unique responsible nodes of this class among MATCHED points",
                "points": (
                    "matched GT points attributed via this class (one responsible node per point)"
                ),
                "m3": "returned volume of the unique responsible nodes, each counted ONCE",
            },
            "finding_statement": finding,
            "evaluated_events_source": str(REALIZED_EVENTS_CSV.relative_to(REPO)),
            # C3 (round 3): integrity statement carried IN the output block.
            "events_integrity": {
                "n_rows_vs_producing_manifest": {
                    "csv": len(rows),
                    "manifest": rows_declared,
                    "status": rows_status,
                },
                "residual_limitation": (
                    "row-count and total-sum checks are content-preserving-mutation "
                    "BLIND: a same-sum rewrite of individual row fields passes both. "
                    "Per-row hashes do not exist in the §11.1 schema; closing this "
                    "needs a writer-side change, not a scorer-side check"
                ),
            },
        }
        attribution_path = out_dir / "attribution.json"
        attribution_path.write_text(json.dumps(attribution_out, indent=2))

        wall = time.perf_counter() - t0
        manifest.update(
            {
                "status": "completed",
                "wall_clock_sec": round(wall, 3),
                "outputs": {
                    "attribution": str(attribution_path.relative_to(REPO)),
                },
                "result_headline": {
                    "n_gt_matched": n_matched,
                    "n_gt_points": attr["n_gt_points"],
                    "radius_m": cfg.diagnostics.attribution_radius_m,
                    "suspicious_state": attr["suspicious_state"],
                    "matched_split": split,
                },
                "completed_at_iso": datetime.now(UTC).isoformat(),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))
    except Exception:
        manifest["status"] = "failed"
        manifest["failed_at_iso"] = datetime.now(UTC).isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise

    # --- stdout HEADLINE ------------------------------------------------------
    print(
        f"D-GT EDGE ATTRIBUTION | matched {n_matched}/{attr['n_gt_points']} at "
        f"r*={cfg.diagnostics.attribution_radius_m:g} m | SUSPICIOUS="
        f"{attr['suspicious_state']}"
    )
    for cls, b in split.items():
        print(f"  {cls}: nodes={b['nodes']} points={b['points']} m3={b['m3']!r}")
    print(f"  {finding}")
    print(f"wrote {out_dir.relative_to(REPO)}/attribution.json")
    return attribution_out


app = typer.Typer(add_completion=False)


@app.command()
def main(
    config: Path = typer.Option(COUPLING_YAML, help="Coupling YAML (rule-7 resolved)."),
    out_dir: Path = typer.Option(OUT_DIR, help="Owned output directory (rule-6 run dir)."),
) -> None:
    """Evaluate edge-mode GT attribution on the REALIZED resmoke events."""
    run(config=config, out_dir=out_dir)


if __name__ == "__main__":
    app()
