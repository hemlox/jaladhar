"""Enforces CLAUDE.md Rule 1: Every point carries a verified source URL,
- Generates execution manifest in runs/groundtruth/manifest.json."""

from __future__ import annotations

import csv
import json
import math
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import typer
import yaml

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

BBMP_BBOX = {
    "min_lon": 77.46005,
    "min_lat": 12.83362,
    "max_lon": 77.78436,
    "max_lat": 13.14266,
}


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * R * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


@dataclass(frozen=True)
class GroundTruthPoint:
    id: str
    location_name: str
    lat: float
    lon: float
    geolocation_method: str
    geolocation_confidence: str
    observed_date: str
    depth_band_low_m: float | None
    depth_band_high_m: float | None
    depth_cue: str
    depth_confidence: str
    source_url: str
    source_publication_date: str
    source_quote: str
    retrieved_at_utc: str
    in_bbmp_kml: bool
    notes: str


def select_event_groundtruth(
    points: list[GroundTruthPoint],
    event_window_start: str,
    event_window_end: str,
) -> tuple[list[GroundTruthPoint], list[dict[str, str]]]:
    """Select records observed during one replay, retaining explicit rejections."""
    if event_window_start > event_window_end:
        raise ValueError(
            f"event_window_start {event_window_start} is after event_window_end {event_window_end}"
        )

    eligible: list[GroundTruthPoint] = []
    rejected: list[dict[str, str]] = []
    for point in points:
        if event_window_start <= point.observed_date <= event_window_end:
            eligible.append(point)
        else:
            rejected.append(
                {
                    "id": point.id,
                    "observed_date": point.observed_date,
                    "reason": "observed_date_outside_event_window",
                    "event_window_start": event_window_start,
                    "event_window_end": event_window_end,
                }
            )
    return eligible, rejected


def resolve_groundtruth_scoring_config(
    groundtruth_config: dict[str, Any],
) -> tuple[str, str, float]:
    required = ["scoring_event_window_start", "scoring_event_window_end", "max_snap_distance_m"]
    missing = [key for key in required if key not in groundtruth_config]
    if missing:
        raise KeyError(f"Missing validation.groundtruth config keys: {missing}")

    start = str(groundtruth_config["scoring_event_window_start"])
    end = str(groundtruth_config["scoring_event_window_end"])
    max_snap_distance_m = float(groundtruth_config["max_snap_distance_m"])
    if start > end:
        raise ValueError(f"scoring event start {start} is after end {end}")
    if max_snap_distance_m < 0:
        raise ValueError("max_snap_distance_m must be non-negative")
    return start, end, max_snap_distance_m


def load_and_validate_groundtruth(
    csv_path: Path = REPO / "data/raw/groundtruth/sept2022_points.csv",
    bbmp_kml_dir: Path = REPO / "data/raw/bbmp",
    overlap_threshold_m: float = 100.0,
) -> tuple[list[GroundTruthPoint], dict[str, Any]]:
    """Load CSV, validate all rule-1 and spatial invariants, and cross-check against BBMP KMLs."""
    if not csv_path.exists():
        raise FileNotFoundError(f"Ground truth CSV not found at {csv_path}")

    bbmp_points: list[tuple[float, float]] = []
    if bbmp_kml_dir.exists():
        for kml_name in ["flood_prone_locations", "low_lying_areas", "vulnerable_to_flooding"]:
            kml_file = bbmp_kml_dir / f"{kml_name}.kml"
            if kml_file.exists():
                gdf = gpd.read_file(kml_file)
                for geom in gdf.geometry:
                    if geom.geom_type == "Point":
                        bbmp_points.append((float(geom.y), float(geom.x)))

    points: list[GroundTruthPoint] = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_idx, r in enumerate(reader, start=1):
            # Rule 1 checks: source provenance
            url = r["source_url"].strip()
            pub_date = r["source_publication_date"].strip()
            quote = r["source_quote"].strip()
            if not url or not pub_date or not quote:
                raise ValueError(
                    f"Row {row_idx} ({r.get('id', 'unknown')}): missing source provenance. "
                    "Rule 1 requires non-empty source_url, source_publication_date, "
                    "and source_quote."
                )

            obs_date = r["observed_date"].strip()
            if not ("2022-08-30" <= obs_date <= "2022-09-06"):
                raise ValueError(
                    f"Row {row_idx} ({r.get('id', 'unknown')}): observed_date {obs_date} "
                    "is OUT_OF_WINDOW (2022-08-30..2022-09-06)."
                )

            # Spatial boundary check
            lat = float(r["lat"])
            lon = float(r["lon"])
            if not (
                BBMP_BBOX["min_lat"] <= lat <= BBMP_BBOX["max_lat"]
                and BBMP_BBOX["min_lon"] <= lon <= BBMP_BBOX["max_lon"]
            ):
                raise ValueError(
                    f"Row {row_idx} ({r.get('id', 'unknown')}): coordinate ({lat}, {lon}) "
                    "outside BBMP bounding box."
                )

            depth_low_str = r.get("depth_band_low_m", "").strip()
            depth_high_str = r.get("depth_band_high_m", "").strip()
            depth_low = float(depth_low_str) if depth_low_str else None
            depth_high = float(depth_high_str) if depth_high_str else None

            if depth_low is not None and depth_high is not None:
                if depth_low >= depth_high:
                    raise ValueError(
                        f"Row {row_idx} ({r.get('id', 'unknown')}): "
                        f"depth_band_low_m ({depth_low}) must be < "
                        f"depth_band_high_m ({depth_high})."
                    )

            in_bbmp = False
            for b_lat, b_lon in bbmp_points:
                if haversine_distance_m(lat, lon, b_lat, b_lon) <= overlap_threshold_m:
                    in_bbmp = True
                    break

            points.append(
                GroundTruthPoint(
                    id=r["id"].strip(),
                    location_name=r["location_name"].strip(),
                    lat=lat,
                    lon=lon,
                    geolocation_method=r["geolocation_method"].strip(),
                    geolocation_confidence=r["geolocation_confidence"].strip(),
                    observed_date=obs_date,
                    depth_band_low_m=depth_low,
                    depth_band_high_m=depth_high,
                    depth_cue=r["depth_cue"].strip(),
                    depth_confidence=r.get("depth_confidence", "").strip(),
                    source_url=url,
                    source_publication_date=pub_date,
                    source_quote=quote,
                    retrieved_at_utc=r.get("retrieved_at_utc", "").strip(),
                    in_bbmp_kml=in_bbmp,
                    notes=r.get("notes", "").strip(),
                )
            )

    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            dist = haversine_distance_m(points[i].lat, points[i].lon, points[j].lat, points[j].lon)
            if dist <= 50.0:
                raise ValueError(
                    f"Duplicate points within {dist:.1f} m: "
                    f"{points[i].id} ({points[i].location_name}) and "
                    f"{points[j].id} ({points[j].location_name})"
                )

    confidence_counts: dict[str, int] = {}
    method_counts: dict[str, int] = {}
    extent_only_count = 0
    bbmp_overlap_count = 0

    for p in points:
        confidence_counts[p.geolocation_confidence] = (
            confidence_counts.get(p.geolocation_confidence, 0) + 1
        )
        method_counts[p.geolocation_method] = method_counts.get(p.geolocation_method, 0) + 1
        if p.depth_cue == "NONE" or p.depth_band_low_m is None:
            extent_only_count += 1
        if p.in_bbmp_kml:
            bbmp_overlap_count += 1

    summary = {
        "total_points_found": len(points),
        "in_window_dates": "2022-08-30 to 2022-09-06",
        "geolocation_confidence_breakdown": confidence_counts,
        "geolocation_method_breakdown": method_counts,
        "extent_only_count": extent_only_count,
        "with_depth_band_count": len(points) - extent_only_count,
        "bbmp_kml_overlap_count": bbmp_overlap_count,
    }
    return points, summary


@app.command()
def main(
    config: Path = typer.Option(REPO / "configs/validation.yaml", help="Path to validation config"),
) -> None:
    """Validate groundtruth dataset, check all invariants, and update manifest."""
    with open(config) as f:
        cfg = yaml.safe_load(f)

    gt_cfg = cfg.get("groundtruth", {})
    csv_path = REPO / gt_cfg.get("points_csv", "data/raw/groundtruth/sept2022_points.csv")
    bbmp_dir = REPO / gt_cfg.get("bbmp_kml_dir", "data/raw/bbmp")
    runs_dir = REPO / gt_cfg.get("runs_dir", "runs/groundtruth")
    runs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = runs_dir / "manifest.json"

    t0 = time.perf_counter()
    manifest: dict[str, Any] = {
        "stage": "groundtruth_sept2022_collection",
        "status": "running",
        "git_sha": git_sha(),
        "start_time_iso": datetime.now(UTC).isoformat(),
        "config_path": str(config),
        "csv_path": str(csv_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        points, summary = load_and_validate_groundtruth(csv_path=csv_path, bbmp_kml_dir=bbmp_dir)
        wall_clock = time.perf_counter() - t0

        manifest.update(
            {
                "status": "completed",
                "wall_clock_sec": round(wall_clock, 4),
                "summary": summary,
                "points_count": len(points),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))

        typer.echo("=======================================================")
        typer.echo("GROUND-TRUTH FLOOD POINTS VALIDATION (PROMPT.md §8 item 4)")
        typer.echo("=======================================================")
        typer.echo(f"Total points found: {summary['total_points_found']}")
        typer.echo(f"Confidence breakdown: {summary['geolocation_confidence_breakdown']}")
        typer.echo(f"Method breakdown: {summary['geolocation_method_breakdown']}")
        typer.echo(f"Extent-only points: {summary['extent_only_count']}")
        typer.echo(f"Points with depth bands: {summary['with_depth_band_count']}")
        typer.echo(f"BBMP KML overlap count: {summary['bbmp_kml_overlap_count']}")
        typer.echo(f"\nWrote manifest to {manifest_path}")

    except Exception as e:
        manifest.update(
            {
                "status": "failed",
                "error": str(e),
                "wall_clock_sec": round(time.perf_counter() - t0, 4),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise


if __name__ == "__main__":
    app()
