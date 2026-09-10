"""F4 fix verification — G1 scorer START manifest carries the exclusion block.
Scope statement (V7): fixture-scale, 2x2 rasters, one segment, no scoring
START manifest is written (the depth product is a malformed stand-in), which
is exactly the artifact the finding targets: ``manifest.json``'s
manifest's resolved_config would lack basin_class_raster /
passed — the defect realized in runs/wf3_replay2_gates_v7_excluded/manifest.json.
The no-flags companion pins backward compatibility: absent flags => manifest"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "score_g1.py"


def _load_scorer_module():
    spec = importlib.util.spec_from_file_location("wf3_score_g1_f4", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture_repo(tmp_path: Path) -> tuple[Path, dict[str, Path]]:

    repo = tmp_path / "repo"
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
    road_path = repo / "data/test/road_segment_id.tif"
    basin_path = repo / "data/test/basin_class.tif"
    for path in (depth_path, road_path, basin_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(depth_path, "w", dtype="float32", **profile) as target:
        target.write(np.zeros((1, 2, 2), dtype=np.float32))
    with rasterio.open(road_path, "w", dtype="int32", **profile) as target:
        target.write(np.array([[[1, 0], [0, 0]]], dtype=np.int32))
    with rasterio.open(basin_path, "w", dtype="int32", **profile) as target:
        target.write(np.array([[[1, 2], [2, 3]]], dtype=np.int32))

    lookup = repo / "data/test/lookup.csv"
    lookup.write_text("segment_id\n1\n", encoding="utf-8")
    complaints = repo / "data/test/complaints.kml"
    complaints.write_text("exists-only; never read before the seeded failure", encoding="utf-8")
    strict_points = repo / "data/test/strict_points.csv"
    strict_points.write_text("id\n", encoding="utf-8")
    source_manifest = repo / "runs/upstream/manifest.json"
    source_manifest.parent.mkdir(parents=True, exist_ok=True)
    source_manifest.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    convention = repo / "data/test/convention.json"
    convention.write_text(
        json.dumps(
            {
                "primary_convention": "3x3 local max",
                "reported_sensitivity": "exact-cell",
                "no_post_hoc_switching": True,
            }
        ),
        encoding="utf-8",
    )
    # manifest is written — the seeded failure this test observes around.
    product = repo / "runs/product/products/segment_status.csv"
    product.parent.mkdir(parents=True, exist_ok=True)
    product.write_text("foo\nbar\n", encoding="utf-8")
    paths = {
        "depth_raster": depth_path,
        "road_raster": road_path,
        "lookup_csv": lookup,
        "complaints": complaints,
        "strict_depth_points": strict_points,
        "source_manifest": source_manifest,
        "convention_path": convention,
        "product_path": product,
        "basin_class_raster": basin_path,
    }
    return repo, paths


def _run_scorer(module: Any, repo: Path, paths: dict[str, Path], *, exclude: bool) -> Path:
    output_dir = repo / "runs/gates_fixture"
    kwargs: dict[str, Any] = {}
    if exclude:
        kwargs.update(
            basin_class_raster=paths["basin_class_raster"],
            excluded_basin_classes=frozenset({1, 2, 3}),
            exclusion_mode="segment_touch",
        )
    with pytest.raises(module.G1ScoringError):
        module.score_g1(
            depth_raster=paths["depth_raster"],
            road_raster=paths["road_raster"],
            lookup_csv=paths["lookup_csv"],
            complaints=paths["complaints"],
            output_dir=output_dir,
            source_manifest=paths["source_manifest"],
            product_path=paths["product_path"],
            convention_path=paths["convention_path"],
            strict_depth_points=paths["strict_depth_points"],
            event_window_start="2022-09-04",
            event_window_end="2022-09-06",
            expected_bbmp_count=None,
            seed=42,
            n_nulls=module.EXPECTED_NULL_DRAWS,
            max_snap_distance_m=110.0,
            hit_rate_threshold=0.60,
            lift_threshold=3.0,
            depth_in_band_threshold=0.4,
            **kwargs,
        )
    return output_dir


@pytest.mark.parametrize("exclude", [True, False])
def test_start_manifest_resolved_config_carries_exclusion_block_iff_flags_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, exclude: bool
) -> None:
    module = _load_scorer_module()
    # The tmp fixture is not a git repository; provenance is pinned, not measured.
    monkeypatch.setattr(
        module,
        "_git_provenance",
        lambda: {"git_sha": "fixture-sha", "git_tree_clean": True, "git_dirty_paths": []},
    )
    repo, paths = _fixture_repo(tmp_path)

    output_dir = _run_scorer(module, repo, paths, exclude=exclude)
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    resolved = manifest["resolved_config"]

    if exclude:
        assert resolved["exclusion_mode"] == "segment_touch"
        assert resolved["excluded_basin_classes"] == [1, 2, 3]
        assert resolved["basin_class_raster"] == str(paths["basin_class_raster"].resolve())
    else:
        assert "basin_class_raster" not in resolved
        assert "excluded_basin_classes" not in resolved
        assert "exclusion_mode" not in resolved


def test_missing_basin_raster_fails_aggregated_before_output_dir_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    module = _load_scorer_module()
    monkeypatch.setattr(
        module,
        "_git_provenance",
        lambda: {"git_sha": "fixture-sha", "git_tree_clean": True, "git_dirty_paths": []},
    )
    repo, paths = _fixture_repo(tmp_path)

    from jaladhar.validation.segment_status import ConfigResolutionError

    output_dir = repo / "runs/gates_fixture"
    with pytest.raises(ConfigResolutionError) as excinfo:
        module.score_g1(
            depth_raster=paths["depth_raster"],
            road_raster=paths["road_raster"],
            lookup_csv=paths["lookup_csv"],
            complaints=paths["complaints"],
            output_dir=output_dir,
            source_manifest=paths["source_manifest"],
            product_path=paths["product_path"],
            convention_path=paths["convention_path"],
            strict_depth_points=paths["strict_depth_points"],
            event_window_start="2022-09-04",
            event_window_end="2022-09-06",
            expected_bbmp_count=None,
            seed=42,
            n_nulls=module.EXPECTED_NULL_DRAWS,
            max_snap_distance_m=110.0,
            hit_rate_threshold=0.60,
            lift_threshold=3.0,
            depth_in_band_threshold=0.4,
            basin_class_raster=repo / "data/test/absent_basin.tif",
            excluded_basin_classes=frozenset({1}),
            exclusion_mode="cell",
        )
    message = str(excinfo.value)
    assert "basin_class_raster" in message
    assert "absent_basin.tif" in message
    assert not output_dir.exists()  # failed before mkdir / manifest write
