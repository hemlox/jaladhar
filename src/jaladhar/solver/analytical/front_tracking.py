"""Wetting and drying front tracking and sensitivity analysis.

Evaluates front propagation speed, mass conservation, gating mode ('hard' vs 'ramp'),
and numerical depth threshold sensitivity against Ritter's closed-form wet-front trajectory:
    x_front(t) = 2 * t * sqrt(g * h0)
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
import typer

from jaladhar.solver.acc import SolverParams
from jaladhar.solver.analytical.dambreak import RitterCase
from jaladhar.solver.run import simulate
from jaladhar.solver.state import load_solver_config
from jaladhar.solver.timestep import TimestepController

REPO = Path(__file__).resolve().parents[4]
CONFIG = REPO / "configs" / "solver.yaml"

app = typer.Typer(add_completion=False)


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def _cfg(**overrides) -> dict[str, Any]:
    cfg = load_solver_config(CONFIG, REPO)
    for dotted, value in overrides.items():
        section, key = dotted.split(".", 1)
        cfg[section][key] = value
    return cfg


def _params(dx: float, **overrides) -> SolverParams:
    cfg = _cfg(**overrides)
    return SolverParams.from_config(cfg, dx)


@dataclass
class FrontTrackingRow:
    dx_m: float
    time_s: float
    x_front_exact_m: float
    x_front_num_m: float
    front_pos_err_m: float
    rel_mass_residual: float
    steps: int


@dataclass
class ThresholdSensitivityRow:
    threshold_m: float
    mode: str
    steps: int
    x_front_num_m: float
    front_pos_err_m: float
    rel_mass_residual: float
    l1_error_m: float


def run_front_resolution_study(
    times: list[float] | None = None,
    resolutions: list[float] | None = None,
    length_m: float = 200.0,
    h0: float = 1.0,
) -> list[FrontTrackingRow]:
    if times is None:
        times = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    if resolutions is None:
        resolutions = [10.0, 5.0, 2.5]

    ritter = RitterCase(h0=h0)
    c0 = ritter.c0
    rows: list[FrontTrackingRow] = []

    for dx in resolutions:
        static, x, h0_tensor = ritter.make_domain(length_m=length_m, dx=dx, device="cpu")
        p = _params(
            dx,
            **{
                "boundaries.mode": "closed",
                "wetdry.mode": "hard",
                "timestep.cfl_alpha": 0.5,
                "timestep.max_dt_s": 10.0,
            },
        )
        ctrl = TimestepController(dx=dx, alpha=0.5, max_dt_s=10.0)

        for t in times:
            res = simulate(static, p, ctrl, duration_s=t, h0=h0_tensor)
            h = res.h[1, :].numpy()
            wet = h > 0.001
            x_num = float(x[wet].max()) if np.any(wet) else 0.0
            x_exact = 2.0 * c0 * t
            err = x_num - x_exact

            v0 = float(h0_tensor[1, :].sum() * dx)
            vt = float(res.h[1, :].sum() * dx)
            rel_mass = (vt - v0) / v0

            rows.append(
                FrontTrackingRow(
                    dx_m=dx,
                    time_s=t,
                    x_front_exact_m=x_exact,
                    x_front_num_m=x_num,
                    front_pos_err_m=err,
                    rel_mass_residual=rel_mass,
                    steps=res.steps,
                )
            )

    return rows


def run_threshold_sensitivity_study(
    thresholds: list[float] | None = None,
    modes: list[str] | None = None,
    dx: float = 2.5,
    length_m: float = 200.0,
    t_eval: float = 4.0,
    h0: float = 1.0,
) -> list[ThresholdSensitivityRow]:
    if thresholds is None:
        thresholds = [0.0001, 0.001, 0.01]
    if modes is None:
        modes = ["hard", "ramp"]

    ritter = RitterCase(h0=h0)
    c0 = ritter.c0
    x_exact = 2.0 * c0 * t_eval
    rows: list[ThresholdSensitivityRow] = []

    for thresh in thresholds:
        for mode in modes:
            static, x, h0_tensor = ritter.make_domain(length_m=length_m, dx=dx, device="cpu")
            p = _params(
                dx,
                **{
                    "boundaries.mode": "closed",
                    "wetdry.mode": mode,
                    "physics.depth_threshold_m": thresh,
                    "physics.hf_floor_m": thresh,
                    "timestep.cfl_alpha": 0.5,
                    "timestep.max_dt_s": 10.0,
                },
            )
            ctrl = TimestepController(dx=dx, alpha=0.5, max_dt_s=10.0)
            res = simulate(static, p, ctrl, duration_s=t_eval, h0=h0_tensor)
            h = res.h[1, :].numpy()
            wet = h > thresh
            x_num = float(x[wet].max()) if np.any(wet) else 0.0
            err = x_num - x_exact

            v0 = float(h0_tensor[1, :].sum() * dx)
            vt = float(res.h[1, :].sum() * dx)
            rel_mass = (vt - v0) / v0

            h_exact = ritter.exact_h(x, t_eval)
            l1 = float(np.mean(np.abs(h - h_exact)))

            rows.append(
                ThresholdSensitivityRow(
                    threshold_m=thresh,
                    mode=mode,
                    steps=res.steps,
                    x_front_num_m=x_num,
                    front_pos_err_m=err,
                    rel_mass_residual=rel_mass,
                    l1_error_m=l1,
                )
            )

    return rows


@app.command()
def main(
    out: Path = typer.Option(REPO / "runs" / "analytical_ladder", help="Output directory"),
) -> None:
    """Run wet/dry front tracking and sensitivity benchmarks."""
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest_front_tracking.json"
    start_time = time.time()

    manifest: dict[str, Any] = {
        "stage": "wet_dry_front_tracking_and_sensitivity",
        "kind": "measurement",
        "status": "running",
        "git_sha": git_sha(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    typer.echo("================================================================================")
    typer.echo("=== WETTING/DRYING FRONT PROPAGATION & SENSITIVITY STUDY ===")
    typer.echo("================================================================================\n")

    res_rows = run_front_resolution_study()
    typer.echo("--- 1. Front Position Error & Mass Conservation Over Time ---")
    typer.echo(
        f"{'dx (m)':<8} | {'Time (s)':<8} | {'x_exact (m)':<12} | {'x_num (m)':<10} | {'Err (m)':<10} | {'Rel Mass Res':<14} | {'Steps':<6}"
    )
    typer.echo("-" * 80)
    for r in res_rows:
        typer.echo(
            f"{r.dx_m:<8.1f} | {r.time_s:<8.1f} | {r.x_front_exact_m:<12.2f} | "
            f"{r.x_front_num_m:<10.2f} | {r.front_pos_err_m:<+10.2f} | {r.rel_mass_residual:<+14.2e} | {r.steps:<6d}"
        )

    sens_rows = run_threshold_sensitivity_study()
    typer.echo("\n--- 2. Depth Threshold & Gating Mode Sensitivity (dx=2.5m, t=4.0s) ---")
    typer.echo(
        f"{'Threshold (m)':<14} | {'Mode':<6} | {'Steps':<6} | {'x_num (m)':<10} | {'Err (m)':<10} | {'Rel Mass Res':<14} | {'L1 Err (m)':<10}"
    )
    typer.echo("-" * 85)
    for s in sens_rows:
        typer.echo(
            f"{s.threshold_m:<14.4f} | {s.mode:<6s} | {s.steps:<6d} | "
            f"{s.x_front_num_m:<10.2f} | {s.front_pos_err_m:<+10.2f} | {s.rel_mass_residual:<+14.2e} | {s.l1_error_m:<10.6f}"
        )

    elapsed_s = time.time() - start_time
    manifest.update(
        {
            "status": "completed",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_wall_s": elapsed_s,
            "resolution_study": [asdict(r) for r in res_rows],
            "threshold_sensitivity": [asdict(s) for s in sens_rows],
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2))
    typer.echo(f"\nWrote manifest to {manifest_path}")


if __name__ == "__main__":
    app()
