"""V2: if this were broken I would observe a G2 verdict computed from ``total_returned_m3``
alone with NO component-class split present in its input (an unsplit number scored silently),
or a component_split whose two classes have collapsed into one bucket, or SUSPICIOUS firing
at exactly 0.5 instead of STRICTLY above it.

=============================================================================================
INVARIANT #13 ``dead-end-split-reported`` — WF-2 spec §11.3 + §12 row #13
=============================================================================================
Claim: diagnostics partition EVERY surcharge event by component class (outfall_terminating vs
dead_end, joined from ``DrainGraph.component_class`` = directed reachability to the 17 outfall
nodes) and report BOTH headline figures DISTINCTLY — the directed node-count split (realized
graph [F]: 55 / 1666 of 1721, directed share 55/1721 ~= 0.0320) AND the length-weighted
weak-connectivity fraction (0.736484 [F], read from the WF-1 build manifest's
``component_policy`` block); G2 scoring REFUSES an unsplit input (deleting the split key =>
:class:`DiagnosticsRefusal`, never a silent score); SUSPICIOUS fires strictly when dead-end
share of GT-matched returned volume > ``suspicious_deadend_share`` (0.5).

WHY THIS IS THE OWNER'S PROTECTION [F-context]: ALL 116 preregistered predicted nodes are
dead_end class. Under v1 there is NO outfall export term (deviation D-A pending), so every m³
captured in a dead-end component must eventually surcharge back — dead-end-dominated GT
reproduction is EXPECTED BY CONSTRUCTION. The SUSPICIOUS machinery exists precisely so that
this topology artifact is reported as a finding instead of celebrated as skill; an unsplit G2
number would make that celebration invisible.

ARMS (each with hand-computed expectations, own values — fixture PATTERNS reused from
tests/coupling/test_diagnostics.py):
  (a) split arithmetic on a declared-synthetic 9-node TWO-COMPONENT graph (chain->outfall =
      4 outfall_terminating; 5 isolated dead_ends) with events spanning BOTH classes: per-class
      n_events + returned sums match hand computation EXACTLY, per_class + totals partition
      all 5 events with no third bucket;
  (b) both headline fractions present and DISTINCTLY labelled: directed counts {4, 5} ->
      directed fraction 4/9 ~= 0.4444 vs manifest-carried length-weighted 0.736484 — asserted
      DIFFERENT numbers measuring different things (count/directed vs length/weak wording),
      plus the [F] anchor: the REAL build manifest JSON carries 0.736484 verbatim;
  (c) unsplit refusal (the invariant's teeth): twin input WITH the split passes; deleting ONLY
      the split key => DiagnosticsRefusal; a single-class split dict refuses too; CLI score-g2
      on an unsplit manifest exits non-zero printing REFUSED;
  (d) SUSPICIOUS boundary at ULP scale: share exactly 0.5 => FALSE; threshold nudged one ULP
      below 0.5 => TRUE; one ULP above => FALSE; clearly above/below (0.55/0.45) both ways.
      Threshold-side ULP probes are exact by construction. Volume-side adjacent-double
      volumes CAN move the share — fl(dead_end + outfall) of two adjacent doubles lands
      EXACTLY half-way between two representable sums and the half-ULP tie resolves by
      round-half-to-EVEN mantissa parity, so roughly HALF of adjacent pairs shift the
      dead-end share off 0.5 (auditor-measured anchor, re-verified this session:
      nextafter(10.0, inf) / fl(10.0 + nextafter(10.0, inf)) == 0.5000000000000001 !=
      0.5, while the outfall-nudged twin divides to exactly 0.5 — the old claim held only
      for the denominator-nudged orientation). An earlier draft asserted the volume side
      CANNOT move ("still divides to exactly 0.5"); that was WRONG. The strictness
      observable lives on the threshold side BY CHOICE — exact by construction there —
      not because the volume side is immovable; a deterministic volume-side probe now
      asserts the moving case as well;
  (e) end-to-end: a REAL attribute_ground_truth call (dead-end-dominant: share == 1.0 =>
      suspicious TRUE) is embedded into a score-g2 manifest fixture — PASS variant exits 0
      printing SUSPICIOUS=TRUE with the 55/1666 split numbers and the BY-CONSTRUCTION note;
      FAIL variant (returned < floor) exits NON-ZERO still printing SUSPICIOUS=TRUE.

V5 RED DEMOS — mutations applied to /tmp/opencode/redtest_inv13/ COPIES of diagnostics.py
ONLY (repo source never touched; each anchor verified to occur EXACTLY ONCE in the committed
source so drift fails loudly instead of mutating nothing):
  M1  unsplit-refusal removed: ``_normalized_class_counts`` returns zeroed counts instead of
      raising => the SAME call that raises DiagnosticsRefusal on pristine code now returns
      ("pass", ...) scored from totals alone. Observable moved VERBATIM: raise -> pass tuple.
  M2  component_split collapses both classes into one bucket: ``cls = classes[idx]`` ->
      ``cls = "dead_end"`` => per-class arithmetic moves from the hand values
      {ot: 2 events / 9.75 m³, de: 3 / 15.875} to {ot: 0 / 0.0, de: 5 / 25.625}. Both
      observables captured verbatim beside the assertions.

Mutation record (V5): kind ``unsplit_refusal_removed`` at site
``jaladhar.coupling.diagnostics._normalized_class_counts`` (M1) and kind
``class_collapse_to_single_bucket`` at site ``jaladhar.coupling.diagnostics.component_split``
(M2); applied to /tmp copies only; reverted by construction (copies discarded).

V7 scope statement (template): Scope run: 0 simulation steps, 16 cells (declared-synthetic
9-node graph on a 4x4 grid), CPU-only, parameter range share in {0.45, 0.5, 0.55} +- 1 ULP,
radius 100 m synthetic; events are SYNTHETIC rows, not a real coupled population. Scope
claimed: the diagnostics layer's partition arithmetic, dual-headline labelling, unsplit
refusal, strict-threshold semantics and CLI gate plumbing — NOT exchange physics or routing
(own units). Gap => PARTIAL/BLOCKED: the REAL coupled population (events from a real coupled
run over the real 1,721-node graph, live recomputation of the directed 55/1666 split, and the
real 24-point GT join against it) is gated by the WF-1 artefact seam (D-G) plus the first
coupled smoke run, so those legs cannot execute today; the 0.736484 figure IS anchored to its
declared artefact (runs/drain_graph_build/manifest.json read directly) while 55/1666 enters
only as the spec §11.3 [F] fixture constant.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import torch
from conftest import load_mutated_source
from typer.testing import CliRunner

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.diagnostics import (
    COMPONENT_CLASSES,
    DiagnosticsRefusal,
    app,
    attribute_ground_truth,
    component_split,
    g2_verdict,
)
from jaladhar.coupling.router import (
    RefuseLoadError,
    build_drain_graph,
    load_drain_graph,
)
from jaladhar.coupling.solver_hook import simulate_coupled
from jaladhar.solver.run import uniform_storm
from jaladhar.solver.state import load_solver_config

REPO = Path(__file__).resolve().parents[2]
DIAGNOSTICS_SRC = REPO / "src" / "jaladhar" / "coupling" / "diagnostics.py"
REAL_BUILD_MANIFEST = REPO / "runs" / "drain_graph_build" / "manifest.json"
RED_DEMO_ROOT = Path("/tmp/opencode/redtest_inv13")

RUNNER = CliRunner()

DIRECTED_SPLIT_F = {"outfall_terminating": 55, "dead_end": 1666}
DIRECTED_SPLIT_F_V2 = {"outfall_terminating": 68, "dead_end": 1653}
LENGTH_FRACTION_F = 0.736484


def _ev(node_id: int, total: float, first: int = 1, last: int = 2, head: float = 2.0):
    return {
        "node_id": node_id,
        "total_returned_m3": total,
        "first_step": first,
        "last_step": last,
        "max_head_m": head,
    }


EVENTS_BOTH_CLASSES = [
    _ev(2, 2.5),
    _ev(4, 7.25),
    _ev(5, 4.0),
    _ev(7, 0.125),
    _ev(9, 11.75),
]
HAND_OT = {"n_events": 2, "returned_m3": 9.75}
HAND_DE = {"n_events": 3, "returned_m3": 15.875}
HAND_TOTAL_RETURNED = 25.625


def _two_component_graph():
    n = 9
    edge_from = torch.tensor([0, 1, 2], dtype=torch.int64)
    edge_to = torch.tensor([1, 2, 3], dtype=torch.int64)
    outfall = torch.zeros(n, dtype=torch.bool)
    outfall[3] = True
    cells = [(0, 1), (0, 2), (0, 3), (0, 0), (1, 1), (2, 1), (3, 1), (1, 3), (3, 3)]
    nmap = torch.full((4, 4), -1, dtype=torch.int32)
    for i, (r, c) in enumerate(cells):
        nmap[r, c] = i + 1
    return build_drain_graph(
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=torch.ones(3, dtype=torch.bool),
        q_cap_nom_m3s=torch.full((3,), 0.5, dtype=torch.float64),
        width_mean_m=torch.full((n,), 6.71, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, n, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n, dtype=torch.float64),
        outfall_node=outfall,
        node_cell_row=torch.tensor([r for r, _ in cells], dtype=torch.int32),
        node_cell_col=torch.tensor([c for _, c in cells], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.ones(n, dtype=torch.int64),
        manifest={
            "config_snapshot": {
                "grid": {
                    "height": 4,
                    "width": 4,
                    "transform": [10.0, 0.0, 500000.0, 10.0, 0.0, 4500000.0],
                    "cell_area_m2": 100.0,
                }
            },
            "component_policy": {"outfall_terminating_observed_length_fraction": LENGTH_FRACTION_F},
        },
    )


def _gt_bundle(tmp_path: Path, rows: list[dict]) -> Path:
    lines = ["id,location_name,x_m,y_m"] + [
        f"{r['id']},{r.get('name', 'synthetic')},{r['x_m']},{r['y_m']}" for r in rows
    ]
    csv_path = tmp_path / f"points_{len(rows)}.csv"
    csv_path.write_text("\n".join(lines) + "\n")
    man_path = tmp_path / f"gt_manifest_{len(rows)}.json"
    man_path.write_text(json.dumps({"csv_path": str(csv_path), "points_count": len(rows)}))
    return man_path


NODE_DE, NODE_OT = 101, 202
XY_SPARSE = {NODE_DE: (5000.0, 5000.0), NODE_OT: (6000.0, 5000.0)}
CLS_SPARSE = {NODE_DE: "dead_end", NODE_OT: "outfall_terminating"}
BOUNDARY_POINTS = [
    {"id": "PA", "x_m": 5000.0, "y_m": 5000.0},
    {"id": "PB", "x_m": 6000.0, "y_m": 5000.0},
]


def _boundary_attr(events: list[dict], tmp_path: Path, threshold: float | None = None) -> dict:
    man = _gt_bundle(tmp_path, BOUNDARY_POINTS)
    kw = {} if threshold is None else {"suspicious_deadend_share": threshold}
    return attribute_ground_truth(
        events,
        man,
        radius_m=100.0,
        node_xy_m=XY_SPARSE,
        node_class=CLS_SPARSE,
        **kw,
    )


def _g2_inputs(steps: int, returned: float, *, split: bool = True) -> dict:
    d: dict = {
        "total_surcharging_steps": steps,
        "total_returned_m3": returned,
        "component_class_split": dict(DIRECTED_SPLIT_F),
    }
    if not split:
        del d["component_class_split"]
    return d


# V5 red-demo machinery: mutated COPY under /tmp/opencode, repo source untouched


def _load_mutated_diagnostics(replacements: list[tuple[str, str]], tag: str):
    """Copy diagnostics.py to /tmp/opencode/redtest_inv13/<tag>/, apply the listed textual
    mutations (each anchor must occur EXACTLY ONCE — drift fails HERE, loudly), import the
    copy standalone. Pattern from tests/coupling/test_inv03_capture_bounded.py."""
    mod, _path = load_mutated_source(
        DIAGNOSTICS_SRC,
        replacements,
        tag=tag,
        prefix="inv13",
        directory=RED_DEMO_ROOT,
    )
    return mod


M1_ANCHOR = (
    """    raise DiagnosticsRefusal(\n"""
    '''        "[diagnostics] G2 input carries NO usable component-class split "'''
)
M1_MUTATION = (
    """    return {"outfall_terminating": 0, "dead_end": 0}"""
    """  # INV13-M1: unsplit-refusal REMOVED\n""" + M1_ANCHOR
)

M2_ANCHOR = """        cls = classes[idx]"""

# making the mutation a silent no-op — exactly the vacuous red demo V5 forbids.
M2_MUTATION = """        cls = "dead_end"  # INV13-M2: both classes COLLAPSED into one bucket"""


class TestSplitArithmeticHandComputed:
    def test_every_event_partitioned_into_exactly_two_classes(self):
        got = component_split(EVENTS_BOTH_CLASSES, _two_component_graph())
        assert got["per_class"]["outfall_terminating"] == HAND_OT
        assert got["per_class"]["dead_end"] == HAND_DE
        assert got["totals"] == {
            "n_events": 5,
            "returned_m3": HAND_TOTAL_RETURNED,
        }

        n_partitioned = sum(c["n_events"] for c in got["per_class"].values())
        assert n_partitioned == got["totals"]["n_events"] == 5
        assert set(got["per_class"]) <= set(COMPONENT_CLASSES)

        assert "DIRECTED" in got["basis"] and "component_class" in got["basis"]

    def test_zero_events_yield_empty_buckets_not_missing_keys(self):
        got = component_split([], _two_component_graph())
        assert got["per_class"]["outfall_terminating"] == {"n_events": 0, "returned_m3": 0.0}
        assert got["totals"]["n_events"] == 0


class TestDualHeadlineFractionsDistinct:
    def test_directed_and_length_weighted_figures_both_present_and_different(self):
        hf = component_split(EVENTS_BOTH_CLASSES, _two_component_graph())["headline_fractions"]
        directed = hf["directed_node_count_split"]
        length_frac = hf["length_weighted_weak_connectivity_outfall_fraction"]

        assert directed["counts"] == {"outfall_terminating": 4, "dead_end": 5}
        assert sum(directed["counts"].values()) == 9

        assert length_frac == LENGTH_FRACTION_F
        assert "component_policy" in hf["length_weighted_source"]

        directed_fraction = directed["counts"]["outfall_terminating"] / sum(
            directed["counts"].values()
        )
        assert abs(directed_fraction - LENGTH_FRACTION_F) > 0.05, (
            "the directed node-count share and the length-weighted weak-connectivity "
            "fraction measure DIFFERENT quantities; here they differ by "
            f"{abs(directed_fraction - LENGTH_FRACTION_F):.4f}"
        )

        d_txt = directed["measures"].lower()
        l_txt = hf["length_weighted_measures"].lower()
        assert "count of nodes" in d_txt and "directed" in d_txt
        assert "length" in l_txt and "weak" in l_txt
        assert "weak" not in d_txt and "count of nodes" not in l_txt

    def test_length_weighted_figure_is_anchored_to_the_real_build_manifest(self):
        policy = json.loads(REAL_BUILD_MANIFEST.read_text())["component_policy"]
        assert policy["outfall_terminating_observed_length_fraction"] == LENGTH_FRACTION_F
        assert policy["accepted_by_owner"] is True

    def test_manifest_without_component_policy_reports_absent_not_zero(self):
        g = _two_component_graph()
        stripped = dict(g.manifest)
        stripped.pop("component_policy")
        g2 = build_drain_graph(
            edge_from=g.edge_from,
            edge_to=g.edge_to,
            capacity_bearing=g.capacity_bearing,
            q_cap_nom_m3s=g.q_cap_nom_m3s,
            width_mean_m=g.width_mean_m,
            shaft_length_proxy_m=1.0,
            node_elev_m=g.node_elev_m,
            contrib_area_m2=g.contrib_area_m2,
            outfall_node=g.outfall_node,
            node_cell_row=g.node_cell_row,
            node_cell_col=g.node_cell_col,
            node_id_map=g.node_id_map,
            node_cell_count=g.node_cell_count,
            manifest={"config_snapshot": stripped["config_snapshot"]},
        )
        hf = component_split([], g2)["headline_fractions"]
        assert hf["length_weighted_weak_connectivity_outfall_fraction"] is None
        assert "ABSENT" in hf["length_weighted_source"]


class TestG2UnsplitRefusal:
    def test_twin_input_with_split_passes_refusal_keys_off_the_split(self):
        passing = _g2_inputs(12, 480.5)
        verdict, reason = g2_verdict(passing)
        assert verdict == "pass"
        assert "outfall_terminating=55" in reason and "dead_end=1666" in reason

    def test_deleting_only_the_split_key_raises_never_scores(self):
        unsplit = _g2_inputs(12, 480.5, split=False)
        with pytest.raises(DiagnosticsRefusal, match="unsplit"):
            g2_verdict(unsplit)

    def test_single_class_split_dict_also_refuses(self):
        single = _g2_inputs(12, 480.5)
        single["component_class_split"] = {"dead_end": 1666}
        with pytest.raises(DiagnosticsRefusal, match="component-class split"):
            g2_verdict(single)

    def test_cli_score_g2_on_unsplit_manifest_refused_nonzero(self, tmp_path):
        m = tmp_path / "unsplit_manifest.json"
        m.write_text(json.dumps({"total_surcharging_steps": 12, "total_returned_m3": 480.5}))
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 1
        assert "REFUSED" in r.output
        assert "G2 PASS" not in r.output, "an unsplit manifest must never look green"


class TestSuspiciousStrictThresholdBoundary:
    def test_share_exactly_at_threshold_is_not_suspicious(self, tmp_path):
        got = _boundary_attr([_ev(NODE_DE, 10.0), _ev(NODE_OT, 10.0)], tmp_path)
        share = got["dead_end_share_of_gt_matched_returned_volume"]
        assert share == 0.5, f"hand-built equal volumes must divide to exactly 0.5, got {share!r}"
        assert got["suspicious"] is False
        assert got["suspicious_state"] == "FALSE"

    def test_one_ulp_below_threshold_fires_one_ulp_above_does_not(self, tmp_path):
        events = [_ev(NODE_DE, 10.0), _ev(NODE_OT, 10.0)]
        lo = _boundary_attr(events, tmp_path, threshold=math.nextafter(0.5, 0.0))
        assert lo["suspicious"] is True, "share 0.5 > nextafter(0.5, 0.0) must fire STRICTLY"
        assert lo["suspicious_state"] == "TRUE"
        assert lo["suspicious_deadend_share_threshold"] == math.nextafter(0.5, 0.0)
        hi = _boundary_attr(events, tmp_path, threshold=math.nextafter(0.5, math.inf))
        assert hi["suspicious"] is False, "share 0.5 < nextafter(0.5, inf) must stay FALSE"
        assert hi["suspicious_deadend_share_threshold"] == math.nextafter(0.5, math.inf)

    @pytest.mark.parametrize(
        "volumes,suspicious_expected",
        [
            ((55.0, 45.0), True),
            ((45.0, 55.0), False),
        ],
    )
    def test_clearly_either_side_of_default_threshold(self, tmp_path, volumes, suspicious_expected):
        de_v, ot_v = volumes
        got = _boundary_attr([_ev(NODE_DE, de_v), _ev(NODE_OT, ot_v)], tmp_path)
        expected_share = de_v / (de_v + ot_v)
        assert got["dead_end_share_of_gt_matched_returned_volume"] == expected_share
        assert got["suspicious"] is suspicious_expected
        assert got["suspicious_state"] == ("TRUE" if suspicious_expected else "FALSE")

    def test_volume_side_adjacent_double_nudge_moves_the_share(self, tmp_path):
        v = 10.0
        de_nudged = _boundary_attr(
            [_ev(NODE_DE, math.nextafter(v, math.inf)), _ev(NODE_OT, v)], tmp_path
        )
        share = de_nudged["dead_end_share_of_gt_matched_returned_volume"]
        print(f"\n[inv13-vol-side] de-nudged share = {share!r}")
        assert (
            share == 0.5000000000000001
        ), f"adjacent-double dead-end nudge did not move the share off 0.5: {share!r}"
        assert de_nudged["suspicious"] is True
        assert de_nudged["suspicious_state"] == "TRUE"

        ot_nudged = _boundary_attr(
            [_ev(NODE_DE, v), _ev(NODE_OT, math.nextafter(v, math.inf))], tmp_path
        )
        share_ot = ot_nudged["dead_end_share_of_gt_matched_returned_volume"]
        print(f"[inv13-vol-side] outfall-nudged control share = {share_ot!r}")
        assert share_ot == 0.5, f"outfall-nudged twin moved unexpectedly: {share_ot!r}"
        assert ot_nudged["suspicious"] is False


class TestEndToEndDeadEndDominantCli:
    def _attribution_block(self, tmp_path: Path) -> dict:
        man = _gt_bundle(tmp_path, [{"id": "P_ONLY", "x_m": 5000.0, "y_m": 5000.0}])
        attr = attribute_ground_truth(
            [_ev(NODE_DE, 90.0)],
            man,
            radius_m=100.0,
            node_xy_m={NODE_DE: (5000.0, 5000.0)},
            node_class={NODE_DE: "dead_end"},
        )
        assert attr["dead_end_share_of_gt_matched_returned_volume"] == 1.0
        assert attr["suspicious"] is True and attr["suspicious_state"] == "TRUE"
        return attr

    def test_passing_dead_end_dominant_run_prints_suspicious_true_exit_zero(self, tmp_path):
        m = tmp_path / "passing.json"
        m.write_text(
            json.dumps(
                {
                    "total_surcharging_steps": 9,
                    "total_returned_m3": 51234.5,
                    "component_class_split": dict(DIRECTED_SPLIT_F),
                    "gt_attribution": self._attribution_block(tmp_path),
                }
            )
        )
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 0, r.output
        first = r.output.splitlines()[0]
        assert first.startswith("G2 PASS")
        assert "SUSPICIOUS=TRUE" in first
        assert "outfall_terminating=55" in first and "dead_end=1666" in first
        assert "BY CONSTRUCTION" in r.output, "the D-A finding rides along, never celebrated"

    def test_failing_dead_end_dominant_run_prints_suspicious_true_exit_nonzero(self, tmp_path):
        m = tmp_path / "failing.json"
        m.write_text(
            json.dumps(
                {
                    "total_surcharging_steps": 9,
                    "total_returned_m3": 0.42,
                    "component_class_split": dict(DIRECTED_SPLIT_F),
                    "gt_attribution": self._attribution_block(tmp_path),
                }
            )
        )
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 1
        first = r.output.splitlines()[0]
        assert first.startswith("G2 FAIL")
        assert "SUSPICIOUS=TRUE" in first


# RED DEMOS (V5) — mutations on /tmp copies; repo source untouched


class TestRedDemoV5MutationsOnTmpCopies:
    def test_m1_unsplit_refusal_removed_scores_the_unsplit_input(self):
        unsplit = _g2_inputs(12, 480.5, split=False)
        with pytest.raises(DiagnosticsRefusal) as ei:
            g2_verdict(unsplit)
        pristine_observable = f"DiagnosticsRefusal: {str(ei.value)[:80]}..."

        mod = _load_mutated_diagnostics([(M1_ANCHOR, M1_MUTATION)], "m1")
        mutant_result = mod.g2_verdict(unsplit)
        verdict, reason = mutant_result
        # The same assertion that is GREEN against pristine code is RED against the mutant:
        assert isinstance(
            mutant_result, tuple
        ), f"pristine raised {pristine_observable}; mutant returned {mutant_result!r}"
        assert verdict == "pass" and "outfall_terminating=0" in reason, (
            f"M1 moved the observable: pristine={pristine_observable} -> "
            f"mutant={mutant_result!r} (unsplit input SCORED from totals alone)"
        )
        # And the mutant's teeth are gone for the single-class collapse too:
        single = _g2_inputs(12, 480.5)
        single["component_class_split"] = {"dead_end": 1666}
        s_verdict, _ = mod.g2_verdict(single)
        assert s_verdict == "pass", "M1 also scores a single-class (collapsed) input"

    def test_m2_class_collapse_moves_per_class_arithmetic_off_hand_values(self):
        pristine = component_split(EVENTS_BOTH_CLASSES, _two_component_graph())
        assert pristine["per_class"]["outfall_terminating"] == HAND_OT
        assert pristine["per_class"]["dead_end"] == HAND_DE

        mod = _load_mutated_diagnostics([(M2_ANCHOR, M2_MUTATION)], "m2")
        mut = mod.component_split(EVENTS_BOTH_CLASSES, _two_component_graph())
        mut_ot = mut["per_class"]["outfall_terminating"]
        assert mut_ot == {
            "n_events": 0,
            "returned_m3": 0.0,
        }, f"M2 moved ot bucket from {HAND_OT!r} to collapsed {mut_ot!r}"
        assert mut["per_class"]["dead_end"] == {
            "n_events": 5,
            "returned_m3": HAND_TOTAL_RETURNED,
        }, (
            f"M2 moved de bucket from {HAND_DE!r} to collapsed "
            f"{mut['per_class']['dead_end']!r} (both classes merged into one bucket)"
        )


# BLOCKED companion (V7, house convention — cf. inv01/05/10/11): the REAL event


@pytest.mark.slow
def test_real_graph_companion_real_event_population_or_blocked() -> None:
    cfg = resolve_config(REPO / "configs" / "coupling.yaml", REPO)
    try:
        g = load_drain_graph(cfg, REPO)
    except RefuseLoadError as exc:
        pytest.xfail(
            "BLOCKED (V7): real-population dead-end-split coverage closes when the WF-1 "
            "artefact seam D-G closes AND the first coupled smoke run over the real "
            f"1,721-node graph supplies a real event population -> {exc}"
        )
    scfg = load_solver_config(REPO / "configs" / "solver.yaml", REPO)
    scfg["boundaries"]["mode"] = "closed"
    rows, cols = (int(v) for v in g.node_id_map.shape)
    res = simulate_coupled(
        cfg,
        scfg,
        REPO,
        graph=g,
        h0=torch.full((rows, cols), 0.05, dtype=torch.float32),
        rain=uniform_storm(110.0, 7200.0),
        duration_s=60.0,
        max_steps=2,
        mass_check_every=10_000_000,
        smoke=True,
    )
    man = json.loads(cfg.outputs.manifest.read_text())
    assert res.steps <= 2 and man["status"] == "completed"
    events = res.ledger.flush_open_events()
    split = component_split(events, g)
    counts = split["headline_fractions"]["directed_node_count_split"]["counts"]

    # see DIRECTED_SPLIT_F_V2 record above; the loaded graph carries the echo).
    assert (
        counts == DIRECTED_SPLIT_F_V2
    ), f"live directed split {counts!r} != spec §11.3 [F] {DIRECTED_SPLIT_F_V2!r}"
    assert split["terminal_seed_resolution"]["definition_version"] == "v2-lake-boundary"
    assert split["totals"]["n_events"] == len(events)
    print(
        f"\n[inv13-real] grid={rows}x{cols} nodes={g.num_nodes} events={len(events)} "
        f"split={counts}"
    )
