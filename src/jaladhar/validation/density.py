"""Urban Density Stratification for SAR Flood Extent Scoring.

PROMPT.md §8 item 3: Sentinel-1 SAR flood scoring must be reported
stratified by urban density, because SAR flood mapping degrades severely
in dense built-up areas (layover, shadow, double-bounce) while performing
well over open water, lake margins, and peri-urban land.

This module:
1. Loads the binary building mask from data/processed/building_mask.tif (or config).
2. Computes a continuous building-fraction raster via a 100 m moving average window.
   (Rationale: Sentinel-1 GRD resolution is ~20 m; urban microwave scattering
   effects operate over block/neighborhood scales ~100 m / 1 hectare).
3. Derives density classes (OPEN, MODERATE, DENSE) from the measured empirical
   distribution and assigns integer class labels.
4. Writes aligned GeoTIFFs for building fraction and density classes.
5. Writes a provenance manifest to runs/density_stratification/manifest.json.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import typer
import yaml
from scipy.ndimage import uniform_filter

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


@dataclass(frozen=True)
class DensityClassDefinition:
    class_id: int
    name: str
    min_fraction: float
    max_fraction: float
    cell_count: int
    area_km2: float
    percent_of_domain: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "name": self.name,
            "min_fraction": round(self.min_fraction, 4),
            "max_fraction": round(self.max_fraction, 4),
            "cell_count": self.cell_count,
            "area_km2": round(self.area_km2, 4),
            "percent_of_domain": round(self.percent_of_domain, 2),
        }


@dataclass(frozen=True)
class DensityStratificationResult:
    window_size_m: float
    kernel_size_cells: int
    histogram_bins: list[tuple[float, float, int, float]]  # (low, high, count, pct)
    classes: list[DensityClassDefinition]
    total_cells: int
    total_area_km2: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_size_m": self.window_size_m,
            "kernel_size_cells": self.kernel_size_cells,
            "total_cells": self.total_cells,
            "total_area_km2": round(self.total_area_km2, 4),
            "histogram": [
                {
                    "low": round(b[0], 2),
                    "high": round(b[1], 2),
                    "cell_count": b[2],
                    "percent": round(b[3], 2),
                }
                for b in self.histogram_bins
            ],
            "classes": [c.to_dict() for c in self.classes],
        }


def compute_building_fraction(
    building_mask: np.ndarray,
    window_size_m: float = 100.0,
    resolution_m: float = 10.0,
) -> tuple[np.ndarray, int]:
    """Compute local building fraction via a moving uniform filter window.

    Args:
        building_mask: 2D binary or float array (1 = building, 0 = non-building).
        window_size_m: Physical width of the moving window in meters (default 100m).
        resolution_m: Grid cell resolution in meters (default 10m).

    Returns:
        tuple of (building_fraction_2d_array, kernel_size_cells)
    """
    kernel_size = int(round(window_size_m / resolution_m))
    if kernel_size % 2 == 0:
        kernel_size += 1  # ensure odd size for symmetric centering

    # Uniform filter computes local mean over the window
    bf = uniform_filter(
        building_mask.astype(np.float32),
        size=kernel_size,
        mode="constant",
        cval=0.0,
    )
    # Clip numerical epsilon
    bf = np.clip(bf, 0.0, 1.0)
    return bf, kernel_size


def classify_density(
    building_fraction: np.ndarray,
    tercile_method: str = "nonzero",
    custom_cuts: tuple[float, float] | None = None,
    cell_area_m2: float = 100.0,
) -> tuple[np.ndarray, DensityStratificationResult]:
    """Derive OPEN, MODERATE, and DENSE urban density classes from building fraction.

    Classes:
      0: OPEN (low built-up fraction, e.g. lakes, open fields, peri-urban)
      1: MODERATE (medium built-up fraction, e.g. suburban residential, layouts)
      2: DENSE (high built-up fraction, e.g. dense urban core, commercial hubs)

    Args:
        building_fraction: 2D array of building fractions [0, 1].
        tercile_method: 'nonzero' (terciles of non-zero cells, default: ~0.10 and ~0.30)
                        or 'all' (terciles across entire domain).
        custom_cuts: Optional explicit (cut1, cut2) threshold tuple.
        cell_area_m2: Physical area of one cell in m^2 (default 100 m^2 for 10m grid).

    Returns:
        tuple of (density_class_raster (uint8), DensityStratificationResult)
    """
    total_cells = building_fraction.size
    total_area_km2 = (total_cells * cell_area_m2) / 1e6

    # 1. Compute empirical 10-bin histogram [0..1]
    hist_counts, bin_edges = np.histogram(building_fraction, bins=10, range=(0.0, 1.0))
    hist_bins: list[tuple[float, float, int, float]] = []
    for i in range(len(hist_counts)):
        pct = (hist_counts[i] / total_cells) * 100.0
        hist_bins.append(
            (float(bin_edges[i]), float(bin_edges[i + 1]), int(hist_counts[i]), float(pct))
        )

    # 2. Determine class cuts from empirical distribution
    if custom_cuts is not None:
        cut1, cut2 = custom_cuts
    elif tercile_method == "nonzero":
        nonzero_vals = building_fraction[building_fraction > 0.0]
        if len(nonzero_vals) > 0:
            cut1 = float(np.percentile(nonzero_vals, 33.333))
            cut2 = float(np.percentile(nonzero_vals, 66.667))
        else:
            cut1, cut2 = 0.10, 0.30
    else:  # all cells
        cut1 = float(np.percentile(building_fraction, 33.333))
        cut2 = float(np.percentile(building_fraction, 66.667))

    # 3. Create integer classification raster
    # 0 = OPEN (< cut1), 1 = MODERATE (cut1 <= bf < cut2), 2 = DENSE (bf >= cut2)
    density_raster = np.zeros(building_fraction.shape, dtype=np.uint8)
    density_raster[(building_fraction >= cut1) & (building_fraction < cut2)] = 1
    density_raster[building_fraction >= cut2] = 2

    # 4. Compute per-class statistics
    count_open = int(np.sum(density_raster == 0))
    count_mod = int(np.sum(density_raster == 1))
    count_dense = int(np.sum(density_raster == 2))

    classes = [
        DensityClassDefinition(
            class_id=0,
            name="OPEN",
            min_fraction=0.0,
            max_fraction=cut1,
            cell_count=count_open,
            area_km2=(count_open * cell_area_m2) / 1e6,
            percent_of_domain=(count_open / total_cells) * 100.0,
        ),
        DensityClassDefinition(
            class_id=1,
            name="MODERATE",
            min_fraction=cut1,
            max_fraction=cut2,
            cell_count=count_mod,
            area_km2=(count_mod * cell_area_m2) / 1e6,
            percent_of_domain=(count_mod / total_cells) * 100.0,
        ),
        DensityClassDefinition(
            class_id=2,
            name="DENSE",
            min_fraction=cut2,
            max_fraction=1.0,
            cell_count=count_dense,
            area_km2=(count_dense * cell_area_m2) / 1e6,
            percent_of_domain=(count_dense / total_cells) * 100.0,
        ),
    ]

    result = DensityStratificationResult(
        window_size_m=100.0,
        kernel_size_cells=11,
        histogram_bins=hist_bins,
        classes=classes,
        total_cells=total_cells,
        total_area_km2=total_area_km2,
    )
    return density_raster, result


def run_density_pipeline(
    config_path: Path = REPO / "configs/validation.yaml",
) -> DensityStratificationResult:
    """Run full density stratification pipeline from config and save rasters and manifest."""
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    d_cfg = cfg.get("density_stratification", {})
    bmask_rel = d_cfg.get("building_mask_path", "data/processed/building_mask.tif")
    bf_out_rel = d_cfg.get("output_building_fraction_path", "data/processed/building_fraction.tif")
    dc_out_rel = d_cfg.get(
        "output_density_classes_path", "data/processed/urban_density_classes.tif"
    )
    runs_dir_rel = d_cfg.get("runs_dir", "runs/density_stratification")
    window_size_m = float(d_cfg.get("window_size_m", 100.0))

    bmask_path = REPO / bmask_rel
    bf_out_path = REPO / bf_out_rel
    dc_out_path = REPO / dc_out_rel
    runs_dir = REPO / runs_dir_rel
    runs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = runs_dir / "manifest.json"

    t0 = time.perf_counter()
    manifest: dict[str, Any] = {
        "stage": "urban_density_stratification",
        "status": "running",
        "git_sha": git_sha(),
        "start_time_iso": datetime.now(UTC).isoformat(),
        "config_path": str(config_path),
        "input_building_mask": str(bmask_path),
        "window_size_m": window_size_m,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        if not bmask_path.exists():
            raise FileNotFoundError(f"Building mask raster not found at {bmask_path}")

        with rasterio.open(bmask_path) as src:
            bmask = src.read(1)
            profile = src.profile.copy()
            res_m = abs(src.transform.a)

        # 1. Compute moving window building fraction
        bf, ksize = compute_building_fraction(
            bmask, window_size_m=window_size_m, resolution_m=res_m
        )

        # 2. Derive density classes
        density_raster, result = classify_density(
            bf, tercile_method="nonzero", cell_area_m2=res_m * res_m
        )

        # 3. Write building fraction GeoTIFF
        bf_profile = profile.copy()
        bf_profile.update(dtype="float32", nodata=None, compress="deflate")
        bf_out_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(bf_out_path, "w", **bf_profile) as dst:
            dst.write(bf.astype(np.float32), 1)

        # 4. Write density classes GeoTIFF
        dc_profile = profile.copy()
        dc_profile.update(dtype="uint8", nodata=255, compress="deflate")
        dc_out_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dc_out_path, "w", **dc_profile) as dst:
            dst.write(density_raster.astype(np.uint8), 1)

        wall_clock = time.perf_counter() - t0
        manifest.update(
            {
                "status": "completed",
                "wall_clock_sec": round(wall_clock, 3),
                "output_building_fraction": str(bf_out_path),
                "output_density_classes": str(dc_out_path),
                "stratification": result.to_dict(),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))
        return result

    except Exception as e:
        manifest.update(
            {
                "status": "failed",
                "error": str(e),
                "wall_clock_sec": round(time.perf_counter() - t0, 3),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs/validation.yaml", help="Path to validation config YAML"
    ),
) -> None:
    """Compute urban density stratification raster and report distribution stats."""
    typer.echo(f"Running urban density stratification from config: {config}")
    res = run_density_pipeline(config_path=config)

    typer.echo("\n=======================================================")
    typer.echo("URBAN DENSITY STRATIFICATION SUMMARY (PROMPT.md §8 item 3)")
    typer.echo("=======================================================")
    typer.echo(
        f"Moving window size: {res.window_size_m} m "
        f"({res.kernel_size_cells}x{res.kernel_size_cells} cells)"
    )
    typer.echo(f"Total domain cells: {res.total_cells:,} ({res.total_area_km2:.2f} km²)\n")

    typer.echo("Measured Building Fraction Histogram:")
    typer.echo("  ------------------------------------------------")
    typer.echo("  Building Fraction Bin | Cell Count | Percent")
    typer.echo("  ------------------------------------------------")
    for b in res.histogram_bins:
        typer.echo(f"  [{b[0]:.2f} - {b[1]:.2f}]            | {b[2]:10,d} | {b[3]:5.2f}%")
    typer.echo("  ------------------------------------------------\n")

    typer.echo("Derived Density Classes:")
    typer.echo("  ------------------------------------------------------------------------")
    typer.echo("  Class ID | Name     | Fraction Range | Cell Count | Area (km²) | Domain %")
    typer.echo("  ------------------------------------------------------------------------")
    for c in res.classes:
        line_str = (
            f"     {c.class_id}     | {c.name:8s} | "
            f"[{c.min_fraction:.2f} - {c.max_fraction:.2f}]   | "
            f"{c.cell_count:10,d} | {c.area_km2:10.2f} | {c.percent_of_domain:5.2f}%"
        )
        typer.echo(line_str)
    typer.echo("  ------------------------------------------------------------------------")


if __name__ == "__main__":
    app()
