"""If the per-step capture stability cap were broken — removed, bypassed, or carrying a wrong
fraction — I would observe, WITHOUT reading any exchange.py-internal variable: the contract output
field ``captured_depth_m`` exceeding ``capture_cap_fraction * max(0, h_before - hf_floor)``
recomputed HERE element-wise from the raw input field, the carried node heads, the graph widths and
the floats resolved from ``configs/coupling.yaml``; and/or the exit negative-depth RuntimeError
firing on a deep pond whose hydraulic rate exceeds the pond itself; and/or non-finite node heads.

Invariant #3 ``capture-bounded`` (runs/wf2_design_phase/spec.md §12 row 3). Falsifiable claim,
verbatim: "Per cell/step ``captured_depth_m <= 0.9*max(0, h_before - hf_floor)``; per node/step
capture obeys its inlet hydraulic rate ``Q_regime*dt``; node head stays finite/bounded (max
recorded)." Fraction 0.9 is read from ``configs/coupling.yaml`` via ``resolve_config``, never from
the module constant (V2 independence: the reference shares no code with exchange.py — hand-typed
CW/CD/G/cell-area literals plus config floats; the constant<->config parity is invariant-seam
territory already covered by test_exchange.py::test_constants_match_resolved_config).

V5 RED DEMOS — /tmp COPY ONLY of exchange.py (the repo file is never modified; each demo reads the
committed source, applies ONE textual mutation asserting the anchor occurs exactly once, writes the
copy under /tmp/opencode/, and imports it standalone — its ``from jaladhar.coupling.router import
...`` still resolves through the installed package, so the mutation isolates exactly the cap
mechanism):
- PRIMARY (spec row #3's named mutation): ``_apply_capture_cap`` returns the unconditional
  hydraulic rate ``Q_regime*dt/cell_area`` instead of ``torch.minimum(rate, cap)``. Fixture A
  (L_weir=950 m pond 2.0 m) shows the DIRECT bound observable: captured 1.8567 m > cap 1.7991 m
  with h_new still >= 0 — the violation is visible without relying on the exit guard. Fixture B
  (L=5000 m, pond 2.0 m) shows the loud trip: unconditional capture 9.77 m drains a 2.0 m pond ->
  RuntimeError "negative surface depth". Real failing output is captured and printed.
- SECONDARY RECORD (doubles as invariant #4's named mutation): ``capture_cap_fraction`` 0.9 -> 1.1
  on another /tmp copy -> negative depths -> RuntimeError. Recorded here because the same mutation
  also violates THIS invariant's stated bound (fraction read from config is 0.9); #4 owns it.

V7 SCOPE (template filled per test): green property sweep = 4x4 tile, 16 cells / 8 allocated /
3 nodes, dts {0.01, 0.48, 2.5} x 3 coupled steps each (9 couple_step calls), depths spanning
[5e-7, 120] m across the 0.1 m regime switch (inclusive-orifice boundary included), gate-closed AND
gate-open cells, cap-binding AND non-binding cells — cap-binding is ENFORCED, not incidental: a
counter of cell-steps where captured >= cap*(1-1e-6) with cap > 0 is accumulated over the whole
sweep and asserted > 0 at its end (inv04's pattern), so a sweep where the cap never binds fails
here rather than passing vacuously; finiteness sweep = 5x5 tile / 10 allocated /
3 nodes (one capacity-bearing edge, one isolated outfall, one surcharging node), 20 steps at
dt=0.48 plus 8 steps at dt=2.5, depths to 250 m. Scope claimed: the PINNED FORMS hold for arbitrary
allocated cells, dts and adversarial-but-in-contract fields. Gap => PARTIAL: the real-graph
instantiation (1721 nodes) cannot be exercised while WF-1's frozen reader refuses the only realized
artefact (consumer_assertion_3, zero_length_dropped_count=42 — same blocker documented in
test_exchange.py); what closes it: WF-1 republishing without dropped zero-length edges, then
running this file's sweeps through ``load_drain_graph`` instead of the synthetic builder.

CPU-only throughout (V12); no CUDA allocation anywhere. Self-contained: shares no helper with
test_exchange.py (fixture STYLE reused, values different).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import torch
from conftest import load_mutated_source, static_fields

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.exchange import (
    CoupleResult,
    NodeState,
    build_node_state,
    couple_step,
)
from jaladhar.coupling.router import DrainGraph, build_drain_graph
from jaladhar.solver.state import StaticFields

REPO = Path(__file__).resolve().parents[2]
EXCHANGE_SRC = REPO / "src" / "jaladhar" / "coupling" / "exchange.py"
RED_DEMO_ROOT = Path("/tmp") / "opencode"

# Hand-computation constants — typed LITERALLY here (V2: the reference shares no code with

CW = 1.7
CD = 0.65
G = 9.81
AREA = 100.0

# V5 mutation anchors — full lines, asserted to occur EXACTLY ONCE in the committed source so the

_ANCHOR_CAP_LINE = "    return torch.minimum(hydraulic_rate_depth, cap_depth)"
_MUTANT_UNCONDITIONAL = "    return hydraulic_rate_depth"
_ANCHOR_FRACTION = "CAPTURE_CAP_FRACTION: float = 0.9"
_MUTANT_FRACTION_1_1 = "CAPTURE_CAP_FRACTION: float = 1.1"


@pytest.fixture(scope="session")
def cfg():
    """Resolved coupling config — the ONLY source of the bound fraction / floors (V2)."""
    return resolve_config(REPO / "configs" / "coupling.yaml", REPO)


def _toy_graph(
    edges: list[tuple[int, int, float | None]],
    num_nodes: int,
    *,
    widths: list[float],
    nmap: torch.Tensor,
    outfalls: tuple[int, ...] = (),
) -> DrainGraph:
    if not edges:
        edges = [(1, num_nodes if num_nodes >= 2 else 2, None)]
        num_nodes = max(num_nodes, 2)
        if len(widths) < num_nodes:
            widths = list(widths) + [30.0] * (num_nodes - len(widths))
    edge_from = torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64)
    edge_to = torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64)
    cb = torch.tensor([e[2] is not None for e in edges], dtype=torch.bool)
    raw = torch.tensor([0.0 if e[2] is None else float(e[2]) for e in edges], dtype=torch.float64)
    q_cap = torch.where(cb, raw, torch.full((len(edges),), float("nan"), dtype=torch.float64))
    outfall = torch.zeros(num_nodes, dtype=torch.bool)
    for o in outfalls:
        outfall[o - 1] = True
    counts = [int((nmap == i + 1).sum()) for i in range(num_nodes)]
    return build_drain_graph(
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=cb,
        q_cap_nom_m3s=q_cap,
        width_mean_m=torch.tensor(widths, dtype=torch.float64),
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
    return static_fields(shape, zeroed=True)


def _state(heads: list[float]) -> NodeState:
    hs = torch.tensor(heads, dtype=torch.float32)
    z64 = torch.zeros(len(heads), dtype=torch.float64)
    return NodeState(h_node_m=hs, vol_in_m3_cum=z64, vol_out_m3_cum=z64.clone())


def _run(
    fn_couple_step, h: torch.Tensor, graph: DrainGraph, node_state: NodeState, dt: float
) -> CoupleResult:
    st = _static_zero(tuple(h.shape))
    qx = torch.zeros(h.shape[0], h.shape[1] - 1)
    qy = torch.zeros(h.shape[0] - 1, h.shape[1])
    return fn_couple_step(h, qx, qy, st, node_state, graph, dt)


def _bounds_reference(
    h_before: torch.Tensor,
    node_heads_before: torch.Tensor,
    graph: DrainGraph,
    dt: float,
    cfg,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    frac = float(cfg.exchange.capture_cap_fraction)
    hf = float(cfg.exchange.hf_floor_m)
    switch = float(cfg.exchange.regime_switch_m)
    a_frac = float(cfg.exchange.a_open_width_fraction)
    mapped = graph.node_id_map >= 0
    owner = (graph.node_id_map[mapped] - 1).to(torch.int64)
    h64 = h_before.to(torch.float64)
    h_eff = torch.clamp(h64[mapped], min=0.0)
    width = graph.width_mean_m[owner]
    head_diff = h_eff - node_heads_before.to(torch.float64)[owner]
    is_weir = h_eff < switch
    q_weir = CW * width * torch.clamp(h_eff, min=hf) ** 1.5
    q_orif = CD * (width * a_frac) * torch.sqrt(2.0 * G * torch.clamp(head_diff, min=hf))
    q_regime = torch.where(is_weir, q_weir, q_orif) * (head_diff > 0).to(torch.float64)
    hyd_depth = q_regime * dt / AREA
    cap_depth = frac * torch.clamp(h64[mapped] - hf, min=0.0)
    return cap_depth, hyd_depth, owner, mapped


def _load_mutated_exchange(replacements: list[tuple[str, str]], tag: str) -> tuple[object, Path]:
    """Copy exchange.py to /tmp/opencode, apply ONE textual mutation, import the COPY standalone.

    The repo source is NEVER modified. Each old string must occur EXACTLY ONCE in the committed
    source — a drift fails HERE, loudly, rather than mutating nothing (a vacuous red demo).
    """
    return load_mutated_source(
        EXCHANGE_SRC, replacements, tag=tag, prefix="inv03", directory=RED_DEMO_ROOT
    )


class TestCaptureBoundGreen:
    def test_per_cell_cap_and_hydraulic_rate_independent_recompute(self, cfg):
        """PROPERTY: for every allocated cell and every swept step, captured_depth_m <= BOTH
        (a) capture_cap_fraction * max(0, h_before - hf_floor)  [stability cap, fraction from
            configs/coupling.yaml], and
        (b) its inlet hydraulic rate Q_regime * dt / cell_area  [physics rate],
        recomputed in THIS file from the input field + carried node heads + graph widths +
        resolved config; captured >= 0; unallocated cells capture EXACTLY 0; node-aggregated
        capture stays within the node's summed inlet rates (claim clause 2); AND the sweep's
        cap-binding coverage is ENFORCED — cell-steps where captured >= cap*(1-1e-6) with
        cap > 0 are counted across the whole sweep and asserted > 0 at its end (inv04's
        anti-vacuity pattern), so the bound cannot pass vacuously while the hydraulic rate
        is everywhere below the cap.

        V7 scope: 4x4 tile, 16 cells / 8 allocated / 3 nodes (widths 12/800/30 m), dts
        {0.01, 0.48, 2.5} x 3 coupled steps each = 9 couple_step calls, depths [5e-7, 120] m
        spanning the 0.1 m switch (0.1 EXACT included), gate-open AND gate-closed cells,
        cap-binding AND non-binding cells (binding count asserted > 0), one surcharging node
        (return feeds recapture);
        scope claimed: the pinned forms for arbitrary in-contract cells/dts — NOT the real-graph
        allocation (PARTIAL gap, see module docstring)."""
        assert 0.0 < float(cfg.exchange.capture_cap_fraction) < 1.0, (
            "invariant premise: the shipped cap fraction must be a strict margin; if the config "
            "ever ships >= 1.0 the bound alone no longer guarantees h_new >= 0 (invariant #4)"
        )
        g = _toy_graph(
            [],
            3,
            widths=[12.0, 800.0, 30.0],
            nmap=torch.tensor(
                [[1, 1, 2, 2], [1, 3, 2, -1], [-1, -1, 3, -1], [-1, -1, -1, -1]],
                dtype=torch.int32,
            ),
        )
        h_adv = torch.tensor(
            [
                [0.0, 5e-7, 0.0999999, 0.1],
                [0.35, 1.0, 2.5, 25.0],
                [0.001, 60.0, 0.049, 3.0],
                [0.0, 0.0, 120.0, 0.02],
            ],
            dtype=torch.float32,
        )
        cap_bound_cell_steps = 0
        for dt in (0.01, 0.48, 2.5):
            h = h_adv.clone()
            state = _state([0.62, 0.0, 2.2])
            for step in range(3):
                h_before = h.clone()
                heads_before = state.h_node_m.clone()
                cr = _run(couple_step, h, g, state, dt)
                cap, hyd, owner, mapped = _bounds_reference(h_before, heads_before, g, dt, cfg)
                got = cr.captured_depth_m.to(torch.float64)

                assert bool(
                    (got[~mapped] == 0.0).all()
                ), f"unallocated cell captured at dt={dt} step={step}"
                got_m = got[mapped]
                assert bool((got_m >= 0.0).all()), f"negative capture at dt={dt} step={step}"
                assert bool((got_m <= cap * (1 + 1e-6) + 1e-12).all()), (
                    f"STABILITY CAP VIOLATED at dt={dt} step={step}: worst "
                    f"{float((got_m - cap).max()):.3e} m over "
                    f"{float(cap.max()):.6f} m cap (fraction "
                    f"{float(cfg.exchange.capture_cap_fraction)}, hf_floor "
                    f"{float(cfg.exchange.hf_floor_m)} from configs/coupling.yaml)"
                )
                assert bool((got_m <= hyd * (1 + 1e-6) + 1e-12).all()), (
                    f"HYDRAULIC RATE EXCEEDED at dt={dt} step={step}: worst "
                    f"{float((got_m - hyd).max()):.3e} m over rate {float(hyd.max()):.6f} m"
                )
                # Binding-coverage evidence (V7, inv04's pattern): count cell-steps where

                cap_bound_cell_steps += int(((got_m >= cap * (1 - 1e-6)) & (cap > 0)).sum().item())

                for j in range(g.num_nodes):
                    sel = owner == j
                    if not bool(sel.any()):
                        continue
                    s_got = float(got_m[sel].sum().item()) * AREA
                    s_rate = float(hyd[sel].sum().item()) * AREA
                    assert s_got <= s_rate * (1 + 1e-6) + 1e-9, (
                        f"node {j + 1} captured {s_got:.6e} m3 > summed inlet rate "
                        f"{s_rate:.6e} m3 at dt={dt} step={step}"
                    )

                h = cr.h_new
                state = cr.node_state_new

        assert cap_bound_cell_steps > 0, (
            "no cell-step ever hit the stability cap across the whole sweep — the bound "
            "assertions above cannot distinguish a working cap from an absent one "
            "(vacuity guard, inv04's pattern)"
        )
        print(
            f"[inv03-sweep] property sweep: 3 dts x 3 steps = 9 couple_step calls over the "
            f"4x4 tile; cap-binding cell-steps = {cap_bound_cell_steps} (> 0 enforced)"
        )

    def test_node_heads_finite_and_bounded_multistep_sweep(self, cfg):
        """PROPERTY: node heads stay FINITE every step, and BOUNDED by an input-only envelope —
        total system water at t=0 divided by the smallest node plan area. Envelope derivation
        (inputs only): route() is volume-conservative (router.py §7 pin: v += in - out), capture/
        return merely MOVE water between surface and nodes, and this sweep applies no forcing, so
        no node can ever hold more water than existed in the whole system at t=0; head <= that
        volume / min(plan area). The max recorded head is reported in the failure message.

        V7 scope: 5x5 tile, 25 cells / 10 allocated / 3 nodes (widths 12/45/30 m; edge 1->2 at
        7.5 m3/s; isolated outfall node 3; node 3 starts surcharging at 2.4 m), 20 steps at
        dt=0.48 THEN a fresh 8 steps at dt=2.5 (28 couple_step calls), initial depths to 250 m;
        scope claimed: finiteness + envelope over adversarial multi-step coupling — NOT long-run
        storm-window behaviour (coupled-run manifest territory, invariants #1/#6)."""
        g = _toy_graph(
            [(1, 2, 7.5)],
            3,
            widths=[12.0, 45.0, 30.0],
            nmap=torch.tensor(
                [
                    [1, 1, 1, -1, -1],
                    [1, -1, 2, 2, -1],
                    [-1, -1, 2, -1, -1],
                    [-1, 3, 3, 3, -1],
                    [-1, -1, -1, -1, -1],
                ],
                dtype=torch.int32,
            ),
            outfalls=(3,),
        )
        h_adv = torch.tensor(
            [
                [0.0, 250.0, 60.0, 0.35, 0.049],
                [0.001, 0.1, 0.0, 25.0, 0.0],
                [0.0, 0.0, 5.0, 0.0, 0.0],
                [0.0, 0.001, 60.0, 0.049, 0.0],
                [0.0, 0.0, 0.0, 0.0, 0.0],
            ],
            dtype=torch.float32,
        )
        pa_min = float(g.node_plan_area_m2.min().item())
        assert pa_min > 0.0
        envelope = None
        max_recorded = -math.inf

        for dt, steps in ((0.48, 20), (2.5, 8)):
            h = h_adv.clone()
            state = _state([0.0, 0.9, 2.4])
            if envelope is None:
                v_total0 = float(h.to(torch.float64).sum().item()) * AREA + float(
                    (state.h_node_m.to(torch.float64) * g.node_plan_area_m2).sum().item()
                )
                envelope = v_total0 / pa_min
            for step in range(steps):
                cr = _run(couple_step, h, g, state, dt)
                heads = cr.node_state_new.h_node_m
                assert bool(
                    torch.isfinite(heads).all()
                ), f"NON-FINITE node head at dt={dt} step={step}: {heads}"
                assert math.isfinite(
                    cr.max_node_head_m
                ), f"non-finite recorded peak head at dt={dt} step={step}"
                assert bool(
                    torch.isfinite(cr.captured_depth_m).all()
                ), f"non-finite captured field at dt={dt} step={step}"
                max_recorded = max(max_recorded, cr.max_node_head_m, float(heads.max().item()))
                h = cr.h_new
                state = cr.node_state_new

        assert envelope is not None and max_recorded <= envelope * (1 + 1e-6) + 1e-6, (
            f"node head UNBOUNDED: recorded max {max_recorded!r} m exceeds input-only envelope "
            f"{envelope!r} m (= total system water at t=0 / min plan area {pa_min} m2)"
        )
        print(
            f"\n[inv03-sweep] max recorded node head = {max_recorded:.4f} m; envelope = "
            f"{envelope:.4f} m; pa_min = {pa_min} m2 — finite and bounded"
        )


# RED DEMOS (V5) — /tmp copies only; repo source untouched


class TestCaptureCapRedDemos:
    def test_v5_red_primary_cap_removed_unconditional_capture(self, cfg):
        """PRIMARY RED (spec §12 row #3's named mutation): ``_apply_capture_cap`` mutated in a
        /tmp COPY to return the UNCONDITIONAL hydraulic rate Q_regime*dt/cell_area (torch.minimum
        severed). Fixture A shows the direct observable WITHOUT the exit guard: mutant captures
        ~1.8567 m from a 2.0 m pond where the config-fraction bound is 0.9*(2-0.001) = 1.7991 m —
        captured EXCEEDS the independently recomputed bound while h_new is still >= 0. Fixture B
        (wider inlet) drives capture ~9.77 m out of a 2.0 m pond -> the loud RuntimeError. Both
        real failing outputs are printed. Pristine controls first: same fixtures, committed
        module -> captured == bound exactly (binding), no exception.

        V7 scope: 1 cell / 1 node per fixture, single steps at dt=0.48, orifice regime
        (h_eff = 2.0 m >= switch), L_weir in {950, 5000} m; scope claimed: the mutation's effect
        on the cap mechanism in isolation."""
        frac = float(cfg.exchange.capture_cap_fraction)
        hf = float(cfg.exchange.hf_floor_m)
        dt = 0.48

        g_a = _toy_graph([], 1, widths=[950.0], nmap=torch.tensor([[1]], dtype=torch.int32))
        h_a = torch.full((1, 1), 2.0, dtype=torch.float32)
        bound_a = frac * max(0.0, float(h_a[0, 0]) - hf)

        cr_ok = _run(couple_step, h_a.clone(), g_a, build_node_state(g_a), dt)
        got_ok = float(cr_ok.captured_depth_m[0, 0])
        assert got_ok <= bound_a * (1 + 1e-6) + 1e-12, "pristine control violated the cap?!"
        assert got_ok == pytest.approx(bound_a, rel=1e-6), (got_ok, bound_a)
        assert cr_ok.cap_binding_this_step == 1
        assert bool((cr_ok.h_new >= 0).all())

        mut, path_a = _load_mutated_exchange(
            [(_ANCHOR_CAP_LINE, _MUTANT_UNCONDITIONAL)], "cap_removed"
        )
        cr_bad = _run(mut.couple_step, h_a.clone(), g_a, mut.build_node_state(g_a), dt)
        got_bad = float(cr_bad.captured_depth_m[0, 0])
        assert got_bad > bound_a * (1 + 1e-6), (
            f"MUTATION UNDETECTED: removing torch.minimum did not breach the bound "
            f"(mutant {got_bad!r} vs cap {bound_a!r})"
        )
        assert bool((cr_bad.h_new >= 0).all()), (
            "fixture A was supposed to show the BOUND violation directly (h_new >= 0); "
            "it tripped the exit guard instead — retune the fixture split"
        )
        print(
            f"\n[inv03-red-primary/A] mutant captured={got_bad:.6f} m > bound={bound_a:.6f} m "
            f"(excess {got_bad - bound_a:+.6f} m, h_new={float(cr_bad.h_new[0, 0]):.6f} m); "
            f"mutated copy: {path_a}"
        )

        g_b = _toy_graph([], 1, widths=[5000.0], nmap=torch.tensor([[1]], dtype=torch.int32))
        h_b = torch.full((1, 1), 2.0, dtype=torch.float32)
        cr_ok_b = _run(couple_step, h_b.clone(), g_b, build_node_state(g_b), dt)
        assert bool((cr_ok_b.h_new >= 0).all()), "pristine control B went negative?!"

        with pytest.raises(RuntimeError, match="negative") as excinfo:
            # mutant calls need the MUTANT's own NodeState class: couple_step isinstance-checks

            _run(mut.couple_step, h_b.clone(), g_b, mut.build_node_state(g_b), dt)
        failing = str(excinfo.value)
        assert "capture cap invariant" in failing, failing
        print(f"[inv03-red-primary/B] real failing output: {failing}")
        a_open = 5000.0 * float(cfg.exchange.a_open_width_fraction)
        uncond = CD * a_open * math.sqrt(2 * G * 2.0) * dt / AREA
        print(
            f"[inv03-red-primary] hand-check: unconditional rate = {uncond:.4f} m > pond 2.0 m; "
            f"pristine cap {bound_a:.4f} m holds on the same fixture"
        )

    def test_v5_red_secondary_fraction_1_1_record_also_serves_invariant_4(self, cfg):
        """SECONDARY RECORD (invariant #4's named mutation, kept beside #3 because the same
        mutation breaches THIS invariant's stated bound): ``capture_cap_fraction`` 0.9 -> 1.1 in
        a /tmp COPY. Pond 0.5 m, L=5000 m, dt=0.48: mutant cap 1.1*(0.5-0.001) = 0.5489 m > pond
        -> captured 0.5489 m -> h_new = -0.0489 m -> RuntimeError. Pristine control: cap
        0.9*0.499 = 0.4491 m < pond -> green. #4 owns the negative-depth claim; here it stands as
        corroboration that #3's bound is what keeps the algebra safe.

        V7 scope: 1 cell / 1 node, single step, dt=0.48, orifice regime."""
        g = _toy_graph([], 1, widths=[5000.0], nmap=torch.tensor([[1]], dtype=torch.int32))
        h = torch.full((1, 1), 0.5, dtype=torch.float32)
        dt = 0.48

        cr_ok = _run(couple_step, h.clone(), g, build_node_state(g), dt)
        assert bool((cr_ok.h_new >= 0).all())
        frac_cfg = float(cfg.exchange.capture_cap_fraction)
        assert float(cr_ok.captured_depth_m[0, 0]) <= frac_cfg * 0.499 * (1 + 1e-6) + 1e-12

        mut11, path_11 = _load_mutated_exchange(
            [(_ANCHOR_FRACTION, _MUTANT_FRACTION_1_1)], "frac_1_1"
        )
        with pytest.raises(RuntimeError, match="negative") as excinfo:
            _run(mut11.couple_step, h.clone(), g, mut11.build_node_state(g), dt)
        failing = str(excinfo.value)
        assert "capture cap invariant" in failing, failing
        print(f"\n[inv03-red-secondary] real failing output: {failing}")
        print(
            f"[inv03-red-secondary] mutated copy (fraction 1.1): {path_11}; pristine cap "
            f"{frac_cfg * 0.499:.4f} m < pond 0.5 m held on the same fixture"
        )
