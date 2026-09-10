#!/usr/bin/env python3
"Build the retrospective admission sidecar for Replay #2's per-frame depth series. The historical solver manifest (`runs/goal_d_replay2_final_config/manifest.json`) predates the frame-series product and binds only its aggregate rasters. This script never rewrites that manifest: it emits a `data/curation/` sidecar binding the realized `depth_rasters/` bytes -- one SHA-256 per frame, the frame count, the cadence, and the valid-time derivation rule -- so downstream consumers can assert the series they read is the series the solver wrote (V1: hashes are realized state; directory listings are not)."  # noqa: E501

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

REPO = Path(__file__).resolve().parents[1]
FRAME_RE = re.compile(r"^depth_t(\d+)s\.tif$")

DEFAULT_SOURCE_DIR = REPO / "runs/goal_d_replay2_final_config/depth_rasters"
DEFAULT_SOURCE_MANIFEST = REPO / "runs/goal_d_replay2_final_config/manifest.json"
DEFAULT_OUTPUT = REPO / "data/curation/wf3_replay2_frame_series_admission.json"
EXPECTED_COUNT = 97
EXPECTED_CADENCE_SECONDS = 1800


class FrameAdmissionError(RuntimeError):
    pass


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_frame_series_admission(
    source_dir: Path,
    source_manifest_path: Path,
    *,
    expected_count: int = EXPECTED_COUNT,
    expected_cadence_seconds: int = EXPECTED_CADENCE_SECONDS,
    repo_root: Path = REPO,
) -> dict[str, object]:

    if not source_dir.is_dir():
        raise FrameAdmissionError(f"frame series directory is absent: {source_dir}")
    if not source_manifest_path.is_file():
        raise FrameAdmissionError(f"source manifest is absent: {source_manifest_path}")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("status") != "completed":
        raise FrameAdmissionError(
            f"source manifest status must be completed, got {source_manifest.get('status')!r}"
        )
    window = source_manifest.get("event_window")
    if not isinstance(window, dict):
        nested = source_manifest.get("event")
        window = nested if isinstance(nested, dict) else {}
    event_start_raw = window.get("start")
    if not isinstance(event_start_raw, str):
        raise FrameAdmissionError("source manifest has no event_window.start anchor")
    try:
        parsed_start = datetime.fromisoformat(event_start_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FrameAdmissionError(
            f"event_window.start is not ISO-8601: {event_start_raw!r}"
        ) from exc
    if parsed_start.tzinfo is None:
        raise FrameAdmissionError("event_window.start must be timezone-aware")
    event_start = parsed_start.astimezone(UTC)

    entries: list[dict[str, object]] = []
    for path in sorted(source_dir.iterdir()):
        match = FRAME_RE.fullmatch(path.name)
        if match is None:
            continue
        offset_seconds = int(match.group(1))
        entries.append(
            {
                "file": f"depth_rasters/{path.name}",
                "offset_seconds": offset_seconds,
                "sha256": sha256_file(path),
                "valid_time_utc": (event_start + timedelta(seconds=offset_seconds)).isoformat(),
            }
        )
    if len(entries) != expected_count:
        raise FrameAdmissionError(
            f"realized frame count {len(entries)} != expected {expected_count} in {source_dir}"
        )
    offsets = [int(entry["offset_seconds"]) for entry in entries]
    expected_offsets = list(
        range(0, expected_count * expected_cadence_seconds, expected_cadence_seconds)
    )
    if offsets != expected_offsets:
        raise FrameAdmissionError(
            "frame offsets are not the uniform "
            f"{expected_cadence_seconds}-s series 0..{expected_offsets[-1]}"
        )

    duration_hours = source_manifest.get("event_window", {}).get("duration_hours")
    if duration_hours is not None and float(duration_hours) * 3600.0 != offsets[-1]:
        raise FrameAdmissionError(
            f"last frame offset {offsets[-1]} s != manifest duration_hours {duration_hours}"
        )

    return {
        "decision": "ADMITTED",
        "product_label": "UNCOUPLED BASELINE",
        "coupling_enabled": False,
        "forcing_kind": "historical_replay",
        "temporal_aggregation": "frame_series",
        "series_kind": "instantaneous_solver_frames",
        "source_run_path": "runs/goal_d_replay2_final_config",
        "depth_raster_dir": str(source_dir.relative_to(repo_root.resolve())),
        "frame_count": len(entries),
        "cadence_seconds": expected_cadence_seconds,
        "first_offset_seconds": offsets[0],
        "last_offset_seconds": offsets[-1],
        "valid_time_rule": (
            f"event_window.start ({event_start.isoformat()}) + frame offset seconds; the final "
            "frame is the post-final-interval state and lies one cadence past event_window.end"
        ),
        "event_window_start_utc": event_start.isoformat(),
        "source_manifest_sha256": sha256_file(source_manifest_path),
        "frames": entries,
        "basis": (
            "The historical solver manifest predates the frame-series product and does not "
            "bind depth_rasters/. This retrospective sidecar supplies that binding without "
            "rewriting history."
        ),
        "does_not_claim": [
            "surface-drain graph coupling",
            "surcharge or backflow",
            "live rainfall nowcast",
        ],
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }


app = typer.Typer(add_completion=False)


@app.command()
def main(
    output: Path = typer.Option(DEFAULT_OUTPUT, help="Sidecar JSON to write atomically"),
    source_dir: Path = typer.Option(DEFAULT_SOURCE_DIR),
    source_manifest: Path = typer.Option(DEFAULT_SOURCE_MANIFEST),
) -> None:

    try:
        payload = build_frame_series_admission(source_dir, source_manifest)
    except FrameAdmissionError as exc:
        typer.echo(f"frame-series admission failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    def _atomic_write(target: Path, text: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)

    _atomic_write(output, json.dumps(payload, indent=2, sort_keys=False) + "\n")
    typer.echo("status=completed")
    typer.echo(f"sidecar={output.relative_to(REPO)}")
    typer.echo(f"frames={payload['frame_count']} cadence_s={payload['cadence_seconds']}")
    typer.echo(
        f"span={payload['first_offset_seconds']}..{payload['last_offset_seconds']}s "
        f"source_manifest_sha256={payload['source_manifest_sha256'][:16]}..."
    )


if __name__ == "__main__":
    app()
