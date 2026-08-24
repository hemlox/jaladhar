"""KSNDMC live-collector health audit: success/failure counts and data content.

Counts every capture in data/raw/ksndmc/live/, every parseable gauge reading,
every parse failure, and the rainfall content (per-gauge-day max, live max).
Writes a manifest alongside the report. This is the committed script behind any
KSNDMC-health number quoted in this repo (CLAUDE.md V9).

Repro: python scripts/audit_ksndmc_live.py
"""
from __future__ import annotations

import json
import subprocess
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import typer

from jaladhar.forcing.ksndmc_audit import parse_capture_file

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[1]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


@app.command()
def main(data_dir: Path = typer.Option(REPO / "data/raw/ksndmc/live")) -> None:
    files = sorted(data_dir.glob("*/*.json"))
    per_day: dict[str, int] = defaultdict(int)
    per_day_parse_fail: dict[str, int] = defaultdict(int)
    per_day_readings: dict[str, int] = defaultdict(int)
    per_day_gauges: dict[str, set[int]] = defaultdict(set)
    per_day_districts: dict[str, set[str]] = defaultdict(set)
    gauge_day_max: dict[tuple[int, str], float] = defaultdict(float)
    total_readings = 0
    parse_fail_total = 0
    live_by_gauge: dict[int, dict] = {}

    for f in files:
        day = f.parent.name
        per_day[day] += 1
        try:
            obs = parse_capture_file(f)
        except Exception as e:  # parser already guards, but double-safety
            per_day_parse_fail[day] += 1
            parse_fail_total += 1
            continue
        if not obs:
            # a file with zero RAINGAUGE rows (schema drift) is a soft failure
            if f.stat().st_size < 50:
                per_day_parse_fail[day] += 1
                parse_fail_total += 1
            continue
        for o in obs:
            per_day_readings[day] += 1
            total_readings += 1
            per_day_gauges[day].add(o.raingauge_id)
            per_day_districts[day].add(o.district)
            key = (o.raingauge_id, day)
            if o.rain_mm > gauge_day_max[key]:
                gauge_day_max[key] = o.rain_mm
            live_by_gauge[o.raingauge_id] = {
                "rain_mm": o.rain_mm,
                "raintime": o.raintime,
                "capture_utc": o.capture_utc,
                "hobli": o.hobli,
                "district": o.district,
            }

    gauge_days = sorted(gauge_day_max.items(), key=lambda kv: -kv[1])

    report = {
        "run_at_iso": datetime.now(UTC).isoformat(),
        "total_captures": len(files),
        "parse_failures": parse_fail_total,
        "total_gauge_readings": total_readings,
        "distinct_gauges_seen": len(live_by_gauge),
        "per_day": {
            d: {
                "captures": per_day[d],
                "parse_failures": per_day_parse_fail.get(d, 0),
                "readings": per_day_readings[d],
                "gauges": len(per_day_gauges[d]),
                "districts": sorted(per_day_districts[d]),
            }
            for d in sorted(per_day)
        },
        "top_gauge_day_max_mm": [
            {
                "gauge": g,
                "day": d,
                "max_mm": round(v, 2),
                "hobli": live_by_gauge[g]["hobli"],
                "district": live_by_gauge[g]["district"],
            }
            for (g, d), v in gauge_days[:10]
        ],
        "wet_gauges_last_capture": [
            {"gauge": g, "rain_mm": v["rain_mm"], "hobli": v["hobli"], "district": v["district"]}
            for g, v in live_by_gauge.items()
            if v["rain_mm"] > 0
        ],
    }

    # write report + manifest
    out_dir = REPO / "runs/ksndmc_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    manifest = {
        "stage": "ksndmc_live_audit",
        "status": "completed",
        "git_sha": git_sha(),
        "start_time_iso": datetime.now(UTC).isoformat(),
        "end_time_iso": datetime.now(UTC).isoformat(),
        "inputs": {"data_dir": str(data_dir.relative_to(REPO))},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    typer.echo("=" * 72)
    typer.echo("KSNDMC LIVE COLLECTOR HEALTH")
    typer.echo("=" * 72)
    typer.echo(f"Captures: {len(files)}  |  Parse failures: {parse_fail_total}  "
               f"|  Gauge readings: {total_readings:,}  |  Distinct gauges: {len(live_by_gauge)}")
    for d in sorted(per_day):
        print(
            f"  {d}: {per_day[d]:>4} captures, {per_day_readings[d]:>7,} readings, "
            f"{len(per_day_gauges[d]):>3} gauges, "
            f"{per_day_parse_fail.get(d, 0)} parse fails"
        )
    typer.echo("")
    typer.echo("Top gauge-day rainfall maxima:")
    for row in report["top_gauge_day_max_mm"]:
        print(
            f"  {row['gauge']:>6}  {row['day']}  {row['max_mm']:6.2f} mm  "
            f"{row['district']}/{row['hobli']}"
        )
    typer.echo("")
    typer.echo("Wet gauges on the latest capture (RAIN > 0):")
    for row in report["wet_gauges_last_capture"]:
        print(f"  gauge {row['gauge']}  {row['rain_mm']:.2f} mm  {row['district']}/{row['hobli']}")
    typer.echo(f"\nReport: {out_dir}/report.json  Manifest: {out_dir}/manifest.json")


if __name__ == "__main__":
    app()
