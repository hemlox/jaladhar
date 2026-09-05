"""WF-2 M3 — G4 SUBDOMAIN TILE THROUGHPUT MEASUREMENT (measurement only).

Owner directive (G4 escalated, binding): measure SUBDOMAIN TILE THROUGHPUT on
the COUPLED production loop — same configuration, a 1/16-area tile, timed. No
linear-scaling assumption either direction; the number decides whether
"instantly" is claimable against G4's 10-minute gate and has never been
measured in this project.

What this module does, end to end:

1. TILE PICKER — grid truth from ``runs/drain_graph_build/manifest.json``
   ``config_snapshot.grid``; square sides ``round(sqrt(H*W*f))`` for f in
   {1/16, 1/9, 1/4}. A deterministic exact-max-containment scan over candidate
   top-left corners ranks boxes ``(-n_predicted_active, -n_active_cb_edges,
   row0, col0)`` requiring ``n_predicted_active >= 10`` (the D-W falsifier
   gate, NEVER lowered) AND ``n_active_cb_edges > 0``. ZERO timing information
   enters selection. The chosen box + method + tie-break are recorded into the
   top-level manifest BEFORE any timed run.
2. CONFIG INSTANTIATOR — per-tile ``coupling_tile_<tag>.yaml`` copying ALL
   leaves of the live schema verbatim from a byte-snapshot of
   ``configs/coupling.yaml``
   (precedent ``runs/wf2_coupled_resmoke/coupling_resmoke.yaml``), differing
   ONLY in ``outputs.*`` dirs and the smoke envelope: duration 1200 s (same
   storm window as resmoke, apples-to-apples), ``cpu_budget_wall_clock_min``
   8.0 (arms the §14 step-5 probe backstop), max_steps per printed arithmetic.
   The snapshot + instantiated yamls are frozen INSIDE the run dir with
   sha256s recorded before any run (protection against concurrent edits to
   ``src/jaladhar/coupling/config.py`` by the M2 agent).
3. ORCHESTRATOR — top-level rule-6 manifest written at start (status
   running, git sha/dirty, torch thread count RECORDED never changed, input
   sha256s), updated IN PLACE cumulatively per tile. Each tile prints a
   pre-run compute budget, runs the PRODUCTION path
   (``simulate_coupled(window=(slice,slice), graph=None, static=None)``,
   i.e. ``load_drain_graph(window=...) -> load_domain(window=...) -> twin ->
   coupled loop``, no injection), then merges a driver report IN PLACE into
   the tile's own simulate_coupled-written manifest. Anti-vacuity hard asserts
   before a tile counts as timing evidence: edges_routed>0, captured>0,
   h_max_final>0, predicted targets >= 10 — failure marks the tile
   INVALID-FOR-TIMING and fails the script. Refusals / compute-budget stops
   become clean PARTIAL records from the terminal manifest bytes, never
   silent retries.
4. MICROBENCH (default ON, <10 s) — couple_step on the TILE's loaded
   production graph following scripts/wf2_fullgraph_cost.py protocol
   (2 warmup + 25 timed reps, declared-synthetic uniform 0.3 m h, dt 0.48),
   recorded beside end-to-end numbers as the WARMUP-EXCLUDED pure-loop rate
   comparable to the 143.148 ms full-graph anchor; decomposes surface
   (cell-count) vs route (node-count) scaling.
5. RED DEMO (V5) — a corner 892x892 window far from all predicted targets is
   attempted through ``load_drain_graph`` expecting the refusal
   ``'[window] only N/116 falsifier target node(s) active (required >= 10)'``
   with non-zero exit — proving production tiles satisfy the real gate rather
   than sliding under it.

Anchors are CITED from disk bytes, never re-run: couple_step 143.148 ms/step
(route-only 0.743 ms) in runs/wf2_fullgraph_cost/manifest.json; resmoke
1534.26 ms/step loop-side + 1385.79 ms/step twin over 538 steps / 1200 s on
12,728,415 cells in runs/wf2_coupled_resmoke/manifest.json.

Run order: tile_1_16 (mandatory) -> tile_1_9 -> tile_1_4 CONDITIONAL (only if
elapsed <= ~10 min after the previous tile; owner cap ~25 min total; 1/16 alone
answers the mandatory question). Skips print their arithmetic.

Measurement protocol (stated plainly): headline rates are WHOLE-RUN
end-to-end timings matching the anchor basis — ``(wall_clock_s -
twin.wall_clock_s) / steps`` loop-side, which includes assembly + coupled
loop; post-loop product writes excluded (<~1%). Per-step wall history does not
exist in the current manifest schema (named limitation; adding it would touch
solver_hook.py, which M2 owns concurrently). Anything beyond what was timed
is labelled EXTRAPOLATION-FROM-MEASURED with arithmetic shown.

Rule-6 manifests throughout; CPU-only (V12); every number from bytes read
this run (V1).
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
import typer
from yaml import safe_dump as _yaml_safe_dump
from yaml import safe_load as _yaml_safe_load

from jaladhar.coupling.config import LEAF_KEYS, ConfigError, resolve_config
from jaladhar.coupling.exchange import build_node_state, couple_step
from jaladhar.coupling.ledger import CouplingMassBreach
from jaladhar.coupling.router import (
    OWNER_ADJUDICATION_REF_M5B,
    RefuseLoadError,
    load_drain_graph,
    route,
)
from jaladhar.coupling.solver_hook import (
    ComputeBudgetExceeded,
    KillThresholdHalt,
    simulate_coupled,
)
from jaladhar.drainage.graph_io import read_artefact
from jaladhar.provenance import write_json_atomic
from jaladhar.solver.run import estimate_steps_band, uniform_storm
from jaladhar.solver.state import StaticFields, load_solver_config

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[1]

STAGE = "wf2_tile_throughput"

# --- inputs (read-only; other agents' trees untouched) ----------------------
CONFIG_SRC_REL = "configs/coupling.yaml"
GRAPH_BUILD_MANIFEST_REL = "runs/drain_graph_build/manifest.json"
GPKG_REL = "runs/drain_graph_build/drain_graph.gpkg"
ADJACENCY_REL = "runs/drain_graph_build/drain_graph_adjacency.json"
PRED_SET_REL = "runs/wf2_falsifier_preregistration/surcharge_prediction_set.json"
ANCHOR_FULLGRAPH_REL = "runs/wf2_fullgraph_cost/manifest.json"
ANCHOR_RESMOKE_REL = "runs/wf2_coupled_resmoke/manifest.json"

# --- tile plan ---------------------------------------------------------------
TILE_TAGS = ("tile_1_16", "tile_1_9", "tile_1_4")
TILE_FRACTION: dict[str, float] = {"tile_1_16": 1.0 / 16.0, "tile_1_9": 1.0 / 9.0, "tile_1_4": 0.25}
MAX_STEPS_BY_TAG: dict[str, int] = {"tile_1_16": 1500, "tile_1_9": 1200, "tile_1_4": 600}

# --- smoke envelope (SAME storm window as resmoke for comparability) --------
SMOKE_DURATION_S = 1200.0
CPU_BUDGET_WALL_CLOCK_MIN = 8.0  # arms the library's §14 step-5 probe backstop

# --- conditional-chain arithmetic (owner caps) --------------------------------
COND_ELAPSED_S = 600.0  # proceed to next tile only if <= ~10 min elapsed so far
OWNER_CAP_S = 1500.0  # ~25 min total timed-chain cap

# --- microbench protocol (scripts/wf2_fullgraph_cost.py verbatim numbers) ----
MICRO_WARMUP = 2
MICRO_REPS = 25
MICRO_DT_S = 0.48
MICRO_DEPTH_M = 0.3

# D-W gate is preregistered at 10; the picker must satisfy it and NEVER lower it.
DW_GATE_EXPECTED = 10


class TileSelectionError(RuntimeError):
    """Raised when no candidate window satisfies the D-W gate for a tile."""


class ConfigDriftError(RuntimeError):
    """Raised when the frozen coupling snapshot disagrees with the live schema."""


# ---------------------------------------------------------------------------
# small shared helpers (pattern: scripts/wf2_resmoke_window.py)
# ---------------------------------------------------------------------------


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


def _repo(rel: str | Path) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else REPO / p


def _guard_top_level_manifest_collision(manifest_path: Path) -> None:
    """Refuse to overwrite ANY pre-existing top-level manifest (E1).

    A later --pick-only invocation once REPLACED the banked top-level manifest
    runs/wf2_tile_throughput/manifest.json with a whole-file atomic write of a
    stub (status "completed_pick_only"), destroying the timing evidence. The
    guard therefore fires on ANY pre-existing status — running, completed,
    completed_pick_only, or any other — before a single byte is written into
    the out-dir, naming the existing record's start time and demanding a
    different --out-dir.
    """
    if not manifest_path.exists():
        return
    existing_status = "<unreadable>"
    started_utc = "<unknown>"
    try:
        data = json.loads(manifest_path.read_text())
        existing_status = str(data.get("status", "<missing status>"))
        started_utc = str(data.get("started_utc", data.get("start_time", "<missing>")))
    except (OSError, json.JSONDecodeError):
        pass
    raise RuntimeError(
        f"REFUSING to write top-level manifest {manifest_path}: a manifest already exists "
        f"there (status={existing_status!r}, started_utc={started_utc!r}). This protects "
        "banked timing evidence from whole-file atomic clobbering. Re-run with a DIFFERENT "
        "--out-dir (e.g. --out-dir runs/wf2_tile_throughput_<suffix>)."
    )


def _floats_close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


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


# ---------------------------------------------------------------------------
# anchors — cited from disk bytes, never re-run (V3)
# ---------------------------------------------------------------------------


def _read_anchors() -> dict[str, Any]:
    fg = json.loads(_repo(ANCHOR_FULLGRAPH_REL).read_text())
    rs = json.loads(_repo(ANCHOR_RESMOKE_REL).read_text())
    if fg.get("status") != "completed" or rs.get("status") != "completed":
        raise RuntimeError("anchor manifests are not at status completed; refusing to cite them")
    couple_stats = fg["fullgraph_couple_ms_per_step"]
    twin_block = rs["kill_thresholds"]["K1"]["uncoupled_twin"]
    steps, twin_steps = int(rs["steps"]), int(twin_block["steps"])
    wall, twin_wall = float(rs["wall_clock_s"]), float(twin_block["wall_clock_s"])
    loop_side_s = wall - twin_wall
    dts = rs["dt_schedule"]
    return {
        "fullgraph_couple_ms_mean": float(couple_stats["mean"]),
        "fullgraph_route_only_ms_mean": float(couple_stats["route_only"]["mean"]),
        "fullgraph_protocol": fg["protocol"],
        "resmoke_steps": steps,
        "resmoke_sim_time_s": float(rs["sim_time_s"]),
        "resmoke_duration_s": float(rs["duration_s"]),
        "resmoke_wall_clock_s": wall,
        "resmoke_twin_wall_clock_s": twin_wall,
        "resmoke_twin_steps": twin_steps,
        "resmoke_cells": int(rs["cells"]),
        "resmoke_grid_shape": [int(v) for v in rs["grid_shape"]],
        "resmoke_dt_min_s": float(dts["dt_min_s"]),
        "resmoke_dt_max_s": float(dts["dt_max_s"]),
        # DERIVED anchor rates, same basis as this milestone reports:
        # loop-side = (wall - twin)/steps includes assembly + coupled loop;
        # post-loop product writes excluded (<~1%).
        "resmoke_loop_side_ms_per_step": round(loop_side_s / steps * 1000.0, 4),
        "resmoke_twin_ms_per_step": round(twin_wall / twin_steps * 1000.0, 4),
        "basis_note": (
            "loop-side = (wall_clock_s - twin.wall_clock_s)/steps over the whole run "
            "(assembly + coupled loop included; post-loop product writes excluded "
            "(<~1%)) — matches how this milestone reports tiles"
        ),
        "sources": {
            "fullgraph_cost": str(_repo(ANCHOR_FULLGRAPH_REL)),
            "resmoke": str(_repo(ANCHOR_RESMOKE_REL)),
        },
    }


# ---------------------------------------------------------------------------
# artefact load for picking (production reader, same mapping as resmoke_window)
# ---------------------------------------------------------------------------


def _load_picker_arrays(cfg: Any) -> dict[str, Any]:
    art = read_artefact(
        str(cfg.graph.gpkg),
        str(cfg.graph.adjacency),
        str(cfg.graph.manifest),
        owner_adjudication_ref=OWNER_ADJUDICATION_REF_M5B + ";consumer=wf2-tile-throughput",
    )
    nodes_gdf, edges_gdf = art["nodes_gdf"], art["edges_gdf"]
    grid = _producer_grid(Path(cfg.graph.manifest))
    res = grid["resolution_m"]
    x_arr = nodes_gdf["x_m"].to_numpy(dtype=np.float64)
    y_arr = nodes_gdf["y_m"].to_numpy(dtype=np.float64)
    node_row = np.floor((grid["y_top_m"] - y_arr) / res).astype(np.int64)
    node_col = np.floor((x_arr - grid["x_origin_m"]) / res).astype(np.int64)
    gh, gw = grid["height"], grid["width"]
    inside = (node_col >= 0) & (node_col < gw) & (node_row >= 0) & (node_row < gh)
    if not bool(inside.all()):
        raise RuntimeError("a node falls outside the producer-declared buffered grid")

    edge_from = edges_gdf["from_node"].to_numpy(dtype=np.int64) - 1
    edge_to = edges_gdf["to_node"].to_numpy(dtype=np.int64) - 1
    src = edges_gdf["edge_source"].astype(str).to_numpy()
    q_notna = edges_gdf["q_capacity_nom_m3s"].notna().to_numpy()
    cb_mask = (src == "observed") & q_notna  # router.py is_cb definition

    pred_ids = json.loads(Path(cfg.diagnostics.falsifier_set).read_text())["predicted_node_ids"]
    pred_idx = np.asarray([int(i) - 1 for i in pred_ids], dtype=np.int64)

    return {
        "grid": grid,
        "gh": gh,
        "gw": gw,
        "num_nodes": len(nodes_gdf),
        "num_edges_reader": len(edges_gdf),
        "node_row": node_row,
        "node_col": node_col,
        "edge_from": edge_from,
        "edge_to": edge_to,
        "cb_mask": cb_mask,
        "n_cb_total": int(cb_mask.sum()),
        "pred_idx": pred_idx,
        "pred_row": node_row[pred_idx],
        "pred_col": node_col[pred_idx],
        "n_predicted": int(pred_idx.size),
    }


# ---------------------------------------------------------------------------
# TILE PICKER — deterministic exact-max-containment scan, zero timing input
# ---------------------------------------------------------------------------

PICK_METHOD = (
    "exact max-containment scan over candidate top-left corners "
    "{unique(min(pred_row, H-side))} x {unique(min(pred_col, W-side))}: any maximal "
    "box translates so its top/left edge sits ON a contained point (clamped into the "
    "grid), so this candidate family covers the optimum of the PRIMARY key "
    "(n_predicted_active) exactly. The capacity-bearing-edge tie-break is optimized "
    "WITHIN this candidate family (deterministic, stated); global optimality is proven "
    "for the primary key only. Ranked (-n_predicted_active, -n_active_cb_edges, row0, "
    "col0); strict '<' keeps the FIRST minimum under sorted (row0, col0) iteration. "
    "ZERO timing information participates in selection."
)


def _window_counts(arr: dict[str, Any], r0: int, c0: int, side: int) -> dict[str, int]:
    pred_r, pred_c = arr["pred_row"], arr["pred_col"]
    n_pred = int(
        ((pred_r >= r0) & (pred_r < r0 + side) & (pred_c >= c0) & (pred_c < c0 + side)).sum()
    )
    act = (
        (arr["node_row"] >= r0)
        & (arr["node_row"] < r0 + side)
        & (arr["node_col"] >= c0)
        & (arr["node_col"] < c0 + side)
    )
    a_edge = act[arr["edge_from"]] & act[arr["edge_to"]]
    return {
        "n_predicted_active": n_pred,
        "n_active_nodes": int(act.sum()),
        "n_active_edges": int(a_edge.sum()),
        "n_active_cb_edges": int((a_edge & arr["cb_mask"]).sum()),
    }


def _pick_box_for_side(arr: dict[str, Any], side: int, gate_min_pred: int) -> dict[str, Any]:
    gh, gw = arr["gh"], arr["gw"]
    if side > min(gh, gw):
        raise TileSelectionError(f"side {side} exceeds grid {(gh, gw)}")
    cand_r = np.unique(np.minimum(arr["pred_row"], gh - side))
    cand_c = np.unique(np.minimum(arr["pred_col"], gw - side))
    best_overall: tuple[tuple[int, int, int, int], dict[str, Any]] | None = None
    best_passing: tuple[tuple[int, int, int, int], dict[str, Any]] | None = None
    n_eval = 0
    n_passing = 0
    for r0 in cand_r.tolist():  # ascending, deterministic
        for c0 in cand_c.tolist():  # ascending, deterministic
            n_eval += 1
            counts = _window_counts(arr, int(r0), int(c0), side)
            rank = (
                -counts["n_predicted_active"],
                -counts["n_active_cb_edges"],
                int(r0),
                int(c0),
            )
            if best_overall is None or rank < best_overall[0]:
                best_overall = (rank, {"r0": int(r0), "c0": int(c0), **counts})
            passes = (
                counts["n_predicted_active"] >= gate_min_pred and counts["n_active_cb_edges"] > 0
            )
            if passes:
                n_passing += 1
                if best_passing is None or rank < best_passing[0]:
                    best_passing = (rank, {"r0": int(r0), "c0": int(c0), **counts})
    if best_overall is None:
        raise TileSelectionError("candidate family empty — predicted set empty?")
    if best_passing is None:
        raise TileSelectionError(
            f"NO candidate box satisfies the D-W gate (n_predicted_active >= {gate_min_pred} "
            f"AND n_active_cb_edges > 0); global max predicted containment = "
            f"{best_overall[1]['n_predicted_active']} — refusing to slide under the gate"
        )
    return {
        "chosen": best_passing[1],
        "rank_key": list(best_passing[0]),
        "gate": {
            "min_predicted_nodes_required": gate_min_pred,
            "require_n_active_cb_edges_gt": 0,
            "source": "smoke.min_predicted_nodes_in_window (D-W held, never lowered)",
        },
        "n_candidates_evaluated": n_eval,
        "n_candidates_passing_gate": n_passing,
        "best_overall_any_gate": best_overall[1],
        "method": PICK_METHOD,
    }


def _square_side(total_area: float, fraction: float) -> int:
    return int(round(math.sqrt(total_area * fraction)))


def _red_demo_window(arr: dict[str, Any], side: int, gate_min_pred: int) -> dict[str, Any]:
    """EXACT global minimum-predicted-containment side x side window.

    The owner directive sketched 'a corner window far from all predicted targets',
    but grid CORNERS can still hold >= 10 targets, so the honest generalization is
    the provably-farthest window: a summed-area table over the 3521x3615 predicted-
    point indicator gives O(1) exact counts for EVERY one of the
    (H-side+1)*(W-side+1) placements; the lexicographic minimum (count, row0,
    col0) is deterministic under row-major argmin. Zero timing information.
    """
    gh, gw = arr["gh"], arr["gw"]
    ii = np.zeros((gh + 1, gw + 1), dtype=np.int32)
    np.add.at(ii, (arr["pred_row"] + 1, arr["pred_col"] + 1), 1)
    ii = ii.cumsum(axis=0).cumsum(axis=1)
    n_r, n_c = gh - side + 1, gw - side + 1
    best = None
    chunk = max(1, 4_000_000 // max(n_c, 1))
    for r0_lo in range(0, n_r, chunk):
        r0_hi = min(r0_lo + chunk, n_r)
        r1 = ii[np.arange(r0_lo, r0_hi)[:, None] + side, np.arange(n_c)[None, :] + side]
        r2 = ii[np.arange(r0_lo, r0_hi)[:, None], np.arange(n_c)[None, :] + side]
        r3 = ii[np.arange(r0_lo, r0_hi)[:, None] + side, np.arange(n_c)[None, :]]
        r4 = ii[np.arange(r0_lo, r0_hi)[:, None], np.arange(n_c)[None, :]]
        counts = (r1 - r2 - r3 + r4).astype(np.int64)
        flat_idx = int(counts.argmin())  # row-major: ties resolve to lowest (row0, col0)
        cands = (
            (int(counts.flat[flat_idx]), r0_lo + flat_idx // n_c, flat_idx % n_c),
            {"n_predicted_active": int(counts.flat[flat_idx])},
        )
        if best is None or cands[0] < best[0]:
            best = cands
    (n_pred, r0, c0), extra = best
    possible = n_pred < gate_min_pred
    return {
        "r0": int(r0),
        "c0": int(c0),
        "rows": [int(r0), int(r0) + side],
        "cols": [int(c0), int(c0) + side],
        "side": side,
        "n_predicted_active": int(n_pred),
        "red_demo_possible_at_this_side": bool(possible),
        **extra,
        "method": (
            "exact global min-containment via summed-area table over ALL "
            f"{n_r * n_c} placements; lexicographic (count, row0, col0) minimum. "
            "If even this window holds >= the gate, NO window at this side can "
            "demonstrate the D-W gate red — recorded as a finding, not worked around"
        ),
    }


def pick_all_tiles(
    arr: dict[str, Any], gate_min_pred: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pick all three tiles deterministically; returns (selection, meta)."""
    gh, gw = arr["gh"], arr["gw"]
    total_area = float(gh * gw)
    selection: dict[str, Any] = {}
    meta = {
        "grid_truth_source": str(_repo(GRAPH_BUILD_MANIFEST_REL)),
        "grid": {"height": gh, "width": gw},
        "total_cells": int(total_area),
        "n_predicted_targets": arr["n_predicted"],
        "n_capacity_bearing_edges_reader": arr["n_cb_total"],
        "picker_inputs": {
            "node_row_col_mapping": "floor((y_top - y)/res), floor((x - x_origin)/res) "
            "(identical to scripts/wf2_resmoke_window.py)",
            "reader": "jaladhar.drainage.graph_io.read_artefact (production asserting reader)",
        },
    }
    for tag in TILE_TAGS:
        frac_declared = TILE_FRACTION[tag]
        side = _square_side(total_area, frac_declared)
        picked = _pick_box_for_side(arr, side, gate_min_pred)
        ch = picked["chosen"]
        cells = side * side
        frac_realized = cells / total_area
        selection[tag] = {
            "tag": tag,
            "declared_area_fraction": frac_declared,
            "side": side,
            "dims": [side, side],
            "cells": cells,
            "realized_area_fraction": round(frac_realized, 8),
            "deviation_pct_vs_declared": round(
                100.0 * (frac_realized - frac_declared) / frac_declared, 6
            ),
            "rows": [ch["r0"], ch["r0"] + side],
            "cols": [ch["c0"], ch["c0"] + side],
            "counts": {
                k: ch[k]
                for k in (
                    "n_predicted_active",
                    "n_active_nodes",
                    "n_active_edges",
                    "n_active_cb_edges",
                )
            },
            "rank_key": picked["rank_key"],
            "gate": picked["gate"],
            "n_candidates_evaluated": picked["n_candidates_evaluated"],
            "n_candidates_passing_gate": picked["n_candidates_passing_gate"],
            "max_predicted_active_any_candidate": picked["best_overall_any_gate"][
                "n_predicted_active"
            ],
            "method": picked["method"],
            "square_not_rectangle_note": (
                "owner's example sketched a rectangle; SQUARE sides round(sqrt(H*W*f)) are "
                "used per binding directive — realized area fractions match the declared "
                "fractions to well within 0.05% (see deviation_pct_vs_declared)"
            ),
        }
        selection[tag]["gate_red_demo_window"] = _red_demo_window(arr, side, gate_min_pred)
    return selection, meta


# ---------------------------------------------------------------------------
# INDEPENDENT recount (V2) — observable NOT produced by the picker code path
# ---------------------------------------------------------------------------


def _independent_recount(tag: str, sel: dict[str, Any], grid: dict[str, Any]) -> dict[str, Any]:
    """Recount predicted-active and cb-edge counts for the CHOSEN box through a
    separate code path: adjacency-JSON coordinates (not the gdf) + RAW gpkg
    edges layer via geopandas (not read_artefact)."""
    import geopandas as gpd  # local import: recount-only dependency path

    r0, r1 = sel["rows"]
    c0, c1 = sel["cols"]
    adj = json.loads(_repo(ADJACENCY_REL).read_text())
    nodes_adj = adj["nodes"]
    ids = np.array(sorted(int(k) for k in nodes_adj), dtype=np.int64)
    xs = np.array([float(nodes_adj[str(i)]["x_m"]) for i in ids], dtype=np.float64)
    ys = np.array([float(nodes_adj[str(i)]["y_m"]) for i in ids], dtype=np.float64)
    res, x_org, y_top = grid["resolution_m"], grid["x_origin_m"], grid["y_top_m"]
    rows = np.floor((y_top - ys) / res).astype(np.int64)
    cols = np.floor((xs - x_org) / res).astype(np.int64)
    act = (rows >= r0) & (rows < r1) & (cols >= c0) & (cols < c1)

    pred_ids = json.loads(_repo(PRED_SET_REL).read_text())["predicted_node_ids"]
    npred_re = int(sum(bool(act[i - 1]) for i in (int(p) for p in pred_ids)))

    edges_raw = gpd.read_file(_repo(GPKG_REL), layer="drain_edges")
    src = edges_raw["edge_source"].astype(str).to_numpy()
    q = edges_raw["q_capacity_nom_m3s"]
    cb_raw = (src == "observed") & q.notna().to_numpy()
    ef = edges_raw["from_node"].to_numpy(dtype=np.int64) - 1
    et = edges_raw["to_node"].to_numpy(dtype=np.int64) - 1
    both = act[ef] & act[et]
    ncb_re = int((both & cb_raw).sum())

    picker_npred = sel["counts"]["n_predicted_active"]
    picker_ncb = sel["counts"]["n_active_cb_edges"]
    raw_minus_reader = int(len(edges_raw)) - int(sel["n_reader_edges_total"])
    npred_match = npred_re == picker_npred
    # The reader drops zero-length/self-loop edges; the raw layer can only hold
    # MORE cb edges inside the window than the reader-derived count.
    ncb_consistent = (ncb_re >= picker_ncb) and (ncb_re - picker_ncb <= max(raw_minus_reader, 0))
    return {
        "tag": tag,
        "code_path": (
            "adjacency-JSON node coordinates + RAW geopandas gpkg edges layer "
            "(read_artefact NOT invoked; shares no code with the picker)"
        ),
        "npred_recount": npred_re,
        "npred_picker": picker_npred,
        "npred_exact_match": npred_match,
        "ncb_recount_raw_layer": ncb_re,
        "ncb_picker_reader": picker_ncb,
        "raw_edges_total": int(len(edges_raw)),
        "reader_edges_total": int(sel["n_reader_edges_total"]),
        "raw_minus_reader_bound": raw_minus_reader,
        "ncb_consistent_within_drop_bound": ncb_consistent,
        "verdict": (
            "MATCH" if (npred_match and ncb_consistent) else "MISMATCH — investigate before timing"
        ),
    }


# ---------------------------------------------------------------------------
# CONFIG INSTANTIATOR
# ---------------------------------------------------------------------------


def _leaf_paths(mapping: dict[str, Any], prefix: str = "") -> set[str]:
    leaves: set[str] = set()
    for key, val in mapping.items():
        dotted = f"{prefix}{key}"
        if isinstance(val, dict):
            leaves |= _leaf_paths(val, f"{dotted}.")
        else:
            leaves.add(dotted)
    return leaves


def _instantiate_tile_yaml_text(
    snapshot_bytes: bytes, tag: str, max_steps: int, out_dir: Path
) -> tuple[str, dict[str, Any]]:
    raw = _yaml_safe_load(snapshot_bytes.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ConfigDriftError("coupling snapshot is not a mapping")
    tile_dir = out_dir / tag
    try:
        tile_dir_rel = str(tile_dir.relative_to(REPO))
    except ValueError:
        tile_dir_rel = str(tile_dir.resolve())
    out = raw.setdefault("outputs", {})
    out["run_dir"] = tile_dir_rel
    out["manifest"] = f"{tile_dir_rel}/manifest.json"
    out["surcharge_events_csv"] = f"{tile_dir_rel}/products/surcharge_events.csv"
    out["event_continuity_csv"] = f"{tile_dir_rel}/products/event_continuity.csv"
    out["depth_series_dir"] = f"{tile_dir_rel}/depth"
    sm = raw.setdefault("smoke", {})
    sm["duration_s"] = SMOKE_DURATION_S
    sm["max_steps"] = int(max_steps)
    sm["cpu_budget_wall_clock_min"] = CPU_BUDGET_WALL_CLOCK_MIN

    mine = _leaf_paths(raw)
    schema = set(LEAF_KEYS)
    drift = {
        "in_schema_missing_from_yaml": sorted(schema - mine),
        "in_yaml_unknown_to_schema": sorted(mine - schema),
        "schema_leaf_count_live": len(schema),
        "yaml_leaf_count": len(mine),
    }
    if drift["in_schema_missing_from_yaml"] or drift["in_yaml_unknown_to_schema"]:
        raise ConfigDriftError(
            "concurrent-edit protection fired: frozen coupling snapshot leaf set != live "
            f"resolver schema (M2 may be editing src/jaladhar/coupling/config.py or "
            f"configs/coupling.yaml concurrently): {drift}"
        )

    header = (
        f"# WF-2 M3 instantiated per-tile config — {tag} (GENERATED, do not hand-edit).\n"
        f"#\n"
        f"# ALL leaves copied VERBATIM from the byte-snapshot of {CONFIG_SRC_REL}\n"
        f"# taken at orchestration start ({len(mine)} leaves, resolver-schema-verified);\n"
        f"# ONLY outputs.* dirs and the smoke envelope differ:\n"
        f"#   smoke.duration_s={SMOKE_DURATION_S} (SAME storm window as resmoke),\n"
        f"#   smoke.max_steps={max_steps},\n"
        f"#   smoke.cpu_budget_wall_clock_min={CPU_BUDGET_WALL_CLOCK_MIN} (§14 probe backstop).\n"
        f"# smoke.min_predicted_nodes_in_window stays EXACTLY as upstream (=10) — NEVER lowered.\n"
        f"# This file is an INSTANTIATED run input living under runs/ (gitignored).\n"
    )
    return header + _yaml_safe_dump(raw, sort_keys=False), drift


# ---------------------------------------------------------------------------
# microbench (wf2_fullgraph_cost protocol on the TILE's loaded graph)
# ---------------------------------------------------------------------------


def _time_calls(fn: Any, warmup: int, reps: int) -> list[float]:
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    return times


def _stats(times_ms: list[float]) -> dict[str, float]:
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
        "reps": float(n),
    }


def _zero_static(shape: tuple[int, int]) -> StaticFields:
    """Shape-honest zeroed shell (declared-synthetic; couple_step reads only
    drain_cap_m_s, asserted zero). Same construction as wf2_fullgraph_cost."""
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


def _couple_microbench(graph: Any, side: int) -> dict[str, Any]:
    """couple_step + route-only on the TILE's production-loaded graph."""
    shape = (side, side)
    static = _zero_static(shape)
    assert bool((static.drain_cap_m_s == 0).all()), "drain_cap shell must be zero"
    h = torch.full(shape, MICRO_DEPTH_M, dtype=torch.float32)
    qx = torch.zeros(side, side - 1, dtype=torch.float32)
    qy = torch.zeros(side - 1, side, dtype=torch.float32)
    node_state = build_node_state(graph)
    with torch.no_grad():
        cr = couple_step(h, qx, qy, static, node_state, graph, MICRO_DT_S)
        total = _time_calls(
            lambda: couple_step(h, qx, qy, static, node_state, graph, MICRO_DT_S),
            MICRO_WARMUP,
            MICRO_REPS,
        )
        vol_route = MICRO_DEPTH_M * graph.node_plan_area_m2.clone()
        rt = _time_calls(lambda: route(vol_route, graph, MICRO_DT_S), MICRO_WARMUP, MICRO_REPS)
        _, _, route_diag = route(vol_route, graph, MICRO_DT_S)
    total_stats, route_stats = _stats(total), _stats(rt)
    return {
        "protocol": {
            "assembly": "the tile's OWN production graph from simulate_coupled "
            "(load_drain_graph(window=...)) — no re-load",
            "h_field": f"torch.full(({side},{side}), {MICRO_DEPTH_M}, float32) "
            "DECLARED-SYNTHETIC (timing forcing only, no physical claim)",
            "dt_s": MICRO_DT_S,
            "warmup_calls": MICRO_WARMUP,
            "timed_calls": MICRO_REPS,
            "device": "cpu",
            "autograd": "forward-only under torch.no_grad()",
            "percentile_method": "nearest-rank on sorted per-call times",
        },
        "diagnostic_call": {
            "capture_m3": cr.capture_m3,
            "return_m3": cr.return_m3,
            "surcharging_nodes": cr.surcharging_nodes,
            "edges_allocated_cells": int((graph.node_id_map >= 0).sum().item()),
        },
        "couple_ms_per_step": total_stats,
        "route_only_ms_per_step": route_stats,
        "derived_capture_return_surface_mean_ms": total_stats["mean"] - route_stats["mean"],
        "route_diag_last_call": {
            "volume_transferred_m3": route_diag["volume_transferred_m3"],
            "capacity_bound_edges": route_diag["capacity_bound_edges"],
            "edges_routed": route_diag["edges_routed"],
        },
        "label": "WARMUP-EXCLUDED pure-loop rate; NOT the headline end-to-end number",
    }


# ---------------------------------------------------------------------------
# per-tile driver report merge + anti-vacuity
# ---------------------------------------------------------------------------


def _merge_driver_report(manifest_path: Path, report: dict[str, Any], allow_remerge: bool) -> None:
    """Merge IN PLACE into the tile's simulate_coupled-written manifest (rule 6).

    Run-identity guard (E2): the driver report key is never replaced wholesale
    inside a tile manifest whose start_time / git_sha belong to a DIFFERENT run
    — refuse unless --allow-remerge is passed.
    """
    cur = json.loads(manifest_path.read_text())
    differing = [
        name
        for name, ex, inc in (
            ("start_time", cur.get("start_time"), report.get("start_time")),
            ("git_sha", cur.get("git_sha"), report.get("git_sha")),
        )
        if ex != inc
    ]
    if differing and not allow_remerge:
        raise RuntimeError(
            f"REFUSING to merge driver report into {manifest_path}: run-identity mismatch "
            f"on {differing} (existing start_time={cur.get('start_time')!r} "
            f"git_sha={cur.get('git_sha')!r} vs incoming start_time={report.get('start_time')!r} "
            f"git_sha={report.get('git_sha')!r}). Pass --allow-remerge to override."
        )
    cur["wf2_tile_driver_report"] = report
    write_json_atomic(manifest_path, cur)


ANTI_VACUITY_RULES = (
    ("plan_edges_routed_gt_0", lambda r: r["plan_edges_routed"] > 0),
    ("captured_to_drains_m3_gt_0", lambda r: r["captured_to_drains_m3"] > 0),
    ("h_max_final_gt_0", lambda r: r["h_max_final_m"] > 0),
    (
        "n_active_predicted_targets_ge_10_DW_held",
        lambda r: r["n_active_predicted_targets"] >= DW_GATE_EXPECTED,
    ),
)


def _anti_vacuity(report: dict[str, Any]) -> list[str]:
    return [name for name, fn in ANTI_VACUITY_RULES if not fn(report)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@app.command()
def main(
    out_dir: Path = typer.Option(Path(f"runs/{STAGE}"), "--out-dir"),
    config: Path = typer.Option(Path(CONFIG_SRC_REL), "--config", help="live coupling yaml source"),
    pick_only: Path | None = typer.Option(
        None, "--pick-only", help="write selection.json into this dir and exit (no timing)"
    ),
    with_microbench: bool = typer.Option(
        True, "--with-couple-microbench/--no-with-couple-microbench"
    ),
    tiles: str = typer.Option(
        "auto", "--tiles", help="'auto' conditional chain or comma subset e.g. tile_1_16"
    ),
    gate_red_demo: str | None = typer.Option(
        None,
        "--gate-red-demo",
        hidden=True,
        help=(
            "internal: 'r0,c0,side' — attempt the refusing windowed load; "
            "exit 3 refused / 4 not fired"
        ),
    ),
    allow_remerge: bool = typer.Option(
        False, "--allow-remerge", help="merge a driver report into a tile manifest from another run"
    ),
) -> None:
    """Measure subdomain tile throughput on the coupled production loop (M3)."""
    od = out_dir if out_dir.is_absolute() else REPO / out_dir

    # ---- internal red-demo mode (V5) ----------------------------------------
    if gate_red_demo is not None:
        r0_s, c0_s, side_s = gate_red_demo.split(",")
        r0, c0, side = int(r0_s), int(c0_s), int(side_s)
        cfg = resolve_config(config if config.is_absolute() else REPO / config, REPO)
        window = (slice(r0, r0 + side), slice(c0, c0 + side))
        try:
            load_drain_graph(cfg, REPO, window=window)
        except RefuseLoadError as exc:
            msg = str(exc)
            typer.echo(f"RED-DEMO REFUSED AS EXPECTED: {msg}")
            ok = msg.startswith("[window] only") and "falsifier target node(s) active" in msg
            raise typer.Exit(code=3 if ok else 4) from exc
        typer.echo(
            "RED-DEMO FAILED: the D-W gate did NOT refuse a corner window far from all "
            "predicted targets — the production gate is weaker than preregistered"
        )
        raise typer.Exit(code=4)

    # ---- E1: never clobber a pre-existing top-level manifest ---------------
    _guard_top_level_manifest_collision(od / "manifest.json")

    od.mkdir(parents=True, exist_ok=True)
    t_all = time.perf_counter()

    # ---- concurrent-edit protection: freeze the coupling config FIRST -------
    snap_src = _repo(config)
    snap_bytes = snap_src.read_bytes()
    snap_path = od / "config_snapshot_configs_coupling.yaml"
    snap_path.write_bytes(snap_bytes)  # byte-verbatim snapshot
    snap_sha = hashlib.sha256(snap_bytes).hexdigest()
    live_sha = _sha256_file(snap_src)

    # resolve ONCE from the SNAPSHOT so picker semantics are immune to M2 edits;
    # drift between snapshot leaves and the LIVE resolver schema fails LOUDLY here.
    try:
        cfg0 = resolve_config(snap_path, REPO)
    except ConfigError as exc:
        typer.echo(f"CONFIG SNAPSHOT UNRESOLVABLE (concurrent-edit collision?): {exc}")
        raise typer.Exit(code=1) from exc
    gate_min_pred = int(cfg0.smoke.min_predicted_nodes_in_window)
    if gate_min_pred != DW_GATE_EXPECTED:
        typer.echo(
            f"D-W GATE DRIFT: smoke.min_predicted_nodes_in_window={gate_min_pred} != "
            f"{DW_GATE_EXPECTED} — refusing to run against a moved gate"
        )
        raise typer.Exit(code=1)

    sha, dirty, n_porcelain = _git_state()
    threads = torch.get_num_threads()  # RECORDED, never changed

    input_paths = {
        "gpkg": Path(cfg0.graph.gpkg),
        "adjacency": Path(cfg0.graph.adjacency),
        "graph_manifest": Path(cfg0.graph.manifest),
        "prediction_set": Path(cfg0.diagnostics.falsifier_set),
        "graph_build_manifest": _repo(GRAPH_BUILD_MANIFEST_REL),
        "anchor_fullgraph_cost": _repo(ANCHOR_FULLGRAPH_REL),
        "anchor_resmoke": _repo(ANCHOR_RESMOKE_REL),
        "config_source": snap_src,
        "config_snapshot_frozen": snap_path,
    }
    manifest: dict[str, Any] = {
        "stage": STAGE,
        "milestone": "WF-2 Round 3 / M3 — G4 subdomain tile throughput",
        "mode": "measurement_only",
        "status": "running",
        "started_utc": _utc_now(),
        "cpu_only": True,
        "device": "cpu",
        "torch_num_threads_recorded_NEVER_changed": threads,
        "git_sha": sha,
        "git_dirty": dirty,
        "git_porcelain_entry_count": n_porcelain,
        "ownership_note": (
            "owns scripts/wf2_tile_throughput.py + runs/wf2_tile_throughput/**; M2 owns "
            "src/jaladhar/coupling/{diagnostics,config,solver_hook}.py + wf2_gt_* — disjoint"
        ),
        "concurrent_edit_protection": {
            "snapshot_of": str(snap_src),
            "snapshot_path": str(snap_path),
            "snapshot_sha256": snap_sha,
            "live_source_sha256_at_start": live_sha,
            "snapshot_byte_identical_to_live_at_start": snap_sha == live_sha,
            "policy": (
                "per-tile yamls instantiate from the FROZEN snapshot; if a coupled run ever "
                "fails config resolution mid-batch it is re-instantiated from this snapshot "
                "once and the collision is REPORTED, never silently retried"
            ),
        },
        "dw_gate": {
            "min_predicted_nodes_in_window_realized": gate_min_pred,
            "expected": DW_GATE_EXPECTED,
            "held": gate_min_pred == DW_GATE_EXPECTED,
            "note": "NEVER lowered anywhere in this milestone",
        },
        "inputs_sha256": {k: _sha256_file(p) for k, p in input_paths.items()},
        "anchors": _read_anchors(),
        "tiles": {},
        "chain": {"conditional_elapsed_cap_s": COND_ELAPSED_S, "owner_total_cap_s": OWNER_CAP_S},
        "measurement_protocol": {
            "headline": (
                "WHOLE-RUN end-to-end timing on the anchor basis: loop-side ms/step = "
                "(wall_clock_s - twin.wall_clock_s)/steps — assembly + coupled loop "
                "included; post-loop product writes excluded (<~1%); twin ms/step = "
                "twin.wall_clock_s/twin.steps; NO warmup exclusion "
                "on headline numbers"
            ),
            "microbench_role": (
                "couple_step-only rate (2 warmup + 25 timed reps, declared-synthetic uniform "
                "0.3 m h, dt 0.48) recorded BESIDE the headline as the warmup-excluded "
                "pure-loop rate comparable to the 143.148 ms full-graph anchor"
            ),
            "named_limitation": (
                "per-step wall history does not exist in the current manifest schema; adding "
                "it would touch solver_hook.py which M2 owns concurrently — so phase splits "
                "inside a step are out of scope for this milestone"
            ),
            "extrapolation_policy": (
                "anything beyond what was timed is labelled EXTRAPOLATION-FROM-MEASURED "
                "with arithmetic shown; the full-domain number is never claimed"
            ),
        },
        "_t0": time.perf_counter(),
    }
    manifest_path = od / "manifest.json"
    write_json_atomic(manifest_path, manifest)

    # ---- picker (zero timing information) -----------------------------------
    arr = _load_picker_arrays(cfg0)
    selection, pick_meta = pick_all_tiles(arr, gate_min_pred)
    for tag in TILE_TAGS:
        selection[tag]["n_reader_edges_total"] = arr["num_edges_reader"]

    def _deterministic_selection_blob() -> dict[str, Any]:
        return {
            "stage": STAGE,
            "grid_truth": pick_meta["grid"],
            "n_predicted_targets": pick_meta["n_predicted_targets"],
            "n_capacity_bearing_edges_reader": pick_meta["n_capacity_bearing_edges_reader"],
            "tiles": selection,
            "method_global": PICK_METHOD,
        }

    sel_json_path = od / "selection.json"
    sel_json_path.write_text(json.dumps(_deterministic_selection_blob(), indent=2) + "\n")

    if pick_only is not None:
        pod = pick_only if pick_only.is_absolute() else REPO / pick_only
        pod.mkdir(parents=True, exist_ok=True)
        (pod / "selection.json").write_text(
            json.dumps(_deterministic_selection_blob(), indent=2) + "\n"
        )
        manifest["pick_only_dir"] = str(pod)
        manifest["status"] = "completed_pick_only"
        manifest["finished_utc"] = _utc_now()
        manifest["wall_clock_sec"] = round(time.perf_counter() - t_all, 3)
        write_json_atomic(manifest_path, manifest)
        typer.echo(f"pick-only selection written to {pod/'selection.json'} (byte-deterministic)")
        raise typer.Exit()

    # selection recorded into the TOP-LEVEL manifest BEFORE any timed run (binding)
    manifest["tile_selection"] = _deterministic_selection_blob()
    manifest["tile_selection_recorded_before_timed_runs"] = True
    write_json_atomic(manifest_path, manifest)
    for tag in TILE_TAGS:
        s = selection[tag]
        typer.echo(
            f"PICK {tag}: side={s['side']} rows={s['rows']} cols={s['cols']} "
            f"pred={s['counts']['n_predicted_active']}/{pick_meta['n_predicted_targets']} "
            f"cb_edges={s['counts']['n_active_cb_edges']} "
            f"(area dev {s['deviation_pct_vs_declared']:+.4f}% vs declared)"
        )

    # ---- INDEPENDENT recount (V2) -------------------------------------------
    recounts = [_independent_recount(tag, selection[tag], arr["grid"]) for tag in TILE_TAGS]
    manifest["independent_recount_V2"] = recounts
    write_json_atomic(manifest_path, manifest)
    for rc in recounts:
        typer.echo(f"RECOUNT {rc['tag']}: {rc['verdict']} (npred {rc['npred_recount']} exact)")

    # ---- instantiate the three frozen per-tile yamls NOW --------------------
    tile_cfg_meta: dict[str, Any] = {}
    for tag in TILE_TAGS:
        text, drift = _instantiate_tile_yaml_text(snap_bytes, tag, MAX_STEPS_BY_TAG[tag], od)
        tdir = od / tag
        tdir.mkdir(parents=True, exist_ok=True)
        ypath = tdir / f"coupling_tile_{tag}.yaml"
        ypath.write_text(text)
        tile_cfg_meta[tag] = {
            "path": str(ypath),
            "sha256": _sha256_file(ypath),
            "leaf_count": drift["yaml_leaf_count"],
            "schema_leaf_count_live": drift["schema_leaf_count_live"],
            "max_steps": MAX_STEPS_BY_TAG[tag],
            "duration_s": SMOKE_DURATION_S,
            "cpu_budget_wall_clock_min": CPU_BUDGET_WALL_CLOCK_MIN,
        }
    manifest["instantiated_tile_configs_frozen"] = tile_cfg_meta
    write_json_atomic(manifest_path, manifest)

    # ---- GATE RED DEMO (V5) — subprocess for a REAL non-zero exit ------------
    corner = selection["tile_1_16"]["gate_red_demo_window"]
    if not corner.get("red_demo_possible_at_this_side", False):
        manifest["gate_red_demo_V5"] = {
            "refused_as_expected": False,
            "finding": (
                f"NO {corner['side']}x{corner['side']} window anywhere on the grid holds "
                f"< {gate_min_pred} predicted targets (global minimum "
                f"{corner['n_predicted_active']}) — a red demo is impossible at this side; "
                "the gate cannot be shown to bite anywhere at tile scale"
            ),
            "window": corner,
        }
        write_json_atomic(manifest_path, manifest)
        typer.echo("RED DEMO: IMPOSSIBLE AT THIS SIDE (see manifest finding)")
        raise typer.Exit(code=1)
    demo_cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--out-dir",
        str(od),
        "--config",
        str(snap_path),
        "--gate-red-demo",
        f"{corner['r0']},{corner['c0']},{corner['side']}",
    ]
    demo = subprocess.run(demo_cmd, capture_output=True, text=True, cwd=REPO, timeout=600)
    combined_out = (demo.stdout + demo.stderr)[-4000:]
    refused_as_expected = demo.returncode == 3 and "RED-DEMO REFUSED AS EXPECTED:" in combined_out
    verbatim = ""
    for line in combined_out.splitlines():
        if line.startswith("RED-DEMO"):
            verbatim = line
            break
    manifest["gate_red_demo_V5"] = {
        "command": demo_cmd,
        "returncode": demo.returncode,
        "expected_returncode": 3,
        "corner_window": corner,
        "refusal_verbatim": verbatim,
        "refused_as_expected": refused_as_expected,
    }
    write_json_atomic(manifest_path, manifest)
    typer.echo(f"RED DEMO: rc={demo.returncode} :: {verbatim[:160]}")

    # ---- solver config (host chain, loaded once; storm identical to resmoke) --
    solver_cfg = load_solver_config(cfg0.solver_config, REPO)
    with open(REPO / solver_cfg["compute_config"]) as f:
        solver_cfg["_compute"] = _yaml_safe_load(f)
    storm = solver_cfg["storm"]
    rain = uniform_storm(float(storm["total_mm"]), float(storm["duration_s"]))
    manifest["forcing"] = {
        "kind": storm["kind"],
        "total_mm": float(storm["total_mm"]),
        "storm_duration_s": float(storm["duration_s"]),
        "simulated_window_s": SMOKE_DURATION_S,
        "note": "first 1200 s of the 110 mm / 7200 s uniform DESIGN storm — DECLARED SYNTHETIC",
    }
    write_json_atomic(manifest_path, manifest)

    # ---- chain control -------------------------------------------------------
    tiles_arg = [t.strip() for t in tiles.split(",") if t.strip()]
    run_tags = list(TILE_TAGS) if tiles_arg == ["auto"] else tiles_arg
    t_chain0: float | None = None
    fatal_invalid = False
    realized_steps_hint: int | None = None
    realized_loop_ms_hint: float | None = None
    last_completed_tag: str | None = None

    def _budget_block(tag: str) -> dict[str, Any]:
        s = selection[tag]
        cells, anc = s["cells"], manifest["anchors"]
        frac = cells / anc["resmoke_cells"]
        loop_lo = anc["resmoke_loop_side_ms_per_step"] * frac  # optimistic: linear in cells
        loop_hi = anc["resmoke_loop_side_ms_per_step"]  # pessimistic: no scaling at all
        twin_lo = anc["resmoke_twin_ms_per_step"] * frac
        twin_hi = anc["resmoke_twin_ms_per_step"]
        max_steps = MAX_STEPS_BY_TAG[tag]
        steps_lo = math.ceil(SMOKE_DURATION_S / float(solver_cfg["timestep"]["max_dt_s"]))
        try:
            band = tuple(solver_cfg.get("compute_estimate", {}).get("h_max_band_m", (1.0, 30.0)))
            analytic_lo, analytic_hi = estimate_steps_band(solver_cfg, SMOKE_DURATION_S, band)
        except KeyError:
            analytic_lo = analytic_hi = -1
        pair_lo_s = (loop_lo + twin_lo) / 1000.0
        pair_hi_s = (loop_hi + twin_hi) / 1000.0
        wall_lo_min = pair_lo_s * steps_lo / 60.0
        wall_hi_min = pair_hi_s * max_steps / 60.0
        return {
            "cells": cells,
            "ideal_linear_fraction_of_anchor_cells": round(frac, 8),
            "projected_loop_ms_per_step_band": [round(loop_lo, 2), round(loop_hi, 2)],
            "projected_twin_ms_per_step_band": [round(twin_lo, 2), round(twin_hi, 2)],
            "steps_band": [steps_lo, max_steps],
            "analytic_fail_safe_steps_band": [analytic_lo, analytic_hi],
            "projected_wall_pair_band_min": [round(wall_lo_min, 2), round(wall_hi_min, 2)],
            "band_definition": "lo = linear-in-cells scaling of the anchors x min steps; "
            "hi = UNSCALED anchor rates x max_steps (deliberate over-bound)",
            "section14_backstop": (
                f"cpu_budget_wall_clock_min={CPU_BUDGET_WALL_CLOCK_MIN} arms the library's "
                "step-5 measured probe: a tile truly running at unscaled cost REFUSES cleanly "
                "instead of burning the cap"
            ),
        }

    def _record(tag: str, record: dict[str, Any]) -> None:
        manifest["tiles"][tag] = record
        write_json_atomic(manifest_path, manifest)

    def _run_one(tag: str) -> dict[str, Any]:
        nonlocal fatal_invalid, realized_steps_hint, realized_loop_ms_hint, t_chain0
        nonlocal last_completed_tag
        s = selection[tag]
        side = s["side"]
        budget = _budget_block(tag)
        typer.echo(f"\n=== {tag}: PRE-RUN BUDGET ===")
        for k, v in budget.items():
            typer.echo(f"  {k}: {v}")
        record: dict[str, Any] = {
            "tag": tag,
            "selection": {k: s[k] for k in ("rows", "cols", "dims", "cells")},
            "pre_run_budget": budget,
            "started_utc": _utc_now(),
        }
        if t_chain0 is None:
            t_chain0 = time.perf_counter()
        t_tile0 = time.perf_counter()

        ypath = Path(tile_cfg_meta[tag]["path"])
        try:
            cfg = resolve_config(ypath, REPO)
        except ConfigError as exc:
            # concurrent-edit collision path: re-instantiate from the FROZEN snapshot
            # once and retry; a second failure is REPORTED, never silently retried.
            text, _ = _instantiate_tile_yaml_text(snap_bytes, tag, MAX_STEPS_BY_TAG[tag], od)
            ypath.write_text(text)
            try:
                cfg = resolve_config(ypath, REPO)
            except ConfigError as exc2:
                record.update(
                    {
                        "status": "SKIPPED_CONFIG_COLLISION",
                        "config_error_first_verbatim": str(exc)[:2000],
                        "config_error_after_reinstantiate_verbatim": str(exc2)[:2000],
                        "wall_clock_s": time.perf_counter() - t_tile0,
                    }
                )
                typer.echo(f"=== {tag}: SKIPPED_CONFIG_COLLISION (see manifest) ===")
                return record

        window = (slice(s["rows"][0], s["rows"][1]), slice(s["cols"][0], s["cols"][1]))
        try:
            res = simulate_coupled(
                cfg,
                solver_cfg,
                REPO,
                rain=rain,
                duration_s=SMOKE_DURATION_S,
                max_steps=MAX_STEPS_BY_TAG[tag],
                window=window,
                smoke=True,
            )
        except (RefuseLoadError, KillThresholdHalt, ComputeBudgetExceeded) as exc:
            term = _terminal_summary(Path(cfg.outputs.manifest))
            record.update(
                {
                    "status": f"PARTIAL_{type(exc).__name__}",
                    "exception_type": type(exc).__name__,
                    "exception_verbatim": str(exc)[:2000],
                    "terminal_manifest_status": term.get("status"),
                    "terminal_manifest_excerpt": term,
                    "clean_partial_note": "terminal manifest bytes are the record; NO silent retry",
                    "wall_clock_s": time.perf_counter() - t_tile0,
                }
            )
            typer.echo(f"=== {tag}: PARTIAL ({type(exc).__name__}) — see manifest ===")
            return record
        except CouplingMassBreach as exc:
            term = _terminal_summary(Path(cfg.outputs.manifest))
            record.update(
                {
                    "status": "PARTIAL_CouplingMassBreach",
                    "exception_verbatim": str(exc)[:2000],
                    "terminal_manifest_excerpt": term,
                    "wall_clock_s": time.perf_counter() - t_tile0,
                }
            )
            typer.echo(f"=== {tag}: PARTIAL (mass breach) — see manifest ===")
            return record
        except Exception as exc:  # noqa: BLE001 — one bad tile must not kill the batch
            term = _terminal_summary(Path(cfg.outputs.manifest))
            record.update(
                {
                    "status": "PARTIAL_UNEXPECTED_EXCEPTION",
                    "exception_type": type(exc).__name__,
                    "exception_verbatim": str(exc)[:2000],
                    "terminal_manifest_excerpt": term,
                    "wall_clock_s": time.perf_counter() - t_tile0,
                }
            )
            typer.echo(f"=== {tag}: PARTIAL (unexpected {type(exc).__name__}) ===")
            return record

        # ---- success: split walls, realized rates, anti-vacuity, microbench ---
        twin_wall = float(res.twin_summary["wall_clock_s"])
        twin_steps = int(res.twin_summary["steps"])
        loop_side = float(res.wall_clock_s) - twin_wall
        dt_sched = res.controller.schedule
        report = {
            "tag": tag,
            "window_rows": list(s["rows"]),
            "window_cols": list(s["cols"]),
            "grid_shape": [side, side],
            "cells": s["cells"],
            "device": "cpu",
            "torch_num_threads": threads,
            "requested_duration_s": SMOKE_DURATION_S,
            "steps": int(res.steps),
            "sim_time_s": float(res.sim_time_s),
            "wall_clock_s": float(res.wall_clock_s),
            "twin_wall_clock_s": twin_wall,
            "twin_steps": twin_steps,
            "loop_side_wall_clock_s": loop_side,
            "twin_ms_per_step": round(twin_wall / twin_steps * 1000.0, 4),
            "loop_side_ms_per_step": round(loop_side / int(res.steps) * 1000.0, 4),
            "dt_min_s": float(min(dt_sched)) if dt_sched else None,
            "dt_max_s": float(max(dt_sched)) if dt_sched else None,
            "n_active_predicted_targets": int(res.graph.n_active_predicted_targets),
            "plan_edges_routed": int(res.graph.plan.edges_routed),
            "n_active_nodes": int(res.graph.n_active_nodes),
            "n_active_edges": int(res.graph.n_active_edges),
            "captured_to_drains_m3": float(res.ledger.captured_to_drains_m3),
            "surcharge_returned_m3": float(res.ledger.surcharge_returned_m3),
            "h_max_final_m": float(res.h.max().item()),
            "rejected_steps": int(res.rejected_steps),
            "retry_attempts": int(res.retry_attempts),
            "max_courant_realized": float(res.max_courant),
            "k1_fired": bool(res.final_manifest["kill_thresholds"]["K1"]["fired"]),
            "g2_verdict": res.final_manifest["g2"]["verdict"],
            "rate_basis": manifest["measurement_protocol"]["headline"],
            "selection_crossref": "top-level manifest tile_selection (coords + counts)",
            "start_time": str(res.start_manifest.get("start_time")),
            "git_sha": str(res.start_manifest.get("git_sha")),
        }
        _merge_driver_report(Path(cfg.outputs.manifest), report, allow_remerge)

        violations = _anti_vacuity(report)
        record["driver_report"] = report
        record["anti_vacuity_violations"] = violations
        record["wall_clock_chain_s"] = time.perf_counter() - t_tile0
        if violations:
            record["status"] = "INVALID_FOR_TIMING"
            fatal_invalid = True
            typer.echo(f"=== {tag}: INVALID-FOR-TIMING {violations} ===")
            return record

        record["status"] = "COMPLETED"
        if with_microbench:
            mb = _couple_microbench(res.graph, side)
            record["microbench"] = mb
            typer.echo(
                f"MICRO {tag}: couple {mb['couple_ms_per_step']['mean']:.3f} ms | "
                f"route-only {mb['route_only_ms_per_step']['mean']:.3f} ms "
                f"(warmup-excluded, {MICRO_REPS} reps)"
            )
        typer.echo(
            f"=== {tag}: COMPLETED steps={report['steps']} "
            f"dt[{report['dt_min_s']:.3f},{report['dt_max_s']:.3f}]s "
            f"wall={report['wall_clock_s']:.1f}s "
            f"(twin {report['twin_ms_per_step']:.1f} + loop-side "
            f"{report['loop_side_ms_per_step']:.1f} ms/step) ==="
        )
        realized_steps_hint = report["steps"]
        realized_loop_ms_hint = report["loop_side_ms_per_step"]
        last_completed_tag = tag
        return record

    def _terminal_summary(tile_manifest: Path) -> dict[str, Any]:
        try:
            data = json.loads(tile_manifest.read_text())
            keep = (
                "status",
                "steps",
                "sim_time_s",
                "wall_clock_s",
                "grid_shape",
                "cells",
                "refusal_verbatim",
                "refusal_source",
                "error",
                "halt_reason",
                "projected_wall_clock_min_at_probe",
            )
            return {k: data[k] for k in keep if k in data}
        except Exception:  # noqa: BLE001 — summary must never mask the primary error
            return {"status": "TERMINAL_MANIFEST_UNREADABLE", "path": str(tile_manifest)}

    for i, tag in enumerate(run_tags):
        s = selection[tag]
        if i == 0 and tag != "tile_1_16":
            typer.echo("CHAIN WARNING: first tile is not tile_1_16 (mandatory tile overridden)")
        if tag != "tile_1_16" and t_chain0 is not None:
            elapsed = time.perf_counter() - t_chain0
            remaining_owner = OWNER_CAP_S - elapsed
            proj_min = None
            if realized_steps_hint and realized_loop_ms_hint and last_completed_tag is not None:
                # EXTRAPOLATION-FROM-MEASURED: next-tile rate scaled by cell ratio from
                # the LAST COMPLETED tile; steps hint from that same realized run.
                ratio = s["cells"] / selection[last_completed_tag]["cells"]
                proj_rate_ms = realized_loop_ms_hint * ratio * 2.0 + manifest["anchors"][
                    "resmoke_twin_ms_per_step"
                ] * (s["cells"] / manifest["anchors"]["resmoke_cells"])
                proj_min = proj_rate_ms * realized_steps_hint / 1000.0 / 60.0
            reason = None
            if elapsed > COND_ELAPSED_S:
                reason = (
                    f"elapsed {elapsed:.1f}s > conditional cap {COND_ELAPSED_S:.0f}s after "
                    "previous tiles"
                )
            elif proj_min is not None and proj_min * 60.0 > remaining_owner:
                reason = (
                    f"EXTRAPOLATION-FROM-MEASURED projected next-tile pair wall "
                    f"{proj_min*60.0:.0f}s exceeds remaining owner cap {remaining_owner:.0f}s "
                    f"(elapsed {elapsed:.0f}s of {OWNER_CAP_S:.0f}s)"
                )
            if reason is not None:
                arith = {
                    "tag": tag,
                    "cells": s["cells"],
                    "elapsed_after_previous_s": round(elapsed, 1),
                    "conditional_cap_s": COND_ELAPSED_S,
                    "remaining_owner_cap_s": round(remaining_owner, 1),
                    "extrapolated_projection_min": (
                        round(proj_min, 2) if proj_min is not None else "unavailable"
                    ),
                    "skipped_reason": reason,
                    "label": "SKIP arithmetic printed as directed; nothing timed for this tile",
                }
                manifest.setdefault("skipped_tiles", {})[tag] = arith
                write_json_atomic(manifest_path, manifest)
                typer.echo(f"SKIP {tag}: {reason}")
                continue
        rec = _run_one(tag)
        _record(tag, rec)

    # ---- verify from REALIZED disk bytes -------------------------------------
    verify = {}
    for tag in run_tags:
        s = selection[tag]
        tm = od / tag / "manifest.json"
        entry: dict[str, Any] = {"tile_manifest": str(tm)}
        try:
            data = json.loads(tm.read_text())
            rep = data.get("wf2_tile_driver_report") or {}
            twin_block = ((data.get("kill_thresholds") or {}).get("K1") or {}).get(
                "uncoupled_twin"
            ) or {}
            checks = {
                "status_completed": data.get("status") == "completed",
                "grid_shape_matches": list(data.get("grid_shape", [])) == s["dims"],
                "steps_gt_0": int(data.get("steps", 0)) > 0,
                "device_cpu": data.get("device") == "cpu",
                "driver_report_merged": bool(rep),
                "crosscheck_wall_clock_s": _floats_close(
                    data.get("wall_clock_s"), rep.get("wall_clock_s")
                ),
                "crosscheck_twin_wall_clock_s": _floats_close(
                    twin_block.get("wall_clock_s"), rep.get("twin_wall_clock_s")
                ),
                "crosscheck_steps": data.get("steps") == rep.get("steps"),
                "crosscheck_git_sha": data.get("git_sha") == rep.get("git_sha"),
            }
            entry["checks"] = checks
            entry["ok"] = all(checks.values())
            if not entry["ok"]:
                entry["mismatched_fields"] = [name for name, ok in checks.items() if not ok]
        except Exception as exc:  # noqa: BLE001
            entry["checks"] = {"manifest_readable": False}
            entry["ok"] = False
            entry["error"] = str(exc)
        verify[tag] = entry
    manifest["verification_from_disk_bytes"] = verify

    # ---- SUMMARY + SCALING BLOCK ---------------------------------------------
    anchors = manifest["anchors"]
    scaling: dict[str, Any] = {
        "reference": {
            "anchor_loop_side_ms_per_step": anchors["resmoke_loop_side_ms_per_step"],
            "anchor_twin_ms_per_step": anchors["resmoke_twin_ms_per_step"],
            "anchor_cells": anchors["resmoke_cells"],
            "anchor_basis": anchors["basis_note"],
        }
    }
    g4_statement_parts = []
    for tag in TILE_TAGS:
        rec = manifest["tiles"].get(tag)
        if not rec or rec.get("status") != "COMPLETED":
            continue
        rep = rec["driver_report"]
        sf = rep["loop_side_ms_per_step"] / anchors["resmoke_loop_side_ms_per_step"]
        ideal = rep["cells"] / anchors["resmoke_cells"]
        eff = ideal / sf
        scaling[tag] = {
            "scaling_factor_t": round(sf, 6),
            "definition": "loop_side_ms_per_step_t / anchor loop-side ms/step (same basis, "
            "assembly + coupled loop included; post-loop product writes excluded (<~1%), "
            "apples-to-apples)",
            "ideal_linear_t": round(ideal, 8),
            "efficiency_t": round(eff, 6),
            "definition_efficiency": "ideal_linear_t / scaling_factor_t (1.0 = perfectly "
            "linear in cells; >1 better than linear)",
            "arithmetic": (
                f"{rep['loop_side_ms_per_step']:.2f} / "
                f"{anchors['resmoke_loop_side_ms_per_step']:.2f} = {sf:.4f}; "
                f"{rep['cells']}/{anchors['resmoke_cells']} = {ideal:.6f}; "
                f"{ideal:.6f}/{sf:.4f} = {eff:.4f}"
            ),
        }
        if rec.get("microbench"):
            mb = rec["microbench"]["couple_ms_per_step"]["mean"]
            mb_route = rec["microbench"]["route_only_ms_per_step"]["mean"]
            scaling[tag]["microbench_crossref"] = {
                "tile_couple_ms_mean": round(mb, 4),
                "tile_route_only_ms_mean": round(mb_route, 4),
                "vs_fullgraph_anchor_143p148": round(mb / anchors["fullgraph_couple_ms_mean"], 6),
                "primary_comparator": "active_cell_fraction",
                "note": (
                    "PRIMARY comparator is ACTIVE-CELL fraction — combined couple cost is "
                    "surface-dominated; active-NODE fraction applies ONLY to the "
                    "separately-recorded route_only rate"
                ),
                "active_cell_fraction": round(rep["cells"] / anchors["resmoke_cells"], 6),
                "route_only_rate_node_comparison": {
                    "active_node_fraction": round(
                        rec["driver_report"]["n_active_nodes"] / arr["num_nodes"], 6
                    ),
                    "route_only_ms_mean": round(mb_route, 4),
                },
            }
        if tag == "tile_1_16":
            g4_statement_parts.append(
                f"G4 AT TILE SCOPE: the 1/16-area tile realizes {sf*100:.2f}% of full-domain "
                f"loop-side per-step cost ({rep['loop_side_ms_per_step']:.1f} vs "
                f"{anchors['resmoke_loop_side_ms_per_step']:.1f} ms/step); a full 1200 s "
                f"nowcast window at tile scope costs "
                f"{(rep['wall_clock_s'])/60.0:.2f} min end-to-end excluding post-loop "
                f"product writes (twin+loop+assembly) — measured. Implication for the "
                f"10-minute gate holds AT TILE SCOPE ONLY."
            )
    manifest["summary_and_scaling"] = scaling
    manifest["g4_framing"] = {
        "statements": g4_statement_parts or ["no completed tile — nothing measurable to state"],
        "scope_disclaimer": (
            "NEVER claims the full-domain number; G4's full-domain question is answered "
            "elsewhere. This milestone measures SUBDOMAIN TILE THROUGHPUT only."
        ),
    }
    manifest["comparability_caveat"] = (
        "Tiles realize 425-486 steps over the same 1200 s window vs the full-domain "
        "anchor's 538, with differing dt floors (1.320/0.486/0.844 vs 0.758 s); fixed "
        "assembly amortized over fewer steps inflates small-tile per-step rates, which "
        "DEFLATES the reported superlinear efficiencies (conservative direction)."
    )
    mandatory_ok = manifest["tiles"].get("tile_1_16", {}).get(
        "status"
    ) == "COMPLETED" and verify.get("tile_1_16", {}).get("ok", False)
    manifest["mandatory_tile_1_16_timing_valid"] = mandatory_ok
    manifest["fatal_invalid_for_timing"] = fatal_invalid
    manifest["finished_utc"] = _utc_now()
    manifest["wall_clock_sec"] = round(time.perf_counter() - t_all, 3)
    all_verify_ok = all(v.get("ok") for v in verify.values()) if verify else False
    manifest["status"] = (
        "completed"
        if (mandatory_ok and not fatal_invalid)
        else ("completed_with_invalid_tiles" if mandatory_ok else "failed_mandatory_tile_missing")
    )
    write_json_atomic(manifest_path, manifest)

    typer.echo(f"\nSUMMARY scaling: {json.dumps(scaling.get('tile_1_16', {}), default=str)}")
    typer.echo(f"verification (disk bytes): all_ok={all_verify_ok}")
    typer.echo(f"manifest: {manifest_path}")
    if manifest["status"] != "completed":
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
