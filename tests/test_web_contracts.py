"Compact retained WF-6 coverage for archival batch C. Scopes are deliberately beside the checks (V7): product/validator checks use temporary schema-only fixtures; causal checks use the realised drain graph when present and otherwise exercise its honest missing-artifact state. V5 mutation records name the deliberate mutation and assert that it changes the observable. No fixture below represents rainfall, terrain, or a scientific result."  # noqa: E501

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import shapely.geometry as sgeom

from jaladhar.validation.depth_product_contract import (
    DepthProductContractError,
    sha256_file,
    validate_product_manifest,
)
from jaladhar.validation.segment_status import (
    build_exclusion_mask,
    build_segment_status_dataframe,
)
from jaladhar.web import drains as drains_mod
from jaladhar.web.app import DashboardStore
from jaladhar.web.drains import (
    NONE_MEASURED_STATEMENT,
    DrainSnapshotReader,
)
from jaladhar.web.forcing import ForcingUnavailable, inventory_granules
from jaladhar.web.intersections import derive_junction_nodes
from tests.helpers.fixture_products import fixture_repo as _fixture_repo

REPO = Path(__file__).resolve().parents[1]


def test_storage_exclusion_is_load_bearing_and_validator_refuses_dirty_tree(
    tmp_path: Path,
) -> None:
    "Scope: 1x5 producer fixture plus a complete temporary product manifest. V5 mutation: omitting ``excluded_mask`` must reproduce the contaminated flooded/90 cm state; the working path must instead remove storage cells. The validator's independent refusal also prevents an unrecorded dirty source tree from becoming a consumer contract."  # noqa: E501
    depth = np.array([[0.0, 0.0, 0.9, 0.9, 0.9]])
    road = np.ones((1, 5), dtype=np.int64)
    basin = np.array([[4, 4, 1, 1, 1]], dtype=np.uint8)
    lookup = __import__("pandas").DataFrame({"segment_id": [1]})

    # MUTATED path: deleting the mask plumbing restores storage-water flooding.
    contaminated = build_segment_status_dataframe(depth, road, lookup)
    assert contaminated.loc[0, "flood_status"] == "flooded"
    assert contaminated.loc[0, "band_high_cm"] == 90
    excluded = build_segment_status_dataframe(
        depth, road, lookup, excluded_mask=build_exclusion_mask(basin, {1})
    )
    assert excluded.loc[0, "flood_status"] == "not_flooded"
    assert (excluded.loc[0, "band_low_cm"], excluded.loc[0, "band_high_cm"]) == (0, 0)

    repo, _product_dir, _row = _fixture_repo(tmp_path)
    contract = repo / "configs/contracts/depth_product.json"
    manifest_path = repo / "runs/product/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["git_tree_clean"] = False
    manifest_path.write_text(json.dumps(manifest))
    rows = json.loads((manifest_path.parent / "products/segment_status.json").read_text())["rows"]
    with pytest.raises(DepthProductContractError, match="git_tree_clean"):
        validate_product_manifest(
            manifest,
            manifest_path,
            rows=rows,
            lookup_ids={1},
            repo_root=repo,
            contract_path=contract,
        )


def test_frozen_depth_product_reaches_dashboard_consumer(tmp_path: Path) -> None:
    "Scope: one temporary completed product, both dashboard twins and loader. V8 observable: the realized product is accepted by the independent consumer, has one snapshot, and retains the frozen no-data semantics."  # noqa: E501
    repo, product_dir, _ = _fixture_repo(tmp_path)
    store = DashboardStore(repo_root=repo, product=product_dir)
    assert store.status == "ready"
    assert len(store.snapshots) == 1
    assert store.state_payload()["leads"] == [0]
    assert store.snapshots[0].rows[0]["confidence"] == "no_data"


def test_gate_reference_hashes_current_bytes_and_refuses_missing(tmp_path: Path) -> None:
    "Scope: temporary product, gate report, and a synthetic series manifest. The served resolver hashes current report bytes, refuses an unbound/missing report, and never turns a stale reference into an accepted score. The historical annotation CLI is absent from this trimmed checkout; its additive-write behavior remains covered by the realized manifest contract."  # noqa: E501
    repo, product_dir, _ = _fixture_repo(tmp_path)
    product = product_dir / "segment_status.csv"
    report = tmp_path / "g1.json"
    report.write_text(
        json.dumps(
            {
                "g1": {"status": "FAIL"},
                "g3": {"status": "BLOCKED"},
                "input_sha256": {"depth_product": sha256_file(product)},
            }
        )
    )
    store = DashboardStore(repo_root=repo, product=product_dir)
    store.series = SimpleNamespace(
        manifest_path=repo / "runs/series/manifest.json",
        manifest={
            "event_maximum_gate_reference": {
                "gate_report_path": str(repo / "g1.json"),
                "gate_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                "scope": "event_maximum",
            }
        },
    )
    local_report = repo / "g1.json"
    local_report.write_bytes(report.read_bytes())
    store.series.manifest["event_maximum_gate_reference"]["gate_report_sha256"] = sha256_file(
        local_report
    )
    good = store._scientific_gate_summary()
    assert good["report_sha256"] == hashlib.sha256(local_report.read_bytes()).hexdigest()
    assert good["g1_status"] == "FAIL · event maximum"
    store.series.manifest["event_maximum_gate_reference"]["gate_report_sha256"] = "0" * 64
    stale = store._scientific_gate_summary()
    assert stale["reference_stale"] is True and stale["g1_status"] == "FAIL · event maximum"
    store.series.manifest["event_maximum_gate_reference"]["gate_report_path"] = "missing.json"
    refused = store._scientific_gate_summary()
    assert refused["status"] == "invalid" and refused["g1_status"] == "UNAVAILABLE"


def test_gate_reference_staleness_keeps_event_score(tmp_path: Path) -> None:
    "Scope: one fixture product and one temporary gate report. V5 mutation: zeroing the recorded reference hash must flip only ``reference_stale``; the realized FAIL score remains visible, never UNKNOWN."  # noqa: E501
    repo, product_dir, _ = _fixture_repo(tmp_path)
    report = repo / "runs/gate.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"g1": {"status": "FAIL"}, "g3": {"status": "BLOCKED"}}))
    store = DashboardStore(repo_root=repo, product=product_dir)
    store.series = SimpleNamespace(
        manifest_path=repo / "runs/series/manifest.json",
        manifest={
            "event_maximum_gate_reference": {
                "gate_report_path": str(report),
                "gate_report_sha256": sha256_file(report),
                "scope": "event_maximum",
                "event_product_sha256": "unused",
            }
        },
    )
    good = store._scientific_gate_summary()
    assert good["g1_status"] == "FAIL · event maximum" and not good["reference_stale"]
    ref = store.series.manifest["event_maximum_gate_reference"]
    ref["gate_report_sha256"] = "0" * 64
    stale = store._scientific_gate_summary()
    assert stale["reference_stale"] is True
    assert stale["g1_status"] == "FAIL · event maximum"


def test_causal_payload_hashes_manifest_and_stays_honest_or_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    "Scope: full realised drain graph, or a missing-artifact refusal state. V2 observable: the payload hash is recomputed from manifest bytes here. Predicted wording is explicitly not measured; absent graph bytes yield the designed empty state rather than invented nodes."  # noqa: E501
    if drains_mod.GPKG_PATH.is_file() and drains_mod.MANIFEST_PATH.is_file():
        reader = DrainSnapshotReader(geometry_provider=lambda _sid: [0.0, 0.0])
        payload = reader.causal_for_segment("wf6-compact")
        assert (
            payload["predicted_set"]["sha256"]
            == hashlib.sha256(drains_mod.MANIFEST_PATH.read_bytes()).hexdigest()
        )
        assert "not a measured event" in payload["predicted_set"]["basis"].lower()
        assert payload["status"] in {"predicted", "none_measured"}
        if payload["status"] == "predicted":
            assert "not a measured surcharge" in payload["statement"]
    else:
        missing = monkeypatch.setattr(drains_mod, "GPKG_PATH", Path("/absent/graph.gpkg"))
        del missing
        monkeypatch.setattr(drains_mod, "MANIFEST_PATH", Path("/absent/manifest.json"))
        payload = DrainSnapshotReader().causal_for_segment("missing")
        assert payload["status"] == "none_measured"
        assert payload["statement"] == NONE_MEASURED_STATEMENT


def test_missing_forcing_artifact_is_explicitly_blocked(tmp_path: Path) -> None:
    "Scope: empty local granule directory only; no rainfall is fabricated. V7 BLOCKED semantics: the independent inventory must refuse with a named acquisition path and a zero-file inventory, rather than emit a substitute."  # noqa: E501
    empty = tmp_path / "granules"
    empty.mkdir()
    with pytest.raises(ForcingUnavailable) as exc_info:
        inventory_granules(empty)
    assert exc_info.value.inventory["n_hdf5"] == 0
    assert "fetch_imerg" in exc_info.value.closes_how


def test_intersection_through_street_and_mutation() -> None:
    "Scope: three synthetic line strings through one junction. V5 mutation: setting pass-through tolerance to zero drops the 8 m crossing; the normal path must retain it as a distinct approach and blocked street."  # noqa: E501
    segments = [
        {
            "segment_id": 1,
            "name": "A",
            "geometry": sgeom.LineString([(-10, 0), (0, 0)]),
            "endpoints": [(-10, 0), (0, 0)],
        },
        {
            "segment_id": 2,
            "name": "B",
            "geometry": sgeom.LineString([(8, -10), (8, 10)]),
            "endpoints": [(8, -10), (8, 10)],
        },
        {
            "segment_id": 3,
            "name": "C",
            "geometry": sgeom.LineString([(0, 0), (5, 5)]),
            "endpoints": [(0, 0), (5, 5)],
        },
    ]
    status = {
        "rows": {
            1: {"flood_status": "not_flooded", "band_high_cm": 0, "band_low_cm": 0},
            2: {"flood_status": "flooded", "band_high_cm": 40, "band_low_cm": 30},
            3: {"flood_status": "not_flooded", "band_high_cm": 0, "band_low_cm": 0},
        },
        "lead_minutes": 90,
        "valid_time_utc": "2026-01-01T00:00:00Z",
    }
    healthy = derive_junction_nodes(segments, status)[0][0]
    mutated = derive_junction_nodes(segments, status, passthrough_tol_m=0.0)[0][0]
    assert healthy["approaches_total"] == 3 and healthy["approaches_blocked"] == 1
    assert mutated["approaches_total"] == 2
    assert healthy["passthrough_streets"] == ["B"]


def test_real_graph_proximity_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope: realized graph bytes; Shapely independently measures selected-edge proximity.
    V5 mutation: disabling the predicate must remove predicted resolution."""
    if not drains_mod.GPKG_PATH.is_file() or not drains_mod.MANIFEST_PATH.is_file():
        pytest.skip(
            "BLOCKED: drain graph bytes unavailable; restore runs/drain_graph_build to close"
        )
    reader = DrainSnapshotReader()
    snapshot = reader.get()
    if not snapshot.flagged_edges:
        pytest.skip(
            "BLOCKED: realized graph has no flagged capacity edges; rerun capacity stage to close"
        )
    edge_id = snapshot.flagged_edges[0][0]
    edge_index = int(np.flatnonzero(snapshot.edge_ids == edge_id)[0])
    start, end = snapshot.edge_offsets[edge_index : edge_index + 2]
    coords = snapshot.edge_coords[start * 2 : end * 2].reshape(-1, 2)
    point = coords[len(coords) // 2]
    independent_distance = sgeom.LineString(coords.tolist()).distance(sgeom.Point(point.tolist()))
    assert independent_distance <= drains_mod.CAUSAL_PROXIMITY_M

    resolved = DrainSnapshotReader(
        geometry_provider=lambda _segment_id: point.tolist()
    ).causal_for_segment("real-graph-case")
    assert resolved["status"] == "predicted"
    assert edge_id in resolved["matched_flagged_edge_ids"]

    monkeypatch.setattr(drains_mod, "_distance_within", lambda *_args, **_kwargs: False)
    mutated = DrainSnapshotReader(
        geometry_provider=lambda _segment_id: point.tolist()
    ).causal_for_segment("real-graph-case")
    assert mutated["status"] == "none_measured"
