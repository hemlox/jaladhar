"""The ACC (Bates et al. 2010 local-inertial) timestep — the differentiable core.
Phase 3 calibrates Manning's n, drain capacity and the building conveyance
chosen for autograd safety as well as physics. That is a design constraint
Forming `eta` at Bengaluru's absolute elevation destroys precision. Measured
datum-relative z' has MEDIAN 163.8 m, where float32 ULP is 1.53e-5 m. Every
numerically-stable rearrangement, NOT a physics change.
enters. That makes PROMPT.md §7's datum requirement structurally satisfied
rather than merely tested, and the datum invariant (add 1000 m to every
(eta-as-state was evaluated and rejected on measured numbers: it makes
WET/DRY AS A FACE WEIGHT, AND WHY MASS IS EXACT UNDER BOTH MODES
hard: w = (hf > threshold)                    the published Bates gate
j through the same face, whatever the weight. So mass conservation is exact
flood-extent CSI without improving the physics — the cheapest way for an
rather than enforced afterwards. Boundary outflow participates in the same"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
import torch.nn.functional as F


@dataclass(frozen=True)
class SolverParams:
    """Every value traces to `configs/solver.yaml`; nothing here has a default"""

    dx: float
    gravity: float
    depth_threshold_m: float
    hf_floor_m: float
    wetdry_mode: str
    ramp_width_m: float
    min_conveyance_factor: float
    boundary_mode: str
    min_bed_slope: float

    @staticmethod
    def from_config(cfg: dict[str, Any], dx: float) -> SolverParams:
        phys, wd, bld, bnd = (
            cfg["physics"],
            cfg["wetdry"],
            cfg["buildings"],
            cfg["boundaries"],
        )
        mode = wd["mode"]
        if mode not in ("hard", "ramp"):
            raise ValueError(f"wetdry.mode={mode!r} not implemented (hard|ramp)")
        bmode = bnd["mode"]
        if bmode not in ("free", "closed"):
            raise ValueError(f"boundaries.mode={bmode!r} not implemented (free|closed)")
        if not 0.0 < phys["hf_floor_m"] <= phys["depth_threshold_m"]:
            raise ValueError(
                f"physics.hf_floor_m={phys['hf_floor_m']} must be in "
                f"(0, depth_threshold_m={phys['depth_threshold_m']}] — it is the guard "
                "that keeps hf**(7/3) away from zero before the division."
            )
        return SolverParams(
            dx=dx,
            gravity=float(phys["gravity_m_s2"]),
            depth_threshold_m=float(phys["depth_threshold_m"]),
            hf_floor_m=float(phys["hf_floor_m"]),
            wetdry_mode=mode,
            ramp_width_m=float(wd["ramp_width_m"]),
            min_conveyance_factor=float(bld["min_conveyance_factor"]),
            boundary_mode=bmode,
            min_bed_slope=float(bnd["min_bed_slope"]),
        )


def face_weight(hf: torch.Tensor, p: SolverParams) -> torch.Tensor:
    if p.wetdry_mode == "hard":
        return (hf > p.depth_threshold_m).to(hf.dtype)
    elif p.wetdry_mode == "ramp":
        lo = p.depth_threshold_m
        u = torch.clamp((hf - lo) / p.ramp_width_m, 0.0, 1.0)
        return u * u * (3.0 - 2.0 * u)
    raise ValueError(f"unknown wetdry mode {p.wetdry_mode}")


def _div_x(fx: torch.Tensor) -> torch.Tensor:
    return F.pad(fx, (1, 0)) - F.pad(fx, (0, 1))


def _div_y(fy: torch.Tensor) -> torch.Tensor:
    return F.pad(fy, (0, 0, 1, 0)) - F.pad(fy, (0, 0, 0, 1))


def _momentum(
    q: torch.Tensor,
    h_lo: torch.Tensor,
    h_hi: torch.Tensor,
    dz: torch.Tensor,
    n_face: torch.Tensor,
    dt: float,
    p: SolverParams,
) -> tuple[torch.Tensor, torch.Tensor]:
    hf = torch.maximum(h_lo, h_hi + dz) - torch.clamp(dz, min=0.0)

    # the physics decides wet/dry rather than the numerical guard.
    w = face_weight(hf, p)

    hf_safe = torch.clamp(hf, min=p.hf_floor_m)

    grad = ((h_hi - h_lo) + dz) / p.dx

    num = q - p.gravity * hf_safe * dt * grad
    den = 1.0 + p.gravity * dt * n_face * n_face * q.abs() / hf_safe.pow(7.0 / 3.0)

    q_new = w * (num / den)
    return q_new, hf


def acc_step(
    h: torch.Tensor,
    qx: torch.Tensor,
    qy: torch.Tensor,
    static: Any,
    dt: float,
    p: SolverParams,
    rain_rate_m_s: float | torch.Tensor = 0.0,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]:
    """the boundary bed slopes. `dt` is a plain float taken from the RECORDED
    Returns `(h, qx, qy, diagnostics)`. Diagnostics include float64 mass-budget
    reductions and the signed realized face depth increments in metres. The"""
    qx_new, hf_x = _momentum(qx, h[:, :-1], h[:, 1:], static.dz_x, static.n_x, dt, p)
    qy_new, hf_y = _momentum(qy, h[:-1, :], h[1:, :], static.dz_y, static.n_y, dt, p)

    fx = static.c_x * qx_new * (dt / p.dx)
    fy = static.c_y * qy_new * (dt / p.dx)

    # -- 3. boundary outflow ---------------------------------------------
    b_out = _boundary_outflux(h, static, dt, p)  # (H, W), >= 0, depth units

    out = (
        F.pad(torch.relu(fx), (0, 1))
        + F.pad(torch.relu(-fx), (1, 0))
        + F.pad(torch.relu(fy), (0, 0, 0, 1))
        + F.pad(torch.relu(-fy), (0, 0, 1, 0))
        + b_out
    )
    r = torch.clamp(h / torch.clamp(out, min=1e-12), max=1.0)

    # face always agree on how much crossed it — mass stays exact.
    fx = fx * torch.where(fx > 0, r[:, :-1], r[:, 1:])
    fy = fy * torch.where(fy > 0, r[:-1, :], r[1:, :])
    b_out = b_out * r

    # -- 5. mass update ---------------------------------------------------
    h_new = h + _div_x(fx) + _div_y(fy) - b_out
    if isinstance(rain_rate_m_s, torch.Tensor) or rain_rate_m_s != 0.0:
        h_new = h_new + rain_rate_m_s * dt

    # negative depth into negative drainage, silently creating water. Stop immediately.
    # We allow up to 4 ULP of float32 subtraction roundoff derived from the LOCAL
    # so physical breaches (> 4 local ULP) halt immediately while IEEE 754 precision is respected.
    with torch.no_grad():
        flux_mag = (
            F.pad(fx.abs(), (1, 0))
            + F.pad(fx.abs(), (0, 1))
            + F.pad(fy.abs(), (0, 0, 1, 0))
            + F.pad(fy.abs(), (0, 0, 0, 1))
            + b_out
        )
        if isinstance(rain_rate_m_s, torch.Tensor) or rain_rate_m_s != 0.0:
            flux_mag = flux_mag + rain_rate_m_s * dt
        local_scale = torch.maximum(h, flux_mag)
        tol = 4.0 * torch.finfo(h_new.dtype).eps * local_scale
        if (h_new < -tol).any():
            neg_mask = h_new < -tol
            neg_indices = torch.nonzero(neg_mask)
            first_idx = tuple(neg_indices[0].tolist())
            min_val = float(h_new[neg_mask].min())
            count = int(neg_mask.sum())
            raise RuntimeError(
                f"Negative depth before sinks: {count} cell(s) went negative (min depth {min_val:.6e} m, "  # noqa: E501
                f"first violation at index {first_idx}: {float(h_new[first_idx]):.6e} m, tolerance {float(tol[first_idx]):.3e} m). "  # noqa: E501
                "Flux limiter failed to prevent over-drainage."
            )

    drained = torch.minimum(static.drain_cap_m_s * dt, h_new)
    h_new = h_new - drained
    infiltrated = torch.minimum(static.infil_rate_m_s * dt, h_new)
    h_new = h_new - infiltrated

    with torch.no_grad():
        transport_depth_change = _div_x(fx) + _div_y(fy) - b_out
        diagnostics = {
            # retaining the full autograd graph merely to log realized flux.
            "realized_face_depth_increment_x_m": fx.detach(),
            "realized_face_depth_increment_y_m": fy.detach(),
            # boundary; rain and sinks are excluded, domain outfalls included.
            "transport_depth_change_m": transport_depth_change.detach(),
            "drained_depth_m": drained.detach(),
            "infiltrated_depth_m": infiltrated.detach(),
            "boundary_out_m": b_out.sum(dtype=torch.float64),
            "drained_m": drained.sum(dtype=torch.float64),
            "infiltrated_m": infiltrated.sum(dtype=torch.float64),
            "negative_depth_cells": (h_new < 0).sum(),
            "max_hf": torch.maximum(hf_x.max(), hf_y.max()),
        }
        diagnostics["min_drained_m"] = float(drained.min())
        diagnostics["min_infiltrated_m"] = float(infiltrated.min())
    return h_new, qx_new, qy_new, diagnostics


def _boundary_outflux(h: torch.Tensor, static: Any, dt: float, p: SolverParams) -> torch.Tensor:
    """(uniform-flow) outfall, `q = (1/n) * h^(5/3) * sqrt(S)`, the LISFLOOD-FP
    Both are needed and neither is sufficient alone. "Boundary flux is
    outward or zero" passes trivially on a sealed boundary, so it cannot"""
    if p.boundary_mode == "closed":
        return torch.zeros_like(h)

    height, width = h.shape

    def _edge_depth(h_edge: torch.Tensor, n_edge: torch.Tensor, s_edge: torch.Tensor):
        # boundary leaks nothing.
        wet = (h_edge > p.depth_threshold_m).to(h.dtype)
        h_safe = torch.clamp(h_edge, min=p.hf_floor_m)
        return (h_safe.pow(5.0 / 3.0) * s_edge.sqrt() / n_edge) * wet * (dt / p.dx)

    # compromised the gradient path through the one boundary term Phase 3
    ow, oe, on, os = static.edge_open
    west = (_edge_depth(h[:, 0], static.edge_w_n, static.edge_w_s) * ow).unsqueeze(1)
    east = (_edge_depth(h[:, -1], static.edge_e_n, static.edge_e_s) * oe).unsqueeze(1)
    north = (_edge_depth(h[0, :], static.edge_n_n, static.edge_n_s) * on).unsqueeze(0)
    south = (_edge_depth(h[-1, :], static.edge_s_n, static.edge_s_s) * os).unsqueeze(0)

    return (
        F.pad(west, (0, width - 1))
        + F.pad(east, (width - 1, 0))
        + F.pad(north, (0, 0, 0, height - 1))
        + F.pad(south, (0, 0, height - 1, 0))
    )
