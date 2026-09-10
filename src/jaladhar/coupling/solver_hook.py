from __future__ import annotations

import csv
import json
import math
import subprocess
import time
from bisect import bisect_right
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch
import typer
from yaml import safe_load as _yaml_safe_load

from jaladhar.coupling.config import ConfigError, CouplingConfig, resolve_config
from jaladhar.coupling.diagnostics import (
    DiagnosticsRefusal,
    attribute_ground_truth,
    attribute_ground_truth_edges,
    g2_verdict,
    load_drain_edges_geoms,
)
from jaladhar.coupling.exchange import (
    NodeState,
    build_node_state,
    couple_step,
    zero_drain_cap_out_of_place,
)
from jaladhar.coupling.ledger import CouplingMassBreach, CouplingMassLedger, IndirectCflMonitor
from jaladhar.coupling.router import DrainGraph, RefuseLoadError, load_drain_graph
from jaladhar.provenance import write_json_atomic
from jaladhar.solver.acc import SolverParams, acc_step
from jaladhar.solver.mass import MassBudget
from jaladhar.solver.run import (
    estimate_steps_band,
    uniform_storm,
)
from jaladhar.solver.run import (
    simulate as uncoupled_simulate,
)
from jaladhar.solver.state import StaticFields, initial_state, load_domain, load_solver_config
from jaladhar.solver.timestep import TimestepController

app = typer.Typer(add_completion=False)

REPO = Path(__file__).resolve().parents[3]
STAGE = "wf2_coupled_run"

K1_MIN_EVAL_STEPS = 4

COMPUTE_PROBE_STEP = 5

__all__ = [
    "ComputeBudgetExceeded",
    "CoupledRunResult",
    "KillThresholdHalt",
    "coupled_parameter_set",
    "simulate_coupled",
]


class KillThresholdHalt(RuntimeError):
    """Pre-registered kill threshold fired — HALT, report, do not tune (spec §10.6/§13)."""


class ComputeBudgetExceeded(RuntimeError):
    """Measured wall-clock projection exceeds smoke.cpu_budget_wall_clock_min (§14)."""


def _assert_zeroed(static_zeroed: StaticFields) -> None:
    """Spec §9.1: simulate_coupled asserts ``(drain_cap_m_s == 0).all()`` BEFORE the loop;
    couple_step re-asserts at entry (contract binding). Module-level so the V5 red demo
    can neutralize THIS layer alone and prove the next layer (couple_step's entry assert)
    is independently armed — defence in depth must be demonstrable layer by layer."""
    if not bool((static_zeroed.drain_cap_m_s == 0).all()):
        raise RuntimeError(
            f"[{STAGE}] guard (a): zeroed copy still carries non-zero drain_cap_m_s "
            f"({int((static_zeroed.drain_cap_m_s != 0).sum())} cells) — refusing to run "
            "with a live legacy sink beside capture"
        )


def coupled_parameter_set(static: StaticFields) -> dict[str, torch.Tensor]:
    return {k: v for k, v in static.parameters().items() if k != "drain_cap_m_s"}


def _git_info(repo_root: Path) -> dict[str, Any]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            cwd=repo_root,
        ).strip()
    except Exception:
        sha = "unknown"
    try:
        porcelain = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            text=True,
            stderr=subprocess.DEVNULL,
            cwd=repo_root,
        ).splitlines()
    except Exception:
        porcelain = []
    return {"git_sha": sha, "git_dirty": bool(porcelain), "git_porcelain_paths": porcelain[:32]}


def _require_solver_keys(solver_cfg: dict[str, Any]) -> None:
    missing: list[str] = []

    def check(d: Any, *keys: str) -> None:
        curr = d
        path: list[str] = []
        for k in keys:
            path.append(k)
            if not isinstance(curr, dict) or k not in curr:
                missing.append(".".join(path))
                return
            curr = curr[k]

    check(solver_cfg, "_domain", "resolution_m")
    check(solver_cfg, "physics", "gravity_m_s2")
    check(solver_cfg, "physics", "hf_floor_m")
    check(solver_cfg, "physics", "depth_threshold_m")
    check(solver_cfg, "timestep", "cfl_alpha")
    check(solver_cfg, "timestep", "cfl_ceiling")
    check(solver_cfg, "timestep", "min_dt_s")
    check(solver_cfg, "timestep", "max_dt_s")
    check(solver_cfg, "wetdry", "mode")
    check(solver_cfg, "wetdry", "ramp_width_m")
    check(solver_cfg, "buildings", "min_conveyance_factor")
    check(solver_cfg, "boundaries", "mode")
    check(solver_cfg, "boundaries", "min_bed_slope")
    check(solver_cfg, "mass", "relative_tolerance")
    check(solver_cfg, "mass", "relative_tolerance_is_measured")
    check(solver_cfg, "mass", "check_every_steps")

    if missing:
        raise KeyError(
            f"[{STAGE}] solver config pre-flight: missing {len(missing)} required key(s): "
            f"{', '.join(missing)} (rule 7: aggregated, first second, all of them)"
        )


def _resolved_coupling_snapshot(cfg: CouplingConfig) -> dict[str, Any]:

    def _jsonable(v: Any) -> Any:
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, dict):
            return {k: _jsonable(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_jsonable(x) for x in v]
        return v

    return {k: _jsonable(v) for k, v in asdict(cfg).items()}


COUPLED_MODE_TRANSFORMATION: dict[str, Any] = {
    "legacy_drain_disabled": True,
    "description": (
        "Coupled mode replaces the legacy raster sink with the weir-orifice exchange: a NEW "
        "frozen StaticFields is constructed OUT OF PLACE with drain_cap_m_s = torch.zeros_like "
        "(guard a); the original instance is asserted bitwise-unchanged every run"
    ),
    "zeroing_site": "jaladhar.coupling.exchange.zero_drain_cap_out_of_place",
    "entry_assert": (
        "couple_step asserts (static.drain_cap_m_s == 0).all() at entry (guard a, contract "
        "legacy_sink_replacement_binding)"
    ),
    "parameter_view_guard_d": (
        "coupled_parameter_set(): drain_cap_m_s removed from the StaticFields.parameters() view "
        "so no calibration can tune the dead knob"
    ),
    "distinct_reported_lines_deviation_D_E": [
        "legacy_drain_out_m3 (= MassBudget.drain_out; == 0.0 EXACTLY when coupled)",
        "drain_out_net_m3 (= captured - returned; may be NEGATIVE - physical)",
    ],
    "call_site_realization_deviation_D_F": (
        "pinned interior call site ('after mass update, before sink stage') is unreachable "
        "inside monolithic acc_step and NO solver file is modified; couple_step is invoked "
        "immediately AFTER acc_step returns per step. Consequences: drain stage inside "
        "acc_step is exactly zero (no double count); infiltration ordering discrepancy is nil "
        "because the resolver refuses non-zero uniform_rate_mm_per_hr (D-F)"
    ),
}


def _node_geometry_assumption(cfg: CouplingConfig) -> dict[str, Any]:
    return {
        "node_plan_area_rule": (
            "width_mean_m over CAPACITY-BEARING incident edges (isolated fallback "
            f"{cfg.storage.isolated_node_width_m} m) * shaft_length_proxy_m "
            f"({cfg.storage.shaft_length_proxy_m}) — vertical-shaft proxy"
        ),
        "assumed_freeboard_m": cfg.storage.assumed_freeboard_m,
        "basis": "declared assumption v1",
        "assumed_uncalibrated": True,
        "capture_radius_m": cfg.exchange.capture_radius_m,
        "a_open_width_fraction": cfg.exchange.a_open_width_fraction,
        "return_distribution": "uniform across the node's allocated cells (declared-choice v1)",
        # §9.4 graft: width provenance is BELOW CPHEEO confidence; every manifest line where
        "width_grade": "BELOW_CPHEEO_CONFIDENCE",
    }


DEVIATIONS_NOTICE: dict[str, str] = {
    "D_A": (
        "pending owner adjudication: no terminal/outfall export term exists in the frozen v1 "
        "sign conventions; fill-and-surcharge-by-construction ships WITH mandatory class "
        "partition + SUSPICIOUS machinery. Only a minority of nodes reach outfalls under "
        "the active terminal definition (see component_class_split and terminal_seed_resolution "
        "in this manifest) [F], so v1 systematically over-returns volume to streets relative "
        "to any export law"
    ),
    "D_C": (
        "wording conflict resolved FOR the frozen contract: NULL-capacity edges (286 synthetic "
        "+ 61 zero-slope + 32 area-capped = 379) carry Q_edge=0 ALWAYS; excluded from capture "
        "allocation AND outgoing denominators"
    ),
    "D_E": (
        "applied per owner decision 2026-08-25: legacy_drain_out_m3 and drain_out_net_m3 are "
        "DISTINCT reported quantities; contract backward_compat_alias intentionally NOT "
        "implemented (guard c's red test depends on the separation)"
    ),
    "D_F": (
        "realization recorded: couple_step invoked immediately AFTER acc_step returns (pinned "
        "interior call site unreachable inside monolithic acc_step; solver files untouchable); "
        "infiltration ordering discrepancy nil under the resolver-enforced 0.0 rate"
    ),
}

FROZEN_READER_SOURCE = "frozen_reader"
"""``.source`` tag carried by a RefuseLoadError that escaped the frozen WF-1
``read_artefact`` call — the ONLY refusals entitled to terminal status "refused_with_D_G"."""

NODE_MODE_RADIUS_BASIS = (
    "historical node-mode attribution radius, pinned at 100.0 m since v1 "
    "(declared assumption ‡, unsourced, never tuned); read from config leaf "
    "diagnostics.attribution_radius_node_m so it stays independent of the edge-mode "
    "radius (attribution_radius_m); superseded for production attribution by owner "
    "ruling D-GT 2026-08-26 — regression/A-B evidence only"
)


@contextmanager
def _tag_frozen_reader_refusals():
    import jaladhar.coupling.router as _router

    original = _router.read_artefact

    def _tagged(*args: Any, **kwargs: Any):
        try:
            return original(*args, **kwargs)
        except RefuseLoadError as exc:
            exc.source = FROZEN_READER_SOURCE
            raise

    _router.read_artefact = _tagged
    try:
        yield
    finally:
        _router.read_artefact = original


def _refusal_gate_name(exc: BaseException) -> str:
    msg = str(exc)
    if msg.startswith("[") and "]" in msg:
        return msg[1 : msg.index("]")]
    return "unclassified"


def _load_falsifier_header(path: Path, refuse_if_missing: bool) -> dict[str, Any]:
    if not path.exists():
        if refuse_if_missing:
            exc = RefuseLoadError(
                f"[{STAGE}] falsifier prediction set missing at {path} "
                "(diagnostics.refuse_start_on_missing_falsifier=true) — refusing to start"
            )
            exc.source = "diagnostics.falsifier_set"
            raise exc
        return {"loaded_at_start": False, "path": str(path)}
    try:
        data = json.loads(path.read_text())
        return {
            "loaded_at_start": True,
            "path": str(path),
            "n_predicted_edges": int(data["n_predicted_edges"]),
            "n_predicted_nodes": len(data["predicted_node_ids"]),
            "source_gpkg_sha256": str(data["source_gpkg_sha256"]),
            "status": "comparison_deferred_to_diagnostics_unit",
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        gate_exc = RefuseLoadError(
            f"[{STAGE}] falsifier prediction set at {path} unreadable/lacking counts: {exc}"
        )
        gate_exc.source = "diagnostics.falsifier_set"
        raise gate_exc from exc


@dataclass
class CoupledRunResult:

    h: torch.Tensor
    qx: torch.Tensor
    qy: torch.Tensor
    steps: int
    sim_time_s: float
    wall_clock_s: float
    controller: TimestepController
    budget: MassBudget
    ledger: CouplingMassLedger
    cfl_monitor: IndirectCflMonitor
    graph: DrainGraph
    node_state_final: NodeState
    static_original: StaticFields
    static_zeroed: StaticFields
    max_courant: float = 0.0
    max_selected_courant: float = 0.0
    rejected_steps: int = 0
    retry_attempts: int = 0
    min_depth: float = 0.0
    snapshots: list[tuple[float, torch.Tensor]] = field(default_factory=list)
    twin_summary: dict[str, Any] = field(default_factory=dict)
    start_manifest: dict[str, Any] = field(default_factory=dict)
    final_manifest: dict[str, Any] = field(default_factory=dict)
    g2_anti_vacuity_fail: bool = False
    """§11.1 anti-vacuity: True iff total_surcharging_steps == 0 OR total_returned_m3 <
    diagnostics.g2_min_returned_m3. Mirrored into final_manifest["g2"]; the CLI exits
    non-zero on it — the library reports, it does not raise."""


def _uncoupled_steps_covered(twin_cumtime: list[float], tau: float) -> int:
    return bisect_right(twin_cumtime, tau)


def _write_products(
    cfg: CouplingConfig,
    ledger: CouplingMassLedger,
    snapshots: list[tuple[float, torch.Tensor]],
) -> dict[str, Any]:
    products: dict[str, Any] = {}
    events = ledger.flush_open_events()
    csv_path = cfg.outputs.surcharge_events_csv
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    header = "node_id,total_returned_m3,first_step,last_step,max_head_m"
    lines = [header] + [
        f"{e['node_id']},{e['total_returned_m3']!r},{e['first_step']},{e['last_step']},"
        f"{e['max_head_m']!r}"
        for e in sorted(events, key=lambda e: e["node_id"])
    ]
    csv_path.write_text("\n".join(lines) + "\n")
    products["surcharge_events_csv"] = str(csv_path)
    products["surcharge_events_rows"] = len(events)

    depth_dir = cfg.outputs.depth_series_dir
    depth_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for t, h_snap in snapshots:
        p = depth_dir / f"depth_t{int(round(t)):06d}s.npy"
        np.save(p, h_snap.detach().cpu().numpy().astype("float32"))
        written.append(str(p))
    products["depth_series"] = written
    return products


def simulate_coupled(
    cfg: CouplingConfig,
    solver_cfg: dict[str, Any],
    repo_root: Path,
    *,
    graph: DrainGraph | None = None,
    static: StaticFields | None = None,
    node_state: NodeState | None = None,
    h0: torch.Tensor | None = None,
    rain: Callable[[float], float] | None = None,
    duration_s: float | None = None,
    max_steps: int = 2_000_000,
    window: tuple[slice, slice] | tuple[int, int, int, int] | None = None,
    mass_check_every: int | None = None,
    snapshot_every_s: float | None = None,
    smoke: bool = False,
) -> CoupledRunResult:
    repo_root = Path(repo_root)
    t_start = time.perf_counter()
    if cfg.device != "cpu":
        raise ValueError(f"[{STAGE}] CPU-only workflow (V12); cfg.device={cfg.device!r}")
    if cfg.enabled is not True:
        raise ValueError(
            f"[{STAGE}] simulate_coupled is the COUPLED driver; coupling.enabled={cfg.enabled!r} "
            "(enabled=false means the stock solver path, byte-identical behaviour)"
        )
    if cfg.legacy_sink_replacement is not True:
        raise ValueError(
            f"[{STAGE}] guard (b): coupling.legacy_sink_replacement must be true when enabled; "
            "the manifest must DECLARE the legacy sink replacement"
        )
    _require_solver_keys(solver_cfg)

    dx = float(solver_cfg["_domain"]["resolution_m"])
    p = SolverParams.from_config(solver_cfg, dx)
    controller = TimestepController(
        dx=dx,
        alpha=float(solver_cfg["timestep"]["cfl_alpha"]),
        cfl_ceiling=float(solver_cfg["timestep"]["cfl_ceiling"]),
        gravity=p.gravity,
        min_dt_s=float(solver_cfg["timestep"]["min_dt_s"]),
        max_dt_s=float(solver_cfg["timestep"]["max_dt_s"]),
    )
    duration = (
        float(duration_s)
        if duration_s is not None
        else float(solver_cfg["storm"]["simulation_duration_s"])
    )
    check_every = (
        int(mass_check_every)
        if mass_check_every is not None
        else int(solver_cfg["mass"]["check_every_steps"])
    )
    snap_every = (
        float(snapshot_every_s)
        if snapshot_every_s is not None
        else float(cfg.outputs.write_every_s)
    )

    cfg.outputs.run_dir.mkdir(parents=True, exist_ok=True)
    git = _git_info(repo_root)
    band = tuple(solver_cfg.get("compute_estimate", {}).get("h_max_band_m", (1.0, 30.0)))
    try:
        n_lo, n_hi = estimate_steps_band(solver_cfg, duration, band)
    except KeyError:
        n_lo = n_hi = -1
    start_manifest: dict[str, Any] = {
        "stage": STAGE,
        "status": "running",
        **git,
        "start_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "device": cfg.device,
        "smoke": bool(smoke),
        "window": None if window is None else repr(window),
        "config_snapshot": {
            "coupling_resolved": _resolved_coupling_snapshot(cfg),
            "solver_yaml": json.loads(json.dumps(solver_cfg, default=str)),
        },
        "coupled_mode_transformation": dict(COUPLED_MODE_TRANSFORMATION),
        "node_geometry_assumption": _node_geometry_assumption(cfg),
        "deviations_notice": dict(DEVIATIONS_NOTICE),
        "compute_budget_estimate": {
            "cells": "realized_at_input_assembly",
            "steps_band_uncoupled_equivalent": [n_lo, n_hi],
            "device": "cpu",
            "wall_clock_projection": "measured <=5-step probe against smoke cap (§14)",
            "smoke_cap_wall_clock_min": cfg.smoke.cpu_budget_wall_clock_min,
            "compute_anchor_context": {
                k: v for k, v in (solver_cfg.get("_compute", {}) or {}).get("anchor", {}).items()
            }
            or "compute.yaml anchor absent (GPU-measured; context only, never the CPU number)",
        },
    }
    write_json_atomic(cfg.outputs.manifest, start_manifest)

    def update_manifest(status: str, extra: dict[str, Any]) -> dict[str, Any]:
        current: dict[str, Any] = {}
        try:
            loaded = json.loads(cfg.outputs.manifest.read_text())
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, json.JSONDecodeError):
            current = {}
        payload = {**start_manifest, **current, **extra, "status": status}
        write_json_atomic(cfg.outputs.manifest, payload)
        return payload

    try:
        with _tag_frozen_reader_refusals():
            graph_loaded = (
                graph if graph is not None else load_drain_graph(cfg, repo_root, window=window)
            )
    except RefuseLoadError as exc:
        if getattr(exc, "source", None) == FROZEN_READER_SOURCE:

            update_manifest(
                "refused_with_D_G",
                {
                    "refusal_verbatim": str(exc),
                    "d_g_notice": (
                        "frozen read_artefact refuses the only realized artefact "
                        "(zero_length_dropped_count=42 / capacity_block_consistency on "
                        "93 null-class edges / adjacency dropped lists) — pending owner "
                        "adjudication of D-G; no assertion bypassed, no load fabricated"
                    ),
                    "wall_clock_s": time.perf_counter() - t_start,
                },
            )
        else:

            update_manifest(
                "refused_load_gate",
                {
                    "refusal_verbatim": str(exc),
                    "refusal_source": f"router.load_drain_graph[{_refusal_gate_name(exc)}]",
                    "wall_clock_s": time.perf_counter() - t_start,
                },
            )
        raise

    try:
        falsifier_header = _load_falsifier_header(
            cfg.diagnostics.falsifier_set, cfg.diagnostics.refuse_start_on_missing_falsifier
        )
    except RefuseLoadError as exc:

        update_manifest(
            "refused_falsifier_missing",
            {
                "refusal_verbatim": str(exc),
                "refusal_source": "diagnostics.falsifier_set",
                "wall_clock_s": time.perf_counter() - t_start,
            },
        )
        raise

    static_original = (
        static
        if static is not None
        else load_domain(solver_cfg, repo_root, window=window, device=cfg.device)
    )

    n_cells = int(static_original.shape[0]) * int(static_original.shape[1])
    update_manifest(
        "running",
        {"cells": n_cells, "grid_shape": list(static_original.shape), "duration_s": duration},
    )

    live_before = static_original.drain_cap_m_s.clone()
    static_zeroed = zero_drain_cap_out_of_place(static_original)
    if not torch.equal(static_original.drain_cap_m_s, live_before):
        raise RuntimeError(f"[{STAGE}] guard (a): zeroing MUTATED the original StaticFields")
    _assert_zeroed(static_zeroed)
    params_coupled = coupled_parameter_set(static_zeroed)
    if "drain_cap_m_s" in params_coupled:
        raise RuntimeError(f"[{STAGE}] guard (d): dead knob present in the coupled parameter view")

    h, qx, qy = initial_state(static_zeroed, device=cfg.device)
    if h0 is not None:
        h = h0.clone()
    node_state_run = node_state if node_state is not None else build_node_state(graph_loaded)
    if tuple(node_state_run.h_node_m.shape) != (graph_loaded.num_nodes,):
        raise ValueError(f"[{STAGE}] injected node_state size != num_nodes")
    initial_rest = bool((node_state_run.h_node_m == 0).all()) and bool(
        (node_state_run.vol_in_m3_cum == 0).all() and (node_state_run.vol_out_m3_cum == 0).all()
    )

    budget = MassBudget(
        cell_area_m2=dx * dx,
        relative_tolerance=float(solver_cfg["mass"]["relative_tolerance"]),
        tolerance_is_measured=bool(solver_cfg["mass"]["relative_tolerance_is_measured"]),
    )
    ledger = CouplingMassLedger(
        cell_area_m2=dx * dx,
        host_budget=budget,
        graph=graph_loaded,
        judged_bar_relative=cfg.budget.judged_bar_relative,
        contract_tolerance=cfg.budget.relative_tolerance,
    )
    monitor = IndirectCflMonitor(alarm_factor=cfg.dt_policy.indirect_cfl_alarm_factor)
    budget.start(h)

    twin_ctrl = TimestepController(
        dx=dx,
        alpha=float(solver_cfg["timestep"]["cfl_alpha"]),
        cfl_ceiling=float(solver_cfg["timestep"]["cfl_ceiling"]),
        gravity=p.gravity,
        min_dt_s=float(solver_cfg["timestep"]["min_dt_s"]),
        max_dt_s=float(solver_cfg["timestep"]["max_dt_s"]),
    )
    twin_t0 = time.perf_counter()
    twin_res = uncoupled_simulate(
        static_zeroed,
        p,
        twin_ctrl,
        duration_s=duration,
        rain=rain,
        h0=h0.clone() if h0 is not None else None,
        budget=None,
        mass_check_every=1_000_000_000,
        snapshot_every_s=None,
        max_steps=max_steps,
        device=cfg.device,
    )
    twin_cum: list[float] = []
    acc_t = 0.0
    for d in twin_ctrl.schedule:
        acc_t += d
        twin_cum.append(acc_t)
    twin_summary = {
        "steps": twin_res.steps,
        "sim_time_s": twin_res.sim_time_s,
        "wall_clock_s": time.perf_counter() - twin_t0,
        "dt_min_s": min(twin_ctrl.schedule) if twin_ctrl.schedule else 0.0,
        "dt_max_s": max(twin_ctrl.schedule) if twin_ctrl.schedule else 0.0,
        "role": "K1 denominator: measured uncoupled-equivalent schedule (same forcing/bounds)",
    }

    t, steps = 0.0, 0
    max_courant = max_selected = 0.0
    steps_with_rejection = 0
    min_depth = 0.0
    neg_cells_peak = 0
    snapshots: list[tuple[float, torch.Tensor]] = []
    next_snap = 0.0
    k1_fired = False
    loop_t0 = time.perf_counter()
    projected_wall_min_reported: float | None = None

    try:
        with torch.no_grad():
            while t < duration and steps < max_steps:
                dt = controller.dt_for(h)
                dt = min(dt, duration - t)
                if dt <= 0:
                    break
                r = rain(t) if rain is not None else 0.0
                c_sel = controller.courant(h, dt)
                max_selected = max(max_selected, c_sel)

                had_rejection = False
                while True:
                    h_acc, qx_new, qy_new, diag = acc_step(
                        h, qx, qy, static_zeroed, dt, p, rain_rate_m_s=r
                    )
                    cr_try = couple_step(
                        h_acc, qx_new, qy_new, static_zeroed, node_state_run, graph_loaded, dt
                    )
                    c_realized = controller.courant(cr_try.h_new, dt)
                    if c_realized > controller.cfl_ceiling and dt > controller.min_dt_s:
                        controller.rejections += 1
                        had_rejection = True
                        dt = max(dt / 2.0, controller.min_dt_s)
                        continue
                    break

                if had_rejection:
                    steps_with_rejection += 1

                h, qx, qy = cr_try.h_new, qx_new, qy_new
                node_state_run = cr_try.node_state_new
                controller.schedule.append(dt)
                max_courant = max(max_courant, c_realized)
                min_depth = min(min_depth, float(h.min()))
                neg_cells_peak = max(neg_cells_peak, int(diag["negative_depth_cells"]))

                budget.accumulate(h, diag, r, dt, n_cells)
                ledger.accumulate(cr_try, dt)

                d_h_couple = float(
                    torch.maximum(
                        cr_try.captured_depth_m.max(), cr_try.returned_depth_m.max()
                    ).item()
                )
                monitor.observe(float(h.max().item()), d_h_couple)

                t += dt
                steps += 1

                n_unc = _uncoupled_steps_covered(twin_cum, t)
                if steps >= K1_MIN_EVAL_STEPS and n_unc >= 1:
                    allowed = math.floor(1.0 / cfg.dt_policy.min_fraction_of_uncoupled * n_unc)
                    if steps > allowed:
                        k1_fired = True
                        raise KillThresholdHalt(
                            f"K1: {steps} coupled steps cover t={t:.3f}s but the uncoupled "
                            f"equivalent covers it in {n_unc} (ratio "
                            f"{steps / max(n_unc, 1):.2f} > "
                            f"{1.0 / cfg.dt_policy.min_fraction_of_uncoupled:.1f}); dt collapsed "
                            "below "
                            f"{100 * cfg.dt_policy.min_fraction_of_uncoupled:.0f}% of the "
                            "uncoupled schedule — HALT, report, do not tune (spec §10.6)"
                        )

                if steps % check_every == 0:

                    ledger.mass_check(steps)

                if steps == COMPUTE_PROBE_STEP:
                    elapsed = time.perf_counter() - loop_t0
                    projected_total = elapsed * (duration / max(t, 1e-9))
                    projected_wall_min_reported = projected_total / 60.0
                    typer.echo(
                        f"[{STAGE}] compute-budget probe @ step {steps}: "
                        f"{elapsed / steps * 1000:.1f} ms/step realized, projected total "
                        f"{projected_wall_min_reported:.2f} min vs cap "
                        f"{cfg.smoke.cpu_budget_wall_clock_min:.1f} min"
                    )
                    if projected_wall_min_reported > cfg.smoke.cpu_budget_wall_clock_min:
                        raise ComputeBudgetExceeded(
                            f"projected coupled wall clock "
                            f"{projected_wall_min_reported:.2f} min exceeds "
                            f"smoke.cpu_budget_wall_clock_min="
                            f"{cfg.smoke.cpu_budget_wall_clock_min:.1f} min — refusing (§14)"
                        )

                if snap_every is not None and t >= next_snap:
                    snapshots.append((t, h.detach().cpu()))
                    next_snap += snap_every

    except (KillThresholdHalt, ComputeBudgetExceeded) as exc:
        status = "halted_K1" if isinstance(exc, KillThresholdHalt) else "refused_compute_budget"
        update_manifest(
            status,
            _terminal_fields(
                cfg=cfg,
                graph=graph_loaded,
                controller=controller,
                budget=budget,
                ledger=ledger,
                monitor=monitor,
                steps=steps,
                sim_time=t,
                wall_clock=time.perf_counter() - t_start,
                twin=twin_summary,
                k1_fired=k1_fired,
                n_unc_final=_uncoupled_steps_covered(twin_cum, t),
                falsifier_header=falsifier_header,
                initial_rest=initial_rest,
                projected_wall_min=projected_wall_min_reported,
                products={},
                halt_reason=str(exc),
            ),
        )
        exc.manifest_status = status
        raise

    except (CouplingMassBreach, RuntimeError, ValueError) as exc:
        update_manifest(
            "failed",
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "steps_realized": steps,
                "sim_time_s": t,
                "wall_clock_s": time.perf_counter() - t_start,
            },
        )
        raise

    wall_clock = time.perf_counter() - t_start
    products = _write_products(cfg, ledger, snapshots)
    final_manifest = update_manifest(
        "completed",
        _terminal_fields(
            cfg=cfg,
            graph=graph_loaded,
            controller=controller,
            budget=budget,
            ledger=ledger,
            monitor=monitor,
            steps=steps,
            sim_time=t,
            wall_clock=wall_clock,
            twin=twin_summary,
            k1_fired=k1_fired,
            n_unc_final=_uncoupled_steps_covered(twin_cum, t),
            falsifier_header=falsifier_header,
            initial_rest=initial_rest,
            projected_wall_min=projected_wall_min_reported,
            products=products,
            halt_reason=None,
            extra={
                "max_courant_realized": max_courant,
                "max_courant_selected": max_selected,
                "rejected_steps": steps_with_rejection,
                "retry_attempts": controller.rejections,
                "min_depth_m": min_depth,
                "peak_negative_depth_cells": neg_cells_peak,
                "snapshots_written": len(snapshots),
            },
        ),
    )

    return CoupledRunResult(
        h=h,
        qx=qx,
        qy=qy,
        steps=steps,
        sim_time_s=t,
        wall_clock_s=wall_clock,
        controller=controller,
        budget=budget,
        ledger=ledger,
        cfl_monitor=monitor,
        graph=graph_loaded,
        node_state_final=node_state_run,
        static_original=static_original,
        static_zeroed=static_zeroed,
        max_courant=max_courant,
        max_selected_courant=max_selected,
        rejected_steps=steps_with_rejection,
        retry_attempts=controller.rejections,
        min_depth=min_depth,
        snapshots=snapshots,
        twin_summary=twin_summary,
        start_manifest=start_manifest,
        final_manifest=final_manifest,
        g2_anti_vacuity_fail=bool(final_manifest["g2"]["anti_vacuity_fail"]),
    )


def _g2_anti_vacuity_fail(ledger: CouplingMassLedger, min_returned_m3: float) -> bool:
    return ledger.total_surcharging_steps == 0 or ledger.surcharge_returned_m3 < min_returned_m3


def _gt_not_assessed_block(exc: BaseException) -> dict[str, Any]:
    return {
        "suspicious": False,
        "suspicious_state": "NOT_ASSESSED",
        "dead_end_share_of_gt_matched_returned_volume": None,
        "n_gt_matched": None,
        "note": (
            "gt_attribution UNAVAILABLE for this run — recorded honestly; the score-g2 "
            "headline carries SUSPICIOUS=NOT_ASSESSED and its UNASSESSED warning. No "
            "attribution is fabricated"
        ),
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def _gt_attribution_block(
    cfg: CouplingConfig, graph: DrainGraph, events: list[dict[str, Any]]
) -> dict[str, Any]:
    try:
        if cfg.diagnostics.attribution_mode == "edge":
            geom_by_id, _from_by_id, to_by_id, edge_crs = load_drain_edges_geoms(
                Path(cfg.graph.gpkg)
            )
            return attribute_ground_truth_edges(
                events,
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

        return attribute_ground_truth(
            events,
            Path(cfg.diagnostics.ground_truth_manifest),
            radius_m=float(cfg.diagnostics.attribution_radius_node_m),
            graph=graph,
            suspicious_deadend_share=float(cfg.diagnostics.suspicious_deadend_share),
            radius_basis=NODE_MODE_RADIUS_BASIS,
        )
    except (DiagnosticsRefusal, OSError, ValueError, csv.Error) as exc:
        return _gt_not_assessed_block(exc)
    except Exception as exc:
        return _gt_not_assessed_block(exc)


def _terminal_fields(
    *,
    cfg: CouplingConfig,
    graph: DrainGraph,
    controller: TimestepController,
    budget: MassBudget,
    ledger: CouplingMassLedger,
    monitor: IndirectCflMonitor,
    steps: int,
    sim_time: float,
    wall_clock: float,
    twin: dict[str, Any],
    k1_fired: bool,
    n_unc_final: int,
    falsifier_header: dict[str, Any],
    initial_rest: bool,
    projected_wall_min: float | None,
    products: dict[str, Any],
    halt_reason: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ledger_dict = ledger.as_dict()
    rain_in = budget.rain_in
    captured = ledger.captured_to_drains_m3
    returned = ledger.surcharge_returned_m3
    ratio_k3 = (returned / captured) if captured > 0 else None
    storage_tell_fired = bool(
        ratio_k3 is not None and ratio_k3 < 0.05 and captured > 0.01 * rain_in
    )
    class_split = {
        "outfall_terminating": graph.component_class.count("outfall_terminating"),
        "dead_end": graph.component_class.count("dead_end"),
    }

    events = ledger.flush_open_events()
    gt_block = _gt_attribution_block(cfg, graph, events)
    g2_verdict_text, g2_reason = g2_verdict(
        {
            "total_surcharging_steps": ledger.total_surcharging_steps,
            "total_returned_m3": returned,
            "component_class_split": class_split,
            "gt_attribution": gt_block,
        },
        g2_min_returned_m3=cfg.diagnostics.g2_min_returned_m3,
    )
    fields: dict[str, Any] = {
        "coupling_enabled": cfg.enabled,
        "coupling_version": cfg.coupling_version,
        "version_pins": {
            "assumed_freeboard_m": cfg.storage.assumed_freeboard_m,
            "capacity_rule_version": graph.manifest.get("capacity_rule_version"),
            "graph_fingerprint": graph.graph_fingerprint,
        },
        "legacy_drain_disabled": True,
        "captured_to_drains_m3": captured,
        "surcharge_returned_m3": returned,
        "drain_out_net_m3": ledger.drain_out_net_m3,
        "legacy_drain_out_m3": ledger.legacy_drain_out_m3,
        "alias_note": "D-E: backward_compat_alias NOT applied; distinct lines above",
        "relative_residual": ledger.contract_residual(),
        "total_water_relative_residual": ledger.total_water_relative_residual(),
        "residual_tolerance_block": {
            "contract_relative_tolerance": cfg.budget.relative_tolerance,
            "judged_bar_relative": cfg.budget.judged_bar_relative,
            "judged_form": "total_water",
            "realized_reference_residual": cfg.budget.realized_reference_residual,
            "reference_note": "replay#2 [F]; reported beside, NOT a gate",
        },
        "v_nodes_t_m3": ledger.v_nodes_m3(),
        "residual_identity_note": (
            "contract + V_nodes reconciles identically with total-water up to the "
            "reconciliation identity error (edge fluxes telescope globally)"
        ),
        "total_surcharging_steps": ledger.total_surcharging_steps,
        "total_returned_m3": returned,
        "surcharge_events_csv": products.get(
            "surcharge_events_csv", str(cfg.outputs.surcharge_events_csv)
        ),
        "surcharge_events_rows": products.get("surcharge_events_rows"),
        "cap_binding_steps": ledger.cap_binding_steps,
        "cap_binding_fraction_of_steps": (ledger.cap_binding_steps / steps) if steps else 0.0,
        "graph_is_dag": graph.manifest.get("graph_is_dag") is True,
        "topo_order_sha256": graph.topo_sha256,
        "node_geometry_assumption": _node_geometry_assumption(cfg),
        "falsifier_comparison": falsifier_header,
        "component_class_split": class_split,
        **(
            {"terminal_seed_resolution": graph.manifest.get("terminal_seed_resolution")}
            if graph.manifest.get("terminal_seed_resolution") is not None
            else {}
        ),
        "gt_attribution": gt_block,
        "component_length_weighted_fraction": (
            (graph.manifest.get("component_policy") or {}).get(
                "outfall_terminating_observed_length_fraction"
            )
        ),
        "g2": {
            "verdict": g2_verdict_text,
            "reason": g2_reason,
            "anti_vacuity_fail": _g2_anti_vacuity_fail(ledger, cfg.diagnostics.g2_min_returned_m3),
            "anti_vacuity_inputs_recorded": {
                "total_surcharging_steps": ledger.total_surcharging_steps,
                "total_returned_m3": returned,
            },
        },
        "kill_thresholds": {
            "K1": {
                "policy": "halt when coupled steps > 2x uncoupled-equivalent over same window",
                "min_fraction_of_uncoupled": cfg.dt_policy.min_fraction_of_uncoupled,
                "fired": k1_fired,
                "coupled_steps": steps,
                "uncoupled_equivalent_steps_at_final_tau": n_unc_final,
                "halt_reason": halt_reason,
                "uncoupled_twin": twin,
            },
            "K2": {
                "recorded": True,
                "cap_binding_steps": ledger.cap_binding_steps,
                "fraction_of_steps": (ledger.cap_binding_steps / steps) if steps else 0.0,
            },
            "K3": {
                "recorded": True,
                "returned_over_captured": ratio_k3,
                "storage_absorption_tell_fired": storage_tell_fired,
                "tell_definition": (
                    "returned/captured < 0.05 over the full window while captured > 0.01*rain_in "
                    "=> declared shaft proxy eating volume => prismatic-v2 adjudication request, "
                    "NEVER tuning"
                ),
            },
            "K4": {
                "status": "computed_by_diagnostics_unit",
                "tell": "OVERSHOOT: returned->captured inversion with depths rising above bands",
            },
        },
        "indirect_cfl_monitor": monitor.as_dict(),
        "deviations_notice": dict(DEVIATIONS_NOTICE),
        "mass_host_budget_verbatim": budget.as_dict(),
        "coupling_ledger": ledger_dict,
        "dt_schedule": controller.compress(),
        "initial_node_state_at_rest": initial_rest,
        "steps": steps,
        "sim_time_s": sim_time,
        "wall_clock_s": wall_clock,
        "projected_wall_clock_min_at_probe": projected_wall_min,
        "depth_series_dir": str(cfg.outputs.depth_series_dir),
    }
    if extra:
        fields.update(extra)
    return fields


@app.command()
def run(
    config: Path = typer.Option(
        Path("configs/coupling.yaml"), "--config", help="Coupling YAML (rule-7 resolved)"
    ),
    smoke: bool = typer.Option(
        False, "--smoke", help="Smoke envelope from the config's smoke block"
    ),
) -> None:
    repo_root = REPO
    try:
        cfg = resolve_config(config, repo_root)
    except ConfigError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    solver_cfg = load_solver_config(cfg.solver_config, repo_root)
    with open(repo_root / solver_cfg["compute_config"]) as f:
        solver_cfg["_compute"] = _yaml_safe_load(f)

    storm = solver_cfg["storm"]
    if smoke:
        duration = cfg.smoke.duration_s
        max_steps = cfg.smoke.max_steps
    else:
        duration = float(storm["simulation_duration_s"])
        max_steps = 2_000_000
    rain = uniform_storm(float(storm["total_mm"]), float(storm["duration_s"]))

    try:
        res = simulate_coupled(
            cfg,
            solver_cfg,
            repo_root,
            rain=rain,
            duration_s=duration,
            max_steps=max_steps,
            snapshot_every_s=cfg.outputs.write_every_s,
            smoke=smoke,
        )
    except RefuseLoadError as exc:

        if getattr(exc, "source", None) == FROZEN_READER_SOURCE:
            typer.echo(f"REFUSED (D-G seam): {exc}")
        else:
            typer.echo(f"REFUSED: {exc}")
        raise typer.Exit(code=1) from exc
    except KillThresholdHalt as exc:
        typer.echo(f"K1 HALT: {exc}")
        raise typer.Exit(code=1) from exc
    except ComputeBudgetExceeded as exc:
        typer.echo(f"COMPUTE BUDGET: {exc}")
        raise typer.Exit(code=1) from exc
    except CouplingMassBreach as exc:
        typer.echo(f"MASS BREACH: {exc}")
        raise typer.Exit(code=1) from exc

    led = res.ledger

    vacuous = res.g2_anti_vacuity_fail
    if vacuous:
        typer.echo("G2 FAIL: NO NODE EVER SURCHARGED (anti-vacuity; verdict owned by diagnostics)")
    typer.echo(f"steps={res.steps:,} sim={res.sim_time_s:,.0f}s wall={res.wall_clock_s:,.1f}s")
    typer.echo(
        f"captured_to_drains_m3={led.captured_to_drains_m3!r} "
        f"surcharge_returned_m3={led.surcharge_returned_m3!r} "
        f"drain_out_net_m3={led.drain_out_net_m3!r} legacy_drain_out_m3={led.legacy_drain_out_m3!r}"
    )
    typer.echo(
        f"relative_residual(contract)={led.contract_residual():.6e} "
        f"total_water_relative_residual(JUDGED)={led.total_water_relative_residual():.6e} "
        f"(bar {cfg.budget.judged_bar_relative:.1e}, reference "
        f"{cfg.budget.realized_reference_residual:.2e})"
    )
    typer.echo(f"wrote {cfg.outputs.manifest}")
    if vacuous:
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
