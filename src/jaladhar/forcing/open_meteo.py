"""Open-Meteo Forecast Rainfall Forcing Adapter.

Adapts Open-Meteo public NWP forecast API to the canonical domain grid.
Each forecast interval represents 1-hour incremental precipitation (mm).

HONESTY & PROVISIONALITY:
Forecast precipitation from NWP is city-wide / regional resolution.
A single forecast grid point yields 1 distinct native cell across the 10 m grid.
Never blended with routing lead time or historical observations.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests
import typer
import yaml

from src.jaladhar.forcing.interface import (
    ForcingMode,
    RainfallAdapter,
    RainfallEvent,
    RainfallInterval,
)
from src.jaladhar.terrain.grid import Grid, build_grid

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def resolve_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate all required forecast config keys at startup."""
    missing: list[str] = []
    for key in ["domain_config", "mode", "forecast", "output"]:
        if key not in cfg:
            missing.append(key)

    fc = cfg.get("forecast", {})
    for k in ["endpoint", "latitude", "longitude", "forecast_days", "hourly_variables"]:
        if k not in fc:
            missing.append(f"forecast.{k}")

    if missing:
        raise KeyError(
            f"Missing required config keys in forcing config: {missing}. "
            "Resolve all keys before execution."
        )

    mode = cfg["mode"]
    if mode != "forecast":
        raise ValueError(
            f"Open-Meteo adapter expected mode 'forecast', got '{mode}'. "
            "Modes are mutually exclusive and must not be blended."
        )

    return cfg


def fetch_open_meteo_forecast(
    endpoint: str,
    lat: float,
    lon: float,
    forecast_days: int,
    hourly_vars: list[str],
    tz: str = "Asia/Kolkata",
    timeout_s: int = 15,
) -> dict[str, Any]:
    """Fetch forecast from Open-Meteo API. Raises requests.HTTPError on failure."""
    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "hourly": hourly_vars,
        "timezone": tz,
        "forecast_days": forecast_days,
    }
    r = requests.get(endpoint, params=params, timeout=timeout_s)
    r.raise_for_status()
    return r.json()


class OpenMeteoForecastAdapter(RainfallAdapter):
    """Forecast mode rainfall adapter for Open-Meteo NWP data."""

    def __init__(
        self,
        config_path: Path = REPO / "configs/forcing.yaml",
        raw_response: dict[str, Any] | None = None,
    ):
        with open(config_path) as f:
            raw_cfg = yaml.safe_load(f)
        # Create a modified config copy if mode is different in default config
        cfg = dict(raw_cfg)
        cfg["mode"] = "forecast"
        self.config = resolve_config(cfg)
        self.config_path = config_path

        domain_cfg_path = REPO / self.config["domain_config"]
        with open(domain_cfg_path) as f:
            domain_cfg = yaml.safe_load(f)
        self.grid, _ = build_grid(domain_cfg, REPO)
        self._raw_response = raw_response

    @property
    def mode(self) -> ForcingMode:
        return ForcingMode.FORECAST

    def get_forcing(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> RainfallEvent:
        """Fetch or parse Open-Meteo forecast and map to canonical grid."""
        fc_cfg = self.config["forecast"]

        if self._raw_response is None:
            data = fetch_open_meteo_forecast(
                endpoint=fc_cfg["endpoint"],
                lat=float(fc_cfg["latitude"]),
                lon=float(fc_cfg["longitude"]),
                forecast_days=int(fc_cfg["forecast_days"]),
                hourly_vars=fc_cfg["hourly_variables"],
                tz=fc_cfg.get("timezone", "Asia/Kolkata"),
            )
        else:
            data = self._raw_response

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        precip = hourly.get("precipitation", [])

        if not times or not precip or len(times) != len(precip):
            raise ValueError(
                "Invalid Open-Meteo payload: 'time' and 'precipitation' arrays missing or mismatched."
            )

        native_cell_ids = np.zeros((self.grid.height, self.grid.width), dtype=np.int32)
        intervals: list[RainfallInterval] = []

        for t_str, pr_val in zip(times, precip, strict=True):
            # Parse ISO time
            # Open-Meteo formats as YYYY-MM-DDTHH:00
            ts = datetime.fromisoformat(t_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            if start_time is not None and ts < start_time:
                continue
            if end_time is not None and ts > end_time:
                continue

            depth_val = float(max(0.0, pr_val or 0.0))
            # Broadcast uniform depth across canonical 10 m grid
            grid_mm = np.full(
                (self.grid.height, self.grid.width),
                depth_val,
                dtype=np.float32,
            )

            intervals.append(
                RainfallInterval(
                    timestamp=ts,
                    interval_minutes=60.0,
                    rainfall_grid_mm=grid_mm,
                    native_cell_ids=native_cell_ids,
                    distinct_native_cells=1,
                    metadata={
                        "hourly_precipitation_mm": depth_val,
                        "source": "open_meteo_forecast",
                    },
                )
            )

        if not intervals:
            raise ValueError(
                "No forecast intervals found in specified time window. "
                "Per CLAUDE.md rule 1, stopping rather than fabricating forecast."
            )

        event = RainfallEvent(
            mode=self.mode,
            intervals=intervals,
            cell_resolution_m=self.grid.resolution,
            source_name="open_meteo_nwp_forecast",
            metadata={
                "elevation_m": data.get("elevation"),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "num_timesteps": len(intervals),
                "distinct_native_cells": 1,
            },
        )
        event.verify_mass_conservation()
        return event


@app.command()
def main(
    config: Path = typer.Option(REPO / "configs/forcing.yaml", help="Path to forcing config"),
    out: Path = typer.Option(REPO / "runs/forcing_forecast", help="Output directory for manifest"),
) -> None:
    """Fetch Open-Meteo forecast and verify interval mapping."""
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"

    t0 = time.perf_counter()
    manifest: dict[str, Any] = {
        "stage": "forcing_open_meteo_forecast",
        "status": "running",
        "git_sha": git_sha(),
        "config_path": str(config),
        "start_time_iso": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        adapter = OpenMeteoForecastAdapter(config)
        event = adapter.get_forcing()
        event.verify_mass_conservation()

        wall_clock = time.perf_counter() - t0
        manifest.update(
            {
                "status": "completed",
                "wall_clock_sec": wall_clock,
                "num_intervals": event.num_intervals,
                "duration_hours": event.total_duration_hours,
                "total_volume_m3": event.total_volume_m3,
                "areal_mean_depth_mm": event.areal_mean_total_mm,
                "peak_hourly_rate_mm_hr": event.peak_hourly_rate_mm_hr,
                "distinct_native_cells": event.distinct_native_cells,
                "mass_conservation_verified": True,
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))

        typer.echo(f"Open-Meteo Forecast Adapter Execution Summary:")
        typer.echo(f"  Forecast intervals: {event.num_intervals} (hours)")
        typer.echo(f"  Duration: {event.total_duration_hours:.1f} hours")
        typer.echo(f"  Areal-Mean Total Depth: {event.areal_mean_total_mm:.2f} mm")
        typer.echo(f"  Peak Hourly Rate: {event.peak_hourly_rate_mm_hr:.2f} mm/hr")
        typer.echo(f"  Total Domain Forecast Volume: {event.total_volume_m3:,.1f} m^3")
        typer.echo(f"  Mass balance check: PASS")
        typer.echo(f"\nWrote manifest to {manifest_path}")

    except Exception as e:
        manifest.update(
            {
                "status": "failed",
                "error": str(e),
                "wall_clock_sec": time.perf_counter() - t0,
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise


if __name__ == "__main__":
    app()
