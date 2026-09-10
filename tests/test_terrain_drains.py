"""- Realized raster profiles, bounds, dtypes, and non-overwriting coexistence (V1).
- V8 contract seam assertions between drain manifest producer and solver consumer.
- Invariant V5 red-under-mutation testing on consumer-side assertions."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import yaml

from jaladhar.solver.state import assert_drain_manifest_contract, load_domain
from jaladhar.terrain.drains import _publish_canonical_artifacts
from jaladhar.terrain.grid import build_grid, load_config

REPO = Path(__file__).resolve().parents[1]
INTERIM = REPO / "data" / "interim" / "terrain"
PROCESSED = REPO / "data" / "processed"
RUNS = REPO / "runs" / "terrain_drains"


def _require_realized(*paths: Path) -> None:
    """Mark full-artifact checks BLOCKED when Phase 1 data is not built."""
    missing = [str(path.relative_to(REPO)) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"BLOCKED: terrain-drain artifacts are not realized: {missing}")


def test_source_artifacts_publish_identical_canonical_aliases_without_overwrite(tmp_path):
    """V2/V5: canonical bytes track selection while the other source survives.
    If publication breaks, the selected-source and canonical digests differ.
    The deliberate canonical mutation below demonstrates that observable red"""
    osm = tmp_path / "waterways_osm.gpkg"
    bbmp = tmp_path / "waterways_bbmp.gpkg"
    canonical = tmp_path / "waterways.gpkg"
    osm.write_bytes(b"independent-osm-artifact")
    bbmp.write_bytes(b"selected-bbmp-artifact")

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    _publish_canonical_artifacts({bbmp: canonical})
    assert digest(canonical) == digest(bbmp)
    assert osm.read_bytes() == b"independent-osm-artifact"

    canonical.write_bytes(b"deliberate-mutation")
    assert digest(canonical) != digest(bbmp)
    _publish_canonical_artifacts({bbmp: canonical})
    assert digest(canonical) == digest(bbmp)
    assert osm.read_bytes() == b"independent-osm-artifact"


def test_interrupted_publish_preserves_previous_canonical(tmp_path, monkeypatch):
    """If broken: a failed copy exposes partial bytes at the consumer path.
    Scope: one source/canonical pair with a deliberately interrupted copy."""
    source = tmp_path / "drain_capacity_bbmp.tif"
    canonical = tmp_path / "drain_capacity.tif"
    source.write_bytes(b"new-complete-source")
    canonical.write_bytes(b"previous-complete-canonical")

    def interrupted_copy(source_path: Path, target_path: Path) -> None:
        target_path.write_bytes(b"partial")
        raise OSError("deliberate interrupted copy")

    monkeypatch.setattr(shutil, "copy2", interrupted_copy)
    with pytest.raises(OSError, match="interrupted copy"):
        _publish_canonical_artifacts({source: canonical})

    assert canonical.read_bytes() == b"previous-complete-canonical"
    assert list(tmp_path.glob(".drain_capacity.tif.*.tmp")) == []


def test_bbmp_drain_rasters_match_grid_specs_when_realized():
    """Realized raster check (V1): verify canonical and buffered BBMP drain rasters.
    V5 red demonstration: on a clean machine without the BBMP boundary file
    (data/raw/boundary/bbmp_wards_2023_final.kml) this test previously failed"""
    cfg = load_config(REPO / "configs" / "domain_bengaluru.yaml")
    # Gate boundary precondition before build_grid (V8 seam: grid requires boundary)
    boundary_path = REPO / cfg["boundary"]["path"]
    _require_realized(boundary_path)
    grid, _ = build_grid(cfg, REPO)
    buf_grid = grid.buffered(float(cfg["dem"]["buffer_m"]))

    canonical_paths = [
        INTERIM / "distance_to_drain_bbmp.tif",
        INTERIM / "drain_capacity_bbmp.tif",
        PROCESSED / "distance_to_drain_bbmp.tif",
        PROCESSED / "drain_capacity_bbmp.tif",
    ]

    _require_realized(*canonical_paths)
    for p in canonical_paths:
        with rasterio.open(p) as ds:
            assert ds.shape == (grid.height, grid.width), f"{p} shape mismatch: {ds.shape}"
            assert ds.crs.to_string() == str(grid.crs), f"{p} CRS mismatch: {ds.crs}"
            assert ds.dtypes[0] == "float32", f"{p} dtype mismatch: {ds.dtypes[0]}"
            data = ds.read(1)
            assert np.isfinite(data).all(), f"{p} contains non-finite values (NaN/Inf)"

    buffered_paths = [
        INTERIM / "distance_to_drain_bbmp_buffered.tif",
        INTERIM / "drain_capacity_bbmp_buffered.tif",
        PROCESSED / "buffered" / "distance_to_drain_bbmp.tif",
        PROCESSED / "buffered" / "drain_capacity_bbmp.tif",
    ]

    _require_realized(*buffered_paths)
    for p in buffered_paths:
        with rasterio.open(p) as ds:
            assert ds.shape == (buf_grid.height, buf_grid.width), f"{p} shape mismatch: {ds.shape}"
            assert ds.crs.to_string() == str(buf_grid.crs), f"{p} CRS mismatch: {ds.crs}"
            assert ds.dtypes[0] == "float32", f"{p} dtype mismatch: {ds.dtypes[0]}"
            data = ds.read(1)
            assert np.isfinite(data).all(), f"{p} contains non-finite values (NaN/Inf)"


def test_osm_and_bbmp_rasters_coexist_side_by_side():
    """Verify OSM-derived rasters were preserved and not deleted or overwritten."""
    osm_dist = INTERIM / "distance_to_drain_osm.tif"
    bbmp_dist = INTERIM / "distance_to_drain_bbmp.tif"
    osm_cap = INTERIM / "drain_capacity_osm.tif"
    bbmp_cap = INTERIM / "drain_capacity_bbmp.tif"
    osm_gpkg = INTERIM / "waterways_osm.gpkg"
    bbmp_gpkg = INTERIM / "waterways_bbmp.gpkg"

    _require_realized(osm_dist, bbmp_dist, osm_cap, bbmp_cap, osm_gpkg, bbmp_gpkg)

    gdf_osm = gpd.read_file(osm_gpkg)
    gdf_bbmp = gpd.read_file(bbmp_gpkg)
    assert not gdf_osm.empty
    assert not gdf_bbmp.empty

    with rasterio.open(osm_dist) as ds_o, rasterio.open(bbmp_dist) as ds_b:
        arr_o = ds_o.read(1)
        arr_b = ds_b.read(1)
        assert (arr_b == 0.0).sum() > (arr_o == 0.0).sum()
        assert not np.array_equal(arr_o, arr_b)


def test_drain_manifest_v8_guaranteed_properties():
    """Verify V8 manifest contract: guaranteed properties recorded for consumers."""
    manifest_path = RUNS / "manifest.json"
    _require_realized(manifest_path)

    m = json.loads(manifest_path.read_text())
    assert m["stage"] == "phase1_terrain_drains"
    assert m["status"] == "completed"
    assert isinstance(m["source"], str) and m["source"]
    assert m["feature_count"] > 0
    assert m["crs"] == "EPSG:32643"
    assert m["total_length_km"] > 0.0

    # aggregate as a class and crashed before testing the V8 contract.
    cb = m["class_breakdown"]
    assert cb
    classes = {name: item for name, item in cb.items() if name != "total"}
    assert classes
    assert sum(item["features_valid"] for item in classes.values()) == m["feature_count"]
    assert sum(item["length_km"] for item in classes.values()) == pytest.approx(
        m["total_length_km"], rel=1e-5
    )
    assert cb["total"]["features_total"] == m["feature_count"]
    assert cb["total"]["total_length_km"] == pytest.approx(m["total_length_km"], rel=1e-5)

    assert m["assumed_uncalibrated"] is True
    assert m["is_prior"] is True
    assert "PRIOR" in m["capacity_status"]


def test_consumer_side_drain_manifest_assertion_passes():
    """Verify consumer-side load_domain executes without error on valid manifest."""
    _require_realized(RUNS / "manifest.json", PROCESSED / "buffered" / "elevation.tif")
    with open(REPO / "configs" / "solver.yaml") as f:
        solver_cfg = yaml.safe_load(f)
    with open(REPO / solver_cfg["domain_config"]) as f:
        solver_cfg["_domain"] = yaml.safe_load(f)

    static = load_domain(solver_cfg, REPO, use_buffered=True)
    assert static is not None


def test_consumer_side_drain_manifest_assertion_red_under_mutations():
    """V5: direct seam assertions redden under deliberate in-memory mutations."""
    orig_manifest = {
        "source": "bbmp",
        "feature_count": 1,
        "total_length_m": 10.0,
        "crs": "EPSG:32643",
        "class_breakdown": {"primary": {"features_valid": 1, "length_km": 0.01}},
        "assumed_uncalibrated": True,
    }
    assert_drain_manifest_contract(orig_manifest, "EPSG:32643")

    # Mutation 1: Missing source
    m1 = dict(orig_manifest)
    del m1["source"]
    with pytest.raises(ValueError, match="missing guaranteed property: 'source'"):
        assert_drain_manifest_contract(m1, "EPSG:32643")

    # Mutation 2: Wrong CRS
    m2 = dict(orig_manifest)
    m2["crs"] = "EPSG:4326"
    with pytest.raises(ValueError, match="Drain CRS mismatch"):
        assert_drain_manifest_contract(m2, "EPSG:32643")

    # Mutation 3: assumed_uncalibrated set to False (violates prior status)
    m3 = dict(orig_manifest)
    m3["assumed_uncalibrated"] = False
    with pytest.raises(ValueError, match="must preserve assumed_uncalibrated=True"):
        assert_drain_manifest_contract(m3, "EPSG:32643")


def test_capacity_prior_mathematical_consistency():
    _require_realized(INTERIM / "distance_to_drain_bbmp.tif", INTERIM / "drain_capacity_bbmp.tif")
    with (
        rasterio.open(INTERIM / "distance_to_drain_bbmp.tif") as ds_d,
        rasterio.open(INTERIM / "drain_capacity_bbmp.tif") as ds_c,
    ):
        dist = ds_d.read(1)
        cap = ds_c.read(1)

    cfg = load_config(REPO / "configs" / "domain_bengaluru.yaml")
    baseline = float(next(iter(cfg["drains"]["baseline_capacity_by_landuse_mm_per_hr"].values())))
    decay_m = float(cfg["drains"]["distance_decay"]["decay_m"])

    drain_cells = dist == 0.0
    assert np.allclose(cap[drain_cells], baseline, atol=1e-5)

    # Check analytical formula across arbitrary sample points
    expected = (baseline * np.exp(-dist / decay_m)).astype(np.float32)
    assert np.allclose(cap, expected, atol=1e-5)
