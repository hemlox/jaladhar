"""Produce the frozen per-road-segment depth product.

This module is deliberately a small, CPU-only boundary between a realized depth
raster and the products consumed by the dashboard, routing API, and G1 scorer.
It does not create a depth field.  A missing, misaligned, or non-finite solver
field is an error and the run fails closed.

The product follows ``configs/contracts/depth_product.json``:

* status is evaluated from float metres with the realized primary segment rule;
* depth bands are the floor-rounded minimum and maximum over canonical cells;
* every lookup segment is emitted, including explicit ``unknown/no_data`` rows;
* a manifest is written with ``status=running`` before output work and updated
  in place when the run completes or fails.

The module imports the existing segment-rule machinery rather than duplicating
the flood-rule constants.  It never imports torch and never selects a device.

Storage-water exclusion (WF-6 U0a, gated): cells whose ``basin_class`` is in an
explicit excluded set are dropped from per-segment min/max/count aggregation AND
from flood-status determination, because the solver itself excludes storage
water bodies (basin_class 1), quarries (2), and landfills (3) from its scoring
while its initialized-at-spill depth field still reaches road cells.  Two
exclusion modes exist and both are manifest-recorded:

* ``segment_touch`` (DEFAULT, audit-reproducing): a segment with ANY
  excluded-class canonical cell has all its cells dropped.  This is what the
  audit wave measured ("basin_class==1 touch test") and it reproduces every
  audited quantity exactly -- flooded 40519->33311, max 1128->369 cm, flooded
  band p50/p90/mean 47/151/68.9 -> 44/134/61.7 -- and leaves exactly the 61
  unexplained >3 m segments the audit reported.  Cell-level dropping does NOT
  reproduce those numbers (37195/389), because shore-jitter cells of bridges
  and lakeside ways remain initialized-wet.
* ``cell`` (literal cell-level dropping): only the excluded-class cells
  themselves are dropped.

The default class set {1, 2, 3} is exactly the ``results.exclusions``
definition the calibration loss already uses -- the same definition, not a
second one.  A segment left with zero contributing cells synthesizes as
``no_data``/``unknown``/[0, 0] like any other segment with zero usable
canonical cells; the manifest records the measured delta against the frozen
raster-absence totals rather than laundering it.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio
import typer
import yaml
from rasterio.crs import CRS

REPO = Path(__file__).resolve().parents[3]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from jaladhar.provenance import require_clean_git, write_json_atomic  # noqa: E402
from jaladhar.validation.depth_product_contract import (  # noqa: E402
    MAX_FORECAST_LEAD_MINUTES,
    UNCOUPLED_BASELINE_LABEL,
    load_requirements,
    validate_product_manifest,
    validate_uncoupled_baseline_admission,
)
from jaladhar.validation.segment_validation import (  # noqa: E402
    PRIMARY_RULE,
    evaluate_segments_from_mask,
)  # noqa: E402

PRODUCT_FIELDS = [
    "run_id",
    "segment_id",
    "band_low_cm",
    "band_high_cm",
    "confidence",
    "flood_status",
    "valid_time_utc",
    "issue_time_utc",
    "forecast_lead_minutes",
    "source_manifest_path",
]
DEFAULT_CONTRACT_PATH = REPO / "configs" / "contracts" / "depth_product.json"


class SegmentStatusError(RuntimeError):
    """Raised when a product input cannot be verified from realized state."""


class ConfigResolutionError(ValueError):
    """Raised with all startup configuration errors collected together."""


@dataclass(frozen=True)
class RasterInputs:
    """Aligned realized arrays and the metadata needed at the seam."""

    depth_m: np.ndarray
    road_ids: np.ndarray
    transform: rasterio.Affine
    crs: str
    depth_path: Path
    road_path: Path


@dataclass(frozen=True)
class RoadIndex:
    """Road-cell index and D8 adjacency used by the frozen rule machinery."""

    road_mask: np.ndarray
    rows: np.ndarray
    cols: np.ndarray
    segment_ids: np.ndarray
    adj_src: np.ndarray
    adj_dst: np.ndarray


def _git_provenance() -> dict[str, Any]:
    """Capture reproducible Git provenance, refusing dirty source bytes."""

    git_sha = require_clean_git(REPO)
    return {
        "git_sha": git_sha,
        "git_tree_clean": True,
        "git_dirty_paths": [],
    }


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash realized input bytes for the terminal manifest."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_path(path: Path | str) -> Path:
    value = Path(path)
    return value if value.is_absolute() else (REPO / value)


def inputs_grid_cells_estimate(raster_path: Path) -> int | None:
    """Cell count of a raster read from metadata only, for budget estimates.

    Deliberately non-fatal: an unreadable input must still let the lifecycle
    manifest reach ``running`` so the guarded phase records the failure.
    """
    try:
        with rasterio.open(raster_path) as source:
            return int(source.height * source.width)
    except Exception:  # noqa: BLE001 - estimate only; real errors raise inside the run
        return None


def relative_repo_path(path: Path) -> str:
    """Return a path usable by a consumer resolving paths from the repository root."""
    return os.path.relpath(path.resolve(), REPO.resolve())


def manifest_config_value(value: Any) -> Any:
    """Serialize config values without baking the producing checkout into provenance.

    Production inputs and outputs live below ``REPO`` and are recorded relative to
    it.  Test-only paths outside the repository remain absolute so their realized
    location is not misrepresented.
    """
    if isinstance(value, Path):
        resolved = value.resolve()
        try:
            return resolved.relative_to(REPO.resolve()).as_posix()
        except ValueError:
            return str(resolved)
    if isinstance(value, dict):
        return {key: manifest_config_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [manifest_config_value(item) for item in value]
    return value


def normalise_utc(value: str) -> str:
    """Validate an aware timestamp and serialize it as aware UTC ISO-8601."""
    text = str(value).strip()
    if not text:
        raise ValueError("timestamp must be non-empty")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware: {value!r}")
    return parsed.astimezone(UTC).isoformat()


def _validate_time_order(valid_time_utc: str, issue_time_utc: str, lead_minutes: int) -> None:
    valid = datetime.fromisoformat(valid_time_utc)
    issue = datetime.fromisoformat(issue_time_utc)
    if lead_minutes == 0 and issue < valid:
        raise ValueError("nowcast issue_time_utc must be >= valid_time_utc")
    if lead_minutes > 0 and issue > valid:
        raise ValueError("forecast issue_time_utc must be <= valid_time_utc")


def _validate_source_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"source solver manifest is absent: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SegmentStatusError(f"source solver manifest is not valid JSON: {path}") from exc
    if payload.get("status") != "completed":
        raise SegmentStatusError(
            f"source solver manifest must be completed, got {payload.get('status')!r}: {path}"
        )
    git_sha_value = payload.get("git_sha")
    if not isinstance(git_sha_value, str) or not git_sha_value.strip():
        raise SegmentStatusError(f"source solver manifest has no Git provenance: {path}")
    return payload


def _declared_coupling_enabled(payload: dict[str, Any]) -> bool | None:
    """Find an explicit coupling declaration without treating absence as false."""
    values: list[bool] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "coupling_enabled" in value:
                candidate = value["coupling_enabled"]
                if isinstance(candidate, bool):
                    values.append(candidate)
            coupling = value.get("coupling")
            if isinstance(coupling, dict) and isinstance(coupling.get("enabled"), bool):
                values.append(bool(coupling["enabled"]))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    unique = set(values)
    if len(unique) > 1:
        raise SegmentStatusError("source manifest contains conflicting coupling declarations")
    return values[0] if values else None


def _validate_coupled_depth_output(
    source_payload: dict[str, Any], depth_raster: Path
) -> dict[str, str]:
    """Bind a coupled product input to the upstream realized post-couple output."""

    output = source_payload.get("depth_raster_output")
    if not isinstance(output, dict):
        raise SegmentStatusError(
            "coupled source manifest does not bind its depth raster output; expected "
            "depth_raster_output {path, sha256, depth_field_state}"
        )
    if output.get("depth_field_state") != "post_couple_step_h_new":
        raise SegmentStatusError(
            "coupled source depth raster output is not declared post_couple_step_h_new"
        )
    path_value = output.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        raise SegmentStatusError("coupled source depth raster output path is absent")
    declared_path = _repo_path(path_value).resolve()
    if declared_path != depth_raster.resolve():
        raise SegmentStatusError(
            "coupled source depth raster output path does not match the product input"
        )
    declared_hash = output.get("sha256")
    realized_hash = sha256_file(depth_raster)
    if not isinstance(declared_hash, str) or declared_hash != realized_hash:
        raise SegmentStatusError(
            "coupled source depth raster output sha256 does not match realized input bytes"
        )
    return {
        "path": relative_repo_path(depth_raster),
        "sha256": realized_hash,
        "depth_field_state": "post_couple_step_h_new",
    }


def _assert_distinct_paths(source_dir: Path, output_dir: Path) -> None:
    source = source_dir.resolve()
    output = output_dir.resolve()
    if source == output or source.is_relative_to(output) or output.is_relative_to(source):
        raise SegmentStatusError(
            "read-only source run directory and writable product directory must be distinct "
            f"and non-nested: source={source}, output={output}"
        )


def _validate_lookup(lookup: pd.DataFrame) -> pd.DataFrame:
    required = {"segment_id"}
    missing = sorted(required - set(lookup.columns))
    if missing:
        raise SegmentStatusError(f"segment lookup is missing columns: {missing}")
    if lookup.empty:
        raise SegmentStatusError("segment lookup is empty")
    ids = pd.to_numeric(lookup["segment_id"], errors="coerce")
    if ids.isna().any() or (ids <= 0).any() or (ids % 1 != 0).any():
        raise SegmentStatusError("segment lookup segment_id values must be positive integers")
    result = lookup.copy()
    result["segment_id"] = ids.astype(np.int64)
    if result["segment_id"].duplicated().any():
        raise SegmentStatusError("segment lookup segment_id values must be unique")
    return result.sort_values("segment_id").reset_index(drop=True)


def load_aligned_rasters(depth_path: Path, road_path: Path) -> RasterInputs:
    """Load and verify the realized depth and road-ID rasters at their seam."""
    if not depth_path.exists():
        raise FileNotFoundError(f"depth raster is absent: {depth_path}")
    if not road_path.exists():
        raise FileNotFoundError(f"road segment raster is absent: {road_path}")

    with rasterio.open(depth_path) as depth_src, rasterio.open(road_path) as road_src:
        if (depth_src.height, depth_src.width) != (road_src.height, road_src.width):
            raise SegmentStatusError(
                "depth and road rasters have different shapes: "
                f"depth={(depth_src.height, depth_src.width)}, "
                f"road={(road_src.height, road_src.width)}"
            )
        if depth_src.crs != road_src.crs:
            raise SegmentStatusError(
                f"depth and road CRS differ: depth={depth_src.crs}, road={road_src.crs}"
            )
        if not np.allclose(tuple(depth_src.transform), tuple(road_src.transform)):
            raise SegmentStatusError("depth and road transforms are not aligned")
        if depth_src.crs is None:
            raise SegmentStatusError("aligned rasters must carry a CRS")

        depth = depth_src.read(1, masked=True).astype(np.float64).filled(np.nan)
        road_ids = road_src.read(1, masked=True).filled(0).astype(np.int64)

    road_mask = road_ids > 0
    if not np.any(road_mask):
        raise SegmentStatusError("road raster contains no positive segment cells")
    if not np.all(np.isfinite(depth[road_mask])):
        raise SegmentStatusError("depth raster has missing/non-finite values on road cells")
    if float(np.min(depth[road_mask])) < 0.0:
        raise SegmentStatusError("depth raster has negative values on road cells")

    return RasterInputs(
        depth_m=depth,
        road_ids=road_ids,
        transform=depth_src.transform,
        crs=str(depth_src.crs),
        depth_path=depth_path,
        road_path=road_path,
    )


def build_road_index(road_ids: np.ndarray) -> RoadIndex:
    """Build the D8 same-segment adjacency expected by the existing rule code."""
    road_mask = np.asarray(road_ids) > 0
    rows, cols = np.where(road_mask)
    segment_ids = np.asarray(road_ids[rows, cols], dtype=np.int64)
    cell_index = np.full(road_mask.shape, -1, dtype=np.int64)
    cell_index[road_mask] = np.arange(len(rows), dtype=np.int64)

    sources: list[np.ndarray] = []
    destinations: list[np.ndarray] = []
    height, width = road_mask.shape
    for d_row, d_col in (
        (-1, -1),
        (-1, 0),
        (-1, 1),
        (0, -1),
        (0, 1),
        (1, -1),
        (1, 0),
        (1, 1),
    ):
        neighbour_row = rows + d_row
        neighbour_col = cols + d_col
        in_bounds = (
            (neighbour_row >= 0)
            & (neighbour_row < height)
            & (neighbour_col >= 0)
            & (neighbour_col < width)
        )
        source_index = np.where(in_bounds)[0]
        neighbour_index = cell_index[neighbour_row[in_bounds], neighbour_col[in_bounds]]
        same_segment = (neighbour_index >= 0) & (
            segment_ids[source_index] == segment_ids[neighbour_index]
        )
        sources.append(source_index[same_segment])
        destinations.append(neighbour_index[same_segment])

    return RoadIndex(
        road_mask=road_mask,
        rows=rows,
        cols=cols,
        segment_ids=segment_ids,
        adj_src=np.concatenate(sources) if sources else np.empty(0, dtype=np.int64),
        adj_dst=np.concatenate(destinations) if destinations else np.empty(0, dtype=np.int64),
    )


def build_segment_status_dataframe(
    depth_m: np.ndarray,
    road_ids: np.ndarray,
    lookup: pd.DataFrame,
    *,
    road_index: RoadIndex | None = None,
    excluded_mask: np.ndarray | None = None,
) -> pd.DataFrame:
    """Evaluate realized segment status and bands, including lookup-only no-data IDs.

    Independent observable: if the segment rule or no-data synthesis were
    broken, a deliberately changed road-cell depth or lookup ID would change
    the realized row status/counts, not merely a copied declaration.

    ``excluded_mask`` marks cells (e.g. storage-water basin classes) dropped
    from band aggregation and flood-status determination; they are treated as
    absent canonical cells, so a segment whose cells are all excluded
    synthesizes as no_data exactly like a raster-absent segment.  Pass
    ``road_index=None`` when excluding -- the index must be built from the
    masked road IDs, not supplied prebuilt over unmasked ones.
    """
    depth = np.asarray(depth_m, dtype=np.float64)
    road = np.asarray(road_ids)
    if depth.shape != road.shape:
        raise SegmentStatusError("depth and road arrays must have identical shapes")
    if excluded_mask is not None:
        if road_index is not None:
            raise SegmentStatusError(
                "excluded_mask requires road_index=None; the index must be rebuilt "
                "from the excluded-masked road IDs"
            )
        excluded_mask = np.asarray(excluded_mask, dtype=bool)
        if excluded_mask.shape != road.shape:
            raise SegmentStatusError("excluded_mask and road arrays must have identical shapes")
        road = np.where(excluded_mask, np.int64(0), road)
    lookup = _validate_lookup(lookup)
    index = road_index or build_road_index(road)
    if not np.all(np.isfinite(depth[index.road_mask])):
        raise SegmentStatusError("depth has non-finite values on road cells")
    raster_ids = set(int(value) for value in np.unique(index.segment_ids))
    lookup_ids = set(int(value) for value in lookup["segment_id"])
    extra_raster_ids = sorted(raster_ids - lookup_ids)
    if extra_raster_ids:
        raise SegmentStatusError(
            "road raster contains segment IDs absent from lookup: "
            f"{extra_raster_ids[:10]}" + ("..." if len(extra_raster_ids) > 10 else "")
        )

    cell_depth = depth[index.road_mask]
    threshold_mask = np.zeros(depth.shape, dtype=bool)
    threshold_mask[index.road_mask] = cell_depth >= float(PRIMARY_RULE.depth_threshold_m)
    evaluated = evaluate_segments_from_mask(
        threshold_mask,
        index.road_mask,
        index.segment_ids,
        index.adj_src,
        index.adj_dst,
        PRIMARY_RULE.fraction_threshold,
        PRIMARY_RULE.contiguous_cells,
    )
    evaluated.index = evaluated.index.astype(np.int64)
    status_map = evaluated["is_flooded"].to_dict()

    unique_ids, inverse = np.unique(index.segment_ids, return_inverse=True)
    low_m = np.full(len(unique_ids), np.inf, dtype=np.float64)
    high_m = np.full(len(unique_ids), -np.inf, dtype=np.float64)
    np.minimum.at(low_m, inverse, cell_depth)
    np.maximum.at(high_m, inverse, cell_depth)
    low_map = dict(zip(unique_ids.tolist(), low_m.tolist(), strict=True))
    high_map = dict(zip(unique_ids.tolist(), high_m.tolist(), strict=True))

    rows: list[dict[str, Any]] = []
    for segment_id in lookup["segment_id"].tolist():
        segment_id = int(segment_id)
        if segment_id not in low_map:
            rows.append(
                {
                    "segment_id": segment_id,
                    "band_low_cm": 0,
                    "band_high_cm": 0,
                    "confidence": "no_data",
                    "flood_status": "unknown",
                }
            )
            continue
        low_cm = int(np.floor(max(0.0, low_map[segment_id]) * 100.0))
        high_cm = int(np.floor(max(0.0, high_map[segment_id]) * 100.0))
        rows.append(
            {
                "segment_id": segment_id,
                "band_low_cm": low_cm,
                "band_high_cm": high_cm,
                "confidence": "modeled_direct",
                "flood_status": "flooded" if bool(status_map[segment_id]) else "not_flooded",
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "segment_id",
            "band_low_cm",
            "band_high_cm",
            "confidence",
            "flood_status",
        ],
    )


def load_aligned_class_raster(class_path: Path, reference: RasterInputs) -> tuple[np.ndarray, dict]:
    """Load a class raster and assert grid identity against the depth/road seam.

    V8 boundary rule: the exclusion producer and the terrain producer share this
    raster, so alignment is asserted here against realized metadata (shape, CRS,
    transform) rather than assumed from prose.
    """
    if not class_path.exists():
        raise FileNotFoundError(f"class raster is absent: {class_path}")
    with rasterio.open(class_path) as source:
        if (source.height, source.width) != reference.road_ids.shape:
            raise SegmentStatusError(
                "class raster shape differs from the canonical grid: "
                f"{(source.height, source.width)} != {reference.road_ids.shape}"
            )
        if source.crs is None or str(source.crs) != reference.crs:
            raise SegmentStatusError(
                f"class raster CRS differs from the canonical grid: {source.crs} != {reference.crs}"
            )
        if not np.allclose(tuple(source.transform), tuple(reference.transform)):
            raise SegmentStatusError("class raster transform differs from the canonical grid")
        array = source.read(1)
    info = {
        "path": relative_repo_path(class_path),
        "shape": [int(v) for v in array.shape],
        "crs": reference.crs,
        "transform": [float(v) for v in tuple(reference.transform)],
    }
    return array, info


def build_exclusion_mask(class_array: np.ndarray, excluded_classes: Iterable[int]) -> np.ndarray:
    """Boolean mask of cells whose class is in the explicit excluded set."""
    classes = sorted({int(value) for value in excluded_classes})
    if not classes:
        raise SegmentStatusError("excluded class set is empty")
    if any(value <= 0 for value in classes):
        raise SegmentStatusError(
            f"excluded classes must be positive basin_class values, got {classes}"
        )
    return np.isin(np.asarray(class_array), np.asarray(classes, dtype=class_array.dtype))


EXCLUSION_MODES = ("cell", "segment_touch")


def build_segment_touch_exclusion_mask(
    road_ids: np.ndarray, class_array: np.ndarray, excluded_classes: Iterable[int]
) -> tuple[np.ndarray, int]:
    """All cells of every segment holding >=1 excluded-class cell (audit semantics).

    The audit wave's "basin_class==1 touch test" removed whole segments touching
    storage water; only this mode reproduces its published numbers (flooded
    33311, max 369 cm, 61 residual >3 m segments).
    """
    on_road = build_exclusion_mask(class_array, excluded_classes) & (road_ids > 0)
    touched_ids = np.unique(road_ids[on_road])
    return np.isin(road_ids, touched_ids) & (road_ids > 0), int(len(touched_ids))


def resolve_exclusion_mask(
    road_ids: np.ndarray,
    class_array: np.ndarray,
    excluded_classes: Iterable[int],
    exclusion_mode: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Mode-dispatching exclusion mask with measured accounting metadata."""
    if exclusion_mode not in EXCLUSION_MODES:
        raise SegmentStatusError(
            f"unknown exclusion_mode {exclusion_mode!r}; expected one of {EXCLUSION_MODES}"
        )
    classes = sorted({int(value) for value in excluded_classes})
    if exclusion_mode == "cell":
        mask = build_exclusion_mask(class_array, classes)
    else:
        mask, touched = build_segment_touch_exclusion_mask(road_ids, class_array, classes)
    info = {
        "mode": exclusion_mode,
        "excluded_classes": classes,
        "cells_masked_on_grid": int(mask.sum()),
    }
    return mask, info


def summarize_variant(rows_frame: pd.DataFrame) -> dict[str, Any]:
    """Flooded-row band statistics in the exact quantities the audit reported.

    Population: flood_status=='flooded' rows; statistic source: band_high_cm.
    Anchored against the v5 product bytes: flooded-only p50/p90/mean/max
    reproduce 47/151/68.90/1128 there, while modeled-population cuts do not.
    """
    flooded = rows_frame[rows_frame["flood_status"] == "flooded"]
    band_high = flooded["band_high_cm"].to_numpy(dtype=np.int64)
    modeled_gt300 = rows_frame[
        (rows_frame["confidence"] == "modeled_direct") & (rows_frame["band_high_cm"] > 300)
    ]
    return {
        "n_rows": int(len(rows_frame)),
        "n_flooded": int(len(flooded)),
        "n_no_data": int((rows_frame["confidence"] == "no_data").sum()),
        "max_band_high_cm": int(band_high.max()) if len(band_high) else 0,
        "flooded_p50_band_high_cm": float(np.percentile(band_high, 50)) if len(band_high) else 0.0,
        "flooded_p90_band_high_cm": float(np.percentile(band_high, 90)) if len(band_high) else 0.0,
        "flooded_mean_band_high_cm": round(float(band_high.mean()), 2) if len(band_high) else 0.0,
        "n_modeled_gt300cm": int(len(modeled_gt300)),
        "n_flooded_gt300cm": int((flooded["band_high_cm"] > 300).sum()),
    }


_DELTA_KEYS = (
    "n_flooded",
    "n_no_data",
    "max_band_high_cm",
    "flooded_p50_band_high_cm",
    "flooded_p90_band_high_cm",
    "n_flooded_gt300cm",
)


def _summary_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, float | int]:
    """Signed after-minus-before differences between two variant summaries."""
    return {key: round(after[key] - before[key], 2) for key in _DELTA_KEYS}


def _git_output(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo_root, text=True, stderr=subprocess.PIPE
        ).rstrip("\r\n")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"cannot establish Git provenance for {repo_root}: {exc}") from exc


def _admitted_dirty_git_provenance(repo_root: Path, reason: str) -> dict[str, Any]:
    """Realized Git state recorded without laundering a dirty source tree.

    Used only when the caller passes an explicit admission reason.  The manifest
    carries ``git_tree_clean=false`` plus every porcelain path so no consumer can
    mistake the run for reproducible-from-HEAD; adoption remains owner-gated.
    """
    git_sha = _git_output(repo_root, "rev-parse", "--verify", "HEAD^{commit}")
    porcelain = _git_output(repo_root, "status", "--porcelain", "--untracked-files=all")
    paths = [line[3:] for line in porcelain.splitlines() if line.strip()]
    if not reason.strip():
        raise ValueError("dirty-tree admission requires a non-empty reason")
    return {
        "git_sha": git_sha,
        "git_tree_clean": False,
        "git_dirty_paths": paths,
        "source_tree_dirty_admission": {
            "reason": reason.strip(),
            "porcelain_paths": paths,
            "policy": (
                "recorded-not-laundered: concurrent units own the listed paths and this "
                "unit is forbidden to commit/stash them; product adoption stays "
                "owner-gated and a clean-tree re-run is required before it can be "
                "served as a reproducible default"
            ),
        },
    }


def measure_osm_water_cells(
    osm_water_gpkg: Path, reference: RasterInputs
) -> tuple[np.ndarray, dict[str, Any]]:
    """Rasterize mapped OSM water polygons onto the canonical grid (N9 observable).

    This is a diagnostic sidecar only -- the audit refuted OSM-water overlap as
    the deep-segment mechanism, so these booleans never enter product rows.
    """
    if not osm_water_gpkg.exists():
        raise FileNotFoundError(f"OSM water polygons are absent: {osm_water_gpkg}")
    import geopandas as gpd  # lazy heavy import; CPU-only diagnostic path
    from rasterio.features import rasterize

    polygons = gpd.read_file(osm_water_gpkg)
    if polygons.crs is None:
        raise SegmentStatusError(f"OSM water layer carries no CRS: {osm_water_gpkg}")
    target_crs = CRS.from_user_input(reference.crs)
    source_crs = CRS.from_user_input(polygons.crs)
    if source_crs != target_crs:
        polygons = polygons.to_crs(target_crs)
    water_mask = rasterize(
        ((geometry, 1) for geometry in polygons.geometry if geometry is not None),
        out_shape=reference.road_ids.shape,
        transform=reference.transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    ).astype(bool)
    info = {
        "path": relative_repo_path(osm_water_gpkg),
        "feature_count": int(len(polygons)),
        "source_crs": str(CRS.from_user_input(polygons.crs)),
        "reprojected_to_grid_crs": bool(source_crs != target_crs),
        "water_cell_count_on_grid": int(water_mask.sum()),
    }
    return water_mask, info


def _read_lookup(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"segment lookup is absent: {path}")
    try:
        return _validate_lookup(pd.read_csv(path))
    except pd.errors.EmptyDataError as exc:
        raise SegmentStatusError(f"segment lookup is empty: {path}") from exc


def _buffered_no_data_split(
    road_ids: np.ndarray, lookup_ids: Iterable[int], buffered_path: Path | None
) -> dict[str, int | None | str]:
    canonical_ids = set(int(value) for value in np.unique(road_ids) if int(value) > 0)
    no_data_ids = set(int(value) for value in lookup_ids) - canonical_ids
    if buffered_path is None:
        return {
            "buffered_margin_only": None,
            "fully_clipped_no_cells_anywhere": None,
            "status": "buffered raster not supplied",
        }
    if not buffered_path.exists():
        raise FileNotFoundError(f"buffered road raster is absent: {buffered_path}")
    with rasterio.open(buffered_path) as source:
        buffered = source.read(1, masked=True).filled(0)
    buffered_ids = set(int(value) for value in np.unique(buffered) if int(value) > 0)
    return {
        "buffered_margin_only": len(no_data_ids & buffered_ids),
        "fully_clipped_no_cells_anywhere": len(no_data_ids - buffered_ids),
        "status": "measured from canonical and buffered realized road rasters",
    }


def _write_csv_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PRODUCT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _write_mapping_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write a diagnostic sidecar mapping (never a product-row schema change)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["segment_id"])
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _resolve_product_config(
    *,
    depth_raster: Path,
    road_raster: Path,
    lookup_csv: Path,
    output_dir: Path,
    source_manifest: Path,
    valid_time_utc: str,
    forecast_lead_minutes: int,
    coupling_enabled: bool | None,
    buffered_road_raster: Path | None,
    contract_path: Path,
    baseline_admission: Path | None,
    basin_class_raster: Path | None = None,
    osm_water_gpkg: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    resolved = {
        "depth_raster": depth_raster,
        "road_raster": road_raster,
        "lookup_csv": lookup_csv,
        "output_dir": output_dir,
        "source_manifest": source_manifest,
        "buffered_road_raster": buffered_road_raster,
        "contract_path": contract_path,
        "baseline_admission": baseline_admission,
        "basin_class_raster": basin_class_raster,
        "osm_water_gpkg": osm_water_gpkg,
    }
    for name, path in resolved.items():
        if path is not None and name != "output_dir" and not path.exists():
            errors.append(f"{name}: absent at {path}")
    if output_dir.resolve() == REPO.resolve():
        errors.append("output_dir: repository root is not a valid writable run directory")
    expected_runs_root = (REPO / "runs").resolve()
    if output_dir.resolve().parent != expected_runs_root or not output_dir.name:
        errors.append(
            f"output_dir: frozen product location must be exactly runs/<run_id>, got {output_dir}"
        )
    if not 0 <= forecast_lead_minutes <= MAX_FORECAST_LEAD_MINUTES:
        errors.append("forecast_lead_minutes: must be within the fixed 0-180 minute horizon")
    if coupling_enabled is None:
        errors.append("coupling_enabled: explicit true/false declaration is required")
    if buffered_road_raster is None:
        errors.append("buffered_road_raster: required to realize the frozen no-data split")

    try:
        valid = normalise_utc(valid_time_utc)
    except ValueError as exc:
        errors.append(f"valid_time_utc: {exc}")
        valid = ""
    if source_manifest.exists():
        try:
            source_payload = _validate_source_manifest(source_manifest)
        except (OSError, SegmentStatusError) as exc:
            errors.append(str(exc))
            source_payload = {}
    else:
        source_payload = {}

    try:
        _assert_distinct_paths(source_manifest.parent, output_dir)
    except SegmentStatusError as exc:
        errors.append(str(exc))

    if output_dir.exists():
        manifest_path = output_dir / "manifest.json"
        if manifest_path.exists():
            errors.append(f"output_dir: refusing to replace existing manifest {manifest_path}")

    declared_coupling: bool | None = None
    admission_view: dict[str, Any] | None = None
    if source_payload:
        try:
            declared_coupling = _declared_coupling_enabled(source_payload)
        except SegmentStatusError as exc:
            errors.append(str(exc))
            declared_coupling = None
        if declared_coupling is None:
            if coupling_enabled is False and baseline_admission is not None:
                try:
                    admission_view = validate_uncoupled_baseline_admission(
                        baseline_admission,
                        source_manifest,
                        repo_root=REPO,
                        depth_raster_path=depth_raster,
                    )
                except (OSError, SegmentStatusError, ValueError) as exc:
                    errors.append(str(exc))
                else:
                    declared_coupling = False
            else:
                errors.append(
                    "source manifest does not declare coupling_enabled and no valid "
                    "uncoupled-baseline admission was supplied"
                )
        elif coupling_enabled is not None and declared_coupling != coupling_enabled:
            errors.append(
                "coupling_enabled does not match source manifest: "
                f"source={declared_coupling}, requested={coupling_enabled}"
            )
        surcharge_path = source_manifest.parent / "products" / "surcharge_events.csv"
        if declared_coupling is True and not surcharge_path.exists():
            errors.append(
                "source manifest declares coupling_enabled=true but "
                "products/surcharge_events.csv is absent"
            )
        if declared_coupling is False and surcharge_path.exists():
            errors.append(
                "source manifest declares coupling_enabled=false but "
                "products/surcharge_events.csv is present"
            )
        if declared_coupling is True:
            try:
                depth_output_binding = _validate_coupled_depth_output(source_payload, depth_raster)
            except SegmentStatusError as exc:
                errors.append(str(exc))
                depth_output_binding = None
        else:
            depth_output_binding = None
    else:
        depth_output_binding = None

    if errors:
        raise ConfigResolutionError(
            "segment-status startup configuration failed:\n- " + "\n- ".join(errors)
        )
    return {
        **resolved,
        "valid_time_utc": valid,
        "forecast_lead_minutes": int(forecast_lead_minutes),
        "coupling_enabled": bool(coupling_enabled),
        "source_payload": source_payload,
        "declared_coupling": declared_coupling,
        "admission_view": admission_view,
        "product_label": (UNCOUPLED_BASELINE_LABEL if admission_view is not None else None),
        "depth_output_binding": depth_output_binding,
        "depth_field_state": (
            depth_output_binding["depth_field_state"]
            if depth_output_binding is not None
            else "uncoupled_source_depth"
        ),
        "surcharge_path": (
            source_manifest.parent / "products" / "surcharge_events.csv"
            if declared_coupling is True
            else None
        ),
    }


def build_segment_status_product(
    *,
    depth_raster: Path,
    road_raster: Path,
    lookup_csv: Path,
    output_dir: Path,
    source_manifest: Path,
    valid_time_utc: str,
    forecast_lead_minutes: int,
    coupling_enabled: bool | None,
    buffered_road_raster: Path | None = None,
    contract_path: Path = DEFAULT_CONTRACT_PATH,
    baseline_admission: Path | None = None,
    basin_class_raster: Path | None = None,
    excluded_basin_classes: frozenset[int] | None = None,
    exclusion_mode: str = "segment_touch",
    osm_water_gpkg: Path | None = None,
    admit_dirty_tree_reason: str | None = None,
) -> dict[str, Any]:
    """Build the product and its lifecycle manifest from realized inputs."""
    excluded_basin_classes = frozenset(excluded_basin_classes or frozenset())
    if excluded_basin_classes and basin_class_raster is None:
        raise SegmentStatusError(
            "excluded_basin_classes requires basin_class_raster: exclusions are asserted "
            "against realized class bytes, never against a declaration alone"
        )
    if not excluded_basin_classes and basin_class_raster is not None:
        raise SegmentStatusError(
            "basin_class_raster without excluded_basin_classes is ambiguous; pass an "
            "explicit class set (empty set means legacy no-exclusion behaviour)"
        )
    if excluded_basin_classes and exclusion_mode not in EXCLUSION_MODES:
        raise SegmentStatusError(
            f"unknown exclusion_mode {exclusion_mode!r}; expected one of {EXCLUSION_MODES}"
        )
    config = _resolve_product_config(
        depth_raster=depth_raster,
        road_raster=road_raster,
        lookup_csv=lookup_csv,
        output_dir=output_dir,
        source_manifest=source_manifest,
        valid_time_utc=valid_time_utc,
        forecast_lead_minutes=forecast_lead_minutes,
        coupling_enabled=coupling_enabled,
        buffered_road_raster=buffered_road_raster,
        contract_path=contract_path,
        baseline_admission=baseline_admission,
        basin_class_raster=basin_class_raster,
        osm_water_gpkg=osm_water_gpkg,
    )
    if admit_dirty_tree_reason is not None:
        provenance = _admitted_dirty_git_provenance(REPO, admit_dirty_tree_reason)
    else:
        provenance = _git_provenance()
    producer_issue_time_utc = normalise_utc(datetime.now(UTC).isoformat())
    _validate_time_order(config["valid_time_utc"], producer_issue_time_utc, forecast_lead_minutes)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    started = time.perf_counter()
    requirements = load_requirements(contract_path)
    run_id = output_dir.name
    source_bundle = output_dir / "provenance" / "source.bundle"
    # Rule 6: inputs and their SHAs enter the manifest at run start, not only on
    # success -- provenance that exists only when nothing goes wrong is not provenance.
    start_input_hashes = {
        "depth_raster": sha256_file(depth_raster),
        "road_raster": sha256_file(road_raster),
        "lookup_csv": sha256_file(lookup_csv),
        "buffered_road_raster": sha256_file(buffered_road_raster) if buffered_road_raster else None,
        "depth_source_manifest": sha256_file(source_manifest),
        **(
            {"basin_class_raster": sha256_file(basin_class_raster)}
            if basin_class_raster is not None
            else {}
        ),
        **({"osm_water_gpkg": sha256_file(osm_water_gpkg)} if osm_water_gpkg is not None else {}),
    }
    storage_exclusion_start: dict[str, Any] | None = None
    if excluded_basin_classes:
        storage_exclusion_start = {
            "rule": (
                "cells of segments affected by basin_class in excluded_classes are dropped "
                "from per-segment min/max/count aggregation and from flood_status "
                "determination; a segment left with zero contributing cells synthesizes "
                "no_data/unknown/[0,0]"
            ),
            "mode": exclusion_mode,
            "mode_note": (
                "segment_touch = any segment with >=1 excluded-class cell loses all its "
                "cells (audit 'touch test' semantics; the only mode reproducing the "
                "audited 33311/369/44/134/61.7 and the 61 residual >3 m segments); "
                "cell = only the excluded-class cells themselves are dropped"
            ),
            "excluded_classes": sorted(excluded_basin_classes),
            "default_variant": (
                "segment_touch_class_1_2_3"
                if exclusion_mode == "segment_touch"
                else "cell_class_1_2_3"
            ),
            "definition_basis": (
                "identical to the solver's results.exclusions definition used by the "
                "calibration loss (storage/lakes=1, quarries=2, landfills=3) -- same "
                "definition, not a second invention"
            ),
            "variants_measured": ["no_exclusion", "class_1_only", "class_1_2_3"],
            "all_canonical_cells_excluded": None,
            "owner_gate": (
                "G1 was scored on the contaminated v5 product; this v6 sits beside it and "
                "becomes default only after owner adjudication; no G1/WF-4 rescore in this unit"
            ),
        }
    sidecar_path = output_dir / "products" / "segment_osm_water_intersection.csv"
    manifest: dict[str, Any] = {
        "stage": "wf3_segment_status_product",
        "status": "running",
        **provenance,
        "run_id": run_id,
        "start_time_iso": producer_issue_time_utc,
        "resolved_config": {
            key: manifest_config_value(value)
            for key, value in config.items()
            if key != "source_payload"
        },
        "source_manifest_path": relative_repo_path(manifest_path),
        "depth_source_manifest_path": relative_repo_path(source_manifest),
        "coupling_enabled": config["coupling_enabled"],
        "product_label": config["product_label"],
        "demo_fallback": config["admission_view"] is not None,
        "uncoupled_baseline_admission_path": (
            relative_repo_path(baseline_admission) if config["admission_view"] is not None else None
        ),
        "uncoupled_baseline_admission_sha256": (
            config["admission_view"]["sha256"] if config["admission_view"] is not None else None
        ),
        "depth_field_state": config["depth_field_state"],
        "forcing_kind": (
            config["admission_view"]["forcing_kind"]
            if config["admission_view"] is not None
            else None
        ),
        "temporal_aggregation": (
            config["admission_view"]["temporal_aggregation"]
            if config["admission_view"] is not None
            else None
        ),
        "event_window_start_utc": (
            config["admission_view"]["event_window_start_utc"]
            if config["admission_view"] is not None
            else None
        ),
        "event_window_end_utc": (
            config["admission_view"]["event_window_end_utc"]
            if config["admission_view"] is not None
            else None
        ),
        "depth_raster_output_binding": config["depth_output_binding"],
        "surcharge_events_path": (
            relative_repo_path(config["surcharge_path"])
            if config["surcharge_path"] is not None
            else None
        ),
        "input_paths": {
            "depth_raster": relative_repo_path(depth_raster),
            "road_raster": relative_repo_path(road_raster),
            "lookup_csv": relative_repo_path(lookup_csv),
            "buffered_road_raster": relative_repo_path(buffered_road_raster),
            "depth_source_manifest": relative_repo_path(source_manifest),
            **(
                {"basin_class_raster": relative_repo_path(basin_class_raster)}
                if basin_class_raster is not None
                else {}
            ),
            **(
                {"osm_water_gpkg": relative_repo_path(osm_water_gpkg)}
                if osm_water_gpkg is not None
                else {}
            ),
        },
        "input_sha256_at_start": start_input_hashes,
        "storage_water_exclusion": storage_exclusion_start,
        "compute_budget_estimate": {
            "device": "cpu",
            "grid_cells_per_array": inputs_grid_cells_estimate(depth_raster),
            "aggregation_passes_planned": 1 + (2 if excluded_basin_classes else 0),
            "estimated_wall_clock_sec_upper": 120,
        },
        "parameters": {
            "depth_threshold_m": float(PRIMARY_RULE.depth_threshold_m),
            "fraction_threshold": float(PRIMARY_RULE.fraction_threshold),
            "contiguous_cells": int(PRIMARY_RULE.contiguous_cells),
            "band_convention": requirements.band_convention,
            "device": "cpu",
        },
        "outputs": {
            "csv": relative_repo_path(output_dir / "products" / "segment_status.csv"),
            "json": relative_repo_path(output_dir / "products" / "segment_status.json"),
            **(
                {"osm_water_sidecar_csv": relative_repo_path(sidecar_path)}
                if osm_water_gpkg is not None
                else {}
            ),
        },
        "source_snapshot_bundle": (
            relative_repo_path(source_bundle) if source_bundle.is_file() else None
        ),
        "source_snapshot_bundle_sha256": (
            sha256_file(source_bundle) if source_bundle.is_file() else None
        ),
    }
    write_json_atomic(manifest_path, manifest)
    try:
        inputs = load_aligned_rasters(depth_raster, road_raster)
        lookup = _read_lookup(lookup_csv)
        if inputs.road_ids.shape != requirements.grid_shape:
            raise SegmentStatusError(
                "realized grid shape does not match the frozen depth-product identity: "
                f"{inputs.road_ids.shape} != {requirements.grid_shape}"
            )
        if CRS.from_user_input(inputs.crs).to_epsg() != requirements.grid_epsg:
            raise SegmentStatusError(
                "realized grid CRS does not match the frozen depth-product identity"
            )
        if abs(float(inputs.transform.a) - requirements.grid_resolution_m) > 1e-9:
            raise SegmentStatusError(
                "realized grid resolution does not match the frozen depth-product identity"
            )
        index = build_road_index(inputs.road_ids)
        # Variant pass 1: the unexcluded field -- the contaminated v5 definition,
        # kept as the in-run mutation baseline for the manifest's delta record.
        baseline_frame = build_segment_status_dataframe(
            inputs.depth_m, inputs.road_ids, lookup, road_index=index
        )
        rows_frame = baseline_frame
        variant_summaries: dict[str, Any] = {"no_exclusion": summarize_variant(baseline_frame)}
        fully_excluded_count = 0
        if excluded_basin_classes:
            assert basin_class_raster is not None
            class_array, class_info = load_aligned_class_raster(basin_class_raster, inputs)
            road_flat = inputs.road_ids.reshape(-1)
            # Direct per-segment accounting of dropped cells, used to cross-check
            # the synthesized no-data delta below (V2: two independent observables).
            n_ids = int(inputs.road_ids.max()) + 1
            total_cells_by_id = np.bincount(road_flat[road_flat > 0], minlength=n_ids)
            present_ids = np.unique(road_flat[road_flat > 0])

            def _variant_frame(classes: frozenset[int]) -> pd.DataFrame:
                mask, mask_info = resolve_exclusion_mask(
                    inputs.road_ids, class_array, classes, exclusion_mode
                )
                excl_on_road = (inputs.road_ids > 0) & mask
                excluded_cells_by_id = np.bincount(
                    road_flat[excl_on_road.reshape(-1)], minlength=n_ids
                )
                nonlocal fully_excluded_count
                if classes == excluded_basin_classes:
                    fully_excluded_count = int(
                        (total_cells_by_id[present_ids] == excluded_cells_by_id[present_ids]).sum()
                    )
                    variant_summaries["excluded_road_cell_counts"] = {
                        "mode": exclusion_mode,
                        "cells_on_roads_dropped": int(excl_on_road.sum()),
                        "segments_fully_excluded": fully_excluded_count,
                        "mask_info": mask_info,
                        "classes_present_on_grid": {
                            str(int(value)): int(count)
                            for value, count in zip(
                                *np.unique(class_array, return_counts=True), strict=True
                            )
                        },
                    }
                return build_segment_status_dataframe(
                    inputs.depth_m, inputs.road_ids, lookup, excluded_mask=mask
                )

            # Variants 2 and 3: audit-measured class-1-only, and the emitted default.
            frame_class1 = _variant_frame(frozenset({1}))
            emitted_frame = _variant_frame(excluded_basin_classes)
            variant_summaries[f"{exclusion_mode}_class_1_only"] = summarize_variant(frame_class1)
            variant_summaries[f"{exclusion_mode}_class_1_2_3"] = summarize_variant(emitted_frame)
            variant_summaries["basin_class_raster"] = class_info
            variant_summaries["variant_delta"] = {
                "no_exclusion_to_class_1": _summary_delta(
                    variant_summaries["no_exclusion"],
                    variant_summaries[f"{exclusion_mode}_class_1_only"],
                ),
                "class_1_to_class_1_2_3": _summary_delta(
                    variant_summaries[f"{exclusion_mode}_class_1_only"],
                    variant_summaries[f"{exclusion_mode}_class_1_2_3"],
                ),
            }
            rows_frame = emitted_frame
        source_manifest_rel = relative_repo_path(manifest_path)
        rows = [
            {
                "run_id": run_id,
                "segment_id": int(row.segment_id),
                "band_low_cm": int(row.band_low_cm),
                "band_high_cm": int(row.band_high_cm),
                "confidence": str(row.confidence),
                "flood_status": str(row.flood_status),
                "valid_time_utc": config["valid_time_utc"],
                "issue_time_utc": producer_issue_time_utc,
                "forecast_lead_minutes": int(config["forecast_lead_minutes"]),
                "source_manifest_path": source_manifest_rel,
            }
            for row in rows_frame.itertuples(index=False)
        ]
        products_dir = output_dir / "products"
        csv_path = products_dir / "segment_status.csv"
        json_path = products_dir / "segment_status.json"
        _write_csv_atomic(csv_path, rows)
        write_json_atomic(
            json_path,
            {
                "schema": "depth_product/1.1.0-frozen",
                "manifest_path": relative_repo_path(manifest_path),
                "rows": rows,
            },
        )
        no_data = rows_frame[rows_frame["confidence"] == "no_data"]
        flooded = rows_frame[rows_frame["flood_status"] == "flooded"]
        osm_water_stats: dict[str, Any] | None = None
        if osm_water_gpkg is not None:
            water_mask, water_info = measure_osm_water_cells(osm_water_gpkg, inputs)
            n_ids = int(inputs.road_ids.max()) + 1
            ids_flat = inputs.road_ids.reshape(-1)
            water_on_road = water_mask & (inputs.road_ids > 0)
            intersects_by_id = np.bincount(ids_flat[water_on_road.reshape(-1)], minlength=n_ids) > 0
            sidecar_rows = [
                {
                    "segment_id": int(segment_id),
                    "intersects_osm_water": bool(intersects_by_id[int(segment_id)]),
                }
                for segment_id in lookup["segment_id"].tolist()
            ]
            _write_mapping_csv_atomic(sidecar_path, sidecar_rows)
            flooded_ids = set(flooded["segment_id"].tolist())
            deep_modeled = rows_frame[
                (rows_frame["confidence"] == "modeled_direct") & (rows_frame["band_high_cm"] > 300)
            ]
            deep_ids = set(deep_modeled["segment_id"].tolist())
            osm_water_stats = {
                **water_info,
                "n_segments_intersecting": int(
                    sum(row["intersects_osm_water"] for row in sidecar_rows)
                ),
                "n_flooded_intersecting": int(
                    sum(
                        1
                        for row in sidecar_rows
                        if row["segment_id"] in flooded_ids and row["intersects_osm_water"]
                    )
                ),
                "n_deep_gt300cm_intersecting": int(
                    sum(
                        1
                        for row in sidecar_rows
                        if row["segment_id"] in deep_ids and row["intersects_osm_water"]
                    )
                ),
                "role": (
                    "N9 diagnostic only; the audit refuted OSM-water overlap as the "
                    "deep-segment mechanism, so these booleans never enter product rows"
                ),
            }
        split = _buffered_no_data_split(
            inputs.road_ids, lookup["segment_id"].tolist(), buffered_road_raster
        )
        expected_split = {
            "buffered_margin_only": requirements.no_data_buffered_margin_only,
            "fully_clipped_no_cells_anywhere": requirements.no_data_fully_clipped,
        }
        if any(split.get(key) != value for key, value in expected_split.items()):
            raise SegmentStatusError(
                "realized no-data split does not match the frozen measured split: "
                f"realized={split}, expected={expected_split}"
            )
        if len(no_data) != requirements.no_data_total + fully_excluded_count:
            raise SegmentStatusError(
                "realized no-data total does not match frozen raster-absence total plus "
                "the directly counted all-cells-excluded segments: "
                f"{len(no_data)} != {requirements.no_data_total} + {fully_excluded_count}"
            )
        input_hashes = {
            "depth_raster": sha256_file(depth_raster),
            "road_raster": sha256_file(road_raster),
            "lookup_csv": sha256_file(lookup_csv),
            "buffered_road_raster": sha256_file(buffered_road_raster),
            "depth_source_manifest": sha256_file(source_manifest),
            **(
                {"basin_class_raster": sha256_file(basin_class_raster)}
                if basin_class_raster is not None
                else {}
            ),
            **(
                {"osm_water_gpkg": sha256_file(osm_water_gpkg)}
                if osm_water_gpkg is not None
                else {}
            ),
        }
        output_hashes = {
            "csv": sha256_file(csv_path),
            "json": sha256_file(json_path),
            **(
                {"osm_water_sidecar_csv": sha256_file(sidecar_path)}
                if osm_water_gpkg is not None
                else {}
            ),
        }
        terminal_fields = {
            "status": "completed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": round(time.perf_counter() - started, 4),
            "n_segments_expected": int(len(lookup)),
            "n_no_data_segments": int(len(no_data)),
            "no_data_segments_measured_split": expected_split,
            "depth_convention": requirements.depth_convention,
            "band_convention": requirements.band_convention,
            "valid_time_utc": config["valid_time_utc"],
            "issue_time_utc": producer_issue_time_utc,
            "forecast_lead_minutes": int(config["forecast_lead_minutes"]),
            "grid_identity": {
                "shape": [int(value) for value in inputs.road_ids.shape],
                "crs": f"EPSG:{requirements.grid_epsg}",
                "resolution_m": requirements.grid_resolution_m,
                "transform": [float(value) for value in tuple(inputs.transform)],
            },
            "input_sha256": input_hashes,
            "output_sha256": output_hashes,
            "storage_water_exclusion": (
                {
                    **(storage_exclusion_start or {}),
                    "all_canonical_cells_excluded": fully_excluded_count,
                    "canonical_absent_no_data_frozen": requirements.no_data_total,
                    "realized_no_data_total": int(len(no_data)),
                }
                if excluded_basin_classes
                else None
            ),
            "variant_summaries": variant_summaries,
            "osm_water_intersection": osm_water_stats,
            "residual_anomaly": (
                {
                    "note": (
                        "REPORTED, NOT EXPLAINED AWAY: after storage-water exclusion some "
                        "flooded segments remain deeper than the 2 m plausibility band; "
                        "the class-{1,2,3} rule removes the initialized-at-spill mechanism "
                        "only, and these residuals are unexplained by it"
                    ),
                    "n_flooded_gt300cm_post_exclusion": int((flooded["band_high_cm"] > 300).sum()),
                    "max_band_high_cm_post_exclusion": (
                        int(flooded["band_high_cm"].max()) if len(flooded) else 0
                    ),
                }
                if excluded_basin_classes
                else None
            ),
            "realized_state": {
                "n_segments_expected": int(len(lookup)),
                "n_rows_written": int(len(rows)),
                "n_modeled_direct": int((rows_frame["confidence"] == "modeled_direct").sum()),
                "n_no_data": int(len(no_data)),
                "n_flooded": int(len(flooded)),
                "n_not_flooded": int((rows_frame["flood_status"] == "not_flooded").sum()),
                "no_data_segments_measured_split": split,
                "grid": {
                    "shape": [int(value) for value in inputs.road_ids.shape],
                    "crs": inputs.crs,
                    "transform": [float(value) for value in tuple(inputs.transform)],
                    "road_cell_count": int(index.road_mask.sum()),
                },
                "input_sha256": input_hashes,
            },
        }
        manifest = {**manifest, **terminal_fields}
        write_json_atomic(manifest_path, manifest)
        validate_product_manifest(
            manifest,
            manifest_path,
            rows=rows,
            lookup_ids=set(int(value) for value in lookup["segment_id"]),
            repo_root=REPO,
            contract_path=contract_path,
        )
        return {"manifest": manifest, "rows": rows, "csv_path": csv_path, "json_path": json_path}
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


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config is absent: {path}")
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"config must be a mapping: {path}")
    return payload


app = typer.Typer(add_completion=False)


@app.command()
def main(
    depth_raster: Path = typer.Option(..., help="Realized float depth raster in metres"),
    output_dir: Path = typer.Option(..., help="Fresh writable product run directory"),
    road_raster: Path | None = typer.Option(None, help="Canonical road segment-ID raster"),
    lookup_csv: Path | None = typer.Option(None, help="Road segment lookup CSV"),
    source_manifest: Path | None = typer.Option(None, help="Completed solver manifest"),
    buffered_road_raster: Path | None = typer.Option(
        None, help="Optional realized buffered road-ID raster for no-data split"
    ),
    baseline_admission: Path | None = typer.Option(
        None,
        help="Audited data/curation admission for a legacy UNCOUPLED BASELINE manifest",
    ),
    basin_class_raster: Path | None = typer.Option(
        None, help="Basin-class raster enabling storage-water exclusion"
    ),
    exclude_basin_classes: str = typer.Option(
        "",
        help="Comma-separated basin_class values to exclude (e.g. '1,2,3'); empty disables",
    ),
    exclusion_mode: str = typer.Option(
        "segment_touch",
        help="Exclusion semantics: segment_touch (audit-reproducing) or cell",
    ),
    osm_water_gpkg: Path | None = typer.Option(
        None, help="Optional OSM water polygons for the N9 diagnostic sidecar"
    ),
    admit_dirty_tree_reason: str | None = typer.Option(
        None,
        help="Explicit reason admitting a dirty source tree (recorded, never laundered)",
    ),
    valid_time_utc: str = typer.Option(..., help="Aware UTC time represented by the depth raster"),
    forecast_lead_minutes: int = typer.Option(..., min=0),
    coupling_enabled: str = typer.Option(
        ..., help="Explicit source coupling declaration: true/false"
    ),
    config: Path = typer.Option(REPO / "configs/validation.yaml", help="Path config for defaults"),
) -> None:
    """Write the contract-frozen per-road-segment depth product."""
    cfg = _load_yaml(config)
    groundtruth_cfg = cfg.get("groundtruth", {})
    segment_cfg = cfg.get("segment_validation", {})
    resolved_road = _repo_path(road_raster or groundtruth_cfg.get("road_segment_id_path", ""))
    resolved_lookup = _repo_path(lookup_csv or segment_cfg.get("roads_segment_lookup_path", ""))
    resolved_source = _repo_path(source_manifest or (depth_raster.parent / "manifest.json"))
    coupling_text = coupling_enabled.strip().lower()
    if coupling_text not in {"true", "false"}:
        raise typer.BadParameter("coupling_enabled must be true or false")
    try:
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
        result = build_segment_status_product(
            depth_raster=_repo_path(depth_raster),
            road_raster=resolved_road,
            lookup_csv=resolved_lookup,
            output_dir=_repo_path(output_dir),
            source_manifest=resolved_source,
            valid_time_utc=valid_time_utc,
            forecast_lead_minutes=forecast_lead_minutes,
            coupling_enabled=coupling_text == "true",
            buffered_road_raster=(
                _repo_path(buffered_road_raster) if buffered_road_raster is not None else None
            ),
            baseline_admission=(
                _repo_path(baseline_admission) if baseline_admission is not None else None
            ),
            basin_class_raster=(
                _repo_path(basin_class_raster) if basin_class_raster is not None else None
            ),
            excluded_basin_classes=excluded_classes,
            exclusion_mode=exclusion_mode,
            osm_water_gpkg=_repo_path(osm_water_gpkg) if osm_water_gpkg is not None else None,
            admit_dirty_tree_reason=admit_dirty_tree_reason,
        )
    except ConfigResolutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    manifest = result["manifest"]
    typer.echo(f"status={manifest['status']}")
    typer.echo(f"manifest={_repo_path(output_dir) / 'manifest.json'}")
    typer.echo(f"csv={result['csv_path']}")
    typer.echo(f"rows={manifest['realized_state']['n_rows_written']}")
    typer.echo(f"no_data={manifest['realized_state']['n_no_data']}")
    realized = manifest["realized_state"]
    typer.echo(f"flooded={realized['n_flooded']}")
    if manifest.get("variant_summaries"):
        for name, summary in manifest["variant_summaries"].items():
            if isinstance(summary, dict) and "n_flooded" in summary:
                typer.echo(
                    f"variant[{name}] flooded={summary['n_flooded']} "
                    f"max_cm={summary['max_band_high_cm']} "
                    f"p50={summary['flooded_p50_band_high_cm']} "
                    f"p90={summary['flooded_p90_band_high_cm']} "
                    f"mean={summary['flooded_mean_band_high_cm']}"
                )
    if manifest.get("residual_anomaly"):
        typer.echo(
            "residual_anomaly: n_flooded_gt300cm="
            f"{manifest['residual_anomaly']['n_flooded_gt300cm_post_exclusion']} "
            f"max_cm={manifest['residual_anomaly']['max_band_high_cm_post_exclusion']} "
            "(reported, not explained away)"
        )


if __name__ == "__main__":
    app()
