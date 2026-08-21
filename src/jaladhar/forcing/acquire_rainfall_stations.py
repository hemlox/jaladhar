"""Acquire archived Bengaluru station observations without inventing forcing.

This module keeps the source observations separate from the model forcing
adapters.  NOAA Global Hourly JSON preserves the variable-length ISD records,
including AA1 accumulation groups and raw METAR/SPECI remarks.  ISD-Lite is
also downloaded because its fixed-width one-hour precipitation field is an
independent check on whether the airport records carry quantitative rain.

IEM is queried as a METAR/SPECI archive cross-check.  IEM's dataset page states
that precipitation is unavailable for non-US sites and its site terms retain
copyright, so this module records response metadata only and does not copy the
IEM payload into the repository.

No amount is inferred from ``RA``, ``TSRA``, ``+``/``-`` intensity markers, or
from an accumulation over a longer period.  Such observations remain
qualitative or accumulation-average measurements and cannot be reported as an
hourly or sub-hourly peak.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
import subprocess
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
import typer
import yaml

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO / "configs/rainfall_stations.yaml"

IMERG_DOMAIN_PEAK_MM_HR = 21.40
IMERG_FLOOD_POINT_MEAN_PEAK_MM_HR = 10.4

_PRECIPITATION_TOKENS = (
    "+TSRA",
    "-TSRA",
    "TSRA",
    "+SHRA",
    "-SHRA",
    "SHRA",
    "+RA",
    "-RA",
    "RA",
    "+DZ",
    "-DZ",
    "DZ",
    "+TS",
    "-TS",
    "TS",
    "VCSH",
)
_QUALITATIVE_STRENGTH = {
    "+TSRA": 6,
    "TSRA": 5,
    "-TSRA": 4,
    "+SHRA": 3,
    "SHRA": 2,
    "-SHRA": 1,
    "+RA": 3,
    "RA": 2,
    "-RA": 1,
    "+DZ": 3,
    "DZ": 2,
    "-DZ": 1,
    "+TS": 3,
    "TS": 2,
    "-TS": 1,
    "VCSH": 1,
}


@dataclass(frozen=True)
class DownloadResult:
    """Realized result of one HTTP request or an existing local artifact."""

    source: str
    url: str
    path: str | None
    status: str
    http_status: int | None
    bytes: int | None
    sha256: str | None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "url": self.url,
            "path": self.path,
            "status": self.status,
            "http_status": self.http_status,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "error": self.error,
        }


def git_sha() -> str:
    """Return the repository revision used for the acquisition manifest."""

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _require(mapping: dict[str, Any], keys: Iterable[str], prefix: str) -> list[str]:
    return [f"{prefix}.{key}" for key in keys if key not in mapping]


def resolve_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Touch every acquisition key before any network request is made."""

    missing: list[str] = []
    missing.extend(_require(cfg, ["start_date", "end_date", "output_dir"], "config"))

    noaa = cfg.get("noaa_global_hourly", {})
    missing.extend(
        _require(
            noaa,
            ["endpoint", "stations", "format", "include_attributes"],
            "noaa_global_hourly",
        )
    )

    lite = cfg.get("noaa_isd_lite", {})
    missing.extend(_require(lite, ["base_url", "stations"], "noaa_isd_lite"))

    iem = cfg.get("iem_metar", {})
    missing.extend(_require(iem, ["endpoint", "stations", "data"], "iem_metar"))

    if missing:
        raise KeyError(
            "Missing required rainfall-station acquisition config keys: "
            f"{missing}. Resolve all keys before execution."
        )

    try:
        start = date.fromisoformat(str(cfg["start_date"]))
        end = date.fromisoformat(str(cfg["end_date"]))
    except ValueError as exc:
        raise ValueError("config.start_date and config.end_date must be YYYY-MM-DD") from exc
    if end < start:
        raise ValueError("config.end_date must not precede config.start_date")

    for section_name in ("noaa_global_hourly", "noaa_isd_lite", "iem_metar"):
        section = cfg[section_name]
        if not section["stations"]:
            raise ValueError(f"{section_name}.stations must not be empty")

    return cfg


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(payload)
    temporary.replace(path)


def fetch_bytes(
    url: str,
    target: Path | None,
    source: str,
    *,
    params: dict[str, Any] | None = None,
    timeout_s: int = 120,
    force: bool = False,
) -> DownloadResult:
    """Fetch a response atomically, or verify an existing local artifact."""

    request_url = requests.Request("GET", url, params=params).prepare().url
    if target is not None and target.exists() and target.stat().st_size > 0 and not force:
        payload = target.read_bytes()
        return DownloadResult(
            source=source,
            url=request_url,
            path=str(target.relative_to(REPO)),
            status="reused",
            http_status=None,
            bytes=len(payload),
            sha256=_sha256_bytes(payload),
        )

    try:
        response = requests.get(url, params=params, timeout=timeout_s)
        response.raise_for_status()
        payload = response.content
        if target is not None:
            _write_atomic(target, payload)
            path = str(target.relative_to(REPO))
        else:
            path = None
        return DownloadResult(
            source=source,
            url=response.url,
            path=path,
            status="downloaded",
            http_status=response.status_code,
            bytes=len(payload),
            sha256=_sha256_bytes(payload),
        )
    except Exception as exc:
        return DownloadResult(
            source=source,
            url=request_url,
            path=str(target.relative_to(REPO)) if target is not None else None,
            status="failed",
            http_status=getattr(getattr(exc, "response", None), "status_code", None),
            bytes=None,
            sha256=None,
            error=f"{type(exc).__name__}: {exc}",
        )


def _date_params(start: date, end: date) -> dict[str, Any]:
    return {
        "dataset": "global-hourly",
        "stations": "",
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "format": "json",
        "includeAttributes": "true",
    }


def acquire_noaa_global_hourly(
    cfg: dict[str, Any], output_dir: Path, start: date, end: date, force: bool
) -> list[DownloadResult]:
    section = cfg["noaa_global_hourly"]
    destination = output_dir / "noaa_global_hourly"
    results: list[DownloadResult] = []
    for station in section["stations"]:
        params = _date_params(start, end)
        params["stations"] = station
        params["format"] = str(section["format"])
        params["includeAttributes"] = str(bool(section["include_attributes"])).lower()
        target = destination / f"{station}_{start:%Y%m%d}_{end:%Y%m%d}.json"
        result = fetch_bytes(
            str(section["endpoint"]),
            target,
            "noaa_global_hourly",
            params=params,
            force=force,
        )
        results.append(result)
    return results


def acquire_noaa_isd_lite(
    cfg: dict[str, Any], output_dir: Path, year: int, force: bool
) -> list[DownloadResult]:
    section = cfg["noaa_isd_lite"]
    destination = output_dir / "noaa_isd_lite"
    results: list[DownloadResult] = []
    for station in section["stations"]:
        filename = f"{station}-{year}.gz"
        url = f"{str(section['base_url']).rstrip('/')}/{year}/{filename}"
        target = destination / filename
        results.append(fetch_bytes(url, target, "noaa_isd_lite", force=force))
    return results


def acquire_iem_probe(
    cfg: dict[str, Any], output_dir: Path, start: date, end: date
) -> DownloadResult:
    """Query IEM and retain only provenance metadata, not its copyrighted body."""

    section = cfg["iem_metar"]
    params: list[tuple[str, str]] = [
        ("data", str(section["data"])),
        ("sts", f"{start.isoformat()}T00:00:00Z"),
        ("ets", f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z"),
        ("tz", "UTC"),
        ("format", "onlycomma"),
        ("latlon", "yes"),
        ("elev", "yes"),
        ("missing", "empty"),
        ("trace", "empty"),
        ("direct", "no"),
    ]
    params.extend(("station", str(station)) for station in section["stations"])
    url = str(section["endpoint"])
    try:
        response = requests.get(url, params=params, timeout=120)
        response.raise_for_status()
        payload = response.content
        metadata = {
            "source": "iem_metar",
            "request_url": response.url,
            "status": "responded_metadata_only",
            "http_status": response.status_code,
            "bytes": len(payload),
            "sha256": _sha256_bytes(payload),
            "body_retained": False,
            "reason": (
                "IEM dataset page says non-US precipitation is unavailable; "
                "site terms are all rights reserved."
            ),
        }
        target = output_dir / "iem_metar_probe.json"
        _write_atomic(target, json.dumps(metadata, indent=2).encode("utf-8"))
        return DownloadResult(
            source="iem_metar",
            url=response.url,
            path=str(target.relative_to(REPO)),
            status="responded_metadata_only",
            http_status=response.status_code,
            bytes=len(payload),
            sha256=_sha256_bytes(payload),
        )
    except Exception as exc:
        return DownloadResult(
            source="iem_metar",
            url=url,
            path=str((output_dir / "iem_metar_probe.json").relative_to(REPO)),
            status="failed",
            http_status=getattr(getattr(exc, "response", None), "status_code", None),
            bytes=None,
            sha256=None,
            error=f"{type(exc).__name__}: {exc}",
        )


def parse_aa_group(value: str | None) -> dict[str, Any] | None:
    """Decode one NOAA AA1 group without changing its accumulation semantics."""

    if not value:
        return None
    fields = value.split(",")
    if len(fields) != 4:
        return None
    period_raw, depth_raw, condition, quality = fields
    if period_raw == "99" or depth_raw == "9999":
        return {
            "period_hours": None,
            "depth_mm": None,
            "condition_code": condition,
            "quality_code": quality,
            "raw": value,
        }
    try:
        period_hours = int(period_raw)
        depth_mm = int(depth_raw) / 10.0
    except ValueError:
        return None
    if period_hours <= 0:
        return None
    return {
        "period_hours": period_hours,
        "depth_mm": depth_mm,
        "condition_code": condition,
        "quality_code": quality,
        "raw": value,
    }


def _qualitative_precipitation(remark: str) -> list[str]:
    tokens: list[str] = []
    for token in _PRECIPITATION_TOKENS:
        if re.search(rf"(?<![A-Z+-]){re.escape(token)}(?![A-Z])", remark):
            tokens.append(token)
    return sorted(tokens)


def _parse_isd_lite_line(line: str) -> dict[str, Any] | None:
    fields = line.split()
    if len(fields) < 12:
        return None
    try:
        timestamp = datetime(
            int(fields[0]),
            int(fields[1]),
            int(fields[2]),
            int(fields[3]),
            tzinfo=UTC,
        )
    except ValueError:
        return None

    def depth(raw: str) -> float | None:
        value = int(raw)
        if value == -9999:
            return None
        if value == -1:
            return 0.0
        return value / 10.0

    try:
        precip_1h = depth(fields[10])
        precip_6h = depth(fields[11])
    except ValueError:
        return None
    return {
        "time_utc": timestamp.isoformat(),
        "precip_1h_mm": precip_1h,
        "precip_1h_raw": fields[10],
        "precip_6h_mm": precip_6h,
        "precip_6h_raw": fields[11],
        "raw": line.rstrip("\n"),
    }


def parse_isd_lite(path: Path, start: datetime, end_exclusive: datetime) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="ascii", errors="replace") as handle:
        for line in handle:
            parsed = _parse_isd_lite_line(line)
            if parsed is None:
                continue
            timestamp = datetime.fromisoformat(parsed["time_utc"])
            if start <= timestamp < end_exclusive:
                records.append(parsed)
    return records


def parse_global_hourly(
    path: Path, start: datetime, end_exclusive: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON list in {path}")
    aa_rows: list[dict[str, Any]] = []
    metar_rows: list[dict[str, Any]] = []
    for record in payload:
        timestamp = datetime.fromisoformat(str(record["DATE"]).replace("Z", "+00:00")).replace(
            tzinfo=UTC
        )
        if not start <= timestamp < end_exclusive:
            continue
        aa = parse_aa_group(record.get("AA1"))
        if aa is not None:
            aa_rows.append(
                {
                    "station": record.get("STATION"),
                    "name": record.get("NAME"),
                    "report_type": record.get("REPORT_TYPE"),
                    "time_utc": timestamp.isoformat(),
                    **aa,
                }
            )
        if record.get("REPORT_TYPE") in {"FM-15", "FM-16"}:
            remark = str(record.get("REM", ""))
            metar_rows.append(
                {
                    "station": record.get("STATION"),
                    "name": record.get("NAME"),
                    "report_type": record.get("REPORT_TYPE"),
                    "time_utc": timestamp.isoformat(),
                    "qualitative_precipitation": ";".join(_qualitative_precipitation(remark)),
                    "quantitative_precip_mm": None,
                }
            )
    return aa_rows, metar_rows


def _station_id_from_path(path: Path) -> str:
    if "_" in path.name:
        return path.name.split("_")[0]
    return f"{path.name.split('-', 1)[0]}99999"


def derive_artifacts(
    cfg: dict[str, Any], output_dir: Path, start_date: date, end_date: date
) -> dict[str, Any]:
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)
    end_exclusive = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    aa_rows: list[dict[str, Any]] = []
    metar_rows: list[dict[str, Any]] = []
    station_metrics: dict[str, Any] = {}
    for path in sorted((output_dir / "noaa_global_hourly").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"Expected a JSON list in {path}")
        station_aa, station_metar = parse_global_hourly(path, start, end_exclusive)
        aa_rows.extend(station_aa)
        metar_rows.extend(station_metar)
        all_valid = [
            row
            for row in station_aa
            if row["depth_mm"] is not None and row["period_hours"] is not None
        ]
        fully_contained = [
            row
            for row in all_valid
            if datetime.fromisoformat(row["time_utc"]) - timedelta(hours=row["period_hours"])
            >= start
        ]
        for row in fully_contained:
            row["window_start_utc"] = (
                datetime.fromisoformat(row["time_utc"]) - timedelta(hours=row["period_hours"])
            ).isoformat()
            row["accumulation_average_intensity_mm_hr"] = row["depth_mm"] / row["period_hours"]

        peak_average = max(
            fully_contained,
            key=lambda row: row["accumulation_average_intensity_mm_hr"],
            default=None,
        )
        station = _station_id_from_path(path)
        qualitative_rows = [row for row in station_metar if row["qualitative_precipitation"]]
        qualitative_tokens = sorted(
            {
                token
                for row in qualitative_rows
                for token in row["qualitative_precipitation"].split(";")
            }
        )
        strongest_marker = max(
            qualitative_tokens,
            key=lambda token: _QUALITATIVE_STRENGTH.get(token, 0),
            default=None,
        )
        strongest_times = [
            row["time_utc"]
            for row in qualitative_rows
            if strongest_marker and strongest_marker in row["qualitative_precipitation"].split(";")
        ]
        report_types = Counter(str(record.get("REPORT_TYPE")) for record in payload)
        special_report_times = [
            record.get("DATE") for record in payload if record.get("REPORT_TYPE") == "FM-16"
        ]
        station_metrics[station] = {
            "raw_records_in_window": len(payload),
            "first_record_time_utc": payload[0].get("DATE") if payload else None,
            "last_record_time_utc": payload[-1].get("DATE") if payload else None,
            "report_type_counts": dict(sorted(report_types.items())),
            "special_report_times_utc": special_report_times,
            "aa1_records": len(station_aa),
            "aa1_valid_depth_records": len(all_valid),
            "aa1_fully_contained_records": len(fully_contained),
            "metar_or_speci_records": len(station_metar),
            "qualitative_precipitation_reports": len(qualitative_rows),
            "qualitative_precipitation_markers": qualitative_tokens,
            "strongest_qualitative_marker": strongest_marker,
            "strongest_qualitative_marker_times_utc": strongest_times,
            "hourly_peak_intensity_mm_hr": None,
            "subhourly_peak_intensity_mm_hr": None,
            "peak_accumulation_average_intensity_mm_hr": (
                peak_average["accumulation_average_intensity_mm_hr"] if peak_average else None
            ),
            "peak_accumulation_average_time_utc": (
                peak_average["time_utc"] if peak_average else None
            ),
            "peak_accumulation_period_hours": (
                peak_average["period_hours"] if peak_average else None
            ),
            "peak_accumulation_depth_mm": peak_average["depth_mm"] if peak_average else None,
            "comparison_to_imerg_domain_peak": (
                peak_average["accumulation_average_intensity_mm_hr"] / IMERG_DOMAIN_PEAK_MM_HR
                if peak_average
                else None
            ),
            "comparison_note": (
                "This is an accumulation-average ratio, not a peak-intensity ratio. "
                "AA1 periods are multi-hour and may overlap; no hourly or sub-hourly peak "
                "is derived."
            ),
        }

    lite_rows: list[dict[str, Any]] = []
    lite_metrics: dict[str, Any] = {}
    for path in sorted((output_dir / "noaa_isd_lite").glob("*.gz")):
        records = parse_isd_lite(path, start, end_exclusive)
        station = _station_id_from_path(path)
        for row in records:
            row = {"station": station, **row}
            lite_rows.append(row)
        valid_1h = [row for row in records if row["precip_1h_mm"] is not None]
        valid_6h = [row for row in records if row["precip_6h_mm"] is not None]
        peak_1h = max(valid_1h, key=lambda row: row["precip_1h_mm"], default=None)
        lite_metrics[station] = {
            "records_in_window": len(records),
            "one_hour_precipitation_records": len(valid_1h),
            "six_hour_precipitation_records": len(valid_6h),
            "peak_hourly_intensity_mm_hr": peak_1h["precip_1h_mm"] if peak_1h else None,
            "peak_hourly_time_utc": peak_1h["time_utc"] if peak_1h else None,
        }
        if peak_1h and station in station_metrics:
            station_metrics[station]["hourly_peak_intensity_mm_hr"] = peak_1h["precip_1h_mm"]
            station_metrics[station]["hourly_peak_time_utc"] = peak_1h["time_utc"]

    derived_dir = output_dir / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)
    with (derived_dir / "noaa_aa1_accumulations.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = [
            "station",
            "name",
            "report_type",
            "time_utc",
            "window_start_utc",
            "period_hours",
            "depth_mm",
            "accumulation_average_intensity_mm_hr",
            "condition_code",
            "quality_code",
            "raw",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(aa_rows, key=lambda row: (row["station"], row["time_utc"])))

    with (derived_dir / "noaa_metar_qualitative_weather.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = [
            "station",
            "name",
            "report_type",
            "time_utc",
            "qualitative_precipitation",
            "quantitative_precip_mm",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(metar_rows, key=lambda row: (row["station"], row["time_utc"])))

    with (derived_dir / "noaa_isd_lite_precipitation.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = [
            "station",
            "time_utc",
            "precip_1h_mm",
            "precip_1h_raw",
            "precip_6h_mm",
            "precip_6h_raw",
            "raw",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(lite_rows, key=lambda row: (row["station"], row["time_utc"])))

    metrics = {
        "window_start_utc": start.isoformat(),
        "window_end_utc_exclusive": end_exclusive.isoformat(),
        "station_metrics": station_metrics,
        "isd_lite_metrics": lite_metrics,
        "imerg_reference": {
            "domain_peak_mm_hr": IMERG_DOMAIN_PEAK_MM_HR,
            "flood_point_mean_peak_mm_hr": IMERG_FLOOD_POINT_MEAN_PEAK_MM_HR,
            "provenance": (
                "Prior gate measurement recorded in OPEN-ITEMS.md item AN; not re-run here."
            ),
        },
        "interpretation": {
            "hourly_peak": "null means no quantitative one-hour station field was realized.",
            "subhourly_peak": "null means no quantitative sub-hourly station field was realized.",
            "qualitative_metar": (
                "METAR/SPECI weather tokens are retained as observations, never converted to mm/hr."
            ),
            "station_scope": (
                "Each series is a point observation and cannot be treated as a "
                "717 km2 domain field."
            ),
        },
    }
    (derived_dir / "station_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    return metrics


@app.command()
def main(
    config: Path = typer.Option(DEFAULT_CONFIG, help="Acquisition YAML configuration"),
    force: bool = typer.Option(False, help="Redownload existing raw artifacts"),
) -> None:
    """Download and derive archived Bengaluru station-rainfall evidence."""

    with config.open(encoding="utf-8") as handle:
        cfg = resolve_config(yaml.safe_load(handle))
    start_date = date.fromisoformat(str(cfg["start_date"]))
    end_date = date.fromisoformat(str(cfg["end_date"]))
    output_dir = REPO / str(cfg["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "acquisition_manifest.json"
    started = time.perf_counter()
    manifest: dict[str, Any] = {
        "stage": "rainfall_station_acquisition",
        "status": "running",
        "git_sha": git_sha(),
        "config_path": str(config.relative_to(REPO)),
        "window": {
            "start_date": start_date.isoformat(),
            "end_date_inclusive": end_date.isoformat(),
            "timezone": "UTC",
        },
        "started_at_utc": datetime.now(UTC).isoformat(),
        "compute_budget": "network I/O only; no solver or fabricated forcing stage",
        "results": [],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    try:
        typer.echo("Acquisition budget: 4 NOAA JSON + 4 ISD-Lite files + 1 IEM metadata probe")
        results = acquire_noaa_global_hourly(cfg, output_dir, start_date, end_date, force)
        results.extend(acquire_noaa_isd_lite(cfg, output_dir, start_date.year, force))
        results.append(acquire_iem_probe(cfg, output_dir, start_date, end_date))
        manifest["results"] = [result.as_dict() for result in results]
        metrics = derive_artifacts(cfg, output_dir, start_date, end_date)
        manifest.update(
            {
                "status": "completed",
                "completed_at_utc": datetime.now(UTC).isoformat(),
                "wall_clock_sec": time.perf_counter() - started,
                "derived_metrics_path": str(
                    (output_dir / "derived/station_metrics.json").relative_to(REPO)
                ),
                "quantitative_hourly_peak_available": any(
                    value.get("peak_hourly_intensity_mm_hr") is not None
                    for value in metrics["isd_lite_metrics"].values()
                ),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        typer.echo(f"Wrote realized station evidence under {output_dir.relative_to(REPO)}")
    except Exception as exc:
        manifest.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "wall_clock_sec": time.perf_counter() - started,
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    app()
