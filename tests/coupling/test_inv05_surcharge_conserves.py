"""V2: if surcharge conservation were broken I would observe the SURFACE-side returned
volume — ``sum(returned_depth_m) * cell_area``, produced by the RASTER SCATTER path
(uniform f32 increments masked_scattered onto each owner cell) — disagree with the
NODE-side removed volume — the per-step DELTA of the f64 ``vol_out_m3_cum`` cumulative
books less the router's own published edge transfers, produced by INDEX_ADD BOOKKEEPING
on node volumes. The two sides are computed by disjoint code paths from the same internal
decision (``r_node``), so agreement detects a defect on EITHER side: a perturbed book
subtraction moves only the books side; a mis-scattered increment or a wrong cell-count
divide moves only the raster side. Agreement is therefore a non-mirror observable.

===============================================================================
INVARIANT #5 ``surcharge-conserves`` — sprint-level coupling invariant (WF-2)
===============================================================================
Claim (spec §12 row #5): surface-side returned volume equals node-side removed volume
EACH STEP, across a MULTI-STEP coupled chain in which routing feeds returns — i.e.
upstream capture is routed downstream through capacity-bearing edges and drives
downstream surcharge — with BOTH stability caps (0.9 capture cap, 0.9 return cap)
witnessed binding at least once, asserted PER STEP and CUMULATIVELY. This is the
sprint-level form; the single-step unit identity lives in test_exchange.py
(``test_surcharge_conserves_identity_and_uniform_distribution``) and does not exercise
routing-fed returns, multi-node scatters, or book accumulation across steps.

Identity measured (per step t, all quantities realized from returned objects):

    surf_t   = sum(returned_depth_m.to(f64)) * 100.0                  # raster scatter path
    removed_t = Σ_i [Δvol_out_m3_cum_i] − Σ_i edge_out_i              # books path, where
              (edge_out_i recomputed from the DETACHED diagnostic ``edge_flow_m3s`` × dt;
              routing transfers cancel exactly in the global books sum because every
              transferred m³ leaves one node's out-books and enters another's in-books,
              so subtracting them isolates the RETURN term — the quantity #5 guards)

    assert |surf_t − removed_t| ≤ max(4e-6 · max(|surf_t|, |removed_t|), 1e-12)
    plus the same bound on the run-cumulative sums.

Tolerance basis: the dominant noise is ONE f32 rounding of each uniform increment
(rel ε ≈ 6e-8) plus f32 cast noise on the diagnostic q_edge (abs ≈ 2e-10 m³ here);
realized green misagreement must sit FAR under 4e-6 (printed as evidence), while the
V5 mutants move it to ~1e-2 / 1.0 — a ≥2500× margin either way.

V5 RED DEMOS — /tmp COPY ONLY of exchange.py (repo source never touched). The pinned
mutation site is exchange.py line "vol_out_m3_cum=node_state.vol_out_m3_cum +
edge_out_vol + r_node," (asserted unique before patching; if the production line ever
moves, this pin fails LOUD and must be re-pointed consciously):
  1. ``x1.01_node_side_book_subtraction`` — spec §12#5's named mutation: scale the
     NODE-side subtraction by 1.01 in the BOOKS update only. Predicted non-crash: the
     in-function continuity guard compares local f64 tensors upstream of this line, the
     negative-depth guard reads the untouched raster path — so the IDENTITY assertion
     itself is what fires (~9.9e-3 relative disagreement).
  2. ``drop_return_from_books`` — remove r_node from the books while keeping the raster
     add: books-side removal collapses to ~0 against a live raster field (rel → 1.0).
Both attempts are executed and logged (kind, crashed?, realized worst rel, captured
failing output excerpt); both must redden WITHOUT crashing, else the test fails naming
the attempt. Outcomes are asserted, not narrated.

V7 scope statement (filled template): Scope run: 12 steps (dt=0.48) + 24 steps
(dt=0.05) x 3 nodes / 2 capacity-bearing edges x 6 cells (each node owns 2 cells),
declared-synthetic single-storm forcing (a 2.0 m pond collapsing over node 1 of a
1→2→3 chain, node 3 the outfall); scope claimed: per-step AND cumulative
surface-vs-books return-conservation identity across a ROUTING-FED multi-step coupled
chain on CPU, both caps witnessed. Gap ⇒ PARTIAL: synthetic topology only; adaptive
solver-dt schedules and CUDA device are out of scope by design (V12 CPU-only workflow).
BLOCKED companion: ``test_real_graph_companion_full_or_blocked`` covers the real
1,721-node graph when the WF-1 artefact seam loads; it xfails LOUDLY naming the closure
condition while the reader refuses the artefact. Declared-synthetic throughout — NOT
Bengaluru data, never presented as such (rule 1).

Fixture-design note (V6 record, first draft corrected before any green claim): the
return cap does NOT bind "near activation" by the raw hydraulic forms — clamp-first
floors every head at hf_floor, so the floored weir return (≈ Cw·L·hf^1.5·dt ≈
5.9e-5 m³ on a 2.285 m shaft) exceeds the volumetric cap only within ≈ 2.9e-5 m of
activation head. The first draft fed node3 at 0.02 m³/s (4.2e-3 m-head steps) and
jumped straight over that window; the witness correctly refused to fire, the FIXTURE
was rebuilt (4e-5 m³/s feeder ⇒ 8.4e-6 m-head steps), and neither the test's identity
nor the production code changed. The unit tier's barely-above-activation case
(test_exchange.py) is the same physics observed at the window's edge.

Hand-references below are typed literals sharing no code with
jaladhar.coupling.exchange (V2); fixture arrays go through the PRODUCTION assembly
path (router.build_drain_graph) exactly like the unit tier.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch

from jaladhar.coupling import exchange as exchange_mod
from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.exchange import NodeState
from jaladhar.coupling.router import (
    DrainGraph,
    RefuseLoadError,
    build_drain_graph,
    load_drain_graph,
)
from jaladhar.solver.state import StaticFields

REPO = Path(__file__).resolve().parents[2]

# --- hand-reference constants (literals HERE, V2: no imports from the module) -------
AREA = 100.0  # contract units block cell_area
FREEBOARD = 1.5  # ‡ declared assumption v1 activation head
HF_FLOOR = 0.001
CAPTURE_CAP_FRAC = 0.9
RETURN_CAP_FRAC = 0.9

DT_MAIN = 0.48  # the workflow's canonical dt
DT_ALT = 0.05  # second scenario: different regime mix (slower feeding)
STEPS_BY_DT = {DT_MAIN: 12, DT_ALT: 24}
PER_STEP_REL_TOL = 4e-6  # 4-ULP-class band; mutants land ≥2500x above it
CUM_REL_TOL = 4e-6
WITNESS_RTOL = 1e-5

EDGE_CAP_12 = 50.0  # m3/s, node1 -> node2: the feeder that drives surcharge
# node2 -> node3 trickle, sized to the RETURN-CAP WINDOW: clamp-first floors the
# return heads at hf_floor, so near activation the hydraulic return is floored at
# Cw*L*hf^1.5*dt ≈ 5.9e-5 m3 while the volumetric cap shrinks ∝ excess — the cap
# binds only within ≈ 2.9e-5 m of activation head (floored-hydraulic == cap point).
# A 4e-5 m3/s feeder advances node3 by 8.4e-6 m head/step, INSIDE that window, so
# once node3 crosses freeboard it stays pinned in the cap-binding regime.
EDGE_CAP_23 = 4e-5
WIDTHS = (5000.0, 2.285, 2.285)  # wide-inlet node1 (capture cap binds); shaft nodes
PA3 = WIDTHS[2] * 1.0  # shaft proxy plan area of node3
DEFICIT_3_M3 = 1.2e-4  # node3 starts this far BELOW activation: crosses mid-run
HEAD2_ABOVE = float(np.nextafter(np.float32(FREEBOARD), np.float32(2.0)))
HEAD3_BELOW = float(np.float32((FREEBOARD * PA3 - DEFICIT_3_M3) / PA3))
H0 = torch.tensor([[2.0, 0.05, 0.0], [2.0, 0.05, 0.0]], dtype=torch.float32)

# --- V5 mutation pins (see module docstring) -----------------------------------------
MUTATION_SITE = "vol_out_m3_cum=node_state.vol_out_m3_cum + edge_out_vol + r_node,"
MUTATIONS: dict[str, tuple[str, str, str]] = {
    "x1.01_node_side_book_subtraction": (
        MUTATION_SITE,
        "vol_out_m3_cum=node_state.vol_out_m3_cum + edge_out_vol + (1.01 * r_node),",
        "spec §12#5 named mutation: node-side subtraction scaled x1.01 in the books",
    ),
    "drop_return_from_books": (
        MUTATION_SITE,
        "vol_out_m3_cum=node_state.vol_out_m3_cum + edge_out_vol,",
        "return subtraction dropped from books while the raster add is kept",
    ),
}


# ---------------------------------------------------------------------------
# Fixture (declared-synthetic) + static shell — self-contained, production seam
# ---------------------------------------------------------------------------
def _static_zero(shape: tuple[int, int]) -> StaticFields:
    """Dry StaticFields shell, drain_cap EXACTLY zero (coupled-mode contract state)."""
    hh, ww = shape
    z = lambda *s: torch.zeros(*s, dtype=torch.float32)  # noqa: E731
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


def _chain_graph() -> DrainGraph:
    """1→2→3 DAG, both edges capacity-bearing; every node owns 2 cells."""
    nmap = torch.tensor([[1, 2, 3], [1, 2, 3]], dtype=torch.int32)
    return build_drain_graph(
        edge_from=torch.tensor([0, 1], dtype=torch.int64),
        edge_to=torch.tensor([1, 2], dtype=torch.int64),
        capacity_bearing=torch.ones(2, dtype=torch.bool),
        q_cap_nom_m3s=torch.tensor([EDGE_CAP_12, EDGE_CAP_23], dtype=torch.float64),
        width_mean_m=torch.tensor(WIDTHS, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, 3, dtype=torch.float64),
        contrib_area_m2=torch.zeros(3, dtype=torch.float64),
        outfall_node=torch.tensor([False, False, True]),
        node_cell_row=torch.arange(3, dtype=torch.int32),
        node_cell_col=torch.arange(3, dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.tensor([2, 2, 2], dtype=torch.int64),
    )


def _initial_state(mod: Any) -> Any:
    """NodeState built FROM THE MODULE UNDER TEST (red harness swaps modules).

    node2 a hair ABOVE activation; node3 DEFICIT_3_M3 below it so the trickle
    feeder walks it through the return-cap window mid-run (dt=MAIN scenario).
    """
    return mod.NodeState(
        h_node_m=torch.tensor([0.0, HEAD2_ABOVE, HEAD3_BELOW], dtype=torch.float32),
        vol_in_m3_cum=torch.zeros(3, dtype=torch.float64),
        vol_out_m3_cum=torch.zeros(3, dtype=torch.float64),
    )


def _run_chain(mod: Any, dt: float, steps: int) -> list[dict[str, Any]]:
    """Multi-step coupled chain; per-step record carries BOTH identity sides."""
    g = _chain_graph()
    static = _static_zero((2, 3))
    state = _initial_state(mod)
    h = H0.clone()
    qx, qy = torch.zeros(2, 2), torch.zeros(1, 3)
    records: list[dict[str, Any]] = []
    for t in range(steps):
        out_prev = state.vol_out_m3_cum.clone()
        head_prev = state.h_node_m.clone()
        cr = mod.couple_step(h, qx, qy, static, state, g, dt)
        transfer = cr.edge_flow_m3s.to(torch.float64) * dt
        edge_out = torch.index_add(torch.zeros(3, dtype=torch.float64), 0, g.edge_from, transfer)
        edge_in = torch.index_add(torch.zeros(3, dtype=torch.float64), 0, g.edge_to, transfer)
        d_out = cr.node_state_new.vol_out_m3_cum - out_prev
        per_node = d_out - edge_out  # books-side RETURN per node (routing cancelled)
        records.append(
            {
                "step": t,
                "dt": dt,
                "surf": float(cr.returned_depth_m.to(torch.float64).sum().item()) * AREA,
                "removed": float(per_node.sum().item()),
                "per_node": per_node,
                "cap_flag": cr.cap_binding_this_step,
                "surch": cr.surcharging_nodes,
                "h_before": h.clone(),
                "captured": cr.captured_depth_m.clone(),
                "head3_prev": float(head_prev[2].item()),
                "edge_in3": float(edge_in[2].item()),
            }
        )
        h = cr.h_new
        state = cr.node_state_new
    return records


# ---------------------------------------------------------------------------
# The identity check (shared verbatim by the green runs and the red harness)
# ---------------------------------------------------------------------------
def _worst_rel(records: list[dict[str, Any]]) -> float:
    return max(
        abs(r["surf"] - r["removed"]) / max(abs(r["surf"]), abs(r["removed"]), 1e-12)
        for r in records
    )


def _check_identity(records: list[dict[str, Any]]) -> dict[str, float]:
    """INVARIANT #5 proper: per-step AND cumulative surface==books agreement."""
    cum_s = cum_r = 0.0
    worst = 0.0
    for r in records:
        cum_s += r["surf"]
        cum_r += r["removed"]
        ref = max(abs(r["surf"]), abs(r["removed"]), 1e-12)
        rel = abs(r["surf"] - r["removed"]) / ref
        worst = max(worst, rel)
        assert rel <= PER_STEP_REL_TOL, (
            f"INVARIANT #5 surcharge-conserves violated at step {r['step']}: "
            f"surf={r['surf']!r} m3 vs books_removed={r['removed']!r} m3 "
            f"(rel={rel:.3e} > {PER_STEP_REL_TOL:.1e})"
        )
    cum_rel = abs(cum_s - cum_r) / max(cum_s, cum_r, 1e-12)
    assert cum_rel <= CUM_REL_TOL, (
        f"INVARIANT #5 cumulative form violated: cum_surf={cum_s!r} vs "
        f"cum_removed={cum_r!r} (rel={cum_rel:.3e} > {CUM_REL_TOL:.1e})"
    )
    return {"worst_per_step": worst, "cumulative": cum_rel, "cum_surf": cum_s}


def _check_mechanism_witnesses(records: list[dict[str, Any]]) -> None:
    """Anti-vacuity: the identity is tested on a LIVE mechanism, both caps binding.

    Witnesses are FORM matches against hand-typed formulas (no shared code), so a
    cap silently severed makes the witness fail, not just the flag disappear.
    """
    assert any(r["surch"] >= 1 for r in records), "no step ever surcharged"
    total_surf = sum(r["surf"] for r in records)
    assert total_surf > 1.0, f"identity tested on {total_surf!r} m3 — vacuously tight"

    surching = set()
    for r in records:
        for i in torch.nonzero(r["per_node"] > 0).flatten().tolist():
            surching.add(int(i))
    assert 1 in surching, "routing-fed node2 never returned — chain does not feed returns"
    assert 2 in surching, "trickle-fed node3 never crossed activation — cap window absent"
    assert 0 not in surching, "node1 (head ~0.07 m << freeboard) must never return"

    # CAPTURE-CAP witness: some step captures EXACTLY 0.9*(h_before - hf_floor).
    cap_seen = None
    for r in records:
        h00 = float(r["h_before"][0, 0].item())
        cap = CAPTURE_CAP_FRAC * max(0.0, h00 - HF_FLOOR)
        got = float(r["captured"][0, 0].item())
        if cap > 0 and abs(got - cap) <= 1e-6 * cap:
            cap_seen = (r["step"], got, cap)
            break
    assert cap_seen is not None, "capture cap never observed binding (invariant premise)"
    assert any(r["cap_flag"] == 1 for r in records), "cap_binding_this_step never fired"

    # RETURN-CAP witness: node3's books-side return == 0.9*(routed vol - freeboard vol).
    # Reconstruction uses only observables: vol_routed3 = head3_prev*PA3 + edge_in3
    # (capture3 == 0 by the no-reverse gate; node3 has no outgoing edges).
    ret_seen = None
    for r in records:
        vol_routed3 = r["head3_prev"] * PA3 + r["edge_in3"]
        excess = max(0.0, vol_routed3 - FREEBOARD * PA3)
        cap = RETURN_CAP_FRAC * excess
        ret3 = float(r["per_node"][2].item())
        if cap > 0 and abs(ret3 - cap) <= WITNESS_RTOL * cap:
            ret_seen = (r["step"], ret3, cap, excess)
            break
    assert ret_seen is not None, "return cap never observed binding (invariant premise)"
    # the witness DISCRIMINATES: the regime-appropriate hydraulic form (clamp-first
    # floored, same discipline as the module) differs strongly from the linear cap
    # in this window (floored weir ≈ 5.9e-5 m3 vs caps of order 1e-5 and below)
    step, ret3, cap, excess = ret_seen
    dt = records[step]["dt"]
    e_head = excess / PA3
    weir_floored_dt = 1.7 * WIDTHS[2] * max(e_head, HF_FLOOR) ** 1.5 * dt
    assert (
        abs(ret3 - weir_floored_dt) > 0.10 * cap
    ), "return-cap witness cannot distinguish cap-form from hydraulic-form — vacuous"


# ---------------------------------------------------------------------------
# INVARIANT #5 — the sprint-level green tests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("dt", [DT_MAIN, DT_ALT], ids=["dt0.48-x12", "dt0.05-x24"])
def test_inv05_surcharge_conserves_multistep_routing_chain(dt: float) -> None:
    """Per-step AND cumulative identity over a routing-fed multi-step coupled chain.

    dt=0.48 (12 steps) additionally proves BOTH caps bind (witness forms above);
    dt=0.05 (24 steps, slower feeding: node3's trickle cannot cross activation
    within the window) still must conserve on every live-return step.
    """
    records = _run_chain(exchange_mod, dt, steps=STEPS_BY_DT[dt])
    report = _check_identity(records)  # raises with the full failing identity line
    if dt == DT_MAIN:
        _check_mechanism_witnesses(records)
    print(
        f"\n[inv05-green] dt={dt}: steps={STEPS_BY_DT[dt]} nodes=3 cells=6 "
        f"cum_returned={report['cum_surf']!r} m3 "
        f"worst_per_step_rel={report['worst_per_step']:.3e} "
        f"cumulative_rel={report['cumulative']:.3e} (tol {PER_STEP_REL_TOL:.1e})"
    )
    print(
        "[inv05-scope] Scope run: 12 steps x 3 nodes x 2 CB edges x 6 cells x dt "
        f"{dt} s, declared-synthetic storm; claimed: per-step+cumulative return "
        "conservation across routing-fed chain (both-cap witness on dt=0.48). "
        "PARTIAL: synthetic topology only; adaptive-dt schedules and CUDA out of "
        "scope (V12). BLOCKED companion: real-graph coverage gated by the WF-1 "
        "artefact reader."
    )


# ---------------------------------------------------------------------------
# V5 RED DEMO — mutated /tmp copies of exchange.py; the assertion reddens, not a crash
# ---------------------------------------------------------------------------
def _materialise_mutated_exchange(kind: str, root: Path) -> Any:
    """Copy THE RUNNING exchange.py bytes to /tmp, patch the pinned site, import."""
    src = Path(exchange_mod.__file__).read_text()
    needle, replacement, _doc = MUTATIONS[kind]
    n_hits = src.count(needle)
    assert n_hits == 1, f"mutation-site pin stale ({n_hits} matches for {needle!r})"
    path = root / f"exchange_mut_{kind}.py"
    path.write_text(src.replace(needle, replacement))
    spec = importlib.util.spec_from_file_location(f"inv05_{kind}_{uuid.uuid4().hex[:8]}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass machinery resolves annotations via sys.modules
    spec.loader.exec_module(mod)  # router/state imports resolve to the REAL package
    return mod


def test_v5_red_demo_book_mutations_redden_the_identity() -> None:
    """Both book-side mutations must keep the run RUNNING but break the identity.

    Attempt log printed for the record (V5 honesty): kind, crashed?, realized
    worst rel, captured failing-output excerpt. Green control first: the same
    checker passes on pristine code and its realized misagreement is recorded
    so the red margin is quantified against IT, not against nothing.
    """
    green = _check_identity(_run_chain(exchange_mod, DT_MAIN, steps=STEPS_BY_DT[DT_MAIN]))
    assert green["worst_per_step"] <= PER_STEP_REL_TOL
    print(f"\n[inv05-red] GREEN control worst_rel={green['worst_per_step']:.3e}")

    root = Path(tempfile.mkdtemp(prefix="inv05_red_", dir="/tmp/opencode"))
    try:
        for kind, (_needle, _repl, doc) in MUTATIONS.items():
            mod = _materialise_mutated_exchange(kind, root)
            crash: str | None = None
            records = None
            try:
                records = _run_chain(mod, DT_MAIN, steps=STEPS_BY_DT[DT_MAIN])
            except Exception as exc:  # noqa: BLE001 — an attempt record IS the result
                crash = repr(exc)
            assert crash is None, (
                f"mutation {kind!r} CRASHED instead of letting the identity redden "
                f"(adjust the mutation): {crash}"
            )
            assert records is not None and len(records) == STEPS_BY_DT[DT_MAIN]
            with pytest.raises(AssertionError) as excinfo:
                _check_identity(records)
            failing_output = str(excinfo.value)
            worst = _worst_rel(records)
            assert (
                worst > 50 * PER_STEP_REL_TOL
            ), f"mutation {kind!r} did not move the observable enough: worst={worst:.3e}"
            assert worst > 100 * green["worst_per_step"], (
                f"mutation {kind!r} barely moved the observable vs green control: "
                f"{worst:.3e} vs {green['worst_per_step']:.3e}"
            )
            print(
                f"[inv05-red] ATTEMPT kind={kind} ({doc}): crashed=False "
                f"worst_rel={worst:.3e} VERDICT=RED"
            )
            print(f"[inv05-red]   failing output: {failing_output[:180]}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# BLOCKED companion (V7): real-graph sprint coverage, loud about what closes it
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_real_graph_companion_full_or_blocked() -> None:
    """The SAME per-step/cumulative identity on the REAL 1,721-node graph.

    Pure-return isolation scenario: streets dry, a seed of active nodes preset
    above freeboard — every returned m³ must reconcile books-vs-raster through
    the real heterogeneous cell counts and the real routing tables. While the
    frozen WF-1 reader refuses the only realized artefact, this xfails LOUDLY
    naming the closure condition (house pattern from test_exchange.py).
    """
    cfg = resolve_config(REPO / "configs" / "coupling.yaml", REPO)
    try:
        g = load_drain_graph(cfg, REPO)
    except RefuseLoadError as exc:
        pytest.xfail(
            "BLOCKED (V7): real-graph sprint coverage of surcharge-conserves closes when "
            f"WF-1 republishes a artefact the frozen reader accepts -> {exc}"
        )
    counts = g.node_cell_count
    eligible = torch.nonzero((counts >= 1) & g.active_node).flatten()
    assert eligible.numel() >= 25, "too few allocatable nodes to seed surcharge"
    heads = torch.zeros(g.num_nodes, dtype=torch.float32)
    heads[eligible[:25]] = 2.5  # above freeboard ⇒ immediate returns onto own cells
    state = NodeState(
        h_node_m=heads,
        vol_in_m3_cum=torch.zeros(g.num_nodes, dtype=torch.float64),
        vol_out_m3_cum=torch.zeros(g.num_nodes, dtype=torch.float64),
    )
    rows, cols = (int(v) for v in g.node_id_map.shape)
    h = torch.zeros((rows, cols), dtype=torch.float32)
    qx, qy = torch.zeros(rows, cols - 1), torch.zeros(rows - 1, cols)
    static = _static_zero((rows, cols))
    total = 0.0
    for t in range(3):
        out_prev = state.vol_out_m3_cum.clone()
        cr = exchange_mod.couple_step(h, qx, qy, static, state, g, DT_MAIN)
        transfer = cr.edge_flow_m3s.to(torch.float64) * DT_MAIN
        edge_out = torch.index_add(
            torch.zeros(g.num_nodes, dtype=torch.float64), 0, g.edge_from, transfer
        )
        d_out = cr.node_state_new.vol_out_m3_cum - out_prev
        surf = float(cr.returned_depth_m.to(torch.float64).sum().item()) * AREA
        removed = float((d_out - edge_out).sum().item())
        ref = max(abs(surf), abs(removed), 1e-12)
        assert (
            abs(surf - removed) <= PER_STEP_REL_TOL * ref
        ), f"real-graph identity violated at step {t}: surf={surf!r} removed={removed!r}"
        total += surf
        h, state = cr.h_new, cr.node_state_new
    assert total > 1.0, f"real-graph scenario returned only {total!r} m3 — vacuous"
    print(f"\n[inv05-real] grid={rows}x{cols} nodes={g.num_nodes} cum_returned={total!r} m3")
