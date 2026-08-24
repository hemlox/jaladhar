"""Orchestrate the augmented underpass carve: derive_inverts + carve_corridors.

Imports the pipeline functions (src/jaladhar is read-only; nothing here edits
the pipeline files). Reads a NEW clearance CSV (existing register rows + new
measured-clearance rows, each carrying quote/source/URL) and carves the
augmented passable set from the PARENT conditioned DEM into a NEW variant
raster under runs/underpass_measured/, with its own manifest.

Provenance chain (rule 6: manifest written at start, updated in place):
  parent DEM, augmented clearance CSV (per-segment clearance + source + URL),
  derived invert CSV, variant raster, git SHA.

Repro: PYTHONPATH=. .venv/bin/python scripts/run_underpass_measured_carve.py
      --clearance-csv <augmented.csv> --out-dir runs/underpass_measured
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml

from jaladhar.terrain.underpass_carve import carve_corridors
from jaladhar.terrain.underpass_invert import derive_inverts

REPO = Path("/home/darshil/Desktop/sih/clginternal")
TERRAIN = REPO / "data/interim/terrain"
VARIANT_NAME = "dem_conditioned_postbreach_variant_measured.tif"
PIPELINE_VARIANT_NAME = "dem_conditioned_postbreach_variant_carve.tif"  # carve_corridors constant

app = typer.Typer(add_completion=False)


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


@app.command()
def main(
    clearance_csv: Path = typer.Option(
        REPO / "runs/underpass_measured/underpass_register_clearance_augmented.csv",
        help="NEW clearance CSV: existing rows + measured rows (quote/source/URL columns)",
    ),
    out_dir: Path = typer.Option(REPO / "runs/underpass_measured", help="New files only"),
    analysis_config: Path = typer.Option(REPO / "configs/analysis.yaml"),
    domain_config: Path = typer.Option(REPO / "configs/domain_bengaluru.yaml"),
) -> None:
    # ---- pre-flight: resolve every key the run will ever need (rule 7) ------
    analysis_cfg = yaml.safe_load(analysis_config.read_text())["paths"]
    domain_cfg = yaml.safe_load(domain_config.read_text())
    if "roughness" not in domain_cfg or "road_buffer_halfwidth_m" not in domain_cfg["roughness"]:
        raise KeyError("domain_bengaluru.yaml: roughness.road_buffer_halfwidth_m missing")

    register_csv = TERRAIN / "unrepresentative_underpass_register.csv"
    register_gpkg = TERRAIN / "unrepresentative_underpass_register.gpkg"
    bridges_gpkg = TERRAIN / "underpass_bridges.gpkg"
    parent_dem = REPO / analysis_cfg["conditioned_dem"]
    gt_csv = REPO / analysis_cfg["groundtruth_csv"]
    building_mask = TERRAIN / "building_mask_buffered.tif"
    basin_class = TERRAIN / "basin_class_buffered.tif"

    for p in [clearance_csv, register_csv, register_gpkg, parent_dem, gt_csv, building_mask, basin_class]:
        if not p.exists():
            raise FileNotFoundError(f"missing input: {p}")

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "carve_manifest.json"
    manifest: dict = {
        "stage": "underpass_measured_carve",
        "status": "running",
        "git_sha": git_sha(),
        "start_time_iso": datetime.now(UTC).isoformat(),
        "parent_dem": str(parent_dem.relative_to(REPO)),
        "augmented_clearance_csv": str(clearance_csv.relative_to(REPO)),
        "variant_name": VARIANT_NAME,
        "inputs": {
            "register_csv": str(register_csv.relative_to(REPO)),
            "register_gpkg": str(register_gpkg.relative_to(REPO)),
            "bridges_gpkg": str(bridges_gpkg.relative_to(REPO)),
            "gt_csv": str(gt_csv.relative_to(REPO)),
            "building_mask": str(building_mask.relative_to(REPO)),
            "basin_class": str(basin_class.relative_to(REPO)),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    t0 = time.perf_counter()
    try:
        # ---- derive (in memory; CSV written to out_dir for provenance) -----
        derived, summary = derive_inverts(
            register_csv, clearance_csv, register_gpkg, bridges_gpkg, parent_dem, gt_csv
        )
        derived_csv = out_dir / "underpass_invert_derivation_measured.csv"
        derived.to_csv(derived_csv, index=False)

        # ---- carve from the parent DEM into a NEW variant (out_dir) ---------
        report = carve_corridors(
            domain_cfg, derived_csv, register_gpkg, parent_dem, building_mask, basin_class, out_dir
        )
        if (
            report["building_footprint_violations"]
            or report["registered_basin_interior_violations"]
        ):
            raise RuntimeError(
                "CARVE ABORTED: building/basin violations "
                f"b={report['building_footprint_violations']} "
                f"basin={report['registered_basin_interior_violations']}"
            )

        # carve_corridors writes the pipeline constant name; rename to ours
        pipeline_out = out_dir / PIPELINE_VARIANT_NAME
        final_out = out_dir / VARIANT_NAME
        if pipeline_out.exists():
            if final_out.exists():
                final_out.unlink()
            pipeline_out.rename(final_out)
    except Exception:
        manifest["status"] = "failed"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise

    wall = time.perf_counter() - t0
    manifest.update(
        {
            "status": "completed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": round(wall, 2),
            "derive_summary": {
                k: summary[k]
                for k in [
                    "n_derived_rows",
                    "n_excluded_long_no_crossing",
                    "n_passes_sanity",
                    "irc_fallback_used_rows",
                    "confidence_counts",
                ]
            },
            "carve_report": report,
            "variant_path": str(final_out.relative_to(REPO)),
            "derived_csv": str(derived_csv.relative_to(REPO)),
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2))

    typer.echo("=" * 80)
    typer.echo("UNDERPASS MEASURED-CLEARANCE CARVE")
    typer.echo("=" * 80)
    typer.echo(
        f"Segments carved: {report['n_segments_carved']}, skipped: {report['n_segments_skipped']}"
    )
    typer.echo(f"Cells modified: {report['cells_modified']:,}")
    typer.echo(f"Volume removed: {report['volume_removed_m3']:,.1f} m3")
    typer.echo(
        f"Violations: buildings={report['building_footprint_violations']}, "
        f"basins={report['registered_basin_interior_violations']}"
    )
    typer.echo(f"Variant: {final_out.relative_to(REPO)}")
    typer.echo(f"Manifest: {manifest_path.relative_to(REPO)}")


if __name__ == "__main__":
    app()