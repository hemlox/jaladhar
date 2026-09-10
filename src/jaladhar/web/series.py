"Frame-series store for the dashboard: consumes a WF-3b frame run. A frame run's manifest declares ``series_kind = \"instantaneous_solver_frames\"`` and carries a ``frames`` array -- one entry per 30-minute solver frame with its tag, valid time, twin product paths, SHA-256 bindings and the producer's own flooded/not-flooded/no-data counts. The single-product store cannot take this series: every frame realises ``forecast_lead_minutes = 0`` (they are replay valid times, not forecast leads), which the frozen contract's distinct-lead rule correctly refuses. This module therefore keys frames by SERIES POSITION and valid time. Honesty posture (rules 1-3, V1): * every frame's twin bytes are SHA-256-verified against the manifest before its rows are trusted; * per-frame row sanity is checked while parsing (dense ids, ordered non-negative bands, frozen status enum, flooded implies band_high >= the contract threshold) and the counted flooded segments must equal the producer's ``n_flooded`` -- a frame that disagrees is marked invalid and excluded, never silently rendered; * frames warm in a background thread (measured ~0.45 s each); the state payload reports warm progress and the timeline enables only when every frame is warm, so play can never show a half-loaded series; * nothing is synthesised. If a frame fails, the series degrades and says so."  # noqa: E501

from __future__ import annotations

import csv
import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from jaladhar.web.basemap import _flood_threshold_cm

STATUS_CODES = {"not_flooded": 0, "flooded": 1, "unknown": 2}
SERIES_KIND = "instantaneous_solver_frames"


class SeriesError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeriesFrame:
    tag: str
    valid_time_utc: str
    offset_seconds: int
    n_flooded_producer: int
    csv_path: Path
    json_path: Path
    csv_sha256: str
    json_sha256: str


@dataclass
class WarmFrame:
    index: int
    band_low: np.ndarray = field(repr=False, default=None)
    band_high: np.ndarray = field(repr=False, default=None)
    status: np.ndarray = field(repr=False, default=None)
    n_flooded_counted: int = -1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FrameSeries:
    "One immutable frame-series run, warmed lazily in the background."

    def __init__(self, run_dir: Path, *, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.run_dir = run_dir.resolve()
        self.lock = threading.Lock()
        self.error: str | None = None
        self.manifest: dict[str, Any] = {}
        self.frames: list[SeriesFrame] = []
        self.warm: dict[int, WarmFrame] = {}
        self.invalid: dict[int, str] = {}
        self._warm_done = threading.Event()
        self.manifest_path = self.run_dir / "manifest.json"
        self.manifest_sha256: str = ""
        self.flood_threshold_cm = _flood_threshold_cm()
        self.started_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            self._accept()
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim
            self.error = f"{type(exc).__name__}: {exc}"
            self._warm_done.set()
            return
        self.warmer = threading.Thread(target=self._warm_all, daemon=True)
        self.warmer.start()

    def _accept(self) -> None:
        if not self.manifest_path.is_file():
            raise SeriesError(f"frame run manifest missing: {self.manifest_path}")
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SeriesError(
                f"unreadable frame run manifest: {self.manifest_path}: {exc}"
            ) from exc
        if not isinstance(raw, dict) or raw.get("series_kind") is None:
            declared = raw.get("series_kind") if isinstance(raw, dict) else None
            raise SeriesError(f"not a frame series (series_kind={declared!r})")
        self.frames = parse_frame_entries(self.run_dir, raw, repo_root=self.repo_root)
        self.manifest = raw
        self.manifest_sha256 = _sha256(self.manifest_path)

    def _warm_all(self) -> None:
        for index, frame in enumerate(self.frames):
            try:
                self._warm_one(index, frame)
            except Exception as exc:  # noqa: BLE001 - frame excluded, surfaced
                with self.lock:
                    self.invalid[index] = f"{type(exc).__name__}: {exc}"
        with self.lock:
            self._warm_done.set()

    def _warm_one(self, index: int, frame: SeriesFrame) -> None:
        if _sha256(frame.csv_path) != frame.csv_sha256:
            raise SeriesError(f"csv sha256 mismatch for {frame.tag}")
        if _sha256(frame.json_path) != frame.json_sha256:
            raise SeriesError(f"json sha256 mismatch for {frame.tag}")
        low = np.empty(176171 * 2, dtype="<u2")
        high = np.empty(176171 * 2, dtype="<u2")
        status = np.empty(176171 * 2, dtype="u1")
        count = 0
        flooded_counted = 0
        prev_id = 0
        with frame.csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                segment_id = int(row["segment_id"])
                if segment_id != prev_id + 1:
                    raise SeriesError(
                        f"{frame.tag}: segment ids not dense/ascending at {segment_id}"
                    )
                prev_id = segment_id
                band_low = int(row["band_low_cm"])
                band_high = int(row["band_high_cm"])
                flood_status = row["flood_status"]
                code = STATUS_CODES.get(flood_status)
                if code is None:
                    raise SeriesError(f"{frame.tag}: unknown flood_status {flood_status!r}")
                if band_low < 0 or band_high < band_low:
                    raise SeriesError(f"{frame.tag}: unordered band at segment {segment_id}")
                if code == 1 and band_high < self.flood_threshold_cm:
                    raise SeriesError(
                        f"{frame.tag}: flooded segment {segment_id} below "
                        f"{self.flood_threshold_cm} cm"
                    )
                if code == 2 and (band_low, band_high) != (0, 0):
                    raise SeriesError(f"{frame.tag}: unknown row {segment_id} with non-zero band")
                if count >= len(low):
                    low = np.concatenate([low, np.empty(len(low), dtype="<u2")])
                    high = np.concatenate([high, np.empty(len(high), dtype="<u2")])
                    status = np.concatenate([status, np.empty(len(status), dtype="u1")])
                low[count] = band_low
                high[count] = band_high
                status[count] = code
                if code == 1:
                    flooded_counted += 1
                count += 1
        if flooded_counted != frame.n_flooded_producer:
            raise SeriesError(
                f"{frame.tag}: counted flooded {flooded_counted} != producer n_flooded "
                f"{frame.n_flooded_producer}"
            )
        warm = WarmFrame(
            index=index,
            band_low=low[:count],
            band_high=high[:count],
            status=status[:count],
            n_flooded_counted=flooded_counted,
        )
        with self.lock:
            self.warm[index] = warm

    @property
    def ready(self) -> bool:
        return self.error is None and bool(self.frames)

    @property
    def all_warm(self) -> bool:
        return self.ready and len(self.warm) == len(self.frames)

    def frame_blob(self, index: int) -> bytes:

        with self.lock:
            warm = self.warm.get(index)
        if warm is None:
            raise SeriesError(f"frame {index} is not warm yet")
        header = np.asarray([index, len(warm.band_low)], dtype="<i4").tobytes()
        return (
            header
            + np.asarray(warm.band_low, dtype="<u2").tobytes()
            + np.asarray(warm.band_high, dtype="<u2").tobytes()
            + np.asarray(warm.status, dtype="u1").tobytes()
        )

    def lookup_segment(self, index: int, segment_id: int) -> dict[str, Any] | None:

        with self.lock:
            warm = self.warm.get(index)
        frame = self.frames[index]
        if warm is not None and 1 <= segment_id <= len(warm.band_low):
            row = {
                "run_id": self.manifest.get("run_id"),
                "segment_id": segment_id,
                "band_low_cm": int(warm.band_low[segment_id - 1]),
                "band_high_cm": int(warm.band_high[segment_id - 1]),
                "flood_status": {0: "not_flooded", 1: "flooded", 2: "unknown"}[
                    int(warm.status[segment_id - 1])
                ],
                "valid_time_utc": frame.valid_time_utc,
                "issue_time_utc": self.manifest.get("issue_time_utc"),
                "forecast_lead_minutes": 0,
                "source_manifest_path": self._repo_relative(self.manifest_path),
            }
            return row
        with frame.csv_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if int(row["segment_id"]) == segment_id:
                    return {
                        "run_id": row["run_id"],
                        "segment_id": segment_id,
                        "band_low_cm": int(row["band_low_cm"]),
                        "band_high_cm": int(row["band_high_cm"]),
                        "flood_status": row["flood_status"],
                        "valid_time_utc": row["valid_time_utc"],
                        "issue_time_utc": row["issue_time_utc"],
                        "forecast_lead_minutes": int(row["forecast_lead_minutes"]),
                        "source_manifest_path": row["source_manifest_path"],
                    }
        return None

    def meta_payload(self) -> dict[str, Any]:
        with self.lock:
            warm_count = len(self.warm)
            invalid = dict(self.invalid)
        frames_meta = [
            {
                "index": i,
                "tag": f.tag,
                "valid_time_utc": f.valid_time_utc,
                "offset_seconds": f.offset_seconds,
                "n_flooded": f.n_flooded_producer,
            }
            for i, f in enumerate(self.frames)
        ]
        flooded = [f["n_flooded"] for f in frames_meta]
        peak_index = flooded.index(max(flooded)) if flooded else None
        min_index = flooded.index(min(flooded)) if flooded else None
        return {
            "mode": "series",
            "available": self.ready,
            "error": self.error,
            "run_id": self.manifest.get("run_id"),
            "product_label": self.manifest.get("product_label"),
            "forcing_kind": self.manifest.get("forcing_kind"),
            "temporal_aggregation": self.manifest.get("temporal_aggregation"),
            "series_kind": self.manifest.get("series_kind"),
            "cadence_seconds": self.manifest.get("cadence_seconds"),
            "coupling_enabled": self.manifest.get("coupling_enabled"),
            "manifest_path": self._repo_relative(self.manifest_path),
            "manifest_sha256": self.manifest_sha256,
            "frame_series_admission_path": self.manifest.get("frame_series_admission_path"),
            "n_frames": len(self.frames),
            "warm_frames": warm_count,
            "all_warm": self.all_warm,
            "invalid_frames": invalid,
            "frames": frames_meta,
            "peak": (
                {
                    "index": peak_index,
                    "valid_time_utc": frames_meta[peak_index]["valid_time_utc"],
                    "n_flooded": flooded[peak_index],
                }
                if peak_index is not None
                else None
            ),
            "minimum": (
                {
                    "index": min_index,
                    "valid_time_utc": frames_meta[min_index]["valid_time_utc"],
                    "n_flooded": flooded[min_index],
                }
                if min_index is not None
                else None
            ),
            "final": (
                {"index": len(frames_meta) - 1, "n_flooded": flooded[-1]} if flooded else None
            ),
            "snapshot_utc": self.started_utc,
        }

    def _repo_relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.repo_root).as_posix()
        except ValueError:
            return str(path.resolve())


def read_series_manifest(manifest_path: Path) -> dict[str, Any] | None:
    "Return the parsed manifest when it declares a frame series, else None. This predicate -- a JSON object whose ``series_kind`` is present -- is the single declaration test used by explicit-path resolution and by cold discovery alike."  # noqa: E501

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict) and payload.get("series_kind") is not None:
        return payload
    return None


def parse_frame_entries(
    run_dir: Path, raw: dict[str, Any], *, repo_root: Path
) -> list[SeriesFrame]:
    "Validate a series manifest's guarantees and build its frame entries. Raises :class:`SeriesError` on any violated guarantee. Shared by ``FrameSeries._accept`` and by discovery so there is one acceptance truth; per-frame byte verification stays in warm-up, where it already runs."  # noqa: E501

    if raw.get("series_kind") != SERIES_KIND:
        raise SeriesError(f"not a frame series (series_kind={raw.get('series_kind')!r})")
    if raw.get("status") != "completed":
        raise SeriesError(f"frame run status is {raw.get('status')!r}, not completed")
    entries = raw.get("frames")
    if not isinstance(entries, list) or not entries:
        raise SeriesError("manifest carries no frames array")
    expected = raw.get("frame_count_expected")
    written = raw.get("frame_count_written")
    if expected != written or len(entries) != written:
        raise SeriesError(
            f"frame count mismatch: expected={expected} written={written} entries={len(entries)}"
        )
    frames: list[SeriesFrame] = []
    for entry in entries:
        csv_path = repo_root / entry["csv"]
        json_path = repo_root / entry["json"]
        if not csv_path.is_file() or not json_path.is_file():
            raise SeriesError(f"frame {entry['tag']}: twin product missing on disk")
        frames.append(
            SeriesFrame(
                tag=str(entry["tag"]),
                valid_time_utc=str(entry["valid_time_utc"]),
                offset_seconds=int(entry["offset_seconds"]),
                n_flooded_producer=int(entry["n_flooded"]),
                csv_path=csv_path,
                json_path=json_path,
                csv_sha256=str(entry["csv_sha256"]),
                json_sha256=str(entry["json_sha256"]),
            )
        )
    times = [f.valid_time_utc for f in frames]
    if len(set(times)) != len(times):
        raise SeriesError("duplicate valid_time_utc across frames")
    if times != sorted(times):
        raise SeriesError("frames are not ordered by valid_time_utc")
    return frames


@dataclass(frozen=True)
class DiscoveredSeriesRun:

    run_dir: Path
    manifest: dict[str, Any]


def discover_series_runs(
    repo_root: Path,
) -> tuple[list[DiscoveredSeriesRun], list[tuple[str, str]]]:
    "Scan ``runs/*/manifest.json`` for declaring frame-series runs. Returns ``(valid_runs, skipped)`` where ``valid_runs`` is ordered NEWEST FIRST by the producer-recorded completion time (``end_time_iso``, falling back to ``start_time_iso``, run name as final deterministic tie-break), and ``skipped`` pairs each rejected declaring run with its realized reason. Runs without a series declaration are not candidates here at all -- flat twins have their own discovery path. SELECTION ORDER (recorded in dashboard state payloads): a frame series is preferred over flat event-maximum products when both are valid, because the series realizes strictly more demonstrated state -- every 30-minute solver frame of the replay plus the measured peak curve -- of which the event-maximum product is a summary. Recency breaks ties within each kind."  # noqa: E501

    runs_root = repo_root / "runs"
    valid: list[DiscoveredSeriesRun] = []
    skipped: list[tuple[str, str]] = []
    if not runs_root.is_dir():
        return valid, skipped

    def completed_iso(manifest: dict[str, Any]) -> str:
        return str(manifest.get("end_time_iso") or manifest.get("start_time_iso") or "")

    for manifest_path in sorted(runs_root.glob("*/manifest.json")):
        raw = read_series_manifest(manifest_path)
        if raw is None:
            continue
        run_id = manifest_path.parent.name
        try:
            parse_frame_entries(manifest_path.parent, raw, repo_root=repo_root)
        except SeriesError as exc:
            skipped.append((run_id, str(exc)))
            continue
        except (KeyError, TypeError, ValueError) as exc:
            skipped.append((run_id, f"{type(exc).__name__}: {exc}"))
            continue
        valid.append(DiscoveredSeriesRun(run_dir=manifest_path.parent, manifest=raw))
    valid.sort(key=lambda candidate: (completed_iso(candidate.manifest), candidate.run_dir.name))
    valid.reverse()
    return valid, skipped


def detect_series(product_argument: Path | None, *, repo_root: Path) -> FrameSeries | None:
    "Return a FrameSeries for a frame run, or None when no frame series applies. Accepted explicit arguments: the run directory, the manifest path inside it, or the products directory of a run whose manifest (one level up) declares frames. With no argument this is THE discovery truth shared by the dashboard and the launcher: runs/*/manifest.json is scanned for declaring frame-series runs and the newest valid one wins (see :func:`discover_series_runs`)."  # noqa: E501

    if product_argument is None:
        valid, _skipped = discover_series_runs(repo_root)
        if not valid:
            return None
        return FrameSeries(valid[0].run_dir, repo_root=repo_root)
    try:
        resolved = product_argument.expanduser().resolve()
        resolved.relative_to(repo_root.resolve())
    except ValueError:
        return None
    candidates: list[Path] = []
    if resolved.is_dir():
        candidates = [resolved, resolved / "manifest.json"]
        if resolved.name == "products":
            candidates.append(resolved.parent / "manifest.json")
    elif resolved.is_file():
        candidates = [resolved]
    for candidate in candidates:
        manifest = candidate / "manifest.json" if candidate.is_dir() else candidate
        if not manifest.is_file():
            continue
        payload = read_series_manifest(manifest)
        if payload is not None:
            return FrameSeries(manifest.parent, repo_root=repo_root)
    return None
