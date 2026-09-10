"""Enforces Verification Rules V1-V8:
- V1: Realized rasters, tables, and manifests verified directly against disk.
- V2: Independent observables named before writing checks.
- V5: Red-under-mutation demonstrated for invariant checks.
- V7: Check scope stated beside claimed scope.
- Invariants:
6. Realized Phase 3 segment report on disk integrity."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial import cKDTree

from jaladhar.provenance import DirtyTreeError, require_clean_git
from jaladhar.validation.segment_validation import (
    PRIMARY_RULE,
    _check_segment_output_collision,
    compute_contingency_and_null,
    evaluate_segments_from_mask,
    load_phase3_closing_water_budget,
    run_segment_validation_gate,
)

REPO = Path(__file__).resolve().parents[1]


def test_closing_water_budget_comes_from_realized_phase3_manifest(tmp_path: Path) -> None:
    """Changing a producer mass term must change the segment report account.
    V5 red mutation: restoring the former literal dictionary leaves drain at
    29,367,395.33 instead of the fixture's 20.0. Scope: one manifest seam."""
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "status": "completed",
                "results": {"simulation": {"sim_duration_hours": 2.0}},
                "mass_balance": {
                    "v_initial_m3": 0.0,
                    "rain_in_m3": 100.0,
                    "boundary_out_m3": 30.0,
                    "drain_out_m3": 20.0,
                    "infil_out_m3": 10.0,
                    "v_current_m3": 35.0,
                    "residual_m3": -5.0,
                    "relative_residual": 0.05,
                    "created_by_clamping_m3": 0.0,
                },
            }
        )
    )

    account = load_phase3_closing_water_budget(path)

    assert account["simulation_duration_hours"] == 2.0
    assert account["drain_sink_m3"] == 20.0
    assert account["drain_sink_pct"] == 20.0
    assert account["boundary_outfall_pct"] == 30.0
    assert account["mass_residual_m3"] == -5.0
    assert account["mass_residual_derivation_verified"] is True


def test_closing_water_budget_rejects_historical_manifest_without_mass_block(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"status": "completed"}))

    with pytest.raises(RuntimeError, match="rerun required"):
        load_phase3_closing_water_budget(path)


def test_closing_water_budget_rejects_inconsistent_mass_derivation(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "status": "completed",
                "mass_balance": {
                    "v_initial_m3": 0.0,
                    "rain_in_m3": 100.0,
                    "boundary_out_m3": 30.0,
                    "drain_out_m3": 20.0,
                    "infil_out_m3": 10.0,
                    "v_current_m3": 35.0,
                    "residual_m3": 0.0,
                    "relative_residual": 0.0,
                    "created_by_clamping_m3": 0.0,
                },
            }
        )
    )

    with pytest.raises(ValueError, match="does not match its stated terms"):
        load_phase3_closing_water_budget(path)


PHASE3_REPORT = REPO / "runs/segment_validation/baseline/segment_validation_report.json"
PHASE3_MANIFEST = REPO / "runs/phase3_validation/manifest.json"
SEGMENT_MANIFEST = REPO / "runs/segment_validation/baseline/manifest.json"
LEGACY_PHASE3_REPORT = REPO / "runs/phase3_validation/segment_validation_report.json"
LEGACY_SEGMENT_MANIFEST = REPO / "runs/phase3_validation/segment_validation_manifest.json"


def _require_artifact(path: Path, description: str) -> Path:
    if not path.exists():
        pytest.skip(f"BLOCKED: {description} is absent: {path}")
    return path


# Invariant 1: Primary Segment Flood Rule Definition & Execution


def test_primary_segment_flood_rule_invariants() -> None:
    """Invariant: Segment is flooded if fraction >= 20% OR contiguous cells >= 3 at D >= 0.15 m."""
    assert PRIMARY_RULE.depth_threshold_m == 0.15
    assert PRIMARY_RULE.fraction_threshold == 0.20
    assert PRIMARY_RULE.contiguous_cells == 3

    r_segs_a = np.full(10, 1, dtype=np.int32)
    road_mask_a = np.ones(10, dtype=bool)
    cell_mask_a = np.zeros(10, dtype=bool)
    cell_mask_a[0:2] = True
    adj_src_a = np.array([0, 1])
    adj_dst_a = np.array([1, 0])

    res_a = evaluate_segments_from_mask(
        cell_mask_a, road_mask_a, r_segs_a, adj_src_a, adj_dst_a, 0.20, 3
    )
    assert bool(res_a.loc[1, "is_flooded"]) is True
    assert res_a.loc[1, "flooded_cells"] == 2

    cell_mask_b = np.zeros(10, dtype=bool)
    cell_mask_b[0] = True
    res_b = evaluate_segments_from_mask(
        cell_mask_b, road_mask_a, r_segs_a, adj_src_a, adj_dst_a, 0.20, 3
    )
    assert bool(res_b.loc[1, "is_flooded"]) is False

    r_segs_c = np.full(100, 2, dtype=np.int32)
    road_mask_c = np.ones(100, dtype=bool)
    cell_mask_c = np.zeros(100, dtype=bool)
    cell_mask_c[10:13] = True
    adj_src_c = np.array([10, 11, 11, 12])
    adj_dst_c = np.array([11, 10, 12, 11])

    res_c = evaluate_segments_from_mask(
        cell_mask_c, road_mask_c, r_segs_c, adj_src_c, adj_dst_c, 0.20, 3
    )
    assert bool(res_c.loc[2, "is_flooded"]) is True
    assert res_c.loc[2, "max_contig"] == 3


# Invariant 2: Hand-Computed Contingency & Null Model Math


def test_hand_computed_segment_contingency_and_null() -> None:
    pred_mask = np.zeros(100, dtype=bool)
    obs_mask = np.zeros(100, dtype=bool)

    pred_mask[0:20] = True
    obs_mask[0:5] = True
    obs_mask[20:25] = True

    res = compute_contingency_and_null(pred_mask, obs_mask, n_draws=10000, seed=42)

    assert res.total_segments == 100
    assert res.predicted_flooded_segments == 20
    assert res.observed_flooded_segments == 10
    assert res.hits == 5
    assert res.misses == 5
    assert res.false_alarms == 15
    assert res.correct_negatives == 75

    assert pytest.approx(res.pod, rel=1e-4) == 0.50
    assert pytest.approx(res.far, rel=1e-4) == 0.75
    assert pytest.approx(res.csi, rel=1e-4) == 0.20

    assert pytest.approx(res.null_tp_mean, rel=2e-2) == 2.0
    assert pytest.approx(res.null_pod_mean, rel=2e-2) == 0.20
    assert pytest.approx(res.null_csi_mean, rel=2e-2) == 0.07432

    assert pytest.approx(res.lift_ratio_pod, rel=2e-2) == 2.50
    assert pytest.approx(res.lift_ratio_csi, rel=2e-2) == 0.20 / 0.07432


# Invariant 3: Degenerate Case Handling (NaN, Never Silent 0.0)


def test_degenerate_segment_scoring_nan_handling() -> None:
    """Invariant: Undefined metrics return float('nan') and never silently return 0.0."""
    pred_mask = np.zeros(50, dtype=bool)
    obs_mask = np.zeros(50, dtype=bool)

    # Nothing predicted, nothing observed
    res = compute_contingency_and_null(pred_mask, obs_mask)
    assert math.isnan(res.csi)
    assert math.isnan(res.pod)
    assert math.isnan(res.far)
    assert math.isnan(res.lift_ratio_csi)

    # Some predicted, none observed
    pred_mask[0:10] = True
    res2 = compute_contingency_and_null(pred_mask, obs_mask)
    assert res2.csi == 0.0
    assert math.isnan(res2.pod)
    assert res2.far == 1.0


# Invariant 4: Point Snapping Geometry


def test_point_snapping_geometry() -> None:
    """Invariant: Nearest road segment snapping finds the exact minimum Euclidean distance cell."""
    coords = np.array([[100.0, 200.0], [150.0, 250.0], [300.0, 400.0]])
    r_segs = np.array([101, 102, 103], dtype=np.int32)
    tree = cKDTree(coords)

    d, idx = tree.query([150.0, 250.0])
    assert d == 0.0
    assert r_segs[idx] == 102

    d2, idx2 = tree.query([103.0, 204.0])
    assert pytest.approx(d2, rel=1e-4) == 5.0
    assert r_segs[idx2] == 101


# Invariant 5: Stratification Partition Consistency


def test_historical_retired_density_report_is_not_current_producer_output() -> None:
    """Historical report scope: current producer explicitly retires density stratification."""
    report_path = _require_artifact(PHASE3_REPORT, "realized Phase 3 segment report")

    with open(report_path) as f:
        data = json.load(f)

    assert "density_stratification" not in data.get("stratification", {})
    assert data["retired_instruments"]["density_stratification"] == "retired_with_sar_pipeline"


# Invariant 6: Realized Manifest and Report Traceability (Rule 6)


def test_realized_segment_manifest_owns_the_rescore_report() -> None:
    """The segment runner owns a separate terminal manifest and report reference."""
    manifest_path = _require_artifact(SEGMENT_MANIFEST, "realized segment-rescore manifest")
    report_path = _require_artifact(PHASE3_REPORT, "realized Phase 3 segment report")

    manifest = json.loads(manifest_path.read_text())
    report = json.loads(report_path.read_text())
    assert manifest["stage"] == "phase3_segment_validation_rescore"
    assert manifest["status"] == "completed"
    assert len(manifest["git_sha"]) >= 7
    assert Path(manifest["report_path"]) == PHASE3_REPORT
    assert manifest["headline_results"] == report["headline_results"]
    assert manifest["verdict"] == report["verdict_for_adjudication"]


# Invariant 7: PU Labeling Defect Correction (Part A)


def test_positive_unlabeled_defect_relabeling() -> None:
    """Invariant: For PU datasets (BBMP, Ground Truth), CSI and FAR are marked uninterpretable.
    - CSI and FAR are null/None in serialized output with explicit PU defect rationale."""
    report_path = _require_artifact(PHASE3_REPORT, "realized Phase 3 segment report")
    with open(report_path) as f:
        report = json.load(f)

    bbmp = report["headline_results"]["bbmp_validation"]
    assert bbmp["csi_interpretable"] is False
    assert bbmp["far_interpretable"] is False
    assert bbmp["csi"] is None
    assert bbmp["far"] is None
    assert bbmp["pu_labeling_defect_rationale"]

    assert "sentinel1_change_validation" not in report["headline_results"]
    assert report["retired_instruments"]["sar"] == "retired_invalid_instrument"


# Invariant 8: Trunk Road Signal & Exact Statistical Tests (Part B)


def test_trunk_road_signal_is_realized_from_current_run() -> None:
    report_path = _require_artifact(PHASE3_REPORT, "realized Phase 3 segment report")
    with open(report_path) as f:
        report = json.load(f)

    trunk_eval = report["trunk_signal_evaluation"]
    trunk = trunk_eval["trunk_alone"]
    assert trunk["total_segments"] >= trunk["observed_flooded_segments"]
    assert trunk["total_segments"] >= trunk["predicted_flooded_segments"]
    assert trunk["hits"] <= min(
        trunk["observed_flooded_segments"], trunk["predicted_flooded_segments"]
    )
    assert trunk_eval["interpretation"]["status"] == "UNRESOLVED"


# Invariant 9: Underpass Co-Location Null Model & Joint Partition (Part C)


def test_underpass_partition_is_realized_and_colocation_is_not_fabricated() -> None:
    """Current register partition is measured; missing co-location instrument stays unresolved."""
    report_path = _require_artifact(PHASE3_REPORT, "realized Phase 3 segment report")
    with open(report_path) as f:
        report = json.load(f)

    up_eval = report["underpass_colocation_evaluation"]
    partitions = up_eval["marginal_dip_partition"]
    total = sum(
        partitions[key]["count"]
        for key in [
            "FLAT_RUNTHROUGH_dip_le_0.10m",
            "INTERMEDIATE_GAP_dip_0.10_to_0.30m",
            "SAG_DETECTED_dip_gt_0.30m",
        ]
    )
    assert total == up_eval["total_underpass_segments"]
    assert up_eval["marginal_dip_partition"]["dip_partition_sum_pct"] == pytest.approx(100.0)
    assert (
        sum(
            item["count"]
            for item in up_eval["mutually_exclusive_joint_matrix"].values()
            if isinstance(item, dict)
        )
        == total
    )
    assert up_eval["mutually_exclusive_joint_matrix"]["joint_sum_pct"] == pytest.approx(100.0)
    assert up_eval["co_location"]["status"] == "UNRESOLVED"


# Invariant 10: Ground-Truth Snap Distance Threshold (Part E)


def test_groundtruth_snap_distance_threshold() -> None:
    """Invariant: replay scoring applies both date and configured snap eligibility."""
    report_path = _require_artifact(PHASE3_REPORT, "realized Phase 3 segment report")
    with open(report_path) as f:
        report = json.load(f)

    snap_audit = report["snap_distance_audit"]
    assert snap_audit["configured_max_snap_distance_m"] > 0.0
    assert all(
        item["reason"] == "observed_date_outside_event_window"
        for item in snap_audit["date_rejections"]
    )
    assert all(
        item["excluded_by_snap_distance"]
        and not item["eligible_for_scoring"]
        and "snap_distance_exceeds_configured_max" in item["rejection_reasons"]
        for item in snap_audit["excluded_points"]
    )

    audit_eligible = snap_audit["comparison"]["eligible_positive_segments"]
    headline = report["headline_results"]["groundtruth_validation_date_and_snap_eligible"]
    assert headline["observed_flooded_segments"] == audit_eligible["N"]
    assert headline["hits"] == audit_eligible["TP"]
    assert headline["pod"] == round(audit_eligible["POD"], 6)
    assert headline["lift_ratio_pod"] == round(audit_eligible["lift_pod"], 4)


# Invariant 11: Phase 3 Gate Run Closing Water Budget (Part D)


def test_phase3_gate_closing_water_budget() -> None:
    """The rescore reports a budget derived from the completed producer manifest.
    Observable: changing a producer mass term changes the rescore account; a copied
    historic percentage cannot satisfy this producer-consumer seam."""
    report_path = _require_artifact(PHASE3_REPORT, "realized Phase 3 segment report")
    with open(report_path) as f:
        report = json.load(f)

    manifest_path = _require_artifact(PHASE3_MANIFEST, "completed Phase 3 manifest")
    expected = load_phase3_closing_water_budget(manifest_path)
    wb = report["phase3_gate_closing_water_budget"]
    assert wb == expected
    assert wb["mass_residual_derivation_verified"] is True


# Invariant 12: Segment input/output ownership split (Amendment 1)


def test_segment_output_dirs_must_be_distinct(tmp_path: Path) -> None:
    """polluted). Scope: config seam only, no I/O, no GPU."""
    with pytest.raises(KeyError, match="must be distinct"):
        _check_segment_output_collision(tmp_path / "phase3", tmp_path / "phase3")
    with pytest.raises(KeyError, match="must not be nested"):
        _check_segment_output_collision(tmp_path / "phase3", tmp_path / "phase3" / "sub")
    with pytest.raises(KeyError, match="must not be nested"):
        _check_segment_output_collision(tmp_path / "phase3" / "sub", tmp_path / "phase3")
    _check_segment_output_collision(tmp_path / "phase3", tmp_path / "segment_baseline")
    # Also via public API must fail fast before manifest
    with pytest.raises(KeyError, match="must be distinct"):
        run_segment_validation_gate(
            phase3_run_dir=tmp_path / "phase3",
            output_dir=tmp_path / "phase3",
            val_config_path=REPO / "configs/validation.yaml",
        )


# Invariant 13: Manifest failure lifecycle wraps every operation after start (Amendment 2)


def test_segment_manifest_failure_lifecycle_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V5 red: deliberate failure after manifest start must leave status failed, not running.
    Parent behavior (only final write wrapped) left manifest permanently running
    partial report labelled completed."""
    try:
        require_clean_git(REPO)
    except DirtyTreeError as exc:
        pytest.skip(
            "BLOCKED: lifecycle mutation requires a clean source tree so the provenance "
            f"guard does not fire before the deliberate post-start failure: {exc}"
        )

    phase3_run_dir = REPO / "runs/phase3_validation"
    output_dir = tmp_path / "segment_fail_test"
    # Ensure solver input exists (otherwise test is BLOCKED, not red)
    if not (phase3_run_dir / "manifest.json").exists():
        pytest.skip(f"BLOCKED: solver manifest absent: {phase3_run_dir / 'manifest.json'}")
    if not (phase3_run_dir / "depth_event_maximum.tif").exists():
        pytest.skip(f"BLOCKED: depth raster absent: {phase3_run_dir / 'depth_event_maximum.tif'}")

    # Monkeypatch to fail after manifest start but before report
    def fail_after_start(*args, **kwargs):
        raise RuntimeError("deliberate post-start failure for V5 red")

    monkeypatch.setattr(
        "jaladhar.validation.segment_validation.build_road_network_index", fail_after_start
    )

    with pytest.raises(RuntimeError, match="deliberate post-start failure"):
        run_segment_validation_gate(
            phase3_run_dir=phase3_run_dir,
            output_dir=output_dir,
            val_config_path=REPO / "configs/validation.yaml",
        )

    manifest_path = output_dir / "manifest.json"
    assert manifest_path.exists(), "manifest must exist even after failure (Rule 6)"
    data = json.loads(manifest_path.read_text())
    assert data["status"] == "failed", f"expected failed, got {data['status']}"
    assert "error" in data or "error_type" in data
    assert "end_time_iso" in data
    # No partial report may be labelled completed
    report_path = output_dir / "segment_validation_report.json"
    if report_path.exists():
        # If a report was written, it must not be considered completed via manifest
        assert data["status"] != "completed"
