"""Diagnostics unit tests — WF-2 (spec §4.6 + §11; deliverable 2).

Covers, against the REAL pre-registered falsifier set and the REAL ground-truth
manifest where stated:

- refuse-to-start: missing falsifier file, aggregated schema mismatches, drifted
  CSV header, unsplit G2 input (invariant #13's red target realized at unit
  level), missing node geometry for attribution;
- compare_falsifier correctness against HAND-BUILT overlaps with known counts;
- component-split arithmetic on a toy graph (chain+outfall+isolated dead_end)
  including both distinctly-labelled headline fractions;
- GT attribution radius BOUNDARY behaviour (inclusive at exactly radius_m) and
  the SUSPICIOUS threshold firing on BOTH sides;
- CSV schema exactness: hand-written expected BYTES (independent observable —
  not produced by the code under test), zero-row file, round-trip read;
- an end-to-end mini pipeline over a subset of REAL predicted node ids.

V2 notes (non-mirror observables): CSV bytes are asserted against literals typed
into this file; compare_falsifier expectations are hand-counted; the radius test
places GT points at exact geometric offsets (100.0 m inclusive vs 100.2 m out);
the reprojection path is anchored ABSOLUTELY (Bengaluru must land inside the
UTM43N band around x~762 km / y~1430 km) rather than by re-running the module's
own transformer.

V7 scope: every test is pure-CPU on synthetic event rows plus the two real JSON/CSV
artefacts named above; no simulation runs here. Scope claimed: diagnostics logic,
schema discipline, verdict/refusal semantics — NOT exchange physics or routing
(covered by their own units).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
import yaml
from shapely.geometry import LineString
from typer.testing import CliRunner

from jaladhar.coupling.diagnostics import (
    G2_MIN_RETURNED_M3_DEFAULT,
    DiagnosticsRefusal,
    FalsifierSet,
    _load_gt_points,
    app,
    attribute_ground_truth,
    attribute_ground_truth_edges,
    compare_falsifier,
    component_split,
    g2_verdict,
    load_drain_edges_geoms,
    load_falsifier_set,
    read_surcharge_events_csv,
    sha256_file,
    write_surcharge_events_csv,
)
from jaladhar.coupling.router import build_drain_graph

REPO = Path(__file__).resolve().parents[2]
FALSIFIER_PATH = REPO / "runs" / "wf2_falsifier_preregistration" / "surcharge_prediction_set.json"
GT_MANIFEST_PATH = REPO / "runs" / "groundtruth" / "manifest.json"

RUNNER = CliRunner()


def _ev(node_id: int, total: float = 1.0, first: int = 1, last: int = 2, head: float = 2.0):
    return {
        "node_id": node_id,
        "total_returned_m3": total,
        "first_step": first,
        "last_step": last,
        "max_head_m": head,
    }


def _toy_graph(manifest: dict | None = None):
    n = 4
    edge_from = torch.tensor([0, 1], dtype=torch.int64)
    edge_to = torch.tensor([1, 2], dtype=torch.int64)
    outfall = torch.zeros(n, dtype=torch.bool)
    outfall[2] = True
    nmap = torch.full((4, 4), -1, dtype=torch.int32)
    for i, (r, c) in enumerate([(1, 1), (1, 2), (1, 3), (3, 1)]):
        nmap[r, c] = i + 1
    man = {
        "config_snapshot": {
            "grid": {
                "height": 4,
                "width": 4,
                "transform": [10.0, 0.0, 500000.0, 10.0, 0.0, 4500000.0],
                "cell_area_m2": 100.0,
            }
        },
        "component_policy": {"outfall_terminating_observed_length_fraction": 0.736484},
    }
    if manifest is not None:
        man = manifest
    return build_drain_graph(
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=torch.ones(2, dtype=torch.bool),
        q_cap_nom_m3s=torch.tensor([0.5, 0.5], dtype=torch.float64),
        width_mean_m=torch.full((n,), 6.71, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, n, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n, dtype=torch.float64),
        outfall_node=outfall,
        node_cell_row=torch.tensor([1, 1, 1, 3], dtype=torch.int32),
        node_cell_col=torch.tensor([1, 2, 3, 1], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.ones(n, dtype=torch.int64),
        manifest=man,
    )


def _big_deadend_graph(n: int = 1700):
    outfall = torch.zeros(n, dtype=torch.bool)
    outfall[0] = True
    return build_drain_graph(
        edge_from=torch.tensor([1], dtype=torch.int64),
        edge_to=torch.tensor([0], dtype=torch.int64),
        capacity_bearing=torch.ones(1, dtype=torch.bool),
        q_cap_nom_m3s=torch.tensor([0.5], dtype=torch.float64),
        width_mean_m=torch.full((n,), 6.71, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.zeros(n, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n, dtype=torch.float64),
        outfall_node=outfall,
        node_cell_row=torch.zeros(n, dtype=torch.int32),
        node_cell_col=(torch.arange(n, dtype=torch.int32) % 64 + 8),
        node_id_map=torch.full((16, 80), -1, dtype=torch.int32),
        node_cell_count=torch.ones(n, dtype=torch.int64),
        manifest={"component_policy": {}},
    )


def _gt_bundle(
    tmp_path: Path,
    rows: list[dict],
    *,
    points_count: int | None = None,
    extra_cols: bool = False,
) -> Path:
    cols = (
        ["id", "lat", "lon", "x_m", "y_m"] if extra_cols else ["id", "location_name", "lat", "lon"]
    )
    lines = [",".join(cols)]
    for r in rows:
        if extra_cols:
            lines.append(
                f"{r['id']},{r.get('lat', 12.9)},{r.get('lon', 77.6)},{r['x_m']},{r['y_m']}"
            )
        else:
            lines.append(f"{r['id']},{r.get('name', 'synthetic')},{r['lat']},{r['lon']}")
    csv_path = tmp_path / "points.csv"
    csv_path.write_text("\n".join(lines) + "\n")
    manifest = {"csv_path": str(csv_path), "points_count": points_count or len(rows)}
    man_path = tmp_path / "gt_manifest.json"
    man_path.write_text(json.dumps(manifest))
    return man_path


class TestLoadFalsifierSet:
    def test_real_preregistered_set_loads_with_declared_counts(self):
        fs = load_falsifier_set(FALSIFIER_PATH)
        assert isinstance(fs, FalsifierSet)
        assert fs.n_predicted_edges == 70, "realized preregistered count [F]"
        assert fs.n_predicted_nodes == 116
        assert isinstance(fs.predicted_node_ids, tuple)
        ids = list(fs.predicted_node_ids)
        assert ids == sorted(ids), "recorded set is canonical ascending"
        assert len(set(ids)) == 116, "unique node targets"
        assert len(fs.source_gpkg_sha256) == 64
        assert fs.source_gpkg_sha256.startswith("7c63c9c1")
        assert isinstance(fs.edges, tuple) and len(fs.edges) == 70
        id_set = set(ids)
        assert all(e["to_node"] in id_set for e in fs.edges)

    def test_recorded_sha_matches_the_real_gpkg_bytes(self):
        fs = load_falsifier_set(FALSIFIER_PATH)
        assert sha256_file(REPO / "runs" / "drain_graph_build" / "drain_graph.gpkg") == (
            fs.source_gpkg_sha256
        )

    def test_missing_file_refuses_to_start(self, tmp_path):
        with pytest.raises(DiagnosticsRefusal, match="NOT FOUND"):
            load_falsifier_set(tmp_path / "absent.json")

    def test_missing_keys_aggregate_into_one_refusal(self, tmp_path):
        data = json.loads(FALSIFIER_PATH.read_text())
        del data["n_predicted_edges"]
        data["source_gpkg_sha256"] = "nothex"
        p = tmp_path / "mutated.json"
        p.write_text(json.dumps(data))
        with pytest.raises(DiagnosticsRefusal) as ei:
            load_falsifier_set(p)
        msg = str(ei.value)
        assert (
            "n_predicted_edges" in msg and "source_gpkg_sha256" in msg
        ), "rule-7 spirit: ALL schema problems named in ONE refusal"

    def test_inconsistent_edge_count_refuses(self, tmp_path):
        data = json.loads(FALSIFIER_PATH.read_text())
        data["n_predicted_edges"] = 69
        p = tmp_path / "mutated.json"
        p.write_text(json.dumps(data))
        with pytest.raises(DiagnosticsRefusal, match="disagrees"):
            load_falsifier_set(p)

    def test_unsorted_ids_refuse_a_mutated_set(self, tmp_path):
        data = json.loads(FALSIFIER_PATH.read_text())
        ids = data["predicted_node_ids"]
        ids[0], ids[1] = ids[1], ids[0]
        p = tmp_path / "mutated.json"
        p.write_text(json.dumps(data))
        with pytest.raises(DiagnosticsRefusal, match="ascending"):
            load_falsifier_set(p)


class TestSurchargeEventsCsv:
    def test_exact_bytes_header_column_order_and_sorting(self, tmp_path):
        """Expected bytes are TYPED INTO THIS TEST (V2: independent observable)."""
        rows = [
            _ev(59, total=0.001, first=1, last=1, head=1e-06),
            _ev(34, total=12.5, first=3, last=9, head=2.1),
        ]
        out = tmp_path / "events.csv"
        write_surcharge_events_csv(rows, out)
        expected = (
            "node_id,total_returned_m3,first_step,last_step,max_head_m\n"
            "34,12.5,3,9,2.1\n"
            "59,0.001,1,1,1e-06\n"
        )
        assert out.read_text() == expected

    def test_zero_row_file_is_allowed_header_only(self, tmp_path):
        out = tmp_path / "empty.csv"
        write_surcharge_events_csv([], out)
        assert out.read_text() == "node_id,total_returned_m3,first_step,last_step,max_head_m\n"
        assert read_surcharge_events_csv(out) == []

    def test_round_trip_read_returns_typed_rows(self, tmp_path):
        rows = [_ev(7, 3.25, 10, 14, 1.75), _ev(3, 0.5, 1, 2, 1.51)]
        out = tmp_path / "rt.csv"
        write_surcharge_events_csv(rows, out)
        back = read_surcharge_events_csv(out)
        assert [r["node_id"] for r in back] == [3, 7]
        assert back[0]["total_returned_m3"] == 0.5
        assert back[1]["total_returned_m3"] == 3.25
        assert isinstance(back[0]["first_step"], int)

    def test_drifted_header_refuses(self, tmp_path):
        out = tmp_path / "bad.csv"
        out.write_text("node_id,returned_m3,first,last,head\n1,2,3,4,5\n")
        with pytest.raises(DiagnosticsRefusal, match="exact G2"):
            read_surcharge_events_csv(out)

    def test_row_missing_required_field_refuses_aggregated(self, tmp_path):
        bad_rows = [
            {"node_id": 1, "total_returned_m3": 1.0},
            {
                "node_id": "x",
                "total_returned_m3": "y",
                "first_step": 1,
                "last_step": 1,
                "max_head_m": 1.0,
            },
        ]
        with pytest.raises(DiagnosticsRefusal) as ei:
            write_surcharge_events_csv(bad_rows, tmp_path / "never.csv")
        msg = str(ei.value)
        assert "row 0" in msg and "row 1" in msg
        assert not (tmp_path / "never.csv").exists(), "refusal precedes any write"


class TestCompareFalsifier:
    def _hand_fs(self) -> FalsifierSet:
        return FalsifierSet(
            n_predicted_edges=2,
            predicted_node_ids=(10, 20, 30),
            edges=(
                {"edge_id": 900, "from_node": 9, "to_node": 20},
                {"edge_id": 901, "from_node": 29, "to_node": 40},
            ),
            source_gpkg_sha256="a" * 64,
        )

    def test_hand_built_overlap_counts(self):
        events = [_ev(20, 5.0), _ev(30, 1.0), _ev(50, 2.0)]
        got = compare_falsifier(events, self._hand_fs())
        assert got["n_predicted_edges"] == 2
        assert got["n_predicted_nodes"] == 3
        assert got["predicted_nodes_surcharged"] == 2
        assert got["predicted_nodes_surcharged_ids"] == [20, 30]

        assert got["flagged_edges_whose_downstream_node_surcharged"] == 1
        assert got["flagged_edges_not_surcharged"] == [901]
        assert got["unflagged_surcharging_nodes"] == [50]
        x = got["source_gpkg_sha256_crosscheck"]
        assert x["status"] == "not_checked_no_loaded_graph_sha_provided"

    def test_perfect_disjoint_and_full_extremes(self):
        fs = self._hand_fs()
        full = compare_falsifier([_ev(10), _ev(20), _ev(30)], fs)
        assert full["predicted_nodes_surcharged"] == 3
        assert full["unflagged_surcharging_nodes"] == []
        assert full["flagged_edges_whose_downstream_node_surcharged"] == 1
        disjoint = compare_falsifier([_ev(99)], fs)
        assert disjoint["predicted_nodes_surcharged"] == 0
        assert disjoint["unflagged_surcharging_nodes"] == [99]
        assert disjoint["flagged_edges_not_surcharged"] == [900, 901]

    def test_sha_crosscheck_match_and_mismatch(self):
        fs = self._hand_fs()
        ok = compare_falsifier([_ev(10)], fs, gpkg_sha256_of_loaded_graph="A" * 64)
        assert ok["source_gpkg_sha256_crosscheck"]["status"] == "match"
        bad = compare_falsifier([_ev(10)], fs, gpkg_sha256_of_loaded_graph="b" * 64)
        assert bad["source_gpkg_sha256_crosscheck"]["status"] == "MISMATCH"


class TestComponentSplit:
    def test_arithmetic_on_toy_graph(self):
        g = _toy_graph()
        events = [_ev(2, 2.5), _ev(4, 4.0)]
        got = component_split(events, g)
        assert got["per_class"]["outfall_terminating"] == {"n_events": 1, "returned_m3": 2.5}
        assert got["per_class"]["dead_end"] == {"n_events": 1, "returned_m3": 4.0}
        assert got["totals"] == {"n_events": 2, "returned_m3": 6.5}
        hf = got["headline_fractions"]
        assert hf["directed_node_count_split"]["counts"] == {
            "outfall_terminating": 3,
            "dead_end": 1,
        }
        assert hf["length_weighted_weak_connectivity_outfall_fraction"] == 0.736484
        assert "component_policy" in hf["length_weighted_source"]

    def test_both_headline_measures_labelled_distinctly(self):
        got = component_split([], _toy_graph())["headline_fractions"]
        d_txt = got["directed_node_count_split"]["measures"].lower()
        l_txt = got["length_weighted_measures"].lower()
        assert "count of nodes" in d_txt and "directed" in d_txt
        assert "length-weighted" in l_txt.replace("observed-length", "length-weighted") or (
            "length" in l_txt and "weak" in l_txt
        )
        assert got["directed_node_count_split"]["counts"] != (
            got["length_weighted_weak_connectivity_outfall_fraction"]
        ), "the two figures measure different things; they must not collapse into one number"

    def test_manifest_without_component_policy_reports_absent_not_zero(self):
        man = {
            "config_snapshot": {
                "grid": {
                    "height": 4,
                    "width": 4,
                    "transform": [10.0, 0.0, 500000.0, 10.0, 0.0, 4500000.0],
                    "cell_area_m2": 100.0,
                }
            }
        }
        got = component_split([], _toy_graph(man))["headline_fractions"]
        assert got["length_weighted_weak_connectivity_outfall_fraction"] is None
        assert "ABSENT" in got["length_weighted_source"]

    def test_event_node_outside_graph_refuses(self):
        with pytest.raises(DiagnosticsRefusal, match="outside"):
            component_split([_ev(999)], _toy_graph())


class TestAttributeGroundTruthRadius:
    def test_radius_boundary_is_inclusive_at_exactly_radius_m(self, tmp_path):
        pts = [
            {"id": "AT_100", "x_m": 5100.0, "y_m": 5000.0},
            {"id": "OUT_100P2", "x_m": 5100.2, "y_m": 5000.0},
            {"id": "IN_90", "x_m": 5090.0, "y_m": 5000.0},
        ]
        man = _gt_bundle(tmp_path, pts, extra_cols=True)
        got = attribute_ground_truth(
            [_ev(1, 7.5)],
            man,
            radius_m=100.0,
            node_xy_m=[(5000.0, 5000.0)],
            node_class=["dead_end"],
        )
        by_id = {p["point_id"]: p for p in got["points"]}
        assert by_id["IN_90"]["matched"] is True
        assert by_id["AT_100"]["matched"] is True, "boundary is INCLUSIVE (d <= radius)"
        assert by_id["AT_100"]["nearest_distance_m"] == 100.0
        assert by_id["OUT_100P2"]["matched"] is False
        assert got["n_gt_matched"] == 2
        assert got["gt_matched_nodes"] == [1]

    def test_nearest_node_wins_among_two_candidates(self, tmp_path):
        pts = [{"id": "MID", "x_m": 5040.0, "y_m": 5000.0}]
        man = _gt_bundle(tmp_path, pts, extra_cols=True)
        got = attribute_ground_truth(
            [_ev(1, 3.0), _ev(2, 9.0)],
            man,
            radius_m=100.0,
            node_xy_m=[(5000.0, 5000.0), (5100.0, 5000.0)],
            node_class=["dead_end", "outfall_terminating"],
        )
        rec = got["points"][0]
        assert rec["nearest_node_id"] == 1 and rec["nearest_distance_m"] == 40.0
        assert got["gt_matched_nodes"] == [1], "only the NEAREST matched node carries volume"


class TestSuspiciousFlag:
    def test_suspicious_fires_above_threshold(self, tmp_path):
        pts = [{"id": "P1", "x_m": 5000.0, "y_m": 5000.0}]
        man = _gt_bundle(tmp_path, pts, extra_cols=True)
        got = attribute_ground_truth(
            [_ev(1, 15.0), _ev(2, 5.0)],
            man,
            radius_m=100.0,
            node_xy_m=[(5000.0, 5000.0), (10000.0, 10000.0)],
            node_class=["dead_end", "outfall_terminating"],
        )
        assert got["dead_end_share_of_gt_matched_returned_volume"] == 1.0
        assert got["suspicious"] is True and got["suspicious_state"] == "TRUE"

    def test_share_exactly_at_threshold_is_NOT_suspicious_strictly_greater_rule(self, tmp_path):

        pts = [
            {"id": "PA", "x_m": 5000.0, "y_m": 5000.0},
            {"id": "PB", "x_m": 6000.0, "y_m": 5000.0},
        ]
        man = _gt_bundle(tmp_path, pts, extra_cols=True)
        got = attribute_ground_truth(
            [_ev(1, 10.0), _ev(2, 10.0)],
            man,
            radius_m=100.0,
            node_xy_m=[(5000.0, 5000.0), (6000.0, 5000.0)],
            node_class=["dead_end", "outfall_terminating"],
            suspicious_deadend_share=0.5,
        )
        assert got["dead_end_share_of_gt_matched_returned_volume"] == 0.5
        assert got["suspicious"] is False and got["suspicious_state"] == "FALSE"

    def test_threshold_parameter_moves_the_flag(self, tmp_path):
        pts = [
            {"id": "PA", "x_m": 5000.0, "y_m": 5000.0},
            {"id": "PB", "x_m": 6000.0, "y_m": 5000.0},
        ]
        man = _gt_bundle(tmp_path, pts, extra_cols=True)
        events = [_ev(1, 55.0), _ev(2, 45.0)]
        kw = dict(
            radius_m=100.0,
            node_xy_m=[(5000.0, 5000.0), (6000.0, 5000.0)],
            node_class=["dead_end", "outfall_terminating"],
        )
        assert (
            attribute_ground_truth(events, man, suspicious_deadend_share=0.5, **kw)["suspicious"]
            is True
        )
        assert (
            attribute_ground_truth(events, man, suspicious_deadend_share=0.6, **kw)["suspicious"]
            is False
        )

    def test_shared_node_volume_counted_once_across_points(self, tmp_path):

        pts = [
            {"id": "A", "x_m": 5010.0, "y_m": 5000.0},
            {"id": "B", "x_m": 5000.0, "y_m": 5010.0},
            {"id": "C", "x_m": 4990.0, "y_m": 5000.0},
        ]
        man = _gt_bundle(tmp_path, pts, extra_cols=True)
        got = attribute_ground_truth(
            [_ev(1, 30.0), _ev(2, 10.0)],
            man,
            radius_m=100.0,
            node_xy_m=[(5000.0, 5000.0), (6000.0, 5000.0)],
            node_class=["dead_end", "outfall_terminating"],
        )
        assert got["n_gt_matched"] == 3
        assert got["gt_matched_nodes"] == [1]
        assert got["returned_m3_by_class_of_gt_matched_nodes"]["dead_end"] == 30.0
        assert got["dead_end_share_of_gt_matched_returned_volume"] == 1.0

    def test_nothing_matched_is_NOT_assessed_not_suspicious(self, tmp_path):
        pts = [{"id": "FAR", "x_m": 9000.0, "y_m": 9000.0}]
        man = _gt_bundle(tmp_path, pts, extra_cols=True)
        got = attribute_ground_truth(
            [_ev(1, 30.0)],
            man,
            radius_m=100.0,
            node_xy_m=[(5000.0, 5000.0)],
            node_class=["dead_end"],
        )
        assert got["n_gt_matched"] == 0
        assert got["dead_end_share_of_gt_matched_returned_volume"] is None
        assert got["suspicious"] is False and got["suspicious_state"] == "NOT_ASSESSED"

    def test_explicit_xy_without_classes_refuses_unsplit_number(self):
        with pytest.raises(DiagnosticsRefusal, match="node_class"):
            attribute_ground_truth([_ev(1)], Path("unused"), node_xy_m=[(0.0, 0.0)])

    def test_no_geometry_at_all_refuses(self, tmp_path):
        with pytest.raises(DiagnosticsRefusal, match="node geometry"):
            attribute_ground_truth([_ev(1)], _gt_bundle(tmp_path, [], extra_cols=True))


class TestGroundTruthJoinReality:
    def test_real_gt_manifest_yields_24_points_via_declared_pointer(self):
        pts, prov = _load_gt_points(GT_MANIFEST_PATH)
        assert len(pts) == 24
        assert prov["manifest_status"] == "completed"

    def test_reprojection_lands_in_the_utm43n_bengaluru_band_absolute_anchor(self, tmp_path):
        got = attribute_ground_truth(
            [_ev(1)],
            GT_MANIFEST_PATH,
            radius_m=1.0,
            node_xy_m=[(-1.0e9, -1.0e9)],
            node_class=["dead_end"],
        )
        assert got["n_gt_points"] == 24 and got["n_gt_matched"] == 0
        xs = [p["x_m"] for p in got["points"]]
        ys = [p["y_m"] for p in got["points"]]
        assert all(700_000 < x < 800_000 for x in xs), f"Bengaluru UTM43N eastings {xs[:3]}"
        assert all(1_400_000 < y < 1_460_000 for y in ys), f"Bengaluru UTM43N northings {ys[:3]}"
        assert got["radius_m_declared_assumption"] == 1.0

    def test_declared_points_count_disagreeing_with_csv_refuses(self, tmp_path):
        man = _gt_bundle(tmp_path, [{"id": "A", "lat": 12.9, "lon": 77.6}], points_count=24)
        with pytest.raises(DiagnosticsRefusal, match="points_count"):
            attribute_ground_truth([_ev(1)], man, node_xy_m=[(0.0, 0.0)], node_class=["dead_end"])


def _g2_inputs(steps: int, returned: float, *, split=True, sus=None) -> dict:
    d: dict = {
        "total_surcharging_steps": steps,
        "total_returned_m3": returned,
        "component_class_split": {"outfall_terminating": 55, "dead_end": 1666},
    }
    if not split:
        del d["component_class_split"]
    if sus is not None:
        d["gt_attribution"] = {"suspicious": sus}
    return d


class TestG2Verdict:
    def test_fail_on_empty_steps(self):
        verdict, reason = g2_verdict(_g2_inputs(0, 0.0))
        assert verdict == "fail"
        assert "NO NODE EVER SURCHARGED" in reason
        assert "SUSPICIOUS=NOT_ASSESSED" in reason
        assert "1666" in reason

    def test_fail_on_low_volume_below_contract_floor(self):
        verdict, reason = g2_verdict(_g2_inputs(12, 0.999))
        assert verdict == "fail"
        assert "0.999" in reason and f"{G2_MIN_RETURNED_M3_DEFAULT!r}" in reason

    def test_boundary_exactly_at_floor_passes(self):
        verdict, reason = g2_verdict(_g2_inputs(3, 1.0))
        assert verdict == "pass" and "SUSPICIOUS=NOT_ASSESSED" in reason

    def test_suspicious_state_appears_in_reason_both_ways(self):
        _, r_true = g2_verdict(_g2_inputs(5, 50.0, sus=True))
        assert "SUSPICIOUS=TRUE" in r_true
        _, r_false = g2_verdict(_g2_inputs(5, 50.0, sus=False))
        assert "SUSPICIOUS=FALSE" in r_false
        assert "BY CONSTRUCTION" in r_true, "D-A context rides along with the flag"

    def test_refuses_unsplit_input_invariant13_red_target(self):
        passing = _g2_inputs(5, 50.0, sus=False)
        assert g2_verdict(passing)[0] == "pass"
        with pytest.raises(DiagnosticsRefusal, match="split"):
            g2_verdict(_g2_inputs(5, 50.0, split=False))

    def test_accepts_richer_component_split_block_shape(self):
        rich = _g2_inputs(5, 50.0)
        rich["component_class_split"] = {
            "per_class": {
                "outfall_terminating": {"n_events": 1, "returned_m3": 50.0},
                "dead_end": {"n_events": 4, "returned_m3": 0.0},
            }
        }
        verdict, _ = g2_verdict(rich)
        assert verdict == "pass"

    def test_malformed_counters_aggregate_into_one_refusal(self):
        with pytest.raises(DiagnosticsRefusal) as ei:
            g2_verdict({"component_class_split": {"outfall_terminating": 55, "dead_end": 1666}})
        msg = str(ei.value)
        assert "total_surcharging_steps" in msg and "total_returned_m3" in msg


class TestEndToEndMiniPipeline:
    def test_pipeline_over_real_predicted_subset(self, tmp_path):
        fs = load_falsifier_set(FALSIFIER_PATH)
        subset_edges = fs.edges[:6]
        sub_ids = sorted({e["to_node"] for e in subset_edges})
        hit = sub_ids[:2]
        unflagged = [max(fs.predicted_node_ids) + 1, max(fs.predicted_node_ids) + 2]
        events = [
            _ev(n, 4.0 + i, first=1, last=5, head=2.0 + 0.1 * i)
            for i, n in enumerate(hit + unflagged)
        ]

        csv_path = tmp_path / "products" / "surcharge_events.csv"
        write_surcharge_events_csv(events, csv_path)
        rows_back = read_surcharge_events_csv(csv_path)
        assert len(rows_back) == len(events)

        cmp_block = compare_falsifier(
            rows_back, fs, gpkg_sha256_of_loaded_graph=fs.source_gpkg_sha256
        )
        assert cmp_block["predicted_nodes_surcharged"] == len(hit)
        assert cmp_block["predicted_nodes_surcharged_ids"] == sorted(hit)
        assert cmp_block["unflagged_surcharging_nodes"] == sorted(unflagged)
        flagged_down = sum(1 for e in subset_edges if e["to_node"] in set(hit))
        assert cmp_block["flagged_edges_whose_downstream_node_surcharged"] >= flagged_down
        assert cmp_block["flagged_edges_whose_downstream_node_surcharged"] <= 6
        assert cmp_block["source_gpkg_sha256_crosscheck"]["status"] == "match"

        assert len(cmp_block["flagged_edges_not_surcharged"]) >= 64

        g = _big_deadend_graph()
        split_block = component_split(rows_back, g)
        assert split_block["per_class"]["dead_end"]["n_events"] == len(rows_back)
        assert split_block["per_class"]["outfall_terminating"]["n_events"] == 0
        assert (
            split_block["headline_fractions"]["directed_node_count_split"]["counts"]["dead_end"]
            == g.num_nodes - 2
        ), "only toy nodes 1-2 reach the outfall; every predicted-range node is dead_end"

        man = _gt_bundle(
            tmp_path,
            [{"id": "SYN1", "x_m": 5000.0, "y_m": 5000.0}],
            extra_cols=True,
        )
        xy_sparse = {
            r["node_id"]: (
                (5000.0, 5000.0)
                if r["node_id"] == hit[0]
                else (1.4e7 + r["node_id"], 1.4e7 + r["node_id"])
            )
            for r in rows_back
        }
        attr = attribute_ground_truth(
            rows_back,
            man,
            radius_m=100.0,
            node_xy_m=xy_sparse,
            node_class={nid: "dead_end" for nid in xy_sparse},
        )
        assert attr["n_gt_matched"] == 1
        assert attr["points"][0]["nearest_node_id"] == hit[0]
        assert attr["dead_end_share_of_gt_matched_returned_volume"] == 1.0
        assert attr["suspicious"] is True

        inputs = {
            "total_surcharging_steps": 5,
            "total_returned_m3": sum(r["total_returned_m3"] for r in rows_back),
            "component_class_split": {
                "outfall_terminating": g.component_class.count("outfall_terminating"),
                "dead_end": g.component_class.count("dead_end"),
            },
            "falsifier_comparison": cmp_block,
            "component_split_block": split_block,
            "gt_attribution": attr,
        }
        verdict, reason = g2_verdict(inputs)
        assert verdict == "pass"
        assert "SUSPICIOUS=TRUE" in reason

    def test_same_pipeline_with_zero_events_fails_G2_loudly(self, tmp_path):
        csv_path = tmp_path / "products" / "surcharge_events.csv"
        write_surcharge_events_csv([], csv_path)
        rows = read_surcharge_events_csv(csv_path)
        assert rows == []
        g = _big_deadend_graph()
        inputs = {
            "total_surcharging_steps": 0,
            "total_returned_m3": 0.0,
            "component_class_split": {
                "outfall_terminating": 2,
                "dead_end": g.num_nodes - 2,
            },
        }
        verdict, reason = g2_verdict(inputs)
        assert verdict == "fail" and "NO NODE EVER SURCHARGED" in reason


def _manifest_fixture(path: Path, **fields) -> Path:
    path.write_text(json.dumps(fields))
    return path


class TestScoreG2Cli:
    def test_no_surge_headline_exact_and_exit_nonzero(self, tmp_path):
        m = _manifest_fixture(
            tmp_path / "m.json",
            total_surcharging_steps=0,
            total_returned_m3=0.0,
            component_class_split={"outfall_terminating": 55, "dead_end": 1666},
        )
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 1
        assert r.output.splitlines()[0] == "G2 FAIL: NO NODE EVER SURCHARGED"

    def test_fail_low_volume_suspicious_true_exit_nonzero(self, tmp_path):
        m = _manifest_fixture(
            tmp_path / "m.json",
            total_surcharging_steps=9,
            total_returned_m3=0.42,
            component_class_split={"outfall_terminating": 55, "dead_end": 1666},
            gt_attribution={"suspicious": True},
        )
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 1
        first = r.output.splitlines()[0]
        assert first.startswith("G2 FAIL")
        assert "SUSPICIOUS=TRUE" in first
        assert "dead_end=1666" in first and "outfall_terminating=55" in first

    def test_pass_suspicious_false_exit_zero(self, tmp_path):
        m = _manifest_fixture(
            tmp_path / "m.json",
            total_surcharging_steps=120,
            total_returned_m3=51234.5,
            component_class_split={"outfall_terminating": 55, "dead_end": 1666},
            gt_attribution={"suspicious_state": "FALSE"},
        )
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 0, r.output
        first = r.output.splitlines()[0]
        assert first.startswith("G2 PASS")
        assert "SUSPICIOUS=FALSE" in first

    def test_unsplit_manifest_refused_nonzero(self, tmp_path):
        m = _manifest_fixture(
            tmp_path / "m.json",
            total_surcharging_steps=9,
            total_returned_m3=42.0,
        )
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 1
        assert "REFUSED" in r.output

    def test_non_coupled_manifest_refused_nonzero(self, tmp_path):
        m = _manifest_fixture(tmp_path / "m.json", status="completed", stage="something_else")
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 1
        assert "REFUSED" in r.output


class TestSuspiciousStateSurfaces:
    def test_unit_state_reader_accepts_both_block_locations(self):
        from jaladhar.coupling.diagnostics import _suspicious_state_of

        assert _suspicious_state_of({"gt_attribution": {"suspicious_state": "FALSE"}}) == "FALSE"
        assert (
            _suspicious_state_of(
                {"diagnostics": {"gt_attribution_and_suspicious": {"suspicious_state": "TRUE"}}}
            )
            == "TRUE"
        )
        assert _suspicious_state_of({}) == "NOT_ASSESSED"

        both = {
            "gt_attribution": {"suspicious_state": "FALSE"},
            "diagnostics": {"gt_attribution_and_suspicious": {"suspicious_state": "TRUE"}},
        }
        assert _suspicious_state_of(both) == "FALSE"

    def test_smoke_shaped_nested_block_scores_true_in_headline(self, tmp_path):
        m = _manifest_fixture(
            tmp_path / "m.json",
            total_surcharging_steps=120,
            total_returned_m3=51234.5,
            component_class_split={"outfall_terminating": 55, "dead_end": 1666},
            diagnostics={"gt_attribution_and_suspicious": {"suspicious_state": "TRUE"}},
        )
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 0, r.output
        first = r.output.splitlines()[0]
        assert first.startswith("G2 PASS")
        assert "SUSPICIOUS=TRUE" in first

    def test_not_assessed_on_surcharging_run_prints_explicit_warning(self, tmp_path):
        m = _manifest_fixture(
            tmp_path / "m.json",
            total_surcharging_steps=120,
            total_returned_m3=51234.5,
            component_class_split={"outfall_terminating": 55, "dead_end": 1666},
        )
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 0, r.output
        lines = r.output.splitlines()
        first = lines[0]
        assert first.startswith("G2 PASS") and "SUSPICIOUS=NOT_ASSESSED" in first
        assert any(
            line.startswith("WARNING: dead-end contamination UNASSESSED") for line in lines
        ), f"UNASSESSED warning missing from:\n{r.output}"

    def test_zero_step_run_getters_no_contamination_warning(self, tmp_path):
        m = _manifest_fixture(
            tmp_path / "m.json",
            total_surcharging_steps=0,
            total_returned_m3=0.0,
            component_class_split={"outfall_terminating": 55, "dead_end": 1666},
        )
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 1
        assert r.output.splitlines()[0] == "G2 FAIL: NO NODE EVER SURCHARGED"
        assert "UNASSESSED" not in r.output

    def test_dead_end_dominant_true_flag_carried_in_pass_headline(self, tmp_path):
        m = _manifest_fixture(
            tmp_path / "m.json",
            total_surcharging_steps=64,
            total_returned_m3=2048.0,
            component_class_split={"outfall_terminating": 55, "dead_end": 1666},
            gt_attribution={
                "suspicious_state": "TRUE",
                "suspicious": True,
                "dead_end_share_of_gt_matched_returned_volume": 0.97,
            },
        )
        r = RUNNER.invoke(app, ["score-g2", str(m)])
        assert r.exit_code == 0, r.output
        first = r.output.splitlines()[0]
        assert first.startswith("G2 PASS")
        assert "SUSPICIOUS=TRUE" in first


# V7 SCOPE — PARTIAL BY CONSTRUCTION: the toy graph below has 4 edges; the

# V5 MUTATION PROBE (recorded): swapping to_node -> from_node in
# ``responsible_node_of_edge`` MUST redden the near-dead-end-reach test. The

_EDGE_GEOMS = {
    11: LineString([(2000.0, 5000.0), (3000.0, 5000.0)]),
    12: LineString([(3000.0, 5000.0), (3000.0, 6000.0)]),
    13: LineString([(4000.0, 5000.0), (5000.0, 5000.0)]),
    14: LineString([(4500.0, 5500.0), (5000.0, 5000.0)]),
}
_RESP_TO_NODE = {11: 2, 12: 4, 13: 3, 14: 3}
_NODE_CLASS = {
    1: "outfall_terminating",
    2: "outfall_terminating",
    3: "dead_end",
    4: "outfall_terminating",
}
_EDGE_CRS = "EPSG:32643"


def _edge_events() -> list[dict]:
    return [_ev(3, 30.0)]


class TestAttributeGroundTruthEdges:
    def test_near_dead_end_edge_matches_with_responsible_to_node_share_one(self, tmp_path):
        man = _gt_bundle(tmp_path, [{"id": "P1", "x_m": 4950.0, "y_m": 4950.0}], extra_cols=True)
        got = attribute_ground_truth_edges(
            _edge_events(),
            man,
            radius_m=100.0,
            edge_geoms=_EDGE_GEOMS,
            responsible_node_of_edge=_RESP_TO_NODE,
            node_class=_NODE_CLASS,
            radius_basis="test basis string",
            edge_crs=_EDGE_CRS,
        )
        assert got["attribution_mode"] == "edge"
        assert got["radius_m_declared_assumption"] == 100.0
        assert got["radius_basis"] == "test basis string"
        rec = got["points"][0]
        assert rec["nearest_edge_id"] == 13
        assert rec["nearest_distance_m"] == pytest.approx(50.0)
        assert rec["responsible_node_id"] == 3, "responsible is the DOWNSTREAM node"
        assert rec["responsible_node_class"] == "dead_end"
        assert got["n_gt_matched"] == 1
        assert got["gt_responsible_nodes"] == [3]
        assert got["returned_m3_by_class_of_gt_responsible_nodes"]["dead_end"] == 30.0
        assert got["dead_end_share_of_gt_matched_returned_volume"] == 1.0
        assert got["suspicious"] is True and got["suspicious_state"] == "TRUE"

    def test_inclusive_boundary_at_exactly_radius_m(self, tmp_path):
        man = _gt_bundle(
            tmp_path, [{"id": "AT_100", "x_m": 5100.0, "y_m": 5000.0}], extra_cols=True
        )
        got = attribute_ground_truth_edges(
            _edge_events(),
            man,
            radius_m=100.0,
            edge_geoms=_EDGE_GEOMS,
            responsible_node_of_edge=_RESP_TO_NODE,
            node_class=_NODE_CLASS,
        )
        rec = got["points"][0]
        assert rec["nearest_distance_m"] == pytest.approx(100.0)
        assert rec["matched"] is True, "boundary is INCLUSIVE (d <= radius)"
        assert rec["responsible_node_id"] == 3

    def test_v5_mutation_probe_wrong_endpoint_swaps_all_three_observables(self, tmp_path):
        """(c) RED TARGET recorded in the section docstring: building the
        responsible map from from_node instead of to_node must redden test (a).
        Node 2 is outfall-TERMINATING and appears in NO event, so under the swap:
        n_gt_matched 1 -> 0, share 1.0 -> None, state TRUE -> NOT_ASSESSED."""
        man = _gt_bundle(tmp_path, [{"id": "P1", "x_m": 4950.0, "y_m": 4950.0}], extra_cols=True)
        mutated_resp = {
            eid: from_n
            for eid, (from_n, _t) in {
                11: (2, 4),
                12: (3, 4),
                13: (2, 3),
                14: (6, 3),
            }.items()
        }
        got = attribute_ground_truth_edges(
            _edge_events(),
            man,
            radius_m=100.0,
            edge_geoms=_EDGE_GEOMS,
            responsible_node_of_edge=mutated_resp,
            node_class=_NODE_CLASS,
        )
        assert got["n_gt_matched"] == 0, (
            "mutation probe: wrong-end attribution LOST the match "
            "(from_node 2 is unsurcharged) — test (a) would redden here"
        )
        assert got["dead_end_share_of_gt_matched_returned_volume"] is None
        assert got["suspicious"] is False and got["suspicious_state"] == "NOT_ASSESSED"

    def test_ties_break_to_lowest_edge_id(self, tmp_path):
        man = _gt_bundle(tmp_path, [{"id": "TIE", "x_m": 5000.0, "y_m": 5000.0}], extra_cols=True)
        got = attribute_ground_truth_edges(
            _edge_events(),
            man,
            radius_m=100.0,
            edge_geoms=_EDGE_GEOMS,
            responsible_node_of_edge=_RESP_TO_NODE,
            node_class=_NODE_CLASS,
        )
        assert got["points"][0]["nearest_edge_id"] == 13

    def test_zero_matches_is_not_assessed_but_records_nearest_honestly(self, tmp_path):
        man = _gt_bundle(tmp_path, [{"id": "FAR", "x_m": 90000.0, "y_m": 90000.0}], extra_cols=True)
        got = attribute_ground_truth_edges(
            _edge_events(),
            man,
            radius_m=100.0,
            edge_geoms=_EDGE_GEOMS,
            responsible_node_of_edge=_RESP_TO_NODE,
            node_class=_NODE_CLASS,
        )
        assert got["n_gt_matched"] == 0
        rec = got["points"][0]
        assert rec["matched"] is False
        assert (
            rec["nearest_edge_id"] == 13 and rec["responsible_node_id"] == 3
        ), "the nearest-edge join is recorded even when unmatched (honest provenance)"
        assert got["dead_end_share_of_gt_matched_returned_volume"] is None
        assert got["suspicious_state"] == "NOT_ASSESSED"

    def test_refusals_aggregate_expected_count_crs_geometry_and_absent_event_nodes(self, tmp_path):
        man = _gt_bundle(tmp_path, [{"id": "P1", "x_m": 4950.0, "y_m": 5000.0}], extra_cols=True)

        with pytest.raises(DiagnosticsRefusal, match="expected_counts.edges"):
            attribute_ground_truth_edges(
                _edge_events(),
                man,
                100.0,
                _EDGE_GEOMS,
                responsible_node_of_edge=_RESP_TO_NODE,
                expected_edge_count=len(_EDGE_GEOMS) + 1,
                node_class=_NODE_CLASS,
            )

        with pytest.raises(DiagnosticsRefusal, match="metre-projected"):
            attribute_ground_truth_edges(
                _edge_events(),
                man,
                100.0,
                _EDGE_GEOMS,
                responsible_node_of_edge=_RESP_TO_NODE,
                node_class=_NODE_CLASS,
                edge_crs="EPSG:4326",
            )

        with pytest.raises(DiagnosticsRefusal, match="geometry"):
            attribute_ground_truth_edges(
                _edge_events(),
                man,
                100.0,
                {**_EDGE_GEOMS, 15: None},
                responsible_node_of_edge={**_RESP_TO_NODE, 15: 3},
                node_class=_NODE_CLASS,
            )

        with pytest.raises(DiagnosticsRefusal, match="absent from"):
            attribute_ground_truth_edges(
                [_ev(99, 5.0)],
                man,
                100.0,
                _EDGE_GEOMS,
                responsible_node_of_edge=_RESP_TO_NODE,
                node_class=_NODE_CLASS,
            )

        with pytest.raises(DiagnosticsRefusal, match="class source"):
            attribute_ground_truth_edges(
                _edge_events(),
                man,
                100.0,
                _EDGE_GEOMS,
                responsible_node_of_edge=_RESP_TO_NODE,
            )

    def test_shared_responsible_node_volume_counted_once_across_points(self, tmp_path):
        pts = [
            {"id": "A", "x_m": 4950.0, "y_m": 4950.0},
            {"id": "B", "x_m": 5000.0, "y_m": 5050.0},
        ]
        man = _gt_bundle(tmp_path, pts, extra_cols=True)
        got = attribute_ground_truth_edges(
            _edge_events(),
            man,
            radius_m=100.0,
            edge_geoms=_EDGE_GEOMS,
            responsible_node_of_edge=_RESP_TO_NODE,
            node_class=_NODE_CLASS,
        )
        assert got["n_gt_matched"] == 2
        assert got["gt_responsible_nodes"] == [3], "both points join the SAME responsible node"
        assert (
            got["returned_m3_by_class_of_gt_responsible_nodes"]["dead_end"] == 30.0
        ), "node 3's returned volume counted ONCE despite two matching points"
        assert got["suspicious_state"] == "TRUE"


class TestLoadDrainEdgesGeoms:
    def test_real_gpkg_loads_full_id_keyed_maps(self):
        """The REAL WF-1 artefact loads all 1587 edges into id-keyed maps.

        V2 observables independent of the loader: the count is the DECLARED
        ``graph.expected_counts.edges`` read fresh from configs/coupling.yaml, and
        the endpoint literals for edge 601 (649 -> 1721) are typed from drain_nodes
        truth — the same truth the C1 CSV regression test asserts against."""
        geom_by_id, from_by_id, to_by_id, crs = load_drain_edges_geoms(
            REPO / "runs" / "drain_graph_build" / "drain_graph.gpkg"
        )
        declared = yaml.safe_load((REPO / "configs" / "coupling.yaml").read_text())
        n_declared = int(declared["graph"]["expected_counts"]["edges"])
        assert n_declared == 1587
        assert len(geom_by_id) == len(from_by_id) == len(to_by_id) == n_declared
        assert set(geom_by_id) == set(from_by_id) == set(to_by_id)
        assert all(
            g.geom_type == "LineString" for g in geom_by_id.values()
        ), "ruling D-GT: TRUE multi-vertex polylines only — no straight-segment proxy"
        assert from_by_id[601] == 649 and to_by_id[601] == 1721
        assert str(crs).startswith("EPSG:"), f"realized CRS {crs!r} unexpected"

    def test_geographic_crs_refused(self, tmp_path):
        import geopandas as gpd

        gdf = gpd.GeoDataFrame(
            {"edge_id": [1], "from_node": [2], "to_node": [3]},
            geometry=[LineString([(0.0, 0.0), (0.001, 0.001)])],
            crs="EPSG:4326",
        )
        p = tmp_path / "geographic.gpkg"
        gdf.to_file(p, layer="drain_edges", driver="GPKG")
        with pytest.raises(DiagnosticsRefusal, match="metre-projected"):
            load_drain_edges_geoms(p)

    def test_missing_gpkg_refused(self, tmp_path):
        with pytest.raises(DiagnosticsRefusal, match="not found"):
            load_drain_edges_geoms(tmp_path / "absent.gpkg")
