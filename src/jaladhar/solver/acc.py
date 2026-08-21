"""The ACC (Bates et al. 2010 local-inertial) timestep — the differentiable core.

Phase 3 calibrates Manning's n, drain capacity and the building conveyance
factor by gradient descent THROUGH this function, so every operation here is
chosen for autograd safety as well as physics. That is a design constraint
from the first line, not a later retrofit: making a finished solver
differentiable means rewriting the wet/dry treatment, the flux limiter and
the loop, i.e. all of it.

DIFFERENCES-ONLY EVALUATION — WHY THIS LOOKS UNLIKE PROMPT.md §7
----------------------------------------------------------------
§7 writes the scheme in terms of the water surface `eta = h + z`:

    hf   = max(h_i + z_i, h_j + z_j) - max(z_i, z_j)
    grad = d(h + z)/dx

Forming `eta` at Bengaluru's absolute elevation destroys precision. Measured
on the actual buffered solver domain: raw elevation 725.162-969.494 m, so
datum-relative z' has MEDIAN 163.8 m, where float32 ULP is 1.53e-5 m. Every
surface gradient would carry that as noise.

Factoring z_i out of both expressions gives an algebraically IDENTICAL form
in which only elevation DIFFERENCES appear:

    dz   = z_j - z_i                          (static, precomputed in float64)
    hf   = max(h_i, h_j + dz) - max(0, dz)
    grad = ((h_j - h_i) + dz) / dx

Now every quantity is O(0.1 m) instead of O(164 m), and surface-gradient
round-off drops from ~1.5e-5 m to ~1.2e-8 m — about 1000x. This is a
numerically-stable rearrangement, NOT a physics change.

Consequence: ABSOLUTE ELEVATION APPEARS NOWHERE IN THIS MODULE. Only `dz`
enters. That makes PROMPT.md §7's datum requirement structurally satisfied
rather than merely tested, and the datum invariant (add 1000 m to every
elevation, expect bitwise-identical depths) then guards the structure — it
goes red the moment anyone reintroduces an absolute z.

(eta-as-state was evaluated and rejected on measured numbers: it makes
well-balancedness exactly zero, but at the median z' the per-step drain
increment of 6.21e-6 m is 0.41 ULP of eta and rounds away entirely — over
97.17% of the domain. It would buy exactness in a secondary property by
silently deleting the drain sink. See OPEN-ITEMS.md and the phase plan.)

THE TRAP THAT KILLS AUTOGRAD SOLVERS
------------------------------------
`torch.where(cond, safe_value, unsafe_value)` does NOT protect the backward
pass. Gradients flow through BOTH branches, so a NaN or inf produced in the
branch `where` discards still poisons `.backward()`. Every division here
therefore CLAMPS ITS DENOMINATOR FIRST, divides second, and selects third.
`hf` is floored at `p.hf_floor_m` before `hf ** (7/3)` is ever evaluated.

WET/DRY AS A FACE WEIGHT, AND WHY MASS IS EXACT UNDER BOTH MODES
----------------------------------------------------------------
Both gate modes are implemented identically — as a scalar weight per FACE
multiplying that face's flux:

    hard: w = (hf > threshold)                    the published Bates gate
    ramp: w = smoothstep over the threshold band  a deliberate deviation

This is not an implementation convenience. A face weight is inherently
symmetric: whatever leaves cell i through a face is exactly what enters cell
j through the same face, whatever the weight. So mass conservation is exact
under BOTH modes by construction. If the ramped residual were ever to exceed
the hard-gate residual, the ramp would have been applied per-cell somewhere
instead of per-face.

The ramp width is a FIXED CONFIG CONSTANT and is excluded from Phase 3's
parameter vector by design: widening the ramp smears the front, which raises
flood-extent CSI without improving the physics — the cheapest way for an
optimiser to game its own metric.

NON-NEGATIVE DEPTH IS PROVEN, NOT CLAMPED
-----------------------------------------
`torch.clamp(h, min=0)` silently CREATES water and is not used. Instead a
donor-cell limiter scales each cell's outgoing fluxes by
`r = min(1, h / total_outflux)`. Since every face has exactly one donor, the
limited outflux from cell c is `r_c * outflux_c <= h_c`, and influx is
non-negative, so `h_new >= h_c - h_c + 0 = 0` is guaranteed algebraically
rather than enforced afterwards. Boundary outflow participates in the same
limiter — otherwise an edge cell could over-drain through the outfall.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
import torch.nn.functional as F

# ---------------------------------------------------------------- parameters


@dataclass(frozen=True)
class SolverParams:
    """Scalars resolved from config ONCE, so the hot path does no dict lookups.

    Every value traces to `configs/solver.yaml`; nothing here has a default
    that could silently diverge from the config file (CLAUDE.md §Style).
    """

    dx: float
    gravity: float
    depth_threshold_m: float
    hf_floor_m: float
    wetdry_mode: str  # "hard" | "ramp"
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


# ------------------------------------------------------------------ helpers


def face_weight(hf: torch.Tensor, p: SolverParams) -> torch.Tensor:
    """Per-face wet/dry weight in [0, 1]. See the module docstring.

    Computed on TRUE hf (before any numerical floor).
    """
    if p.wetdry_mode == "hard":
        return (hf > p.depth_threshold_m).to(hf.dtype)
    elif p.wetdry_mode == "ramp":
        # Smoothstep across [depth_threshold_m, depth_threshold_m + ramp_width_m]
        lo = p.depth_threshold_m
        u = torch.clamp((hf - lo) / p.ramp_width_m, 0.0, 1.0)
        return u * u * (3.0 - 2.0 * u)
    raise ValueError(f"unknown wetdry mode {p.wetdry_mode}")


def _div_x(fx: torch.Tensor) -> torch.Tensor:
    """Out-of-place x-divergence. `fx` is (H, W-1); pad order is (l, r, t, b).

    Positive fx is flow from cell (i, j) to (i, j+1).
    """
    return F.pad(fx, (1, 0)) - F.pad(fx, (0, 1))


def _div_y(fy: torch.Tensor) -> torch.Tensor:
    """Out-of-place y-divergence. `fy` is (H-1, W); pad order is (l, r, t, b)."""
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
    """One direction's momentum update. Returns (q_new, hf).

    `h_lo`/`h_hi` are the depths on the low/high side of each face and `dz`
    is the STATIC bed difference `z_hi - z_lo` across it. Absolute elevation
    never appears — see the module docstring.
    """
    # Effective flow depth at the face, in differences-only form:
    #   hf = max(eta_i, eta_j) - max(z_i, z_j)  ==  max(h_lo, h_hi + dz) - max(0, dz)
    hf = torch.maximum(h_lo, h_hi + dz) - torch.clamp(dz, min=0.0)

    # The gate weight is computed from the TRUE hf, before any flooring, so
    # the physics decides wet/dry rather than the numerical guard.
    w = face_weight(hf, p)

    # CLAMP BEFORE DIVIDE. hf**(7/3) with hf -> 0 gives inf, and torch.where
    # would NOT protect the backward pass from it (gradients flow through the
    # discarded branch too). Flooring here makes both value and gradient
    # finite everywhere, unconditionally.
    hf_safe = torch.clamp(hf, min=p.hf_floor_m)

    # Water-surface gradient, also differences-only:
    #   d(eta)/dx = ((h_hi - h_lo) + dz) / dx
    grad = ((h_hi - h_lo) + dz) / p.dx

    num = q - p.gravity * hf_safe * dt * grad
    # den = 1 + non-negative, so den >= 1 always and can never vanish.
    den = 1.0 + p.gravity * dt * n_face * n_face * q.abs() / hf_safe.pow(7.0 / 3.0)

    # Multiply by the gate weight rather than torch.where-ing to zero: same
    # forward values, finite gradient everywhere.
    q_new = w * (num / den)
    return q_new, hf


# ------------------------------------------------------------------ step


def acc_step(
    h: torch.Tensor,
    qx: torch.Tensor,
    qy: torch.Tensor,
    static: Any,
    dt: float,
    p: SolverParams,
    rain_rate_m_s: float | torch.Tensor = 0.0,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]:
    """Advance one ACC timestep. Pure function; no tensor is mutated in place.

    `static` supplies the precomputed face fields (`dz_x`, `dz_y`, `n_x`,
    `n_y`, `c_x`, `c_y`), the per-cell `drain_cap_m_s` / `infil_rate_m_s`, and
    the boundary bed slopes. `dt` is a plain float taken from the RECORDED
    schedule, so it is a constant in the graph — no `.item()` is called here
    and no Python branch depends on a tensor value.

    Returns `(h, qx, qy, diagnostics)`. Diagnostics include float64 mass-budget
    reductions and the signed realized face depth increments in metres. The
    latter are post-conveyance and post-donor-limiter: they are the exact
    ``fx``/``fy`` fields used in the state update, unlike returned ``qx`` and
    ``qy`` which are momentum states before donor limiting. Diagnostics are
    computed inside a `torch.no_grad()` block and never feed the state, so they
    add nothing to the graph. (This is deliberately NOT the forbidden
    `.detach()`-in-the-hot-path pattern, which would sever the state's own
    gradient path.)
    """
    # -- 1. momentum, both directions ------------------------------------
    qx_new, hf_x = _momentum(qx, h[:, :-1], h[:, 1:], static.dz_x, static.n_x, dt, p)
    qy_new, hf_y = _momentum(qy, h[:-1, :], h[1:, :], static.dz_y, static.n_y, dt, p)

    # -- 2. conveyance ----------------------------------------------------
    fx = static.c_x * qx_new * (dt / p.dx)  # depth increment per face, metres
    fy = static.c_y * qy_new * (dt / p.dx)

    # -- 3. boundary outflow ---------------------------------------------
    b_out = _boundary_outflux(h, static, dt, p)  # (H, W), >= 0, depth units

    # -- 4. donor-cell flux limiter --------------------------------------
    out = (
        F.pad(torch.relu(fx), (0, 1))
        + F.pad(torch.relu(-fx), (1, 0))
        + F.pad(torch.relu(fy), (0, 0, 0, 1))
        + F.pad(torch.relu(-fy), (0, 0, 1, 0))
        + b_out
    )
    r = torch.clamp(h / torch.clamp(out, min=1e-12), max=1.0)

    # Each face is scaled by ITS DONOR's ratio, so the two cells sharing a
    # face always agree on how much crossed it — mass stays exact.
    fx = fx * torch.where(fx > 0, r[:, :-1], r[:, 1:])
    fy = fy * torch.where(fy > 0, r[:-1, :], r[1:, :])
    b_out = b_out * r

    # -- 5. mass update ---------------------------------------------------
    h_new = h + _div_x(fx) + _div_y(fy) - b_out
    if isinstance(rain_rate_m_s, torch.Tensor) or rain_rate_m_s != 0.0:
        h_new = h_new + rain_rate_m_s * dt

    # Non-negativity is algebraic (guaranteed by the donor-cell flux limiter).
    # If the limiter ever fails, do NOT quietly clamp before sinks — that turns
    # negative depth into negative drainage, silently creating water. Stop immediately.
    # We allow up to 4 ULP of float32 subtraction roundoff derived from the LOCAL
    # intermediate magnitude: tol_i = 4 * eps * max(h_old_i, sum(|flux_i|) * dt / A),
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
                f"Negative depth before sinks: {count} cell(s) went negative (min depth {min_val:.6e} m, "
                f"first violation at index {first_idx}: {float(h_new[first_idx]):.6e} m, tolerance {float(tol[first_idx]):.3e} m). "
                "Flux limiter failed to prevent over-drainage."
            )

    # -- 6. sinks, in the FIXED configured order: drain, then infiltration -
    drained = torch.minimum(static.drain_cap_m_s * dt, h_new)
    h_new = h_new - drained
    infiltrated = torch.minimum(static.infil_rate_m_s * dt, h_new)
    h_new = h_new - infiltrated

    with torch.no_grad():
        # This is the exact routed term used by the state update. Including
        # open-domain outfalls makes its negative integral over an arbitrary
        # mask equal all routed export, even when that mask touches the model
        # perimeter.
        transport_depth_change = _div_x(fx) + _div_y(fy) - b_out
        diagnostics = {
            # Diagnostic-only views. Detaching here cannot sever the state:
            # h_new was already formed from fx/fy above. It prevents callers
            # retaining the full autograd graph merely to log realized flux.
            "realized_face_depth_increment_x_m": fx.detach(),
            "realized_face_depth_increment_y_m": fy.detach(),
            # Cell-centred conservative transport increment. Summing this
            # over a mask is the negative signed flux through that mask's
            # boundary; rain and sinks are excluded, domain outfalls included.
            "transport_depth_change_m": transport_depth_change.detach(),
            "drained_depth_m": drained.detach(),
            "infiltrated_depth_m": infiltrated.detach(),
            "boundary_out_m": b_out.sum(dtype=torch.float64),
            "drained_m": drained.sum(dtype=torch.float64),
            "infiltrated_m": infiltrated.sum(dtype=torch.float64),
            # Expected to be EXACTLY zero: the limiter makes non-negativity
            # algebraic. A non-zero value is a finding, not a rounding detail.
            "negative_depth_cells": (h_new < 0).sum(),
            "max_hf": torch.maximum(hf_x.max(), hf_y.max()),
        }
        diagnostics["min_drained_m"] = float(drained.min())
        diagnostics["min_infiltrated_m"] = float(infiltrated.min())
    return h_new, qx_new, qy_new, diagnostics


def _boundary_outflux(h: torch.Tensor, static: Any, dt: float, p: SolverParams) -> torch.Tensor:
    """Depth leaving through the domain perimeter this step, as an (H, W) field.

    "closed" is reflective — an exactly-zero field. "free" is a normal-depth
    (uniform-flow) outfall, `q = (1/n) * h^(5/3) * sqrt(S)`, the LISFLOOD-FP
    FREE precedent, with `S` the local bed slope.

    Both are needed and neither is sufficient alone. "Boundary flux is
    outward or zero" passes trivially on a sealed boundary, so it cannot
    catch a sign error that seals the outfall — and that failure is severe
    and silent: water accumulates city-wide with nowhere to leave, mass
    conserves perfectly, and every depth is wrong. The companion check is
    that standing water at the boundary produces STRICTLY POSITIVE outflow,
    which is what this branch makes true.
    """
    if p.boundary_mode == "closed":
        return torch.zeros_like(h)

    height, width = h.shape

    def _edge_depth(h_edge: torch.Tensor, n_edge: torch.Tensor, s_edge: torch.Tensor):
        """Normal-depth outflow for one edge, as a depth increment."""
        # Gated by the same wet/dry threshold the interior faces use, so a dry
        # boundary leaks nothing.
        wet = (h_edge > p.depth_threshold_m).to(h.dtype)
        # sqrt(S) has infinite derivative at S = 0, so S is floored where it
        # is built (see state.py) — same clamp-before-the-nonlinearity
        # discipline as hf. h is floored for the same reason: h^(5/3) is fine
        # at 0 but its derivative is not.
        h_safe = torch.clamp(h_edge, min=p.hf_floor_m)
        return (h_safe.pow(5.0 / 3.0) * s_edge.sqrt() / n_edge) * wet * (dt / p.dx)

    # Built out-of-place with F.pad. An earlier draft of this function wrote
    # `contrib[idx] = ...`, which is precisely the in-place-on-a-tensor
    # pattern this module's docstring forbids — it would have silently
    # compromised the gradient path through the one boundary term Phase 3
    # needs to see.
    ow, oe, on, os = static.edge_open
    west = (_edge_depth(h[:, 0], static.edge_w_n, static.edge_w_s) * ow).unsqueeze(1)
    east = (_edge_depth(h[:, -1], static.edge_e_n, static.edge_e_s) * oe).unsqueeze(1)
    north = (_edge_depth(h[0, :], static.edge_n_n, static.edge_n_s) * on).unsqueeze(0)
    south = (_edge_depth(h[-1, :], static.edge_s_n, static.edge_s_s) * os).unsqueeze(0)

    # Corner cells legitimately belong to two edges and drain through both.
    return (
        F.pad(west, (0, width - 1))
        + F.pad(east, (width - 1, 0))
        + F.pad(north, (0, 0, 0, height - 1))
        + F.pad(south, (0, 0, height - 1, 0))
    )
