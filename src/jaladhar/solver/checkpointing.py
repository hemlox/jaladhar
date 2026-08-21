"""Single-level segmented checkpointing for the differentiable ACC solver.

WHY THIS EXISTS: THE 124-STEP VRAM WALL
---------------------------------------
Standard reverse-mode automatic differentiation retains all forward activations
and intermediate tensors for backpropagation. On a 512x512 tile (262,144 cells),
measured memory consumption per step is:
    I = 46.4521 MiB per step (retained intermediates)
    S = 3.0000 MiB (state: h, qx, qy)
    B = 5,754.16 MiB (available VRAM budget on RTX 4060 Laptop GPU)

Without checkpointing, maximum differentiable step count is strictly capped at:
    N_linear_cap = B / I = 123.9 steps (~124 steps)

A 6-hour urban storm simulation requires ~9,665 steps. Differentiable calibration
in Phase 3 is impossible without activation checkpointing.

DESIGN RELATIONS (SINGLE-LEVEL SEGMENTED CHECKPOINTING)
-------------------------------------------------------
Split N steps into L segments of length K = N / L steps.
During forward pass, retain only the state (h, qx, qy) at segment boundaries (L * S).
During backward pass, recompute intermediate activations for one segment at a time (K * I).

Peak memory during backpropagation:
    VRAM(K) = L * S + K * I = (N / K) * S + K * I

Minimizing VRAM(K) with respect to segment length K yields:
    d(VRAM)/dK = -N * S / K^2 + I = 0
    => optimal segment length K* = sqrt(N * S / I)
    => optimal segment count L* = sqrt(N * I / S)
    => predicted peak VRAM = 2 * sqrt(N * S * I)
    => maximum reachable steps N_max = B^2 / (4 * S * I) = 59,401 steps

At N = 9,665 on a 512^2 tile:
    K* = sqrt(9665 * 3.0 / 46.4521) = 24.98 ~ 25 steps per segment
    L* = 9665 / 25 = 386.6 ~ 387 segments
    Predicted peak VRAM = 2 * sqrt(9665 * 3.0 * 46.4521) = 2,321.4 MiB

COMPOSITION WITH RECORD-THEN-REPLAY
-----------------------------------
Checkpointing operates on the fixed dt schedule recorded during the forward pass.
The schedule and time progression across segment boundaries are deterministic and
identical to standard replay, ensuring exact mathematical equivalence of forward
outputs and gradient calculations down to numerical precision.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import torch
import torch.utils.checkpoint
import typer

from jaladhar.solver.acc import SolverParams, acc_step
from jaladhar.solver.state import StaticFields, initial_state

app = typer.Typer(add_completion=False)

# Measured memory parameters on RTX 4060 Laptop (512x512 tile, 262,144 cells)
# from runs/solver_memory/manifest.json
MEASURED_I_MIB_PER_STEP: float = 46.4521484375
MEASURED_S_MIB_STATE: float = 3.0
MEASURED_BUDGET_MIB: float = 5754.15625


def optimal_segment_length(
    n_steps: int,
    s_mib: float = MEASURED_S_MIB_STATE,
    i_mib: float = MEASURED_I_MIB_PER_STEP,
) -> int:
    """Compute theoretical optimal segment length K* = sqrt(N * S / I)."""
    if n_steps <= 0:
        return 1
    k_opt = math.sqrt(n_steps * s_mib / i_mib)
    return max(1, round(k_opt))


def predicted_peak_vram_mib(
    n_steps: int,
    s_mib: float = MEASURED_S_MIB_STATE,
    i_mib: float = MEASURED_I_MIB_PER_STEP,
) -> float:
    """Compute predicted peak VRAM in MiB: 2 * sqrt(N * S * I)."""
    if n_steps <= 0:
        return s_mib
    return 2.0 * math.sqrt(n_steps * s_mib * i_mib)


def checkpointed_replay(
    static: StaticFields,
    p: SolverParams,
    schedule: list[float],
    *,
    segment_size: int | None = None,
    rain: Callable[[float], float] | None = None,
    h0: torch.Tensor | None = None,
    qx0: torch.Tensor | None = None,
    qy0: torch.Tensor | None = None,
    device: str | torch.device = "cpu",
    use_reentrant: bool = False,
) -> torch.Tensor:
    """Differentiable pass over a FIXED recorded schedule using segmented checkpointing.

    Splits `schedule` into segments of length `segment_size` (defaulting to K* = sqrt(N*S/I)).
    Retains only segment-boundary states on forward pass and recomputes segment interiors
    during backward pass via `torch.utils.checkpoint.checkpoint`.

    Parameters:
        static: Static fields containing spatial parameters (n_x, c_x, etc.).
        p: Solver parameters.
        schedule: Fixed dt values recorded during forward integration.
        segment_size: Steps per checkpoint segment (None => auto-computed K*).
        rain: Callable returning rain intensity in m/s at time t.
        h0, qx0, qy0: Initial state tensors (defaults to dry bed at rest).
        device: Target compute device.
        use_reentrant: PyTorch checkpoint mode (False recommended for torch >= 2.0).

    Returns:
        Final depth tensor h with autograd graph intact for calibration.
    """
    n_steps = len(schedule)
    if n_steps == 0:
        if h0 is not None:
            return h0
        h_init, _, _ = initial_state(static, device=device)
        return h_init

    k_step = segment_size if segment_size is not None else optimal_segment_length(n_steps)
    k_step = max(1, k_step)

    h, qx, qy = initial_state(static, device=device)
    if h0 is not None:
        h = h0
    if qx0 is not None:
        qx = qx0
    if qy0 is not None:
        qy = qy0

    # Build segment slices and their starting simulation times
    segments: list[tuple[list[float], float]] = []
    t_curr = 0.0
    for idx in range(0, n_steps, k_step):
        dt_chunk = schedule[idx : idx + k_step]
        segments.append((dt_chunk, t_curr))
        t_curr += sum(dt_chunk)

    for dt_chunk, t_start in segments:
        def _segment_step(
            h_in: torch.Tensor,
            qx_in: torch.Tensor,
            qy_in: torch.Tensor,
            _dts: list[float] = dt_chunk,
            _t0: float = t_start,
        ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
            h_local, qx_local, qy_local = h_in, qx_in, qy_in
            t_local = _t0
            for dt in _dts:
                r = rain(t_local) if rain is not None else 0.0
                h_local, qx_local, qy_local, _ = acc_step(
                    h_local, qx_local, qy_local, static, dt, p, rain_rate_m_s=r
                )
                t_local += dt
            return h_local, qx_local, qy_local

        h, qx, qy = torch.utils.checkpoint.checkpoint(
            _segment_step,
            h,
            qx,
            qy,
            use_reentrant=use_reentrant,
        )

    return h


@app.command()
def main(
    steps: int = typer.Option(100, help="Number of steps to benchmark"),
    tile_size: int = typer.Option(512, help="Spatial tile dimension (e.g. 512)"),
    segment_size: int = typer.Option(0, help="Segment length (0 for auto K*)"),
) -> None:
    """Benchmark segmented checkpointing differentiable replay VRAM and execution time."""
    from jaladhar.solver.run import uniform_storm
    from jaladhar.solver.state import build_static_fields

    device = "cuda" if torch.cuda.is_available() else "cpu"
    typer.echo(f"Benchmarking checkpointed replay on {device} (tile {tile_size}x{tile_size}, {steps} steps)...")

    z = np.full((tile_size, tile_size), 800.0)
    static = build_static_fields(
        z,
        np.full((tile_size, tile_size), 0.03),
        np.ones((tile_size, tile_size)),
        np.zeros((tile_size, tile_size)),
        dx=10.0,
        infil_mm_hr=0.0,
        min_conveyance_factor=0.05,
        min_bed_slope=1e-4,
        device=device,
    )
    p = SolverParams(
        dx=10.0,
        gravity=9.81,
        depth_threshold_m=0.001,
        hf_floor_m=0.001,
        wetdry_mode="hard",
        ramp_width_m=0.001,
        min_conveyance_factor=0.05,
        boundary_mode="free",
        min_bed_slope=1e-4,
    )

    static.n_x.requires_grad_(True)
    schedule = [1.0] * steps
    k_seg = segment_size if segment_size > 0 else optimal_segment_length(steps)
    pred_vram = predicted_peak_vram_mib(steps)

    if device == "cuda":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    h_out = checkpointed_replay(
        static,
        p,
        schedule,
        segment_size=k_seg,
        rain=uniform_storm(110.0, 7200.0),
        device=device,
    )
    loss = h_out.sum()
    loss.backward()

    typer.echo(f"Backward completed. Gradient norm: {float(static.n_x.grad.norm()):.4e}")
    if device == "cuda":
        peak_mib = torch.cuda.max_memory_allocated() / (1024 * 1024)
        typer.echo(f"Peak VRAM: {peak_mib:.2f} MiB (Predicted: {pred_vram:.2f} MiB, K*={k_seg})")


if __name__ == "__main__":
    app()
