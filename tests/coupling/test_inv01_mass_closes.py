"""INVARIANT #1 ``mass-closes`` — total-water coupled budget closure (spec §10.3, §12 row #1).

V2 observable: "if this were broken I would observe ___". If ANY term of the coupled water
balance were dropped, double-counted, or aliased, I would observe a NON-ZERO total-water
residual of O(volume) when I recompute it MYSELF in this file — from the raw manifest budget
lines on disk (``v_current_m3, v_initial_m3, rain_in_m3, infil_out_m3, boundary_out_m3,
created_by_clamping_m3`` and the LEGACY ``drain_out_m3`` line) plus V_nodes summed by THIS
test's own expression over the final f64 node books — NOT by calling
``ledger.total_water_residual()``. Second independent angle: the telescoping reconciliation
``|captured_to_drains - surcharge_returned - V_nodes| / scale ~ 0`` compares the CONTRACT
scalars (computed ONCE inside couple_step from realized f32 depth fields) against the NODE-BOOK
f64 accumulators (index_add bookkeeping) — two different code paths (ledger docstring, V2
non-mirror), so agreement is evidence and disagreement is a dropped term. A third angle asserts
the reported block reconciles algebraically: ``contract_residual + V_nodes - total_water =
net - legacy`` evaluated on the realized numbers, with the D-E separation explicit.

Claim under test (spec §12 row #1): the total-water coupled budget closes to <= 1e-4 relative —
and to the replay#2 realized level (config ``budget.realized_reference_residual`` = 1.79e-05)
on the toy case — with ``drain_out_m3``(legacy) and ``captured_to_drains_m3`` as SEPARATE
explicit manifest lines (D-E: no backward_compat_alias), and contract-form residual +
V_nodes(t) reconcile identically.

V5 RED DEMO — mutation recorded (run 2026-08-26, /tmp copy ONLY; live tree untouched):
full ``jaladhar`` package copied to ``/tmp/opencode/redtest_mass/jaladhar``, then the named
mutation applied THERE (spec §12 row #1: "drop the return term from node continuity"):

    --- jaladhar/coupling/exchange.py (copy under /tmp/opencode/redtest_mass)
    +++ mutated copy
    @@ -453,7 +453,7 @@  # NodeState construction at the end of couple_step
         node_state_new = NodeState(
             h_node_m=h_node_new,
             vol_in_m3_cum=node_state.vol_in_m3_cum + capture_at_node + edge_in_vol,
    -        vol_out_m3_cum=node_state.vol_out_m3_cum + edge_out_vol + r_node,
    +        vol_out_m3_cum=node_state.vol_out_m3_cum + edge_out_vol,

so node-side books never subtract returned volume => V_nodes(t) grows by the cumulative
returned volume => the total-water residual must jump to ~ returned/scale (~5.4e-03 on this
fixture, ~54x the 1e-4 hard bar). The driver was run with cadence checks bypassed so the
LEDGER'S OWN CouplingMassBreach could not be the catcher — the failure below is THIS TEST's
recomputation catching the mutation independently (that is the V2 point). Real failing output,
verbatim (PYTHONPATH=/tmp/opencode/redtest_mass shadowing the editable install; precedence
proven separately by importing jaladhar.coupling.exchange from the shadowed path first):

    E       AssertionError: INVARIANT #1 RED UNDER MUTATION: recomputed total-water residual 5.371817e-03 exceeds the hard bar 1.0e-04 (raw residual 67.23127950922753 m3; if this were the recorded mutation, the excess should approximate cumulative returned/scale = 5.371205e-03)
    E       assert 0.005371817432377899 <= 0.0001
    E
    E   tests/coupling/test_inv01_mass_closes.py:261: AssertionError
    E   =========================== short test summary info ============================
    E   FAILED tests/coupling/test_inv01_mass_closes.py::TestInv01MassCloses::test_total_water_closes_from_raw_lines
    E   ========================= 1 failed, 1 xfailed in 1.54s =========================

The measured excess (67.2313 m3) matches cumulative returned volume (67.2236 m3 + f32 field
rounding) — precisely the spec §12 row #1 failure signature "total-water residual ~= returned
volume". Green control: same command WITHOUT the PYTHONPATH shadow passes — the observable
MOVES with the mutation (V5 satisfied); the live tree never carried it.

V7 scope statement — scope_run vs scope_claimed, as numbers:
  scope_run:     <=200 steps x 4096 cells (64x64 flat closed synthetic domain) x 2000 s
                 simulated, 8-node declared-synthetic chain graph, uniform 110 mm/7200 s
                 storm, CPU-only.
  scope_claimed: city-scale coupled conservation — ~866k cells, 1721-node real drain graph,
                 3 h nowcasting window, 1e4+ adaptive steps.
  The gap is real and named: a green toy-scope run of a city-scale invariant is PARTIAL by
  rule. The BLOCKED companion below xfails loudly naming what closes it (full-domain coupled
  run post GPU authorisation), so the shortfall shows in test output, not just here.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace as dc_replace
from pathlib import Path

import pytest
import torch

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.router import build_drain_graph
from jaladhar.coupling.solver_hook import simulate_coupled
from jaladhar.solver.run import uniform_storm
from jaladhar.solver.state import StaticFields, load_solver_config

REPO = Path(__file__).resolve().parents[2]

# Spec-pinned bars, hardcoded HERE from spec §10.3 text (never imported from the module under
# test: in the /tmp red demo that module is the SHADOWED copy — the bars are the spec's, not
# the code's).
JUDGED_BAR_RELATIVE = 1.0e-4  # invariant #1 hard bar (total-water form)
IDENTITY_TOLERANCE_RELATIVE = 1.0e-3  # reconciliation identity tolerance (§10.3)


# ---------------------------------------------------------------------------
# Synthetic micro-run fixture — same approach as tests/coupling/test_solver_hook.py
# (declared-synthetic, closed domain, injected chain graph; self-contained here by mandate).
# ---------------------------------------------------------------------------


def _static(shape: tuple[int, int], *, drain_cap_m_s: float = 3.2e-6) -> StaticFields:
    """Flat CLOSED synthetic domain with a live legacy drain prior (zeroing must matter)."""
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
        edge_open=(0.0, 0.0, 0.0, 0.0),  # CLOSED: boundary_out == 0 exactly
        shape=shape,
    )


def _chain_graph(rows: int, cols: int, n_nodes: int = 8, cap: float = 0.05):
    """n-node capacity-bearing chain across distinct cells; terminal node is an outfall."""
    base_r, base_c = max(1, rows // 8), max(1, cols // 8)
    cells = [(base_r + i, base_c) for i in range(n_nodes)]
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
        width_mean_m=torch.tensor([6.71] * n_nodes, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, n_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n_nodes, dtype=torch.float64),
        outfall_node=outfall,
        node_cell_row=torch.tensor([c[0] for c in cells], dtype=torch.int32),
        node_cell_col=torch.tensor([c[1] for c in cells], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.ones(n_nodes, dtype=torch.int64),
    )


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


def _solver_cfg() -> dict:
    cfg = load_solver_config(REPO / "configs" / "solver.yaml", REPO)
    cfg = copy.deepcopy(cfg)
    cfg["boundaries"]["mode"] = "closed"
    return cfg


class TestInv01MassCloses:
    def test_total_water_closes_from_raw_lines(self, tmp_path):
        """The invariant itself: ONE end-to-end toy coupled run; the test then recomputes the
        judged residual from raw budget lines + node books and bars it against BOTH the 1e-4
        hard bar AND the replay#2 realized reference level; asserts the D-E separate-line
        requirement and both reconciliation angles.

        mass_check_every is deliberately HUGE so the ledger's own mid-run CouplingMassBreach
        cannot be the catcher (cadence enforcement is covered elsewhere); the explicit final
        ``mass_check`` runs only AFTER this test's independent assertions, so under any
        future regression THIS observable fires first.
        """
        cfg = _coupling_cfg(tmp_path)

        res = simulate_coupled(
            cfg,
            _solver_cfg(),
            REPO,
            graph=_chain_graph(64, 64),
            static=_static((64, 64)),
            rain=uniform_storm(110.0, 7200.0),
            duration_s=2400.0,
            max_steps=200,
            mass_check_every=10_000_000,  # see docstring: keep the LEDGER out of the loop
            snapshot_every_s=900.0,
            smoke=True,
        )
        led = res.ledger

        # --- realized state off DISK (V1): the terminal manifest bytes --------------
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "completed"
        host_lines = man["mass_host_budget_verbatim"]

        # --- claim part 3: SEPARATE explicit lines (D-E), not an alias ---------------
        for key in (
            "captured_to_drains_m3",
            "surcharge_returned_m3",
            "drain_out_net_m3",
            "legacy_drain_out_m3",
        ):
            assert key in man, f"D-E separate line {key!r} missing from the manifest"
        captured_line = float(man["captured_to_drains_m3"])
        returned_line = float(man["surcharge_returned_m3"])
        net_line = float(man["drain_out_net_m3"])
        legacy_line = float(man["legacy_drain_out_m3"])
        # guard c realized: coupling owns the sink — legacy EXACTLY zero, capture positive.
        assert legacy_line == 0.0, (
            f"legacy_drain_out_m3 = {legacy_line!r}: a live legacy sink beside capture "
            "double-counts drainage (guard c)"
        )
        assert host_lines["drain_out_m3"] == 0.0, (
            "host MassBudget verbatim saw non-zero drainage while coupling owned the sink"
        )
        assert captured_line > 0.0, "no volume was ever captured: the closure would be vacuous"
        assert net_line == captured_line - returned_line
        # anti-vacuity of THIS closure check: water actually moved through the graph
        assert returned_line > 0.0 and led.total_surcharging_steps > 0

        # --- V2 angle A: recompute the JUDGED residual from raw lines -----------------
        # V_nodes summed by THIS expression over the final f64 books (not ledger.v_nodes_m3()).
        v_nodes = float(
            (res.node_state_final.vol_in_m3_cum - res.node_state_final.vol_out_m3_cum)
            .sum(dtype=torch.float64)
            .item()
        )
        v_current = float(host_lines["v_current_m3"])
        v_initial = float(host_lines["v_initial_m3"])
        rain_in = float(host_lines["rain_in_m3"])
        infil_out = float(host_lines["infil_out_m3"])
        boundary_out = float(host_lines["boundary_out_m3"])
        clamping = float(host_lines["created_by_clamping_m3"])  # expected EXACTLY 0 (mass.py)

        tw_manual_m3 = (
            v_current
            + v_nodes
            - v_initial
            - rain_in
            + legacy_line
            + infil_out
            + boundary_out
            - clamping
        )
        scale = max(abs(rain_in), abs(v_initial), 1e-12)
        tw_manual_rel = abs(tw_manual_m3) / scale
        print(f"\n[inv01] recomputed total-water residual: {tw_manual_m3!r} m3 "
              f"=> {tw_manual_rel:.6e} relative (scale {scale!r})")
        print(f"[inv01] V_nodes(recomputed from books)={v_nodes!r} "
              f"captured={captured_line!r} returned={returned_line!r} net={net_line!r}")

        assert clamping == 0.0, "water created by clamping is a finding, never foldable"
        assert tw_manual_rel <= JUDGED_BAR_RELATIVE, (
            f"INVARIANT #1 RED UNDER MUTATION: recomputed total-water residual "
            f"{tw_manual_rel:.6e} exceeds the hard bar {JUDGED_BAR_RELATIVE:.1e} "
            f"(raw residual {tw_manual_m3!r} m3; if this were the recorded mutation, "
            f"the excess should approximate cumulative returned/scale = "
            f"{returned_line / scale:.6e})"
        )
        # toy case also closes to the replay#2 realized level (reference read from CONFIG,
        # never hardcoded; config labels it 'reported beside, NOT a gate' — this test IS the
        # place that actually holds the toy case to it).
        reference = cfg.budget.realized_reference_residual
        assert tw_manual_rel <= reference, (
            f"toy-case total-water residual {tw_manual_rel:.6e} exceeded the replay#2 "
            f"realized reference level {reference:.2e}"
        )

        # cross-surface agreement: the manifest's reported judged number is the SAME quantity
        # as this test's independent recomputation (catches transcription/serialization drift)
        assert tw_manual_rel == pytest.approx(float(man["total_water_relative_residual"]), rel=1e-9)
        assert v_nodes == pytest.approx(float(man["v_nodes_t_m3"]), rel=1e-9)

        # --- V2 angle B: telescoping reconciliation, contract scalars vs node books ---
        # Edge fluxes telescope globally => delta(V_nodes) = captured - returned EXACTLY in
        # exact arithmetic; the two sides come from different code paths (f32 field sums vs
        # f64 index_add bookkeeping). A dropped term moves this O(volume); rounding cannot.
        identity_rel = abs(net_line - v_nodes) / max(abs(net_line), abs(v_nodes), 1e-12)
        print(f"[inv01] reconciliation identity |net - V_nodes|/scale = {identity_rel:.3e}")
        assert identity_rel <= IDENTITY_TOLERANCE_RELATIVE, (
            f"reconciliation identity |captured - returned - V_nodes| relative = "
            f"{identity_rel:.6e} > {IDENTITY_TOLERANCE_RELATIVE:.1e}: a bookkeeping term "
            "has been dropped (O(volume)), this is not rounding"
        )

        # --- third angle: contract + V_nodes - total_water == net - legacy, realized ----
        # Documents the D-E separation algebraically on the realized numbers: substituting
        # the NET line (contract form) vs the LEGACY line (total-water form) must reconcile
        # through V_nodes identically. (Load-bearing independence lives in angles A/B.)
        contract_manual_m3 = (
            v_current - v_initial - rain_in + net_line + infil_out + boundary_out - clamping
        )
        lhs = contract_manual_m3 + v_nodes - tw_manual_m3
        assert lhs == pytest.approx(net_line - legacy_line, abs=1e-6)
        # and the contract form is REPORTED beside (never the pass line): it sits ~-V_nodes
        # while water is in flight, here small because the toy window ends near-empty
        assert float(man["relative_residual"]) <= cfg.budget.relative_tolerance

        # --- only NOW the ledger's own cadence-style check (green must survive it too) --
        row = led.mass_check(res.steps)
        assert row["step"] == float(res.steps)
        assert row["total_water_relative_residual"] <= JUDGED_BAR_RELATIVE

    def test_city_scale_full_domain_closure_blocked(self):
        """V7 BLOCKED companion: scope_run above is 4096 cells x <=200 steps; the claimed
        scope of invariant #1 is city-scale conservation. This companion names what closes
        the gap and turns red the day that tier exists."""
        pytest.xfail(
            "BLOCKED (invariant #1, city-scale tier): full-domain coupled run (~866k cells, "
            "1721-node real drain graph, 3 h window, 1e4+ steps) awaits GPU authorisation "
            "post G1-G4; until that run exists, toy-scope closure is PARTIAL by rule"
        )
