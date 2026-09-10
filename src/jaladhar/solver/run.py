"""analytical ladder, and the invariant tests. Having a single driver means the"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch
import typer

from jaladhar.solver.acc import SolverParams, acc_step
from jaladhar.solver.mass import MassBudget
from jaladhar.solver.state import StaticFields, initial_state, load_domain, load_solver_config
from jaladhar.solver.timestep import TimestepController
from jaladhar.terrain.grid import build_grid

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


def git_sha() -> str:
    """CLAUDE.md rule 6. Phase 1's bug #7/#1 was manifests written by a CLI that
    and shipped a manifest with git_sha: None. `tests/test_manifest_provenance.py`
    now enforces it across every manifest under runs/, so the next CLI that"""
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


@dataclass
class RunResult:
    h: torch.Tensor
    qx: torch.Tensor
    qy: torch.Tensor
    steps: int
    sim_time_s: float
    wall_clock_s: float
    controller: TimestepController
    budget: MassBudget | None
    max_courant: float = 0.0  # realized, evaluated AFTER the step
    max_selected_courant: float = 0.0
    steps_exceeding_selected: int = 0
    rejected_steps: int = 0
    retry_attempts: int = 0
    min_depth: float = 0.0
    max_abs_q: float = 0.0
    peak_negative_cells: int = 0
    min_cell_drained_m: float = 0.0
    min_cell_infiltrated_m: float = 0.0
    depth_snapshots: list[tuple[float, torch.Tensor]] = field(default_factory=list)


def uniform_storm(total_mm: float, duration_s: float) -> Callable[[float], float]:
    """A synthetic DESIGN storm — PROMPT.md §7's acceptance requires one.
    Never presented as observed data. Real forcing stays blocked until the"""
    rate = total_mm / 1000.0 / duration_s

    def rain(t: float) -> float:
        return rate if t < duration_s else 0.0

    return rain


def simulate(
    static: StaticFields,
    p: SolverParams,
    controller: TimestepController,
    *,
    duration_s: float,
    rain: Callable[[float], float] | None = None,
    h0: torch.Tensor | None = None,
    budget: MassBudget | None = None,
    mass_check_every: int = 100,
    snapshot_every_s: float | None = None,
    max_steps: int = 2_000_000,
    device: str = "cpu",
    record_schedule: bool = True,
) -> RunResult:
    h, qx, qy = initial_state(static, device=device)
    if h0 is not None:
        h = h0.clone()
    n_cells = static.shape[0] * static.shape[1]
    if budget is not None:
        budget.start(h)

    t, steps, max_courant, next_snap = 0.0, 0, 0.0, 0.0
    max_selected = 0.0
    steps_exceeding = 0
    min_depth, max_abs_q, neg_cells = 0.0, 0.0, 0
    min_cell_drain, min_cell_infil = 0.0, 0.0
    snapshots: list[tuple[float, torch.Tensor]] = []
    t0 = time.perf_counter()

    steps_with_rejection = 0
    with torch.no_grad():
        while t < duration_s and steps < max_steps:
            dt = controller.dt_for(h)
            dt = min(dt, duration_s - t)
            if dt <= 0:
                break
            r = rain(t) if rain is not None else 0.0
            c_sel = controller.courant(h, dt)
            max_selected = max(max_selected, c_sel)

            # If realized Courant exceeds cfl_ceiling in the recording pass,
            had_rejection = False
            while True:
                h_new, qx_new, qy_new, diag = acc_step(h, qx, qy, static, dt, p, rain_rate_m_s=r)
                c_realized = controller.courant(h_new, dt)
                if (
                    record_schedule
                    and c_realized > controller.cfl_ceiling
                    and dt > controller.min_dt_s
                ):
                    controller.rejections += 1
                    had_rejection = True
                    dt = max(dt / 2.0, controller.min_dt_s)
                    continue
                break

            if had_rejection:
                steps_with_rejection += 1

            h, qx, qy = h_new, qx_new, qy_new
            if record_schedule:
                controller.schedule.append(dt)
            max_courant = max(max_courant, c_realized)
            if c_realized > c_sel + 1e-6:
                steps_exceeding += 1
            min_depth = min(min_depth, float(h.min()))
            max_abs_q = max(max_abs_q, float(qx.abs().max()), float(qy.abs().max()))
            neg_cells = max(neg_cells, int(diag["negative_depth_cells"]))
            min_cell_drain = min(min_cell_drain, float(diag["min_drained_m"]))
            min_cell_infil = min(min_cell_infil, float(diag["min_infiltrated_m"]))

            if budget is not None:
                budget.accumulate(h, diag, r, dt, n_cells)
            t += dt
            steps += 1
            if budget is not None and steps % mass_check_every == 0:
                budget.check(steps)
            if snapshot_every_s is not None and t >= next_snap:
                snapshots.append((t, h.detach().cpu()))
                next_snap += snapshot_every_s

    return RunResult(
        h=h,
        qx=qx,
        qy=qy,
        steps=steps,
        sim_time_s=t,
        wall_clock_s=time.perf_counter() - t0,
        controller=controller,
        budget=budget,
        max_courant=max_courant,
        max_selected_courant=max_selected,
        steps_exceeding_selected=steps_exceeding,
        rejected_steps=steps_with_rejection,
        retry_attempts=controller.rejections,
        min_depth=min_depth,
        max_abs_q=max_abs_q,
        peak_negative_cells=neg_cells,
        min_cell_drained_m=min_cell_drain,
        min_cell_infiltrated_m=min_cell_infil,
        depth_snapshots=snapshots,
    )


def replay(
    static: StaticFields,
    p: SolverParams,
    schedule: list[float],
    *,
    rain: Callable[[float], float] | None = None,
    h0: torch.Tensor | None = None,
    device: str = "cpu",
) -> torch.Tensor:
    h, qx, qy = initial_state(static, device=device)
    if h0 is not None:
        h = h0
    t = 0.0
    for dt in schedule:
        r = rain(t) if rain is not None else 0.0
        h, qx, qy, _ = acc_step(h, qx, qy, static, dt, p, rain_rate_m_s=r)
        t += dt
    return h


def estimate_steps_band(
    cfg: dict[str, Any], duration_s: float, h_max_band: tuple[float, float]
) -> tuple[int, int]:
    """not honour. Cause was an h_max ceiling of 3.0 m against a realized 23.9 m;
    the formula was fine (0.7*10/sqrt(9.81*23.9) = 0.457 s -> ~47k steps, which
    A gate whose purpose is refusing over-budget work MUST fail safe, so it is"""
    lo = estimate_steps(cfg, duration_s, max(h_max_band))
    hi = estimate_steps(cfg, duration_s, min(h_max_band))
    return (min(lo, hi), max(lo, hi))


def estimate_steps(cfg: dict[str, Any], duration_s: float, h_max_m: float) -> int:
    """Timesteps for `duration_s` under the configured CFL, at an assumed h_max."""
    alpha = float(cfg["timestep"]["cfl_alpha"])
    g = float(cfg["physics"]["gravity_m_s2"])
    dx = float(cfg["_domain"]["resolution_m"])
    dt = min(alpha * dx / (g * max(h_max_m, 1e-6)) ** 0.5, float(cfg["timestep"]["max_dt_s"]))
    return max(int(duration_s / dt), 1)


def compute_budget_estimate(cfg: dict[str, Any], n_cells: int, steps: int) -> dict[str, Any]:
    """CLAUDE.md §Compute: emit the estimate BEFORE any long run, and refuse to"""
    anchor = cfg["_compute"]["anchor"]
    ms = steps * n_cells * (anchor["ms_per_timestep"] / anchor["cells"])
    hours = ms / 1000.0 / 3600.0
    nightly = float(cfg["_compute"]["budget"]["nightly_gpu_hours"])
    return {
        "cells": n_cells,
        "timesteps": steps,
        "estimated_gpu_hours": hours,
        "nightly_gpu_hours": nightly,
        "fraction_of_nightly": hours / nightly,
        "exceeds": hours > nightly,
    }


def _resolve_config(cfg: dict[str, Any]) -> None:
    missing: list[str] = []

    def check(d: Any, *keys: str) -> None:
        curr = d
        path: list[str] = []
        for k in keys:
            path.append(k)
            if not isinstance(curr, dict) or k not in curr:
                missing.append(".".join(path))
                return
            curr = curr[k]

    check(cfg, "compute_config")
    check(cfg, "_compute", "anchor", "ms_per_timestep")
    check(cfg, "_compute", "anchor", "cells")
    check(cfg, "_compute", "budget", "nightly_gpu_hours")

    check(cfg, "_domain", "resolution_m")
    check(cfg, "_domain", "dem", "buffer_m")

    # Physics & timestep keys
    check(cfg, "physics", "gravity_m_s2")
    check(cfg, "physics", "depth_threshold_m")
    check(cfg, "physics", "hf_floor_m")
    check(cfg, "timestep", "cfl_alpha")
    check(cfg, "timestep", "cfl_ceiling")
    check(cfg, "timestep", "min_dt_s")
    check(cfg, "timestep", "max_dt_s")

    check(cfg, "storm", "simulation_duration_s")
    check(cfg, "storm", "total_mm")
    check(cfg, "storm", "duration_s")
    check(cfg, "compute_estimate", "h_max_band_m")

    # Mass budget keys
    check(cfg, "mass", "relative_tolerance")
    check(cfg, "mass", "relative_tolerance_is_measured")
    check(cfg, "mass", "check_every_steps")

    check(cfg, "output", "dir")
    check(cfg, "output", "write_every_s")

    check(cfg, "buildings", "min_conveyance_factor")
    check(cfg, "buildings", "face_combination")
    check(cfg, "boundaries", "min_bed_slope")
    check(cfg, "boundaries", "mode")

    check(cfg, "wetdry", "mode")
    check(cfg, "wetdry", "ramp_width_m")

    check(cfg, "sinks", "drain", "enabled")
    check(cfg, "sinks", "infiltration", "enabled")
    check(cfg, "sinks", "infiltration", "uniform_rate_mm_per_hr")

    if missing:
        raise KeyError(
            f"_resolve_config: missing {len(missing)} required config key(s): {', '.join(missing)}"
        )


def write_depth_series(
    res: RunResult, cfg: dict[str, Any], out_dir: Path, is_smoke: bool = False
) -> list[str]:
    import rasterio

    from jaladhar.terrain.grid import build_grid

    out_dir.mkdir(parents=True, exist_ok=True)
    grid, _ = build_grid(cfg["_domain"], REPO)
    buf_cells = round(
        float(cfg["_domain"]["dem"]["buffer_m"]) / float(cfg["_domain"]["resolution_m"])
    )
    if is_smoke:
        import affine

        h_shape = res.h.shape
        smoke_transform = grid.transform * affine.Affine.translation(1000, 1000)
        profile = {
            "driver": "GTiff",
            "dtype": "float32",
            "width": h_shape[1],
            "height": h_shape[0],
            "count": 1,
            "crs": grid.crs,
            "transform": smoke_transform,
        }
    else:
        profile = grid.profile(dtype="float32", nodata=None)

    paths = []
    for t, h in res.depth_snapshots:
        path = out_dir / f"depth_t{int(round(t)):06d}s.tif"
        h_np = h.detach().cpu().numpy().astype("float32")
        if not is_smoke and h_np.shape != (grid.height, grid.width):
            h_np = h_np[buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width]
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(h_np, 1)
        paths.append(str(path.relative_to(REPO)))
    return paths


def write_dt_sidecar(schedule: list[float], path: Path) -> dict[str, Any]:
    """The first acceptance run produced a 2.4 MB manifest because the schedule was
    smaller; hundreds of Phase 4 scenarios inline would be gigabytes of manifest."""
    import gzip
    import struct

    path.parent.mkdir(parents=True, exist_ok=True)
    raw = struct.pack(f"<{len(schedule)}f", *schedule)
    with gzip.open(path, "wb") as f:
        f.write(raw)
    return {
        "path": str(path.relative_to(REPO)),
        "format": "gzip(float32 little-endian)",
        "n_steps": len(schedule),
        "bytes_raw": len(raw),
        "bytes_stored": path.stat().st_size,
    }


@app.command()
def main(
    config: Path = typer.Option(REPO / "configs" / "solver.yaml"),
    out: Path = typer.Option(REPO / "runs" / "solver"),
    duration_s: float = typer.Option(0.0, help="Override simulation duration (0 = config)"),
    smoke: bool = typer.Option(False, "--smoke", help="Run 2 timesteps on 64x64 subgrid"),
) -> None:
    """Run the synthetic design storm over the full domain."""
    import yaml

    cfg = load_solver_config(config, REPO)
    with open(REPO / cfg["compute_config"]) as f:
        cfg["_compute"] = yaml.safe_load(f)

    _resolve_config(cfg)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dx = float(cfg["_domain"]["resolution_m"])
    p = SolverParams.from_config(cfg, dx)

    window = (slice(1000, 1064), slice(1000, 1064)) if smoke else None
    static = load_domain(cfg, REPO, window=window, device=device)
    n_cells = static.shape[0] * static.shape[1]

    storm = cfg["storm"]
    if smoke:
        total_s = 20.0
        max_steps = 2
        snapshot_every_s = 10.0
    else:
        total_s = duration_s or float(storm["simulation_duration_s"])
        max_steps = 2_000_000
        snapshot_every_s = float(cfg["output"]["write_every_s"])

    ctrl = TimestepController(
        dx=dx,
        alpha=float(cfg["timestep"]["cfl_alpha"]),
        cfl_ceiling=float(cfg["timestep"]["cfl_ceiling"]),
        gravity=float(cfg["physics"]["gravity_m_s2"]),
        min_dt_s=float(cfg["timestep"]["min_dt_s"]),
        max_dt_s=float(cfg["timestep"]["max_dt_s"]),
    )

    # h_max is Phase 2 physics and not known before the run, so the estimate is
    band = tuple(cfg["compute_estimate"]["h_max_band_m"])
    n_lo, n_hi = estimate_steps_band(cfg, total_s, band)
    est = compute_budget_estimate(cfg, n_cells, n_hi)
    est["h_max_band_m"] = list(band)
    est["steps_band"] = [n_lo, n_hi]
    est["gated_on"] = "pessimistic (deepest h_max, most steps)"
    typer.echo(
        f"compute budget estimate: {est['estimated_gpu_hours']:.3f} GPU-h "
        f"({100*est['fraction_of_nightly']:.1f}% of nightly {est['nightly_gpu_hours']}), "
        f"steps {est['steps_band'][0]:,}-{est['steps_band'][1]:,}, gated on the pessimistic end"
    )
    if not smoke and est["exceeds"]:
        typer.echo("FATAL: would exceed configured nightly budget — refusing to start.")
        raise typer.Exit(code=1)

    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    start_time_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    start_manifest = {
        "stage": "phase2_solver_run",
        "status": "running",
        "git_sha": git_sha(),
        "config_path": str(config),
        "device": device,
        "start_time": start_time_iso,
        "smoke": smoke,
        "compute_budget": est,
    }
    manifest_path.write_text(json.dumps(start_manifest, indent=2, default=str))

    budget = MassBudget(
        cell_area_m2=dx * dx,
        relative_tolerance=float(cfg["mass"]["relative_tolerance"]),
        tolerance_is_measured=bool(cfg["mass"]["relative_tolerance_is_measured"]),
    )
    out_cfg = cfg["output"]
    depth_dir = REPO / out_cfg["dir"]
    res = simulate(
        static,
        p,
        ctrl,
        duration_s=total_s,
        rain=uniform_storm(float(storm["total_mm"]), float(storm["duration_s"])),
        budget=budget,
        mass_check_every=1 if smoke else int(cfg["mass"]["check_every_steps"]),
        snapshot_every_s=snapshot_every_s,
        max_steps=max_steps,
        device=device,
    )

    # acceptance run produced a manifest and NOTHING ELSE — output.write_every_s
    written = write_depth_series(res, cfg, depth_dir, is_smoke=smoke)
    typer.echo(f"wrote {len(written)} depth rasters to {depth_dir}")

    grid, _ = build_grid(cfg["_domain"], REPO)
    buf_cells = round(
        float(cfg["_domain"]["dem"]["buffer_m"]) / float(cfg["_domain"]["resolution_m"])
    )
    h_final = res.h.detach().cpu().numpy()
    if not smoke and h_final.shape != (grid.height, grid.width):
        h_canonical = h_final[
            buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width
        ]
    else:
        h_canonical = h_final
    max_depth_canonical = float(h_canonical.max())
    max_depth_buffered = float(h_final.max())

    fraction_exceeding = float(res.steps_exceeding_selected / res.steps) if res.steps > 0 else 0.0
    fraction_rejected = float(res.rejected_steps / res.steps) if res.steps > 0 else 0.0

    typer.echo(
        f"steps={res.steps:,}  sim={res.sim_time_s:,.0f}s  wall={res.wall_clock_s:,.1f}s  "
        f"max_courant={res.max_courant:.3f} (selected max={res.max_selected_courant:.3f}, "
        f"cfl_ceiling={ctrl.cfl_ceiling:.3f}, "
        f"rejections={res.rejected_steps}/{res.steps} = {100*fraction_rejected:.2f}%)"
    )
    typer.echo(
        f"depth (canonical): min={res.min_depth:.3e}  max={max_depth_canonical:.4f} m  "
        f"(buffered max: {max_depth_buffered:.4f} m)   "
        f"mass rel-residual={budget.relative_residual():.3e}"
    )

    result_manifest = {
        "stage": "phase2_solver_run",
        "status": "completed",
        "git_sha": git_sha(),
        "config_path": str(config),
        "device": device,
        "cells": n_cells,
        "steps": res.steps,
        "sim_time_s": res.sim_time_s,
        "wall_clock_s": res.wall_clock_s,
        "max_courant_realized": res.max_courant,
        "max_courant_selected": res.max_selected_courant,
        "cfl_ceiling": ctrl.cfl_ceiling,
        "retry_attempts": res.retry_attempts,
        "steps_with_rejections": res.rejected_steps,
        "rejected_steps": res.rejected_steps,
        "fraction_rejected_steps": fraction_rejected,
        "steps_exceeding_selected": res.steps_exceeding_selected,
        "fraction_steps_exceeding_selected": fraction_exceeding,
        "min_depth_m": res.min_depth,
        "max_depth_m": max_depth_canonical,
        "max_depth_buffered_m": max_depth_buffered,
        "wetdry_mode": p.wetdry_mode,
        "boundary_mode": p.boundary_mode,
        "datum_m": static.datum_m,
        "compute_budget": est,
        "mass": budget.as_dict(),
        "dt_schedule": {
            **{k: v for k, v in res.controller.compress().items() if k != "runs_rle"},
            "sidecar": write_dt_sidecar(res.controller.schedule, out / "dt_schedule.f32.gz"),
        },
        "depth_series": written,
    }
    manifest_path.write_text(json.dumps(result_manifest, indent=2, default=str))
    typer.echo(f"\nwrote {manifest_path}")


if __name__ == "__main__":
    app()
