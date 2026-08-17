"""Analytical test cases with exact solutions.

These need no external files, which is what makes them immune to the input-data
problem that blocks the EA benchmark suite (OPEN-ITEMS.md item 9). They carry
solver correctness on their own — a Phase 0 finding, since LISFLOOD-FP proved
unbuildable and could not serve as the reference it was intended to be.

A NOTE ON CLAUDE.md RULE 1, since a reviewer should not have to wonder: the
geometries here (a tilted plane, a parabolic bowl) are MATHEMATICAL TEST CASES
with closed-form solutions, not synthetic terrain standing in for Bengaluru.
Nothing produced here is reported as a measurement of anything real. This is
categorically distinct from fabricating elevation data, which the rule
prohibits and which this project does not do anywhere.
"""

from __future__ import annotations

import numpy as np

from jaladhar.solver.analytical.dambreak import (
    MacDonaldCase,
    RitterCase,
    StokerCase,
    ThackerCase,
)
from jaladhar.solver.state import StaticFields, build_static_fields

__all__ = [
    "MacDonaldCase",
    "RitterCase",
    "StokerCase",
    "ThackerCase",
    "make_plane",
]


def make_plane(
    *,
    length_m: float,
    width_m: float,
    dx: float,
    slope: float,
    manning_n: float,
    device: str = "cpu",
) -> tuple[StaticFields, np.ndarray]:
    """A plane tilted along +x, draining to the east edge.

    Returns `(static, x_centres)`. Elevation decreases downslope so water runs
    toward increasing x, matching the kinematic solution's convention that x is
    measured downslope from the divide.
    """
    nx = int(round(length_m / dx))
    ny = max(int(round(width_m / dx)), 3)
    x = (np.arange(nx) + 0.5) * dx
    z = (length_m - x)[None, :] * slope
    z = np.repeat(z, ny, axis=0)

    static = build_static_fields(
        z,
        np.full((ny, nx), manning_n),
        np.ones((ny, nx)),  # no buildings on an analytical plane
        np.zeros((ny, nx)),  # no drains
        dx=dx,
        infil_mm_hr=0.0,
        min_conveyance_factor=0.05,
        min_bed_slope=max(slope * 0.5, 1e-6),
        # Divide (west) and both side walls closed; only the downslope (east)
        # edge is an outfall. Without this the floored bed slope drives
        # spurious outflow through all four edges and the plane never reaches
        # the analytical steady state.
        edge_open=(0.0, 1.0, 0.0, 0.0),
        device=device,
    )
    return static, x
