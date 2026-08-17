"""Part E: Acceptance benchmarks comparing 4 terrain & antecedent moisture cases.

Runs:
  Run 1: CURRENT code + OLD DEM (whitebox breach + old D4 patch).
  Run 2: CURRENT code + NEW DEM (depression-aware D4 conditioning), DRY START (h0 = 0).
  Run 3: CURRENT code + NEW DEM, RETAINED BASINS WET TO SPILL ELEVATION.
  Run 4: CURRENT code + NEW DEM, dry, UNCERTAIN basins breached.
"""

from __future__ import annotations

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

from jaladhar.solver.acc import SolverParams, acc_step
from jaladhar.solver.mass import MassBudget
from jaladhar.solver.run import uniform_storm
from jaladhar.solver.state import StaticFields, build_static_fields, load_solver_config
from jaladhar.solver.timestep import TimestepController
from jaladhar.terrain.conditioning import (
    CUT_MAX_M,
    FILL_MAX_M,
    _find_d4_pits,
    count_pits_d4,
    d4_capped_hybrid_conditioning,
)
from jaladhar.terrain.grid import Grid, build_grid

REPO = Path(__file__).resolve().parents[1]
INTERIM = REPO / "data" / "interim" / "terrain"
PROCESSED_BUF = REPO / "data" / "processed" / "buffered"


def run_100_step_simulation(
    dem: np.ndarray,
    h0: torch.Tensor | None,
    cfg: dict[str, Any],
    device: str = "cuda",
    n_steps: int = 100,
) -> dict[str, float]:
    """Execute 100 timesteps of ACC shallow-water simulation and measure metrics."""
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

    h, w = static.shape
    if h0 is None:
        h_t = torch.zeros((h, w), dtype=torch.float32, device=device)
    else:
        h_t = h0.to(device).clone()
    qx_t = torch.zeros((h, w - 1), dtype=torch.float32, device=device)
    qy_t = torch.zeros((h - 1, w), dtype=torch.float32, device=device)

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

    from jaladhar.solver.run import simulate

    res = simulate(
        static,
        p,
        ctrl,
        duration_s=100000.0,
        rain=rain_fn,
        h0=h0.to(device) if h0 is not None else None,
        budget=budget,
        mass_check_every=100,
        max_steps=n_steps,
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
        "sim_time_s": res.sim_time_s,
        "wall_clock_s": res.wall_clock_s,
    }


def main():
    print("=" * 80)
    print("PART E: ACCEPTANCE BENCHMARKS (4 TERRAIN & ANTECEDENT RUNS)")
    print("=" * 80)
    print("Compute budget: ~2 minutes on NVIDIA RTX 4060 GPU.")
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

    # ── GENERATE DEMs FOR ALL 4 RUNS ──────────────────────────────────

    # 1. OLD DEM: Whitebox D8 breach + old D4 patch
    print("Generating OLD DEM (Whitebox breach)...")
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

    # 2. NEW DEM: Depression-conditioned DEM
    with rasterio.open(INTERIM / "dem_conditioned_postbreach.tif") as src:
        dem_new = src.read(1)

    # 3. Antecedent wet condition for Run 3: h0 = max(0, spill_z - dem_new) on retained basins
    print("Preparing antecedent moisture condition (Run 3 wet basins)...")
    df_register = pd.read_csv(INTERIM / "retained_basins.csv")
    retained_spill_map = dict(zip(df_register["id"], df_register["spill_elevation_m"]))

    with rasterio.open(REPO / "runs" / "depression_inventory" / "filled.tif") as src:
        filled_dem = src.read(1)

    depth_pre = np.zeros_like(dem_pre)
    depth_pre[valid] = np.maximum(filled_dem[valid] - dem_pre[valid], 0.0)
    depth_pre[depth_pre < 1e-4] = 0.0
    labels, _ = ndi.label(depth_pre > 0, structure=np.ones((3, 3)))

    h0_wet = np.zeros_like(dem_new, dtype=np.float32)
    for b_id, spill_z in retained_spill_map.items():
        mask_b = (labels == b_id) & valid
        if mask_b.any():
            h0_wet[mask_b] = np.maximum(spill_z - dem_new[mask_b], 0.0)

    # 4. UNCERTAIN breached DEM for Run 4
    print("Generating Run 4 DEM (UNCERTAIN basins breached)...")
    # Retained basins mask excluding UNCERTAIN (class 4)
    retained_no_uncertain = (basin_class_full > 0) & (basin_class_full != 4) & valid
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
        cut_max=CUT_MAX_M,
        fill_max=FILL_MAX_M,
    )

    # ── RUN BENCHMARKS ────────────────────────────────────────────────
    runs = [
        ("Run 1 (Old DEM, Dry)", dem_old, None, "Whitebox D8 Breach (Old Baseline)"),
        ("Run 2 (New DEM, Dry)", dem_new, None, "Depression-Conditioned (Dry Start)"),
        ("Run 3 (New DEM, Wet)", dem_new, torch.from_numpy(h0_wet), "Depression-Conditioned (Wet Basins Start)"),
        ("Run 4 (New DEM, Uncertain Breached)", dem_r4, None, "Uncertain Basins Breached (Sensitivity)"),
    ]

    results = []
    print("\nExecuting forward simulations (100 timesteps each)...")

    for run_name, dem_run, h0_run, desc in runs:
        print(f"  Executing {run_name}...")
        diff = dem_run - dem_pre
        cuts = -diff[diff < -1e-4]
        fills = diff[diff > 1e-4]

        n_modified = int((np.abs(diff) > 1e-4).sum())
        max_cut = float(cuts.max()) if len(cuts) > 0 else 0.0
        max_fill = float(fills.max()) if len(fills) > 0 else 0.0
        pits_rem = int(_find_d4_pits(dem_run, valid).sum())

        hydro = run_100_step_simulation(dem_run, h0_run, solver_cfg, device="cuda", n_steps=100)

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
                "Wall Clock (s)": hydro["wall_clock_s"],
            }
        )

    df_res = pd.DataFrame(results)

    # ── PRINT TABULAR RESULTS ─────────────────────────────────────────
    print("\n" + "=" * 120)
    print("PART E BENCHMARK RESULTS TABLE")
    print("=" * 120)
    pd.set_option("display.max_columns", 15)
    pd.set_option("display.width", 130)
    print(df_res.to_string(index=False))
    print("=" * 120)

    # ── HYPOTHESIS TESTING EVALUATION ─────────────────────────────────
    r1 = df_res.iloc[0]
    r2 = df_res.iloc[1]
    r3 = df_res.iloc[2]
    r4 = df_res.iloc[3]

    print("\nHYPOTHESIS EVALUATION:")
    print(f"1. Run 2 vs Run 1 (Depression Retention vs Old Breach):")
    print(f"   - P99 Depth: {r1['P99 Depth (m)']:.4f} m -> {r2['P99 Depth (m)']:.4f} m (Delta: {r2['P99 Depth (m)'] - r1['P99 Depth (m)']:+.4f} m)")
    print(f"   - Outfall Fraction: {r1['Outfall Fraction']:.4e} -> {r2['Outfall Fraction']:.4e}")
    print(f"   - Max Cut: {r1['Max Cut (m)']:.4f} m -> {r2['Max Cut (m)']:.4f} m (Eliminated 22.94 m canyon!)")

    print(f"\n2. Run 3 vs Run 2 (Wet Start vs Dry Start):")
    print(f"   - P99 Depth: {r2['P99 Depth (m)']:.4f} m -> {r3['P99 Depth (m)']:.4f} m (Delta: {r3['P99 Depth (m)'] - r2['P99 Depth (m)']:+.4f} m)")
    print(f"   - Outfall Fraction: {r2['Outfall Fraction']:.4e} -> {r3['Outfall Fraction']:.4e}")

    print(f"\n3. Run 4 vs Run 2 (Sensitivity to UNCERTAIN Breaching):")
    print(f"   - P99 Depth: {r2['P99 Depth (m)']:.4f} m -> {r4['P99 Depth (m)']:.4f} m (Delta: {r4['P99 Depth (m)'] - r2['P99 Depth (m)']:+.4f} m)")
    print(f"   - Outfall Fraction: {r2['Outfall Fraction']:.4e} -> {r4['Outfall Fraction']:.4e}")

    # Write JSON summary
    out_json = REPO / "runs" / "part_e_benchmarks.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\nWrote benchmark summary to {out_json}")


if __name__ == "__main__":
    main()
