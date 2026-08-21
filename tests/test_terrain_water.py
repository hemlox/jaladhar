"""Unit checks for the OSM standing-water source stage."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon, box

from jaladhar.terrain.water import WaterFetchError, fetch_osm_polygons, fetch_osm_water


def test_fetch_osm_water_keeps_only_clipped_polygons(monkeypatch) -> None:
    """If broken, line geometry or outside water would reach the shared reference file.

    Scope: one configured OSM response with one inside polygon, one line, and
    one outside polygon. This tests source filtering only; an Overpass call is
    intentionally not made in the unit suite.
    """
    response = gpd.GeoDataFrame(
        {"name": ["Inside", "line", "outside"]},
        geometry=[
            Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]),
            LineString([(0, 0), (2, 2)]),
            Polygon([(10, 10), (11, 10), (11, 11), (10, 11)]),
        ],
        crs="EPSG:4326",
    )
    monkeypatch.setattr(
        "jaladhar.terrain.water.ox.features_from_polygon", lambda *_args, **_kwargs: response
    )

    result = fetch_osm_water(
        query_polygon_wgs84=box(-1, -1, 12, 12),
        clip_polygon_wgs84=box(-1, -1, 3, 3),
        tags={"natural": ["water"]},
        timeout_s=1,
    )

    assert len(result) == 1
    assert result.iloc[0]["name"] == "Inside"
    assert result.geometry.iloc[0].equals(Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]))


def test_fetch_osm_water_rejects_empty_overpass_response(monkeypatch) -> None:
    """Red path: an empty response cannot become an empty reference layer."""
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    monkeypatch.setattr(
        "jaladhar.terrain.water.ox.features_from_polygon", lambda *_args, **_kwargs: empty
    )

    with pytest.raises(WaterFetchError, match="zero configured water features"):
        fetch_osm_water(box(0, 0, 1, 1), box(0, 0, 1, 1), {"natural": ["water"]}, 1)


def test_fetch_osm_polygons_labels_a_missing_quarry_source(monkeypatch) -> None:
    """Red path: a no-result quarry query cannot silently become no quarries."""
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    monkeypatch.setattr(
        "jaladhar.terrain.water.ox.features_from_polygon", lambda *_args, **_kwargs: empty
    )

    with pytest.raises(WaterFetchError, match="zero configured quarry features"):
        fetch_osm_polygons(
            box(0, 0, 1, 1),
            box(0, 0, 1, 1),
            {"landuse": ["quarry"]},
            1,
            source_name="quarry",
        )
