"""Step 0 — reproduce the existing underpass carve pipeline and compare realized
state against the committed manifests (V1, V4).

Runs the committed pipeline functions (import only, src/jaladhar is read-only)
with the committed inputs:
  - derive_inverts(register, clearance, gpkg, bridges, parent DEM, GT csv)
  - carve_corridors(domain cfg, derived csv, register gpkg, parent DEM,
    building mask, basin class, out_dir=runs/underpass_measured/repro)

and compares against:
  - runs/underpass_invert/manifest.json   (derivation summary)
  - runs/underpass_carve/manifest.json    (carve report: 109 cells, 42373.3 m3,
                                           22 carved, 1 skipped)

If any committed number does not reproduce, the goal STOPS here per the
goal brief ("Realized must match the committed manifest").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from jaladhar.terrain.underpass_carve import carve_corridors
from jaladhar.terrain.underpass_invert import derive_inverts

REPO = Path("/home/darshil/Desktop/sih/clginternal")
TERRAIN = REPO / "data/interim/terrain"
REPRO_DIR = REPO / "runs/underpass_measured/repro"


def main() -> int:
    analysis_cfg = yaml.safe_load((REPO / "configs/analysis.yaml").read_text())["paths"]
    domain_cfg = yaml.safe_load((REPO / "configs/domain_bengaluru.yaml").read_text())

    invert_csv = TERRAIN / "underpass_invert_derivation.csv"
    register_csv = TERRAIN / "unrepresentative_underpass_register.csv"
    clearance_csv = TERRAIN / "underpass_register_clearance.csv"
    register_gpkg = TERRAIN / "unrepresentative_underpass_register.gpkg"
    bridges_gpkg = TERRAIN / "underpass_bridges.gpkg"
    parent_dem = REPO / analysis_cfg["conditioned_dem"]
    gt_csv = REPO / analysis_cfg["groundtruth_csv"]
    building_mask = TERRAIN / "building_mask_buffered.tif"
    basin_class = TERRAIN / "basin_class_buffered.tif"

    for p in [invert_csv, register_csv, clearance_csv, register_gpkg, parent_dem, gt_csv]:
        assert p.exists(), f"missing: {p}"

    committed_invert = json.loads((REPO / "runs/underpass_invert/manifest.json").read_text())
    committed_carve = json.loads((REPO / "runs/underpass_carve/manifest.json").read_text())

    failures: list[str] = []

    # --- 1. invert derivation (in memory; nothing written) -------------------
    out, summary = derive_inverts(
        register_csv, clearance_csv, register_gpkg, bridges_gpkg, parent_dem, gt_csv
    )
    committed_sum = committed_invert["summary"]
    keys = [
        "n_register_rows",
        "n_derived_rows",
        "n_excluded_long_no_crossing",
        "n_sanity_invert_above_approach",
        "n_sanity_dip_outside_range",
        "n_passes_sanity",
        "irc_fallback_used_rows",
    ]
    print("=== INVERT DERIVATION REPRO ===")
    for k in keys:
        got, want = summary[k], committed_sum[k]
        ok = got == want
        print(f"  {k:38} realized={got!r:>12} committed={want!r:>12} {'OK' if ok else 'MISMATCH'}")
        if not ok:
            failures.append(f"invert {k}: {got} != {want}")
    if summary["confidence_counts"] != committed_sum["confidence_counts"]:
        failures.append(
            f"invert confidence_counts: {summary['confidence_counts']} != {committed_sum['confidence_counts']}"
        )
        print(f"  confidence_counts MISMATCH realized={summary['confidence_counts']} committed={committed_sum['confidence_counts']}")
    else:
        print(f"  confidence_counts realized==committed: {summary['confidence_counts']}")

    # GT comparisons: spot-check all rows against committed
    gc_got = {c["gt_id"]: c for c in summary["gt_comparisons"]}
    gc_want = {c["gt_id"]: c for c in committed_sum["gt_comparisons"]}
    def eq(a, b):
        try:
            if a is None and b is None:
                return True
            fa, fb = float(a), float(b)
            if fa != fa and fb != fb:  # NaN == NaN
                return True
            return fa == fb
        except (TypeError, ValueError):
            return a == b

    n_gt_mismatch = 0
    for gid, w in gc_want.items():
        g = gc_got.get(gid, {})
        for fld in ["nearest_segment_osm_id", "derived_invert_operating_m", "derived_implied_dip_depth_m",
                    "derived_confidence", "dip_covers_band_low"]:
            if not eq(g.get(fld), w.get(fld)):
                n_gt_mismatch += 1
                print(f"  GT {gid} {fld} MISMATCH realized={g.get(fld)!r} committed={w.get(fld)!r}")
    if n_gt_mismatch:
        failures.append(f"invert gt_comparisons: {n_gt_mismatch} field mismatches")
    else:
        print(f"  gt_comparisons: all {len(gc_want)} points match committed")

    # --- 2. carve reproduction into repro dir (new file, never the frozen one) ----
    print("\n=== CARVE REPRO ===")
    REPRO_DIR.mkdir(parents=True, exist_ok=True)
    report = carve_corridors(
        domain_cfg, invert_csv, register_gpkg, parent_dem, building_mask, basin_class, REPRO_DIR
    )
    committed_rep = committed_carve["report"]
    for k in ["n_segments_carved", "n_segments_skipped", "cells_modified", "volume_removed_m3",
              "building_footprint_violations", "registered_basin_interior_violations"]:
        got, want = report[k], committed_rep[k]
        ok = got == want
        print(f"  {k:38} realized={got!r:>12} committed={want!r:>12} {'OK' if ok else 'MISMATCH'}")
        if not ok:
            failures.append(f"carve {k}: {got} != {want}")
    per_got = {(p["osm_id"], p["cells_modified"], p["volume_removed_m3"], p["skipped_reason"]) for p in report["per_segment"]}
    per_want = {(p["osm_id"], p["cells_modified"], p["volume_removed_m3"], p["skipped_reason"]) for p in committed_rep["per_segment"]}
    if per_got == per_want:
        print(f"  per_segment: all {len(per_got)} rows match committed")
    else:
        only_got = per_got - per_want
        only_want = per_want - per_got
        failures.append(f"carve per_segment mismatch: only_realized={only_got} only_committed={only_want}")
        print(f"  per_segment MISMATCH only_realized={only_got}\n  only_committed={only_want}")

    variant = REPRO_DIR / "dem_conditioned_postbreach_variant_carve.tif"
    print(f"\n  variant written: {variant} ({variant.stat().st_size} bytes)")
    print(f"\n=== RESULT: {'STOP — REPRODUCTION FAILED' if failures else 'REPRODUCTION OK (committed state matches realized)'} ===")
    for f in failures:
        print("  FAIL:", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())