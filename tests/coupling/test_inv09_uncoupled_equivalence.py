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

import importlib.util
import sys
from dataclasses import replace as dc_replace
from pathlib import Path

import pytest
import torch
from conftest import (
    chain_graph,
    closed_solver_config,
    coupling_config,
    hermetic_config,
    static_fields,
)

from jaladhar.coupling.exchange import (
    build_node_state,
    couple_step,
    zero_drain_cap_out_of_place,
)
from jaladhar.coupling.solver_hook import (
    coupled_parameter_set,
    simulate_coupled,
)
from jaladhar.solver.acc import SolverParams
from jaladhar.solver.mass import MassBudget
from jaladhar.solver.run import simulate as stock_simulate
from jaladhar.solver.run import uniform_storm
from jaladhar.solver.state import StaticFields
from jaladhar.solver.timestep import TimestepController

REPO = Path(__file__).resolve().parents[2]

SHAPE = (32, 32)
N_NODES = 4
DURATION_S = 2400.0
MAX_STEPS = 200
STORM_MM = 110.0
STORM_DURATION_S = 7200.0
H0_M = 0.02
RAIN = uniform_storm(STORM_MM, STORM_DURATION_S)

RED_DURATION_S = 60.0
RED_MAX_STEPS = 50

_static = static_fields
_chain_graph = chain_graph


def _solver_cfg():
    return closed_solver_config(REPO)


def _coupling_cfg(tmp_path):
    return coupling_config(REPO, tmp_path)


def _hermetic_cfg(tmp_path):
    return hermetic_config(REPO, tmp_path)


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
        mass_check_every=10**9,
        snapshot_every_s=None,
        max_steps=MAX_STEPS,
        device="cpu",
    )


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
        static = _static(SHAPE)
        live_before = static.drain_cap_m_s.clone()

        res_a = _stock_run(static, scfg)
        assert res_a.steps > 20, "envelope collapsed: equivalence over <20 steps proves little"

        assert (
            res_a.budget.as_dict()["drain_out_m3"] > 0.0
        ), "stock run drained nothing — fixtures lost their REAL capacities"

        static_zeroed = zero_drain_cap_out_of_place(static)
        assert torch.equal(static.drain_cap_m_s, live_before)
        assert bool((static_zeroed.drain_cap_m_s == 0).all())

        graph = _chain_graph(*SHAPE, n_nodes=N_NODES)
        qx0 = torch.zeros(SHAPE[0], SHAPE[1] - 1, dtype=torch.float32)
        qy0 = torch.zeros(SHAPE[0] - 1, SHAPE[1], dtype=torch.float32)
        cr = couple_step(_pond(SHAPE), qx0, qy0, static_zeroed, build_node_state(graph), graph, 5.0)
        assert cr.capture_m3 > 0.0

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

        res_b = _stock_run(static, scfg)

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

        full_view = static.parameters()
        assert "drain_cap_m_s" in full_view, "off-path parameters view lost the knob"
        assert torch.equal(full_view["drain_cap_m_s"], live_before)
        coupled_view = coupled_parameter_set(static_zeroed)
        assert set(coupled_view) == set(full_view) - {"drain_cap_m_s"}
        for k, v in coupled_view.items():
            assert v is full_view[k], "filtered view must share storage with the source"

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
        assert cfg_off.enabled is False
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
        assert "drain_cap_m_s" in zeroed.parameters()

        full_after = s.parameters()
        assert set(full_after) == set(full_before)
        assert "drain_cap_m_s" in full_after
        assert torch.equal(full_after["drain_cap_m_s"], cap_snapshot)
        assert torch.equal(s.drain_cap_m_s, cap_snapshot)


# V5 RED DEMO — /tmp-copy mutation of solver_hook.py ONLY (repo file untouched)

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
        cfg_off = dc_replace(cfg_off, enabled=False)

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

        ref_static = _static(SHAPE)
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

        assert res_ref.budget.as_dict()["drain_out_m3"] > 0.0
