"""Per-Road-Segment Flood Status Validation and Rescoring Harness.

Implements topological per-road-segment validation for JALADHAR:
- Evaluates flood status per road segment on the 10 m canonical grid lattice.
- Pre-defined, rule-based topological flood definition:
    At least F% of segment cells, OR at least N contiguous cells, exceed depth D.
    Primary rule (held fixed): D = 0.15 m, F = 20%, N = 3 cells (30 m bottleneck).
- Evaluates against three independent label sets:
    1. BBMP flood-prone locations (399 raw / 398 in-domain points snapped to road segments)
    2. Ground-truth geolocated flood locations (24 points snapped to road segments)
    3. Sentinel-1 SAR change-detection flood extent (5 Sept 2022 flood vs 12 Aug 2022 pre-flood,
       calibrated sigma0 <= -16 dB change, permanent water bodies excluded).
- Mandatory Monte Carlo Null Model (>= 5,000 draws) with 95% CI and lift ratio calculation.
- Stratification by Urban Density (OPEN, MODERATE, DENSE), Road Class (OSM highway), and Arterials.
- Strict enforcement of CLAUDE.md Rules 1-7 and Verification Rules V1-V8.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
import rasterio
import scipy.stats as stats
import yaml
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from jaladhar.provenance import require_clean_git, write_json_atomic
from jaladhar.terrain.grid import build_grid, load_config
from jaladhar.validation.density import run_density_pipeline
from jaladhar.validation.event_replay import compute_s1_sigma0_db
from jaladhar.validation.groundtruth import (
    load_and_validate_groundtruth,
    resolve_groundtruth_scoring_config,
    select_event_groundtruth,
)

REPO = Path(__file__).resolve().parents[3]

# Fixed primary rule parameters (Part A)
PRIMARY_DEPTH_THRESHOLD_M = 0.15
PRIMARY_FRACTION_THRESHOLD = 0.20
PRIMARY_CONTIGUOUS_CELLS = 3


def git_sha() -> str:
    """Return a strict, clean-tree Git SHA for the standalone report."""
    return require_clean_git(REPO)


@dataclass(frozen=True)
class SegmentFloodRule:
    """Definition of what constitutes a flooded road segment."""

    depth_threshold_m: float
    fraction_threshold: float
    contiguous_cells: int
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth_threshold_m": self.depth_threshold_m,
            "fraction_threshold": self.fraction_threshold,
            "contiguous_cells": self.contiguous_cells,
            "rationale": self.rationale,
        }


PRIMARY_RULE = SegmentFloodRule(
    depth_threshold_m=PRIMARY_DEPTH_THRESHOLD_M,
    fraction_threshold=PRIMARY_FRACTION_THRESHOLD,
    contiguous_cells=PRIMARY_CONTIGUOUS_CELLS,
    rationale=(
        "Depth threshold D=0.15 m corresponds to municipal vehicle impassability "
        "(curb overflow and passenger car stalling limit). Fraction threshold F=20% captures "
        "widespread inundation across short-to-medium segments. Contiguous count N=3 cells (30 m) "
        "captures localized bottlenecks that block traffic on long arterial segments."
    ),
)


@dataclass(frozen=True)
class SegmentScoreResult:
    """Contingency scores and null model comparison for a stratum/label set."""

    total_segments: int
    predicted_flooded_segments: int
    observed_flooded_segments: int
    hits: int
    misses: int
    false_alarms: int
    correct_negatives: int
    pod: float | None
    far: float | None
    csi: float | None
    null_tp_mean: float | None
    null_pod_mean: float | None
    null_csi_mean: float | None
    null_csi_ci_low: float | None
    null_csi_ci_high: float | None
    lift_ratio_pod: float | None
    lift_ratio_csi: float | None
    is_positive_unlabeled: bool = False
    pu_caveat: str | None = None
    exact_p_value_fisher: float | None = None
    exact_p_value_binom: float | None = None
    pod_ci_95: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.is_positive_unlabeled:
            return {
                "total_segments": self.total_segments,
                "predicted_flooded_segments": self.predicted_flooded_segments,
                "observed_flooded_segments": self.observed_flooded_segments,
                "hits": self.hits,
                "misses": self.misses,
                "false_alarms_unobserved": self.false_alarms,
                "pod": None if (self.pod is None or math.isnan(self.pod)) else round(self.pod, 6),
                "pod_95ci": (
                    [round(self.pod_ci_95[0], 6), round(self.pod_ci_95[1], 6)]
                    if self.pod_ci_95 and not math.isnan(self.pod_ci_95[0])
                    else None
                ),
                "csi_interpretable": False,
                "far_interpretable": False,
                "csi": None,
                "far": None,
                "pu_labeling_defect_rationale": (
                    self.pu_caveat
                    or (
                        "Positive-Unlabeled (PU) dataset: absence from positive list is unlabelled, "
                        "not a true negative. Reported 'false alarms' include genuinely flooded segments "
                        "not in the list. CSI and FAR require true negatives and are not interpretable. "
                        "POD and Lift over random null (TP / E[TP]) survive."
                    )
                ),
                "null_tp_mean": (
                    None
                    if (self.null_tp_mean is None or math.isnan(self.null_tp_mean))
                    else round(self.null_tp_mean, 4)
                ),
                "null_pod_mean": (
                    None
                    if (self.null_pod_mean is None or math.isnan(self.null_pod_mean))
                    else round(self.null_pod_mean, 6)
                ),
                "lift_ratio_pod": (
                    None
                    if (self.lift_ratio_pod is None or math.isnan(self.lift_ratio_pod))
                    else round(self.lift_ratio_pod, 4)
                ),
                "lift_ratio_csi": None,
                "exact_p_value_fisher": (
                    None
                    if self.exact_p_value_fisher is None
                    else round(self.exact_p_value_fisher, 6)
                ),
                "exact_p_value_binom": (
                    None if self.exact_p_value_binom is None else round(self.exact_p_value_binom, 6)
                ),
            }

        return {
            "total_segments": self.total_segments,
            "predicted_flooded_segments": self.predicted_flooded_segments,
            "observed_flooded_segments": self.observed_flooded_segments,
            "hits": self.hits,
            "misses": self.misses,
            "false_alarms": self.false_alarms,
            "correct_negatives": self.correct_negatives,
            "pod": None if (self.pod is None or math.isnan(self.pod)) else round(self.pod, 6),
            "pod_95ci": (
                [round(self.pod_ci_95[0], 6), round(self.pod_ci_95[1], 6)]
                if self.pod_ci_95 and not math.isnan(self.pod_ci_95[0])
                else None
            ),
            "csi_interpretable": True,
            "far_interpretable": True,
            "far": None if (self.far is None or math.isnan(self.far)) else round(self.far, 6),
            "csi": None if (self.csi is None or math.isnan(self.csi)) else round(self.csi, 6),
            "reference_inventory_caveat": (
                "Domain-wide microwave change-detection reference. Subject to layover/double-bounce "
                "miss rate in dense urban and single -16 dB backscatter cut."
            ),
            "null_tp_mean": (
                None
                if (self.null_tp_mean is None or math.isnan(self.null_tp_mean))
                else round(self.null_tp_mean, 4)
            ),
            "null_pod_mean": (
                None
                if (self.null_pod_mean is None or math.isnan(self.null_pod_mean))
                else round(self.null_pod_mean, 6)
            ),
            "null_csi_mean": (
                None
                if (self.null_csi_mean is None or math.isnan(self.null_csi_mean))
                else round(self.null_csi_mean, 6)
            ),
            "null_csi_95ci": [
                (
                    None
                    if (self.null_csi_ci_low is None or math.isnan(self.null_csi_ci_low))
                    else round(self.null_csi_ci_low, 6)
                ),
                (
                    None
                    if (self.null_csi_ci_high is None or math.isnan(self.null_csi_ci_high))
                    else round(self.null_csi_ci_high, 6)
                ),
            ],
            "lift_ratio_pod": (
                None
                if (self.lift_ratio_pod is None or math.isnan(self.lift_ratio_pod))
                else round(self.lift_ratio_pod, 4)
            ),
            "lift_ratio_csi": (
                None
                if (self.lift_ratio_csi is None or math.isnan(self.lift_ratio_csi))
                else round(self.lift_ratio_csi, 4)
            ),
            "exact_p_value_fisher": (
                None if self.exact_p_value_fisher is None else round(self.exact_p_value_fisher, 6)
            ),
            "exact_p_value_binom": (
                None if self.exact_p_value_binom is None else round(self.exact_p_value_binom, 6)
            ),
        }


def build_road_network_index(
    road_raster_path: Path = REPO / "data/processed/road_segment_id.tif",
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    cKDTree,
    rasterio.Affine,
    tuple[int, int],
]:
    """Load road raster and build spatial index and intra-segment adjacency graph."""
    with rasterio.open(road_raster_path) as src:
        road_raster = src.read(1)
        grid_transform = src.transform
        grid_shape = (src.height, src.width)

    road_mask = road_raster > 0
    r_rows, r_cols = np.where(road_mask)
    r_segs = road_raster[r_rows, r_cols]
    n_road_cells = len(r_segs)

    # Road cell coordinates (UTM 43N)
    road_xs, road_ys = rasterio.transform.xy(grid_transform, r_rows, r_cols)
    road_coords = np.column_stack([road_xs, road_ys])
    tree = cKDTree(road_coords)

    # Intra-segment 8-neighbor adjacency graph
    h, w = grid_shape
    road_idx_grid = np.full((h, w), -1, dtype=np.int32)
    road_idx_grid[road_mask] = np.arange(n_road_cells, dtype=np.int32)

    adj_src, adj_dst = [], []
    for dr, dc in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
        nbr_r = r_rows + dr
        nbr_c = r_cols + dc
        valid = (nbr_r >= 0) & (nbr_r < h) & (nbr_c >= 0) & (nbr_c < w)
        src_nodes = np.where(valid)[0]
        nbr_nodes = road_idx_grid[nbr_r[valid], nbr_c[valid]]
        valid_edge = (nbr_nodes >= 0) & (r_segs[src_nodes] == r_segs[nbr_nodes])
        adj_src.append(src_nodes[valid_edge])
        adj_dst.append(nbr_nodes[valid_edge])

    adj_src_arr = np.concatenate(adj_src)
    adj_dst_arr = np.concatenate(adj_dst)

    return (
        road_raster,
        r_rows,
        r_cols,
        r_segs,
        adj_src_arr,
        adj_dst_arr,
        tree,
        grid_transform,
        grid_shape,
    )


def evaluate_segments_from_mask(
    cell_mask: np.ndarray,
    road_mask: np.ndarray,
    r_segs: np.ndarray,
    adj_src: np.ndarray,
    adj_dst: np.ndarray,
    fraction_threshold: float = PRIMARY_FRACTION_THRESHOLD,
    contiguous_cells: int = PRIMARY_CONTIGUOUS_CELLS,
) -> pd.DataFrame:
    """Evaluate whether each segment meets the flood criteria under the given cell mask."""
    n_road_cells = len(r_segs)
    r_flooded = cell_mask[road_mask]

    # Filter adjacency edges to pairs where both cells are flooded
    edge_mask = r_flooded[adj_src] & r_flooded[adj_dst]
    graph = coo_matrix(
        (np.ones(np.sum(edge_mask), dtype=bool), (adj_src[edge_mask], adj_dst[edge_mask])),
        shape=(n_road_cells, n_road_cells),
    )
    _, labels = connected_components(graph, directed=False)
    comp_sizes = np.bincount(labels)
    r_comp_sizes = np.where(r_flooded, comp_sizes[labels], 0)

    df = pd.DataFrame({"seg_id": r_segs, "flooded": r_flooded, "comp_size": r_comp_sizes})
    grouped = df.groupby("seg_id").agg(
        total_cells=("flooded", "count"),
        flooded_cells=("flooded", "sum"),
        max_contig=("comp_size", "max"),
    )
    grouped["pct_flooded"] = grouped["flooded_cells"] / grouped["total_cells"]
    grouped["is_flooded"] = (grouped["pct_flooded"] >= fraction_threshold) | (
        grouped["max_contig"] >= contiguous_cells
    )
    return grouped


def compute_s1_change_detection_mask(
    repo: Path = REPO,
    val_cfg: dict[str, Any] | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Compute calibrated Sentinel-1 change detection flood mask on canonical grid.

    Returns:
        (s1_change_mask_unmasked, s1_change_mask_masked, metadata)
    """
    if val_cfg is None:
        with (repo / "configs/validation.yaml").open() as handle:
            val_cfg = yaml.safe_load(handle)
    sar_cfg = val_cfg["sar_water_classifier"]
    domain_cfg = load_config(repo / val_cfg["domain_config"])
    grid, _ = build_grid(domain_cfg, repo)
    dst_shape = (grid.height, grid.width)

    s1_flood_tif = repo / sar_cfg["flood_scene_tif"]
    s1_flood_xml = repo / sar_cfg["flood_calibration_xml"]
    s1_pre_tif = repo / sar_cfg["pre_scene_tif"]
    s1_pre_xml = repo / sar_cfg["pre_calibration_xml"]

    sigma0_flood, valid_flood = compute_s1_sigma0_db(
        s1_flood_tif, s1_flood_xml, grid.bounds, dst_shape, grid.transform
    )
    sigma0_pre, valid_pre = compute_s1_sigma0_db(
        s1_pre_tif, s1_pre_xml, grid.bounds, dst_shape, grid.transform
    )

    with rasterio.open(repo / sar_cfg["basin_class_path"]) as src:
        basin_class = src.read(1)
    perm_water = (basin_class == 1) | (basin_class == 2) | (basin_class == 3)

    water_threshold_db = float(sar_cfg["water_threshold_db"])
    flood_wet = (sigma0_flood <= water_threshold_db) & valid_flood
    pre_wet = (sigma0_pre <= water_threshold_db) & valid_pre
    change_wet_unmasked = flood_wet & (~pre_wet)
    change_wet_masked = change_wet_unmasked & (~perm_water)

    meta = {
        "water_threshold_db": water_threshold_db,
        "flood_scene_wet_cells": int(np.sum(flood_wet)),
        "pre_scene_wet_cells": int(np.sum(pre_wet)),
        "persistent_wet_cells": int(np.sum(flood_wet & pre_wet)),
        "change_detection_cells_unmasked": int(np.sum(change_wet_unmasked)),
        "change_detection_cells_masked": int(np.sum(change_wet_masked)),
        "permanent_water_excluded_cells": int(np.sum(perm_water)),
    }
    return change_wet_unmasked, change_wet_masked, meta


def snap_points_to_segments(
    points_wgs84: list[tuple[float, float]],
    tree: cKDTree,
    r_segs: np.ndarray,
    max_distance_m: float | None = None,
    point_ids: list[str] | None = None,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    """Snap WGS84 lat/lon points to nearest road segments in UTM 43N coordinates.

    Args:
        points_wgs84: List of (lon, lat) tuples in EPSG:4326.
        tree: Spatial index of road network cell coordinates in EPSG:32643.
        r_segs: Segment IDs corresponding to each indexed tree coordinate.
        max_distance_m: Optional threshold beyond which points are flagged as excluded.

    Returns:
        (snapped_segment_ids, distances_m, point_details)
    """
    trans_wgs_to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
    snapped_segs: list[int] = []
    dists: list[float] = []
    details: list[dict[str, Any]] = []

    if point_ids is not None and len(point_ids) != len(points_wgs84):
        raise ValueError("point_ids must have the same length as points_wgs84")

    for point_index, (lon, lat) in enumerate(points_wgs84):
        ux, uy = trans_wgs_to_utm.transform(lon, lat)
        d, idx = tree.query([ux, uy])
        seg_id = int(r_segs[idx])
        d_val = float(d)
        is_excluded = max_distance_m is not None and d_val > max_distance_m

        snapped_segs.append(seg_id)
        dists.append(d_val)
        details.append(
            {
                "id": point_ids[point_index] if point_ids is not None else None,
                "lon": lon,
                "lat": lat,
                "utm_x": ux,
                "utm_y": uy,
                "snap_distance_m": round(d_val, 2),
                "snapped_segment_id": seg_id,
                "excluded_by_snap_distance": is_excluded,
                "eligible_for_scoring": not is_excluded,
                "rejection_reason": (
                    "snap_distance_exceeds_configured_max" if is_excluded else None
                ),
            }
        )

    return np.array(snapped_segs, dtype=np.int32), np.array(dists, dtype=np.float32), details


def compute_contingency_and_null(
    pred_mask: pd.Series | np.ndarray,
    obs_mask: pd.Series | np.ndarray,
    n_draws: int = 5000,
    seed: int = 42,
    is_positive_unlabeled: bool = False,
    pu_caveat: str | None = None,
) -> SegmentScoreResult:
    """Compute 2x2 contingency metrics, exact statistical tests, and Monte Carlo null model comparison."""
    p = np.asarray(pred_mask, dtype=bool)
    o = np.asarray(obs_mask, dtype=bool)
    n_tot = len(p)
    n_pred = int(np.sum(p))
    n_obs = int(np.sum(o))

    tp = int(np.sum(p & o))
    fn = n_obs - tp
    fp = n_pred - tp
    tn = n_tot - tp - fn - fp

    pod = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    far = fp / (tp + fp) if (tp + fp) > 0 else float("nan")
    csi = tp / (tp + fn + fp) if (tp + fn + fp) > 0 else float("nan")

    p_null = n_pred / n_tot if n_tot > 0 else 0.0

    # Exact Clopper-Pearson 95% Confidence Interval on POD
    if n_obs > 0:
        ci_low = float(stats.beta.ppf(0.025, tp, n_obs - tp + 1)) if tp > 0 else 0.0
        ci_high = float(stats.beta.ppf(0.975, tp + 1, n_obs - tp)) if tp < n_obs else 1.0
        pod_ci_95 = [ci_low, ci_high]
        p_val_binom = float(stats.binom.sf(tp - 1, n_obs, p_null)) if p_null > 0 else 1.0
    else:
        pod_ci_95 = [float("nan"), float("nan")]
        p_val_binom = float("nan")

    # Exact Fisher test (one-tailed greater)
    if n_tot > 0:
        table = [[tp, fp], [fn, tn]]
        _, p_val_fisher = stats.fisher_exact(table, alternative="greater")
        p_val_fisher = float(p_val_fisher)
    else:
        p_val_fisher = float("nan")

    if n_tot > 0 and n_obs > 0 and n_pred > 0:
        rng = np.random.default_rng(seed)
        null_tp = rng.hypergeometric(ngood=n_pred, nbad=n_tot - n_pred, nsample=n_obs, size=n_draws)
        null_pod = null_tp / n_obs
        null_csi = null_tp / (n_pred + n_obs - null_tp)

        null_tp_mean = float(np.mean(null_tp))
        null_pod_mean = float(np.mean(null_pod))
        null_csi_mean = float(np.mean(null_csi))
        null_csi_ci_low = float(np.percentile(null_csi, 2.5))
        null_csi_ci_high = float(np.percentile(null_csi, 97.5))

        lift_pod = (
            pod / null_pod_mean if (null_pod_mean > 0 and not math.isnan(pod)) else float("nan")
        )
        lift_csi = (
            csi / null_csi_mean if (null_csi_mean > 0 and not math.isnan(csi)) else float("nan")
        )
    else:
        null_tp_mean = float("nan")
        null_pod_mean = float("nan")
        null_csi_mean = float("nan")
        null_csi_ci_low = float("nan")
        null_csi_ci_high = float("nan")
        lift_pod = float("nan")
        lift_csi = float("nan")

    return SegmentScoreResult(
        total_segments=n_tot,
        predicted_flooded_segments=n_pred,
        observed_flooded_segments=n_obs,
        hits=tp,
        misses=fn,
        false_alarms=fp,
        correct_negatives=tn,
        pod=pod,
        far=far,
        csi=csi,
        null_tp_mean=null_tp_mean,
        null_pod_mean=null_pod_mean,
        null_csi_mean=null_csi_mean,
        null_csi_ci_low=null_csi_ci_low,
        null_csi_ci_high=null_csi_ci_high,
        lift_ratio_pod=lift_pod,
        lift_ratio_csi=lift_csi,
        is_positive_unlabeled=is_positive_unlabeled,
        pu_caveat=pu_caveat,
        exact_p_value_fisher=p_val_fisher,
        exact_p_value_binom=p_val_binom,
        pod_ci_95=pod_ci_95,
    )


def _resolve_segment_groundtruth_config(
    val_cfg: dict[str, Any], repo_root: Path = REPO
) -> tuple[str, str, float, Path]:
    """Resolve every ground-truth input needed later by segment scoring."""
    gt_cfg = val_cfg.get("groundtruth", {})
    scoring_start, scoring_end, max_snap_distance_m = resolve_groundtruth_scoring_config(gt_cfg)
    try:
        points_csv = repo_root / str(gt_cfg["points_csv"])
    except KeyError as exc:
        raise ValueError("validation groundtruth.points_csv is required") from exc
    return scoring_start, scoring_end, max_snap_distance_m, points_csv


def resolve_segment_validation_config(
    val_cfg: dict[str, Any], repo_root: Path = REPO
) -> dict[str, Path]:
    """Resolve all segment-rescore inputs before derived work starts."""
    required = {
        "groundtruth.road_segment_id_path": val_cfg.get("groundtruth", {}).get(
            "road_segment_id_path"
        ),
        "groundtruth.bbmp_kml_dir": val_cfg.get("groundtruth", {}).get("bbmp_kml_dir"),
        "density_stratification.output_density_classes_path": val_cfg.get(
            "density_stratification", {}
        ).get("output_density_classes_path"),
        "segment_validation.roads_segment_lookup_path": val_cfg.get("segment_validation", {}).get(
            "roads_segment_lookup_path"
        ),
        "segment_validation.underpass_register_csv": val_cfg.get("segment_validation", {}).get(
            "underpass_register_csv"
        ),
        "sar_water_classifier.flood_scene_tif": val_cfg.get("sar_water_classifier", {}).get(
            "flood_scene_tif"
        ),
        "sar_water_classifier.flood_calibration_xml": val_cfg.get("sar_water_classifier", {}).get(
            "flood_calibration_xml"
        ),
        "sar_water_classifier.pre_scene_tif": val_cfg.get("sar_water_classifier", {}).get(
            "pre_scene_tif"
        ),
        "sar_water_classifier.pre_calibration_xml": val_cfg.get("sar_water_classifier", {}).get(
            "pre_calibration_xml"
        ),
        "sar_water_classifier.basin_class_path": val_cfg.get("sar_water_classifier", {}).get(
            "basin_class_path"
        ),
    }
    missing = [key for key, value in required.items() if not value]
    threshold = val_cfg.get("sar_water_classifier", {}).get("water_threshold_db")
    if threshold is None:
        missing.append("sar_water_classifier.water_threshold_db")
    else:
        try:
            threshold_value = float(threshold)
        except (TypeError, ValueError) as exc:
            raise ValueError("sar_water_classifier.water_threshold_db must be numeric") from exc
        if not np.isfinite(threshold_value):
            raise ValueError("sar_water_classifier.water_threshold_db must be finite")
    if missing:
        raise KeyError(f"Segment validation config missing keys: {missing}")
    return {key: repo_root / str(value) for key, value in required.items()}


def load_phase3_closing_water_budget(manifest_path: Path) -> dict[str, Any]:
    """Build the closing account only from the completed Phase 3 manifest."""
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "completed":
        raise RuntimeError("Phase 3 manifest is not completed")
    mass = manifest.get("mass_balance")
    if not isinstance(mass, dict):
        raise RuntimeError("Phase 3 manifest lacks the realized mass_balance block; rerun required")
    required = [
        "rain_in_m3",
        "boundary_out_m3",
        "drain_out_m3",
        "infil_out_m3",
        "v_current_m3",
        "v_initial_m3",
        "residual_m3",
        "relative_residual",
        "created_by_clamping_m3",
    ]
    missing = [key for key in required if key not in mass]
    if missing:
        raise RuntimeError(f"Phase 3 mass_balance is missing keys: {missing}")
    rain = float(mass["rain_in_m3"])
    if not np.isfinite(rain) or rain <= 0:
        raise ValueError("Phase 3 realized rainfall volume must be finite and positive")

    def term(name: str) -> float:
        value = float(mass[name])
        if not np.isfinite(value):
            raise ValueError(f"Phase 3 mass term {name} is not finite")
        return value

    boundary = term("boundary_out_m3")
    drain = term("drain_out_m3")
    infiltration = term("infil_out_m3")
    storage = term("v_current_m3")
    initial = term("v_initial_m3")
    residual = term("residual_m3")
    relative = term("relative_residual")
    created_by_clamping = term("created_by_clamping_m3")
    derived_residual = (
        storage - initial - rain + drain + infiltration + boundary - created_by_clamping
    )
    residual_scale = max(abs(rain), abs(initial), 1e-12)
    derived_relative = abs(derived_residual) / residual_scale
    if not np.isclose(residual, derived_residual, rtol=1e-10, atol=1e-6):
        raise ValueError(
            "Phase 3 mass residual does not match its stated terms: "
            f"reported={residual}, derived={derived_residual}"
        )
    if not np.isclose(relative, derived_relative, rtol=1e-10, atol=1e-12):
        raise ValueError(
            "Phase 3 relative residual does not match its stated derivation: "
            f"reported={relative}, derived={derived_relative}"
        )
    duration = manifest.get("results", {}).get("simulation", {}).get("sim_duration_hours")
    return {
        "simulation_duration_hours": duration,
        "rainfall_in_m3": rain,
        "rainfall_in_pct": 100.0,
        "boundary_outfall_m3": boundary,
        "boundary_outfall_pct": boundary / rain * 100.0,
        "drain_sink_m3": drain,
        "drain_sink_pct": drain / rain * 100.0,
        "infiltration_m3": infiltration,
        "infiltration_pct": infiltration / rain * 100.0,
        "surface_storage_end_m3": storage,
        "surface_storage_end_pct": storage / rain * 100.0,
        "mass_residual_m3": residual,
        "mass_relative_residual": relative,
        "mass_residual_derivation_verified": True,
        "source_manifest": str(manifest_path),
        "note": "Realized mass terms copied from the completed Phase 3 solver manifest.",
    }


def _run_segment_validation_impl(
    *,
    phase3_run_dir: Path,
    output_dir: Path,
    val_config_path: Path,
    n_mc_draws: int,
    seed: int,
    run_git_sha: str,
    t0: float,
    val_cfg: dict,
    resolved_paths: dict,
    scoring_start: str,
    scoring_end: str,
    max_snap_distance_m: float,
    gt_points_csv: Path,
) -> dict[str, Any]:
    """Private implementation: all heavy work, no lifecycle (called by wrapper)."""
    # 1. Road index & graph
    (
        road_raster,
        r_rows,
        r_cols,
        r_segs,
        adj_src,
        adj_dst,
        tree,
        grid_transform,
        grid_shape,
    ) = build_road_network_index(resolved_paths["groundtruth.road_segment_id_path"])
    road_mask = road_raster > 0
    unique_segs = np.unique(r_segs)
    n_total_segs = len(unique_segs)

    # 2. Metadata: Density and Road Class Lookup
    density_raster_path = resolved_paths["density_stratification.output_density_classes_path"]
    if not density_raster_path.exists():
        run_density_pipeline(val_config_path)
    with rasterio.open(density_raster_path) as src:
        density_raster = src.read(1)
    r_densities = density_raster[r_rows, r_cols]

    seg_df = pd.DataFrame({"seg_id": r_segs, "density": r_densities})
    seg_density = seg_df.groupby("seg_id")["density"].agg(
        lambda x: x.mode().iloc[0] if len(x) > 0 else 0
    )
    density_map = {0: "OPEN", 1: "MODERATE", 2: "DENSE"}

    roads_lookup = pd.read_csv(
        resolved_paths["segment_validation.roads_segment_lookup_path"]
    ).set_index("segment_id")

    seg_meta = pd.DataFrame(index=unique_segs)
    seg_meta["density"] = seg_density.map(density_map)
    seg_meta["highway"] = roads_lookup.loc[seg_meta.index, "highway"].fillna("unclassified")
    seg_meta["name"] = roads_lookup.loc[seg_meta.index, "name"].fillna("")
    arterial_types = {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
    }
    seg_meta["is_arterial"] = seg_meta["highway"].isin(arterial_types)

    # 3. Load Modeled Depth Rasters
    depth_event_max_path = phase3_run_dir / "depth_event_maximum.tif"
    depth_sar_instant_path = phase3_run_dir / "depth_sar_instant_20220905_004028Z.tif"
    if not depth_event_max_path.exists() or not depth_sar_instant_path.exists():
        raise FileNotFoundError(
            f"Modeled depth rasters missing in {phase3_run_dir}. Required: depth_event_maximum.tif and depth_sar_instant_20220905_004028Z.tif"
        )

    with rasterio.open(depth_event_max_path) as src:
        depth_event_max = src.read(1)
    with rasterio.open(depth_sar_instant_path) as src:
        depth_sar_instant = src.read(1)

    # Evaluate Modeled Depth under Primary Rule (D=0.15 m, F=20%, N=3)
    model_event_eval = evaluate_segments_from_mask(
        depth_event_max >= PRIMARY_RULE.depth_threshold_m,
        road_mask,
        r_segs,
        adj_src,
        adj_dst,
        PRIMARY_RULE.fraction_threshold,
        PRIMARY_RULE.contiguous_cells,
    )
    model_sar_eval = evaluate_segments_from_mask(
        depth_sar_instant >= PRIMARY_RULE.depth_threshold_m,
        road_mask,
        r_segs,
        adj_src,
        adj_dst,
        PRIMARY_RULE.fraction_threshold,
        PRIMARY_RULE.contiguous_cells,
    )

    seg_meta["pred_event_max"] = model_event_eval["is_flooded"]
    seg_meta["pred_sar_instant"] = model_sar_eval["is_flooded"]

    # 4. Label Set 1: BBMP Points Snapping
    bbmp_dir = resolved_paths["groundtruth.bbmp_kml_dir"]
    bbmp_bbox = val_cfg["groundtruth"].get("bbmp_bbox_wgs84")
    if not isinstance(bbmp_bbox, list) or len(bbmp_bbox) != 4:
        raise KeyError(
            "validation.groundtruth.bbmp_bbox_wgs84 must be [min_lon, min_lat, max_lon, max_lat]"
        )
    min_lon, min_lat, max_lon, max_lat = (float(value) for value in bbmp_bbox)
    bbmp_points_wgs: list[tuple[float, float]] = []
    bbmp_records: list[dict[str, Any]] = []
    bbmp_points_raw_count = 0

    for kml_file in sorted(bbmp_dir.glob("*.kml")):
        gdf = gpd.read_file(kml_file)
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom.geom_type == "Point":
                bbmp_points_raw_count += 1
                # Check within domain bbox bounds
                if min_lon <= geom.x <= max_lon and min_lat <= geom.y <= max_lat:
                    bbmp_points_wgs.append((geom.x, geom.y))
                    bbmp_records.append(
                        {
                            "kml": kml_file.name,
                            "name": str(row.get("Name", "")),
                            "lon": geom.x,
                            "lat": geom.y,
                        }
                    )

    bbmp_snapped_segs, bbmp_dists, bbmp_snap_details = snap_points_to_segments(
        bbmp_points_wgs, tree, r_segs
    )
    bbmp_pos_unique_segs = set(bbmp_snapped_segs)
    seg_meta["obs_bbmp"] = seg_meta.index.isin(bbmp_pos_unique_segs)

    # 5. Label Set 2: Ground Truth Points Snapping (24 points)
    gt_points_all, _ = load_and_validate_groundtruth(gt_points_csv, bbmp_dir)
    gt_points_date_eligible, gt_date_rejections = select_event_groundtruth(
        gt_points_all,
        scoring_start,
        scoring_end,
    )
    date_eligible_ids = {point.id for point in gt_points_date_eligible}
    gt_points_wgs = [(p.lon, p.lat) for p in gt_points_all]
    gt_snapped_segs, gt_dists, gt_snap_details = snap_points_to_segments(
        gt_points_wgs,
        tree,
        r_segs,
        max_distance_m=max_snap_distance_m,
        point_ids=[point.id for point in gt_points_all],
    )
    gt_pos_unique_segs = {
        int(segment_id)
        for point, segment_id, detail in zip(
            gt_points_all, gt_snapped_segs, gt_snap_details, strict=True
        )
        if point.id in date_eligible_ids and detail["eligible_for_scoring"]
    }
    seg_meta["obs_gt"] = seg_meta.index.isin(gt_pos_unique_segs)

    # 6. Label Set 3: Sentinel-1 SAR Change Detection Reference
    s1_change_unmasked, s1_change_masked, s1_meta = compute_s1_change_detection_mask(REPO, val_cfg)
    sar_obs_eval = evaluate_segments_from_mask(
        s1_change_masked,
        road_mask,
        r_segs,
        adj_src,
        adj_dst,
        PRIMARY_RULE.fraction_threshold,
        PRIMARY_RULE.contiguous_cells,
    )
    seg_meta["obs_sar_change"] = sar_obs_eval["is_flooded"]

    # 7. Compute Citywide Headline Results against Mandatory Null Model (PU Labeling aware)
    bbmp_headline = compute_contingency_and_null(
        seg_meta["pred_event_max"],
        seg_meta["obs_bbmp"],
        n_draws=n_mc_draws,
        seed=seed,
        is_positive_unlabeled=True,
        pu_caveat=(
            "BBMP is a flood-prone complaint register, not an inventory of 5 Sept 2022. "
            "Absence from it is unlabelled, not negative. CSI and FAR are not interpretable; "
            "POD and Lift over random null (TP / E[TP]) survive."
        ),
    )
    sar_headline = compute_contingency_and_null(
        seg_meta["pred_sar_instant"],
        seg_meta["obs_sar_change"],
        n_draws=n_mc_draws,
        seed=seed,
        is_positive_unlabeled=False,
    )

    # 8. PART C: Stratification by Urban Density
    density_strata_bbmp: dict[str, dict[str, Any]] = {}
    density_strata_sar: dict[str, dict[str, Any]] = {}
    for d_name in ["OPEN", "MODERATE", "DENSE"]:
        sub = seg_meta[seg_meta["density"] == d_name]
        density_strata_bbmp[d_name] = compute_contingency_and_null(
            sub["pred_event_max"],
            sub["obs_bbmp"],
            n_draws=n_mc_draws,
            seed=seed,
            is_positive_unlabeled=True,
        ).to_dict()
        density_strata_sar[d_name] = compute_contingency_and_null(
            sub["pred_sar_instant"],
            sub["obs_sar_change"],
            n_draws=n_mc_draws,
            seed=seed,
            is_positive_unlabeled=False,
        ).to_dict()

    # 9. PART C: Stratification by Road Class
    road_class_strata_bbmp: dict[str, dict[str, Any]] = {}
    road_class_strata_sar: dict[str, dict[str, Any]] = {}
    key_road_classes = [
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "residential",
        "service",
        "living_street",
        "track",
        "unclassified",
    ]
    for hw_class in key_road_classes:
        sub = seg_meta[seg_meta["highway"] == hw_class]
        if len(sub) > 0:
            road_class_strata_bbmp[hw_class] = compute_contingency_and_null(
                sub["pred_event_max"],
                sub["obs_bbmp"],
                n_draws=n_mc_draws,
                seed=seed,
                is_positive_unlabeled=True,
            ).to_dict()
            road_class_strata_sar[hw_class] = compute_contingency_and_null(
                sub["pred_sar_instant"],
                sub["obs_sar_change"],
                n_draws=n_mc_draws,
                seed=seed,
                is_positive_unlabeled=False,
            ).to_dict()

    # 10. PART C: Arterial / Trunk Segments Alone
    arterial_sub = seg_meta[seg_meta["is_arterial"]]
    arterial_bbmp = compute_contingency_and_null(
        arterial_sub["pred_event_max"],
        arterial_sub["obs_bbmp"],
        n_draws=n_mc_draws,
        seed=seed,
        is_positive_unlabeled=True,
    )
    arterial_sar = compute_contingency_and_null(
        arterial_sub["pred_sar_instant"],
        arterial_sub["obs_sar_change"],
        n_draws=n_mc_draws,
        seed=seed,
        is_positive_unlabeled=False,
    )

    # Detailed Arterial density subsets
    arterial_density_bbmp: dict[str, dict[str, Any]] = {}
    for d_name in ["OPEN", "MODERATE", "DENSE"]:
        sub_art = seg_meta[seg_meta["is_arterial"] & (seg_meta["density"] == d_name)]
        arterial_density_bbmp[d_name] = compute_contingency_and_null(
            sub_art["pred_event_max"],
            sub_art["obs_bbmp"],
            n_draws=n_mc_draws,
            seed=seed,
            is_positive_unlabeled=True,
        ).to_dict()

    # 11. Sensitivity Sweeps Across Depth Thresholds D and Fractions F
    depth_sweep_results: list[dict[str, Any]] = []
    for d_th in [0.05, 0.10, 0.15, 0.20, 0.30]:
        ev = evaluate_segments_from_mask(
            depth_event_max >= d_th,
            road_mask,
            r_segs,
            adj_src,
            adj_dst,
            PRIMARY_RULE.fraction_threshold,
            PRIMARY_RULE.contiguous_cells,
        )
        res = compute_contingency_and_null(
            ev["is_flooded"],
            seg_meta["obs_bbmp"],
            n_draws=n_mc_draws,
            seed=seed,
            is_positive_unlabeled=True,
        )
        d_res = res.to_dict()
        d_res["depth_threshold_m"] = d_th
        depth_sweep_results.append(d_res)

    fraction_sweep_results: list[dict[str, Any]] = []
    for f_th in [0.10, 0.20, 0.30, 0.50]:
        ev = evaluate_segments_from_mask(
            depth_event_max >= PRIMARY_RULE.depth_threshold_m,
            road_mask,
            r_segs,
            adj_src,
            adj_dst,
            f_th,
            PRIMARY_RULE.contiguous_cells,
        )
        res = compute_contingency_and_null(
            ev["is_flooded"],
            seg_meta["obs_bbmp"],
            n_draws=n_mc_draws,
            seed=seed,
            is_positive_unlabeled=True,
        )
        f_res = res.to_dict()
        f_res["fraction_threshold"] = f_th
        fraction_sweep_results.append(f_res)

    # 12. Ground Truth Points Detailed Breakdown (All 24 and Snap Distance Filtered)
    gt_details: list[dict[str, Any]] = []
    gt_filtered_pos_segs: set[int] = set()

    for idx_gt, pt in enumerate(gt_points_all):
        seg_id = int(gt_snapped_segs[idx_gt])
        snap_d = float(gt_dists[idx_gt])
        is_excl = snap_d > max_snap_distance_m
        date_eligible = pt.id in date_eligible_ids
        eligible_for_scoring = date_eligible and not is_excl
        rejection_reasons: list[str] = []
        if not date_eligible:
            rejection_reasons.append("observed_date_outside_event_window")
        if is_excl:
            rejection_reasons.append("snap_distance_exceeds_configured_max")
        if eligible_for_scoring:
            gt_filtered_pos_segs.add(seg_id)

        seg_status = model_event_eval.loc[seg_id]
        hw = roads_lookup.loc[seg_id, "highway"] if seg_id in roads_lookup.index else "unclassified"
        name = roads_lookup.loc[seg_id, "name"] if seg_id in roads_lookup.index else ""
        gt_details.append(
            {
                "id": pt.id,
                "location_name": pt.location_name,
                "observed_date": pt.observed_date,
                "depth_band": (
                    f"{pt.depth_band_low_m}-{pt.depth_band_high_m}m"
                    if pt.depth_band_low_m
                    else "extent"
                ),
                "snapped_segment_id": seg_id,
                "snap_distance_m": round(snap_d, 2),
                "excluded_by_snap_distance": is_excl,
                "eligible_for_scoring": eligible_for_scoring,
                "rejection_reasons": rejection_reasons,
                "highway": hw,
                "osm_road_name": name,
                "segment_total_cells": int(seg_status["total_cells"]),
                "segment_flooded_cells": int(seg_status["flooded_cells"]),
                "segment_pct_flooded": round(float(seg_status["pct_flooded"]) * 100, 2),
                "segment_max_contig_cells": int(seg_status["max_contig"]),
                "model_segment_flooded": bool(seg_status["is_flooded"]),
            }
        )

    seg_meta["obs_gt_eligible"] = seg_meta.index.isin(gt_filtered_pos_segs)
    gt_headline_eligible = compute_contingency_and_null(
        seg_meta["pred_event_max"],
        seg_meta["obs_gt_eligible"],
        n_draws=n_mc_draws,
        seed=seed,
        is_positive_unlabeled=True,
        pu_caveat=(
            f"Replay-date-eligible ground-truth points with snap distance <= {max_snap_distance_m} m."
        ),
    )

    # 13. Deep-Dive: Trunk Road Signal & Tension Audit
    trunk_sub = seg_meta[seg_meta["highway"] == "trunk"]
    trunk_score = compute_contingency_and_null(
        trunk_sub["pred_event_max"],
        trunk_sub["obs_bbmp"],
        n_draws=n_mc_draws,
        seed=seed,
        is_positive_unlabeled=True,
    )
    trunk_motorway_sub = seg_meta[
        seg_meta["highway"].isin(["trunk", "motorway", "trunk_link", "motorway_link"])
    ]
    trunk_motorway_score = compute_contingency_and_null(
        trunk_motorway_sub["pred_event_max"],
        trunk_motorway_sub["obs_bbmp"],
        n_draws=n_mc_draws,
        seed=seed,
        is_positive_unlabeled=True,
    )

    trunk_eval_summary = {
        "trunk_alone": trunk_score.to_dict(),
        "trunk_plus_motorway_and_links": trunk_motorway_score.to_dict(),
        "arterials_as_class": arterial_bbmp.to_dict(),
        "arterials_by_density": arterial_density_bbmp,
        "interpretation": {
            "status": "UNRESOLVED",
            "reason": (
                "Current trunk metrics are reported above. Causal attribution to terrain, "
                "road geometry, or complaint coverage requires an independently reviewed "
                "case trace and is not inferred here."
            ),
        },
    }

    # 14. Deep-Dive: Underpass Co-Location Baseline & Classification Partition Audit
    underpass_csv = resolved_paths["segment_validation.underpass_register_csv"]
    if underpass_csv.exists():
        up_csv_df = pd.read_csv(underpass_csv)
        n_up_tot = len(up_csv_df)
        if n_up_tot == 0:
            raise ValueError("Underpass register is empty; no partition can be measured")
        is_flat = up_csv_df["max_dip_m"] <= 0.10
        is_sag = up_csv_df["max_dip_m"] > 0.30
        is_gap = (up_csv_df["max_dip_m"] > 0.10) & (up_csv_df["max_dip_m"] <= 0.30)
        is_crest = up_csv_df["max_crest_m"] > 0.30

        def count_and_pct(mask: pd.Series) -> dict[str, float | int]:
            return {"count": int(mask.sum()), "pct": round(float(mask.mean()) * 100, 2)}

        dip_partition_sum_pct = float(
            (is_flat.sum() + is_gap.sum() + is_sag.sum()) / n_up_tot * 100
        )
        joint_masks = {
            "flat_and_crest": is_flat & is_crest,
            "flat_and_no_crest": is_flat & ~is_crest,
            "gap_and_crest": is_gap & is_crest,
            "gap_and_no_crest": is_gap & ~is_crest,
            "sag_and_crest": is_sag & is_crest,
            "sag_and_no_crest": is_sag & ~is_crest,
        }
        underpass_partition_audit = {
            "total_underpass_segments": n_up_tot,
            "marginal_dip_partition": {
                "FLAT_RUNTHROUGH_dip_le_0.10m": count_and_pct(is_flat),
                "INTERMEDIATE_GAP_dip_0.10_to_0.30m": count_and_pct(is_gap),
                "SAG_DETECTED_dip_gt_0.30m": count_and_pct(is_sag),
                "dip_partition_sum_pct": dip_partition_sum_pct,
            },
            "marginal_crest": {
                "CREST_VISIBLE_crest_gt_0.30m": count_and_pct(is_crest),
            },
            "mutually_exclusive_joint_matrix": {
                **{name: count_and_pct(mask) for name, mask in joint_masks.items()},
                "joint_sum_pct": float(
                    sum(mask.sum() for mask in joint_masks.values()) / n_up_tot * 100
                ),
            },
            "co_location": {
                "status": "UNRESOLVED",
                "reason": (
                    "The current runner does not have a configured, independently validated "
                    "geometry instrument for underpass-buffer co-location and its null model. "
                    "Historical rates and p-values are intentionally not copied into this run."
                ),
            },
        }
    else:
        raise FileNotFoundError(
            f"Underpass register required for segment validation: {underpass_csv}"
        )

    # 15. Closing Water Budget from the realized Phase 3 producer contract.
    closing_water_budget = load_phase3_closing_water_budget(phase3_run_dir / "manifest.json")

    wall_clock = time.perf_counter() - t0

    # Build Master Report
    report: dict[str, Any] = {
        "stage": "phase3_segment_validation_gate",
        "timestamp_iso": datetime.now(UTC).isoformat(),
        "git_sha": run_git_sha,
        "wall_clock_sec": round(wall_clock, 2),
        "primary_rule": PRIMARY_RULE.to_dict(),
        "domain_summary": {
            "total_road_segments": n_total_segs,
            "total_road_raster_cells": int(np.sum(road_mask)),
            "bbmp_points_raw": bbmp_points_raw_count,
            "bbmp_points_in_domain": len(bbmp_points_wgs),
            "bbmp_positive_unique_segments": len(bbmp_pos_unique_segs),
            "ground_truth_records_loaded": len(gt_points_all),
            "ground_truth_points_date_eligible": len(gt_points_date_eligible),
            "ground_truth_positive_unique_segments": len(gt_pos_unique_segs),
            "ground_truth_scoring_eligible_count": sum(
                1 for detail in gt_details if detail["eligible_for_scoring"]
            ),
            "s1_sar_change_detection_metadata": s1_meta,
            "s1_sar_change_positive_segments": int(seg_meta["obs_sar_change"].sum()),
        },
        "headline_results": {
            "bbmp_validation": bbmp_headline.to_dict(),
            "groundtruth_validation_date_and_snap_eligible": gt_headline_eligible.to_dict(),
            "sentinel1_change_validation": sar_headline.to_dict(),
        },
        "stratification": {
            "density_stratification": {
                "bbmp_event_max": density_strata_bbmp,
                "sar_change_instant": density_strata_sar,
            },
            "arterial_trunk_alone": {
                "total_arterial_segments": int(seg_meta["is_arterial"].sum()),
                "bbmp_event_max": arterial_bbmp.to_dict(),
                "sar_change_instant": arterial_sar.to_dict(),
            },
            "road_class_stratification": {
                "bbmp_event_max": road_class_strata_bbmp,
                "sar_change_instant": road_class_strata_sar,
            },
        },
        "trunk_signal_evaluation": trunk_eval_summary,
        "underpass_colocation_evaluation": underpass_partition_audit,
        "phase3_gate_closing_water_budget": closing_water_budget,
        "sensitivity_sweeps": {
            "depth_threshold_sweep_m": depth_sweep_results,
            "fraction_threshold_sweep": fraction_sweep_results,
        },
        "groundtruth_points_detail": gt_details,
        "snap_distance_audit": {
            "configured_max_snap_distance_m": max_snap_distance_m,
            "date_rejections": gt_date_rejections,
            "excluded_points": [d for d in gt_details if d["excluded_by_snap_distance"]],
            "comparison": {
                "eligible_positive_segments": {
                    "N": len(gt_filtered_pos_segs),
                    "TP": gt_headline_eligible.hits,
                    "POD": gt_headline_eligible.pod,
                    "lift_pod": gt_headline_eligible.lift_ratio_pod,
                },
            },
        },
        "error_budgets": {
            "solver_vs_observation": (
                "Evaluated in this segment-level rescore. Causal error attribution requires "
                "independent case traces and is not inferred from aggregate metrics here."
            ),
            "surrogate_vs_solver": "Not yet existent (surrogate training deferred to Phase 5).",
        },
        "verdict_for_adjudication": {
            "status": "FLAGGED_FOR_ADJUDICATION",
            "findings": [
                "Ground-truth scoring uses only records eligible on both replay date and configured snap distance; see headline_results for realized metrics.",
                f"Sentinel-1 change metrics are realized in this report: {sar_headline.to_dict()}.",
                f"Trunk-road metrics are realized in this report: {trunk_score.to_dict()}.",
                "Underpass co-location is descriptive evidence only; it does not establish a causal mechanism.",
            ],
            "recommendation": "Flag for adjudication. Do not calibrate friction or drain parameters.",
        },
    }

    # Write report files (stage-owned output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "segment_validation_report.json"
    write_json_atomic(report_path, report)
    return report


def run_segment_validation_gate(
    phase3_run_dir: Path = REPO / "runs/phase3_validation",
    output_dir: Path = REPO / "runs/segment_validation/baseline",
    val_config_path: Path = REPO / "configs/validation.yaml",
    n_mc_draws: int = 10000,
    seed: int = 42,
    runs_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute complete per-road-segment validation rescore and produce JSON report.

    Split ownership (Amendment 1):
      * phase3_run_dir — read-only solver input (manifest, depth rasters)
      * output_dir — writable stage-owned output (manifest.json, segment_validation_report.json)
    The two must be distinct and non-nested; violation fails at startup (Rule 7).
    Canonical: runs/segment_validation/baseline and .../variant.
    Legacy shared files in runs/phase3_validation/ are preserved but superseded.
    """
    if runs_dir is not None:
        output_dir = Path(runs_dir)
    phase3_run_dir = Path(phase3_run_dir)
    output_dir = Path(output_dir)
    _check_segment_output_collision(phase3_run_dir, output_dir)

    run_git_sha = git_sha()
    t0 = time.perf_counter()
    with open(val_config_path) as f:
        val_cfg = yaml.safe_load(f)
    scoring_start, scoring_end, max_snap_distance_m, gt_points_csv = (
        _resolve_segment_groundtruth_config(val_cfg)
    )
    resolved_paths = resolve_segment_validation_config(val_cfg)

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    legacy_manifest_path = output_dir / "segment_validation_manifest.json"
    lifecycle = RunManifest(
        manifest_path,
        stage="phase3_segment_validation_rescore",
        repo_root=REPO,
        resolved_config=val_cfg,
        config_paths={"validation": str(val_config_path)},
        fields={"report_path": str(output_dir / "segment_validation_report.json")},
    )
    lifecycle.start()
    try:
        report = _run_segment_validation_impl(
            phase3_run_dir=phase3_run_dir,
            output_dir=output_dir,
            val_config_path=val_config_path,
            n_mc_draws=n_mc_draws,
            seed=seed,
            run_git_sha=run_git_sha,
            t0=t0,
            val_cfg=val_cfg,
            resolved_paths=resolved_paths,
            scoring_start=scoring_start,
            scoring_end=scoring_end,
            max_snap_distance_m=max_snap_distance_m,
            gt_points_csv=gt_points_csv,
        )
        lifecycle.complete(
            {
                "report_path": str(output_dir / "segment_validation_report.json"),
                "headline_results": report["headline_results"],
                "verdict": report["verdict_for_adjudication"],
                "wall_clock_sec": report["wall_clock_sec"],
                "git_sha": report["git_sha"],
            }
        )
        try:
            import json as _json

            _legacy_data = _json.loads(manifest_path.read_text())
            write_json_atomic(legacy_manifest_path, _legacy_data)
        except Exception:
            pass
        return report
    except BaseException as exc:
        try:
            lifecycle.fail(exc)
        except Exception:
            pass
        raise
