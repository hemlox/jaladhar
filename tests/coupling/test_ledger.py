"""Ledger unit tests — WF-2 coupling (spec §4.4/§10; deliverable 2).

TOY tier, all declared-synthetic, CPU-only (V12):

- accumulation identities against HAND-BUILT ``CoupleResult`` fixtures (V2: the references
  are scalar arithmetic in THIS file sharing no code with jaladhar.coupling.ledger),
- ``drain_out_net_m3`` NEGATIVE is allowed (net return is physical, D-E),
- contract-form and total-water residual formulas vs hand-computed numbers, including an
  INCONSISTENT-books case proving the two formulas are distinct quantities (V10),
- guard-c runtime teeth: mirrored legacy drainage != 0.0 exactly => AntiDoubleCountError,
- judged-bar and reconciliation-identity breaches raise CouplingMassBreach at the mass-check
  cadence; V5 red demo: dropping the RETURN term from the node books (invariant #1's named
  mutation) trips the identity while the consistent control passes — the observable MOVES,
- guard-c negative-dust allowance (item A): a REPLAY of the measured production trajectory
  (-1.8e-6 m3/step of min(0,h) f32 dust against a frozen 4*eps32*scale bound; refuter halt
  at step 12,471) completes past 13k steps with the default algebra, trips at ~11.9k with
  the allowance disabled (the old behaviour), and a POSITIVE drift still trips immediately;
  the >=10x margin constraint is asserted numerically, not narrated,
- non-finite residuals breach loudly (item F): NaN books / NaN budget cannot silently pass
  gates that compare False against every tolerance,
- surcharge-event list interface: contiguous steps merge into ONE event, a gap splits,
  max_head tracked, G2-schema fields present, §10.4 continuity components close identically,
- IndirectCflMonitor alarm arithmetic vs hand-computed sqrt factors.

V7 scope statements live in each test docstring. Scope claimed here is the LEDGER's own
arithmetic and bookkeeping on toy fixtures; end-to-end conservation over real grids is the
driver test's scope (test_solver_hook.py::test_micro_run_end_to_end_manifest_lifecycle).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import torch

import jaladhar.coupling.ledger as ledger_mod
from jaladhar.coupling.exchange import CoupleResult, NodeState
from jaladhar.coupling.ledger import (
    DUST_ALLOWANCE_SAFETY_FACTOR,
    LEGACY_ZERO_BOUND_RELATIVE,
    AntiDoubleCountError,
    CouplingMassBreach,
    CouplingMassLedger,
    IndirectCflMonitor,
)
from jaladhar.coupling.router import build_drain_graph
from jaladhar.solver.mass import MassBudget

AREA = 100.0

# Fixtures (hand-built; V2: no shared code path with the module under test)


def _node_state(heads: list[float], vin: list[float], vout: list[float]) -> NodeState:
    return NodeState(
        h_node_m=torch.tensor(heads, dtype=torch.float32),
        vol_in_m3_cum=torch.tensor(vin, dtype=torch.float64),
        vol_out_m3_cum=torch.tensor(vout, dtype=torch.float64),
    )


def _cr(
    *,
    capture_m3: float,
    return_m3: float,
    vin: list[float],
    vout: list[float],
    heads: list[float] | None = None,
    returned_field: list[list[float]] | None = None,
    surcharging: int = 0,
    cap_binding: int = 0,
) -> CoupleResult:
    shape = (len(returned_field), len(returned_field[0])) if returned_field else (1, 1)
    h_new = torch.zeros(shape, dtype=torch.float32)
    return CoupleResult(
        h_new=h_new,
        node_state_new=_node_state(heads or [0.0] * len(vin), vin, vout),
        captured_depth_m=torch.zeros(shape, dtype=torch.float32),
        returned_depth_m=(
            torch.tensor(returned_field, dtype=torch.float32)
            if returned_field
            else torch.zeros(shape, dtype=torch.float32)
        ),
        capture_m3=capture_m3,
        return_m3=return_m3,
        surcharging_nodes=surcharging,
        max_node_head_m=max(heads) if heads else 0.0,
        cap_binding_this_step=cap_binding,
        edge_flow_m3s=torch.zeros(1, dtype=torch.float32),
    )


def _host(**kw) -> MassBudget:
    b = MassBudget(cell_area_m2=AREA, relative_tolerance=1.0e-3)
    b.v_initial = kw.get("v_initial", 1000.0)
    b.v_current = kw.get("v_current", 1100.0)
    b.rain_in = kw.get("rain_in", 200.0)
    b.drain_out = kw.get("drain_out", 0.0)
    b.infil_out = kw.get("infil_out", 30.0)
    b.boundary_out = kw.get("boundary_out", 15.0)
    b.created_by_clamping = kw.get("created_by_clamping", 0.0)
    return b


def _toy_graph():
    return build_drain_graph(
        edge_from=torch.zeros(1, dtype=torch.int64),
        edge_to=torch.ones(1, dtype=torch.int64),
        capacity_bearing=torch.ones(1, dtype=torch.bool),
        q_cap_nom_m3s=torch.tensor([5.0], dtype=torch.float64),
        width_mean_m=torch.tensor([6.71, 2.285], dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.tensor([10.0, 0.0], dtype=torch.float64),
        contrib_area_m2=torch.zeros(2, dtype=torch.float64),
        outfall_node=torch.tensor([False, True]),
        node_cell_row=torch.arange(2, dtype=torch.int32),
        node_cell_col=torch.arange(2, dtype=torch.int32),
        node_id_map=torch.tensor([[1, -1]], dtype=torch.int32),
        node_cell_count=torch.tensor([1, 0], dtype=torch.int64),
    )


class TestAccumulationIdentities:
    def test_sums_net_and_tallies_vs_fixtures(self):
        """Three fixtures with known volumes: cumulative captured/returned equal the sums,
        net = captured - returned, cap-binding and surcharging-step tallies match.

        V7 scope: 3 accumulate() calls, aggregate counters only (no graph/events)."""
        led = CouplingMassLedger(AREA, _host())
        led.accumulate(
            _cr(
                capture_m3=10.0, return_m3=0.0, vin=[10.0], vout=[0.0], surcharging=0, cap_binding=0
            ),
            0.48,
        )
        led.accumulate(
            _cr(
                capture_m3=0.0, return_m3=4.0, vin=[10.0], vout=[4.0], surcharging=2, cap_binding=1
            ),
            0.48,
        )
        led.accumulate(
            _cr(
                capture_m3=7.0, return_m3=3.0, vin=[17.0], vout=[7.0], surcharging=0, cap_binding=1
            ),
            0.48,
        )
        assert led.captured_to_drains_m3 == 17.0
        assert led.surcharge_returned_m3 == 7.0
        assert led.drain_out_net_m3 == 10.0
        assert led.steps == 3
        assert led.cap_binding_steps == 2
        assert led.total_surcharging_steps == 1

    def test_negative_net_allowed_physical(self):
        """RETURN-heavy step => drain_out_net_m3 < 0 with NO exception: net return is
        physical (D-E). The alias would have hidden this sign — its absence is load-bearing.

        V7 scope: 1 accumulate() call, aggregate counters only."""
        led = CouplingMassLedger(AREA, _host())
        led.accumulate(_cr(capture_m3=2.0, return_m3=9.0, vin=[11.0], vout=[9.0]), 0.48)
        assert led.captured_to_drains_m3 == 2.0
        assert led.surcharge_returned_m3 == 9.0
        assert led.drain_out_net_m3 == -7.0
        assert led.as_dict()["drain_out_net_m3"] == -7.0

    def test_guard_c_mirror_assert_fires_on_nonzero_legacy(self):
        """GUARD C runtime teeth: the moment the mirrored HOST drain_out is non-zero while
        coupling accumulates, AntiDoubleCountError fires. Zero control first (green), then
        the deliberate non-zero (red) — the observable MOVES.

        V7 scope: 2 accumulate() calls against a stubbed host scalar."""
        host = _host()
        led = CouplingMassLedger(AREA, host)
        led.accumulate(_cr(capture_m3=5.0, return_m3=0.0, vin=[5.0], vout=[0.0]), 0.48)
        assert led.legacy_drain_out_m3 == 0.0

        host.drain_out = 5.0
        with pytest.raises(AntiDoubleCountError, match="guard \\(c\\)"):
            led.accumulate(_cr(capture_m3=5.0, return_m3=0.0, vin=[10.0], vout=[0.0]), 0.48)


class TestResidualFormulasHandComputed:
    def test_consistent_books_both_forms_hand_computed(self):
        """Hand arithmetic, consistent books (V_nodes = net = 25 m3):
        contract   = 1100 - 1000 - 200 + 25 + 30 + 15 - 0      = -30
        total_water= (1100 + 25) - 1000 - 200 + 0 + 30 + 15 - 0 = -30
        relative form divides by max(|rain|, |v_initial|, 1e-12) = max(200, 1000) = 1000,
        so both relatives are 30/1000 = 0.03 (V6 record: the first draft of this test
        divided by rain alone and was WRONG — the spec's scale is the max, code is right).
        Equal forms HERE precisely because the reconciliation identity holds exactly.

        V7 scope: 1 accumulate() call; formula-level check with a stubbed host budget."""
        led = CouplingMassLedger(AREA, _host())
        led.accumulate(_cr(capture_m3=45.0, return_m3=20.0, vin=[70.0], vout=[45.0]), 0.48)
        assert led.v_nodes_m3() == pytest.approx(25.0)
        assert led.contract_residual_m3() == pytest.approx(-30.0)
        assert led.contract_residual() == pytest.approx(0.03)
        assert led.total_water_residual_m3() == pytest.approx(-30.0)
        assert led.total_water_relative_residual() == pytest.approx(0.03)

    def test_inconsistent_books_forms_are_distinct(self):
        """Books short by 1 m3 (V_nodes=24, net=25): contract stays -30 (uses NET),
        total-water moves to -31 (uses INVENTORY). Two formulas, two answers — the D-E
        separation and the V10 derivation discipline made visible.

        V7 scope: 1 accumulate() call."""
        led = CouplingMassLedger(AREA, _host())
        led.accumulate(_cr(capture_m3=45.0, return_m3=20.0, vin=[69.0], vout=[45.0]), 0.48)
        assert led.contract_residual_m3() == pytest.approx(-30.0)
        assert led.total_water_residual_m3() == pytest.approx(-31.0)


class TestMassCheckBreaches:
    def test_judged_bar_breach_halts(self):
        """Created water (+1 m3 on the inventory) blows the 1e-4 judged bar => the cadence
        check raises CouplingMassBreach and the history row was appended BEFORE the raise.

        V7 scope: 1 accumulate() + 1 mass_check()."""
        host = _host(v_current=1101.0, infil_out=0.0, boundary_out=0.0)
        led = CouplingMassLedger(AREA, host)
        led.accumulate(_cr(capture_m3=0.0, return_m3=0.0, vin=[0.0], vout=[0.0]), 0.48)
        with pytest.raises(CouplingMassBreach, match="total-water"):
            led.mass_check(7)
        assert led.history[-1]["step"] == 7.0

    def test_v5_red_dropped_return_term_breaks_identity(self):
        """V5 RED DEMO (invariant #1's named mutation, recorded here): DROP THE RETURN TERM
        from the node books — return_m3=50 arrives but vout omits it. The reconciliation
        identity jumps to O(volume) (0.3125 relative) and mass_check HALTS naming the
        identity; the consistent control on identical exchange volumes passes. The
        observable MOVES.

        Host stubs are BALANCED against the fixture world (V6 record: the first draft
        reused the generic unbalanced stub and the judged-bar branch fired in the CONTROL
        before the mutant ran — the stub was wrong, not the ledger). Balance: rain 200,
        no sinks, v_current = v_initial + rain - net => control closes to ~0 on BOTH forms;
        scale = max(200, 1000) keeps the mutant's total-water at 0.05 while its identity
        sits at 0.3125, and identity is checked FIRST.

        V7 scope: 2 single-step ledger runs (control + mutant), aggregate-level check."""

        def balanced_host() -> MassBudget:
            b = MassBudget(cell_area_m2=AREA, relative_tolerance=1.0e-3)
            b.v_initial = 1000.0
            b.v_current = 1000.0 + 200.0 - 110.0
            b.rain_in = 200.0
            b.infil_out = 0.0
            b.boundary_out = 0.0
            return b

        ok = CouplingMassLedger(AREA, balanced_host())
        ok.accumulate(_cr(capture_m3=160.0, return_m3=50.0, vin=[160.0], vout=[50.0]), 0.48)
        assert ok.total_water_relative_residual() <= 1e-4
        ok.mass_check(1)

        mutant = CouplingMassLedger(AREA, balanced_host())
        mutant.accumulate(_cr(capture_m3=160.0, return_m3=50.0, vin=[160.0], vout=[0.0]), 0.48)
        expected_rel = abs(110.0 - 160.0) / 160.0
        assert mutant._identity_worst_rel == pytest.approx(expected_rel)
        with pytest.raises(CouplingMassBreach, match="reconciliation identity"):
            mutant.mass_check(1)


class TestSurchargeEvents:
    def _ledger_with_graph(self) -> CouplingMassLedger:
        return CouplingMassLedger(AREA, _host(), _toy_graph())

    def test_contiguous_merge_gap_split_schema_and_continuity(self):
        """Steps 1-2 surcharging merge into ONE event; a silent step closes it; a later
        surge opens a SECOND event. G2 schema fields present; max_head is the max over the
        event; §10.4 components close identically (in - out_routed - ret - dStorage = 0).

        Books (cumulative, consistent: dStorage = capture - return):
          s1: cap 160 ret 50 -> vin [160,0]  vout [50,0]
          s2: cap 160 ret 50 -> vin [320,0]  vout [100,0]
          s3: cap  40 ret  0 -> vin [360,0]  vout [100,0]   (event 1 closes: last=2)
          s4: cap  60 ret 20 -> vin [420,0]  vout [120,0]   (event 2 opens)

        V7 scope: 4 accumulate() calls on a 1-cell-mapped 2-node toy graph; event logic
        only (routing/telescoping is exercised end-to-end in the driver micro-run)."""
        led = self._ledger_with_graph()
        seq = [
            (160.0, 50.0, [160.0, 0.0], [50.0, 0.0], [1.6, 0.0], [[0.5, 0.0]]),
            (160.0, 50.0, [320.0, 0.0], [100.0, 0.0], [2.5, 0.0], [[0.5, 0.0]]),
            (40.0, 0.0, [360.0, 0.0], [100.0, 0.0], [2.5, 0.0], [[0.0, 0.0]]),
            (60.0, 20.0, [420.0, 0.0], [120.0, 0.0], [3.1, 0.0], [[0.2, 0.0]]),
        ]
        for cap, ret, vin, vout, heads, field in seq:
            led.accumulate(
                _cr(
                    capture_m3=cap,
                    return_m3=ret,
                    vin=vin,
                    vout=vout,
                    heads=heads,
                    returned_field=field,
                    surcharging=1 if ret > 0 else 0,
                ),
                0.48,
            )
        led.flush_open_events()
        assert len(led.events) == 2
        e1, e2 = led.events
        assert (e1["node_id"], e1["first_step"], e1["last_step"]) == (1, 1, 2)
        assert e1["total_returned_m3"] == pytest.approx(100.0)
        assert e1["max_head_m"] == pytest.approx(max(np32(1.6), np32(2.5)))
        assert (e2["first_step"], e2["last_step"]) == (4, 4)
        assert e2["total_returned_m3"] == pytest.approx(20.0)
        for ev in (e1, e2):
            closure = (
                ev["vol_in_during_m3"]
                - ev["vol_out_routed_during_m3"]
                - ev["returned_during_m3"]
                - ev["delta_storage_m3"]
            )
            assert abs(closure) <= 1e-9
        assert e1["vol_in_during_m3"] == pytest.approx(320.0)
        assert e1["vol_out_routed_during_m3"] == pytest.approx(0.0)
        assert e1["delta_storage_m3"] == pytest.approx(220.0)


def np32(x: float) -> float:
    return float(torch.tensor(x, dtype=torch.float32))


class TestIndirectCflMonitor:
    def test_alarm_arithmetic_vs_hand_computed_factors(self):
        """factor = sqrt(h/(h+dH)), hand-computed:
             (1.0, 0.0)  -> 1.0                        no alarm
             (1.0, 0.1)  -> sqrt(1/1.10) = 0.953463    no alarm (>= 0.902)
             (1.0, 0.23) -> sqrt(1/1.23) = 0.901664    ALARM (< 0.902; ~ +23% h_max)
           min_factor tracks the minimum; dry/no-exchange observations are factor 1.0.

        V7 scope: 6 observe() calls, scalar arithmetic only."""
        mon = IndirectCflMonitor(alarm_factor=0.902)
        assert mon.observe(1.0, 0.0) == 1.0
        got = mon.observe(1.0, 0.1)
        assert got == pytest.approx(math.sqrt(1.0 / 1.10), rel=1e-12)
        assert got >= 0.902
        got2 = mon.observe(1.0, 0.23)
        assert got2 == pytest.approx(math.sqrt(1.0 / 1.23), rel=1e-12)
        assert got2 < 0.902
        mon.observe(2.0, 0.0)
        mon.observe(0.0, 5.0)
        assert mon.steps_observed == 5
        assert mon.alarm_steps == 1
        assert mon.min_factor == pytest.approx(math.sqrt(1.0 / 1.23), rel=1e-12)
        d = mon.as_dict()
        assert d["alarm_steps"] == 1 and d["min_factor"] < 0.902

    def test_nonfinite_refused(self):
        """NaN/inf observations mean upstream broke — refused, not absorbed as factor 1.0.

        V7 scope: 2 refused calls."""
        mon = IndirectCflMonitor()
        with pytest.raises(ValueError, match="non-finite"):
            mon.observe(float("nan"), 0.1)
        with pytest.raises(ValueError, match="non-finite"):
            mon.observe(1.0, float("inf"))


class TestGuardCNegativeDustAllowance:
    """Item A: guard (c) FALSE-HALT on conserving storm-then-dry runs.

    Measured production facts being replayed (refuter reproduction, V2: the trajectory
    here is arithmetic typed into THIS file, not produced by the code under test):
      - cumulative min(0*dt, h_new) f32 NEGATIVE dust accrues ~linearly at
        DUST_PER_STEP = 1.8e-6 m3/step AFTER rain stops;
      - the OLD acceptance bound froze at 4*eps32*max(|v_current|,|rain_in|) because
        rain_in is fixed post-storm while v_current declines -> halt at step 12,471
        with drain_out = -0.021501892301856174 m3 against bound 2.150e-02 m3;
      - a GENUINE double-count leaks ~ +0.819 m3 by ~12k steps (POSITIVE drift).

    Constraints under which the algebra was chosen (asserted below, not narrated):
      (a) the genuine +0.819 m3 signal must still trip with >=10x margin at that scale;
      (b) a conserving storm-then-dry run must complete past 13k steps;
      (c) the mirror stays VERBATIM-reported.

    V7 scope: ledger-level replays of scalar trajectories (13k accumulate() calls with
    zero-volume fixtures; no tensors beyond the fixture); the driver-loop realization of
    constraint (b) lives in
    tests/coupling/test_solver_hook.py::TestStormThenDryGuardC."""

    STEPS = 13_000
    DUST_PER_STEP = 1.8e-6
    RAIN_IN_FROZEN = 45_093.0
    GENUINE_M3 = 0.819

    def _storm_then_dry_host(self) -> MassBudget:
        b = MassBudget(cell_area_m2=100.0, relative_tolerance=1.0e-3)
        b.v_initial = 500.0
        b.rain_in = self.RAIN_IN_FROZEN
        b.v_current = 12.5
        b.infil_out = b.boundary_out = 0.0
        return b

    def _zero_cr(self) -> CoupleResult:
        return _cr(capture_m3=0.0, return_m3=0.0, vin=[0.0], vout=[0.0])

    def test_green_thirteen_k_step_dust_replay_completes(self):
        host = self._storm_then_dry_host()
        led = CouplingMassLedger(100.0, host)
        zero = self._zero_cr()
        for k in range(1, self.STEPS + 1):
            host.drain_out = -self.DUST_PER_STEP * k
            led.accumulate(zero, 0.48)
        mirror = led.legacy_drain_out_m3
        neg_bound_now = (
            ledger_mod.LEGACY_ZERO_BOUND_RELATIVE * self.RAIN_IN_FROZEN + led.dust_allowance_m3
        )
        print(
            f"\n[guard-c-dust/green] steps={led.steps} mirror={mirror!r} m3 "
            f"dust_observed={led.dust_observed_m3!r} allowance={led.dust_allowance_m3!r} "
            f"neg_bound={neg_bound_now:.4e}"
        )
        assert led.steps == self.STEPS
        assert mirror == pytest.approx(-self.DUST_PER_STEP * self.STEPS, rel=1e-9)
        assert led.dust_allowance_m3 == pytest.approx(
            DUST_ALLOWANCE_SAFETY_FACTOR * self.DUST_PER_STEP * self.STEPS, rel=1e-9
        )
        assert led.dust_allowance_m3 > abs(mirror), "allowance failed to lead the dust"
        d = led.as_dict()
        assert d["guard_c_negative_dust"]["observed_m3"] == pytest.approx(abs(mirror))

    def test_red_allowance_disabled_reproduces_the_production_halt(self, monkeypatch):
        """RED (V5, named mutation recorded here): DUST_ALLOWANCE_SAFETY_FACTOR -> 0.0
        disables the allowance, recovering the OLD algebra — the same replay then trips
        AntiDoubleCountError when cumulative dust crosses the frozen static bound, at
        step ceil(4*eps32*45093 / 1.8e-6) ~= 11,946, i.e. the measured production halt
        mechanism (refuter's realized halt: 12,471 on its own declining-v trajectory).
        The observable MOVES between this mutant arm and the green replay above."""
        monkeypatch.setattr(ledger_mod, "DUST_ALLOWANCE_SAFETY_FACTOR", 0.0)
        host = self._storm_then_dry_host()
        led = CouplingMassLedger(100.0, host)
        zero = self._zero_cr()
        static_bound = ledger_mod.LEGACY_ZERO_BOUND_RELATIVE * self.RAIN_IN_FROZEN
        expected_trip = math.ceil(static_bound / self.DUST_PER_STEP)
        with pytest.raises(AntiDoubleCountError, match="guard \\(c\\)") as excinfo:
            for k in range(1, self.STEPS + 1):
                host.drain_out = -self.DUST_PER_STEP * k
                led.accumulate(zero, 0.48)
        msg = str(excinfo.value)
        print(
            f"\n[guard-c-dust/red-mutant] tripped at step {led.steps} "
            f"(predicted {expected_trip}; refuter's production halt: 12,471): {msg[:140]}..."
        )
        assert 11_000 <= led.steps <= 12_600
        assert led.steps == expected_trip
        assert "negative-dust allowance" in msg

    def test_red_positive_double_count_still_trips_immediately(self):
        host = self._storm_then_dry_host()
        led = CouplingMassLedger(100.0, host)
        zero = self._zero_cr()
        per_step = self.GENUINE_M3 / 12_044
        with pytest.raises(AntiDoubleCountError, match="double-counted against capture"):
            for k in range(1, self.STEPS + 1):
                host.drain_out = per_step * k
                led.accumulate(zero, 0.48)
        print(
            f"\n[guard-c-dust/red-positive] tripped at step {led.steps}, "
            f"drain={host.drain_out!r}"
        )
        assert led.steps < 1_000, "a real sink leaked ~3 orders too long before tripping"

    def test_margin_constraints_asserted_numerically_at_measured_scale(self):
        static_bound = LEGACY_ZERO_BOUND_RELATIVE * self.RAIN_IN_FROZEN
        dust_12471 = self.DUST_PER_STEP * 12_471
        neg_bound_12471 = static_bound + DUST_ALLOWANCE_SAFETY_FACTOR * dust_12471
        dust_13000 = self.DUST_PER_STEP * self.STEPS
        neg_bound_13000 = static_bound + DUST_ALLOWANCE_SAFETY_FACTOR * dust_13000
        print(
            f"\n[guard-c-dust/margins] static={static_bound:.4e} neg_bound@12471="
            f"{neg_bound_12471:.4e} genuine={self.GENUINE_M3} m3 dust@13k={dust_13000:.4e} "
            f"neg_bound@13k={neg_bound_13000:.4e}"
        )
        assert neg_bound_12471 <= self.GENUINE_M3 / 10.0, (
            "constraint (a) violated: <10x margin between the negative bound and the "
            "measured genuine double-count at the 12k-step scale"
        )
        assert (
            neg_bound_13000 >= 2.0 * dust_13000
        ), "constraint (b) violated: 13k-step dust does not clear the bound with 2x headroom"


class TestNonFiniteResidualsBreach:
    def test_nan_budget_total_water_breaches_not_silently_passes(self):
        host = _host(v_current=float("nan"))
        led = CouplingMassLedger(AREA, host)
        led.accumulate(_cr(capture_m3=0.0, return_m3=0.0, vin=[0.0], vout=[0.0]), 0.48)
        assert not math.isfinite(led.total_water_relative_residual())
        with pytest.raises(CouplingMassBreach, match="NON-FINITE"):
            led.mass_check(3)

    def test_nan_books_identity_worst_stored_as_inf_and_breaches(self):
        host = _host()
        led = CouplingMassLedger(AREA, host)
        led.accumulate(_cr(capture_m3=0.0, return_m3=0.0, vin=[float("nan")], vout=[0.0]), 0.48)
        assert led._identity_worst_rel == math.inf
        with pytest.raises(CouplingMassBreach, match="NON-FINITE"):
            led.mass_check(3)
        row = led.history[-1]
        assert isinstance(row["reconciliation_identity_rel"], str)
        assert json.dumps(row)
        dumped = json.dumps(led.as_dict())
        assert "non-finite(" in dumped

    def test_finite_control_mass_check_still_passes(self):
        host = _host(v_current=1000.0 + 200.0 - 25.0, infil_out=0.0, boundary_out=0.0)
        led = CouplingMassLedger(AREA, host)
        led.accumulate(_cr(capture_m3=45.0, return_m3=20.0, vin=[70.0], vout=[45.0]), 0.48)
        row = led.mass_check(9)
        assert isinstance(row["total_water_relative_residual"], float)


class TestGuardCNegativeJumpTripsOnFirstOccurrence:

    def test_red_single_negative_jump_larger_than_static_trips_first_occurrence(self):
        """A -5.0 m3 SINGLE-step jump against a ~5.2e-4 m3 static bound must trip
        AntiDoubleCountError ON THE JUMP STEP itself. Pre-fix this exact input was silently
        absorbed (the jump self-funded a 10.0 m3 allowance one line before its own check).

        V7 scope: 2 accumulate() calls, scalar mirror trajectory only."""
        host = _host()
        led = CouplingMassLedger(AREA, host)
        zero = _cr(capture_m3=0.0, return_m3=0.0, vin=[0.0], vout=[0.0])
        led.accumulate(zero, 0.48)
        host.drain_out = -5.0
        with pytest.raises(AntiDoubleCountError, match="guard \\(c\\)"):
            led.accumulate(zero, 0.48)

    def test_steady_leak_absorbed_to_documented_boundary_then_absolute_cap_trips(self):
        """The residual hole the allowance cannot close, probed at a rate CLEARLY above the
        measured dust slope (1e-4 vs 1.8e-6 m3/step, ~55x) yet BELOW single-jump static
        sensitivity at this host scale (~5.2e-4): no single increment could trip even a
        correct pre-update check, and each increment re-funds the allowance 2x one step
        later — so ONLY the absolute cap bounds it. Documented capped-visibility boundary:
        the leak rides silently to max(static, DUST_ABSORB_CAP_M3)=1.0 m3 cumulative
        (~10,000 steps), trips immediately after; positive side untouched throughout.

        V7 scope: <=12k scalar accumulate() calls with zero-volume fixtures."""
        host = _host()
        led = CouplingMassLedger(AREA, host)
        zero = _cr(capture_m3=0.0, return_m3=0.0, vin=[0.0], vout=[0.0])
        rate = 1.0e-4
        cap = ledger_mod.DUST_ABSORB_CAP_M3
        static_bound = ledger_mod.LEGACY_ZERO_BOUND_RELATIVE * 1100.0
        absorb_cap = max(static_bound, cap)

        for k in range(1, 3001):
            host.drain_out = -rate * k
            led.accumulate(zero, 0.48)
        assert led.steps == 3000
        assert (
            abs(led.legacy_drain_out_m3) < absorb_cap
        ), "capped-visibility boundary moved: steady leak tripped BEFORE the absolute cap"

        with pytest.raises(AntiDoubleCountError, match="ABSOLUTE negative-absorb cap") as ei:
            for k in range(3001, 12_001):
                host.drain_out = -rate * k
                led.accumulate(zero, 0.48)
        print(
            f"\n[guard-c-cap/steady-leak] tripped at step {led.steps} "
            f"({abs(led.legacy_drain_out_m3)!r} m3): {str(ei.value)[:120]}..."
        )
        assert abs(led.legacy_drain_out_m3) <= absorb_cap * 1.01, "cap overshot materially"
        assert 9_900 <= led.steps <= 10_100, "trip step drifted from cap/rate arithmetic"

    def test_absorb_cap_sized_against_measured_dust_visibility_statement(self):
        """Cap JUSTIFICATION as arithmetic over MEASURED magnitudes (rule 3): largest
        realized cumulative min(0,h) dust = 0.0234 m3 (13k-step toy replay); production-
        slope extrapolation over a full 3 h storm-then-dry window (~43k steps x
        1.8e-6 m3/step) ~= 0.077 m3. The cap must sit >=10x above BOTH so genuine dust can
        never trip it, which simultaneously states what stays visible: any sustained
        NEGATIVE leak steeper than ~13x the measured dust rate crosses 1.0 m3 within one
        such window; slower leaks are DECLARED indistinguishable from f32 dust.

        V7 scope: module-constant arithmetic only."""
        cap = ledger_mod.DUST_ABSORB_CAP_M3
        measured_toy_dust_13k = 0.0234
        prod_window_extrapolation = 1.8e-6 * 43_000
        assert cap >= 10.0 * measured_toy_dust_13k
        assert cap >= 10.0 * prod_window_extrapolation
        visible_rate_per_window = cap / 43_000
        print(
            f"\n[guard-c-cap/sizing] cap={cap!r} m3 >= 10x toy-dust({measured_toy_dust_13k}) "
            f"and >= 10x prod-extrap({prod_window_extrapolation:.4f}); visible sustained "
            f"leak threshold ~= {visible_rate_per_window:.2e} m3/step "
            f"(>= {visible_rate_per_window / 1.8e-6:.0f}x the measured dust rate)"
        )
        assert visible_rate_per_window >= 10.0 * 1.8e-6


class TestNonFiniteMirrorBreachAndJsonSafety:

    def test_nan_mirror_verbatim_mass_check_breaches_and_json_clean(self):
        """NaN mirror stays VERBATIM (never clamped, V1), mass_check HALTS naming the
        mirror NON-FINITE, and json.dumps(as_dict(), allow_nan=False) — strict JSON —
        carries loud 'non-finite(...)' strings instead of invalid literals.

        V7 scope: 2 accumulate() calls + 1 mass_check() on a stubbed host scalar."""
        host = _host()
        led = CouplingMassLedger(AREA, host)
        zero = _cr(capture_m3=0.0, return_m3=0.0, vin=[0.0], vout=[0.0])
        led.accumulate(zero, 0.48)
        host.drain_out = float("nan")
        led.accumulate(zero, 0.48)
        assert math.isnan(led.legacy_drain_out_m3), "mirror must be stored verbatim"
        dumped = json.dumps(led.as_dict(), allow_nan=False)
        assert "NaN" not in dumped and "Infinity" not in dumped
        assert "non-finite(" in dumped
        with pytest.raises(CouplingMassBreach, match="drain_out mirror is NON-FINITE"):
            led.mass_check(4)


REPO = Path(__file__).resolve().parents[2]
