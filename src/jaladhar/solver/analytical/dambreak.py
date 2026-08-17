"""Closed-form analytical solutions for shallow water validation ladder.

This module provides exact closed-form benchmark solutions to validate the ACC
(local-inertial, Bates et al. 2010) solver:
  1. Ritter (1892) dry-bed dam break (frictionless, advection-dominated, Fr >= 1)
  2. Stoker (1957) wet-bed dam break (frictionless, shock-forming, Fr ~ 1.2)
  3. Thacker (1981) oscillating flow in a parabolic bowl (frictionless, low-Froude, Fr ~ 0.03)
  4. MacDonald (1997) steady channel flow with Manning friction (frictional, subcritical, Fr ~ 0.3)

CRITICAL PHYSICS GUARD (CLAUDE.md V1-V8):
The ACC formulation drops the nonlinear advection term (u * du/dx) to achieve
computational efficiency and unconditional gradient stability.
  - Cases 1 & 2 (Ritter, Stoker) are advection-dominated and high-Froude.
    The solver is EXPECTED to disagree with the exact SWE solutions near the front.
    These cases characterise the degradation envelope as a function of Froude number.
  - Cases 3 & 4 (Thacker, MacDonald) operate at low Froude (Fr << 0.5). In Thacker,
    spatial velocity gradient du/dx is identically zero, making it an exact
    solution for both SWE and ACC. The solver is EXPECTED TO MATCH Thacker and
    MacDonald to high precision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import scipy.integrate as integ
import scipy.optimize as opt
import torch

from jaladhar.solver.state import StaticFields, build_static_fields

GRAVITY = 9.81


@dataclass(frozen=True)
class RitterCase:
    """Ritter (1892) frictionless dry-bed dam break.

    Reservoir depth h0 for x <= 0, dry bed (h=0) for x > 0.
    Rarefaction wave expands between x = -t*sqrt(g*h0) and x = 2*t*sqrt(g*h0).
    """

    h0: float = 1.0
    g: float = GRAVITY

    @property
    def c0(self) -> float:
        return math.sqrt(self.g * self.h0)

    def exact_h(self, x: np.ndarray, t: float) -> np.ndarray:
        """Exact depth profile h(x, t)."""
        x_arr = np.asarray(x, dtype=np.float64)
        if t <= 0.0:
            return np.where(x_arr <= 0.0, self.h0, 0.0)
        c0 = self.c0
        g = self.g
        h = np.zeros_like(x_arr)
        mask_left = x_arr <= -c0 * t
        mask_fan = (x_arr > -c0 * t) & (x_arr < 2.0 * c0 * t)
        h[mask_left] = self.h0
        h[mask_fan] = (1.0 / (9.0 * g)) * (2.0 * c0 - x_arr[mask_fan] / t) ** 2
        return h

    def exact_u(self, x: np.ndarray, t: float) -> np.ndarray:
        """Exact flow velocity profile u(x, t)."""
        x_arr = np.asarray(x, dtype=np.float64)
        if t <= 0.0:
            return np.zeros_like(x_arr)
        c0 = self.c0
        u = np.zeros_like(x_arr)
        mask_fan = (x_arr > -c0 * t) & (x_arr < 2.0 * c0 * t)
        u[mask_fan] = (2.0 / 3.0) * (x_arr[mask_fan] / t + c0)
        return u

    def exact_q(self, x: np.ndarray, t: float) -> np.ndarray:
        """Exact unit-width discharge q(x, t) = h * u."""
        return self.exact_h(x, t) * self.exact_u(x, t)

    def froude(self, x: np.ndarray, t: float) -> np.ndarray:
        """Froude number Fr(x, t) = u / sqrt(g*h)."""
        h = self.exact_h(x, t)
        u = self.exact_u(x, t)
        fr = np.zeros_like(h)
        wet = h > 1e-4
        fr[wet] = u[wet] / np.sqrt(self.g * h[wet])
        return fr

    def make_domain(
        self,
        length_m: float,
        dx: float,
        width_cells: int = 3,
        device: str = "cpu",
    ) -> tuple[StaticFields, np.ndarray, torch.Tensor]:
        """Create static fields, 1D x coordinates, and initial depth tensor."""
        nx = int(round(length_m / dx))
        ny = max(width_cells, 3)
        x = (np.arange(nx) + 0.5) * dx - (length_m / 2.0)
        z = np.zeros((ny, nx), dtype=np.float64)
        static = build_static_fields(
            z,
            np.zeros((ny, nx), dtype=np.float64),  # frictionless n = 0
            np.ones((ny, nx), dtype=np.float64),
            np.zeros((ny, nx), dtype=np.float64),
            dx=dx,
            infil_mm_hr=0.0,
            min_conveyance_factor=0.05,
            min_bed_slope=1e-6,
            edge_open=(0.0, 0.0, 0.0, 0.0),  # closed reflective walls
            device=device,
        )
        h0_1d = np.where(x <= 0.0, self.h0, 0.0).astype(np.float32)
        h0 = torch.tensor(np.repeat(h0_1d[None, :], ny, axis=0), device=device)
        return static, x, h0


@dataclass(frozen=True)
class StokerCase:
    """Stoker (1957) frictionless wet-bed dam break (1D Riemann problem).

    Upstream depth h0 for x <= 0, downstream depth h1 > 0 for x > 0.
    Produces an upstream rarefaction wave, an intermediate plateau (hm, um),
    and a downstream bore (shock) propagating at speed S.
    """

    h0: float = 1.0
    h1: float = 0.1
    g: float = GRAVITY

    def __post_init__(self):
        if not (self.h0 > self.h1 > 0.0):
            raise ValueError(f"Stoker requires h0 > h1 > 0 (got h0={self.h0}, h1={self.h1})")

    @property
    def c0(self) -> float:
        return math.sqrt(self.g * self.h0)

    @property
    def c1(self) -> float:
        return math.sqrt(self.g * self.h1)

    @property
    def hm(self) -> float:
        """Intermediate plateau depth solving the Rankine-Hugoniot match."""

        def f(h_test: float) -> float:
            c_test = math.sqrt(self.g * h_test)
            u_fan = 2.0 * (self.c0 - c_test)
            u_shock = (h_test - self.h1) * math.sqrt(
                0.5 * self.g * (h_test + self.h1) / (h_test * self.h1)
            )
            return u_fan - u_shock

        res = opt.root_scalar(f, bracket=[self.h1, self.h0], method="brentq")
        return float(res.root)

    @property
    def cm(self) -> float:
        return math.sqrt(self.g * self.hm)

    @property
    def um(self) -> float:
        return 2.0 * (self.c0 - self.cm)

    @property
    def shock_speed(self) -> float:
        return float((self.um * self.hm) / (self.hm - self.h1))

    def exact_h(self, x: np.ndarray, t: float) -> np.ndarray:
        x_arr = np.asarray(x, dtype=np.float64)
        if t <= 0.0:
            return np.where(x_arr <= 0.0, self.h0, self.h1)
        h = np.full_like(x_arr, self.h1)
        xA = -self.c0 * t
        xB = (self.um - self.cm) * t
        xS = self.shock_speed * t

        h[x_arr <= xA] = self.h0
        fan = (x_arr > xA) & (x_arr < xB)
        h[fan] = (1.0 / (9.0 * self.g)) * (2.0 * self.c0 - x_arr[fan] / t) ** 2
        plat = (x_arr >= xB) & (x_arr < xS)
        h[plat] = self.hm
        return h

    def exact_u(self, x: np.ndarray, t: float) -> np.ndarray:
        x_arr = np.asarray(x, dtype=np.float64)
        if t <= 0.0:
            return np.zeros_like(x_arr)
        u = np.zeros_like(x_arr)
        xA = -self.c0 * t
        xB = (self.um - self.cm) * t
        xS = self.shock_speed * t

        fan = (x_arr > xA) & (x_arr < xB)
        u[fan] = (2.0 / 3.0) * (x_arr[fan] / t + self.c0)
        plat = (x_arr >= xB) & (x_arr < xS)
        u[plat] = self.um
        return u

    def exact_q(self, x: np.ndarray, t: float) -> np.ndarray:
        return self.exact_h(x, t) * self.exact_u(x, t)

    def froude(self, x: np.ndarray, t: float) -> np.ndarray:
        h = self.exact_h(x, t)
        u = self.exact_u(x, t)
        fr = np.zeros_like(h)
        wet = h > 1e-4
        fr[wet] = u[wet] / np.sqrt(self.g * h[wet])
        return fr

    def make_domain(
        self,
        length_m: float,
        dx: float,
        width_cells: int = 3,
        device: str = "cpu",
    ) -> tuple[StaticFields, np.ndarray, torch.Tensor]:
        nx = int(round(length_m / dx))
        ny = max(width_cells, 3)
        x = (np.arange(nx) + 0.5) * dx - (length_m / 2.0)
        z = np.zeros((ny, nx), dtype=np.float64)
        static = build_static_fields(
            z,
            np.zeros((ny, nx), dtype=np.float64),
            np.ones((ny, nx), dtype=np.float64),
            np.zeros((ny, nx), dtype=np.float64),
            dx=dx,
            infil_mm_hr=0.0,
            min_conveyance_factor=0.05,
            min_bed_slope=1e-6,
            edge_open=(0.0, 0.0, 0.0, 0.0),
            device=device,
        )
        h0_1d = np.where(x <= 0.0, self.h0, self.h1).astype(np.float32)
        h0 = torch.tensor(np.repeat(h0_1d[None, :], ny, axis=0), device=device)
        return static, x, h0


@dataclass(frozen=True)
class ThackerCase:
    """Thacker (1981) exact planar oscillation in a parabolic bowl.

    Bed topography: z(x) = h0 * (x / a)^2
    Water surface oscillates as an inclined plane with frequency omega = sqrt(2*g*h0) / a.
    Velocity u(x, t) = -eta0 * omega * sin(omega * t) is SPATIALLY UNIFORM,
    so du/dx = 0 identically -> nonlinear advection term u*du/dx is IDENTICALLY ZERO.
    This makes Thacker an exact solution for BOTH full SWE and ACC local-inertial schemes.
    """

    a: float = 1000.0  # bowl half-width (m)
    h0: float = 1.0  # central water depth at rest (m)
    eta0: float = 20.0  # oscillation amplitude (m)
    g: float = GRAVITY

    @property
    def omega(self) -> float:
        return math.sqrt(2.0 * self.g * self.h0) / self.a

    @property
    def period_s(self) -> float:
        return 2.0 * math.pi / self.omega

    @property
    def max_froude(self) -> float:
        return float(math.sqrt(2.0) * self.eta0 / self.a)

    def exact_z(self, x: np.ndarray) -> np.ndarray:
        x_arr = np.asarray(x, dtype=np.float64)
        return self.h0 * (x_arr / self.a) ** 2

    def exact_h(self, x: np.ndarray, t: float) -> np.ndarray:
        x_arr = np.asarray(x, dtype=np.float64)
        cos_wt = math.cos(self.omega * t)
        val = self.h0 * (1.0 - (1.0 / (self.a**2)) * (x_arr - self.eta0 * cos_wt) ** 2)
        return np.maximum(0.0, val)

    def exact_u(self, x: np.ndarray, t: float) -> np.ndarray:
        x_arr = np.asarray(x, dtype=np.float64)
        h = self.exact_h(x_arr, t)
        u = np.zeros_like(x_arr)
        wet = h > 0.0
        u[wet] = -self.eta0 * self.omega * math.sin(self.omega * t)
        return u

    def exact_surface(self, x: np.ndarray, t: float) -> np.ndarray:
        return self.exact_z(x) + self.exact_h(x, t)

    def make_domain(
        self,
        length_m: float,
        dx: float,
        width_cells: int = 3,
        device: str = "cpu",
    ) -> tuple[StaticFields, np.ndarray, torch.Tensor]:
        nx = int(round(length_m / dx))
        ny = max(width_cells, 3)
        x = (np.arange(nx) + 0.5) * dx - (length_m / 2.0)
        z_1d = self.exact_z(x)
        z = np.repeat(z_1d[None, :], ny, axis=0)
        static = build_static_fields(
            z,
            np.zeros((ny, nx), dtype=np.float64),
            np.ones((ny, nx), dtype=np.float64),
            np.zeros((ny, nx), dtype=np.float64),
            dx=dx,
            infil_mm_hr=0.0,
            min_conveyance_factor=0.05,
            min_bed_slope=1e-6,
            edge_open=(0.0, 0.0, 0.0, 0.0),
            device=device,
        )
        h0_1d = self.exact_h(x, 0.0).astype(np.float32)
        h0 = torch.tensor(np.repeat(h0_1d[None, :], ny, axis=0), device=device)
        return static, x, h0


@dataclass(frozen=True)
class MacDonaldCase:
    """MacDonald (1997) steady 1D subcritical channel flow with Manning friction.

    Prescribes a smooth subcritical depth profile h(x) with unit discharge q0.
    The bed elevation z(x) is derived to balance friction and pressure gradient:
      dz/dx = (Fr^2 - 1) * dh/dx - Sf, where Sf = (n*q0)^2 / h^(10/3).
    """

    length_m: float = 1000.0
    q0: float = 1.0
    manning_n: float = 0.03
    h_mid: float = 1.0
    delta_h: float = 0.1
    g: float = GRAVITY

    def exact_h(self, x: np.ndarray) -> np.ndarray:
        x_arr = np.asarray(x, dtype=np.float64)
        return self.h_mid + self.delta_h * np.sin(2.0 * np.pi * x_arr / self.length_m)

    def exact_dh_dx(self, x: np.ndarray) -> np.ndarray:
        x_arr = np.asarray(x, dtype=np.float64)
        return (
            self.delta_h
            * (2.0 * np.pi / self.length_m)
            * np.cos(2.0 * np.pi * x_arr / self.length_m)
        )

    def bed_slope(self, x: np.ndarray) -> np.ndarray:
        h = self.exact_h(x)
        dh_dx = self.exact_dh_dx(x)
        fr2 = (self.q0**2) / (self.g * (h**3))
        sf = ((self.manning_n * self.q0) ** 2) / (h ** (10.0 / 3.0))
        return (fr2 - 1.0) * dh_dx - sf

    def exact_z(self, x: np.ndarray) -> np.ndarray:
        x_arr = np.asarray(x, dtype=np.float64)
        dz_dx = self.bed_slope(x_arr)
        z_raw = integ.cumulative_trapezoid(dz_dx, x_arr, initial=0.0)
        return z_raw - z_raw[-1]  # datum at outlet x=L

    def froude(self, x: np.ndarray) -> np.ndarray:
        h = self.exact_h(x)
        return self.q0 / (h * np.sqrt(self.g * h))

    def make_domain(
        self,
        dx: float,
        width_cells: int = 3,
        device: str = "cpu",
    ) -> tuple[StaticFields, np.ndarray, torch.Tensor]:
        nx = int(round(self.length_m / dx))
        ny = max(width_cells, 3)
        x = (np.arange(nx) + 0.5) * dx
        z_1d = self.exact_z(x)
        z = np.repeat(z_1d[None, :], ny, axis=0)
        static = build_static_fields(
            z,
            np.full((ny, nx), self.manning_n, dtype=np.float64),
            np.ones((ny, nx), dtype=np.float64),
            np.zeros((ny, nx), dtype=np.float64),
            dx=dx,
            infil_mm_hr=0.0,
            min_conveyance_factor=0.05,
            min_bed_slope=1e-4,
            edge_open=(0.0, 1.0, 0.0, 0.0),  # west closed, east open outfall
            device=device,
        )
        h0_1d = self.exact_h(x).astype(np.float32)
        h0 = torch.tensor(np.repeat(h0_1d[None, :], ny, axis=0), device=device)
        return static, x, h0
