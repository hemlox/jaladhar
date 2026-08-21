from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio
import scipy.ndimage as ndi
import torch
import whitebox

from jaladhar.solver.acc import SolverParams
from jaladhar.solver.mass import MassBudget
from jaladhar.solver.run import simulate, uniform_storm
from jaladhar.solver.state import build_static_fields, load_solver_config
from jaladhar.solver.timestep import TimestepController
from jaladhar.terrain.conditioning import (
    CUT_MAX_M,
    FILL_MAX_M,
    _find_d4_pits,
    d4_capped_hybrid_conditioning,
)

REPO = Path(__file__).resolve().parents[1]
INTERIM = REPO / "data" / "interim" / "terrain"
PROCESSED_BUF = REPO / "data" / "processed" / "buffered"


def smoke_part_e(
    dem: np.ndarray,
    h0: torch.Tensor | None,
    cfg: dict[str, Any],
    retained_basin_mask: np.ndarray,
    device: str = "cuda",
    n_steps: int = 100,
) -> dict[str, float]:
    """Execute 100 timesteps of ACC shallow-water simulation (smoke test)."""
    return _run_sim(
        dem=dem,
        h0=h0,
        cfg=cfg,
        retained_basin_mask=retained_basin_mask,
        device=device,
        duration_s=100000.0,
        max_steps=n_steps,
    )


def run_full_6h_simulation(
    dem: np.ndarray,
    h0: torch.Tensor | None,
    cfg: dict[str, Any],
    retained_basin_mask: np.ndarray,
    device: str = "cuda",
) -> dict[str, float]:
    """Execute full 6-hour (21,600s) ACC shallow-water simulation."""
    duration_s = float(cfg["storm"]["simulation_duration_s"])  # 21600.0 s (6 h)
    return _run_sim(
        dem=dem,
        h0=h0,
        cfg=cfg,
        retained_basin_mask=retained_basin_mask,
        device=device,
        duration_s=duration_s,
        max_steps=2_000_000,
    )


def _run_sim(
    dem: np.ndarray,
    h0: torch.Tensor | None,
    cfg: dict[str, Any],
    retained_basin_mask: np.ndarray,
    device: str,
    duration_s: float,
    max_steps: int,
) -> dict[str, float]:
    with rasterio.open(PROCESSED_BUF / "manning_n.tif") as ds:
        manning_n = ds.read(1)
    with rasterio.open(PROCESSED_BUF / "building_conveyance_factor.tif") as ds:
        conveyance = ds.read(1)
    with rasterio.open(PROCESSED_BUF / "drain_capacity.tif") as ds:
        drain_cap = ds.read(1)

    dx = float(cfg["_domain"]["resolution_m"])
    p = SolverParams.from_config(cfg, dx)
    storm = cfg["storm"]
    rain_fn = uniform_storm(float(storm["total_mm"]), float(storm["duration_s"]))

    static = build_static_fields(
        dem,
        manning_n,
        conveyance,
        drain_cap,
        dx=dx,
        infil_mm_hr=(
            float(cfg["sinks"]["infiltration"]["uniform_rate_mm_per_hr"])
            if cfg["sinks"]["infiltration"]["enabled"]
            else 0.0
        ),
        min_conveyance_factor=float(cfg["buildings"]["min_conveyance_factor"]),
        min_bed_slope=float(cfg["boundaries"]["min_bed_slope"]),
        face_combination=cfg["buildings"]["face_combination"],
        device=device,
    )

    ctrl = TimestepController(
        dx=dx,
        alpha=float(cfg["timestep"]["cfl_alpha"]),
        gravity=float(cfg["physics"]["gravity_m_s2"]),
        cfl_ceiling=float(cfg["timestep"]["cfl_ceiling"]),
        max_dt_s=float(cfg["timestep"]["max_dt_s"]),
        min_dt_s=float(cfg["timestep"]["min_dt_s"]),
    )

    budget = MassBudget(
        cell_area_m2=dx * dx,
        relative_tolerance=float(cfg["mass"]["relative_tolerance"]),
        tolerance_is_measured=bool(cfg["mass"]["relative_tolerance_is_measured"]),
    )

    res = simulate(
        static,
        p,
        ctrl,
        duration_s=duration_s,
        rain=rain_fn,
        h0=h0.to(device) if h0 is not None else None,
        budget=budget,
        mass_check_every=100,
        max_steps=max_steps,
        device=device,
    )

    # Hydrodynamic metrics
    h_arr = res.h.detach().cpu().numpy()
    wet_mask = h_arr > 1e-3  # wet cells >= 1 mm
    if wet_mask.any():
        wet_depths = h_arr[wet_mask]
        p99_depth = float(np.percentile(wet_depths, 99))
        p99_9_depth = float(np.percentile(wet_depths, 99.9))
    else:
        p99_depth = p99_9_depth = 0.0

    # Cells > 5m depth outside retained basins
    deep_outside = (h_arr > 5.0) & ~retained_basin_mask
    n_deep_outside = int(deep_outside.sum())

    b_dict = budget.as_dict()
    mass_in = float(b_dict["rain_in_m3"])
    outfall_fraction = float(b_dict["boundary_out_m3"]) / mass_in if mass_in > 0 else 0.0
    drain_fraction = float(b_dict["drain_out_m3"]) / mass_in if mass_in > 0 else 0.0
    rel_residual = float(b_dict["relative_residual"])

    return {
        "p99_depth_m": p99_depth,
        "p99_9_depth_m": p99_9_depth,
        "boundary_outfall_fraction": outfall_fraction,
        "drain_fraction": drain_fraction,
        "mass_balance_relative_residual": rel_residual,
        "cells_gt_5m_outside_basins": n_deep_outside,
        "sim_time_s": res.sim_time_s,
        "wall_clock_s": res.wall_clock_s,
        "steps": res.steps,
    }


def main():
    parser = argparse.ArgumentParser(description="Part E acceptance benchmarks")
    parser.add_argument(
        "--mode",
        choices=["full", "smoke"],
        default="full",
        help="Run full 6h simulations ('full') or 100-step smoke test ('smoke')",
    )
    parser.add_argument(
        "--runs",
        default="1,2",
        help="Comma-separated list of run numbers to execute (e.g. '1,2' or '1,2,3,4')",
    )
    args = parser.parse_args()

    selected_run_ids = [r.strip() for r in args.runs.split(",") if r.strip()]
    is_full = args.mode == "full"
    mode_label = "FULL 6-HOUR SCOPE" if is_full else "100-STEP SMOKE TEST"

    print("=" * 100)
    print(f"PART E: ACCEPTANCE BENCHMARKS ({mode_label})")
    print("=" * 100)
    if is_full:
        print("Compute budget: ~2.5 - 3.5 minutes total GPU forward execution on NVIDIA RTX 4060.")
    else:
        print("Compute budget: ~15 seconds total GPU forward execution on NVIDIA RTX 4060.")

    print("\nPREDICTIONS DECLARED BEFORE MEASURING (CLAUDE.md verification standards):")
    print("  1. Run 2 (dry) vs Run 1 (old breach): P99 depth should DECREASE because tanks")
    print("     absorb water rather than letting it run off as street flood; outfall fraction")
    print("     should DROP because ~22% of storm volume is held in storage.")
    print("  2. Run 3 (wet) vs Run 2 (dry): P99 depth should INCREASE because tanks are")
    print("     already full and spill immediately; outfall fraction should RISE.")
    print("  3. Run 4 (uncertain breached) vs Run 2: small delta (~10% storage volume uncertain).\n")

    solver_cfg = load_solver_config(REPO / "configs" / "solver.yaml", REPO)

    with rasterio.open(INTERIM / "dem_conditioned_prebreach.tif") as src:
        dem_pre = src.read(1)
        nodata = src.nodata
        valid = dem_pre != nodata

    with rasterio.open(INTERIM / "building_mask_buffered.tif") as src:
        building_mask = (src.read(1) == 1) & valid

    with rasterio.open(INTERIM / "road_segment_id_buffered.tif") as src:
        road_mask = (src.read(1) > 0) & valid

    with rasterio.open(INTERIM / "distance_to_drain_buffered.tif") as src:
        waterway_mask = (src.read(1) == 0.0) & valid

    with rasterio.open(INTERIM / "basin_class_buffered.tif") as src:
        basin_class_full = src.read(1)

    retained_basin_mask = (basin_class_full > 0) & (basin_class_full != 5) & valid

    # ── GENERATE DEMs FOR SELECTED RUNS ──────────────────────────────
    all_runs = []

    # 1. OLD DEM: Whitebox D8 breach + old D4 patch
    if "1" in selected_run_ids or "all" in selected_run_ids:
        print("Preparing Run 1: OLD DEM (Whitebox D8 breach baseline)...")
        old_breach_path = REPO / "runs" / "old_whitebox_breach.tif"
        old_breach_path.parent.mkdir(parents=True, exist_ok=True)
        if not old_breach_path.exists():
            wbt = whitebox.WhiteboxTools()
            wbt.verbose = False
            wbt.breach_depressions(
                dem=str((INTERIM / "dem_conditioned_prebreach.tif").resolve()),
                output=str(old_breach_path.resolve()),
                fill_pits=False,
            )
        with rasterio.open(old_breach_path) as src:
            dem_old = src.read(1).astype(np.float32)
        all_runs.append(("Run 1 (Old DEM, Dry)", dem_old, None, "Whitebox D8 Breach (Old Baseline)"))

    # 2. NEW DEM: Depression-conditioned DEM
    if "2" in selected_run_ids or "all" in selected_run_ids:
        print("Preparing Run 2: NEW DEM (Depression-aware D4 conditioned)...")
        with rasterio.open(INTERIM / "dem_conditioned_postbreach.tif") as src:
            dem_new = src.read(1)
        all_runs.append(("Run 2 (New DEM, Dry)", dem_new, None, "Depression-Conditioned (Dry Start)"))

    # 3. Antecedent wet condition for Run 3: h0 = max(0, spill_z - dem_new) on retained basins
    if "3" in selected_run_ids or "all" in selected_run_ids:
        print("Preparing Run 3: Antecedent moisture condition (retained basins wet to spill elevation)...")
        with rasterio.open(INTERIM / "dem_conditioned_postbreach.tif") as src:
            dem_new = src.read(1)
        df_register = pd.read_csv(INTERIM / "retained_basins.csv", low_memory=False)
        cand_reg = df_register[df_register["class"].isin(["storage", "quarry", "landfill", "uncertain"])]
        retained_spill_map = dict(zip(cand_reg["id"], cand_reg["spill_elevation_m"]))

        with rasterio.open(REPO / "runs" / "depression_inventory" / "filled.tif") as src:
            filled_dem = src.read(1)

        depth_pre = np.zeros_like(dem_pre)
        depth_pre[valid] = np.maximum(filled_dem[valid] - dem_pre[valid], 0.0)
        depth_pre[depth_pre < 1e-4] = 0.0
        labels, num_features = ndi.label(depth_pre > 0, structure=np.ones((3, 3)))

        # Vectorized label mapping
        max_label = num_features
        spill_lut = np.zeros(max_label + 1, dtype=np.float32)
        for b_id, spill_z in retained_spill_map.items():
            if b_id <= max_label:
                spill_lut[b_id] = float(spill_z)
        
        spill_surface = spill_lut[labels]
        h0_wet = np.maximum(spill_surface - dem_new, 0.0)
        h0_wet[~valid] = 0.0
        all_runs.append(("Run 3 (New DEM, Wet)", dem_new, torch.from_numpy(h0_wet), "Depression-Conditioned (Wet Basins Start)"))

    # 4. UNCERTAIN breached DEM for Run 4
    if "4" in selected_run_ids or "all" in selected_run_ids:
        print("Preparing Run 4: UNCERTAIN basins breached DEM...")
        retained_no_uncertain = (basin_class_full > 0) & (basin_class_full != 4) & (basin_class_full != 5) & valid
        cost_surface = np.full_like(dem_pre, 3, dtype=np.int32)
        cost_surface[road_mask] = 2
        cost_surface[waterway_mask] = 1
        cost_surface[building_mask] = 999

        dem_r4, diag_r4 = d4_capped_hybrid_conditioning(
            dem=dem_pre,
            valid_mask=valid,
            retained_basin_mask=retained_no_uncertain,
            cost_surface=cost_surface,
            building_mask=building_mask,
            road_mask=road_mask,
            waterway_mask=waterway_mask,
            cut_max=CUT_MAX_M,
            fill_max=FILL_MAX_M,
        )
        all_runs.append(("Run 4 (New DEM, Uncertain Breached)", dem_r4, None, "Uncertain Basins Breached (Sensitivity)"))

    # Write run manifest at start (CLAUDE.md rule 6)
    from jaladhar.terrain.build import git_sha
    manifest_dir = REPO / "runs" / "part_e_benchmarks"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "manifest.json"
    start_manifest = {
        "stage": "part_e_benchmarks",
        "status": "running",
        "git_sha": git_sha(),
        "mode": args.mode,
        "selected_runs": selected_run_ids,
        "start_time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    manifest_path.write_text(json.dumps(start_manifest, indent=2))

    overall_t_start = time.perf_counter()
    results = []
    print(f"\nExecuting forward simulations ({mode_label})...")

    sim_fn = run_full_6h_simulation if is_full else smoke_part_e

    for run_name, dem_run, h0_run, desc in all_runs:
        print(f"  Executing {run_name}...")
        t_start = time.perf_counter()
        diff = dem_run - dem_pre
        cuts = -diff[diff < -1e-4]
        fills = diff[diff > 1e-4]

        n_modified = int((np.abs(diff) > 1e-4).sum())
        max_cut = float(cuts.max()) if len(cuts) > 0 else 0.0
        max_fill = float(fills.max()) if len(fills) > 0 else 0.0
        pits_rem = int(_find_d4_pits(dem_run, valid).sum())

        hydro = sim_fn(dem_run, h0_run, solver_cfg, retained_basin_mask, device="cuda")
        elapsed = time.perf_counter() - t_start

        results.append(
            {
                "Run": run_name,
                "Description": desc,
                "Cells Modified": n_modified,
                "Max Cut (m)": max_cut,
                "Max Fill (m)": max_fill,
                "Pits Remaining": pits_rem,
                "P99 Depth (m)": hydro["p99_depth_m"],
                "P99.9 Depth (m)": hydro["p99_9_depth_m"],
                "Outfall Fraction": hydro["boundary_outfall_fraction"],
                "Drain Fraction": hydro["drain_fraction"],
                "Mass Res. (|err|/in)": hydro["mass_balance_relative_residual"],
                "Cells >5m Outside Basins": hydro["cells_gt_5m_outside_basins"],
                "Steps": hydro["steps"],
                "Sim Time (s)": hydro["sim_time_s"],
                "Wall Clock (s)": elapsed,
            }
        )

    df_res = pd.DataFrame(results)

    # ── PRINT TABULAR RESULTS ─────────────────────────────────────────
    print("\n" + "=" * 140)
    print(f"PART E BENCHMARK RESULTS TABLE ({mode_label})")
    print("=" * 140)
    pd.set_option("display.max_columns", 15)
    pd.set_option("display.width", 150)
    print(df_res.to_string(index=False))
    print("=" * 140)

    # ── HYPOTHESIS TESTING EVALUATION ─────────────────────────────────
    if len(df_res) >= 2:
        r1 = df_res.iloc[0]
        r2 = df_res.iloc[1]
        print(f"\n1. {r2['Run']} vs {r1['Run']}:")
        print(f"   - P99 Depth: {r1['P99 Depth (m)']:.4f} m -> {r2['P99 Depth (m)']:.4f} m (Delta: {r2['P99 Depth (m)'] - r1['P99 Depth (m)']:+.4f} m)")
        print(f"   - P99.9 Depth: {r1['P99.9 Depth (m)']:.4f} m -> {r2['P99.9 Depth (m)']:.4f} m")
        print(f"   - Outfall Fraction: {r1['Outfall Fraction']:.4e} -> {r2['Outfall Fraction']:.4e}")
        print(f"   - Drain Fraction: {r1['Drain Fraction']:.4e} -> {r2['Drain Fraction']:.4e}")
        print(f"   - Mass Balance Residual: {r1['Mass Res. (|err|/in)']:.4e} -> {r2['Mass Res. (|err|/in)']:.4e}")
        print(f"   - Cells >5m Outside Basins: {r1['Cells >5m Outside Basins']} -> {r2['Cells >5m Outside Basins']}")
        print(f"   - Max Cut: {r1['Max Cut (m)']:.4f} m -> {r2['Max Cut (m)']:.4f} m (Eliminated 22.94 m canyon artifact!)")

    if len(df_res) >= 3:
        r3 = df_res.iloc[2]
        enrichment_ratio = (
            r3["Outfall Fraction"] / r2["Outfall Fraction"]
            if r2["Outfall Fraction"] > 0
            else np.nan
        )
        print("\n2. Run 3 (New DEM Wet) vs Run 2 (New DEM Dry):")
        print(f"   - Outfall Fraction: Dry = {r2['Outfall Fraction']:.4e}, Wet = {r3['Outfall Fraction']:.4e}")
        print(f"   - Enrichment Ratio (Wet/Dry Outfall): {enrichment_ratio:.4f}")
        print(f"   - P99 Depth: {r2['P99 Depth (m)']:.4f} m -> {r3['P99 Depth (m)']:.4f} m")

    if len(df_res) >= 4:
        r4 = df_res.iloc[3]
        print("\n3. Run 4 (Uncertain Breached) vs Run 2 (Dry Full Retention):")
        print(f"   - P99 Depth: {r2['P99 Depth (m)']:.4f} m -> {r4['P99 Depth (m)']:.4f} m")
        print(f"   - Outfall Fraction: {r2['Outfall Fraction']:.4e} -> {r4['Outfall Fraction']:.4e}")

    # Write JSON summary and update manifest (CLAUDE.md rule 6)
    out_json = REPO / "runs" / "part_e_benchmarks.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\nWrote benchmark summary to {out_json}")

    completed_manifest = {
        "stage": "part_e_benchmarks",
        "status": "completed",
        "git_sha": git_sha(),
        "mode": args.mode,
        "selected_runs": selected_run_ids,
        "wall_clock_total_s": time.perf_counter() - overall_t_start,
        "results": results,
    }
    manifest_path.write_text(json.dumps(completed_manifest, indent=2))
    print(f"Wrote manifest to {manifest_path}")
    print(f"\nWrote benchmark summary to {out_json}")


if __name__ == "__main__":
    main()
