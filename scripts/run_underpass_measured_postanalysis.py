"""Post-run analysis for the measured-clearance underpass carve run.

Reads the measured variant's depth_event_maximum + baseline depth raster,
computes canonical-grid depths at all 24 GT points (lat/lon -> EPSG:32643 ->
raster row/col), and checks:

  1. Pre-registered bars per validated location (GT_06 window [1.20, 2.95]).
  2. Non-regression: every GT point not in the carve set must move <= 0.01 m
     vs baseline (GT_06 is in the carve set; GT_22 is 763 m from the Madiwala
     carve and expected unaffected — its movement is reported regardless).
  3. Madiwala carved-cell ponding (the 22 new cells = diff between the
     measured variant and the existing carve variant) + water-surface-vs-deck
     overfill check at the deepest carved cell.

Repro: PYTHONPATH=. .venv/bin/python scripts/run_underpass_measured_postanalysis.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyproj
import rasterio

BASE = Path("/home/darshil/Desktop/sih/clginternal")
RUN_DIR = BASE / "runs/underpass_measured/full_48h_variant"
VAR_D = RUN_DIR / "depth_event_maximum.tif"
BASE_D = BASE / "runs/phase3_validation/depth_event_maximum.tif"
PARENT_DEM = BASE / "data/interim/terrain/dem_conditioned_postbreach.tif"
VAR_DEM = BASE / "runs/underpass_measured/dem_conditioned_postbreach_variant_measured.tif"
OLD_CARVE_DEM = BASE / "data/interim/terrain/dem_conditioned_postbreach_variant_carve.tif"
GT = BASE / "data/raw/groundtruth/sept2022_points.csv"
OUT_CSV = RUN_DIR / "gt_depths_vs_baseline_measured.csv"
PREREG = BASE / "runs/underpass_measured/PREREGISTRATION.md"

GT06_WINDOW = (1.20, 1.45 + 1.5)  # [band_low, band_high + deck_op], GT_06 convention
NONREG_DELTA_M = 0.01
CARVED_BUF_OFFSET = 50  # depth rasters are canonical; DEMs are buffered (+50 rows/cols)


def rc_from_xy(x, y, transform):
    col = int(np.floor((x - transform.c) / transform.a))
    row = int(np.floor((y - transform.f) / transform.e))
    return row, col


def main() -> int:
    assert VAR_D.exists(), f"run output missing: {VAR_D} — the 48 h run has not happened"
    with rasterio.open(VAR_D) as s:
        hv = s.read(1)
        tv = s.transform
        tv_crs = s.crs
    with rasterio.open(BASE_D) as s:
        hb = s.read(1)
        tb = s.transform
    assert tv == tb, "variant and baseline transforms differ"

    with rasterio.open(VAR_DEM) as s:
        zv = s.read(1)
    with rasterio.open(PARENT_DEM) as s:
        zp = s.read(1)
    with rasterio.open(OLD_CARVE_DEM) as s:
        zo = s.read(1)
    assert zv.shape == zp.shape == zo.shape, "DEM shapes differ"

    diff_all = zv != zp
    br, bc = np.where(diff_all)
    new_cells = (zv != zo)  # 22 Madiwala cells = measured variant minus existing carve
    nr, nc = np.where(new_cells)
    print(f"carved cells vs parent: {len(br)} (measured manifest: 131)")
    print(f"Madiwala new cells vs existing carve variant: {len(nr)} (carve manifest: 22)")

    gt = pd.read_csv(GT)
    tf = pyproj.Transformer.from_crs(4326, tv_crs, always_xy=True)
    rows = []
    carve_set = set()
    for _, g in gt.iterrows():
        x, y = tf.transform(g["lon"], g["lat"])
        r, c = rc_from_xy(x, y, tv)
        in_carve = bool((zv[r + CARVED_BUF_OFFSET, c + CARVED_BUF_OFFSET]
                         != zp[r + CARVED_BUF_OFFSET, c + CARVED_BUF_OFFSET]))
        if in_carve:
            carve_set.add(str(g["id"]))
        rows.append(
            {
                "id": g["id"],
                "row": r,
                "col": c,
                "depth_variant_m": float(hv[r, c]),
                "depth_baseline_m": float(hb[r, c]),
                "delta_m": round(float(hv[r, c]) - float(hb[r, c]), 4),
                "band_low": g.get("depth_band_low_m"),
                "band_high": g.get("depth_band_high_m"),
                "in_carve_set": in_carve,
                "non_regression": bool(
                    in_carve or abs(float(hv[r, c]) - float(hb[r, c])) <= NONREG_DELTA_M
                ),
            }
        )
    out = pd.DataFrame(rows)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    print(f"\ncarve-set GT points: {sorted(carve_set)}")
    print(out.to_string(index=False))

    failed = []
    g6 = out[out["id"] == "GT_06"].iloc[0]
    lo, hi = GT06_WINDOW
    win = "IN" if lo <= g6["depth_variant_m"] <= hi else "OUT"
    moved = abs(g6["depth_variant_m"] - g6["depth_baseline_m"]) >= 0.1
    print(f"\nGT_06 window [{lo}, {hi}]: variant {g6['depth_variant_m']:.3f} m "
          f"(baseline {g6['depth_baseline_m']:.3f}) -> {win} | moved >= 0.1: {moved}")
    if win != "IN":
        failed.append("GT_06 outside pre-registered window")

    nreg = out[~out["in_carve_set"] & ~out["non_regression"]]
    print(f"\nnon-regression: {len(out) - len(nreg)}/{len(out)} points OK "
          f"(carve set {sorted(carve_set)}); violations:")
    if len(nreg):
        print(nreg.to_string(index=False))
        failed.append(f"{len(nreg)} non-regression violations")

    # Madiwala ponding: depth at the 22 new cells (canonical depth coords)
    cr, cc = nr - CARVED_BUF_OFFSET, nc - CARVED_BUF_OFFSET
    depths = hv[cr, cc]
    wet = depths > 0.01
    print(f"\nMadiwala carved cells (n={len(depths)}): wet {int(wet.sum())}, "
          f"max {depths.max():.2f} m, median wet "
          f"{np.median(depths[wet]):.2f} m" if wet.any() else
          f"Madiwala carved cells (n={len(depths)}): ALL DRY")
    if not wet.any():
        failed.append("Madiwala carve did not pond (all carved cells dry)")

    i = int(np.argmax(depths))
    surf = zv[nr[i], nc[i]] + depths[i]
    near = zp[max(0, nr[i] - 1):nr[i] + 2, max(0, nc[i] - 1):nc[i] + 2].max()
    overfill = surf - near
    print(f"deepest carved cell (canonical {cr[i]},{cc[i]}) h={depths[i]:.2f} m")
    print(f"water surface {surf:.2f} m vs parent-DEM max in 3x3 {near:.2f} m "
          f"(overfill by {overfill:+.2f} m)")
    if overfill > 0.05:
        failed.append("Madiwala deepest carved cell overfills the deck rim by > 0.05 m")

    print(f"\n=== RESULT: {'BARS MET' if not failed else 'BARS FAILED'} ===")
    for f in failed:
        print("  FAIL:", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())