"""Underpass invert derivation — goal Part 2.

    invert_z = z_deck - clearance - deck_structural_depth

  z_deck                 measured, register z_max_m                    [FINDING]
  clearance              OSM maxheight where present, IRC minimum otherwise
                                                                       [FINDING]/[DERIVED]
  deck_structural_depth  stated assumption, band, not a point           [DERIVED]

Every output row records the formula, both input values, both sources, and a
confidence class. Written to a NEW file (`underpass_invert_derivation.csv`);
the register (`unrepresentative_underpass_register.csv`) is never touched —
its value is that it contains zero invented depths.

SOURCE NOTES (cited):

- IRC:54 (1974) "Standard for Lateral and Vertical Clearances at Underpasses
  for Vehicular Traffic", clause 8 VERTICAL CLEARANCE: "Vertical clearance at
  underpasses shall be at least 5 metres. However, in urban areas, this
  should be increased to 5.50 metres so that double-decker buses could be
  accommodated." Bengaluru is urban -> 5.50 m fallback.

- IRC:5 (2015) "Standard Specifications and Code of Practice for Road
  Bridges, Section I", clause 104.4.2.1 (Clearance over roads): "The minimum
  vertical clearance of any structure provided over the project road shall be
  5.0 m for non-urban areas and 5.5 m in case of urban areas." -> 5.50 m
  corroborates IRC:54 for the deck-over-road case.

- Railway over-bridge standard: IRC:5 clause 104.4.3 defers ROB clearances to
  the Railway authority; Indian Railways Schedule of Dimensions (1676 mm BG,
  rev. 2004, ACS No.7) sets heavy overhead structures (ROB) at >= 5870 mm
  above RAIL level — that dimension governs the railway's own envelope, not
  the clearance a road under a railway bridge needs. For the road below a
  railway crossing the underpass rule (IRC:54 clause 8) applies: 5.50 m urban.

HANDLING OF THE TWO KNOWN BIASES (goal Part 1.4):

- maxheight is a SIGNED limit, normally LESS than true clearance. A signed
  limit guarantees the real clearance is >= the tag value, so using the tag
  as clearance gives an invert that is >= the true invert (shallow bias).
  The bias is recorded per row as `signed_limit_bias` and the derived
  clearance is reported AS the tag value (never silently inflated).

- deck_structural_depth is unknown for every segment. It is carried as an
  explicit band [1.0, 2.0] m with operating point 1.5 m — stated assumption,
  [DERIVED], NOT a measurement. Rationale for the band: prestressed concrete
  box-girder flyover decks on ~25-40 m spans (IRC:18 span/depth ~ 22-25)
  give ~1.0-1.4 m; T-beam decks ~1.5-1.8 m; ballasted steel girder railway
  bridges (girder + ballast + rails) ~1.8-2.4 m. The band is deliberately
  wider than the spread of these archetypes because no measured deck depth
  exists in this repo; a future measured value replaces it.

CONFIDENCE CLASSES (stated before derivation):

  HIGH   - OSM clearance tag parsed AND register dem_behavior in
           {CREST_OR_DECK_VISIBLE, SAG_DETECTED}: z_max_m is the deck the
           satellite saw.
  MEDIUM - OSM clearance tag parsed AND dem_behavior in
           {MONOTONIC_SLOPE, FLAT_RUNTHROUGH}: z_max_m may not be a deck.
  LOW    - no OSM clearance tag; IRC 5.50 m fallback used. [DERIVED]

SANITY CHECKS (each reported per row):

  S1 invert_above_approach: invert_z_operating > min(approach elevations)
     -> the derivation failed there (a drain that is not a dip).
  S2 implied_dip_depth_outside_range: dip = min(approach) - invert_z_operating
     outside the stated plausible range [0.5, 8.0] m. Below 0.5 m is not a
     bathtub; above 8.0 m is not an urban underpass.
  S3 signed_limit_flag: row used a signed limit as clearance (bias noted).

GT-point comparison (goal Part 2, last check): for each SUB-GRID ground-truth
point, the nearest segment's derived invert and implied dip depth are
reported against the point's observed depth band. GT_06 Panathur: DEM shows a
9.3 cm dip against a 1.20-1.45 m observed flood — the derived dip must be
consistent with the observed band or the derivation fails at the point where
it can be tested.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
import rasterio
import typer
from shapely.geometry import Point

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

IRC_URBAN_CLEARANCE_M = 5.50  # IRC:54 (1974) clause 8; IRC:5 (2015) 104.4.2.1
IRC_URBAN_CLEARANCE_CITATION = (
    "IRC:54 (1974) clause 8 (urban 5.50 m; corroborated by IRC:5 (2015) "
    "clause 104.4.2.1: 5.5 m urban over project roads)"
)
DECK_DEPTH_BAND_M = (1.0, 2.0)  # [DERIVED] stated assumption, see module docstring
DECK_DEPTH_OPERATING_M = 1.5
DIP_PLAUSIBLE_RANGE_M = (0.5, 8.0)  # stated sanity band
DECKY_BEHAVIORS = {"CREST_OR_DECK_VISIBLE", "SAG_DETECTED"}
MAX_REACH_M = 250.0  # register segments longer than this are road lines, not underpass reaches


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def derive_inverts(
    register_csv: Path,
    clearance_csv: Path,
    register_gpkg: Path,
    bridges_gpkg: Path | None,
    dem_path: Path,
    gt_csv: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Derive invert bands for every register segment with a usable deck elevation.

    z_deck is NOT taken blindly from `z_max_m`. The register's osm geometry is
    the ROAD line; segments can be kilometres long (measured: 11,339 m at
    GT_05 Silk Board, 2,126 m at GT_19 Munnekollal), and on such segments
    `z_max_m` is the highest point of a long road, not the deck. Pre-check
    (2026-08-18, before derivation): those two segments alone yield
    `invert_above_approach` (dip -39 m / -14 m) — a formula artifact, not a
    mechanism. Handling:

      * segment length <= MAX_REACH_M: z_deck = z_max_m on the reach
        (deck top / dip rim — the same road surface; the formula's dip
        depth = clearance + deck_depth is independent of the DEM's own dip).
      * longer segments: z_deck = DEM max in a window around the bridge
        crossing (bridges_gpkg intersection); if no bridge crossing exists,
        the row is EXCLUDED with `exclude_reason=long_segment_no_crossing`
        and reported — never silently dropped.
    """
    register = pd.read_csv(register_csv, low_memory=False)
    register["osm_id"] = register["osm_id"].astype(str)

    clearance = pd.read_csv(clearance_csv, low_memory=False)
    clearance["osm_id"] = clearance["osm_id"].astype(str)
    clr_by_id = clearance.set_index("osm_id")

    reg_gdf = gpd.read_file(register_gpkg)
    reg_gdf["osm_id"] = reg_gdf["osm_id"].astype(str)

    bridges_gdf = gpd.read_file(bridges_gpkg) if bridges_gpkg is not None else None

    with rasterio.open(dem_path) as src:
        dem = src.read(1)
        transform = src.transform

    trans_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)

    def elev_at(x: float, y: float) -> float:
        col = int((x - transform.c) / transform.a)
        row = int((transform.f - y) / (-transform.e))
        if 0 <= row < dem.shape[0] and 0 <= col < dem.shape[1]:
            return float(dem[row, col])
        return np.nan

    def deck_max_near(x: float, y: float, radius_m: float = 50.0) -> float:
        """Max DEM elevation in a radius around a point (the deck ridge)."""
        half = int(round(radius_m / 10.0))
        col = int((x - transform.c) / transform.a)
        row = int((transform.f - y) / (-transform.e))
        r0, r1 = max(0, row - half), min(dem.shape[0], row + half + 1)
        c0, c1 = max(0, col - half), min(dem.shape[1], col + half + 1)
        return float(np.nanmax(dem[r0:r1, c0:c1]))

    gt_df = pd.read_csv(gt_csv)
    gt_df["depth_band_low_m"] = pd.to_numeric(gt_df["depth_band_low_m"], errors="coerce")
    gt_df["depth_band_high_m"] = pd.to_numeric(gt_df["depth_band_high_m"], errors="coerce")

    rows: list[dict[str, Any]] = []
    n_high = n_medium = n_low = 0
    n_excluded_long_no_crossing = 0
    for _, seg in reg_gdf.iterrows():
        osm_id = str(seg["osm_id"])
        if osm_id not in clr_by_id.index:
            continue
        clr = clr_by_id.loc[osm_id]
        clr_m = float(clr["clearance_m_parsed"]) if pd.notna(clr["clearance_m_parsed"]) else np.nan
        clr_src = str(clr.get("clearance_tag_source", ""))
        if pd.notna(clr_m) and clr_m > 0:
            clearance_m = clr_m
            clearance_source = f"OSM {clr_src}"
            signed_bias = clr_src in ("maxheight", "maxheight:signed")
            has_osm = True
        else:
            clearance_m = IRC_URBAN_CLEARANCE_M
            clearance_source = IRC_URBAN_CLEARANCE_CITATION
            signed_bias = False
            has_osm = False

        behavior = str(seg.get("dem_behavior", ""))
        geom = seg.geometry
        if geom.geom_type == "MultiLineString":
            seg_len = sum(p.length for p in geom.geoms)
        else:
            seg_len = float(geom.length)

        z_deck = float(seg["z_max_m"])
        crossing_used = False
        if seg_len > MAX_REACH_M:
            # long road line: locate the deck via the bridge crossing
            if bridges_gdf is not None and len(bridges_gdf):
                inter = gpd.overlay(
                    gpd.GeoDataFrame(geometry=[geom], crs=reg_gdf.crs),
                    bridges_gdf,
                    how="intersection",
                )
                if len(inter):
                    pts = inter.union_all()
                    if pts.geom_type == "Point":
                        z_deck = deck_max_near(pts.x, pts.y)
                        crossing_used = True
                    else:
                        parts = pts.geoms if hasattr(pts, "geoms") else [pts]
                        reps = [p.centroid for p in parts]
                        z_deck = max(deck_max_near(p.x, p.y) for p in reps)
                        crossing_used = True
            if not crossing_used:
                n_excluded_long_no_crossing += 1
                rows.append(
                    {
                        "osm_id": osm_id,
                        "name": str(seg.get("name", "")),
                        "highway": str(seg.get("highway", "")),
                        "dem_behavior": behavior,
                        "segment_length_m": round(seg_len, 1),
                        "exclude_reason": "long_segment_no_crossing",
                        "confidence": "EXCLUDED",
                    }
                )
                continue

        if z_deck != z_deck:  # NaN
            continue
        if behavior in DECKY_BEHAVIORS:
            confidence = "HIGH" if has_osm else "LOW"
        else:
            confidence = "MEDIUM" if has_osm else "LOW"
        if confidence == "HIGH":
            n_high += 1
        elif confidence == "MEDIUM":
            n_medium += 1
        else:
            n_low += 1

        invert_low = z_deck - clearance_m - DECK_DEPTH_BAND_M[1]
        invert_high = z_deck - clearance_m - DECK_DEPTH_BAND_M[0]
        invert_op = z_deck - clearance_m - DECK_DEPTH_OPERATING_M

        geom = seg.geometry
        if geom.geom_type == "MultiLineString":
            z_a = elev_at(*geom.geoms[0].coords[0])
            z_b = elev_at(*geom.geoms[-1].coords[-1])
        else:
            z_a = elev_at(*geom.coords[0])
            z_b = elev_at(*geom.coords[-1])
        z_approach = (
            float(np.nanmin([z_a, z_b])) if not (np.isnan(z_a) and np.isnan(z_b)) else np.nan
        )
        dip_depth = (z_approach - invert_op) if np.isfinite(z_approach) else np.nan

        s1 = bool(np.isfinite(z_approach) and invert_op > z_approach)
        s2 = bool(
            np.isfinite(dip_depth)
            and not (DIP_PLAUSIBLE_RANGE_M[0] <= dip_depth <= DIP_PLAUSIBLE_RANGE_M[1])
        )
        s3 = signed_bias

        rows.append(
            {
                "osm_id": osm_id,
                "name": str(seg.get("name", "")),
                "highway": str(seg.get("highway", "")),
                "dem_behavior": behavior,
                "segment_length_m": round(seg_len, 1),
                "z_deck_m": round(z_deck, 3),
                "z_deck_source": (
                    "DEM max in 50 m radius of bridge crossing ([FINDING])"
                    if crossing_used
                    else "register z_max_m (DEM, [FINDING])"
                ),
                "clearance_m": round(clearance_m, 3),
                "clearance_source": clearance_source,
                "clearance_is_signed_limit": signed_bias,
                "deck_structural_depth_band_m": f"[{DECK_DEPTH_BAND_M[0]}, {DECK_DEPTH_BAND_M[1]}]",
                "deck_structural_depth_source": "stated assumption [DERIVED], see module docstring",
                "invert_z_low_m": round(invert_low, 3),
                "invert_z_high_m": round(invert_high, 3),
                "invert_z_operating_m": round(invert_op, 3),
                "formula": "invert_z = z_deck - clearance - deck_structural_depth",
                "approach_z_min_m": round(z_approach, 3) if np.isfinite(z_approach) else np.nan,
                "implied_dip_depth_m": round(dip_depth, 3) if np.isfinite(dip_depth) else np.nan,
                "confidence": confidence,
                "sanity_invert_above_approach": s1,
                "sanity_dip_depth_outside_range": s2,
                "sanity_uses_signed_limit": s3,
                "passes_sanity": bool(not s1 and not s2),
                "exclude_reason": "",
            }
        )

    out = pd.DataFrame(rows)

    # GT-point comparison: nearest segment per SUB-GRID-relevant point
    gt_pts = []
    for _, r in gt_df.iterrows():
        x, y = trans_utm.transform(float(r["lon"]), float(r["lat"]))
        gt_pts.append(
            {
                "id": str(r["id"]),
                "name": str(r["location_name"]),
                "geometry": Point(x, y),
                "band_low": (
                    float(r["depth_band_low_m"]) if pd.notna(r["depth_band_low_m"]) else np.nan
                ),
                "band_high": (
                    float(r["depth_band_high_m"]) if pd.notna(r["depth_band_high_m"]) else np.nan
                ),
            }
        )
    gt_gdf = gpd.GeoDataFrame(gt_pts, crs="EPSG:32643")

    gt_comparisons = []
    derived_idx = reg_gdf.index[reg_gdf["osm_id"].isin(out["osm_id"])]
    for _, gp in gt_gdf.iterrows():
        dists = reg_gdf.distance(gp.geometry)
        nb_idx = int(dists.idxmin())
        nb_osm = str(reg_gdf.iloc[nb_idx]["osm_id"])
        nb_dist = float(dists.min())
        derived = out[out["osm_id"] == nb_osm]
        drow = derived.iloc[0].to_dict() if len(derived) else {}

        # fallback: nearest DERIVED segment within 500 m (a long road line may
        # be the nearest segment while the underpass reach is a different id)
        fb_dist = np.inf
        fb_row = {}
        for i in derived_idx:
            d = float(reg_gdf.geometry.iloc[i].distance(gp.geometry))
            if d < fb_dist:
                fb_dist = d
                fb_row = out[out["osm_id"] == str(reg_gdf.iloc[i]["osm_id"])].iloc[0].to_dict()
        use_fb = fb_dist <= 500.0 and fb_row and (not drow or fb_dist < nb_dist)
        if use_fb:
            nb_osm, nb_dist = str(fb_row["osm_id"]), fb_dist
            drow = fb_row

        dip = drow.get("implied_dip_depth_m", np.nan)
        gt_comparisons.append(
            {
                "gt_id": gp["id"],
                "gt_name": gp["name"],
                "observed_band_m": (
                    f"[{gp['band_low']:.2f}, {gp['band_high']:.2f}]"
                    if np.isfinite(gp["band_low"])
                    else "extent-only"
                ),
                "nearest_segment_osm_id": nb_osm,
                "nearest_segment_dist_m": round(nb_dist, 2),
                "nearest_segment_behavior": str(reg_gdf.iloc[nb_idx].get("dem_behavior", "")),
                "derived_invert_operating_m": drow.get("invert_z_operating_m", np.nan),
                "derived_implied_dip_depth_m": dip,
                "derived_confidence": drow.get("confidence", "NO_DERIVATION"),
                "derived_passes_sanity": drow.get("passes_sanity", False),
                "derived_exclude_reason": drow.get("exclude_reason", ""),
                "dip_covers_band_low": bool(
                    np.isfinite(dip) and np.isfinite(gp["band_low"]) and dip >= gp["band_low"]
                ),
            }
        )

    summary = {
        "n_register_rows": int(len(register)),
        "n_derived_rows": int(len(out)),
        "n_excluded_long_no_crossing": n_excluded_long_no_crossing,
        "confidence_counts": out["confidence"].value_counts().to_dict(),
        "n_sanity_invert_above_approach": int(out["sanity_invert_above_approach"].sum()),
        "n_sanity_dip_outside_range": int(out["sanity_dip_depth_outside_range"].sum()),
        "n_passes_sanity": int(out["passes_sanity"].sum()),
        "irc_fallback_used_rows": int((out["confidence"] == "LOW").sum()),
        "gt_comparisons": gt_comparisons,
    }
    return out, summary


@app.command()
def main(
    analysis_config: Path = typer.Option(
        REPO / "configs/analysis.yaml", help="Analysis config (DEM/GT paths)"
    ),
    out_dir: Path = typer.Option(
        REPO / "data/interim/terrain", help="Output directory (new files only)"
    ),
) -> None:
    """Derive underpass inverts; write NEW csv + GT comparison; log run manifest."""
    import yaml

    paths = yaml.safe_load(analysis_config.read_text())["paths"]
    register_csv = out_dir / "unrepresentative_underpass_register.csv"
    clearance_csv = out_dir / "underpass_register_clearance.csv"
    register_gpkg = out_dir / "unrepresentative_underpass_register.gpkg"
    bridges_gpkg = out_dir / "underpass_bridges.gpkg"
    dem_path = REPO / paths["conditioned_dem"]
    gt_csv = REPO / paths["groundtruth_csv"]

    for p in [register_csv, clearance_csv, register_gpkg, dem_path, gt_csv]:
        if not p.exists():
            raise FileNotFoundError(f"missing input: {p}")

    t0 = time.perf_counter()
    runs_dir = REPO / "runs" / "underpass_invert"
    runs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = runs_dir / "manifest.json"
    manifest: dict[str, Any] = {
        "stage": "underpass_invert_derivation",
        "status": "running",
        "git_sha": git_sha(),
        "start_time_iso": datetime.now(UTC).isoformat(),
        "formula": "invert_z = z_deck - clearance - deck_structural_depth",
        "irc_fallback_m": IRC_URBAN_CLEARANCE_M,
        "irc_fallback_citation": IRC_URBAN_CLEARANCE_CITATION,
        "deck_depth_band_m": list(DECK_DEPTH_BAND_M),
        "deck_depth_operating_m": DECK_DEPTH_OPERATING_M,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        bridges = bridges_gpkg if bridges_gpkg.exists() else None
        out, summary = derive_inverts(
            register_csv, clearance_csv, register_gpkg, bridges, dem_path, gt_csv
        )
    except Exception:
        manifest["status"] = "failed"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise

    out_csv = out_dir / "underpass_invert_derivation.csv"
    out.to_csv(out_csv, index=False)
    gt_csv_out = runs_dir / "gt_invert_comparison.csv"
    pd.DataFrame(summary["gt_comparisons"]).to_csv(gt_csv_out, index=False)

    wall_clock = time.perf_counter() - t0
    manifest.update(
        {
            "status": "completed",
            "end_time_iso": datetime.now(UTC).isoformat(),
            "wall_clock_sec": round(wall_clock, 2),
            "summary": summary,
            "output_csv": str(out_csv.relative_to(REPO)),
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2))

    typer.echo("=" * 80)
    typer.echo("UNDERPASS INVERT DERIVATION")
    typer.echo(f"Formula: {manifest['formula']}")
    typer.echo(f"IRC fallback: {IRC_URBAN_CLEARANCE_M} m — {IRC_URBAN_CLEARANCE_CITATION}")
    typer.echo(
        f"deck depth band: {DECK_DEPTH_BAND_M} m "
        f"(operating {DECK_DEPTH_OPERATING_M} m) [DERIVED]"
    )
    typer.echo("=" * 80)
    typer.echo(f"Derived rows: {len(out)}  confidence: {summary['confidence_counts']}")
    typer.echo(
        f"Sanity: invert-above-approach {summary['n_sanity_invert_above_approach']}, "
        f"dip-out-of-range {summary['n_sanity_dip_outside_range']}, "
        f"passes {summary['n_passes_sanity']}"
    )
    typer.echo("")
    typer.echo("GT-POINT COMPARISON (nearest segment):")
    for c in summary["gt_comparisons"]:
        typer.echo(
            f"  {c['gt_id']:6} {c['gt_name'][:34]:34} band={c['observed_band_m']:14} "
            f"seg={c['nearest_segment_osm_id']} ({c['nearest_segment_dist_m']:.1f} m, "
            f"{c['nearest_segment_behavior']}) dip={c['derived_implied_dip_depth_m']} m "
            f"conf={c['derived_confidence']} "
            f"dip_covers_band_low={c['dip_covers_band_low']} excl={c['derived_exclude_reason']}"
        )
    typer.echo(f"Wrote {out_csv}")
    typer.echo(f"Wrote {gt_csv_out}")
    typer.echo(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    app()
