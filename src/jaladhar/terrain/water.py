"""This is a source-data stage, not a terrain approximation.  The canonical
flood storage. A failed Overpass query is fatal: an empty or synthetic"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import osmnx as ox
import typer
from shapely.geometry import box

from jaladhar.terrain.grid import build_grid, load_config, run_stage

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


class WaterFetchError(Exception):
    """OSM water acquisition failed or yielded no usable polygon geometry."""


def fetch_osm_polygons(
    query_polygon_wgs84: Any,
    clip_polygon_wgs84: Any,
    tags: dict[str, list[str]],
    timeout_s: int,
    source_name: str,
) -> gpd.GeoDataFrame:
    ox.settings.requests_timeout = timeout_s
    ox.settings.log_console = False
    try:
        features = ox.features_from_polygon(query_polygon_wgs84, tags=tags)
    except Exception as exc:
        raise WaterFetchError(
            f"Overpass {source_name}-polygon fetch failed: {type(exc).__name__}: {exc}"
        ) from exc
    if features.empty:
        raise WaterFetchError(f"Overpass returned zero configured {source_name} features")

    features = features[features.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if features.empty:
        raise WaterFetchError(
            f"Overpass returned {source_name} features, but none were polygon geometries"
        )
    features = gpd.clip(features, clip_polygon_wgs84)
    features = features[features.geometry.notna() & ~features.geometry.is_empty].copy()
    if features.empty:
        raise WaterFetchError(
            f"zero {source_name} polygons survived clipping to the buffered terrain domain"
        )
    return features


def fetch_osm_water(
    query_polygon_wgs84: Any,
    clip_polygon_wgs84: Any,
    tags: dict[str, list[str]],
    timeout_s: int,
) -> gpd.GeoDataFrame:
    water = fetch_osm_polygons(
        query_polygon_wgs84, clip_polygon_wgs84, tags, timeout_s, source_name="water"
    )

    if "name" not in water.columns:
        water["name"] = ""
    else:
        water["name"] = water["name"].fillna("").astype(str)
    return water


def build_water(cfg: dict[str, Any], repo_root: Path = REPO) -> dict[str, Any]:
    grid, grid_diag = build_grid(cfg, repo_root)
    buffered_grid = grid.buffered(float(cfg["dem"]["buffer_m"]))
    water_cfg = cfg["water"]
    buffered_box_projected = box(*buffered_grid.bounds)
    buffered_box_wgs84 = (
        gpd.GeoSeries([buffered_box_projected], crs=grid.crs).to_crs("EPSG:4326").iloc[0]
    )
    query_box = box(*buffered_box_wgs84.bounds)
    timeout_s = int(water_cfg["overpass_timeout_s"])
    sources = {
        "water": ("osm_water.gpkg", water_cfg["osm_tags"]),
        "quarry": ("osm_quarries.gpkg", water_cfg["quarry_tags"]),
        "landfill": ("osm_landfills.gpkg", water_cfg["landfill_tags"]),
    }

    raw_dir = repo_root / cfg["paths"]["raw_osm_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    feature_counts: dict[str, int] = {}
    for source_name, (filename, tags) in sources.items():
        if source_name == "water":
            features = fetch_osm_water(query_box, buffered_box_wgs84, tags, timeout_s)
        else:
            features = fetch_osm_polygons(
                query_box, buffered_box_wgs84, tags, timeout_s, source_name=source_name
            )
        output_path = raw_dir / filename
        features.to_file(output_path, driver="GPKG")
        feature_counts[source_name] = len(features)
        outputs[source_name] = str(output_path.relative_to(repo_root))

    return {
        "grid_diagnostics": grid_diag,
        "buffered_grid": buffered_grid.to_manifest_dict(),
        "source": "OpenStreetMap via Overpass",
        "source_tags": {name: tags for name, (_, tags) in sources.items()},
        "feature_counts": feature_counts,
        "outputs": outputs,
    }


@app.command()
def main(
    config: Path = typer.Option(
        REPO / "configs" / "domain_bengaluru.yaml", help="Path to domain config YAML"
    ),
    out: Path = typer.Option(
        REPO / "runs" / "terrain_water", help="Directory for the stage manifest"
    ),
) -> None:
    cfg = load_config(config)
    try:
        result = run_stage("phase1_terrain_water", build_water, cfg, config, REPO, out)
    except WaterFetchError as exc:
        typer.echo(f"\nFATAL: {exc}")
        raise typer.Exit(code=1) from exc
    for source_name, count in result["feature_counts"].items():
        output_path = result["outputs"][source_name]
        typer.echo(f"wrote {count:,} OSM {source_name} polygons to {output_path}")
    typer.echo(f"wrote {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
