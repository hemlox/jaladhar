"""Run the retained historical replay and BBMP/ground-truth validation gate."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pyproj
import rasterio
import torch
import typer
import yaml
from scipy.spatial import cKDTree

from jaladhar.forcing.imerg import ImergHistoricalAdapter
from jaladhar.forcing.interface import (
    ForcingMode,
    assert_nowcast_metadata_contract,
    assert_rate_conversion_parameterised,
    interval_depth_mm_to_rate_m_s,
)
from jaladhar.provenance import RunManifest
from jaladhar.solver.acc import SolverParams, acc_step
from jaladhar.solver.mass import MassBudget
from jaladhar.solver.run import compute_budget_estimate, estimate_steps_band, write_dt_sidecar
from jaladhar.solver.state import load_domain, load_solver_config
from jaladhar.solver.timestep import TimestepController
from jaladhar.terrain.grid import build_grid
from jaladhar.validation.groundtruth import (
    load_and_validate_groundtruth,
    resolve_groundtruth_scoring_config,
    select_event_groundtruth,
)

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


def resolve_phase3_config(
    solver_cfg: dict[str, Any],
    forcing_cfg: dict[str, Any],
    val_cfg: dict[str, Any],
    compute_cfg: dict[str, Any],
) -> dict[str, Any]:
    missing: list[str] = []

    for k in ["physics", "timestep", "wetdry", "buildings", "boundaries", "sinks", "mass"]:
        if k not in solver_cfg:
            missing.append(f"solver.{k}")

    if forcing_cfg.get("mode") != "historical":
        missing.append("forcing.mode == 'historical'")
    if "historical" not in forcing_cfg or "granules_dir" not in forcing_cfg["historical"]:
        missing.append("forcing.historical.granules_dir")

    for key in ["anchor", "budget", "pool", "vram"]:
        if key not in compute_cfg:
            missing.append(f"compute.{key}")
    if "pool" in compute_cfg and not compute_cfg["pool"]:
        missing.append("compute.pool (at least one measured GPU worker)")
    if "vram" in compute_cfg and "safety_margin_fraction" not in compute_cfg["vram"]:
        missing.append("compute.vram.safety_margin_fraction")

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
    mass_dict: dict[str, Any]
    depth_snapshots: list[tuple[float, str]]
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
    snapshot_every_s: float = 1800.0,
    lifecycle: RunManifest | None = None,
    elevation_override: Path | None = None,
    h0_raster: Path | None = None,  # warm start: realized depth raster (canonical grid) at issue
    tile: tuple[int, int, int, int] | None = None,
) -> SimulationOutput:
    out_dir.mkdir(parents=True, exist_ok=True)
    if lifecycle is None:
        raise ValueError("run_phase3_simulation requires the gate's RunManifest owner")

    solver_cfg = load_solver_config(solver_cfg_path, REPO)
    with open(forcing_cfg_path) as f:
        forcing_cfg = yaml.safe_load(f)
    with open(val_cfg_path) as f:
        val_cfg = yaml.safe_load(f)
    with open(compute_cfg_path) as f:
        compute_cfg = yaml.safe_load(f)
    solver_cfg["_compute"] = compute_cfg

    resolve_phase3_config(solver_cfg, forcing_cfg, val_cfg, compute_cfg)

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
    buf_cells = round(float(solver_cfg["_domain"]["dem"]["buffer_m"]) / dx)
    tile_window: tuple[slice, slice] | None = None
    tile_shape: tuple[int, int] | None = None
    w_r0 = w_r1 = w_c0 = w_c1 = None
    if tile is not None:
        r0, r1, c0, c1 = tile
        if not (0 <= r0 < r1 <= grid.height and 0 <= c0 < c1 <= grid.width):
            raise ValueError(f"tile {tile} outside canonical grid {grid.height}x{grid.width}")
        w_r0, w_r1 = r0 + buf_cells, r1 + buf_cells
        w_c0, w_c1 = c0 + buf_cells, c1 + buf_cells
        tile_window = (slice(w_r0, w_r1), slice(w_c0, w_c1))
        tile_shape = (r1 - r0, c1 - c0)
        static = load_domain(
            solver_cfg,
            REPO,
            device=device,
            use_buffered=True,
            elevation_override=elevation_override,
            window=tile_window,
        )
        if static.shape != tile_shape:
            raise ValueError(
                f"load_domain tile shape {static.shape} != expected canonical crop {tile_shape}"
            )
    p = SolverParams.from_config(solver_cfg, dx)

    adapter = ImergHistoricalAdapter(forcing_cfg_path)
    st = datetime.fromisoformat(event_start_iso.replace("Z", "+00:00"))
    et = datetime.fromisoformat(event_end_iso.replace("Z", "+00:00"))
    sim_duration_s = (et - st).total_seconds() + 1800.0

    event = adapter.get_forcing(st, et)
    event.verify_mass_conservation()
    if event.mode is ForcingMode.NOWCAST:
        assert_nowcast_metadata_contract(event)

    native_ids_canon = adapter.native_cell_ids
    has_uncovered_cells = bool(np.any(native_ids_canon < 0))
    native_ids_buf = (
        np.pad(native_ids_canon, buf_cells, mode="constant", constant_values=-1)
        if has_uncovered_cells
        else np.pad(native_ids_canon, buf_cells, mode="edge")
    )
    native_ids_lookup = np.where(
        native_ids_buf >= 0,
        native_ids_buf,
        event.distinct_native_cells,
    )
    if tile is not None:
        native_ids_lookup = native_ids_lookup[w_r0:w_r1, w_c0:w_c1]
    native_ids_tensor = torch.as_tensor(native_ids_lookup, dtype=torch.long, device=device)

    interval_rates_gpu: list[torch.Tensor] = []
    interval_rates_native_m_s: list[np.ndarray] = []
    for iv in event.intervals:
        rate_16 = np.zeros(iv.distinct_native_cells, dtype=np.float32)
        for cid in range(iv.distinct_native_cells):
            mask = adapter.native_cell_ids == cid
            values = iv.rainfall_grid_mm[mask]
            if values.size == 0 or not np.all(values == values[0]):
                raise AssertionError(
                    f"native rainfall cell {cid} is absent or non-uniform in interval"
                )
            rate_16[cid] = interval_depth_mm_to_rate_m_s(float(values[0]), iv.interval_minutes)
        interval_rates_native_m_s.append(rate_16.copy())
        rate_with_dry_sentinel = np.concatenate((rate_16, np.zeros(1, dtype=np.float32)))
        interval_rates_gpu.append(
            torch.as_tensor(rate_with_dry_sentinel, dtype=torch.float32, device=device)
        )
    if event.mode is ForcingMode.NOWCAST:
        assert_rate_conversion_parameterised(event, interval_rates_native_m_s)

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
        f"Simulation Window: {event_start_iso} to {event_end_iso} "
        f"({sim_duration_s / 3600:.1f} hours)"
    )
    typer.echo(f"Distinct IMERG cells over domain: {event.distinct_native_cells}")
    typer.echo(f"Domain Areal Mean Rainfall: {event.areal_mean_total_mm:.2f} mm")
    typer.echo(f"Total Domain Rainfall Volume: {event.total_volume_m3:,.1f} m³")
    typer.echo(
        f"Compute Budget Estimate: {est['estimated_gpu_hours']:.3f} GPU-h "
        f"({100 * est['fraction_of_nightly']:.1f}% of nightly {est['nightly_gpu_hours']} h ceiling)"
    )

    # 5. Record the realized budget before solver launch (Rule 6)
    lifecycle.update_running(
        {
            "device": device,
            "elevation_override": (
                str(elevation_override.relative_to(REPO))
                if elevation_override is not None
                else None
            ),
            "event_window": {
                "start": event_start_iso,
                "end": event_end_iso,
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
    if h0_raster is not None:
        with rasterio.open(h0_raster) as h0_src:
            h0 = h0_src.read(1).astype(np.float32)
        if not np.all(np.isfinite(h0)) or np.any(h0 < 0):
            raise ValueError(f"h0_raster must be finite and non-negative depth: {h0_raster}")
        if tile is not None:
            if h0.shape == (grid.height, grid.width):
                h0 = h0[tile[0] : tile[1], tile[2] : tile[3]]
            if h0.shape != tile_shape:
                raise ValueError(
                    f"h0_raster tile shape {h0.shape} != expected canonical crop {tile_shape}"
                )
            h[...] = torch.as_tensor(h0, dtype=torch.float32, device=device)
        elif h0.shape == (grid.height, grid.width):
            h[
                buf_cells : buf_cells + grid.height,
                buf_cells : buf_cells + grid.width,
            ] = torch.as_tensor(h0, dtype=torch.float32, device=device)
        elif h0.shape == static.shape:
            h[...] = torch.as_tensor(h0, dtype=torch.float32, device=device)
        else:
            raise ValueError(
                f"h0_raster shape {h0.shape} not compatible with canonical "
                f"{(grid.height, grid.width)} or solver {(static.shape[0], static.shape[1])}"
            )
    budget.start(h)
    cumulative_transport_depth_m = torch.zeros(static.shape, dtype=torch.float64, device=device)
    cumulative_drain_depth_m = torch.zeros(static.shape, dtype=torch.float64, device=device)
    cumulative_infiltration_depth_m = torch.zeros(static.shape, dtype=torch.float64, device=device)

    h_canonical_max = np.zeros(
        tile_shape if tile is not None else (grid.height, grid.width), dtype=np.float32
    )

    snapshots_meta: list[tuple[float, str]] = []
    snapshots_dir = out_dir / "depth_rasters"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    profile_canon = grid.profile(dtype="float32", nodata=None)
    if tile is not None:
        profile_canon["height"] = tile_shape[0]
        profile_canon["width"] = tile_shape[1]
        profile_canon["transform"] = grid.transform * rasterio.Affine.translation(tile[2], tile[0])

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

            if tile is not None:
                h_cpu_canon = h.detach().cpu().numpy()
            else:
                h_cpu_canon = (
                    h[buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width]
                    .detach()
                    .cpu()
                    .numpy()
                )
            np.maximum(h_canonical_max, h_cpu_canon, out=h_canonical_max)

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
    if tile is not None:
        h_final_canon = h.detach().cpu().numpy()
    else:
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
    if tile is not None:
        buffered_transform = profile_canon["transform"]
    else:
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
                "quantity": (
                    "sum_of_accepted_post_limiter_div_x_fx_plus_div_y_fy_minus_domain_outflow"
                ),
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

    imerg_cell_ids_path = out_dir / "imerg_native_cell_ids.tif"
    profile_int = grid.profile(dtype="int32", nodata=-1)
    if tile is not None:
        profile_int["height"] = tile_shape[0]
        profile_int["width"] = tile_shape[1]
        profile_int["transform"] = grid.transform * rasterio.Affine.translation(tile[2], tile[0])
        native_ids_write = adapter.native_cell_ids[tile[0] : tile[1], tile[2] : tile[3]]
    else:
        native_ids_write = adapter.native_cell_ids
    with rasterio.open(imerg_cell_ids_path, "w", **profile_int) as dst:
        dst.write(native_ids_write.astype(np.int32), 1)

    dt_sidecar = write_dt_sidecar(ctrl.schedule, out_dir / "dt_schedule.f32.gz")
    lifecycle.update_running({"dt_schedule": dt_sidecar})

    typer.echo("\nSimulation completed successfully.")
    typer.echo(f"  Total Steps: {steps:,}")
    typer.echo(f"  Simulated Duration: {t / 3600:.2f} hours")
    typer.echo(f"  Wall Clock: {wall_clock:.1f} s ({wall_clock / 60:.2f} min)")
    typer.echo(
        f"  Mass Relative Residual: {budget.relative_residual():.3e} "
        f"(Tolerance: {budget.relative_tolerance:.1e})"
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
    """Score the realized replay against retained BBMP and ground-truth labels."""
    with val_cfg_path.open() as handle:
        val_cfg = yaml.safe_load(handle)
    gt_cfg = val_cfg["groundtruth"]
    road_path = REPO / gt_cfg["road_segment_id_path"]
    with rasterio.open(road_path) as src:
        grid_transform, grid_shape = src.transform, src.shape
    if sim_out.h_canonical_max.shape != grid_shape:
        raise ValueError(
            f"replay maximum shape {sim_out.h_canonical_max.shape} != road label grid {grid_shape}"
        )

    bbmp_dir = REPO / gt_cfg["bbmp_kml_dir"]
    bbmp_points: list[tuple[float, float, str]] = []
    trans_wgs_to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)

    for kml_file in sorted(bbmp_dir.glob("*.kml")):
        gdf = gpd.read_file(kml_file)
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom.geom_type == "Point":
                ux, uy = trans_wgs_to_utm.transform(geom.x, geom.y)
                p_name = str(row.get("Name", row.get("Description", kml_file.stem)))
                bbmp_points.append((ux, uy, p_name))

    bbmp_sweep_scores: list[dict[str, Any]] = []
    bbmp_depths: list[float] = []

    for ux, uy, _ in bbmp_points:
        row, col = rasterio.transform.rowcol(grid_transform, ux, uy)
        if 0 <= row < grid_shape[0] and 0 <= col < grid_shape[1]:
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
            r0, r1 = max(0, row - 1), min(grid_shape[0], row + 2)
            c0, c1 = max(0, col - 1), min(grid_shape[1], col + 2)
            model_depth_point = float(sim_out.h_canonical_max[row, col])
            model_depth_local_max = float(np.max(sim_out.h_canonical_max[r0:r1, c0:c1]))
        else:
            model_depth_point = 0.0
            model_depth_local_max = 0.0

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
            "excluded_description": "No SAR/permanent-water exclusion; retained labels only.",
            "excluded_cell_count": 0,
            "domain_total_cells": sim_out.h_canonical_max.size,
            "excluded_pct_of_domain": 0.0,
        },
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
        "retired_instruments": {
            "sar": "retired_invalid_instrument",
            "density_stratification": "retired_with_sar_pipeline",
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
