"""Synthetic checks for Phase 3 ground-truth scoring eligibility.

Scope: date eligibility and snap-distance rejection only. These checks do not
claim that the realized September replay or road-network snapping is valid.
"""

import numpy as np
import pytest
import rasterio
from scipy.spatial import cKDTree

from jaladhar.validation.event_replay import groundtruth_road_snap_distances
from jaladhar.validation.groundtruth import (
    GroundTruthPoint,
    resolve_groundtruth_scoring_config,
    select_event_groundtruth,
)
from jaladhar.validation.segment_validation import (
    _resolve_segment_groundtruth_config,
    resolve_segment_validation_config,
    snap_points_to_segments,
)


def _point(point_id: str, observed_date: str) -> GroundTruthPoint:
    return GroundTruthPoint(
        id=point_id,
        location_name=point_id,
        lat=13.0,
        lon=77.6,
        geolocation_method="synthetic_test_fixture",
        geolocation_confidence="test",
        observed_date=observed_date,
        depth_band_low_m=None,
        depth_band_high_m=None,
        depth_cue="",
        depth_confidence="",
        source_url="https://example.invalid/test",
        source_publication_date="2022-09-06",
        source_quote="synthetic unit-test record only",
        retrieved_at_utc="2026-08-20T00:00:00Z",
        in_bbmp_kml=False,
        notes="synthetic unit-test record",
    )


def test_event_selection_excludes_records_outside_replay_dates() -> None:
    """If broken, an Aug 30 ID appears in the Sep 4--5 scoring input.

    Scope: three synthetic dates spanning both boundaries. Red mutation
    demonstrated: replacing the selector body with ``return list(points), []``
    admitted GT_14 and failed the eligible-ID assertion.
    """
    points = [
        _point("GT_14", "2022-08-30"),
        _point("GT_START", "2022-09-04"),
        _point("GT_END", "2022-09-05"),
    ]

    eligible, rejected = select_event_groundtruth(points, "2022-09-04", "2022-09-05")

    assert [point.id for point in eligible] == ["GT_START", "GT_END"]
    assert rejected == [
        {
            "id": "GT_14",
            "observed_date": "2022-08-30",
            "reason": "observed_date_outside_event_window",
            "event_window_start": "2022-09-04",
            "event_window_end": "2022-09-05",
        }
    ]


def test_snap_threshold_rejects_over_limit_with_explicit_reason(monkeypatch) -> None:
    """If broken, a point beyond the configured distance gets a scoreable ID.

    Scope: two synthetic projected points at 10 m and 11 m from one road cell.
    Red mutation demonstrated: changing ``> max_distance_m`` to ``>=`` rejects
    the boundary point and fails this check.
    """
    class IdentityTransformer:
        def transform(self, x: float, y: float) -> tuple[float, float]:
            return x, y

    monkeypatch.setattr(
        "jaladhar.validation.segment_validation.pyproj.Transformer.from_crs",
        lambda *args, **kwargs: IdentityTransformer(),
    )
    tree = cKDTree(np.array([[0.0, 0.0]]))
    r_segs = np.array([7], dtype=np.int32)

    _, distances, details = snap_points_to_segments(
        [(10.0, 0.0), (11.0, 0.0)],
        tree,
        r_segs,
        max_distance_m=10.0,
        point_ids=["AT_LIMIT", "OVER_LIMIT"],
    )

    assert distances.tolist() == [10.0, 11.0]
    assert details[0]["eligible_for_scoring"] is True
    assert details[0]["rejection_reason"] is None
    assert details[1]["eligible_for_scoring"] is False
    assert details[1]["rejection_reason"] == "snap_distance_exceeds_configured_max"
    assert details[1]["id"] == "OVER_LIMIT"


def test_snap_threshold_is_resolved_from_config() -> None:
    """If broken, changing YAML-equivalent input does not change the resolved limit.

    Scope: startup resolution of all three scoring keys. Red mutation
    demonstrated: returning a literal 110.0 fails the 7.5 m assertion.
    """
    start, end, max_snap_distance_m = resolve_groundtruth_scoring_config(
        {
            "scoring_event_window_start": "2022-09-04",
            "scoring_event_window_end": "2022-09-05",
            "max_snap_distance_m": 7.5,
        }
    )

    assert (start, end) == ("2022-09-04", "2022-09-05")
    assert max_snap_distance_m == 7.5


def test_segment_points_csv_is_resolved_from_config(tmp_path) -> None:
    """If broken, segment scoring silently returns to the old hardcoded CSV.

    Scope: startup resolution only; no road or replay artifacts are loaded.
    """
    cfg = {
        "groundtruth": {
            "scoring_event_window_start": "2022-09-04",
            "scoring_event_window_end": "2022-09-05",
            "max_snap_distance_m": 110.0,
            "points_csv": "fixtures/alternate.csv",
        }
    }

    *_, points_csv = _resolve_segment_groundtruth_config(cfg, tmp_path)
    assert points_csv == tmp_path / "fixtures/alternate.csv"

    del cfg["groundtruth"]["points_csv"]
    with pytest.raises(ValueError, match="groundtruth.points_csv is required"):
        _resolve_segment_groundtruth_config(cfg, tmp_path)


def test_segment_preflight_aggregates_later_sar_and_terrain_keys(tmp_path) -> None:
    """Missing late-use paths must fail before density computation or scoring."""
    with pytest.raises(KeyError, match="pre_scene_tif"):
        resolve_segment_validation_config(
            {
                "groundtruth": {"road_segment_id_path": "roads.tif", "bbmp_kml_dir": "bbmp"},
                "density_stratification": {"output_density_classes_path": "density.tif"},
                "segment_validation": {
                    "roads_segment_lookup_path": "roads.csv",
                    "underpass_register_csv": "underpasses.csv",
                },
                "sar_water_classifier": {
                    "flood_scene_tif": "flood.tif",
                    "flood_calibration_xml": "flood.xml",
                    "basin_class_path": "basin.tif",
                },
            },
            tmp_path,
        )


def test_point_scoring_snap_distance_uses_realized_road_cells() -> None:
    """If broken, the point scorer's distance is independent of road geometry.

    Scope: a synthetic 3x3, 10 m grid with one positive road cell.
    """
    transform = rasterio.transform.from_origin(0.0, 30.0, 10.0, 10.0)
    roads = np.zeros((3, 3), dtype=np.int32)
    roads[1, 1] = 9
    distances = groundtruth_road_snap_distances(
        [(15.0, 15.0), (25.0, 15.0)], roads, transform
    )
    assert distances.tolist() == [0.0, 10.0]
