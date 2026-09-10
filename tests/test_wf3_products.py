from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd
import pytest
import rasterio
from pyproj import CRS
from rasterio.transform import from_origin
from scipy.spatial import cKDTree

from jaladhar.provenance import DirtyTreeError
from jaladhar.routing.api import RoutingService
from jaladhar.routing.graph import RoadGraphError, RoadNetwork, SegmentMetadata
from jaladhar.routing.policy import (
    VehiclePolicy,
    VehiclePolicyCatalog,
    load_vehicle_policies,
)
from jaladhar.routing.product import (
    DepthProduct,
    DepthProductError,
    SegmentFloodState,
    load_depth_product,
)
from jaladhar.validation import segment_status
from jaladhar.validation.depth_product_contract import (
    DepthProductContractError,
    sha256_file,
    validate_product_manifest,
    validate_uncoupled_baseline_admission,
)
from jaladhar.web.app import DashboardError, DashboardStore
from tests.helpers.fixture_products import (
    fixture_repo as _fixture_repo,
)
from tests.helpers.fixture_products import (
    frame_series_repo as _frame_series_repo,
)
from tests.helpers.fixture_products import (
    write_json as _write_json,
)


def _score_module(name: str):
    script = Path(__file__).resolve().parents[1] / "scripts/score_g1.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_product(repo: Path, product_dir: Path):
    return load_depth_product(
        product_dir / "segment_status.csv",
        repo_root=repo,
        lookup_path=repo / "data/interim/terrain/roads_segment_lookup.csv",
        contract_path=repo / "configs/contracts/depth_product.json",
    )


def _demo_common():
    demo_dir = Path(__file__).resolve().parents[1] / "scripts/demo"
    sys.path.insert(0, str(demo_dir))
    try:
        import _demo_common
    finally:
        sys.path.remove(str(demo_dir))
    return _demo_common


def _launch_demo():
    demo_dir = Path(__file__).resolve().parents[1] / "scripts/demo"
    sys.path.insert(0, str(demo_dir))
    try:
        import launch_demo
    finally:
        sys.path.remove(str(demo_dir))
    return launch_demo


def _two_node_network(tmp_path: Path, payload: bytes, *, with_metadata: bool = False):
    graph = nx.MultiGraph()
    graph.add_edge(1, 2, segment_id=1, length_m=5.0)
    source = tmp_path / "roads.gpkg"
    lookup = tmp_path / "lookup.csv"
    source.write_bytes(payload)
    lookup.write_text("segment_id\n1\n", encoding="utf-8")
    return RoadNetwork(
        graph=graph,
        metadata={1: SegmentMetadata(1, 101, "primary", "road")} if with_metadata else {},
        node_coordinates={1: (0.0, 0.0), 2: (5.0, 0.0)},
        component_by_node={1: 1, 2: 1},
        source_path=source,
        lookup_path=lookup,
        crs=CRS.from_epsg(32643),
        edge_part_count=1,
    )


def _unavailable_policies() -> VehiclePolicyCatalog:
    return VehiclePolicyCatalog(path=None, policies={}, error="fixture policy unavailable")


def test_dashboard_treats_csv_json_as_one_twin(tmp_path: Path) -> None:
    "Mutation target: loading each twin as a snapshot would duplicate lead zero."

    repo, product_dir, _row = _fixture_repo(tmp_path)
    store = DashboardStore(repo_root=repo, product=product_dir)

    assert store.status == "ready"
    assert len(store.snapshots) == 1
    assert store.state_payload()["leads"] == [0]


def test_segment_producer_writes_consumer_compatible_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo, _product_dir, _row = _fixture_repo(tmp_path)
    upstream = repo / "runs/upstream/manifest.json"
    upstream_payload = json.loads(upstream.read_text(encoding="utf-8"))
    upstream_payload["git_sha"] = "upstream-sha"
    _write_json(upstream, upstream_payload)
    lookup = repo / "data/interim/terrain/roads_segment_lookup.csv"
    lookup.write_text("segment_id\n1\n2\n", encoding="utf-8")
    contract_path = repo / "configs/contracts/depth_product.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["no_data_segments_measured_split"] = {
        "total_canonical_absent": 1,
        "buffered_margin_only": 1,
        "fully_clipped_no_cells_anywhere": 0,
    }
    _write_json(contract_path, contract)

    transform = from_origin(500000.0, 1450000.0, 10.0, 10.0)
    raster_profile = {
        "driver": "GTiff",
        "height": 2,
        "width": 2,
        "count": 1,
        "transform": transform,
        "crs": "EPSG:32643",
    }
    depth_path = repo / "data/test/depth.tif"
    road_path = repo / "data/test/roads.tif"
    buffered_path = repo / "data/test/roads_buffered.tif"
    depth_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(depth_path, "w", dtype="float32", **raster_profile) as target:
        target.write(np.zeros((1, 2, 2), dtype=np.float32))
    with rasterio.open(road_path, "w", dtype="int32", **raster_profile) as target:
        target.write(np.array([[[1, 0], [0, 0]]], dtype=np.int32))
    with rasterio.open(buffered_path, "w", dtype="int32", **raster_profile) as target:
        target.write(np.array([[[1, 2], [0, 0]]], dtype=np.int32))

    monkeypatch.setattr(segment_status, "REPO", repo)
    monkeypatch.setattr(
        segment_status,
        "_git_provenance",
        lambda: {"git_sha": "product-sha", "git_tree_clean": True, "git_dirty_paths": []},
    )
    output_dir = repo / "runs/product-built"
    result = segment_status.build_segment_status_product(
        depth_raster=depth_path,
        road_raster=road_path,
        lookup_csv=lookup,
        output_dir=output_dir,
        source_manifest=upstream,
        valid_time_utc="2026-08-24T00:00:00+00:00",
        forecast_lead_minutes=0,
        coupling_enabled=False,
        buffered_road_raster=buffered_path,
        contract_path=contract_path,
    )

    assert result["manifest"]["status"] == "completed"
    assert result["manifest"]["run_id"] == "product-built"
    assert result["manifest"]["n_no_data_segments"] == 1
    product = load_depth_product(
        result["csv_path"], repo_root=repo, lookup_path=lookup, contract_path=contract_path
    )
    assert product.run_id == "product-built"
    assert product.rows[2].flood_status == "unknown"


def test_dashboard_rejects_twin_disagreement(tmp_path: Path) -> None:
    "V5 red: a valid but different JSON twin must redden the pair check."

    repo, product_dir, row = _fixture_repo(tmp_path)
    changed = {**row, "issue_time_utc": "2026-08-24T00:01:00+00:00"}
    _write_json(product_dir / "segment_status.json", {"rows": [changed]})
    manifest_path = repo / "runs/product/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_sha256"]["json"] = sha256_file(product_dir / "segment_status.json")
    _write_json(manifest_path, manifest)

    store = DashboardStore(repo_root=repo, product=product_dir)

    assert store.status == "error"
    assert "twins disagree" in str(store.error)


def test_routing_rejects_incomplete_manifest_and_lead_181(tmp_path: Path) -> None:
    "V5 red: missing Git provenance and an over-horizon lead both fail at load."

    repo, product_dir, row = _fixture_repo(tmp_path)
    manifest_path = repo / "runs/product/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("git_sha")
    _write_json(manifest_path, manifest)
    with pytest.raises(DepthProductError, match="git_sha"):
        _load_product(repo, product_dir)

    manifest["git_sha"] = "fixture-sha"
    _write_json(manifest_path, manifest)
    row["forecast_lead_minutes"] = 181
    with (product_dir / "segment_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(DepthProductError, match="0-180"):
        _load_product(repo, product_dir)


def test_demo_numeric_provenance_does_not_inherit_from_ancestor(tmp_path: Path) -> None:
    "V5 red: an arbitrary descendant number cannot borrow a root manifest path."

    repo, _product_dir, _row = _fixture_repo(tmp_path)
    demo_common = _demo_common()
    DemoError = demo_common.DemoError
    verify_json_number_provenance = demo_common.verify_json_number_provenance

    manifest = repo / "runs/product/manifest.json"
    payload = {
        "source_manifest_path": "runs/product/manifest.json",
        "nested": {"arbitrary_metric": 999},
    }
    with pytest.raises(DemoError, match="without its own manifest path"):
        verify_json_number_provenance(
            payload,
            repo_root=repo,
            expected_manifests=(manifest,),
        )


def test_demo_http_success_is_not_readiness() -> None:
    "V5 red: a 200 health payload with not_ready state must block launch."

    DemoError = _demo_common().DemoError
    _require_ready_api_health = _launch_demo()._require_ready_api_health

    with pytest.raises(DemoError, match="status='not_ready'"):
        _require_ready_api_health({"status": "not_ready"})


def test_g1_null_is_spatial_and_point_scoped() -> None:

    module = _score_module("wf3_score_g1")
    road_xy = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [10.0, 10.0]])
    score = module._spatial_null_score(
        flooded_by_id={1: True, 2: False},
        complaint_points_xy=np.array([[0.0, 0.0], [10.0, 10.0]]),
        observed_segment_ids=np.array([1, 2]),
        observed_within_snap_limit=np.array([True, True]),
        road_tree=cKDTree(road_xy),
        road_segment_ids=np.array([1, 2, 1, 2]),
        n_draws=20,
        seed=17,
        max_snap_distance_m=15.0,
    )

    assert score["fixed_denominator_points"] == 2
    assert score["coordinate_realized_points"] == 2
    assert score["within_snap_modeled_points"] == 2
    assert score["unique_complaint_segments"] == 2
    assert score["null"]["draws"] == 20
    assert "toroidal spatial translation" in score["null"]["method"]


def test_product_manifest_refuses_dirty_source_tree() -> None:
    "V5 red: dirty provenance must refuse instead of producing a non-reproducible SHA."

    original = getattr(segment_status, "require_clean_git", None)
    try:
        segment_status.require_clean_git = lambda _repo: (_ for _ in ()).throw(
            DirtyTreeError("deliberate dirty-tree mutation")
        )
        with pytest.raises(DirtyTreeError, match="deliberate dirty-tree mutation"):
            segment_status._git_provenance()
    finally:
        if original is None:
            delattr(segment_status, "require_clean_git")
        else:
            segment_status.require_clean_git = original


def test_manifest_config_paths_are_repository_relative() -> None:
    "V5 target: a clean-mirror run must not embed that mirror's absolute root."

    value = {
        "depth": segment_status.REPO / "runs/upstream/depth.tif",
        "nested": [segment_status.REPO / "data/source.csv"],
    }

    assert segment_status.manifest_config_value(value) == {
        "depth": "runs/upstream/depth.tif",
        "nested": ["data/source.csv"],
    }


def test_consumer_rejects_dirty_product_manifest(tmp_path: Path) -> None:
    "V5 red: a completed manifest cannot launder a dirty source tree."

    repo, product_dir, _row = _fixture_repo(tmp_path)
    manifest_path = repo / "runs/product/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["git_tree_clean"] = False
    _write_json(manifest_path, manifest)

    with pytest.raises(DepthProductError, match="git_tree_clean"):
        _load_product(repo, product_dir)


def test_product_bytes_are_bound_to_manifest_hashes(tmp_path: Path) -> None:
    "V5 red: coordinated twin edits after completion must redden an immutable hash."

    repo, product_dir, row = _fixture_repo(tmp_path)
    changed = {**row, "issue_time_utc": "2026-08-24T00:01:00+00:00"}
    _write_json(
        product_dir / "segment_status.json",
        {
            "schema": "depth_product/test-frozen",
            "manifest_path": "runs/product/manifest.json",
            "rows": [changed],
        },
    )
    with (product_dir / "segment_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(changed))
        writer.writeheader()
        writer.writerow(changed)

    with pytest.raises(DepthProductError, match="output_sha256"):
        _load_product(repo, product_dir)


def test_product_manifest_must_live_at_runs_run_id(tmp_path: Path) -> None:
    "V5 red: a same-named directory outside runs/ cannot satisfy the frozen path."

    repo, _product_dir, row = _fixture_repo(tmp_path)
    manifest = json.loads((repo / "runs/product/manifest.json").read_text(encoding="utf-8"))
    outside = repo / "elsewhere/product/manifest.json"

    with pytest.raises(DepthProductContractError, match=r"runs/<run_id>/manifest.json"):
        validate_product_manifest(
            manifest,
            outside,
            rows=[row],
            lookup_ids={1},
            repo_root=repo,
            contract_path=repo / "configs/contracts/depth_product.json",
        )


def test_coupled_product_requires_upstream_depth_output_binding(tmp_path: Path) -> None:
    "V5 red: coupling declarations cannot relabel an unrelated depth raster."

    repo, product_dir, _row = _fixture_repo(tmp_path)
    upstream_path = repo / "runs/upstream/manifest.json"
    upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
    upstream.update(
        {
            "coupling_enabled": True,
            "coupling_version": "fixture-coupling-v1",
            "legacy_drain_disabled": True,
            "graph_is_dag": True,
            "total_surcharging_steps": 1,
            "total_returned_m3": 1.0,
        }
    )
    _write_json(upstream_path, upstream)
    surcharge = repo / "runs/upstream/products/surcharge_events.csv"
    surcharge.parent.mkdir(parents=True, exist_ok=True)
    surcharge.write_text("node_id,total_returned_m3\n1,1.0\n", encoding="utf-8")

    manifest_path = repo / "runs/product/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "coupling_enabled": True,
            "depth_field_state": "post_couple_step_h_new",
            "surcharge_events_path": "runs/upstream/products/surcharge_events.csv",
        }
    )
    manifest["input_sha256"]["depth_source_manifest"] = sha256_file(upstream_path)
    _write_json(manifest_path, manifest)

    with pytest.raises(DepthProductError, match="depth raster output"):
        load_depth_product(
            product_dir / "segment_status.csv",
            repo_root=repo,
            lookup_path=repo / "data/interim/terrain/roads_segment_lookup.csv",
            contract_path=repo / "configs/contracts/depth_product.json",
        )


def test_dashboard_rejects_nonuniform_snapshot_timestamps(tmp_path: Path) -> None:
    "V5 red: snapshot-level metadata cannot come from an arbitrary first row."

    repo, product_dir, row = _fixture_repo(tmp_path)
    lookup = repo / "data/interim/terrain/roads_segment_lookup.csv"
    lookup.write_text("segment_id\n1\n2\n", encoding="utf-8")
    contract_path = repo / "configs/contracts/depth_product.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["no_data_segments_measured_split"] = {
        "total_canonical_absent": 2,
        "buffered_margin_only": 2,
        "fully_clipped_no_cells_anywhere": 0,
    }
    _write_json(contract_path, contract)
    second = {**row, "segment_id": 2, "issue_time_utc": "2026-08-24T00:01:00+00:00"}
    rows = [row, second]
    _write_json(product_dir / "segment_status.json", {"rows": rows})
    with (product_dir / "segment_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerows(rows)
    manifest_path = repo / "runs/product/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["n_segments_expected"] = 2
    manifest["n_no_data_segments"] = 2
    manifest["no_data_segments_measured_split"]["buffered_margin_only"] = 2
    manifest["input_sha256"]["lookup_csv"] = sha256_file(lookup)
    manifest["output_sha256"] = {
        "csv": sha256_file(product_dir / "segment_status.csv"),
        "json": sha256_file(product_dir / "segment_status.json"),
    }
    _write_json(manifest_path, manifest)

    store = DashboardStore(repo_root=repo, product=product_dir)
    assert store.status == "error"
    assert "metadata changes" in str(store.error)


def test_demo_rejects_csv_json_value_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    "V5 red: matching IDs and manifest paths do not prove twin value equality."

    repo, product_dir, row = _fixture_repo(tmp_path)
    demo_common = _demo_common()
    monkeypatch.setattr(demo_common, "REPO_ROOT", repo)
    monkeypatch.setattr(demo_common, "RUNS_ROOT", repo / "runs")
    monkeypatch.setattr(
        demo_common, "DEPTH_PRODUCT_CONTRACT", repo / "configs/contracts/depth_product.json"
    )
    changed = {**row, "issue_time_utc": "2026-08-24T00:01:00+00:00"}
    with (product_dir / "segment_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(changed))
        writer.writeheader()
        writer.writerow(changed)
    manifest_path = repo / "runs/product/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_sha256"]["csv"] = sha256_file(product_dir / "segment_status.csv")
    _write_json(manifest_path, manifest)

    with pytest.raises(demo_common.DemoError, match="twins disagree"):
        demo_common.validate_run_dir(repo / "runs/product", repo_root=repo)


def test_dashboard_http_success_is_not_readiness() -> None:
    "V5 red: an HTTP-successful dashboard empty state must block demo launch."

    DemoError = _demo_common().DemoError
    _require_ready_dashboard_state = _launch_demo()._require_ready_dashboard_state

    with pytest.raises(DemoError, match="status='empty'"):
        _require_ready_dashboard_state({"status": "empty"})


def test_route_coordinate_snap_has_required_limit(tmp_path: Path) -> None:
    "V5 red: a distant coordinate must be refused rather than snapped citywide."

    source = tmp_path / "roads.gpkg"
    lookup = tmp_path / "lookup.csv"
    source.write_bytes(b"realized-road-fixture")
    lookup.write_text("segment_id\n", encoding="utf-8")
    network = RoadNetwork(
        graph=nx.MultiGraph(),
        metadata={},
        node_coordinates={1: (0.0, 0.0)},
        component_by_node={1: 1},
        source_path=source,
        lookup_path=lookup,
        crs=CRS.from_epsg(32643),
        edge_part_count=0,
    )

    with pytest.raises(RoadGraphError, match="maximum snap distance"):
        network.snap_point([100.0, 100.0], max_distance_m=10.0)


def test_vehicle_policy_requires_local_hashed_source_evidence(tmp_path: Path) -> None:
    "V5 red: citation strings alone cannot make a threshold verified evidence."

    policy_path = tmp_path / "data/curation/policy.json"
    _write_json(
        policy_path,
        {
            "policies": {
                "emergency": {
                    "max_impassable_depth_cm": 30,
                    "citation": {"title": "plausible title", "locator": "plausible locator"},
                }
            }
        },
    )
    catalog = load_vehicle_policies(policy_path, repo_root=tmp_path)
    assert catalog.available is False
    assert "source_evidence" in str(catalog.error)


def test_g1_reduced_point_scope_cannot_pass() -> None:
    "V5 red: a green metric over 398 eligible points cannot close a 399-point gate."

    module = _score_module("wf3_score_g1_scope")

    verdict, reason = module._gate_verdict(
        {"fixed_denominator_points": 398, "hit_rate": 0.99, "null": {"lift": 9.0}},
        expected_point_count=399,
        hit_rate_threshold=0.60,
        lift_threshold=3.0,
    )
    assert verdict == "BLOCKED"
    assert "398/399" in reason


def test_g1_null_applies_same_snap_limit_and_fixed_denominator() -> None:
    "V5 red: dropping far null points changes the denominator and inflates null rates."

    module = _score_module("wf3_score_g1_snap")

    class AlwaysFarTree:
        data = np.array([[0.0, 0.0], [10.0, 10.0]])

        def query(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            return np.full(len(points), 100.0), np.zeros(len(points), dtype=np.int64)

    score = module._spatial_null_score(
        flooded_by_id={1: True},
        complaint_points_xy=np.array([[0.0, 0.0]]),
        observed_segment_ids=np.array([1]),
        observed_within_snap_limit=np.array([False]),
        road_tree=AlwaysFarTree(),
        road_segment_ids=np.array([1]),
        n_draws=5,
        seed=17,
        max_snap_distance_m=10.0,
        fixed_unscorable_count=1,
    )

    assert score["fixed_denominator_points"] == 2
    assert score["coordinate_realized_points"] == 1
    assert score["within_snap_modeled_points"] == 0
    assert score["outside_grid_fixed_misses"] == 1
    assert score["out_of_snap_fixed_misses"] == 1
    assert score["hits"] == 0
    assert score["null"]["mean_hit_rate"] == 0.0
    assert score["null"]["out_of_snap_points_range"] == [2, 2]


def test_g1_primary_status_mismatch_is_owner_blocker(tmp_path: Path) -> None:
    "V5 red: primary status may not diverge from the product/UI without adjudication."

    repo, product_dir, _row = _fixture_repo(tmp_path)
    product = _load_product(repo, product_dir)
    frame = pd.DataFrame(
        [
            {
                "segment_id": 1,
                "band_low_cm": 0,
                "band_high_cm": 0,
                "confidence": "modeled_direct",
                "flood_status": "not_flooded",
            }
        ]
    )
    module = _score_module("wf3_score_g1_convention")

    with pytest.raises(module.G1ScoringError, match="owner adjudication"):
        module._assert_product_matches_frame(product, frame, convention="local_max_3x3")


def test_g3_reports_local_max_and_exact_cell_as_distinct_observables() -> None:
    "V5 red: reusing one sampled depth for both conventions hides disagreement. Scope: one strict row on a 3x3 field. The containing cell is in band while one neighbor is above it, so exact-cell must count WITHIN and local-max ABOVE."  # noqa: E501
    module = _score_module("wf3_score_g3_conventions")
    point = module.StrictDepthPoint(
        point_id="GT_FIXTURE",
        location_name="fixture",
        observed_date="2022-09-05",
        band_low_m=0.4,
        band_high_m=0.6,
        x=1.0,
        y=1.0,
        row=1,
        col=1,
    )
    depth = np.array([[0.1, 0.8, 0.1], [0.1, 0.5, 0.1], [0.1, 0.1, 0.1]], dtype=np.float32)
    tree = cKDTree(np.array([[1.0, 1.0]]))
    road_ids = np.array([7], dtype=np.int64)

    exact = module._depth_band_score(
        points=[point],
        depth_m=depth,
        road_tree=tree,
        road_segment_ids=road_ids,
        max_snap_distance_m=5.0,
        local_max=False,
        in_band_threshold=0.4,
    )
    local = module._depth_band_score(
        points=[point],
        depth_m=depth,
        road_tree=tree,
        road_segment_ids=road_ids,
        max_snap_distance_m=5.0,
        local_max=True,
        in_band_threshold=0.4,
    )

    assert exact["within_band_count"] == 1
    assert exact["status"] == "RETROSPECTIVE_DIAGNOSTIC"
    assert exact["diagnostic_threshold_result"] == "PASS"
    assert local["above_band_count"] == 1
    assert local["status"] == "RETROSPECTIVE_DIAGNOSTIC"
    assert local["diagnostic_threshold_result"] == "FAIL"


def test_dashboard_real_payloads_pass_strict_numeric_provenance(tmp_path: Path) -> None:
    "V5 invariant: nested manifest numbers must own their provenance explicitly."

    repo, product_dir, _row = _fixture_repo(tmp_path)
    store = DashboardStore(repo_root=repo, product=product_dir)
    demo_dir = Path(__file__).resolve().parents[1] / "scripts/demo"
    sys.path.insert(0, str(demo_dir))
    try:
        from _demo_common import (
            verify_dashboard_state_provenance,
            verify_json_number_provenance,
        )
    finally:
        sys.path.remove(str(demo_dir))
    manifest = repo / "runs/product/manifest.json"

    assert (
        verify_dashboard_state_provenance(
            store.state_payload(), repo_root=repo, expected_manifests=(manifest,)
        )
        > 0
    )
    assert (
        verify_json_number_provenance(
            store.segments_payload(segment_id=1),
            repo_root=repo,
            expected_manifests=(manifest,),
        )
        > 0
    )


def test_routing_requires_server_snap_ceiling_and_rejects_caller_escalation(
    tmp_path: Path,
) -> None:
    "V5 invariant: the caller cannot define its own citywide snap policy."

    network = _two_node_network(tmp_path, b"route-fixture", with_metadata=True)
    unavailable = _unavailable_policies()
    service = RoutingService(network, None, "no product", unavailable, repo_root=tmp_path)

    assert service.health()["snap_policy"]["status"] == "unavailable_owner_gate"
    with pytest.raises(Exception, match="server-side maximum snap distance"):
        service.route(
            {
                "origin": [0.0, 0.0],
                "destination": [5.0, 0.0],
                "vehicle_class": "emergency",
                "forecast_lead_minutes": 0,
                "max_snap_distance_m": 2_000_000.0,
            }
        )


def test_route_response_uses_relative_multi_source_provenance(tmp_path: Path) -> None:
    "V5 red input: an absolute road path must redden the route-specific provenance check."

    repo, product_dir, row = _fixture_repo(tmp_path)
    road_path = repo / "data/interim/terrain/roads.gpkg"
    road_path.write_bytes(b"persisted-road-bytes")
    lookup_path = repo / "data/interim/terrain/roads_segment_lookup.csv"
    evidence_path = repo / "data/curation/vehicle-source.txt"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text("captured source evidence", encoding="utf-8")
    policy_path = repo / "data/curation/vehicle-policy.json"
    _write_json(
        policy_path,
        {
            "source_evidence": {
                "path": "data/curation/vehicle-source.txt",
                "sha256": sha256_file(evidence_path),
            },
            "policies": {
                "emergency": {
                    "max_impassable_depth_cm": 30,
                    "citation": {"title": "captured source", "locator": "local evidence"},
                }
            },
        },
    )
    manifest_path = repo / "runs/product/manifest.json"
    payload = {
        "status": "disconnected",
        "reason": "no_flood_safe_route",
        "origin": {"node_id": 1, "x_m": 0.0, "y_m": 0.0, "distance_m": 1.0},
        "destination": {"node_id": 2, "x_m": 1.0, "y_m": 1.0, "distance_m": 1.0},
        "vehicle_class": "emergency",
        "vehicle_limit_cm": 30,
        "max_snap_distance_m": 10.0,
        "lead_times": {"forecast_lead_minutes": 0},
        "provenance": {
            "road_graph": {
                "path": "data/interim/terrain/roads.gpkg",
                "sha256": sha256_file(road_path),
            },
            "road_lookup": {
                "path": "data/interim/terrain/roads_segment_lookup.csv",
                "sha256": sha256_file(lookup_path),
            },
            "depth_product": {
                "path": "runs/product/products/segment_status.csv",
                "sha256": sha256_file(product_dir / "segment_status.csv"),
                "manifest_path": "runs/product/manifest.json",
                "manifest_sha256": sha256_file(manifest_path),
            },
            "vehicle_policy": {
                "path": "data/curation/vehicle-policy.json",
                "sha256": sha256_file(policy_path),
                "source_evidence": {
                    "path": "data/curation/vehicle-source.txt",
                    "sha256": sha256_file(evidence_path),
                },
            },
        },
        "avoided_segments": [],
        "route": None,
    }
    demo_common = _demo_common()
    DemoError = demo_common.DemoError
    verify_route_response_provenance = demo_common.verify_route_response_provenance

    count = verify_route_response_provenance(
        payload,
        repo_root=repo,
        expected_manifest=manifest_path,
        expected_products=(product_dir / "segment_status.csv", product_dir / "segment_status.json"),
        expected_rows=(row,),
    )
    assert count > 0

    mutated = json.loads(json.dumps(payload))
    mutated["provenance"]["road_graph"]["path"] = str(road_path.resolve())
    with pytest.raises(DemoError, match="repository-relative"):
        verify_route_response_provenance(
            mutated,
            repo_root=repo,
            expected_manifest=manifest_path,
            expected_products=(
                product_dir / "segment_status.csv",
                product_dir / "segment_status.json",
            ),
            expected_rows=(row,),
        )


def test_road_graph_path_success_and_disconnection_are_distinct(tmp_path: Path) -> None:
    "Fixture scope: one 3-node component exercises safe and blocked path observables."

    graph = nx.MultiGraph()
    graph.add_edge(1, 2, segment_id=1, length_m=5.0)
    graph.add_edge(2, 3, segment_id=2, length_m=7.0)
    source = tmp_path / "roads.gpkg"
    lookup = tmp_path / "lookup.csv"
    source.write_bytes(b"route-fixture")
    lookup.write_text("segment_id\n1\n2\n", encoding="utf-8")
    network = RoadNetwork(
        graph=graph,
        metadata={},
        node_coordinates={1: (0.0, 0.0), 2: (5.0, 0.0), 3: (12.0, 0.0)},
        component_by_node={1: 1, 2: 1, 3: 1},
        source_path=source,
        lookup_path=lookup,
        crs=CRS.from_epsg(32643),
        edge_part_count=2,
    )

    realized = network.shortest_path(1, 3, set())
    assert realized is not None
    assert [edge[3]["segment_id"] for edge in realized[1]] == [1, 2]
    assert network.shortest_path(1, 3, {2}) is None


def test_routing_service_returns_no_safe_route_instead_of_flooded_baseline(
    tmp_path: Path,
) -> None:
    "V5 red: using the baseline path after safe-path failure returns water-crossing edges. Observable if broken: a same-component request whose only two edges exceed the cited vehicle limit returns ``status=ok`` or a non-null route. Scope: one three-node component with every connecting segment blocked."  # noqa: E501
    repo = tmp_path / "repo"
    road_path = repo / "data/interim/terrain/roads.gpkg"
    lookup_path = repo / "data/interim/terrain/roads_segment_lookup.csv"
    product_path = repo / "runs/product/products/segment_status.csv"
    manifest_path = repo / "runs/product/manifest.json"
    policy_path = repo / "data/curation/vehicle-policy.json"
    evidence_path = repo / "data/curation/vehicle-policy-source.txt"
    for path, payload in (
        (road_path, b"realized-road-graph"),
        (lookup_path, b"realized-road-lookup"),
        (product_path, b"realized-depth-product"),
        (manifest_path, b'{"status":"completed"}\n'),
        (policy_path, b"realized-policy"),
        (evidence_path, b"realized-policy-source"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    graph = nx.MultiGraph()
    graph.add_edge(1, 2, segment_id=1, length_m=5.0)
    graph.add_edge(2, 3, segment_id=2, length_m=7.0)
    metadata = {
        segment_id: SegmentMetadata(segment_id, 100 + segment_id, "primary", f"road-{segment_id}")
        for segment_id in (1, 2)
    }
    network = RoadNetwork(
        graph=graph,
        metadata=metadata,
        node_coordinates={1: (0.0, 0.0), 2: (5.0, 0.0), 3: (12.0, 0.0)},
        component_by_node={1: 1, 2: 1, 3: 1},
        source_path=road_path,
        lookup_path=lookup_path,
        crs=CRS.from_epsg(32643),
        edge_part_count=2,
    )
    rows = {
        segment_id: SegmentFloodState(
            segment_id=segment_id,
            band_low_cm=20,
            band_high_cm=25,
            confidence="modeled_direct",
            flood_status="flooded",
        )
        for segment_id in (1, 2)
    }
    now = datetime.now(UTC)
    product = DepthProduct(
        source_path=product_path,
        source_manifest_path=manifest_path,
        run_id="product",
        forecast_lead_minutes=0,
        valid_time_utc=now,
        issue_time_utc=now,
        rows=rows,
        manifest={},
        flood_band_threshold_cm=15,
    )
    policies = VehiclePolicyCatalog(
        path=policy_path,
        policies={
            "emergency": VehiclePolicy(
                vehicle_class="emergency",
                max_impassable_depth_cm=10,
                citation_title="captured source",
                citation_locator="local evidence",
            )
        },
        error=None,
        source_sha256=sha256_file(policy_path),
        evidence_path=evidence_path,
        evidence_sha256=sha256_file(evidence_path),
    )
    service = RoutingService(
        network,
        product,
        None,
        policies,
        repo_root=repo,
        server_max_snap_distance_m=10.0,
        scientific_readiness={
            "status": "fixture_pass",
            "operational_ready": True,
            "scope": "three-node synthetic topology only",
        },
    )

    response = service.route(
        {
            "origin": [0.0, 0.0],
            "destination": [12.0, 0.0],
            "vehicle_class": "emergency",
            "forecast_lead_minutes": 0,
            "max_snap_distance_m": 1.0,
        }
    )

    assert response["status"] == "disconnected"
    assert response["reason"] == "no_flood_safe_route"
    assert response["route"] is None
    assert {row["segment_id"] for row in response["avoided_segments"]} == {1, 2}


def test_uncoupled_admission_binds_realized_manifest_and_depth_bytes(tmp_path: Path) -> None:
    "V5 red: an admission that binds only the manifest leaves the depth bytes unbound. Observable if broken: swapping the depth raster beneath a recorded admission would pass validation, because nothing compared the admission's recorded ``depth_raster_sha256`` against realized bytes. Scope: one fixture upstream manifest without coupling declarations and one admission sidecar under data/curation; hash comparisons only, no scientific claim."  # noqa: E501

    repo = tmp_path / "repo"
    depth_path = repo / "runs/upstream/depth_event_maximum.tif"
    depth_path.parent.mkdir(parents=True, exist_ok=True)
    depth_path.write_bytes(b"realized-event-maximum-bytes")
    upstream = repo / "runs/upstream/manifest.json"
    _write_json(
        upstream,
        {
            "status": "completed",
            "git_sha": "upstream-sha",
            "stage": "phase3_uncalibrated_validation_gate",
            "event": {
                "start": "2022-09-04T00:00:00Z",
                "end": "2022-09-05T23:30:00Z",
            },
        },
    )
    admission = repo / "data/curation/admission.json"
    admission.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        admission,
        {
            "decision": "ADMITTED",
            "product_label": "UNCOUPLED BASELINE",
            "coupling_enabled": False,
            "source_manifest_sha256": sha256_file(upstream),
            "source_git_sha": "upstream-sha",
            "source_stage": "phase3_uncalibrated_validation_gate",
            "source_status": "completed",
            "depth_raster_path": "runs/upstream/depth_event_maximum.tif",
            "depth_raster_sha256": sha256_file(depth_path),
            "forcing_kind": "historical_replay",
            "temporal_aggregation": "event_maximum",
            "event_window_start_utc": "2022-09-04T00:00:00Z",
            "event_window_end_utc": "2022-09-05T23:30:00Z",
        },
    )

    view = validate_uncoupled_baseline_admission(admission, upstream, repo_root=repo)
    assert view["depth_raster_sha256"] == sha256_file(depth_path)
    assert view["source_manifest_sha256"] == sha256_file(upstream)

    realized_shape = json.loads(upstream.read_text(encoding="utf-8"))
    del realized_shape["event"]
    realized_shape["event_window"] = {
        "start": "2022-09-04T00:00:00Z",
        "end": "2022-09-05T23:30:00Z",
    }
    _write_json(upstream, realized_shape)
    admission_realized = json.loads(admission.read_text(encoding="utf-8"))
    admission_realized["source_manifest_sha256"] = sha256_file(upstream)
    _write_json(admission, admission_realized)
    view = validate_uncoupled_baseline_admission(admission, upstream, repo_root=repo)
    assert view["event_window_start_utc"] == "2022-09-04T00:00:00Z"

    tampered_depth = json.loads(admission.read_text(encoding="utf-8"))
    tampered_depth["depth_raster_sha256"] = "0" * 64
    _write_json(admission, tampered_depth)
    with pytest.raises(DepthProductContractError, match="depth_raster_sha256"):
        validate_uncoupled_baseline_admission(admission, upstream, repo_root=repo)

    rewritten = json.loads(admission.read_text(encoding="utf-8"))
    rewritten["depth_raster_sha256"] = sha256_file(depth_path)
    rewritten["source_manifest_sha256"] = "f" * 64
    _write_json(admission, rewritten)
    with pytest.raises(DepthProductContractError, match="source_manifest_sha256"):
        validate_uncoupled_baseline_admission(admission, upstream, repo_root=repo)


def test_dashboard_gate_report_binds_either_product_twin(tmp_path: Path) -> None:
    "V5 red: gate reports bind the CSV twin; a JSON-only check dead-ends the panel. Observable if broken: a report whose input_sha256.depth_product equals the CSV bytes renders scientific_gate status='invalid' even though the twins were already enforced row-identical at load. Scope: one fixture run + one hash-bound report."  # noqa: E501

    repo, product_dir, _row = _fixture_repo(tmp_path)
    report = repo / "runs/gates/g1_score.json"
    _write_json(
        report,
        {
            "g1": {"status": "FAIL"},
            "g3": {"status": "BLOCKED"},
            "input_sha256": {"depth_product": sha256_file(product_dir / "segment_status.csv")},
        },
    )
    store = DashboardStore(repo_root=repo, product=product_dir, gate_report=report)

    gate = store.state_payload()["scientific_gate"]
    assert gate["status"] == "loaded_not_accepted"
    assert gate["g1_status"] == "FAIL"
    assert gate["signed_g3_status"] == "BLOCKED"
    assert gate["bound_product_sha256"] == sha256_file(product_dir / "segment_status.csv")

    tampered = json.loads(report.read_text(encoding="utf-8"))
    tampered["input_sha256"]["depth_product"] = "0" * 64
    _write_json(report, tampered)
    store = DashboardStore(repo_root=repo, product=product_dir, gate_report=report)
    assert store.state_payload()["scientific_gate"]["status"] == "invalid"


def test_dashboard_offset_beyond_rows_is_typed_error(tmp_path: Path) -> None:
    "V5 red: an offset past the realized row count must not escape as IndexError."

    repo, product_dir, _row = _fixture_repo(tmp_path)
    store = DashboardStore(repo_root=repo, product=product_dir)

    with pytest.raises(DashboardError, match="query parameter offset"):
        store.segments_payload(offset=5)


def test_non_positive_server_snap_cap_refuses_like_missing(tmp_path: Path) -> None:
    "V5 red: a non-positive owner cap must refuse routes as unavailable_owner_gate. Observable if broken: cap=-5 falls through to the escalation branch and returns 400 snap_limit_exceeds_server_policy for every request instead of the 503 unavailable refusal that matches health(). Scope: one two-node fixture graph."  # noqa: E501

    network = _two_node_network(tmp_path, b"cap-fixture")
    unavailable = _unavailable_policies()
    service = RoutingService(
        network,
        None,
        "no product",
        unavailable,
        repo_root=tmp_path,
        server_max_snap_distance_m=-5.0,
    )

    assert service.health()["snap_policy"]["status"] == "unavailable_owner_gate"
    with pytest.raises(Exception, match="server-side maximum snap distance"):
        service.route(
            {
                "origin": [0.0, 0.0],
                "destination": [5.0, 0.0],
                "vehicle_class": "emergency",
                "forecast_lead_minutes": 0,
                "max_snap_distance_m": 1.0,
            }
        )


def test_demo_launcher_appends_owner_snap_cap() -> None:
    "V5 red: without the helper the launcher cannot forward the server snap cap."

    demo_dir = Path(__file__).resolve().parents[1] / "scripts/demo"
    sys.path.insert(0, str(demo_dir))
    try:
        from launch_demo import append_snap_cap
    finally:
        sys.path.remove(str(demo_dir))

    base = ["python", "-m", "jaladhar.routing.api", "serve"]
    assert append_snap_cap(base, None) == base
    extended = append_snap_cap(base, 110.0)
    assert extended[-2:] == ["--server-max-snap-distance-m", "110.0"]
    assert base == ["python", "-m", "jaladhar.routing.api", "serve"]


def test_frame_series_admission_binds_realized_frame_bytes(tmp_path: Path) -> None:
    "V5 red: an admission that skips per-frame hashes admits substituted frames."

    fx = _frame_series_repo(tmp_path)
    from jaladhar.validation.depth_product_contract import (
        validate_frame_series_admission,
    )

    view = validate_frame_series_admission(
        fx["frame_admission"], fx["upstream"], repo_root=fx["repo"]
    )
    assert view["frame_count"] == fx["n_frames"]
    assert view["product_label"] == "UNCOUPLED BASELINE"

    first = sorted(fx["series_dir"].glob("depth_t*s.tif"))[1]
    original_bytes = first.read_bytes()
    with rasterio.open(first, "r+") as target:
        data = target.read(1)
        data[0, 1] = 9.9
        target.write(data, 1)
    with pytest.raises(DepthProductContractError, match="sha256 differs"):
        validate_frame_series_admission(fx["frame_admission"], fx["upstream"], repo_root=fx["repo"])

    first.write_bytes(original_bytes)
    validate_frame_series_admission(fx["frame_admission"], fx["upstream"], repo_root=fx["repo"])

    # Undeclared extra raster must also refuse.
    extra = fx["series_dir"] / "depth_t9999999s.tif"
    extra.write_bytes(b"undeclared")
    try:
        with pytest.raises(DepthProductContractError, match="undeclared rasters"):
            validate_frame_series_admission(
                fx["frame_admission"], fx["upstream"], repo_root=fx["repo"]
            )
    finally:
        extra.unlink()


def test_multiframe_product_emits_per_frame_contract_twins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    "V5 red: collapsing frames to one timestamp/depth would hide the timeline. Observable if broken: every frame carries the first frame's valid time and the union-max depth, so bands stop varying across frames. Scope: 3-frame fixture series on a 2x2 grid; the event-max product is untouched."  # noqa: E501

    from jaladhar.validation import segment_status_frames

    fx = _frame_series_repo(tmp_path)
    monkeypatch.setattr(segment_status_frames, "REPO", fx["repo"])
    monkeypatch.setattr(
        segment_status_frames,
        "_git_provenance",
        lambda: {"git_sha": "frames-sha", "git_tree_clean": True, "git_dirty_paths": []},
    )
    output_dir = fx["repo"] / "runs/frames_v1"

    result = segment_status_frames.build_multiframe_product(
        source_manifest=fx["upstream"],
        output_dir=output_dir,
        road_raster=fx["road_path"],
        lookup_csv=fx["lookup"],
        buffered_road_raster=fx["buffered_path"],
        frame_admission=fx["frame_admission"],
        contract_path=fx["contract_path"],
        baseline_admission=fx["baseline_admission"],
    )
    manifest = result["manifest"]
    assert manifest["status"] == "completed"
    assert manifest["frame_count_written"] == fx["n_frames"]
    assert manifest["demo_fallback"] is False
    assert manifest["temporal_aggregation"] == "frame_series"
    assert manifest["product_label"] == "UNCOUPLED BASELINE"
    assert "products/frames/<tag>" in manifest["artifact_location_deviation"]

    times: list[str] = []
    band_sequences: list[tuple[int, int]] = []
    for entry in manifest["frames"]:
        payload = json.loads((fx["repo"] / entry["json"]).read_text(encoding="utf-8"))
        rows = {row["segment_id"]: row for row in payload["rows"]}
        assert set(rows) == {1, 2}
        assert rows[2]["confidence"] == "no_data"
        assert rows[2]["band_low_cm"] == rows[2]["band_high_cm"] == 0
        assert rows[2]["flood_status"] == "unknown"
        assert payload["frame"]["valid_time_utc"] == entry["valid_time_utc"]
        times.append(entry["valid_time_utc"])
        band_sequences.append((rows[1]["band_low_cm"], rows[1]["band_high_cm"]))
        assert sha256_file(fx["repo"] / entry["csv"]) == entry["csv_sha256"]

    parsed = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in times]
    assert all(b - a == timedelta(seconds=1800) for a, b in zip(parsed, parsed[1:], strict=False))
    assert band_sequences[0] == (0, 0)
    assert band_sequences[1][1] >= 15
    assert band_sequences[2][1] < 15
    assert len(set(band_sequences)) == 3


def test_g1_metric_framing_discloses_point_vs_segment_anchor() -> None:
    "V5 red: a buried anchor mismatch would let one metric silently pass for another. Observable if broken: the framing block would omit the point-mediated anchor or misstate the hit difference (239 point-mediated at 0.10 m vs 182 segment-mediated = 57 hits of pure metric difference on the same 399-point denominator). Scope: pure derivation from a fixture sweep; no rasters."  # noqa: E501

    module = _score_module("wf3_score_g1_framing")
    source_payload = {
        "results": {
            "bbmp_scoring": {
                "sweep": [
                    {"threshold_m": 0.05, "total_points": 399, "hits": 278},
                    {
                        "threshold_m": 0.1,
                        "total_points": 399,
                        "hits": 239,
                        "hit_rate_pod": 0.599,
                    },
                ]
            }
        }
    }

    framing = module._metric_framing_block(
        source_payload,
        segment_hits=182,
        fixed_denominator_points=399,
        source_manifest_relative_path="runs/goal_d_replay2_final_config/manifest.json",
    )

    assert framing["status"] == "DISCLOSED"
    assert framing["owner_acknowledged_threshold_anchor_error"] is True
    assert framing["threshold_does_not_move"] is True
    assert framing["point_mediated"]["hits"] == 239
    assert framing["segment_mediated_this_report"]["hits"] == 182
    assert framing["metric_difference_hits"] == 57

    mutated = json.loads(json.dumps(source_payload))
    del mutated["results"]["bbmp_scoring"]
    missing = module._metric_framing_block(
        mutated,
        segment_hits=182,
        fixed_denominator_points=399,
        source_manifest_relative_path="x",
    )
    assert missing["status"] == "unavailable"


def test_segment_product_failure_updates_started_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    "V5 red mutation: a post-start raster failure must leave status=failed, never running."
    output_dir = tmp_path / "repo/runs/failure"
    repo = tmp_path / "repo"
    source_manifest = repo / "runs/upstream/manifest.json"
    depth = repo / "data/depth.tif"
    road = repo / "data/roads.tif"
    lookup = repo / "data/lookup.csv"
    buffered = repo / "data/buffered.tif"
    for path in (source_manifest, depth, road, lookup, buffered):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    monkeypatch.setattr(segment_status, "REPO", repo)
    monkeypatch.setattr(
        segment_status,
        "_resolve_product_config",
        lambda **_kwargs: {
            "depth_raster": depth,
            "road_raster": road,
            "lookup_csv": lookup,
            "output_dir": output_dir,
            "source_manifest": source_manifest,
            "buffered_road_raster": buffered,
            "contract_path": repo / "contract.json",
            "valid_time_utc": "2026-08-24T00:00:00+00:00",
            "forecast_lead_minutes": 0,
            "coupling_enabled": False,
            "declared_coupling": False,
            "admission_view": None,
            "product_label": None,
            "depth_output_binding": None,
            "depth_field_state": "uncoupled_source_depth",
            "surcharge_path": None,
            "source_payload": {},
        },
    )
    monkeypatch.setattr(
        segment_status,
        "_git_provenance",
        lambda: {"git_sha": "fixture", "git_tree_clean": True, "git_dirty_paths": []},
    )
    monkeypatch.setattr(
        segment_status,
        "load_requirements",
        lambda _path: SimpleNamespace(band_convention="fixture-band"),
    )

    def deliberate_failure(*_args, **_kwargs):
        raise RuntimeError("deliberate post-start raster failure")

    monkeypatch.setattr(segment_status, "load_aligned_rasters", deliberate_failure)
    with pytest.raises(RuntimeError, match="deliberate post-start raster failure"):
        segment_status.build_segment_status_product(
            depth_raster=depth,
            road_raster=road,
            lookup_csv=lookup,
            output_dir=output_dir,
            source_manifest=source_manifest,
            valid_time_utc="2026-08-24T00:00:00+00:00",
            forecast_lead_minutes=0,
            coupling_enabled=False,
            buffered_road_raster=buffered,
            contract_path=repo / "contract.json",
        )
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["error_type"] == "RuntimeError"
    assert "end_time_iso" in manifest


# ------------------------------------------------------------ M1 frames_v2 chain

FRAMES_V1_MANIFEST = Path(__file__).resolve().parents[1] / (
    "runs/wf3_replay2_uncoupled_baseline_frames_v1/manifest.json"
)
FRAMES_V2_MANIFEST = Path(__file__).resolve().parents[1] / (
    "runs/wf3_replay2_uncoupled_baseline_frames_v2/manifest.json"
)


def test_multiframe_exclusion_lifecycle_manifest_rule6(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    "Rule 6: the RUNNING frames manifest carries exclusion, adjudication, input SHAs. Observable if broken: the running manifest would lack the storage_water_exclusion block, the adopted adjudication_ref, or basin_class_raster's sha256 -- meaning a mid-run crash would leave provenance that only exists when nothing goes wrong. Scope: 3-frame fixture series on a 2x2 grid with segment 1 touch-excluded."  # noqa: E501

    from jaladhar.validation import segment_status_frames

    fx = _frame_series_repo(tmp_path)
    basin_path = fx["repo"] / "data/processed/basin_class.tif"
    profile = {
        "driver": "GTiff",
        "height": 2,
        "width": 2,
        "count": 1,
        "transform": from_origin(500000.0, 1450000.0, 10.0, 10.0),
        "crs": "EPSG:32643",
    }
    with rasterio.open(basin_path, "w", dtype="uint8", **profile) as target:
        target.write(np.array([[[1, 4], [4, 4]]], dtype=np.uint8))
    adjudication = fx["repo"] / "runs/wf3_replay2_uncoupled_baseline_v6/manifest.json"
    _write_json(
        adjudication,
        {
            "status": "completed",
            "owner_adjudication": {"decision": "adopt", "date": "2026-08-26"},
            "residual_anomaly": {
                "n_flooded_gt300cm_post_exclusion": 61,
                "max_band_high_cm_post_exclusion": 369,
            },
        },
    )

    monkeypatch.setattr(segment_status_frames, "REPO", fx["repo"])
    monkeypatch.setattr(
        segment_status_frames,
        "_git_provenance",
        lambda: {"git_sha": "frames-sha", "git_tree_clean": True, "git_dirty_paths": []},
    )
    captured: list[dict[str, Any]] = []
    original_write = segment_status_frames.write_json_atomic

    def spy(path: Path, payload: dict[str, Any]) -> None:
        captured.append(json.loads(json.dumps(payload)))
        original_write(path, payload)

    monkeypatch.setattr(segment_status_frames, "write_json_atomic", spy)

    result = segment_status_frames.build_multiframe_product(
        source_manifest=fx["upstream"],
        output_dir=fx["repo"] / "runs/frames_v2_fixture",
        road_raster=fx["road_path"],
        lookup_csv=fx["lookup"],
        buffered_road_raster=fx["buffered_path"],
        frame_admission=fx["frame_admission"],
        contract_path=fx["contract_path"],
        baseline_admission=fx["baseline_admission"],
        basin_class_raster=basin_path,
        exclude_basin_classes=frozenset({1, 2, 3}),
        exclusion_mode="segment_touch",
        adjudication_manifest=adjudication,
    )
    manifest = result["manifest"]
    assert manifest["status"] == "completed"

    running = [payload for payload in captured if payload.get("status") == "running"]
    assert running, "producer never wrote a status=running manifest"
    start = running[0]
    assert start["storage_water_exclusion"]["mode"] == "segment_touch"
    assert start["storage_water_exclusion"]["excluded_classes"] == [1, 2, 3]
    assert start["adjudication_ref"]["decision"] == "adopt"
    assert start["adjudication_ref"]["sha256"] == sha256_file(adjudication)
    assert start["open_anomaly"]["status"] == "OPEN"
    assert start["input_paths"]["basin_class_raster"].endswith("basin_class.tif")
    assert start["input_sha256"]["basin_class_raster"] == sha256_file(basin_path)

    assert manifest["realized_state"]["n_no_data_per_frame"] == 2
    assert manifest["realized_state"]["n_fully_excluded_segments"] == 1
    assert manifest["realized_state"]["max_band_high_cm_over_series"] <= 369
    for entry in manifest["frames"]:
        assert entry["max_band_high_cm"] <= 400
        rows = json.loads((fx["repo"] / entry["json"]).read_text(encoding="utf-8"))["rows"]
        by_id = {row["segment_id"]: row for row in rows}
        assert by_id[1]["confidence"] == "no_data"


@pytest.mark.skipif(
    not (FRAMES_V1_MANIFEST.exists() and FRAMES_V2_MANIFEST.exists()),
    reason=(
        "BLOCKED at full scale: anchor needs "
        f"{FRAMES_V1_MANIFEST} and {FRAMES_V2_MANIFEST} (frames_v2 is emitted by the "
        "M1 producer run)"
    ),
)
class TestFramesV2FullScaleAnchor:
    "Full-scale anchor reading BOTH realized manifests (V1 contaminated, V2 excluded). Independent observable: v1 frame 0 flooded exactly 1921 segments on the contaminated field; the excluded v2 series must flood strictly fewer in frame 0 and never exceed the adopted v6 residual ceiling (369 cm + margin) in any frame. Scope: all 97 frames of the realized run manifests."  # noqa: E501

    def test_v2_frame0_below_contaminated_and_bands_within_residual_ceiling(self) -> None:
        v1 = json.loads(FRAMES_V1_MANIFEST.read_text(encoding="utf-8"))
        v2 = json.loads(FRAMES_V2_MANIFEST.read_text(encoding="utf-8"))
        assert v1["status"] == v2["status"] == "completed"
        assert v1["frame_count_written"] == v2["frame_count_written"] == 97
        assert v1["frames"][0]["n_flooded"] == 1921
        v2_frame0 = v2["frames"][0]["n_flooded"]
        # Measured 2026-08-26: ALL 1921 of v1's frame-0 flooded segments touch an
        assert 0 <= v2_frame0 < 1921 and v2_frame0 != 1921
        assert all(entry["max_band_high_cm"] <= 400 for entry in v2["frames"])
        assert v2["realized_state"]["max_band_high_cm_over_series"] <= 400

    def test_v2_twin_bytes_match_manifest_hashes(self) -> None:
        v2 = json.loads(FRAMES_V2_MANIFEST.read_text(encoding="utf-8"))
        for entry in (v2["frames"][0], v2["frames"][-1]):
            assert sha256_file(Path(entry["csv"])) == entry["csv_sha256"], entry["tag"]
            assert sha256_file(Path(entry["json"])) == entry["json_sha256"], entry["tag"]
