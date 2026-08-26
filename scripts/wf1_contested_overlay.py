"""WF-1 addendum overlay: do synthesised connectors route through the 349
retained-basin-interior cells that the 2026-08-20 conditioning guard flagged?

REALIZED-STATE HONESTY (read before citing results):
The exact 349-cell set was produced by the FAILED 2026-08-20 rebuild
(staircase best_m intermediates inside the THEN-current retained_basin_mask,
stale-filled era; runs/condinvest/manifest.json findings.staircase_inside_retained
= 349). Its defining inputs no longer exist on disk:
  - stale runs/depression_inventory/filled.tif sha256 5e38b385de1b... (now the
    FRESH ecceed21... file occupies that path),
  - the p3-condinvest worktree holding the reproduction scratch is deleted.
Recomputing the exact 349 therefore requires restoring those bytes first; this
script does NOT fabricate them (rule 1). It reports:

  A. EXACT member check - the one cell documented cell-level in
     runs/condinvest/REPORT.md section 4: intermediate (row 114, col 1347)
     reached from pit (114, 1346). Membership of connectors in {A} is EXACT.
  B. SUPERSET bound - every cell satisfying the traced signature of the 349
     ("waterway-cost cells inside storage/retained basin interiors"):
     (basin_class_buffered > 0 and != CLASS_RESIDUAL) AND within the 5 m
     footprint of a BBMP waterway line. Connector intersections with B are an
     UPPER BOUND on the true 349 intersection (any connector hitting none of B
     hits none of the 349).
  C. BLOCKED closure statement - what would restore the exact set.

Rule-6 manifest written at run start, updated in place. CPU-only.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import typer
from rasterio.features import rasterize

REPO = Path(__file__).resolve().parents[1]
RUN_DIR = REPO / "runs" / "wf1_contested_overlay"
GPKG = REPO / "runs" / "drain_graph_build" / "drain_graph.gpkg"
BASIN = REPO / "data" / "interim" / "terrain" / "basin_class_buffered.tif"
WATERWAYS = REPO / "data" / "interim" / "terrain" / "waterways_bbmp.gpkg"
CLASS_RESIDUAL = 5

app = typer.Typer()


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git() -> tuple[str, bool]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
    )
    return sha, dirty


def _rasterize_lines_to_cells(gdf: gpd.GeoDataFrame, profile_like) -> np.ndarray:
    """Burn cells whose CENTER lies within 5 m of a line (same footprint rule as
    the stitcher's reach footprints)."""
    shapes = [(g.buffer(5.0, quad_segs=8), 1) for g in gdf.geometry]
    return rasterize(
        shapes,
        out_shape=(profile_like.height, profile_like.width),
        transform=profile_like.transform,
        fill=0,
        dtype="uint8",
        default_value=1,
    ).astype(bool)


@app.command()
def main() -> None:
    t0 = time.perf_counter()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    man_path = RUN_DIR / "manifest.json"
    git_sha, git_dirty = _git()
    manifest = {
        "stage": "wf1_contested_overlay",
        "status": "running",
        "started_utc": datetime.now(UTC).isoformat(),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "inputs": {
            "drain_graph_gpkg": str(GPKG),
            "gpkg_sha256": _sha(GPKG) if GPKG.exists() else None,
            "basin_class_buffered": str(BASIN),
            "basin_sha256": _sha(BASIN) if BASIN.exists() else None,
            "waterways_bbmp": str(WATERWAYS),
            "condinvest_evidence": (
                "runs/condinvest/REPORT.md#section-4 + runs/condinvest/manifest.json"
            ),
        },
    }
    man_path.write_text(json.dumps(manifest, indent=2))

    edges = gpd.read_file(GPKG, layer="drain_edges")
    syn = edges[edges.edge_source == "synthesised"]
    obs = edges[edges.edge_source == "observed"]

    with rasterio.open(BASIN) as bsrc:
        bclass = bsrc.read(1)

        # lightweight profile holder surviving dataset close
        class _Prof:
            pass

        bprof = _Prof()
        bprof.height = bsrc.height
        bprof.width = bsrc.width
        bprof.transform = bsrc.transform
        bprof.crs = bsrc.crs
        btransform = bsrc.transform
        bcrs = bsrc.crs
        bnodata = bsrc.nodata

    # Set B: signature superset of the flagged 349 (waterway cells inside
    # retained-basin interiors under the CURRENT on-disk classification).
    ww = gpd.read_file(WATERWAYS, layer="waterways_bbmp")
    ww_cells = _rasterize_lines_to_cells(ww, bprof)
    nodata_v = bnodata if bnodata is not None else 255
    retained = (bclass > 0) & (bclass != CLASS_RESIDUAL) & (bclass != nodata_v)
    superset = retained & ww_cells
    n_superset = int(superset.sum())

    # Rasterise synthesised connector geometry with the same footprint rule,
    # on the identical lattice.
    conn_cells = _rasterize_lines_to_cells(syn, bprof)
    obs_cells = _rasterize_lines_to_cells(obs, bprof)

    hit_superset_conn = int((conn_cells & superset).sum())
    hit_superset_obs = int((obs_cells & superset).sum())

    # Which connectors (edge ids) touch the superset - bounded listing.
    idx = np.flatnonzero(conn_cells & superset)
    touched_ids: list[int] = []
    if len(idx):
        rr, cc = np.divmod(idx, bprof.width)
        # cheap attribution: buffer each candidate edge bbox test is heavy;
        # instead rasterise per-edge lazily only when superset is small enough
        # (it is not - attribute by sampling vertices instead)
        xs = float(btransform.c) + (cc + 0.5) * 10.0
        ys = float(btransform.f) - (rr + 0.5) * 10.0
        pts = gpd.GeoSeries(gpd.points_from_xy(xs, ys), crs=bcrs)
        joined = gpd.sjoin(
            gpd.GeoDataFrame(geometry=pts), syn[["edge_id", "geometry"]], predicate="within"
        )
        touched_ids = sorted(int(v) for v in joined["edge_id"].unique())

    # Set A: documented trace cell (114, 1347) - exact member of the 349.
    trace_hit_conn = bool(conn_cells[114, 1347])
    trace_hit_obs = bool(obs_cells[114, 1347])

    manifest.update(
        {
            "completed_utc": datetime.now(UTC).isoformat(),
            "wall_clock_sec": round(time.perf_counter() - t0, 2),
            "method": {
                "A_exact": "documented trace cell (114,1347) from runs/condinvest REPORT section 4",
                "B_superset": (
                    "(basin_class_buffered>0 & !=5 residual) AND within 5 m footprint of "
                    "a BBMP waterway line - the traced signature class of all 349 "
                    "('100% waterway-cost cells inside storage basins'); intersection "
                    "with B is an UPPER BOUND on intersection with the true 349"
                ),
                "C_blocked": (
                    "exact 349 enumeration BLOCKED: stale filled.tif sha 5e38b385de1b "
                    "and p3-condinvest scratch are gone from disk; restore those bytes "
                    "and re-run instrumented conditioning.py staircase to enumerate"
                ),
            },
            "results": {
                "synthesised_edges_total": int(len(syn)),
                "superset_cell_count": n_superset,
                "connector_cells_in_superset": hit_superset_conn,
                "observed_edge_cells_in_superset": hit_superset_obs,
                "distinct_connectors_touching_superset": len(touched_ids),
                "touched_edge_ids_sample": touched_ids[:25],
                "trace_cell_114_1347_connector_hit": trace_hit_conn,
                "trace_cell_114_1347_observed_hit": trace_hit_obs,
            },
            "interpretation_guard": (
                "FINDING vs READING: cell counts are measured raster facts; the claim "
                "'therefore connector X crosses one of THE 349' is only warranted for "
                "set A (exact). Set B hits are necessary-not-sufficient evidence."
            ),
            "status": "completed",
        }
    )
    man_path.write_text(json.dumps(manifest, indent=2))

    print(f"[overlay] superset cells={n_superset}")
    print(
        f"[overlay] connector cells in superset={hit_superset_conn} "
        f"(distinct connectors={len(touched_ids)})"
    )
    print(f"[overlay] observed cells in superset={hit_superset_obs}")
    print(f"[overlay] trace cell (114,1347): connector={trace_hit_conn} observed={trace_hit_obs}")
    print(f"[overlay] manifest -> {man_path}")


if __name__ == "__main__":
    app()
