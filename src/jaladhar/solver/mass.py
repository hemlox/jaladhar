"""Mass budget — a RUNTIME diagnostic, not a test-only check.

    residual = V(t) - V(0) - rain_in + drain_out + infil_out + boundary_out
               - mass_created_by_clamping

All accumulators are float64 scalars computed under `no_grad`, so the residual
measures SCHEME error rather than the error of summing 12M float32 cells 10^4
times. The scheme is telescoping-exact in exact arithmetic — every flux is
added to one cell and subtracted from its neighbour — so what remains is pure
float32 round-off.

`mass_created_by_clamping` is tracked separately and is EXPECTED TO BE EXACTLY
ZERO: non-negativity comes from the donor-cell limiter algebraically, not from
clamping afterwards. A non-zero value is a finding, not a rounding detail, and
folding it into the residual would hide exactly the failure it exists to catch.

ON BREACH: HALT AND DUMP. Do not continue and report at the end — a broken run
must not be able to look like a valid one. This is deliberately asymmetric with
`timestep.py`'s stale-schedule handling, which re-records and retries: a stale
dt is recoverable numerical control, a mass breach means the scheme is broken.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import torch


class MassBreach(Exception):
    """The mass budget exceeded tolerance mid-run. Per CLAUDE.md rule 1, stop."""


@dataclass
class MassBudget:
    """float64 accumulators over a run. Cell area converts depth to volume."""

    cell_area_m2: float
    relative_tolerance: float
    tolerance_is_measured: bool = False

    v_initial: float = 0.0
    v_current: float = 0.0
    rain_in: float = 0.0
    drain_out: float = 0.0
    infil_out: float = 0.0
    boundary_out: float = 0.0
    created_by_clamping: float = 0.0
    min_cell_drained_m: float = 0.0
    min_cell_infiltrated_m: float = 0.0
    history: list[dict[str, float]] = field(default_factory=list)

    def volume(self, h: torch.Tensor) -> float:
        with torch.no_grad():
            return float(h.sum(dtype=torch.float64)) * self.cell_area_m2

    def start(self, h: torch.Tensor) -> None:
        self.v_initial = self.volume(h)
        self.v_current = self.v_initial

    def accumulate(
        self,
        h: torch.Tensor,
        diagnostics: dict[str, torch.Tensor | float],
        rain_rate_m_s: float | torch.Tensor,
        dt: float,
        n_cells: int,
    ) -> None:
        with torch.no_grad():
            step_drained_m = float(diagnostics["drained_m"])
            step_infil_m = float(diagnostics["infiltrated_m"])
            min_step_drained = float(diagnostics["min_drained_m"])
            min_step_infil = float(diagnostics["min_infiltrated_m"])

            # Dynamic 4-ULP machine epsilon tolerance derived from the depth scale
            h_max = float(torch.clamp(h.max(), min=1e-4))
            tol = 4.0 * torch.finfo(torch.float32).eps * h_max

            # PERMANENT RUNTIME CHECKS: sinks must NEVER be negative (no implicit water creation)
            if step_drained_m < -tol * n_cells:
                raise RuntimeError(
                    f"Negative total drainage this step: {step_drained_m:.6e} m (tol {-tol * n_cells:.3e} m)"
                )
            if step_infil_m < -tol * n_cells:
                raise RuntimeError(
                    f"Negative total infiltration this step: {step_infil_m:.6e} m (tol {-tol * n_cells:.3e} m)"
                )
            if min_step_drained < -tol:
                raise RuntimeError(
                    f"Negative cell drainage detected: {min_step_drained:.6e} m (tol {-tol:.3e} m)"
                )
            if min_step_infil < -tol:
                raise RuntimeError(
                    f"Negative cell infiltration detected: {min_step_infil:.6e} m (tol {-tol:.3e} m)"
                )

            self.v_current = self.volume(h)
            if isinstance(rain_rate_m_s, torch.Tensor):
                self.rain_in += float(rain_rate_m_s.sum(dtype=torch.float64)) * dt * self.cell_area_m2
            else:
                self.rain_in += rain_rate_m_s * dt * n_cells * self.cell_area_m2
            self.drain_out += step_drained_m * self.cell_area_m2
            self.infil_out += step_infil_m * self.cell_area_m2
            self.boundary_out += float(diagnostics["boundary_out_m"]) * self.cell_area_m2
            self.min_cell_drained_m = min(self.min_cell_drained_m, min_step_drained)
            self.min_cell_infiltrated_m = min(self.min_cell_infiltrated_m, min_step_infil)

            if self.drain_out < -tol * n_cells * self.cell_area_m2:
                raise RuntimeError(
                    f"Cumulative drainage negative: {self.drain_out:.6e} m^3 (tol {-tol * n_cells * self.cell_area_m2:.3e} m^3)"
                )
            if self.infil_out < -tol * n_cells * self.cell_area_m2:
                raise RuntimeError(
                    f"Cumulative infiltration negative: {self.infil_out:.6e} m^3 (tol {-tol * n_cells * self.cell_area_m2:.3e} m^3)"
                )

    def residual(self) -> float:
        return (
            self.v_current
            - self.v_initial
            - self.rain_in
            + self.drain_out
            + self.infil_out
            + self.boundary_out
            - self.created_by_clamping
        )

    def relative_residual(self) -> float:
        scale = max(abs(self.rain_in), abs(self.v_initial), 1e-12)
        return abs(self.residual()) / scale

    def check(self, step: int, dump_dir: Path | None = None) -> float:
        rel = self.relative_residual()
        self.history.append({"step": float(step), "relative_residual": rel})
        if rel > self.relative_tolerance:
            if dump_dir is not None:
                dump_dir.mkdir(parents=True, exist_ok=True)
                (dump_dir / "mass_breach.json").write_text(
                    json.dumps({"step": step, **self.as_dict()}, indent=2)
                )
            raise MassBreach(
                f"mass residual {rel:.3e} exceeded tolerance "
                f"{self.relative_tolerance:.3e} at step {step}. "
                f"Per-term budget: {self.as_dict()}"
            )
        return rel

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "v_initial_m3": self.v_initial,
            "v_current_m3": self.v_current,
            "rain_in_m3": self.rain_in,
            "drain_out_m3": self.drain_out,
            "infil_out_m3": self.infil_out,
            "boundary_out_m3": self.boundary_out,
            "min_cell_drained_m": self.min_cell_drained_m,
            "min_cell_infiltrated_m": self.min_cell_infiltrated_m,
            # Expected EXACTLY zero — see the module docstring.
            "created_by_clamping_m3": self.created_by_clamping,
            "residual_m3": self.residual(),
            "relative_residual": self.relative_residual(),
            "relative_tolerance": self.relative_tolerance,
            # Carried so a reader can never mistake a placeholder for a
            # measured threshold (CLAUDE.md V1).
            "tolerance_is_measured": self.tolerance_is_measured,
        }
