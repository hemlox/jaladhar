"""Small realized-state product fixtures shared by web contract tests."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import from_origin

from jaladhar.validation.depth_product_contract import sha256_file

CONTRACT = {
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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _inputs(repo: Path, *, two_segments: bool = False) -> dict[str, Path]:
    lookup = repo / "data/interim/terrain/roads_segment_lookup.csv"
    lookup.parent.mkdir(parents=True, exist_ok=True)
    lookup.write_text("segment_id\n1\n" + ("2\n" if two_segments else ""), encoding="utf-8")
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
    return inputs


def base_repo(tmp_path: Path, *, two_segments: bool = False) -> Path:
    repo = tmp_path / "repo"
    write_json(repo / "configs/contracts/depth_product.json", CONTRACT)
    _inputs(repo, two_segments=two_segments)
    write_json(
        repo / "runs/upstream/manifest.json",
        {"status": "completed", "git_sha": "upstream-sha", "coupling_enabled": False},
    )
    return repo


def fixture_repo(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    repo = base_repo(tmp_path)
    inputs = _inputs(repo)
    upstream = repo / "runs/upstream/manifest.json"
    inputs["depth_source_manifest"] = upstream
    run_dir = repo / "runs/product"
    product_dir = run_dir / "products"
    row = {
        "run_id": "product",
        "segment_id": 1,
        "band_low_cm": 0,
        "band_high_cm": 0,
        "confidence": "no_data",
        "flood_status": "unknown",
        "valid_time_utc": "2026-08-24T00:00:00+00:00",
        "issue_time_utc": "2026-08-24T00:00:00+00:00",
        "forecast_lead_minutes": 0,
        "source_manifest_path": "runs/product/manifest.json",
    }
    relative = {name: path.relative_to(repo).as_posix() for name, path in inputs.items()}
    manifest = {
        "stage": "fixture_contract_reader",
        "status": "completed",
        "git_sha": "fixture-sha",
        "git_tree_clean": True,
        "run_id": "product",
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
        "input_paths": relative,
        "input_sha256": {name: sha256_file(path) for name, path in inputs.items()},
        "outputs": {
            "csv": "runs/product/products/segment_status.csv",
            "json": "runs/product/products/segment_status.json",
        },
    }
    write_json(
        product_dir / "segment_status.json",
        {
            "schema": "depth_product/test-frozen",
            "manifest_path": "runs/product/manifest.json",
            "rows": [row],
        },
    )
    _write_csv(product_dir / "segment_status.csv", [row])
    manifest["output_sha256"] = {
        "csv": sha256_file(product_dir / "segment_status.csv"),
        "json": sha256_file(product_dir / "segment_status.json"),
    }
    write_json(run_dir / "manifest.json", manifest)
    return repo, product_dir, row


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def flat_row(run_id: str) -> dict[str, Any]:
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


def write_flat_run(repo: Path, run_id: str, *, end_time: str, break_twin: bool = False) -> Path:
    run_dir, product_dir, row = (
        repo / "runs" / run_id,
        repo / "runs" / run_id / "products",
        flat_row(run_id),
    )
    write_json(
        product_dir / "segment_status.json", {"schema": "depth_product/test-frozen", "rows": [row]}
    )
    _write_csv(product_dir / "segment_status.csv", [row])
    inputs = _inputs(repo)
    inputs["depth_source_manifest"] = repo / "runs/upstream/manifest.json"
    relative = {name: path.relative_to(repo).as_posix() for name, path in inputs.items()}
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
        "input_paths": relative,
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
    write_json(run_dir / "manifest.json", manifest)
    return run_dir


def write_series_run(
    repo: Path, run_id: str, *, end_time: str, n_flooded: int = 0, break_count: bool = False
) -> Path:
    run_dir = repo / "runs" / run_id
    frame_dir = run_dir / "products/frames/t0000000"
    row = flat_row(run_id)
    write_json(
        frame_dir / "segment_status.json", {"schema": "depth_product/test-frozen", "rows": [row]}
    )
    _write_csv(frame_dir / "segment_status.csv", [row])
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
    write_json(run_dir / "manifest.json", manifest)
    return run_dir


def frame_series_repo(tmp_path: Path, *, n_frames: int = 3) -> dict[str, Any]:
    repo = base_repo(tmp_path, two_segments=True)
    contract_path = repo / "configs/contracts/depth_product.json"
    lookup = repo / "data/interim/terrain/roads_segment_lookup.csv"
    profile = {
        "driver": "GTiff",
        "height": 2,
        "width": 2,
        "count": 1,
        "transform": from_origin(500000.0, 1450000.0, 10.0, 10.0),
        "crs": "EPSG:32643",
    }
    road_dir = repo / "data/processed"
    road_path, buffered_path = (
        road_dir / "road_segment_id.tif",
        road_dir / "road_segment_id_buffered.tif",
    )
    road_dir.mkdir(parents=True, exist_ok=True)
    with rasterio.open(road_path, "w", dtype="int32", **profile) as target:
        target.write(np.array([[[1, 0], [0, 0]]], dtype=np.int32))
    with rasterio.open(buffered_path, "w", dtype="int32", **profile) as target:
        target.write(np.array([[[1, 2], [0, 0]]], dtype=np.int32))
    series_dir = repo / "runs/upstream/depth_rasters"
    series_dir.mkdir(parents=True, exist_ok=True)
    for offset, depth in enumerate([0.00, 0.20, 0.05][:n_frames]):
        array = np.zeros((1, 2, 2), dtype=np.float32)
        array[0, 0, 0] = np.float32(depth)
        with rasterio.open(
            series_dir / f"depth_t{offset * 1800:07d}s.tif", "w", dtype="float32", **profile
        ) as target:
            target.write(array)
    upstream = repo / "runs/upstream/manifest.json"
    write_json(
        upstream,
        {
            "status": "completed",
            "git_sha": "upstream-sha",
            "stage": "phase3_uncalibrated_validation_gate",
            "event": {"start": "2022-09-04T00:00:00Z", "end": "2022-09-05T23:30:00Z"},
        },
    )
    baseline = repo / "data/curation/baseline_admission.json"
    first_frame = sorted(series_dir.glob("depth_t*s.tif"))[0]
    write_json(
        baseline,
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
        },
    )
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        from build_frame_series_admission import build_frame_series_admission
    finally:
        sys.path.remove(str(scripts))
    payload = build_frame_series_admission(
        series_dir, upstream, expected_count=n_frames, expected_cadence_seconds=1800, repo_root=repo
    )
    frame_admission = repo / "data/curation/frame_series_admission.json"
    write_json(frame_admission, payload)
    return {
        "repo": repo,
        "contract_path": contract_path,
        "lookup": lookup,
        "road_path": road_path,
        "buffered_path": buffered_path,
        "upstream": upstream,
        "baseline_admission": baseline,
        "frame_admission": frame_admission,
        "series_dir": series_dir,
        "n_frames": n_frames,
    }
