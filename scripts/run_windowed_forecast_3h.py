#!/usr/bin/env python
"Windowed 3-hour uncoupled forecast driver (SIH-26085 presentation spine). Issues an UNCOUPLED forecast issued at T=2022-09-04T18:40Z, valid to T+3h (2022-09-04T21:40Z, the flooded-segment rise). It is driven by the KSNDMC-alert-anchored IMERG rainfall that ACTUALLY fell over that window -- i.e. a PERFECT RAINFALL NOWCAST experiment. It isolates the flood model from rainfall-forecast error; rainfall nowcasting is IMD's job, not this model's. The claim being demonstrated is predicting WHERE water goes given rainfall, never predicting rainfall. That label is written into the manifest, and every consumer must carry it. WARM START: a nowcast at time T starts from the city's state at T, so when --h0-raster is given the solver is initialized from that realized depth field (the replay's frame at the issue instant) instead of a dry start. --tile \"r0,r1,c0,c1\" runs the same window on a 1/N canonical-coordinate subdomain tile: the subdomain throughput measurement that decides whether real-time (gate G4, <=10 min for 3 h) is claimable at all. This driver does NOT score anything against the satellite observation -- that is a separate job. Ownership: this driver owns its own run directory and this script only. It does not touch coupling/drainage/web/routing/demo/contracts paths."  # noqa: E501

from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import torch  # noqa: E402
import typer  # noqa: E402
import yaml  # noqa: E402

from jaladhar.provenance import RunManifest  # noqa: E402
from jaladhar.solver.state import load_solver_config  # noqa: E402
from jaladhar.validation.event_replay import (  # noqa: E402
    resolve_phase3_config,
    run_phase3_simulation,
)

app = typer.Typer(add_completion=False)

ISSUED_DEFAULT = "2022-09-04T18:40:00Z"
VALID_DEFAULT = "2022-09-04T21:40:00Z"


def valid_state_slug(valid_time_iso: str) -> str:
    parsed = datetime.fromisoformat(valid_time_iso.replace("Z", "+00:00"))
    return parsed.strftime("%Y%m%dT%H%MZ")


def parse_tile(value: str | None) -> tuple[int, int, int, int] | None:
    if value is None or not value.strip():
        return None
    parts = [int(item) for item in value.split(",")]
    if len(parts) != 4:
        raise typer.BadParameter("--tile must be 'r0,r1,c0,c1'")
    r0, r1, c0, c1 = parts
    if not (r0 < r1 and c0 < c1):
        raise typer.BadParameter(f"--tile must satisfy r0<r1 and c0<c1, got {value}")
    return (r0, r1, c0, c1)


class GPUStatsSampler:

    def __init__(self, interval_s: float = 3.0) -> None:
        self.interval_s = interval_s
        self.util_percent: list[float] = []
        self.vram_used_mib: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu,memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    util_text, vram_text = result.stdout.strip().split(",")
                    self.util_percent.append(float(util_text))
                    self.vram_used_mib.append(float(vram_text))
            except Exception:  # noqa: BLE001 - sampler must never kill the run
                pass
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> dict[str, float]:
        self._stop.set()
        self._thread.join(timeout=10)
        return {
            "gpu_util_max_percent": max(self.util_percent) if self.util_percent else 0.0,
            "gpu_util_mean_percent": (
                float(np.mean(self.util_percent)) if self.util_percent else 0.0
            ),
            "gpu_vram_used_max_mib": max(self.vram_used_mib) if self.vram_used_mib else 0.0,
            "samples": len(self.util_percent),
        }


def read_dt_schedule(path: Path) -> dict[str, float] | None:
    import gzip

    if not path.exists():
        return None
    try:
        with gzip.open(path, "rb") as handle:
            dt = np.frombuffer(handle.read(), dtype=np.float32)
        if dt.size == 0:
            return None
        return {
            "count": int(dt.size),
            "min_s": float(dt.min()),
            "mean_s": float(dt.mean()),
            "max_s": float(dt.max()),
        }
    except Exception:  # noqa: BLE001 - sidecar is a report, not the run
        return None


@app.command()
def main(
    solver_config: Path = typer.Option(REPO / "configs/solver.yaml", help="Solver config"),
    forcing_config: Path = typer.Option(REPO / "configs/forcing.yaml", help="Forcing config"),
    val_config: Path = typer.Option(REPO / "configs/validation.yaml", help="Validation config"),
    compute_config: Path = typer.Option(REPO / "configs/compute.yaml", help="Compute config"),
    out: Path = typer.Option(REPO / "runs/wf3_uncoupled_3h_forecast", help="Fresh run directory"),
    issue_time_iso: str = typer.Option(ISSUED_DEFAULT, help="Forecast issue time UTC"),
    valid_time_iso: str = typer.Option(VALID_DEFAULT, help="Forecast valid time UTC"),
    dirty_tree_reason: str | None = typer.Option(
        None,
        help="Explicit reason admitting a dirty source tree (recorded, never laundered)",
    ),
    h0_raster: Path | None = typer.Option(
        None, help="Warm start: realized depth raster (canonical grid) at the issue instant"
    ),
    tile: str | None = typer.Option(
        None,
        help="Subdomain tile 'r0,r1,c0,c1' in canonical coords (throughput measurement)",
    ),
) -> None:
    out = out.resolve()
    if out.exists():
        typer.echo(f"FATAL: output directory already exists: {out}", err=True)
        raise typer.Exit(code=1)
    tile_tuple = parse_tile(tile)

    solver_cfg = load_solver_config(solver_config, REPO)
    with forcing_config.open() as handle:
        forcing_cfg = yaml.safe_load(handle)
    with val_config.open() as handle:
        val_cfg = yaml.safe_load(handle)
    with compute_config.open() as handle:
        compute_cfg = yaml.safe_load(handle)
    solver_cfg["_compute"] = compute_cfg
    resolve_phase3_config(solver_cfg, forcing_cfg, val_cfg, compute_cfg)

    domain = solver_cfg["_domain"]
    buffer_cells = round(float(domain["dem"]["buffer_m"]) / float(domain["resolution_m"]))
    warm_start = {
        "enabled": h0_raster is not None,
        "h0_raster": str(h0_raster) if h0_raster is not None else None,
        "basis": (
            "a nowcast at time T starts from the city's state at T; h0 is the "
            "replay's realized depth field at the issue instant (frame "
            "t066600s = 2022-09-04T18:30Z, the last frame at/before the 18:40Z "
            "issue), embedded into the solver's buffered domain with dry margins"
            if h0_raster is not None
            else None
        ),
        "velocity_initialised": (
            "zero (frames carry depth only; the velocity field re-equilibrates)"
        ),
    }
    run_definition = {
        "kind": "perfect_rainfall_nowcast",
        "label": (
            "FLOOD FORECAST GIVEN A PERFECT RAINFALL NOWCAST (actual IMERG rainfall). "
            "NOT a rainfall forecast: the model predicts WHERE water goes given "
            "rainfall, it does not predict rainfall."
        ),
        "issued_utc": issue_time_iso,
        "valid_utc": valid_time_iso,
        "lead_minutes": int(
            (
                datetime.fromisoformat(valid_time_iso.replace("Z", "+00:00"))
                - datetime.fromisoformat(issue_time_iso.replace("Z", "+00:00"))
            ).total_seconds()
            // 60
        ),
        "rainfall_source": (
            "KSNDMC-alert-anchored GPM IMERG v07 Final (historical), window "
            f"{issue_time_iso}..{valid_time_iso} -- the actual rainfall"
        ),
        "rainfall_is_predicted": False,
        "rainfall_is_observed": True,
        "coupling": "uncoupled",
        "coupling_note": "drain coupling is not validated; this run uses the uncoupled solver",
        "window_note": (
            "new window: issue 18:40Z, valid 21:40Z, ending at the flooded-segment "
            "rise (peak ~21:30Z); forecasts the rise, not the recession"
        ),
    }

    resolved_config = {
        "solver": solver_cfg,
        "forcing": forcing_cfg,
        "compute": compute_cfg,
        "run_definition": run_definition,
        "warm_start": warm_start,
        "tile": {"canonical_coords": list(tile_tuple)} if tile_tuple is not None else None,
        "domain_config": {
            "resolution_m": domain["resolution_m"],
            "crs": domain["crs"],
            "dem_buffer_m": domain["dem"]["buffer_m"],
            "buffer_cells": buffer_cells,
        },
    }

    lifecycle = RunManifest(
        out / "manifest.json",
        stage=(
            "wf3_uncoupled_3h_windowed_forecast_tile"
            if tile_tuple is not None
            else "wf3_uncoupled_3h_windowed_forecast"
        ),
        repo_root=REPO,
        resolved_config=resolved_config,
        config_paths={
            "solver": str(solver_config),
            "forcing": str(forcing_config),
            "validation": str(val_config),
            "compute": str(compute_config),
        },
        fields={
            "coupling_enabled": False,
            "forecast_experiment": run_definition,
            "warm_start": warm_start,
            "tile": {"canonical_coords": list(tile_tuple)} if tile_tuple is not None else None,
            "window": {
                "start": issue_time_iso,
                "end": valid_time_iso,
            },
        },
        dirty_tree_admission_reason=dirty_tree_reason,
    )
    lifecycle.start()

    sampler = GPUStatsSampler(interval_s=3.0)
    sampler.start()
    baseline_smi = subprocess.run(
        ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    t0 = time.perf_counter()

    try:
        sim_out = run_phase3_simulation(
            solver_cfg_path=solver_config,
            forcing_cfg_path=forcing_config,
            val_cfg_path=val_config,
            compute_cfg_path=compute_config,
            out_dir=out,
            event_start_iso=issue_time_iso,
            event_end_iso=valid_time_iso,
            lifecycle=lifecycle,
            h0_raster=h0_raster,
            tile=tile_tuple,
        )
    except BaseException as exc:
        sampler.stop()
        lifecycle.fail(exc)
        raise

    wall_clock_s = time.perf_counter() - t0
    gpu_stats = sampler.stop()
    after_smi = subprocess.run(
        ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader"],
        capture_output=True,
        text=True,
    ).stdout.strip()

    peak_vram_torch_mib = float(torch.cuda.max_memory_allocated()) / 2**20
    ms_per_step = (wall_clock_s * 1000.0) / sim_out.steps if sim_out.steps else 0.0
    dt_realized = read_dt_schedule(out / "dt_schedule.f32.gz")
    valid_state_slug_s = valid_state_slug(valid_time_iso)
    valid_state_path = out / f"depth_valid_{valid_state_slug_s}.tif"
    with rasterio.open(out / "depth_final.tif") as final_src:
        valid_profile = final_src.profile.copy()
    with rasterio.open(valid_state_path, "w", **valid_profile) as dst:
        dst.write(sim_out.h_sar_instant, 1)
    domain_cells_canonical = sim_out.h_canonical_final.size

    try:
        lifecycle.complete(
            {
                "latency_g4": {
                    "wall_clock_sec": round(wall_clock_s, 3),
                    "wall_clock_min": round(wall_clock_s / 60.0, 3),
                    "ms_per_step": round(ms_per_step, 4),
                    "steps": sim_out.steps,
                    "sim_time_s": round(sim_out.sim_time_s, 3),
                    "realized_dt": dt_realized,
                    "g4_requirement_min": 10.0,
                    "g4_passes": wall_clock_s <= 600.0,
                    "g4_note": (
                        "G4 requires a 3-hour forecast in <=10 min wall clock; "
                        "the run is the measurement, reported honestly"
                    ),
                },
                "domain_config_realized": {
                    "mode": "subdomain_tile" if tile_tuple is not None else "full_buffered_domain",
                    "tile_canonical_coords": list(tile_tuple) if tile_tuple is not None else None,
                    "tile_shape": (
                        list(sim_out.h_canonical_final.shape) if tile_tuple is not None else None
                    ),
                    "canonical_grid_shape": list(sim_out.h_canonical_final.shape),
                    "resolution_m": domain["resolution_m"],
                    "dem_buffer_m": domain["dem"]["buffer_m"],
                    "buffer_cells": buffer_cells,
                    "cells": int(domain_cells_canonical),
                },
                "gpu_realized": {
                    "device": "cuda",
                    "device_name": torch.cuda.get_device_name(0),
                    "sampled": gpu_stats,
                    "peak_vram_torch_allocated_mib": round(peak_vram_torch_mib, 3),
                    "nvidia_smi_before": baseline_smi,
                    "nvidia_smi_after": after_smi,
                },
                "solver_results": {
                    "max_courant_realized": round(sim_out.max_courant, 4),
                    "max_selected_courant": round(sim_out.max_selected_courant, 4),
                    "rejected_steps": sim_out.rejected_steps,
                    "retry_attempts": sim_out.retry_attempts,
                    "mass_relative_residual": sim_out.mass_dict["relative_residual"],
                    "total_rain_volume_m3": sim_out.total_rain_volume_m3,
                    "areal_mean_rain_mm": sim_out.areal_mean_rain_mm,
                    "distinct_imerg_cells": sim_out.distinct_imerg_cells,
                },
                "artifacts": {
                    "final_depth": sim_out.final_depth_path,
                    "final_depth_buffered": sim_out.final_depth_buffered_path,
                    "valid_state_depth": str(valid_state_path.relative_to(REPO)),
                    "valid_state_utc": valid_time_iso,
                    "note": (
                        "the in-loop snapshot raster "
                        "'depth_sar_instant_20220905_004028Z.tif' is a driver-artifact "
                        "name (hardcoded upstream) and holds the valid-time state; the "
                        "correctly-named valid_state_depth raster above is authoritative"
                    ),
                    "cumulative_transport_depth": sim_out.cumulative_transport_depth_path,
                    "cumulative_drain_depth": sim_out.cumulative_drain_depth_path,
                    "cumulative_infiltration_depth": sim_out.cumulative_infiltration_depth_path,
                    "dt_schedule": f"runs/{out.name}/dt_schedule.f32.gz",
                },
                "note": (
                    "This run does NOT score against the Sentinel-1 observation; "
                    "comparison is a separate job. The 'final state' for the v6 "
                    "product is the valid-time depth raster (state at "
                    f"{valid_time_iso})."
                ),
            }
        )
    except BaseException as exc:
        lifecycle.fail(exc)
        raise

    typer.echo("=" * 78)
    typer.echo("WF3 UNCoupled 3h Windowed Forecast — COMPLETE")
    typer.echo("=" * 78)
    typer.echo(f"  wall clock:      {wall_clock_s / 60:.2f} min ({wall_clock_s:.1f} s)")
    typer.echo(f"  ms/step:         {ms_per_step:.2f}")
    typer.echo(f"  steps:           {sim_out.steps:,}")
    typer.echo(f"  sim time:        {sim_out.sim_time_s / 3600:.3f} h")
    typer.echo(f"  mean dt:         {dt_realized['mean_s'] if dt_realized else 'n/a'} s")
    typer.echo(f"  peak VRAM (torch): {peak_vram_torch_mib:.1f} MiB")
    typer.echo(f"  peak VRAM (smi):  {gpu_stats['gpu_vram_used_max_mib']:.1f} MiB")
    typer.echo(f"  GPU util max:    {gpu_stats['gpu_util_max_percent']:.1f}%")
    typer.echo(
        f"  G4 (<=10 min):   {'PASS' if wall_clock_s <= 600.0 else 'FAIL'} — measured, not tuned"
    )
    typer.echo(f"  manifest:        {out / 'manifest.json'}")
    typer.echo(f"  valid-state depth raster: {valid_state_path}")


if __name__ == "__main__":
    app()
