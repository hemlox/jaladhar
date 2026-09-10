"""Single source of truth for the JALADHAR terrain raster grid.
Every layer in the terrain stack (elevation, building mask, Manning's n,
resolution, and the BBMP boundary path — and the bbox is snapped OUTWARD to
the property later conditioning/derived-layer steps actually need."""

from __future__ import annotations

import json
import math
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import typer
import yaml
from affine import Affine
from rasterio.crs import CRS
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Grid:
    """`.assert_aligned()` is the invariant-1 check every stack-layer writer"""

    transform: Affine
    width: int
    height: int
    crs: CRS
    bounds: tuple[float, float, float, float]
    resolution: float

    def profile(
        self,
        dtype: str = "float32",
        count: int = 1,
        nodata: float | None = None,
        compress: str = "deflate",
        tiled: bool = True,
        **extra: Any,
    ) -> dict[str, Any]:
        prof: dict[str, Any] = {
            "driver": "GTiff",
            "dtype": dtype,
            "count": count,
            "crs": self.crs,
            "transform": self.transform,
            "width": self.width,
            "height": self.height,
            "nodata": nodata,
            "compress": compress,
        }
        if tiled:
            prof["tiled"] = True
            prof["blockxsize"] = 256
            prof["blockysize"] = 256
        prof.update(extra)
        return prof

    def assert_aligned(self, dataset: Any, atol: float = 1e-6) -> None:
        """the canonical domain Grid exactly (invariant 1: "all layers share"""
        if isinstance(dataset, dict):
            t, w, h, c = (
                dataset["transform"],
                dataset["width"],
                dataset["height"],
                dataset["crs"],
            )
        else:
            t, w, h, c = dataset.transform, dataset.width, dataset.height, dataset.crs

        if (w, h) != (self.width, self.height):
            raise ValueError(
                f"grid misaligned: shape ({w}, {h}) != canonical " f"({self.width}, {self.height})"
            )
        for got, want in zip(tuple(t)[:6], tuple(self.transform)[:6], strict=True):
            if abs(got - want) > atol:
                raise ValueError(
                    f"grid misaligned: transform {tuple(t)[:6]} != canonical "
                    f"{tuple(self.transform)[:6]}"
                )
        got_epsg = CRS.from_user_input(c).to_epsg()
        want_epsg = self.crs.to_epsg()
        if got_epsg is not None and want_epsg is not None and got_epsg != want_epsg:
            raise ValueError(f"grid misaligned: crs EPSG:{got_epsg} != canonical EPSG:{want_epsg}")

    def buffered(self, buffer_m: float) -> Grid:
        """back to this Grid with zero resampling. This is how invariant 10"""
        cells = buffer_m / self.resolution
        if abs(cells - round(cells)) > 1e-9:
            raise ValueError(
                f"buffer_m={buffer_m} is not an exact multiple of "
                f"resolution={self.resolution} — the buffered grid would land "
                "off-lattice from the canonical Grid."
            )
        bc = round(cells)
        win = Window(
            col_off=-bc, row_off=-bc, width=self.width + 2 * bc, height=self.height + 2 * bc
        )
        new_transform = window_transform(win, self.transform)
        new_bounds = (
            self.bounds[0] - buffer_m,
            self.bounds[1] - buffer_m,
            self.bounds[2] + buffer_m,
            self.bounds[3] + buffer_m,
        )
        return Grid(
            transform=new_transform,
            width=self.width + 2 * bc,
            height=self.height + 2 * bc,
            crs=self.crs,
            bounds=new_bounds,
            resolution=self.resolution,
        )

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "crs": self.crs.to_string(),
            "bounds": list(self.bounds),
            "resolution": self.resolution,
            "transform": list(self.transform)[:6],
        }


@contextmanager
def atomic_output_path(path: Path) -> Iterator[Path]:
    """uses for network downloads (a partial file must never look complete),"""
    tmp = path.with_name(path.stem + ".part" + path.suffix)
    try:
        yield tmp
        tmp.rename(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def load_config(config_path: Path) -> dict[str, Any]:
    with open(config_path) as f:
        return yaml.safe_load(f)


def run_stage(
    stage: str,
    build_fn: Callable[[dict[str, Any], Path], dict[str, Any]],
    cfg: dict[str, Any],
    config_path: Path,
    repo_root: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """Time `build_fn`, write ITS manifest, return its result. THE one place
    a stage's manifest gets written — called identically by every terrain
    manifest is written whenever and however a stage actually runs, never"""
    t0 = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    start_manifest = {
        "stage": stage,
        "status": "running",
        "git_sha": git_sha(),
        "config_path": str(config_path),
        "start_time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out_dir / "manifest.json").write_text(json.dumps(start_manifest, indent=2, default=str))

    try:
        result = build_fn(cfg, repo_root)
    except Exception as e:
        error_manifest = {
            "stage": stage,
            "status": "failed",
            "git_sha": git_sha(),
            "config_path": str(config_path),
            "error": str(e),
            "wall_clock_sec": time.perf_counter() - t0,
        }
        (out_dir / "manifest.json").write_text(json.dumps(error_manifest, indent=2, default=str))
        raise

    wall_clock = time.perf_counter() - t0
    manifest = {
        "stage": stage,
        "status": "completed",
        "git_sha": git_sha(),
        "config_path": str(config_path),
        "wall_clock_sec": wall_clock,
        **result,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    return result


def load_boundary(cfg: dict[str, Any], repo_root: Path = REPO) -> gpd.GeoDataFrame:
    """Load and validate the BBMP boundary, in its NATIVE (file) CRS.
    Extracted from `build_grid` so every module that needs the boundary
    (a KML reader returning a partial layer produces a wrong polygon with"""
    b_cfg = cfg["boundary"]
    boundary_path = repo_root / b_cfg["path"]
    if not boundary_path.exists():
        raise FileNotFoundError(
            f"boundary file not found: {boundary_path}. Per CLAUDE.md rule 1, "
            "stopping rather than fabricating a boundary."
        )

    gdf = gpd.read_file(boundary_path)
    n_features = len(gdf)
    expected_n = b_cfg["expected_feature_count"]
    if n_features != expected_n:
        raise ValueError(
            f"boundary feature count mismatch: got {n_features}, expected "
            f"{expected_n} from {boundary_path}. A reader silently returning "
            "a partial layer produces a wrong bbox with no exception — this "
            "check exists specifically to catch that."
        )
    return gdf


def build_grid(cfg: dict[str, Any], repo_root: Path = REPO) -> tuple[Grid, dict[str, Any]]:
    """Loads the BBMP boundary via `load_boundary` (feature-count-validated),
    Returns `(grid, diagnostics)` — diagnostics feeds the CLI's manifest."""
    b_cfg = cfg["boundary"]
    gdf = load_boundary(cfg, repo_root)
    n_features = len(gdf)

    crs = CRS.from_user_input(cfg["crs"])
    resolution = float(cfg["resolution_m"])

    gdf_proj = gdf.to_crs(crs)
    raw_bounds = tuple(float(x) for x in gdf_proj.total_bounds)

    gdf_valid = gdf_proj.copy()
    gdf_valid["geometry"] = gdf_valid.make_valid()
    union_geom = gdf_valid.union_all()
    union_area_km2 = union_geom.area / 1e6

    expected_area = b_cfg["expected_area_km2"]
    tol = b_cfg["area_tolerance_km2"]
    if abs(union_area_km2 - expected_area) > tol:
        raise ValueError(
            f"boundary union area {union_area_km2:.3f} km^2 is outside "
            f"{expected_area} +/- {tol} km^2 — suspect wrong file, wrong "
            "layer, or a failed reprojection."
        )

    xmin, ymin, xmax, ymax = raw_bounds
    sxmin = math.floor(xmin / resolution) * resolution
    symin = math.floor(ymin / resolution) * resolution
    sxmax = math.ceil(xmax / resolution) * resolution
    symax = math.ceil(ymax / resolution) * resolution

    width = round((sxmax - sxmin) / resolution)
    height = round((symax - symin) / resolution)
    transform = Affine(resolution, 0.0, sxmin, 0.0, -resolution, symax)

    grid = Grid(
        transform=transform,
        width=width,
        height=height,
        crs=crs,
        bounds=(sxmin, symin, sxmax, symax),
        resolution=resolution,
    )

    diagnostics = {
        "boundary_path": str(b_cfg["path"]),
        "n_features": n_features,
        "raw_bounds_native_crs": [float(x) for x in gdf.total_bounds],
        "raw_bounds_projected": list(raw_bounds),
        "raw_width_m": xmax - xmin,
        "raw_height_m": ymax - ymin,
        "union_area_km2": union_area_km2,
        "expected_area_km2": expected_area,
        "area_tolerance_km2": tol,
        "snapped_bounds": list(grid.bounds),
        "snapped_width_m": sxmax - sxmin,
        "snapped_height_m": symax - symin,
        "phase0_raw_ceil_width_cells": math.ceil((xmax - xmin) / resolution),
        "phase0_raw_ceil_height_cells": math.ceil((ymax - ymin) / resolution),
        "grid_width": width,
        "grid_height": height,
        "grid_cells": width * height,
    }
    return grid, diagnostics


def build_grid_result(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    """manifest is written regardless of caller, see `run_stage`'s docstring)
    needs a single flat dict to spread into the manifest. This is that"""
    grid, diag = build_grid(cfg, repo_root)
    return {
        "crs": str(grid.crs),
        "resolution_m": grid.resolution,
        **diag,
        "grid": grid.to_manifest_dict(),
    }


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_grid", help="Directory to write the manifest into"
    ),
) -> None:
    """Build the canonical Grid from config, print it, and write a manifest."""
    cfg = load_config(config)
    diag = run_stage("phase1_terrain_grid", build_grid_result, cfg, config, REPO, out)
    g = diag["grid"]

    typer.echo(f"boundary: {diag['boundary_path']} ({diag['n_features']} features)")
    typer.echo(
        f"union area: {diag['union_area_km2']:.3f} km^2 "
        f"(expected {diag['expected_area_km2']} +/- {diag['area_tolerance_km2']} km^2)"
    )
    typer.echo(f"raw bbox:     {diag['raw_width_m']:.3f} x {diag['raw_height_m']:.3f} m")
    typer.echo(f"snapped bbox: {diag['snapped_width_m']:.1f} x {diag['snapped_height_m']:.1f} m")
    typer.echo(f"grid: {g['width']} x {g['height']} = {g['width'] * g['height']:,} cells")
    typer.echo(
        f"  (Phase 0's raw-ceil-of-width estimate: "
        f"{diag['phase0_raw_ceil_width_cells']} x {diag['phase0_raw_ceil_height_cells']} — "
        "outward-snapping both bbox corners independently, as this module does, "
        "can add up to +1 cell per axis versus ceiling the raw width alone from "
        "a fixed origin. Both are correct; they answer different questions.)"
    )
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
