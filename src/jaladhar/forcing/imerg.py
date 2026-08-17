"""GPM IMERG Historical Rainfall Forcing Adapter.

Adapts half-hourly GPM_3IMERGHH v07 granules (0.1 deg) to the canonical 10 m
domain grid (3421 x 3515, EPSG:32643).

Converts native half-hourly precipitation rate (mm/hr) to incremental depth (mm)
for each interval: depth_mm = rate_mm_hr * 0.5 hr.

HONESTY CONSTRAINT (enforced in code):
The BBMP extent falls inside a block of 0.1-deg IMERG cells (16 cells covering
the canonical 10 m domain raster). The adapter exposes the native cell mapping
so downstream code cannot silently assume fine-scale rainfall detail.
"""

from __future__ import annotations

import glob
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pyproj
import typer
import yaml

from src.jaladhar.forcing.interface import (
    ForcingMode,
    RainfallAdapter,
    RainfallEvent,
    RainfallInterval,
)
from src.jaladhar.terrain.grid import Grid, build_grid

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def resolve_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate all required config keys at startup. Fails immediately with aggregated errors."""
    missing: list[str] = []
    for key in ["domain_config", "mode", "historical", "output"]:
        if key not in cfg:
            missing.append(key)

    hist = cfg.get("historical", {})
    for k in ["source", "granules_dir", "time_step_minutes"]:
        if k not in hist:
            missing.append(f"historical.{k}")

    if missing:
        raise KeyError(
            f"Missing required config keys in forcing config: {missing}. "
            "Resolve all keys before execution."
        )

    mode = cfg["mode"]
    if mode != "historical":
        raise ValueError(
            f"IMERG adapter expected mode 'historical', got '{mode}'. "
            "Modes are mutually exclusive and must not be blended."
        )

    return cfg


def compute_imerg_grid_mapping(grid: Grid) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Compute mapping from canonical 10 m UTM grid to IMERG (ilon, ilat) indices.

    Returns:
        ilon_grid: (height, width) array of IMERG longitude indices
        ilat_grid: (height, width) array of IMERG latitude indices
        native_cell_ids: (height, width) array of unique integer IDs per distinct IMERG cell
        n_distinct: Number of distinct IMERG cells covering the grid
    """
    cols = np.arange(grid.width)
    rows = np.arange(grid.height)
    x_coords = grid.bounds[0] + (cols + 0.5) * grid.resolution
    y_coords = grid.bounds[3] - (rows + 0.5) * grid.resolution

    xx, yy = np.meshgrid(x_coords, y_coords)
    transformer = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    lons, lats = transformer.transform(xx, yy)

    # IMERG v07 grid: 0.1 deg cells, center at -179.95 + i*0.1, -89.95 + j*0.1
    # Bins are [lon - 0.05, lon + 0.05) -> floor((lon + 180) / 0.1)
    ilon = np.floor((lons + 180.0) / 0.1).astype(np.int32)
    ilat = np.floor((lats + 90.0) / 0.1).astype(np.int32)

    # Generate unique integer cell IDs for each (ilon, ilat) pair
    unique_pairs = sorted(set(zip(ilon.flatten(), ilat.flatten(), strict=False)))
    pair_to_id = {pair: idx for idx, pair in enumerate(unique_pairs)}

    # Vectorized assignment
    native_cell_ids = np.zeros(ilon.shape, dtype=np.int32)
    for pair, cid in pair_to_id.items():
        mask = (ilon == pair[0]) & (ilat == pair[1])
        native_cell_ids[mask] = cid

    return ilon, ilat, native_cell_ids, len(unique_pairs)


def parse_granule_timestamp(file_name: str) -> datetime:
    """Extract start timestamp from IMERG filename."""
    # Example: 3B-HHR.MS.MRG.3IMERG.20220905-S000000-E002959.0000.V07B.HDF5
    stamp = file_name.split(".3IMERG.")[1].split("-")[0:2]
    dt_str = stamp[0] + stamp[1][1:]  # YYYYMMDDHHMMSS
    return datetime.strptime(dt_str, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


class ImergHistoricalAdapter(RainfallAdapter):
    """Historical rainfall adapter for GPM IMERG half-hourly data."""

    def __init__(self, config_path: Path = REPO / "configs/forcing.yaml"):
        with open(config_path) as f:
            raw_cfg = yaml.safe_load(f)
        self.config = resolve_config(raw_cfg)
        self.config_path = config_path

        # Load domain grid
        domain_cfg_path = REPO / self.config["domain_config"]
        with open(domain_cfg_path) as f:
            domain_cfg = yaml.safe_load(f)
        self.grid, self.grid_diag = build_grid(domain_cfg, REPO)

        # Precompute coordinate mapping
        (
            self.ilon_grid,
            self.ilat_grid,
            self.native_cell_ids,
            self.distinct_cells_count,
        ) = compute_imerg_grid_mapping(self.grid)

        granules_dir = REPO / self.config["historical"]["granules_dir"]
        self.granule_files = sorted(granules_dir.glob("*.HDF5"))
        if not self.granule_files:
            raise FileNotFoundError(
                f"No IMERG granules found in {granules_dir}. "
                "Per CLAUDE.md rule 1, stopping rather than fabricating rainfall."
            )

    @property
    def mode(self) -> ForcingMode:
        return ForcingMode.HISTORICAL

    def get_forcing(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> RainfallEvent:
        """Load IMERG granules within [start_time, end_time] and map to canonical grid."""
        intervals: list[RainfallInterval] = []

        for p in self.granule_files:
            ts = parse_granule_timestamp(p.name)
            if ts < start_time or ts > end_time:
                continue

            with h5py.File(p, "r") as f:
                precip_global = f["Grid/precipitation"][0]  # shape (3600, 1800) in mm/hr
                # Extract rates onto canonical grid
                rate_mm_hr = precip_global[self.ilon_grid, self.ilat_grid]
                # Negative values are IMERG fill/nodata -> 0.0 mm/hr
                rate_mm_hr = np.where(rate_mm_hr < 0, 0.0, rate_mm_hr)

                # Convert mm/hr across 30-min (0.5 hr) interval to incremental depth (mm)
                interval_minutes = 30.0
                depth_mm = rate_mm_hr * (interval_minutes / 60.0)

                intervals.append(
                    RainfallInterval(
                        timestamp=ts,
                        interval_minutes=interval_minutes,
                        rainfall_grid_mm=depth_mm.astype(np.float32),
                        native_cell_ids=self.native_cell_ids,
                        distinct_native_cells=self.distinct_cells_count,
                        metadata={
                            "granule": p.name,
                            "mean_intensity_mm_hr": float(np.mean(rate_mm_hr)),
                            "max_intensity_mm_hr": float(np.max(rate_mm_hr)),
                        },
                    )
                )

        if not intervals:
            raise ValueError(
                f"No IMERG granules found in requested window [{start_time}, {end_time}]. "
                "Per CLAUDE.md rule 1, stopping rather than fabricating data."
            )

        event = RainfallEvent(
            mode=self.mode,
            intervals=intervals,
            cell_resolution_m=self.grid.resolution,
            source_name="gpm_imerg_v07_final",
            metadata={
                "distinct_native_cells": self.distinct_cells_count,
                "grid_shape": [self.grid.height, self.grid.width],
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
            },
        )
        # Assert mass balance invariant
        event.verify_mass_conservation()
        return event


@app.command()
def main(
    config: Path = typer.Option(REPO / "configs/forcing.yaml", help="Path to forcing config"),
    start: str = typer.Option("2022-09-05T00:00:00Z", help="Start timestamp ISO UTC"),
    end: str = typer.Option("2022-09-05T23:30:00Z", help="End timestamp ISO UTC"),
    out: Path = typer.Option(REPO / "runs/forcing", help="Output directory for run manifest"),
) -> None:
    """Run IMERG historical adapter and verify Sept 5 2022 target metrics."""
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"

    # Write initial manifest per rule 6
    t0 = time.perf_counter()
    manifest: dict[str, Any] = {
        "stage": "forcing_imerg_historical",
        "status": "running",
        "git_sha": git_sha(),
        "config_path": str(config),
        "start_time_iso": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        adapter = ImergHistoricalAdapter(config)
        st = datetime.fromisoformat(start.replace("Z", "+00:00"))
        et = datetime.fromisoformat(end.replace("Z", "+00:00"))

        event = adapter.get_forcing(st, et)
        event.verify_mass_conservation()

        wall_clock = time.perf_counter() - t0

        # Update manifest in place on completion per rule 6
        manifest.update(
            {
                "status": "completed",
                "wall_clock_sec": wall_clock,
                "num_intervals": event.num_intervals,
                "duration_hours": event.total_duration_hours,
                "total_volume_m3": event.total_volume_m3,
                "areal_mean_depth_mm": event.areal_mean_total_mm,
                "peak_hourly_rate_mm_hr": event.peak_hourly_rate_mm_hr,
                "distinct_native_cells": event.distinct_native_cells,
                "mass_conservation_verified": True,
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))

        typer.echo(f"IMERG Historical Adapter Execution Summary:")
        typer.echo(f"  Granules processed: {event.num_intervals}")
        typer.echo(f"  Duration: {event.total_duration_hours:.1f} hours")
        typer.echo(f"  Distinct IMERG native cells covering grid: {event.distinct_native_cells}")
        typer.echo(f"  Domain Areal-Mean Depth: {event.areal_mean_total_mm:.2f} mm")
        typer.echo(f"  Peak Cell Intensity: {event.peak_hourly_rate_mm_hr:.2f} mm/hr")
        typer.echo(f"  Total Domain Rainfall Volume: {event.total_volume_m3:,.1f} m^3")
        typer.echo(f"  Mass balance check: PASS")
        typer.echo(f"\nWrote manifest to {manifest_path}")

    except Exception as e:
        manifest.update(
            {
                "status": "failed",
                "error": str(e),
                "wall_clock_sec": time.perf_counter() - t0,
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise


if __name__ == "__main__":
    app()
