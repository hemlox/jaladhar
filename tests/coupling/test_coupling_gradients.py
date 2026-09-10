"""If gradients were severed, I would observe: autograd.grad(loss, capacity_scale)
becomes EXACTLY 0.0 or non-finite (NaN) while every forward depth stays finite.

Contract file for ``configs/contracts/coupling_iface.json``
``autograd_rules.v5_mutant_test_required`` (invariant #14, key
``coupling-gradients``): "(a) >=512x512 tile, >=100 steps, surcharge active;
(b) finite non-zero grads w.r.t. capacity scaling; (c) demonstrated RED by
mutating the capture cap to ``.detach()`` or a dead ``torch.where`` branch ->
grad zero/NaN, mutation recorded beside the test; scope stated per V7."

Documented capacity-scaling path (ONE path, per the invariant brief): the leaf
``capacity_scale`` multiplies ``width_mean_m``, which feeds L_weir, A_open and
the node plan areas together — the intake-capacity group named in
``autograd_rules.differentiable_terms``. Width (not q_cap) is chosen because it
is upstream of strictly more differentiable paths (capture forms, return forms,
plan-area bookkeeping), so the non-zero assertion is structurally guaranteed
whenever ANY exchange runs, rather than only when routing carries volume.

Fixtures are DECLARED-SYNTHETIC lattices driven through the production assembly
(``build_drain_graph`` -> topo/component/plan machinery -> ``couple_step``),
never presented as Bengaluru data. The REAL-graph companion tier is blocked
upstream house-wide (the frozen WF-1 reader refuses the only realized
artefact); the closure condition is recorded in tests/coupling/test_exchange.py
and applies here identically.

V5 mutation records live beside the red tests below; both contract-named
mutation flavours are demonstrated (.detach() on the capture cap AND a dead /
poisoned ``torch.where`` branch), each against a pristine control on the SAME
fixture, each asserting its observable MOVES (V2/V5). CPU-only throughout
(V12); wall clock is measured and printed (compute discipline).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
import torch

from jaladhar.coupling import exchange as xchg
from jaladhar.coupling.exchange import (
    NodeState,
    build_node_state,
    couple_step,
    zero_drain_cap_out_of_place,
)
from jaladhar.coupling.router import DrainGraph, build_drain_graph
from jaladhar.solver.state import StaticFields

DT_MAIN = 0.48
RAIN_RATE_M_S = 20e-3 / 3600.0

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "contracts" / "coupling_iface.json"
)


def _v5_clause_a_minimums(contract_path: Path = CONTRACT_PATH) -> tuple[int, int]:
    """Parse the clause-(a) scope minimums out of the FROZEN contract TEXT
    (``autograd_rules.v5_mutant_test_required``: "(a) >=512x512 tile, >=100 steps,
    surcharge active") so the runtime asserts in the contract test below cannot drift:
    they are read from the contract BYTES each run, so relaxing the frozen contract
    moves the pins visibly and drifting this file's loop literals BELOW the contract
    fails loudly instead of shrinking exercised scope silently."""
    rules = json.loads(contract_path.read_text())["autograd_rules"]
    rule = rules["v5_mutant_test_required"]
    m_tile = re.search(r">=(\d+)x(\d+)\s+tile", rule)
    m_steps = re.search(r">=(\d+)\s+steps", rule)
    if m_tile is None or m_steps is None:
        raise AssertionError(
            f"[inv14] contract clause-(a) literals unparsable in {contract_path}: {rule!r}"
        )
    return int(m_tile.group(1)), int(m_steps.group(1))


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


def _preset_state(heads: list[float], n: int | None = None) -> NodeState:
    n = len(heads) if n is None else n
    hs = torch.tensor(heads + [0.0] * (n - len(heads)), dtype=torch.float32)
    z64 = torch.zeros(n, dtype=torch.float64)
    return NodeState(h_node_m=hs, vol_in_m3_cum=z64.clone(), vol_out_m3_cum=z64.clone())


def _toy_graph(nmap: torch.Tensor, widths: torch.Tensor) -> DrainGraph:
    mapped_max = int(nmap.max().item()) if bool((nmap >= 0).any()) else 2
    num_nodes = max(mapped_max, 2)
    if widths.numel() < num_nodes:
        pad = torch.full((num_nodes - widths.numel(),), 2.285, dtype=widths.dtype)
        widths = torch.cat([widths, pad])
    counts = torch.bincount(nmap[nmap >= 0] - 1, minlength=num_nodes).to(torch.int64)
    return build_drain_graph(
        edge_from=torch.zeros(1, dtype=torch.int64),
        edge_to=torch.ones(1, dtype=torch.int64),
        capacity_bearing=torch.zeros(1, dtype=torch.bool),
        q_cap_nom_m3s=torch.full((1,), float("nan"), dtype=torch.float64),
        width_mean_m=widths,
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, num_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(num_nodes, dtype=torch.float64),
        outfall_node=torch.arange(num_nodes) == num_nodes - 1,
        node_cell_row=torch.arange(num_nodes, dtype=torch.int32),
        node_cell_col=torch.arange(num_nodes, dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=counts,
    )


def _lattice_graph_512(base_widths: torch.Tensor, capacity_scale: torch.Tensor) -> DrainGraph:
    n_side, block, per_row = 512, 64, 8
    nmap = torch.full((n_side, n_side), -1, dtype=torch.int32)
    for i in range(per_row):
        for j in range(per_row):
            nid = i * per_row + j + 1
            nmap[i * block : (i + 1) * block, j * block : (j + 1) * block] = nid
    num_nodes = per_row * per_row
    edges: list[tuple[int, int, float]] = []
    for i in range(per_row):
        for j in range(per_row):
            nid = i * per_row + j + 1
            if j + 1 < per_row:
                edges.append((nid, nid + 1, 3.0))
            if i + 1 < per_row:
                edges.append((nid, nid + per_row, 3.0))
    return build_drain_graph(
        edge_from=torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64),
        edge_to=torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64),
        capacity_bearing=torch.ones(len(edges), dtype=torch.bool),
        q_cap_nom_m3s=torch.tensor([float(e[2]) for e in edges], dtype=torch.float64),
        width_mean_m=base_widths * capacity_scale,
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, num_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(num_nodes, dtype=torch.float64),
        outfall_node=torch.arange(num_nodes) == num_nodes - 1,
        node_cell_row=torch.arange(num_nodes, dtype=torch.int32),
        node_cell_col=torch.arange(num_nodes, dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.full((num_nodes,), block * block, dtype=torch.int64),
    )


def _delta_loss(h_in: torch.Tensor, res_h_new: torch.Tensor) -> torch.Tensor:
    return ((h_in - res_h_new).to(torch.float64) ** 2).sum()


@pytest.mark.slow
def test_contract_512x512_100steps_surcharge_active_capacity_scale_grad() -> None:
    """CONTRACT v5_mutant_test_required (a)+(b) at its own scope.

    Chained 100-step coupled loop (host-emulated wet storm added out-of-place
    before each couple_step, mirroring the run.simulate call-site order) on a
    512x512 tile, 64-node lattice DAG; ONE autograd.grad over the whole chain
    w.r.t. the width-scale leaf. Surcharge premise asserted PER STEP
    (anti-vacuity: a gradient demo over a never-surcharging window proves
    nothing about the replay()-calibration path).

    Scope run (V7 template): 100 steps, 262,144 cells, 48.0 s simulated
    (dt=0.48 s), width scale fixed at 1.37, uniform 0.75 m initial pond +
    20 mm/hr storm; scope claimed: the gradient PIPELINE (multi-step chained
    autograd through capture+route+return at contract tile/steps) executes on
    CPU with finite non-zero d(loss)/d(capacity_scale) and surcharge active
    throughout. Gaps, named: (i) synthetic lattice, not Bengaluru terrain
    (real-graph tier blocked upstream, see module docstring); (ii) in this
    regime the shaft-proxy heads overshoot within one step so capture seals
    (no-reverse gate) after the first step and the sustained gradient is
    RETURN-path dominated — capture-path gradients are demonstrated at unit
    scale in this file's regime probes and in test_exchange.py; (iii) single
    parameter point, not a sweep."""
    torch.manual_seed(2026)
    base_widths = torch.full((64,), 6.71, dtype=torch.float64)
    capacity_scale = torch.tensor(1.37, dtype=torch.float64, requires_grad=True)
    graph = _lattice_graph_512(base_widths, capacity_scale)
    static = zero_drain_cap_out_of_place(_static_zero((512, 512)))
    h = torch.full((512, 512), 0.75, dtype=torch.float32)
    state = build_node_state(graph)

    rain_dt = RAIN_RATE_M_S * DT_MAIN
    steps = 100

    min_tile, min_steps = _v5_clause_a_minimums()
    assert h.shape == (min_tile, min_tile), (
        f"clause (a) tile floor violated: {tuple(h.shape)} < ({min_tile}, {min_tile}) "
        f"(contract {CONTRACT_PATH.name} autograd_rules.v5_mutant_test_required)"
    )
    assert steps >= min_steps, (
        f"clause (a) step floor violated: {steps} < {min_steps} "
        f"(contract {CONTRACT_PATH.name} autograd_rules.v5_mutant_test_required)"
    )
    t_start = time.perf_counter()
    loss = torch.zeros((), dtype=torch.float64)
    cum_cap = cum_ret = 0.0
    walls: list[float] = []
    for step in range(steps):
        t_step = time.perf_counter()
        h_before = h + rain_dt
        qx = torch.zeros(512, 511)
        qy = torch.zeros(511, 512)
        cr = couple_step(h_before, qx, qy, static, state, graph, DT_MAIN)
        walls.append(time.perf_counter() - t_step)

        assert cr.surcharging_nodes > 0, f"step {step}: no node surcharged — premise dead"
        assert bool(torch.isfinite(cr.h_new).all()), f"step {step}: non-finite surface depth"
        loss = loss + cr.h_new.to(torch.float64).pow(2).sum()
        cum_cap += cr.capture_m3
        cum_ret += cr.return_m3
        h, state = cr.h_new, cr.node_state_new
        if step == steps - 1:

            field_m3 = float(cr.returned_depth_m.to(torch.float64).sum().item()) * 100.0
            assert cr.return_m3 == field_m3, (cr.return_m3, field_m3)

    elapsed = time.perf_counter() - t_start
    proj_band = (elapsed / steps) * 1000.0
    print(
        f"\n[grad-512] steps={steps} wall={elapsed:.2f}s "
        f"(mean {1000.0 * elapsed / steps:.1f} ms/step; x1000-step projection band "
        f"{proj_band:.0f}s)"
    )
    print(
        f"[grad-512] cum_captured={cum_cap:.6e} m3 cum_returned={cum_ret:.6e} m3 "
        f"first_step_wall={walls[0] * 1e3:.1f} ms steady_wall={walls[-1] * 1e3:.1f} ms"
    )

    assert proj_band < 1800.0, f"projected 1000-step wall {proj_band:.0f}s exceeds budget"

    assert cum_ret >= 1.0, f"window returned only {cum_ret!r} m3 — vacuous"
    assert cum_cap > 0.0, "capture never fired; wet-storm premise unrealized"

    grad = torch.autograd.grad(loss, capacity_scale)[0]
    grad_val = float(grad.item())
    print(f"[grad-512] d(loss)/d(capacity_scale) = {grad_val!r}")
    assert bool(torch.isfinite(grad)), f"non-finite capacity-scale gradient: {grad_val!r}"
    assert grad_val != 0.0, "EXACTLY zero gradient w.r.t. capacity scaling — flow severed"


# Per-regime gradients (weir-phase vs orifice-phase), unit scale — PARTIAL by


def _regime_probe_grad(pond_m: float) -> tuple[float, bool]:
    scale = torch.tensor(1.37, dtype=torch.float64, requires_grad=True)
    g = _toy_graph(torch.tensor([[1]], dtype=torch.int32), torch.tensor([6.71]) * scale)
    h = torch.full((1, 1), pond_m, dtype=torch.float32)
    cr = couple_step(
        h,
        torch.zeros(1, 0),
        torch.zeros(0, 1),
        zero_drain_cap_out_of_place(_static_zero((1, 1))),
        build_node_state(g),
        g,
        DT_MAIN,
    )
    loss = _delta_loss(h, cr.h_new)
    grad = torch.autograd.grad(loss, scale)[0]
    return float(grad.item()), bool(torch.isfinite(grad))


def test_weir_phase_capacity_scale_grad_finite_nonzero() -> None:
    """WEIR-phase step (h_eff=0.05 m < 0.1 switch): Q_weir = Cw*L*scale*h^1.5 is
    linear in scale => finite non-zero gradient.

    PARTIAL (V7): 1 cell, 1 node, 1 step, dt=0.48 s; scope claimed: the weir
    form alone carries capacity-scale gradient. Closed at scale by
    test_contract_512x512_100steps_surcharge_active_capacity_scale_grad."""
    grad_val, finite = _regime_probe_grad(pond_m=0.05)
    print(f"\n[grad-weir] d(loss)/d(scale) = {grad_val!r} finite={finite}")
    assert finite and grad_val != 0.0


def test_orifice_phase_capacity_scale_grad_finite_nonzero() -> None:
    """ORIFICE-phase step (h_eff=1.0 m >= 0.1 switch, empty node => head_diff=h_eff):
    Q_orifice = Cd*A_open*scale*sqrt(2 g dh) is linear in scale => finite
    non-zero gradient.

    PARTIAL (V7): 1 cell, 1 node, 1 step, dt=0.48 s; scope claimed: the orifice
    form alone carries capacity-scale gradient. Closed at scale by the contract
    test above."""
    grad_val, finite = _regime_probe_grad(pond_m=1.0)
    print(f"\n[grad-orifice] d(loss)/d(scale) = {grad_val!r} finite={finite}")
    assert finite and grad_val != 0.0


def test_v5_red_clause_a_guard_catches_literal_drift(tmp_path) -> None:
    """V5 RED demo for the NEW clause-(a) runtime pin (behavioural addition — shown
    failing under the defect it guards against; no source mutation involved, the mutated
    artefact is this suite's OWN /tmp copy of the contract JSON).

    Defect world (pre-fix): the 512/100 literals existed only in contract prose and as
    bare loop constants with NO runtime assert — editing either shrank the exercised
    scope silently (the auditor's one-third-asserted finding). Two observables:
      (1) the new guard expression RAISES on drifted literals (256x256 tile, 50 steps);
      (2) the pins track the CONTRACT BYTES, not a hardcoded echo: a /tmp copy of the
          contract with clause (a) edited to >=256x256 / >=50 parses to (256, 50), so a
          relaxed frozen contract moves the pin visibly instead of leaving a stale
          constant agreeing with nothing."""
    drifted = json.loads(CONTRACT_PATH.read_text())
    drifted["autograd_rules"] = dict(drifted["autograd_rules"])
    drifted["autograd_rules"]["v5_mutant_test_required"] = (
        drifted["autograd_rules"]["v5_mutant_test_required"]
        .replace(">=512x512 tile", ">=256x256 tile")
        .replace(">=100 steps", ">=50 steps")
    )
    tmp_contract = tmp_path / "coupling_iface_drifted.json"
    tmp_contract.write_text(json.dumps(drifted))
    assert _v5_clause_a_minimums(tmp_contract) == (256, 50), (
        "clause-(a) parser did not track the drifted contract bytes — the pin would be "
        "a hardcoded echo, not a realized read"
    )

    min_tile, min_steps = _v5_clause_a_minimums()
    with pytest.raises(AssertionError, match="tile floor"):
        assert (256, 256) == (
            min_tile,
            min_tile,
        ), f"clause (a) tile floor violated: {(256, 256)} < ({min_tile}, {min_tile})"
    with pytest.raises(AssertionError, match="step floor"):
        assert 50 >= min_steps, f"clause (a) step floor violated: {50} < {min_steps}"


def test_return_path_capacity_scale_grad_finite_nonzero() -> None:
    """RETURN-PATH-ISOLATED green probe — inv14 clause (b) mirrored onto q_ret, closing
    the return-side gap symmetrically with the capture-side regime probes above.

    Fixture: single cell over a node preset ABOVE the 1.5 m freeboard (3.0 m head =>
    ret_active == 1, h_excess = 1.5 m selects the ORIFICE return branch); the dry surface
    cell seals capture TWICE over (no-reverse gate at head_diff <= 0 AND stability cap
    cap_depth == 0.9*clamp(0 - hf_floor, min=0) == 0), asserted EXACTLY zero below; the
    null edge makes routing structurally inert. The ONLY capacity-scale path into the
    exchange-isolating loss therefore runs through q_ret = Cd*A_open(scale)*sqrt(2 g
    h_excess) (the width-mean leaf feeds L_weir / A_open on the RETURN forms directly,
    exchange.py return block).

    Premises asserted BEFORE the gradient (anti-vacuity): capture_m3 == 0.0 exactly,
    surcharging_nodes >= 1, returned depth landed on the node's cell.

    PARTIAL (V7): 1 cell, 1 preset-head node, 1 step, dt=0.48 s, orifice return regime;
    scope claimed: the return form alone carries capacity-scale gradient. Closed at
    scale by test_contract_512x512_100steps_surcharge_active_capacity_scale_grad (whose
    sustained window is itself return-dominated per its scope note (ii)).

    V6 note (draft corrected during the vacuity-audit fix round): two draft defects,
    both in the TEST, never the code. (1) The first draft passed a 1-node NodeState
    into this helper's 2-node toy graph — exchange's entry shape validation refused it
    (working as designed); fixed by padding via ``_preset_state([3.0], n=2)``. (2) The
    same draft labelled the probe 'return weir phase'; h_excess = 1.5 m >=
    REGIME_SWITCH_M = 0.1 selects the ORIFICE return branch. Spec and code were right;
    the fixture and its label were wrong. Both drafts pass the gradient claim; only the
    fixture/wording moved."""
    scale = torch.tensor(1.37, dtype=torch.float64, requires_grad=True)
    g = _toy_graph(torch.tensor([[1]], dtype=torch.int32), torch.tensor([6.71]) * scale)
    h_in = torch.zeros(1, 1, dtype=torch.float32)
    cr = couple_step(
        h_in,
        torch.zeros(1, 0),
        torch.zeros(0, 1),
        zero_drain_cap_out_of_place(_static_zero((1, 1))),
        _preset_state([3.0], 2),
        g,
        DT_MAIN,
    )
    assert cr.capture_m3 == 0.0, "capture must be sealed exactly for a return-only path"
    assert cr.surcharging_nodes >= 1, "return never fired — premise dead"
    assert float(cr.returned_depth_m.sum().item()) > 0.0, "no returned depth hit the surface"
    loss = _delta_loss(h_in, cr.h_new)
    grad = torch.autograd.grad(loss, scale)[0]
    grad_val = float(grad.item())
    print(f"\n[grad-return] d(loss)/d(capacity_scale) through q_ret = {grad_val!r}")
    assert (
        bool(torch.isfinite(grad)) and grad_val != 0.0
    ), f"return-path gradient severed or non-finite: {grad_val!r}"


# V5 RED demos — both contract-named mutation flavours, controls on SAME fixture


def test_v5_red_dead_where_branch_grad_nan_forward_finite(monkeypatch) -> None:
    """V5 RED DEMO — mutation flavour 2: DEAD torch.where BRANCH.

    Mutation (recorded here, reverted by monkeypatch teardown):
    ``xchg._regime_q`` replaced by a variant whose ORIFICE branch evaluates
    ``sqrt(2*g*orifice_head)`` WITHOUT the hf_floor clamp (weir head stays
    floored — V6 note below records why).

    Fixture geometry (both arms): cell A over a DRY node (capture live, weir
    phase, head_diff=+0.05) and cell B over a node preset to 0.5 m
    (head_diff=-0.45 m). On cell B the mutant's dead/orifice branch evaluates
    sqrt(2g*(-0.45)) = NaN; forward, the regime select still picks the finite
    weir value and the NO-REVERSE-CAPTURE gate zeroes the exchange, so h_new
    stays FINITE (asserted) — the defect is invisible forward and only
    detonates in backward (grad_zero * 1/NaN = NaN at the saved sqrt output,
    landing on A_open => capacity_scale), which is exactly the clamp-first
    rationale exchange.py documents. Pristine control on the SAME fixture:
    finite non-zero gradient.

    V6 record (test changed once, 2026-08-26): the FIRST draft of this mutant
    removed BOTH floors, and its own forward-finiteness assertion caught the
    draft red for the wrong reason — unflooring weir_head also breaks the
    RETURN path, where h_excess < 0 enters the SELECTED weir branch
    (pow(negative,1.5) = NaN forward). That is a different, forward-visible
    defect, not the dead-unselected-branch trap the contract names. THE TEST
    was wrong (mutation over-scoped); code and spec are right. The mutant now
    unfloors ONLY the orifice head."""
    scale_c = torch.tensor(1.37, dtype=torch.float64, requires_grad=True)
    g = _toy_graph(torch.tensor([[1, 2]], dtype=torch.int32), torch.tensor([6.71, 6.71]) * scale_c)

    def run(scale: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = torch.full((1, 2), 0.05, dtype=torch.float32)
        cr = couple_step(
            h,
            torch.zeros(1, 1),
            torch.zeros(0, 2),
            zero_drain_cap_out_of_place(_static_zero((1, 2))),
            _preset_state([0.0, 0.5]),
            g,
            DT_MAIN,
        )
        loss = _delta_loss(h, cr.h_new)
        return torch.autograd.grad(loss, scale)[0], cr.h_new

    grad_green, hnew_green = run(scale_c)
    green_val = float(grad_green.item())
    print(
        f"\n[v5-red-where] pristine grad={green_val!r} forward_finite="
        f"{bool(torch.isfinite(hnew_green).all())}"
    )
    assert bool(torch.isfinite(grad_green)) and green_val != 0.0, "control not green"

    def _unfloored(is_weir, weir_head, orifice_head, length_m, opening_area_m2):
        weir_safe = torch.clamp(weir_head, min=xchg.HF_FLOOR_M)
        q_weir = xchg.WEIR_COEFF_CW * length_m * weir_safe.pow(1.5)
        q_orifice = (
            xchg.ORIFICE_COEFF_CD
            * opening_area_m2
            * torch.sqrt(2.0 * xchg.GRAVITY_M_S2 * orifice_head)
        )
        return torch.where(is_weir, q_weir, q_orifice)

    monkeypatch.setattr(xchg, "_regime_q", _unfloored)
    scale_m = torch.tensor(1.37, dtype=torch.float64, requires_grad=True)
    g_m = _toy_graph(
        torch.tensor([[1, 2]], dtype=torch.int32), torch.tensor([6.71, 6.71]) * scale_m
    )
    h_m = torch.full((1, 2), 0.05, dtype=torch.float32)
    cr_m = couple_step(
        h_m,
        torch.zeros(1, 1),
        torch.zeros(0, 2),
        zero_drain_cap_out_of_place(_static_zero((1, 2))),
        _preset_state([0.0, 0.5]),
        g_m,
        DT_MAIN,
    )
    loss_m = _delta_loss(h_m, cr_m.h_new)
    grad_mut = torch.autograd.grad(loss_m, scale_m)[0]
    mut_repr = repr(float(grad_mut.item()))
    fwd_finite = bool(torch.isfinite(cr_m.h_new).all())
    print(f"[v5-red-where] mutant grad={mut_repr} forward_finite={fwd_finite}")
    assert fwd_finite, "forward broke too — wrong mutation (must stay forward-finite)"
    assert not bool(
        torch.isfinite(grad_mut)
    ), f"mutation undetected: gradient survived the dead NaN branch (grad={mut_repr})"


def test_v5_red_detach_capture_cap_grad_exactly_zero(monkeypatch) -> None:
    """V5 RED DEMO — mutation flavour 1: CAPTURE CAP DETACHED.

    Mutation (recorded here, reverted by monkeypatch teardown):
    ``xchg._apply_capture_cap`` returns ``torch.minimum(rate, cap).detach()``
    instead of the pinned differentiable minimum. Fixture: 1 cell / 1 node /
    null edge (routing structurally inert), pond 0.05 m over a dry node — the
    ONLY capacity-scale path into the exchange-isolating loss runs through the
    capture cap, so severing it drives d(delta-loss)/d(capacity_scale) to
    EXACTLY 0.0 while the pristine control on the SAME fixture is finite
    non-zero. Verbatim values are printed and embedded in the assertions."""
    scale_c = torch.tensor(1.37, dtype=torch.float64, requires_grad=True)
    g = _toy_graph(torch.tensor([[1]], dtype=torch.int32), torch.tensor([6.71]) * scale_c)
    h_in = torch.full((1, 1), 0.05, dtype=torch.float32)
    static = zero_drain_cap_out_of_place(_static_zero((1, 1)))
    ns = build_node_state(g)

    def run(scale: torch.Tensor) -> float:
        cr = couple_step(
            h_in,
            torch.zeros(1, 0),
            torch.zeros(0, 1),
            static,
            ns,
            g,
            DT_MAIN,
        )
        loss = _delta_loss(h_in, cr.h_new)
        return float(torch.autograd.grad(loss, scale)[0].item())

    green_val = run(scale_c)
    print(f"\n[v5-red-detach] pristine grad={green_val!r}")
    assert torch.isfinite(torch.tensor(green_val)) and green_val != 0.0, "control not green"

    def _detached(rate, cap):
        return torch.minimum(rate, cap).detach()

    monkeypatch.setattr(xchg, "_apply_capture_cap", _detached)
    scale_m = torch.tensor(1.37, dtype=torch.float64, requires_grad=True)
    g_m = _toy_graph(torch.tensor([[1]], dtype=torch.int32), torch.tensor([6.71]) * scale_m)
    cr_m = couple_step(
        h_in,
        torch.zeros(1, 0),
        torch.zeros(0, 1),
        static,
        build_node_state(g_m),
        g_m,
        DT_MAIN,
    )
    mut_val = float(torch.autograd.grad(_delta_loss(h_in, cr_m.h_new), scale_m)[0].item())
    print(f"[v5-red-detach] mutant grad={mut_val!r}")
    assert mut_val == 0.0, (
        f"mutation undetected: gradients survived capture-cap detach "
        f"(pristine={green_val!r}, mutant={mut_val!r})"
    )
