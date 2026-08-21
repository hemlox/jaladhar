"""Phase 3 Validation Gate: September 2022 Event Replay and Uncalibrated Baseline Scoring.

PROMPT.md §8 & CLAUDE.md:
- Hard go/no-go gate for the project.
- Forces model with GPM IMERG historical half-hourly adapter over the Sept 2022 flood event.
- Evaluates uncalibrated baseline against three independent label sets:
  1. BBMP flood-prone locations (399 raw points across 3 KMLs: 70 + 129 + 200) -> hit rate and FAR
  2. Sentinel-1 SAR flood extent (06:10 IST 5 Sept 2022 SAR pass), STRATIFIED BY URBAN DENSITY,
     with and without permanent-water exclusion (basin_class 1, 2, 3), across a depth threshold sweep.
  3. Ground-truth geolocated flood points (24 points) -> depth as a band and depth RMSE.
- Evaluates against published bar (CSI ≈ 0.73, RMSE ≈ 0.17 m; Water 17(8):1239, 2025).
- Enforces strict anti-fabrication and Rule 6/7 provenance requirements.
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

import geopandas as gpd
import numpy as np
import pyproj
import rasterio
import scipy.ndimage
import torch
import typer
import yaml
from rasterio.crs import CRS
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds
from scipy.interpolate import Rbf, RegularGridInterpolator
from scipy.spatial import cKDTree

from jaladhar.forcing.imerg import ImergHistoricalAdapter
from jaladhar.provenance import RunManifest
from jaladhar.solver.acc import SolverParams, acc_step
from jaladhar.solver.mass import MassBudget
from jaladhar.solver.run import compute_budget_estimate, estimate_steps_band, write_dt_sidecar
from jaladhar.solver.state import load_domain, load_solver_config
from jaladhar.solver.timestep import TimestepController
from jaladhar.terrain.grid import build_grid
from jaladhar.validation.density import run_density_pipeline
from jaladhar.validation.groundtruth import (
    load_and_validate_groundtruth,
    resolve_groundtruth_scoring_config,
    select_event_groundtruth,
)
from jaladhar.validation.scoring import (
    score_threshold_curve,
)

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


def resolve_phase3_config(
    solver_cfg: dict[str, Any],
    forcing_cfg: dict[str, Any],
    val_cfg: dict[str, Any],
    compute_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Validate all required config keys at startup per CLAUDE.md Rule 7."""
    missing: list[str] = []

    # Solver keys
    for k in ["physics", "timestep", "wetdry", "buildings", "boundaries", "sinks", "mass"]:
        if k not in solver_cfg:
            missing.append(f"solver.{k}")

    # Forcing keys
    if forcing_cfg.get("mode") != "historical":
        missing.append("forcing.mode == 'historical'")
    if "historical" not in forcing_cfg or "granules_dir" not in forcing_cfg["historical"]:
        missing.append("forcing.historical.granules_dir")

    # Compute keys
    for key in ["anchor", "budget", "pool", "vram"]:
        if key not in compute_cfg:
            missing.append(f"compute.{key}")
    if "pool" in compute_cfg and not compute_cfg["pool"]:
        missing.append("compute.pool (at least one measured GPU worker)")
    if "vram" in compute_cfg and "safety_margin_fraction" not in compute_cfg["vram"]:
        missing.append("compute.vram.safety_margin_fraction")

    # Validation keys
    if "groundtruth" not in val_cfg:
        missing.append("validation.groundtruth")
    else:
        for key in [
            "scoring_event_window_start",
            "scoring_event_window_end",
            "max_snap_distance_m",
        ]:
            if key not in val_cfg["groundtruth"]:
                missing.append(f"validation.groundtruth.{key}")
    if "density_stratification" not in val_cfg:
        missing.append("validation.density_stratification")
    else:
        for key in ["output_density_classes_path"]:
            if key not in val_cfg["density_stratification"]:
                missing.append(f"validation.density_stratification.{key}")
    if "sar_water_classifier" not in val_cfg:
        missing.append("validation.sar_water_classifier")
    else:
        for key in [
            "flood_scene_tif",
            "flood_calibration_xml",
            "basin_class_path",
            "water_threshold_db",
        ]:
            if key not in val_cfg["sar_water_classifier"]:
                missing.append(f"validation.sar_water_classifier.{key}")
    if "groundtruth" in val_cfg:
        for key in ["bbmp_kml_dir", "road_segment_id_path"]:
            if key not in val_cfg["groundtruth"]:
                missing.append(f"validation.groundtruth.{key}")

    if missing:
        raise KeyError(
            f"Phase 3 config validation failed: missing keys {missing}. "
            "Resolve all config keys before simulation start."
        )

    return {
        "solver": solver_cfg,
        "forcing": forcing_cfg,
        "validation": val_cfg,
        "compute": compute_cfg,
    }


def compute_s1_sigma0_db(
    tif_path: Path,
    xml_path: Path,
    dst_bounds: Any,
    dst_shape: tuple[int, int],
    dst_transform: Any,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute calibrated Sentinel-1 VV sigma0 (dB) on target grid."""
    import xml.etree.ElementTree as ET

    with rasterio.open(tif_path) as src:
        gcps, _ = src.gcps
        with WarpedVRT(src, crs=CRS.from_epsg(32643)) as vrt:
            win = from_bounds(*dst_bounds, transform=vrt.transform)
            dn = vrt.read(1, window=win, out_shape=dst_shape).astype(np.float32)

    tree = ET.parse(xml_path)
    vectors = tree.getroot().find("calibrationVectorList")
    lut_lines = [int(vec.find("line").text) for vec in vectors]
    lut_pixels = np.fromstring(vectors[0].find("pixel").text, sep=" ", dtype=int)
    lut_sigma0 = np.array(
        [np.fromstring(vec.find("sigmaNought").text, sep=" ", dtype=np.float32) for vec in vectors]
    )
    interp = RegularGridInterpolator((lut_lines, lut_pixels), lut_sigma0, method="linear")

    gcp_lons = np.array([g.x for g in gcps])
    gcp_lats = np.array([g.y for g in gcps])
    gcp_cols = np.array([g.col for g in gcps])
    gcp_rows = np.array([g.row for g in gcps])

    rbf_row = Rbf(gcp_lons, gcp_lats, gcp_rows, function="linear")
    rbf_col = Rbf(gcp_lons, gcp_lats, gcp_cols, function="linear")

    trans_to_wgs = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)

    rows_idx = np.linspace(0, dst_shape[0] - 1, 100)
    cols_idx = np.linspace(0, dst_shape[1] - 1, 100)
    rr, cc = np.meshgrid(rows_idx, cols_idx, indexing="ij")
    xx, yy = rasterio.transform.xy(dst_transform, rr, cc)
    lons, lats = trans_to_wgs.transform(np.array(xx), np.array(yy))

    src_r = rbf_row(lons, lats)
    src_c = rbf_col(lons, lats)
    pts = np.column_stack([src_r.ravel(), src_c.ravel()])
    lut_samples = interp(pts).reshape(100, 100)

    zoom_factors = (dst_shape[0] / 100.0, dst_shape[1] / 100.0)
    asigma_full = scipy.ndimage.zoom(lut_samples, zoom_factors, order=1).astype(np.float32)

    valid = (dn > 0) & (asigma_full > 0)
    sigma0_lin = np.zeros_like(dn)
    sigma0_lin[valid] = (dn[valid] / asigma_full[valid]) ** 2

    sigma0_db = np.full(dst_shape, np.nan, dtype=np.float32)
    sigma0_db[valid] = 10.0 * np.log10(np.maximum(sigma0_lin[valid], 1e-7))
    return sigma0_db, valid


@dataclass
class SimulationOutput:
    steps: int
    sim_time_s: float
    wall_clock_s: float
    max_courant: float
    max_selected_courant: float
    rejected_steps: int
    retry_attempts: int
    h_canonical_final: np.ndarray
    h_canonical_max: np.ndarray
    h_sar_instant: np.ndarray
    sar_timestamp_iso: str
    mass_dict: dict[str, Any]
    depth_snapshots: list[tuple[float, str]]  # (sim_time_s, file_path)
    total_rain_volume_m3: float
    areal_mean_rain_mm: float
    distinct_imerg_cells: int
    native_cell_ids_canonical: np.ndarray
    cumulative_transport_depth_path: str
    cumulative_drain_depth_path: str
    cumulative_infiltration_depth_path: str
    final_depth_path: str
    final_depth_buffered_path: str


def groundtruth_road_snap_distances(
    points_utm: list[tuple[float, float]],
    road_segment_ids: np.ndarray,
    road_transform: Any,
) -> np.ndarray:
    """Measure each point's distance to the nearest realized road cell centre."""
    rows, cols = np.nonzero(road_segment_ids > 0)
    if rows.size == 0:
        raise ValueError("road segment raster contains no positive road cells")
    xs, ys = rasterio.transform.xy(road_transform, rows, cols, offset="center")
    tree = cKDTree(np.column_stack((xs, ys)))
    distances, _ = tree.query(np.asarray(points_utm, dtype=np.float64), k=1)
    return np.asarray(distances, dtype=np.float64)


def run_phase3_simulation(
    solver_cfg_path: Path = REPO / "configs/solver.yaml",
    forcing_cfg_path: Path = REPO / "configs/forcing.yaml",
    val_cfg_path: Path = REPO / "configs/validation.yaml",
    compute_cfg_path: Path = REPO / "configs/compute.yaml",
    out_dir: Path = REPO / "runs/phase3_validation",
    event_start_iso: str = "2022-09-04T00:00:00Z",
    event_end_iso: str = "2022-09-05T23:30:00Z",
    sar_instant_iso: str = "2022-09-05T00:40:28Z",
    snapshot_every_s: float = 1800.0,  # 30-min snapshot cadence
    lifecycle: RunManifest | None = None,
    elevation_override: Path | None = None,  # master underpass: variant DEM on same grid
) -> SimulationOutput:
    """Execute uncalibrated solver run forced by IMERG over September 2022 event."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if lifecycle is None:
        raise ValueError("run_phase3_simulation requires the gate's RunManifest owner")

    # 1. Load configs & pre-flight verification
    solver_cfg = load_solver_config(solver_cfg_path, REPO)
    with open(forcing_cfg_path) as f:
        forcing_cfg = yaml.safe_load(f)
    with open(val_cfg_path) as f:
        val_cfg = yaml.safe_load(f)
    with open(compute_cfg_path) as f:
        compute_cfg = yaml.safe_load(f)
    solver_cfg["_compute"] = compute_cfg

    resolve_phase3_config(solver_cfg, forcing_cfg, val_cfg, compute_cfg)

    # 2. Setup domain and grid
    grid, _ = build_grid(solver_cfg["_domain"], REPO)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu" and not compute_cfg.get("execution", {}).get(
        "allow_cpu_phase3_replay", False
    ):
        raise RuntimeError(
            "full Phase 3 replay refused on CPU: no measured CPU throughput exists for its "
            "compute budget. Run it on a configured GPU worker or explicitly enable the "
            "unmeasured CPU override in configs/compute.yaml."
        )
    static = load_domain(
        solver_cfg, REPO, device=device, use_buffered=True, elevation_override=elevation_override
    )
    dx = float(solver_cfg["_domain"]["resolution_m"])
    p = SolverParams.from_config(solver_cfg, dx)

    # 3. Load IMERG historical rainfall forcing
    adapter = ImergHistoricalAdapter(forcing_cfg_path)
    st = datetime.fromisoformat(event_start_iso.replace("Z", "+00:00"))
    et = datetime.fromisoformat(event_end_iso.replace("Z", "+00:00"))
    sar_time = datetime.fromisoformat(sar_instant_iso.replace("Z", "+00:00"))

    sar_sim_offset_s = (sar_time - st).total_seconds()
    sim_duration_s = (et - st).total_seconds() + 1800.0  # include full final interval

    event = adapter.get_forcing(st, et)
    event.verify_mass_conservation()

    # Buffer native cell ids to match buffered solver domain (pad 50 cells)
    buf_cells = round(float(solver_cfg["_domain"]["dem"]["buffer_m"]) / dx)
    native_ids_canon = adapter.native_cell_ids
    native_ids_buf = np.pad(native_ids_canon, buf_cells, mode="edge")
    native_ids_tensor = torch.as_tensor(native_ids_buf, dtype=torch.long, device=device)

    # Pre-extract interval rates on GPU (m/s)
    interval_rates_gpu: list[torch.Tensor] = []
    for iv in event.intervals:
        rate_16 = np.zeros(iv.distinct_native_cells, dtype=np.float32)
        # Depth in 30 min (mm) -> rate in mm/hr = depth * 2 -> rate in m/s = (depth * 2) / 3600 / 1000
        for cid in range(iv.distinct_native_cells):
            mask = adapter.native_cell_ids == cid
            rate_16[cid] = float(iv.rainfall_grid_mm[mask][0]) * 2.0 / 3600.0 / 1000.0
        interval_rates_gpu.append(torch.as_tensor(rate_16, dtype=torch.float32, device=device))

    # 4. Compute budget estimate before launch (CLAUDE.md §Compute)
    band = tuple(solver_cfg["compute_estimate"]["h_max_band_m"])
    n_lo, n_hi = estimate_steps_band(solver_cfg, sim_duration_s, band)
    n_cells = static.shape[0] * static.shape[1]
    est = compute_budget_estimate(solver_cfg, n_cells, n_hi)
    est["h_max_band_m"] = list(band)
    est["steps_band"] = [n_lo, n_hi]
    est["gated_on"] = "pessimistic (deepest h_max, most steps)"
    diagnostics_mib = n_cells * 3 * torch.tensor([], dtype=torch.float64).element_size() / 2**20
    est["persistent_diagnostic_buffers_mib"] = diagnostics_mib
    est["execution_device"] = device
    est["estimated_peak_with_diagnostics_mib"] = (
        float(compute_cfg["anchor"]["peak_vram_mib"]) + diagnostics_mib
    )
    measured_workers = [w for w in compute_cfg["pool"] if w.get("measured")]
    if not measured_workers:
        raise ValueError("compute.pool has no measured GPU worker")
    smallest_configured_mib = min(float(w["vram_total_mib"]) for w in measured_workers)
    safety_fraction = float(compute_cfg["vram"]["safety_margin_fraction"])
    est["smallest_configured_vram_mib"] = smallest_configured_mib
    est["configured_usable_vram_mib"] = smallest_configured_mib * (1.0 - safety_fraction)
    est["vram_exceeds"] = (
        est["estimated_peak_with_diagnostics_mib"] > est["configured_usable_vram_mib"]
    )
    if device == "cuda":
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        est["runtime_free_vram_mib"] = free_bytes / 2**20
        est["runtime_total_vram_mib"] = total_bytes / 2**20
        est["runtime_vram_exceeds"] = est["estimated_peak_with_diagnostics_mib"] > (
            est["runtime_free_vram_mib"] * (1.0 - safety_fraction)
        )
    else:
        est["runtime_vram_exceeds"] = False

    typer.echo("=================================================================")
    typer.echo("PHASE 3 VALIDATION GATE — UNCALIBRATED BASELINE RUN (PROMPT.md §8)")
    typer.echo("=================================================================")
    typer.echo("Forcing Mode: HISTORICAL (GPM IMERG v07 Final 30-min granules)")
    typer.echo(
        f"Simulation Window: {event_start_iso} to {event_end_iso} ({sim_duration_s / 3600:.1f} hours)"
    )
    typer.echo(f"Distinct IMERG cells over domain: {event.distinct_native_cells}")
    typer.echo(f"Domain Areal Mean Rainfall: {event.areal_mean_total_mm:.2f} mm")
    typer.echo(f"Total Domain Rainfall Volume: {event.total_volume_m3:,.1f} m³")
    typer.echo(
        f"SAR Comparison Instant: {sar_instant_iso} (offset: {sar_sim_offset_s / 3600:.2f} h)"
    )
    typer.echo(
        f"Compute Budget Estimate: {est['estimated_gpu_hours']:.3f} GPU-h "
        f"({100 * est['fraction_of_nightly']:.1f}% of nightly {est['nightly_gpu_hours']} h ceiling)"
    )

    # 5. Record the realized budget before solver launch (Rule 6)
    lifecycle.update_running(
        {
            "device": device,
            "elevation_override": (
                str(elevation_override.relative_to(REPO)) if elevation_override is not None else None
            ),
            "event_window": {
                "start": event_start_iso,
                "end": event_end_iso,
                "sar_instant": sar_instant_iso,
                "duration_hours": sim_duration_s / 3600.0,
            },
            "forcing_summary": {
                "source": "gpm_imerg_v07_final",
                "distinct_native_cells": event.distinct_native_cells,
                "areal_mean_mm": event.areal_mean_total_mm,
                "peak_intensity_mm_hr": event.peak_hourly_rate_mm_hr,
                "total_volume_m3": event.total_volume_m3,
            },
            "compute_budget": est,
        }
    )
    if est["exceeds"] or est["vram_exceeds"] or est["runtime_vram_exceeds"]:
        typer.echo(
            "FATAL: Estimated compute or VRAM exceeds configured budget — refusing to start."
        )
        raise RuntimeError("Compute or VRAM budget exceeded")
    t0 = time.perf_counter()

    # 6. Initialize solver state
    ctrl = TimestepController(
        dx=dx,
        alpha=float(solver_cfg["timestep"]["cfl_alpha"]),
        cfl_ceiling=float(solver_cfg["timestep"]["cfl_ceiling"]),
        gravity=float(solver_cfg["physics"]["gravity_m_s2"]),
        min_dt_s=float(solver_cfg["timestep"]["min_dt_s"]),
        max_dt_s=float(solver_cfg["timestep"]["max_dt_s"]),
    )
    budget = MassBudget(
        cell_area_m2=dx * dx,
        relative_tolerance=float(solver_cfg["mass"]["relative_tolerance"]),
        tolerance_is_measured=bool(solver_cfg["mass"]["relative_tolerance_is_measured"]),
    )

    h = torch.zeros(static.shape, dtype=torch.float32, device=device)
    qx = torch.zeros((static.shape[0], static.shape[1] - 1), dtype=torch.float32, device=device)
    qy = torch.zeros((static.shape[0] - 1, static.shape[1]), dtype=torch.float32, device=device)
    budget.start(h)
    cumulative_transport_depth_m = torch.zeros(static.shape, dtype=torch.float64, device=device)
    cumulative_drain_depth_m = torch.zeros(static.shape, dtype=torch.float64, device=device)
    cumulative_infiltration_depth_m = torch.zeros(static.shape, dtype=torch.float64, device=device)

    # State tracking
    h_canonical_max = np.zeros((grid.height, grid.width), dtype=np.float32)
    h_sar_instant = np.zeros((grid.height, grid.width), dtype=np.float32)
    sar_captured = False

    snapshots_meta: list[tuple[float, str]] = []
    snapshots_dir = out_dir / "depth_rasters"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    profile_canon = grid.profile(dtype="float32", nodata=None)

    # 7. Timestep Loop
    t = 0.0
    steps = 0
    max_courant = 0.0
    max_selected_courant = 0.0
    steps_exceeding_selected = 0
    steps_with_rejection = 0
    next_snap = 0.0
    interval_s = 1800.0

    typer.echo("\nStarting forward solver simulation...")
    last_log_t = time.perf_counter()

    with torch.no_grad():
        while t < sim_duration_s:
            dt = ctrl.dt_for(h)
            dt = min(dt, sim_duration_s - t)
            if dt <= 0:
                break

            iv_idx = min(int(t // interval_s), len(interval_rates_gpu) - 1)
            r_grid = interval_rates_gpu[iv_idx][native_ids_tensor]

            c_sel = ctrl.courant(h, dt)
            max_selected_courant = max(max_selected_courant, c_sel)

            had_rejection = False
            while True:
                h_new, qx_new, qy_new, diag = acc_step(
                    h, qx, qy, static, dt, p, rain_rate_m_s=r_grid
                )
                c_realized = ctrl.courant(h_new, dt)
                if c_realized > ctrl.cfl_ceiling and dt > ctrl.min_dt_s:
                    ctrl.rejections += 1
                    had_rejection = True
                    dt = max(dt / 2.0, ctrl.min_dt_s)
                    continue
                break

            if had_rejection:
                steps_with_rejection += 1

            h, qx, qy = h_new, qx_new, qy_new
            # Only the accepted trial reaches this point. Accumulating inside
            # the retry loop would count rejected transport.
            cumulative_transport_depth_m.add_(diag["transport_depth_change_m"], alpha=1.0)
            cumulative_drain_depth_m.add_(diag["drained_depth_m"], alpha=1.0)
            cumulative_infiltration_depth_m.add_(diag["infiltrated_depth_m"], alpha=1.0)
            ctrl.schedule.append(dt)
            max_courant = max(max_courant, c_realized)
            if c_realized > c_sel + 1e-6:
                steps_exceeding_selected += 1

            budget.accumulate(h, diag, r_grid, dt, n_cells)
            t += dt
            steps += 1

            # Update event maximum depth over canonical grid
            h_cpu_canon = (
                h[buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width]
                .detach()
                .cpu()
                .numpy()
            )
            np.maximum(h_canonical_max, h_cpu_canon, out=h_canonical_max)

            # Capture SAR comparison instant (06:10 IST on 5 Sept 2022)
            if not sar_captured and t >= sar_sim_offset_s:
                h_sar_instant = h_cpu_canon.copy()
                sar_captured = True
                sar_path = out_dir / "depth_sar_instant_20220905_004028Z.tif"
                with rasterio.open(sar_path, "w", **profile_canon) as dst:
                    dst.write(h_sar_instant, 1)
                typer.echo(
                    f"  [SNAPSHOT] Captured SAR epoch at t={t / 3600:.2f}h -> {sar_path.name}"
                )

            # Write cadence snapshots
            if t >= next_snap:
                snap_path = snapshots_dir / f"depth_t{int(round(t)):06d}s.tif"
                with rasterio.open(snap_path, "w", **profile_canon) as dst:
                    dst.write(h_cpu_canon, 1)
                snapshots_meta.append((t, str(snap_path.relative_to(REPO))))
                next_snap += snapshot_every_s

            if steps % 500 == 0 or (time.perf_counter() - last_log_t) > 30.0:
                elapsed = time.perf_counter() - t0
                pct = (t / sim_duration_s) * 100.0
                typer.echo(
                    f"  step {steps:6,d} | sim t={t / 3600:5.2f}h ({pct:4.1f}%) | "
                    f"dt={dt:4.2f}s | max_h={float(h.max()):5.2f}m | "
                    f"wall={elapsed / 60:4.1f}m | mass_res={budget.relative_residual():.2e}"
                )
                last_log_t = time.perf_counter()

    wall_clock = time.perf_counter() - t0
    h_final_canon = (
        h[buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width]
        .detach()
        .cpu()
        .numpy()
    )

    # Save realized final and maximum depth rasters on the canonical grid.
    final_depth_path = out_dir / "depth_final.tif"
    with rasterio.open(final_depth_path, "w", **profile_canon) as dst:
        dst.write(h_final_canon, 1)
    max_depth_path = out_dir / "depth_event_maximum.tif"
    with rasterio.open(max_depth_path, "w", **profile_canon) as dst:
        dst.write(h_canonical_max, 1)

    transport_path = out_dir / "cumulative_transport_depth_buffered.tif"
    drain_path = out_dir / "cumulative_drain_depth_buffered.tif"
    infiltration_path = out_dir / "cumulative_infiltration_depth_buffered.tif"
    final_depth_buffered_path = out_dir / "depth_final_buffered.tif"
    buffered_transform = profile_canon["transform"] * rasterio.Affine.translation(
        -buf_cells, -buf_cells
    )
    transport_profile = {
        **profile_canon,
        "width": static.shape[1],
        "height": static.shape[0],
        "transform": buffered_transform,
        "dtype": "float64",
        "nodata": None,
    }
    transport_cpu = cumulative_transport_depth_m.cpu().numpy()
    h_final_buffered = h.detach().cpu().numpy()
    final_depth_buffered_profile = {**transport_profile, "dtype": "float32"}
    with rasterio.open(final_depth_buffered_path, "w", **final_depth_buffered_profile) as dst:
        dst.write(h_final_buffered, 1)
    with rasterio.open(transport_path, "w", **transport_profile) as dst:
        dst.write(transport_cpu, 1)
    drain_cpu = cumulative_drain_depth_m.cpu().numpy()
    with rasterio.open(drain_path, "w", **transport_profile) as dst:
        dst.write(drain_cpu, 1)
    infiltration_cpu = cumulative_infiltration_depth_m.cpu().numpy()
    with rasterio.open(infiltration_path, "w", **transport_profile) as dst:
        dst.write(infiltration_cpu, 1)
    lifecycle.update_running(
        {
            "final_depth": {
                "path": str(final_depth_path.relative_to(REPO)),
                "shape": list(h_final_canon.shape),
                "units": "m",
                "dtype": "float32",
                "grid_role": "canonical_domain_grid",
            },
            "final_depth_buffered": {
                "path": str(final_depth_buffered_path.relative_to(REPO)),
                "shape": list(h_final_buffered.shape),
                "units": "m",
                "dtype": "float32",
                "grid_role": "buffered_solver_cell_grid",
                "quantity": "accepted_solver_state_depth_at_event_end",
            },
            "cumulative_transport_depth": {
                "path": str(transport_path.relative_to(REPO)),
                "shape": list(transport_cpu.shape),
                "units": "m",
                "dtype": "float64",
                "grid_role": "buffered_solver_cell_grid",
                "quantity": "sum_of_accepted_post_limiter_div_x_fx_plus_div_y_fy_minus_domain_outflow",
                "includes": ["internal_cell_face_transport", "domain_boundary_outflow"],
                "excludes": ["rain", "drain", "infiltration"],
            },
            "cumulative_drain_depth": {
                "path": str(drain_path.relative_to(REPO)),
                "shape": list(drain_cpu.shape),
                "units": "m",
                "dtype": "float64",
                "grid_role": "buffered_solver_cell_grid",
                "quantity": "sum_of_accepted_realized_drain_sink_depth",
            },
            "cumulative_infiltration_depth": {
                "path": str(infiltration_path.relative_to(REPO)),
                "shape": list(infiltration_cpu.shape),
                "units": "m",
                "dtype": "float64",
                "grid_role": "buffered_solver_cell_grid",
                "quantity": "sum_of_accepted_realized_infiltration_sink_depth",
            },
        }
    )

    # Save native IMERG cell id raster (Honesty constraint)
    imerg_cell_ids_path = out_dir / "imerg_native_cell_ids.tif"
    profile_int = grid.profile(dtype="int32", nodata=-1)
    with rasterio.open(imerg_cell_ids_path, "w", **profile_int) as dst:
        dst.write(adapter.native_cell_ids.astype(np.int32), 1)

    # Write dt schedule binary sidecar
    dt_sidecar = write_dt_sidecar(ctrl.schedule, out_dir / "dt_schedule.f32.gz")
    lifecycle.update_running({"dt_schedule": dt_sidecar})

    typer.echo("\nSimulation completed successfully.")
    typer.echo(f"  Total Steps: {steps:,}")
    typer.echo(f"  Simulated Duration: {t / 3600:.2f} hours")
    typer.echo(f"  Wall Clock: {wall_clock:.1f} s ({wall_clock / 60:.2f} min)")
    typer.echo(
        f"  Mass Relative Residual: {budget.relative_residual():.3e} (Tolerance: {budget.relative_tolerance:.1e})"
    )
    typer.echo(f"  Max Realized Courant: {max_courant:.3f}")
    typer.echo(f"  Event Max Depth (P99): {float(np.percentile(h_canonical_max, 99)):.3f} m")

    return SimulationOutput(
        steps=steps,
        sim_time_s=t,
        wall_clock_s=wall_clock,
        max_courant=max_courant,
        max_selected_courant=max_selected_courant,
        rejected_steps=steps_with_rejection,
        retry_attempts=ctrl.rejections,
        h_canonical_final=h_final_canon,
        h_canonical_max=h_canonical_max,
        h_sar_instant=h_sar_instant,
        sar_timestamp_iso=sar_instant_iso,
        mass_dict=budget.as_dict(),
        depth_snapshots=snapshots_meta,
        total_rain_volume_m3=event.total_volume_m3,
        areal_mean_rain_mm=event.areal_mean_total_mm,
        distinct_imerg_cells=event.distinct_native_cells,
        native_cell_ids_canonical=adapter.native_cell_ids,
        cumulative_transport_depth_path=str(transport_path.relative_to(REPO)),
        cumulative_drain_depth_path=str(drain_path.relative_to(REPO)),
        cumulative_infiltration_depth_path=str(infiltration_path.relative_to(REPO)),
        final_depth_path=str(final_depth_path.relative_to(REPO)),
        final_depth_buffered_path=str(final_depth_buffered_path.relative_to(REPO)),
    )


def score_phase3_validation(
    sim_out: SimulationOutput,
    val_cfg_path: Path = REPO / "configs/validation.yaml",
    out_dir: Path = REPO / "runs/phase3_validation",
    thresholds_m: list[float] = (0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.00),
) -> dict[str, Any]:
    """Score uncalibrated simulation outputs against Sentinel-1, BBMP, and Ground Truth points."""
    with open(val_cfg_path) as f:
        val_cfg = yaml.safe_load(f)

    # 1. Load Basin Class raster for permanent water exclusion (classes 1, 2, 3)
    sar_cfg = val_cfg["sar_water_classifier"]
    gt_cfg = val_cfg["groundtruth"]
    basin_class_path = REPO / sar_cfg["basin_class_path"]
    if not basin_class_path.exists():
        raise FileNotFoundError(f"Basin class raster missing at {basin_class_path}")
    with rasterio.open(basin_class_path) as src:
        basin_class = src.read(1)
        grid_bounds = src.bounds
        grid_shape = src.shape
        grid_transform = src.transform

    # Permanent water exclusion: 1 (storage/lakes), 2 (quarries), 3 (landfills)
    permanent_water_mask = (basin_class == 1) | (basin_class == 2) | (basin_class == 3)
    excluded_water_cells_count = int(np.sum(permanent_water_mask))

    # 2. Load Urban Density Classes raster (OPEN, MODERATE, DENSE)
    density_classes_path = REPO / val_cfg["density_stratification"]["output_density_classes_path"]
    if not density_classes_path.exists():
        run_density_pipeline(val_cfg_path)
    with rasterio.open(density_classes_path) as src:
        density_raster = src.read(1)

    density_class_names = {0: "OPEN", 1: "MODERATE", 2: "DENSE"}

    # 3. Compute Sentinel-1 Observed Water Extent for 5 Sept 2022 00:40:28 UTC
    s1_tif = REPO / sar_cfg["flood_scene_tif"]
    s1_xml = REPO / sar_cfg["flood_calibration_xml"]
    if not s1_tif.exists() or not s1_xml.exists():
        raise FileNotFoundError("Sentinel-1 flood scene files missing in data/raw/sentinel1/")

    sigma0_db, s1_valid = compute_s1_sigma0_db(
        s1_tif, s1_xml, grid_bounds, grid_shape, grid_transform
    )
    water_threshold_db = float(sar_cfg["water_threshold_db"])
    s1_water_observed = (sigma0_db <= water_threshold_db) & s1_valid

    # Save calibrated S1 backscatter and water mask rasters
    s1_out_path = out_dir / "sentinel1_flood_sigma0_db.tif"
    with rasterio.open(
        s1_out_path,
        "w",
        driver="GTiff",
        dtype="float32",
        width=grid_shape[1],
        height=grid_shape[0],
        count=1,
        crs="EPSG:32643",
        transform=grid_transform,
        nodata=np.nan,
    ) as dst:
        dst.write(sigma0_db, 1)

    threshold_label = f"{abs(water_threshold_db):g}".replace(".", "p")
    s1_mask_out_path = out_dir / f"sentinel1_flood_observed_water_mask_{threshold_label}dB.tif"
    with rasterio.open(
        s1_mask_out_path,
        "w",
        driver="GTiff",
        dtype="uint8",
        width=grid_shape[1],
        height=grid_shape[0],
        count=1,
        crs="EPSG:32643",
        transform=grid_transform,
        nodata=255,
    ) as dst:
        dst.write(s1_water_observed.astype(np.uint8), 1)

    # 4. PART B.2: Sentinel-1 Extent Scoring (Stratified + Exclusion + Sweep)
    sar_dt = datetime.fromisoformat(sim_out.sar_timestamp_iso.replace("Z", "+00:00"))
    sar_sweep_results = score_threshold_curve(
        predicted_depth=sim_out.h_sar_instant,
        observed_flooded=s1_water_observed,
        comparison_timestamp=sar_dt,
        thresholds_m=thresholds_m,
        permanent_water_mask=permanent_water_mask,
        density_raster=density_raster,
        density_classes=density_class_names,
    )

    # 5. PART B.1: BBMP Flood-Prone List Scoring (398 points)
    bbmp_dir = REPO / gt_cfg["bbmp_kml_dir"]
    bbmp_points: list[tuple[float, float, str]] = []  # (x_utm, y_utm, name)
    trans_wgs_to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)

    for kml_file in sorted(bbmp_dir.glob("*.kml")):
        gdf = gpd.read_file(kml_file)
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom.geom_type == "Point":
                ux, uy = trans_wgs_to_utm.transform(geom.x, geom.y)
                p_name = str(row.get("Name", row.get("Description", kml_file.stem)))
                bbmp_points.append((ux, uy, p_name))

    # Query modeled event maximum depth at BBMP locations (with 50 m / 5-cell radius max filter)
    bbmp_sweep_scores: list[dict[str, Any]] = []
    bbmp_depths: list[float] = []

    for ux, uy, _ in bbmp_points:
        row, col = rasterio.transform.rowcol(grid_transform, ux, uy)
        if 0 <= row < grid_shape[0] and 0 <= col < grid_shape[1]:
            # 5x5 window max depth (50 m neighborhood)
            r0, r1 = max(0, row - 2), min(grid_shape[0], row + 3)
            c0, c1 = max(0, col - 2), min(grid_shape[1], col + 3)
            val = float(np.max(sim_out.h_canonical_max[r0:r1, c0:c1]))
        else:
            val = 0.0
        bbmp_depths.append(val)

    bbmp_depths_arr = np.array(bbmp_depths)
    total_bbmp = len(bbmp_points)

    for th in thresholds_m:
        hits = int(np.sum(bbmp_depths_arr > th))
        hit_rate = hits / total_bbmp if total_bbmp > 0 else 0.0
        # Domain flooded cell fraction as FAR proxy
        pred_flooded_cells = int(np.sum(sim_out.h_canonical_max > th))
        bbmp_sweep_scores.append(
            {
                "threshold_m": th,
                "total_points": total_bbmp,
                "hits": hits,
                "hit_rate_pod": round(hit_rate, 4),
                "predicted_flooded_cells_domain": pred_flooded_cells,
                "domain_flooded_pct": round(
                    (pred_flooded_cells / sim_out.h_canonical_max.size) * 100.0, 3
                ),
            }
        )

    # 6. PART B.3: Ground-Truth Points Scoring (24 points)
    gt_csv = REPO / val_cfg["groundtruth"]["points_csv"]
    gt_points_all, gt_summary = load_and_validate_groundtruth(gt_csv, bbmp_dir)
    scoring_start, scoring_end, max_snap_distance_m = resolve_groundtruth_scoring_config(gt_cfg)
    gt_points, gt_date_rejections = select_event_groundtruth(
        gt_points_all,
        scoring_start,
        scoring_end,
    )

    gt_points_utm = [trans_wgs_to_utm.transform(pt.lon, pt.lat) for pt in gt_points]
    road_raster_path = REPO / gt_cfg["road_segment_id_path"]
    with rasterio.open(road_raster_path) as road_src:
        road_segment_ids = road_src.read(1)
        road_snap_distances_m = groundtruth_road_snap_distances(
            gt_points_utm, road_segment_ids, road_src.transform
        )

    gt_eval_records: list[dict[str, Any]] = []
    depth_errors_sq: list[float] = []
    depth_errors_abs: list[float] = []
    within_band_count = 0
    below_band_count = 0
    above_band_count = 0

    snap_rejections: list[dict[str, Any]] = []
    for pt, (ux, uy), snap_distance_m in zip(
        gt_points, gt_points_utm, road_snap_distances_m, strict=True
    ):
        row, col = rasterio.transform.rowcol(grid_transform, ux, uy)

        if 0 <= row < grid_shape[0] and 0 <= col < grid_shape[1]:
            # Query point & 3x3 local neighborhood max
            r0, r1 = max(0, row - 1), min(grid_shape[0], row + 2)
            c0, c1 = max(0, col - 1), min(grid_shape[1], col + 2)
            model_depth_point = float(sim_out.h_canonical_max[row, col])
            model_depth_local_max = float(np.max(sim_out.h_canonical_max[r0:r1, c0:c1]))
            sar_instant_depth = float(sim_out.h_sar_instant[row, col])
        else:
            model_depth_point = 0.0
            model_depth_local_max = 0.0
            sar_instant_depth = 0.0

        low = pt.depth_band_low_m
        high = pt.depth_band_high_m

        rejection_reasons: list[str] = []
        has_depth_band = low is not None and high is not None
        snap_eligible = snap_distance_m <= max_snap_distance_m
        eligible_for_depth_scoring = has_depth_band and snap_eligible
        if not has_depth_band:
            rejection_reasons.append("no_depth_band")
        if not snap_eligible:
            rejection_reasons.append("snap_distance_exceeds_configured_max")
            snap_rejections.append(
                {
                    "id": pt.id,
                    "snap_distance_m": round(float(snap_distance_m), 3),
                    "configured_max_snap_distance_m": max_snap_distance_m,
                    "reason": "snap_distance_exceeds_configured_max",
                }
            )

        if eligible_for_depth_scoring:
            mid = (low + high) / 2.0
            diff = model_depth_local_max - mid
            depth_errors_sq.append(diff * diff)
            depth_errors_abs.append(abs(diff))

            if model_depth_local_max < low:
                band_status = "BELOW"
                below_band_count += 1
            elif model_depth_local_max > high:
                band_status = "ABOVE"
                above_band_count += 1
            else:
                band_status = "WITHIN_BAND"
                within_band_count += 1
        else:
            mid = None
            band_status = "EXTENT_ONLY"

        gt_eval_records.append(
            {
                "id": pt.id,
                "location_name": pt.location_name,
                "observed_date": pt.observed_date,
                "observed_band_low_m": low,
                "observed_band_high_m": high,
                "depth_cue": pt.depth_cue,
                "model_depth_point_m": round(model_depth_point, 4),
                "model_depth_local_max_m": round(model_depth_local_max, 4),
                "sar_instant_depth_m": round(sar_instant_depth, 4),
                "road_snap_distance_m": round(float(snap_distance_m), 3),
                "band_status": band_status,
                "eligible_for_depth_scoring": eligible_for_depth_scoring,
                "rejection_reasons": rejection_reasons,
            }
        )

    gt_points_with_depth = len(depth_errors_sq)
    gt_extent_only_count = len(gt_points) - gt_points_with_depth
    gt_rmse_m = math.sqrt(np.mean(depth_errors_sq)) if depth_errors_sq else None
    gt_mae_m = float(np.mean(depth_errors_abs)) if depth_errors_abs else None

    # 7. Aggregate Full Validation Results
    val_report: dict[str, Any] = {
        "stage": "phase3_uncalibrated_validation_gate",
        "timestamp_iso": datetime.now(UTC).isoformat(),
        "simulation": {
            "steps": sim_out.steps,
            "sim_duration_hours": sim_out.sim_time_s / 3600.0,
            "wall_clock_sec": round(sim_out.wall_clock_s, 2),
            "max_courant_realized": round(sim_out.max_courant, 3),
            "mass_relative_residual": sim_out.mass_dict["relative_residual"],
            "total_rainfall_volume_m3": sim_out.total_rain_volume_m3,
            "areal_mean_rainfall_mm": round(sim_out.areal_mean_rain_mm, 2),
            "distinct_imerg_cells": sim_out.distinct_imerg_cells,
        },
        "exclusions": {
            "excluded_basin_classes": [1, 2, 3],
            "excluded_description": "storage/lakes (1), quarries (2), landfills (3)",
            "excluded_cell_count": excluded_water_cells_count,
            "domain_total_cells": sim_out.h_canonical_max.size,
            "excluded_pct_of_domain": round(
                (excluded_water_cells_count / sim_out.h_canonical_max.size) * 100.0, 3
            ),
        },
        "sar_stratified_scoring": [res.to_dict() for res in sar_sweep_results],
        "bbmp_scoring": {
            "total_points": total_bbmp,
            "sweep": bbmp_sweep_scores,
        },
        "groundtruth_scoring": {
            "total_points": len(gt_points),
            "records_loaded": len(gt_points_all),
            "date_rejections": gt_date_rejections,
            "snap_rejections": snap_rejections,
            "configured_max_snap_distance_m": max_snap_distance_m,
            "event_window_start": scoring_start,
            "event_window_end": scoring_end,
            "points_with_depth_band": gt_points_with_depth,
            "realized_depth_scoring_count": gt_points_with_depth,
            "extent_only_count": gt_extent_only_count,
            "within_band_count": within_band_count,
            "below_band_count": below_band_count,
            "above_band_count": above_band_count,
            "depth_rmse_m": round(gt_rmse_m, 4) if gt_rmse_m is not None else None,
            "depth_mae_m": round(gt_mae_m, 4) if gt_mae_m is not None else None,
            "points_detail": gt_eval_records,
            "target_gap_note": (
                f"Dataset loaded {len(gt_points_all)} verified records; "
                f"{len(gt_points)} are eligible for this replay window. The set was not padded."
            ),
        },
        "external_literature_comparison": {
            "status": "NOT_SCORED",
            "reason": (
                "External literature figures are not realized artifacts of this run and are not "
                "used as a Phase 3 acceptance comparator."
            ),
            "error_budgets": {
                "solver_vs_observation": "Evaluated in this gate",
                "surrogate_vs_solver": "Not applicable (surrogate training deferred to Phase 5)",
            },
        },
    }

    return val_report


def run_phase3_gate(
    solver_cfg_path: Path = REPO / "configs/solver.yaml",
    forcing_cfg_path: Path = REPO / "configs/forcing.yaml",
    val_cfg_path: Path = REPO / "configs/validation.yaml",
    compute_cfg_path: Path = REPO / "configs/compute.yaml",
    out_dir: Path = REPO / "runs/phase3_validation",
    event_start_iso: str = "2022-09-04T00:00:00Z",
    event_end_iso: str = "2022-09-05T23:30:00Z",
    sar_instant_iso: str = "2022-09-05T00:40:28Z",
    elevation_override: Path | None = None,
    *,
    repo_root: Path = REPO,
) -> dict[str, Any]:
    """Own the sole Phase 3 manifest from preflight through terminal status."""
    solver_cfg = load_solver_config(solver_cfg_path, repo_root)
    with forcing_cfg_path.open() as handle:
        forcing_cfg = yaml.safe_load(handle)
    with val_cfg_path.open() as handle:
        val_cfg = yaml.safe_load(handle)
    with compute_cfg_path.open() as handle:
        compute_cfg = yaml.safe_load(handle)
    solver_cfg["_compute"] = compute_cfg
    resolve_phase3_config(solver_cfg, forcing_cfg, val_cfg, compute_cfg)

    lifecycle = RunManifest(
        out_dir / "manifest.json",
        stage="phase3_uncalibrated_validation_gate",
        repo_root=repo_root,
        resolved_config={
            "solver": solver_cfg,
            "forcing": forcing_cfg,
            "validation": val_cfg,
            "compute": compute_cfg,
            "event": {
                "start": event_start_iso,
                "end": event_end_iso,
                "sar_instant": sar_instant_iso,
            },
        },
        config_paths={
            "solver": str(solver_cfg_path),
            "forcing": str(forcing_cfg_path),
            "validation": str(val_cfg_path),
            "compute": str(compute_cfg_path),
        },
    )
    lifecycle.start()
    try:
        sim_out = run_phase3_simulation(
            solver_cfg_path=solver_cfg_path,
            forcing_cfg_path=forcing_cfg_path,
            val_cfg_path=val_cfg_path,
            compute_cfg_path=compute_cfg_path,
            out_dir=out_dir,
            event_start_iso=event_start_iso,
            event_end_iso=event_end_iso,
            sar_instant_iso=sar_instant_iso,
            lifecycle=lifecycle,
            elevation_override=elevation_override,
        )
        report = score_phase3_validation(
            sim_out=sim_out,
            val_cfg_path=val_cfg_path,
            out_dir=out_dir,
        )
        domain_total = int(report["exclusions"]["domain_total_cells"])
        excluded = int(report["exclusions"]["excluded_cell_count"])
        lifecycle.complete(
            {
                "scored_domain_cell_count": domain_total - excluded,
                "mass_balance": sim_out.mass_dict,
                "artifacts": {
                    "final_depth": sim_out.final_depth_path,
                    "final_depth_buffered": sim_out.final_depth_buffered_path,
                    "cumulative_transport_depth": sim_out.cumulative_transport_depth_path,
                    "cumulative_drain_depth": sim_out.cumulative_drain_depth_path,
                    "cumulative_infiltration_depth": sim_out.cumulative_infiltration_depth_path,
                },
                "results": report,
            }
        )
        return report
    except BaseException as exc:
        lifecycle.fail(exc)
        raise


@app.command()
def main(
    solver_config: Path = typer.Option(REPO / "configs/solver.yaml", help="Solver config path"),
    forcing_config: Path = typer.Option(REPO / "configs/forcing.yaml", help="Forcing config path"),
    val_config: Path = typer.Option(
        REPO / "configs/validation.yaml", help="Validation config path"
    ),
    compute_config: Path = typer.Option(REPO / "configs/compute.yaml", help="Compute config path"),
    out: Path = typer.Option(REPO / "runs/phase3_validation", help="Output directory"),
    elevation_override: Path | None = typer.Option(
        None, help="Variant DEM raster (same buffered grid) to replace elevation.tif"
    ),
) -> None:
    """Run Phase 3 Validation Gate end to end and produce uncalibrated baseline score."""
    run_phase3_gate(
        solver_cfg_path=solver_config,
        forcing_cfg_path=forcing_config,
        val_cfg_path=val_config,
        compute_cfg_path=compute_config,
        out_dir=out,
        elevation_override=elevation_override,
    )

    typer.echo("\n=======================================================")
    typer.echo("PHASE 3 VALIDATION GATE — UNCALIBRATED BASELINE REPORT")
    typer.echo("=======================================================")
    typer.echo(f"Wrote full validation report to {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
