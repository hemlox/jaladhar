"""SAR Water Classifier Investigation & Phase 3 Gate Rescoring Module.

Validates Sentinel-1 C-band VV water detection against independent known-water
(OSM lake interiors) and known-dry test sets. Characterizes physical vs threshold
failure modes (water hyacinth volume/double-bounce scattering and surface froth vs
specular open water), evaluates candidate classifiers, and rescores the Phase 3
validation gate with null-model baselines.

Standing Rules Compliance:
- CLAUDE.md R1: Strict separation of [FINDING], [READING], and [HYPOTHESIS].
- CLAUDE.md R2: Validate instrument before subject.
- CLAUDE.md R5: Falsification check before verdict.
- CLAUDE.md V3: Independent test sets (OSM polygons & geomorphic dry terrain).
- CLAUDE.md V6/V9: Clean committed script and manifest logging.
- CLAUDE.md V11: Explicit naming of measured vs unmeasured axes.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pyproj
import rasterio
import scipy.ndimage
import typer
import yaml
from rasterio.crs import CRS
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds
from scipy.interpolate import Rbf, RegularGridInterpolator
from scipy.ndimage import distance_transform_edt, uniform_filter

from jaladhar.validation.scoring import score_extent

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


def get_git_sha() -> str:
    """Retrieve current commit SHA."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def resolve_sar_classifier_config(val_cfg_path: Path) -> dict[str, Any]:
    """Pre-flight check resolving all required config keys and input file paths.

    Rule 7: Touches every key and path at startup, failing immediately with an
    aggregated error message if any are missing or unreachable.
    """
    if not val_cfg_path.exists():
        raise FileNotFoundError(f"Validation config file not found: {val_cfg_path}")

    with open(val_cfg_path) as f:
        cfg = yaml.safe_load(f)

    missing_keys: list[str] = []
    missing_files: list[str] = []

    if "sar_water_classifier" not in cfg:
        missing_keys.append("sar_water_classifier")
        raise KeyError(f"Config validation failed: missing top-level key {missing_keys}")

    sc = cfg["sar_water_classifier"]
    required_paths = [
        ("flood_scene_tif", sc.get("flood_scene_tif")),
        ("flood_calibration_xml", sc.get("flood_calibration_xml")),
        ("flood_annotation_xml", sc.get("flood_annotation_xml")),
        ("pre_scene_tif", sc.get("pre_scene_tif")),
        ("pre_calibration_xml", sc.get("pre_calibration_xml")),
        ("pre_annotation_xml", sc.get("pre_annotation_xml")),
        ("osm_water_gpkg", sc.get("osm_water_gpkg")),
        ("basin_class_path", sc.get("basin_class_path")),
        ("elevation_path", sc.get("elevation_path")),
        ("slope_path", sc.get("slope_path")),
        ("flow_accumulation_path", sc.get("flow_accumulation_path")),
        ("density_classes_path", sc.get("density_classes_path")),
        ("depth_sar_instant_path", sc.get("depth_sar_instant_path")),
    ]

    for key_name, rel_path in required_paths:
        if not rel_path:
            missing_keys.append(f"sar_water_classifier.{key_name}")
        else:
            full_path = REPO / rel_path
            if not full_path.exists():
                missing_files.append(f"{key_name} -> {full_path}")

    if missing_keys or missing_files:
        msg = "Pre-flight config resolution failed:\n"
        if missing_keys:
            msg += f"  Missing Config Keys: {missing_keys}\n"
        if missing_files:
            msg += f"  Missing Input Files: {missing_files}\n"
        raise FileNotFoundError(msg)

    return cfg


def compute_calibrated_s1_with_incidence(
    tif_path: Path,
    calib_xml_path: Path,
    annot_xml_path: Path,
    dst_bounds: Any,
    dst_shape: tuple[int, int],
    dst_transform: Any,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute calibrated Sentinel-1 VV sigma0 (dB), intensity, incidence, gamma0 (dB)."""
    with rasterio.open(tif_path) as src:
        gcps, _ = src.gcps
        with WarpedVRT(src, crs=CRS.from_epsg(32643)) as vrt:
            win = from_bounds(*dst_bounds, transform=vrt.transform)
            dn = vrt.read(1, window=win, out_shape=dst_shape).astype(np.float32)

    # 1. Calibration LUT interpolation
    tree = ET.parse(calib_xml_path)
    vectors = tree.getroot().find("calibrationVectorList")
    lut_lines = [int(vec.find("line").text) for vec in vectors]
    lut_pixels = np.fromstring(vectors[0].find("pixel").text, sep=" ", dtype=int)
    lut_sigma0 = np.array(
        [np.fromstring(vec.find("sigmaNought").text, sep=" ", dtype=np.float32) for vec in vectors]
    )
    interp = RegularGridInterpolator((lut_lines, lut_pixels), lut_sigma0, method="linear")

    gcp_lons = np.array([g.x for g in gcps])
    gcp_lats = np.array([g.y for g in gcps])
    gcp_cols = np.array([g.col for g in gcps])
    gcp_rows = np.array([g.row for g in gcps])

    rbf_row = Rbf(gcp_lons, gcp_lats, gcp_rows, function="linear")
    rbf_col = Rbf(gcp_lons, gcp_lats, gcp_cols, function="linear")

    trans_to_wgs = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)

    rows_idx = np.linspace(0, dst_shape[0] - 1, 100)
    cols_idx = np.linspace(0, dst_shape[1] - 1, 100)
    rr, cc = np.meshgrid(rows_idx, cols_idx, indexing="ij")
    xx, yy = rasterio.transform.xy(dst_transform, rr, cc)
    grid_lons, grid_lats = trans_to_wgs.transform(np.array(xx), np.array(yy))

    src_r = rbf_row(grid_lons, grid_lats)
    src_c = rbf_col(grid_lons, grid_lats)
    pts = np.column_stack([src_r.ravel(), src_c.ravel()])
    lut_samples = interp(pts).reshape(100, 100)

    zoom_factors = (dst_shape[0] / 100.0, dst_shape[1] / 100.0)
    asigma_full = scipy.ndimage.zoom(lut_samples, zoom_factors, order=1).astype(np.float32)

    valid = (dn > 0) & (asigma_full > 0)
    sigma0_lin = np.zeros(dst_shape, dtype=np.float32)
    sigma0_lin[valid] = (dn[valid] / asigma_full[valid]) ** 2

    sigma0_db = np.full(dst_shape, np.nan, dtype=np.float32)
    sigma0_db[valid] = 10.0 * np.log10(np.maximum(sigma0_lin[valid], 1e-7))

    # 2. Geolocation Grid Incidence Angle interpolation
    annot_tree = ET.parse(annot_xml_path)
    grid_pts = annot_tree.getroot().find("geolocationGrid").find("geolocationGridPointList")
    pt_lats = np.array([float(pt.find("latitude").text) for pt in grid_pts])
    pt_lons = np.array([float(pt.find("longitude").text) for pt in grid_pts])
    pt_inc = np.array([float(pt.find("incidenceAngle").text) for pt in grid_pts])

    rbf_inc = Rbf(pt_lons, pt_lats, pt_inc, function="linear")
    inc_samples = rbf_inc(grid_lons.ravel(), grid_lats.ravel()).reshape(100, 100)
    inc_angle_deg = scipy.ndimage.zoom(inc_samples, zoom_factors, order=1).astype(np.float32)

    # 3. Gamma0 computation: gamma0 = sigma0 / cos(theta)
    inc_rad = np.radians(inc_angle_deg)
    cos_inc = np.cos(inc_rad)
    gamma0_db = np.full(dst_shape, np.nan, dtype=np.float32)
    gamma0_db[valid] = sigma0_db[valid] - 10.0 * np.log10(np.maximum(cos_inc[valid], 1e-5))

    return sigma0_db, sigma0_lin, inc_angle_deg, gamma0_db


def assemble_positive_test_set(
    osm_gpkg_path: Path,
    shape: tuple[int, int],
    transform: Any,
    erosion_m: float = 30.0,
) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, Any]]:
    """Assemble independent positive known-water test set from eroded OSM water polygons."""
    gdf = gpd.read_file(osm_gpkg_path)
    total_raw_features = len(gdf)

    eroded_geoms = gdf.geometry.buffer(-erosion_m)
    valid_eroded = eroded_geoms[~eroded_geoms.is_empty]
    valid_polygon_count = len(valid_eroded)

    pos_mask = rasterize(
        [(geom, 1) for geom in valid_eroded],
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype=np.uint8,
    ).astype(bool)

    # Named major lake masks
    target_lakes = {
        "Varthur Lake": "Varthur Lake",
        "Bellandur Lake": "Bellandur Lake",
        "Hebbal Lake": "Hebbal Lake",
        "Madiwala Lake": "Madiwala Lake",
        "Ulsoor Lake": "Halasuru lake",
        "Agara Lake": "Agara Lake",
        "Lalbagh Tank": "Lalbagh Tank",
        "Kaikondrahalli Lake": "Kaikondrahalli Lake",
        "Yelahanka Lake": "Yelahanka Lake",
    }

    lake_masks: dict[str, np.ndarray] = {}
    lake_cell_counts: dict[str, int] = {}

    for short_name, osm_name in target_lakes.items():
        sub = gdf[gdf["name"] == osm_name]
        if len(sub) > 0:
            g_eroded = sub.geometry.iloc[0].buffer(-erosion_m)
            if not g_eroded.is_empty:
                l_mask = rasterize(
                    [(g_eroded, 1)],
                    out_shape=shape,
                    transform=transform,
                    fill=0,
                    dtype=np.uint8,
                ).astype(bool)
                lake_masks[short_name] = l_mask
                lake_cell_counts[short_name] = int(l_mask.sum())

    stats = {
        "source": "OpenStreetMap natural=water / water=lake (osm_water.gpkg)",
        "total_raw_features": total_raw_features,
        "erosion_distance_m": erosion_m,
        "valid_eroded_polygons": valid_polygon_count,
        "total_positive_cells": int(pos_mask.sum()),
        "total_positive_area_km2": round(pos_mask.sum() * 100.0 / 1e6, 3),
        "lake_cell_counts": lake_cell_counts,
    }

    return pos_mask, lake_masks, stats


def assemble_negative_test_set(
    osm_gpkg_path: Path,
    basin_class_path: Path,
    elevation_path: Path,
    slope_path: Path,
    flow_acc_path: Path,
    shape: tuple[int, int],
    transform: Any,
    min_dist_water_m: float = 500.0,
    min_dist_basin_m: float = 500.0,
    min_slope: float = 0.02,
    max_flow_acc: int = 5,
    min_elevation_m: float = 900.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Assemble independent negative known-dry test set from steep, high-elevation terrain."""
    gdf = gpd.read_file(osm_gpkg_path)
    osm_water_mask = rasterize(
        [(geom, 1) for geom in gdf.geometry],
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype=np.uint8,
    ).astype(bool)

    with rasterio.open(basin_class_path) as src:
        basin_class = src.read(1)
    with rasterio.open(elevation_path) as src:
        elevation = src.read(1)
    with rasterio.open(slope_path) as src:
        slope = src.read(1)
    with rasterio.open(flow_acc_path) as src:
        flow_acc = src.read(1)

    dist_to_water_m = distance_transform_edt(~osm_water_mask) * 10.0
    dist_to_basin_m = distance_transform_edt(~((basin_class >= 1) & (basin_class <= 3))) * 10.0

    neg_mask = (
        (dist_to_water_m > min_dist_water_m)
        & (dist_to_basin_m > min_dist_basin_m)
        & (slope > min_slope)
        & (flow_acc < max_flow_acc)
        & (elevation > min_elevation_m)
    )

    stats = {
        "description": (
            f"Steep dry ridge land (>500m water/depr, slope>{min_slope*100:.0f}%, "
            f"flow_acc<{max_flow_acc}, elev>{min_elevation_m:.0f}m)"
        ),
        "min_dist_to_water_m": min_dist_water_m,
        "min_dist_to_depressions_m": min_dist_basin_m,
        "min_slope": min_slope,
        "max_flow_accumulation_cells": max_flow_acc,
        "min_elevation_m": min_elevation_m,
        "total_negative_cells": int(neg_mask.sum()),
        "total_negative_area_km2": round(neg_mask.sum() * 100.0 / 1e6, 3),
    }

    return neg_mask, stats


def compute_distribution_stats(vals: np.ndarray) -> dict[str, float]:
    """Compute comprehensive percentile and summary statistics for a 1D array."""
    v = vals[np.isfinite(vals)]
    if len(v) == 0:
        return {}
    return {
        "count": int(len(v)),
        "mean": round(float(np.mean(v)), 2),
        "std": round(float(np.std(v)), 2),
        "min": round(float(np.min(v)), 2),
        "p5": round(float(np.percentile(v, 5)), 2),
        "p10": round(float(np.percentile(v, 10)), 2),
        "p25": round(float(np.percentile(v, 25)), 2),
        "p50_median": round(float(np.median(v)), 2),
        "p75": round(float(np.percentile(v, 75)), 2),
        "p90": round(float(np.percentile(v, 90)), 2),
        "p95": round(float(np.percentile(v, 95)), 2),
        "max": round(float(np.max(v)), 2),
        "pod_at_minus_16dB": round(float(np.mean(v <= -16.0)), 4),
    }


def compute_otsu_threshold(vals: np.ndarray, num_bins: int = 256) -> float:
    """Compute Otsu optimal threshold on a 1D continuous distribution."""
    v = vals[np.isfinite(vals)]
    if len(v) == 0:
        return float("nan")
    hist, bin_edges = np.histogram(v, bins=num_bins)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    total = len(v)
    sum_total = np.sum(hist * bin_centers)

    weight_bg = 0
    sum_bg = 0
    max_var = 0.0
    best_thresh = bin_centers[0]

    for i in range(len(hist)):
        weight_bg += hist[i]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += hist[i] * bin_centers[i]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        var_between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var_between > max_var:
            max_var = var_between
            best_thresh = bin_centers[i]

    return float(best_thresh)


def evaluate_null_model(
    pos_mask: np.ndarray,
    neg_mask: np.ndarray,
    n_draws: int = 5000,
    seed: int = 42,
) -> dict[str, Any]:
    """Compute random null distribution over >=5,000 draws for test set evaluation."""
    rng = np.random.default_rng(seed)
    pos_count = int(pos_mask.sum())
    neg_count = int(neg_mask.sum())
    total_test_cells = pos_count + neg_count

    null_rates: dict[str, Any] = {}
    for flag_rate in [0.01, 0.02, 0.05, 0.10, 0.20]:
        k_flag = int(round(total_test_cells * flag_rate))
        pos_hits = rng.hypergeometric(
            ngood=pos_count, nbad=neg_count, nsample=k_flag, size=n_draws
        )
        pods = pos_hits / pos_count
        fprs = (k_flag - pos_hits) / neg_count
        null_rates[f"flag_rate_{int(flag_rate*100)}pct"] = {
            "mean_pod": round(float(np.mean(pods)), 5),
            "ci95_pod": [
                round(float(np.percentile(pods, 2.5)), 5),
                round(float(np.percentile(pods, 97.5)), 5),
            ],
            "mean_fpr": round(float(np.mean(fprs)), 5),
            "ci95_fpr": [
                round(float(np.percentile(fprs, 2.5)), 5),
                round(float(np.percentile(fprs, 97.5)), 5),
            ],
        }
    return null_rates


@dataclass
class CandidateEvaluationResult:
    name: str
    family: str
    description: str
    overall_pos_pod: float
    neg_fpr: float
    test_set_csi: float
    test_set_balanced_acc: float
    lake_pods: dict[str, float]
    domain_flagged_cells: int
    domain_flagged_pct: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_candidate_evaluation_suite(
    sigma_flood_db: np.ndarray,
    sigma_flood_lin: np.ndarray,
    inc_angle_deg: np.ndarray,
    gamma0_flood_db: np.ndarray,
    sigma_pre_db: np.ndarray,
    sigma_pre_lin: np.ndarray,
    pos_mask: np.ndarray,
    lake_masks: dict[str, np.ndarray],
    neg_mask: np.ndarray,
    density_raster: np.ndarray,
) -> tuple[list[CandidateEvaluationResult], dict[str, np.ndarray]]:
    """Evaluate all candidate classifiers against independent test sets."""
    # 1. Texture Features
    mean_I = uniform_filter(sigma_flood_lin, size=5)
    sq_mean_I = uniform_filter(sigma_flood_lin**2, size=5)
    var_I = np.maximum(sq_mean_I - mean_I**2, 0.0)
    std_I = np.sqrt(var_I)
    cv_flood = std_I / np.maximum(mean_I, 1e-7)

    # 2. Otsu thresholds
    otsu_global_sigma = compute_otsu_threshold(sigma_flood_db)
    otsu_global_gamma = compute_otsu_threshold(gamma0_flood_db)
    otsu_open = compute_otsu_threshold(sigma_flood_db[density_raster == 0])
    otsu_moderate = compute_otsu_threshold(sigma_flood_db[density_raster == 1])
    otsu_dense = compute_otsu_threshold(sigma_flood_db[density_raster == 2])

    stratified_otsu_mask = (
        ((density_raster == 0) & (sigma_flood_db <= otsu_open))
        | ((density_raster == 1) & (sigma_flood_db <= otsu_moderate))
        | ((density_raster == 2) & (sigma_flood_db <= otsu_dense))
    )

    delta_drop_2db = (sigma_flood_db - sigma_pre_db) <= -2.0
    delta_drop_3db = (sigma_flood_db - sigma_pre_db) <= -3.0

    strat_otsu_name = (
        f"Density-Stratified Otsu (OPEN {otsu_open:.1f}, "
        f"MOD {otsu_moderate:.1f}, DENSE {otsu_dense:.1f} dB)"
    )

    # 3. Candidate Suite Definition
    candidate_definitions: list[tuple[str, str, str, np.ndarray]] = [
        ("Cut <= -16 dB (Baseline)", "Fixed dB Cut", "Cut at -16.0 dB", sigma_flood_db <= -16.0),
        ("Cut <= -20 dB", "Fixed dB Cut", "Cut at -20.0 dB", sigma_flood_db <= -20.0),
        ("Cut <= -18 dB", "Fixed dB Cut", "Cut at -18.0 dB", sigma_flood_db <= -18.0),
        ("Cut <= -15 dB", "Fixed dB Cut", "Cut at -15.0 dB", sigma_flood_db <= -15.0),
        ("Cut <= -14 dB", "Fixed dB Cut", "Cut at -14.0 dB", sigma_flood_db <= -14.0),
        ("Cut <= -12 dB", "Fixed dB Cut", "Cut at -12.0 dB", sigma_flood_db <= -12.0),
        ("Cut <= -10 dB", "Fixed dB Cut", "Cut at -10.0 dB", sigma_flood_db <= -10.0),
        ("Cut <= -8 dB", "Fixed dB Cut", "Cut at -8.0 dB", sigma_flood_db <= -8.0),
        ("Cut <= -6 dB", "Fixed dB Cut", "Cut at -6.0 dB", sigma_flood_db <= -6.0),
        (
            "Gamma0 <= -16 dB",
            "Incidence Correction",
            "Gamma0 at -16.0 dB",
            gamma0_flood_db <= -16.0,
        ),
        (
            "Gamma0 <= -14 dB",
            "Incidence Correction",
            "Gamma0 at -14.0 dB",
            gamma0_flood_db <= -14.0,
        ),
        (
            "Gamma0 <= -12 dB",
            "Incidence Correction",
            "Gamma0 at -12.0 dB",
            gamma0_flood_db <= -12.0,
        ),
        (
            f"Global Otsu ({otsu_global_sigma:.2f} dB)",
            "Otsu Thresholding",
            "Global Otsu on sigma0",
            sigma_flood_db <= otsu_global_sigma,
        ),
        (
            f"Global Otsu Gamma0 ({otsu_global_gamma:.2f} dB)",
            "Otsu Thresholding",
            "Global Otsu on gamma0",
            gamma0_flood_db <= otsu_global_gamma,
        ),
        (
            strat_otsu_name,
            "Otsu Thresholding",
            "Otsu per urban density class",
            stratified_otsu_mask,
        ),
        (
            "Change-Detection (-16dB flood & pre > -16dB)",
            "Change Detection",
            "Single-date -16dB with persistent pre water removed",
            (sigma_flood_db <= -16.0) & (sigma_pre_db > -16.0),
        ),
        (
            "Change-Detection (-14dB flood & pre > -14dB)",
            "Change Detection",
            "Single-date -14dB with persistent pre water removed",
            (sigma_flood_db <= -14.0) & (sigma_pre_db > -14.0),
        ),
        (
            "Change-Detection (-12dB flood & pre > -12dB)",
            "Change Detection",
            "Single-date -12dB with persistent pre water removed",
            (sigma_flood_db <= -12.0) & (sigma_pre_db > -12.0),
        ),
        ("Delta Drop <= -3.0 dB", "Change Detection", "Delta drop <= -3 dB", delta_drop_3db),
        ("Delta Drop <= -2.0 dB", "Change Detection", "Delta drop <= -2 dB", delta_drop_2db),
        (
            "Radiometric Ratio <= 0.50 (-3 dB drop)",
            "Change Detection",
            "Linear ratio flood/pre <= 0.50",
            (sigma_flood_lin / np.maximum(sigma_pre_lin, 1e-7)) <= 0.50,
        ),
        (
            "Texture Cv <= 0.35 & sigma <= -10 dB",
            "Texture / Speckle",
            "Cv <= 0.35 with sigma <= -10 dB",
            (cv_flood <= 0.35) & (sigma_flood_db <= -10.0),
        ),
        (
            "Texture Cv <= 0.50 & sigma <= -12 dB",
            "Texture / Speckle",
            "Cv <= 0.50 with sigma <= -12 dB",
            (cv_flood <= 0.50) & (sigma_flood_db <= -12.0),
        ),
        (
            "Composite: sigma<=-16 | (cv<=0.30 & sigma<=-10)",
            "Composite",
            "Specular -16 dB OR homogeneous -10 dB with Cv<=0.30",
            (sigma_flood_db <= -16.0) | ((cv_flood <= 0.30) & (sigma_flood_db <= -10.0)),
        ),
        (
            "Composite: sigma<=-16 | (delta<=-2.0dB & sigma<=-12)",
            "Composite",
            "Specular -16 dB OR delta drop <= -2 dB with sigma<=-12",
            (sigma_flood_db <= -16.0) | (delta_drop_2db & (sigma_flood_db <= -12.0)),
        ),
    ]

    results: list[CandidateEvaluationResult] = []
    masks_dict: dict[str, np.ndarray] = {}

    pos_count = int(pos_mask.sum())
    neg_count = int(neg_mask.sum())

    for name, family, desc, pred_mask in candidate_definitions:
        pos_hits = int(np.sum(pred_mask & pos_mask))
        pos_pod = pos_hits / pos_count if pos_count > 0 else 0.0

        neg_fa = int(np.sum(pred_mask & neg_mask))
        neg_fpr = neg_fa / neg_count if neg_count > 0 else 0.0

        denom = pos_hits + (pos_count - pos_hits) + neg_fa
        test_csi = pos_hits / denom if denom > 0 else 0.0
        balanced_acc = (pos_pod + (1.0 - neg_fpr)) / 2.0

        lake_pods: dict[str, float] = {}
        for l_name, l_mask in lake_masks.items():
            l_cnt = int(l_mask.sum())
            if l_cnt > 0:
                l_hits = int(np.sum(pred_mask & l_mask))
                lake_pods[l_name] = round(l_hits / l_cnt, 4)
            else:
                lake_pods[l_name] = float("nan")

        domain_cells = int(np.sum(pred_mask))
        domain_pct = round(domain_cells * 100.0 / pred_mask.size, 3)

        results.append(
            CandidateEvaluationResult(
                name=name,
                family=family,
                description=desc,
                overall_pos_pod=round(pos_pod, 4),
                neg_fpr=round(neg_fpr, 4),
                test_set_csi=round(test_csi, 4),
                test_set_balanced_acc=round(balanced_acc, 4),
                lake_pods=lake_pods,
                domain_flagged_cells=domain_cells,
                domain_flagged_pct=domain_pct,
            )
        )
        masks_dict[name] = pred_mask

    return results, masks_dict


def rescore_gate_across_sar_references(
    depth_instant: np.ndarray,
    sar_masks: dict[str, np.ndarray],
    basin_class: np.ndarray,
    density_raster: np.ndarray,
    density_names: dict[int, str],
    sar_time: datetime,
    threshold_m: float = 0.10,
) -> dict[str, Any]:
    """Rescore Phase 3 validation gate against candidate SAR references."""
    perm_mask = (basin_class >= 1) & (basin_class <= 3)
    non_perm_mask = ~perm_mask
    n_non_perm = int(np.sum(non_perm_mask))

    pred_flooded_cells = int(np.sum((depth_instant > threshold_m) & non_perm_mask))

    gate_rescores: dict[str, Any] = {}

    for name, obs_mask in sar_masks.items():
        score_res = score_extent(
            predicted_depth=depth_instant,
            observed_flooded=obs_mask,
            comparison_timestamp=sar_time,
            threshold_m=threshold_m,
            permanent_water_mask=perm_mask,
            density_raster=density_raster,
            density_classes=density_names,
        )

        m = score_res.masked
        strat_m = score_res.stratified_masked

        obs_wet_cells = int(np.sum(obs_mask & non_perm_mask))
        exp_tp_random = (
            (obs_wet_cells * pred_flooded_cells) / n_non_perm if n_non_perm > 0 else 0.0
        )
        obs_tp = m.hits
        lift_random = obs_tp / exp_tp_random if exp_tp_random > 0 else 0.0
        rand_denom = obs_wet_cells + pred_flooded_cells - exp_tp_random
        rand_null_csi = exp_tp_random / rand_denom if rand_denom > 0 else 0.0

        # OPEN stratum specific lift
        open_mask = (density_raster == 0) & non_perm_mask
        n_open = int(np.sum(open_mask))
        pred_open = int(np.sum((depth_instant > threshold_m) & open_mask))
        obs_open = int(np.sum(obs_mask & open_mask))
        exp_tp_open = (obs_open * pred_open) / n_open if n_open > 0 else 0.0
        act_tp_open = strat_m["OPEN"].hits if strat_m and "OPEN" in strat_m else 0
        lift_open = act_tp_open / exp_tp_open if exp_tp_open > 0 else 0.0

        gate_rescores[name] = {
            "masked_domain_cells": n_non_perm,
            "observed_flooded_cells": obs_wet_cells,
            "observed_flooded_pct": round(obs_wet_cells * 100.0 / n_non_perm, 3),
            "predicted_flooded_cells": pred_flooded_cells,
            "predicted_flooded_pct": round(pred_flooded_cells * 100.0 / n_non_perm, 3),
            "hits_tp": m.hits,
            "misses_fn": m.misses,
            "false_alarms_fp": m.false_alarms,
            "csi": round(m.csi, 6) if not math.isnan(m.csi) else None,
            "pod": round(m.pod, 6) if not math.isnan(m.pod) else None,
            "far": round(m.far, 6) if not math.isnan(m.far) else None,
            "random_null_expected_tp": round(exp_tp_random, 1),
            "random_null_csi": round(rand_null_csi, 6),
            "lift_over_random": round(lift_random, 3),
            "stratified_masked": (
                {k: v.to_dict() for k, v in strat_m.items()} if strat_m else None
            ),
            "open_stratum_lift": round(lift_open, 3),
        }

    return gate_rescores


def run_sar_water_investigation(
    val_cfg_path: Path = REPO / "configs/validation.yaml",
    out_dir: Path | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Execute end-to-end SAR Water Classifier Investigation & Phase 3 Gate Rescoring."""
    start_time = time.time()
    np.random.seed(seed)
    cfg = resolve_sar_classifier_config(val_cfg_path)
    sc = cfg["sar_water_classifier"]

    out_path = Path(sc["runs_dir"]) if out_dir is None else out_dir
    out_path.mkdir(parents=True, exist_ok=True)

    manifest_file = out_path / "manifest.json"

    initial_manifest = {
        "stage": "sar_water_classifier_investigation",
        "status": "running",
        "start_time_iso": datetime.now(UTC).isoformat(),
        "git_sha": get_git_sha(),
        "config_snapshot": sc,
    }
    with open(manifest_file, "w") as f:
        json.dump(initial_manifest, f, indent=2)

    basin_class_path = REPO / sc["basin_class_path"]
    with rasterio.open(basin_class_path) as src:
        bounds = src.bounds
        shape = src.shape
        transform = src.transform
        basin_class = src.read(1)

    flood_tif = REPO / sc["flood_scene_tif"]
    flood_calib_xml = REPO / sc["flood_calibration_xml"]
    flood_annot_xml = REPO / sc["flood_annotation_xml"]

    pre_tif = REPO / sc["pre_scene_tif"]
    pre_calib_xml = REPO / sc["pre_calibration_xml"]
    pre_annot_xml = REPO / sc["pre_annotation_xml"]

    (
        sigma_flood_db,
        sigma_flood_lin,
        inc_flood_deg,
        gamma0_flood_db,
    ) = compute_calibrated_s1_with_incidence(
        flood_tif, flood_calib_xml, flood_annot_xml, bounds, shape, transform
    )

    (
        sigma_pre_db,
        sigma_pre_lin,
        inc_pre_deg,
        gamma0_pre_db,
    ) = compute_calibrated_s1_with_incidence(
        pre_tif, pre_calib_xml, pre_annot_xml, bounds, shape, transform
    )

    # Save calibrated rasters
    for fname, arr in [
        ("sentinel1_flood_sigma0_db.tif", sigma_flood_db),
        ("sentinel1_pre_sigma0_db.tif", sigma_pre_db),
        ("sentinel1_flood_incidence_angle_deg.tif", inc_flood_deg),
        ("sentinel1_flood_gamma0_db.tif", gamma0_flood_db),
    ]:
        with rasterio.open(
            out_path / fname,
            "w",
            driver="GTiff",
            dtype="float32",
            width=shape[1],
            height=shape[0],
            count=1,
            crs="EPSG:32643",
            transform=transform,
            nodata=np.nan,
        ) as dst:
            dst.write(arr, 1)

    density_path = REPO / sc["density_classes_path"]
    with rasterio.open(density_path) as src:
        density_raster = src.read(1)

    density_names = {0: "OPEN", 1: "MODERATE", 2: "DENSE"}

    depth_path = REPO / sc["depth_sar_instant_path"]
    with rasterio.open(depth_path) as src:
        depth_instant = src.read(1)

    osm_gpkg = REPO / sc["osm_water_gpkg"]
    pos_mask, lake_masks, pos_stats = assemble_positive_test_set(
        osm_gpkg, shape, transform, erosion_m=float(sc["positive_erosion_m"])
    )

    elevation_path = REPO / sc["elevation_path"]
    slope_path = REPO / sc["slope_path"]
    flow_acc_path = REPO / sc["flow_accumulation_path"]

    neg_mask, neg_stats = assemble_negative_test_set(
        osm_gpkg,
        basin_class_path,
        elevation_path,
        slope_path,
        flow_acc_path,
        shape,
        transform,
        min_dist_water_m=float(sc["negative_distance_m"]),
    )

    with rasterio.open(
        out_path / "positive_water_test_mask.tif",
        "w",
        driver="GTiff",
        dtype="uint8",
        width=shape[1],
        height=shape[0],
        count=1,
        crs="EPSG:32643",
        transform=transform,
        nodata=255,
    ) as dst:
        dst.write(pos_mask.astype(np.uint8), 1)

    with rasterio.open(
        out_path / "negative_dry_test_mask.tif",
        "w",
        driver="GTiff",
        dtype="uint8",
        width=shape[1],
        height=shape[0],
        count=1,
        crs="EPSG:32643",
        transform=transform,
        nodata=255,
    ) as dst:
        dst.write(neg_mask.astype(np.uint8), 1)

    lake_distributions: dict[str, Any] = {}
    for short_name, l_mask in lake_masks.items():
        lake_distributions[short_name] = {
            "flood_scene_20220905": compute_distribution_stats(sigma_flood_db[l_mask]),
            "pre_scene_20220812": compute_distribution_stats(sigma_pre_db[l_mask]),
            "delta_sigma0_db": compute_distribution_stats((sigma_flood_db - sigma_pre_db)[l_mask]),
        }

    overall_distributions = {
        "positive_water_set_flood": compute_distribution_stats(sigma_flood_db[pos_mask]),
        "positive_water_set_pre": compute_distribution_stats(sigma_pre_db[pos_mask]),
        "positive_water_delta": compute_distribution_stats(
            (sigma_flood_db - sigma_pre_db)[pos_mask]
        ),
        "negative_dry_set_flood": compute_distribution_stats(sigma_flood_db[neg_mask]),
        "negative_dry_set_pre": compute_distribution_stats(sigma_pre_db[neg_mask]),
        "negative_dry_delta": compute_distribution_stats(
            (sigma_flood_db - sigma_pre_db)[neg_mask]
        ),
    }

    candidate_results, candidate_masks = run_candidate_evaluation_suite(
        sigma_flood_db=sigma_flood_db,
        sigma_flood_lin=sigma_flood_lin,
        inc_angle_deg=inc_flood_deg,
        gamma0_flood_db=gamma0_flood_db,
        sigma_pre_db=sigma_pre_db,
        sigma_pre_lin=sigma_pre_lin,
        pos_mask=pos_mask,
        lake_masks=lake_masks,
        neg_mask=neg_mask,
        density_raster=density_raster,
    )

    null_model = evaluate_null_model(
        pos_mask=pos_mask,
        neg_mask=neg_mask,
        n_draws=int(sc["null_draws"]),
        seed=42,
    )

    sar_epoch_dt = datetime(2022, 9, 5, 0, 40, 28, tzinfo=UTC)

    otsu_o = compute_otsu_threshold(sigma_flood_db[density_raster == 0])
    otsu_m = compute_otsu_threshold(sigma_flood_db[density_raster == 1])
    otsu_d = compute_otsu_threshold(sigma_flood_db[density_raster == 2])
    strat_key = (
        f"Density-Stratified Otsu (OPEN {otsu_o:.1f}, "
        f"MOD {otsu_m:.1f}, DENSE {otsu_d:.1f} dB)"
    )

    key_gate_candidates = {
        "Single-date <= -16 dB (Gate Original)": candidate_masks["Cut <= -16 dB (Baseline)"],
        "Change-Detection (-16dB flood & pre > -16dB)": candidate_masks[
            "Change-Detection (-16dB flood & pre > -16dB)"
        ],
        "Single-date <= -14 dB": candidate_masks["Cut <= -14 dB"],
        "Single-date <= -12 dB": candidate_masks["Cut <= -12 dB"],
        "Single-date <= -10 dB": candidate_masks["Cut <= -10 dB"],
        "Delta Drop <= -3.0 dB": candidate_masks["Delta Drop <= -3.0 dB"],
        "Delta Drop <= -2.0 dB": candidate_masks["Delta Drop <= -2.0 dB"],
        "Density-Stratified Otsu": candidate_masks[strat_key],
    }

    gate_rescores = rescore_gate_across_sar_references(
        depth_instant=depth_instant,
        sar_masks=key_gate_candidates,
        basin_class=basin_class,
        density_raster=density_raster,
        density_names=density_names,
        sar_time=sar_epoch_dt,
        threshold_m=0.10,
    )

    wall_clock = round(time.time() - start_time, 2)
    candidates_summary = [res.to_dict() for res in candidate_results]

    with open(out_path / "candidate_comparison_table.json", "w") as f:
        json.dump(candidates_summary, f, indent=2)

    with open(out_path / "lake_backscatter_distributions.json", "w") as f:
        json.dump({"per_lake": lake_distributions, "overall": overall_distributions}, f, indent=2)

    with open(out_path / "gate_rescore_comparison.json", "w") as f:
        json.dump(gate_rescores, f, indent=2)

    final_manifest = {
        "stage": "sar_water_classifier_investigation",
        "status": "completed",
        "timestamp_iso": datetime.now(UTC).isoformat(),
        "git_sha": get_git_sha(),
        "wall_clock_sec": wall_clock,
        "config_snapshot": sc,
        "part1_test_sets": {
            "positive_set": pos_stats,
            "negative_set": neg_stats,
            "null_model_5000_draws": null_model,
        },
        "part2_distributions": {
            "overall_distributions": overall_distributions,
            "per_lake_distributions": lake_distributions,
        },
        "part3_candidate_evaluations": candidates_summary,
        "part4_gate_rescores": gate_rescores,
    }

    with open(manifest_file, "w") as f:
        json.dump(final_manifest, f, indent=2)

    return final_manifest


@app.command()
def main(
    val_config: Path = typer.Option(REPO / "configs/validation.yaml", help="Validation config"),
    out: Path = typer.Option(
        REPO / "runs/phase3_validation/sar_water_classifier", help="Output directory"
    ),
) -> None:
    """CLI Driver for SAR Water Classifier Investigation & Phase 3 Gate Rescoring."""
    typer.echo("Executing SAR Water Classifier Investigation...")
    manifest = run_sar_water_investigation(val_cfg_path=val_config, out_dir=out)
    typer.echo(f"Investigation complete in {manifest['wall_clock_sec']} s.")
    typer.echo(f"Results Manifest: {out / 'manifest.json'}")


if __name__ == "__main__":
    app()
