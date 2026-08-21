"""Ground-truth point classification — goal Part 0.

For ALL 24 points in `data/raw/groundtruth/sept2022_points.csv`, using the
validated D8 routing machinery from `jaladhar.analysis.water_tracer` (its
Part 0 delineation gate already passes; this module does NOT rebuild it —
it reuses `build_flow_pointer_and_accumulation` and
`delineate_upstream_catchment_d8` directly, and reuses the cached scratch
intermediates when present):

  1. Upstream contributing catchment area, in cells and km2.
  2. Distance to nearest underpass-register segment
     (`data/interim/terrain/unrepresentative_underpass_register.gpkg`).
  3. Whether a registered basin (basin_class 1-4) lies upstream.
  4. Classification into exactly one of:
       CATCHMENT-SCALE  real upstream catchment and/or upstream lake (GT_17's regime)
       SUB-GRID         near-zero contributing area + tagged underpass within a stated distance
       NEITHER          everything else, described
  5. Counts per class, plus a cross-check against the ground-truth CSV's
     own `notes` field; every disagreement is reported.

CLASSIFICATION RULE (stated before classifying):

  * A = upstream D8 contributing area of the exact GT cell (cells, km2).
  * d = distance from the GT point to the nearest underpass-register segment.
  * upstream_basin = any cell with basin_class in {1,2,3,4} inside the
    upstream catchment.

  - CATCHMENT-SCALE:  A >= 1.0 km2  OR  upstream_basin == True.
  - SUB-GRID:         A <  0.1 km2  AND  d <= 150 m.
  - NEITHER:          otherwise (A in [0.1, 1.0) km2, or A < 0.1 km2 with
                      no underpass within 150 m, or A >= 1.0 km2 with no
                      upstream basin — impossible given the rule above).

  Rationale for the thresholds: the four already-traced points span
  1 cell (GT_05) to 158 km2 (GT_17). A "real catchment" for street-level
  pluvial flooding in a 717 km2 city with ~150 m of rain is at least a
  square kilometre — below that, whatever water reaches the cell is
  local, and a local regime with an underpass within 150 m (15 cells) is
  the sub-grid signature (deck-on-DEM). These thresholds are stated
  before any classification happens, per the goal.

The event-maximum modelled depth at each point (exact cell and 3x3 max)
is also recorded, so per-class model performance can be read directly
from this report.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
import rasterio
import typer
import yaml

from jaladhar.analysis.water_tracer import (
    build_flow_pointer_and_accumulation,
    delineate_upstream_catchment_d8,
)

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

CANONICAL_SHAPE = (3421, 3515)
BUFFER_CELLS = 50
CLASS_RULE_DOC = (
    "CATCHMENT-SCALE: A >= 1.0 km2 OR upstream basin (class 1-4) present; "
    "SUB-GRID: A < 0.1 km2 AND nearest register segment <= 150 m; "
    "NEITHER: otherwise."
)


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


@dataclass
class GtClassification:
    point_id: str
    location_name: str
    lat: float
    lon: float
    observed_date: str
    depth_band_low_m: float
    depth_band_high_m: float
    notes: str
    canonical_row: int
    canonical_col: int
    buffered_row: int
    buffered_col: int
    upstream_catchment_cells: int
    upstream_catchment_km2: float
    distance_to_nearest_underpass_m: float
    nearest_underpass_osm_id: str
    nearest_underpass_dem_behavior: str
    upstream_basin_present: bool
    upstream_basin_classes: str
    upstream_basin_class_counts: str
    event_max_depth_exact_cell_m: float
    event_max_depth_3x3_max_m: float
    classification: str
    classification_evidence: str
    notes_crosscheck: str


def resolve_classification_config(cfg: dict[str, Any]) -> None:
    missing: list[str] = []
    paths = cfg.get("paths", {})
    for pkey in [
        "conditioned_dem",
        "basin_class",
        "depth_event_max",
        "groundtruth_csv",
        "underpass_register_gpkg",
        "runs_dir",
    ]:
        if pkey not in paths:
            missing.append(f"paths.{pkey}")
        elif not (REPO / paths[pkey]).exists() and pkey != "runs_dir":
            missing.append(f"paths.{pkey} (file not found: {paths[pkey]})")
    if missing:
        raise KeyError(f"Pre-flight configuration check failed: missing/invalid keys: {missing}")


def load_gt_points(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["depth_band_low_m"] = pd.to_numeric(df["depth_band_low_m"], errors="coerce")
    df["depth_band_high_m"] = pd.to_numeric(df["depth_band_high_m"], errors="coerce")
    return df


def classify_all_gt_points(
    cfg: dict[str, Any],
) -> list[GtClassification]:
    """Run the full classification for all 24 ground-truth points."""
    conditioned_dem = REPO / cfg["paths"]["conditioned_dem"]
    basin_class_path = REPO / cfg["paths"]["basin_class"]
    depth_max_path = REPO / cfg["paths"]["depth_event_max"]
    gt_csv_path = REPO / cfg["paths"]["groundtruth_csv"]
    register_gpkg_path = REPO / cfg["paths"]["underpass_register_gpkg"]
    runs_dir = REPO / cfg["paths"]["runs_dir"]
    runs_dir.mkdir(parents=True, exist_ok=True)

    # 1. D8 routing on the buffered conditioned DEM (cached scratch reused)
    pntr, flowacc, to_r, to_c = build_flow_pointer_and_accumulation(
        conditioned_dem, runs_dir / "routing_scratch"
    )
    h, w = to_r.shape
    if (h, w) != (3521, 3615):
        raise RuntimeError(f"Unexpected buffered routing shape {(h, w)}, expected (3521, 3615)")

    # 2. Rasters
    with rasterio.open(basin_class_path) as src:
        basin_class = src.read(1)
    with rasterio.open(depth_max_path) as src:
        depth_max = src.read(1)
        depth_transform = src.transform

    # 3. Register segments (projected to UTM 32643 by its producer)
    register = gpd.read_file(register_gpkg_path)
    register_proj = register.to_crs("EPSG:32643")

    # 4. Ground truth points
    gt_df = load_gt_points(gt_csv_path)
    trans = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)

    def rowcol_from_lonlat(lon: float, lat: float) -> tuple[int, int]:
        x, y = trans.transform(lon, lat)
        col = int((x - depth_transform.c) / depth_transform.a)
        row = int((depth_transform.f - y) / (-depth_transform.e))
        if not (0 <= row < depth_max.shape[0] and 0 <= col < depth_max.shape[1]):
            raise ValueError(f"point ({lon}, {lat}) outside canonical grid")
        return row, col

    results: list[GtClassification] = []
    for _, row in gt_df.iterrows():
        pid = str(row["id"])
        r_c, c_c = rowcol_from_lonlat(float(row["lon"]), float(row["lat"]))
        r_b, c_b = r_c + BUFFER_CELLS, c_c + BUFFER_CELLS

        # 1. Upstream contributing catchment (exact cell, D8 reverse BFS)
        c_mask = delineate_upstream_catchment_d8(r_b, c_b, to_r, to_c)
        n_cells = int(c_mask.sum())
        area_km2 = n_cells * 100.0 / 1e6

        # 2. Distance to nearest underpass-register segment
        gt_point = gpd.GeoSeries(
            [gpd.points_from_xy([float(row["lon"])], [float(row["lat"])], crs="EPSG:4326")[0]],
            crs="EPSG:4326",
        ).to_crs("EPSG:32643")[0]
        dists = register_proj.distance(gt_point)
        d_min = float(dists.min())
        nearest_idx = int(dists.argmin())
        nearest_osm = str(register_proj.iloc[nearest_idx]["osm_id"])
        nearest_behavior = str(register_proj.iloc[nearest_idx].get("dem_behavior", ""))

        # 3. Upstream registered basin (class 1-4)
        c_mask_canon = c_mask[
            BUFFER_CELLS : BUFFER_CELLS + CANONICAL_SHAPE[0],
            BUFFER_CELLS : BUFFER_CELLS + CANONICAL_SHAPE[1],
        ]
        up_classes = basin_class[c_mask_canon]
        classes_present = sorted(int(v) for v in np.unique(up_classes) if int(v) in (1, 2, 3, 4))
        upstream_basin = len(classes_present) > 0
        class_counts = {
            int(v): int(np.sum(up_classes == v))
            for v in np.unique(up_classes)
            if int(v) in (1, 2, 3, 4)
        }

        # Depth at exact cell and 3x3 max
        d_exact = float(depth_max[r_c, c_c])
        d_3x3 = float(depth_max[max(0, r_c - 1) : r_c + 2, max(0, c_c - 1) : c_c + 2].max())

        # Classification (rule stated in module docstring)
        if area_km2 >= 1.0 or upstream_basin:
            cls = "CATCHMENT-SCALE"
            evidence = (
                f"A={area_km2:.4f} km2 ({n_cells:,} cells); "
                f"upstream_basin={upstream_basin} (classes {classes_present})"
            )
        elif area_km2 < 0.1 and d_min <= 150.0:
            cls = "SUB-GRID"
            evidence = (
                f"A={area_km2:.4f} km2 ({n_cells:,} cells); "
                f"underpass {d_min:.1f} m (osm {nearest_osm}, {nearest_behavior})"
            )
        else:
            cls = "NEITHER"
            evidence = (
                f"A={area_km2:.4f} km2 ({n_cells:,} cells); "
                f"nearest underpass {d_min:.1f} m; upstream_basin={upstream_basin}"
            )

        # Cross-check against the CSV notes field.
        # Lake-mechanism keywords (tight, to avoid "stormwater overflow" false
        # positives): "lake" must co-occur with overflow/outlet/weir/kodi/
        # breach/spill, or the note names a kodi/weir/outlet directly.
        notes = str(row.get("notes", ""))
        notes_lower = notes.lower()
        lake_mech = (
            "lake" in notes_lower
            and any(
                w in notes_lower for w in ["overflow", "outlet", "weir", "kodi", "breach", "spill"]
            )
        ) or any(w in notes_lower for w in ["kodi junction", "weir", "lake outlet"])
        underpass_mech = any(
            w in notes_lower for w in ["underpass", "flyover", "railway underpass"]
        )
        if cls == "CATCHMENT-SCALE":
            if lake_mech:
                xcheck = "AGREE: notes describe lake/overflow mechanism"
            elif underpass_mech:
                xcheck = "DISAGREE: notes describe underpass/flyover, classified CATCHMENT-SCALE"
            else:
                xcheck = "NEUTRAL: notes do not name a mechanism"
        elif cls == "SUB-GRID":
            if underpass_mech:
                xcheck = "AGREE: notes describe underpass/flyover mechanism"
            elif lake_mech:
                xcheck = "DISAGREE: notes describe lake/overflow, classified SUB-GRID"
            else:
                xcheck = "NEUTRAL: notes do not name a mechanism"
        else:
            if lake_mech:
                xcheck = (
                    "DISAGREE: notes describe lake/overflow, but no lake/class-1 basin is "
                    "upstream of the exact cell in the D8 catchment (spill-path point)"
                )
            else:
                xcheck = "NEUTRAL: NEITHER class; notes: " + notes

        results.append(
            GtClassification(
                point_id=pid,
                location_name=str(row["location_name"]),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                observed_date=str(row.get("observed_date", "")),
                depth_band_low_m=(
                    float(row["depth_band_low_m"]) if pd.notna(row["depth_band_low_m"]) else np.nan
                ),
                depth_band_high_m=(
                    float(row["depth_band_high_m"])
                    if pd.notna(row["depth_band_high_m"])
                    else np.nan
                ),
                notes=notes,
                canonical_row=r_c,
                canonical_col=c_c,
                buffered_row=r_b,
                buffered_col=c_b,
                upstream_catchment_cells=n_cells,
                upstream_catchment_km2=round(area_km2, 6),
                distance_to_nearest_underpass_m=round(d_min, 2),
                nearest_underpass_osm_id=nearest_osm,
                nearest_underpass_dem_behavior=nearest_behavior,
                upstream_basin_present=upstream_basin,
                upstream_basin_classes=",".join(str(c) for c in classes_present),
                upstream_basin_class_counts=",".join(
                    f"{k}:{v}" for k, v in sorted(class_counts.items())
                ),
                event_max_depth_exact_cell_m=round(d_exact, 6),
                event_max_depth_3x3_max_m=round(d_3x3, 6),
                classification=cls,
                classification_evidence=evidence,
                notes_crosscheck=xcheck,
            )
        )

    return results


def run_classification(config_path: Path) -> dict[str, Any]:
    """Non-CLI entry: classify all 24 GT points, write report + manifest."""
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    resolve_classification_config(cfg)

    runs_dir = REPO / cfg["paths"]["runs_dir"]
    runs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = runs_dir / "manifest.json"

    t0 = time.perf_counter()
    manifest: dict[str, Any] = {
        "stage": "gt_point_classification",
        "status": "running",
        "git_sha": git_sha(),
        "start_time_iso": datetime.now(UTC).isoformat(),
        "classification_rule": CLASS_RULE_DOC,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        results = classify_all_gt_points(cfg)
    except Exception:
        manifest["status"] = "failed"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise

    rows = [asdict(r) for r in results]
    df = pd.DataFrame(rows)
    out_csv = runs_dir / "gt_classification.csv"
    df.to_csv(out_csv, index=False)

    counts = df["classification"].value_counts().to_dict()
    wall_clock = time.perf_counter() - t0

    per_class_depth = {}
    for cls in ["CATCHMENT-SCALE", "SUB-GRID", "NEITHER"]:
        sub = df[df["classification"] == cls]
        if len(sub):
            per_class_depth[cls] = {
                "n": int(len(sub)),
                "n_under_1cm": int((sub["event_max_depth_exact_cell_m"] < 0.01).sum()),
                "depth_min_m": float(sub["event_max_depth_exact_cell_m"].min()),
                "depth_median_m": float(sub["event_max_depth_exact_cell_m"].median()),
                "depth_max_m": float(sub["event_max_depth_exact_cell_m"].max()),
            }
        else:
            per_class_depth[cls] = {"n": 0}

    disagreements = df[df["notes_crosscheck"].str.startswith("DISAGREE")][
        ["point_id", "classification", "notes_crosscheck"]
    ].to_dict("records")

    manifest.update(
        {
            "status": "completed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": round(wall_clock, 2),
            "n_points": len(results),
            "classification_counts": counts,
            "per_class_model_performance": per_class_depth,
            "notes_disagreements": disagreements,
            "output_csv": str(out_csv.relative_to(REPO)),
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2))

    typer.echo("=" * 80)
    typer.echo("GT POINT CLASSIFICATION — ALL 24 POINTS")
    typer.echo(f"Rule: {CLASS_RULE_DOC}")
    typer.echo("=" * 80)
    for r in results:
        band = (
            f"{r.depth_band_low_m}-{r.depth_band_high_m}" if pd.notna(r.depth_band_low_m) else "n/a"
        )
        typer.echo(
            f"{r.point_id:6} {r.classification:16} A={r.upstream_catchment_km2:10.4f} km2 "
            f"({r.upstream_catchment_cells:>7,} cells)  "
            f"upass={r.distance_to_nearest_underpass_m:8.1f} m "
            f"basin={r.upstream_basin_present!s:5} depth={r.event_max_depth_exact_cell_m:8.4f} m "
            f"band={band}"
        )
    typer.echo("")
    typer.echo(f"Counts: {counts}")
    typer.echo("")
    typer.echo("PER-CLASS MODEL PERFORMANCE (event-max depth at exact cell):")
    for cls, stats in per_class_depth.items():
        if stats["n"] == 0:
            typer.echo(f"  {cls:16} n=0")
            continue
        typer.echo(
            f"  {cls:16} n={stats['n']:2d}  under-1cm={stats['n_under_1cm']:2d}  "
            f"depth min/med/max = {stats['depth_min_m']:.4f} / "
            f"{stats['depth_median_m']:.4f} / {stats['depth_max_m']:.4f} m"
        )
    typer.echo("")
    if disagreements:
        typer.echo("NOTES CROSS-CHECK DISAGREEMENTS:")
        for d in disagreements:
            typer.echo(f"  {d['point_id']:6} [{d['classification']}] {d['notes_crosscheck']}")
    typer.echo("")
    typer.echo(f"Wrote {out_csv}")
    typer.echo(f"Manifest: {manifest_path}")
    return manifest


@app.command()
def main(
    config: Path = typer.Option(REPO / "configs/analysis.yaml", help="Analysis config YAML"),
) -> None:
    """Classify all 24 ground-truth points; write report + manifest."""
    run_classification(config)


if __name__ == "__main__":
    app()
