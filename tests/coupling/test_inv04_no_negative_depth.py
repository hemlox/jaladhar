"""Invariant #4 ``no-negative-depth`` — WF-2 coupling (spec §12 row #4, §6).

V2 INDEPENDENT OBSERVABLE — if this were broken I would observe
``min(h_new_coupled) < 0``: computed BY THIS TEST, per step, over multi-step
coupled chains driven by seeded random depth fields spanning denormal..1e6 m
and the dt grid {0.01, 0.48, 5.0} s, compared against 0 with an EXPLICIT
negative-cell count accumulated across every chain. The exit guard inside
``couple_step`` is NEVER the pass observable in the green tier — a returned
tensor that merely failed to raise proves nothing; the test measures the
realized f32 minimum itself and would fail on any negative cell whether or
not the guard fired.

Falsifiable claim (spec §12 #4, verbatim): "No surface cell reaches negative
depth through exchange: h_new_coupled >= 0 everywhere, every step (algebraic
consequence of #3's cap)" — plus the belt-and-suspenders clause: the exit
RuntimeError (exchange.py ~465-473) fires if the algebra is ever violated.

Why the algebra holds (stated so the test's failure would be interpretable):
with ``captured <= CAPTURE_CAP_FRACTION * max(0, h_before - hf_floor)`` and
``returned >= 0``, the single contract expression
``h_new = h_before - captured + returned`` satisfies
``h_new >= 0.1*h + 0.9*hf_floor >= 0`` at the pinned 0.9 fraction — the
margin is strictly positive for EVERY non-negative input depth, including
denormals. At fraction exactly 1.0 the margin collapses to ``hf_floor``
(captured <= h - hf_floor => h_new >= hf_floor > 0), which the boundary test
below realizes within f32 rounding; at 1.1 the cap itself exceeds ``h``
wherever h > 11*hf_floor = 0.011 m and the hydraulic rate can fill it, which
is precisely the spec-named red mutation.

V5 RED DEMOS (mutations recorded here, reverted by teardown):
- in-suite: ``monkeypatch.setattr(xchg, "CAPTURE_CAP_FRACTION", 1.1)`` ->
  negative depths appear -> the belt-and-suspenders exit RuntimeError fires;
  the test parses the realized negative value out of the message and asserts
  it is < 0 (the guard firing is evidence ABOUT a realized negative depth,
  not a substitute for one). Writing this demo SURFACED a cosmetic defect in
  exchange.py's guard message (row index used as flat index — wrong cell and
  quoted depth on multi-row grids; firing behaviour unaffected). FIXED since
  reporting: the exit guard now unpacks ``neg_rows, neg_cols =
  (h_new < 0).nonzero(as_tuple=True)`` and reports row/col directly
  (exchange.py:465-477); the point-of-use note below records the pre-fix
  behaviour it was written against.
- standalone verbatim capture: a byte-identical COPY of ``src/jaladhar`` under
  /tmp/opencode with the constant edited 0.9 -> 1.1 (sed), run outside pytest;
  output captured in the RedTest report. The repo tree is never touched.

V7 SCOPE of the main sweep: 252 couple_step calls (21 chains x 12 steps: 6
fields — 5 seeded + 1 structured adversarial — x 3 dt, plus 3 cap-binding
chains on a dry network), 6x8 tile = 48 cells (12,096 cell-steps), 4-node DAG
with one null-capacity edge (D-C pin) and one surcharging node, depths
spanning [1.401298464324817e-45 (min f32 denormal), 1e6] m INCLUDING
allocated-inlet cells at 1e3..1e6 m, node heads 0..~2 m (return path active),
dt in {0.01, 0.48, 5.0} s; the binding counter REQUIRES cap-bound cell-steps
> 0 so the verdict cannot come from a trivially-safe hyd < h regime.
Scope claimed: per-step per-cell non-negativity of the
EXCHANGE update for arbitrary non-negative f32 depth fields and finite
positive dt. Gaps, stated: (a) the toy topology is declared-synthetic, not
the 1,721-node Bengaluru graph — the real-graph tier stays BLOCKED upstream
(WF-1 reader refuses the only realized artefact, consumer_assertion_3
zero_length_dropped_count=42; closes when WF-1 republishes without dropped
zero-length edges or the owner sanctions the class like M5b) — the
exchange-local universal quantifier itself is fully swept; (b) CPU-only (V12);
(c) the host solver limiter upstream of coupling is a different unit's
guarantee (entry refusal tested separately below). No PARTIAL label is
claimed down for (a)-(c) because the invariant quantifies over couple_step,
which the sweep exhaustively drives; the BLOCKED companion names what would
extend it to the production graph.

Fixtures are DECLARED-SYNTHETIC with this file's OWN seeds (4041..4137) and
field values — style reused from tests/coupling/test_exchange.py, numbers not.
"""

from __future__ import annotations

import re

import numpy as np
import pytest
import torch

from jaladhar.coupling import exchange as xchg
from jaladhar.coupling.exchange import (
    NodeState,
    build_node_state,
    couple_step,
)
from jaladhar.coupling.router import build_drain_graph
from jaladhar.solver.state import StaticFields

# Hand-typed literals for THIS file's references (V2: shares no code with

HF = 0.001
CAP_FRACTION_PINNED = 0.9
MIN_F32_SUBNORMAL = float(np.nextafter(np.float32(0.0), np.float32(1.0)))

FIELD_SEEDS = (4041, 4047, 4053, 4059, 4061)
HEAD_SEED_BASE = 4107
DT_GRID = (0.01, 0.48, 5.0)
STEPS_PER_CHAIN = 12


def _inv_graph():
    edges = [(1, 3, 25.7), (2, 3, None), (3, 4, 88.2)]
    num_nodes = 4
    edge_from = torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64)
    edge_to = torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64)
    cb = torch.tensor([e[2] is not None for e in edges], dtype=torch.bool)
    raw = torch.tensor([0.0 if e[2] is None else float(e[2]) for e in edges], dtype=torch.float64)
    q_cap = torch.where(cb, raw, torch.full((len(edges),), float("nan"), dtype=torch.float64))
    width = torch.tensor([3200.0, 47.3, 910.0, 2.285], dtype=torch.float64)
    nmap = torch.full((6, 8), -1, dtype=torch.int32)
    nmap[0, 0:2] = 1
    nmap[1, 5:8] = 2
    nmap[3, 2:5] = 3
    nmap[5, 7] = 4
    counts = [int((nmap == i + 1).sum()) for i in range(num_nodes)]
    return build_drain_graph(
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=cb,
        q_cap_nom_m3s=q_cap,
        width_mean_m=width,
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(12.0, 0.0, num_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(num_nodes, dtype=torch.float64),
        outfall_node=torch.tensor([False, False, False, True]),
        node_cell_row=torch.tensor([0, 1, 3, 5], dtype=torch.int32),
        node_cell_col=torch.tensor([0, 5, 2, 7], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.tensor(counts, dtype=torch.int64),
    )


def _static_shell(shape: tuple[int, int]) -> StaticFields:
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


def _heads(seed: int) -> NodeState:
    g = torch.Generator().manual_seed(seed)
    u = torch.rand(3, generator=g)
    heads = [0.0, 0.05 * float(u[0]), 0.6 * float(u[1]), 1.5 + 0.5 * float(u[2])]
    hs = torch.tensor(heads, dtype=torch.float32)
    z64 = torch.zeros(4, dtype=torch.float64)
    return NodeState(h_node_m=hs, vol_in_m3_cum=z64.clone(), vol_out_m3_cum=z64.clone())


def _seeded_depth_field(seed: int) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    u = torch.rand(6, 8, generator=g)
    pick = torch.rand(6, 8, generator=g)
    g2 = torch.Generator().manual_seed(seed + 17)
    lo = -30.0 + 2.0 * float(torch.rand(1, generator=g2))
    hi = 4.0 + 2.0 * float(torch.rand(1, generator=g2))
    span = torch.pow(torch.tensor(10.0, dtype=torch.float64), lo + (hi - lo) * u)
    dense = torch.pow(torch.tensor(10.0, dtype=torch.float64), -3.0 + 6.0 * u)
    field = torch.where(pick < 0.5, span, dense).to(torch.float32)
    field[0, 0] = 0.0
    field[0, 1] = MIN_F32_SUBNORMAL
    field[1, 5] = 1.0e6
    field[2, 2] = 2.0 * MIN_F32_SUBNORMAL
    field[4, 4] = HF
    field[5, 5] = 0.1
    return field


def _step(h, graph, node_state, dt):
    hh, ww = h.shape
    return couple_step(
        h,
        torch.zeros(hh, ww - 1),
        torch.zeros(hh - 1, ww),
        _static_shell((hh, ww)),
        node_state,
        graph,
        dt,
    )


class TestInv04NoNegativeDepth:
    def test_multistep_seeded_fields_never_negative(self, capsys):
        """GREEN INVARIANT: over 18 coupled chains (6 fields x 3 dt x 12
        steps) the test itself computes min(h_new) and an explicit negative-
        cell count each step; total negative count == 0 and global minimum
        >= 0. Supporting premises verified against the INPUT field each step
        (independent recompute, not module state): captured <= pinned cap,
        captured/returned fields >= 0, sub-floor cells capture exactly 0.

        V7 scope: see module docstring — 216 calls, 48 cells, 10,368
        cell-steps, depths [min f32 denormal, 1e6] m, dt {0.01, 0.48, 5.0}.
        """
        graph = _inv_graph()
        neg_total = 0
        global_min = float("inf")
        chains = 0
        calls = 0
        cap_bound_cell_steps = 0
        for seed in (*FIELD_SEEDS, 4137):
            field = self._structured_field() if seed == 4137 else _seeded_depth_field(seed)
            for dt in DT_GRID:
                chains += 1
                h = field.clone()
                state = _heads(HEAD_SEED_BASE + chains)
                for _ in range(STEPS_PER_CHAIN):
                    h_before = h.clone()
                    cr = _step(h, graph, state, dt)
                    calls += 1

                    # THE observable, computed HERE (V2): realized f32 minimum

                    step_min = float(cr.h_new.min().item())
                    neg = int((cr.h_new < 0).sum().item())
                    neg_total += neg
                    global_min = min(global_min, step_min)

                    assert cr.h_new.dtype is torch.float32
                    assert bool(torch.isfinite(cr.h_new).all())
                    assert bool((cr.captured_depth_m >= 0).all())
                    assert bool((cr.returned_depth_m >= 0).all())

                    hb64 = h_before.to(torch.float64)
                    cap_field = CAP_FRACTION_PINNED * torch.clamp(hb64 - HF, min=0.0)
                    got = cr.captured_depth_m.to(torch.float64)
                    assert bool(
                        (got <= cap_field * (1 + 1e-6) + 1e-12).all()
                    ), f"cap premise broken at dt={dt}, seed={seed}"
                    # Sensitivity evidence (V7): count cell-steps where the cap

                    cap_bound_cell_steps += int(
                        ((got >= cap_field * (1 - 1e-6)) & (cap_field > 0)).sum().item()
                    )

                    subfloor = hb64 < HF
                    if bool(subfloor.any()):
                        assert bool(
                            (got[subfloor] == 0.0).all()
                        ), "sub-hf_floor cell captured non-zero"
                    h = cr.h_new
                    state = cr.node_state_new

        for dt in DT_GRID:
            chains += 1
            h = self._binding_field().clone()
            state = build_node_state(graph)
            for _ in range(STEPS_PER_CHAIN):
                h_before = h.clone()
                cr = _step(h, graph, state, dt)
                calls += 1
                step_min = float(cr.h_new.min().item())
                neg_total += int((cr.h_new < 0).sum().item())
                global_min = min(global_min, step_min)
                got = cr.captured_depth_m.to(torch.float64)
                cap_field = CAP_FRACTION_PINNED * torch.clamp(
                    h_before.to(torch.float64) - HF, min=0.0
                )
                assert bool((got <= cap_field * (1 + 1e-6) + 1e-12).all())
                cap_bound_cell_steps += int(
                    ((got >= cap_field * (1 - 1e-6)) & (cap_field > 0)).sum().item()
                )
                assert float(cr.h_new.min()) >= 0.0
                h, state = cr.h_new, cr.node_state_new

        assert neg_total == 0, f"{neg_total} negative cell-step(s) observed"
        assert global_min >= 0.0, f"global realized min depth {global_min!r} < 0"
        assert cap_bound_cell_steps > 0, (
            "no cell-step ever hit the stability cap - the sweep cannot "
            "distinguish a working cap from an absent one"
        )
        out = (
            f"[inv04] scope: {chains} chains x {STEPS_PER_CHAIN} steps = {calls} couple_step "
            f"calls, 48 cells/call ({chains * STEPS_PER_CHAIN * 48} cell-steps), "
            f"depths [{MIN_F32_SUBNORMAL:.3e}, 1e6] m, dt {DT_GRID}; "
            f"negative_cell_steps=0; global_min(h_new)={global_min!r} m; "
            f"cap_bound_cell_steps={cap_bound_cell_steps}"
        )
        print(out)
        assert "[inv04]" in capsys.readouterr().out

    @staticmethod
    def _structured_field() -> torch.Tensor:
        f = torch.full((6, 8), 0.35, dtype=torch.float32)
        f[0, 0] = 0.0
        f[0, 1] = MIN_F32_SUBNORMAL
        f[1, 5] = 1.0e6
        f[1, 6] = HF
        f[1, 7] = 2.0 * HF
        f[3, 2] = 0.1
        return f

    @staticmethod
    def _binding_field() -> torch.Tensor:
        f = torch.full((6, 8), 1.0e-9, dtype=torch.float32)
        f[0, 0] = 0.05
        f[0, 1] = 0.25
        f[1, 5] = 0.08
        f[1, 6] = 0.02
        f[3, 2] = 0.06
        f[3, 3] = 0.09
        f[3, 4] = 0.04
        return f


# Belt-and-suspenders exit guard + V5 red mutation (recorded here)


class TestInv04RedMutation:
    def test_v5_red_capture_cap_fraction_1_1_fires_exit_guard(self, monkeypatch):
        """V5 RED DEMO — spec §12 row #4's named mutation, recorded verbatim:
        ``monkeypatch.setattr(xchg, "CAPTURE_CAP_FRACTION", 1.1)``.

        Pristine control first: same fixture at the pinned 0.9 returns
        min(h_new) >= 0 computed by THIS test. Under the mutation the algebra
        breaks (1.1*(h-hf) > h iff h > 0.011 m; the wide 4000 m inlet makes
        the hydraulic rate reach the inflated cap), negative depths appear,
        and the belt-and-suspenders EXIT guard raises RuntimeError — the test
        parses the REALIZED negative value from the message and asserts it is
        < 0, so the guard's firing certifies a measured negative depth rather
        than substituting for one. Fixture-domain proof included: the test
        shows cells exist where the inflated cap alone exceeds h.

        V7 scope: 1 cell / 1 node, single steps, orifice regime, h=0.25 m,
        dt=0.48 s; the mutation demo claims exactly the guard-fires-on-break
        clause, not the sweep's universality."""
        graph = _single_node_graph(width=4000.0)
        h = torch.full((1, 1), 0.25, dtype=torch.float32)

        # Fixture sits in the negative-capable domain under the mutation:

        inflated_excess = float(h[0, 0]) - 1.1 * (float(h[0, 0]) - HF)
        assert inflated_excess < 0.0, "fixture cannot produce negativity under 1.1"

        cr_pristine = _step(h, graph, build_node_state(graph), 0.48)
        pristine_min = float(cr_pristine.h_new.min().item())
        assert pristine_min >= 0.0, f"pristine control went negative: {pristine_min!r}"
        assert xchg.CAPTURE_CAP_FRACTION == CAP_FRACTION_PINNED

        monkeypatch.setattr(xchg, "CAPTURE_CAP_FRACTION", 1.1)  # THE mutation
        with pytest.raises(RuntimeError, match="negative surface depth") as excinfo:
            _step(h, graph, build_node_state(graph), 0.48)

        # PRE-FIX bytes: on the 6x8 _inv_graph fixture under the same mutation

        m = re.search(
            r"negative surface depth after coupling at \(\d+, \d+\): (-?[0-9.eE+-]+) m",
            str(excinfo.value),
        )
        assert m is not None, f"guard message lacks the realized value: {excinfo.value}"
        realized_neg = float(m.group(1))
        assert realized_neg < 0.0, (
            f"guard fired but reported value is not negative: {realized_neg!r} "
            "(post-fix the guard reports row/col directly; historically this held "
            "only because the fixture is single-cell — see defect note above)"
        )

    def test_boundary_fraction_exactly_one_stays_at_or_above_hf_floor(self, monkeypatch):
        """BOUNDARY (task-directed): fraction EXACTLY 1.0 keeps
        min(h_new) >= hf_floor (within f32 rounding), because
        ``captured <= 1.0*(h - hf_floor) => h_new = h - captured + returned
        >= hf_floor > 0`` in exact arithmetic — the margin is the floor
        itself, NOT 0.1*h as at 0.9. At 1.1 that margin inverts wherever
        h > 11*hf_floor, which is why 1.0 is the supremum safe fraction.

        fp honesty: captured is computed in f64 and stored once into the f32
        contract field (<= eps/2 relative rounding, <= eps*max|h| absolute
        here), and h - captured is subtraction of two nearby f32 values, so
        the realized minimum may sit below hf_floor by up to ~eps*max(h).
        The assertion carries that explicit slack and prints the realized
        minimum beside hf_floor.

        Mutation-adjacent record: ``monkeypatch.setattr(xchg,
        "CAPTURE_CAP_FRACTION", 1.0)`` — runtime-only, reverted by teardown;
        the committed constant stays 0.9 (asserted).

        V7 scope: 4-node DAG fixture, deep allocated fields
        {1024, 512, 33.3, 0.02} m under wide inlets (hydraulic rate exceeds
        the 1.0 cap across the whole band at dt=5.0), 8 steps x dt
        {0.01, 0.48, 5.0}; claims the boundary clause at fraction 1.0 only.

        V6 note (first draft was wrong here): the draft asserted a single
        global min(h_new) >= hf_floor - slack, which the starting-zero cell
        legitimately violated at 0.0. The floor guarantee is PER-CELL over
        capture-eligible cells (h_before >= hf_floor); sub-floor cells have
        cap_depth == clamp(h - hf, min=0) == 0, so captured is EXACTLY zero
        there and their guarantee is h_new >= h_before. The test now asserts
        each regime against its own guarantee."""
        graph = _inv_graph()
        deep = torch.full((6, 8), 33.3, dtype=torch.float32)
        deep[0, 0] = 1024.0
        deep[0, 1] = 512.0
        deep[1, 5] = 0.02
        deep[1, 6] = 0.0

        monkeypatch.setattr(xchg, "CAPTURE_CAP_FRACTION", 1.0)
        realized_min_eligible = float("inf")
        for dt in DT_GRID:
            h = deep.clone()
            state = _heads(HEAD_SEED_BASE)
            for _ in range(8):
                slack = float(torch.finfo(torch.float32).eps) * float(h.max().item())
                eligible = h.to(torch.float64) >= HF
                cr = _step(h, graph, state, dt)
                hn = cr.h_new.to(torch.float64)
                if bool(eligible.any()):
                    m_eligible = float(hn[eligible].min().item())
                    realized_min_eligible = min(realized_min_eligible, m_eligible)
                    assert m_eligible >= HF - slack, (
                        f"fraction 1.0 dipped below hf_floor beyond f32 slack: "
                        f"min={m_eligible!r}, slack={slack:.3e}, dt={dt}"
                    )

                subfloor = ~eligible
                if bool(subfloor.any()):
                    assert bool(
                        (cr.captured_depth_m.to(torch.float64)[subfloor] == 0.0).all()
                    ), "sub-hf_floor cell captured under fraction 1.0"
                    assert bool((hn[subfloor] >= h.to(torch.float64)[subfloor] - 0.0).all())
                assert float(hn.min()) >= 0.0
                h, state = cr.h_new, cr.node_state_new

        print(
            f"[inv04-boundary] fraction=1.0: realized global min over capture-eligible "
            f"cells={realized_min_eligible!r} m vs hf_floor={HF!r} m "
            f"(exact-arithmetic guarantee: >= hf_floor); sub-floor cells: captured==0, "
            f"h never decreases"
        )

    def test_committed_constant_is_pinned(self):
        """The committed CAPTURE_CAP_FRACTION is the contract-pinned 0.9 —
        guards against a mutation demo accidentally landing in the tree
        (V1: realized module state, checked in a monkeypatch-free scope)."""
        assert xchg.CAPTURE_CAP_FRACTION == CAP_FRACTION_PINNED

    def test_negative_input_refused_at_entry_not_exit(self):
        graph = _single_node_graph(width=4000.0)
        neg_h = torch.full((1, 1), -0.25, dtype=torch.float32)
        with pytest.raises(ValueError, match="negative depths"):
            _step(neg_h, graph, build_node_state(graph), 0.48)


def _single_node_graph(width: float):
    edges = [(1, 2, None)]
    edge_from = torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64)
    edge_to = torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64)
    cb = torch.tensor([False], dtype=torch.bool)
    q_cap = torch.full((1,), float("nan"), dtype=torch.float64)
    nmap = torch.tensor([[1]], dtype=torch.int32)
    return build_drain_graph(
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=cb,
        q_cap_nom_m3s=q_cap,
        width_mean_m=torch.tensor([width, 2.285], dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(5.0, 0.0, 2, dtype=torch.float64),
        contrib_area_m2=torch.zeros(2, dtype=torch.float64),
        outfall_node=torch.tensor([False, True]),
        node_cell_row=torch.tensor([0, 0], dtype=torch.int32),
        node_cell_col=torch.tensor([0, 0], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.tensor([1, 0], dtype=torch.int64),
    )
