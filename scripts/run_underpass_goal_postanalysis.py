"""Part 4c post-run analysis for the full 48h variant run (underpass goal).

Reads variant depth_event_maximum + baseline depth_event_maximum, computes
canonical-grid depths at all 24 GT points via lat/lon -> EPSG:32643
reprojection then raster row/col, reports the GT_06 flood-window check,
GT_17/GT_16 non-regression, and carved-dip ponding (including a water
surface vs approach-level overfill check at the deepest carved cell).

Repro: python scripts/run_underpass_goal_postanalysis.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyproj
import rasterio

BASE = Path("/home/darshil/Desktop/sih/clginternal")
VAR_D = BASE / "runs/underpass_goal/full_48h_variant/depth_event_maximum.tif"
BASE_D = BASE / "runs/phase3_validation/depth_event_maximum.tif"
PARENT_DEM = BASE / "data/interim/terrain/dem_conditioned_postbreach.tif"
VAR_DEM = BASE / "data/interim/terrain/dem_conditioned_postbreach_variant_carve.tif"
GT = BASE / "data/raw/groundtruth/sept2022_points.csv"
OUT_CSV = BASE / "runs/underpass_goal/full_48h_variant/gt_depths_vs_baseline.csv"

# Pre-registered bar (PART4_PREREGISTRATION.md):
#   GT_06 flood window = [band_low, band_high + 1.5] with observed [1.20, 1.45]
#   GT_17 must stay within [0.85, 1.10] (non-regression vs baseline 1.65 over-prediction)
DECK_OPERATING_M = 1.5
WINDOW = {"GT_06": (1.20, 1.45 + DECK_OPERATING_M)}
GT17 = {"GT_17": (0.85, 1.10)}
CARVED_BUF_OFFSET = 50  # buffered grid is canonical + 50 rows/cols


def rc_from_xy(x, y, transform):
    col = int(np.floor((x - transform.c) / transform.a))
    row = int(np.floor((y - transform.f) / transform.e))
    return row, col


def main():
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
    assert zv.shape == zp.shape, "variant and parent DEM shapes differ"
    diff = zv != zp
    br, bc = np.where(diff)
    print(f"realized carved cells: {len(br)} (carve manifest reports 109)")

    gt = pd.read_csv(GT)
    tf = pyproj.Transformer.from_crs(4326, tv_crs, always_xy=True)
    rows = []
    for _, g in gt.iterrows():
        x, y = tf.transform(g["lon"], g["lat"])
        r, c = rc_from_xy(x, y, tv)
        rows.append(
            {
                "id": g["id"],
                "row": r,
                "col": c,
                "depth_variant_m": float(hv[r, c]),
                "depth_baseline_m": float(hb[r, c]),
                "band_low": g.get("depth_band_low_m"),
                "band_high": g.get("depth_band_high_m"),
            }
        )
    out = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    print(out.to_string(index=False))

    g6 = out[out["id"] == "GT_06"].iloc[0]
    lo, hi = WINDOW["GT_06"]
    win = "IN" if lo <= g6["depth_variant_m"] <= hi else "OUT"
    print(f"\nGT_06 flood window [{lo}, {hi}]: variant {g6['depth_variant_m']:.3f} m "
          f"(baseline {g6['depth_baseline_m']:.3f}) -> {win}")
    g17 = out[out["id"] == "GT_17"].iloc[0]
    lo17, hi17 = GT17["GT_17"]
    win17 = "IN" if lo17 <= g17["depth_variant_m"] <= hi17 else "OUT"
    print(f"GT_17 band [{lo17}, {hi17}]: variant {g17['depth_variant_m']:.3f} m "
          f"(baseline {g17['depth_baseline_m']:.3f}) -> {win17}")

    # carved-dip ponding: carved cells are on the buffered grid (offset from canonical)
    cr, cc = br - CARVED_BUF_OFFSET, bc - CARVED_BUF_OFFSET
    depths = hv[cr, cc]
    print(
        f"\ncarved cells (n={len(depths)}): variant wet {int((depths > 0.01).sum())}, "
        f"max {depths.max():.2f} m, median wet {np.median(depths[depths > 0.01]):.2f} m"
    )

    i = int(np.argmax(depths))
    print(f"deepest carved cell canonical ({cr[i]},{cc[i]}) h={depths[i]:.2f} m")

    surf = zv[br[i], bc[i]] + depths[i]
    near = zp[max(0, br[i] - 1):br[i] + 2, max(0, bc[i] - 1):bc[i] + 2].max()
    print(f"water surface {surf:.2f} m vs parent-DEM max in 3x3 {near:.2f} m "
          f"(overfill by {surf - near:+.2f} m)")


if __name__ == "__main__":
    sys.exit(main())