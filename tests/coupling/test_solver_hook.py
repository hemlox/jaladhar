"""Solver-hook unit tests — WF-2 integration (spec §9; deliverables 2+3).

TOY tier (declared-synthetic; CPU-only V12) + one REAL-ARTEFACT seam test:

- DELIVERABLE 3 micro-run: 64x64 grid, 8-node chain graph, uniform design storm,
  max_steps=200 — manifest written at START (status running) and UPDATED IN PLACE to
  completed; BOTH residuals printed; legacy drain_out == 0.0 EXACTLY while
  captured_to_drains_m3 > 0 (guard c realized); total-water residual <= 1e-4 hard bar;
  §9.3 manifest field inventory spot-checked incl. WIDTH_DOWNGRADE tag and K1-K4 block.
- GUARD (d): coupled_parameter_set is a view OF StaticFields.parameters() minus the dead knob.
- GUARD (a) RED DEMO: monkeypatching zero_drain_cap_out_of_place to IDENTITY passes the live
  static through => couple_step's entry assert fires ("guard (a)") on the FIRST coupled step;
  pristine control completes; the manifest records status "failed" for the mutant (rule 6).
- K1 HALT: a return-driven dt collapse (huge-shaft node returning onto one cell of a dry
  closed domain) trips K1 against the MEASURED uncoupled twin schedule => KillThresholdHalt,
  manifest status halted_K1, twin recorded.
- FORMER D-G SEAM, CLOSED (V6 flip, adjudication round 2): contract v1.2.0 accepted the
  realized artefact, so simulate_coupled's production path now PASSES the frozen reader and
  the §14 epsilon compute-budget cap halts it at "refused_compute_budget" with none of D-G's
  refusal trappings (was: propagates RefuseLoadError to refused_with_D_G — which failed loud
  'DID NOT RAISE' the day the artefact loaded, per its own design).
- REFUSAL LABELS: a falsifier-gate refusal (absent path OR unreadable/lacking-counts header)
  is a DIFFERENT RefuseLoadError source and must land at "refused_falsifier_missing", never
  wearing D-G's status (red->green: it previously did). Router-side LOAD-GATE refusals
  (window falsifier gate, malformed window...) are a THIRD source landing at
  "refused_load_gate" with refusal_source naming the gate; only the frozen read_artefact
  refusal wears "refused_with_D_G" (item C: the blanket except mislabeled all of them, and
  the CLI echoed '(D-G seam)' for every source).
- DRIVER GT ATTRIBUTION + G2 VERDICT (item E): the terminal manifest carries a TOP-LEVEL
  gt_attribution block computed via diagnostics.attribute_ground_truth and g2.verdict via
  diagnostics.g2_verdict — dead-end-dominated reproduction lands SUSPICIOUS=TRUE in the
  manifest AND in score-g2's headline; an unavailable GT bundle degrades honestly to
  NOT_ASSESSED with the verbatim error.
- GUARD-C STORM-THEN-DRY (item A): a conserving storm-then-dry run completes past 13k
  coupled steps — the old frozen f32-noise bound false-halted this run shape at step 12k.
- G2 VACUOUS EXIT (§11.1): a completed run in which no node ever surcharged reports
  g2_anti_vacuity_fail=True in result + manifest, and the typer CLI exits non-zero; a
  surcharging control run exits 0.
- RULE 7: missing solver-config keys aggregate into ONE KeyError naming all of them.

V2 observables: residual values are recomputed by the LEDGER from tensors the driver never
summarizes; the manifest's realized bytes are read back from disk, not from return objects;
guard-a's proof is couple_step's OWN assert (different module), not the driver's word.
V7 scope statements live in each docstring.
"""

from __future__ import annotations

import copy
import csv
import json
import re
import subprocess
import sys
from dataclasses import replace as dc_replace
from pathlib import Path

import pytest
import torch
from typer.testing import CliRunner

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.router import RefuseLoadError, build_drain_graph
from jaladhar.coupling.solver_hook import (
    COMPUTE_PROBE_STEP,
    ComputeBudgetExceeded,
    CouplingMassBreach,
    KillThresholdHalt,
    coupled_parameter_set,
    simulate_coupled,
)
from jaladhar.solver.run import uniform_storm
from jaladhar.solver.state import StaticFields, load_solver_config

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Synthetic fixtures (declared-synthetic; NOT Bengaluru data)
# ---------------------------------------------------------------------------


def _static(shape: tuple[int, int], *, drain_cap_m_s: float = 3.2e-6) -> StaticFields:
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


def _chain_graph(
    rows: int,
    cols: int,
    n_nodes: int = 8,
    cap: float = 0.05,
    widths: list[float] | None = None,
    *,
    outfall: bool = True,
    manifest: dict | None = None,
):
    """n-node capacity-bearing chain across distinct cells; terminal node is an outfall.
    Cells sit in-board so even a 4x4 fixture keeps every node inside the grid.

    ``outfall=False`` marks NO node as an outfall => every component class is 'dead_end'
    (the fill-and-surcharge-by-construction D-A world) for the gt-attribution tests.
    ``manifest`` rides through to build_drain_graph (e.g. config_snapshot.grid.transform
    so diagnostics can reconstruct node positions)."""
    base_r, base_c = max(1, rows // 8), max(1, cols // 8)
    cells = [(base_r + i, base_c) for i in range(n_nodes)]
    assert cells[-1][0] < rows and cells[-1][1] < cols
    nmap = torch.full((rows, cols), -1, dtype=torch.int32)
    for i, (r, c) in enumerate(cells):
        nmap[r, c] = i + 1
    edges = [(i, i + 1, cap) for i in range(1, n_nodes)]
    edge_from = torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64)
    edge_to = torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64)
    cb = torch.ones(len(edges), dtype=torch.bool)
    q_cap = torch.tensor([float(e[2]) for e in edges], dtype=torch.float64)
    outfall_mask = torch.zeros(n_nodes, dtype=torch.bool)
    if outfall:
        outfall_mask[n_nodes - 1] = True
    return build_drain_graph(
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=cb,
        q_cap_nom_m3s=q_cap,
        width_mean_m=torch.tensor(widths or [6.71] * n_nodes, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, n_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n_nodes, dtype=torch.float64),
        outfall_node=outfall_mask,
        node_cell_row=torch.tensor([c[0] for c in cells], dtype=torch.int32),
        node_cell_col=torch.tensor([c[1] for c in cells], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.ones(n_nodes, dtype=torch.int64),
        manifest=manifest,
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
    cfg["boundaries"]["mode"] = "closed"  # closed synthetic micro-domain
    return cfg


# ---------------------------------------------------------------------------
# Deliverable 3: end-to-end synthetic micro-run + manifest lifecycle (rule 6)
# ---------------------------------------------------------------------------


def _terminal_echo_manifest() -> dict:
    """M1 echo riding through build_drain_graph; shape mirrors
    ``jaladhar.drainage.terminal.as_manifest_block()`` so the F2 persistence
    assertion exercises the exact block shape production loads carry."""
    return {
        "terminal_seed_resolution": {
            "definition_version": "v2-lake-boundary-fixture",
            "counts_by_rule": {
                "declared_gpkg_outfall": 1,
                "lake_polygon": 0,
                "domain_boundary": 0,
            },
            "seeds_by_rule": {
                "declared_gpkg_outfall": [8],
                "lake_polygon": [],
                "domain_boundary": [],
            },
            "seed_union_count": 1,
            "note": (
                "fixture echo riding through build_drain_graph(manifest=...); "
                "shape mirrors drainage.terminal.as_manifest_block()"
            ),
        }
    }


class TestMicroRun:
    def test_micro_run_end_to_end_manifest_lifecycle(self, tmp_path, capsys):
        """64x64 grid, 8-node chain, uniform 110 mm/7200 s design storm, max_steps=200,
        CLOSED boundaries. Asserts (deliverable 3): manifest STARTS at status running with
        the rule-6 start fields and is UPDATED IN PLACE to completed (same git_sha AND
        same start_time survive into the terminal file); both residuals printed and the
        total-water form <= 1e-4; legacy drain_out == 0.0 EXACTLY while captured > 0;
        surcharge FIRES via terminal-node accumulation (D-A consequence); §9.3 fields land.

        Round-3 extensions: D3 — the driver seam must land an ASSESSED edge-mode
        gt_attribution block (real gpkg edges + real GT bundle); zero matches is honest
        NOT_ASSESSED WITH its recorded rule labels, never a missing/degraded block.
        F2 — the M1 terminal_seed_resolution echo must PERSIST into the terminal
        manifest, not live only on the in-memory graph object.

        V7 scope: <=200 steps x 4096 cells x ~2000 simulated seconds, flat closed toy
        domain, declared-synthetic graph; scope claimed: driver loop + ledger closure +
        manifest lifecycle + the two terminal-block seams above. Full-domain behaviour
        and real forcing remain open until the D-G seam closes."""
        cfg = _coupling_cfg(tmp_path)
        scfg = _solver_cfg()
        static_before = _static((64, 64))
        drain_before = static_before.drain_cap_m_s.clone()

        res = simulate_coupled(
            cfg,
            scfg,
            REPO,
            graph=_chain_graph(64, 64, manifest=_terminal_echo_manifest()),
            static=static_before,
            rain=uniform_storm(110.0, 7200.0),
            duration_s=2400.0,
            max_steps=200,
            mass_check_every=50,
            snapshot_every_s=900.0,
            smoke=True,
        )

        # --- realized run ------------------------------------------------------
        assert 100 <= res.steps <= 200
        assert res.sim_time_s > 500.0
        led = res.ledger
        tw = led.total_water_relative_residual()
        contract = led.contract_residual()
        print(f"\n[micro-run] steps={res.steps} sim_time={res.sim_time_s:.1f}s " f"cells={64 * 64}")
        print(
            f"[micro-run] captured_to_drains_m3={led.captured_to_drains_m3!r} "
            f"surcharge_returned_m3={led.surcharge_returned_m3!r} "
            f"drain_out_net_m3={led.drain_out_net_m3!r} "
            f"legacy_drain_out_m3={led.legacy_drain_out_m3!r}"
        )
        print(f"[micro-run] relative_residual(contract, REPORTED)={contract:.6e}")
        print(f"[micro-run] total_water_relative_residual(JUDGED)={tw:.6e} " f"(hard bar 1e-4)")
        print(
            f"[micro-run] v_nodes_t_m3={led.v_nodes_m3()!r} "
            f"surcharging_steps={led.total_surcharging_steps} "
            f"events={len(led.events)}"
        )

        # guard c realized: legacy sink EXACTLY zero, capture strictly positive
        assert res.budget.as_dict()["drain_out_m3"] == 0.0
        assert led.legacy_drain_out_m3 == 0.0
        assert led.captured_to_drains_m3 > 0.0
        # judged closure on the synthetic closed micro-run
        assert tw <= 1e-4, f"total-water residual {tw:.3e} exceeded the hard bar"
        # net may be negative (physical); whatever its sign it equals captured - returned
        assert led.drain_out_net_m3 == led.captured_to_drains_m3 - led.surcharge_returned_m3
        # terminal node accumulates upstream captures with no export law (D-A) -> surge
        assert led.total_surcharging_steps > 0, (
            "terminal chain node was expected to cross freeboard; a silent window here "
            "would make the G2 anti-vacuity story untestable at toy scale"
        )
        assert len(led.events) >= 1
        # original StaticFields untouched bitwise (guard a, realized)
        assert torch.equal(static_before.drain_cap_m_s, drain_before)

        # --- manifest lifecycle: start -> in-place completion -------------------
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "completed"
        assert man["stage"] == "wf2_coupled_run"
        start = res.start_manifest
        assert start["status"] == "running"
        assert start["git_sha"] == man["git_sha"]
        assert start["start_time"] == man["start_time"], "manifest was not updated IN PLACE"
        assert isinstance(man["git_dirty"], bool)
        assert set(start["config_snapshot"]) == {"coupling_resolved", "solver_yaml"}
        assert start["coupled_mode_transformation"]["legacy_drain_disabled"] is True
        assert "compute_budget_estimate" in start
        # §9.3 producer-writes fields (realized file, not declarations)
        for key in (
            "coupling_enabled",
            "coupling_version",
            "captured_to_drains_m3",
            "surcharge_returned_m3",
            "drain_out_net_m3",
            "legacy_drain_out_m3",
            "relative_residual",
            "total_water_relative_residual",
            "v_nodes_t_m3",
            "total_surcharging_steps",
            "total_returned_m3",
            "surcharge_events_csv",
            "cap_binding_steps",
            "graph_is_dag",
            "topo_order_sha256",
            "legacy_drain_disabled",
            "node_geometry_assumption",
            "falsifier_comparison",
            "component_class_split",
            "g2",
            "kill_thresholds",
            "indirect_cfl_monitor",
            "deviations_notice",
            "mass_host_budget_verbatim",
        ):
            assert key in man, f"§9.3 field {key!r} missing from the completed manifest"
        assert abs(man["total_water_relative_residual"] - tw) <= 0.0
        assert man["residual_tolerance_block"]["judged_bar_relative"] == 1e-4
        nga = man["node_geometry_assumption"]
        assert nga["assumed_freeboard_m"] == 1.5 and nga["assumed_uncalibrated"] is True
        assert nga["width_grade"] == "BELOW_CPHEEO_CONFIDENCE"
        kt = man["kill_thresholds"]
        assert kt["K1"]["fired"] is False and "uncoupled_twin" in kt["K1"]
        assert kt["K2"]["recorded"] is True and kt["K3"]["recorded"] is True
        assert kt["K4"]["status"] == "computed_by_diagnostics_unit"
        dv = man["deviations_notice"]
        for k in ("D_A", "D_C", "D_E", "D_F"):
            assert k in dv and dv[k]
        # products landed
        assert Path(man["surcharge_events_csv"]).exists()
        header = Path(man["surcharge_events_csv"]).read_text().splitlines()[0]
        assert header == "node_id,total_returned_m3,first_step,last_step,max_head_m"

        # D3 (round-3): an ASSESSED edge-mode gt_attribution must come through the
        # DRIVER seam on this run — real gpkg edge polylines + the real GT bundle,
        # dispatched by the driver at terminal time. Zero matches is honest
        # NOT_ASSESSED WITH its recorded rule labels; a missing or degraded block
        # (no attribution_mode) reddens.
        gta = man["gt_attribution"]
        assert gta.get("attribution_mode") == "edge", (
            f"expected an assessed edge-mode block through the driver seam; got keys "
            f"{sorted(gta)} (a degrade block here means the seam broke, not a quiet GT)"
        )
        assert gta["radius_m_declared_assumption"] == 40.0  # edge mode uses ITS OWN leaf
        if gta["n_gt_matched"] > 0:
            assert gta["suspicious_state"] in ("TRUE", "FALSE")
        else:
            assert gta["suspicious_state"] == "NOT_ASSESSED"
            assert gta.get(
                "measure_labels"
            ), "zero-match edge block must record WHY it is unassessed (rule labels)"

        # F2 (round-3): the M1 terminal_seed_resolution echo PERSISTS into the final
        # manifest bytes instead of living only on the in-memory graph.manifest.
        tsr = man.get("terminal_seed_resolution")
        assert isinstance(tsr, dict) and tsr, "terminal_seed_resolution not persisted"
        assert tsr["definition_version"] == "v2-lake-boundary-fixture"
        assert tsr["counts_by_rule"]["declared_gpkg_outfall"] == 1

        out = capsys.readouterr().out
        assert "total_water_relative_residual(JUDGED)" in out
        assert "[micro-run]" in out


# ---------------------------------------------------------------------------
# Guards d and a (spec §9.1)
# ---------------------------------------------------------------------------


class TestGuards:
    def test_guard_d_parameter_view_removes_dead_knob(self):
        """coupled_parameter_set is a FILTERED VIEW OF StaticFields.parameters(): identical
        tensor objects, exactly the dead knob removed, source method untouched.

        V7 scope: one StaticFields shell; structural check."""
        s = _static((3, 3))
        full = s.parameters()
        view = coupled_parameter_set(s)
        assert "drain_cap_m_s" not in view
        assert set(view) == set(full) - {"drain_cap_m_s"}
        for k, v in view.items():
            assert v is full[k], "parameter view must share storage, not copy"
        assert "drain_cap_m_s" in full  # the SOURCE method still exposes it untouched

    def test_guard_a_v5_red_identity_passthrough_fires_entry_assert(self, tmp_path):
        """V5 RED DEMO (guard-c plumbing, task-named mutation recorded here): monkeypatch
        zero_drain_cap_out_of_place to IDENTITY so the LIVE raster passes through. The
        driver's own pre-loop zeroing check is bypassed by construction (the patch replaces
        the zeroing itself), so the FIRST line of defence that MUST fire is couple_step's
        entry assert — ValueError naming guard (a). Pristine control completes first, and
        the mutant's manifest lands at status failed (rule 6: provenance even in failure).

        V7 scope: 3-step pristine control + first-step mutant on a 16x16 toy domain with
        one inert node; scope claimed: the zeroing->entry-assert chain only."""
        cfg = dc_replace(_coupling_cfg(tmp_path), enabled=True)
        scfg = _solver_cfg()
        live = _static((16, 16))
        graph = _chain_graph(16, 16, n_nodes=2, cap=0.05)

        control = simulate_coupled(
            cfg,
            scfg,
            REPO,
            graph=graph,
            static=_static((16, 16)),
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10_000_000,
        )
        assert control.steps == 3

        import jaladhar.coupling.solver_hook as hook

        original = hook.zero_drain_cap_out_of_place
        # Neutralize the DRIVER's own pre-loop layer only; the exchange entry assert must
        # then be what fires — proving each guard layer is independently armed.
        monkey = pytest.MonkeyPatch()
        monkey.setattr(hook, "_assert_zeroed", lambda s: None)
        hook.zero_drain_cap_out_of_place = lambda s: s  # THE MUTATION: identity passthrough
        try:
            with pytest.raises(ValueError, match="guard \\(a\\)"):
                simulate_coupled(
                    cfg,
                    scfg,
                    REPO,
                    graph=graph,
                    static=live,
                    duration_s=30.0,
                    max_steps=3,
                    mass_check_every=10_000_000,
                )
        finally:
            hook.zero_drain_cap_out_of_place = original
            monkey.undo()

        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "failed"
        assert "guard (a)" in man["error"]

    def test_rule7_missing_solver_keys_aggregate(self, tmp_path):
        """Rule 7 inside the driver: dropping TWO solver keys raises ONE KeyError naming
        BOTH, before any simulation work.

        V7 scope: pre-flight only (no loop executed)."""
        scfg = _solver_cfg()
        del scfg["timestep"]["cfl_alpha"]
        del scfg["mass"]["check_every_steps"]
        with pytest.raises(KeyError) as excinfo:
            simulate_coupled(
                _coupling_cfg(tmp_path),
                scfg,
                REPO,
                graph=_chain_graph(8, 8, n_nodes=2),
                static=_static((8, 8)),
                duration_s=10.0,
                max_steps=2,
            )
        msg = str(excinfo.value)
        assert "timestep.cfl_alpha" in msg and "mass.check_every_steps" in msg


# ---------------------------------------------------------------------------
# K1 kill threshold (§10.6) — measured uncoupled-equivalent schedule
# ---------------------------------------------------------------------------


class TestK1Halt:
    def test_k1_halt_fires_when_coupled_dt_collapses(self, tmp_path):
        """Return-driven collapse: a huge-shaft node (plan area 5000 m2) preset above
        freeboard returns onto ONE cell of a dry closed domain each step; the surface
        h_max jumps metres-per-step so the coupled dt collapses while the dry uncoupled
        twin strides at max_dt. K1 compares the coupled step count against the TWIN'S
        realized schedule prefix and HALTS.

        Asserts: KillThresholdHalt raised within a small step count; manifest left at
        status halted_K1 with K1.fired=true, the twin summary, and no tuning.

        V7 scope: 4x4 cells, 2 nodes (one active), no rain, closed boundaries; scope
        claimed: the K1 detector + halt path, not exchange physics (covered elsewhere)."""
        cfg = _coupling_cfg(tmp_path)
        scfg = _solver_cfg()
        # width 5000 m => plan area 5000 m2: the excess-above-freeboard volume is ~22,500
        # m3 and the per-step return onto ONE cell keeps h_max (and thus dt) pinned near
        # the floor for the whole window — a SUSTAINED collapse, which is what K1 exists
        # to catch (a 3-step blip recovers and must NOT fire the halt).
        graph = _chain_graph(4, 4, n_nodes=2, widths=[5000.0, 2.285])
        live = _static((4, 4))

        from jaladhar.coupling.exchange import NodeState

        surged_state = NodeState(
            h_node_m=torch.tensor([6.0, 0.0], dtype=torch.float32),
            vol_in_m3_cum=torch.zeros(2, dtype=torch.float64),
            vol_out_m3_cum=torch.zeros(2, dtype=torch.float64),
        )

        with pytest.raises(KillThresholdHalt, match="K1"):
            simulate_coupled(
                cfg,
                scfg,
                REPO,
                graph=graph,
                static=live,
                node_state=surged_state,
                duration_s=60.0,
                max_steps=500,
                mass_check_every=10_000_000,
            )

        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "halted_K1"
        k1 = man["kill_thresholds"]["K1"]
        assert k1["fired"] is True
        assert k1["coupled_steps"] >= 4
        assert k1["uncoupled_equivalent_steps_at_final_tau"] >= 1
        assert k1["uncoupled_twin"]["steps"] >= 1
        assert "HALT, report, do not tune" in k1["halt_reason"]


# ---------------------------------------------------------------------------
# D-G seam reality (task mandate: propagate cleanly, never fabricate a load)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_real_artefact_loads_production_path_past_former_d_g_seam(tmp_path):
    """V6 FLIP (WF-2 adjudication round 2): the world changed under this test —
    contract v1.2.0 (owner D-G amendment 2026-08-26,
    configs/contracts/drain_graph.json amendment_history) accepted the realized
    artefact, so the former form of this test (expect RefuseLoadError at
    refused_with_D_G) failed loud 'DID NOT RAISE' exactly as its tripwire design
    required. GREEN now: with NO graph injected, simulate_coupled takes the
    PRODUCTION path router.load_drain_graph -> frozen read_artefact on the ONLY
    realized artefact, which must PASS input assembly.

    Cheap proof WITHOUT a heavy simulation (§14): clamp
    smoke.cpu_budget_wall_clock_min to an epsilon cap — the resolver refuses
    literal 0.0 as non-positive, so the minimal legal budget is 1e-9 min — and
    bound the run to COMPUTE_PROBE_STEP steps, where the measured probe raises
    ComputeBudgetExceeded AFTER input assembly + graph + domain load + the
    <=5-step uncoupled twin. Reaching THAT status proves the run got past the
    former refusal site into the compute phase wearing none of D-G's trappings.

    Asserts: (a) no RefuseLoadError raised — ComputeBudgetExceeded naming the
    §14 cap instead; (b) manifest status refused_compute_budget (the
    K1-family halt handler), NOT refused_with_D_G; (c) refusal_verbatim /
    d_g_notice absent from the REALIZED manifest bytes; plus realized
    cells/grid_shape proving full-domain production assembly (no window, no toy).

    V7 scope: config resolve + production input assembly over the 3521x3615
    grid (~1 s/step CPU: twin capped at max_steps + 5 coupled steps) + the §14
    probe branch; scope claimed: the former D-G refusal site passes and the
    budget gate lands its OWN distinct terminal status."""
    base = resolve_config(REPO / "configs" / "coupling.yaml", REPO)
    outs = dc_replace(
        base.outputs,
        run_dir=tmp_path / "run",
        manifest=tmp_path / "run" / "manifest.json",
        surcharge_events_csv=tmp_path / "run" / "products" / "surcharge_events.csv",
        event_continuity_csv=tmp_path / "run" / "products" / "event_continuity.csv",
        depth_series_dir=tmp_path / "run" / "depth",
    )
    cfg = dc_replace(
        base,
        outputs=outs,
        smoke=dc_replace(base.smoke, cpu_budget_wall_clock_min=1e-9),
    )

    with pytest.raises(ComputeBudgetExceeded) as excinfo:
        simulate_coupled(
            cfg,
            _solver_cfg(),
            REPO,
            rain=uniform_storm(110.0, 7200.0),
            duration_s=60.0,
            max_steps=COMPUTE_PROBE_STEP,
            mass_check_every=10_000_000,
        )
    # (a) the halt is the §14 budget gate, not any load refusal
    assert not isinstance(excinfo.value, RefuseLoadError)
    assert "smoke.cpu_budget_wall_clock_min" in str(excinfo.value)
    assert excinfo.value.manifest_status == "refused_compute_budget"

    man = json.loads(cfg.outputs.manifest.read_text())
    # (b) the K1-family halt status, never D-G's
    assert man["status"] == "refused_compute_budget"
    assert man["status"] != "refused_with_D_G"
    # (c) the D-G refusal trappings are gone from the realized bytes
    assert "refusal_verbatim" not in man, man.get("refusal_verbatim")
    assert "d_g_notice" not in man
    # realized input assembly past the former refusal site: FULL domain
    rr, cc = man["grid_shape"]
    assert min(rr, cc) > 1000 and man["cells"] == rr * cc


# ---------------------------------------------------------------------------
# Judged-bar enforcement through the DRIVER (CouplingMassBreach surfaces, rule 6 holds)
# ---------------------------------------------------------------------------


def test_mass_breach_marks_manifest_failed(tmp_path, monkeypatch):
    """If the JUDGED residual breaches mid-run the driver must stop and mark the manifest
    failed — a broken run cannot look valid. Mutation: force the breach by monkeypatching
    the ledger's judged bar to an impossible value (any honest run then 'breaches').

    V7 scope: 16x16 toy domain, <=5 steps; the detector under test is the cadence branch."""
    from jaladhar.coupling import ledger as ledger_mod

    cfg = _coupling_cfg(tmp_path)
    scfg = _solver_cfg()

    class ImpossibleBarLedger(ledger_mod.CouplingMassLedger):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.judged_bar_relative = -1.0  # every check "breaches"

    monkeypatch.setattr("jaladhar.coupling.solver_hook.CouplingMassLedger", ImpossibleBarLedger)
    with pytest.raises(CouplingMassBreach, match="total-water"):
        simulate_coupled(
            cfg,
            scfg,
            REPO,
            graph=_chain_graph(16, 16, n_nodes=2),
            static=_static((16, 16)),
            rain=uniform_storm(110.0, 7200.0),
            duration_s=120.0,
            max_steps=5,
            mass_check_every=1,
        )
    man = json.loads(cfg.outputs.manifest.read_text())
    assert man["status"] == "failed"


# ---------------------------------------------------------------------------
# Refusal-source labels (V5 red->green, verifier demo): the falsifier gate and
# the D-G graph-reader seam are DIFFERENT RefuseLoadError sources and must not
# share a terminal status.
# ---------------------------------------------------------------------------


def _hermetic_cfg(tmp_path: Path):
    """Coupling cfg with tmp outputs AND a valid falsifier set written under tmp_path,
    so these tests never depend on the repo's realized preregistration artefact."""
    fset = tmp_path / "prediction_set.json"
    fset.write_text(
        json.dumps(
            {
                "n_predicted_edges": 1,
                "predicted_node_ids": [1],
                "source_gpkg_sha256": "0" * 64,
            }
        )
    )
    base = resolve_config(REPO / "configs" / "coupling.yaml", REPO)
    outs = dc_replace(
        base.outputs,
        run_dir=tmp_path / "run",
        manifest=tmp_path / "run" / "manifest.json",
        surcharge_events_csv=tmp_path / "run" / "products" / "surcharge_events.csv",
        event_continuity_csv=tmp_path / "run" / "products" / "event_continuity.csv",
        depth_series_dir=tmp_path / "run" / "depth",
    )
    return dc_replace(
        base, outputs=outs, diagnostics=dc_replace(base.diagnostics, falsifier_set=fset)
    )


class TestRefusalSourceLabels:
    def test_missing_falsifier_set_refuses_as_falsifier_missing_not_D_G(self, tmp_path):
        """RED (recorded): with the graph loading FINE via injection and only
        diagnostics.falsifier_set absent, the shared except wrote manifest status
        'refused_with_D_G' — a missing prediction set misattributed to the drain-graph
        artefact seam. GREEN: status 'refused_falsifier_missing', verbatim message kept.

        V7 scope: input assembly only; no simulation work executes."""
        cfg = _hermetic_cfg(tmp_path)
        absent = tmp_path / "absent" / "prediction_set.json"
        cfg = dc_replace(cfg, diagnostics=dc_replace(cfg.diagnostics, falsifier_set=absent))

        with pytest.raises(RefuseLoadError, match="falsifier prediction set missing"):
            simulate_coupled(
                cfg,
                _solver_cfg(),
                REPO,
                graph=_chain_graph(16, 16, n_nodes=2),
                static=_static((16, 16)),
                duration_s=30.0,
                max_steps=3,
                mass_check_every=10_000_000,
            )

        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "refused_falsifier_missing"
        assert man["refusal_verbatim"].startswith(
            "[wf2_coupled_run] falsifier prediction set missing"
        )
        assert str(absent) in man["refusal_verbatim"]
        assert "d_g_notice" not in man, "falsifier refusal must not carry D-G's notice"

    def test_unreadable_falsifier_set_also_refuses_as_falsifier_missing(self, tmp_path):
        """Schema failure branch of the same gate (invalid JSON header) gets the SAME
        distinct terminal status — absence and unreadability are one refusal class here,
        both distinct from D-G.

        V7 scope: input assembly only; no simulation work executes."""
        cfg = _hermetic_cfg(tmp_path)
        corrupt = tmp_path / "corrupt_prediction_set.json"
        corrupt.write_text('{"n_predicted_edges": ')  # truncated JSON
        cfg = dc_replace(cfg, diagnostics=dc_replace(cfg.diagnostics, falsifier_set=corrupt))

        with pytest.raises(RefuseLoadError, match="unreadable/lacking counts"):
            simulate_coupled(
                cfg,
                _solver_cfg(),
                REPO,
                graph=_chain_graph(16, 16, n_nodes=2),
                static=_static((16, 16)),
                duration_s=30.0,
                max_steps=3,
                mass_check_every=10_000_000,
            )

        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "refused_falsifier_missing"
        assert "unreadable/lacking counts" in man["refusal_verbatim"]

    @staticmethod
    def _run_with_gate_refusal(monkeypatch, tmp_path, refusal_msg: str):
        """simulate_coupled with hook.load_drain_graph raising a ROUTER-SIDE gate
        refusal (the [window]/[consumer]/... family — read_artefact did not refuse).
        Returns (cfg, raised exception)."""
        import jaladhar.coupling.solver_hook as hook

        cfg = _hermetic_cfg(tmp_path)

        def gate_refusal(c, r, window=None):
            raise RefuseLoadError(refusal_msg)

        monkeypatch.setattr(hook, "load_drain_graph", gate_refusal)
        with pytest.raises(RefuseLoadError) as excinfo:
            simulate_coupled(
                cfg,
                _solver_cfg(),
                REPO,
                duration_s=30.0,
                max_steps=3,
                mass_check_every=10_000_000,
            )
        return cfg, excinfo.value

    def test_window_gate_refusal_lands_refused_load_gate_not_D_G(self, tmp_path, monkeypatch):
        """RED (recorded, item C): the blanket 'except RefuseLoadError' wrote status
        refused_with_D_G + the hardcoded d_g_notice for EVERY loader refusal — a
        router-side '[window] only N/M predicted target nodes active' falsifier-gate
        refusal wore D-G's artefact-seam label although read_artefact never refused.
        GREEN: status 'refused_load_gate', refusal_source names the window gate, and NO
        d_g_notice rides along.

        V7 scope: input assembly only; no simulation work executes."""
        cfg, _ = self._run_with_gate_refusal(
            monkeypatch,
            tmp_path,
            "[window] only 3/116 pre-registered predicted target nodes active in this "
            "window; required >= 10 (smoke.min_predicted_nodes_in_window)",
        )
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "refused_load_gate"
        assert man["status"] != "refused_with_D_G"
        assert "only 3/116" in man["refusal_verbatim"]
        assert "[window]" in man["refusal_source"]
        assert "d_g_notice" not in man, "a window-gate refusal must not carry D-G's notice"

    def test_malformed_window_refusal_also_lands_refused_load_gate(self, tmp_path, monkeypatch):
        """Same discrimination for the malformed-window refusal ('[window] unsupported
        window form') — another router-side gate, never the frozen reader's seam.

        V7 scope: input assembly only."""
        cfg, _ = self._run_with_gate_refusal(
            monkeypatch,
            tmp_path,
            "[window] unsupported window form: 'bogus'; expected None, (rows_slice, "
            "cols_slice), or (row0, row1, col0, col1) ints",
        )
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "refused_load_gate"
        assert "unsupported window form" in man["refusal_verbatim"]
        assert "router.load_drain_graph[window]" == man["refusal_source"]

    def test_cli_echo_names_D_G_seam_only_for_frozen_reader(self, tmp_path, monkeypatch):
        """RED (recorded, item C): the CLI catch-all echoed 'REFUSED (D-G seam)' for
        every RefuseLoadError INCLUDING falsifier-missing, whose manifest correctly said
        refused_falsifier_missing — surfaces contradicted. GREEN: '(D-G seam)' appears
        ONLY when .source is the frozen reader; all other sources echo plain REFUSED.

        V7 scope: CLI refusal path only; no simulation runs."""
        import jaladhar.coupling.solver_hook as hook

        # arm 1: a router-side gate refusal -> plain REFUSED, no seam label
        gate = RefuseLoadError("[window] only 3/116 predicted target nodes active")
        monkeypatch.setattr(hook, "resolve_config", lambda p, r: _hermetic_cfg(tmp_path))
        monkeypatch.setattr(
            hook, "load_drain_graph", lambda c, r, window=None: (_ for _ in ()).throw(gate)
        )
        result = CliRunner().invoke(hook.app, ["--config", "unused.yaml"])
        assert result.exit_code == 1
        assert "REFUSED:" in result.output
        assert "(D-G seam)" not in result.output

        # arm 2: exactly what _tag_frozen_reader_refusals produces -> seam label kept
        frozen = RefuseLoadError("[consumer_assertion_3] zero_length_dropped_count=42 != 0")
        frozen.source = "frozen_reader"
        monkeypatch.setattr(
            hook, "load_drain_graph", lambda c, r, window=None: (_ for _ in ()).throw(frozen)
        )
        result2 = CliRunner().invoke(hook.app, ["--config", "unused.yaml"])
        assert result2.exit_code == 1
        assert "REFUSED (D-G seam):" in result2.output

    def test_tagger_marks_read_artefact_refusals_and_only_those(self, monkeypatch):
        """Unit-level V5 demo of the discrimination mechanism itself: with read_artefact
        patched to refuse, the wrapper tags .source='frozen_reader'; a refusal raised by
        any OTHER router code path inside the same window stays UNTAGGED (=> lands at
        refused_load_gate via the driver branch).

        V7 scope: two synthetic refusals through the context manager; no I/O."""
        import jaladhar.coupling.router as router_mod
        import jaladhar.coupling.solver_hook as hook

        def refusing_reader(*a, **k):
            raise RefuseLoadError("[consumer_assertion_3] zero_length_dropped_count=42 != 0")

        monkeypatch.setattr(router_mod, "read_artefact", refusing_reader)
        with hook._tag_frozen_reader_refusals():
            with pytest.raises(RefuseLoadError) as ei:
                router_mod.read_artefact("gpkg", "adj", "man")
            assert getattr(ei.value, "source", None) == "frozen_reader"
            other = RefuseLoadError("[consumer] partition mismatch")
            assert getattr(other, "source", None) is None
        # the wrapper restored the patched reader after the window (no leak)
        assert router_mod.read_artefact is refusing_reader


# ---------------------------------------------------------------------------
# G2 anti-vacuity verdict + CLI non-zero exit (§11.1; V5 red->green, verifier
# demo: dry injected-graph run completed with surcharging_steps==0, returned
# 0.0, condition True — and exited 0).
# ---------------------------------------------------------------------------


class TestG2VacuousExit:
    @staticmethod
    def _install_cli_fixtures(monkeypatch, tmp_path, *, duration_s, max_steps, n_nodes, shape):
        """Point the CLI's config/graph/domain lookups at hermetic toy fixtures so the
        command runs end-to-end without touching repo run paths."""
        import jaladhar.coupling.solver_hook as hook

        cfg = _hermetic_cfg(tmp_path)
        cfg = dc_replace(
            cfg,
            smoke=dc_replace(cfg.smoke, duration_s=duration_s, max_steps=max_steps),
        )
        monkeypatch.setattr(hook, "resolve_config", lambda p, r: cfg)
        monkeypatch.setattr(
            hook,
            "load_drain_graph",
            lambda c, r, window=None: _chain_graph(*shape, n_nodes=n_nodes),
        )
        monkeypatch.setattr(hook, "load_domain", lambda *a, **k: _static(shape))
        return hook, cfg

    def test_library_reports_g2_anti_vacuity_fail_in_result_and_manifest(self, tmp_path):
        """The LIBRARY half of the fix: a dry micro-run completes normally (no raise) but
        reports anti_vacuity_fail=True on the result object, in the returned final manifest,
        AND in the realized bytes on disk — one computation, three surfaces that cannot
        disagree.

        V7 scope: <=3 steps x 256 cells, dry closed toy domain; scope claimed: verdict
        propagation only (exchange physics covered elsewhere)."""
        cfg = _hermetic_cfg(tmp_path)
        res = simulate_coupled(
            cfg,
            _solver_cfg(),
            REPO,
            graph=_chain_graph(16, 16, n_nodes=2),
            static=_static((16, 16)),
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10_000_000,
        )
        assert res.steps == 3
        assert res.ledger.total_surcharging_steps == 0
        assert res.ledger.surcharge_returned_m3 < cfg.diagnostics.g2_min_returned_m3
        assert res.g2_anti_vacuity_fail is True
        assert res.final_manifest["g2"]["anti_vacuity_fail"] is True
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["g2"]["anti_vacuity_fail"] is True

    def test_cli_exits_nonzero_on_g2_vacuous_window(self, tmp_path, monkeypatch):
        """RED (recorded): this exact invocation returned exit_code 0 while printing the
        G2 FAIL headline — docstring/spec §11.1 promise unimplemented. GREEN: exit_code 1
        with the headline still first (never buried) and the manifest recording the verdict.

        V7 scope: <=3 steps x 256 cells dry toy domain through the full typer command."""
        hook, cfg = self._install_cli_fixtures(
            monkeypatch, tmp_path, duration_s=30.0, max_steps=3, n_nodes=2, shape=(16, 16)
        )
        result = CliRunner().invoke(hook.app, ["--config", "unused.yaml", "--smoke"])
        assert result.exit_code == 1, result.output
        assert result.output.splitlines()[0].startswith("G2 FAIL")
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "completed"
        # realized vacuity inputs (the exit is grounded in recorded state, not echoes)
        assert man["total_surcharging_steps"] == 0
        assert man["total_returned_m3"] < cfg.diagnostics.g2_min_returned_m3
        assert man["g2"]["anti_vacuity_fail"] is True

    def test_cli_exits_zero_when_surcharge_fires(self, tmp_path, monkeypatch):
        """Control for the new exit: the TestMicroRun scenario (known to surcharge) through
        the same CLI path exits 0 and records anti_vacuity_fail=False — the non-zero exit
        tracks the verdict, it is not blanket-on.

        V7 scope: <=200 steps x 4096 cells synthetic storm, same envelope as TestMicroRun."""
        hook, cfg = self._install_cli_fixtures(
            monkeypatch, tmp_path, duration_s=2400.0, max_steps=200, n_nodes=8, shape=(64, 64)
        )
        result = CliRunner().invoke(hook.app, ["--config", "unused.yaml", "--smoke"])
        assert result.exit_code == 0, result.output
        assert not any(line.startswith("G2 FAIL") for line in result.output.splitlines())
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "completed"
        assert man["total_surcharging_steps"] > 0
        assert man["g2"]["anti_vacuity_fail"] is False


# ---------------------------------------------------------------------------
# Driver emits gt_attribution + computed g2 verdict (BugHunt round-1 item E-i):
# owner directive wf2-coupling.js:145-152 — G2 MUST split outfall vs dead-end and
# dead-end-dominated GT reproduction is flagged SUSPICIOUS IN THE HEADLINE.
# ---------------------------------------------------------------------------


class TestDriverEmitsGtAttribution:
    @staticmethod
    def _gt_bundle_at(tmp_path: Path, x_m: float, y_m: float) -> Path:
        """Synthetic GT bundle (declared-synthetic point, x_m/y_m columns so no
        reprojection is needed) placed exactly on a node's reconstructed position."""
        csv_path = tmp_path / "points.csv"
        csv_path.write_text(f"id,lat,lon,x_m,y_m\nSYN1,12.9,77.6,{x_m!r},{y_m!r}\n")
        man_path = tmp_path / "gt_manifest.json"
        man_path.write_text(json.dumps({"csv_path": str(csv_path), "points_count": 1}))
        return man_path

    def test_terminal_manifest_carries_top_level_gt_attribution_and_computed_g2(self, tmp_path):
        """RED (recorded, item E-i): grep solver_hook.py for gt_attribution returned ZERO
        hits — the production driver never computed it and wrote g2.verdict =
        'not_computed_here'; even a smoke run whose diagnostics block said TRUE re-scored
        NOT_ASSESSED. GREEN: an all-dead-end chain surcharging under a GT point lands a
        TOP-LEVEL gt_attribution block with suspicious_state=TRUE and a COMPUTED g2
        verdict; score-g2 on the realized bytes prints SUSPICIOUS=TRUE in its headline.

        V7 scope: <=200 steps x 4096 cells synthetic storm, declared-synthetic 8-node
        dead-end chain + one synthetic GT point; scope claimed: driver emission +
        scorer consumption seam."""
        from typer.testing import CliRunner as _CliRunner

        from jaladhar.coupling.diagnostics import app as diag_app

        cfg = _hermetic_cfg(tmp_path)
        # terminal node id 8 sits at cell (row 8+7, col 8); with res=10, x0=500000,
        # ytop=4500000 its reconstructed centre is (500085, 4499845).
        gt_manifest = self._gt_bundle_at(tmp_path, 500085.0, 4499845.0)
        # V6 record (2026-08-26, WF-2 M2 ruling D-GT): configs/coupling.yaml now
        # defaults diagnostics.attribution_mode to "edge". THIS fixture pins
        # "node": its synthetic GT point sits on a RECONSTRUCTED TOY-node position,
        # a geometry no real gpkg polyline can carry, so the seam it verifies
        # (driver emits gt_attribution + computed g2 at terminal time) is exercised
        # through the RETAINED node mode (SUPERSEDED-BY-RULING, regression/A-B
        # evidence role) rather than weakened or deleted.
        cfg = dc_replace(
            cfg,
            diagnostics=dc_replace(
                cfg.diagnostics, ground_truth_manifest=gt_manifest, attribution_mode="node"
            ),
        )
        grid_manifest = {
            "config_snapshot": {
                "grid": {
                    "height": 64,
                    "width": 64,
                    "transform": [10.0, 0.0, 500000.0, 10.0, 0.0, 4500000.0],
                    "cell_area_m2": 100.0,
                }
            }
        }
        graph = _chain_graph(64, 64, n_nodes=8, outfall=False, manifest=grid_manifest)

        res = simulate_coupled(
            cfg,
            _solver_cfg(),
            REPO,
            graph=graph,
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
        assert res.ledger.total_surcharging_steps > 0, "premise: the window must surcharge"
        attr = man["gt_attribution"]
        print(
            f"\n[driver-gt] suspicious_state={attr['suspicious_state']!r} "
            f"share={attr.get('dead_end_share_of_gt_matched_returned_volume')!r} "
            f"matched={attr.get('n_gt_matched')!r}"
        )
        assert attr["suspicious_state"] == "TRUE", f"expected dead-end-dominated TRUE, got {attr!r}"
        assert attr["suspicious"] is True
        assert attr["n_gt_matched"] >= 1
        # every chain component here is dead_end by construction -> share 1.0
        assert attr["dead_end_share_of_gt_matched_returned_volume"] == pytest.approx(1.0)
        # g2 verdict computed via the diagnostics library, never 'not_computed_here'
        g2_block = man["g2"]
        assert g2_block["verdict"] == "pass"
        assert g2_block["verdict"] != "not_computed_here"
        assert "SUSPICIOUS=TRUE" in g2_block["reason"]

        # the SCORER consumes the realized bytes: headline carries the flag, exit per
        # anti-vacuity rules only (flagging, not failing)
        scored = _CliRunner().invoke(diag_app, ["score-g2", str(cfg.outputs.manifest)])
        assert scored.exit_code == 0, scored.output
        assert "SUSPICIOUS=TRUE" in scored.output.splitlines()[0]
        assert not any(
            line.startswith("WARNING: dead-end contamination UNASSESSED")
            for line in scored.output.splitlines()
        ), "an assessed block must not trigger the UNASSESSED warning"

    def test_unavailable_gt_bundle_recorded_not_assessed_honestly(self, tmp_path):
        """A MISSING ground-truth manifest must degrade to an honest top-level
        NOT_ASSESSED block carrying the verbatim error — never fabricate
        attribution, never crash the completed run, never drop the field.

        D7 rewording (round 3; ASSERTION UNCHANGED): the historical note here
        cited the superseded NODE-mode refusal reason ("surfacing as the
        geometry-source refusal, since the 2-node toy graph carries no grid
        transform"). Under the DEFAULT edge mode (ruling D-GT) the refusal
        surfaces earlier and plainer: attribute_ground_truth_edges refuses inside
        _load_gt_points with 'ground-truth manifest not found' BEFORE any node or
        edge geometry is consulted — the toy graph's missing grid transform is
        irrelevant on this path."""
        cfg = _hermetic_cfg(tmp_path)
        absent_gt = tmp_path / "absent" / "gt_manifest.json"
        cfg = dc_replace(
            cfg, diagnostics=dc_replace(cfg.diagnostics, ground_truth_manifest=absent_gt)
        )
        res = simulate_coupled(
            cfg,
            _solver_cfg(),
            REPO,
            graph=_chain_graph(16, 16, n_nodes=2),
            static=_static((16, 16)),
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10_000_000,
        )
        assert res.steps == 3
        man = json.loads(cfg.outputs.manifest.read_text())
        attr = man["gt_attribution"]
        assert attr["suspicious_state"] == "NOT_ASSESSED"
        assert attr["error_type"] == "DiagnosticsRefusal"
        assert attr["error"], "fallback block lost the verbatim refusal"
        assert man["g2"]["verdict"] in ("pass", "fail")


# ---------------------------------------------------------------------------
# Guard-c storm-then-dry completability (BugHunt round-1 item A, constraint b,
# driver-loop realization; ledger-level algebra lives in test_ledger.py)
# ---------------------------------------------------------------------------


class TestStormThenDryGuardC:
    @pytest.mark.slow
    def test_conserving_storm_then_dry_run_completes_past_13k_steps(self, tmp_path):
        """GREEN (constraint b): rain stops at t=300 s but the closed conserving domain
        keeps stepping to t=700 s at dt<=0.05 => >13k coupled steps WITHOUT guard (c)
        firing — the old frozen-bound algebra false-halted production at step 12,471 on
        exactly this run shape (measured -1.8e-6 m3/step dust vs freezing scale). The
        judged cadence checks all pass en route; manifest lands 'completed'.

        V7 scope: ~14k steps x 256 cells, flat closed toy domain, declared-synthetic
        2-node graph; wall clock cited below. The quantitative dust-vs-signal margins
        are asserted in tests/coupling/test_ledger.py::TestGuardCNegativeDustAllowance."""
        import time as _time

        cfg = _coupling_cfg(tmp_path)
        scfg = _solver_cfg()
        scfg["timestep"]["max_dt_s"] = 0.05
        scfg["timestep"]["min_dt_s"] = 0.005
        rain = uniform_storm(110.0, 300.0)  # storm-then-dry: zero rate from t=300 s

        t0 = _time.perf_counter()
        res = simulate_coupled(
            cfg,
            scfg,
            REPO,
            graph=_chain_graph(16, 16, n_nodes=2),
            static=_static((16, 16)),
            rain=rain,
            duration_s=700.0,
            max_steps=20_000,
            mass_check_every=1000,
        )
        wall_s = _time.perf_counter() - t0
        print(
            f"\n[storm-then-dry] steps={res.steps:,} sim={res.sim_time_s:,.0f}s "
            f"wall={wall_s:.1f}s legacy_mirror={res.ledger.legacy_drain_out_m3!r} m3 "
            f"dust_observed={res.ledger.dust_observed_m3!r} "
            f"allowance={res.ledger.dust_allowance_m3!r}"
        )
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "completed"
        assert res.steps >= 13_000, (
            f"only {res.steps} steps realized — the accelerated envelope failed to reach "
            "the 13k-step scale constraint (b)"
        )
        # mirror stays verbatim and inside the asymmetric envelope (no trip happened)
        assert abs(res.ledger.legacy_drain_out_m3) <= (
            res.ledger._legacy_zero_bound_m3() + res.ledger.dust_allowance_m3
        )


# ---------------------------------------------------------------------------
# GT-attribution honest degrade must cover csv.Error (round-2 r2-mass-2/item 2):
# the round-1 tuple (DiagnosticsRefusal, OSError, ValueError) let csv.Error escape
# _gt_attribution_block, so _terminal_fields raised DURING the completion/K1-halt
# handlers => manifest stranded at status 'running' forever and KillThresholdHalt
# swallowed into __context__.
# ---------------------------------------------------------------------------


def _corrupt_gt_bundle(tmp_path: Path) -> Path:
    """GT bundle whose CSV carries one oversized field (> csv.field_size_limit(),
    131072): parsing raises csv.Error — which is NEITHER DiagnosticsRefusal nor
    OSError nor ValueError, exactly the class the round-1 tuple missed. The manifest
    itself is valid JSON at an existing path (the rule-7 resolver requires existence),
    so refusal happens INSIDE attribute_ground_truth's CSV parse."""
    csv_path = tmp_path / "points_oversized.csv"
    huge = "9" * (csv.field_size_limit() + 1024)
    csv_path.write_text(f"id,lat,lon\nBROKEN,{huge},77.6\n")
    man_path = tmp_path / "gt_manifest_oversized.json"
    man_path.write_text(json.dumps({"csv_path": str(csv_path), "points_count": 1}))
    return man_path


def _grid_manifest(rows: int, cols: int) -> dict:
    """Producer-declared grid so diagnostics can reconstruct node positions from the toy
    chain graph (without it _resolve_node_geometry refuses BEFORE the GT CSV is ever
    parsed, and the test would exercise the wrong refusal)."""
    return {
        "config_snapshot": {
            "grid": {
                "height": rows,
                "width": cols,
                "transform": [10.0, 0.0, 500000.0, 10.0, 0.0, 4500000.0],
                "cell_area_m2": 100.0,
            }
        }
    }


class TestGtDegradeCoversCsvError:
    def test_completion_path_degrades_not_assessed_on_oversized_gt_csv(self, tmp_path):
        """A completed run whose GT CSV raises csv.Error must reach terminal status
        'completed' with a top-level NOT_ASSESSED gt_attribution carrying the verbatim
        error — never a stranded 'running' manifest, never a propagated csv.Error.

        V7 scope: 3 steps x 256 cells dry toy domain; scope claimed: the terminal-field
        degrade seam only."""
        cfg = _hermetic_cfg(tmp_path)
        cfg = dc_replace(
            cfg,
            diagnostics=dc_replace(
                cfg.diagnostics, ground_truth_manifest=_corrupt_gt_bundle(tmp_path)
            ),
        )
        res = simulate_coupled(
            cfg,
            _solver_cfg(),
            REPO,
            graph=_chain_graph(16, 16, n_nodes=2, manifest=_grid_manifest(16, 16)),
            static=_static((16, 16)),
            duration_s=30.0,
            max_steps=3,
            mass_check_every=10_000_000,
        )
        assert res.steps == 3
        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "completed", "terminal manifest write must survive GT refusal"
        attr = man["gt_attribution"]
        assert attr["suspicious_state"] == "NOT_ASSESSED"
        assert attr["error_type"] == csv.Error.__name__
        assert "field larger than field limit" in attr["error"]

    def test_k1_halt_path_writes_halted_K1_despite_oversized_gt_csv(self, tmp_path):
        """Same corrupt GT bundle behind a K1-firing run: the halt handler's terminal
        write must land 'halted_K1' (pre-fix the csv.Error escaped INSIDE the except
        handler, swallowed KillThresholdHalt into __context__ and left status 'running').

        V7 scope: 4x4 cells, 2 nodes (one huge-shaft), no rain; K1 detector + terminal
        write under GT refusal."""
        from jaladhar.coupling.exchange import NodeState

        cfg = _coupling_cfg(tmp_path)
        cfg = dc_replace(
            cfg,
            diagnostics=dc_replace(
                cfg.diagnostics, ground_truth_manifest=_corrupt_gt_bundle(tmp_path)
            ),
        )
        graph = _chain_graph(4, 4, n_nodes=2, widths=[5000.0, 2.285], manifest=_grid_manifest(4, 4))
        surged_state = NodeState(
            h_node_m=torch.tensor([6.0, 0.0], dtype=torch.float32),
            vol_in_m3_cum=torch.zeros(2, dtype=torch.float64),
            vol_out_m3_cum=torch.zeros(2, dtype=torch.float64),
        )
        with pytest.raises(KillThresholdHalt, match="K1"):
            simulate_coupled(
                cfg,
                _solver_cfg(),
                REPO,
                graph=graph,
                static=_static((4, 4)),
                node_state=surged_state,
                duration_s=60.0,
                max_steps=500,
                mass_check_every=10_000_000,
            )

        man = json.loads(cfg.outputs.manifest.read_text())
        assert man["status"] == "halted_K1", "K1 terminal manifest must survive GT refusal"
        k1 = man["kill_thresholds"]["K1"]
        assert k1["fired"] is True and k1["coupled_steps"] >= 4
        attr = man["gt_attribution"]
        assert attr["suspicious_state"] == "NOT_ASSESSED"
        assert attr["error_type"] == csv.Error.__name__


# ---------------------------------------------------------------------------
# Smoke CLI GT degrade (round-2 item 4): smoke_real_window.py called
# attribute_ground_truth BARE after simulate had already written a completed
# manifest — a refused/unreadable GT bundle crashed the CLI with a traceback
# AFTER completion. The driver now reuses solver_hook's honest-degrade wrapper
# and prints a warning; the exit code comes from the typed gates only.
#
# RE-SMOKE UPDATE (V6 flip recorded; driver rewrite landed together with this):
# contract v1.2.0 closed D-G, so the driver's retired declared-deviation
# assembly is gone — it now runs the FULL-DOMAIN production path via
# simulate_coupled(graph=None). The xfails marker is REMOVED accordingly. The
# DEGRADE INTENT IS UNCHANGED VERBATIM: rename the ground-truth bundle away =>
# completed + gt_attribution NOT_ASSESSED + warning line, exit 0. Only the
# mechanics moved: the production path costs ~1.1 s/step on the full grid, so
# this subprocess gets an explicit small envelope (duration_s patched in its
# generated config, --max-steps on the driver) — a plumbing test must not pay
# for a full storm window.
# ---------------------------------------------------------------------------


class TestSmokeCliGtDegrade:
    def test_smoke_completes_with_renamed_groundtruth_manifest(self, tmp_path):
        """Full smoke_real_window subprocess against the REAL graph/domain with the
        ground-truth bundle renamed away under /tmp: the config points at a VALID
        manifest whose declared csv_path no longer exists (the resolver requires the
        manifest file itself to exist, so the rename is simulated one level down, at
        the bundle's CSV). The driver completes simulate, prints the WARNING +
        NOT_ASSESSED line instead of a traceback, writes the terminal manifest with a
        TOP-LEVEL gt_attribution degrade block, and exits from the typed gates
        (0 here — the window surcharges).

        V7 scope: one bounded-envelope FULL-DOMAIN production-path run via
        subprocess (duration_s=900, max_steps=240 => ~5-8 min wall on CPU);
        scope claimed: stage-14 degrade + typed exit, on real terrain bytes."""
        script = REPO / "tests" / "coupling" / "smoke_real_window.py"
        base_cfg_text = (REPO / "configs" / "coupling.yaml").read_text()
        # the bundle's CSV is renamed away; the manifest that POINTS at it stays in place
        gt_manifest = tmp_path / "gt_manifest.json"
        gt_manifest.write_text(
            json.dumps(
                {
                    "csv_path": str(tmp_path / "renamed_away" / "points.csv"),
                    "points_count": 24,
                }
            )
        )
        patched, n_sub = re.subn(
            r"^  ground_truth_manifest:.*$",
            f'  ground_truth_manifest: "{gt_manifest}"  # bundle CSV renamed away (test)',
            base_cfg_text,
            flags=re.M,
        )
        assert n_sub == 1, "ground_truth_manifest line not found in configs/coupling.yaml"
        # bound the envelope: production full-domain path (~1.1 s/step measured);
        # 900 simulated s proxies ~196 steps on the previous realized schedule,
        # comfortably inside the subprocess timeout while crossing surcharge onset.
        patched, n_dur = re.subn(
            r"^  duration_s: .*$",
            "  duration_s: 900.0                 # bounded envelope for the GT-degrade subprocess",
            patched,
            flags=re.M,
        )
        assert n_dur == 1, "smoke.duration_s line not found in configs/coupling.yaml"
        cfg_path = tmp_path / "coupling_gt_missing.yaml"
        cfg_path.write_text(patched)
        out_dir = tmp_path / "smoke_out"

        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--config",
                str(cfg_path),
                "--out-dir",
                str(out_dir),
                "--max-steps",
                "240",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=880,
        )
        print(f"\n[smoke-gt-degrade] rc={proc.returncode}\nstdout tail:\n{proc.stdout[-1200:]}")
        print(f"stderr tail:\n{proc.stderr[-600:]}")
        assert (
            proc.returncode == 0
        ), f"smoke exited {proc.returncode} — stderr tail: {proc.stderr[-800:]}"
        assert "Traceback" not in proc.stderr, "GT refusal must not crash the CLI post-completion"
        assert "WARNING" in proc.stdout and "NOT_ASSESSED" in proc.stdout
        man = json.loads((out_dir / "manifest.json").read_text())
        assert man["status"] == "completed"
        # TOP-LEVEL degrade block (driver-emitted at terminal time by solver_hook)
        gt = man["gt_attribution"]
        assert gt["suspicious_state"] == "NOT_ASSESSED"
        assert gt["error_type"] == "DiagnosticsRefusal"
        assert gt["error"], "degrade block lost the verbatim refusal"
