"""V2: if guard (c)'s anti-double-count property were broken I would observe the DOUBLE-COUNT
SIGNATURE over one and the same coupled step range: the LEGACY drained term — host
``MassBudget.drain_out``, mirrored verbatim into ``ledger.legacy_drain_out_m3`` and reported as
its own manifest line (deviation D-E) — materially NON-ZERO (above the documented
``4 x eps32 x scale`` f32-noise bound by orders of magnitude) WHILE ``captured_to_drains_m3 >
0``: two sinks removing the same surface water, drainage counted twice against one capture.

==============================================================================
INVARIANT #10 ``anti-double-count`` — GUARD C (WF-2 spec §12 row #10, §2)
==============================================================================
Claim: with coupling ON, the legacy sink contributes ``legacy_drain_out_m3 == 0.0`` EXACTLY
(within the documented 4 x eps32 noise bound; the mirror itself is reported VERBATIM, never
clamped) AND ``captured_to_drains_m3 > 0`` under a wet storm. The zeroed field makes acc.py's
drain stage ``min(0 * dt, h) = 0`` exactly, so the legacy accumulator cannot move; the raster's
degenerate case (ALL cells non-zero [C]) means "capacity zero everywhere" is unreachable, so
the zeroing must be ASSERTED, never assumed. Three defence layers stand between a removed
zeroing and a realized double count:

    L1  driver pre-loop       ``solver_hook._assert_zeroed``        RuntimeError "guard (a)"
    L2  exchange entry assert ``exchange.couple_step``              ValueError  "guard (a)"
    L3  ledger runtime teeth  ``CouplingMassLedger.accumulate``     AntiDoubleCountError

V5 RED DEMOS — THE named mutation of spec §12#10, applied via monkeypatch ONLY (repo sources
untouched; the identity-passthrough pattern is reused verbatim from
tests/coupling/test_solver_hook.py::TestGuards): ``zero_drain_cap_out_of_place -> identity``,
i.e. the LIVE legacy raster survives into coupled mode. Three arms, each disarming every
detector ABOVE the layer under test so that layer is proven armed independently (the guard-a
demo establishes the L1->L2 precedent; arm 3 extends it to L3):
  arm 1  nothing disarmed => L1 refuses before any simulation work (manifest "failed");
  arm 2  L1 disarmed      => L2 refuses on the first coupled step;
  arm 3  L1+L2 disarmed   => the defect is FULLY realized — acc_step drains through the live
         raster while couple_step captures through the exchange (the exchange receives a zeroed
         VIEW solely so its own entry assert does not preempt the demonstration: this is exactly
         the world in which both asserts were deleted, the defect configuration itself, not
         another detector) => L3 fires AntiDoubleCountError on step 1, AND an independent
         hand-driven witness computes BOTH terms from returned tensors on the same fixture
         constants and finds the signature (capture > 0; legacy drain orders above the
         hand-recomputed 4 x eps32 bound) — my arithmetic, not the ledger's word (V2).

GREEN arm (pristine code, no monkeypatching): the micro-run realizes the claim — mirror == 0.0
EXACTLY and captured > 0 — computed IN-TEST from the final manifest's SEPARATE lines read back
from disk (``mass_host_budget_verbatim.drain_out_m3`` from mass.py's own accumulator,
``coupling_ledger.legacy_drain_out_m3``, and the top-level D-E line), never from an in-memory
self-assessment, with the acceptance bound recomputed by hand from the manifest's own scale
values and both mirror values printed verbatim.

Mutation record (V5, beside the tests): mutation kind
``identity_passthrough__zeroing_removed`` at site ``jaladhar.coupling.exchange.
zero_drain_cap_out_of_place`` (production body: ``replace(static, drain_cap_m_s=
torch.zeros_like(...))``); applied in all three arms; arms differ ONLY in which detectors stay
armed. Reverted for the green arm. Red margin measured, not narrated: realized legacy drain vs
bound ratio asserted >= 100x in the production arm.

V7 scope statement (template): Scope run: green arm <=200 steps x 4096 cells x 2,000 s
realized (step-capped before the 2,400 s requested); red arms <=3 steps x 256 cells x 30 s
(refusal lands on step 1); declared-synthetic 8-/2-node
chain graphs and the synthetic 110 mm / 7200 s DESIGN storm (never observed data, solver.yaml
declaration honoured); scope claimed: guard-c across ALL THREE defence layers plus the
manifest-line realization of the D-E separation, CPU toy domains only. Gap => PARTIAL:
full-domain coverage (real buffered terrain + real 1,721-node graph) is gated by the WF-1
artefact seam (D-G) — the frozen reader refuses the only realized artefact today; BLOCKED
companion ``test_real_graph_companion_full_or_blocked`` xfails LOUDLY naming the closure
condition, and runs a bounded (<=2-step) full-domain realization if the seam ever opens.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

import pytest
import torch

import jaladhar.coupling.solver_hook as hook
from jaladhar.coupling import exchange as exchange_mod
from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.exchange import build_node_state
from jaladhar.coupling.ledger import LEGACY_ZERO_BOUND_RELATIVE, AntiDoubleCountError
from jaladhar.coupling.router import RefuseLoadError, build_drain_graph, load_drain_graph
from jaladhar.coupling.solver_hook import simulate_coupled
from jaladhar.solver.acc import SolverParams, acc_step
from jaladhar.solver.run import uniform_storm
from jaladhar.solver.state import StaticFields, initial_state, load_solver_config

REPO = Path(__file__).resolve().parents[2]

# --- hand-reference constants (typed literals HERE; V2: not imported from production) -------
AREA_M2 = 100.0  # contract units block cell_area (= resolution_m^2, domain resolution 10 m)
CAP_M_S = 3.2e-6  # live legacy drain prior — declared-synthetic, same status as the config prior
H_SEED_M = 0.05  # wet seed: puts every cell above hf_floor so capture is live on step 1
BOUND_REL = 4.0 * float(torch.finfo(torch.float32).eps)  # the documented 4 x eps32 noise bound

# Meta-check target: production must enforce exactly the documented bound (V1: the doc's
# number and the enforced number are different claims until compared).
assert LEGACY_ZERO_BOUND_RELATIVE == BOUND_REL


def _hand_bound_m3(scale_m3: float) -> float:
    """The documented acceptance bound, recomputed by hand: 4 x eps32 x scale."""
    return BOUND_REL * max(abs(float(scale_m3)), 1e-12)


# ---------------------------------------------------------------------------
# Fixtures — declared-synthetic; reused verbatim from tests/coupling/test_solver_hook.py
# (house pattern). NOT Bengaluru data; rule 1 respected.
# ---------------------------------------------------------------------------


def _static(shape: tuple[int, int], *, drain_cap_m_s: float = CAP_M_S) -> StaticFields:
    """Flat closed domain with a LIVE legacy drain prior (zeroing must matter)."""
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
        edge_open=(0.0, 0.0, 0.0, 0.0),  # CLOSED micro-domain: boundary_out == 0 exactly
        shape=shape,
    )


def _chain_graph(rows: int, cols: int, n_nodes: int = 8, cap: float = 0.05):
    """n-node capacity-bearing chain across distinct cells; terminal node is an outfall."""
    base_r, base_c = max(1, rows // 8), max(1, cols // 8)
    cells = [(base_r + i, base_c) for i in range(n_nodes)]
    assert cells[-1][0] < rows and cells[-1][1] < cols
    nmap = torch.full((rows, cols), -1, dtype=torch.int32)
    for i, (r, c) in enumerate(cells):
        nmap[r, c] = i + 1
    edges = [(i, i + 1, cap) for i in range(1, n_nodes)]
    return build_drain_graph(
        edge_from=torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64),
        edge_to=torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64),
        capacity_bearing=torch.ones(len(edges), dtype=torch.bool),
        q_cap_nom_m3s=torch.tensor([float(e[2]) for e in edges], dtype=torch.float64),
        width_mean_m=torch.full((n_nodes,), 6.71, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, n_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n_nodes, dtype=torch.float64),
        outfall_node=(torch.arange(n_nodes) == n_nodes - 1),
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


def _solver_cfg() -> dict[str, Any]:
    cfg = load_solver_config(REPO / "configs" / "solver.yaml", REPO)
    cfg = copy.deepcopy(cfg)
    cfg["boundaries"]["mode"] = "closed"  # closed synthetic micro-domain
    return cfg


# ---------------------------------------------------------------------------
# GREEN arm — pristine code realizes the claim; observables from manifest lines on disk
# ---------------------------------------------------------------------------


def test_inv10_green_mirror_zero_exactly_and_capture_positive(tmp_path) -> None:
    """Pristine coupled micro-run (wet synthetic design storm): legacy drain mirror is 0.0
    EXACTLY on every reporting surface while capture is strictly positive — read from the
    REALIZED manifest bytes, not from any returned object (V1), with the 4 x eps32 bound
    recomputed by hand from the manifest's own scale values and the mirrors printed VERBATIM.

    Independence (V2): the three surfaces come from two different modules — mass.py's own
    f64 accumulator (``mass_host_budget_verbatim.drain_out_m3``) and the coupling ledger's
    mirror (top-level D-E line + ``coupling_ledger`` block) — so a defect that moves either
    side alone cannot pass unnoticed.

    V7 scope: <=200 steps x 4096 cells x 2,000 s realized (step-capped before the 2,400 s
    requested), flat closed toy domain,
    declared-synthetic 8-node chain + design storm; scope claimed: the green half of guard c
    plus the D-E separate-line realization. Full-domain gap => PARTIAL; see module docstring
    and the BLOCKED companion."""
    cfg = _coupling_cfg(tmp_path)
    scfg = _solver_cfg()

    simulate_coupled(
        cfg,
        scfg,
        REPO,
        graph=_chain_graph(64, 64),
        static=_static((64, 64)),
        rain=uniform_storm(110.0, 7200.0),
        duration_s=2400.0,
        max_steps=200,
        mass_check_every=50,
        snapshot_every_s=900.0,
        smoke=True,
    )

    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "completed"
    host = man["mass_host_budget_verbatim"]
    led = man["coupling_ledger"]

    # The wet-storm premise must be realized first: rain actually fell, steps actually ran.
    assert man["steps"] > 0
    assert host["rain_in_m3"] > 0.0

    # --- TERM 1: legacy drained term, three surfaces, each == 0.0 EXACTLY -----------------
    mirror_host = host["drain_out_m3"]  # mass.py accumulator (module 1)
    mirror_led = led["legacy_drain_out_m3"]  # coupling ledger mirror (module 2)
    mirror_top = man["legacy_drain_out_m3"]  # top-level D-E line (serialized from module 2)
    print(f"\n[inv10-green] legacy MassBudget.drain_out_m3 (verbatim): {mirror_host!r}")
    print(f"[inv10-green] ledger.legacy_drain_out_m3     (verbatim): {mirror_led!r}")
    print(f"[inv10-green] manifest top-level legacy_drain_out_m3:    {mirror_top!r}")
    assert mirror_host == 0.0, f"mass.py's own accumulator moved: {mirror_host!r}"
    assert mirror_led == 0.0, f"ledger mirror moved: {mirror_led!r}"
    assert mirror_top == 0.0, f"top-level D-E line moved: {mirror_top!r}"

    # Documented acceptance form: |mirror| within 4 x eps32 x scale, scale recomputed by hand
    # from the manifest's own values (not the ledger's bound computation).
    scale = max(abs(host["rain_in_m3"]), abs(host["v_current_m3"]), 1e-12)
    bound = _hand_bound_m3(scale)
    assert abs(mirror_top) <= bound, (
        f"|legacy_drain_out_m3|={abs(mirror_top)!r} exceeds the documented 4x eps32 bound "
        f"{bound:.3e} m3 (scale={scale!r})"
    )
    print(f"[inv10-green] hand-recomputed bound = 4*eps32*{scale!r} = {bound:.3e} m3")

    # --- TERM 2: capture strictly positive under the wet storm ----------------------------
    captured_top = man["captured_to_drains_m3"]
    captured_led = led["captured_to_drains_m3"]
    print(
        f"[inv10-green] captured_to_drains_m3 = {captured_top!r} m3 "
        f"({100.0 * captured_top / host['rain_in_m3']:.2f}% of rain_in)"
    )
    assert captured_top > 0.0, "coupled wet-storm run captured nothing — capture side vacuous"
    assert captured_led == captured_top, "manifest surfaces disagree on captured_to_drains_m3"

    # The two D-E quantities are DISTINCT lines (guard c's red test depends on the separation).
    assert "drain_out_net_m3" in man and "surcharge_returned_m3" in man
    print("[inv10-green] PASS: mirror==0.0 exactly on 3 surfaces; captured>0; D-E lines distinct")


# ---------------------------------------------------------------------------
# RED arm 1 — THE named mutation, nothing else touched: L1 refuses pre-loop
# ---------------------------------------------------------------------------


def test_inv10_red_arm1_identity_passthrough_refuses_at_driver_assert(
    tmp_path, monkeypatch
) -> None:
    """MUTATION ``identity_passthrough__zeroing_removed``: hook.zero_drain_cap_out_of_place
    returns the static unchanged. NO detector disarmed: the driver's pre-loop
    ``(drain_cap_m_s == 0).all()`` check (L1) refuses before any simulation work.

    V5 record: with the zeroing gone and a LIVE prior (CAP_M_S > 0 on every cell), this is the
    first of three layers that must fire. FINDING (V1, measured 2026-08-26, first red run): L1
    sits BEFORE the driver's terminal-status try block, so the refusal propagates uncaught and
    the manifest keeps its last IN-PLACE state, status "running" — unlike every other refusal
    path (D-G seam, falsifier gate, K1 halt, compute budget, in-loop failures), which land a
    terminal status. This test asserts that realized state and names the gap; it does not bless
    it. Terminal-status coverage of the pre-loop guards is an integration-unit item, out of
    this file's ownership. V7 scope: input assembly only; no loop executes."""
    cfg = _coupling_cfg(tmp_path)
    scfg = _solver_cfg()
    monkeypatch.setattr(hook, "zero_drain_cap_out_of_place", lambda s: s)  # THE MUTATION

    with pytest.raises(RuntimeError, match="guard \\(a\\)") as excinfo:
        simulate_coupled(
            cfg,
            scfg,
            REPO,
            graph=_chain_graph(16, 16, n_nodes=2),
            static=_static((16, 16)),
            rain=uniform_storm(110.0, 7200.0),
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10_000_000,
        )
    print(f"\n[inv10-red-arm1] refused verbatim: {excinfo.value}")
    assert "zeroed copy still carries non-zero" in str(excinfo.value)

    # Realized manifest state (see docstring FINDING): last in-place update survives; no
    # terminal-status handler covers the pre-loop guard refusal.
    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "running", (
        f"expected the documented pre-loop reality (status stays at its last in-place value), "
        f"got {man['status']!r} — if this now lands a terminal status, UPDATE this test and "
        "delete the docstring FINDING: the integration unit closed the gap"
    )
    # DECLARATION-parity check (relabelled per the vacuity-audit adjudication): this
    # manifest flag is guard b's DECLARATION surface — the driver recording that the
    # coupled_mode_transformation was APPLIED. It is NOT itself the realized-zeroing
    # proof; that lives in L1/L2, which fired above. Pinning the declaration keeps the
    # manifest surface honest without this assert claiming more than it declares.
    assert man["coupled_mode_transformation"]["legacy_drain_disabled"] is True, (
        "guard-b DECLARATION surface moved: manifest no longer records "
        "legacy_drain_disabled=true (the REALIZED zeroing is proven separately by L1 "
        "having raised above)"
    )
    print(
        "[inv10-red-arm1] manifest status realized as 'running' (pre-loop refusal precedes "
        "the driver's terminal-status try block) — NAMED GAP, integration-unit item"
    )


# ---------------------------------------------------------------------------
# RED arm 2 — L1 disarmed (house pattern): L2, the exchange entry assert, refuses on step 1
# ---------------------------------------------------------------------------


def test_inv10_red_arm2_identity_passthrough_refuses_at_exchange_entry(
    tmp_path, monkeypatch
) -> None:
    """Same mutation, L1 neutralized (monkeypatch of ``hook._assert_zeroed``, exactly the
    reuse pattern from test_solver_hook.py::TestGuards): the FIRST coupled step must now be
    refused by couple_step's OWN entry assert (L2) — defence in depth demonstrated layer by
    layer, not by the driver's word about its downstream guard.

    V7 scope: one rejected coupled step on a 16x16 toy domain; scope claimed: the L1->L2 seam."""
    cfg = _coupling_cfg(tmp_path)
    scfg = _solver_cfg()
    monkeypatch.setattr(hook, "zero_drain_cap_out_of_place", lambda s: s)  # THE MUTATION
    monkeypatch.setattr(hook, "_assert_zeroed", lambda s: None)  # L1 disarmed

    with pytest.raises(ValueError, match="guard \\(a\\)") as excinfo:
        simulate_coupled(
            cfg,
            scfg,
            REPO,
            graph=_chain_graph(16, 16, n_nodes=2),
            static=_static((16, 16)),
            rain=uniform_storm(110.0, 7200.0),
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10_000_000,
        )
    print(f"\n[inv10-red-arm2] refused verbatim: {excinfo.value}")
    assert "static.drain_cap_m_s is not zeroed" in str(excinfo.value)

    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "failed"
    assert "guard (a)" in man["error"]


# ---------------------------------------------------------------------------
# RED arm 3 — L1+L2 disarmed: the double-count configuration FULLY realized;
#             the invariant's own teeth (L3 AntiDoubleCountError) fire on step 1
# ---------------------------------------------------------------------------


def test_inv10_red_arm3_double_count_realized_then_ledger_refuses(tmp_path, monkeypatch) -> None:
    """The deep red demo. THE mutation (identity passthrough) keeps the legacy sink LIVE in
    acc_step; the exchange still receives a zeroed VIEW (its own L2 assert would otherwise
    preempt the demonstration) — together this is precisely the world where both guard-(a)
    asserts were deleted: TWO sinks operate on the same water, and only the ledger's runtime
    teeth (L3) stand between the run and a silently double-counted budget.

    Part 1 — INDEPENDENT WITNESS (V2): one hand-driven step on the same fixture constants
    (live cap into acc_step, zeroed view into couple_step), BOTH terms computed by MY
    reductions from returned tensors — legacy removal via the hand-typed acc.py:313-314 form
    ``sum(min(cap*dt, h)) * area``, capture via my own reduction of ``returned``-style field
    ``captured_depth_m``. Asserts the SIGNATURE: capture > 0 AND legacy drain orders above the
    hand-recomputed bound — before trusting any production accumulator.

    Part 2 — PRODUCTION REFUSAL (V5): simulate_coupled under the same configuration raises
    AntiDoubleCountError naming guard (c) on step 1; the verbatim message is printed, its
    realized drain value parsed and asserted >= 100x the bound it reports (materially
    non-zero, not dust), and the manifest lands at status "failed" with error_type
    AntiDoubleCountError (rule 6: provenance even in failure).

    Mutation record: ``identity_passthrough__zeroing_removed`` +
    detector-disarmament of L1/L2 (documented above); reverted via monkeypatch teardown for
    every other test.

    V7 scope: witness 1 step x 256 cells at dt=1.0 s; production refusal on coupled step 1
    (controller-selected dt, closed 16x16 domain, h seeded 0.05 m so capture is live on the
    refusing step — both terms non-zero AT the moment of refusal); scope claimed: the L3
    detector against the fully-realized defect, not the upstream layers (arms 1-2)."""
    cfg = _coupling_cfg(tmp_path)
    scfg = _solver_cfg()
    graph = _chain_graph(16, 16, n_nodes=2)
    static_live = _static((16, 16))

    # --- Part 1: independent witness of the double-count signature ------------------------
    dx = float(scfg["_domain"]["resolution_m"])
    p = SolverParams.from_config(scfg, dx)
    dt_w = 1.0
    h0, qx0, qy0 = initial_state(static_live, device="cpu")
    h0 = torch.full_like(h0, H_SEED_M)
    h_acc, qx_a, qy_a, _diag = acc_step(h0, qx0, qy0, static_live, dt_w, p, rain_rate_m_s=0.0)

    # legacy term: hand-typed acc.py:313-314 semantics over the LIVE field (my arithmetic)
    legacy_witness_m3 = (
        float(torch.minimum(static_live.drain_cap_m_s * dt_w, h_acc).sum(dtype=torch.float64))
        * AREA_M2
    )
    # capture term: the exchange sees a ZEROED view (its entry assert intact here);
    # I reduce captured_depth_m myself instead of reading cr.capture_m3.
    cr = exchange_mod.couple_step(
        h_acc,
        qx_a,
        qy_a,
        exchange_mod.zero_drain_cap_out_of_place(static_live),
        build_node_state(graph),
        graph,
        dt_w,
    )
    captured_witness_m3 = float(cr.captured_depth_m.sum(dtype=torch.float64)) * AREA_M2

    scale_witness = max(abs(float(h_acc.sum(dtype=torch.float64))) * AREA_M2, 1e-12)
    bound_witness = _hand_bound_m3(scale_witness)
    ratio_witness = legacy_witness_m3 / bound_witness
    print(
        f"\n[inv10-red-arm3/witness] legacy_removed={legacy_witness_m3!r} m3  "
        f"captured={captured_witness_m3!r} m3  (same step, dt={dt_w} s)"
    )
    print(
        f"[inv10-red-arm3/witness] hand bound={bound_witness:.3e} m3  "
        f"ratio={ratio_witness:.0f}x"
    )
    assert captured_witness_m3 > 0.0, "witness: capture did not fire on the wet seed"
    assert legacy_witness_m3 > 50.0 * bound_witness, (
        f"witness: legacy drain {legacy_witness_m3!r} m3 is not materially above the "
        f"documented bound {bound_witness:.3e} m3 — the 'orders above' premise fails"
    )

    # --- Part 2: production path refuses under the fully-realized defect ------------------
    real_couple_step = hook.couple_step

    def exchange_view_shim(h, qx, qy, static_arg, node_state, g, dt):
        """Disarmament of the L2 DETECTOR ONLY: feed couple_step a zeroed view so the
        demonstration reaches L3. The DEFECT stays fully live on the solver side — acc_step
        below drains through the untouched LIVE raster."""
        return real_couple_step(
            h,
            qx,
            qy,
            exchange_mod.zero_drain_cap_out_of_place(static_arg),
            node_state,
            g,
            dt,
        )

    monkeypatch.setattr(hook, "zero_drain_cap_out_of_place", lambda s: s)  # THE MUTATION
    monkeypatch.setattr(hook, "_assert_zeroed", lambda s: None)  # L1 disarmed
    monkeypatch.setattr(hook, "couple_step", exchange_view_shim)  # L2 disarmed

    with pytest.raises(AntiDoubleCountError, match="guard \\(c\\)") as excinfo:
        simulate_coupled(
            cfg,
            scfg,
            REPO,
            graph=graph,
            static=_static((16, 16)),
            h0=torch.full((16, 16), H_SEED_M, dtype=torch.float32),
            rain=uniform_storm(110.0, 7200.0),
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10_000_000,
        )
    msg = str(excinfo.value)
    print(f"[inv10-red-arm3/production] REFUSED verbatim: {msg}")

    m = re.search(
        r"legacy MassBudget\.drain_out is (\S+) m3, beyond the f32-noise bound (\S+) m3", msg
    )
    assert m is not None, f"refusal message lacks the parseable mirror/bound pair: {msg!r}"
    realized_drain_m3 = float(m.group(1))
    reported_bound_m3 = float(m.group(2))
    assert realized_drain_m3 > 100.0 * reported_bound_m3, (
        f"realized legacy drain {realized_drain_m3!r} m3 is not orders above the reported "
        f"bound {reported_bound_m3:.3e} m3 — refusal fired on dust, not on a double count"
    )

    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "failed"
    assert man["error_type"] == "AntiDoubleCountError"
    assert man["error"] == msg or msg in man["error"], "manifest lost the verbatim refusal"
    print(
        f"[inv10-red-arm3/production] realized legacy drain={realized_drain_m3!r} m3 "
        f"vs bound {reported_bound_m3:.3e} m3 ({realized_drain_m3 / reported_bound_m3:.0f}x); "
        "manifest status=failed, error_type=AntiDoubleCountError — GUARD C RED CONFIRMED"
    )


# ---------------------------------------------------------------------------
# BLOCKED companion (V7): full-domain coverage names what closes it
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_real_graph_companion_full_or_blocked() -> None:
    """The SAME green-arm claim on the REAL full domain (real buffered terrain + real
    1,721-node graph): legacy mirror == 0.0 exactly while captured_to_drains_m3 > 0, from
    the completed manifest. While the frozen WF-1 reader refuses the only realized artefact
    (D-G seam, owner adjudication pending) this xfails LOUDLY naming the closure condition
    (house pattern from test_inv05/test_solver_hook). If the seam ever opens, a BOUNDED
    (<=2-step) full-domain realization runs and must realize the claim end-to-end."""
    cfg = resolve_config(REPO / "configs" / "coupling.yaml", REPO)
    try:
        g = load_drain_graph(cfg, REPO)
    except RefuseLoadError as exc:
        pytest.xfail(
            "BLOCKED (V7): full-domain anti-double-count coverage closes when WF-1 republishes "
            f"an artefact the frozen reader accepts (D-G seam) -> {exc}"
        )
    rows, cols = (int(v) for v in g.node_id_map.shape)
    res = simulate_coupled(
        cfg,
        _solver_cfg(),
        REPO,
        graph=g,
        h0=torch.full((rows, cols), H_SEED_M, dtype=torch.float32),
        rain=uniform_storm(110.0, 7200.0),
        duration_s=60.0,
        max_steps=2,
        mass_check_every=10_000_000,
        smoke=True,
    )
    man = json.loads(cfg.outputs.manifest.read_text())
    assert res.steps <= 2
    assert man["status"] == "completed"
    assert man["legacy_drain_out_m3"] == 0.0
    assert man["captured_to_drains_m3"] > 0.0
    print(
        f"\n[inv10-real] grid={rows}x{cols} nodes={g.num_nodes} "
        f"legacy={man['legacy_drain_out_m3']!r} captured={man['captured_to_drains_m3']!r}"
    )
