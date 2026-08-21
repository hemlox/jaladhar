"""Manning's n roughness raster for the JALADHAR terrain pipeline.

Self-sufficient: runs its OWN OSM queries rather than depending on
`roads.py` or `buildings.py`'s output files. The plan lists roads/
buildings/roughness/drains as independent, parallelisable siblings with
no dependency between them — making roughness.py read another module's
interim output would silently create the ordering dependency the plan
explicitly rules out, and would break this module if it is ever run alone.

Config-driven throughout (CLAUDE.md: Manning's n is a Phase-3-calibrated
TUNABLE VECTOR, never a literal in code) — see `configs/domain_bengaluru.
yaml`'s `roughness:` section: the four Manning classes and their
[min, max] ranges (`manning_n`), the picked scalar `operating_point` per
class, the `landuse_classes` OSM-tag mapping, and the per-highway-type
`road_buffer_halfwidth_m` used to turn road CENTRELINES (OSM's road data
is overwhelmingly lines, not area polygons) into approximate paved-surface
polygons.

Class priority where layers overlap (rasterized low-to-high so the higher
class wins on shared cells, matching roads.py's deterministic-ordering
approach): vegetated_open < dense_urban < water < roads_paved. A road
running through a park or across a causeway is, physically, pavement at
that cell — the local surface, not the underlying landuse tag, is what a
flowing storm actually meets. Any cell no layer covers gets
`unclassified_default`, explicit and logged (invariant 6: never silently
zero, which blows up the ACC scheme's friction term).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import osmnx as ox
import rasterio
import typer
from rasterio.features import rasterize
from shapely.geometry import box

from jaladhar.terrain.grid import build_grid, load_config, run_stage

try:
    from osmnx._errors import InsufficientResponseError
except ImportError:  # pragma: no cover - defensive against an osmnx version bump

    class InsufficientResponseError(Exception):  # type: ignore[no-redef]
        pass


app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

# Rasterized in this order (lowest priority first) so a later layer's
# rasterize call overwrites an earlier one's cells where they overlap.
CLASS_PRIORITY = ["vegetated_open", "dense_urban", "water", "roads_paved"]


class RoughnessFetchError(Exception):
    """An OSM fetch failed or returned nothing usable for this layer.

    Per CLAUDE.md rule 1: stop and report — never fall back to a uniform/
    synthetic roughness raster silently. A landuse or road query returning
    zero features for a real city this size means something is wrong with
    the query, not that Bengaluru genuinely has none of that class.
    """


def fetch_road_polygons(
    query_polygon_wgs84: Any,
    union_wgs84: Any,
    highway_types: list[str],
    halfwidth_m: dict[str, float],
    grid_crs: Any,
    timeout_s: int,
) -> gpd.GeoDataFrame:
    """Independent highway=* query -> centrelines buffered into paved-surface polygons.

    A SEPARATE Overpass fetch from roads.py's (by design — see module
    docstring). Buffering happens in the metric CRS (buffer distances are
    metres; buffering directly in WGS84 degrees would be wrong).
    """
    ox.settings.requests_timeout = timeout_s
    ox.settings.log_console = False
    try:
        gdf = ox.features_from_polygon(query_polygon_wgs84, tags={"highway": True})
    except Exception as e:
        raise RoughnessFetchError(
            f"Overpass highway=* fetch failed: {type(e).__name__}: {e}"
        ) from e
    if gdf.empty:
        raise RoughnessFetchError("Overpass returned zero highway=* features")

    gdf = gdf[gdf["highway"].isin(highway_types)]
    gdf = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    if gdf.empty:
        raise RoughnessFetchError(f"no highway_types={highway_types} LineString features found")

    gdf = gpd.clip(gdf, union_wgs84)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    if gdf.empty:
        raise RoughnessFetchError("zero road features survived clipping to the BBMP polygon")

    gdf_proj = gdf.to_crs(grid_crs)
    default_hw = halfwidth_m["default"]
    widths = gdf_proj["highway"].map(lambda h: halfwidth_m.get(h, default_hw))
    gdf_proj["geometry"] = gdf_proj.geometry.buffer(widths, cap_style="flat")
    return gdf_proj


def fetch_landuse_class(
    query_polygon_wgs84: Any,
    union_wgs84: Any,
    tag_filter: dict[str, list[str]],
    grid_crs: Any,
    timeout_s: int,
) -> gpd.GeoDataFrame | None:
    """One Manning class's OSM polygons (e.g. dense_urban's landuse=[...] tags).

    Returns None (not an error) if this specific class has zero features —
    a real possibility for e.g. `water` in a landlocked bbox subset — the
    caller decides whether an entirely-empty CLASS is fatal; a query that
    errors outright, versus one that legitimately finds nothing, are
    different situations and must not be conflated.
    """
    ox.settings.requests_timeout = timeout_s
    ox.settings.log_console = False
    try:
        gdf = ox.features_from_polygon(query_polygon_wgs84, tags=tag_filter)
    except InsufficientResponseError:
        return None
    except Exception as e:
        raise RoughnessFetchError(
            f"Overpass fetch failed for tags={tag_filter}: {type(e).__name__}: {e}"
        ) from e
    if gdf.empty:
        return None
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if gdf.empty:
        return None
    gdf = gpd.clip(gdf, union_wgs84)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    if gdf.empty:
        return None
    return gdf.to_crs(grid_crs)


def build_roughness(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    """Fetch road+landuse polygons over the buffered domain, rasterize Manning's n by class priority."""
    grid, grid_diag = build_grid(cfg, repo_root)
    buffer_m = float(cfg["dem"]["buffer_m"])
    buffered_grid = grid.buffered(buffer_m)
    buf_cells = round(buffer_m / grid.resolution)
    ro_cfg = cfg["roughness"]
    roads_cfg = cfg["roads"]

    # Compute bounding box of the BUFFERED domain in WGS84
    buffered_box_proj = box(*buffered_grid.bounds)
    buffered_box_wgs84 = (
        gpd.GeoSeries([buffered_box_proj], crs=grid.crs).to_crs("EPSG:4326").iloc[0]
    )
    query_box = box(*buffered_box_wgs84.bounds)

    operating_point = ro_cfg["operating_point"]
    unclassified_default = float(ro_cfg["manning_n"]["unclassified_default"])
    n_raster_buffered = np.full(
        (buffered_grid.height, buffered_grid.width), unclassified_default, dtype=np.float32
    )

    class_cell_counts: dict[str, int] = {}
    class_feature_counts: dict[str, int] = {}
    classes_with_zero_features: list[str] = []

    for cls in CLASS_PRIORITY:
        if cls == "roads_paved":
            gdf_proj = fetch_road_polygons(
                query_box,
                buffered_box_wgs84,
                roads_cfg["highway_types"],
                ro_cfg["road_buffer_halfwidth_m"],
                grid.crs,
                ro_cfg["overpass_timeout_s"],
            )
        else:
            tag_filter = ro_cfg["landuse_classes"][cls]
            gdf_proj = fetch_landuse_class(
                query_box, buffered_box_wgs84, tag_filter, grid.crs, ro_cfg["overpass_timeout_s"]
            )
            if gdf_proj is None:
                classes_with_zero_features.append(cls)
                class_feature_counts[cls] = 0
                class_cell_counts[cls] = 0
                continue

        class_feature_counts[cls] = len(gdf_proj)
        value = np.float32(operating_point[cls])
        class_mask = rasterize(
            [(geom, 1) for geom in gdf_proj.geometry],
            out_shape=(buffered_grid.height, buffered_grid.width),
            transform=buffered_grid.transform,
            fill=0,
            all_touched=False,
            dtype="uint8",
        )
        n_raster_buffered = np.where(class_mask == 1, value, n_raster_buffered)
        class_cell_counts[cls] = int(class_mask.sum())

    if len(classes_with_zero_features) == len(CLASS_PRIORITY) - 1:  # only roads_paved survived
        raise RoughnessFetchError(
            f"every landuse class ({classes_with_zero_features}) returned zero features — "
            "suspect a tag-mapping or query error, not that Bengaluru genuinely has none "
            "of dense_urban/vegetated_open/water"
        )

    n_min, n_max = float(n_raster_buffered.min()), float(n_raster_buffered.max())
    if n_min <= 0.0:
        raise RoughnessFetchError(
            f"Manning's n raster contains a value <= 0 (min={n_min}) — this would blow up "
            "the ACC scheme's friction term; refusing to write it"
        )

    # Crop to canonical grid
    n_raster_canonical = n_raster_buffered[
        buf_cells : buf_cells + grid.height, buf_cells : buf_cells + grid.width
    ]

    interim_dir = repo_root / cfg["paths"]["interim_terrain_dir"]
    interim_dir.mkdir(parents=True, exist_ok=True)

    # Write buffered raster
    buffered_raster_path = interim_dir / "manning_n_buffered.tif"
    with rasterio.open(buffered_raster_path, "w", **buffered_grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(n_raster_buffered, 1)

    # Write canonical raster
    raster_path = interim_dir / "manning_n.tif"
    with rasterio.open(raster_path, "w", **grid.profile(dtype="float32", nodata=None)) as dst:
        dst.write(n_raster_canonical, 1)

    unclassified_cells = int((n_raster_canonical == np.float32(unclassified_default)).sum())

    return {
        "grid_diagnostics": grid_diag,
        "canonical_grid": grid.to_manifest_dict(),
        "buffered_grid": buffered_grid.to_manifest_dict(),
        "class_priority_low_to_high": CLASS_PRIORITY,
        "operating_point": operating_point,
        "unclassified_default": unclassified_default,
        "class_feature_counts": class_feature_counts,
        "class_cell_counts": class_cell_counts,
        "classes_with_zero_features": classes_with_zero_features,
        "unclassified_cells": unclassified_cells,
        "unclassified_cell_fraction": unclassified_cells / (grid.width * grid.height),
        "manning_n_min": n_min,
        "manning_n_max": n_max,
        "raster_path": str(raster_path.relative_to(repo_root)),
    }


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_roughness", help="Directory to write the manifest into"
    ),
) -> None:
    """Fetch road+landuse polygons, rasterize Manning's n by class priority."""
    cfg = load_config(config)
    try:
        result = run_stage("phase1_terrain_roughness", build_roughness, cfg, config, REPO, out)
    except RoughnessFetchError as e:
        typer.echo(f"\nFATAL: {e}")
        raise typer.Exit(code=1) from e

    for cls in CLASS_PRIORITY:
        typer.echo(
            f"  {cls:14} n={result['operating_point'][cls]:.3f}  "
            f"features={result['class_feature_counts'][cls]:6}  "
            f"cells={result['class_cell_counts'][cls]:9,}"
        )
    typer.echo(
        f"  unclassified   n={result['unclassified_default']:.3f}  "
        f"cells={result['unclassified_cells']:9,} "
        f"({100 * result['unclassified_cell_fraction']:.1f}%)"
    )
    if result["classes_with_zero_features"]:
        typer.echo(f"WARNING zero features for: {result['classes_with_zero_features']}")
    typer.echo(f"n range: [{result['manning_n_min']:.4f}, {result['manning_n_max']:.4f}]")
    typer.echo(f"wrote {result['raster_path']}")
    typer.echo(f"\nwrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
