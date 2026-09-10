#!/usr/bin/env python
"Score WF-3/G1 from realized depth, road, and BBMP complaint artifacts. The scorer is CPU-only. It never manufactures a raster, treats an excluded complaint as a negative, or substitutes a missing depth-convention declaration. The G1 population is the modeled-direct segment universe; lookup-only ``no_data`` segments are present in the product but are not silently counted as dry in the gate denominator. The scorer: 1. evaluates per-segment flood status using the existing primary rule; 2. snaps all in-domain BBMP points to the nearest realized road cell, enforcing the configured maximum snap distance; 3. reports point-level hit rate, flooded-segment fraction, and a 10,000-draw toroidal spatial-translation null with the supplied seed and lift; 4. preserves exact-cell and 3x3 local-max point readings separately as retrospective diagnostics, never as the signed per-segment held-out G3 gate. The primary/sensitivity ordering is read from ``data/curation/goal_d_depth_convention.json``. If that file is absent or malformed, this command stops before scoring."  # noqa: E501

from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
import typer
import yaml
from scipy.spatial import cKDTree

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from jaladhar.provenance import write_json_atomic  # noqa: E402
from jaladhar.routing.product import (  # noqa: E402
    DepthProduct,
    DepthProductError,
    load_depth_product,
)
from jaladhar.validation.segment_status import (  # noqa: E402
    ConfigResolutionError,
    RasterInputs,
    RoadIndex,
    SegmentStatusError,
    _admitted_dirty_git_provenance,
    _assert_distinct_paths,
    _git_provenance,
    _repo_path,
    build_road_index,
    build_segment_status_dataframe,
    load_aligned_class_raster,
    load_aligned_rasters,
    manifest_config_value,
    relative_repo_path,
    resolve_exclusion_mask,
    sha256_file,
)  # noqa: E402
from jaladhar.validation.segment_validation import PRIMARY_RULE  # noqa: E402

EXPECTED_NULL_DRAWS = 10_000
DEFAULT_CONVENTION_PATH = REPO / "data/curation/goal_d_depth_convention.json"
DEFAULT_VALIDATION_CONFIG = REPO / "configs/validation.yaml"

POINT_ANCHOR_THRESHOLD_M = 0.10


class G1ScoringError(RuntimeError):
    "Raised when a realized G1 input cannot be scored honestly."


def _metric_framing_block(
    source_payload: dict[str, Any],
    *,
    segment_hits: int,
    fixed_denominator_points: int,
    source_manifest_relative_path: str,
) -> dict[str, Any]:

    def _unavailable(reason: str) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "reason": reason,
            "owner_acknowledged_threshold_anchor_error": True,
            "threshold_does_not_move": True,
        }

    try:
        sweep = source_payload["results"]["bbmp_scoring"]["sweep"]
    except (KeyError, TypeError):
        return _unavailable("source manifest lacks results.bbmp_scoring.sweep")
    if not isinstance(sweep, list):
        return _unavailable("results.bbmp_scoring.sweep is not a list")
    anchors = [
        entry
        for entry in sweep
        if isinstance(entry, dict)
        and float(entry.get("threshold_m", -1.0)) == POINT_ANCHOR_THRESHOLD_M
    ]
    if len(anchors) != 1:
        return _unavailable(
            f"no unique {POINT_ANCHOR_THRESHOLD_M} m point-mediated anchor in the sweep"
        )
    anchor = anchors[0]
    point_hits = int(anchor["hits"])
    point_denominator = int(anchor["total_points"])
    framing: dict[str, Any] = {
        "status": "DISCLOSED",
        "owner_acknowledged_threshold_anchor_error": True,
        "signed_hit_rate_minimum_anchor": (
            f"The signed 60% minimum was anchored to the POINT-mediated reading "
            f"(point depth >= {POINT_ANCHOR_THRESHOLD_M} m at the complaint cell) of this "
            "same Replay #2 run."
        ),
        "point_mediated": {
            "source_manifest_path": source_manifest_relative_path,
            "source_field": (
                f"results.bbmp_scoring.sweep[threshold_m=={POINT_ANCHOR_THRESHOLD_M}]"
            ),
            "threshold_m": POINT_ANCHOR_THRESHOLD_M,
            "hits": point_hits,
            "denominator": point_denominator,
            "hit_rate_pod": anchor.get("hit_rate_pod"),
        },
        "segment_mediated_this_report": {
            "hits": segment_hits,
            "denominator": fixed_denominator_points,
            "flood_rule": "PRIMARY_RULE D=0.15 m F=20% N=3-contiguous-cells",
        },
        "metric_difference_hits": point_hits - segment_hits,
        "interpretation": (
            "Same run, same points; the difference is pure metric (point-cell depth vs "
            "per-segment flood_status), not model change."
        ),
        "threshold_does_not_move": True,
        "basis": (
            "R5/V5: the threshold was signed 2026-08-24 before any new number existed. "
            "The anchor mismatch is disclosed as an owner-acknowledged error in the "
            "gate's framing; it is never retuned to fit either metric."
        ),
    }
    if point_denominator != fixed_denominator_points:
        framing["denominator_mismatch_warning"] = (
            f"point denominator {point_denominator} != this report's fixed denominator "
            f"{fixed_denominator_points}; treat the hit difference as indicative only"
        )
    return framing


@dataclass(frozen=True)
class DepthConvention:

    primary: str
    sensitivity: str
    declaration_path: Path
    declaration_sha256: str


@dataclass(frozen=True)
class ComplaintPoint:

    point_id: str
    source_file: str
    source_row: int
    x: float
    y: float
    row: int
    col: int


@dataclass(frozen=True)
class StrictDepthPoint:

    point_id: str
    location_name: str
    observed_date: str
    band_low_m: float
    band_high_m: float
    x: float
    y: float
    row: int
    col: int


def load_strict_depth_points(
    path: Path,
    *,
    raster_crs: str,
    transform: rasterio.Affine,
    height: int,
    width: int,
    event_window_start: str,
    event_window_end: str,
) -> tuple[list[StrictDepthPoint], dict[str, Any]]:

    import pandas as pd

    if not path.is_file():
        raise FileNotFoundError(f"strict-depth point file is absent: {path}")
    frame = pd.read_csv(path)
    required = {
        "id",
        "location_name",
        "lat",
        "lon",
        "observed_date",
        "depth_band_low_m",
        "depth_band_high_m",
        "strict_depth_eligible",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise G1ScoringError(f"strict-depth point file lacks columns: {missing}")
    eligible_flag = frame["strict_depth_eligible"].map(
        lambda value: value is True or str(value).strip().casefold() == "true"
    )
    dates = frame["observed_date"].astype(str)
    event_mask = dates.between(event_window_start, event_window_end)
    selected = frame[eligible_flag & event_mask].copy()
    if selected.empty:
        raise G1ScoringError("no strict-depth rows are eligible in the scoring event window")
    if selected[["lat", "lon", "depth_band_low_m", "depth_band_high_m"]].isna().any().any():
        raise G1ScoringError("an eligible strict-depth row has missing coordinate or band bounds")
    if (selected["depth_band_low_m"] > selected["depth_band_high_m"]).any():
        raise G1ScoringError("an eligible strict-depth row has reversed band bounds")

    geometry = gpd.points_from_xy(selected["lon"], selected["lat"], crs="EPSG:4326")
    projected = gpd.GeoDataFrame(selected, geometry=geometry, crs="EPSG:4326").to_crs(raster_crs)
    points: list[StrictDepthPoint] = []
    excluded_outside_grid: list[str] = []
    for row in projected.itertuples(index=False):
        x = float(row.geometry.x)
        y = float(row.geometry.y)
        grid_row, grid_col = rasterio.transform.rowcol(transform, x, y)
        if not (0 <= grid_row < height and 0 <= grid_col < width):
            excluded_outside_grid.append(str(row.id))
            continue
        points.append(
            StrictDepthPoint(
                point_id=str(row.id),
                location_name=str(row.location_name),
                observed_date=str(row.observed_date),
                band_low_m=float(row.depth_band_low_m),
                band_high_m=float(row.depth_band_high_m),
                x=x,
                y=y,
                row=int(grid_row),
                col=int(grid_col),
            )
        )
    return points, {
        "records_loaded": int(len(frame)),
        "strict_event_rows": int(len(selected)),
        "in_depth_grid_rows": int(len(points)),
        "excluded_outside_depth_grid": excluded_outside_grid,
        "event_window_start": event_window_start,
        "event_window_end": event_window_end,
        "source_path": relative_repo_path(path),
    }


def load_depth_convention(path: Path) -> DepthConvention:
    if not path.exists():
        raise FileNotFoundError(f"declared depth-convention file is absent: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise G1ScoringError(f"declared depth-convention file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise G1ScoringError("depth-convention declaration must be a JSON object")

    primary_text = str(payload.get("primary_convention", "")).casefold()
    sensitivity_text = str(payload.get("reported_sensitivity", "")).casefold()
    if "3x3" not in primary_text or "local max" not in primary_text:
        raise G1ScoringError("depth-convention declaration does not sign 3x3 local-max as primary")
    if "exact-cell" not in sensitivity_text and "exact cell" not in sensitivity_text:
        raise G1ScoringError("depth-convention declaration does not sign exact-cell as sensitivity")
    if not payload.get("no_post_hoc_switching"):
        raise G1ScoringError(
            "depth-convention declaration lacks the no-post-hoc-switching safeguard"
        )
    return DepthConvention(
        primary="local_max_3x3",
        sensitivity="exact_cell",
        declaration_path=path,
        declaration_sha256=sha256_file(path),
    )


def _candidate_point_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"BBMP complaint path is absent: {path}")
    supported = {".kml", ".gpkg", ".geojson", ".json", ".shp"}
    files = sorted(item for item in path.iterdir() if item.suffix.lower() in supported)
    if not files:
        raise FileNotFoundError(f"no supported complaint point files found in {path}")
    return files


def load_bbmp_points(
    path: Path,
    *,
    raster_crs: str,
    transform: rasterio.Affine,
    height: int,
    width: int,
    expected_count: int | None = 399,
) -> tuple[list[ComplaintPoint], dict[str, Any]]:
    raw_points: list[tuple[str, str, int, float, float]] = []
    for source_file in _candidate_point_files(path):
        try:
            frame = gpd.read_file(source_file)
        except Exception as exc:
            raise G1ScoringError(f"could not read complaint artifact {source_file}: {exc}") from exc
        if frame.crs is None:
            raise G1ScoringError(f"complaint artifact has no CRS: {source_file}")
        point_frame = frame[frame.geometry.geom_type == "Point"]
        for source_row, (_, record) in enumerate(point_frame.iterrows()):
            geometry = record.geometry
            raw_points.append(
                (
                    f"{source_file.name}:{source_row}",
                    source_file.name,
                    int(source_row),
                    float(geometry.x),
                    float(geometry.y),
                )
            )
        if len(point_frame) != len(frame):
            raise G1ScoringError(f"complaint artifact contains non-point geometries: {source_file}")

    if expected_count is not None and len(raw_points) != expected_count:
        raise G1ScoringError(
            f"realized BBMP point count is {len(raw_points)}, expected {expected_count}; "
            "refusing to silently score a different label set"
        )

    transformed: list[ComplaintPoint] = []
    excluded = 0
    for source_file in _candidate_point_files(path):
        frame = gpd.read_file(source_file)
        point_frame = frame[frame.geometry.geom_type == "Point"]
        projected = point_frame.to_crs(raster_crs)
        for source_row, (_, record) in enumerate(projected.iterrows()):
            x = float(record.geometry.x)
            y = float(record.geometry.y)
            row, col = rasterio.transform.rowcol(transform, x, y)
            if not (0 <= row < height and 0 <= col < width):
                excluded += 1
                continue
            transformed.append(
                ComplaintPoint(
                    point_id=f"{source_file.name}:{source_row}",
                    source_file=source_file.name,
                    source_row=int(source_row),
                    x=x,
                    y=y,
                    row=int(row),
                    col=int(col),
                )
            )
    return transformed, {
        "raw_count": len(raw_points),
        "in_domain_count": len(transformed),
        "excluded_outside_depth_grid": excluded,
        "source_path": relative_repo_path(path),
    }


def _road_snap_index(inputs: RasterInputs) -> tuple[cKDTree, np.ndarray]:
    rows, cols = np.where(inputs.road_ids > 0)
    xs, ys = rasterio.transform.xy(inputs.transform, rows, cols, offset="center")
    return cKDTree(np.column_stack((xs, ys))), inputs.road_ids[rows, cols].astype(np.int64)


def _sample_local_max(depth_m: np.ndarray, point: ComplaintPoint) -> float:
    r0, r1 = point.row - 1, point.row + 2
    c0, c1 = point.col - 1, point.col + 2
    if r0 < 0 or c0 < 0 or r1 > depth_m.shape[0] or c1 > depth_m.shape[1]:
        raise G1ScoringError(
            f"3x3 local-max window is clipped at {point.point_id} ({point.row},{point.col})"
        )
    window = depth_m[r0:r1, c0:c1]
    if window.shape != (3, 3) or not np.all(np.isfinite(window)):
        raise G1ScoringError(f"3x3 local-max window is not fully realized at {point.point_id}")
    return float(np.max(window))


def _spatial_null_score(
    *,
    flooded_by_id: dict[int, bool],
    complaint_points_xy: np.ndarray,
    observed_segment_ids: np.ndarray,
    observed_within_snap_limit: np.ndarray,
    road_tree: cKDTree,
    road_segment_ids: np.ndarray,
    n_draws: int,
    seed: int,
    max_snap_distance_m: float,
    fixed_unscorable_count: int = 0,
) -> dict[str, Any]:

    if n_draws <= 0:
        raise ValueError("G1 spatial-null draw count must be positive")
    if complaint_points_xy.ndim != 2 or complaint_points_xy.shape[1] != 2:
        raise ValueError("complaint_points_xy must have shape (N,2)")
    if len(complaint_points_xy) != len(observed_segment_ids) or len(complaint_points_xy) != len(
        observed_within_snap_limit
    ):
        raise ValueError("complaint coordinates, segment IDs, and snap flags must align")
    if fixed_unscorable_count < 0:
        raise ValueError("fixed_unscorable_count must be non-negative")
    observed_hits = np.asarray(
        [bool(flooded_by_id.get(int(value), False)) for value in observed_segment_ids],
        dtype=bool,
    ) & np.asarray(observed_within_snap_limit, dtype=bool)
    n_observed = int(len(observed_segment_ids) + fixed_unscorable_count)
    hits = int(observed_hits.sum())
    hit_rate = hits / n_observed if n_observed else None
    if n_observed:
        road_xy = np.asarray(road_tree.data, dtype=np.float64)
        lower = road_xy.min(axis=0)
        span = road_xy.max(axis=0) - lower
        if np.any(span <= 0.0):
            raise G1ScoringError("road spatial-null extent is degenerate")
        rng = np.random.default_rng(seed)
        null_hit_rates = np.empty(n_draws, dtype=np.float64)
        out_of_snap_counts = np.empty(n_draws, dtype=np.int64)
        for draw in range(n_draws):
            offset = rng.uniform(0.0, span)
            translated = ((complaint_points_xy - lower + offset) % span) + lower
            distances, indices = road_tree.query(translated)
            within_limit = np.asarray(distances, dtype=np.float64) <= max_snap_distance_m
            translated_ids = road_segment_ids[np.asarray(indices, dtype=np.int64)]
            translated_hits = (
                np.asarray(
                    [bool(flooded_by_id.get(int(value), False)) for value in translated_ids],
                    dtype=bool,
                )
                & within_limit
            )
            null_hit_rates[draw] = float(translated_hits.sum()) / n_observed
            out_of_snap_counts[draw] = int((~within_limit).sum()) + fixed_unscorable_count
        null_hits = null_hit_rates * n_observed
        null_mean_hits = float(np.mean(null_hits))
        null_mean_hit_rate = float(np.mean(null_hit_rates))
        lift = (
            hit_rate / null_mean_hit_rate if hit_rate is not None and null_mean_hit_rate else None
        )
        null_block: dict[str, Any] = {
            "method": "toroidal spatial translation of in-domain complaint-point pattern",
            "spatial_scope": (
                "one shared random XY offset per draw over the realized canonical-road "
                "bounding box; wraparound preserves point count and relative pattern"
            ),
            "draws": n_draws,
            "seed": int(seed),
            "max_snap_distance_m": float(max_snap_distance_m),
            "snap_semantics": (
                "same configured eligibility rule for observed and translated points; beyond-limit "
                "points remain in the fixed denominator as misses"
            ),
            "outside_grid_fixed_misses": fixed_unscorable_count,
            "mean_out_of_snap_points_per_draw": float(np.mean(out_of_snap_counts)),
            "out_of_snap_points_range": [
                int(np.min(out_of_snap_counts)),
                int(np.max(out_of_snap_counts)),
            ],
            "mean_hits": null_mean_hits,
            "mean_hit_rate": null_mean_hit_rate,
            "hit_rate_ci_95": [
                float(np.percentile(null_hit_rates, 2.5)),
                float(np.percentile(null_hit_rates, 97.5)),
            ],
            "lift": lift,
        }
    else:
        null_block = {
            "method": "toroidal spatial translation of in-domain complaint-point pattern",
            "draws": n_draws,
            "seed": int(seed),
            "mean_hits": None,
            "mean_hit_rate": None,
            "hit_rate_ci_95": [None, None],
            "lift": None,
            "undefined_reason": "no complaint points passed domain and snap-distance eligibility",
        }
    return {
        "fixed_denominator_points": n_observed,
        "coordinate_realized_points": int(len(observed_segment_ids)),
        "within_snap_modeled_points": int(np.asarray(observed_within_snap_limit, dtype=bool).sum()),
        "outside_grid_fixed_misses": fixed_unscorable_count,
        "out_of_snap_fixed_misses": int(
            (~np.asarray(observed_within_snap_limit, dtype=bool)).sum()
        ),
        "unique_complaint_segments": int(
            len(
                set(
                    int(value)
                    for value, within in zip(
                        observed_segment_ids, observed_within_snap_limit, strict=True
                    )
                    if bool(within)
                )
            )
        ),
        "hits": hits,
        "misses": n_observed - hits,
        "hit_rate": hit_rate,
        "null": null_block,
    }


def _convention_score(
    *,
    depth_for_segments: np.ndarray,
    point_depths: dict[str, float],
    inputs: RasterInputs,
    lookup: Any,
    road_index: RoadIndex,
    tree: cKDTree,
    road_segment_ids: np.ndarray,
    snap_segment_ids: np.ndarray,
    snap_distances: np.ndarray,
    points: list[ComplaintPoint],
    max_snap_distance_m: float,
    n_draws: int,
    seed: int,
    fixed_unscorable_count: int = 0,
    excluded_mask: np.ndarray | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    frame = build_segment_status_dataframe(
        depth_for_segments,
        inputs.road_ids,
        lookup,
        road_index=None if excluded_mask is not None else road_index,
        excluded_mask=excluded_mask,
    )
    modeled = frame[frame["confidence"] == "modeled_direct"].copy()
    flooded_by_id = dict(
        zip(
            modeled["segment_id"].astype(np.int64),
            modeled["flood_status"] == "flooded",
            strict=True,
        )
    )

    point_details: dict[str, dict[str, Any]] = {}
    complaint_points_xy: list[tuple[float, float]] = []
    observed_segment_ids: list[int] = []
    observed_within_snap_limit: list[bool] = []
    for point, segment_id, snap_distance in zip(
        points, snap_segment_ids, snap_distances, strict=True
    ):
        segment_id = int(segment_id)
        snap_distance = float(snap_distance)
        within_snap_limit = snap_distance <= max_snap_distance_m
        in_modeled_universe = segment_id in flooded_by_id and within_snap_limit
        segment_hit = bool(flooded_by_id.get(segment_id, False))
        point_depth = float(point_depths[point.point_id])
        point_hit = point_depth >= float(PRIMARY_RULE.depth_threshold_m)
        complaint_points_xy.append((point.x, point.y))
        observed_segment_ids.append(segment_id)
        observed_within_snap_limit.append(in_modeled_universe)
        point_details[point.point_id] = {
            "source_file": point.source_file,
            "source_row": point.source_row,
            "row": point.row,
            "col": point.col,
            "segment_id": segment_id,
            "snap_distance_m": snap_distance,
            "max_snap_distance_m": max_snap_distance_m,
            "point_depth_m": point_depth,
            "point_depth_flooded": point_hit,
            "segment_flooded": segment_hit if in_modeled_universe else None,
            "modeled_direct_segment": in_modeled_universe,
            "exclusion_reason": (
                None
                if in_modeled_universe
                else (
                    "snap_distance_exceeds_configured_max"
                    if not within_snap_limit
                    else "snapped_segment_has_no_canonical_depth"
                )
            ),
        }

    score = _spatial_null_score(
        flooded_by_id=flooded_by_id,
        complaint_points_xy=np.asarray(complaint_points_xy, dtype=np.float64).reshape(-1, 2),
        observed_segment_ids=np.asarray(observed_segment_ids, dtype=np.int64),
        observed_within_snap_limit=np.asarray(observed_within_snap_limit, dtype=bool),
        road_tree=tree,
        road_segment_ids=road_segment_ids,
        n_draws=n_draws,
        seed=seed,
        max_snap_distance_m=max_snap_distance_m,
        fixed_unscorable_count=fixed_unscorable_count,
    )
    score["total_modeled_segments"] = int(len(modeled))
    score["predicted_flooded_segments"] = int((modeled["flood_status"] == "flooded").sum())
    score["flooded_segment_fraction"] = score["predicted_flooded_segments"] / len(modeled)
    depth_arr = np.asarray(depth_for_segments)
    on_road = inputs.road_ids > 0
    raw_flooded_cells = int(
        np.logical_and(on_road, depth_arr >= float(PRIMARY_RULE.depth_threshold_m)).sum()
    )
    road_cell_count = int(on_road.sum())
    if excluded_mask is None:
        score["flooded_road_cell_fraction"] = float(raw_flooded_cells / road_cell_count)
    else:
        keep = np.logical_and(on_road, ~np.asarray(excluded_mask, dtype=bool))
        kept_flooded_cells = int(
            np.logical_and(keep, depth_arr >= float(PRIMARY_RULE.depth_threshold_m)).sum()
        )
        score["flooded_road_cell_fraction"] = float(kept_flooded_cells / int(keep.sum()))
        score["flooded_road_cell_fraction_basis"] = "exclusion_masked"
        score["flooded_road_cell_fraction_unexcluded_includes_initial_condition_water"] = float(
            raw_flooded_cells / road_cell_count
        )
    return score, point_details


def _depth_band_score(
    *,
    points: list[StrictDepthPoint],
    depth_m: np.ndarray,
    road_tree: cKDTree,
    road_segment_ids: np.ndarray,
    max_snap_distance_m: float,
    local_max: bool,
    in_band_threshold: float,
) -> dict[str, Any]:
    "Score one predeclared G3 depth-reading convention on strict rows."

    details: list[dict[str, Any]] = []
    within = 0
    below = 0
    above = 0
    excluded = 0
    for point in points:
        distance, index = road_tree.query((point.x, point.y))
        snap_distance = float(distance)
        segment_id = int(road_segment_ids[int(index)])
        if snap_distance > max_snap_distance_m:
            excluded += 1
            details.append(
                {
                    "id": point.point_id,
                    "location_name": point.location_name,
                    "eligible": False,
                    "exclusion_reason": "snap_distance_exceeds_configured_max",
                    "snap_distance_m": snap_distance,
                    "max_snap_distance_m": max_snap_distance_m,
                    "segment_id": segment_id,
                }
            )
            continue
        depth = (
            _sample_local_max(
                depth_m,
                ComplaintPoint(
                    point_id=point.point_id,
                    source_file=point.point_id,
                    source_row=0,
                    x=point.x,
                    y=point.y,
                    row=point.row,
                    col=point.col,
                ),
            )
            if local_max
            else float(depth_m[point.row, point.col])
        )
        if not math.isfinite(depth):
            raise G1ScoringError(f"strict-depth reading is non-finite at {point.point_id}")
        if depth < point.band_low_m:
            status = "BELOW"
            below += 1
        elif depth > point.band_high_m:
            status = "ABOVE"
            above += 1
        else:
            status = "WITHIN"
            within += 1
        details.append(
            {
                "id": point.point_id,
                "location_name": point.location_name,
                "observed_date": point.observed_date,
                "eligible": True,
                "segment_id": segment_id,
                "snap_distance_m": snap_distance,
                "observed_band_low_m": point.band_low_m,
                "observed_band_high_m": point.band_high_m,
                "model_depth_m": depth,
                "band_status": status,
            }
        )
    denominator = len(points) - excluded
    in_band_rate = within / denominator if denominator else None
    diagnostic_threshold_result = (
        "BLOCKED"
        if in_band_rate is None
        else ("PASS" if in_band_rate >= in_band_threshold else "FAIL")
    )
    return {
        "status": "RETROSPECTIVE_DIAGNOSTIC",
        "diagnostic_threshold_result": diagnostic_threshold_result,
        "within_band_count": within,
        "below_band_count": below,
        "above_band_count": above,
        "denominator": denominator,
        "excluded_count": excluded,
        "in_band_rate": in_band_rate,
        "in_band_minimum": in_band_threshold,
        "points": details,
    }


def _source_groundtruth_reproduction(
    source_manifest: dict[str, Any],
    local_score: dict[str, Any],
    exact_score: dict[str, Any],
) -> dict[str, Any]:

    try:
        historical = source_manifest["results"]["groundtruth_scoring"]["points_detail"]
    except (KeyError, TypeError) as exc:
        raise G1ScoringError(
            "source manifest lacks historical groundtruth point readings for reproduction"
        ) from exc
    historical_by_id = {str(row["id"]): row for row in historical if isinstance(row, dict)}
    local_by_id = {
        str(row["id"]): row for row in local_score["points"] if row.get("eligible") is True
    }
    exact_by_id = {
        str(row["id"]): row for row in exact_score["points"] if row.get("eligible") is True
    }
    mismatches: list[dict[str, Any]] = []
    for point_id in sorted(set(local_by_id) | set(exact_by_id)):
        historical_row = historical_by_id.get(point_id)
        if historical_row is None:
            mismatches.append({"id": point_id, "reason": "absent_from_source_manifest"})
            continue
        expected_local = historical_row.get("model_depth_local_max_m")
        expected_exact = historical_row.get("model_depth_point_m")
        realized_local = local_by_id.get(point_id, {}).get("model_depth_m")
        realized_exact = exact_by_id.get(point_id, {}).get("model_depth_m")
        if (
            expected_local is None
            or expected_exact is None
            or round(float(realized_local), 4) != round(float(expected_local), 4)
            or round(float(realized_exact), 4) != round(float(expected_exact), 4)
        ):
            mismatches.append(
                {
                    "id": point_id,
                    "historical_local_max_m": expected_local,
                    "rerun_local_max_m": realized_local,
                    "historical_exact_cell_m": expected_exact,
                    "rerun_exact_cell_m": realized_exact,
                }
            )
    return {
        "comparison": "rounded to the historical manifest's four-decimal precision",
        "compared_rows": len(set(local_by_id) | set(exact_by_id)),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def _assert_product_matches_frame(product: DepthProduct, frame: Any, *, convention: str) -> None:
    "Ensure a scored convention cannot silently diverge from the rendered product."

    realized = {
        int(row.segment_id): (
            int(row.band_low_cm),
            int(row.band_high_cm),
            str(row.confidence),
            str(row.flood_status),
        )
        for row in frame.itertuples(index=False)
    }
    product_rows = {
        segment_id: (
            state.band_low_cm,
            state.band_high_cm,
            state.confidence,
            state.flood_status,
        )
        for segment_id, state in product.rows.items()
    }
    if realized != product_rows:
        differing = sorted(
            segment_id
            for segment_id in set(realized) | set(product_rows)
            if realized.get(segment_id) != product_rows.get(segment_id)
        )
        if convention == "local_max_3x3":
            raise G1ScoringError(
                "local_max_3x3 segment status disagrees with the frozen product consumed by "
                "dashboard/routing; owner adjudication is required before G1 can choose which "
                f"status is authoritative; first differing IDs={differing[:10]}"
            )
        raise G1ScoringError(
            f"{convention} recomputation disagrees with the frozen product consumed by "
            f"dashboard/routing; first differing IDs={differing[:10]}"
        )


def _gate_verdict(
    score: dict[str, Any],
    *,
    expected_point_count: int | None,
    hit_rate_threshold: float,
    lift_threshold: float,
) -> tuple[str, str]:
    "Return a scope-aware verdict; a reduced population can never pass G1."

    denominator = int(score["fixed_denominator_points"])
    if expected_point_count is not None and denominator != expected_point_count:
        return (
            "BLOCKED",
            "fixed complaint-point denominator is "
            f"{denominator}/{expected_point_count}; V7 forbids PASS on a reduced label set",
        )
    hit_rate = score["hit_rate"]
    lift = score["null"]["lift"]
    if hit_rate is None or lift is None:
        return "BLOCKED", "hit rate or null lift is undefined"
    if hit_rate >= hit_rate_threshold and lift >= lift_threshold:
        return "PASS", "both signed thresholds are met at the full claimed point scope"
    return "FAIL", "one or both signed thresholds are not met"


def _resolve_score_config(
    *,
    depth_raster: Path,
    road_raster: Path,
    lookup_csv: Path,
    complaints: Path,
    output_dir: Path,
    source_manifest: Path,
    product_path: Path,
    convention_path: Path,
    strict_depth_points: Path,
    expected_bbmp_count: int | None,
    n_nulls: int,
    max_snap_distance_m: float,
    basin_class_raster: Path | None = None,
    excluded_basin_classes: frozenset[int] | None = None,
    exclusion_mode: str | None = None,
) -> dict[str, Any]:
    excluded = frozenset(excluded_basin_classes or frozenset())
    errors: list[str] = []
    required_paths: dict[str, Path] = {
        "depth_raster": depth_raster,
        "road_raster": road_raster,
        "lookup_csv": lookup_csv,
        "complaints": complaints,
        "source_manifest": source_manifest,
        "product_path": product_path,
        "convention_path": convention_path,
        "strict_depth_points": strict_depth_points,
    }
    if excluded:
        if basin_class_raster is None:
            errors.append(
                "basin_class_raster: required when excluded_basin_classes is set "
                "(exclusions are asserted against realized class bytes, never a "
                "declaration alone)"
            )
        else:
            required_paths["basin_class_raster"] = basin_class_raster
        if exclusion_mode not in ("cell", "segment_touch"):
            errors.append(
                f"exclusion_mode: unknown mode {exclusion_mode!r}; expected cell|segment_touch"
            )
    for name, path in required_paths.items():
        if not path.exists():
            errors.append(f"{name}: absent at {path}")
    if n_nulls != EXPECTED_NULL_DRAWS:
        errors.append(f"n_nulls: must equal {EXPECTED_NULL_DRAWS}, got {n_nulls}")
    if not math.isfinite(max_snap_distance_m) or max_snap_distance_m <= 0.0:
        errors.append("max_snap_distance_m: must be finite and positive")
    if output_dir.resolve() == REPO.resolve():
        errors.append("output_dir: repository root is not a valid writable run directory")
    if source_manifest.exists():
        try:
            source = json.loads(source_manifest.read_text(encoding="utf-8"))
            if source.get("status") != "completed":
                errors.append(
                    f"source_manifest: status must be completed, got {source.get('status')!r}"
                )
        except json.JSONDecodeError as exc:
            errors.append(f"source_manifest: invalid JSON ({exc})")
    try:
        _assert_distinct_paths(source_manifest.parent, output_dir)
    except SegmentStatusError as exc:
        errors.append(str(exc))
    if output_dir.exists():
        if (output_dir / "manifest.json").exists():
            errors.append(f"output_dir: refusing to replace {(output_dir / 'manifest.json')}")
    if errors:
        raise ConfigResolutionError("G1 startup configuration failed:\n- " + "\n- ".join(errors))
    resolved: dict[str, Any] = {
        "depth_raster": depth_raster,
        "road_raster": road_raster,
        "lookup_csv": lookup_csv,
        "complaints": complaints,
        "output_dir": output_dir,
        "source_manifest": source_manifest,
        "product_path": product_path,
        "convention_path": convention_path,
        "strict_depth_points": strict_depth_points,
        "expected_bbmp_count": expected_bbmp_count,
        "n_nulls": n_nulls,
        "max_snap_distance_m": max_snap_distance_m,
    }
    if excluded:
        resolved["basin_class_raster"] = basin_class_raster
        resolved["excluded_basin_classes"] = sorted(excluded)
        resolved["exclusion_mode"] = exclusion_mode
    return resolved


def score_g1(
    *,
    depth_raster: Path,
    road_raster: Path,
    lookup_csv: Path,
    complaints: Path,
    output_dir: Path,
    source_manifest: Path,
    product_path: Path,
    convention_path: Path,
    strict_depth_points: Path,
    event_window_start: str,
    event_window_end: str,
    expected_bbmp_count: int | None,
    seed: int,
    n_nulls: int,
    max_snap_distance_m: float,
    hit_rate_threshold: float,
    lift_threshold: float,
    depth_in_band_threshold: float,
    admit_dirty_tree_reason: str | None = None,
    basin_class_raster: Path | None = None,
    excluded_basin_classes: frozenset[int] | None = None,
    exclusion_mode: str = "segment_touch",
) -> dict[str, Any]:
    excluded_basin_classes = frozenset(excluded_basin_classes or frozenset())
    config = _resolve_score_config(
        depth_raster=depth_raster,
        road_raster=road_raster,
        lookup_csv=lookup_csv,
        complaints=complaints,
        output_dir=output_dir,
        source_manifest=source_manifest,
        product_path=product_path,
        convention_path=convention_path,
        strict_depth_points=strict_depth_points,
        expected_bbmp_count=expected_bbmp_count,
        n_nulls=n_nulls,
        max_snap_distance_m=max_snap_distance_m,
        basin_class_raster=basin_class_raster,
        excluded_basin_classes=excluded_basin_classes or None,
        exclusion_mode=exclusion_mode if excluded_basin_classes else None,
    )
    convention = load_depth_convention(convention_path)
    if not (0.0 <= hit_rate_threshold <= 1.0):
        raise ValueError("hit_rate_threshold must be within [0,1]")
    if lift_threshold < 0.0:
        raise ValueError("lift_threshold must be non-negative")
    if not 0.0 <= depth_in_band_threshold <= 1.0:
        raise ValueError("depth_in_band_threshold must be within [0,1]")

    if admit_dirty_tree_reason is not None:
        provenance = _admitted_dirty_git_provenance(REPO, admit_dirty_tree_reason)
    else:
        provenance = _git_provenance()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    report_path = output_dir / "g1_score.json"
    source_bundle = output_dir / "provenance" / "source.bundle"
    started = time.perf_counter()
    manifest: dict[str, Any] = {
        "stage": "wf3_g1_segment_scorer",
        "status": "running",
        **provenance,
        "start_time_iso": datetime.now(UTC).isoformat(),
        "resolved_config": {key: manifest_config_value(value) for key, value in config.items()},
        "parameters": {
            "depth_threshold_m": float(PRIMARY_RULE.depth_threshold_m),
            "fraction_threshold": float(PRIMARY_RULE.fraction_threshold),
            "contiguous_cells": int(PRIMARY_RULE.contiguous_cells),
            "null_draws": int(n_nulls),
            "seed": int(seed),
            "hit_rate_threshold": float(hit_rate_threshold),
            "lift_threshold": float(lift_threshold),
            "depth_in_band_threshold": float(depth_in_band_threshold),
            "device": "cpu",
        },
        "convention": {
            "primary": convention.primary,
            "sensitivity": convention.sensitivity,
            "declaration_path": relative_repo_path(convention_path),
            "declaration_sha256": convention.declaration_sha256,
        },
        "source_manifest_path": relative_repo_path(source_manifest),
        "depth_product_path": relative_repo_path(product_path),
        "report_path": relative_repo_path(report_path),
        "source_snapshot_bundle": (
            relative_repo_path(source_bundle) if source_bundle.is_file() else None
        ),
        "source_snapshot_bundle_sha256": (
            sha256_file(source_bundle) if source_bundle.is_file() else None
        ),
    }
    write_json_atomic(manifest_path, manifest)
    try:
        import pandas as pd

        inputs = load_aligned_rasters(depth_raster, road_raster)
        lookup = pd.read_csv(lookup_csv)
        # consistency assert below would refuse the very product under test.
        excluded_mask = None
        storage_exclusion_block: dict[str, Any] | None = None
        if excluded_basin_classes:
            if basin_class_raster is None:
                raise G1ScoringError(
                    "excluded_basin_classes requires basin_class_raster: exclusions are "
                    "asserted against realized class bytes, never a declaration alone"
                )
            if exclusion_mode not in ("cell", "segment_touch"):
                raise G1ScoringError(
                    f"unknown exclusion_mode {exclusion_mode!r}; expected cell|segment_touch"
                )
            class_array, class_info = load_aligned_class_raster(basin_class_raster, inputs)
            excluded_mask, mask_info = resolve_exclusion_mask(
                inputs.road_ids, class_array, sorted(excluded_basin_classes), exclusion_mode
            )
            road_index = None
            storage_exclusion_block = {
                "rule": (
                    "segment statuses are scored exactly as produced: cells of segments "
                    "affected by basin_class in excluded_classes are dropped from band "
                    "aggregation and flood-status determination"
                ),
                "mode": exclusion_mode,
                "excluded_classes": sorted(excluded_basin_classes),
                "basin_class_raster": relative_repo_path(basin_class_raster),
                "basin_class_raster_sha256": sha256_file(basin_class_raster),
                "cells_masked_on_grid": int(mask_info["cells_masked_on_grid"]),
                "consistency_assert": (
                    "_assert_product_matches_frame recomputes WITH this same exclusion "
                    "before any gate number is produced"
                ),
            }
        else:
            road_index = build_road_index(inputs.road_ids)
        try:
            product = load_depth_product(
                product_path,
                repo_root=REPO,
                lookup_path=lookup_csv,
            )
        except DepthProductError as exc:
            raise G1ScoringError(f"frozen depth product is invalid: {exc}") from exc
        declared_depth_source = product.manifest.get("depth_source_manifest_path")
        if declared_depth_source != relative_repo_path(source_manifest):
            raise G1ScoringError(
                "depth product does not cite the configured upstream depth manifest"
            )
        if product.manifest.get("input_sha256", {}).get("depth_raster") != sha256_file(
            depth_raster
        ):
            raise G1ScoringError(
                "depth product input hash does not match the scorer's realized depth raster"
            )
        exact_frame = build_segment_status_dataframe(
            inputs.depth_m,
            inputs.road_ids,
            lookup,
            road_index=road_index,
            excluded_mask=excluded_mask,
        )
        _assert_product_matches_frame(product, exact_frame, convention="exact_cell")
        points, point_summary = load_bbmp_points(
            complaints,
            raster_crs=inputs.crs,
            transform=inputs.transform,
            height=inputs.depth_m.shape[0],
            width=inputs.depth_m.shape[1],
            expected_count=expected_bbmp_count,
        )
        tree, road_segment_ids = _road_snap_index(inputs)
        point_coordinates = np.column_stack(([p.x for p in points], [p.y for p in points]))
        snap_distances, snap_indices = tree.query(point_coordinates)
        snap_segment_ids = road_segment_ids[snap_indices]
        for point, distance in zip(points, snap_distances, strict=True):
            if not math.isfinite(float(distance)):
                raise G1ScoringError(f"could not snap complaint point {point.point_id}")

        complaint_point_depths = {
            point.point_id: float(inputs.depth_m[point.row, point.col]) for point in points
        }
        g1_score, complaint_details = _convention_score(
            depth_for_segments=inputs.depth_m,
            point_depths=complaint_point_depths,
            inputs=inputs,
            lookup=lookup,
            road_index=road_index,
            tree=tree,
            road_segment_ids=road_segment_ids,
            snap_segment_ids=snap_segment_ids,
            snap_distances=np.asarray(snap_distances, dtype=np.float64),
            points=points,
            max_snap_distance_m=max_snap_distance_m,
            n_draws=n_nulls,
            seed=seed,
            fixed_unscorable_count=(
                int(point_summary["raw_count"]) - int(point_summary["in_domain_count"])
            ),
            excluded_mask=excluded_mask,
        )
        g1_verdict, g1_reason = _gate_verdict(
            g1_score,
            expected_point_count=expected_bbmp_count,
            hit_rate_threshold=hit_rate_threshold,
            lift_threshold=lift_threshold,
        )
        raw_count = int(point_summary["raw_count"])
        g1_score["full_raw_label_set_hit_rate"] = (
            g1_score["hits"] / raw_count if raw_count else None
        )
        if g1_score["fixed_denominator_points"] != raw_count:
            raise G1ScoringError(
                "fixed G1 denominator does not equal the realized raw BBMP label count"
            )

        strict_points, strict_summary = load_strict_depth_points(
            strict_depth_points,
            raster_crs=inputs.crs,
            transform=inputs.transform,
            height=inputs.depth_m.shape[0],
            width=inputs.depth_m.shape[1],
            event_window_start=event_window_start,
            event_window_end=event_window_end,
        )
        g3_local = _depth_band_score(
            points=strict_points,
            depth_m=inputs.depth_m,
            road_tree=tree,
            road_segment_ids=road_segment_ids,
            max_snap_distance_m=max_snap_distance_m,
            local_max=True,
            in_band_threshold=depth_in_band_threshold,
        )
        g3_exact = _depth_band_score(
            points=strict_points,
            depth_m=inputs.depth_m,
            road_tree=tree,
            road_segment_ids=road_segment_ids,
            max_snap_distance_m=max_snap_distance_m,
            local_max=False,
            in_band_threshold=depth_in_band_threshold,
        )
        g3_scores = {
            convention.primary: g3_local,
            convention.sensitivity: g3_exact,
        }
        g3_disagreement = (
            len({score["diagnostic_threshold_result"] for score in g3_scores.values()}) > 1
        )
        source_payload = json.loads(source_manifest.read_text(encoding="utf-8"))
        reproduction = _source_groundtruth_reproduction(source_payload, g3_local, g3_exact)
        reproduction["status"] = "CONFIRMED" if reproduction["mismatch_count"] == 0 else "REFUTED"
        metric_framing = _metric_framing_block(
            source_payload,
            segment_hits=int(g1_score["hits"]),
            fixed_denominator_points=int(g1_score["fixed_denominator_points"]),
            source_manifest_relative_path=relative_repo_path(source_manifest),
        )
        report: dict[str, Any] = {
            "stage": "WF-3 G1 classification and retrospective G3 point-depth diagnostics",
            "status": g1_verdict,
            "product_label": product.manifest.get("product_label"),
            "g1": {
                "status": g1_verdict,
                "reason": g1_reason,
                "metric_framing": metric_framing,
                "thresholds": {
                    "hit_rate_minimum": float(hit_rate_threshold),
                    "null_lift_minimum": float(lift_threshold),
                    "null_draws": int(n_nulls),
                    "seed": int(seed),
                },
                "score": g1_score,
                "point_details": complaint_details,
            },
            "g3": {
                "status": "BLOCKED",
                "reason": (
                    "The signed gate requires held-out per-segment depth-band comparison. "
                    "No owner-signed segment-band comparison predicate or independent holdout "
                    "exists; these values are retrospective point-depth diagnostics only."
                ),
                "required_to_unblock": [
                    "owner-signed segment-band comparison predicate",
                    "newly frozen independent held-out strict-depth set",
                ],
                "in_band_minimum": depth_in_band_threshold,
                "primary_convention": convention.primary,
                "sensitivity_convention": convention.sensitivity,
                "convention_disagreement": g3_disagreement,
                "holdout_status": "RETROSPECTIVE_NOT_HELD_OUT",
                "strict_depth_scope": strict_summary,
                "conventions": g3_scores,
                "source_manifest_reproduction": reproduction,
            },
            "depth_rule": {
                "depth_threshold_m": float(PRIMARY_RULE.depth_threshold_m),
                "fraction_threshold": float(PRIMARY_RULE.fraction_threshold),
                "contiguous_cells": int(PRIMARY_RULE.contiguous_cells),
            },
            "label_set": point_summary,
            "scope": {
                "claimed": (
                    "per-road-segment flood status evaluated at BBMP complaint points "
                    "within the configured snap-distance limit"
                ),
                "run": (
                    f"{point_summary['raw_count']} raw points, "
                    f"{point_summary['in_domain_count']} in depth-grid domain, "
                    f"{g1_score['within_snap_modeled_points']} within-snap modeled points, "
                    "all raw rows retained in the fixed G1 denominator"
                ),
                "max_snap_distance_m": max_snap_distance_m,
            },
            "derivation": {
                "segment_status": (
                    "unaltered float-metre event-max depth -> existing D/F/N segment rule -> "
                    "the same frozen product consumed by dashboard and routing; G3 reading "
                    "conventions do not redefine G1 segment status and do not discharge signed G3"
                ),
                "observed_segments": (
                    "BBMP points -> nearest realized road-cell segment IDs -> "
                    "point-level modeled-direct flood statuses; repeated segment IDs remain "
                    "repeated complaint observations"
                ),
                "null": (
                    "toroidal XY translation of the eligible complaint pattern over the "
                    "realized road extent; one shared offset per draw preserves point count "
                    "and relative spatial pattern"
                ),
                "lift": "observed segment hit rate divided by mean null hit rate",
                "local_max": (
                    "3x3 local maximum sampled only for the predeclared G3 strict-depth reading"
                ),
                "exact_cell": (
                    "unaltered realized depth field sampled at the containing raster cell"
                ),
            },
            "input_sha256": {
                "depth_raster": sha256_file(depth_raster),
                "road_raster": sha256_file(road_raster),
                "lookup_csv": sha256_file(lookup_csv),
                "complaints": {
                    relative_repo_path(path): sha256_file(path)
                    for path in _candidate_point_files(complaints)
                },
                "source_manifest": sha256_file(source_manifest),
                "depth_product": sha256_file(product_path),
                "convention": convention.declaration_sha256,
                "strict_depth_points": sha256_file(strict_depth_points),
                **(
                    {"basin_class_raster": sha256_file(basin_class_raster)}
                    if basin_class_raster is not None and excluded_basin_classes
                    else {}
                ),
            },
            **(
                {"storage_water_exclusion": storage_exclusion_block}
                if storage_exclusion_block is not None
                else {}
            ),
        }
        write_json_atomic(report_path, report)
        terminal = {
            **manifest,
            "status": "completed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": round(time.perf_counter() - started, 4),
            "realized_state": {
                "report_sha256": sha256_file(report_path),
                "raw_bbmp_points": point_summary["raw_count"],
                "in_domain_bbmp_points": point_summary["in_domain_count"],
                "g1_verdict": g1_verdict,
                "g1_fixed_denominator_points": g1_score["fixed_denominator_points"],
                "g3_convention_verdicts": {
                    name: score["diagnostic_threshold_result"] for name, score in g3_scores.items()
                },
                "signed_g3_status": "BLOCKED",
                "g3_convention_disagreement": g3_disagreement,
                "source_manifest_reproduction": reproduction["status"],
            },
        }
        write_json_atomic(manifest_path, terminal)
        return {"manifest": terminal, "report": report}
    except BaseException as exc:
        failed = {
            **manifest,
            "status": "failed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": round(time.perf_counter() - started, 4),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        write_json_atomic(manifest_path, failed)
        raise


app = typer.Typer(add_completion=False)


@app.command()
def main(
    depth_raster: Path = typer.Option(..., help="Realized event-max depth raster in metres"),
    product_file: Path = typer.Option(
        ..., help="Contract-valid segment_status CSV/JSON consumed by dashboard and routing"
    ),
    output_dir: Path = typer.Option(..., help="Fresh writable scorer run directory"),
    road_raster: Path | None = typer.Option(None, help="Canonical road segment-ID raster"),
    lookup_csv: Path | None = typer.Option(None, help="Road segment lookup CSV"),
    complaints: Path | None = typer.Option(None, help="BBMP KML directory or point file"),
    source_manifest: Path | None = typer.Option(None, help="Completed depth-run manifest"),
    convention_path: Path = typer.Option(DEFAULT_CONVENTION_PATH, help="Signed depth convention"),
    strict_depth_points: Path | None = typer.Option(
        None, help="Curated strict-depth CSV; defaults to groundtruth.points_csv"
    ),
    config: Path = typer.Option(DEFAULT_VALIDATION_CONFIG, help="Validation config for defaults"),
    admit_dirty_tree_reason: str | None = typer.Option(
        None,
        help="Explicit reason admitting a dirty source tree (recorded, never laundered)",
    ),
    basin_class_raster: Path | None = typer.Option(
        None, help="Basin-class raster; required when scoring an excluded product"
    ),
    exclude_basin_classes: str = typer.Option(
        "",
        help="Comma-separated basin_class values the scored product excludes (e.g. '1,2,3')",
    ),
    exclusion_mode: str = typer.Option(
        "segment_touch",
        help="Exclusion semantics of the scored product: segment_touch or cell",
    ),
) -> None:
    with config.open(encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}
    gt_cfg = cfg.get("groundtruth", {})
    segment_cfg = cfg.get("segment_validation", {})
    g1_cfg = cfg.get("g1", {})
    g3_cfg = cfg.get("g3", {})
    required_g1 = {
        "hit_rate_minimum",
        "null_lift_minimum",
        "null_draws",
        "seed",
        "expected_raw_bbmp_points",
    }
    missing_g1 = (
        sorted(required_g1 - set(g1_cfg)) if isinstance(g1_cfg, dict) else sorted(required_g1)
    )
    required_groundtruth = {
        "max_snap_distance_m",
        "points_csv",
        "scoring_event_window_start",
        "scoring_event_window_end",
    }
    missing_groundtruth = sorted(required_groundtruth - set(gt_cfg))
    missing_g3 = ["in_band_minimum"] if "in_band_minimum" not in g3_cfg else []
    if missing_g1 or missing_groundtruth or missing_g3:
        missing = [f"g1.{key}" for key in missing_g1]
        missing.extend(f"groundtruth.{key}" for key in missing_groundtruth)
        missing.extend(f"g3.{key}" for key in missing_g3)
        raise typer.BadParameter("validation config is missing: " + ", ".join(missing))
    resolved_road = _repo_path(road_raster or gt_cfg.get("road_segment_id_path", ""))
    resolved_lookup = _repo_path(lookup_csv or segment_cfg.get("roads_segment_lookup_path", ""))
    resolved_complaints = _repo_path(complaints or gt_cfg.get("bbmp_kml_dir", ""))
    resolved_strict_depth = _repo_path(strict_depth_points or gt_cfg["points_csv"])
    resolved_source = _repo_path(source_manifest or (Path(depth_raster).parent / "manifest.json"))
    excluded_classes: frozenset[int] | None = None
    if exclude_basin_classes.strip():
        try:
            excluded_classes = frozenset(
                int(item) for item in exclude_basin_classes.split(",") if item.strip()
            )
        except ValueError as exc:
            raise typer.BadParameter(
                f"exclude_basin_classes must be comma-separated integers: {exc}"
            ) from exc
        if not excluded_classes or any(value <= 0 for value in excluded_classes):
            raise typer.BadParameter("exclude_basin_classes values must be positive integers")
        if basin_class_raster is None:
            raise typer.BadParameter("exclude_basin_classes requires --basin-class-raster")
    try:
        result = score_g1(
            depth_raster=_repo_path(depth_raster),
            road_raster=resolved_road,
            lookup_csv=resolved_lookup,
            complaints=resolved_complaints,
            output_dir=_repo_path(output_dir),
            source_manifest=resolved_source,
            product_path=_repo_path(product_file),
            convention_path=_repo_path(convention_path),
            strict_depth_points=resolved_strict_depth,
            event_window_start=str(gt_cfg["scoring_event_window_start"]),
            event_window_end=str(gt_cfg["scoring_event_window_end"]),
            expected_bbmp_count=int(g1_cfg["expected_raw_bbmp_points"]),
            seed=int(g1_cfg["seed"]),
            n_nulls=int(g1_cfg["null_draws"]),
            max_snap_distance_m=float(gt_cfg["max_snap_distance_m"]),
            hit_rate_threshold=float(g1_cfg["hit_rate_minimum"]),
            lift_threshold=float(g1_cfg["null_lift_minimum"]),
            depth_in_band_threshold=float(g3_cfg["in_band_minimum"]),
            admit_dirty_tree_reason=admit_dirty_tree_reason,
            basin_class_raster=(
                _repo_path(basin_class_raster) if basin_class_raster is not None else None
            ),
            excluded_basin_classes=excluded_classes,
            exclusion_mode=exclusion_mode,
        )
    except ConfigResolutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    report = result["report"]
    typer.echo(f"status={report['status']}")
    typer.echo(f"manifest={_repo_path(output_dir) / 'manifest.json'}")
    typer.echo(f"report={_repo_path(output_dir) / 'g1_score.json'}")
    g1_score = report["g1"]["score"]
    typer.echo(
        f"G1: denominator={g1_score['fixed_denominator_points']} "
        f"hits={g1_score['hits']} hit_rate={g1_score['hit_rate']} "
        f"lift={g1_score['null']['lift']} verdict={report['g1']['status']}"
    )
    framing = report["g1"].get("metric_framing") or {}
    point_mediated = framing.get("point_mediated")
    exclusion_block = report.get("storage_water_exclusion")
    if isinstance(exclusion_block, dict):
        typer.echo(
            "scoring EXCLUDED product: mode="
            f"{exclusion_block['mode']} classes={exclusion_block['excluded_classes']} "
            "(consistency assert recomputed with the same exclusion)"
        )
    if framing.get("status") == "DISCLOSED" and isinstance(point_mediated, dict):
        typer.echo(
            "G1 metric framing (owner-acknowledged anchor error): signed 60% anchored to "
            f"POINT-mediated {point_mediated['hits']}/{point_mediated['denominator']} at "
            f"{point_mediated['threshold_m']} m; this report scores SEGMENT-mediated "
            f"{g1_score['hits']}/{g1_score['fixed_denominator_points']} "
            f"(difference {framing['metric_difference_hits']} hits); threshold unchanged"
        )
    elif framing.get("status") == "unavailable":
        typer.echo(f"G1 metric framing unavailable: {framing.get('reason')}", err=True)
    for name, score in report["g3"]["conventions"].items():
        typer.echo(
            f"G3 {name}: in_band={score['within_band_count']}/{score['denominator']} "
            f"rate={score['in_band_rate']} diagnostic={score['diagnostic_threshold_result']} "
            "signed_gate=BLOCKED"
        )


if __name__ == "__main__":
    app()
