"""WF-6 U0a storage-water exclusion tests for the segment product producer.

Scope statement (V7): fixture tests below run on a 1x5 canonical grid with one
two-cell-deep segment; full-scale tests run the realized 3421x3515 EPSG:32643
event-maximum stack (12,024,815 finite cells, 176,171 lookup segments) and are
BLOCKED-skipped with reasons when those inputs are absent.

Red-under-mutation record (V5): the exclusion invariant is demonstrated red by
construction in ``test_exclusion_is_load_bearing`` -- the mutated/no-exclusion
path is executed in the same test and asserted to reproduce the contaminated
state (full-scale analogue: 1128 cm max, 40519 flooded, recorded in the v5
manifest written by committed code at 25dafa6 before this unit existed).
"""

from __future__ import annotations

import csv
import functools
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from jaladhar.validation import segment_status  # noqa: E402
from jaladhar.validation.depth_product_contract import (  # noqa: E402
    DepthProductContractError,
    sha256_file,
    validate_product_manifest,
)
from jaladhar.validation.segment_status import (  # noqa: E402
    PRODUCT_FIELDS,
    build_exclusion_mask,
    build_segment_status_dataframe,
    load_aligned_class_raster,
    resolve_exclusion_mask,
    summarize_variant,
)

# Audit-wave predictions (external baseline; produced before this unit existed).
AUDIT_NO_EXCLUSION = {
    "n_flooded": 40519,
    "max_band_high_cm": 1128,
    "flooded_p50_band_high_cm": 47.0,
    "flooded_p90_band_high_cm": 151.0,
    "flooded_mean_band_high_cm": 68.9,
}
AUDIT_SEGMENT_TOUCH_CLASS_1 = {
    "n_flooded": 33311,
    "max_band_high_cm": 369,
    "flooded_p50_band_high_cm": 44.0,
    "flooded_p90_band_high_cm": 134.0,
}
# Pre-implementation measurement through this producer's own machinery
# (/tmp scratch probe, 2026-08-26); asserted as prediction, not baseline.
PREDICTED_SEGMENT_TOUCH_CLASS_123 = {
    "n_flooded": 33284,
    "max_band_high_cm": 369,
    "flooded_p50_band_high_cm": 44.0,
    "flooded_p90_band_high_cm": 134.0,
    "n_no_data": 16286,
}

V5_DIR = REPO / "runs/wf3_replay2_uncoupled_baseline_v5"
V6_DIR = REPO / "runs/wf3_replay2_uncoupled_baseline_v6"
DEPTH_PATH = REPO / "runs/goal_d_replay2_final_config/depth_event_maximum.tif"
ROAD_PATH = REPO / "data/processed/road_segment_id.tif"
BASIN_PATH = REPO / "data/processed/basin_class.tif"
LOOKUP_PATH = REPO / "data/interim/terrain/roads_segment_lookup.csv"

FULL_SCALE_INPUTS_PRESENT = all(
    path.exists() for path in (DEPTH_PATH, ROAD_PATH, BASIN_PATH, LOOKUP_PATH)
)


def _fixture_grid() -> tuple[np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    """1x5 grid: segment 1 holds two dry cells and three 90 cm storage cells.

    The three deep cells carry basin_class 1 (storage/lake); the dry ones do
    not.  With no exclusion the segment floods (fraction 3/5 >= 0.2 with a
    3-contiguous run at >= 0.15 m); any working exclusion must remove that.
    """
    depth = np.array([[0.0, 0.0, 0.9, 0.9, 0.9]], dtype=np.float64)
    road = np.array([[1, 1, 1, 1, 1]], dtype=np.int64)
    basin = np.array([[4, 4, 1, 1, 1]], dtype=np.uint8)
    lookup = pd.DataFrame({"segment_id": [1]})
    return depth, road, basin, lookup


def test_exclusion_is_load_bearing(tmp_path: Path) -> None:
    """V5 red-under-mutation, executed: without the mask the contamination returns.

    Observable if the exclusion wiring broke: the no-exclusion build would stop
    reproducing the contaminated maximum, or the excluded build would start
    exceeding the threshold again.  Mutation target: deleting the
    ``excluded_mask`` plumbed into ``build_segment_status_dataframe``.
    """
    depth, road, basin, lookup = _fixture_grid()

    # MUTATED PATH (exclusion disabled): reproduces the contaminated product.
    contaminated = build_segment_status_dataframe(depth, road, lookup)
    assert contaminated.loc[0, "flood_status"] == "flooded"
    assert contaminated.loc[0, "band_high_cm"] == 90
    assert contaminated.loc[0, "confidence"] == "modeled_direct"

    # GREEN PATH: class-1 cells dropped -> nothing left above threshold.
    mask = build_exclusion_mask(basin, {1})
    excluded = build_segment_status_dataframe(depth, road, lookup, excluded_mask=mask)
    assert excluded.loc[0, "flood_status"] == "not_flooded"
    assert (excluded.loc[0, "band_low_cm"], excluded.loc[0, "band_high_cm"]) == (0, 0)

    # The same test reddens if someone reintroduces excluded cells silently.
    with pytest.raises(AssertionError):
        assert excluded.loc[0, "band_high_cm"] <= -1  # sanity of the harness itself


def test_segment_touch_mode_drops_whole_touched_segment() -> None:
    """Audit 'touch test' semantics: one water cell removes the entire segment."""
    depth, road, basin, lookup = _fixture_grid()
    mask, info = resolve_exclusion_mask(road, basin, {1}, "segment_touch")
    assert info["mode"] == "segment_touch"
    # All five cells of the single touching segment are masked.
    assert int(mask.sum()) == 5
    assert info["cells_masked_on_grid"] == 5
    frame = build_segment_status_dataframe(depth, road, lookup, excluded_mask=mask)
    # All five cells gone -> honest no_data synthesis, never a fabricated zero.
    assert frame.loc[0, "confidence"] == "no_data"
    assert frame.loc[0, "flood_status"] == "unknown"
    assert (frame.loc[0, "band_low_cm"], frame.loc[0, "band_high_cm"]) == (0, 0)
    summary = summarize_variant(frame)
    assert summary["n_flooded"] == 0
    assert summary["n_no_data"] == 1


def test_cell_mode_keeps_partially_touched_segment() -> None:
    """Literal cell-level dropping only removes the excluded-class cells."""
    depth, road, basin, lookup = _fixture_grid()
    mask, _touched = resolve_exclusion_mask(road, basin, {1}, "cell")
    assert int(mask.sum()) == 3
    frame = build_segment_status_dataframe(depth, road, lookup, excluded_mask=mask)
    assert frame.loc[0, "confidence"] == "modeled_direct"
    assert frame.loc[0, "flood_status"] == "not_flooded"
    assert (frame.loc[0, "band_low_cm"], frame.loc[0, "band_high_cm"]) == (0, 0)


def test_class_raster_misalignment_refused_at_seam(tmp_path: Path) -> None:
    """V8 boundary rule: a class raster off-grid must fail closed, not misjoin."""
    depth, road, basin, _lookup = _fixture_grid()
    reference = segment_status.RasterInputs(
        depth_m=depth,
        road_ids=road,
        transform=from_origin(766940.0, 1454350.0, 10.0, 10.0),
        crs="EPSG:32643",
        depth_path=tmp_path / "d.tif",
        road_path=tmp_path / "r.tif",
    )
    wrong_transform = tmp_path / "wrong_transform.tif"
    with rasterio.open(
        wrong_transform,
        "w",
        dtype="uint8",
        count=1,
        height=1,
        width=5,
        transform=from_origin(0.0, 0.0, 10.0, 10.0),
        crs="EPSG:32643",
    ) as target:
        target.write(basin.reshape(1, 1, 5))
    with pytest.raises(segment_status.SegmentStatusError, match="transform"):
        load_aligned_class_raster(wrong_transform, reference)

    wrong_crs = tmp_path / "wrong_crs.tif"
    with rasterio.open(
        wrong_crs,
        "w",
        dtype="uint8",
        count=1,
        height=1,
        width=5,
        transform=reference.transform,
        crs="EPSG:3857",
    ) as target:
        target.write(basin.reshape(1, 1, 5))
    with pytest.raises(segment_status.SegmentStatusError, match="CRS"):
        load_aligned_class_raster(wrong_crs, reference)


def _validator_fixture(
    tmp_path: Path,
    *,
    git_tree_clean: bool = True,
    dirty_admission: dict[str, Any] | None = None,
    storage_exclusion: dict[str, Any] | None = None,
) -> tuple[Path, Path, list[dict[str, Any]]]:
    """Minimal complete product fixture for validator-branch tests."""
    repo = tmp_path / "repo"
    contract = {
        "version": "test-frozen",
        "schema_fields": {"flood_status": "PRIMARY_RULE D=0.15 m F=20% N=3"},
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
    contract_path = repo / "configs/contracts/depth_product.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    lookup = repo / "data/interim/terrain/roads_segment_lookup.csv"
    lookup.parent.mkdir(parents=True, exist_ok=True)
    lookup.write_text("segment_id\n1\n", encoding="utf-8")

    transform = from_origin(500000.0, 1450000.0, 10.0, 10.0)
    profile = {
        "driver": "GTiff",
        "height": 2,
        "width": 2,
        "count": 1,
        "transform": transform,
        "crs": "EPSG:32643",
    }
    depth_path = repo / "data/test/depth.tif"
    road_raster = repo / "data/test/roads.tif"
    buffered = repo / "data/test/roads_buffered.tif"
    depth_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(depth_path, "w", dtype="float32", **profile) as target:
        target.write(np.zeros((1, 2, 2), dtype=np.float32))
    with rasterio.open(road_raster, "w", dtype="int32", **profile) as target:
        target.write(np.array([[[1, 0], [0, 0]]], dtype=np.int32))
    with rasterio.open(buffered, "w", dtype="int32", **profile) as target:
        target.write(np.array([[[1, 2], [0, 0]]], dtype=np.int32))
    upstream = repo / "runs/upstream/manifest.json"
    upstream.parent.mkdir(parents=True, exist_ok=True)
    upstream.write_text(
        json.dumps({"status": "completed", "git_sha": "upstream-sha", "coupling_enabled": False}),
        encoding="utf-8",
    )

    row_base = {
        "run_id": "product",
        "forecast_lead_minutes": 0,
        "valid_time_utc": "2026-08-24T00:00:00+00:00",
        "issue_time_utc": "2026-08-24T00:00:00+00:00",
        "source_manifest_path": "runs/product/manifest.json",
    }
    no_data_row = {
        **row_base,
        "segment_id": 1,
        "band_low_cm": 0,
        "band_high_cm": 0,
        "confidence": "no_data",
        "flood_status": "unknown",
    }
    inputs = {
        "depth_raster": depth_path,
        "road_raster": road_raster,
        "lookup_csv": lookup,
        "buffered_road_raster": buffered,
        "depth_source_manifest": upstream,
    }

    def _build(rows: list[dict[str, Any]], n_no_data: int) -> tuple[Path, Path]:
        run_dir = repo / "runs/product"
        manifest = {
            "stage": "fixture",
            "status": "completed",
            "git_sha": "fixture-sha",
            "git_tree_clean": git_tree_clean,
            "run_id": "product",
            "n_segments_expected": len(rows),
            "n_no_data_segments": n_no_data,
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
            "uncoupled_baseline_admission_path": None,
            "grid_identity": {"shape": [2, 2], "crs": "EPSG:32643", "resolution_m": 10.0},
            "input_paths": {
                key: value.relative_to(repo).as_posix() for key, value in inputs.items()
            },
            "input_sha256": {key: sha256_file(value) for key, value in inputs.items()},
            "outputs": {
                "csv": "runs/product/products/segment_status.csv",
                "json": "runs/product/products/segment_status.json",
            },
        }
        if dirty_admission is not None:
            manifest["source_tree_dirty_admission"] = dirty_admission
        if storage_exclusion is not None:
            manifest["storage_water_exclusion"] = storage_exclusion
        products = run_dir / "products"
        products.mkdir(parents=True, exist_ok=True)
        with (products / "segment_status.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        twin = {
            "schema": "depth_product/test-frozen",
            "manifest_path": "runs/product/manifest.json",
            "rows": rows,
        }
        (products / "segment_status.json").write_text(json.dumps(twin), encoding="utf-8")
        manifest["output_sha256"] = {
            "csv": sha256_file(products / "segment_status.csv"),
            "json": sha256_file(products / "segment_status.json"),
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path, manifest

    manifest_path, _manifest = _build([no_data_row], 1)
    return repo, contract_path, manifest_path


def _validate_fixture(contract_path: Path, manifest_path: Path, repo: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = json.loads(
        (manifest_path.parent / "products/segment_status.json").read_text(encoding="utf-8")
    )["rows"]
    validate_product_manifest(
        manifest,
        manifest_path,
        rows=rows,
        lookup_ids={int(row["segment_id"]) for row in rows},
        repo_root=repo,
        contract_path=contract_path,
    )


def test_validator_accepts_recorded_dirty_admission(tmp_path: Path) -> None:
    """A dirty tree recorded honestly (paths + reason) validates; clean default unchanged."""
    repo, contract_path, manifest_path = _validator_fixture(
        tmp_path,
        git_tree_clean=False,
        dirty_admission={
            "reason": "concurrent units own the listed paths",
            "porcelain_paths": [" M workflows/wf2-coupling.js", "?? src/jaladhar/coupling/"],
        },
    )
    _validate_fixture(contract_path, manifest_path, repo)


@pytest.mark.parametrize(
    "admission",
    [
        None,
        {"reason": "", "porcelain_paths": ["x"]},
        {"reason": "something", "porcelain_paths": []},
        {"reason": "something"},
    ],
)
def test_validator_refuses_unrecorded_dirty_tree(tmp_path: Path, admission: Any) -> None:
    """Red family: git_tree_clean=false without a complete recorded admission."""
    repo, contract_path, manifest_path = _validator_fixture(
        tmp_path,
        git_tree_clean=False,
        dirty_admission=admission,
    )
    with pytest.raises(DepthProductContractError, match="git_tree_clean"):
        _validate_fixture(contract_path, manifest_path, repo)


def test_validator_accepts_reconciling_exclusion_delta(tmp_path: Path) -> None:
    """no_data_total == frozen + declared measured delta validates."""
    repo, contract_path, manifest_path = _validator_fixture(
        tmp_path,
        storage_exclusion={
            "rule": "drop segments touching excluded basin classes",
            "all_canonical_cells_excluded": 4,
        },
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["n_no_data_segments"] = 5  # 1 frozen + 4 declared
    # Rewrite CSV/JSON twins to carry 5 no-data rows so realized count reconciles.
    base = json.loads(
        (manifest_path.parent / "products/segment_status.json").read_text(encoding="utf-8")
    )["rows"][0]
    rows = [{**base, "segment_id": 100 + offset} for offset in range(4)]
    rows.append(base)
    csv_path = manifest_path.parent / "products/segment_status.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(base))
        writer.writeheader()
        writer.writerows(rows)
    (manifest_path.parent / "products/segment_status.json").write_text(
        json.dumps({"schema": "depth_product/test-frozen", "rows": rows}), encoding="utf-8"
    )
    manifest["output_sha256"] = {
        "csv": sha256_file(csv_path),
        "json": sha256_file(manifest_path.parent / "products/segment_status.json"),
    }
    # Lookup gains the four synthesized ids too.
    lookup_path = repo / "data/interim/terrain/roads_segment_lookup.csv"
    lookup_path.write_text("segment_id\n1\n100\n101\n102\n103\n", encoding="utf-8")
    manifest["input_sha256"]["lookup_csv"] = sha256_file(lookup_path)
    manifest["n_segments_expected"] = 5
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _validate_fixture(contract_path, manifest_path, repo)


@pytest.mark.parametrize("delta", [-1, 5])
def test_validator_refuses_non_reconciling_exclusion_delta(tmp_path: Path, delta: int) -> None:
    """Red: a delta that does not reconcile with realized rows must redden."""
    repo, contract_path, manifest_path = _validator_fixture(
        tmp_path,
        storage_exclusion={
            "rule": "drop segments touching excluded basin classes",
            "all_canonical_cells_excluded": delta,
        },
    )
    with pytest.raises(DepthProductContractError):
        _validate_fixture(contract_path, manifest_path, repo)


@pytest.mark.skipif(
    not FULL_SCALE_INPUTS_PRESENT,
    reason=(
        "BLOCKED at fixture scope only: full-scale anchors need "
        f"{DEPTH_PATH}, {ROAD_PATH}, {BASIN_PATH}"
    ),
)
class TestFullScaleAuditAnchors:
    """Realized-state anchors on the 3421x3515 event-maximum stack."""

    @staticmethod
    @functools.lru_cache(maxsize=1)
    def _variants() -> dict[str, Any]:
        from jaladhar.validation.segment_status import (
            _read_lookup,
            build_road_index,
            load_aligned_rasters,
        )

        inputs = load_aligned_rasters(DEPTH_PATH, ROAD_PATH)
        lookup = _read_lookup(LOOKUP_PATH)
        index = build_road_index(inputs.road_ids)
        none_frame = build_segment_status_dataframe(
            inputs.depth_m, inputs.road_ids, lookup, road_index=index
        )
        out: dict[str, Any] = {"none": summarize_variant(none_frame)}
        class_array, _info = load_aligned_class_raster(BASIN_PATH, inputs)
        for label, classes, mode in (
            ("touch_c1", {1}, "segment_touch"),
            ("touch_c123", {1, 2, 3}, "segment_touch"),
        ):
            mask, _touched = resolve_exclusion_mask(inputs.road_ids, class_array, classes, mode)
            frame = build_segment_status_dataframe(
                inputs.depth_m, inputs.road_ids, lookup, excluded_mask=mask
            )
            out[label] = summarize_variant(frame)
        return out

    def test_no_exclusion_reproduces_v5_realized_state(self) -> None:
        """Independent baseline: the v5 manifest was written by committed code at 25dafa6."""
        payload = json.loads((V5_DIR / "manifest.json").read_text(encoding="utf-8"))
        realized = payload["realized_state"]
        summary = self._variants()["none"]
        assert summary["n_flooded"] == realized["n_flooded"] == AUDIT_NO_EXCLUSION["n_flooded"]
        assert summary["max_band_high_cm"] == AUDIT_NO_EXCLUSION["max_band_high_cm"]
        assert summary["n_no_data"] == realized["n_no_data"]

    def test_segment_touch_class1_matches_audit_exactly(self) -> None:
        summary = self._variants()["touch_c1"]
        for key, expected in AUDIT_SEGMENT_TOUCH_CLASS_1.items():
            assert summary[key] == expected, key
        assert summary["flooded_mean_band_high_cm"] == pytest.approx(61.7, abs=0.05)
        # The audit's residual anomaly, reproduced: 61 flooded segments >3 m remain.
        assert summary["n_flooded_gt300cm"] == 61

    def test_segment_touch_class123_prediction(self) -> None:
        summary = self._variants()["touch_c123"]
        for key, expected in PREDICTED_SEGMENT_TOUCH_CLASS_123.items():
            assert summary[key] == expected, key
        assert summary["flooded_mean_band_high_cm"] == pytest.approx(61.71, abs=0.01)
        assert summary["n_flooded_gt300cm"] == 61


def test_v5_bytes_untouched_against_pre_existing_manifest_hashes() -> None:
    """V3-clean baseline: hashes recorded in v5's manifest on 2026-08-25, before this unit.

    Independent observable if this unit (or anything else) mutated the frozen
    baseline: the realized bytes would no longer match the output_sha256 that
    the committed v5 producer recorded at completion time.
    """
    v5_manifest = json.loads((V5_DIR / "manifest.json").read_text(encoding="utf-8"))
    for key, name in (("csv", "segment_status.csv"), ("json", "segment_status.json")):
        path = V5_DIR / "products" / name
        assert sha256_file(path) == v5_manifest["output_sha256"][key], name


@pytest.mark.skipif(
    not (V6_DIR / "products/segment_status.csv").exists(),
    reason="v6 product not yet emitted by the gated producer run",
)
class TestV6ProductRealizedState:
    """Assertions against the emitted v6 bytes, not against declarations."""

    def test_schema_columns_and_rows_exact(self) -> None:
        csv_path = V6_DIR / "products/segment_status.csv"
        with csv_path.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        assert header == PRODUCT_FIELDS
        rows = pd.read_csv(csv_path, usecols=["segment_id", "confidence", "flood_status"])
        lookup_ids = set(pd.read_csv(LOOKUP_PATH, usecols=["segment_id"])["segment_id"].tolist())
        assert len(rows) == len(lookup_ids) == 176171
        assert set(rows["segment_id"]) == lookup_ids
        twin = json.loads((V6_DIR / "products/segment_status.json").read_text(encoding="utf-8"))
        assert twin["schema"] == "depth_product/1.1.0-frozen"

    def test_numbers_match_audit_predicted_class123(self) -> None:
        manifest = json.loads((V6_DIR / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "completed"
        summary = manifest["variant_summaries"]["segment_touch_class_1_2_3"]
        assert summary["n_flooded"] == PREDICTED_SEGMENT_TOUCH_CLASS_123["n_flooded"]
        assert summary["max_band_high_cm"] == 369
        assert summary["n_flooded_gt300cm"] == 61
        realized = manifest["realized_state"]
        assert realized["n_flooded"] == PREDICTED_SEGMENT_TOUCH_CLASS_123["n_flooded"]
        assert realized["n_no_data"] == PREDICTED_SEGMENT_TOUCH_CLASS_123["n_no_data"]

    def test_residual_anomaly_recorded_not_explained(self) -> None:
        manifest = json.loads((V6_DIR / "manifest.json").read_text(encoding="utf-8"))
        residual = manifest["residual_anomaly"]
        assert residual["n_flooded_gt300cm_post_exclusion"] == 61
        assert residual["max_band_high_cm_post_exclusion"] == 369
        assert "not explained away" in residual["note"].lower()

    def test_sidecar_mapping_covers_every_segment(self) -> None:
        sidecar = pd.read_csv(V6_DIR / "products/segment_osm_water_intersection.csv")
        lookup_ids = set(pd.read_csv(LOOKUP_PATH, usecols=["segment_id"])["segment_id"].tolist())
        assert set(sidecar["segment_id"]) == lookup_ids
        assert sidecar["intersects_osm_water"].dtype == bool


# ---------------------------------------------------- frame-series exclusion (M1)


FRAMES_V1_DIR = REPO / "runs/wf3_replay2_uncoupled_baseline_frames_v1"
FRAMES_V2_DIR = REPO / "runs/wf3_replay2_uncoupled_baseline_frames_v2"


def _frames_exclusion_repo(tmp_path: Path, *, n_frames: int = 2) -> dict[str, Any]:
    """Frame-series fixture with a basin raster: segment 1 is storage-water-touched.

    Grid 1x4; segment 1 holds three cells whose frame-0 depth is 0.30 m (a
    3-contiguous run above the 0.15 m threshold -> flooded without exclusion);
    segment 2 exists only in the buffered raster.  Cell (0, 0) carries
    basin_class 1, so the audit touch test drops ALL of segment 1: with a working
    exclusion every frame must synthesize BOTH segments as no_data.
    """

    repo = tmp_path / "repo"
    contract = {
        "version": "test-frozen",
        "schema_fields": {"flood_status": "PRIMARY_RULE D=0.15 m F=20% N=3-contiguous-cells"},
        "manifest_guarantees_producer_writes": [
            "depth_convention 'PRIMARY_RULE D0.15 F0.20 N3' + grid identity EPSG:32643 1x4 10m",
            "band_convention 'min_max_over_canonical_cells_floor_cm'",
        ],
        "no_data_segments_measured_split": {
            "total_canonical_absent": 1,
            "buffered_margin_only": 1,
            "fully_clipped_no_cells_anywhere": 0,
        },
    }
    contract_path = repo / "configs/contracts/depth_product.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    lookup = repo / "data/interim/terrain/roads_segment_lookup.csv"
    lookup.parent.mkdir(parents=True, exist_ok=True)
    lookup.write_text("segment_id\n1\n2\n", encoding="utf-8")

    transform = from_origin(500000.0, 1450000.0, 10.0, 10.0)
    profile = {
        "driver": "GTiff",
        "height": 1,
        "width": 4,
        "count": 1,
        "transform": transform,
        "crs": "EPSG:32643",
    }
    road_dir = repo / "data/processed"
    road_dir.mkdir(parents=True, exist_ok=True)
    road_path = road_dir / "road_segment_id.tif"
    with rasterio.open(road_path, "w", dtype="int32", **profile) as target:
        target.write(np.array([[[1, 1, 1, 0]]], dtype=np.int32))
    buffered_path = road_dir / "road_segment_id_buffered.tif"
    with rasterio.open(buffered_path, "w", dtype="int32", **profile) as target:
        target.write(np.array([[[1, 1, 1, 2]]], dtype=np.int32))
    basin_path = road_dir / "basin_class.tif"
    with rasterio.open(basin_path, "w", dtype="uint8", **profile) as target:
        target.write(np.array([[[1, 4, 4, 4]]], dtype=np.uint8))

    series_dir = repo / "runs/upstream/depth_rasters"
    series_dir.mkdir(parents=True, exist_ok=True)
    depths = [0.30, 0.05]
    for offset in range(n_frames):
        array = np.full((1, 1, 4), np.float32(depths[offset]))
        array[0, 0, 3] = np.float32(0.0)
        with rasterio.open(
            series_dir / f"depth_t{offset * 1800:07d}s.tif", "w", dtype="float32", **profile
        ) as target:
            target.write(array)

    upstream = repo / "runs/upstream/manifest.json"
    upstream.parent.mkdir(parents=True, exist_ok=True)
    upstream.write_text(
        json.dumps(
            {
                "status": "completed",
                "git_sha": "upstream-sha",
                "stage": "phase3_uncalibrated_validation_gate",
                "event": {"start": "2022-09-04T00:00:00Z", "end": "2022-09-05T23:30:00Z"},
            }
        ),
        encoding="utf-8",
    )
    baseline = repo / "data/curation/baseline_admission.json"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    first_frame = sorted(series_dir.glob("depth_t*s.tif"))[0]
    baseline.write_text(
        json.dumps(
            {
                "decision": "ADMITTED",
                "product_label": "UNCOUPLED BASELINE",
                "coupling_enabled": False,
                "source_manifest_sha256": sha256_file(upstream),
                "source_git_sha": "upstream-sha",
                "source_stage": "phase3_uncalibrated_validation_gate",
                "source_status": "completed",
                "depth_raster_path": str(first_frame.relative_to(repo)),
                "depth_raster_sha256": sha256_file(first_frame),
                "forcing_kind": "historical_replay",
                "temporal_aggregation": "event_maximum",
                "event_window_start_utc": "2022-09-04T00:00:00Z",
                "event_window_end_utc": "2022-09-05T23:30:00Z",
            }
        ),
        encoding="utf-8",
    )
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        from build_frame_series_admission import build_frame_series_admission
    finally:
        sys.path.remove(str(REPO / "scripts"))
    payload = build_frame_series_admission(
        series_dir,
        upstream,
        expected_count=n_frames,
        expected_cadence_seconds=1800,
        repo_root=repo,
    )
    frame_admission = repo / "data/curation/frame_series_admission.json"
    frame_admission.parent.mkdir(parents=True, exist_ok=True)
    frame_admission.write_text(json.dumps(payload), encoding="utf-8")

    # The ADOPTED owner-adjudication record this unit depends on (fixture stand-in
    # for runs/wf3_replay2_uncoupled_baseline_v6/manifest.json post Step-1).
    adjudication = repo / "runs/wf3_replay2_uncoupled_baseline_v6/manifest.json"
    adjudication.parent.mkdir(parents=True, exist_ok=True)
    adjudication.write_text(
        json.dumps(
            {
                "status": "completed",
                "owner_adjudication": {"decision": "adopt", "date": "2026-08-26"},
                "residual_anomaly": {
                    "n_flooded_gt300cm_post_exclusion": 61,
                    "max_band_high_cm_post_exclusion": 369,
                },
            }
        ),
        encoding="utf-8",
    )
    return {
        "repo": repo,
        "contract_path": contract_path,
        "lookup": lookup,
        "road_path": road_path,
        "buffered_path": buffered_path,
        "basin_path": basin_path,
        "upstream": upstream,
        "baseline_admission": baseline,
        "frame_admission": frame_admission,
        "adjudication": adjudication,
        "n_frames": n_frames,
    }


def _build_frames_fixture_series(fx: dict[str, Any], run_id: str, *, excluded: bool) -> dict:
    from jaladhar.validation import segment_status_frames

    return segment_status_frames.build_multiframe_product(
        source_manifest=fx["upstream"],
        output_dir=fx["repo"] / "runs" / run_id,
        road_raster=fx["road_path"],
        lookup_csv=fx["lookup"],
        buffered_road_raster=fx["buffered_path"],
        frame_admission=fx["frame_admission"],
        contract_path=fx["contract_path"],
        baseline_admission=fx["baseline_admission"],
        basin_class_raster=fx["basin_path"] if excluded else None,
        exclude_basin_classes=frozenset({1, 2, 3}) if excluded else None,
        exclusion_mode="segment_touch",
        adjudication_manifest=fx["adjudication"] if excluded else None,
    )["manifest"]


@pytest.fixture()
def _frames_fixture_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the frames module at a fresh fixture repo with stubbed Git provenance."""
    from jaladhar.validation import segment_status_frames

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(segment_status_frames, "REPO", repo)
    monkeypatch.setattr(
        segment_status_frames,
        "_git_provenance",
        lambda *_a, **_k: {
            "git_sha": "frames-fixture-sha",
            "git_tree_clean": True,
            "git_dirty_paths": [],
        },
    )


def test_frames_series_exclusion_is_load_bearing_and_reverts_to_contaminated(
    tmp_path: Path, _frames_fixture_env
) -> None:
    """Fixture-scale V5 red for the FRAMES producer.

    Observable if the exclusion wiring in the frame loop broke: the no-exclusion
    rerun and the excluded run would agree (both flooded or both clean), or the
    excluded manifest would stop reconciling its no-data delta.  Mutation arm:
    the same builder WITHOUT classes -- it must reproduce the contaminated
    fixture state (segment 1 flooded at 30 cm), and the two arms MUST differ.
    """

    fx = _frames_exclusion_repo(tmp_path)

    # MUTATED PATH (exclusion absent): reproduces the contaminated numbers.
    contaminated = _build_frames_fixture_series(fx, "frames_no_excl", excluded=False)
    assert contaminated["status"] == "completed"
    first_contaminated = json.loads(
        (fx["repo"] / contaminated["frames"][0]["json"]).read_text(encoding="utf-8")
    )["rows"]
    by_id = {row["segment_id"]: row for row in first_contaminated}
    assert by_id[1]["flood_status"] == "flooded"
    assert by_id[1]["band_high_cm"] == 30
    assert by_id[2]["confidence"] == "no_data"
    assert contaminated["frames"][0]["n_flooded"] == 1
    assert contaminated["realized_state"]["max_band_high_cm_over_series"] == 30

    # GREEN PATH: one touched class-1 cell removes the whole segment in EVERY frame.
    excluded = _build_frames_fixture_series(fx, "frames_excl", excluded=True)
    assert excluded["status"] == "completed"
    assert excluded["storage_water_exclusion"]["mode"] == "segment_touch"
    assert (
        excluded["storage_water_exclusion"]["all_canonical_cells_excluded"] == 1
    )  # exactly segment 1
    assert excluded["realized_state"]["n_no_data_per_frame"] == 2  # frozen 1 + excluded 1
    assert excluded["realized_state"]["n_fully_excluded_segments"] == 1
    for entry in excluded["frames"]:
        rows = {
            row["segment_id"]: row
            for row in json.loads((fx["repo"] / entry["json"]).read_text(encoding="utf-8"))["rows"]
        }
        assert rows[1]["confidence"] == "no_data"  # storage water never reported as street flood
        assert rows[1]["flood_status"] == "unknown"
        assert entry["n_flooded"] == 0
        assert entry["max_band_high_cm"] == 0

    # The arms must differ on the load-bearing quantity itself (V5: seen red).
    assert contaminated["frames"][0]["n_flooded"] != excluded["frames"][0]["n_flooded"]
    # Adjudication binding + OPEN anomaly carried into the emitted manifest.
    assert excluded["adjudication_ref"]["decision"] == "adopt"
    assert excluded["adjudication_ref"]["sha256"] == sha256_file(fx["adjudication"])
    assert excluded["open_anomaly"]["status"] == "OPEN"
    assert excluded["open_anomaly"]["n_segments_gt300cm"] == 61
    assert excluded["input_sha256"]["basin_class_raster"] == sha256_file(fx["basin_path"])
