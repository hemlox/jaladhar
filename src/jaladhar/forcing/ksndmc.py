"""KSNDMC Live Telemetry Nowcast Rainfall Forcing Adapter.

Adapts 15-minute live gauge telemetry from KSNDMC to the canonical domain grid.
Converts cumulative daily gauge readings to 15-minute incremental depths (mm).

GEOLOCATION INTEGRITY (binding rule):
- CORRECT join: live HOBLINAME -> KML TM_RainGauge_LocationName with Bengaluru bbox filter.
- FORBIDDEN join: RAINGAUGE -> KGISTM_RainGauge_LocationID. It produces 178 apparent
  matches of which 100% disagree on station name. This join is explicitly forbidden.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pyproj
from scipy.spatial import cKDTree
from shapely.geometry import box
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
    """Validate all required nowcast config keys at startup."""
    missing: list[str] = []
    for key in ["domain_config", "mode", "nowcast", "output"]:
        if key not in cfg:
            missing.append(key)

    nc = cfg.get("nowcast", {})
    for k in ["source", "live_dir", "stations_kml", "bengaluru_bbox", "time_step_minutes"]:
        if k not in nc:
            missing.append(f"nowcast.{k}")

    if missing:
        raise KeyError(
            f"Missing required config keys in forcing config: {missing}. "
            "Resolve all keys before execution."
        )

    mode = cfg["mode"]
    if mode != "nowcast":
        raise ValueError(
            f"KSNDMC adapter expected mode 'nowcast', got '{mode}'. "
            "Modes are mutually exclusive and must not be blended."
        )

    return cfg


def load_and_locate_gauges(
    kml_path: Path,
    bengaluru_bbox: list[float],
) -> tuple[dict[str, tuple[float, float]], dict[str, Any]]:
    """Load KML stations and geolocate via HOBLINAME within Bengaluru bbox.

    Strictly forbids joining RAINGAUGE -> KGISTM_RainGauge_LocationID.
    """
    if not kml_path.exists():
        raise FileNotFoundError(f"KSNDMC stations KML not found at {kml_path}")

    gdf_kml = gpd.read_file(kml_path, driver="KML")
    bbox_geom = box(*bengaluru_bbox)
    gdf_sub = gdf_kml[gdf_kml.geometry.within(bbox_geom)].copy()

    # Normalize location names
    name_to_coords: dict[str, tuple[float, float]] = {}
    for _, row in gdf_sub.iterrows():
        loc_name = str(row.get("TM_RainGauge_LocationName", "")).strip().lower()
        if loc_name and loc_name not in name_to_coords:
            geom = row.geometry
            if geom and not geom.is_empty:
                name_to_coords[loc_name] = (geom.x, geom.y)

    stats = {
        "total_kml_statewide": len(gdf_kml),
        "total_in_bbox": len(gdf_sub),
        "unique_located_names": len(name_to_coords),
    }
    return name_to_coords, stats


def parse_capture_file(file_path: Path) -> list[dict[str, Any]]:
    """Parse double-encoded JSON KSNDMC capture."""
    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, list):
        return []
    return data


class KsndmcNowcastAdapter(RainfallAdapter):
    """Nowcast mode adapter for KSNDMC live gauge telemetry."""

    def __init__(self, config_path: Path = REPO / "configs/forcing.yaml"):
        with open(config_path) as f:
            raw_cfg = yaml.safe_load(f)
        cfg = dict(raw_cfg)
        cfg["mode"] = "nowcast"
        self.config = resolve_config(cfg)
        self.config_path = config_path

        domain_cfg_path = REPO / self.config["domain_config"]
        with open(domain_cfg_path) as f:
            domain_cfg = yaml.safe_load(f)
        self.grid, _ = build_grid(domain_cfg, REPO)

        nc_cfg = self.config["nowcast"]
        kml_path = REPO / nc_cfg["stations_kml"]
        self.name_to_coords, self.geo_stats = load_and_locate_gauges(
            kml_path, nc_cfg["bengaluru_bbox"]
        )

        self.live_dir = REPO / nc_cfg["live_dir"]
        self.capture_files = sorted(self.live_dir.glob("*/*.json"))

    @property
    def mode(self) -> ForcingMode:
        return ForcingMode.NOWCAST

    def build_gauge_spatial_index(
        self,
        active_gauges: list[dict[str, Any]],
    ) -> tuple[cKDTree, list[tuple[float, float]], list[int], np.ndarray]:
        """Map active gauges into UTM 43N and build spatial KDTree against canonical grid."""
        transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
        station_coords_utm = []
        station_gids = []

        for g in active_gauges:
            hobli = str(g.get("HOBLINAME", "")).strip().lower()
            if hobli in self.name_to_coords:
                lon, lat = self.name_to_coords[hobli]
                ux, uy = transformer.transform(lon, lat)
                station_coords_utm.append((ux, uy))
                station_gids.append(int(g["RAINGAUGE"]))

        if not station_coords_utm:
            raise ValueError("No active gauges could be geolocated inside domain.")

        tree = cKDTree(station_coords_utm)

        # Precompute nearest gauge index for every grid cell on canonical grid
        cols = np.arange(self.grid.width)
        rows = np.arange(self.grid.height)
        x_coords = self.grid.bounds[0] + (cols + 0.5) * self.grid.resolution
        y_coords = self.grid.bounds[3] - (rows + 0.5) * self.grid.resolution
        xx, yy = np.meshgrid(x_coords, y_coords)
        grid_pts = np.column_stack([xx.flatten(), yy.flatten()])

        _, nearest_idx_flat = tree.query(grid_pts, k=1)
        nearest_gauge_map = nearest_idx_flat.reshape((self.grid.height, self.grid.width))

        return tree, station_coords_utm, station_gids, nearest_gauge_map

    def get_forcing(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> RainfallEvent:
        """Parse KSNDMC captures, convert cumulative to incremental, and map to grid."""
        # Group captures by timestamp
        # Organize gauge series chronologically
        time_to_records: dict[datetime, dict[int, float]] = defaultdict(dict)
        gauge_metadata: dict[int, dict[str, Any]] = {}

        for f in self.capture_files:
            fname = f.name
            m = re.search(r"rain_d\d+_(\d{8}T\d{6}Z)", fname)
            if not m:
                continue
            cap_dt = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if start_time is not None and cap_dt < start_time:
                continue
            if end_time is not None and cap_dt > end_time:
                continue

            records = parse_capture_file(f)
            for r in records:
                gid = r.get("RAINGAUGE")
                if gid is None:
                    continue
                gid_int = int(gid)
                rain_val = float(r.get("RAIN", 0.0) or 0.0)
                time_to_records[cap_dt][gid_int] = rain_val
                if gid_int not in gauge_metadata:
                    gauge_metadata[gid_int] = r

        if not time_to_records:
            raise ValueError(
                "No KSNDMC captures found in requested window. "
                "Per CLAUDE.md rule 1, stopping rather than fabricating telemetry."
            )

        # Geolocate gauges
        all_active_gauges = [
            gauge_metadata[gid]
            for gid in gauge_metadata
            if str(gauge_metadata[gid].get("HOBLINAME", "")).strip().lower() in self.name_to_coords
        ]

        (
            tree,
            station_coords_utm,
            station_gids,
            nearest_gauge_map,
        ) = self.build_gauge_spatial_index(all_active_gauges)

        # Convert cumulative series to incremental steps for each gauge
        sorted_times = sorted(time_to_records.keys())
        prev_cumulative: dict[int, float] = {}
        intervals: list[RainfallInterval] = []

        distinct_native_cells = len(station_gids)

        for i, ts in enumerate(sorted_times):
            curr_readings = time_to_records[ts]
            # Compute incremental rainfall for each geolocated station
            inc_by_station = np.zeros(len(station_gids), dtype=np.float32)

            for s_idx, gid in enumerate(station_gids):
                curr_cum = curr_readings.get(gid, prev_cumulative.get(gid, 0.0))
                prev_cum = prev_cumulative.get(gid, 0.0)

                # Reset detection (e.g. morning reset 08:30 IST where cum drops to 0.0)
                if curr_cum < prev_cum:
                    delta_inc = curr_cum  # reset occurred
                else:
                    delta_inc = curr_cum - prev_cum

                inc_by_station[s_idx] = max(0.0, delta_inc)
                prev_cumulative[gid] = curr_cum

            if i == 0:
                # First capture: cumulative baseline established, interval increment is 0.0
                inc_grid = np.zeros((self.grid.height, self.grid.width), dtype=np.float32)
            else:
                # Map station increments onto canonical 10 m grid using nearest gauge Voronoi
                inc_grid = inc_by_station[nearest_gauge_map]

            intervals.append(
                RainfallInterval(
                    timestamp=ts,
                    interval_minutes=15.0,
                    rainfall_grid_mm=inc_grid,
                    native_cell_ids=nearest_gauge_map.astype(np.int32),
                    distinct_native_cells=distinct_native_cells,
                    metadata={"timestamp_utc": ts.isoformat()},
                )
            )

        event = RainfallEvent(
            mode=self.mode,
            intervals=intervals,
            cell_resolution_m=self.grid.resolution,
            source_name="ksndmc_live_telemetry",
            metadata={
                "total_gauges_geolocated": len(station_gids),
                "grid_shape": [self.grid.height, self.grid.width],
            },
        )
        event.verify_mass_conservation()
        return event


@app.command()
def main(
    config: Path = typer.Option(REPO / "configs/forcing.yaml", help="Path to forcing config"),
    start: str = typer.Option("2026-08-16T12:00:00Z", help="Start timestamp ISO UTC"),
    end: str = typer.Option("2026-08-16T22:00:00Z", help="End timestamp ISO UTC"),
    out: Path = typer.Option(REPO / "runs/forcing_nowcast", help="Output directory for manifest"),
) -> None:
    """Run KSNDMC nowcast adapter and verify incremental conversion."""
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"

    t0 = time.perf_counter()
    manifest: dict[str, Any] = {
        "stage": "forcing_ksndmc_nowcast",
        "status": "running",
        "git_sha": git_sha(),
        "config_path": str(config),
        "start_time_iso": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        adapter = KsndmcNowcastAdapter(config)
        st = datetime.fromisoformat(start.replace("Z", "+00:00"))
        et = datetime.fromisoformat(end.replace("Z", "+00:00"))

        event = adapter.get_forcing(st, et)
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

        typer.echo(f"KSNDMC Nowcast Adapter Execution Summary:")
        typer.echo(f"  Capture intervals processed: {event.num_intervals} (15-min)")
        typer.echo(f"  Duration: {event.total_duration_hours:.1f} hours")
        typer.echo(f"  Geolocated active gauges: {event.distinct_native_cells}")
        typer.echo(f"  Areal-Mean Total Depth: {event.areal_mean_total_mm:.2f} mm")
        typer.echo(f"  Peak Hourly Rate: {event.peak_hourly_rate_mm_hr:.2f} mm/hr")
        typer.echo(f"  Total Domain Volume: {event.total_volume_m3:,.1f} m^3")
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
