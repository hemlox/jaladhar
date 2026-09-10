"""V2: if the routing-only-null-capacity pin were broken I would observe, on ONE realized
trajectory: a NON-ZERO ``edge_flow_m3s`` entry on a non-capacity-bearing edge while its
upstream node holds volume (phantom conveyance), a downstream node GAINING volume across such
an edge (phantom inflow its own capture cannot explain), a NaN sentinel surfacing as a finite
number anywhere in realized state (``q_cap_nom_m3s`` bytes, ``CoupleResult.edge_flow_m3s``),
or an assembly/partition refusal FAILING TO FIRE when handed a coerced fixture.

=============================================================================================
INVARIANT #11 ``routing-only-null-capacity`` — WF-2 spec §12 row #11, §6/§7 pin 3
=============================================================================================
Claim (under test): all 379 non-capacity-bearing edges (286 synthetic + 61 zero-slope +
32 area-capped) carry ``Q_edge = 0`` ALWAYS; they never capture surface water (capture is
NODE-based — nodes whose connectivity is ONLY null/synthetic edges still receive capture,
they are real nodes) and never surcharge THROUGH their nodes-by-virtue-of-those-edges (a
null-upstream node may surcharge from its OWN storage; no volume ever ARRIVES via the null
edge); NaN sentinels stay NaN in code, and coercion to zero is caught LOUDLY at two gates:
assembly refuses coerced inputs (``router.build_drain_graph`` sentinel integrity) and the
loader partition gate catches reclassified edges (``load_drain_graph`` §4.2 partition check).

GREEN arms (pristine code):
  G1  coupled micro-run, 9-node mixed topology, 20 couple_step calls in two phases —
      null-edge flow == 0.0 EXACTLY every step; zero-denominator RETENTION upstream of the
      null chain; pure null-relay and null-fed nodes grow by their OWN capture witness only
      (my reduction of ``captured_depth_m``, hand-typed cell area — not production sums);
      surcharge fires on the null-upstream node FROM ITS OWN STORAGE while the null edge
      below it still carries exactly zero, and returned depth lands only on that node's cells.
  G2  seeded random DAGs, MULTI-STEP route() with per-step injection: conservation against
      MY injected total, null Q == 0 exact every step, retention nodes never lose volume.
  G3  sentinel-integrity refusals exercised on MY OWN fixture vectors: non-CB edge carrying
      0.0 or any finite value -> RefuseLoadError; CB edge carrying NaN/0.0/negative ->
      RefuseLoadError (the coerced-AND-reclassified case lands on the CB branch: 0.0 fails
      "finite > 0").
  G4  partition gate on REALIZED bytes through the reader-seam bypass (house pattern from
      test_router.py; ONLY the frozen reader's D-G rejection is bypassed — every router-side
      consumer assertion runs): pristine recount realizes 1208 / 61 / 32 / 286 (null total
      379, remainder 0); coercing one observed null edge NaN->0.0 refuses naming BOTH count
      drifts; nulling a CB edge refuses naming the drift + remainder; coercing a SYNTHETIC
      null edge passes the partition gate BY CONSTRUCTION (that class is structural, not
      capacity-based) and is caught one layer deeper by the assembly sentinel refusal —
      defence in depth demonstrated gate by gate.

V5 RED demos (mutations recorded beside the tests; repo sources NEVER touched). MEASURED
OUTCOMES, including one designed premise that the first red run FALSIFIED (recorded per V6 —
the demo was changed, the code never was):
  R1  THE named mutation of spec §12#11 — NaN->0.0 coercion in q_cap ASSEMBLY — applied to a
      /tmp COPY of router.py (string-inserted ``torch.nan_to_num`` after the clone site,
      imported via importlib; copy deleted afterwards; repo sha256 asserted unchanged).
      DESIGNED PREMISE, FALSIFIED ON FIRST RED RUN: "the assembly refusal stops firing".
      Measured truth: the gate keys on the REQUIRED END STATE ("every non-CB entry IS NaN"),
      not on input provenance, so nan_to_num CANNOT disarm it — the mutated module refuses
      the coerced input EXACTLY as pristine does (coercion to ANY finite value trips the
      same branch). Observables that DID move (red confirmed): (a) the mutated module now
      ALSO refuses WELL-FORMED mixed graphs (over-refusal regime — a legal assemble becomes
      impossible); (b) in the one window the gates cannot watch, POST-ASSEMBLY in-memory
      tampering (frozen-dataclass replace, house precedent from test_router.py), the
      realized q_cap bytes carry 0.0 where NaN must stay while edge_flow/volumes/diag stay
      BIT-IDENTICAL — Q=0 is enforced STRUCTURALLY (null edges never enter the route plan),
      not by the sentinel VALUE. The gates protect DOWNSTREAM consumers of the data; R2
      shows what they are protecting against.
  R2  reconstructed defect world (positive nominal fill — the ONLY reclassification variant
      that can pass assembly, since a 0.0-filled CB cap itself fails "finite > 0", asserted
      in G3): the formerly-null edge CONVEYS from step 0, the component becomes a leaky
      bucket — local surcharge is SUPPRESSED (pristine node 3 surcharges FROM ITS OWN
      storage at head 1.75 m; the reclassified world pins it at ~0 and EXPORTS every
      captured m3 downstream instead). This is spec §12#11's "phantom capture/surcharge
      fires on those components", measured.

BLOCKED companion (slow, xfails LOUDLY): the REAL-graph tier through the UNMUTATED
``load_drain_graph`` reader path. Blocked by defect D-G exactly as in test_router.py: the
frozen WF-1 reader rejects the only realized artefact (zero_length_dropped_count=42, reader
assertion 3 has no override); closes when WF-1 republishes OR the owner extends the M5b-style
sanction to the zero-length class. The G4 bypass tier covers the realized PARTITION arithmetic
on the same bytes meanwhile.

V7 scope statement (template): Scope run: G1 20 coupled steps x 12 cells x 9.6 simulated s
(dt 0.48 s), 9-node / 7-edge mixed topology (3 null edges covering the structural roles:
upstream-retention, relay, attachment-to-CB-node); G2 4 seeded DAGs x 8 steps (generator
range 6-25 nodes, realized draws 6-11; 16 null of 45 edges, 35.6% aggregate), dt in
[0.30, 0.45]; G3 six refusal cases + one clean build/route on one fixture family; G4 four
bypass loads of the realized 1721-node / 1587-edge artefact; R1/R2 toy domains x <=10 steps.
Scope claimed: the null-capacity pin, both loud-catch gates, and the capture/surcharge
attribution semantics on DECLARED-SYNTHETIC topologies driven through the PRODUCTION
functions (build_drain_graph / route / couple_step / the loader's partition logic), plus
realized-byte partition arithmetic. Gap => PARTIAL: full real-graph dynamics behind the
frozen reader are BLOCKED by D-G; see the companion at the bottom of this file.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import replace as dc_replace
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import torch
from conftest import load_mutated_source

from jaladhar.coupling import router as router_mod
from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.exchange import build_node_state, couple_step
from jaladhar.coupling.router import (
    RefuseLoadError,
    build_drain_graph,
    load_drain_graph,
    route,
)
from jaladhar.solver.state import StaticFields

REPO = Path(__file__).resolve().parents[2]
GPKG = REPO / "runs" / "drain_graph_build" / "drain_graph.gpkg"
CFG = resolve_config(REPO / "configs" / "coupling.yaml", REPO)

DT = 0.48
CELL_AREA_M2 = 100.0  # contract units block — hand-typed here (V2: my arithmetic)
ISOLATED_WIDTH_M = 2.285

EDGES: tuple[tuple[int, int, float | None], ...] = (
    (1, 2, 4.0),
    (2, 3, 4.0),
    (4, 5, None),
    (5, 6, 2.5),
    (6, 7, 2.5),
    (8, 9, None),
    (9, 5, None),
)
NUM_NODES = 9
E_NULL = (2, 5, 6)
NULL_IDX = torch.tensor(E_NULL, dtype=torch.int64)
OUTFALLS = (3, 7)
EDGE_WIDTH = {0: 3.0, 1: 3.0, 2: 0.8, 3: 2.0, 4: 2.0, 5: 0.7, 6: 0.9}

WIDTH_MEAN = {
    1: 3.0,
    2: 3.0,
    3: 20.0,
    4: ISOLATED_WIDTH_M,
    5: 2.0,
    6: 2.0,
    7: 20.0,
    8: ISOLATED_WIDTH_M,
    9: ISOLATED_WIDTH_M,
}

NODE_CELLS: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((0, 0),),
    2: ((0, 1),),
    3: ((0, 2),),
    4: ((1, 0), (1, 1), (2, 0), (2, 1)),
    8: ((1, 2),),
    9: ((2, 2),),
    5: ((3, 0),),
    6: ((3, 1),),
    7: ((3, 2),),
}
GRID_SHAPE = (4, 3)


def _mixed_kwargs(q_cap: torch.Tensor, capacity_bearing: torch.Tensor) -> dict[str, object]:
    nmap = torch.full(GRID_SHAPE, -1, dtype=torch.int32)
    counts = torch.zeros(NUM_NODES, dtype=torch.int64)
    rows = torch.zeros(NUM_NODES, dtype=torch.int32)
    cols = torch.zeros(NUM_NODES, dtype=torch.int32)
    for nid, cells in NODE_CELLS.items():
        for r, c in cells:
            nmap[r, c] = nid
        counts[nid - 1] = len(cells)
        rows[nid - 1], cols[nid - 1] = cells[0]
    outfall = torch.zeros(NUM_NODES, dtype=torch.bool)
    for o in OUTFALLS:
        outfall[o - 1] = True
    return dict(
        edge_from=torch.tensor([e[0] - 1 for e in EDGES], dtype=torch.int64),
        edge_to=torch.tensor([e[1] - 1 for e in EDGES], dtype=torch.int64),
        capacity_bearing=capacity_bearing,
        q_cap_nom_m3s=q_cap,
        width_mean_m=torch.tensor(
            [WIDTH_MEAN[i] for i in range(1, NUM_NODES + 1)], dtype=torch.float64
        ),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, NUM_NODES, dtype=torch.float64),
        contrib_area_m2=torch.zeros(NUM_NODES, dtype=torch.float64),
        outfall_node=outfall,
        node_cell_row=rows,
        node_cell_col=cols,
        node_id_map=nmap,
        node_cell_count=counts,
    )


def _pristine_kwargs() -> dict[str, object]:
    raw = torch.tensor([0.0 if e[2] is None else float(e[2]) for e in EDGES], dtype=torch.float64)
    cb = torch.tensor([e[2] is not None for e in EDGES], dtype=torch.bool)
    q = torch.where(cb, raw, torch.full_like(raw, float("nan")))
    return _mixed_kwargs(q, cb)


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
        edge_open=(0.0, 0.0, 0.0, 0.0),
        shape=shape,
    )


def _capture_witness_m3(cr, nmap: torch.Tensor, node_id: int) -> float:
    """MY reduction of the returned detached field (V2: not a production sum)."""
    return float(cr.captured_depth_m.to(torch.float64)[nmap == node_id].sum()) * CELL_AREA_M2


def _return_witness_m3(cr, nmap: torch.Tensor, node_id: int) -> float:
    return float(cr.returned_depth_m.to(torch.float64)[nmap == node_id].sum()) * CELL_AREA_M2


def _node_vol_m3(state, g) -> torch.Tensor:
    return state.h_node_m.to(torch.float64) * g.node_plan_area_m2


def test_green_coupled_null_edges_zero_flow_retention_and_attribution() -> None:
    """20 couple_step steps, two phases, 9-node mixed topology through PRODUCTION code.

    Phase 1 (12 steps, uniform 0.35 m pond — everywhere below freeboard): null-edge flow
    == 0.0 EXACTLY each step; retention subjects' outgoing books stay BITWISE 0.0
    (edge-out 0, return 0); null-connectivity nodes CAPTURE (they are real nodes) and their
    incoming books grow by their OWN capture witness only; node 4's volume never decreases.
    Phase 2 (8 steps, 2.5 m pond over node 4's four cells): node 4 surcharges FROM ITS OWN
    storage while the null edge below it still reads exactly 0.0, returned depth lands only
    on node 4's cells, and node 5 — whose ONLY in-edges are null — still gains nothing via
    them. Cumulative conveyance across ALL null edges over the WHOLE run == 0.0 exactly;
    cumulative node-storage delta reconciles with MY accumulated capture-minus-return.

    V2 observables: returned detached ``edge_flow_m3s`` bytes, f64 cumulative-book
    increments, MY witness reductions — never production self-assessments.
    V7 scope: 20 steps x 12 cells x 9.6 simulated s (dt 0.48 s), 9 nodes / 7 edges
    (3 null); scope claimed: the coupled-tier null pin + retention + surcharge attribution
    on this topology; real-graph dynamics BLOCKED by D-G (see companion)."""
    g = build_drain_graph(**_pristine_kwargs())
    static = _static_shell(GRID_SHAPE)
    nmap = g.node_id_map
    state = build_node_state(g)
    vol0 = _node_vol_m3(state, g).sum()

    h = torch.full(GRID_SHAPE, 0.35, dtype=torch.float32)
    conveyed_null = 0.0
    cum_net_m3 = 0.0
    cap_witness = {nid: 0.0 for nid in (5, 8, 9)}
    book_in_prev = {nid: 0.0 for nid in (5, 8, 9)}
    vol_prev = _node_vol_m3(state, g)

    def step_coupled(h_in: torch.Tensor):
        return couple_step(
            h_in,
            torch.zeros(4, 2, dtype=torch.float32),
            torch.zeros(3, 3, dtype=torch.float32),
            static,
            state,
            g,
            DT,
        )

    for step in range(12):
        cr = step_coupled(h)
        state, h = cr.node_state_new, cr.h_new
        qe = cr.edge_flow_m3s
        assert bool((qe[NULL_IDX] == 0.0).all()), f"phase1 step {step}: null edge carried flow"
        conveyed_null += float(qe[NULL_IDX].to(torch.float64).sum()) * DT
        cum_net_m3 += cr.capture_m3 - cr.return_m3
        assert cr.surcharging_nodes == 0, f"phase1 step {step}: unexpected surcharge"

        for nid in (4, 8, 9):
            moved = float(state.vol_out_m3_cum[nid - 1])
            assert moved == 0.0, f"phase1 step {step}: node {nid} outgoing books moved ({moved!r})"

        for nid in (5, 8, 9):
            w = _capture_witness_m3(cr, nmap, nid)
            assert w >= 0.0, f"phase1 step {step}: node {nid} captured negative depth"
            inc = float(state.vol_in_m3_cum[nid - 1]) - book_in_prev[nid]
            book_in_prev[nid] = float(state.vol_in_m3_cum[nid - 1])
            cap_witness[nid] += w
            assert abs(inc - w) <= 1e-6 * max(1.0, w), (
                f"phase1 step {step}: node {nid} inflow increment {inc!r} != own capture "
                f"{w!r} — volume arrived via something other than its own cells"
            )
        vol_new = _node_vol_m3(state, g)
        assert bool(vol_new[3] >= vol_prev[3]), "retention node 4 lost volume in phase 1"
        vol_prev = vol_new

    for nid in (5, 8, 9):
        assert cap_witness[nid] > 0.0, (
            f"null-connectivity node {nid} captured NOTHING across phase 1 — not behaving "
            "as a real capture-bearing node"
        )

    surcharged_steps = 0
    for step in range(8):
        h2 = torch.full(GRID_SHAPE, 0.35, dtype=torch.float32)
        for r, c in NODE_CELLS[4]:
            h2[r, c] = 2.5
        cr = step_coupled(h2)
        state, h = cr.node_state_new, cr.h_new
        qe = cr.edge_flow_m3s
        assert bool((qe[NULL_IDX] == 0.0).all()), f"phase2 step {step}: null edge carried flow"
        conveyed_null += float(qe[NULL_IDX].to(torch.float64).sum()) * DT
        cum_net_m3 += cr.capture_m3 - cr.return_m3

        for nid in (5, 8, 9):
            w = _capture_witness_m3(cr, nmap, nid)
            inc = float(state.vol_in_m3_cum[nid - 1]) - book_in_prev[nid]
            book_in_prev[nid] = float(state.vol_in_m3_cum[nid - 1])
            cap_witness[nid] += w
            assert abs(inc - w) <= 1e-6 * max(1.0, w), (
                f"phase2 step {step}: node {nid} inflow increment {inc!r} != own capture "
                f"{w!r} — volume arrived via something other than its own cells"
            )
        if _return_witness_m3(cr, nmap, 4) > 0.0:
            surcharged_steps += 1
            owners = torch.unique(nmap[cr.returned_depth_m > 0])
            assert {int(o) for o in owners.tolist()} <= {4}, (
                f"phase2 step {step}: returned depth landed off the surcharging node: "
                f"{owners.tolist()}"
            )
    assert (
        surcharged_steps >= 1
    ), "node 4 never surcharged on its own accumulated storage — fixture premise failed"

    assert conveyed_null == 0.0, f"cumulative conveyance across null edges: {conveyed_null!r}"
    for nid in (5, 8, 9):
        book_total = float(state.vol_in_m3_cum[nid - 1])
        assert abs(book_total - cap_witness[nid]) <= 1e-5 * max(1.0, cap_witness[nid]), (
            f"node {nid}: cumulative inflow {book_total!r} != cumulative own capture "
            f"{cap_witness[nid]!r} — phantom volume arrived via the null edges"
        )

    d_nodes = float(_node_vol_m3(state, g).sum() - vol0)
    assert abs(d_nodes - cum_net_m3) <= 1e-3 * max(
        1.0, abs(d_nodes)
    ), f"storage delta {d_nodes!r} vs accumulated capture-return {cum_net_m3!r}"
    print(
        f"\n[inv11-g1] surcharging_steps(node4)={surcharged_steps} "
        f"conveyed_across_null={conveyed_null!r} m3 "
        f"capture_witness[m3]={ {k: round(v, 6) for k, v in cap_witness.items()} }"
    )


def _random_dag(seed: int):
    gen = torch.Generator().manual_seed(seed)
    n = int(torch.randint(6, 26, (1,), generator=gen))
    edges: list[tuple[int, int, float | None]] = []
    for j in range(2, n + 1):
        for _ in range(int(torch.randint(1, 3, (1,), generator=gen))):
            parent = int(torch.randint(1, j, (1,), generator=gen))
            cap = (
                None
                if float(torch.rand(1, generator=gen)) < 0.30
                else round(float(torch.rand(1, generator=gen)) * 7.0 + 0.2, 3)
            )
            edges.append((parent, j, cap))
    if not any(c is not None for _f, _t, c in edges):
        edges.append((1, 2, 3.0))
    if all(c is not None for _f, _t, c in edges):
        # V7: a trial with ZERO null edges would assert against an empty mask — vacuously

        j = int(torch.randint(0, len(edges), (1,), generator=gen))
        f0, t0, _c0 = edges[j]
        edges[j] = (f0, t0, None)
    ef = torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64)
    et = torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64)
    cb = torch.tensor([e[2] is not None for e in edges], dtype=torch.bool)
    raw = torch.tensor(
        [float(e[2]) if e[2] is not None else 0.0 for e in edges], dtype=torch.float64
    )
    q = torch.where(cb, raw, torch.full_like(raw, float("nan")))
    g = build_drain_graph(
        edge_from=ef,
        edge_to=et,
        capacity_bearing=cb,
        q_cap_nom_m3s=q,
        width_mean_m=torch.full((n,), ISOLATED_WIDTH_M, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, n, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n, dtype=torch.float64),
        outfall_node=torch.zeros(n, dtype=torch.bool),
        node_cell_row=torch.arange(n, dtype=torch.int32),
        node_cell_col=torch.arange(n, dtype=torch.int32),
        node_id_map=torch.full((1, 1), -1, dtype=torch.int32),
        node_cell_count=torch.ones(n, dtype=torch.int64),
    )
    sources = np.nonzero(np.bincount(et.numpy(), minlength=n) == 0)[0]
    retention = np.ones(n, dtype=bool)
    retention[np.unique(ef[cb].numpy())] = False
    return g, sources, retention, n


@pytest.mark.parametrize("seed", [1111, 2222, 3333, 4444])
def test_green_route_tier_multistep_null_zero_conservation_retention(seed: int) -> None:
    """Multi-step route() on seeded mixed DAGs: null Q == 0.0 EXACTLY at EVERY step;
    total volume closes against MY injected total each step (sum of RETURNED tensors, not
    diag bookkeeping); zero-denominator retention nodes never lose volume across the run.

    V7 scope: 4 graphs x 8 steps (generator range 6-25 nodes, realized draws 6-11;
    16 null of 45 edges aggregate, 35.6%), dt in [0.30, 0.45];
    scope claimed: the route-tier null pin + conservation + retention under repeated
    injection."""
    g, sources, retention, n = _random_dag(seed)
    dt = 0.3 + 0.05 * (seed % 4)
    gen = torch.Generator().manual_seed(seed + 1)
    vol = torch.zeros(n, dtype=torch.float64)
    src_t = torch.tensor(sources, dtype=torch.int64)
    ret_t = torch.tensor(retention)
    null_mask = ~g.capacity_bearing
    for step in range(8):
        inj = torch.rand(len(sources), dtype=torch.float64, generator=gen) * 3.0
        vol[src_t] += inj
        vol_new, q_edge, _diag = route(vol, g, dt)
        assert bool((q_edge[null_mask] == 0.0).all()), f"seed {seed} step {step}: null flow"
        scale = max(1.0, float(vol.sum()))
        assert (
            abs(float(vol_new.sum()) - float(vol.sum())) <= 1e-9 * scale
        ), f"seed {seed} step {step}: conservation broken"
        assert bool(
            (vol_new[ret_t] >= vol[ret_t] - 1e-12).all()
        ), f"seed {seed} step {step}: a retention node lost volume"
        vol = vol_new
    print(f"[inv11-g2 seed={seed}] nodes={n} null_edges={int(null_mask.sum())} dt={dt:.2f} OK")


def test_green_sentinel_integrity_assembly_refuses_coerced_inputs() -> None:
    """Every coercion shape is refused LOUDLY at build_drain_graph, exercised on my own
    vectors: a non-CB edge carrying 0.0 (THE invariant-#11 defect), any other finite value;
    a CB edge carrying NaN, 0.0 (coerced AND reclassified — lands on the 'finite > 0'
    branch), or negative. Pristine vectors assemble; sentinels stay NaN bitwise after
    assembly AND after route(); null-edge discharge stays exactly 0.0.

    V2 observable: RefuseLoadError messages from the TWO distinct sentinel branches, then
    the realized q_cap bytes of the built graph post-route.
    V7 scope: six refusal cases + one clean build/route on the 9-node fixture; scope
    claimed: both assembly sentinel branches against every coercion shape they name."""

    def coerced(mutate_q=None, mutate_cb=None):
        kw = _pristine_kwargs()
        q = kw["q_cap_nom_m3s"].clone()
        cb = kw["capacity_bearing"].clone()
        for idx, val in (mutate_q or {}).items():
            q[idx] = val
        for idx, val in (mutate_cb or {}).items():
            cb[idx] = val
        kw["q_cap_nom_m3s"], kw["capacity_bearing"] = q, cb
        return kw

    cases = [
        (coerced(mutate_q={2: 0.0}), r"non-capacity-bearing edges must carry NaN sentinels"),
        (coerced(mutate_q={5: 0.0}), r"non-capacity-bearing edges must carry NaN sentinels"),
        (coerced(mutate_q={6: 0.9}), r"non-capacity-bearing edges must carry NaN sentinels"),
        (coerced(mutate_q={0: float("nan")}), r"capacity-bearing q_cap must be finite > 0"),
        (
            coerced(mutate_q={2: 0.0}, mutate_cb={2: True}),
            r"capacity-bearing q_cap must be finite > 0",
        ),
        (coerced(mutate_q={0: -1.0}), r"capacity-bearing q_cap must be finite > 0"),
    ]
    for i, (kw, pattern) in enumerate(cases):
        with pytest.raises(RefuseLoadError, match=pattern) as excinfo:
            build_drain_graph(**kw)
        print(f"[inv11-g3 case {i}] refused verbatim: {excinfo.value}")

    g = build_drain_graph(**_pristine_kwargs())
    null_mask = ~g.capacity_bearing
    assert bool(torch.isnan(g.q_cap_nom_m3s[null_mask]).all())
    assert bool(torch.isfinite(g.q_cap_nom_m3s[g.capacity_bearing]).all())
    assert bool((g.q_cap_nom_m3s[g.capacity_bearing] > 0).all())
    vol = torch.linspace(0.1, 5.0, NUM_NODES, dtype=torch.float64)
    _vn, qe, _dg = route(vol, g, DT)
    assert bool(torch.isnan(g.q_cap_nom_m3s[null_mask]).all()), "route disturbed the sentinels"
    assert bool((qe[NULL_IDX] == 0.0).all())


_BYTES_CACHE: dict[str, tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict, dict]] = {}


def _real_bytes():
    if "inv11" not in _BYTES_CACHE:
        man = json.loads((REPO / "runs/drain_graph_build/manifest.json").read_text())
        adj = json.loads((REPO / "runs/drain_graph_build/drain_graph_adjacency.json").read_text())
        _BYTES_CACHE["inv11"] = (
            gpd.read_file(GPKG, layer="drain_nodes"),
            gpd.read_file(GPKG, layer="drain_edges"),
            adj,
            man,
        )
    return _BYTES_CACHE["inv11"]


def _bypass_load(monkeypatch, edges_gdf: gpd.GeoDataFrame | None = None):
    nodes_gdf, edges, adj, man = _real_bytes()
    art = {
        "nodes_gdf": nodes_gdf,
        "edges_gdf": edges if edges_gdf is None else edges_gdf,
        "adjacency": adj,
        "manifest": man,
    }
    monkeypatch.setattr(router_mod, "read_artefact", lambda *a, **k: art)
    return load_drain_graph(CFG, REPO)


def _recount_partition(edges_gdf: gpd.GeoDataFrame) -> dict[str, int]:
    src = edges_gdf["edge_source"].astype(str)
    basis = edges_gdf["capacity_basis"].astype(str)
    q_notna = edges_gdf["q_capacity_nom_m3s"].notna().to_numpy()
    obs = (src == "observed").to_numpy()
    syn = (src == "synthesised").to_numpy()
    obs_null = obs & ~q_notna
    zero = obs_null & basis.str.startswith("zero measured slope").to_numpy()
    area = obs_null & basis.str.startswith("contributing area").to_numpy()
    cb = obs & q_notna
    remainder = ~(syn | cb | zero | area)
    return {
        "capacity_bearing": int(cb.sum()),
        "zero_slope": int(zero.sum()),
        "area_capped": int(area.sum()),
        "synthetic": int(syn.sum()),
        "remainder": int(remainder.sum()),
        "null_total": int((~cb).sum()),
    }


@pytest.mark.slow
def test_green_partition_gate_real_bytes_reclassification_refused(monkeypatch) -> None:
    """The claim's headline arithmetic realized on REAL bytes, then every reclassification
    refused loudly by the loader partition gate — except the synthetic-class coercion,
    which the partition gate passes BY CONSTRUCTION and the ASSEMBLY sentinel refusal
    catches one layer deeper (defence in depth, demonstrated gate by gate).

    V2 observables: my independent recount vs the loader's realized mask; verbatim
    RefuseLoadError messages carrying BOTH drifted counts.
    V7 scope: five bypass loads of the realized 1721-node / 1587-edge artefact (full
    domain); scope claimed: the partition gate's reclassification teeth on all observed
    classes + the synthetic-class seam. Real-graph dynamics behind the UNMUTATED reader
    remain BLOCKED by D-G (companion)."""
    _nodes, edges, _adj, _man = _real_bytes()

    rec = _recount_partition(edges)
    print(f"\n[inv11-g4 pristine recount] {rec}")
    assert rec == {
        "capacity_bearing": 1208,
        "zero_slope": 61,
        "area_capped": 32,
        "synthetic": 286,
        "remainder": 0,
        "null_total": 379,
    }, f"realized partition drifted off the claim: {rec}"
    g = _bypass_load(monkeypatch)
    assert int(g.capacity_bearing.sum().item()) == rec["capacity_bearing"]
    assert int((~g.capacity_bearing).sum().item()) == rec["null_total"]
    assert bool(torch.isnan(g.q_cap_nom_m3s[~g.capacity_bearing]).all())

    basis_str = edges["capacity_basis"].astype(str)
    source_str = edges["edge_source"].astype(str)
    q_notna = edges["q_capacity_nom_m3s"].notna().to_numpy()
    zero_idx = int(np.nonzero(basis_str.str.startswith("zero measured slope").to_numpy())[0][0])
    area_idx = int(np.nonzero(basis_str.str.startswith("contributing area").to_numpy())[0][0])
    cb_idx = int(np.nonzero((source_str == "observed").to_numpy() & q_notna)[0][0])
    syn_idx = int(np.nonzero((source_str == "synthesised").to_numpy())[0][0])

    def _mutated(idx: int, value: float) -> gpd.GeoDataFrame:
        em = edges.copy()
        em.loc[idx, "q_capacity_nom_m3s"] = value
        return em

    with pytest.raises(RefuseLoadError, match=r"edge partition mismatch") as exc_zero:
        _bypass_load(monkeypatch, _mutated(zero_idx, 0.0))
    msg = str(exc_zero.value)
    print(f"[inv11-g4 zero-slope coercion] refused verbatim: {msg}")
    assert "capacity_bearing 1209 != 1208" in msg and "zero_slope 60 != 61" in msg

    with pytest.raises(RefuseLoadError, match=r"edge partition mismatch") as exc_area:
        _bypass_load(monkeypatch, _mutated(area_idx, 0.0))
    msg_area = str(exc_area.value)
    print(f"[inv11-g4 area-capped coercion] refused verbatim: {msg_area}")
    assert "capacity_bearing 1209 != 1208" in msg_area and "area_capped 31 != 32" in msg_area

    with pytest.raises(RefuseLoadError, match=r"edge partition mismatch") as exc_cb:
        _bypass_load(monkeypatch, _mutated(cb_idx, float("nan")))
    msg_cb = str(exc_cb.value)
    print(f"[inv11-g4 CB nulled] refused verbatim: {msg_cb}")
    assert "capacity_bearing 1207 != 1208" in msg_cb
    assert "1 edges outside all four partition classes" in msg_cb

    with pytest.raises(
        RefuseLoadError, match=r"non-capacity-bearing edges must carry NaN sentinels"
    ) as exc_syn:
        _bypass_load(monkeypatch, _mutated(syn_idx, 0.0))
    print(f"[inv11-g4 synthetic coercion] caught verbatim: {exc_syn.value}")


# R1 — V5 red demo: THE named mutation (NaN->0.0 in q_cap assembly) on a /tmp COPY


def test_v5_red_demo_nan_to_num_assembly_tmp_copy() -> None:
    """MUTATION ``nan_to_num__sentinel_coerced_in_assembly``: a /tmp COPY of router.py with
    ``torch.nan_to_num(q_cap, nan=0.0)`` inserted immediately after the clone site —
    exactly invariant #11's defect placed in q_cap ASSEMBLY. Repo source untouched
    (sha256 asserted before/after); copy deleted afterwards.

    DESIGNED PREMISE FALSIFIED ON FIRST RED RUN (recorded per V6; demo changed, code never):
    "the assembly refusal stops firing" is FALSE — measured on this exact mutation, the
    gate keys on the required END STATE (every non-CB entry IS NaN), not on input
    provenance, so coercing NaN->0.0 cannot disarm it: the mutated module refuses the
    coerced fixture EXACTLY like pristine (arm B). The observables that DID move:

      (a) RED - over-refusal regime: the mutated module refuses a WELL-FORMED mixed graph
          pristine assembles happily (arm C). A legal load becomes impossible.
      (b) RED (realized state, V1) - in the one window the gates cannot watch, POST-
          ASSEMBLY in-memory tampering (frozen-dataclass replace; house precedent
          test_router.test_zero_denominator_retains_volume): q_cap bytes carry 0.0 where
          NaN must stay, on a graph object that exists and routes.
      (c) FINDING, recorded not passed: under (b) edge_flow/volumes/diag are BIT-IDENTICAL
          to pristine - Q=0 is enforced STRUCTURALLY (null edges never enter the route
          plan), never by the sentinel value. The sentinel+partition gates protect
          DOWNSTREAM consumers of the data; R2 measures what they protect against.

    V7 scope: one mutation; one coerced + one well-formed fixture through BOTH modules;
    one post-gate tampered graph routed at dt 0.48 over 9 nodes; scope claimed: assembly-
    site coercion semantics + the post-gate realized-state window only."""
    repo_router = Path(router_mod.__file__)
    sha_before = hashlib.sha256(repo_router.read_bytes()).hexdigest()
    src = repo_router.read_text()
    needle = "    q_cap = q_cap_nom_m3s.to(dev, torch.float64).clone()\n"
    assert src.count(needle) == 1, "mutation site moved — update this demo, do not blind-edit"
    mutation_line = (
        needle
        + "    q_cap = torch.nan_to_num(q_cap, nan=0.0)"
        + "  # INV11 V5 MUTATION: sentinel coerced (spec §12 row #11)\n"
    )
    mutated, _mut_path = load_mutated_source(
        repo_router,
        [(needle, mutation_line)],
        tag=f"{os.getpid()}_{uuid.uuid4().hex[:8]}",
        prefix="inv11_router",
    )
    try:

        kw_coerced = _pristine_kwargs()
        q_coerced = kw_coerced["q_cap_nom_m3s"].clone()
        q_coerced[2] = 0.0
        kw_coerced["q_cap_nom_m3s"] = q_coerced

        with pytest.raises(RefuseLoadError, match=r"NaN sentinels"):
            build_drain_graph(**kw_coerced)

        with pytest.raises(RefuseLoadError, match=r"NaN sentinels") as exc_mut_coerced:
            mutated.build_drain_graph(**kw_coerced)
        print(
            "\n[inv11-r1 arm B] mutated module refused the COERCED input too "
            "(gate is end-state-keyed, coercion-proof by construction):",
            exc_mut_coerced.value,
        )

        g_clean = build_drain_graph(**_pristine_kwargs())
        with pytest.raises(RefuseLoadError, match=r"NaN sentinels") as exc_mut_clean:
            mutated.build_drain_graph(**_pristine_kwargs())
        print(f"[inv11-r1 arm C] RED: mutated refused a WELL-FORMED graph: {exc_mut_clean.value}")

        gm_tampered = dc_replace(
            g_clean, q_cap_nom_m3s=torch.nan_to_num(g_clean.q_cap_nom_m3s, nan=0.0)
        )
        null_mask = ~gm_tampered.capacity_bearing
        assert bool(torch.isnan(g_clean.q_cap_nom_m3s[null_mask]).all()), "pristine disturbed"
        assert not bool(torch.isnan(gm_tampered.q_cap_nom_m3s[null_mask]).any())
        assert bool(
            (gm_tampered.q_cap_nom_m3s[null_mask] == 0.0).all()
        ), "post-gate tampering did not surface 0.0 in realized sentinel bytes"
        vol = torch.linspace(0.1, 5.0, NUM_NODES, dtype=torch.float64)
        v_p, q_p, d_p = route(vol, g_clean, DT)
        v_m, q_m, d_m = route(vol, gm_tampered, DT)
        assert bool((q_m[NULL_IDX] == 0.0).all()), "tampered world conveys on null edges?!"
        assert torch.equal(q_p, q_m), "discharge moved under sentinel destruction"
        assert torch.equal(v_p, v_m), "volume trajectory moved under sentinel destruction"
        assert d_p == d_m, "routing diagnostics moved under sentinel destruction"
        print(
            "[inv11-r1 arm D] RED: realized sentinel bytes destroyed (NaN->0.0) while "
            "edge_flow/volumes/diag BIT-identical - Q=0 is structural, not value-borne"
        )
    finally:
        pass

    assert (
        hashlib.sha256(repo_router.read_bytes()).hexdigest() == sha_before
    ), "repo router.py changed during the red demo - MUST never happen (copy-only mutation)"


def _two_path_world(cb_null_edge: bool):
    if cb_null_edge:
        edges = [(1, 2, 3.0), (3, 2, 5.0)]
        widths = [3.0, 1.9, 0.8]
    else:
        edges = [(1, 2, 3.0), (3, 2, None)]
        widths = [3.0, 3.0, ISOLATED_WIDTH_M]
    ef = torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64)
    et = torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64)
    cb = torch.tensor([e[2] is not None for e in edges], dtype=torch.bool)
    raw = torch.tensor(
        [float(e[2]) if e[2] is not None else 0.0 for e in edges], dtype=torch.float64
    )
    q = torch.where(cb, raw, torch.full_like(raw, float("nan")))
    nmap = torch.tensor([[1, -1, 2], [-1, 3, -1]], dtype=torch.int32)
    g = build_drain_graph(
        edge_from=ef,
        edge_to=et,
        capacity_bearing=cb,
        q_cap_nom_m3s=q,
        width_mean_m=torch.tensor(widths, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.tensor([10.0, 5.0, 1.0], dtype=torch.float64),
        contrib_area_m2=torch.zeros(3, dtype=torch.float64),
        outfall_node=torch.tensor([False, True, False]),
        node_cell_row=torch.tensor([0, 0, 1], dtype=torch.int32),
        node_cell_col=torch.tensor([0, 2, 1], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.ones(3, dtype=torch.int64),
    )
    return g, nmap


def test_red_world_positive_fill_phantom_capture_conveyance_surcharge() -> None:
    """Spec §12#11's red signature — 'phantom capture/surcharge fires on those components'
    — measured in the reconstructed world where the formerly-null edge is reclassified with
    a positive nominal fill (the only fill value that can pass assembly; the 0.0-fill is
    itself refused, shown in G3). Deep uniform pond (3.0 m > freeboard 1.5), 10 steps.

    Pristine world: the routing-only component fills toward the pond depth and SURCHARGES
    FROM ITS OWN storage while its null edge carries exactly 0.0 forever.
    Reconstructed world (measured on first run): the same edge CONVEYS from step 0 and
    exports EVERY m3 it captures downstream (conveyed == captured), so the component
    becomes a leaky bucket — its head stays pinned at ~0, LOCAL surcharge is suppressed
    entirely (returned 0.0 vs pristine's material return), and the storage problem is
    exported to the downstream node instead. The capture TOTAL also moves materially
    (pristine's wider declared inlet out-captures the coerced narrow one); direction is a
    fixture property, so only the structural signatures are asserted.

    V2 observables: per-step q_edge bytes on the formerly-null edge, MY capture/return
    witnesses, realized max heads — printed verbatim.
    V7 scope: 2 worlds x 10 steps x 6 cells x ~4.8 simulated s, 3 nodes / 2 edges; scope
    claimed: the physics-level consequence the sentinel+partition gates prevent, on a
    declared-synthetic topology."""
    gp, nmap = _two_path_world(cb_null_edge=False)
    gh, _nmap_h = _two_path_world(cb_null_edge=True)
    static = _static_shell((2, 3))
    h0 = torch.full((2, 3), 3.0, dtype=torch.float32)
    null_edge_idx = 1

    def run_world(g):
        state = build_node_state(g)
        h = h0.clone()
        tot_conv = tot_cap3 = tot_ret3 = 0.0
        max_head3 = 0.0
        first_convey_step = None
        for step in range(10):
            cr = couple_step(
                h,
                torch.zeros(2, 2, dtype=torch.float32),
                torch.zeros(1, 3, dtype=torch.float32),
                static,
                state,
                g,
                DT,
            )
            state, h = cr.node_state_new, cr.h_new
            qv = float(cr.edge_flow_m3s[null_edge_idx])
            if qv > 0.0 and first_convey_step is None:
                first_convey_step = step
            tot_conv += qv * DT
            tot_cap3 += _capture_witness_m3(cr, nmap, 3)
            tot_ret3 += _return_witness_m3(cr, nmap, 3)
            max_head3 = max(max_head3, float(state.h_node_m[2]))
        return {
            "conveyed_m3": tot_conv,
            "capture_at_3_m3": tot_cap3,
            "returned_at_3_m3": tot_ret3,
            "max_head_3_m": max_head3,
            "first_convey_step": first_convey_step,
        }

    pr = run_world(gp)
    ph = run_world(gh)
    print(f"\n[inv11-r2] pristine     : {pr}")
    print(f"[inv11-r2] reclassified: {ph}")

    assert pr["conveyed_m3"] == 0.0, f"pristine null edge conveyed {pr['conveyed_m3']!r} m3"
    assert pr["first_convey_step"] is None
    assert pr["returned_at_3_m3"] > 0.0, "pristine node 3 never surcharged — premise failed"
    assert pr["max_head_3_m"] > 1.5, "pristine node 3 never crossed freeboard — premise failed"

    assert (
        ph["first_convey_step"] is not None and ph["first_convey_step"] <= 1
    ), "reclassified edge did not convey immediately — phantom-flow signature missing"
    assert ph["conveyed_m3"] > 0.5, f"conveyance dust: {ph['conveyed_m3']!r} m3"

    assert abs(ph["conveyed_m3"] - ph["capture_at_3_m3"]) <= 1e-5 * max(
        1.0, ph["capture_at_3_m3"]
    ), (
        f"phantom world captured {ph['capture_at_3_m3']!r} but conveyed only "
        f"{ph['conveyed_m3']!r} — export signature incomplete"
    )
    assert (
        ph["returned_at_3_m3"] == 0.0 and pr["returned_at_3_m3"] > 0.0
    ), "surcharge-attribution shift missing: pristine surcharges locally, phantom exports"
    assert ph["max_head_3_m"] < 1.5 <= pr["max_head_3_m"], (
        "head-pinning signature missing: pristine crossed freeboard, "
        f"phantom pinned at {ph['max_head_3_m']:.3f} m"
    )
    print(
        f"[inv11-r2] margins: conveyed={ph['conveyed_m3']:.3f} m3 (== capture, exported); "
        f"local return {pr['returned_at_3_m3']:.3f} -> {ph['returned_at_3_m3']:.3f} m3; "
        f"node-3 max head {pr['max_head_3_m']:.3f} -> {ph['max_head_3_m']:.3f} m; "
        f"capture totals moved: {pr['capture_at_3_m3']:.3f} -> {ph['capture_at_3_m3']:.3f} m3"
    )


# BLOCKED companion (V7): the REAL-graph tier behind the UNMUTATED reader


@pytest.mark.slow
def test_real_graph_companion_full_or_blocked() -> None:
    """The SAME claim on the REAL full-domain graph through the UNMUTATED loader: 379
    non-capacity-bearing edges realize with NaN sentinels, and one route() pass leaves
    Q == 0.0 exactly on every one of them. While the frozen WF-1 reader rejects the only
    realized artefact (defect D-G: zero_length_dropped_count=42 vs assertion 3's
    requirement of 0) this xfails LOUDLY naming the closure conditions (house pattern).
    If the seam ever opens, the bounded realization runs and must hold."""
    try:
        g = load_drain_graph(CFG, REPO)
    except RefuseLoadError as exc:
        pytest.xfail(
            "BLOCKED (V7): real-graph routing-null coverage closes when WF-1 republishes an "
            "artefact the frozen reader accepts (D-G seam: zero_length_dropped_count=42) OR "
            "the owner extends the M5b-style sanction to the zero-length class -> "
            f"{exc}"
        )
    null_mask = ~g.capacity_bearing
    assert int(null_mask.sum().item()) == 379
    assert bool(torch.isnan(g.q_cap_nom_m3s[null_mask]).all())
    vol = torch.full((g.num_nodes,), 1000.0, dtype=torch.float64)
    _vn, qe, _dg = route(vol, g, DT)
    assert bool((qe[null_mask] == 0.0).all())
    print(f"\n[inv11-real] null_edges={int(null_mask.sum().item())} q==0 exactly: True")
