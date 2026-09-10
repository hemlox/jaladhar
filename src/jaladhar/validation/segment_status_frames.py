"""This module is the WF-3b addition to the WF-3 product boundary.  It consumes the
realized per-frame solver rasters of an uncoupled historical replay (admitted via
emits, per frame, a contract-shaped CSV/JSON twin: per-segment depth bands in
Contract fit, stated plainly (the frozen text is not edited):
* every frozen ROW and CONSUMER assertion applies verbatim to each per-frame
for one product.  The run manifest records this deviation explicitly."""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import typer

REPO = Path(__file__).resolve().parents[3]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from jaladhar.provenance import require_clean_git, write_json_atomic  # noqa: E402
from jaladhar.validation.depth_product_contract import (  # noqa: E402
    UNCOUPLED_BASELINE_LABEL,
    load_requirements,
    sha256_file,
    validate_frame_series_admission,
)  # noqa: E402
from jaladhar.validation.segment_status import (  # noqa: E402
    EXCLUSION_MODES,
    PRODUCT_FIELDS,
    ConfigResolutionError,
    SegmentStatusError,
    _admitted_dirty_git_provenance,
    _assert_distinct_paths,
    _buffered_no_data_split,
    _read_lookup,
    _validate_time_order,
    build_road_index,
    build_segment_status_dataframe,
    load_aligned_class_raster,
    load_aligned_rasters,
    normalise_utc,
    resolve_exclusion_mask,
)  # noqa: E402
from jaladhar.validation.segment_validation import PRIMARY_RULE  # noqa: E402

FRAME_FILE_RE = re.compile(r"^depth_t(\d+)s\.tif$")
DEFAULT_CONTRACT_PATH = REPO / "configs" / "contracts" / "depth_product.json"
DEFAULT_FRAME_ADMISSION = REPO / "data/curation/wf3_replay2_frame_series_admission.json"


class MultiframeProductError(RuntimeError):
    """Raised when the realized frame series cannot support the product."""


def _relative(path: Path) -> str:

    import os

    return os.path.relpath(Path(path).resolve(), REPO.resolve())


def _config_value(value: Any) -> Any:
    if isinstance(value, Path):
        resolved = value.resolve()
        try:
            return resolved.relative_to(REPO.resolve()).as_posix()
        except ValueError:
            return str(resolved)
    if isinstance(value, dict):
        return {key: _config_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_config_value(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_config_value(item) for item in value]
    return value


def _git_provenance() -> dict[str, Any]:
    git_sha = require_clean_git(REPO)
    return {"git_sha": git_sha, "git_tree_clean": True, "git_dirty_paths": []}


def _frame_entries(admission_payload: dict[str, Any]) -> list[dict[str, Any]]:
    frames = admission_payload.get("frames")
    if not isinstance(frames, list) or not frames:
        raise MultiframeProductError("frame-series admission declares no frames")
    return sorted(frames, key=lambda frame: int(frame["offset_seconds"]))


def _frame_path(series_dir: Path, relative_file: str) -> Path:
    name = relative_file.rsplit("/", 1)[-1]
    match = FRAME_FILE_RE.fullmatch(name)
    if match is None:
        raise MultiframeProductError(f"unexpected series filename: {name}")
    return series_dir / name


def _resolve_multiframe_config(
    *,
    source_manifest: Path,
    output_dir: Path,
    road_raster: Path,
    lookup_csv: Path,
    buffered_road_raster: Path,
    contract_path: Path,
    baseline_admission: Path | None,
    basin_class_raster: Path | None = None,
    exclude_basin_classes: frozenset[int] | None = None,
    exclusion_mode: str = "segment_touch",
) -> dict[str, Any]:
    errors: list[str] = []
    excluded_basin_classes = frozenset(exclude_basin_classes or frozenset())
    if excluded_basin_classes and basin_class_raster is None:
        errors.append(
            "exclude_basin_classes requires basin_class_raster: exclusions are asserted "
            "against realized class bytes, never against a declaration alone"
        )
    if basin_class_raster is not None and not excluded_basin_classes:
        errors.append(
            "basin_class_raster without exclude_basin_classes is ambiguous; pass an explicit "
            "class set"
        )
    if excluded_basin_classes and exclusion_mode not in EXCLUSION_MODES:
        errors.append(
            f"unknown exclusion_mode {exclusion_mode!r}; expected one of {EXCLUSION_MODES}"
        )
    resolved: dict[str, Any] = {
        "source_manifest": source_manifest,
        "output_dir": output_dir,
        "road_raster": road_raster,
        "lookup_csv": lookup_csv,
        "buffered_road_raster": buffered_road_raster,
        "contract_path": contract_path,
        "basin_class_raster": basin_class_raster,
    }
    for name, path in resolved.items():
        if path is not None and path != output_dir and not path.exists():
            errors.append(f"{name}: absent at {path}")
    if output_dir.resolve() == REPO.resolve():
        errors.append("output_dir: repository root is not a writable run directory")
    expected_runs_root = (REPO / "runs").resolve()
    if output_dir.resolve().parent != expected_runs_root or not output_dir.name:
        errors.append(f"output_dir: must be exactly runs/<run_id>, got {output_dir}")
    try:
        _assert_distinct_paths(source_manifest.parent, output_dir)
    except SegmentStatusError as exc:
        errors.append(str(exc))
    if output_dir.exists() and (output_dir / "manifest.json").exists():
        errors.append(f"output_dir: refusing to replace existing manifest {output_dir}")

    try:
        payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"source_manifest unreadable: {exc}")
        payload = None
    # Coupling lattice mirrors segment_status.py: a COUPLED source is refused;
    # an explicit coupling_enabled=false declaration carries fuller provenance
    # product path already makes). Only a manifest that predates declarations
    source_declares_uncoupled = False
    if isinstance(payload, dict):
        declared_coupling = payload.get("coupling_enabled")
        if declared_coupling is True:
            errors.append(
                "frame-series products are defined only for uncoupled sources; "
                f"source manifest declares coupling_enabled={declared_coupling!r}"
            )
        elif declared_coupling is False:
            source_declares_uncoupled = True
    else:
        errors.append("source_manifest is not a JSON object")

    baseline_inheritance = "legacy_uncoupled_baseline_admission"
    if source_declares_uncoupled:
        baseline_inheritance = (
            "source-declared coupling_enabled=false; legacy admission not bound "
            "(its recorded bindings belong to a different realized run by design)"
        )
    else:
        errors.extend(_baseline_admission_errors(baseline_admission, source_manifest))
        if baseline_admission is None:
            errors.append(
                "baseline_admission: required so the series inherits the audited "
                "UNCOUPLED BASELINE decision"
            )
    if errors:
        raise ConfigResolutionError(
            "multiframe product startup configuration failed:\n- " + "\n- ".join(errors)
        )
    assert source_declares_uncoupled or baseline_admission is not None
    return {
        **resolved,
        "product_label": UNCOUPLED_BASELINE_LABEL,
        "baseline_inheritance": baseline_inheritance,
        "baseline_admission": (
            baseline_admission
            if not source_declares_uncoupled and baseline_admission is not None
            else None
        ),
        "baseline_admission_sha256": (
            sha256_file(baseline_admission)
            if not source_declares_uncoupled and baseline_admission is not None
            else None
        ),
        "excluded_basin_classes": excluded_basin_classes,
        "exclusion_mode": exclusion_mode,
    }


def _baseline_admission_errors(baseline_admission: Path | None, source_manifest: Path) -> list[str]:
    from jaladhar.validation.depth_product_contract import validate_uncoupled_baseline_admission

    if baseline_admission is None:
        return []
    try:
        validate_uncoupled_baseline_admission(baseline_admission, source_manifest, repo_root=REPO)
    except (OSError, SegmentStatusError, ValueError) as exc:
        return [str(exc)]
    return []


def build_multiframe_product(
    *,
    source_manifest: Path,
    output_dir: Path,
    road_raster: Path,
    lookup_csv: Path,
    buffered_road_raster: Path,
    frame_admission: Path = DEFAULT_FRAME_ADMISSION,
    contract_path: Path = DEFAULT_CONTRACT_PATH,
    baseline_admission: Path | None = None,
    basin_class_raster: Path | None = None,
    exclude_basin_classes: frozenset[int] | None = None,
    exclusion_mode: str = "segment_touch",
    admit_dirty_tree_reason: str | None = None,
    adjudication_manifest: Path | None = None,
) -> dict[str, Any]:
    """Build the per-frame contract-shaped product series with a lifecycle manifest."""

    excluded_basin_classes = frozenset(exclude_basin_classes or frozenset())
    if excluded_basin_classes and basin_class_raster is None:
        raise SegmentStatusError(
            "exclude_basin_classes requires basin_class_raster: exclusions are asserted "
            "against realized class bytes, never against a declaration alone"
        )
    if basin_class_raster is not None and not excluded_basin_classes:
        raise SegmentStatusError(
            "basin_class_raster without exclude_basin_classes is ambiguous; pass an explicit "
            "class set"
        )
    config = _resolve_multiframe_config(
        source_manifest=source_manifest,
        output_dir=output_dir,
        road_raster=road_raster,
        lookup_csv=lookup_csv,
        buffered_road_raster=buffered_road_raster,
        contract_path=contract_path,
        baseline_admission=baseline_admission,
        basin_class_raster=basin_class_raster,
        exclude_basin_classes=excluded_basin_classes,
        exclusion_mode=exclusion_mode,
    )
    requirements = load_requirements(contract_path)

    # Owner-adjudication binding: recorded from realized manifest bytes, never assumed.
    adjudication_ref: dict[str, Any] | None = None
    open_anomaly_start: dict[str, Any] | None = None
    if excluded_basin_classes:
        if adjudication_manifest is None or not adjudication_manifest.exists():
            raise ConfigResolutionError(
                "exclude_basin_classes requires an adopted adjudication manifest "
                f"(got {adjudication_manifest})"
            )
        try:
            adjudication_payload = json.loads(adjudication_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigResolutionError(
                f"adjudication manifest unreadable: {adjudication_manifest}: {exc}"
            ) from exc
        decision = adjudication_payload.get("owner_adjudication", {}).get("decision")
        if decision != "adopt":
            raise ConfigResolutionError(
                "adjudication manifest does not record decision='adopt' (realized "
                f"value: {decision!r}); the excluded series cannot be emitted unadopted"
            )
        residual = adjudication_payload.get("residual_anomaly") or {}
        adjudication_ref = {
            "path": _relative(adjudication_manifest),
            "sha256": sha256_file(adjudication_manifest),
            "decision": decision,
        }
        open_anomaly_start = {
            "status": "OPEN",
            "carried_from": {
                "path": _relative(adjudication_manifest),
                "field": "residual_anomaly",
                "n_flooded_gt300cm_post_exclusion": residual.get(
                    "n_flooded_gt300cm_post_exclusion"
                ),
                "max_band_high_cm_post_exclusion": residual.get("max_band_high_cm_post_exclusion"),
            },
            "n_segments_gt300cm": residual.get("n_flooded_gt300cm_post_exclusion"),
            "range_cm_low_bound_cm": 301,
            "range_cm_high_cm": residual.get("max_band_high_cm_post_exclusion"),
            "policy": ("never explained away or excluded without measured cause"),
        }

    frame_admission_view = validate_frame_series_admission(
        frame_admission,
        source_manifest,
        repo_root=REPO,
    )
    admission_payload = json.loads(frame_admission.read_text(encoding="utf-8"))
    frames = _frame_entries(admission_payload)
    series_dir = REPO / str(admission_payload["depth_raster_dir"])

    if admit_dirty_tree_reason is not None:
        provenance = _admitted_dirty_git_provenance(REPO, admit_dirty_tree_reason)
    else:
        provenance = _git_provenance()
    producer_issue_time_utc = normalise_utc(datetime.now(UTC).isoformat())
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    started = time.perf_counter()
    run_id = output_dir.name

    storage_exclusion_start: dict[str, Any] | None = None
    if excluded_basin_classes:
        assert basin_class_raster is not None
        storage_exclusion_start = {
            "rule": (
                "cells of segments affected by basin_class in excluded_classes are dropped "
                "from per-segment min/max/count aggregation and from flood_status "
                "determination, identically in every frame; a segment left with zero "
                "contributing cells synthesizes no_data/unknown/[0,0]"
            ),
            "mode": exclusion_mode,
            "mode_note": (
                "segment_touch = any segment with >=1 excluded-class cell loses all its "
                "cells (audit 'touch test' semantics); cell = only the excluded-class cells "
                "themselves are dropped. One mask is computed once over the canonical road "
                "grid and reused for every frame."
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
                "definition, not a second invention; identical to the adopted v6 "
                "event-maximum product's exclusion"
            ),
            "variant_reference": (
                "per-variant deltas were measured on the event-maximum stack by the v6 "
                "producer and are not re-measured per frame here"
            ),
            "all_canonical_cells_excluded": None,
            "canonical_absent_no_data_frozen": requirements.no_data_total,
            "realized_no_data_total_per_frame": None,
        }

    inputs = load_aligned_rasters(_first_frame_probe(frames, series_dir), road_raster)
    lookup = _read_lookup(lookup_csv)
    index = build_road_index(inputs.road_ids) if not excluded_basin_classes else None
    split = _buffered_no_data_split(
        inputs.road_ids, lookup["segment_id"].tolist(), buffered_road_raster
    )
    expected_split = {
        "buffered_margin_only": requirements.no_data_buffered_margin_only,
        "fully_clipped_no_cells_anywhere": requirements.no_data_fully_clipped,
    }
    if any(split.get(key) != value for key, value in expected_split.items()):
        raise SegmentStatusError(
            f"realized no-data split does not match the frozen split: {split} != {expected_split}"
        )

    # reused for every frame (V8: asserted against realized class bytes at this seam).
    fully_excluded_count = 0
    excluded_mask: np.ndarray | None = None
    if excluded_basin_classes:
        assert basin_class_raster is not None
        class_array, class_info = load_aligned_class_raster(basin_class_raster, inputs)
        excluded_mask, mask_info = resolve_exclusion_mask(
            inputs.road_ids, class_array, sorted(excluded_basin_classes), exclusion_mode
        )
        # the synthesized no-data delta (V2: two independent observables).
        road_flat = inputs.road_ids.reshape(-1)
        n_ids = int(inputs.road_ids.max()) + 1
        total_cells_by_id = np.bincount(road_flat[road_flat > 0], minlength=n_ids)
        present_ids = np.unique(road_flat[road_flat > 0])
        excl_on_road = (inputs.road_ids > 0) & excluded_mask
        excluded_cells_by_id = np.bincount(road_flat[excl_on_road.reshape(-1)], minlength=n_ids)
        fully_excluded_count = int(
            (total_cells_by_id[present_ids] == excluded_cells_by_id[present_ids]).sum()
        )
        expected_no_data_per_frame = requirements.no_data_total + fully_excluded_count
        assert storage_exclusion_start is not None
        storage_exclusion_start.update(
            {
                "all_canonical_cells_excluded": fully_excluded_count,
                "realized_no_data_total_per_frame": expected_no_data_per_frame,
                "excluded_road_cell_counts": {
                    "mode": exclusion_mode,
                    "cells_on_roads_dropped": int(excl_on_road.sum()),
                    "segments_fully_excluded": fully_excluded_count,
                    "mask_info": mask_info,
                    "basin_class_raster": class_info,
                    "classes_present_on_grid": {
                        str(int(value)): int(count)
                        for value, count in zip(
                            *np.unique(class_array, return_counts=True), strict=True
                        )
                    },
                },
            }
        )

    start_input_hashes = {
        "road_raster": sha256_file(road_raster),
        "lookup_csv": sha256_file(lookup_csv),
        "buffered_road_raster": sha256_file(buffered_road_raster),
        "depth_source_manifest": sha256_file(source_manifest),
        "frame_series_admission": sha256_file(frame_admission),
        **(
            {"basin_class_raster": sha256_file(basin_class_raster)}
            if basin_class_raster is not None
            else {}
        ),
    }
    manifest: dict[str, Any] = {
        "stage": "wf3_segment_status_multiframe",
        "status": "running",
        **provenance,
        "run_id": run_id,
        "start_time_iso": producer_issue_time_utc,
        "issue_time_utc": producer_issue_time_utc,
        "resolved_config": {key: _config_value(value) for key, value in config.items()},
        "product_label": UNCOUPLED_BASELINE_LABEL,
        "coupling_enabled": False,
        "demo_fallback": False,
        "forcing_kind": frame_admission_view["forcing_kind"],
        "temporal_aggregation": frame_admission_view["temporal_aggregation"],
        "series_kind": admission_payload.get("series_kind"),
        "frame_count_expected": frame_admission_view["frame_count"],
        "cadence_seconds": frame_admission_view["cadence_seconds"],
        "valid_time_rule": admission_payload.get("valid_time_rule"),
        "event_window_start_utc": admission_payload.get("event_window_start_utc"),
        "frame_series_admission_path": _relative(frame_admission),
        "frame_series_admission_sha256": frame_admission_view["sha256"],
        "uncoupled_baseline_admission_path": (
            _relative(config["baseline_admission"])
            if config.get("baseline_admission") is not None
            else None
        ),
        "uncoupled_baseline_admission_sha256": config.get("baseline_admission_sha256"),
        "depth_source_manifest_path": _relative(source_manifest),
        "artifact_location_deviation": (
            "Per-frame twins live under products/frames/<tag>/ because the frozen contract "
            "fixes one product file pair per runs/<run_id>/products location; all frozen row "
            "and consumer assertions apply verbatim to each frame."
        ),
        "input_paths": {
            "road_raster": _relative(road_raster),
            "lookup_csv": _relative(lookup_csv),
            "buffered_road_raster": _relative(buffered_road_raster),
            "depth_source_manifest": _relative(source_manifest),
            "frame_series_dir": _relative(series_dir),
            **(
                {"basin_class_raster": _relative(basin_class_raster)}
                if basin_class_raster is not None
                else {}
            ),
        },
        "input_sha256": start_input_hashes,
        "storage_water_exclusion": storage_exclusion_start,
        "adjudication_ref": adjudication_ref,
        "open_anomaly": open_anomaly_start,
        "parameters": {
            "depth_threshold_m": float(PRIMARY_RULE.depth_threshold_m),
            "fraction_threshold": float(PRIMARY_RULE.fraction_threshold),
            "contiguous_cells": int(PRIMARY_RULE.contiguous_cells),
            "band_convention": requirements.band_convention,
            "device": "cpu",
        },
        "outputs": {"frames_dir": _relative(output_dir / "products" / "frames")},
    }
    write_json_atomic(manifest_path, manifest)

    frames_written: list[dict[str, Any]] = []
    try:
        measured_first_frame_seconds: float | None = None
        for position, frame in enumerate(frames):
            frame_started = time.perf_counter()
            path = _frame_path(series_dir, str(frame["file"]))
            frame_inputs = load_aligned_rasters(path, inputs.road_path)
            rows_frame = build_segment_status_dataframe(
                frame_inputs.depth_m,
                inputs.road_ids,
                lookup,
                road_index=index,
                excluded_mask=excluded_mask,
            )
            no_data_count = int((rows_frame["confidence"] == "no_data").sum())
            expected_no_data_per_frame = requirements.no_data_total + fully_excluded_count
            if no_data_count != expected_no_data_per_frame:
                raise SegmentStatusError(
                    f"frame {frame['file']} no-data total {no_data_count} != frozen "
                    f"{requirements.no_data_total} + fully-excluded {fully_excluded_count}"
                )
            flooded_frame = rows_frame[rows_frame["flood_status"] == "flooded"]
            frame_max_band_high_cm = (
                int(flooded_frame["band_high_cm"].max()) if len(flooded_frame) else 0
            )
            valid_time_utc = normalise_utc(str(frame["valid_time_utc"]))
            _validate_time_order(valid_time_utc, producer_issue_time_utc, lead_minutes=0)
            tag = f"t{int(frame['offset_seconds']):07d}"
            frame_dir = output_dir / "products" / "frames" / tag
            csv_path = frame_dir / "segment_status.csv"
            json_path = frame_dir / "segment_status.json"
            rows = [
                {
                    "run_id": run_id,
                    "segment_id": int(row.segment_id),
                    "band_low_cm": int(row.band_low_cm),
                    "band_high_cm": int(row.band_high_cm),
                    "confidence": str(row.confidence),
                    "flood_status": str(row.flood_status),
                    "valid_time_utc": valid_time_utc,
                    "issue_time_utc": producer_issue_time_utc,
                    "forecast_lead_minutes": 0,
                    "source_manifest_path": _relative(manifest_path),
                }
                for row in rows_frame.itertuples(index=False)
            ]
            _atomic_csv(csv_path, rows)
            write_json_atomic(
                json_path,
                {
                    "schema": "depth_product/1.1.0-frozen",
                    "manifest_path": _relative(manifest_path),
                    "frame": {
                        "tag": tag,
                        "file": frame["file"],
                        "offset_seconds": int(frame["offset_seconds"]),
                        "sha256": frame["sha256"],
                        "valid_time_utc": valid_time_utc,
                    },
                    "rows": rows,
                },
            )
            elapsed = time.perf_counter() - frame_started
            if measured_first_frame_seconds is None:
                measured_first_frame_seconds = elapsed
            remaining = len(frames) - position - 1
            assert measured_first_frame_seconds is not None
            eta_s = remaining * measured_first_frame_seconds
            typer.echo(
                f"frame {position + 1}/{len(frames)} {tag} "
                f"flooded={int((rows_frame['flood_status'] == 'flooded').sum())} "
                f"max_cm={frame_max_band_high_cm} "
                f"elapsed={elapsed:.2f}s eta_min={eta_s / 60.0:.1f}"
            )
            frames_written.append(
                {
                    "tag": tag,
                    "file": frame["file"],
                    "offset_seconds": int(frame["offset_seconds"]),
                    "valid_time_utc": valid_time_utc,
                    "csv": _relative(csv_path),
                    "json": _relative(json_path),
                    "csv_sha256": sha256_file(csv_path),
                    "json_sha256": sha256_file(json_path),
                    "n_flooded": int((rows_frame["flood_status"] == "flooded").sum()),
                    "n_not_flooded": int((rows_frame["flood_status"] == "not_flooded").sum()),
                    "n_no_data": no_data_count,
                    "max_band_high_cm": frame_max_band_high_cm,
                    "n_flooded_gt300cm": int((flooded_frame["band_high_cm"] > 300).sum()),
                }
            )

        if len(frames_written) != frame_admission_view["frame_count"]:
            raise MultiframeProductError(
                f"wrote {len(frames_written)} frames, expected "
                f"{frame_admission_view['frame_count']}"
            )
        input_hashes = {
            "road_raster": sha256_file(road_raster),
            "lookup_csv": sha256_file(lookup_csv),
            "buffered_road_raster": sha256_file(buffered_road_raster),
            "depth_source_manifest": sha256_file(source_manifest),
            "frame_series_admission": sha256_file(frame_admission),
        }
        terminal = {
            **manifest,
            "status": "completed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": round(time.perf_counter() - started, 4),
            "frame_count_written": len(frames_written),
            "no_data_segments_measured_split": expected_split,
            "grid_identity": {
                "shape": [int(v) for v in inputs.road_ids.shape],
                "crs": inputs.crs,
                "resolution_m": float(inputs.transform.a),
            },
            "input_sha256": {
                **input_hashes,
                **(
                    {"basin_class_raster": sha256_file(basin_class_raster)}
                    if basin_class_raster is not None
                    else {}
                ),
            },
            "frames": frames_written,
            "realized_state": {
                "frames_written": len(frames_written),
                "rows_per_frame": int(len(lookup)),
                "n_no_data_per_frame": requirements.no_data_total + fully_excluded_count,
                "n_fully_excluded_segments": fully_excluded_count,
                "max_flooded_segments_over_series": max(
                    int(item["n_flooded"]) for item in frames_written
                ),
                "max_band_high_cm_over_series": max(
                    int(item["max_band_high_cm"]) for item in frames_written
                ),
                "input_sha256": input_hashes,
            },
        }
        write_json_atomic(manifest_path, terminal)
        return {"manifest": terminal}
    except BaseException as exc:
        failed = {
            **manifest,
            "status": "failed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": round(time.perf_counter() - started, 4),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "frames_completed_before_failure": len(frames_written),
        }
        write_json_atomic(manifest_path, failed)
        raise


def _first_frame_probe(frames: list[dict[str, Any]], series_dir: Path) -> Path:
    return _frame_path(series_dir, str(frames[0]["file"]))


def _atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    import csv as _csv

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = _csv.DictWriter(handle, fieldnames=PRODUCT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


app = typer.Typer(add_completion=False)


@app.command()
def main(
    output_dir: Path = typer.Option(...),
    road_raster: Path = typer.Option(REPO / "data/processed/road_segment_id.tif"),
    lookup_csv: Path = typer.Option(REPO / "data/interim/terrain/roads_segment_lookup.csv"),
    buffered_road_raster: Path = typer.Option(
        REPO / "data/interim/terrain/road_segment_id_buffered.tif"
    ),
    source_manifest: Path = typer.Option(REPO / "runs/goal_d_replay2_final_config/manifest.json"),
    frame_admission: Path = typer.Option(DEFAULT_FRAME_ADMISSION),
    contract_path: Path = typer.Option(DEFAULT_CONTRACT_PATH),
    baseline_admission: Path = typer.Option(
        REPO / "data/curation/wf3_replay2_uncoupled_baseline_admission.json"
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
    admit_dirty_tree_reason: str | None = typer.Option(
        None,
        help="Explicit reason admitting a dirty source tree (recorded, never laundered)",
    ),
    adjudication_manifest: Path | None = typer.Option(
        None,
        help="Adopted owner-adjudication manifest binding the excluded series (required "
        "when excluding)",
    ),
) -> None:
    """Write the per-frame segment product series from admitted realized rasters."""
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
            if adjudication_manifest is None:
                raise typer.BadParameter(
                    "exclude_basin_classes requires --adjudication-manifest recording the "
                    "owner's adopt decision"
                )
        result = build_multiframe_product(
            source_manifest=_repo_path(source_manifest),
            output_dir=_repo_path(output_dir),
            road_raster=_repo_path(road_raster),
            lookup_csv=_repo_path(lookup_csv),
            buffered_road_raster=_repo_path(buffered_road_raster),
            frame_admission=_repo_path(frame_admission),
            contract_path=_repo_path(contract_path),
            baseline_admission=_repo_path(baseline_admission),
            basin_class_raster=(
                _repo_path(basin_class_raster) if basin_class_raster is not None else None
            ),
            exclude_basin_classes=excluded_classes,
            exclusion_mode=exclusion_mode,
            admit_dirty_tree_reason=admit_dirty_tree_reason,
            adjudication_manifest=(
                _repo_path(adjudication_manifest) if adjudication_manifest is not None else None
            ),
        )
    except ConfigResolutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    manifest = result["manifest"]
    typer.echo(f"status={manifest['status']}")
    typer.echo(f"manifest={_repo_path(output_dir) / 'manifest.json'}")
    typer.echo(f"frames_written={manifest['frame_count_written']}")
    realized = manifest["realized_state"]
    typer.echo(f"max_flooded_over_series={realized['max_flooded_segments_over_series']}")
    typer.echo(f"max_band_high_cm_over_series={realized['max_band_high_cm_over_series']}")


def _repo_path(path: Path | str) -> Path:
    value = Path(path)
    return value if value.is_absolute() else (REPO / value)


if __name__ == "__main__":
    app()
