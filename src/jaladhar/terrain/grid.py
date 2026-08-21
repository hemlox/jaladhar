"""Single source of truth for the JALADHAR terrain raster grid.

Every layer in the terrain stack (elevation, building mask, Manning's n,
drain capacity, road segment IDs, slope, flow accumulation, distance-to-
drain) must be pixel-identical: same shape, same transform, same CRS.
Misalignment between layers is the highest-frequency failure mode in a
multi-layer raster stack, and it is SILENT — the solver just reads garbage
neighbours, no exception is ever raised. So the grid is defined exactly
once, here, and every other module imports `Grid` / `build_grid` from this
file rather than recomputing bounds independently (a shapely/GEOS version
bump can shift a recomputed union's bbox by a fraction of a pixel, which is
enough to misalign two layers that were each "correctly" computed).

The grid is built from `configs/domain_bengaluru.yaml` alone — CRS,
resolution, and the BBMP boundary path — and the bbox is snapped OUTWARD to
exact multiples of the resolution so the transform is REPRODUCIBLE FROM THE
CONFIG ALONE. This is deliberately not the same operation as Phase 0's
`bench/acc_stencil.py` benchmark grid, which ceils the raw bbox WIDTH from a
fixed origin (assuming the origin is already on-lattice). Snapping both
corners of the bbox outward independently can add up to +1 cell per axis
versus that estimate, because the fractional slack at BOTH the min and max
edge accumulates. Both are legitimate, they just answer different
questions; `build_grid` here answers "what is the smallest 10 m-aligned
raster that fully contains the union of all 225 ward polygons", which is
the property later conditioning/derived-layer steps actually need.
"""

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
    """A rectangular raster lattice: transform + shape + crs + resolution.

    Build exactly one canonical `Grid` per run (via `build_grid`) and pass it
    to every writer. `.profile()` turns it into a rasterio profile;
    `.assert_aligned()` is the invariant-1 check every stack-layer writer
    must call before writing; `.buffered()` produces a wider Grid on the
    identical lattice, for the DEM fetch's flow-accumulation margin.
    """

    transform: Affine
    width: int
    height: int
    crs: CRS
    bounds: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax
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
        """A rasterio profile for a GeoTIFF on this exact lattice.

        `tiled=True` (with 256x256 internal blocks) so later stages can read
        and write in windows rather than loading the whole raster — CLAUDE.md:
        "prefer processing rasters in windows/blocks... where the library
        supports it."
        """
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
        """Raise ValueError if `dataset` is not pixel-identical to this Grid.

        `dataset` may be an open rasterio dataset (anything exposing
        `.transform` / `.width` / `.height` / `.crs`) or a profile `dict`
        with those same keys.

        STRICT equality — this is for FINAL stack layers, which must match
        the canonical domain Grid exactly (invariant 1: "all layers share
        identical shape, transform, crs"). The buffered DEM `fetch.py`
        produces is a deliberately WIDER lattice (see `.buffered()`) — check
        that against the Grid returned by `.buffered(buffer_m)`, never
        against this method, until a later stage crops it back down.
        """
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
        """A new Grid widened by `buffer_m` on every side, same lattice.

        `buffer_m` must be an exact multiple of `resolution` so the buffered
        grid shares the identical pixel phase as this one — required by
        `fetch.py`: the DEM it writes on the buffered grid must later crop
        back to this Grid with zero resampling. This is how invariant 10
        (>=500 m buffer before flow accumulation, for correct upstream areas
        at the domain edge) is satisfied without inventing a second,
        independently-computed lattice that could drift out of alignment.
        """
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
    """Yield a `.part` sibling of `path`; rename onto `path` on clean exit,
    delete the `.part` file if the block raises.

    Works for any writer that takes a destination path — a direct rasterio
    write, or an external tool (e.g. whitebox) that writes to its own
    `output=` argument — the caller just points the writer at the yielded
    tmp path instead of `path` itself. Same discipline fetch.py already
    uses for network downloads (a partial file must never look complete),
    applied to local writes: without it, a process killed mid-write (or a
    whitebox subprocess that dies partway through) leaves a truncated,
    invalid GeoTIFF sitting at the FINAL path — worse than no file at all,
    because nothing downstream can tell it apart from a genuine one except
    by trying to read it.

    The tmp name inserts `.part` BEFORE the real suffix (`x.part.tif`, not
    `x.tif.part`) — confirmed empirically that whitebox-tools requires its
    `output=` path to literally END in a recognized raster extension: given
    a `.tif.part` path it returns exit code 0 (success) while silently
    writing nothing at all, a false-success that would otherwise turn this
    entire safety mechanism into a silent no-write.
    """
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
    module's own CLI `main()` and by `build.py`'s orchestrator, so a
    manifest is written whenever and however a stage actually runs, never
    only on one of the two call paths.

    This closes a real bug the Phase 1 review found: `build.py` used to
    call e.g. `fetch_dem()` directly, which rewrites the DEM raster but
    (before this function existed) wrote no manifest — only `fetch.py`'s
    standalone `main()` did that. `conditioning.py` then reads `runs/
    terrain_dem/manifest.json` to decide `dem_is_dtm`; a manifest left over
    from an EARLIER run (e.g. before `configs/domain_bengaluru.yaml`'s
    `dem.sources` order was changed) could describe a DEM that is no
    longer the one on disk, with nothing to detect the mismatch.

    Per CLAUDE.md V1 (declared state vs realized state): this function
    makes "the manifest describes the artifact that produced it" true by
    CONSTRUCTION — the write happens in the same call, every time the
    stage runs, regardless of caller — rather than something that has to
    be remembered separately at every call site.

    Raises whatever `build_fn` raises; a failed stage writes no manifest
    (an incomplete/wrong manifest would be worse than none — CLAUDE.md
    rule 1, don't let a failure look like it produced valid output).
    """
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
    polygon — not just for the raster grid, but e.g. as a WGS84 query
    region for an OSM/Overpass fetch — shares one validated load rather
    than four independent re-implementations that could silently drift
    (a KML reader returning a partial layer produces a wrong polygon with
    no exception; this check is what caught Phase 0's `osmnx` geocoding of
    "BBMP" itself returning the HQ building instead of the boundary).

    Returns the GeoDataFrame UNPROJECTED (whatever CRS the file itself
    uses — WGS84 for our KML). Callers needing the metric-CRS union for
    the raster grid should go through `build_grid`, which reprojects.
    """
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
    """Build the canonical domain Grid from a loaded config dict.

    Loads the BBMP boundary via `load_boundary` (feature-count-validated),
    reprojects to the configured CRS, unions it, cross-checks the union
    area against the configured expectation, then snaps the bbox OUTWARD
    to exact multiples of the resolution.

    Returns `(grid, diagnostics)` — diagnostics feeds the CLI's manifest.
    """
    b_cfg = cfg["boundary"]
    gdf = load_boundary(cfg, repo_root)
    n_features = len(gdf)

    crs = CRS.from_user_input(cfg["crs"])
    resolution = float(cfg["resolution_m"])

    gdf_proj = gdf.to_crs(crs)
    # total_bounds is robust to invalid input geometry; use it for the GRID
    # extent regardless of whether the union below needs repair first.
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
    """`build_grid`, reshaped to the flat-dict-return shape `run_stage` expects.

    `build_grid` returns `(Grid, diagnostics)` — the natural shape for a
    caller that wants the `Grid` object directly (every other terrain
    module does) — but `run_stage` (used uniformly across every stage so a
    manifest is written regardless of caller, see `run_stage`'s docstring)
    needs a single flat dict to spread into the manifest. This is that
    adapter, used by this module's own `main()` AND by `build.py`'s
    orchestrator, so grid.py gets the same guarantee every other stage
    does rather than being a special case.
    """
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
