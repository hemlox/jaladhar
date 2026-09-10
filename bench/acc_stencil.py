"Phase 0, open question A — hardware throughput of the ACC solver kernel. WHAT THIS MEASURES, AND WHAT IT DOES NOT ---------------------------------------- This is a *hardware* benchmark, not a hydrological result. It runs the exact tensor operations the Bates et al. (2010) local-inertial scheme performs per timestep -- the same stencil shape, dtype, and memory traffic -- on synthetic tensors, and measures milliseconds per timestep and peak VRAM against grid size. Stencil arithmetic cost is independent of the *values* in the elevation array, so ms/timestep is a genuine measurement. It is NOT a claim about Bengaluru. No depth, discharge, or flood extent produced here means anything physically; nothing from this file may be reported as a hydrological finding. THE BAND, AND WHY THERE IS ONE ------------------------------ Wall clock per scenario = (ms/timestep) x (timesteps per simulated hour). The first factor is measured here. The second comes from the CFL condition dt = alpha * dx / sqrt(g * h_max) where h_max is the maximum water depth in the domain -- physics we do not have in Phase 0. So timesteps/hour is reported as a BAND over an explicit range of (alpha, h_max), never as a point estimate. Phase 2 collapses the band by measuring real h_max from a real storm. Per CLAUDE.md: tensors are allocated directly on device. Host RAM on this machine is tighter than VRAM, and a 32M-cell CPU-side stack would OOM the box."  # noqa: E501

from __future__ import annotations

import json
import math
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import torch
import typer

app = typer.Typer(add_completion=False)

G = 9.81  # m/s^2


@dataclass(frozen=True)
class GridSpec:

    resolution_m: float
    width_m: float
    height_m: float

    @property
    def nx(self) -> int:
        return math.ceil(self.width_m / self.resolution_m)

    @property
    def ny(self) -> int:
        return math.ceil(self.height_m / self.resolution_m)

    @property
    def cells(self) -> int:
        return self.nx * self.ny


def acc_timestep(
    h: torch.Tensor,
    z: torch.Tensor,
    qx: torch.Tensor,
    qy: torch.Tensor,
    n: torch.Tensor,
    dx: float,
    dt: float,
    depth_threshold: float = 1e-3,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    "One local-inertial (ACC) timestep. Shape-faithful to the real scheme. Flux, x-direction (y is symmetric): h_flow = max(h_i + z_i, h_j + z_j) - max(z_i, z_j) q_(t+dt) = (q_t - g * h_flow * dt * d(h+z)/dx) / (1 + g * dt * n^2 * |q_t| / h_flow^(7/3)) Guarded by a depth threshold: below ~1 mm the flux is zeroed, which is required for stability and avoids spending compute on dry cells."  # noqa: E501
    eta = h + z

    # --- x faces ---
    eta_i, eta_j = eta[:, :-1], eta[:, 1:]
    z_i, z_j = z[:, :-1], z[:, 1:]
    hf_x = torch.maximum(eta_i, eta_j) - torch.maximum(z_i, z_j)
    wet_x = hf_x > depth_threshold
    hf_x = torch.clamp(hf_x, min=depth_threshold)

    slope_x = (eta_j - eta_i) / dx
    n_x = 0.5 * (n[:, :-1] + n[:, 1:])
    num_x = qx - G * hf_x * dt * slope_x
    den_x = 1.0 + G * dt * n_x.pow(2) * qx.abs() / hf_x.pow(7.0 / 3.0)
    qx = torch.where(wet_x, num_x / den_x, torch.zeros_like(qx))

    # --- y faces ---
    eta_i, eta_j = eta[:-1, :], eta[1:, :]
    z_i, z_j = z[:-1, :], z[1:, :]
    hf_y = torch.maximum(eta_i, eta_j) - torch.maximum(z_i, z_j)
    wet_y = hf_y > depth_threshold
    hf_y = torch.clamp(hf_y, min=depth_threshold)

    slope_y = (eta_j - eta_i) / dx
    n_y = 0.5 * (n[:-1, :] + n[1:, :])
    num_y = qy - G * hf_y * dt * slope_y
    den_y = 1.0 + G * dt * n_y.pow(2) * qy.abs() / hf_y.pow(7.0 / 3.0)
    qy = torch.where(wet_y, num_y / den_y, torch.zeros_like(qy))

    # --- mass balance ---
    div = torch.zeros_like(h)
    div[:, 1:] += qx
    div[:, :-1] -= qx
    div[1:, :] += qy
    div[:-1, :] -= qy
    h = torch.clamp(h + div * (dt / dx), min=0.0)
    return h, qx, qy


def timesteps_per_hour(dx: float, alpha: float, h_max: float) -> float:
    dt = alpha * dx / math.sqrt(G * h_max)
    return 3600.0 / dt


def bench_grid(nx: int, ny: int, dx: float, iters: int, warmup: int, device: str) -> dict:
    torch.cuda.reset_peak_memory_stats(device) if device == "cuda" else None
    gen = torch.Generator(device=device).manual_seed(0)

    # Synthetic terrain: a gentle plane plus noise. Values are arbitrary -- only the tensor SHAPE
    # and dtype affect the kernel cost being measured.
    z = torch.rand((ny, nx), generator=gen, device=device, dtype=torch.float32) * 10.0
    z += torch.linspace(0, 50, nx, device=device, dtype=torch.float32).unsqueeze(0)
    h = torch.full((ny, nx), 0.05, device=device, dtype=torch.float32)
    n = torch.full((ny, nx), 0.03, device=device, dtype=torch.float32)
    qx = torch.zeros((ny, nx - 1), device=device, dtype=torch.float32)
    qy = torch.zeros((ny - 1, nx), device=device, dtype=torch.float32)

    dt = 0.5
    for _ in range(warmup):
        h, qx, qy = acc_timestep(h, z, qx, qy, n, dx, dt)
    if device == "cuda":
        torch.cuda.synchronize()

    t0 = time.perf_counter()
    for _ in range(iters):
        h, qx, qy = acc_timestep(h, z, qx, qy, n, dx, dt)
    if device == "cuda":
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0

    peak = torch.cuda.max_memory_allocated(device) / 2**20 if device == "cuda" else float("nan")
    result = {
        "nx": nx,
        "ny": ny,
        "cells": nx * ny,
        "iters": iters,
        "ms_per_timestep": 1000.0 * elapsed / iters,
        "peak_vram_mib": peak,
    }
    del z, h, n, qx, qy
    if device == "cuda":
        torch.cuda.empty_cache()
    return result


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


@app.command()
def main(
    width_m: float = typer.Option(35139.0, help="BBMP extent width, metres (UTM 43N)"),
    height_m: float = typer.Option(34199.0, help="BBMP extent height, metres (UTM 43N)"),
    area_km2: float = typer.Option(717.0, help="Measured BBMP union area, km^2"),
    iters: int = typer.Option(50, help="Timed iterations per grid"),
    warmup: int = typer.Option(10, help="Warm-up iterations"),
    out: Path = typer.Option(Path("runs/bench_phase0"), help="Output directory"),
) -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        typer.echo("WARNING: no CUDA device — CPU timings are not comparable to the GPU target.")

    grids = [GridSpec(r, width_m, height_m) for r in (5.0, 10.0, 20.0, 30.0)]

    typer.echo(
        f"device: {torch.cuda.get_device_name(0) if device=='cuda' else platform.processor()}"
    )
    typer.echo(
        f"BBMP extent: {width_m:,.0f} m x {height_m:,.0f} m (bbox), {area_km2:,.1f} km^2 (union)\n"
    )

    rows = []
    for gspec in grids:
        r = bench_grid(gspec.nx, gspec.ny, gspec.resolution_m, iters, warmup, device)
        # cells actually inside the BBMP polygon, not the bbox
        r["resolution_m"] = gspec.resolution_m
        r["cells_in_bbox"] = gspec.cells
        r["cells_in_bbmp"] = int(area_km2 * 1e6 / gspec.resolution_m**2)
        rows.append(r)
        typer.echo(
            f"  {gspec.resolution_m:>4.0f} m  {gspec.nx:>5}x{gspec.ny:<5}  "
            f"{gspec.cells/1e6:>6.2f} M cells (bbox)  "
            f"{r['ms_per_timestep']:>8.2f} ms/step  "
            f"{r['peak_vram_mib']:>8.1f} MiB peak"
        )

    # --- the band: timesteps/hour over the unknown (alpha, h_max) ---
    alphas = [0.5, 0.7]
    h_maxes = [0.3, 1.0, 3.0]  # m — plausible urban range, NOT measured
    typer.echo("\nWall clock per SIMULATED HOUR, as a band over unmeasured (alpha, h_max):")
    typer.echo("  (h_max is Phase 2 physics; this band is not a prediction)")
    for r in rows:
        dx = r["resolution_m"]
        lo = min(timesteps_per_hour(dx, a, hm) for a in alphas for hm in h_maxes)
        hi = max(timesteps_per_hour(dx, a, hm) for a in alphas for hm in h_maxes)
        r["timesteps_per_hour_min"] = lo
        r["timesteps_per_hour_max"] = hi
        s_lo = lo * r["ms_per_timestep"] / 1000.0
        s_hi = hi * r["ms_per_timestep"] / 1000.0
        r["sec_per_sim_hour_min"] = s_lo
        r["sec_per_sim_hour_max"] = s_hi
        typer.echo(
            f"  {dx:>4.0f} m  timesteps/h {lo:>8.0f}..{hi:<8.0f}  "
            f"->  {s_lo:>7.1f}..{s_hi:<8.1f} s per simulated hour"
        )

    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "stage": "phase0_open_question_A",
        "kind": "hardware_throughput_benchmark",
        "disclaimer": (
            "Synthetic tensors. Measures kernel cost only. Not a hydrological result; "
            "no value here describes Bengaluru."
        ),
        "git_sha": git_sha(),
        "device": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "torch": torch.__version__,
        "total_vram_mib": (
            torch.cuda.get_device_properties(0).total_memory / 2**20 if device == "cuda" else None
        ),
        "bbmp_extent": {"width_m": width_m, "height_m": height_m, "area_km2": area_km2},
        "cfl_band_params": {"alpha": alphas, "h_max_m": h_maxes},
        "results": rows,
    }
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2))
    typer.echo(f"\nwrote {path}")


if __name__ == "__main__":
    app()
