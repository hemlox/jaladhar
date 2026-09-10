'''FABDEM is the PRIMARY source, Copernicus GLO-30 is the FALLBACK. Source order
is entirely config-driven (`dem.sources` in `configs/domain_bengaluru.yaml`)
— this module never hardcodes which source is preferred; it tries each
flag this module writes into its manifest, never on a hardcoded assumption:
winning source's licence, copied verbatim from the config entry that won —
stream to a `.part` file, rename on success (a partial file must never look
FIRST, and the source mosaic is reprojected directly into it in one pass —
never reproject the full source extent and then clip, which would waste a
Out of scope for this module (later, unbuilt modules): burning roads/
reported provenance."'''

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import requests
import typer
from rasterio.merge import merge as rio_merge
from rasterio.warp import Resampling, reproject

from jaladhar.terrain.grid import Grid, build_grid, load_config, run_stage

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


class DEMSourceError(Exception):
    """A configured DEM source could not be fully fetched or is unusable.
    Raised per-source so the caller can fall through to the next entry in
    `dem.sources`. If every source raises this, `fetch_dem` re-raises it and"""


def ensure_boundary(cfg: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Fetch and byte-verify the configured BBMP boundary before grid construction."""
    boundary_cfg = cfg["boundary"]
    required = [
        "path",
        "source_url",
        "source_name",
        "licence",
        "expected_sha256",
        "expected_size_bytes",
    ]
    missing = [key for key in required if key not in boundary_cfg]
    if missing:
        raise KeyError(f"boundary config is missing acquisition keys: {missing}")
    path = repo_root / boundary_cfg["path"]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        download_file(str(boundary_cfg["source_url"]), path)
    size = path.stat().st_size
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected_size = int(boundary_cfg["expected_size_bytes"])
    expected_digest = str(boundary_cfg["expected_sha256"])
    if size != expected_size or digest != expected_digest:
        raise ValueError(
            f"boundary byte contract mismatch for {path}: size={size}, sha256={digest}; "
            f"expected size={expected_size}, sha256={expected_digest}"
        )
    return {
        "path": str(path.relative_to(repo_root)),
        "source_name": boundary_cfg["source_name"],
        "source_url": boundary_cfg["source_url"],
        "licence": boundary_cfg["licence"],
        "size_bytes": size,
        "sha256": digest,
    }


def download_file(url: str, dest: Path, timeout: int = 300) -> tuple[str, int]:
    if dest.exists() and dest.stat().st_size > 0:
        try:
            head = requests.head(url, timeout=30, allow_redirects=True)
            remote_size = int(head.headers.get("Content-Length", 0))
        except Exception:
            remote_size = 0
        if remote_size > 0 and dest.stat().st_size == remote_size:
            return "have", dest.stat().st_size

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    if chunk:
                        fh.write(chunk)
        tmp.rename(dest)  # atomic: a partial file must never look complete
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise DEMSourceError(f"{url}: {type(e).__name__}: {e}") from e
    return "ok", dest.stat().st_size


def mosaic_and_reproject(
    tile_paths: list[Path],
    dst_grid: Grid,
    resampling: str,
    dst_nodata: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """never reproject the full source extent and clip afterward."""
    datasets = [rasterio.open(p) for p in tile_paths]
    try:
        tile_meta = [
            {
                "path": str(p),
                "native_crs": str(ds.crs),
                "native_shape": list(ds.shape),
                "native_nodata": ds.nodata,
                "native_dtype": ds.dtypes[0],
                "native_bounds": list(ds.bounds),
            }
            for p, ds in zip(tile_paths, datasets, strict=True)
        ]
        src_crs = datasets[0].crs
        src_nodata = datasets[0].nodata
        for ds in datasets[1:]:
            if ds.crs != src_crs:
                raise DEMSourceError(f"tile CRS mismatch: {ds.crs} vs {src_crs}")

        mosaic_arr, mosaic_transform = rio_merge(datasets)
    finally:
        for ds in datasets:
            ds.close()

    dst_array = np.full((dst_grid.height, dst_grid.width), dst_nodata, dtype=np.float32)
    reproject(
        source=mosaic_arr[0],
        destination=dst_array,
        src_transform=mosaic_transform,
        src_crs=src_crs,
        src_nodata=src_nodata,
        dst_transform=dst_grid.transform,
        dst_crs=dst_grid.crs,
        dst_nodata=dst_nodata,
        resampling=getattr(Resampling, resampling),
    )
    return dst_array, {"tiles": tile_meta, "src_crs": str(src_crs), "src_nodata": src_nodata}


def fetch_source(
    source_cfg: dict[str, Any],
    raw_dem_dir: Path,
    dst_grid: Grid,
    resampling: str,
    dst_nodata: float,
) -> tuple[np.ndarray, dict[str, Any], list[dict[str, Any]]]:
    """Download every tile for one configured DEM source, then mosaic+reproject.
    A source only wins if ALL its tiles download successfully — raises
    DEMSourceError otherwise so the caller falls through to the next"""
    name = source_cfg["name"]
    tile_paths: list[Path] = []
    attempts: list[dict[str, Any]] = []
    for tile in source_cfg["tiles"]:
        dest = raw_dem_dir / name / f"{tile['id']}.tif"
        try:
            status, size = download_file(tile["url"], dest)
        except DEMSourceError as e:
            attempts.append(
                {"source": name, "tile": tile["id"], "status": "FAILED", "error": str(e)}
            )
            raise
        attempts.append(
            {
                "source": name,
                "tile": tile["id"],
                "status": status,
                "bytes": size,
                "path": str(dest),
            }
        )
        tile_paths.append(dest)

    array, mosaic_meta = mosaic_and_reproject(tile_paths, dst_grid, resampling, dst_nodata)
    return array, mosaic_meta, attempts


def fetch_dem(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    """Fetch the DEM per config: try each source in order, mosaic, reproject, write.
    Returns a manifest-ready dict (everything except git_sha/wall_clock,
    which the CLI adds). Raises DEMSourceError if every configured source"""
    boundary = ensure_boundary(cfg, repo_root)
    grid, grid_diag = build_grid(cfg, repo_root)
    buffer_m = float(cfg["dem"]["buffer_m"])
    dst_grid = grid.buffered(buffer_m)

    resampling = cfg["dem"]["resampling"]
    nodata = float(cfg["dem"]["nodata_value"])
    raw_dem_dir = repo_root / cfg["paths"]["raw_dem_dir"]
    interim_dem_dir = repo_root / cfg["paths"]["interim_dem_dir"]

    all_attempts: list[dict[str, Any]] = []
    winning_source: dict[str, Any] | None = None
    array: np.ndarray | None = None
    mosaic_meta: dict[str, Any] | None = None

    for source_cfg in cfg["dem"]["sources"]:
        name = source_cfg["name"]
        try:
            array, mosaic_meta, attempts = fetch_source(
                source_cfg, raw_dem_dir, dst_grid, resampling, nodata
            )
            all_attempts.extend(attempts)
            winning_source = source_cfg
            break
        except DEMSourceError as e:
            all_attempts.append({"source": name, "status": "SOURCE_FAILED", "error": str(e)})
            continue

    if winning_source is None or array is None or mosaic_meta is None:
        tried = [s["name"] for s in cfg["dem"]["sources"]]
        raise DEMSourceError(
            f"No configured DEM source could be fetched (tried: {tried}). "
            "Per CLAUDE.md rule 1: stopping, not fabricating elevation data. "
            f"Attempt log: {all_attempts}"
        )

    interim_dem_dir.mkdir(parents=True, exist_ok=True)
    out_path = interim_dem_dir / f"dem_{grid.resolution:g}m_buffered.tif"
    profile = dst_grid.profile(dtype="float32", nodata=nodata)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(array, 1)

    # Smoke test (invariant 3's spirit): median elevation over the canonical
    # build.py's QC step, out of this session's scope). Catches wrong tile,
    # wrong units, and failed reprojection without false-firing on real relief.
    buf_cells = round(buffer_m / grid.resolution)
    interior = array[
        buf_cells : buf_cells + grid.height,
        buf_cells : buf_cells + grid.width,
    ]
    valid_mask = interior != nodata
    n_total = int(interior.size)
    n_valid = int(valid_mask.sum())
    n_nodata = n_total - n_valid
    if n_valid > 0:
        valid_vals = interior[valid_mask]
        elev_min, elev_median, elev_max = (
            float(valid_vals.min()),
            float(np.median(valid_vals)),
            float(valid_vals.max()),
        )
    else:
        elev_min = elev_median = elev_max = float("nan")

    band = cfg["dem"]["expected_median_elevation_m"]
    median_in_band = bool(n_valid > 0 and band[0] <= elev_median <= band[1])

    return {
        "boundary": boundary,
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "buffer_m": buffer_m,
        "buffered_grid": dst_grid.to_manifest_dict(),
        "resampling": resampling,
        "nodata_value": nodata,
        "source_attempts": all_attempts,
        "dem_source": winning_source["name"],
        "dem_is_dtm": winning_source["is_dtm"],
        "dem_licence": winning_source["licence"],
        "dem_licence_note": winning_source.get("licence_note"),
        "dem_native_resolution_m": winning_source["native_resolution_m"],
        "tiles_used": mosaic_meta["tiles"],
        "output_path": str(out_path.relative_to(repo_root)),
        "elevation_stats_bbox_interior": {
            "min_m": elev_min,
            "median_m": elev_median,
            "max_m": elev_max,
            "n_valid": n_valid,
            "n_nodata": n_nodata,
            "n_total": n_total,
        },
        "expected_median_band_m": band,
        "median_in_expected_band": median_in_band,
    }


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_dem", help="Directory to write the manifest into"
    ),
) -> None:
    cfg = load_config(config)
    try:
        result = run_stage("phase1_terrain_fetch_dem", fetch_dem, cfg, config, REPO, out)
    except DEMSourceError as e:
        typer.echo(f"\nFATAL: {e}")
        raise typer.Exit(code=1) from e

    typer.echo(f"DEM source: {result['dem_source']}   dem_is_dtm={result['dem_is_dtm']}")
    typer.echo(f"licence: {result['dem_licence']}")
    stats = result["elevation_stats_bbox_interior"]
    typer.echo(
        f"elevation (bbox interior): min={stats['min_m']:.1f} "
        f"median={stats['median_m']:.1f} max={stats['max_m']:.1f} m   "
        f"nodata={stats['n_nodata']}/{stats['n_total']}"
    )
    typer.echo(
        f"median in expected band {result['expected_median_band_m']}: "
        f"{result['median_in_expected_band']}"
    )
    typer.echo(f"wrote {result['output_path']}")
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
