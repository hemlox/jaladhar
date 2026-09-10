"""PROMPT.md §7 requires datum-relative elevation, and the invariant that proves
the float32 cast of that difference is bitwise identical either way. The datum"""

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

    dz_x: torch.Tensor
    dz_y: torch.Tensor
    n_x: torch.Tensor  # (H, W-1)  Manning's n at the face
    n_y: torch.Tensor
    c_x: torch.Tensor
    c_y: torch.Tensor
    drain_cap_m_s: torch.Tensor
    infil_rate_m_s: torch.Tensor
    edge_w_n: torch.Tensor  # (H,)  Manning's n along each domain edge
    edge_e_n: torch.Tensor
    edge_n_n: torch.Tensor
    edge_s_n: torch.Tensor
    edge_w_s: torch.Tensor
    edge_e_s: torch.Tensor
    edge_n_s: torch.Tensor
    edge_s_s: torch.Tensor
    edge_open: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    datum_m: float = 0.0  # domain minimum elevation, recorded for the manifest
    shape: tuple[int, int] = (0, 0)

    def parameters(self) -> dict[str, torch.Tensor]:
        """exactly one definition — and so invariant 25 can assert that"""
        return {
            "n_x": self.n_x,
            "n_y": self.n_y,
            "c_x": self.c_x,
            "c_y": self.c_y,
            "drain_cap_m_s": self.drain_cap_m_s,
        }


def _harmonic(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """act in series. Also smooth, where `min` has a subgradient kink: physics and"""
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
    """exactly one definition (the same single-source-of-truth reason"""
    import yaml

    with open(path) as f:
        cfg = yaml.safe_load(f)
    with open(repo_root / cfg["domain_config"]) as f:
        cfg["_domain"] = yaml.safe_load(f)
    return cfg


def _read(path: Path) -> np.ndarray:
    with rasterio.open(path) as ds:
        return ds.read(1)


def assert_drain_manifest_contract(d_manifest: dict[str, Any], domain_crs: str) -> None:
    """Assert the Phase 1 drain guarantees required by the solver (V8 seam)."""
    if "source" not in d_manifest:
        raise ValueError("Drain manifest missing guaranteed property: 'source'")
    if d_manifest.get("feature_count", 0) <= 0:
        raise ValueError(f"Drain manifest invalid feature_count: {d_manifest.get('feature_count')}")
    if d_manifest.get("total_length_m", 0.0) <= 0.0:
        raise ValueError(
            f"Drain manifest invalid total_length_m: {d_manifest.get('total_length_m')}"
        )
    if d_manifest.get("crs") != domain_crs:
        raise ValueError(
            f"Drain CRS mismatch: manifest declares {d_manifest.get('crs')!r}, "
            f"domain requires {domain_crs!r}"
        )
    if "class_breakdown" not in d_manifest:
        raise ValueError("Drain manifest missing guaranteed property: 'class_breakdown'")
    if not d_manifest.get("assumed_uncalibrated", False):
        raise ValueError("Drain manifest must preserve assumed_uncalibrated=True (prior status)")


def load_domain(
    cfg: dict[str, Any],
    repo_root: Path,
    *,
    window: tuple[slice, slice] | None = None,
    device: str | torch.device = "cpu",
    use_buffered: bool = True,
    elevation_override: Path | None = None,
) -> StaticFields:
    """isolating domain-edge boundary outfalls from BBMP reporting cells.
    variant (goal Part 3's carved DEM) on the SAME grid; manning, conveyance
    shape exactly — asserted, not assumed (CLAUDE.md V8: the variant's manifest"""
    processed = repo_root / "data" / "processed"
    if use_buffered and (processed / "buffered").exists():
        processed = processed / "buffered"

    z = _read(processed / "elevation.tif")
    n = _read(processed / "manning_n.tif")
    c = _read(processed / "building_conveyance_factor.tif")
    d = _read(processed / "drain_capacity.tif")
    if elevation_override is not None:
        z_override = _read(elevation_override)
        if z_override.shape != z.shape:
            raise ValueError(
                f"elevation_override shape {z_override.shape} != stack elevation "
                f"{z.shape}; variant must match the {processed.name} grid exactly."
            )
        z = z_override
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

    # Assert drain manifest properties across phase boundary (Invariant V8)
    if cfg.get("sinks", {}).get("drain", {}).get("enabled", False):
        drain_manifest_path = repo_root / "runs" / "terrain_drains" / "manifest.json"
        if drain_manifest_path.exists():
            import json

            d_manifest = json.loads(drain_manifest_path.read_text())
            assert_drain_manifest_contract(d_manifest, str(domain["crs"]))

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
