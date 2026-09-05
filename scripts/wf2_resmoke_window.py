"""WF-2 re-smoke window selection (adjudication round 2, owner directives 2).

Two deliverables against the realized WF-1 artefact (contract v1.2.0, D-G
amendment landed: ``jaladhar.drainage.graph_io.read_artefact`` now ACCEPTS
``runs/drain_graph_build/drain_graph.gpkg``):

D1 (pre-re-smoke gate): does the OLD smoke window (rows[1008,1136) x
   cols[1156,1284), read from ``runs/wf2_coupled_smoke/manifest.json``
   ``window``, never hardcoded) route to an outfall AT ALL? The induced
   subgraph on nodes active in that window is built and directed reachability
   over ACTIVE edges to the outfall set is computed (same semantics as
   ``jaladhar.coupling.router._compute_component_classes``: an outfall reaches
   itself at path length 0). Expected NO - measured, never assumed.

D2 (window selection): scan candidate windows anchored (strided offsets) on
   outfall-node and predicted-target-node coordinates, maximizing IN ORDER:
   (a) the induced subgraph contains a REAL outfall path (some active node
       reaches an active outfall over >= 1 active directed edge - the strong
       reading; a bare outfall with no active feeder is NOT a path),
   (b) >= smoke.min_predicted_nodes_in_window pre-registered targets active,
   (c) most active capacity-bearing edges.
   Cell cap 65,536 first; escalate to 262,144 ONLY if no outfall-path window
   exists at the first cap. The config gate (smoke.min_predicted_nodes_in_window)
   is NEVER relaxed here - shortfall is reported for the owner (D-W holder).

Rule-6 manifest written at run start (status running) and updated in place.
Every number comes from bytes read this run (V1). CPU-only (V12).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import typer

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.router import OWNER_ADJUDICATION_REF_M5B
from jaladhar.drainage.graph_io import read_artefact

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[1]

CAP_FIRST_CELLS = 65_536
CAP_ESCALATE_CELLS = 262_144
OFFSETS_PER_AXIS = 4  # strided top-left candidates per anchor per axis


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


def _producer_grid(graph_manifest_path: Path) -> dict[str, Any]:
    """Producer-declared grid block from the graph build manifest bytes (V8 seam)."""
    man = json.loads(graph_manifest_path.read_text())
    grid = (man.get("config_snapshot") or {}).get("grid") or {}
    missing = [k for k in ("height", "width", "transform", "cell_area_m2") if k not in grid]
    if missing:
        raise RuntimeError(f"producer grid block missing keys {missing} in {graph_manifest_path}")
    tr = [float(v) for v in grid["transform"]]
    res, x_origin, y_top = tr[0], tr[2], tr[5]
    if tr[1] != 0.0 or tr[3] != 0.0 or abs(tr[4]) != res:
        raise RuntimeError(f"producer transform {tr} not axis-aligned at resolution {res}")
    return {
        "height": int(grid["height"]),
        "width": int(grid["width"]),
        "transform": tr,
        "resolution_m": res,
        "x_origin_m": x_origin,
        "y_top_m": y_top,
        "cell_area_m2": float(grid["cell_area_m2"]),
    }


def _backward_closure(
    seed: np.ndarray, edge_from: np.ndarray, edge_to: np.ndarray, active_edge: np.ndarray
) -> np.ndarray:
    """Nodes that reach ``seed`` following directed edges backwards over ACTIVE edges.

    Same propagation direction as router._compute_component_classes (mark sources
    of edges whose target is reached); fixpoint over the 1587-edge array.
    """
    reached = seed.copy()
    while True:
        sel = active_edge & reached[edge_to] & ~reached[edge_from]
        if not bool(sel.any()):
            return reached
        reached[edge_from[sel]] = True


def _exact_max_containment(
    pt_row: np.ndarray,
    pt_col: np.ndarray,
    side: int,
    gh: int,
    gw: int,
    must_contain_row: np.ndarray | None = None,
    must_contain_col: np.ndarray | None = None,
) -> tuple[int, tuple[int, int]]:
    """EXACT max number of ``pt`` points inside any side x side window (exhaustive-
    equivalent, not a sampled bound): any maximal box translates so its top/left
    edge sits ON a contained point, so candidate top-lefts {row_i} x {col_j}
    (clamped into the grid) cover the optimum. With ``must_contain_*`` the box
    must additionally include >= 1 of those points."""
    cand_r = np.minimum(pt_row, gh - side)
    cand_c = np.minimum(pt_col, gw - side)
    best_n, best_box = -1, (0, 0)
    for r0 in np.unique(cand_r):
        in_r = (pt_row >= r0) & (pt_row < r0 + side)
        for c0 in np.unique(cand_c):
            in_w = in_r & (pt_col >= c0) & (pt_col < c0 + side)
            if must_contain_row is not None:
                ok_mc = (
                    (must_contain_row >= r0)
                    & (must_contain_row < r0 + side)
                    & (must_contain_col >= c0)
                    & (must_contain_col < c0 + side)
                )
                if not bool(ok_mc.any()):
                    continue
            n = int(in_w.sum())
            if n > best_n:
                best_n, best_box = n, (int(r0), int(c0))
    return best_n, best_box


@app.command()
def main(
    config: Path = typer.Option(Path("configs/coupling.yaml"), "--config"),
    out_dir: Path = typer.Option(Path("runs/wf2_resmoke_window"), "--out-dir"),
    old_smoke_manifest: Path = typer.Option(
        Path("runs/wf2_coupled_smoke/manifest.json"), "--old-smoke-manifest"
    ),
    runner_up_count: int = typer.Option(8, "--runner-ups"),
) -> None:
    """D1 old-window outfall gate + D2 re-smoke window selection."""
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
        "stage": "wf2_resmoke_window",
        "status": "running",
        "started_utc": _utc_now(),
        "cpu_only": True,
        "git_sha": sha,
        "git_dirty": dirty,
        "git_porcelain_entry_count": n_porcelain,
        "config_snapshot": {
            "config_yaml": str(config),
            "graph_expected_counts": {
                "nodes": cfg.graph.expected_counts.nodes,
                "edges": cfg.graph.expected_counts.edges,
            },
            "expected_partition": {
                "capacity_bearing": cfg.graph.expected_partition.capacity_bearing,
                "zero_slope": cfg.graph.expected_partition.zero_slope,
                "area_capped": cfg.graph.expected_partition.area_capped,
                "synthetic": cfg.graph.expected_partition.synthetic,
            },
            "capture_radius_m": cfg.exchange.capture_radius_m,
            "min_predicted_nodes_in_window_cfg_gate_NEVER_relaxed_here": (
                cfg.smoke.min_predicted_nodes_in_window
            ),
            "cap_first_cells": CAP_FIRST_CELLS,
            "cap_escalate_cells": CAP_ESCALATE_CELLS,
            "criterion_order": [
                "a: real outfall path in induced subgraph (>=1 active edge into an "
                "active outfall reachable from some active node)",
                "b: n_predicted_active >= min_predicted_nodes_in_window (gate held, "
                "not relaxed)",
                "c: max active capacity-bearing edges",
            ],
        },
        "inputs_sha256": {k: _sha256_file(p) for k, p in input_paths.items()},
        "_t0": time.perf_counter(),
    }
    manifest_path = od / "manifest.json"
    _write_manifest(manifest_path, manifest)

    # ---- load the realized artefact through the production asserting reader ----
    art = read_artefact(
        str(input_paths["gpkg"]),
        str(input_paths["adjacency"]),
        str(input_paths["graph_manifest"]),
        owner_adjudication_ref=OWNER_ADJUDICATION_REF_M5B + ";consumer=wf2-resmoke-window",
    )
    nodes_gdf = art["nodes_gdf"]
    edges_gdf = art["edges_gdf"]
    manifest["reader_recorded_properties"] = {
        "dropped_zero_length_count": art["dropped_zero_length_count"],
        "dropped_self_loop_count": art["dropped_self_loop_count"],
        "null_capacity_edge_count": art["null_capacity_edge_count"],
        "nodes": len(nodes_gdf),
        "edges": len(edges_gdf),
    }

    # ---- producer grid (bytes) vs task-declared grid ------------------------
    grid = _producer_grid(input_paths["graph_manifest"])
    task_declared = {
        "height": 3521,
        "width": 3615,
        "transform": [10.0, 0.0, 766440.0, 0.0, -10.0, 1454850.0],
    }
    manifest["producer_grid_realized"] = grid
    manifest["task_declared_grid"] = task_declared
    manifest["grid_match_declared_vs_realized"] = bool(
        grid["height"] == task_declared["height"]
        and grid["width"] == task_declared["width"]
        and grid["transform"] == task_declared["transform"]
    )
    gh, gw, res = grid["height"], grid["width"], grid["resolution_m"]

    # ---- node/edge arrays (0-based index = node_id - 1, router convention) ---
    x_arr = nodes_gdf["x_m"].to_numpy(dtype=np.float64)
    y_arr = nodes_gdf["y_m"].to_numpy(dtype=np.float64)
    node_row = np.floor((grid["y_top_m"] - y_arr) / res).astype(np.int64)
    node_col = np.floor((x_arr - grid["x_origin_m"]) / res).astype(np.int64)
    inside = (node_col >= 0) & (node_col < gw) & (node_row >= 0) & (node_row < gh)
    if not bool(inside.all()):
        raise RuntimeError("a node falls outside the producer-declared buffered grid")

    num_nodes = len(nodes_gdf)
    edge_from = edges_gdf["from_node"].to_numpy(dtype=np.int64) - 1
    edge_to = edges_gdf["to_node"].to_numpy(dtype=np.int64) - 1
    src = edges_gdf["edge_source"].astype(str).to_numpy()
    q_notna = edges_gdf["q_capacity_nom_m3s"].notna().to_numpy()
    cb_mask = (src == "observed") & q_notna  # router.py is_cb definition
    outfall_mask = (nodes_gdf["node_type"].astype(str) == "outfall").to_numpy()
    n_outfalls = int(outfall_mask.sum())
    if n_outfalls != 17:
        raise RuntimeError(f"expected 17 outfall nodes, realized {n_outfalls}")

    pred_ids = json.loads(input_paths["prediction_set"].read_text())["predicted_node_ids"]
    pred_idx = np.asarray([int(i) - 1 for i in pred_ids], dtype=np.int64)
    n_oob_pred = int(((pred_idx < 0) | (pred_idx >= num_nodes)).sum())

    def window_stats(r0: int, c0: int, side: int) -> dict[str, Any]:
        act = (node_row >= r0) & (node_row < r0 + side)
        act &= (node_col >= c0) & (node_col < c0 + side)
        a_edge = act[edge_from] & act[edge_to]
        n_act, n_ae, n_cb = int(act.sum()), int(a_edge.sum()), int((a_edge & cb_mask).sum())
        seed = act & outfall_mask
        weak = _backward_closure(seed, edge_from, edge_to, a_edge)
        preds_of_outfall = np.zeros(num_nodes, dtype=bool)
        if bool(seed.any()):
            cand = edge_from[a_edge & seed[edge_to]]
            if cand.size:
                preds_of_outfall[np.unique(cand)] = True
        strong = _backward_closure(preds_of_outfall, edge_from, edge_to, a_edge)
        return {
            "rows": [r0, r0 + side],
            "cols": [c0, c0 + side],
            "cells": side * side,
            "n_active_nodes": n_act,
            "n_active_edges": n_ae,
            "n_active_cb_edges": n_cb,
            "n_active_outfalls_in_window": int(seed.sum()),
            "outfall_reachable_weak_incl_len0": bool((weak & act).any()),
            "outfall_reachable_strong_len_ge1": bool((strong & act).any()),
            "n_nodes_with_real_outfall_path": int((strong & act).sum()),
            "n_predicted_active": int(act[pred_idx].sum()),
        }

    # ================= DELIVERABLE 1: old-window outfall gate =================
    old_man = json.loads(input_paths["old_smoke_manifest"].read_text())
    win_raw = old_man.get("window")
    # The old driver serialized .window as a str()-of-tuple; parse either form.
    if isinstance(win_raw, str):
        parts = re.findall(r"-?\d+", win_raw)
        win = [int(v) for v in parts] if len(parts) == 4 else None
    elif isinstance(win_raw, list) and len(win_raw) == 4:
        win = [int(v) for v in win_raw]
    else:
        win = None
    if win is None:
        raise RuntimeError(
            f"old smoke manifest {old_smoke_manifest} lacks a 4-int window "
            f"(raw value: {win_raw!r})"
        )
    or0, or1, oc0, oc1 = win
    old_side_r, old_side_c = or1 - or0, oc1 - oc0
    if old_side_r != old_side_c:
        raise RuntimeError(f"old window is not square: rows {old_side_r} cols {old_side_c}")
    d1 = window_stats(or0, oc0, old_side_r)
    d1["source_window_bytes"] = f"{old_smoke_manifest}:window={win_raw!r}"
    d1["old_manifest_recorded_n_active_nodes"] = old_man.get(
        "d_g_declared_deviation_usage", {}
    ).get("n_active_nodes_in_window")
    d1["old_manifest_recorded_predicted_contained"] = old_man.get(
        "smoke_window_derivation", {}
    ).get("predicted_nodes_contained")
    d1["verdict"] = (
        "OLD WINDOW REACHES NO OUTFALL"
        if not d1["outfall_reachable_strong_len_ge1"]
        else "old window DOES reach an outfall"
    )
    manifest["deliverable_1_old_window"] = d1
    _write_manifest(manifest_path, manifest)

    # ================= DELIVERABLE 2: candidate window scan ===================
    anchors: set[tuple[int, int]] = set()
    for mask in (outfall_mask,):
        for r, c in zip(node_row[mask].tolist(), node_col[mask].tolist(), strict=True):
            anchors.add((int(r), int(c)))
    for idx in np.unique(pred_idx):
        anchors.add((int(node_row[idx]), int(node_col[idx])))

    def scan(cap_cells: int) -> tuple[int, list[dict[str, Any]]]:
        side = math.isqrt(cap_cells)
        cands: set[tuple[int, int]] = set()
        offs = [side * i // OFFSETS_PER_AXIS for i in range(OFFSETS_PER_AXIS)]
        for ra, ca in anchors:
            for off_r in offs:
                for off_c in offs:
                    r0 = min(max(ra - off_r, 0), gh - side)
                    c0 = min(max(ca - off_c, 0), gw - side)
                    cands.add((r0, c0))
        results = [window_stats(r0, c0, side) for r0, c0 in sorted(cands)]
        return side, results

    def rank_key(s: dict[str, Any]) -> tuple[int, int, int, int, int, int, int]:
        return (
            int(s["outfall_reachable_strong_len_ge1"]),
            int(s["n_predicted_active"] >= cfg.smoke.min_predicted_nodes_in_window),
            s["n_active_cb_edges"],
            s["n_active_edges"],
            s["n_predicted_active"],
            -s["rows"][0],
            -s["cols"][0],
        )

    side_used, results = scan(CAP_FIRST_CELLS)
    cap_used = CAP_FIRST_CELLS
    escalated = False
    results.sort(key=rank_key, reverse=True)
    if not results[0]["outfall_reachable_strong_len_ge1"]:
        escalated = True
        cap_used = CAP_ESCALATE_CELLS
        side_used, results_512 = scan(CAP_ESCALATE_CELLS)
        results_512.sort(key=rank_key, reverse=True)
        results = results_512

    best = results[0]
    a_ok_pool = [r for r in results if r["outfall_reachable_strong_len_ge1"]]
    max_pred_overall = max(r["n_predicted_active"] for r in results)
    max_pred_in_outfall_windows = max((r["n_predicted_active"] for r in a_ok_pool), default=0)
    chosen = {
        **best,
        "fraction_of_1587_edges_pct": round(100.0 * best["n_active_edges"] / len(edges_gdf), 4),
        "fraction_of_1208_cb_edges_pct": round(
            100.0 * best["n_active_cb_edges"] / int(cb_mask.sum()), 4
        ),
        "predicted_target_met_at_cap": bool(
            best["n_predicted_active"] >= cfg.smoke.min_predicted_nodes_in_window
        ),
        "max_predicted_active_achieved_any_candidate": max_pred_overall,
        "max_predicted_active_achieved_outfall_path_windows": max_pred_in_outfall_windows,
    }
    # Exact (exhaustive-equivalent) containment bounds at the first cap, so the
    # D-W shortfall report separates "anchored scan did not find" from "none
    # exists": max predicted targets in ANY 64k window, and in any 64k window
    # that contains at least one outfall node.
    ex_any_n, ex_any_box = _exact_max_containment(
        node_row[pred_idx], node_col[pred_idx], math.isqrt(CAP_FIRST_CELLS), gh, gw
    )
    ex_of_n, ex_of_box = _exact_max_containment(
        node_row[pred_idx],
        node_col[pred_idx],
        math.isqrt(CAP_FIRST_CELLS),
        gh,
        gw,
        must_contain_row=node_row[outfall_mask],
        must_contain_col=node_col[outfall_mask],
    )
    manifest["deliverable_2_selection"] = {
        "n_predicted_targets": int(pred_idx.size),
        "n_predicted_targets_out_of_grid": n_oob_pred,
        "exact_max_predicted_any_window_at_first_cap": {
            "count": ex_any_n,
            "example_rows_cols": [
                ex_any_box[0],
                ex_any_box[0] + math.isqrt(CAP_FIRST_CELLS),
                ex_any_box[1],
                ex_any_box[1] + math.isqrt(CAP_FIRST_CELLS),
            ],
        },
        "exact_max_predicted_outfall_containing_window_at_first_cap": {
            "count": ex_of_n,
            "example_rows_cols": [
                ex_of_box[0],
                ex_of_box[0] + math.isqrt(CAP_FIRST_CELLS),
                ex_of_box[1],
                ex_of_box[1] + math.isqrt(CAP_FIRST_CELLS),
            ],
        },
        "cap_cells_first": CAP_FIRST_CELLS,
        "side_first": math.isqrt(CAP_FIRST_CELLS),
        "escalation_triggered": escalated,
        "cap_cells_used": cap_used,
        "side_used": side_used,
        "n_anchor_nodes": len(anchors),
        "n_candidates_evaluated": len(results),
        "chosen": chosen,
        "runner_ups": results[1 : 1 + runner_up_count],
        "note_D_W": (
            "smoke.min_predicted_nodes_in_window gate NOT relaxed here (owner holds D-W); "
            "shortfall reported via predicted_target_met_at_cap / max_predicted_active_*"
        ),
    }
    manifest["finished_utc"] = _utc_now()
    manifest["wall_clock_sec"] = round(time.perf_counter() - t_all, 3)
    manifest["status"] = "completed"
    _write_manifest(manifest_path, manifest)

    typer.echo(
        f"D1 {d1['verdict']}: active_nodes={d1['n_active_nodes']} "
        f"cb_edges={d1['n_active_cb_edges']} "
        f"strong_path={d1['outfall_reachable_strong_len_ge1']}"
    )
    typer.echo(
        f"D2 chosen rows{best['rows']} cols{best['cols']} side={side_used} "
        f"active={best['n_active_nodes']} cb={best['n_active_cb_edges']} "
        f"pred={best['n_predicted_active']} escalated={escalated}"
    )
    typer.echo(f"manifest: {manifest_path}")


if __name__ == "__main__":
    app()
