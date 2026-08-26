"""Shared, lightweight consumer checks for the frozen depth-product seam."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_FORECAST_LEAD_MINUTES = 3 * 60
UNCOUPLED_BASELINE_LABEL = "UNCOUPLED BASELINE"


class DepthProductContractError(ValueError):
    """A realized depth product or its manifest violates the frozen contract."""


@dataclass(frozen=True)
class DepthProductRequirements:
    """Machine-resolved constants from ``depth_product.json``."""

    version: str
    depth_convention: str
    band_convention: str
    flood_threshold_cm: int
    grid_shape: tuple[int, int]
    grid_epsg: int
    grid_resolution_m: float
    no_data_total: int
    no_data_buffered_margin_only: int
    no_data_fully_clipped: int


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DepthProductContractError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DepthProductContractError(f"{label} must be a JSON object: {path}")
    return payload


def load_requirements(contract_path: Path) -> DepthProductRequirements:
    """Resolve the exact values embedded in the frozen contract."""

    contract = _load_json(contract_path, "depth-product contract")
    guarantees = "\n".join(
        str(value) for value in contract.get("manifest_guarantees_producer_writes", [])
    )
    depth_match = re.search(r"depth_convention '([^']+)'", guarantees)
    band_match = re.search(r"band_convention '([^']+)'", guarantees)
    grid_match = re.search(
        r"grid identity EPSG:(\d+)\s+(\d+)x(\d+)\s+([0-9]+(?:\.[0-9]+)?)m",
        guarantees,
    )
    flood_description = str(contract.get("schema_fields", {}).get("flood_status", ""))
    flood_match = re.search(r"D\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*m", flood_description)
    split = contract.get("no_data_segments_measured_split")
    if not all((depth_match, band_match, grid_match, flood_match)) or not isinstance(split, dict):
        raise DepthProductContractError(
            "frozen depth-product contract does not expose required machine values: "
            f"{contract_path}"
        )
    try:
        no_data_total = int(split["total_canonical_absent"])
        margin_only = int(split["buffered_margin_only"])
        fully_clipped = int(split["fully_clipped_no_cells_anywhere"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DepthProductContractError("depth-product no-data split is malformed") from exc
    if margin_only + fully_clipped != no_data_total:
        raise DepthProductContractError("depth-product no-data split arithmetic does not reconcile")
    return DepthProductRequirements(
        version=str(contract.get("version", "")),
        depth_convention=depth_match.group(1),
        band_convention=band_match.group(1),
        flood_threshold_cm=round(float(flood_match.group(1)) * 100.0),
        grid_shape=(int(grid_match.group(2)), int(grid_match.group(3))),
        grid_epsg=int(grid_match.group(1)),
        grid_resolution_m=float(grid_match.group(4)),
        no_data_total=no_data_total,
        no_data_buffered_margin_only=margin_only,
        no_data_fully_clipped=fully_clipped,
    )


def resolve_repo_file(repo_root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DepthProductContractError(f"{field} must be a non-empty repository-relative path")
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise DepthProductContractError(f"{field} must remain repository-relative")
    resolved = (repo_root / raw).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise DepthProductContractError(f"{field} escapes the repository") from exc
    if not resolved.is_file():
        raise DepthProductContractError(f"{field} does not exist: {value}")
    return resolved


def resolve_repo_file_dir(repo_root: Path, value: Any) -> Path:
    """Like ``resolve_repo_file`` but for an existing repository-relative directory."""

    field = "depth_raster_dir"
    if not isinstance(value, str) or not value.strip():
        raise DepthProductContractError(f"{field} must be a non-empty repository-relative path")
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise DepthProductContractError(f"{field} must remain repository-relative")
    resolved = (repo_root / raw).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise DepthProductContractError(f"{field} escapes the repository") from exc
    if not resolved.is_dir():
        raise DepthProductContractError(f"{field} is not a directory: {value}")
    return resolved


def _required_int(manifest: dict[str, Any], key: str) -> int:
    value = manifest.get(key)
    if type(value) is not int:
        raise DepthProductContractError(f"manifest.{key} must be an integer")
    return value


def _declared_coupling(payload: Any) -> bool | None:
    values: list[bool] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            candidate = value.get("coupling_enabled")
            if isinstance(candidate, bool):
                values.append(candidate)
            coupling = value.get("coupling")
            if isinstance(coupling, dict) and isinstance(coupling.get("enabled"), bool):
                values.append(coupling["enabled"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    unique = set(values)
    if len(unique) > 1:
        raise DepthProductContractError(
            "upstream depth manifest has conflicting coupling declarations"
        )
    return values[0] if values else None


def validate_uncoupled_baseline_admission(
    admission_path: Path,
    source_manifest_path: Path,
    *,
    repo_root: Path,
    depth_raster_path: Path | None = None,
) -> dict[str, Any]:
    """Validate the one audited legacy source whose manifest predates coupling declarations."""

    admission_path = admission_path.resolve()
    repo_root = repo_root.resolve()
    try:
        relative = admission_path.relative_to(repo_root)
    except ValueError as exc:
        raise DepthProductContractError(
            "uncoupled-baseline admission must remain inside the repository"
        ) from exc
    if relative.parts[:2] != ("data", "curation"):
        raise DepthProductContractError(
            "uncoupled-baseline admission must live under data/curation"
        )
    admission = _load_json(admission_path, "uncoupled-baseline admission")
    source = _load_json(source_manifest_path, "upstream depth manifest")
    # The realized historical manifests record the event window at top level as
    # event_window.{start,end}; an explicit nested event block is accepted for
    # forward-compatible producers.  Absence of either is a hard error.
    source_event = source.get("event")
    if not isinstance(source_event, dict):
        candidate = source.get("event_window")
        if isinstance(candidate, dict):
            source_event = {
                "start": candidate.get("start"),
                "end": candidate.get("end"),
            }
    if (
        not isinstance(source_event, dict)
        or not isinstance(source_event.get("start"), str)
        or not isinstance(source_event.get("end"), str)
    ):
        raise DepthProductContractError(
            "uncoupled-baseline source manifest has no realized event window"
        )
    admitted_depth = resolve_repo_file(
        repo_root, admission.get("depth_raster_path"), "admission.depth_raster_path"
    )
    if depth_raster_path is not None and admitted_depth != depth_raster_path.resolve():
        raise DepthProductContractError(
            "uncoupled-baseline admission depth path differs from product input"
        )
    required = {
        "decision": "ADMITTED",
        "product_label": UNCOUPLED_BASELINE_LABEL,
        "coupling_enabled": False,
        "source_manifest_sha256": sha256_file(source_manifest_path),
        "source_git_sha": source.get("git_sha"),
        "source_stage": source.get("stage"),
        "source_status": source.get("status"),
        "depth_raster_sha256": sha256_file(admitted_depth),
        "forcing_kind": "historical_replay",
        "temporal_aggregation": "event_maximum",
        "event_window_start_utc": source_event.get("start"),
        "event_window_end_utc": source_event.get("end"),
    }
    mismatches = {
        key: {"expected": expected, "realized": admission.get(key)}
        for key, expected in required.items()
        if admission.get(key) != expected
    }
    if mismatches:
        raise DepthProductContractError(
            f"uncoupled-baseline admission does not bind the realized source manifest: {mismatches}"
        )
    if _declared_coupling(source) is not None:
        raise DepthProductContractError(
            "uncoupled-baseline admission is only valid for a legacy source with no coupling "
            "declaration"
        )
    return {
        "path": relative.as_posix(),
        "sha256": sha256_file(admission_path),
        "product_label": UNCOUPLED_BASELINE_LABEL,
        "coupling_enabled": False,
        "source_manifest_sha256": required["source_manifest_sha256"],
        "depth_raster_path": admitted_depth.relative_to(repo_root).as_posix(),
        "depth_raster_sha256": required["depth_raster_sha256"],
        "forcing_kind": required["forcing_kind"],
        "temporal_aggregation": required["temporal_aggregation"],
        "event_window_start_utc": required["event_window_start_utc"],
        "event_window_end_utc": required["event_window_end_utc"],
    }


def validate_frame_series_admission(
    admission_path: Path,
    source_manifest_path: Path,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    """Bind a retrospective frame-series admission to the realized raster bytes.

    Every declared frame must exist with the declared offset and SHA-256, the
    directory must contain no undeclared series files, the cadence must be
    uniform, and the upstream solver manifest hash must match realized bytes.
    """

    admission_path = admission_path.resolve()
    repo_root = repo_root.resolve()
    try:
        relative = admission_path.relative_to(repo_root)
    except ValueError as exc:
        raise DepthProductContractError(
            "frame-series admission must live inside the repository"
        ) from exc
    if relative.parts[:2] != ("data", "curation"):
        raise DepthProductContractError("frame-series admission must live under data/curation")
    admission = _load_json(admission_path, "frame-series admission")
    # The upstream manifest is hashed (not parsed): the frame sidecar binds its bytes.
    required = {
        "decision": "ADMITTED",
        "product_label": UNCOUPLED_BASELINE_LABEL,
        "coupling_enabled": False,
        "forcing_kind": "historical_replay",
        "temporal_aggregation": "frame_series",
        "series_kind": "instantaneous_solver_frames",
        "source_manifest_sha256": sha256_file(source_manifest_path),
    }
    mismatches = {
        key: {"expected": expected, "realized": admission.get(key)}
        for key, expected in required.items()
        if admission.get(key) != expected
    }
    if mismatches:
        raise DepthProductContractError(
            f"frame-series admission does not bind the realized source: {mismatches}"
        )
    if not isinstance(admission.get("valid_time_rule"), str) or not admission["valid_time_rule"]:
        raise DepthProductContractError("frame-series admission lacks its valid-time rule")
    frames = admission.get("frames")
    if not isinstance(frames, list) or not frames:
        raise DepthProductContractError("frame-series admission declares no frames")
    if admission.get("frame_count") != len(frames):
        raise DepthProductContractError("frame-series admission count disagrees with its frames")
    series_dir = resolve_repo_file_dir(repo_root, admission.get("depth_raster_dir"))
    declared_names: set[str] = set()
    offsets: list[int] = []
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise DepthProductContractError(f"frame entry {index} is not an object")
        name = frame.get("file")
        if not isinstance(name, str) or "/" not in name:
            raise DepthProductContractError(f"frame entry {index} has no series-relative file")
        declared_names.add(name.rsplit("/", 1)[1])
        frame_path = series_dir / name.rsplit("/", 1)[1]
        if not frame_path.is_file():
            raise DepthProductContractError(f"declared frame {name} is absent on disk")
        realized_hash = sha256_file(frame_path)
        if frame.get("sha256") != realized_hash:
            raise DepthProductContractError(
                f"frame {name} sha256 differs from realized bytes: "
                f"declared {frame.get('sha256')}, realized {realized_hash}"
            )
        offset = frame.get("offset_seconds")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise DepthProductContractError(f"frame {name} has an invalid offset_seconds")
        offsets.append(offset)
        if not isinstance(frame.get("valid_time_utc"), str):
            raise DepthProductContractError(f"frame {name} lacks valid_time_utc")
    realized_names = {path.name for path in series_dir.iterdir() if path.suffix.lower() == ".tif"}
    extra = sorted(realized_names - declared_names)
    if extra:
        raise DepthProductContractError(f"realized series contains undeclared rasters: {extra[:5]}")
    cadence = admission.get("cadence_seconds")
    if (
        isinstance(cadence, bool)
        or not isinstance(cadence, int)
        or cadence <= 0
        or offsets != list(range(offsets[0], offsets[0] + len(offsets) * cadence, cadence))
    ):
        raise DepthProductContractError("declared frame cadence is not uniform")
    return {
        "path": relative.as_posix(),
        "sha256": sha256_file(admission_path),
        "product_label": required["product_label"],
        "forcing_kind": required["forcing_kind"],
        "temporal_aggregation": required["temporal_aggregation"],
        "frame_count": len(frames),
        "cadence_seconds": cadence,
        "first_offset_seconds": offsets[0],
        "last_offset_seconds": offsets[-1],
        "source_manifest_sha256": required["source_manifest_sha256"],
    }


def _find_key(payload: Any, key: str) -> Any | None:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for child in payload.values():
            found = _find_key(child, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for child in payload:
            found = _find_key(child, key)
            if found is not None:
                return found
    return None


def _assert_coupled_depth_output_binding(
    upstream: dict[str, Any], depth_path: Path, manifest: dict[str, Any]
) -> None:
    """Verify the coupled input is the upstream post-couple output, not a relabelled raster."""

    output = upstream.get("depth_raster_output")
    if not isinstance(output, dict):
        raise DepthProductContractError(
            "coupled upstream manifest does not bind its depth raster output"
        )
    if output.get("depth_field_state") != "post_couple_step_h_new":
        raise DepthProductContractError(
            "coupled upstream depth raster output is not post_couple_step_h_new"
        )
    upstream_path = output.get("path")
    product_binding = manifest.get("depth_raster_output_binding")
    if not isinstance(product_binding, dict):
        raise DepthProductContractError("product has no coupled depth raster output binding")
    if product_binding != output:
        raise DepthProductContractError(
            "product coupled depth raster output binding differs from upstream manifest"
        )
    if not isinstance(upstream_path, str) or not upstream_path.strip():
        raise DepthProductContractError("coupled upstream depth raster output path is absent")
    if Path(upstream_path).is_absolute() or ".." in Path(upstream_path).parts:
        raise DepthProductContractError(
            "coupled upstream depth raster output path must be repository-relative"
        )
    if upstream_path != manifest["input_paths"]["depth_raster"]:
        raise DepthProductContractError(
            "coupled upstream depth raster output path differs from product input"
        )
    realized_hash = sha256_file(depth_path)
    if output.get("sha256") != realized_hash:
        raise DepthProductContractError(
            "coupled upstream depth raster output sha256 differs from realized input bytes"
        )


def validate_product_manifest(
    manifest: dict[str, Any],
    manifest_path: Path,
    *,
    rows: Sequence[dict[str, Any]],
    lookup_ids: set[int],
    repo_root: Path,
    contract_path: Path,
) -> dict[str, Any]:
    """Assert producer guarantees against realized rows, files, and upstream state."""

    requirements = load_requirements(contract_path)
    if manifest.get("status") != "completed":
        raise DepthProductContractError(
            f"product manifest must have status='completed', got {manifest.get('status')!r}"
        )
    git_sha = manifest.get("git_sha")
    if not isinstance(git_sha, str) or not git_sha.strip():
        raise DepthProductContractError("product manifest.git_sha must be non-empty")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise DepthProductContractError("product manifest.run_id must be non-empty")
    expected_manifest_path = (repo_root / "runs" / run_id / "manifest.json").resolve()
    if manifest_path.resolve() != expected_manifest_path:
        raise DepthProductContractError(
            "product manifest must be exactly runs/<run_id>/manifest.json; "
            f"run_id={run_id!r}, path={manifest_path}"
        )
    if manifest.get("git_tree_clean") is not True:
        # WF-6 U0a: a dirty tree may be admitted only when the manifest RECORDS it
        # (realized porcelain paths + reason) instead of laundering it as clean.
        # Default behaviour without an explicit admission is unchanged and strict.
        admission = manifest.get("source_tree_dirty_admission")
        admitted = (
            isinstance(admission, dict)
            and isinstance(admission.get("reason"), str)
            and bool(admission["reason"].strip())
            and isinstance(admission.get("porcelain_paths"), list)
            and bool(admission["porcelain_paths"])
            and all(
                isinstance(path, str) and bool(path.strip())
                for path in admission["porcelain_paths"]
            )
        )
        if not admitted:
            raise DepthProductContractError(
                "product manifest.git_tree_clean must be true for reproducible completion"
            )
    row_run_ids = {str(row.get("run_id", "")) for row in rows}
    if row_run_ids != {run_id}:
        raise DepthProductContractError("product row run_id values do not match the manifest")
    expected = _required_int(manifest, "n_segments_expected")
    no_data_expected = _required_int(manifest, "n_no_data_segments")
    if len(rows) != expected or expected != len(lookup_ids):
        raise DepthProductContractError(
            "product/manifest/lookup segment counts disagree: "
            f"rows={len(rows)} manifest={expected} lookup={len(lookup_ids)}"
        )
    row_ids = {int(row["segment_id"]) for row in rows}
    if row_ids != lookup_ids:
        raise DepthProductContractError(
            "product segment IDs differ from the realized lookup: "
            f"missing={len(lookup_ids - row_ids)} extra={len(row_ids - lookup_ids)}"
        )
    realized_no_data = sum(row.get("confidence") == "no_data" for row in rows)
    if realized_no_data != no_data_expected:
        raise DepthProductContractError(
            f"manifest no-data count {no_data_expected} differs from rows {realized_no_data}"
        )
    # WF-6 U0a: storage-water exclusion moves raster-present-but-unmodelable
    # segments into no_data.  The frozen total still binds, extended by a
    # manifest-declared, measured delta that must reconcile exactly.
    exclusion = manifest.get("storage_water_exclusion")
    if exclusion is None:
        if no_data_expected != requirements.no_data_total:
            raise DepthProductContractError(
                f"manifest no-data count {no_data_expected} differs from frozen measured total "
                f"{requirements.no_data_total}"
            )
    else:
        if not isinstance(exclusion, dict) or not exclusion.get("rule"):
            raise DepthProductContractError(
                "manifest storage_water_exclusion must declare its exclusion rule"
            )
        excluded_only = exclusion.get("all_canonical_cells_excluded")
        if (
            isinstance(excluded_only, bool)
            or not isinstance(excluded_only, int)
            or excluded_only < 0
        ):
            raise DepthProductContractError(
                "manifest storage_water_exclusion.all_canonical_cells_excluded must be a "
                "non-negative measured integer"
            )
        if no_data_expected != requirements.no_data_total + excluded_only:
            raise DepthProductContractError(
                f"manifest no-data count {no_data_expected} does not reconcile with the "
                f"frozen total {requirements.no_data_total} plus the declared all-cells-"
                f"excluded delta {excluded_only}"
            )
    if manifest.get("depth_convention") != requirements.depth_convention:
        raise DepthProductContractError(
            "manifest.depth_convention does not match the frozen contract"
        )
    if manifest.get("band_convention") != requirements.band_convention:
        raise DepthProductContractError(
            "manifest.band_convention does not match the frozen contract"
        )

    split = manifest.get("no_data_segments_measured_split")
    expected_split = {
        "buffered_margin_only": requirements.no_data_buffered_margin_only,
        "fully_clipped_no_cells_anywhere": requirements.no_data_fully_clipped,
    }
    if not isinstance(split, dict) or any(
        split.get(key) != value for key, value in expected_split.items()
    ):
        raise DepthProductContractError(
            "manifest no-data split does not match the frozen realized split"
        )

    grid = manifest.get("grid_identity")
    expected_crs = f"EPSG:{requirements.grid_epsg}"
    if not isinstance(grid, dict):
        raise DepthProductContractError("manifest.grid_identity must be an object")
    if grid.get("shape") != list(requirements.grid_shape):
        raise DepthProductContractError("manifest grid shape does not match the frozen grid")
    if grid.get("crs") != expected_crs:
        raise DepthProductContractError("manifest grid CRS does not match the frozen grid")
    if float(grid.get("resolution_m", -1.0)) != requirements.grid_resolution_m:
        raise DepthProductContractError("manifest grid resolution does not match the frozen grid")

    coupling_enabled = manifest.get("coupling_enabled")
    if not isinstance(coupling_enabled, bool):
        raise DepthProductContractError("manifest.coupling_enabled must be boolean")
    depth_state = manifest.get("depth_field_state")
    if coupling_enabled and depth_state != "post_couple_step_h_new":
        raise DepthProductContractError(
            "coupled product does not declare the required post_couple_step_h_new depth field"
        )
    if not coupling_enabled and depth_state != "uncoupled_source_depth":
        raise DepthProductContractError("uncoupled product depth-field state is not explicit")

    depth_source_path = resolve_repo_file(
        repo_root, manifest.get("depth_source_manifest_path"), "depth_source_manifest_path"
    )
    upstream = _load_json(depth_source_path, "upstream depth manifest")
    if upstream.get("status") != "completed":
        raise DepthProductContractError("upstream depth manifest is not completed")
    if not isinstance(upstream.get("git_sha"), str) or not upstream["git_sha"].strip():
        raise DepthProductContractError("upstream depth manifest has no Git provenance")
    upstream_coupling = _declared_coupling(upstream)
    admission_view: dict[str, Any] | None = None
    if upstream_coupling is None:
        if coupling_enabled:
            raise DepthProductContractError(
                "upstream depth manifest has no coupling declaration and cannot support a "
                "coupled product"
            )
        admission_path = resolve_repo_file(
            repo_root,
            manifest.get("uncoupled_baseline_admission_path"),
            "uncoupled_baseline_admission_path",
        )
        admission_view = validate_uncoupled_baseline_admission(
            admission_path,
            depth_source_path,
            repo_root=repo_root,
            depth_raster_path=resolve_repo_file(
                repo_root,
                manifest.get("input_paths", {}).get("depth_raster"),
                "input_paths.depth_raster",
            ),
        )
        if manifest.get("uncoupled_baseline_admission_sha256") != admission_view["sha256"]:
            raise DepthProductContractError(
                "manifest uncoupled-baseline admission hash does not match realized bytes"
            )
        if manifest.get("product_label") != UNCOUPLED_BASELINE_LABEL:
            raise DepthProductContractError(
                f"legacy uncoupled source must be labelled exactly {UNCOUPLED_BASELINE_LABEL!r}"
            )
    elif upstream_coupling != coupling_enabled:
        raise DepthProductContractError(
            "upstream depth manifest coupling state disagrees with the product"
        )
    if coupling_enabled:
        coupling_version = _find_key(upstream, "coupling_version")
        required_values = {
            "legacy_drain_disabled": True,
            "graph_is_dag": True,
        }
        if not isinstance(coupling_version, str) or not coupling_version.strip():
            raise DepthProductContractError(
                "coupled upstream manifest has no non-empty coupling_version"
            )
        for key, expected_value in required_values.items():
            if _find_key(upstream, key) is not expected_value:
                raise DepthProductContractError(
                    f"coupled upstream manifest does not realize {key}={expected_value!r}"
                )
        for key in ("total_surcharging_steps", "total_returned_m3"):
            value = _find_key(upstream, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise DepthProductContractError(
                    f"coupled upstream manifest lacks non-negative realized {key}"
                )

    expected_surcharge = depth_source_path.parent / "products" / "surcharge_events.csv"
    surcharge_value = manifest.get("surcharge_events_path")
    if coupling_enabled:
        surcharge_path = resolve_repo_file(repo_root, surcharge_value, "surcharge_events_path")
        if surcharge_path != expected_surcharge.resolve():
            raise DepthProductContractError(
                "surcharge_events_path is not the upstream run's products/surcharge_events.csv"
            )
    elif surcharge_value is not None or expected_surcharge.exists():
        raise DepthProductContractError(
            "uncoupled product must not cite or coexist with upstream surcharge_events.csv"
        )

    input_paths = manifest.get("input_paths")
    input_hashes = manifest.get("input_sha256")
    required_inputs = {
        "depth_raster",
        "road_raster",
        "lookup_csv",
        "buffered_road_raster",
        "depth_source_manifest",
    }
    if not isinstance(input_paths, dict) or not isinstance(input_hashes, dict):
        raise DepthProductContractError("manifest input_paths/input_sha256 blocks are required")
    if not required_inputs.issubset(input_paths) or not required_inputs.issubset(input_hashes):
        raise DepthProductContractError("manifest input provenance is incomplete")
    for key in sorted(required_inputs):
        path = resolve_repo_file(repo_root, input_paths[key], f"input_paths.{key}")
        if input_hashes[key] != sha256_file(path):
            raise DepthProductContractError(f"manifest input_sha256.{key} does not match bytes")
    depth_input_path = resolve_repo_file(
        repo_root, input_paths["depth_raster"], "input_paths.depth_raster"
    )
    if (
        resolve_repo_file(
            repo_root, input_paths["depth_source_manifest"], "input_paths.depth_source_manifest"
        )
        != depth_source_path
    ):
        raise DepthProductContractError("depth_source_manifest provenance paths disagree")
    if coupling_enabled:
        _assert_coupled_depth_output_binding(upstream, depth_input_path, manifest)

    outputs = manifest.get("outputs")
    output_hashes = manifest.get("output_sha256")
    if not isinstance(outputs, dict) or not isinstance(output_hashes, dict):
        raise DepthProductContractError("manifest outputs/output_sha256 blocks are required")
    expected_outputs = {
        "csv": manifest_path.parent / "products" / "segment_status.csv",
        "json": manifest_path.parent / "products" / "segment_status.json",
    }
    for key, expected_path in expected_outputs.items():
        output_path = resolve_repo_file(repo_root, outputs.get(key), f"outputs.{key}")
        if output_path != expected_path.resolve():
            raise DepthProductContractError(
                f"manifest outputs.{key} is not the frozen runs/<run_id>/products location"
            )
        if output_hashes.get(key) != sha256_file(output_path):
            raise DepthProductContractError(
                f"manifest output_sha256.{key} does not match completed product bytes"
            )

    result = {
        "status": "completed",
        "git_sha": git_sha,
        "n_segments_expected": expected,
        "n_no_data_segments": no_data_expected,
        "depth_convention": requirements.depth_convention,
        "band_convention": requirements.band_convention,
        "coupling_enabled": coupling_enabled,
        "grid_identity": {
            **grid,
            "source_manifest_path": manifest_path.relative_to(repo_root).as_posix(),
        },
        "input_sha256": input_hashes,
        "output_sha256": output_hashes,
        "source_manifest_path": manifest_path.relative_to(repo_root).as_posix(),
    }
    if manifest.get("product_label") is not None:
        result["product_label"] = manifest["product_label"]
    if admission_view is not None:
        result["uncoupled_baseline_admission"] = admission_view
        for key in (
            "forcing_kind",
            "temporal_aggregation",
            "event_window_start_utc",
            "event_window_end_utc",
        ):
            if manifest.get(key) != admission_view[key]:
                raise DepthProductContractError(
                    f"product manifest.{key} differs from uncoupled-baseline admission"
                )
            result[key] = admission_view[key]
    return result
