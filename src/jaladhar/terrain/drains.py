"""Drain-capacity PRIOR for the JALADHAR terrain pipeline.

Bengaluru's actual stormwater drain network is NOT open data (PROMPT.md
§14.2 — the largest single source of error, and a data-availability
problem, not a modelling one). Everything this module writes is an
EXPLICIT, DOCUMENTED, ARBITRARY starting point for Phase 3 gradient-descent
calibration against observed flooding, never a measurement — see
`configs/domain_bengaluru.yaml`'s `drains:` section, which spells out why
setting a documented model-parameter starting point is what PROMPT.md
§6.1 item 5 itself asks for ("a per-cell removal rate capped by an
ASSUMED capacity"), not a CLAUDE.md rule 1 violation (that rule prohibits
fabricating and presenting DATA/observations as real, which this is not).

Two network representations supported:

  1. BBMP SWD Rajakaluve Network 2022 (Primary, Secondary, Tertiary — 6,839
     features, 1,988.47 km total length, Public Domain via OpenCity, 2.33x
     the OSM network). 2D horizontal centrelines only.
  2. OSM waterway=drain|ditch|stream lines (853.44 km).

Ingredients:
  - REAL: distance to the nearest mapped drain feature (genuine geometry, not
    invented).
  - ARBITRARY (documented as such throughout): a uniform baseline capacity
    and an exponential decay length, both explicit Phase-3 calibration
    targets. The config's `baseline_capacity_by_landuse_mm_per_hr` is
    intentionally the SAME value across every class — differentiating them
    without a source to justify the differences would imply false
    precision we don't have.
  - CLASS-INDEPENDENT PRIOR: Although the BBMP dataset differentiates
    Primary / Secondary / Tertiary classes, open administrative data provides
    no standardized citywide cross-sectional dimensions, depths, or hydraulic
    capacities by class (dimensions are site-specific per DPR). Per CLAUDE.md
    Rule 1, no arbitrary capacity ratios are fabricated between classes; a
    single class-independent uniform prior is applied.

Distance computed via a raster Euclidean distance transform (`scipy.ndimage.
distance_transform_edt`), not a per-cell geometric query against the line
network.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import fiona
import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
import rasterio
import typer
from rasterio.features import rasterize
from scipy.ndimage import distance_transform_edt
from shapely.geometry import box

from jaladhar.terrain.grid import build_grid, load_boundary, load_config, run_stage

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

# Ensure fiona can read KML and LIBKML layers
fiona.drvsupport.supported_drivers["KML"] = "rw"
fiona.drvsupport.supported_drivers["LIBKML"] = "rw"


class DrainFetchError(Exception):
    """The drain waterway fetch/load failed or returned nothing usable.

    Per CLAUDE.md rule 1: stop and report — never fall back to a uniform
    (distance-blind) capacity raster silently.
    """


def _publish_canonical_artifacts(source_artifacts: dict[Path, Path]) -> None:
    """Publish source-specific outputs at the stable paths consumers load."""
    for source_path, canonical_path in source_artifacts.items():
        if source_path == canonical_path:
            continue
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=canonical_path.parent,
                prefix=f".{canonical_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
            shutil.copy2(source_path, temp_path)
            temp_path.replace(canonical_path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def load_bbmp_waterways(
    raw_dir: Path,
    grid_crs: Any,
    clip_box_proj: Any | None = None,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Load BBMP SWD Rajakaluve Primary, Secondary, and Tertiary KML networks.

    Returns the combined reprojected GeoDataFrame and a detailed class breakdown
    dictionary with feature counts and lengths.
    """
    classes = [
        ("primary", "primary_drains_2022.kml"),
        ("secondary", "secondary_drains_2022.kml"),
        ("tertiary", "tertiary_drains_2022.kml"),
    ]

    gdfs: list[gpd.GeoDataFrame] = []
    class_stats: dict[str, Any] = {}

    for drain_class, filename in classes:
        kml_path = raw_dir / filename
        if not kml_path.exists():
            raise DrainFetchError(f"BBMP drain KML missing: {kml_path}")

        try:
            gdf = gpd.read_file(kml_path, engine="fiona")
        except Exception as e:
            raise DrainFetchError(f"Failed to read BBMP KML {kml_path}: {type(e).__name__}: {e}") from e

        n_raw = len(gdf)
        gdf = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
        gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
        n_valid = len(gdf)

        if n_valid == 0:
            raise DrainFetchError(f"No valid LineString/MultiLineString geometries in {kml_path}")

        gdf_proj = gdf.to_crs(grid_crs)
        if clip_box_proj is not None:
            gdf_proj = gpd.clip(gdf_proj, clip_box_proj)
            gdf_proj = gdf_proj[~gdf_proj.geometry.is_empty & gdf_proj.geometry.notna()]

        length_m = float(gdf_proj.geometry.length.sum())
        gdf_proj["drain_class"] = drain_class
        if "Name" in gdf_proj.columns:
            gdf_proj["name"] = gdf_proj["Name"].astype(str)
        elif "name" not in gdf_proj.columns:
            gdf_proj["name"] = f"{drain_class}_drain"

        keep_cols = [c for c in ["drain_class", "name", "geometry"] if c in gdf_proj.columns]
        gdf_proj = gdf_proj[keep_cols]
        gdfs.append(gdf_proj)

        class_stats[drain_class] = {
            "features_raw": n_raw,
            "features_valid": n_valid,
            "features_clipped": len(gdf_proj),
            "length_m": round(length_m, 2),
            "length_km": round(length_m / 1000.0, 3),
            "capacity_assignment": (
                "Class-independent uniform prior (no standardized cross-sectional dimensions in 2D centerline dataset)"
            ),
        }

    combined_gdf = pd.concat(gdfs, ignore_index=True)
    total_len_m = float(combined_gdf.geometry.length.sum())

    class_stats["total"] = {
        "features_total": len(combined_gdf),
        "total_length_m": round(total_len_m, 2),
        "total_length_km": round(total_len_m / 1000.0, 3),
    }

    return combined_gdf, class_stats


def fetch_waterways(
    query_polygon_wgs84: Any,
    union_wgs84: Any,
    waterway_tags: list[str],
    grid_crs: Any,
    timeout_s: int,
) -> gpd.GeoDataFrame:
    """OSM waterway=drain|ditch|stream lines within the BBMP polygon (legacy OSM source)."""
    ox.settings.requests_timeout = timeout_s
    ox.settings.log_console = False
    try:
        gdf = ox.features_from_polygon(query_polygon_wgs84, tags={"waterway": waterway_tags})
    except Exception as e:
        raise DrainFetchError(f"Overpass waterway=* fetch failed: {type(e).__name__}: {e}") from e
    if gdf.empty:
        raise DrainFetchError(f"Overpass returned zero waterway={waterway_tags} features")

    gdf = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    if gdf.empty:
        raise DrainFetchError("features found, but none were LineString/MultiLineString")

    gdf = gpd.clip(gdf, union_wgs84)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    if gdf.empty:
        raise DrainFetchError("zero waterway features survived clipping to the BBMP polygon")
    return gdf.to_crs(grid_crs)


def _baseline_value(by_landuse: dict[str, float]) -> float:
    """The single uniform baseline value — see module docstring for why."""
    values = set(by_landuse.values())
    if len(values) != 1:
        raise ValueError(
            f"drains.baseline_capacity_by_landuse_mm_per_hr is no longer uniform "
            f"({by_landuse}) — this module's flat-value shortcut is now stale; "
            "implement a real landuse classification pass (mirroring roughness.py) "
            "before using per-class values."
        )
    return values.pop()


def build_drains(
    cfg: dict[str, Any],
    repo_root: Path = REPO,
    source: str | None = None,
) -> dict[str, Any]:
    """Rebuild distance-to-drain and drain capacity rasters from the BBMP or OSM drain network.

    Writes new BBMP-derived rasters alongside existing OSM rasters (without overwriting).
    """
    grid, grid_diag = build_grid(cfg, repo_root)
    buffer_m = float(cfg["dem"]["buffer_m"])
    buffered_grid = grid.buffered(buffer_m)
    buf_cells = round(buffer_m / grid.resolution)
    d_cfg = cfg["drains"]

    chosen_source = source or d_cfg.get("source", "bbmp")
    buffered_box_proj = box(*buffered_grid.bounds)

    interim_dir = repo_root / cfg["paths"]["interim_terrain_dir"]
    interim_dir.mkdir(parents=True, exist_ok=True)
    processed_dir = repo_root / cfg["paths"]["processed_dir"]
    processed_dir.mkdir(parents=True, exist_ok=True)
    processed_buf_dir = processed_dir / "buffered"
    processed_buf_dir.mkdir(parents=True, exist_ok=True)

    if chosen_source == "bbmp":
        bbmp_dir = repo_root / d_cfg.get("bbmp_raw_dir", "data/raw/bbmp_drains")
        gdf_proj, class_stats = load_bbmp_waterways(
            raw_dir=bbmp_dir,
            grid_crs=grid.crs,
            clip_box_proj=buffered_box_proj,
        )
        n_features = len(gdf_proj)
        source_label = "bbmp_rajakaluve_2022"
        source_description = (
            "BBMP Stormwater Drains (SWD) Rajakaluve network 2022 via OpenCity (Public Domain)"
        )
        total_len_m = class_stats["total"]["total_length_m"]
        total_len_km = class_stats["total"]["total_length_km"]
        waterways_filename = "waterways_bbmp.gpkg"
        dist_filename = "distance_to_drain_bbmp.tif"
        dist_buf_filename = "distance_to_drain_bbmp_buffered.tif"
        cap_filename = "drain_capacity_bbmp.tif"
        cap_buf_filename = "drain_capacity_bbmp_buffered.tif"
    elif chosen_source == "osm":
        buffered_box_wgs84 = (
            gpd.GeoSeries([buffered_box_proj], crs=grid.crs).to_crs("EPSG:4326").iloc[0]
        )
        query_box = box(*buffered_box_wgs84.bounds)
        gdf_proj = fetch_waterways(
            query_box,
            buffered_box_wgs84,
            d_cfg["waterway_tags"],
            grid.crs,
            d_cfg["overpass_timeout_s"],
        )
        n_features = len(gdf_proj)
        source_label = "osm_overpass_waterways"
        source_description = "OpenStreetMap waterway=drain|ditch|stream features via Overpass"
        total_len_m = float(gdf_proj.geometry.length.sum())
        total_len_km = round(total_len_m / 1000.0, 3)
        class_stats = {
            "osm": {
                "features": n_features,
                "length_m": round(total_len_m, 2),
                "length_km": total_len_km,
            },
            "total": {
                "features_total": n_features,
                "total_length_m": round(total_len_m, 2),
                "total_length_km": total_len_km,
            },
        }
        waterways_filename = "waterways_osm.gpkg"
        dist_filename = "distance_to_drain_osm.tif"
        dist_buf_filename = "distance_to_drain_osm_buffered.tif"
        cap_filename = "drain_capacity_osm.tif"
        cap_buf_filename = "drain_capacity_osm_buffered.tif"
    else:
        raise ValueError(f"Unknown drain source: {chosen_source!r}. Must be 'bbmp' or 'osm'.")

    # Rasterize on the BUFFERED grid
    drain_mask_buffered = rasterize(
        [(geom, 1) for geom in gdf_proj.geometry],
        out_shape=(buffered_grid.height, buffered_grid.width),
        transform=buffered_grid.transform,
        fill=0,
        all_touched=True,
        dtype="uint8",
    )
    n_drain_cells_buffered = int(drain_mask_buffered.sum())
    if n_drain_cells_buffered == 0:
        raise DrainFetchError(
            f"{n_features} waterway features loaded, but rasterized mask has zero drain cells."
        )

    # distance_transform_edt on the BUFFERED grid
    distance_m_buffered = distance_transform_edt(
        drain_mask_buffered == 0, sampling=(grid.resolution, grid.resolution)
    ).astype(np.float32)

    baseline = _baseline_value(d_cfg["baseline_capacity_by_landuse_mm_per_hr"])
    decay_m = float(d_cfg["distance_decay"]["decay_m"])
    if d_cfg["distance_decay"]["functional_form"] != "exponential":
        raise ValueError(
            f"drains.distance_decay.functional_form="
            f"{d_cfg['distance_decay']['functional_form']!r} not implemented (only 'exponential' is)"
        )
    capacity_buffered = (baseline * np.exp(-distance_m_buffered / decay_m)).astype(np.float32)

    # Crop to canonical grid
    distance_m_canonical = distance_m_buffered[
        buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width
    ]
    capacity_canonical = capacity_buffered[
        buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width
    ]
    drain_mask_canonical = drain_mask_buffered[
        buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width
    ]
    n_drain_cells_canonical = int((drain_mask_canonical == 1).sum())

    # Write vector GeoPackage
    waterways_path = interim_dir / waterways_filename
    gdf_proj.to_file(waterways_path, driver="GPKG")

    # Write interim buffered rasters
    buffered_dist_path = interim_dir / dist_buf_filename
    with rasterio.open(
        buffered_dist_path, "w", **buffered_grid.profile(dtype="float32", nodata=None)
    ) as dst:
        dst.write(distance_m_buffered, 1)

    buffered_capacity_path = interim_dir / cap_buf_filename
    with rasterio.open(
        buffered_capacity_path, "w", **buffered_grid.profile(dtype="float32", nodata=None)
    ) as dst:
        dst.write(capacity_buffered, 1)

    # Write interim canonical rasters
    dist_path = interim_dir / dist_filename
    with rasterio.open(dist_path, "w", **grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(distance_m_canonical, 1)

    capacity_path = interim_dir / cap_filename
    with rasterio.open(capacity_path, "w", **grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(capacity_canonical, 1)

    # Also write to processed/ and processed/buffered/ (side-by-side)
    proc_dist_path = processed_dir / dist_filename
    with rasterio.open(proc_dist_path, "w", **grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(distance_m_canonical, 1)

    proc_cap_path = processed_dir / cap_filename
    with rasterio.open(proc_cap_path, "w", **grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(capacity_canonical, 1)

    proc_buf_dist_path = processed_buf_dir / dist_filename
    with rasterio.open(
        proc_buf_dist_path, "w", **buffered_grid.profile(dtype="float32", nodata=None)
    ) as dst:
        dst.write(distance_m_buffered, 1)

    proc_buf_cap_path = processed_buf_dir / cap_filename
    with rasterio.open(
        proc_buf_cap_path, "w", **buffered_grid.profile(dtype="float32", nodata=None)
    ) as dst:
        dst.write(capacity_buffered, 1)

    # Source-specific files remain the immutable evidence. Canonical names are
    # the configured source's active view for conditioning and final assembly.
    _publish_canonical_artifacts(
        {
            waterways_path: interim_dir / "waterways.gpkg",
            dist_path: interim_dir / "distance_to_drain.tif",
            buffered_dist_path: interim_dir / "distance_to_drain_buffered.tif",
            capacity_path: interim_dir / "drain_capacity.tif",
            buffered_capacity_path: interim_dir / "drain_capacity_buffered.tif",
            proc_dist_path: processed_dir / "distance_to_drain.tif",
            proc_cap_path: processed_dir / "drain_capacity.tif",
            proc_buf_dist_path: processed_buf_dir / "distance_to_drain.tif",
            proc_buf_cap_path: processed_buf_dir / "drain_capacity.tif",
        }
    )

    # Compute boundary-masked statistics if boundary is available
    dist_mean_bbmp = None
    dist_median_bbmp = None
    try:
        boundary_gdf = load_boundary(cfg)
        union_geom = (
            boundary_gdf.to_crs(grid.crs).union_all()
            if hasattr(boundary_gdf, "union_all")
            else boundary_gdf.to_crs(grid.crs).unary_union
        )
        bbmp_mask = (
            rasterize(
                [(union_geom, 1)],
                out_shape=(grid.height, grid.width),
                transform=grid.transform,
                fill=0,
                dtype="uint8",
            )
            == 1
        )
        dist_mean_bbmp = float(distance_m_canonical[bbmp_mask].mean())
        dist_median_bbmp = float(np.median(distance_m_canonical[bbmp_mask]))
    except Exception:
        pass

    # Compute GT point distance statistics
    gt_mean_dist = None
    gt_median_dist = None
    gt_csv_path = repo_root / "data/raw/groundtruth/sept2022_points.csv"
    if gt_csv_path.exists():
        try:
            gt_df = pd.read_csv(gt_csv_path)
            gt_gdf = gpd.GeoDataFrame(
                gt_df,
                geometry=gpd.points_from_xy(gt_df.lon, gt_df.lat),
                crs="EPSG:4326",
            ).to_crs(grid.crs)
            coords = [(pt.x, pt.y) for pt in gt_gdf.geometry]
            with rasterio.open(dist_path) as ds:
                gt_samples = [float(val[0]) for val in ds.sample(coords)]
            gt_mean_dist = float(np.mean(gt_samples))
            gt_median_dist = float(np.median(gt_samples))
        except Exception:
            pass

    # Distance buffer coverage table
    total_cells = grid.height * grid.width
    thresholds = [0, 50, 100, 200, 500, 1000]
    coverage_table: dict[str, Any] = {}
    for t in thresholds:
        cnt = int((distance_m_canonical <= t).sum())
        pct = float((cnt / total_cells) * 100.0)
        coverage_table[f"le_{t}m"] = {"cells": cnt, "percent": round(pct, 3)}

    return {
        "source": source_label,
        "source_description": source_description,
        "feature_count": n_features,
        "total_length_m": total_len_m,
        "total_length_km": total_len_km,
        "crs": str(grid.crs),
        "class_breakdown": class_stats,
        "capacity_status": "Phase 1 PRIOR, never a measurement (assumed uncalibrated)",
        "is_prior": True,
        "assumed_uncalibrated": bool(d_cfg.get("assumed_uncalibrated", True)),
        "capacity_assignment_note": (
            f"Class-independent uniform prior ({baseline:.1f} mm/hr baseline, "
            f"{decay_m:.0f}m exponential decay). "
            "BBMP 2D centerline dataset does not contain cross-sectional geometry or invert levels; "
            "fabricating class-differentiated capacities without measured channel dimensions or published "
            "design standards would violate CLAUDE.md Rule 1."
        ),
        "baseline_capacity_mm_per_hr": baseline,
        "distance_decay_m": decay_m,
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "buffered_grid": buffered_grid.to_manifest_dict(),
        "n_features": n_features,
        "n_drain_cells": n_drain_cells_canonical,
        "n_drain_cells_buffered": n_drain_cells_buffered,
        "distance_m_min": float(distance_m_canonical.min()),
        "distance_m_max": float(distance_m_canonical.max()),
        "distance_m_mean": float(distance_m_canonical.mean()),
        "distance_m_median": float(np.median(distance_m_canonical)),
        "distance_m_mean_bbmp_boundary": dist_mean_bbmp,
        "distance_m_median_bbmp_boundary": dist_median_bbmp,
        "gt_points_mean_distance_m": gt_mean_dist,
        "gt_points_median_distance_m": gt_median_dist,
        "coverage_thresholds": coverage_table,
        "capacity_min_mm_per_hr": float(capacity_canonical.min()),
        "capacity_max_mm_per_hr": float(capacity_canonical.max()),
        "waterways_vector_path": str(waterways_path.relative_to(repo_root)),
        "canonical_waterways_vector_path": str(
            (interim_dir / "waterways.gpkg").relative_to(repo_root)
        ),
        "distance_path": str(dist_path.relative_to(repo_root)),
        "buffered_distance_path": str(buffered_dist_path.relative_to(repo_root)),
        "capacity_path": str(capacity_path.relative_to(repo_root)),
        "buffered_capacity_path": str(buffered_capacity_path.relative_to(repo_root)),
        "distance_to_drain_path": str(dist_path.relative_to(repo_root)),
        "drain_capacity_path": str(capacity_path.relative_to(repo_root)),
        "distance_to_drain_bbmp_path": str(dist_path.relative_to(repo_root)),
        "drain_capacity_bbmp_path": str(capacity_path.relative_to(repo_root)),
        "canonical_distance_to_drain_path": str(
            (interim_dir / "distance_to_drain.tif").relative_to(repo_root)
        ),
        "canonical_drain_capacity_path": str(
            (interim_dir / "drain_capacity.tif").relative_to(repo_root)
        ),
    }


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_drains", help="Directory to write the manifest into"
    ),
    source: str = typer.Option(
        "bbmp", help="Drain network source: 'bbmp' (2022 SWD KMLs) or 'osm' (Overpass)"
    ),
) -> None:
    """Fetch/load waterways, compute a distance-decayed drain-capacity PRIOR (not a measurement)."""
    cfg = load_config(config)

    def _runner(c: dict[str, Any], r: Path) -> dict[str, Any]:
        return build_drains(c, repo_root=r, source=source)

    try:
        result = run_stage("phase1_terrain_drains", _runner, cfg, config, REPO, out)
    except (DrainFetchError, ValueError) as e:
        typer.echo(f"\nFATAL: {e}")
        raise typer.Exit(code=1) from e

    typer.echo(
        f"Drain source: {result['source']} ({result['source_description']})\n"
        f"Waterway features: {result['n_features']} | Total length: {result['total_length_km']:.2f} km\n"
        f"Drain cells on grid: {result['n_drain_cells']:,} (canonical) / {result['n_drain_cells_buffered']:,} (buffered)"
    )
    typer.echo(
        f"Distance to nearest drain (domain-wide full grid): "
        f"min={result['distance_m_min']:.1f}m mean={result['distance_m_mean']:.1f}m median={result['distance_m_median']:.1f}m max={result['distance_m_max']:.1f}m"
    )
    if result["distance_m_mean_bbmp_boundary"] is not None:
        typer.echo(
            f"Distance within BBMP municipal boundary (717 km²): "
            f"mean={result['distance_m_mean_bbmp_boundary']:.1f}m median={result['distance_m_median_bbmp_boundary']:.1f}m"
        )
    if result["gt_points_mean_distance_m"] is not None:
        typer.echo(
            f"Distance at 24 Ground-Truth Points: "
            f"mean={result['gt_points_mean_distance_m']:.1f}m median={result['gt_points_median_distance_m']:.1f}m"
        )
    typer.echo(
        f"Capacity prior [{result['capacity_status']}]:\n"
        f"  baseline={result['baseline_capacity_mm_per_hr']:.1f} mm/hr | decay={result['distance_decay_m']:.0f}m\n"
        f"  range=[{result['capacity_min_mm_per_hr']:.3e}, {result['capacity_max_mm_per_hr']:.3f}] mm/hr"
    )
    typer.echo(
        f"Wrote vector: {result['waterways_vector_path']}\n"
        f"Wrote rasters: {result['distance_to_drain_path']}, {result['drain_capacity_path']}"
    )
    typer.echo(f"\nWrote manifest: {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
