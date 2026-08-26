"""Discovery unification + candidate quarantine tests (WF-6 U0b).

Scope statement (V7): these tests exercise the QUARANTINE and SELECTION rules
over small schema-only fixture repositories -- specifically (a) flat twin
candidates in recency order with one invalid candidate poisoned first, (b) a
single-frame flat candidate resolved explicitly (regression guard), (c) cold
frame-series discovery over runs/*/manifest.json including newest-valid-wins
among several series and series-over-flat preference, and (d) the launcher's
honest ROUTING_STATE derivation from a realized /health payload.  They do NOT
exercise the 176k-row full-scale twins (that is the execution-phase curl
evidence on the real repository) nor browser rendering.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from jaladhar.validation.depth_product_contract import sha256_file
from jaladhar.web.app import DashboardStore


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _base_repo(tmp_path: Path) -> Path:
    """Contract + lookup + declared-coupling upstream, mirroring the frozen schema."""

    repo = tmp_path / "repo"
    contract = {
        "version": "test-frozen",
        "schema_fields": {"flood_status": "PRIMARY_RULE D=0.15 m F=20% N=3-contiguous-cells"},
        "manifest_guarantees_producer_writes": [
            "depth_convention 'PRIMARY_RULE D0.15 F0.20 N3' + grid identity EPSG:32643 2x2 10m",
            "band_convention 'min_max_over_canonical_cells_floor_cm'",
        ],
        "no_data_segments_measured_split": {
            "total_canonical_absent": 1,
            "buffered_margin_only": 1,
            "fully_clipped_no_cells_anywhere": 0,
        },
    }
    _write_json(repo / "configs/contracts/depth_product.json", contract)

    lookup = repo / "data/interim/terrain/roads_segment_lookup.csv"
    lookup.parent.mkdir(parents=True, exist_ok=True)
    lookup.write_text("segment_id\n1\n", encoding="utf-8")

    inputs = {
        "depth_raster": repo / "data/test/depth.bin",
        "road_raster": repo / "data/test/roads.bin",
        "lookup_csv": lookup,
        "buffered_road_raster": repo / "data/test/roads_buffered.bin",
    }
    for name, path in inputs.items():
        if name != "lookup_csv":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode("ascii"))

    _write_json(
        repo / "runs/upstream/manifest.json",
        {"status": "completed", "git_sha": "upstream-sha", "coupling_enabled": False},
    )
    inputs["depth_source_manifest"] = repo / "runs/upstream/manifest.json"

    _BASE_REPO_INPUTS[str(repo)] = inputs
    return repo


_BASE_REPO_INPUTS: dict[str, dict[str, Path]] = {}


def _flat_row(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "segment_id": 1,
        "band_low_cm": 0,
        "band_high_cm": 0,
        "confidence": "no_data",
        "flood_status": "unknown",
        "valid_time_utc": "2026-08-24T00:00:00+00:00",
        "issue_time_utc": "2026-08-24T00:00:00+00:00",
        "forecast_lead_minutes": 0,
        "source_manifest_path": f"runs/{run_id}/manifest.json",
    }


def _write_flat_run(repo: Path, run_id: str, *, end_time: str, break_twin: bool = False) -> Path:
    """One contract-shaped flat run; ``break_twin`` omits the JSON half."""

    inputs = _BASE_REPO_INPUTS[str(repo)]
    run_dir = repo / "runs" / run_id
    product_dir = run_dir / "products"
    row = _flat_row(run_id)

    _write_json(
        product_dir / "segment_status.json",
        {"schema": "depth_product/test-frozen", "rows": [row]},
    )
    with (product_dir / "segment_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    relative_inputs = {name: path.relative_to(repo).as_posix() for name, path in inputs.items()}
    manifest = {
        "stage": "fixture_contract_reader",
        "status": "completed",
        "git_sha": "fixture-sha",
        "git_tree_clean": True,
        "run_id": run_id,
        "n_segments_expected": 1,
        "n_no_data_segments": 1,
        "no_data_segments_measured_split": {
            "buffered_margin_only": 1,
            "fully_clipped_no_cells_anywhere": 0,
        },
        "depth_convention": "PRIMARY_RULE D0.15 F0.20 N3",
        "band_convention": "min_max_over_canonical_cells_floor_cm",
        "coupling_enabled": False,
        "depth_field_state": "uncoupled_source_depth",
        "depth_source_manifest_path": "runs/upstream/manifest.json",
        "surcharge_events_path": None,
        "grid_identity": {"shape": [2, 2], "crs": "EPSG:32643", "resolution_m": 10.0},
        "input_paths": relative_inputs,
        "input_sha256": {name: sha256_file(path) for name, path in inputs.items()},
        "outputs": {
            "csv": f"runs/{run_id}/products/segment_status.csv",
            "json": f"runs/{run_id}/products/segment_status.json",
        },
        "start_time_iso": end_time,
        "end_time_iso": end_time,
    }
    if break_twin:
        (product_dir / "segment_status.json").unlink()
    else:
        manifest["output_sha256"] = {
            "csv": sha256_file(product_dir / "segment_status.csv"),
            "json": sha256_file(product_dir / "segment_status.json"),
        }
    _write_json(run_dir / "manifest.json", manifest)
    return run_dir


def _write_series_run(
    repo: Path,
    run_id: str,
    *,
    end_time: str,
    n_flooded: int = 0,
    break_count: bool = False,
) -> Path:
    """One frame-series run with a single 30-minute frame.

    ``break_count`` writes a manifest declaring two frames while carrying one:
    exactly the producer/consumer count mismatch acceptance must reject.
    """

    run_dir = repo / "runs" / run_id
    frame_dir = run_dir / "products" / "frames" / "t0000000"
    row = _flat_row(run_id)
    _write_json(
        frame_dir / "segment_status.json",
        {"schema": "depth_product/test-frozen", "rows": [row]},
    )
    with (frame_dir / "segment_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    manifest = {
        "stage": "fixture_frame_series",
        "status": "completed",
        "git_sha": "fixture-sha",
        "git_tree_clean": True,
        "run_id": run_id,
        "series_kind": "instantaneous_solver_frames",
        "cadence_seconds": 1800,
        "coupling_enabled": False,
        "forcing_kind": "historical_replay",
        "temporal_aggregation": "frame_series",
        "product_label": "UNCOUPLED BASELINE",
        "frame_count_expected": 2 if break_count else 1,
        "frame_count_written": 1,
        "start_time_iso": end_time,
        "end_time_iso": end_time,
        "frames": [
            {
                "tag": "t0000000",
                "valid_time_utc": "2022-09-04T00:00:00+00:00",
                "offset_seconds": 0,
                "n_flooded": n_flooded,
                "csv": f"runs/{run_id}/products/frames/t0000000/segment_status.csv",
                "json": f"runs/{run_id}/products/frames/t0000000/segment_status.json",
                "csv_sha256": sha256_file(frame_dir / "segment_status.csv"),
                "json_sha256": sha256_file(frame_dir / "segment_status.json"),
            }
        ],
    }
    _write_json(run_dir / "manifest.json", manifest)
    return run_dir


# --------------------------------------------------------------------- quarantine


def test_quarantine_skips_invalid_candidate_and_serves_the_valid_one(tmp_path: Path) -> None:
    """A poisoned NEWEST candidate is skipped; an older valid one still serves.

    Red (pre-change): the first failing twin aborted the whole store, so plain
    discovery answered status=error even though a valid candidate existed.
    """

    repo = _base_repo(tmp_path)
    _write_flat_run(repo, "broken_newer", end_time="2026-08-25T12:00:00+00:00", break_twin=True)
    _write_flat_run(repo, "valid_older", end_time="2026-08-25T08:00:00+00:00")

    store = DashboardStore(repo_root=repo)
    assert store.status == "ready"
    assert [snapshot.run_id for snapshot in store.snapshots] == ["valid_older"]
    payload = store.state_payload()
    skipped = payload["skipped_candidates"]
    assert len(skipped) == 1
    assert "broken_newer" in skipped[0]["candidate"]
    assert "twin" in skipped[0]["reason"].lower()


def test_all_candidates_invalid_is_error_with_reasons(tmp_path: Path) -> None:
    """Zero validated candidates AND no series => status=error, reasons collected."""

    repo = _base_repo(tmp_path)
    _write_flat_run(repo, "broken_only", end_time="2026-08-25T12:00:00+00:00", break_twin=True)

    store = DashboardStore(repo_root=repo)
    assert store.status == "error"
    skipped = store.state_payload()["skipped_candidates"]
    assert len(skipped) == 1
    assert "broken_only" in skipped[0]["candidate"]


def test_no_candidates_is_empty_not_error(tmp_path: Path) -> None:
    """A fresh repository keeps the designed empty state (zero features generated)."""

    repo = _base_repo(tmp_path)
    store = DashboardStore(repo_root=repo)
    assert store.status == "empty"
    assert store.state_payload()["skipped_candidates"] == []


def test_explicit_single_candidate_resolves_exactly_as_before(tmp_path: Path) -> None:
    """Single-frame behaviour regresses not at all: explicit product => one lead-0 snapshot."""

    repo = _base_repo(tmp_path)
    run_dir = _write_flat_run(repo, "product", end_time="2026-08-25T08:00:00+00:00")
    store = DashboardStore(repo_root=repo, product=run_dir / "products")
    assert store.status == "ready"
    assert store.series is None
    payload = store.state_payload()
    assert payload["leads"] == [0]
    assert payload["snapshots"][0]["run_id"] == "product"
    assert payload["selection_order"]
    # An explicit product enumerates no discovery candidates beyond itself.
    assert payload["skipped_candidates"] == []


# ------------------------------------------------------- frame-series discovery


def test_cold_discovery_selects_the_frame_series(tmp_path: Path) -> None:
    """Plain discovery (no --product) resolves a declaring frame-series run."""

    repo = _base_repo(tmp_path)
    _write_series_run(repo, "frames_v1", end_time="2026-08-25T16:00:00+00:00", n_flooded=0)

    store = DashboardStore(repo_root=repo)
    assert store.series is not None
    assert store.status == "ready"
    meta = store.series.meta_payload()
    assert meta["run_id"] == "frames_v1"
    assert meta["n_frames"] == 1
    assert meta["frames"][0]["n_flooded"] == 0


def test_newest_valid_series_wins_and_broken_newer_is_reported(tmp_path: Path) -> None:
    """Ordering is newest VALID first; an invalid newer series is a named skip."""

    repo = _base_repo(tmp_path)
    _write_series_run(repo, "series_old", end_time="2026-08-25T10:00:00+00:00")
    _write_series_run(repo, "series_mid", end_time="2026-08-25T14:00:00+00:00")
    # Newest declares a frame count it does not realize: rejected, never selected.
    _write_series_run(
        repo, "series_newest_broken", end_time="2026-08-25T16:00:00+00:00", break_count=True
    )

    store = DashboardStore(repo_root=repo)
    assert store.series is not None
    assert store.series.meta_payload()["run_id"] == "series_mid"

    from jaladhar.web.series import discover_series_runs

    valid, skipped = discover_series_runs(repo)
    assert [candidate.run_dir.name for candidate in valid] == ["series_mid", "series_old"]
    assert any(
        name == "series_newest_broken" and "frame count" in reason for name, reason in skipped
    )


def test_series_preferred_over_flat_when_both_valid(tmp_path: Path) -> None:
    """Documented selection order: a valid frame series outranks a valid flat twin."""

    repo = _base_repo(tmp_path)
    _write_series_run(repo, "frames_v1", end_time="2026-08-25T16:00:00+00:00")
    _write_flat_run(repo, "flat_valid", end_time="2026-08-26T08:00:00+00:00")

    store = DashboardStore(repo_root=repo)
    assert store.series is not None
    assert store.series.meta_payload()["run_id"] == "frames_v1"
    payload = store.state_payload()
    flat_skips = [
        entry
        for entry in payload["skipped_candidates"]
        if entry["kind"] == "flat_twin" and "flat_valid" in entry["candidate"]
    ]
    assert flat_skips, "the not-selected flat candidate must be recorded with the selection order"


def test_stale_admission_probe_names_the_defect(tmp_path: Path) -> None:
    """The cheap series-mode probe reproduces the contract's own hash comparison."""

    from jaladhar.web.app import _flat_probe_reason

    repo = _base_repo(tmp_path)
    run_dir = repo / "runs" / "stale_admission"
    products = run_dir / "products"
    products.mkdir(parents=True)
    (products / "segment_status.csv").write_text("segment_id\n1\n", encoding="utf-8")
    (products / "segment_status.json").write_text("[]", encoding="utf-8")
    admission = repo / "data/curation/admission.json"
    _write_json(admission, {"decision": "ADMITTED"})
    _write_json(
        run_dir / "manifest.json",
        {
            "status": "completed",
            "uncoupled_baseline_admission_path": "data/curation/admission.json",
            "uncoupled_baseline_admission_sha256": "0" * 64,
        },
    )
    twins = {".csv": products / "segment_status.csv", ".json": products / "segment_status.json"}
    reason = _flat_probe_reason(twins, repo_root=repo)
    assert reason is not None and "admission" in reason.lower()

    # Matching bytes produce no disqualifier (validity itself is not claimed).
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["uncoupled_baseline_admission_sha256"] = hashlib.sha256(
        admission.read_bytes()
    ).hexdigest()
    _write_json(run_dir / "manifest.json", manifest)
    assert _flat_probe_reason(twins, repo_root=repo) is None


# ------------------------------------------------------------------ launcher


def _launch_demo_module():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts" / "demo"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import launch_demo

    return launch_demo


def test_routing_state_lines_surface_realized_gate_honestly() -> None:
    """Degraded routing is reported from the payload, never faked to ready."""

    launch_demo = _launch_demo_module()
    degraded = launch_demo._routing_state_lines(
        {
            "status": "not_ready",
            "snap_policy": {"status": "unavailable_owner_gate", "reason": "no owner cap"},
            "vehicle_policies": {"status": "unavailable_external_unknown"},
            "depth_product": {"status": "available"},
            "scientific_readiness": {"status": "unavailable", "operational_ready": False},
        }
    )
    text = "\n".join(degraded)
    # Fix F2 (adjudicated 2026-08-26): the headline derives from the payload's
    # OVERALL status — never from one gate's status string; per-gate detail
    # stays on ROUTING_BLOCKERS.
    assert "ROUTING_STATE=not_ready" in text
    assert "snap_policy=unavailable_owner_gate" in text
    assert "vehicle_policy=unavailable_external_unknown" in text

    ready = launch_demo._routing_state_lines(
        {
            "status": "ready",
            "snap_policy": {"status": "available"},
            "vehicle_policies": {"status": "available"},
            "depth_product": {"status": "available"},
            "scientific_readiness": {"operational_ready": True},
        }
    )
    assert ready[0] == "ROUTING_STATE=ready"
    assert ready[1] == "ROUTING_BLOCKERS=none"


@pytest.mark.parametrize("kind,expected", [("frame_series", 1), ("event_maximum", 0)])
def test_depth_product_file_selection_follows_evidence_kind(kind: str, expected: int) -> None:
    """Flat evidence keeps products/segment_status.csv; frame evidence uses frame 0."""

    scripts_dir = Path(__file__).resolve().parents[1] / "scripts" / "demo"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from _demo_common import depth_product_file_for

    class FakeEvidence:
        pass

    evidence = FakeEvidence()
    evidence.kind = kind
    evidence.product_csv = Path("/repo/runs/r/products/frames/t0000000/segment_status.csv")
    evidence.run_dir = Path("/repo/runs/r")
    resolved = depth_product_file_for(evidence)
    if expected == 1:
        assert resolved == evidence.product_csv
    else:
        assert resolved == evidence.run_dir / "products" / "segment_status.csv"


# --------------------------------------------------- live-repository integration


LIVE_REPO = Path(__file__).resolve().parents[1]
FRAMES_V2_ID = "wf3_replay2_uncoupled_baseline_frames_v2"
FRAMES_V1_ID = "wf3_replay2_uncoupled_baseline_frames_v1"


@pytest.mark.skipif(
    not (
        (LIVE_REPO / "runs" / FRAMES_V2_ID / "manifest.json").exists()
        and (LIVE_REPO / "runs" / FRAMES_V1_ID / "manifest.json").exists()
    ),
    reason=(
        "BLOCKED: live-repo ordering needs both realized series manifests "
        f"(runs/{FRAMES_V2_ID} is emitted by the M1 producer run)"
    ),
)
def test_live_repo_discovers_frames_v2_first_frames_v1_second() -> None:
    """Cold discovery over the REAL runs/ tree ranks frames_v2 above frames_v1.

    Observable if broken: either the excluded v2 series fails validation (it would
    land in skipped with its realized reason) or recency inversion serves the
    contaminated v1 series to the dashboard.
    """

    from jaladhar.web.series import discover_series_runs

    valid, skipped = discover_series_runs(LIVE_REPO)
    names = [candidate.run_dir.name for candidate in valid]
    assert names[0] == FRAMES_V2_ID
    assert FRAMES_V1_ID in names
    skipped_names = [name for name, _reason in skipped]
    assert FRAMES_V2_ID not in skipped_names
    assert FRAMES_V1_ID not in skipped_names
