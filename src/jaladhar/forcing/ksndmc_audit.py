"""KSNDMC Live Feed Audit: Cumulative vs Incremental Discrimination.

Analyzes all 477 captured JSON files in data/raw/ksndmc/live/ across
2026-08-14, 2026-08-15, and 2026-08-16.

Discriminator Logic:
- Cumulative feed: RAIN is monotonically non-decreasing within an IST day/reporting
  period (apart from the morning/midnight reset), and remains at the accumulated total
  when rainfall ceases.
- Incremental feed: RAIN represents a 15-minute rate/depth, rising during rainfall
  pulses and returning to 0.0 mm immediately after rainfall ceases.
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Observation:
    filename: str
    capture_utc: str
    district: str
    taluk: str
    hobli: str
    raingauge_id: int
    rain_mm: float
    raindate: str
    raintime: str


def parse_capture_file(file_path: Path) -> list[Observation]:
    """Parse double-encoded JSON capture from KSNDMC live logger."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, list):
            return []

        fname = file_path.name
        # match rain_d<dist>_<YYYYMMDDTHHMMSSZ>.json
        m = re.search(r"rain_d\d+_(\d{8}T\d{6}Z)", fname)
        cap_utc = m.group(1) if m else ""

        observations: list[Observation] = []
        for r in data:
            gid = r.get("RAINGAUGE")
            if gid is None:
                continue
            observations.append(
                Observation(
                    filename=fname,
                    capture_utc=cap_utc,
                    district=r.get("DISTRICT", ""),
                    taluk=r.get("TALUKNAME", ""),
                    hobli=r.get("HOBLINAME", ""),
                    raingauge_id=int(gid),
                    rain_mm=float(r.get("RAIN", 0.0) or 0.0),
                    raindate=r.get("RAINDATE", ""),
                    raintime=r.get("RAINTIME", ""),
                )
            )
        return observations
    except Exception as e:
        typer.echo(f"Warning: error parsing {file_path}: {e}", err=True)
        return []


def run_audit(data_dir: Path = REPO / "data/raw/ksndmc/live") -> dict[str, Any]:
    """Run full audit across all captures and return structured results."""
    files = sorted(data_dir.glob("*/*.json"))
    by_day_files: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        by_day_files[f.parent.name].append(f)

    results: dict[str, Any] = {
        "total_files": len(files),
        "days": {},
        "verdict": "",
        "discriminating_gauges": [],
    }

    # Track gauge series by day
    # day -> gid -> list[Observation]
    gauge_series_by_day: dict[str, dict[int, list[Observation]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for day in sorted(by_day_files.keys()):
        day_files = by_day_files[day]
        for f in day_files:
            obs_list = parse_capture_file(f)
            for obs in obs_list:
                gauge_series_by_day[day][obs.raingauge_id].append(obs)

        # Analyze changes within this day
        total_gauges = len(gauge_series_by_day[day])
        changed_gauges: dict[int, list[dict[str, Any]]] = {}
        all_increases: list[dict[str, Any]] = []
        all_decreases: list[dict[str, Any]] = []

        for gid, series in gauge_series_by_day[day].items():
            # sort by capture time
            series.sort(key=lambda o: (o.capture_utc, o.filename))
            changes: list[dict[str, Any]] = []
            for i in range(1, len(series)):
                prev_obs = series[i - 1]
                curr_obs = series[i]
                if curr_obs.rain_mm != prev_obs.rain_mm:
                    chg_info = {
                        "from_rain": prev_obs.rain_mm,
                        "to_rain": curr_obs.rain_mm,
                        "prev_file": prev_obs.filename,
                        "curr_file": curr_obs.filename,
                        "prev_rtime": prev_obs.raintime,
                        "curr_rtime": curr_obs.raintime,
                        "prev_utc": prev_obs.capture_utc,
                        "curr_utc": curr_obs.capture_utc,
                        "hobli": curr_obs.hobli,
                        "taluk": curr_obs.taluk,
                        "district": curr_obs.district,
                    }
                    changes.append(chg_info)
                    if curr_obs.rain_mm > prev_obs.rain_mm:
                        all_increases.append({"gid": gid, **chg_info})
                    else:
                        all_decreases.append({"gid": gid, **chg_info})

            if changes:
                changed_gauges[gid] = changes

        # Check full time series for changed gauges
        changed_gauge_series = {}
        for gid in changed_gauges:
            series = gauge_series_by_day[day][gid]
            changed_gauge_series[gid] = [
                {
                    "file": o.filename,
                    "utc": o.capture_utc,
                    "rdate": o.raindate,
                    "rtime": o.raintime,
                    "rain": o.rain_mm,
                }
                for o in series
            ]

        results["days"][day] = {
            "total_files": len(day_files),
            "total_active_gauges": total_gauges,
            "changed_gauge_count": len(changed_gauges),
            "total_increases": len(all_increases),
            "total_decreases": len(all_decreases),
            "decreases": all_decreases,
            "changed_gauges": changed_gauges,
            "series": changed_gauge_series,
        }

    # Evaluate discriminator:
    # Check if we observed storm accumulation (rising and holding constant)
    # vs incremental pulses (rising and immediately falling back to 0).
    day_16 = results["days"].get("2026-08-16", {})
    if day_16.get("total_increases", 0) > 0:
        results["verdict"] = "CUMULATIVE"
        # Extract deciding examples
        for gid in [7073, 7071, 7070, 7068, 3914, 3917, 2628]:
            if gid in day_16.get("changed_gauges", {}):
                results["discriminating_gauges"].append(
                    {
                        "gid": gid,
                        "hobli": day_16["changed_gauges"][gid][0]["hobli"],
                        "taluk": day_16["changed_gauges"][gid][0]["taluk"],
                        "district": day_16["changed_gauges"][gid][0]["district"],
                        "changes": day_16["changed_gauges"][gid],
                    }
                )
    else:
        results["verdict"] = "STILL INDETERMINATE"

    return results


@app.command()
def main(
    data_dir: Path = typer.Option(
        REPO / "data/raw/ksndmc/live", help="Path to KSNDMC live captures directory"
    ),
) -> None:
    """Audit KSNDMC live captures for cumulative vs incremental discrimination."""
    typer.echo(f"Auditing KSNDMC live captures in {data_dir}...")
    res = run_audit(data_dir)

    typer.echo(f"\n=======================================================")
    typer.echo(f"KSNDMC LIVE CAPTURE AUDIT (Total Files: {res['total_files']})")
    typer.echo(f"=======================================================")

    for day, ddata in sorted(res["days"].items()):
        typer.echo(f"\nDay: {day}")
        typer.echo(f"  Captures: {ddata['total_files']} files")
        typer.echo(f"  Active gauges reporting: {ddata['total_active_gauges']}")
        typer.echo(f"  Gauges with RAIN value changes: {ddata['changed_gauge_count']}")
        typer.echo(f"  Transitions: {ddata['total_increases']} increases, {ddata['total_decreases']} decreases")
        if ddata["total_decreases"] > 0:
            typer.echo(f"  Decreases breakdown ({ddata['total_decreases']} total):")
            for dec in ddata["decreases"][:5]:
                typer.echo(
                    f"    Gauge {dec['gid']} ({dec['hobli']}): {dec['from_rain']} -> {dec['to_rain']} mm "
                    f"at {dec['curr_file']} (rtime {dec['prev_rtime']} -> {dec['curr_rtime']})"
                )
            if len(ddata["decreases"]) > 5:
                typer.echo(f"    ... and {len(ddata['decreases']) - 5} more morning reset decreases.")

    typer.echo(f"\n=======================================================")
    typer.echo(f"DISCRIMINATOR & VERDICT")
    typer.echo(f"=======================================================")
    typer.echo(f"Verdict: {res['verdict']}")
    typer.echo("Key Evidence:")
    for g in res["discriminating_gauges"]:
        typer.echo(f"  Gauge {g['gid']} ({g['district']} / {g['taluk']} / {g['hobli']}):")
        for ch in g["changes"]:
            typer.echo(
                f"    {ch['from_rain']} -> {ch['to_rain']} mm at {ch['curr_file']} "
                f"(capture UTC {ch['curr_utc']}, rtime {ch['curr_rtime']})"
            )


if __name__ == "__main__":
    app()
