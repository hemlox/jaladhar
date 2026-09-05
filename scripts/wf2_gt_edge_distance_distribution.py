#!/usr/bin/env python
"""WF-2 round 3 / milestone 2, owner ruling D-GT 2026-08-26: GT-point -> drain-EDGE
distance distribution + anti-tuning radius selection.

The ruling binds: attribute each GT point to the nearest drain EDGE within a
radius justified by drain-catchment geometry, and report that edge's DOWNSTREAM
NODE (to_node) as the responsible node — "water surcharges at a node and runs
along the reach". Before any radius is applied, the EDGE-distance distribution
is measured exactly like the node one was (round 2:
scripts/wf2_gt_distance_distribution.py -> runs/wf2_gt_distances/).

ANTI-TUNING CONTRACT (threshold-shopping banned): the radius is chosen from
GEOMETRY-ONLY anchors pre-registered in the rule-6 manifest BEFORE any match
count exists, by the fixed rule

    r* = ceil-to-10m( min( 2 x exchange.capture_radius_m ,
                           0.5 x median inter-node spacing ) )

and the match rate is reported at r* ONLY, honestly, as a FINDING. The radius
is never tuned upward to raise matches; with the known min edge-distance of
39.7 m [round-2 record], an honest r* ~= 40 m yields roughly 1-3/24 matches and
that low rate is the expected outcome, not a failure. The historical 100 m
figure is CITED from the round-2 record, never recomputed here, and no sweep
over candidate radii exists in this script.

What it measures (all from realized on-disk state, V1):
  1. The 24 GT points via the declared pointer chain
     configs/coupling.yaml diagnostics.ground_truth_manifest -> runs/groundtruth/
     manifest.json -> csv_path -> data/raw/groundtruth/sept2022_points.csv,
     reprojected EPSG:4326 -> EPSG:32643; parsed count asserted against
     manifest points_count.
  2. Nearest-edge TRUE-polyline distance per point: shapely point->LineString
     against ALL 1587 drain_edges geometries (multi-vertex, no straight-segment
     approximation); ties break to LOWEST edge_id.
  3. Distribution min/p10/p25/median/p75/p90/max over the 24 points.
  4. Radius anchors (geometry-only) + the fixed choice rule above.

Refusals (hard stops): GT parsed count != declared points_count; drain_edges
rows != adjacency edge count; layer CRS not metre-projected.

Rule 6: runs/wf2_gt_edges/manifest.json is written AT START (status "running",
git sha + dirty flag, input sha256s, PRE-REGISTERED anchor set + choice rule)
and updated IN PLACE on completion/failure — provenance that survives failure.

Scope (V7): 24 GT points x all 1587 edge polylines, planar Euclidean metres in
EPSG:32643; percentiles numpy linear interpolation over n=24; no subsampling.
This script APPLIES nothing outside its own run directory; the config edit is
a separate, consciously reviewed step.

Usage:
    .venv/bin/python scripts/wf2_gt_edge_distance_distribution.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
import typer
import yaml
from pyproj import Transformer

REPO = Path(__file__).resolve().parents[1]

# --- read-only inputs (this script owns NOTHING outside runs/wf2_gt_edges/) ---
COUPLING_YAML = REPO / "configs/coupling.yaml"
GT_MANIFEST = REPO / "runs/groundtruth/manifest.json"
GPKG = REPO / "runs/drain_graph_build/drain_graph.gpkg"
ADJACENCY = REPO / "runs/drain_graph_build/drain_graph_adjacency.json"

OUT_DIR = REPO / "runs/wf2_gt_edges"

# Round-2 record cited as HISTORICAL CONTEXT only (never recomputed here):
ROUND2_SUMMARY = REPO / "runs/wf2_gt_distances/distribution_summary.json"

CRS_FROM = "EPSG:4326"
CRS_TO = "EPSG:32643"
PERCENTILES = (10.0, 25.0, 50.0, 75.0, 90.0)

OWNER_RULING = (
    "D-GT owner ruling 2026-08-26: attribute each GT point to the NEAREST "
    "drain EDGE within a radius justified by drain-catchment geometry, and "
    "report that edge's DOWNSTREAM NODE (to_node) as the responsible node - "
    "'water surcharges at a node and runs along the reach'. Measure the "
    "edge-distance distribution exactly like the node one BEFORE applying any "
    "radius; report the match rate honestly AS A FINDING; NEVER tune the "
    "radius upward to raise matches (threshold-shopping banned)."
)


class MeasurementRefusal(RuntimeError):
    """Hard stop: a declared input or cross-check contradicts realized state."""


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        return "unknown"


def git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        )
        return bool(out.strip())
    except Exception:
        return True


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ceil_to_grid(value: float, res_m: float) -> float:
    """Round UP to the next multiple of the grid resolution (never below the landmark)."""
    return float(math.ceil(value / res_m - 1e-9) * res_m)


def crs_is_metre_projected(crs: object) -> bool:
    """True iff the CRS is projected with metre axis units (refuses geographic CRS)."""
    try:
        from pyproj import CRS as _CRS

        c = _CRS.from_user_input(crs)
        return not c.is_geographic and all(
            str(ax.unit_name).lower() in ("metre", "meter") for ax in c.axis_info
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def load_gt_points(gt_manifest_path: Path) -> tuple[list[dict], dict]:
    """Follow the declared pointer chain and reproject the points."""
    if not gt_manifest_path.exists():
        raise MeasurementRefusal(f"ground-truth manifest not found: {gt_manifest_path}")
    man = json.loads(gt_manifest_path.read_text())
    csv_declared = man.get("csv_path")
    if not csv_declared:
        raise MeasurementRefusal(f"{gt_manifest_path} lacks 'csv_path'")
    csv_path = Path(csv_declared)
    if not csv_path.is_absolute():
        csv_path = gt_manifest_path.parent / csv_path
    if not csv_path.exists():
        raise MeasurementRefusal(f"CSV declared at {csv_path} (via manifest csv_path) not found")

    transformer = Transformer.from_crs(CRS_FROM, CRS_TO, always_xy=True)
    pts: list[dict] = []
    problems: list[str] = []
    with open(csv_path, newline="") as f:
        rdr = csv.DictReader(f)
        cols = set(rdr.fieldnames or [])
        if not {"id", "lat", "lon"}.issubset(cols):
            raise MeasurementRefusal(f"{csv_path} must carry id/lat/lon; header lacks them")
        for row in rdr:
            pid = (row.get("id") or "").strip()
            if not pid:
                continue
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
                if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                    problems.append(f"{pid}: lat/lon out of range ({lat}, {lon})")
                    continue
                x, y = transformer.transform(lon, lat)
                pts.append(
                    {
                        "point_id": pid,
                        "location_name": (row.get("location_name") or "").strip(),
                        "lat": lat,
                        "lon": lon,
                        "x_m": x,
                        "y_m": y,
                    }
                )
            except (TypeError, ValueError) as exc:
                problems.append(f"{pid}: bad coordinate field ({exc})")
    if problems:
        raise MeasurementRefusal(f"{csv_path}: " + "; ".join(problems))

    declared = man.get("points_count")
    if declared is None:
        raise MeasurementRefusal(f"{gt_manifest_path} lacks 'points_count' — cannot assert")
    if int(declared) != len(pts):
        raise MeasurementRefusal(
            f"manifest declares points_count={declared} but CSV yielded {len(pts)} points"
        )
    prov = {"gt_manifest": str(gt_manifest_path), "csv_path": str(csv_path)}
    return pts, prov


def load_coupling_snapshot() -> dict:
    """Read-only snapshot of the coupling keys this measurement anchors against."""
    cfg = yaml.safe_load(COUPLING_YAML.read_text())
    snap = {
        "capture_radius_m_key": "configs/coupling.yaml exchange.capture_radius_m",
        "capture_radius_m": cfg["exchange"]["capture_radius_m"],
        "suspicious_deadend_share": cfg["diagnostics"]["suspicious_deadend_share"],
        "ground_truth_manifest_declared": cfg["diagnostics"]["ground_truth_manifest"],
    }
    declared_gt = (REPO / snap["ground_truth_manifest_declared"]).resolve()
    if declared_gt != GT_MANIFEST.resolve():
        raise MeasurementRefusal(
            f"coupling.yaml declares ground_truth_manifest={declared_gt} but this script "
            f"pins {GT_MANIFEST} — pointer chain diverged, refusing"
        )
    return snap


def load_graph() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict]:
    nodes = gpd.read_file(GPKG, layer="drain_nodes")
    edges = gpd.read_file(GPKG, layer="drain_edges")
    adj = json.loads(ADJACENCY.read_text())

    if len(edges) != len(adj["edges"]):
        raise MeasurementRefusal(
            f"drain_edges rows ({len(edges)}) != adjacency edges ({len(adj['edges'])})"
        )
    gpkg_ids = set(nodes["node_id"].astype(int))
    adj_ids = {int(k) for k in adj["nodes"]}
    if gpkg_ids != adj_ids:
        raise MeasurementRefusal(
            f"drain_nodes ids ({len(gpkg_ids)}) != adjacency.json nodes ({len(adj_ids)}); "
            "producer artefacts disagree (V8 boundary)"
        )
    for label, gdf in (("drain_nodes", nodes), ("drain_edges", edges)):
        if not crs_is_metre_projected(gdf.crs):
            raise MeasurementRefusal(
                f"gpkg layer '{label}' CRS {gdf.crs!r} is not metre-projected — distances "
                "would be degrees, refusing"
            )
    return nodes, edges, adj


# ---------------------------------------------------------------------------
# Distances
# ---------------------------------------------------------------------------


def nearest_edge_distances(pts_xy: np.ndarray, edge_geoms, edge_ids: np.ndarray):
    """Point->LineString distance against ALL edges; ties break to LOWEST edge_id.

    Edges are scanned in ascending edge_id order with strict `<`, so argmin
    lands on the first (lowest-id) edge among exact ties — same convention as
    diagnostics attribute_ground_truth's OQ1 pin on nodes.
    """
    order = np.argsort(edge_ids)  # ascending => strict-< scan keeps lowest id on ties
    # plain object array: GeometryArray rejects 2-D indexing
    geoms = np.asarray(edge_geoms, dtype=object)[order]
    ids_sorted = edge_ids[order]
    pt_geoms = shapely.points(pts_xy)
    d = np.asarray(shapely.distance(pt_geoms[:, None], geoms[None, :]), dtype=float)
    if d.shape != (len(pts_xy), len(geoms)):
        raise MeasurementRefusal(f"edge-distance matrix shape {d.shape} unexpected")
    j = d.argmin(axis=1)
    return d[np.arange(len(pts_xy)), j], ids_sorted[j]


def dist_stats(d: np.ndarray) -> dict:
    p10, p25, p50, p75, p90 = np.percentile(d, PERCENTILES)  # linear interpolation
    return {
        "n_points": int(d.size),
        "min_m": round(float(d.min()), 1),
        "p10_m": round(float(p10), 1),
        "p25_m": round(float(p25), 1),
        "median_m": round(float(p50), 1),
        "p75_m": round(float(p75), 1),
        "p90_m": round(float(p90), 1),
        "max_m": round(float(d.max()), 1),
    }


def median_internode_spacing(nodes: gpd.GeoDataFrame, edges: gpd.GeoDataFrame) -> float:
    """Median from_node->to_node Euclidean spacing over ALL 1587 edges (metres).

    Inter-node spacing = straight-line distance between the two endpoint nodes
    of each reach, joined through the drain_nodes layer's producer-recorded
    x_m/y_m. The true polyline length is >= this; the anchor deliberately uses
    the endpoint spacing (the catchment-geometry quantity the ruling names).
    """
    xy = {
        int(nid): (float(x), float(y))
        for nid, x, y in zip(nodes["node_id"], nodes["x_m"], nodes["y_m"], strict=True)
    }
    d = [
        math.hypot(xy[int(t)][0] - xy[int(f)][0], xy[int(t)][1] - xy[int(f)][1])
        for f, t in zip(edges["from_node"], edges["to_node"], strict=True)
    ]
    return float(np.median(np.asarray(d)))


# ---------------------------------------------------------------------------
# Anti-tuning radius selection (pre-registered BEFORE any match count exists)
# ---------------------------------------------------------------------------


def preregister_anchors(capture_radius_m: float, grid_res_m: float) -> dict:
    """The anchor set + choice rule, FROZEN before any GT distance is compared.

    Written into the RUNNING manifest and echoed verbatim into the summary so
    the choice rule cannot have been selected after seeing match counts.
    """
    return {
        "owner_ruling_verbatim_intent": OWNER_RULING,
        "anchors": {
            "capture_radius_m": {
                "value": capture_radius_m,
                "source": (
                    "configs/coupling.yaml exchange.capture_radius_m (inlet-capture tolerance)"
                ),
            },
            "median_internode_spacing_m": {
                "value": None,  # measured after pre-registration, rule unchanged
                "source": (
                    "runs/drain_graph_build/drain_graph.gpkg: median from_node->to_node "
                    "Euclidean spacing over all 1587 edges (drain_nodes x_m/y_m join)"
                ),
            },
            "grid_res_m": {
                "value": grid_res_m,
                "source": "producer manifest config_snapshot.grid.transform[0]",
            },
        },
        "choice_rule": (
            "r* = ceil-to-grid(min(2 x capture_radius_m, 0.5 x median_internode_spacing_m), "
            "grid_res_m) — geometry-only anchors; NO GT distribution enters the rule"
        ),
        "anti_tuning_contract": (
            "match rate reported ONLY at r*, honestly, as a FINDING; never tuned upward "
            "to raise matches; 100 m cited as historical context from the round-2 record; "
            "NO sweep implemented"
        ),
        "pre_registered_at_iso": datetime.now(UTC).isoformat(),
    }


def resolve_chosen_radius(anchors: dict) -> tuple[float, str]:
    """Apply the pre-registered rule once both geometry anchors are measured."""
    cap = float(anchors["anchors"]["capture_radius_m"]["value"])
    med = float(anchors["anchors"]["median_internode_spacing_m"]["value"])
    res = float(anchors["anchors"]["grid_res_m"]["value"])
    half = 0.5 * med
    r_star = ceil_to_grid(min(2.0 * cap, half), res)
    basis = (
        f"min(2 x capture_radius_m={2 * cap:g} m, half median internode spacing="
        f"{half:.1f} m) = {min(2.0 * cap, half):g} m, ceiled to a {res:g} m grid multiple"
    )
    return r_star, basis


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run(out_dir: Path = OUT_DIR) -> dict:
    t0 = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    coupling = load_coupling_snapshot()
    inputs = {
        str(p.relative_to(REPO)): sha256_file(p)
        for p in (COUPLING_YAML, GT_MANIFEST, GPKG, ADJACENCY)
    }
    grid_res_m = 10.0  # verified against the producer manifest below, before use
    build_man = json.loads((REPO / "runs/drain_graph_build/manifest.json").read_text())
    tr = (build_man.get("config_snapshot") or {}).get("grid", {}).get("transform") or []
    if not tr or float(tr[0]) != grid_res_m:
        raise MeasurementRefusal(f"producer grid transform[0]={tr[:1]} != assumed {grid_res_m} m")

    # ANTI-TUNING: anchors + choice rule are FROZEN INTO THE RUNNING MANIFEST
    # before any GT-point distance is computed or compared to anything.
    anchors = preregister_anchors(float(coupling["capture_radius_m"]), grid_res_m)
    manifest: dict = {
        "stage": "wf2_gt_edge_distance_distribution",
        "purpose": (
            "WF-2 round 3 M2, ruling D-GT: full GT->EDGE polyline distance distribution + "
            "anti-tuning radius selection. Chooses r* by pre-registered geometry-only rule; "
            "applies nothing outside runs/wf2_gt_edges/."
        ),
        "status": "running",
        "git_sha": git_sha(),
        "git_dirty": git_dirty(),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "start_time_iso": datetime.now(UTC).isoformat(),
        "inputs_sha256": inputs,
        "config_snapshot_readonly": coupling,
        "radius_selection_pre_registration": anchors,
        "scope_v7": (
            "24 GT points x all 1587 multi-vertex edge polylines, shapely point->LineString, "
            "planar Euclidean metres EPSG:32643; percentiles numpy-linear over n=24; ties to "
            "lowest edge_id. No sampling, no subsampling, NO radius sweep."
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        pts, gt_prov = load_gt_points(GT_MANIFEST)
        manifest["gt_pointer_chain"] = gt_prov
        nodes, edges, adj = load_graph()
        manifest["graph_realized"] = {
            "n_nodes_gpkg": int(len(nodes)),
            "n_edges_gpkg": int(len(edges)),
        }

        # measure the second anchor (rule already frozen above), then choose r*
        med_spacing = median_internode_spacing(nodes, edges)
        anchors["anchors"]["median_internode_spacing_m"]["value"] = round(med_spacing, 1)
        r_star, r_basis = resolve_chosen_radius(anchors)
        manifest["radius_selection_realized"] = {
            "chosen_radius_m": r_star,
            "basis": r_basis,
            "reasoning_chain": [
                f"anchor capture_radius_m = {coupling['capture_radius_m']:g} m "
                "(configs/coupling.yaml exchange.capture_radius_m)",
                f"anchor median internode spacing = {med_spacing:.1f} m (all 1587 edges)",
                f"2 x capture = {2 * float(coupling['capture_radius_m']):g} m; "
                f"half spacing = {med_spacing / 2:.1f} m; min = "
                f"{min(2.0 * float(coupling['capture_radius_m']), med_spacing / 2):g} m",
                f"ceil-to-{grid_res_m:g}m -> chosen r* = {r_star:g} m",
            ],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))  # in-place update, still "running"

        # --- distances: 24 x 1587 true polylines -----------------------------
        pts_xy = np.array([[p["x_m"], p["y_m"]] for p in pts])
        edge_ids = edges["edge_id"].to_numpy(np.int64)
        edge_d, ej = nearest_edge_distances(pts_xy, edges.geometry.values, edge_ids)
        stats = dist_stats(edge_d)

        # --- per-point CSV ----------------------------------------------------
        csv_path = out_dir / "gt_edge_distances.csv"
        xy = {
            int(nid): (float(x), float(y))
            for nid, x, y in zip(nodes["node_id"], nodes["x_m"], nodes["y_m"], strict=True)
        }
        # C1 fix (round-3 adjudication): endpoint columns are joined through an
        # EDGE_ID-KEYED lookup mirroring diagnostics.load_drain_edges_geoms'
        # from_by_id/to_by_id. The previous code indexed row-ordered arrays with
        # the 1-based edge_id as a POSITIONAL index -- every row showed the NEXT
        # edge's endpoints, with a latent IndexError at max(edge_id).
        fn_by_id: dict[int, int] = {}
        tn_by_id: dict[int, int] = {}
        for eid_, f_, t_ in zip(
            edges["edge_id"], edges["from_node"], edges["to_node"], strict=True
        ):
            fn_by_id[int(eid_)] = int(f_)
            tn_by_id[int(eid_)] = int(t_)
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "point_id",
                    "location_name",
                    "lat",
                    "lon",
                    "dist_m",
                    "nearest_edge_id",
                    "nearest_edge_from_node",
                    "nearest_edge_to_node",
                    "from_x_m",
                    "from_y_m",
                    "to_x_m",
                    "to_y_m",
                ]
            )
            for i, p in enumerate(pts):
                eid_i = int(ej[i])
                if eid_i not in fn_by_id or eid_i not in tn_by_id:
                    raise MeasurementRefusal(
                        f"nearest edge_id {eid_i} absent from the gpkg endpoint maps — "
                        "refusing to fabricate endpoints"
                    )
                e_from, e_to = fn_by_id[eid_i], tn_by_id[eid_i]
                fx, fy = xy[e_from]
                tx, ty = xy[e_to]
                w.writerow(
                    [
                        p["point_id"],
                        p["location_name"],
                        p["lat"],
                        p["lon"],
                        round(float(edge_d[i]), 3),
                        int(ej[i]),
                        e_from,
                        e_to,
                        fx,
                        fy,
                        tx,
                        ty,
                    ]
                )

        # --- match rate ONLY at r* (no sweep) ---------------------------------
        n_match_rstar = int((edge_d <= r_star).sum())

        historical_100m = None
        if ROUND2_SUMMARY.exists():
            r2 = json.loads(ROUND2_SUMMARY.read_text())
            historical_100m = {
                "source": str(ROUND2_SUMMARY.relative_to(REPO)),
                "figure_cited_not_recomputed": int(
                    r2["edge_vs_node"]["edge_matches_by_radius"]["100"]
                ),
                "of": stats["n_points"],
                "context": (
                    "round-2 node-mode declared radius; superseded by ruling D-GT for "
                    "attribution mode, kept ONLY as historical context"
                ),
            }

        summary = {
            "stage": "wf2_gt_edge_distance_distribution",
            "label_rules": (
                "R1: numbers below are FINDINGS (measured this run); interpretive strings "
                "are READINGS."
            ),
            "owner_ruling": OWNER_RULING,
            "distribution": stats,
            "percentile_method": "numpy.percentile linear interpolation, n=24",
            "chosen_radius_m": r_star,
            "chosen_radius_basis": r_basis,
            "anchors_pre_registered": anchors,
            "match_count_at_chosen": n_match_rstar,
            "of": stats["n_points"],
            "finding_statement": (
                (
                    f"HONEST FINDING: {n_match_rstar}/{stats['n_points']} GT points lie within "
                    f"the geometry-anchored r* = {r_star:g} m of any drain edge polyline. This "
                    "low rate is the EXPECTED outcome under the ruling's anti-tuning contract "
                    "(min edge distance "
                    f"{stats['min_m']} m); it is reported as a finding, never tuned away."
                )
                if n_match_rstar < stats["n_points"]
                else f"All {stats['n_points']} points matched at r*."
            ),
            "historical_100m_context_cited": historical_100m,
            "per_point_csv": str(csv_path.relative_to(REPO)),
        }
        summary_path = out_dir / "edge_distribution_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2))

        wall = time.perf_counter() - t0
        manifest.update(
            {
                "status": "completed",
                "wall_clock_sec": round(wall, 3),
                "outputs": {
                    "per_point_csv": str(csv_path.relative_to(REPO)),
                    "edge_distribution_summary": str(summary_path.relative_to(REPO)),
                },
                "result_headline": {
                    "distribution": stats,
                    "chosen_radius_m": r_star,
                    "match_count_at_chosen": n_match_rstar,
                },
                "completed_at_iso": datetime.now(UTC).isoformat(),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2))
    except Exception:
        manifest["status"] = "failed"
        manifest["failed_at_iso"] = datetime.now(UTC).isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2))
        raise

    print(f"[wf2-gt-edge-dist] completed in {wall:.2f}s; outputs under {out_dir.relative_to(REPO)}")
    print("  edge-distance distribution:", " ".join(f"{k}={v}" for k, v in stats.items()))
    for step in manifest["radius_selection_realized"]["reasoning_chain"]:
        print(f"  anchor-rule: {step}")
    print(
        f"  FINDING: {summary['match_count_at_chosen']}/{summary['of']} points matched "
        f"at chosen r*={r_star:g} m (honest rate; no tuning applied)"
    )
    if historical_100m:
        print(
            f"  historical context (cited, round-2 record): "
            f"{historical_100m['figure_cited_not_recomputed']}/24 at 100 m"
        )
    return summary


app = typer.Typer(add_completion=False)


@app.command()
def main(
    out_dir: Path = typer.Option(OUT_DIR, help="Owned output directory (rule-6 run dir)."),
) -> None:
    """Measure GT->drain-EDGE polyline distances; select r* by pre-registered rule."""
    run(out_dir)


if __name__ == "__main__":
    app()
