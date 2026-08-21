"""Underpass corridor carving — goal Part 3.

A ONE-CELL HOLE IS NOT A BATHTUB. Lowering one cell by 3 m gives a one-cell
pit with no catchment. An underpass floods because the road surface on both
approaches drains into the dip, so the carve follows the road corridor down
to the derived invert and back up, using segment geometry — never a point.

Method, per selected segment:

  1. Take the register segment's own geometry (the road under the deck) and
     its derived invert (underpass_invert.py).
  2. Approach elevations z_a, z_b are the DEM elevations at the segment
     endpoints (measured).
  3. Along the segment, the road surface is lowered to
         profile(d) = invert + grade * |d - d_crossing|
     where d_crossing is the along-segment distance of the lowest DEM cell
     (the deck crossing) and grade = 6% [DERIVED stated assumption: urban
     underpass approach grade, IRC:54 typical practice]. The profile is
     capped at the original DEM: new_z = min(orig_z, profile) — cells whose
     original elevation is already below the profile are untouched.
  4. The corridor is the segment buffered by the highway-class half-width
     from the domain config (roughness.road_buffer_halfwidth_m).

Every modified cell must NOT be inside a building footprint
(building_mask_buffered.tif) and NOT inside a registered basin interior
(basin_class 1-4). Both are asserted; any violation aborts the carve.

Outputs (NEW files only; the frozen conditioned DEM is never touched):

  - data/interim/terrain/dem_conditioned_postbreach_variant_carve.tif
  - report: cells modified, volume removed, contributing catchment at each
    affected GT point BEFORE and AFTER (D8 redelineation on the variant).

FROZEN-DEM RULE: the source `dem_conditioned_postbreach.tif` is read-only
here. The variant carries a `_variant` suffix and a manifest stating its
provenance (parent DEM, carve inputs, git SHA).
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import typer
from rasterio.features import rasterize
from shapely.geometry import Point

from jaladhar.analysis.gt_classification import BUFFER_CELLS
from jaladhar.analysis.water_tracer import (
    build_flow_pointer_and_accumulation,
    delineate_upstream_catchment_d8,
)

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

APPROACH_GRADE = 0.06  # [DERIVED] stated assumption; urban underpass approach grade
VARIANT_NAME = "dem_conditioned_postbreach_variant_carve.tif"


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def carve_corridors(
    domain_cfg: dict[str, Any],
    invert_csv: Path,
    register_gpkg: Path,
    dem_path: Path,
    building_mask_path: Path,
    basin_class_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """Carve the corridor dips into a NEW variant DEM; report everything."""
    invert = pd.read_csv(invert_csv, low_memory=False)
    invert["osm_id"] = invert["osm_id"].astype(str)
    passable = invert[(invert["passes_sanity"]) & (invert["confidence"] != "LOW")].copy()
    if len(passable) == 0:
        raise RuntimeError("no segments pass sanity + confidence gate; refusing to carve")

    reg_gdf = gpd.read_file(register_gpkg)
    reg_gdf["osm_id"] = reg_gdf["osm_id"].astype(str)
    seg_by_id = reg_gdf.set_index("osm_id")

    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float64)
        profile = src.profile.copy()
    with rasterio.open(building_mask_path) as src:
        building_mask = src.read(1).astype(bool)
    with rasterio.open(basin_class_path) as src:
        basin_class = src.read(1)
    basin_interior = (basin_class >= 1) & (basin_class <= 4)

    h, w = dem.shape
    half_widths = domain_cfg["roughness"]["road_buffer_halfwidth_m"]

    variant = dem.copy()
    modified_mask = np.zeros((h, w), dtype=bool)
    per_segment: list[dict[str, Any]] = []

    for _, row in passable.iterrows():
        osm_id = row["osm_id"]
        if osm_id not in seg_by_id.index:
            continue
        geom = seg_by_id.loc[osm_id].geometry
        length = geom.length
        if length < 5.0:
            continue

        highway = str(row.get("highway", ""))
        half_w = float(half_widths.get(highway, half_widths.get("default", 3.5)))
        corridor = geom.buffer(half_w)

        # crossing: along-segment distance of the lowest DEM cell under the deck
        n_pts = max(5, int(np.ceil(length / 5.0)))
        dists = np.linspace(0, length, n_pts)
        zs = []
        for d in dists:
            pt = geom.interpolate(d)
            col = int((pt.x - profile["transform"].c) / profile["transform"].a)
            row_i = int((profile["transform"].f - pt.y) / (-profile["transform"].e))
            if 0 <= row_i < h and 0 <= col < w:
                zs.append(float(dem[row_i, col]))
            else:
                zs.append(np.nan)
        zs = np.array(zs)
        if np.isnan(zs).all():
            continue
        d_cross = float(dists[int(np.nanargmin(zs))])
        invert_z = float(row["invert_z_operating_m"])
        z_a = float(row["approach_z_min_m"])
        if not np.isfinite(z_a):
            continue

        # rasterize corridor
        corr_mask = rasterize(
            [(corridor, 1)], out_shape=(h, w), transform=profile["transform"], fill=0
        ).astype(bool)

        # profile per cell: distance from crossing along the road line
        rr, cc = np.where(corr_mask)
        xs = profile["transform"].c + (cc + 0.5) * profile["transform"].a
        ys = profile["transform"].f + (rr + 0.5) * profile["transform"].e
        pts = gpd.GeoSeries([Point(x, y) for x, y in zip(xs, ys, strict=True)], crs="EPSG:32643")
        along = np.array([geom.project(p) for p in pts.geometry])
        prof = invert_z + APPROACH_GRADE * np.abs(along - d_cross)

        target = np.minimum(dem[rr, cc], prof)
        change = dem[rr, cc] - target
        do_change = (change > 0.005) & ~building_mask[rr, cc] & ~basin_interior[rr, cc]

        if not do_change.any():
            per_segment.append(
                {
                    "osm_id": osm_id,
                    "cells_modified": 0,
                    "volume_removed_m3": 0.0,
                    "skipped_reason": "no cells lowered (already below profile or all blocked)",
                }
            )
            continue

        variant[rr[do_change], cc[do_change]] = target[do_change]
        modified_mask[rr[do_change], cc[do_change]] = True

        n_cells = int(do_change.sum())
        volume = float(np.sum(change[do_change]) * 100.0)
        per_segment.append(
            {
                "osm_id": osm_id,
                "highway": highway,
                "half_width_m": half_w,
                "cells_modified": n_cells,
                "volume_removed_m3": round(volume, 1),
                "skipped_reason": "",
            }
        )

    n_modified = int(modified_mask.sum())
    volume_total = float(np.sum((dem - variant) * 100.0))

    out_path = out_dir / VARIANT_NAME
    profile.update(dtype=rasterio.float64)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(variant, 1)

    # assertions: carve touches no building footprint, no registered basin interior
    building_violations = int(np.sum(modified_mask & building_mask))
    basin_violations = int(np.sum(modified_mask & basin_interior))

    report = {
        "variant_path": str(out_path.relative_to(REPO)),
        "n_segments_carved": sum(1 for s in per_segment if not s["skipped_reason"]),
        "n_segments_skipped": sum(1 for s in per_segment if s["skipped_reason"]),
        "cells_modified": n_modified,
        "volume_removed_m3": round(volume_total, 1),
        "building_footprint_violations": building_violations,
        "registered_basin_interior_violations": basin_violations,
        "per_segment": per_segment,
        "approach_grade": APPROACH_GRADE,
    }
    return report


def redelineate_gt_catchments(
    variant_path: Path, runs_dir: Path, gt_csv_path: Path, depth_max_path: Path
) -> list[dict[str, Any]]:
    """Recompute upstream catchment + depth for the SUB-GRID GT points on the variant."""
    import pyproj

    pntr, flowacc, to_r, to_c = build_flow_pointer_and_accumulation(
        variant_path, runs_dir / "routing_scratch_variant"
    )
    with rasterio.open(depth_max_path) as src:
        t = src.transform
    gt_df = pd.read_csv(gt_csv_path)
    trans = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)

    results = []
    for _, row in gt_df.iterrows():
        x, y = trans.transform(float(row["lon"]), float(row["lat"]))
        r_c = int((t.f - y) / (-t.e))
        c_c = int((x - t.c) / t.a)
        r_b, c_b = r_c + BUFFER_CELLS, c_c + BUFFER_CELLS
        m = delineate_upstream_catchment_d8(r_b, c_b, to_r, to_c)
        results.append(
            {
                "point_id": str(row["id"]),
                "catchment_cells": int(m.sum()),
                "catchment_km2": round(int(m.sum()) * 100.0 / 1e6, 6),
            }
        )
    return results


@app.command()
def main(
    analysis_config: Path = typer.Option(
        REPO / "configs/analysis.yaml", help="Analysis config (DEM/basin paths)"
    ),
    domain_config: Path = typer.Option(
        REPO / "configs/domain_bengaluru.yaml", help="Domain config (roughness widths)"
    ),
    out_dir: Path = typer.Option(
        REPO / "data/interim/terrain", help="Output directory (new files only)"
    ),
) -> None:
    """Carve underpass corridors into a NEW variant DEM; report + manifest."""
    import yaml

    analysis_cfg = yaml.safe_load(analysis_config.read_text())["paths"]
    domain_cfg = yaml.safe_load(domain_config.read_text())
    invert_csv = out_dir / "underpass_invert_derivation.csv"
    register_gpkg = out_dir / "unrepresentative_underpass_register.gpkg"
    dem_path = REPO / analysis_cfg["conditioned_dem"]
    building_mask_path = out_dir / "building_mask_buffered.tif"
    basin_class_path = out_dir / "basin_class_buffered.tif"
    gt_csv = REPO / analysis_cfg["groundtruth_csv"]
    depth_max_path = REPO / analysis_cfg["depth_event_max"]

    for p in [invert_csv, register_gpkg, dem_path, building_mask_path, basin_class_path]:
        if not p.exists():
            raise FileNotFoundError(f"missing input: {p}")

    t0 = time.perf_counter()
    runs_dir = REPO / "runs" / "underpass_carve"
    runs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = runs_dir / "manifest.json"
    manifest: dict[str, Any] = {
        "stage": "underpass_corridor_carve",
        "status": "running",
        "git_sha": git_sha(),
        "start_time_iso": datetime.now(UTC).isoformat(),
        "parent_dem": str(dem_path.relative_to(REPO)),
        "variant_name": VARIANT_NAME,
        "approach_grade": APPROACH_GRADE,
        "building_mask": str(building_mask_path.relative_to(REPO)),
        "basin_class": str(basin_class_path.relative_to(REPO)),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        report = carve_corridors(
            domain_cfg,
            invert_csv,
            register_gpkg,
            dem_path,
            building_mask_path,
            basin_class_path,
            out_dir,
        )
        if (
            report["building_footprint_violations"]
            or report["registered_basin_interior_violations"]
        ):
            raise RuntimeError(
                "CARVE ABORTED: would touch building footprints "
                f"({report['building_footprint_violations']}) or registered basin interiors "
                f"({report['registered_basin_interior_violations']})"
            )

        before_df = pd.read_csv(
            REPO / "runs/analysis/water_tracing/gt_classification.csv", low_memory=False
        )
        after = redelineate_gt_catchments(
            REPO / report["variant_path"], runs_dir, gt_csv, depth_max_path
        )
        after_df = pd.DataFrame(after)
        gt_catchments = []
        for _, b in before_df.iterrows():
            pid = str(b["point_id"])
            a = after_df[after_df["point_id"] == pid]
            if len(a) == 0:
                continue
            gt_catchments.append(
                {
                    "point_id": pid,
                    "classification": b["classification"],
                    "catchment_cells_before": int(b["upstream_catchment_cells"]),
                    "catchment_km2_before": float(b["upstream_catchment_km2"]),
                    "catchment_cells_after": int(a.iloc[0]["catchment_cells"]),
                    "catchment_km2_after": float(a.iloc[0]["catchment_km2"]),
                }
            )
        report["gt_catchments_before_after"] = gt_catchments
    except Exception:
        manifest["status"] = "failed"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise

    wall_clock = time.perf_counter() - t0
    manifest.update(
        {
            "status": "completed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": round(wall_clock, 2),
            "report": report,
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2))

    typer.echo("=" * 80)
    typer.echo("UNDERPASS CORRIDOR CARVE")
    typer.echo("=" * 80)
    typer.echo(
        f"Segments carved: {report['n_segments_carved']}, "
        f"skipped: {report['n_segments_skipped']}"
    )
    typer.echo(f"Cells modified: {report['cells_modified']:,}")
    typer.echo(f"Volume removed: {report['volume_removed_m3']:,.1f} m3")
    typer.echo(
        f"Violations: buildings={report['building_footprint_violations']}, "
        f"basins={report['registered_basin_interior_violations']}"
    )
    typer.echo("")
    typer.echo("GT CATCHMENTS BEFORE/AFTER:")
    for g in report.get("gt_catchments_before_after", []):
        typer.echo(
            f"  {g['point_id']:6} [{g['classification']:16}] "
            f"{g['catchment_cells_before']:>7,} -> {g['catchment_cells_after']:>7,} cells "
            f"({g['catchment_km2_before']:.4f} -> {g['catchment_km2_after']:.4f} km2)"
        )
    typer.echo(f"Variant: {report['variant_path']}")
    typer.echo(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    app()
