"Phase 2, Milestone 1 — MEASURE the autograd memory cost per timestep. The phase plan's central quantitative claim is that a differentiable full-domain run is infeasible on 8 GB and Phase 3 must therefore calibrate on tiles. That claim rests on `I`, the graph intermediates retained per timestep, via the single-level checkpointing optimum peak = 2 * sqrt(N * S * I) => N_max = B^2 / (4 * S * I) At planning time `I` was an ESTIMATE, arrived at by counting tensors in a stencil that had not been written yet. Per CLAUDE.md V1 that is a DECLARATION, not realized state, and the plan committed to replacing it with a measurement before any figure derived from it is quoted. This script is that measurement. METHOD, and why it is a regression rather than a single reading -------------------------------------------------------------- Peak allocation for `n` grad-enabled steps is `fixed + n * I`, where `fixed` covers the static fields, the state, and allocator overhead. A single reading cannot separate the two. Running a sweep over `n` and taking the SLOPE isolates `I` and reports the intercept separately, so neither is inferred from the other. The independent observable (V2): if `I` were being mismeasured — say the graph were silently not being retained — the slope would be flat, and the sweep would show it directly rather than returning a plausible single number. Terrain is a real crop of `data/processed/`, never synthesised (CLAUDE.md rule 1). Only tensor SHAPES affect the quantity being measured, but using real data costs nothing and keeps the rule intact."  # noqa: E501

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import torch
import typer

from jaladhar.solver.acc import SolverParams, acc_step
from jaladhar.solver.state import initial_state, load_domain, load_solver_config

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[1]
MIB = 1024.0**2


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def measure_peak(static, p: SolverParams, n_steps: int, device: str, rain: float) -> float:
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats(device)

    h, qx, qy = initial_state(static, device=device)
    # Calibrating Manning's n is the real Phase 3 scenario, so the graph is rooted where it will
    # actually be rooted.
    static.n_x.requires_grad_(True)
    static.n_y.requires_grad_(True)

    for _ in range(n_steps):
        h, qx, qy, _ = acc_step(h, qx, qy, static, dt=1.0, p=p, rain_rate_m_s=rain)

    loss = h.sum()
    loss.backward()

    peak = torch.cuda.max_memory_allocated(device) / MIB
    static.n_x.requires_grad_(False)
    static.n_y.requires_grad_(False)
    del h, qx, qy, loss
    if static.n_x.grad is not None:
        static.n_x.grad = None
        static.n_y.grad = None
    torch.cuda.empty_cache()
    return peak


@app.command()
def main(
    config: Path = typer.Option(REPO / "configs" / "solver.yaml"),
    tile: int = typer.Option(512, help="Square tile edge, cells"),
    steps: str = typer.Option("2,4,8,16,24", help="Comma-separated step counts to sweep"),
    out: Path = typer.Option(REPO / "runs" / "solver_memory"),
) -> None:
    if not torch.cuda.is_available():
        typer.echo("FATAL: no CUDA device — this measurement is meaningless on CPU.")
        raise typer.Exit(code=1)
    device = "cuda"

    cfg = load_solver_config(config, REPO)
    dx = float(cfg["_domain"]["resolution_m"])
    p = SolverParams.from_config(cfg, dx)

    # Real terrain, cropped. Offset into the interior so the tile is genuinely urban rather than
    # mostly outside the BBMP polygon.
    win = (slice(1200, 1200 + tile), slice(1400, 1400 + tile))
    static = load_domain(cfg, REPO, window=win, device=device)
    rain = float(cfg["storm"]["total_mm"]) / 1000.0 / float(cfg["storm"]["duration_s"])

    typer.echo(f"device: {torch.cuda.get_device_name(0)}")
    typer.echo(
        f"tile: {static.shape[0]}x{static.shape[1]} = {static.shape[0]*static.shape[1]:,} cells"
    )
    typer.echo(f"wetdry.mode={p.wetdry_mode}  boundaries.mode={p.boundary_mode}\n")

    ns = [int(s) for s in steps.split(",")]
    measure_peak(static, p, 2, device, rain)  # warm up the allocator

    rows = []
    for n in ns:
        peak = measure_peak(static, p, n, device, rain)
        rows.append({"steps": n, "peak_mib": peak})
        typer.echo(f"  {n:>3} steps   peak {peak:>9.2f} MiB")

    # Least-squares slope: peak = fixed + I * n
    n_arr = [float(r["steps"]) for r in rows]
    y_arr = [r["peak_mib"] for r in rows]
    n_mean = sum(n_arr) / len(n_arr)
    y_mean = sum(y_arr) / len(y_arr)
    denom = sum((x - n_mean) ** 2 for x in n_arr)
    slope = sum((x - n_mean) * (y - y_mean) for x, y in zip(n_arr, y_arr, strict=True)) / denom
    intercept = y_mean - slope * n_mean

    cells = static.shape[0] * static.shape[1]
    field_mib = cells * 4 / MIB
    i_per_step = slope
    s_state = 3.0 * field_mib  # h, qx, qy carried across a checkpoint boundary

    typer.echo(
        f"\nI  (graph per step) = {i_per_step:8.3f} MiB  = {i_per_step/field_mib:5.2f} fields"
    )
    typer.echo(f"fixed overhead      = {intercept:8.3f} MiB")
    typer.echo(f"S  (state)          = {s_state:8.3f} MiB")

    # Republish N_max for both domains from the MEASURED per-cell figures.
    vram_total = torch.cuda.get_device_properties(0).total_memory / MIB
    budget = vram_total * 0.85 - 989.0  # compute.yaml margin, measured fwd set
    per_cell_i = i_per_step / cells
    per_cell_s = s_state / cells

    typer.echo(f"\nbudget B = {budget:,.0f} MiB   (0.85 x {vram_total:,.0f} - 989 fwd)")
    typer.echo(f"{'domain':>16}  {'cells':>12}  {'S MiB':>9}  {'I MiB':>10}  {'N_max':>10}")
    projections = []
    for name, c in (("512^2 tile", cells), ("full domain", 12_024_815)):
        s_d, i_d = per_cell_s * c, per_cell_i * c
        n_max = budget**2 / (4.0 * s_d * i_d)
        projections.append({"domain": name, "cells": c, "S_mib": s_d, "I_mib": i_d, "n_max": n_max})
        typer.echo(f"{name:>16}  {c:>12,}  {s_d:>9.2f}  {i_d:>10.2f}  {n_max:>10,.0f}")

    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "stage": "phase2_milestone1_autograd_memory",
        "kind": "measurement",
        "git_sha": git_sha(),
        "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "tile_cells": cells,
        "wetdry_mode": p.wetdry_mode,
        "boundary_mode": p.boundary_mode,
        "sweep": rows,
        "I_mib_per_step_measured": i_per_step,
        "I_fields_per_step": i_per_step / field_mib,
        "fixed_overhead_mib": intercept,
        "S_state_mib": s_state,
        "budget_mib": budget,
        "projections": projections,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
