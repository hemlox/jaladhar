"""Exchange unit tests — WF-2 coupling (spec §4.3/§6; contract CoupleResult_fields).

Two tiers, mirroring tests/coupling/test_router.py:

TOY (fast) — physics against HAND-COMPUTED magnitudes on tiny declared-synthetic
graphs driven through the SAME production assembly as the loader
(``build_drain_graph`` -> topo/component machinery -> ``couple_step``):

- weir capture magnitude: Cw=1.7, L=6.71 m, h_eff just under 0.1 m,
  dt=0.4802 s, cell_area 100 -> ~1.73 mm/cell-step (task-specified example),
- orifice capture magnitude: Cd=0.65, A_open=0.671 m2 (=6.71*0.1), dh=1 m
  -> ~9 mm/cell-step (task-specified example),
- regime switch INCLUSIVE lower bound weir (exactly 0.1 m -> orifice),
- NO-REVERSE-CAPTURE fires (head_diff <= 0 -> captured exactly 0), V5 red demo,
- capture cap enforced: captured == 0.9*(h-hf_floor) when the hydraulic rate
  exceeds it; ``cap_binding_this_step`` flags it; property holds over an
  adversarial depth field,
- h_new >= 0 EVERYWHERE incl. adversarial depths (algebraic invariant), V5 red
  under CAPTURE_CAP_FRACTION -> 1.1 (negative depths appear; loud RuntimeError),
- autograd flows through capture+return w.r.t. h AND w.r.t. inlet-width
  scaling; V5 red: ``_apply_capture_cap`` mutated to ``.detach()`` -> grads die,
- surcharge activation threshold (strict > freeboard; AT threshold silent;
  barely above it the RETURN CAP binds),
- surcharge-conserves identity (#5): surface-side returned volume == node-side
  vol_out delta (raster scatter vs node bookkeeping — two code paths, V2),
- uniform return distribution across a node's allocated cells,
- ORDER proof: capture -> route -> return within ONE couple_step (a node pushed
  past freeboard by JUST-ROUTED inflow surcharges in the same call),
- entry assert rejects unzeroed/NaN drain_cap (guard a); purity of
  h/qx/qy/node_state (Phase-2 incident class); dt/dtype/shape validation,
- cumulative books accumulate f64 monotonically across steps,

SCALE probe (fast, declared-synthetic): the full pipeline on a 512x512 tile
with a 64-node lattice DAG — execution evidence for the O(cells) fused pass at
V12-relevant tile sizes while the WF-1 artefact seam is blocked upstream.

REAL DATA (marked ``slow``; skip with ``-m 'not slow'``):
- pinned-constant <-> resolved-config seam check (V8-style),
- DELIVERABLE 3: one end-to-end couple_step on the REAL graph via
  ``router.load_drain_graph`` (configs/coupling.yaml paths), uniform h=0.5 m
  tile sized to the producer grid, dt=0.48, printing captured_m3 / returned_m3 /
  surcharging_nodes.

BLOCKED-state handling (V7): the ONLY realized drain-graph artefact on disk is
rejected by the frozen WF-1 reader itself
(``[consumer_assertion_3] zero_length_dropped_count=42 != 0`` — no sanctioned
override exists for that class; only self-loops got M5b adjudication). Exactly
as in test_router.py, the real tier xfails LOUDLY naming the changes that close
the gap; the instant one lands, these tests execute for real.

V7 scope statements live in each test docstring. V2 observables: hand-computed
references are scalar arithmetic in THIS file sharing no code with the module;
the conservation identity compares the RETURNED FIELD against the NODE BOOKS'
delta (different code paths); every mutation demo asserts its observable MOVES.
CPU-only throughout (V12); no CUDA allocation anywhere.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import torch

from jaladhar.coupling import exchange as xchg
from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.exchange import (
    CoupleResult,
    NodeState,
    build_node_state,
    couple_step,
    zero_drain_cap_out_of_place,
)
from jaladhar.coupling.router import (
    DrainGraph,
    RefuseLoadError,
    build_drain_graph,
    load_drain_graph,
)
from jaladhar.solver.state import StaticFields

REPO = Path(__file__).resolve().parents[2]

# Hand-computation constants — typed LITERALLY here (V2: the references share

CW = 1.7
CD = 0.65
G = 9.81
HF = 0.001
AREA = 100.0


def _toy_graph(
    edges: list[tuple[int, int, float | None]],
    num_nodes: int,
    *,
    widths: list[float] | None = None,
    nmap: torch.Tensor | None = None,
    counts: list[int] | None = None,
    outfalls: tuple[int, ...] = (),
) -> DrainGraph:
    inert_padding = not edges
    if inert_padding:
        edges = [(1, 2, None)]
        if num_nodes < 2:
            num_nodes = 2
        if widths is not None and len(widths) < num_nodes:
            widths = list(widths) + [2.285] * (num_nodes - len(widths))
    n_edges = len(edges)
    edge_from = torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64)
    edge_to = torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64)
    cb = torch.tensor([e[2] is not None for e in edges], dtype=torch.bool)
    raw = torch.tensor([0.0 if e[2] is None else float(e[2]) for e in edges], dtype=torch.float64)
    q_cap = torch.where(cb, raw, torch.full((n_edges,), float("nan"), dtype=torch.float64))
    width = torch.tensor(widths or [6.71] * num_nodes, dtype=torch.float64)
    outfall = torch.zeros(num_nodes, dtype=torch.bool)
    for o in outfalls:
        outfall[o - 1] = True
    if nmap is None:
        nmap = torch.tensor([[i + 1 for i in range(num_nodes)]], dtype=torch.int32)
    if counts is None:
        counts = [int((nmap == i + 1).sum()) for i in range(num_nodes)]
    return build_drain_graph(
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=cb,
        q_cap_nom_m3s=q_cap,
        width_mean_m=width,
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, num_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(num_nodes, dtype=torch.float64),
        outfall_node=outfall,
        node_cell_row=torch.arange(num_nodes, dtype=torch.int32),
        node_cell_col=torch.arange(num_nodes, dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.tensor(counts, dtype=torch.int64),
    )


def _static_zero(shape: tuple[int, int]) -> StaticFields:
    hh, ww = shape

    def z(*s):
        return torch.zeros(*s, dtype=torch.float32)

    return StaticFields(
        dz_x=z(hh, ww - 1),
        dz_y=z(hh - 1, ww),
        n_x=z(hh, ww - 1),
        n_y=z(hh - 1, ww),
        c_x=z(hh, ww - 1),
        c_y=z(hh - 1, ww),
        drain_cap_m_s=z(hh, ww),
        infil_rate_m_s=z(hh, ww),
        edge_w_n=z(hh),
        edge_e_n=z(hh),
        edge_n_n=z(ww),
        edge_s_n=z(ww),
        edge_w_s=z(hh),
        edge_e_s=z(hh),
        edge_n_s=z(ww),
        edge_s_s=z(ww),
        shape=shape,
    )


def _state(heads: list[float], n: int | None = None) -> NodeState:
    n = len(heads) if n is None else n
    hs = torch.tensor(heads + [0.0] * (n - len(heads)), dtype=torch.float32)
    z64 = torch.zeros(n, dtype=torch.float64)
    return NodeState(h_node_m=hs, vol_in_m3_cum=z64.clone(), vol_out_m3_cum=z64.clone())


def _step(h, graph, node_state=None, dt=0.48, static=None) -> CoupleResult:
    st = static if static is not None else _static_zero(tuple(h.shape))
    ns = node_state if node_state is not None else build_node_state(graph)
    hh, ww = h.shape
    return couple_step(h, torch.zeros(hh, ww - 1), torch.zeros(hh - 1, ww), st, ns, graph, dt)


class TestCaptureMagnitudes:
    def test_weir_hand_computed(self):
        """Cw=1.7, L=6.71 m, h_eff just below 0.1 m, dt=0.4802 s -> ~1.73 mm/cell.

        V7 scope: 1 cell, 1 node, single step, weir regime only."""
        g = _toy_graph([], 1, widths=[6.71], nmap=torch.tensor([[1]], dtype=torch.int32))
        h = torch.full((1, 1), 0.1 - 1e-6, dtype=torch.float32)
        cr = _step(h, g, dt=0.4802)
        h_eff = float(h.clamp(min=0)[0, 0])
        expected = CW * 6.71 * h_eff**1.5 * 0.4802 / AREA
        got = float(cr.captured_depth_m[0, 0])
        assert abs(got - expected) <= 1e-6 * expected, (got, expected)
        assert 1.5e-3 < got < 2.0e-3
        assert cr.cap_binding_this_step == 0

        orifice_val = CD * (6.71 * 0.1) * math.sqrt(2 * G * h_eff) * 0.4802 / AREA
        assert abs(got - orifice_val) > 0.3 * expected
        assert cr.capture_m3 == pytest.approx(got * AREA, rel=1e-9)

    def test_orifice_hand_computed(self):
        """Cd=0.65, A_open=0.671 m2 (=6.71*0.1), dh=1 m, dt=0.4802 s -> ~9 mm/cell.

        V7 scope: 1 cell, 1 node, single step, orifice regime only."""
        g = _toy_graph([], 1, widths=[6.71], nmap=torch.tensor([[1]], dtype=torch.int32))
        h = torch.full((1, 1), 1.0, dtype=torch.float32)
        cr = _step(h, g, dt=0.4802)
        dh = float(h[0, 0])
        expected = CD * (6.71 * 0.1) * math.sqrt(2 * G * dh) * 0.4802 / AREA
        got = float(cr.captured_depth_m[0, 0])
        assert abs(got - expected) <= 1e-6 * expected, (got, expected)
        assert 8.5e-3 < got < 10.0e-3
        weir_val = CW * 6.71 * dh**1.5 * 0.4802 / AREA
        assert abs(got - weir_val) > 0.3 * expected

    def test_regime_switch_inclusive_lower_bound_is_orifice(self):
        """h_eff == 0.1 EXACTLY is orifice ('inclusive lower bound weir' puts
        the bound itself in the orifice branch: weir iff h_eff < 0.1).

        V7 scope: 1 cell, 1 node, single step at the switch boundary."""
        g = _toy_graph([], 1, widths=[6.71], nmap=torch.tensor([[1]], dtype=torch.int32))
        h = torch.full((1, 1), 0.1, dtype=torch.float32)
        cr = _step(h, g, dt=0.4802)
        dh = float(h[0, 0])
        got = float(cr.captured_depth_m[0, 0])
        orifice_val = CD * 0.671 * math.sqrt(2 * G * dh) * 0.4802 / AREA
        weir_val = CW * 6.71 * dh**1.5 * 0.4802 / AREA
        assert abs(got - orifice_val) <= 1e-6 * orifice_val, (got, orifice_val)
        assert abs(got - weir_val) > 0.3 * orifice_val


class TestCaptureGuards:
    def test_no_reverse_capture_fires_and_red_demo(self, monkeypatch):
        """A node whose head exceeds the surface NEVER pulls water out: with
        h=0.05 (weir regime, forms evaluate positive) but node head 0.5,
        captured is EXACTLY 0 and h_new is bitwise h.

        V5 RED DEMO (mutation recorded here): monkeypatching
        ``_no_reverse_gate`` to all-ones (guard removed — the missing
        head_diff-test defect class) makes captured > 0 on the same fixture;
        the observable MOVES. Reverts on teardown."""
        g = _toy_graph([], 1, widths=[6.71], nmap=torch.tensor([[1]], dtype=torch.int32))
        h = torch.full((1, 1), 0.05, dtype=torch.float32)
        cr = _step(h, g, node_state=_state([0.5], n=2), dt=0.48)
        assert float(cr.captured_depth_m[0, 0]) == 0.0
        assert cr.capture_m3 == 0.0
        assert torch.equal(cr.h_new, h), "sealed exchange still moved surface water"
        assert cr.surcharging_nodes == 0

        monkeypatch.setattr(xchg, "_no_reverse_gate", lambda hd: torch.ones_like(hd))
        cr_bad = _step(h, g, node_state=_state([0.5], n=2), dt=0.48)
        assert (
            float(cr_bad.captured_depth_m[0, 0]) > 0.0
        ), "mutation undetected: removing the guard did not fire reverse capture"

    def test_capture_cap_enforced_and_binding_flag(self):
        """Hydraulic rate >> cap -> captured == 0.9*(h - hf_floor) EXACTLY,
        cap_binding_this_step == 1, h_new >= 0.

        V7 scope: wide-inlet synthetic node (L=5000 m) chosen so the orifice
        rate dwarfs the stability cap; single cell, single step."""
        g = _toy_graph([], 1, widths=[5000.0], nmap=torch.tensor([[1]], dtype=torch.int32))
        h = torch.full((1, 1), 1.0, dtype=torch.float32)
        cr = _step(h, g, dt=0.48)
        cap = 0.9 * (float(h[0, 0]) - HF)
        got = float(cr.captured_depth_m[0, 0])

        assert got == pytest.approx(cap, rel=1e-7), (got, cap)
        assert cr.cap_binding_this_step == 1
        assert float(cr.h_new[0, 0]) == pytest.approx(float(h[0, 0]) - cap, rel=1e-7)

    def test_h_new_nonnegative_adversarial_field(self):
        """PROPERTY: h_new >= 0 everywhere, every step, over an adversarial
        depth field (zeros, denormals, hf_floor, the switch value, huge
        depths) x several dt — algebraic consequence of the cap. Negative
        INPUT depths are out of contract (host limiter guarantees h >= 0) and
        are refused at entry (see next test).

        V7 scope: 3x3 tile, 2 nodes / 4 allocated cells, dt in {0.01, 0.48,
        5.0}, 3 steps each; depths span [1e-30, 1e6] m; mixed node heads."""
        g = _toy_graph(
            [],
            2,
            widths=[6.71, 5000.0],
            nmap=torch.tensor([[1, 1, -1], [2, 2, -1], [-1, -1, -1]], dtype=torch.int32),
            counts=[2, 2],
        )
        adv = torch.tensor(
            [
                [0.0, 1e-30, 1e-9],
                [HF, 0.1, 0.5],
                [77.0, 1.0e6, 0.049],
            ],
            dtype=torch.float32,
        )
        state = _state([0.3, 2.0])
        for dt in (0.01, 0.48, 5.0):
            h = adv.clone()
            for _ in range(3):
                h_before = h.clone()
                cr = _step(h, g, node_state=state, dt=dt)
                state = cr.node_state_new
                assert bool((cr.h_new >= 0).all()), f"negative depth at dt={dt}"
                assert bool((cr.captured_depth_m >= 0).all())
                assert bool((cr.returned_depth_m >= 0).all())

                cap_field = 0.9 * torch.clamp(h_before.to(torch.float64) - HF, min=0.0)
                got = cr.captured_depth_m.to(torch.float64)
                assert bool(
                    (got <= cap_field * (1 + 1e-6) + 1e-12).all()
                ), f"cap exceeded at dt={dt}"

    def test_negative_input_depth_refused_at_entry(self):
        g = _toy_graph([], 1, widths=[6.71], nmap=torch.tensor([[1]], dtype=torch.int32))
        with pytest.raises(ValueError, match="negative depths"):
            _step(torch.full((1, 1), -0.5, dtype=torch.float32), g)

    def test_v5_red_cap_fraction_mutation_produces_negative_depth(self, monkeypatch):
        """V5 RED DEMO (invariant #4's named mutation, recorded here):
        CAPTURE_CAP_FRACTION 0.9 -> 1.1 breaks the algebraic guarantee; the
        exit guard fires LOUD (RuntimeError naming negative depth) instead of
        handing negative depths to the solver. Pristine control first: green."""
        g = _toy_graph([], 1, widths=[5000.0], nmap=torch.tensor([[1]], dtype=torch.int32))
        h = torch.full((1, 1), 0.25, dtype=torch.float32)
        cr = _step(h, g, dt=0.48)
        assert bool((cr.h_new >= 0).all())

        monkeypatch.setattr(xchg, "CAPTURE_CAP_FRACTION", 1.1)
        with pytest.raises(RuntimeError, match="negative"):
            _step(h, g, dt=0.48)


class TestSurchargeReturn:
    def test_activation_threshold_behavior(self):
        """Return fires iff node head STRICTLY above freeboard 1.5 m; AT the
        threshold silent; barely above it the RETURN CAP binds (R_node ==
        0.9 * excess-above-freeboard volume exactly).

        V7 scope: isolated 2.285 m-shaft node (plan area 2.285 m2), 1 cell,
        single steps at three heads straddling 1.5."""
        g = _toy_graph([], 1, widths=[2.285], nmap=torch.tensor([[1]], dtype=torch.int32))
        h = torch.full((1, 1), 0.05, dtype=torch.float32)
        pa = 2.285
        head_pre = float(np.nextafter(np.float32(1.5), np.float32(2.0)))

        cr_below = _step(h, g, node_state=_state([1.49], n=2), dt=0.48)
        assert cr_below.return_m3 == 0.0 and cr_below.surcharging_nodes == 0

        cr_at = _step(h, g, node_state=_state([1.5], n=2), dt=0.48)
        assert cr_at.return_m3 == 0.0 and cr_at.surcharging_nodes == 0, "strict '>' violated"

        cr_above = _step(h, g, node_state=_state([head_pre], n=2), dt=0.48)
        assert cr_above.surcharging_nodes == 1
        assert cr_above.return_m3 > 0.0
        excess_vol = head_pre * pa - 1.5 * pa
        assert cr_above.return_m3 <= 0.9 * excess_vol * (1 + 1e-6)
        assert cr_above.cap_binding_this_step == 1

    def test_surcharge_conserves_identity_and_uniform_distribution(self):
        """SURCHARGE-CONSERVES (#5): surface-side returned volume
        (SUM(returned_depth)*cell_area) == node-side removed volume (delta
        vol_out_m3_cum) — raster scatter vs node bookkeeping, two different
        code paths (V2 non-mirror). Distribution across a node's 3 allocated
        cells is UNIFORM (bitwise-identical increments).

        V7 scope: 1x3 tile, single node owning all 3 cells (count=3), one
        step, orifice-regime return."""
        g = _toy_graph(
            [], 1, widths=[2.285], nmap=torch.tensor([[1, 1, 1]], dtype=torch.int32), counts=[3]
        )
        h = torch.zeros((1, 3), dtype=torch.float32)
        cr = _step(h, g, node_state=_state([3.0], n=2), dt=0.48)

        depth_side = float(cr.returned_depth_m.to(torch.float64).sum().item()) * AREA
        node_side = float(cr.node_state_new.vol_out_m3_cum[0].item())
        assert node_side > 0.0
        rel = abs(depth_side - node_side) / node_side
        assert rel <= 2e-6, f"identity broken beyond f32 field rounding: rel={rel:.3e}"

        inc = cr.returned_depth_m[0]
        assert float(inc[0]) == float(inc[1]) == float(inc[2]), "non-uniform distribution"
        assert float(inc[0]) == pytest.approx(node_side / (3 * AREA), rel=2e-6)

    def test_capture_route_return_order_same_step(self):
        """ORDER PROOF: a node pushed past freeboard by JUST-ROUTED inflow
        surcharges within the SAME couple_step — impossible unless capture
        lands before route() and return runs after it (spec §4.3 pinned order).

        Fixture numbers (hand-derived): node1 (wide inlet L=5000, pond 2.0 m,
        empty) captures at the CAP: depth 0.9*(2-0.001) = 1.7991 m -> volume
        179.91 m3 over its one 100 m2 cell. Routing caps the single edge at 50
        m3/s -> exactly 24.0 m3 reaches node2 THIS STEP. node2 (shaft area
        2.285 m2) starts a hair BELOW freeboard (3.4265 m3); the routed inflow
        lifts it to ~12 m head -> orifice surge onto its own cell in the same
        call. V7 scope: 2x2 tile, 2 nodes, 1 capacity-bearing edge, one step."""
        g = _toy_graph(
            [(1, 2, 50.0)],
            2,
            widths=[5000.0, 2.285],
            nmap=torch.tensor([[1, -1], [2, -1]], dtype=torch.int32),
            outfalls=(2,),
        )
        h = torch.zeros((2, 2), dtype=torch.float32)
        h[0, 0] = 2.0
        head2 = float(np.float32((1.5 * 2.285 - 1e-3) / 2.285))
        state = _state([0.0, head2])

        cr = _step(h, g, node_state=state, dt=0.48)

        captured_expect = 0.9 * (2.0 - HF) * AREA
        assert cr.cap_binding_this_step == 1
        assert cr.capture_m3 == pytest.approx(captured_expect, rel=1e-6)

        inflow = float(cr.node_state_new.vol_in_m3_cum[1].item())
        expected_inflow = min(50.0, captured_expect / 0.48) * 0.48
        assert inflow == pytest.approx(expected_inflow, rel=1e-5)

        assert cr.surcharging_nodes == 1
        assert cr.return_m3 > 0.0
        assert float(cr.returned_depth_m[1, 0]) > 0.0
        assert float(cr.returned_depth_m[0, 0]) == 0.0

        vol2 = (1.5 * 2.285 - 1e-3) + expected_inflow
        excess = vol2 / 2.285 - 1.5
        q_ret = CD * (2.285 * 0.1) * math.sqrt(2 * G * excess)
        r_expect = min(q_ret * 0.48, 0.9 * (vol2 - 1.5 * 2.285))
        assert cr.return_m3 == pytest.approx(r_expect, rel=1e-4)


def _tracked_width_graph(width_tracked: torch.Tensor) -> DrainGraph:
    return build_drain_graph(
        edge_from=torch.zeros(1, dtype=torch.int64),
        edge_to=torch.ones(1, dtype=torch.int64),
        capacity_bearing=torch.zeros(1, dtype=torch.bool),
        q_cap_nom_m3s=torch.full((1,), float("nan"), dtype=torch.float64),
        width_mean_m=torch.cat([width_tracked, torch.full((1,), 2.285, dtype=width_tracked.dtype)]),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.zeros(2, dtype=torch.float64),
        contrib_area_m2=torch.zeros(2, dtype=torch.float64),
        outfall_node=torch.tensor([False, True]),
        node_cell_row=torch.zeros(2, dtype=torch.int32),
        node_cell_col=torch.arange(2, dtype=torch.int32),
        node_id_map=torch.tensor([[1]], dtype=torch.int32),
        node_cell_count=torch.tensor([1, 0], dtype=torch.int64),
    )


class TestAutograd:
    def test_gradients_flow_through_capture_and_return(self):
        """Gradients reach BOTH mechanisms: finite non-zero h.grad through a
        capture-dominated step, and non-zero grad w.r.t. an inlet-WIDTH scale
        feeding L_weir / A_open / plan-area (replay()-calibration path).

        V7 scope: 1 cell / 1 node, single steps, orifice regime."""
        g = _toy_graph([], 1, widths=[6.71], nmap=torch.tensor([[1]], dtype=torch.int32))

        h = torch.full((1, 1), 1.0, dtype=torch.float32, requires_grad=True)
        cr = _step(h, g, dt=0.48)
        (cr.h_new**2).sum().backward()
        assert h.grad is not None and bool(torch.isfinite(h.grad))
        assert float(h.grad.abs().sum().item()) > 0.0, "no gradient through capture"

        scale = torch.tensor(1.37, dtype=torch.float64, requires_grad=True)
        g_t = _tracked_width_graph(torch.tensor([6.71], dtype=torch.float64) * scale)
        h2 = torch.full((1, 1), 1.0, dtype=torch.float32, requires_grad=True)
        cr2 = _step(h2, g_t, dt=0.48)
        (cr2.h_new**2).sum().backward()
        assert scale.grad is not None and bool(torch.isfinite(scale.grad))
        assert float(scale.grad.item()) != 0.0, "no gradient w.r.t. width scaling"

    def test_v5_red_detach_mutant_kills_gradients(self, monkeypatch):
        """V5 RED DEMO (contract v5_mutant_test_required class, recorded here):
        mutating ``_apply_capture_cap`` to detach its output severs the capture
        gradient. The loss ISOLATES the exchange delta (h_before - h_new), so
        the pristine call has a finite non-zero grad while the mutant's grad is
        EXACTLY zero (the +h and -h paths cancel; only the captured path ever
        carried gradient). First draft used loss = h_new**2, whose direct +h
        term keeps a live gradient even under the mutation — that observable
        did not move and was replaced (V6: the TEST was wrong, the code and
        spec are right)."""
        g = _toy_graph([], 1, widths=[6.71], nmap=torch.tensor([[1]], dtype=torch.int32))

        def _delta_loss(h_in: torch.Tensor, res: CoupleResult) -> torch.Tensor:
            return ((h_in - res.h_new) ** 2).sum()

        h_pristine = torch.full((1, 1), 1.0, dtype=torch.float32, requires_grad=True)
        cr = _step(h_pristine, g, dt=0.48)
        _delta_loss(h_pristine, cr).backward()
        assert h_pristine.grad is not None
        assert float(h_pristine.grad.abs().sum().item()) > 0.0, "control produced no gradient"

        def _detached(rate, cap):
            return torch.minimum(rate, cap).detach()

        monkeypatch.setattr(xchg, "_apply_capture_cap", _detached)
        h_mut = torch.full((1, 1), 1.0, dtype=torch.float32, requires_grad=True)
        cr_mut = _step(h_mut, g, dt=0.48)
        _delta_loss(h_mut, cr_mut).backward()
        grad_sum = 0.0 if h_mut.grad is None else float(h_mut.grad.abs().sum().item())
        assert grad_sum == 0.0, "mutation undetected: gradients survived the detach"


class TestGuardsAndPurity:
    def test_entry_assert_rejects_unzeroed_static(self):
        g = _toy_graph([], 1, nmap=torch.tensor([[1]], dtype=torch.int32))
        h = torch.full((1, 1), 0.5, dtype=torch.float32)
        base = _static_zero((1, 1))
        bad = replace(base, drain_cap_m_s=torch.full((1, 1), 3.2e-6, dtype=torch.float32))
        with pytest.raises(ValueError, match="guard \\(a\\)"):
            _step(h, g, static=bad)
        bad_nan = replace(base, drain_cap_m_s=torch.full((1, 1), float("nan"), dtype=torch.float32))
        with pytest.raises(ValueError, match="guard \\(a\\)"):
            _step(h, g, static=bad_nan)

    def test_zero_drain_cap_out_of_place_is_out_of_place(self):
        s_live = _static_zero((2, 2))
        s_live = replace(s_live, drain_cap_m_s=torch.full((2, 2), 1e-5, dtype=torch.float32))
        s_zero = zero_drain_cap_out_of_place(s_live)
        assert float(s_live.drain_cap_m_s.abs().sum().item()) > 0.0, "original mutated!"
        assert bool((s_zero.drain_cap_m_s == 0).all())
        assert s_zero.drain_cap_m_s.data_ptr() != s_live.drain_cap_m_s.data_ptr()

    def test_purity_inputs_bitwise_unchanged(self):
        g = _toy_graph(
            [(1, 2, 5.0)],
            2,
            widths=[6.71, 2.285],
            nmap=torch.tensor([[1, -1], [2, -1]], dtype=torch.int32),
            outfalls=(2,),
        )
        h = torch.full((2, 2), 0.8, dtype=torch.float32)
        qx, qy = torch.zeros(2, 1), torch.zeros(1, 2)
        state = _state([0.1, 1.6])
        hb, qb, qyb = h.clone(), qx.clone(), qy.clone()
        sb = (state.h_node_m.clone(), state.vol_in_m3_cum.clone(), state.vol_out_m3_cum.clone())

        cr = couple_step(h, qx, qy, _static_zero((2, 2)), state, g, 0.48)

        assert torch.equal(h, hb) and torch.equal(qx, qb) and torch.equal(qy, qyb)
        assert torch.equal(state.h_node_m, sb[0])
        assert torch.equal(state.vol_in_m3_cum, sb[1])
        assert torch.equal(state.vol_out_m3_cum, sb[2])
        assert cr.h_new.data_ptr() != h.data_ptr()
        assert cr.captured_depth_m.data_ptr() != h.data_ptr()

    def test_input_validation_rejections(self):
        g = _toy_graph([], 1, nmap=torch.tensor([[1]], dtype=torch.int32))
        good = torch.full((1, 1), 0.5, dtype=torch.float32)
        st = _static_zero((1, 1))
        ns = build_node_state(g)
        for bad_dt in (0.0, -0.5, float("nan"), True):
            with pytest.raises(ValueError, match="dt"):
                couple_step(good, torch.zeros(1, 0), torch.zeros(0, 1), st, ns, g, bad_dt)
        with pytest.raises(ValueError, match="h must be"):
            _step(good.to(torch.float64), g)
        g2 = _toy_graph([], 1, nmap=torch.tensor([[1, -1]], dtype=torch.int32))
        with pytest.raises(ValueError, match="shape"):
            _step(torch.zeros((1, 1), dtype=torch.float32), g2)
        with pytest.raises(ValueError, match="NodeState"):
            couple_step(good, torch.zeros(1, 0), torch.zeros(0, 1), st, object(), g, 0.48)
        if not torch.cuda.is_available():
            with pytest.raises(ValueError, match="CPU-only"):
                build_node_state(g, device="cuda")

    def test_build_node_state_initial(self):
        g = _toy_graph([], 3, nmap=torch.tensor([[1, 2, 3]], dtype=torch.int32))
        ns = build_node_state(g, "cpu")
        assert ns.h_node_m.dtype is torch.float32 and tuple(ns.h_node_m.shape) == (3,)
        assert bool((ns.h_node_m == 0).all())
        assert ns.vol_in_m3_cum.dtype is torch.float64
        assert ns.vol_out_m3_cum.dtype is torch.float64
        assert bool((ns.vol_in_m3_cum == 0).all()) and bool((ns.vol_out_m3_cum == 0).all())

    def test_books_accumulate_f64_monotonic_multistep(self):
        g = _toy_graph(
            [(1, 2, 5.0)],
            2,
            widths=[6.71, 2.285],
            nmap=torch.tensor([[1, -1], [2, -1]], dtype=torch.int32),
            outfalls=(2,),
        )
        h = torch.full((2, 2), 1.2, dtype=torch.float32)
        state = build_node_state(g)
        prev_in = prev_out = 0.0
        for _ in range(6):
            cr = _step(h, g, node_state=state, dt=0.48)
            state = cr.node_state_new
            assert bool(torch.isfinite(state.vol_in_m3_cum).all())
            assert bool(torch.isfinite(state.vol_out_m3_cum).all())
            assert float(state.vol_in_m3_cum.sum()) >= prev_in
            assert float(state.vol_out_m3_cum.sum()) >= prev_out
            assert state.h_node_m.dtype is torch.float32
            prev_in = float(state.vol_in_m3_cum.sum())
            prev_out = float(state.vol_out_m3_cum.sum())
        assert prev_in > 0.0


class TestScale:
    def test_scale_tile_512_end_to_end(self, capsys):
        """SCALE PROBE (declared-synthetic, NOT Bengaluru data): the full
        couple_step pipeline on a 512x512 tile with a 64-node lattice DAG —
        execution evidence that the O(cells) fused elementwise pass and O(E+N)
        graph ops execute at V12-relevant tile sizes.

        V7 scope: 262,144 cells, 64 nodes / 112 edges, 5 steps at dt=0.48,
        uniform 0.75 m initial pond; scope claimed: module executes at smoke
        tile sizes; full-domain behaviour covered by the real-data tier."""
        n_side, block, per_row = 512, 64, 8
        nmap = torch.full((n_side, n_side), -1, dtype=torch.int32)
        for i in range(per_row):
            for j in range(per_row):
                nid = i * per_row + j + 1
                nmap[i * block : (i + 1) * block, j * block : (j + 1) * block] = nid
        num_nodes = per_row * per_row
        edges = []
        for i in range(per_row):
            for j in range(per_row):
                nid = i * per_row + j + 1
                if j + 1 < per_row:
                    edges.append((nid, nid + 1, 3.0))
                if i + 1 < per_row:
                    edges.append((nid, nid + per_row, 3.0))
        g = _toy_graph(
            edges,
            num_nodes,
            widths=[6.71] * num_nodes,
            nmap=nmap,
            counts=[block * block] * num_nodes,
            outfalls=(num_nodes,),
        )
        h = torch.full((n_side, n_side), 0.75, dtype=torch.float32)
        state = build_node_state(g)
        cum_cap = cum_ret = 0.0
        for s in range(5):
            t0 = time.perf_counter()
            cr = _step(h, g, node_state=state, dt=0.48)
            wall_ms = (time.perf_counter() - t0) * 1e3
            h, state = cr.h_new, cr.node_state_new
            cum_cap += cr.capture_m3
            cum_ret += cr.return_m3
            assert bool((h >= 0).all())
            print(
                f"[scale-512] step {s}: wall={wall_ms:.1f} ms captured_cum={cum_cap:.3f} m3 "
                f"returned_cum={cum_ret:.6f} m3 surcharging={cr.surcharging_nodes} "
                f"max_head={cr.max_node_head_m:.4f} m"
            )
        assert cum_cap > 0.0
        assert cum_ret >= 0.0
        assert "[scale-512]" in capsys.readouterr().out


class TestEntryFiniteBooks:
    def test_nan_books_refused_loudly_at_entry(self):
        """RED DEMO (item F-minor, silent-NaN-manifest incident class): NaN/Inf in ANY
        NodeState book previously PASSED entry (dtype/shape checks only), let every
        downstream gate 'pass' (rel=NaN compares False against each tolerance), and ended
        the run as a manifest containing literal NaN — invalid JSON. GREEN: couple_step
        refuses at the seam, same refusal class/style as the negative-h check.

        V7 scope: 1 refused call per book field on a 2-node toy graph; no exchange runs."""
        g = _toy_graph([], 1, nmap=torch.tensor([[1, 2]], dtype=torch.int32))
        h = torch.full((1, 2), 0.5, dtype=torch.float32)

        nan_state = NodeState(
            h_node_m=torch.zeros(2, dtype=torch.float32),
            vol_in_m3_cum=torch.tensor([0.0, float("nan")], dtype=torch.float64),
            vol_out_m3_cum=torch.zeros(2, dtype=torch.float64),
        )
        with pytest.raises(ValueError, match="non-finite"):
            _step(h, g, node_state=nan_state)

        inf_out = NodeState(
            h_node_m=torch.tensor([float("inf"), 0.0], dtype=torch.float32),
            vol_in_m3_cum=torch.zeros(2, dtype=torch.float64),
            vol_out_m3_cum=torch.tensor([0.0, float("inf")], dtype=torch.float64),
        )
        with pytest.raises(ValueError, match="non-finite"):
            _step(h, g, node_state=inf_out)

    def test_finite_control_still_exchanges(self):
        g = _toy_graph(
            [(1, 2, 5.0)],
            2,
            widths=[6.71, 2.285],
            nmap=torch.tensor([[1, -1], [2, -1]], dtype=torch.int32),
            outfalls=(2,),
        )
        cr = _step(torch.full((2, 2), 0.8, dtype=torch.float32), g)
        assert bool(torch.isfinite(cr.node_state_new.vol_in_m3_cum).all())
        assert cr.capture_m3 > 0.0


@pytest.fixture(scope="session")
def real_cfg():
    return resolve_config(REPO / "configs" / "coupling.yaml", REPO)


@pytest.mark.slow
def test_constants_match_resolved_config(real_cfg):
    import yaml

    cfg = real_cfg
    assert xchg.WEIR_COEFF_CW == cfg.exchange.cw
    assert xchg.ORIFICE_COEFF_CD == cfg.exchange.cd
    assert xchg.HF_FLOOR_M == cfg.exchange.hf_floor_m
    assert xchg.REGIME_SWITCH_M == cfg.exchange.regime_switch_m
    assert xchg.CAPTURE_CAP_FRACTION == cfg.exchange.capture_cap_fraction
    assert xchg.RETURN_CAP_FRACTION == cfg.exchange.return_cap_fraction
    assert xchg.A_OPEN_WIDTH_FRACTION == cfg.exchange.a_open_width_fraction
    assert xchg.ASSUMED_FREEBOARD_M == cfg.storage.assumed_freeboard_m
    solver = yaml.safe_load((REPO / "configs" / "solver.yaml").read_text())
    assert xchg.HF_FLOOR_M == solver["physics"]["hf_floor_m"]
    assert xchg.GRAVITY_M_S2 == solver["physics"]["gravity_m_s2"]
    assert xchg.CELL_AREA_M2 == 100.0


@pytest.mark.slow
def test_real_graph_end_to_end_couple_step(real_cfg, capsys):
    """DELIVERABLE 3: one end-to-end couple_step on the REAL graph loaded via
    router.load_drain_graph (configs/coupling.yaml paths), uniform h=0.5 m tile
    sized to the producer grid, dt=0.48 s; prints captured_m3, returned_m3,
    surcharging_nodes.

    BLOCKED upstream (V7): see module docstring — the frozen WF-1 reader
    refuses the only realized artefact (consumer_assertion_3,
    zero_length_dropped_count=42). Closes when WF-1 republishes without
    dropped zero-length edges OR the owner sanctions the zero-length class
    like M5b; until then this xfails LOUDLY rather than passing vacuously."""
    try:
        g = load_drain_graph(real_cfg, REPO)
    except RefuseLoadError as exc:
        if "zero_length_dropped_count" in str(exc):
            pytest.xfail(
                "BLOCKED (external to exchange unit): frozen graph_io.read_artefact rejects "
                f"the only realized artefact -> {exc}; closes when WF-1 republishes without "
                "dropped zero-length edges OR owner sanctions the zero-length class like M5b"
            )
        raise
    rows, cols = (int(v) for v in g.node_id_map.shape)
    h = torch.full((rows, cols), 0.5, dtype=torch.float32)
    cr = couple_step(
        h,
        torch.zeros(rows, cols - 1, dtype=torch.float32),
        torch.zeros(rows - 1, cols, dtype=torch.float32),
        zero_drain_cap_out_of_place(_static_zero((rows, cols))),
        build_node_state(g),
        g,
        0.48,
    )
    print(
        f"\n[e2e-real-couple] grid={rows}x{cols} nodes={g.num_nodes} edges={g.num_edges} "
        f"allocated_cells={int(g.node_cell_count.sum().item())}"
    )
    print(
        f"[e2e-real-couple] captured_m3={cr.capture_m3!r} returned_m3={cr.return_m3!r} "
        f"surcharging_nodes={cr.surcharging_nodes} cap_binding={cr.cap_binding_this_step} "
        f"max_node_head_m={cr.max_node_head_m!r}"
    )

    indep = float(cr.captured_depth_m.to(torch.float64).sum().item()) * 100.0
    assert cr.capture_m3 == indep, "capture_m3 is not the exact field sum x cell_area"
    assert cr.capture_m3 > 0.0, "a 0.5 m pond over allocated inlets must capture"
    assert cr.return_m3 >= 0.0
    assert bool((cr.h_new >= 0).all())
    assert tuple(cr.edge_flow_m3s.shape) == (g.num_edges,)
    assert cr.edge_flow_m3s.dtype is torch.float32
    assert "[e2e-real-couple]" in capsys.readouterr().out
