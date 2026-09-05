"""Invariant #9 ``uncoupled-equivalence`` — WF-2 coupling (spec §12 row #9, §2, §5, §9.1-9.2).

V2 INDEPENDENT OBSERVABLE — if this were broken I would observe two runs of the
SAME problem definition (same window, same forcing, same initial pond, same
REAL non-zero drain capacities) differing in a REALIZED quantity despite
coupling being off: final h NOT bitwise equal (``torch.equal`` False), step
counts unequal, dt schedules diverging elementwise, or host MassBudget dicts
disagreeing on any accumulator. The observable is produced by the STOCK driver
(``jaladhar.solver.run.simulate``) around a full exercise of the coupled
machinery — never by the coupled code reporting on itself.

Falsifiable claim (spec §12 #9, verbatim): "With capture disabled end-to-end
(coupling off) and REAL capacities restored, behaviour is bit-for-bit the stock
uncoupled result — the coupled code path altered nothing. Degenerate case is
capture-off, NOT capacity-zero: zero supply would make every node surcharge
instantly, provably not the uncoupled result (and unreachable from the raster
anyway [C])."

Realized routing shape (read from the tree, 2026-08-26 — stated so nothing here
is read as a silent reinterpretation): there is NO route-around branch inside
the coupled driver. ``simulate_coupled`` REFUSES ``enabled != True`` at entry
(``solver_hook.py:452-456``) with a message that itself declares "enabled=false
means the stock solver path, byte-identical behaviour" — refusal IS the
routing mechanism of spec §5's "false => stock solver path, byte-identical
behaviour". The equivalence is therefore observed as three realized facts:

1. ROUTING: an ``enabled=False`` config never executes ONE line of coupled work
   — ValueError raised BEFORE the rule-7 preflight and BEFORE the rule-6
   start-manifest write, so no manifest, no run dir, zero coupled side effects.
2. BIT-FOR-BIT: the stock driver run TWICE on one SHARED ``StaticFields``
   instance with live capacities — pristine vs after the ENTIRE coupled
   machinery has exercised itself against that same instance (out-of-place
   zeroing, dead-knob parameter views, LIVE ``couple_step`` exchange calls,
   a full ``simulate_coupled`` micro-run receiving the shared instance) —
   produces bitwise-equal h/qx/qy, equal step counts, elementwise-equal dt
   schedules and equal host budget dicts. Any in-place leak from the coupled
   path onto the shared fields or global interpreter state would move run B's
   bits; none does.
3. GUARD (d), false-path side: ``StaticFields.parameters()`` still exposes
   ``drain_cap_m_s`` with its ORIGINAL values after everything above (the off
   path's calibration view is the untouched original), while the coupled-mode
   view removes exactly that key and shares storage for every other.

V5 RED DEMO — task-named mutation, applied to a /tmp COPY ONLY of
``solver_hook.py`` (repo file byte-untouched, verified by re-read): the
``enabled=False`` guard's raise is replaced with a fall-through so the DISABLED
branch engages the coupled loop anyway — capture LIVE, legacy capacities ZEROED.
That mutant's trajectory over the same problem definition diverges from the
stock-with-real-capacities reference: ``torch.equal`` FAILS. The demo runs
in-suite against the copy imported from ``tmp_path`` and asserts the divergence
is detected (i.e. the comparator bites); verbatim numbers are printed. The same
run documents WHY capacity-zero is not the uncoupled result: the mutant's
manifest carries ``legacy_drain_disabled: true`` while the stock reference's
budget shows strictly positive legacy ``drain_out_m3`` — different sinks,
provably different physics.

V7 SCOPE — realized: 2 x <=200-step stock runs over a 32x32 = 1024-cell closed
declared-synthetic domain, uniform design storm, 0.02 m initial pond, drain
capacity 3.2e-6 m/s on EVERY cell (live prior, synthetic-declared at toy scale);
one 3-step coupled-driver micro-run with capture demonstrably firing
(captured_to_drains_m3 > 0) between them; one refusal path; structural guard-d
checks; red demo ~6 steps x 1024 cells. Scope claimed: the invariant-#9 seam at
toy scale — enabled=false executes zero coupled work, and coupled-path
execution cannot perturb a subsequent uncoupled run's realized bits on shared
inputs, single process, CPU only. Gaps, stated honestly:
(a) PARTIAL vs city scale — the production-graph bit-for-bit replay stays
    BLOCKED upstream: the frozen WF-1 reader refuses the only realized drain
    graph artefact (consumer_assertion_3, zero_length_dropped_count=42), so a
    real-graph / post-GPU replay companion cannot run yet; it closes when D-G
    closes.
(b) Bit identity is claimed WITHIN this process and device (CPU determinism
    for identical op sequences); cross-process/cross-device bit equality is
    NOT claimed by this file.
(c) The toy field values are DECLARED-SYNTHETIC (rule 1: labelled as such);
    real raster capacities enter only through the blocked (a) tier.

Fixtures style reused from tests/coupling/test_solver_hook.py (declared-
synthetic flat closed domain + chain DAG via ``build_drain_graph``); the
invariant's own envelope constants live in this file only.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from dataclasses import replace as dc_replace
from pathlib import Path

import pytest
import torch

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.exchange import (
    build_node_state,
    couple_step,
    zero_drain_cap_out_of_place,
)
from jaladhar.coupling.router import build_drain_graph
from jaladhar.coupling.solver_hook import (
    coupled_parameter_set,
    simulate_coupled,
)
from jaladhar.solver.acc import SolverParams
from jaladhar.solver.mass import MassBudget
from jaladhar.solver.run import simulate as stock_simulate
from jaladhar.solver.run import uniform_storm
from jaladhar.solver.state import StaticFields, load_solver_config
from jaladhar.solver.timestep import TimestepController

REPO = Path(__file__).resolve().parents[2]

# --- invariant envelope (toy scale, declared) ---------------------------------
SHAPE = (32, 32)
N_NODES = 4
DURATION_S = 2400.0
MAX_STEPS = 200
STORM_MM = 110.0
STORM_DURATION_S = 7200.0
H0_M = 0.02  # ponded start so capture is LIVE within the first coupled steps
RAIN = uniform_storm(STORM_MM, STORM_DURATION_S)

# red-demo window: short enough that K1 (>=4 evaluated steps AND >2x twin) can
# neither fire nor matter, long enough that both sides finish past step 1.
RED_DURATION_S = 60.0
RED_MAX_STEPS = 50


# ---------------------------------------------------------------------------
# Fixtures (declared-synthetic; NOT Bengaluru data)
# ---------------------------------------------------------------------------


def _static(shape: tuple[int, int], *, drain_cap_m_s: float = 3.2e-6) -> StaticFields:
    """Flat CLOSED domain whose legacy sink is LIVE everywhere (REAL capacities:
    non-zero on all cells). Synthetic value declared here at toy scale."""
    hh, ww = shape
    z = lambda *s: torch.zeros(*s, dtype=torch.float32)  # noqa: E731
    f = lambda *s, v=0.0: torch.full((*s,), v, dtype=torch.float32)  # noqa: E731
    return StaticFields(
        dz_x=z(hh, ww - 1),
        dz_y=z(hh - 1, ww),
        n_x=f(hh, ww - 1, v=0.03),
        n_y=f(hh - 1, ww, v=0.03),
        c_x=torch.ones(hh, ww - 1, dtype=torch.float32),
        c_y=torch.ones(hh - 1, ww, dtype=torch.float32),
        drain_cap_m_s=f(hh, ww, v=drain_cap_m_s),
        infil_rate_m_s=z(hh, ww),
        edge_w_n=f(hh, v=0.03),
        edge_e_n=f(hh, v=0.03),
        edge_n_n=f(ww, v=0.03),
        edge_s_n=f(ww, v=0.03),
        edge_w_s=f(hh, v=1e-4),
        edge_e_s=f(hh, v=1e-4),
        edge_n_s=f(ww, v=1e-4),
        edge_s_s=f(ww, v=1e-4),
        edge_open=(0.0, 0.0, 0.0, 0.0),  # sealed: boundary_out == 0 exactly
        shape=shape,
    )


def _chain_graph(rows: int, cols: int, n_nodes: int = N_NODES, cap: float = 0.05):
    """Capacity-bearing node chain across distinct cells; terminal outfall."""
    base_r, base_c = max(1, rows // 8), max(1, cols // 8)
    cells = [(base_r + i, base_c) for i in range(n_nodes)]
    assert cells[-1][0] < rows and cells[-1][1] < cols
    nmap = torch.full((rows, cols), -1, dtype=torch.int32)
    for i, (r, c) in enumerate(cells):
        nmap[r, c] = i + 1
    edges = [(i, i + 1, cap) for i in range(1, n_nodes)]
    outfall = torch.zeros(n_nodes, dtype=torch.bool)
    outfall[n_nodes - 1] = True
    return build_drain_graph(
        edge_from=torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64),
        edge_to=torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64),
        capacity_bearing=torch.ones(len(edges), dtype=torch.bool),
        q_cap_nom_m3s=torch.tensor([float(e[2]) for e in edges], dtype=torch.float64),
        width_mean_m=torch.full((n_nodes,), 6.71, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, n_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n_nodes, dtype=torch.float64),
        outfall_node=outfall,
        node_cell_row=torch.tensor([c[0] for c in cells], dtype=torch.int32),
        node_cell_col=torch.tensor([c[1] for c in cells], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.ones(n_nodes, dtype=torch.int64),
    )


def _solver_cfg() -> dict:
    cfg = load_solver_config(REPO / "configs" / "solver.yaml", REPO)
    cfg = copy.deepcopy(cfg)
    cfg["boundaries"]["mode"] = "closed"
    return cfg


def _coupling_cfg(tmp_path: Path):
    base = resolve_config(REPO / "configs" / "coupling.yaml", REPO)
    outs = dc_replace(
        base.outputs,
        run_dir=tmp_path / "run",
        manifest=tmp_path / "run" / "manifest.json",
        surcharge_events_csv=tmp_path / "run" / "products" / "surcharge_events.csv",
        event_continuity_csv=tmp_path / "run" / "products" / "event_continuity.csv",
        depth_series_dir=tmp_path / "run" / "depth",
    )
    return dc_replace(base, outputs=outs)


def _hermetic_cfg(tmp_path: Path):
    """Coupling cfg with tmp outputs AND a valid falsifier header under tmp_path,
    so the driver's §11.2 start gate never depends on repo artefacts."""
    fset = tmp_path / "prediction_set.json"
    fset.write_text(
        json.dumps(
            {
                "n_predicted_edges": 1,
                "predicted_node_ids": [1],
                "source_gpkg_sha256": "0" * 64,
            }
        )
    )
    base = _coupling_cfg(tmp_path)
    return dc_replace(base, diagnostics=dc_replace(base.diagnostics, falsifier_set=fset))


def _pond(shape: tuple[int, int]) -> torch.Tensor:
    return torch.full(shape, H0_M, dtype=torch.float32)


def _stock_problem(scfg: dict) -> tuple[SolverParams, TimestepController]:
    dx = float(scfg["_domain"]["resolution_m"])
    p = SolverParams.from_config(scfg, dx)
    ctrl = TimestepController(
        dx=dx,
        alpha=float(scfg["timestep"]["cfl_alpha"]),
        cfl_ceiling=float(scfg["timestep"]["cfl_ceiling"]),
        gravity=p.gravity,
        min_dt_s=float(scfg["timestep"]["min_dt_s"]),
        max_dt_s=float(scfg["timestep"]["max_dt_s"]),
    )
    return p, ctrl


def _stock_run(static: StaticFields, scfg: dict):
    """One stock-driver pass over THE shared problem definition (V3-clean: the
    baseline and the probe are two runs of the SAME unmodified inputs; neither
    is a cached artefact of the other)."""
    p, ctrl = _stock_problem(scfg)
    dx = float(scfg["_domain"]["resolution_m"])
    budget = MassBudget(
        cell_area_m2=dx * dx,
        relative_tolerance=float(scfg["mass"]["relative_tolerance"]),
        tolerance_is_measured=bool(scfg["mass"]["relative_tolerance_is_measured"]),
    )
    return stock_simulate(
        static,
        p,
        ctrl,
        duration_s=DURATION_S,
        rain=RAIN,
        h0=_pond(SHAPE),
        budget=budget,
        mass_check_every=10**9,  # cadence off: equality is judged on accumulators
        snapshot_every_s=None,
        max_steps=MAX_STEPS,
        device="cpu",
    )


# ---------------------------------------------------------------------------
# GREEN core: bit-for-bit stock equivalence around a FULL coupled exercise
# ---------------------------------------------------------------------------


class TestInv09UncoupledEquivalence:
    def test_stock_run_bitwise_stable_across_full_coupled_exercise(self, tmp_path: Path):
        """Run A (pristine) -> FULL coupled-machinery exercise against the SAME
        StaticFields instance -> Run B. A and B must agree bitwise on h/qx/qy,
        on step count, elementwise on the realized dt schedule, and on every
        MassBudget accumulator dict line. Between them the coupled path did REAL
        work (capture > 0 m^3) on objects derived from the shared instance, so
        any in-place/global leak would move B's bits.

        V7 scope: 2 x <=200-step x 1024-cell closed toy runs (~2400 s window),
        one 3-step capture-live coupled micro-run, refusal path, guard-d checks;
        scope claimed: the #9 seam at toy scale (see module docstring gaps)."""
        scfg = _solver_cfg()
        static = _static(SHAPE)  # THE shared instance: REAL (non-zero) capacities
        live_before = static.drain_cap_m_s.clone()

        # ---- RUN A: pristine stock -------------------------------------------
        res_a = _stock_run(static, scfg)
        assert res_a.steps > 20, "envelope collapsed: equivalence over <20 steps proves little"
        # the sink under protection must be LIVE in both runs, else trivial
        assert (
            res_a.budget.as_dict()["drain_out_m3"] > 0.0
        ), "stock run drained nothing — fixtures lost their REAL capacities"

        # ---- coupled machinery exercise AGAINST THE SHARED INSTANCE ----------
        # (a) out-of-place zeroing (guard a)
        static_zeroed = zero_drain_cap_out_of_place(static)
        assert torch.equal(static.drain_cap_m_s, live_before)
        assert bool((static_zeroed.drain_cap_m_s == 0).all())

        # (b) LIVE exchange call on the zeroed derivative (capture fires)
        graph = _chain_graph(*SHAPE, n_nodes=N_NODES)
        qx0 = torch.zeros(SHAPE[0], SHAPE[1] - 1, dtype=torch.float32)
        qy0 = torch.zeros(SHAPE[0] - 1, SHAPE[1], dtype=torch.float32)
        cr = couple_step(_pond(SHAPE), qx0, qy0, static_zeroed, build_node_state(graph), graph, 5.0)
        assert cr.capture_m3 > 0.0

        # (c) full coupled DRIVER micro-run receiving the shared instance;
        #     max_steps=3 keeps K1 unevaluatable (<4 steps) by construction.
        cfg_on = _hermetic_cfg(tmp_path)
        res_c = simulate_coupled(
            cfg_on,
            scfg,
            REPO,
            graph=graph,
            static=static,
            h0=_pond(SHAPE),
            rain=RAIN,
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10**9,
            smoke=True,
        )
        assert res_c.steps == 3
        assert (
            res_c.ledger.captured_to_drains_m3 > 0.0
        ), "coupled probe was inert — the exercise would prove nothing"
        assert res_c.ledger.legacy_drain_out_m3 == 0.0

        # (d) enabled=False refusal (asserted in depth by its own test below)
        with pytest.raises(ValueError, match="stock solver path"):
            simulate_coupled(
                dc_replace(cfg_on, enabled=False),
                scfg,
                REPO,
                graph=graph,
                static=static,
                duration_s=30.0,
                max_steps=3,
            )

        # ---- RUN B: stock again, same everything ------------------------------
        res_b = _stock_run(static, scfg)

        # ---- the invariant: bit-for-bit ---------------------------------------
        assert torch.equal(res_a.h, res_b.h), "final h diverged across coupled exercise"
        assert torch.equal(res_a.qx, res_b.qx) and torch.equal(res_a.qy, res_b.qy)
        assert res_a.steps == res_b.steps
        assert res_a.sim_time_s == res_b.sim_time_s
        assert (
            res_a.controller.schedule == res_b.controller.schedule
        ), "realized dt schedules differ elementwise"
        dict_a, dict_b = res_a.budget.as_dict(), res_b.budget.as_dict()
        assert dict_a == dict_b, "host MassBudget accumulators disagree"
        assert res_a.retry_attempts == res_b.retry_attempts
        assert res_a.rejected_steps == res_b.rejected_steps

        print(
            f"\n[inv09] stock A/B: steps={res_a.steps} sim={res_a.sim_time_s:.1f}s "
            f"h_bitwise_equal=True schedule_len={len(res_a.controller.schedule)} "
            f"drain_out_m3={dict_a['drain_out_m3']!r} rel_resid={dict_a['relative_residual']:.3e}"
        )
        print(f"[inv09] coupled exercise: captured={res_c.ledger.captured_to_drains_m3!r} m3")

        # ---- guard (d), false-path side: original parameters view RESTORED ----
        full_view = static.parameters()
        assert "drain_cap_m_s" in full_view, "off-path parameters view lost the knob"
        assert torch.equal(full_view["drain_cap_m_s"], live_before)
        coupled_view = coupled_parameter_set(static_zeroed)
        assert set(coupled_view) == set(full_view) - {"drain_cap_m_s"}
        for k, v in coupled_view.items():
            assert v is full_view[k], "filtered view must share storage with the source"

        # REAL capacities survived EVERYTHING above, bitwise
        assert torch.equal(static.drain_cap_m_s, live_before)

    def test_enabled_false_refusal_executes_zero_coupled_work(self, tmp_path: Path):
        """Spec §5: 'enabled: false => stock solver path, byte-identical
        behaviour'. Realized routing: simulate_coupled REFUSES before ANY work —
        the raise precedes the rule-7 preflight and the rule-6 start-manifest
        write, so the realized proof of 'byte-identical' is that NO coupled
        side effect exists at all: no manifest bytes, no run dir.

        V7 scope: entry-guard only; no loop executes."""
        cfg_off = dc_replace(_hermetic_cfg(tmp_path), enabled=False)
        scfg = _solver_cfg()

        with pytest.raises(ValueError) as excinfo:
            simulate_coupled(
                cfg_off,
                scfg,
                REPO,
                graph=_chain_graph(*SHAPE, n_nodes=2),
                static=_static((16, 16)),
                duration_s=30.0,
                max_steps=3,
            )
        msg = str(excinfo.value)
        assert "stock solver path" in msg and "byte-identical" in msg
        assert cfg_off.enabled is False  # the refused config was genuinely "off"
        assert not Path(
            cfg_off.outputs.manifest
        ).exists(), "a start-manifest exists — the disabled branch executed coupled work"
        assert not Path(cfg_off.outputs.run_dir).exists()

    def test_guard_d_false_path_restores_original_parameter_view(self):
        """Guard (d) both sides: COUPLED mode removes exactly drain_cap_m_s from
        a filtered view OF StaticFields.parameters() (storage shared); the OFF
        path keeps/RESTORES the original view — the knob present with its live
        values, the source method itself untouched.

        V7 scope: structural, one StaticFields shell; no simulation."""
        s = _static((8, 8))
        cap_snapshot = s.drain_cap_m_s.clone()
        full_before = s.parameters()
        assert "drain_cap_m_s" in full_before

        zeroed = zero_drain_cap_out_of_place(s)
        view = coupled_parameter_set(zeroed)
        assert "drain_cap_m_s" not in view
        assert set(view) == set(full_before) - {"drain_cap_m_s"}
        for k, v in view.items():
            assert v is full_before[k]
        assert "drain_cap_m_s" in zeroed.parameters()  # filter lives in coupling, not state.py

        # FALSE path: the ORIGINAL instance's view is intact and bitwise-live
        full_after = s.parameters()
        assert set(full_after) == set(full_before)
        assert "drain_cap_m_s" in full_after
        assert torch.equal(full_after["drain_cap_m_s"], cap_snapshot)
        assert torch.equal(s.drain_cap_m_s, cap_snapshot)


# ---------------------------------------------------------------------------
# V5 RED DEMO — /tmp-copy mutation of solver_hook.py ONLY (repo file untouched)
# ---------------------------------------------------------------------------

_MUTATION_ANCHOR = (
    "    if cfg.enabled is not True:\n"
    "        raise ValueError(\n"
    '            f"[{STAGE}] simulate_coupled is the COUPLED driver; '
    'coupling.enabled={cfg.enabled!r} "\n'
    '            "(enabled=false means the stock solver path, byte-identical behaviour)"\n'
    "        )\n"
)
_MUTATION_REPLACEMENT = (
    "    if cfg.enabled is not True:\n"
    "        pass  # INV09 MUTANT: the DISABLED branch falls THROUGH into the coupled loop\n"
)


def _load_mutant_from_tmp(source: str, tmp_path: Path):
    """Write the mutated copy FOUR levels under tmp_path (module-level
    ``REPO = parents[3]`` must resolve to an existing dir at import time) and
    import it fresh. Only ever touches tmp_path — never the repo tree."""
    target_dir = tmp_path / "red" / "a" / "b" / "c"
    target_dir.mkdir(parents=True)
    mutant_file = target_dir / "solver_hook.py"
    mutant_file.write_text(source)
    assert (REPO / "src/jaladhar/coupling/solver_hook.py").read_text().count(
        _MUTATION_ANCHOR
    ) == 1, "repo anchor mutated during red demo"
    spec = importlib.util.spec_from_file_location("inv09_mutant_solver_hook", mutant_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: dataclass resolution reads cls.__module__ out of
    # sys.modules during exec_module; an unregistered module crashes there.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


class TestInv09RedDemo:
    def test_v5_red_disabled_branch_engaging_capture_diverges_bitwise(self, tmp_path: Path):
        """Task-named mutation: make the enabled=False branch STILL engage the
        coupled loop (capture live, legacy capacities zeroed). Under the mutant,
        'coupling off' is implemented as the coupled driver running anyway —
        and its trajectory provably DIVERGES bitwise from the stock run with
        REAL capacities over the identical problem definition. torch.equal
        FAILS => the invariant's comparator bites (this file's green asserts
        are not vacuous).

        Also documents the claim's degenerate-case clause: the mutant runs in a
        CAPACITY-ZERO world (manifest legacy_drain_disabled=true) while the
        stock reference drains strictly positive volume (drain_out_m3 > 0) —
        capacity-zero is provably NOT the uncoupled result.

        V7 scope: <=50-step cap over a 60 s window x 1024 cells, 4-node chain,
        declared-synthetic; scope claimed: detection of the named mutation."""
        repo_source = (REPO / "src/jaladhar/coupling/solver_hook.py").read_text()
        assert (
            repo_source.count(_MUTATION_ANCHOR) == 1
        ), "mutation anchor drifted — update the red demo to the realized guard text"
        mutant = _load_mutant_from_tmp(
            repo_source.replace(_MUTATION_ANCHOR, _MUTATION_REPLACEMENT), tmp_path
        )

        scfg = _solver_cfg()
        mutant_side = tmp_path / "mutant_side"
        mutant_side.mkdir(parents=True, exist_ok=True)
        cfg_off = _hermetic_cfg(mutant_side)
        cfg_off = dc_replace(cfg_off, enabled=False)  # THE CONDITION UNDER TEST

        res_mutant = mutant.simulate_coupled(
            cfg_off,
            scfg,
            REPO,
            graph=_chain_graph(*SHAPE, n_nodes=N_NODES),
            static=_static(SHAPE),
            h0=_pond(SHAPE),
            rain=RAIN,
            duration_s=RED_DURATION_S,
            max_steps=RED_MAX_STEPS,
            mass_check_every=10**9,
            smoke=True,
        )
        # the mutant really did run the COUPLED path under enabled=False
        assert res_mutant.final_manifest["legacy_drain_disabled"] is True
        assert res_mutant.ledger.captured_to_drains_m3 >= 0.0

        # stock reference: IDENTICAL problem, REAL capacities, stock driver
        ref_static = _static(SHAPE)  # same declared values, separate instance
        dx = float(scfg["_domain"]["resolution_m"])
        p, ctrl = _stock_problem(scfg)
        budget = MassBudget(
            cell_area_m2=dx * dx,
            relative_tolerance=float(scfg["mass"]["relative_tolerance"]),
            tolerance_is_measured=bool(scfg["mass"]["relative_tolerance_is_measured"]),
        )
        res_ref = stock_simulate(
            ref_static,
            p,
            ctrl,
            duration_s=RED_DURATION_S,
            rain=RAIN,
            h0=_pond(SHAPE),
            budget=budget,
            mass_check_every=10**9,
            snapshot_every_s=None,
            max_steps=RED_MAX_STEPS,
            device="cpu",
        )

        # RED: bitwise divergence — captured verbatim to stdout
        equal_h = torch.equal(res_mutant.h, res_ref.h)
        h_diff = res_mutant.h.to(torch.float64) - res_ref.h.to(torch.float64)
        max_abs_diff = float(h_diff.abs().max())
        print(
            f"\n[inv09-RED] mutant(steps={res_mutant.steps}, sim={res_mutant.sim_time_s:.1f}s, "
            f"captured={res_mutant.ledger.captured_to_drains_m3!r} m3, "
            f"legacy_drain={res_mutant.final_manifest['legacy_drain_disabled']}) vs "
            f"stock(steps={res_ref.steps}, sim={res_ref.sim_time_s:.1f}s, "
            f"drain_out_m3={res_ref.budget.as_dict()['drain_out_m3']!r}): "
            f"torch.equal(h_mutant, h_stock)={equal_h}  max|h_diff|={max_abs_diff!r} m"
        )
        assert not equal_h, (
            "RED DEMO FAILED TO REPRODUCE: the engaged-disabled mutant matched the "
            "stock result bitwise — either the mutation no longer changes behaviour "
            "or the comparator is broken; do not trust the green tier until explained"
        )
        assert max_abs_diff > 0.0
        # degenerate-case documentation: different sink worlds, per the claim
        assert res_ref.budget.as_dict()["drain_out_m3"] > 0.0
