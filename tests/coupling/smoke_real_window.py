"""WF-2 RE-SMOKE — FULL-DOMAIN production-path coupled smoke driver (CPU-only).

Runnable directly: ``.venv/bin/python tests/coupling/smoke_real_window.py
--config <fresh coupling yaml>``. ``--config`` is REQUIRED with no default
(round-3 fix D4): the old default pointed at the FROZEN 53-leaf snapshot
``runs/wf2_coupled_resmoke/coupling_resmoke.yaml``, which now REFUSES rule-7
resolution (missing keys) after the schema grew — pointing new runs at stale
frozen snapshots is exactly the moved-baseline trap V3 bans. Instantiate a
FRESH yaml instead: copy ``configs/coupling.yaml`` and override only the
``outputs.*`` dirs (+ smoke envelope if wanted), precedent:
``scripts/wf2_tile_throughput.py::_instantiate_tile_yaml_text`` or
``scripts/wf2_resmoke_window.py``'s original instantiation.

WHAT CHANGED VS THE OLD DRIVER (V6 flip recorded here): contract v1.2.0 closed
the D-G seam, so the retired declared-deviation byte-level assembly is GONE —
this driver injects NOTHING and calls :func:`simulate_coupled` directly with
``graph=None``, ``static=None``, ``window=None``: the PRODUCTION path
``router.load_drain_graph -> frozen read_artefact`` over the FULL buffered grid
(1721 nodes / 1587 edges, all 116 pre-registered predicted targets active,
1208 capacity-bearing edges routed including every outfall-terminating
component). The old 128x128 window reached NO outfall and could never answer
directive 2; full domain routes 100% of the graph, which is the chosen scope.

STAGE-BY-STAGE REPORTING (config/budget/load/transformation/forcing/loop/
diagnostics/manifest): config + wall-budget stages print BEFORE the run;
everything after the run prints FROM THE REALIZED MANIFEST BYTES AND PRODUCTS
ON DISK (V1) — the graph/domain/transformation/forcing stages happen inside
``simulate_coupled`` and are read back from what IT recorded, never re-declared.

WALL BUDGET FIRST (CLAUDE.md §Compute): the pre-run gate prints the MEASURED
full-domain rate (~1.1 s/step acc+couple, production-path probe 2026-08-26;
couple component cross-checked against runs/wf2_fullgraph_cost/manifest.json),
an ANALYTIC fail-safe step band, and a PROXY step band from the previous
windowed run's REALIZED dt schedule (labelled proxy, never presented as a
measurement of THIS domain). If the projected wall exceeds
``smoke.cpu_budget_wall_clock_min`` the driver refuses to start. If realized dt
decays harder than projected and ``smoke.max_steps`` is reached first,
``simulate_coupled`` returns normally with ``sim_time_s < duration_s`` and this
driver reports PARTIAL — honesty over completion.

GT DEGRADE SEMANTICS PRESERVED (TestSmokeCliGtDegrade): the top-level
``gt_attribution`` block is computed by the driver library at terminal time
inside ``simulate_coupled``; an unavailable GT bundle degrades to NOT_ASSESSED
with the verbatim error, this driver prints the WARNING line, and the exit code
comes from the typed gates only (anti-vacuity included).
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

import typer
from yaml import safe_load as _yaml_safe_load

from jaladhar.coupling.config import CouplingConfig, resolve_config
from jaladhar.coupling.diagnostics import (
    compare_falsifier,
    load_falsifier_set,
    sha256_file,
)
from jaladhar.coupling.ledger import CouplingMassBreach
from jaladhar.coupling.router import RefuseLoadError
from jaladhar.coupling.solver_hook import (
    ComputeBudgetExceeded,
    KillThresholdHalt,
    simulate_coupled,
)
from jaladhar.provenance import write_json_atomic
from jaladhar.solver.run import estimate_steps_band, uniform_storm
from jaladhar.solver.state import load_solver_config

app = typer.Typer(add_completion=False)

REPO = Path(__file__).resolve().parents[2]

# --- measured-rate provenance (rule 3: every number names its file) --------
# Direct full-domain acc+couple rate from the production-path probe
# (test_real_artefact_loads_production_path_past_former_d_g_seam, 2026-08-26):
# ~1.1 s/step on the 3521x3615 grid. The couple COMPONENT is separately
# measured and logged in runs/wf2_fullgraph_cost/manifest.json (143.15 ms/step
# mean); the twin (uncoupled acc-only) rate below is DERIVED as the difference.
MEASURED_FULLDOMAIN_ACC_COUPLE_S_PER_STEP = 1.1
MEASURED_RATE_SOURCE = (
    "production-path probe 2026-08-26 "
    "(test_real_artefact_loads_production_path_past_former_d_g_seam context; "
    "~1.1 s/step acc+couple on the 3521x3615 grid)"
)
FULLGRAPH_COST_MANIFEST = REPO / "runs" / "wf2_fullgraph_cost" / "manifest.json"
OLD_WINDOW_MANIFEST = REPO / "runs" / "wf2_coupled_smoke" / "manifest.json"


def _proxy_steps_for(duration_s: float, runs_rle: list[list[float]]) -> int | None:
    """Steps the OLD WINDOW's realized dt schedule would need to cover duration_s.

    A PROXY for step-count planning ONLY — the full domain's h_max extremes are
    deeper, so this is an OPTIMISTIC end of the band, declared as such wherever
    it is printed. Returns None when the schedule cannot cover duration_s.
    """
    t = 0.0
    n = 0
    for dt, run_len in runs_rle:
        for _ in range(int(run_len)):
            t += float(dt)
            n += 1
            if t >= duration_s:
                return n
    return None


def _measured_couple_ms() -> float | None:
    """Full-graph couple_step mean ms/step from the logged cost run (or None)."""
    try:
        man = json.loads(FULLGRAPH_COST_MANIFEST.read_text())
        return float(man["fullgraph_couple_ms_per_step"]["mean"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def _print_budget_gate(
    cfg: CouplingConfig,
    solver_cfg: dict[str, Any],
    duration: float,
    max_steps: int,
) -> bool:
    """Print the wall-budget arithmetic BEFORE the run; True => PROCEED."""
    cap_min = float(cfg.smoke.cpu_budget_wall_clock_min)
    couple_ms = _measured_couple_ms()
    twin_rate_s = MEASURED_FULLDOMAIN_ACC_COUPLE_S_PER_STEP - (
        (couple_ms / 1000.0) if couple_ms is not None else 0.145
    )
    pair_rate_s = MEASURED_FULLDOMAIN_ACC_COUPLE_S_PER_STEP + twin_rate_s

    band = tuple(solver_cfg.get("compute_estimate", {}).get("h_max_band_m", (1.0, 30.0)))
    n_lo, n_hi = estimate_steps_band(solver_cfg, duration, band)

    proxy_steps = None
    proxy_note = "proxy source unavailable"
    try:
        old_man = json.loads(OLD_WINDOW_MANIFEST.read_text())
        rle = old_man["dt_schedule"]["runs_rle"]
        proxy_steps = _proxy_steps_for(duration, rle)
        proxy_note = f"{OLD_WINDOW_MANIFEST.relative_to(REPO)} dt_schedule (old 128x128 window)"
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        proxy_steps = None

    # Expected band: proxy x2 for the full domain's deeper h_max extremes
    # (declared worsening factor, not a measurement). Analytic hi is the
    # fail-safe ceiling (deepest h_max end), NOT the expectation.
    exp_lo = proxy_steps if proxy_steps is not None else n_lo
    exp_hi = max(exp_lo * 2, n_lo)
    proj_lo_min = exp_lo * pair_rate_s / 60.0
    proj_hi_min = exp_hi * pair_rate_s / 60.0
    failsafe_min = n_hi * pair_rate_s / 60.0
    max_steps_bound_min = max_steps * pair_rate_s / 60.0

    typer.echo(
        "STAGE compute_budget (BEFORE run, CLAUDE.md §Compute):\n"
        f"  cells=3521x3615={3521 * 3615:,} (full buffered domain, no window)\n"
        f"  measured rate: {MEASURED_FULLDOMAIN_ACC_COUPLE_S_PER_STEP} s/step acc+couple "
        f"[{MEASURED_RATE_SOURCE}]; couple component "
        f"{couple_ms if couple_ms is not None else 'n/a'} ms "
        "(runs/wf2_fullgraph_cost/manifest.json, 25-rep mean); twin acc-only "
        f"{twin_rate_s:.3f} s/step (DERIVED difference) -> {pair_rate_s:.3f} s per "
        "simulated step pair (twin + coupled)\n"
        f"  duration_s={duration} -> ANALYTIC fail-safe band [{n_lo}, {n_hi}] steps "
        f"(h_max_band {band}); wall at fail-safe TOP {failsafe_min:.1f} min\n"
        f"  PROXY band from previous realized schedule: {proxy_steps} steps ({proxy_note}; "
        f"optimistic end) x2 full-domain worsening -> [{exp_lo}, {exp_hi}] steps -> "
        f"projected wall {proj_lo_min:.1f}-{proj_hi_min:.1f} min\n"
        f"  max_steps={max_steps} bounds the loop at the measured rate to "
        f"{max_steps_bound_min:.1f} min IF reached => clean PARTIAL report, not a silent stop\n"
        f"  cap smoke.cpu_budget_wall_clock_min={cap_min} min; §14 step-5 probe armed"
    )
    if min(proj_lo_min, failsafe_min if proxy_steps is None else proj_lo_min) > cap_min:
        typer.echo(
            f"COMPUTE BUDGET: even the OPTIMISTIC projection {proj_lo_min:.1f} min exceeds "
            f"cap {cap_min:.1f} min — refusing to start"
        )
        return False
    if proj_hi_min > cap_min:
        typer.echo(
            f"WALL-BUDGET RISK DECLARED: upper expected band {proj_hi_min:.1f} min exceeds cap "
            f"{cap_min:.1f} min — proceeding with the §14 probe, the max_steps checkpoint and "
            "the PARTIAL-report protocol (honesty over completion)"
        )
    else:
        typer.echo(
            f"COMPUTE BUDGET: projected {proj_lo_min:.1f}-{proj_hi_min:.1f} min <= cap "
            f"{cap_min:.1f} min: PROCEED"
        )
    return True


def _read_events_csv(path: Path) -> list[dict[str, Any]]:
    """Realized surcharge events from disk (schema-exact G2 header required)."""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows = [
            {
                "node_id": int(r["node_id"]),
                "total_returned_m3": float(r["total_returned_m3"]),
                "first_step": int(r["first_step"]),
                "last_step": int(r["last_step"]),
                "max_head_m": float(r["max_head_m"]),
            }
            for r in reader
        ]
    return rows


def _partial_report(manifest_path: Path, exc: BaseException) -> None:
    """Report a halt/refusal FROM THE REALIZED TERMINAL MANIFEST BYTES, then exit."""
    typer.echo(f"RUN STOPPED: {type(exc).__name__}: {exc}")
    try:
        man = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as read_exc:
        typer.echo(f"PARTIAL REPORT UNAVAILABLE: manifest unreadable ({read_exc})")
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"PARTIAL (manifest status={man.get('status')!r}): steps={man.get('steps')} "
        f"sim_time_s={man.get('sim_time_s')} wall_clock_s={man.get('wall_clock_s')} "
        f"cells={man.get('cells')} grid_shape={man.get('grid_shape')}"
    )
    led = man.get("coupling_ledger") or {}
    typer.echo(
        f"PARTIAL budget lines: legacy_drain_out_m3={led.get('legacy_drain_out_m3')!r} "
        f"captured_to_drains_m3={led.get('captured_to_drains_m3')!r} "
        f"surcharge_returned_m3={led.get('surcharge_returned_m3')!r}"
    )
    raise typer.Exit(code=1) from exc


def _require_config(value: Path | None) -> Path:
    """D4 (round 3): --config carries NO default. The old default was the frozen
    53-leaf resmoke snapshot, which refuses rule-7 resolution now that the schema
    has grown — a stale-snapshot invocation must fail HERE with instructions,
    not three minutes into a run."""
    if value is None:
        raise typer.BadParameter(
            "--config is REQUIRED (no default). The previous default pointed at the frozen "
            "53-leaf snapshot runs/wf2_coupled_resmoke/coupling_resmoke.yaml, which predates "
            "the coupling schema growth and now REFUSES rule-7 resolution with missing-key "
            "errors. Instantiate a fresh yaml instead: copy configs/coupling.yaml and override "
            "only outputs.* (+ smoke envelope if wanted); precedent: "
            "scripts/wf2_tile_throughput.py::_instantiate_tile_yaml_text."
        )
    return value


@app.command()
def main(
    config: Path = typer.Option(
        None,
        "--config",
        callback=_require_config,
        help="Coupling YAML (rule-7 resolved). REQUIRED — no frozen-snapshot default.",
    ),
    out_dir: Path = typer.Option(Path("runs/wf2_coupled_resmoke"), "--out-dir"),
    max_steps: int | None = typer.Option(
        None, "--max-steps", help="Override smoke.max_steps (test envelope bound)"
    ),
    duration_s: float | None = typer.Option(
        None, "--duration-s", help="Override smoke.duration_s (test envelope bound)"
    ),
) -> None:
    """WF-2 re-smoke: FULL-DOMAIN production-path coupled run (CPU-only, V12)."""
    repo = REPO
    t_all = time.perf_counter()

    # ---- STAGE config (rule-7 resolver; outputs redirected to --out-dir) -----
    cfg_resolved = resolve_config(config, repo)
    od = Path(out_dir)
    smoke_overrides: dict[str, Any] = {}
    if max_steps is not None:
        smoke_overrides["max_steps"] = int(max_steps)
    if duration_s is not None:
        smoke_overrides["duration_s"] = float(duration_s)
    smoke_params = (
        dc_replace(cfg_resolved.smoke, **smoke_overrides) if smoke_overrides else cfg_resolved.smoke
    )
    cfg = dc_replace(
        cfg_resolved,
        smoke=smoke_params,
        outputs=dc_replace(
            cfg_resolved.outputs,
            run_dir=od,
            manifest=od / "manifest.json",
            surcharge_events_csv=od / "products" / "surcharge_events.csv",
            event_continuity_csv=od / "products" / "event_continuity.csv",
            depth_series_dir=od / "depth",
        ),
    )
    typer.echo(
        f"STAGE config: resolve_config OK config={config} device={cfg.device} "
        f"enabled={cfg.enabled} legacy_sink_replacement={cfg.legacy_sink_replacement} "
        f"version={cfg.coupling_version}; outputs redirected to {od}; "
        f"envelope duration_s={cfg.smoke.duration_s} max_steps={cfg.smoke.max_steps} "
        f"cap_min={cfg.smoke.cpu_budget_wall_clock_min}"
    )

    solver_cfg = load_solver_config(cfg.solver_config, repo)
    with open(repo / solver_cfg["compute_config"]) as f:
        solver_cfg["_compute"] = _yaml_safe_load(f)
    storm = solver_cfg["storm"]
    duration = float(cfg.smoke.duration_s)

    # ---- STAGE wall budget (printed BEFORE any simulation work) --------------
    if not _print_budget_gate(cfg, solver_cfg, duration, int(cfg.smoke.max_steps)):
        raise typer.Exit(code=1)

    # ---- STAGES load/transformation/forcing/loop: PRODUCTION PATH ------------
    # graph=None + static=None + window=None => router.load_drain_graph(full
    # domain) -> load_domain(full buffered grid) -> guards -> uncoupled twin ->
    # coupled loop, exactly as the production CLI drives it. NO injection.
    try:
        res = simulate_coupled(
            cfg,
            solver_cfg,
            repo,
            rain=uniform_storm(float(storm["total_mm"]), float(storm["duration_s"])),
            duration_s=duration,
            max_steps=int(cfg.smoke.max_steps),
            snapshot_every_s=float(cfg.outputs.write_every_s),
            smoke=True,
        )
    except RefuseLoadError as exc:
        _partial_report(cfg.outputs.manifest, exc)
    except KillThresholdHalt as exc:
        _partial_report(cfg.outputs.manifest, exc)
    except ComputeBudgetExceeded as exc:
        _partial_report(cfg.outputs.manifest, exc)
    except CouplingMassBreach as exc:
        _partial_report(cfg.outputs.manifest, exc)

    # ---- STAGE report: everything below reads REALIZED bytes -----------------
    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "completed", f"terminal status {man['status']!r} != completed"
    assert man.get("start_time"), "manifest lost its rule-6 start_time"
    wall = time.perf_counter() - t_all

    steps_realized = int(res.steps)
    twin_wall_s = float(res.twin_summary["wall_clock_s"])
    # Loop-side mean includes input assembly + product writes (DERIVATION STATED);
    # it is the realized number THIS run produced, not the pre-run constant.
    loop_side_s = res.wall_clock_s - twin_wall_s
    realized_ms_per_step = (loop_side_s * 1000.0 / steps_realized) if steps_realized else 0.0
    twin_ms_per_step = (
        (twin_wall_s * 1000.0 / max(int(res.twin_summary["steps"]), 1)) if twin_wall_s else 0.0
    )

    sched = res.controller.schedule
    dt_min, dt_max = (min(sched), max(sched)) if sched else (0.0, 0.0)

    typer.echo("\n================ WF-2 RE-SMOKE REPORT (FULL DOMAIN, realized) ================")
    typer.echo(
        f"STAGE load: production path (no injection) nodes={res.graph.num_nodes} "
        f"edges={res.graph.num_edges} routed_cb_edges={res.graph.plan.edges_routed} "
        f"active_predicted_targets={res.graph.n_active_predicted_targets}/116 "
        f"inactive={res.graph.n_inactive_predicted_targets} "
        f"component_split={man['component_class_split']}"
    )
    typer.echo(
        f"STAGE transformation: guard(a) zeroed OUT-OF-PLACE "
        f"(legacy_drain_disabled={man['legacy_drain_disabled']}) | "
        f"STAGE forcing: uniform_design_storm({storm['total_mm']} mm / "
        f"{storm['duration_s']} s rain window) = DECLARED SYNTHETIC on real terrain; "
        f"simulated window covers its FIRST {res.sim_time_s:,.0f} s"
    )
    partial = res.sim_time_s + 1e-9 < duration
    partial_label = "PARTIAL — max_steps checkpoint reached" if partial else "duration reached"
    typer.echo(
        f"STAGE loop: steps={steps_realized:,} sim_time={res.sim_time_s:,.0f}s of "
        f"{duration:,.0f}s requested ({partial_label}) "
        f"| dt range realized [{dt_min:.3f}, {dt_max:.3f}] s | rejected_steps="
        f"{res.rejected_steps} retry_attempts={res.retry_attempts} "
        f"max_courant={res.max_courant:.3f}"
    )
    typer.echo(
        f"STAGE wall: total_driver={wall:,.1f}s run_wall={res.wall_clock_s:,.1f}s "
        f"(twin {twin_wall_s:,.1f}s + loop-side {loop_side_s:,.1f}s incl. assembly+writes) "
        f"=> realized loop-side {realized_ms_per_step:.1f} ms/step "
        f"(pre-run measured basis {MEASURED_FULLDOMAIN_ACC_COUPLE_S_PER_STEP * 1000:.0f} ms); "
        f"twin realized {twin_ms_per_step:.1f} ms/step"
    )

    # ---- budget lines (guard c: legacy sink EXACTLY zero) --------------------
    led = res.ledger
    assert (
        led.legacy_drain_out_m3 == 0.0
    ), f"guard (c) violated: legacy_drain_out_m3={led.legacy_drain_out_m3!r} != 0.0"
    tw = led.total_water_relative_residual()
    typer.echo(f"legacy_drain_out_m3   = {led.legacy_drain_out_m3!r}  (must be 0.0 EXACTLY)")
    typer.echo(f"captured_to_drains_m3 = {led.captured_to_drains_m3!r}")
    typer.echo(f"surcharge_returned_m3 = {led.surcharge_returned_m3!r}")
    typer.echo(f"drain_out_net_m3 (D-E)= {led.drain_out_net_m3!r}")
    typer.echo(
        f"total_water_relative_residual(JUDGED) = {tw:.6e} "
        f"(hard bar {cfg.budget.judged_bar_relative:.1e}; replay#2 reference "
        f"{cfg.budget.realized_reference_residual:.2e})"
    )

    # ---- surcharge + directive-2 split (from manifest + realized products) ---
    events = _read_events_csv(cfg.outputs.surcharge_events_csv)
    any_surcharge = led.total_surcharging_steps > 0 and len(events) > 0
    max_head = max((e["max_head_m"] for e in events), default=0.0)
    typer.echo(
        f"SURCHARGE: any={any_surcharge} surcharging_steps={led.total_surcharging_steps} "
        f"events={len(events)} (products CSV rows realized) "
        f"total_returned_m3={led.surcharge_returned_m3!r} max_event_head_m={max_head:.4f} "
        f"(freeboard activation 1.5 m ‡)"
    )
    _ccs = man.get("component_class_split") or {}
    typer.echo(
        "DIRECTIVE-2 SPLIT BASIS NOTE: the manifest component_class_split above is NODE-COUNT "
        f"basis (outfall_terminating/dead_end = {_ccs.get('outfall_terminating')}/"
        f"{_ccs.get('dead_end')} as realized by THIS run's terminal definition); the "
        f"EVENT/VOLUME split by class lands beside the products in "
        f"{Path(cfg.outputs.surcharge_events_csv).parent} via "
        "scripts/wf2_return_ratio_split.py (independent adjacency reachability join)"
    )

    # ---- diagnostics stage: library functions on realized artefacts ----------
    fs = load_falsifier_set(cfg.diagnostics.falsifier_set)
    cmp_block = compare_falsifier(cfg.outputs.surcharge_events_csv, fs, sha256_file(cfg.graph.gpkg))
    gt_block = man["gt_attribution"]
    if gt_block.get("error_type") is not None:
        typer.echo(
            f"WARNING: gt_attribution UNAVAILABLE ({gt_block.get('error_type')}: "
            f"{gt_block.get('error')}) — recorded NOT_ASSESSED; no attribution is fabricated"
        )
    typer.echo(
        f"FALSIFIER COMPARISON (as realized, FULL DOMAIN — all 116 targets were observable): "
        f"predicted_nodes_surcharged={cmp_block['predicted_nodes_surcharged']} "
        f"of {cmp_block['n_predicted_nodes']} registered; flagged_edges_downstream_surcharged="
        f"{cmp_block['flagged_edges_whose_downstream_node_surcharged']} "
        f"of {cmp_block['n_predicted_edges']}; "
        f"unflagged_surcharging_nodes={len(cmp_block['unflagged_surcharging_nodes'])}; "
        f"sha_crosscheck={cmp_block['source_gpkg_sha256_crosscheck']['status']}"
    )
    kt = man["kill_thresholds"]
    k3 = kt["K3"]["returned_over_captured"]
    typer.echo(
        f"K statuses: K1 fired={kt['K1']['fired']} (coupled {kt['K1']['coupled_steps']} vs "
        f"uncoupled-equivalent {kt['K1']['uncoupled_equivalent_steps_at_final_tau']}) | "
        f"K2 recorded={kt['K2']['recorded']} cap_binding_fraction="
        f"{kt['K2']['fraction_of_steps']:.4f} | K3 recorded={kt['K3']['recorded']} "
        f"returned/captured={k3 if k3 is not None else 'n/a'} "
        f"storage_absorption_tell={kt['K3']['storage_absorption_tell_fired']} | "
        f"K4 {kt['K4']['status']}"
    )
    typer.echo(
        f"G2 VERDICT (library, realized bytes): {man['g2']['verdict'].upper()} — "
        f"{man['g2']['reason']}"
    )

    # ---- EXTRAPOLATION-FROM-MEASURED band for a 3h full-domain run -----------
    # Directive 3: labelled an extrapolation everywhere it appears; arithmetic
    # uses THIS RUN'S realized loop-side and twin rates, not assumptions.
    steps_3h_lo = int(-(-10800 // max(dt_max, 1e-9)))
    steps_3h_hi = int(-(-10800 // max(dt_min, 1e-9)))
    rate_pair_ms = realized_ms_per_step + twin_ms_per_step
    wall_3h_lo_min = steps_3h_lo * rate_pair_ms / 1000.0 / 60.0
    wall_3h_hi_min = steps_3h_hi * rate_pair_ms / 1000.0 / 60.0
    extrap = {
        "label": "EXTRAPOLATION-FROM-MEASURED (never a measurement)",
        "basis": (
            f"THIS run's realized loop-side {realized_ms_per_step:.1f} ms/step (incl. assembly/"
            f"writes — conservative) + twin {twin_ms_per_step:.1f} ms/step; realized dt range "
            f"[{dt_min:.3f}, {dt_max:.3f}] s treated as the schedule's floor/ceiling"
        ),
        "arithmetic": (
            f"steps(3h=10800s) = ceil(10800/dt) over realized dt range -> "
            f"[{steps_3h_lo}, {steps_3h_hi}] steps; wall = steps x "
            f"({realized_ms_per_step:.1f}+{twin_ms_per_step:.1f}) ms / 1000 / 60"
        ),
        "wall_clock_hours_band": [
            wall_3h_lo_min / 60.0,
            wall_3h_hi_min / 60.0,
        ],
        "scope_warning": (
            "dt on a 3h window may decay BELOW this run's realized minimum (deeper ponding), "
            "so the true cost can exceed the band's top; the band inherits the 2h-storm-first-"
            "window dynamics only"
        ),
    }
    typer.echo(
        f"EXTRAPOLATION-FROM-MEASURED full-domain 3h: steps_band=[{steps_3h_lo}, {steps_3h_hi}] "
        f"-> wall {wall_3h_lo_min / 60.0:.2f}-{wall_3h_hi_min / 60.0:.2f} h "
        "(band; see scope_warning in manifest)"
    )

    # ---- STAGE manifest: merge the driver report IN PLACE (rule 6) -----------
    current = json.loads(cfg.outputs.manifest.read_text())
    merged = {
        **current,
        "diagnostics": {
            "owner": "jaladhar.coupling.diagnostics (library functions, invoked not reimplemented)",
            "falsifier_comparison_full": cmp_block,
            "gt_attribution_pointer": (
                "top-level 'gt_attribution' (driver-emitted at terminal time)"
            ),
        },
        "resmoke_driver_report": {
            "scope": "FULL DOMAIN production path (no window, no injection); CPU-only",
            "wall_clock_driver_total_s": wall,
            "run_wall_clock_s": res.wall_clock_s,
            "twin_wall_clock_s": twin_wall_s,
            "loop_side_wall_clock_s_incl_assembly_writes": loop_side_s,
            "realized_loop_side_ms_per_step": realized_ms_per_step,
            "realized_twin_ms_per_step": twin_ms_per_step,
            "realized_dt_range_s": [dt_min, dt_max],
            "rejected_steps": res.rejected_steps,
            "retry_attempts": res.retry_attempts,
            "max_courant_realized": res.max_courant,
            "partial_window": partial,
            "pre_run_measured_basis": {
                "acc_couple_s_per_step": MEASURED_FULLDOMAIN_ACC_COUPLE_S_PER_STEP,
                "source": MEASURED_RATE_SOURCE,
                "couple_component_ms_from": str(FULLGRAPH_COST_MANIFEST),
            },
            "extrapolation_from_measured_full_domain_3h": extrap,
        },
    }
    write_json_atomic(cfg.outputs.manifest, merged)
    typer.echo(
        f"MANIFEST LIFECYCLE: status='{merged['status']}' start_time={merged['start_time']} "
        f"(driver report merged IN PLACE) -> {cfg.outputs.manifest}"
    )

    if not any_surcharge:
        typer.echo("HEADLINE: NO NODE EVER SURCHARGED (anti-vacuity §11.1)")
    if res.g2_anti_vacuity_fail:
        typer.echo("G2 FAIL: anti-vacuity condition met (no surcharge / returned below floor)")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
