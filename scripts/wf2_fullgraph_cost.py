"""WF-2 true full-graph coupling cost (adjudication round 2, owner directive 3).

The WF-2 coupled-smoke compute budget extrapolated with "coupling 1.54 ms/step
(full-graph routing, cell-count independent)" - but that number was measured by
``tests/coupling/smoke_real_window.py::probe_ms_per_step`` on the OLD smoke
window whose induced subgraph routes 8 nodes / 3 capacity-bearing edges over a
128x128 surface (adjudication round 2: V7 scope inflation, mislabelled term).
This module measures the REAL quantity:

- FULL graph assembled through the production path
  (``jaladhar.coupling.router.load_drain_graph`` -> ``read_artefact`` ->
  ``build_drain_graph``, window=None so ``node_id_map`` spans the whole
  buffered producer grid),
- h field at PRODUCER-grid shape (3521 x 3615) float32, uniform synthetic wet
  field ~0.3 m (declared-synthetic forcing for TIMING only - no physical claim),
- node_state zeros (dry network at rest, ``build_node_state``),
- dt = 0.48 s, device cpu, forward-only under ``torch.no_grad()`` (the old
  probe's protocol; autograd-through-routing calibration cost is OUT of scope),

Protocol: 2 warmup calls, then per-call timings over >= 20 calls for
(a) ``couple_step`` total and (b) ``route``-only on the full graph with a
representative post-capture volume (uniform 0.3 m head x node plan area;
route cost is volume-value-independent). The capture+return+surface share is
reported as the DERIVED difference of means, not a direct measurement.

Assembly guards make the full-graph scope self-evidencing (V2 observable): if
this ever ran against a windowed graph, ``plan.edges_routed`` would collapse
from 1208 and the assert reddens before any timing is recorded.

Rule-6 manifest written at run start (status running) and updated in place.
Every number comes from bytes read this run (V1). CPU-only (V12).
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import torch
import typer

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.exchange import build_node_state, couple_step
from jaladhar.coupling.router import load_drain_graph, route
from jaladhar.solver.state import StaticFields

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[1]

WARMUP_CALLS = 2
TIMED_CALLS = 25
DT_S = 0.48
INIT_DEPTH_M = 0.3


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git_state() -> tuple[str, bool, int]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        sha = "unknown"
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        )
        paths = [ln[3:].strip() for ln in out.splitlines() if ln.strip()]
    except Exception:
        paths = []
    return sha, bool(paths), len(paths)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")


def _time_calls(fn: Callable[[], Any], warmup: int, reps: int) -> list[float]:
    """Per-call wall-clock milliseconds: ``warmup`` unmeasured, then ``reps`` timed."""
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    return times


def _stats(times_ms: list[float]) -> dict[str, float]:
    """mean / p50 / p95 over per-call times (nearest-rank percentiles)."""
    s = sorted(times_ms)
    n = len(s)

    def q(p: float) -> float:
        return s[max(0, int(math.ceil(p * n)) - 1)]

    return {
        "mean": sum(s) / n,
        "p50": q(0.50),
        "p95": q(0.95),
        "min": s[0],
        "max": s[-1],
        "reps": n,
    }


def _zero_static(shape: tuple[int, int]) -> StaticFields:
    """Zeroed StaticFields-shaped shell at the producer-grid shape.

    couple_step reads ONLY static.drain_cap_m_s (entry guard asserts it zero);
    every other field is constructed at its declared shape with zeros so the
    shell is shape-honest. Labelled declared-synthetic, never presented as
    realized terrain fields.
    """
    hh, ww = shape
    z32 = lambda *s: torch.zeros(*s, dtype=torch.float32)  # noqa: E731
    return StaticFields(
        dz_x=z32(hh, ww - 1),
        dz_y=z32(hh - 1, ww),
        n_x=z32(hh, ww - 1),
        n_y=z32(hh - 1, ww),
        c_x=z32(hh, ww - 1),
        c_y=z32(hh - 1, ww),
        drain_cap_m_s=z32(hh, ww),
        infil_rate_m_s=z32(hh, ww),
        edge_w_n=z32(hh),
        edge_e_n=z32(hh),
        edge_n_n=z32(ww),
        edge_s_n=z32(ww),
        edge_w_s=z32(hh),
        edge_e_s=z32(hh),
        edge_n_s=z32(ww),
        edge_s_s=z32(ww),
        shape=shape,
    )


@app.command()
def main(
    config: Path = typer.Option(Path("configs/coupling.yaml"), "--config"),
    out_dir: Path = typer.Option(Path("runs/wf2_fullgraph_cost"), "--out-dir"),
    old_smoke_manifest: Path = typer.Option(
        Path("runs/wf2_coupled_smoke/manifest.json"), "--old-smoke-manifest"
    ),
    warmup: int = typer.Option(WARMUP_CALLS, "--warmup"),
    reps: int = typer.Option(TIMED_CALLS, "--reps"),
    dt: float = typer.Option(DT_S, "--dt"),
    depth_m: float = typer.Option(INIT_DEPTH_M, "--depth-m"),
) -> None:
    """Measure true full-graph couple_step / route ms-per-step on CPU."""
    t_all = time.perf_counter()
    cfg = resolve_config(config, REPO)
    od = Path(out_dir)
    od.mkdir(parents=True, exist_ok=True)

    sha, dirty, n_porcelain = _git_state()
    input_paths = {
        "gpkg": Path(cfg.graph.gpkg),
        "adjacency": Path(cfg.graph.adjacency),
        "graph_manifest": Path(cfg.graph.manifest),
        "prediction_set": Path(cfg.diagnostics.falsifier_set),
        "old_smoke_manifest": Path(old_smoke_manifest),
    }
    manifest: dict[str, Any] = {
        "stage": "wf2_fullgraph_cost",
        "status": "running",
        "started_utc": _utc_now(),
        "cpu_only": True,
        "git_sha": sha,
        "git_dirty": dirty,
        "git_porcelain_entry_count": n_porcelain,
        "torch_num_threads": torch.get_num_threads(),
        "protocol": {
            "assembly": (
                "jaladhar.coupling.router.load_drain_graph(cfg, repo, window=None) "
                "-> read_artefact -> build_drain_graph (production path)"
            ),
            "h_field": f"torch.full((3521, 3615), {depth_m}, float32) declared-synthetic "
            "(timing forcing only, no physical claim)",
            "node_state": "build_node_state(graph) - dry network at rest",
            "dt_s": dt,
            "device": "cpu",
            "autograd": "forward-only under torch.no_grad() (old probe's protocol)",
            "warmup_calls": warmup,
            "timed_calls": reps,
            "percentile_method": "nearest-rank on sorted per-call times",
        },
        "inputs_sha256": {k: _sha256_file(p) for k, p in input_paths.items()},
        "_t0": time.perf_counter(),
    }
    manifest_path = od / "manifest.json"
    _write_manifest(manifest_path, manifest)

    # ---- production full-domain assembly ------------------------------------
    graph = load_drain_graph(cfg, REPO, window=None)
    grid = (graph.manifest.get("config_snapshot") or {}).get("grid") or {}
    gh, gw = int(grid["height"]), int(grid["width"])
    n_cb = int(graph.capacity_bearing.sum().item())
    assembly_guards = {
        "num_nodes": graph.num_nodes,
        "num_edges": graph.num_edges,
        "expected_nodes": cfg.graph.expected_counts.nodes,
        "expected_edges": cfg.graph.expected_counts.edges,
        "capacity_bearing_edges": n_cb,
        "plan_edges_routed": graph.plan.edges_routed,
        "n_active_nodes": graph.n_active_nodes,
        "n_active_edges": graph.n_active_edges,
        "node_id_map_shape": list(graph.node_id_map.shape),
        "n_levels": graph.plan.n_levels,
        "n_active_predicted_targets": graph.n_active_predicted_targets,
        "n_inactive_predicted_targets": graph.n_inactive_predicted_targets,
    }
    # V2 observable for the mislabel being corrected: a windowed assembly would
    # collapse plan.edges_routed far below the full-graph 1208.
    assert graph.num_nodes == cfg.graph.expected_counts.nodes, "not the full node set"
    assert graph.num_edges == cfg.graph.expected_counts.edges, "not the full edge set"
    assert (
        graph.plan.edges_routed == n_cb == cfg.graph.expected_partition.capacity_bearing
    ), "routed subgraph is not the full capacity-bearing set"
    assert graph.n_active_nodes == graph.num_nodes, "windowed assembly leaked in"
    assert tuple(graph.node_id_map.shape) == (gh, gw), "node_id_map is not the full grid"
    manifest["assembly_guards"] = assembly_guards
    _write_manifest(manifest_path, manifest)

    # ---- inputs --------------------------------------------------------------
    static = _zero_static((gh, gw))
    assert bool((static.drain_cap_m_s == 0).all()), "drain_cap shell must be zero"
    h = torch.full((gh, gw), float(depth_m), dtype=torch.float32)
    qx = torch.zeros(gh, gw - 1, dtype=torch.float32)
    qy = torch.zeros(gh - 1, gw, dtype=torch.float32)
    node_state = build_node_state(graph)

    # ---- one diagnostic call: realized-work evidence -------------------------
    with torch.no_grad():
        cr = couple_step(h, qx, qy, static, node_state, graph, dt)
    manifest["diagnostic_call"] = {
        "capture_m3": cr.capture_m3,
        "return_m3": cr.return_m3,
        "surcharging_nodes": cr.surcharging_nodes,
        "cap_binding_this_step": cr.cap_binding_this_step,
        "max_node_head_m": cr.max_node_head_m,
        "h_new_min": float(cr.h_new.min().item()),
        "allocated_cells": int((graph.node_id_map >= 0).sum().item()),
    }

    # ---- timed measurements --------------------------------------------------
    with torch.no_grad():
        total_times = _time_calls(
            lambda: couple_step(h, qx, qy, static, node_state, graph, dt), warmup, reps
        )
        vol_route = float(depth_m) * graph.node_plan_area_m2.clone()
        route_times = _time_calls(lambda: route(vol_route, graph, dt), warmup, reps)
        _, _, route_diag = route(vol_route, graph, dt)

    total_stats = _stats(total_times)
    route_stats = _stats(route_times)
    old_man = json.loads(input_paths["old_smoke_manifest"].read_text())
    old_probe = old_man["compute_budget_pre_run"]["measured_probe"]
    old_couple_ms = float(old_probe["couple_ms_per_step"])
    correction = total_stats["mean"] / old_couple_ms
    manifest["fullgraph_couple_ms_per_step"] = {
        **total_stats,
        "route_only": route_stats,
        "derived_capture_return_surface_mean_ms": total_stats["mean"] - route_stats["mean"],
        "derived_note": (
            "difference of means (couple_step total minus route-only), NOT a directly "
            "measured phase split; route-only vol = depth_m * node_plan_area_m2 "
            "(uniform-head representative post-capture volume; route cost is "
            "volume-value-independent)"
        ),
        "route_diag_last_call": {
            "volume_transferred_m3": route_diag["volume_transferred_m3"],
            "capacity_bound_edges": route_diag["capacity_bound_edges"],
            "edges_routed": route_diag["edges_routed"],
        },
    }
    manifest["comparison_vs_old_label"] = {
        "old_labelled_value_ms_per_step": old_couple_ms,
        "old_probe_source_bytes": (
            f"{old_smoke_manifest}:compute_budget_pre_run.measured_probe.couple_ms_per_step"
        ),
        "old_probe_context": {
            "window_row0_row1_col0_col1": old_man.get("window"),
            "window_cells": old_man.get("compute_budget_pre_run", {}).get("window_cells"),
            "probe_reps": old_probe.get("probe_reps"),
            "n_active_nodes_in_window": old_man.get("d_g_declared_deviation_usage", {}).get(
                "n_active_nodes_in_window"
            ),
            "mislabel_verbatim": old_man.get("extrapolated_cost_bands", {}).get("basis"),
        },
        "correction_factor_vs_old_label": correction,
        "correction_definition": "fullgraph_total_mean_ms / old_labelled_ms",
    }
    manifest["finished_utc"] = _utc_now()
    manifest["wall_clock_sec"] = round(time.perf_counter() - t_all, 3)
    manifest["status"] = "completed"
    _write_manifest(manifest_path, manifest)

    typer.echo(
        f"full-graph couple_step: mean={total_stats['mean']:.3f} ms p50={total_stats['p50']:.3f} "
        f"p95={total_stats['p95']:.3f} (reps={total_stats['reps']})"
    )
    typer.echo(
        f"route()-only:           mean={route_stats['mean']:.3f} ms p50={route_stats['p50']:.3f} "
        f"p95={route_stats['p95']:.3f}"
    )
    typer.echo(
        f"vs old labelled {old_couple_ms:.4f} ms/step (3-CB-edge window probe): "
        f"correction factor x{correction:.2f}"
    )
    typer.echo(f"manifest: {manifest_path}")


if __name__ == "__main__":
    app()
