"Discovery unification + candidate quarantine tests (WF-6 U0b). Scope statement (V7): these tests exercise the QUARANTINE and SELECTION rules over small schema-only fixture repositories -- specifically (a) flat twin candidates in recency order with one invalid candidate poisoned first, (b) a single-frame flat candidate resolved explicitly (regression guard), (c) cold frame-series discovery over runs/*/manifest.json including newest-valid-wins among several series and series-over-flat preference, and (d) the launcher's honest ROUTING_STATE derivation from a realized /health payload. They do NOT exercise the 176k-row full-scale twins (that is the execution-phase curl evidence on the real repository) nor browser rendering."  # noqa: E501

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from jaladhar.web.app import DashboardStore
from tests.helpers.fixture_products import base_repo as _base_repo
from tests.helpers.fixture_products import write_flat_run as _write_flat_run
from tests.helpers.fixture_products import write_json as _write_json
from tests.helpers.fixture_products import write_series_run as _write_series_run


def test_quarantine_skips_invalid_candidate_and_serves_the_valid_one(tmp_path: Path) -> None:
    "A poisoned NEWEST candidate is skipped; an older valid one still serves. Red (pre-change): the first failing twin aborted the whole store, so plain discovery answered status=error even though a valid candidate existed."  # noqa: E501

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

    repo = _base_repo(tmp_path)
    _write_flat_run(repo, "broken_only", end_time="2026-08-25T12:00:00+00:00", break_twin=True)

    store = DashboardStore(repo_root=repo)
    assert store.status == "error"
    skipped = store.state_payload()["skipped_candidates"]
    assert len(skipped) == 1
    assert "broken_only" in skipped[0]["candidate"]


def test_no_candidates_is_empty_not_error(tmp_path: Path) -> None:

    repo = _base_repo(tmp_path)
    store = DashboardStore(repo_root=repo)
    assert store.status == "empty"
    assert store.state_payload()["skipped_candidates"] == []


def test_explicit_single_candidate_resolves_exactly_as_before(tmp_path: Path) -> None:

    repo = _base_repo(tmp_path)
    run_dir = _write_flat_run(repo, "product", end_time="2026-08-25T08:00:00+00:00")
    store = DashboardStore(repo_root=repo, product=run_dir / "products")
    assert store.status == "ready"
    assert store.series is None
    payload = store.state_payload()
    assert payload["leads"] == [0]
    assert payload["snapshots"][0]["run_id"] == "product"
    assert payload["selection_order"]
    assert payload["skipped_candidates"] == []


def test_cold_discovery_selects_the_frame_series(tmp_path: Path) -> None:

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

    repo = _base_repo(tmp_path)
    _write_series_run(repo, "series_old", end_time="2026-08-25T10:00:00+00:00")
    _write_series_run(repo, "series_mid", end_time="2026-08-25T14:00:00+00:00")
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

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["uncoupled_baseline_admission_sha256"] = hashlib.sha256(
        admission.read_bytes()
    ).hexdigest()
    _write_json(run_dir / "manifest.json", manifest)
    assert _flat_probe_reason(twins, repo_root=repo) is None


def _launch_demo_module():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts" / "demo"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import launch_demo

    return launch_demo


def test_routing_state_lines_surface_realized_gate_honestly() -> None:

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
    "Cold discovery over the REAL runs/ tree ranks frames_v2 above frames_v1. Observable if broken: either the excluded v2 series fails validation (it would land in skipped with its realized reason) or recency inversion serves the contaminated v1 series to the dashboard."  # noqa: E501

    from jaladhar.web.series import discover_series_runs

    valid, skipped = discover_series_runs(LIVE_REPO)
    names = [candidate.run_dir.name for candidate in valid]
    assert names[0] == FRAMES_V2_ID
    assert FRAMES_V1_ID in names
    skipped_names = [name for name, _reason in skipped]
    assert FRAMES_V2_ID not in skipped_names
    assert FRAMES_V1_ID not in skipped_names
