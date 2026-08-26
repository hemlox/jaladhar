"""Shared CPU-only lifecycle and contract checks for the demo packaging track.

This module deliberately contains no solver or product-generation logic.  It only
loads a realized run and starts already-built dashboard/API entry points.  A run
without the frozen depth product is rejected instead of being adapted into a
presentation artifact.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = REPO_ROOT / "runs"
DEPTH_PRODUCT_CONTRACT = REPO_ROOT / "configs/contracts/depth_product.json"
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from jaladhar.validation.depth_product_contract import (  # noqa: E402
    MAX_FORECAST_LEAD_MINUTES,
    DepthProductContractError,
    load_requirements,
    sha256_file,
    validate_product_manifest,
)

DEFAULT_DASHBOARD_COMMAND = (
    # The products DIRECTORY, not a single twin: the dashboard discovers every
    # segment_status* file pair in it and exposes each realized forecast lead
    # as its own timeline frame (single-frame runs expose exactly one).  For a
    # frame-series run the directory resolves through the run manifest instead.
    "{python} -m jaladhar.web.app serve --product {run_dir}/products "
    "--geometry {repo_root}/data/interim/terrain/roads_centrelines.gpkg "
    "--host {host} --port {port}"
)
DEFAULT_API_COMMAND = (
    "{python} -m jaladhar.routing.api serve --depth-product-file {depth_product_file} "
    "--host {host} --port {port}"
)

_REQUIRED_FIELDS = {
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
}
_LEGAL_STATUS_PAIRS = {
    ("modeled_direct", "flooded"),
    ("modeled_direct", "not_flooded"),
    ("modeled_interpolated", "flooded"),
    ("modeled_interpolated", "not_flooded"),
    ("no_data", "unknown"),
}
_PROVENANCE_KEYS = (
    "source_manifest_path",
    "manifest_path",
    "run_manifest_path",
    "source_manifest",
    "depth_source_manifest",
)
_CPU_FORBIDDEN_TERMS = re.compile(r"(?i)(?<![a-z0-9])(cuda|gpu|nvidia|mps)(?![a-z0-9])")


class DemoError(RuntimeError):
    """A fail-closed demo preflight, lifecycle, or endpoint error."""


class CPUOnlyViolation(DemoError):
    """A requested child command explicitly names a device-backed backend."""


@dataclass(frozen=True)
class RunEvidence:
    """Realized files that make a run eligible for presentation packaging.

    ``kind`` distinguishes the two discovery shapes: ``event_maximum`` (one
    flat products/segment_status twin pair) and ``frame_series`` (a manifest
    declaring frames[], whose first frame's twins anchor product references).
    """

    repo_root: Path
    run_dir: Path
    manifest_path: Path
    manifest_relative_path: str
    manifest: dict[str, Any]
    product_json: Path
    product_csv: Path
    rows: tuple[dict[str, Any], ...]
    expected_count: int
    source_manifest_paths: tuple[Path, ...]
    source_manifest_relative_paths: tuple[str, ...]
    gate_report: Path | None
    kind: str = "event_maximum"


def depth_product_file_for(evidence: RunEvidence) -> Path:
    """Resolve the {depth_product_file} template placeholder per evidence kind.

    Flat runs keep ``products/segment_status.csv``; frame-series runs anchor on
    frame 0's realized CSV twin so every reference stays inside ONE run.  The
    routing loader enforces the frozen flat-manifest guarantees
    (``n_segments_expected`` et al.) which a series manifest does not carry, so
    for a frame-series run it refuses the file and /health says exactly why --
    that refusal is honest owner-gate-style state, surfaced by the launcher,
    not something this package works around.
    """

    if evidence.kind == "frame_series":
        return evidence.product_csv
    return evidence.run_dir / "products" / "segment_status.csv"


def _find_product_bound_gate_report(product_csv: Path, *, repo_root: Path) -> Path | None:
    """Select the newest completed report that binds these exact product bytes.

    Only the corrected schema with signed G3 explicitly BLOCKED is eligible;
    historical reports that mislabeled point diagnostics as the gate remain audit
    evidence but are not presentation inputs.
    """

    expected_hash = sha256_file(product_csv)
    candidates: list[tuple[str, Path]] = []
    for report_path in sorted((repo_root / "runs").glob("*/g1_score.json")):
        manifest_path = report_path.parent / "manifest.json"
        try:
            report = _load_json(report_path)
            manifest = _load_json(manifest_path)
        except DemoError:
            continue
        if (
            isinstance(report, dict)
            and isinstance(manifest, dict)
            and manifest.get("status") == "completed"
            and report.get("input_sha256", {}).get("depth_product") == expected_hash
            and report.get("g3", {}).get("status") == "BLOCKED"
        ):
            candidates.append((str(manifest.get("end_time_iso", "")), report_path))
    return max(candidates, default=("", None))[1]


@dataclass
class RunningService:
    """A child process launched by this package, including its diagnostic log."""

    name: str
    command: list[str]
    process: subprocess.Popen[Any]
    log_path: Path
    log_handle: TextIO


@dataclass(frozen=True)
class HttpResult:
    """The realized response from one rehearsal probe."""

    status: int
    headers: dict[str, str]
    body: bytes


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DemoError(f"cannot read JSON artifact {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DemoError(f"invalid JSON artifact {path}: {exc}") from exc


def _extract_rows(payload: Any, path: Path) -> tuple[list[dict[str, Any]], str]:
    if isinstance(payload, list):
        rows = payload
        shape = "list"
    elif isinstance(payload, dict):
        rows = None
        shape = "object"
        if isinstance(payload.get("features"), list):
            rows = []
            for feature in payload["features"]:
                if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
                    raise DemoError(f"{path} has a feature without a properties object")
                rows.append(dict(feature["properties"]))
            shape = "object.features"
        for key in ("segments", "rows", "data"):
            candidate = payload.get(key)
            if rows is None and isinstance(candidate, list):
                rows = candidate
                shape = f"object.{key}"
                break
        if rows is None:
            raise DemoError(
                f"{path} has no segment row list; expected a JSON list or an object with "
                "segments/rows/data"
            )
    else:
        raise DemoError(f"{path} must contain a JSON list/object, found {type(payload).__name__}")

    if not rows or not all(isinstance(row, dict) for row in rows):
        raise DemoError(f"{path} does not contain a non-empty list of JSON objects")
    return rows, shape


def _aware_timestamp(value: Any, field: str, row_number: int) -> datetime:
    if not isinstance(value, str) or not value:
        raise DemoError(f"product row {row_number} field {field} is not an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DemoError(
            f"product row {row_number} field {field} is not a timestamp: {value}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DemoError(f"product row {row_number} field {field} is timezone-naive")
    if parsed.utcoffset().total_seconds() != 0:
        raise DemoError(f"product row {row_number} field {field} is not UTC")
    return parsed


def _source_manifest_path(repo_root: Path, value: Any, row_number: int) -> Path:
    if not isinstance(value, str) or not value:
        raise DemoError(f"product row {row_number} source_manifest_path is missing")
    source = Path(value)
    if source.is_absolute() or ".." in source.parts:
        raise DemoError(
            f"product row {row_number} source_manifest_path must be repo-relative: {value}"
        )
    resolved = (repo_root / source).resolve()
    if not resolved.is_file():
        raise DemoError(f"product row {row_number} source manifest does not exist: {value}")
    source_manifest = _load_json(resolved)
    if not isinstance(source_manifest, dict) or source_manifest.get("status") != "completed":
        raise DemoError(
            f"product row {row_number} source manifest is not a completed JSON manifest: {value}"
        )
    return resolved


def _validate_product_rows(
    rows: list[dict[str, Any]], repo_root: Path, run_dir: Path
) -> tuple[tuple[dict[str, Any], ...], set[int], set[Path]]:
    requirements = load_requirements(DEPTH_PRODUCT_CONTRACT)
    ids: set[int] = set()
    source_manifests: set[Path] = set()
    valid_times: set[datetime] = set()
    issue_times: set[datetime] = set()
    leads: set[int] = set()
    for row_number, row in enumerate(rows, start=1):
        missing = _REQUIRED_FIELDS - set(row)
        if missing:
            raise DemoError(
                f"product row {row_number} is missing frozen fields: {', '.join(sorted(missing))}"
            )
        if not isinstance(row["run_id"], str) or not row["run_id"].strip():
            raise DemoError(f"product row {row_number} run_id is not a non-empty string")
        if row["run_id"] != run_dir.name:
            raise DemoError(
                f"product row {row_number} run_id={row['run_id']!r} does not match "
                f"selected directory {run_dir.name!r}"
            )
        segment_id = row["segment_id"]
        if type(segment_id) is not int or segment_id < 0:  # bool is not an integer key here.
            raise DemoError(f"product row {row_number} segment_id is not a non-negative integer")
        if segment_id in ids:
            raise DemoError(f"product contains duplicate segment_id={segment_id}")
        ids.add(segment_id)

        for field in ("band_low_cm", "band_high_cm"):
            value = row[field]
            if type(value) is not int or value < 0:
                raise DemoError(
                    f"product row {row_number} {field} is not a non-negative integer cm"
                )
        if row["band_low_cm"] > row["band_high_cm"]:
            raise DemoError(f"product row {row_number} has band_low_cm above band_high_cm")

        pair = (row["confidence"], row["flood_status"])
        if pair not in _LEGAL_STATUS_PAIRS:
            raise DemoError(f"product row {row_number} has illegal confidence/status pair {pair!r}")
        if row["confidence"] == "no_data" and (row["band_low_cm"], row["band_high_cm"]) != (
            0,
            0,
        ):
            raise DemoError(f"product row {row_number} no_data band is not [0, 0]")
        if row["confidence"] == "modeled_interpolated":
            raise DemoError(
                f"product row {row_number} uses reserved modeled_interpolated confidence"
            )
        if (
            row["flood_status"] == "flooded"
            and row["band_high_cm"] < requirements.flood_threshold_cm
        ):
            raise DemoError(
                f"product row {row_number} flooded band_high_cm is below the frozen rule"
            )
        lead = row["forecast_lead_minutes"]
        if type(lead) is not int or not 0 <= lead <= MAX_FORECAST_LEAD_MINUTES:
            raise DemoError(f"product row {row_number} forecast_lead_minutes is invalid")
        valid_time = _aware_timestamp(row["valid_time_utc"], "valid_time_utc", row_number)
        issue_time = _aware_timestamp(row["issue_time_utc"], "issue_time_utc", row_number)
        if lead == 0 and issue_time < valid_time:
            raise DemoError(f"product row {row_number} nowcast issue time precedes valid time")
        if lead > 0 and issue_time > valid_time:
            raise DemoError(f"product row {row_number} forecast issue time follows valid time")
        valid_times.add(valid_time)
        issue_times.add(issue_time)
        leads.add(lead)
        source_manifests.add(
            _source_manifest_path(repo_root, row["source_manifest_path"], row_number)
        )

    if len(source_manifests) != 1:
        raise DemoError("product metadata changes source_manifest_path within one product file")
    source_manifest = next(iter(source_manifests))
    expected_source_manifest = (run_dir / "manifest.json").resolve()
    if source_manifest != expected_source_manifest:
        raise DemoError(
            "product source_manifest_path must be the selected run's manifest.json: "
            f"{source_manifest.relative_to(repo_root).as_posix()}"
        )
    if len(valid_times) != 1 or len(issue_times) != 1 or len(leads) != 1:
        raise DemoError("product metadata changes within one product file")
    return tuple(rows), ids, source_manifests


def _validate_csv(path: Path, expected_rows: tuple[dict[str, Any], ...]) -> None:
    expected_by_id = {
        int(row["segment_id"]): {field: str(row[field]) for field in _REQUIRED_FIELDS}
        for row in expected_rows
    }
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or set(reader.fieldnames) != _REQUIRED_FIELDS:
                raise DemoError(
                    f"{path} CSV fields differ from the frozen product fields: "
                    f"{sorted(reader.fieldnames or [])}"
                )
            csv_by_id: dict[int, dict[str, str]] = {}
            for row_number, row in enumerate(reader, start=2):
                try:
                    segment_id = int(row["segment_id"])
                except (TypeError, ValueError) as exc:
                    raise DemoError(f"{path}:{row_number} has a non-integer segment_id") from exc
                if segment_id in csv_by_id:
                    raise DemoError(f"{path}:{row_number} repeats segment_id={segment_id}")
                csv_by_id[segment_id] = {field: str(row[field]) for field in _REQUIRED_FIELDS}
    except OSError as exc:
        raise DemoError(f"cannot read product CSV {path}: {exc}") from exc
    if csv_by_id != expected_by_id:
        differing = sorted(
            segment_id
            for segment_id in set(csv_by_id) | set(expected_by_id)
            if csv_by_id.get(segment_id) != expected_by_id.get(segment_id)
        )
        raise DemoError(
            "CSV/JSON product twins disagree in full row values; "
            f"first differing segment IDs={differing[:10]}"
        )


def _lookup_ids(repo_root: Path) -> set[int]:
    path = repo_root / "data/interim/terrain/roads_segment_lookup.csv"
    if not path.is_file():
        raise DemoError(f"realized segment lookup is missing: {path}")
    ids: set[int] = set()
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "segment_id" not in reader.fieldnames:
                raise DemoError(f"realized segment lookup has no segment_id column: {path}")
            for row_number, row in enumerate(reader, start=2):
                try:
                    segment_id = int(row["segment_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise DemoError(f"{path}:{row_number} has a non-integer segment_id") from exc
                if segment_id in ids:
                    raise DemoError(f"{path}:{row_number} repeats segment_id={segment_id}")
                ids.add(segment_id)
    except OSError as exc:
        raise DemoError(f"cannot read realized segment lookup {path}: {exc}") from exc
    if not ids:
        raise DemoError(f"realized segment lookup is empty: {path}")
    return ids


def _validate_manifest_guarantees(
    manifest: dict[str, Any], manifest_path: Path, rows: tuple[dict[str, Any], ...], run_dir: Path
) -> None:
    lookup_ids = _lookup_ids(REPO_ROOT)
    try:
        validate_product_manifest(
            manifest,
            manifest_path,
            rows=rows,
            lookup_ids=lookup_ids,
            repo_root=REPO_ROOT,
            contract_path=DEPTH_PRODUCT_CONTRACT,
        )
    except DepthProductContractError as exc:
        raise DemoError(str(exc)) from exc


def validate_run_dir(
    run_dir: Path, *, repo_root: Path = REPO_ROOT, require_product: bool = True
) -> RunEvidence | None:
    """Validate a completed run and, when requested, its frozen depth product."""
    run_dir = run_dir.expanduser().resolve()
    repo_root = repo_root.resolve()
    try:
        run_dir.relative_to(RUNS_ROOT.resolve())
    except ValueError as exc:
        raise DemoError(f"run directory must be below {RUNS_ROOT}, got {run_dir}") from exc
    if not run_dir.is_dir():
        raise DemoError(f"run directory does not exist: {run_dir}")
    if not DEPTH_PRODUCT_CONTRACT.is_file():
        raise DemoError(f"frozen depth-product contract is missing: {DEPTH_PRODUCT_CONTRACT}")

    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise DemoError(f"run manifest is missing: {manifest_path}")
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise DemoError(f"run manifest must be a JSON object: {manifest_path}")
    if manifest.get("status") != "completed":
        raise DemoError(
            f"run manifest status is {manifest.get('status')!r}; demo requires realized "
            "status 'completed'"
        )
    if not isinstance(manifest.get("git_sha"), str) or not manifest["git_sha"]:
        raise DemoError(f"run manifest has no git_sha: {manifest_path}")
    if not require_product:
        return None

    if manifest.get("series_kind") is not None:
        return _frame_series_evidence(run_dir, manifest, manifest_path, repo_root)

    product_dir = run_dir / "products"
    product_json = product_dir / "segment_status.json"
    product_csv = product_dir / "segment_status.csv"
    missing = [str(path) for path in (product_csv, product_json) if not path.is_file()]
    if missing:
        raise DemoError(
            f"no contract-conforming demo product in {run_dir}; missing {', '.join(missing)}. "
            "A raster or validation report is not a substitute, so no fallback was produced."
        )
    payload = _load_json(product_json)
    rows, _shape = _extract_rows(payload, product_json)
    expected = manifest.get("n_segments_expected")
    if type(expected) is not int or expected <= 0:
        raise DemoError(
            "selected manifest does not expose a positive n_segments_expected guarantee: "
            f"{manifest_path}"
        )
    if len(rows) != expected:
        raise DemoError(
            f"product row count {len(rows)} does not equal manifest n_segments_expected {expected}"
        )
    manifest_relative = manifest_path.relative_to(repo_root).as_posix()
    validated_rows, ids, source_manifests = _validate_product_rows(rows, repo_root, run_dir)
    _validate_manifest_guarantees(manifest, manifest_path, validated_rows, run_dir)
    lookup_ids = _lookup_ids(repo_root)
    if ids != lookup_ids:
        raise DemoError(
            "product segment IDs do not exactly match the realized lookup: "
            f"missing={len(lookup_ids - ids)} extra={len(ids - lookup_ids)}"
        )
    source_manifest_relatives = {
        path.relative_to(repo_root).as_posix() for path in source_manifests
    }
    _validate_csv(product_csv, validated_rows)
    gate_report = _find_product_bound_gate_report(product_csv, repo_root=repo_root)
    return RunEvidence(
        repo_root=repo_root,
        run_dir=run_dir,
        manifest_path=manifest_path,
        manifest_relative_path=manifest_relative,
        manifest=manifest,
        product_json=product_json,
        product_csv=product_csv,
        rows=validated_rows,
        expected_count=expected,
        source_manifest_paths=tuple(sorted(source_manifests)),
        source_manifest_relative_paths=tuple(sorted(source_manifest_relatives)),
        gate_report=gate_report,
    )


def _frame_series_evidence(
    run_dir: Path, manifest: dict[str, Any], manifest_path: Path, repo_root: Path
) -> RunEvidence:
    """Build launcher evidence for a declaring frame-series run.

    Validation delegates to the SAME acceptance truth the dashboard uses
    (``series.parse_frame_entries``); per-frame SHA verification stays where it
    already runs, in the dashboard's warm-up.  Frame 0's twins anchor product
    references so downstream consumers always name realized bytes.
    """

    from jaladhar.web.series import SeriesError, parse_frame_entries

    try:
        frames = parse_frame_entries(run_dir, manifest, repo_root=repo_root)
    except SeriesError as exc:
        raise DemoError(str(exc)) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise DemoError(f"frame-series manifest is malformed: {exc}") from exc
    frame_zero = frames[0]
    manifest_relative = manifest_path.relative_to(repo_root).as_posix()
    gate_report = _find_product_bound_gate_report(frame_zero.csv_path, repo_root=repo_root)
    return RunEvidence(
        repo_root=repo_root,
        run_dir=run_dir,
        manifest_path=manifest_path,
        manifest_relative_path=manifest_relative,
        manifest=manifest,
        product_json=frame_zero.json_path,
        product_csv=frame_zero.csv_path,
        rows=(),
        expected_count=len(frames),
        source_manifest_paths=(manifest_path,),
        source_manifest_relative_paths=(manifest_relative,),
        gate_report=gate_report,
        kind="frame_series",
    )


def discover_fallback(*, repo_root: Path = REPO_ROOT) -> RunEvidence:
    """Return the designated fallback; never create or transform one.

    Selection order matches the dashboard exactly (SELECTION_ORDER in
    ``jaladhar.web.app``): the newest VALID frame-series run wins over any flat
    event-maximum run, because it realizes strictly more demonstrated state.
    """

    from jaladhar.web.series import discover_series_runs

    valid_series, _skipped = discover_series_runs(repo_root)
    for candidate in valid_series:  # newest first
        try:
            evidence = validate_run_dir(candidate.run_dir, repo_root=repo_root)
        except DemoError:
            continue
        if evidence is not None:
            return evidence

    if not RUNS_ROOT.is_dir():
        raise DemoError(f"runs directory is missing: {RUNS_ROOT}; fallback unavailable")
    candidates: list[RunEvidence] = []
    for run_dir in sorted(path for path in RUNS_ROOT.iterdir() if path.is_dir()):
        if not (run_dir / "products" / "segment_status.json").is_file():
            continue
        try:
            evidence = validate_run_dir(run_dir, repo_root=repo_root)
        except DemoError:
            continue
        if evidence is not None:
            if (
                evidence.manifest.get("demo_fallback") is True
                and evidence.manifest.get("product_label") == "UNCOUPLED BASELINE"
                and evidence.manifest.get("coupling_enabled") is False
            ):
                candidates.append(evidence)
    if not candidates:
        raise DemoError(
            "fallback unavailable: no designated UNCOUPLED BASELINE under runs/<run_id> "
            "passed the frozen manifest checks"
        )
    # More than one auditable baseline may be retained because failed or
    # superseded runs are evidence, not scratch files.  Prefer the most recent
    # completed producer and use the run name only as a deterministic tie-break.
    return max(
        candidates,
        key=lambda evidence: (
            str(evidence.manifest.get("start_time_iso", "")),
            evidence.run_dir.name,
        ),
    )


def resolve_command(
    template: str,
    *,
    python: str,
    run_dir: Path,
    host: str,
    port: int,
    repo_root: Path,
    depth_product_file: Path | None = None,
) -> list[str]:
    """Expand a command template and return argv without invoking a shell.

    ``{depth_product_file}`` resolves per evidence kind: the flat run's
    ``products/segment_status.csv`` or a frame-series run's frame-0 twin.
    Older templates that hardcode ``{run_dir}/products/segment_status.csv``
    keep working for flat runs unchanged.
    """
    values = {
        "python": shlex.quote(python),
        "repo_root": shlex.quote(str(repo_root)),
        "run_dir": shlex.quote(str(run_dir)),
        "host": shlex.quote(host),
        "port": str(port),
        "depth_product_file": shlex.quote(str(depth_product_file or "")),
    }
    try:
        rendered = template.format(**values)
    except KeyError as exc:
        raise DemoError(f"command template uses unsupported placeholder {exc.args[0]!r}") from exc
    command = shlex.split(rendered)
    if not command:
        raise DemoError("service command is empty")
    ensure_cpu_command(command)
    return command


def ensure_cpu_command(command: list[str]) -> None:
    rendered = shlex.join(command)
    match = _CPU_FORBIDDEN_TERMS.search(rendered)
    if match:
        raise CPUOnlyViolation(
            "CPU-only rehearsal refuses service command containing device backend "
            f"{match.group(1)!r}: "
            f"{rendered}"
        )


def cpu_environment(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Return a child environment that makes the CPU-only boundary explicit."""
    environment = os.environ.copy()
    environment.update(
        {
            "CUDA_VISIBLE_DEVICES": "",
            "NVIDIA_VISIBLE_DEVICES": "",
            "JALADHAR_CPU_ONLY": "1",
            "JALADHAR_DEVICE": "cpu",
            "TORCH_DEVICE": "cpu",
        }
    )
    source_path = str(repo_root / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = source_path if not existing else source_path + os.pathsep + existing
    return environment


def start_service(name: str, command: list[str], *, repo_root: Path = REPO_ROOT) -> RunningService:
    ensure_cpu_command(command)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=f"jaladhar-demo-{name}-", suffix=".log", delete=False
    )
    log_path = Path(handle.name)
    try:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            env=cpu_environment(repo_root),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError:
        handle.close()
        raise
    return RunningService(name, command, process, log_path, handle)


def stop_services(services: list[RunningService]) -> None:
    for service in reversed(services):
        if service.process.poll() is None:
            service.process.terminate()
    for service in reversed(services):
        try:
            service.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            service.process.kill()
            service.process.wait(timeout=5)
        finally:
            service.log_handle.close()


def log_tail(service: RunningService, lines: int = 30) -> str:
    try:
        content = service.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"<cannot read {service.log_path}: {exc}>"
    return "\n".join(content[-lines:]) or "<empty log>"


def http_request(
    url: str, *, method: str = "GET", payload: Any | None = None, timeout: float = 3.0
) -> HttpResult:
    body = None
    headers = {"Accept": "application/json, text/html;q=0.9, */*;q=0.1"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResult(response.status, dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as exc:
        return HttpResult(exc.code, dict(exc.headers.items()), exc.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DemoError(f"endpoint did not answer {url}: {exc}") from exc


def wait_for_http(service: RunningService, url: str, *, timeout: float = 20.0) -> HttpResult:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    while time.monotonic() < deadline:
        if service.process.poll() is not None:
            raise DemoError(
                f"{service.name} exited with code {service.process.returncode}; "
                f"log {service.log_path}\n"
                f"{log_tail(service)}"
            )
        try:
            result = http_request(url, timeout=min(2.0, max(0.1, deadline - time.monotonic())))
            if 200 <= result.status < 400:
                return result
            last_error = f"HTTP {result.status}"
        except DemoError as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise DemoError(
        f"{service.name} did not become ready at {url}: {last_error}; log {service.log_path}"
    )


def _manifest_from_node(node: dict[str, Any]) -> str | None:
    for key in _PROVENANCE_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    provenance = node.get("provenance")
    if isinstance(provenance, str) and provenance:
        return provenance
    if isinstance(provenance, dict):
        return _manifest_from_node(provenance)
    return None


def _check_numeric_owner(
    *,
    manifest: str | None,
    path: str,
    repo_root: Path,
    expected_manifests: tuple[Path, ...],
    violations: list[str],
    numeric_count: list[int],
) -> None:
    numeric_count[0] += 1
    if not manifest:
        violations.append(f"{path} has numeric value without its own manifest path")
        return
    source = Path(manifest)
    if source.is_absolute() or ".." in source.parts:
        violations.append(f"{path} cites invalid manifest path {manifest!r}")
        return
    resolved = (repo_root / source).resolve()
    expected_resolved = {item.resolve() for item in expected_manifests}
    if resolved not in expected_resolved or not resolved.is_file():
        violations.append(f"{path} cites {manifest!r}, not an expected manifest")


def _numeric_provenance_walk(
    node: Any,
    *,
    repo_root: Path,
    expected_manifests: tuple[Path, ...],
    path: str,
    violations: list[str],
    numeric_count: list[int],
) -> None:
    if isinstance(node, dict):
        local_manifest = _manifest_from_node(node)
        for key, value in node.items():
            if key == "geometry":
                continue
            if type(value) in (int, float):
                _check_numeric_owner(
                    manifest=local_manifest,
                    path=f"{path}.{key}",
                    repo_root=repo_root,
                    expected_manifests=expected_manifests,
                    violations=violations,
                    numeric_count=numeric_count,
                )
                continue
            if isinstance(value, list) and all(type(item) in (int, float) for item in value):
                for index, _item in enumerate(value):
                    _check_numeric_owner(
                        manifest=local_manifest,
                        path=f"{path}.{key}[{index}]",
                        repo_root=repo_root,
                        expected_manifests=expected_manifests,
                        violations=violations,
                        numeric_count=numeric_count,
                    )
                continue
            _numeric_provenance_walk(
                value,
                repo_root=repo_root,
                expected_manifests=expected_manifests,
                path=f"{path}.{key}",
                violations=violations,
                numeric_count=numeric_count,
            )
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _numeric_provenance_walk(
                value,
                repo_root=repo_root,
                expected_manifests=expected_manifests,
                path=f"{path}[{index}]",
                violations=violations,
                numeric_count=numeric_count,
            )
    elif type(node) in (int, float):
        _check_numeric_owner(
            manifest=None,
            path=path,
            repo_root=repo_root,
            expected_manifests=expected_manifests,
            violations=violations,
            numeric_count=numeric_count,
        )


def verify_json_number_provenance(
    payload: Any, *, repo_root: Path, expected_manifests: tuple[Path, ...]
) -> int:
    """Reject every numeric response leaf that cannot resolve to the selected manifest.

    Scope of "numeric leaf": JSON numbers (``int``/``float``, booleans excluded) and
    lists composed solely of them. Numeric strings and boolean leaves are outside this
    instrument's domain by definition; callers needing those checked must do so
    explicitly. ``geometry``-keyed subtrees are exempt BY DESIGN: GeoJSON coordinates
    and dimensions are producer-artifact geometry whose provenance is the geometry
    artifact itself (path + SHA in the state/segment payloads), not a run manifest.
    """
    violations: list[str] = []
    numeric_count = [0]
    _numeric_provenance_walk(
        payload,
        repo_root=repo_root,
        expected_manifests=expected_manifests,
        path="$",
        violations=violations,
        numeric_count=numeric_count,
    )
    if violations:
        raise DemoError("numeric provenance check failed:\n- " + "\n- ".join(violations[:12]))
    return numeric_count[0]


def _verify_bound_file(
    block: Any, *, label: str, repo_root: Path, allowed: tuple[Path, ...] | None = None
) -> Path:
    if not isinstance(block, dict):
        raise DemoError(f"route provenance {label} must be an object")
    value = block.get("path")
    if not isinstance(value, str) or not value:
        raise DemoError(f"route provenance {label}.path is missing")
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise DemoError(f"route provenance {label}.path must be repository-relative")
    resolved = (repo_root / raw).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise DemoError(f"route provenance {label}.path escapes the repository") from exc
    if not resolved.is_file():
        raise DemoError(f"route provenance {label}.path does not exist: {value}")
    if allowed is not None and resolved not in {path.resolve() for path in allowed}:
        raise DemoError(f"route provenance {label}.path is not the selected artifact")
    if block.get("sha256") != sha256_file(resolved):
        raise DemoError(f"route provenance {label}.sha256 does not match realized bytes")
    return resolved


def _count_numbers(value: Any) -> int:
    if type(value) in (int, float):
        return 1
    if isinstance(value, dict):
        return sum(_count_numbers(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_numbers(item) for item in value)
    return 0


def verify_route_response_provenance(
    payload: Any,
    *,
    repo_root: Path,
    expected_manifest: Path,
    expected_products: tuple[Path, ...],
    expected_rows: tuple[dict[str, Any], ...],
) -> int:
    """Verify the route schema's distinct road, product, policy, and runtime derivations."""

    if not isinstance(payload, dict) or payload.get("status") not in {"ok", "disconnected"}:
        raise DemoError("route response is not an ok/disconnected JSON object")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise DemoError("route response has no structured provenance object")
    road_path = _verify_bound_file(
        provenance.get("road_graph"), label="road_graph", repo_root=repo_root
    )
    lookup_path = _verify_bound_file(
        provenance.get("road_lookup"), label="road_lookup", repo_root=repo_root
    )
    if road_path.relative_to(repo_root.resolve()).parts[:1] != ("data",):
        raise DemoError("route road_graph provenance is not rooted under data/")
    if lookup_path.relative_to(repo_root.resolve()).parts[:1] != ("data",):
        raise DemoError("route road_lookup provenance is not rooted under data/")

    product_block = provenance.get("depth_product")
    product_path = _verify_bound_file(
        product_block,
        label="depth_product",
        repo_root=repo_root,
        allowed=expected_products,
    )
    assert isinstance(product_block, dict)
    manifest_value = product_block.get("manifest_path")
    if not isinstance(manifest_value, str) or Path(manifest_value).is_absolute():
        raise DemoError("route depth_product.manifest_path must be repository-relative")
    manifest_path = (repo_root / manifest_value).resolve()
    if manifest_path != expected_manifest.resolve() or not manifest_path.is_file():
        raise DemoError("route depth_product.manifest_path is not the selected manifest")
    if product_block.get("manifest_sha256") != sha256_file(manifest_path):
        raise DemoError("route depth_product.manifest_sha256 does not match realized bytes")
    if product_path.parent != expected_manifest.parent / "products":
        raise DemoError("route depth product is not owned by the selected run manifest")

    policy_block = provenance.get("vehicle_policy")
    policy_path = _verify_bound_file(policy_block, label="vehicle_policy", repo_root=repo_root)
    assert isinstance(policy_block, dict)
    policy_relative = policy_path.relative_to(repo_root.resolve())
    if policy_relative.parts[:2] != ("data", "curation") and policy_relative.parts[:1] != ("runs",):
        raise DemoError("route vehicle_policy provenance is outside data/curation and runs/")
    evidence_path = _verify_bound_file(
        policy_block.get("source_evidence"),
        label="vehicle_policy.source_evidence",
        repo_root=repo_root,
    )
    if evidence_path.relative_to(repo_root.resolve()).parts[:1] != ("data",):
        raise DemoError("route vehicle-policy source evidence is not rooted under data/")

    rows_by_id = {int(row["segment_id"]): row for row in expected_rows}
    expected_lead = {int(row["forecast_lead_minutes"]) for row in expected_rows}
    lead_times = payload.get("lead_times")
    if not isinstance(lead_times, dict) or expected_lead != {
        int(lead_times.get("forecast_lead_minutes", -1))
    }:
        raise DemoError("route forecast lead does not match the selected depth product")
    snap_limit = payload.get("max_snap_distance_m")
    if isinstance(snap_limit, bool) or not isinstance(snap_limit, (int, float)):
        raise DemoError("route max_snap_distance_m is absent")
    for label in ("origin", "destination"):
        snapped = payload.get(label)
        if not isinstance(snapped, dict) or not isinstance(snapped.get("distance_m"), (int, float)):
            raise DemoError(f"route {label} snap evidence is absent")
        if float(snapped["distance_m"]) > float(snap_limit):
            raise DemoError(f"route {label} exceeds max_snap_distance_m")

    try:
        policy_payload = json.loads(policy_path.read_text(encoding="utf-8"))
        policy_entry = policy_payload["policies"][payload["vehicle_class"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DemoError("route vehicle policy cannot be reconciled to its source file") from exc
    if policy_entry.get("max_impassable_depth_cm") != payload.get("vehicle_limit_cm"):
        raise DemoError("route vehicle limit differs from the cited policy file")

    route = payload.get("route")
    if payload["status"] == "ok":
        if not isinstance(route, dict) or not isinstance(route.get("segments"), list):
            raise DemoError("ok route response has no segment list")
        segments = route["segments"]
        for segment in segments:
            if (
                not isinstance(segment, dict)
                or int(segment.get("segment_id", -1)) not in rows_by_id
            ):
                raise DemoError("route contains a segment absent from the selected product")
            expected = rows_by_id[int(segment["segment_id"])]
            if segment.get("flood_status") != expected["flood_status"] or segment.get(
                "depth_band_cm"
            ) != [expected["band_low_cm"], expected["band_high_cm"]]:
                raise DemoError("route segment flood state differs from the selected product")
        edge_count = route.get("edge_count")
        node_count = route.get("node_count")
        if edge_count != len(segments) or node_count != edge_count + 1:
            raise DemoError("route node/edge counts do not reconcile with segment rows")
        distance = sum(float(segment["length_m"]) for segment in segments)
        if not math.isclose(float(route.get("distance_m", math.nan)), distance, rel_tol=1e-9):
            raise DemoError("route distance_m does not equal the sum of segment lengths")
    elif route is not None:
        raise DemoError("disconnected route response must carry route=null")
    return _count_numbers(payload)


def verify_dashboard_state_provenance(
    payload: Any, *, repo_root: Path, expected_manifests: tuple[Path, ...]
) -> int:
    """Verify state numbers, including the lead index shown by the dashboard."""
    if not isinstance(payload, dict):
        raise DemoError("dashboard state must be a JSON object")
    leads = payload.get("leads", [])
    snapshots = payload.get("snapshots", [])
    if not isinstance(leads, list) or not isinstance(snapshots, list):
        raise DemoError("ready dashboard state must expose leads and snapshots lists")
    snapshot_leads: set[int] = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or type(snapshot.get("lead_minutes")) is not int:
            raise DemoError("dashboard state snapshot has no integer lead_minutes")
        snapshot_leads.add(snapshot["lead_minutes"])
    if any(type(lead) is not int for lead in leads):
        raise DemoError("dashboard state leads contains a non-integer value")
    if len(leads) != len(set(leads)) or set(leads) != snapshot_leads:
        raise DemoError("dashboard state lead index does not match manifest-backed snapshots")

    without_lead_index = dict(payload)
    without_lead_index.pop("leads", None)
    return verify_json_number_provenance(
        without_lead_index,
        repo_root=repo_root,
        expected_manifests=expected_manifests,
    ) + len(leads)


def parse_json_response(result: HttpResult, endpoint: str) -> Any:
    try:
        return json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DemoError(f"{endpoint} did not return JSON: {exc}") from exc


def verify_manifest_reference(
    body: bytes, *, manifest_relative_paths: tuple[str, ...], endpoint: str
) -> None:
    text = body.decode("utf-8", errors="replace")
    if not any(path in text for path in manifest_relative_paths):
        raise DemoError(
            f"{endpoint} does not expose one of the selected manifest paths: "
            f"{', '.join(manifest_relative_paths)}"
        )
