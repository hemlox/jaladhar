"""Static fields for the ACC solver: everything that does not change per step.

The solver's hot path (`acc.py`) consumes only DIFFERENCES of elevation, never
elevation itself. This module is where those differences are built, and it is
the only place absolute elevation is ever touched.

WHY ELEVATION ARITHMETIC HAPPENS IN float64 HERE
------------------------------------------------
PROMPT.md §7 requires datum-relative elevation, and the invariant that proves
it is: add 1000 m to every elevation, expect BITWISE-IDENTICAL depths.

That test only passes if the face differences are computed in float64 and cast
down afterwards. Bengaluru sits at ~725-970 m; in float32, `z + 1000` lands near
1900 m where ULP is 2.3e-4 m, so the shift would corrupt `z` itself and the
differences would come out genuinely different — the test would fail for a
reason that has nothing to do with the solver being wrong.

In float64 the promotion is exact (a float32 value has 24 mantissa bits; 1900.0
in float64 has 53), so `(z_j + 1000) - (z_i + 1000) == z_j - z_i` exactly, and
the float32 cast of that difference is bitwise identical either way. The datum
then cancels out of the solver entirely, which is what makes §7's requirement
STRUCTURAL rather than merely tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import torch

MM_PER_HR_TO_M_PER_S = 1.0 / 1000.0 / 3600.0


@dataclass(frozen=True)
class StaticFields:
    """Per-face and per-cell fields the timestep loop reads but never writes."""

    dz_x: torch.Tensor  # (H, W-1)  z_j - z_i across each x face, float32
    dz_y: torch.Tensor  # (H-1, W)
    n_x: torch.Tensor  # (H, W-1)  Manning's n at the face
    n_y: torch.Tensor  # (H-1, W)
    c_x: torch.Tensor  # (H, W-1)  building conveyance factor at the face
    c_y: torch.Tensor  # (H-1, W)
    drain_cap_m_s: torch.Tensor  # (H, W)
    infil_rate_m_s: torch.Tensor  # (H, W)
    edge_w_n: torch.Tensor  # (H,)  Manning's n along each domain edge
    edge_e_n: torch.Tensor
    edge_n_n: torch.Tensor  # (W,)
    edge_s_n: torch.Tensor
    edge_w_s: torch.Tensor  # (H,)  outward bed slope at each edge, floored
    edge_e_s: torch.Tensor
    edge_n_s: torch.Tensor  # (W,)
    edge_s_s: torch.Tensor
    # Per-edge openness (W, E, N, S), 1.0 = free outfall, 0.0 = closed.
    # Needed because a free outfall driven by a FLOORED bed slope manufactures
    # outflow even where the bed is genuinely flat — which silently drains a
    # domain through walls that should hold water (found via the kinematic
    # overland-flow case, where the upslope divide and the flat side walls were
    # all leaking).
    edge_open: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    datum_m: float = 0.0  # domain minimum elevation, recorded for the manifest
    shape: tuple[int, int] = (0, 0)

    def parameters(self) -> dict[str, torch.Tensor]:
        """The fields Phase 3 calibrates. Named here so the parameter vector has
        exactly one definition — and so invariant 25 can assert that
        `wetdry.ramp_width_m` is NOT among them."""
        return {
            "n_x": self.n_x,
            "n_y": self.n_y,
            "c_x": self.c_x,
            "c_y": self.c_y,
            "drain_cap_m_s": self.drain_cap_m_s,
        }


def _harmonic(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """Harmonic mean — conveyances in SERIES along the flow path.

    Water crossing a face traverses half of each cell, so the two conveyances
    act in series. Also smooth, where `min` has a subgradient kink: physics and
    differentiability agree. Both inputs are already floored > 0, so the
    denominator cannot vanish.
    """
    return 2.0 * a * b / (a + b)


def build_static_fields(
    z: np.ndarray,
    manning_n: np.ndarray,
    conveyance: np.ndarray,
    drain_cap_mm_hr: np.ndarray,
    *,
    dx: float,
    infil_mm_hr: float,
    min_conveyance_factor: float,
    min_bed_slope: float,
    face_combination: str = "harmonic",
    edge_open: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    device: str | torch.device = "cpu",
) -> StaticFields:
    """Assemble the static fields from raw arrays.

    `z` is absolute elevation and is consumed here ONLY as differences — it is
    promoted to float64 first (see the module docstring) and never reaches the
    solver.
    """
    z64 = np.asarray(z, dtype=np.float64)
    datum = float(np.nanmin(z64))

    def t32(a: np.ndarray) -> torch.Tensor:
        return torch.as_tensor(np.ascontiguousarray(a), dtype=torch.float32, device=device)

    # --- face bed differences, float64 then cast (the datum-invariance path)
    dz_x = t32(z64[:, 1:] - z64[:, :-1])
    dz_y = t32(z64[1:, :] - z64[:-1, :])

    # --- Manning's n at faces: arithmetic mean of the two cells
    n = np.asarray(manning_n, dtype=np.float64)
    n_x = t32(0.5 * (n[:, :-1] + n[:, 1:]))
    n_y = t32(0.5 * (n[:-1, :] + n[1:, :]))

    # --- conveyance: floor the CELL values first, so the face value inherits
    # the floor (a harmonic mean of two values >= f is itself >= f) and no face
    # can ever fully seal.
    c = np.clip(np.asarray(conveyance, dtype=np.float64), min_conveyance_factor, 1.0)
    c_lo_x, c_hi_x = torch.as_tensor(c[:, :-1]), torch.as_tensor(c[:, 1:])
    c_lo_y, c_hi_y = torch.as_tensor(c[:-1, :]), torch.as_tensor(c[1:, :])
    if face_combination == "harmonic":
        c_x, c_y = _harmonic(c_lo_x, c_hi_x), _harmonic(c_lo_y, c_hi_y)
    elif face_combination == "min":
        c_x, c_y = torch.minimum(c_lo_x, c_hi_x), torch.minimum(c_lo_y, c_hi_y)
    else:
        raise ValueError(f"buildings.face_combination={face_combination!r} (harmonic|min)")
    c_x = c_x.to(torch.float32).to(device)
    c_y = c_y.to(torch.float32).to(device)

    drain = t32(np.asarray(drain_cap_mm_hr, dtype=np.float64) * MM_PER_HR_TO_M_PER_S)
    infil = torch.full_like(drain, float(infil_mm_hr) * MM_PER_HR_TO_M_PER_S)

    # --- boundary bed slopes, one-sided, floored before they ever reach sqrt()
    def slope(a: np.ndarray, b: np.ndarray) -> torch.Tensor:
        return t32(np.maximum(np.abs(a - b) / dx, min_bed_slope))

    return StaticFields(
        dz_x=dz_x,
        dz_y=dz_y,
        n_x=n_x,
        n_y=n_y,
        c_x=c_x,
        c_y=c_y,
        drain_cap_m_s=drain,
        infil_rate_m_s=infil,
        edge_w_n=t32(n[:, 0]),
        edge_e_n=t32(n[:, -1]),
        edge_n_n=t32(n[0, :]),
        edge_s_n=t32(n[-1, :]),
        edge_w_s=slope(z64[:, 1], z64[:, 0]),
        edge_e_s=slope(z64[:, -1], z64[:, -2]),
        edge_n_s=slope(z64[1, :], z64[0, :]),
        edge_s_s=slope(z64[-1, :], z64[-2, :]),
        edge_open=edge_open,
        datum_m=datum,
        shape=(int(z64.shape[0]), int(z64.shape[1])),
    )


def load_solver_config(path: Path, repo_root: Path) -> dict[str, Any]:
    """Load `configs/solver.yaml` and attach its referenced domain config.

    The solver config deliberately does not duplicate CRS, resolution, buffer
    or terrain paths — it names the domain config instead, so the grid has
    exactly one definition (the same single-source-of-truth reason
    `terrain/grid.py` exists).
    """
    import yaml

    with open(path) as f:
        cfg = yaml.safe_load(f)
    with open(repo_root / cfg["domain_config"]) as f:
        cfg["_domain"] = yaml.safe_load(f)
    return cfg


def _read(path: Path) -> np.ndarray:
    with rasterio.open(path) as ds:
        return ds.read(1)


def load_domain(
    cfg: dict[str, Any],
    repo_root: Path,
    *,
    window: tuple[slice, slice] | None = None,
    device: str | torch.device = "cpu",
    use_buffered: bool = True,
) -> StaticFields:
    """Build static fields from Phase 1's `data/processed/` stack.

    When `use_buffered=True` (the default), reads from `data/processed/buffered/`
    so the ACC shallow-water solver solves on the full buffered domain (3521x3615),
    isolating domain-edge boundary outfalls from BBMP reporting cells.

    `window` crops to a sub-domain (used for differentiable tiles and for the
    VRAM measurement). Cropping REAL terrain rather than synthesising a test
    domain keeps CLAUDE.md rule 1 intact — no invented elevation, ever.
    """
    processed = repo_root / "data" / "processed"
    if use_buffered and (processed / "buffered").exists():
        processed = processed / "buffered"

    z = _read(processed / "elevation.tif")
    n = _read(processed / "manning_n.tif")
    c = _read(processed / "building_conveyance_factor.tif")
    d = _read(processed / "drain_capacity.tif")
    if window is not None:
        z, n, c, d = z[window], n[window], c[window], d[window]

    domain = cfg["_domain"]

    # Assert connectivity consistency across phase boundary (Invariant F / CLAUDE.md V8)
    terrain_manifest_path = repo_root / "runs" / "terrain_conditioning" / "manifest.json"
    if terrain_manifest_path.exists():
        import json

        manifest = json.loads(terrain_manifest_path.read_text())
        manifest_conn = manifest.get("connectivity", "D4")
        if manifest_conn != "D4":
            raise ValueError(
                f"Terrain connectivity mismatch: manifest declares {manifest_conn!r}, "
                "but ACC shallow-water solver uses D4 5-point cardinal stencil."
            )

    return build_static_fields(
        z,
        n,
        c,
        d,
        dx=float(domain["resolution_m"]),
        infil_mm_hr=(
            float(cfg["sinks"]["infiltration"]["uniform_rate_mm_per_hr"])
            if cfg["sinks"]["infiltration"]["enabled"]
            else 0.0
        ),
        min_conveyance_factor=float(cfg["buildings"]["min_conveyance_factor"]),
        min_bed_slope=float(cfg["boundaries"]["min_bed_slope"]),
        face_combination=cfg["buildings"]["face_combination"],
        device=device,
    )


def initial_state(
    static: StaticFields, device: str | torch.device = "cpu"
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """A dry domain at rest. Exactly zero — invariant 6 depends on it being so."""
    h, w = static.shape
    return (
        torch.zeros((h, w), dtype=torch.float32, device=device),
        torch.zeros((h, w - 1), dtype=torch.float32, device=device),
        torch.zeros((h - 1, w), dtype=torch.float32, device=device),
    )
