#!/usr/bin/env python
"""WF-2 adjudication round 2, directive D-GT: full GT-point -> drain-node distance distribution.

MEASUREMENT + PROPOSAL ONLY, for OWNER ADJUDICATION. This script applies nothing:
``configs/coupling.yaml`` ``diagnostics.attribution_radius_m`` stays 100.0; every candidate
radius emitted here is labelled PROPOSED - NOT APPLIED (R1: the radius decision is the
owner's; this run supplies the measured distribution and the vacuity consequences).

What it measures (all from realized on-disk state, V1):
  1. The 24 GT points via the declared pointer chain
     configs/coupling.yaml diagnostics.ground_truth_manifest -> runs/groundtruth/manifest.json
     -> csv_path -> data/raw/groundtruth/sept2022_points.csv (WGS84 lat/lon), reprojected
     EPSG:4326 -> EPSG:32643 with pyproj; parsed count asserted against manifest points_count.
  2. Nearest-node distance per point under THREE node denominators:
     (a) all drain_nodes (1721 expected);
     (b) capacity-bearing-connected nodes only (incident to >= 1 drain_edges row with
         non-null q_capacity_nom_m3s);
     (c) outfall-terminating nodes only (directed reachability to the outfalls over
         runs/drain_graph_build/drain_graph_adjacency.json; class recomputed here and
         cross-checked against the producer-recorded split in runs/wf2_coupled/manifest.json).
  3. Full distribution per denominator: per-point table + min/p10/p25/median/p75/p90/max.
  4. Candidate attribution radii PROPOSED - NOT APPLIED, each with match counts per
     denominator and the SUSPICIOUS dead-end-share consequence (geometric proxy: class
     homogeneity of within-radius nodes decides; mixed sets are volume-dependent).
  5. Point -> nearest EDGE (LineString) distance, to expose the structural gap between
     node-based and reach-based attribution.

Scope of every check here (V7): 24 points x {1721, n_cap, n_out} nodes, planar Euclidean
metres in EPSG:32643 over x_m/y_m as recorded in the gpkg; percentiles are numpy linear
interpolation over n=24; reachability is exact BFS over the 1587 directed adjacency edges;
edge distances are shapely point-to-LineString over all 1587 edge geometries. No sampling,
no subsampling — the whole graph, every point.

Usage:
    .venv/bin/python scripts/wf2_gt_distance_distribution.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
import typer
import yaml
from pyproj import Transformer

REPO = Path(__file__).resolve().parents[1]

# --- read-only inputs (this script owns NOTHING outside runs/wf2_gt_distances/) ----------
COUPLING_YAML = REPO / "configs/coupling.yaml"
GT_MANIFEST = REPO / "runs/groundtruth/manifest.json"
GPKG = REPO / "runs/drain_graph_build/drain_graph.gpkg"
ADJACENCY = REPO / "runs/drain_graph_build/drain_graph_adjacency.json"
DRAINAGE_YAML = REPO / "configs/drainage.yaml"
# producer-recorded component-class split used as a cross-check target (read-only):
COUPLED_MANIFEST = REPO / "runs/wf2_coupled/manifest.json"

OUT_DIR = REPO / "runs/wf2_gt_distances"

CRS_FROM = "EPSG:4326"
CRS_TO = "EPSG:32643"
GRID_RES_M = 10.0  # configs/drainage.yaml drainage.grid.transform[0]; verified at runtime
PERCENTILES = (10.0, 25.0, 50.0, 75.0, 90.0)
# Producer-documented expectation for denominator (c) (directive: "expect ~55 such nodes";
# persisted by the producer at runs/wf2_coupled/manifest.json component_class_split).
EXPECTED_OUTFALL_TERMINATING = 55
EXPECTED_DEAD_END = 1666
# Pre-stated criterion for "materially smaller" edge-vs-node distance (declared BEFORE
# measuring; one grid cell, so a sub-cell difference does not count as material):
EDGE_MATERIAL_GAP_M = GRID_RES_M

DENOMINATORS = ("all_nodes", "capacity_bearing", "outfall_terminating")


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


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def load_gt_points(gt_manifest_path: Path) -> tuple[list[dict], dict]:
    """Follow the declared pointer chain and reproject the points (directive step 1)."""
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
    """Read-only snapshot of the coupling keys this measurement is adjudicating against."""
    cfg = yaml.safe_load(COUPLING_YAML.read_text())
    snap = {
        "attribution_radius_m": cfg["diagnostics"]["attribution_radius_m"],
        "suspicious_deadend_share": cfg["diagnostics"]["suspicious_deadend_share"],
        "capture_radius_m": cfg["exchange"]["capture_radius_m"],
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

    gpkg_ids = set(nodes["node_id"].astype(int))
    adj_ids = {int(k) for k in adj["nodes"]}
    if gpkg_ids != adj_ids:
        raise MeasurementRefusal(
            f"drain_graph.gpkg drain_nodes ids ({len(gpkg_ids)}) != adjacency.json nodes "
            f"({len(adj_ids)}); sets differ by "
            f"{len(gpkg_ids ^ adj_ids)} ids — producer artefacts disagree (V8 boundary)"
        )
    if len(edges) != len(adj["edges"]):
        raise MeasurementRefusal(
            f"drain_edges rows ({len(edges)}) != adjacency edges ({len(adj['edges'])})"
        )
    return nodes, edges, adj


def capacity_bearing_node_ids(edges: gpd.GeoDataFrame) -> set[int]:
    """Denominator (b): incident to >= 1 edge with non-null q_capacity_nom_m3s."""
    cap_edges = edges[edges["q_capacity_nom_m3s"].notna()]
    ids: set[int] = set()
    for col in ("from_node", "to_node"):
        ids |= set(cap_edges[col].astype(int))
    return ids


def outfall_terminating_node_ids(
    adj: dict, outfall_ids: set[int]
) -> tuple[set[int], dict]:
    """Denominator (c): directed reachability to any outfall following edges downstream.

    Two independent implementations must agree exactly before the class is accepted:
      1. fixed-point sweep mirroring router._compute_component_classes semantics
         (reached[to] implies reached[from]);
      2. reverse-BFS from the outfall set over reversed edges.
    A disagreement means a direction-convention error, not a world fact — refuse.
    """
    edges_adj = [(int(ed["from"]), int(ed["to"])) for ed in adj["edges"].values()]

    # (1) fixed-point sweep
    reached_fp = set(outfall_ids)
    changed = True
    while changed:
        changed = False
        for f, t in edges_adj:
            if t in reached_fp and f not in reached_fp:
                reached_fp.add(f)
                changed = True

    # (2) reverse BFS
    rev: dict[int, list[int]] = defaultdict(list)
    for f, t in edges_adj:
        rev[t].append(f)
    reached_bfs = set(outfall_ids)
    stack = list(outfall_ids)
    while stack:
        t = stack.pop()
        for f in rev.get(t, []):
            if f not in reached_bfs:
                reached_bfs.add(f)
                stack.append(f)

    if reached_fp != reached_bfs:
        raise MeasurementRefusal(
            "outfall-terminating class: fixed-point sweep and reverse-BFS disagree "
            f"({len(reached_fp)} vs {len(reached_bfs)}) — direction-convention bug, refusing"
        )

    crosscheck = {
        "recomputed_outfall_terminating": len(reached_fp),
        "recomputed_dead_end": None,  # filled by caller against node count
        "expected_outfall_terminating": EXPECTED_OUTFALL_TERMINATING,
        "expected_dead_end": EXPECTED_DEAD_END,
        "producer_record_source": None,
        "producer_record_matches": None,
    }
    if COUPLED_MANIFEST.exists():
        coupled = json.loads(COUPLED_MANIFEST.read_text())
        split = coupled.get("component_class_split")
        if isinstance(split, dict):
            crosscheck["producer_record_source"] = str(COUPLED_MANIFEST)
            crosscheck["producer_record_matches"] = (
                int(split.get("outfall_terminating", -1)) == len(reached_fp)
            )
    return reached_fp, crosscheck


# ---------------------------------------------------------------------------
# Distances
# ---------------------------------------------------------------------------


def nearest_node_distances(
    pts_xy: np.ndarray, nx: np.ndarray, ny: np.ndarray, nids: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Planar Euclidean nearest-node distance + id; ties break to LOWEST node_id
    (same convention as diagnostics.attribute_ground_truth OQ1 pin) via ascending-id sort."""
    order = np.argsort(nids)  # ascending => argmin tie-break picks lowest id
    nx, ny, nids = nx[order], ny[order], nids[order]
    d = np.hypot(pts_xy[:, None, 0] - nx[None, :], pts_xy[:, None, 1] - ny[None, :])
    j = d.argmin(axis=1)
    return d[np.arange(len(pts_xy)), j], nids[j]


def nearest_edge_distances(pts_xy: np.ndarray, edge_geoms) -> tuple[np.ndarray, np.ndarray]:
    geoms = np.asarray(edge_geoms, dtype=object)  # plain object array; GeometryArray rejects 2-D indexing
    pt_geoms = shapely.points(pts_xy)
    d = np.asarray(shapely.distance(pt_geoms[:, None], geoms[None, :]), dtype=float)
    if d.shape != (len(pts_xy), len(geoms)):
        raise MeasurementRefusal(f"edge-distance matrix shape {d.shape} unexpected")
    j = d.argmin(axis=1)
    return d[np.arange(len(pts_xy)), j], j


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


# ---------------------------------------------------------------------------
# Candidates (PROPOSED - NOT APPLIED)
# ---------------------------------------------------------------------------


def suspicious_verdict(within_radius_classes: list[str]) -> tuple[str, str]:
    """Geometric proxy for the SUSPICIOUS rule (threshold from coupling.yaml, 0.5).

    The real machinery fires on the dead-end share of GT-matched RETURNED VOLUME
    (surcharge events). With no events scored here, class homogeneity of the
    within-radius node set bounds the outcome: all-dead_end => share == 1.0 for ANY
    volumes => fires; all-outfall_terminating => share == 0.0 => cannot fire; mixed =>
    volume-dependent => honestly unassessed.
    """
    if not within_radius_classes:
        return "unassessed", "no node within radius => machinery state NOT_ASSESSED"
    n_dead = sum(1 for c in within_radius_classes if c == "dead_end")
    if n_dead == len(within_radius_classes):
        return "yes", (
            f"all {len(within_radius_classes)} within-radius nodes dead_end-class => "
            "dead-end volume share would be 1.0 > threshold for ANY event volumes"
        )
    if n_dead == 0:
        return "no", (
            f"all {len(within_radius_classes)} within-radius nodes outfall_terminating => "
            "dead-end share 0.0 <= threshold"
        )
    return "unassessed", (
        f"mixed classes ({n_dead}/{len(within_radius_classes)} dead_end) => share is "
        "volume-dependent; needs surcharge events to decide"
    )


def build_candidates(
    stats_all: dict,
    per_denom: dict[str, dict],
    cls_of_node: dict[int, str],
    capture_radius_m: float,
    suspicious_threshold: float,
    grid_res_m: float,
    edge_dists: np.ndarray,
) -> list[dict]:
    landmarks = [
        (
            ceil_to_grid(stats_all["median_m"], grid_res_m),
            f"measured median of the all-nodes nearest-distance distribution "
            f"({stats_all['median_m']} m) rounded up to a {grid_res_m:g} m grid multiple",
        ),
        (
            ceil_to_grid(stats_all["p75_m"], grid_res_m),
            f"measured p75 of the all-nodes nearest-distance distribution "
            f"({stats_all['p75_m']} m) rounded up to a {grid_res_m:g} m grid multiple",
        ),
        (
            2.0 * float(capture_radius_m),
            f"drain-catchment geometry anchor, independent of the GT distribution: "
            f"2x exchange.capture_radius_m ({capture_radius_m:g} m inlet-capture pin) "
            f"~= {2.0 * capture_radius_m / (grid_res_m * math.sqrt(2)):.1f} grid-cell "
            "diagonals; tests whether any geometry-anchored radius can make NODE "
            "attribution non-vacuous",
        ),
    ]
    candidates: list[dict] = []
    for radius, basis in landmarks:
        entry: dict = {
            "label": "PROPOSED - NOT APPLIED",
            "radius_m": float(radius),
            "basis": basis,
            "suspicious_threshold_reference": suspicious_threshold,
        }
        for denom in DENOMINATORS:
            d = per_denom[denom]["dist"]
            matched = d <= radius
            n_match = int(matched.sum())
            within_classes = [
                cls_of_node[int(nid)]
                for nid in per_denom[denom]["matched_within"][radius]
            ]
            verdict, why = suspicious_verdict(within_classes)
            n_dead = sum(1 for c in within_classes if c == "dead_end")
            entry[f"matches_{denom}"] = n_match
            entry[f"suspicious_would_fire_{denom}"] = verdict
            entry[f"suspicious_basis_{denom}"] = why
            entry[f"dead_end_count_share_of_within_radius_nodes_{denom}"] = (
                round(n_dead / len(within_classes), 4) if within_classes else None
            )
        # edge-attribution alternative at the same radius (directive item 5)
        entry["matches_nearest_edge_geometry"] = int((edge_dists <= radius).sum())
        candidates.append(entry)
    return candidates


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
        for p in (COUPLING_YAML, GT_MANIFEST, GPKG, ADJACENCY, DRAINAGE_YAML)
    }
    script_sha = sha256_file(Path(__file__).resolve())
    manifest: dict = {
        "stage": "wf2_gt_distance_distribution",
        "purpose": (
            "WF-2 adjudication round 2 directive D-GT: full GT->node distance distribution "
            "under three denominators + PROPOSED-NOT-APPLIED radius candidates. Applies "
            "nothing; attribution_radius_m remains pinned at coupling.yaml value."
        ),
        "status": "running",
        "git_sha": git_sha(),
        "git_dirty": git_dirty(),
        "script_sha256": script_sha,
        "start_time_iso": datetime.now(UTC).isoformat(),
        "inputs_sha256": inputs,
        "config_snapshot_readonly": coupling,
        "scope_v7": (
            "24 GT points x full node subsets (all / capacity-bearing / outfall-terminating), "
            "planar Euclidean metres EPSG:32643 over gpkg x_m/y_m; percentiles numpy-linear "
            "over n=24; exact BFS over all directed adjacency edges; shapely point->LineString "
            "over all 1587 edge geometries. No subsampling."
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
            "n_outfalls": int((nodes["node_type"] == "outfall").sum()),
        }

        # denominators
        node_ids = nodes["node_id"].to_numpy(int)
        nx = nodes["x_m"].to_numpy(float)
        ny = nodes["y_m"].to_numpy(float)
        cap_ids = capacity_bearing_node_ids(edges)
        outfall_ids = set(nodes.loc[nodes["node_type"] == "outfall", "node_id"].astype(int))
        if len(outfall_ids) != 17:
            raise MeasurementRefusal(
                f"expected 17 outfall nodes per directive; realized {len(outfall_ids)}"
            )
        ot_ids, class_crosscheck = outfall_terminating_node_ids(adj, outfall_ids)
        class_crosscheck["recomputed_dead_end"] = len(node_ids) - len(ot_ids)
        if class_crosscheck.get("producer_record_matches") is not True:
            raise MeasurementRefusal(
                "recomputed outfall-terminating split "
                f"({len(ot_ids)}/{len(node_ids) - len(ot_ids)}) does not match the "
                f"producer-recorded component_class_split "
                f"({EXPECTED_OUTFALL_TERMINATING}/{EXPECTED_DEAD_END}) at "
                f"{COUPLED_MANIFEST} — graph version drift or convention bug, refusing"
            )
        manifest["denominators"] = {
            "all_nodes": len(node_ids),
            "capacity_bearing": len(cap_ids),
            "outfall_terminating": len(ot_ids),
            "class_crosscheck": class_crosscheck,
        }

        cls_of_node = {
            int(nid): ("outfall_terminating" if int(nid) in ot_ids else "dead_end")
            for nid in node_ids
        }
        subsets = {
            "all_nodes": np.ones(len(node_ids), dtype=bool),
            "capacity_bearing": np.isin(node_ids, sorted(cap_ids)),
            "outfall_terminating": np.isin(node_ids, sorted(ot_ids)),
        }

        pts_xy = np.array([[p["x_m"], p["y_m"]] for p in pts])
        per_denom: dict[str, dict] = {}
        for denom, mask in subsets.items():
            d, nn = nearest_node_distances(pts_xy, nx[mask], ny[mask], node_ids[mask])
            per_denom[denom] = {
                "dist": d,
                "nearest_id": nn,
                "stats": dist_stats(d),
                "matched_within": {},  # radius -> [node ids within radius], filled below
            }

        # edge distances (directive item 5)
        edge_geoms = edges.geometry.values
        edge_d, ej = nearest_edge_distances(pts_xy, edge_geoms)
        nearest_edge_meta = edges.iloc[ej][["edge_id", "order", "width_m"]].reset_index()

        # per-point CSV
        csv_path = out_dir / "gt_node_distances.csv"
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "point_id", "location_name", "lat", "lon",
                    "dist_all_m", "nearest_node_all", "class_all",
                    "dist_capacity_bearing_m", "nearest_node_capacity_bearing",
                    "class_capacity_bearing",
                    "dist_outfall_terminating_m", "nearest_node_outfall_terminating",
                    "class_outfall_terminating",
                    "nearest_edge_dist_m", "nearest_edge_id", "nearest_edge_order",
                    "nearest_edge_width_m", "node_minus_edge_gap_m",
                ]
            )
            for i, p in enumerate(pts):
                row = [p["point_id"], p["location_name"], p["lat"], p["lon"]]
                for denom in DENOMINATORS:
                    row += [
                        round(float(per_denom[denom]["dist"][i]), 1),
                        int(per_denom[denom]["nearest_id"][i]),
                        cls_of_node[int(per_denom[denom]["nearest_id"][i])],
                    ]
                row += [
                    round(float(edge_d[i]), 1),
                    int(nearest_edge_meta.loc[i, "edge_id"]),
                    str(nearest_edge_meta.loc[i, "order"]),
                    (None if np.isnan(nearest_edge_meta.loc[i, "width_m"])
                     else float(nearest_edge_meta.loc[i, "width_m"])),
                    round(float(per_denom["all_nodes"]["dist"][i] - edge_d[i]), 1),
                ]
                w.writerow(row)

        # candidate radii need the within-radius node sets per denominator
        cap_nodes = set(int(n) for n in node_ids[subsets["capacity_bearing"]])
        ot_nodes = set(int(n) for n in node_ids[subsets["outfall_terminating"]])
        cand_radii = sorted(
            {
                ceil_to_grid(per_denom["all_nodes"]["stats"]["median_m"], GRID_RES_M),
                ceil_to_grid(per_denom["all_nodes"]["stats"]["p75_m"], GRID_RES_M),
                2.0 * float(coupling["capture_radius_m"]),
            }
        )
        for denom in DENOMINATORS:
            mask = subsets[denom]
            ids_masked = node_ids[mask]
            dx = nx[mask]
            dy = ny[mask]
            for r in cand_radii:
                within = []
                for i in range(len(pts_xy)):
                    dd = np.hypot(dx - pts_xy[i, 0], dy - pts_xy[i, 1])
                    within.extend(int(n) for n in ids_masked[dd <= r])
                per_denom[denom]["matched_within"][r] = within

        candidates = build_candidates(
            per_denom["all_nodes"]["stats"],
            per_denom,
            cls_of_node,
            coupling["capture_radius_m"],
            coupling["suspicious_deadend_share"],
            GRID_RES_M,
            edge_d,
        )

        # edge-vs-node structural finding (pre-stated criterion: gap > one grid cell)
        gap = per_denom["all_nodes"]["dist"] - edge_d
        edge_finding = {
            "materiality_criterion_predeclared": (
                f"edge distance smaller than node distance by more than one grid cell "
                f"(> {EDGE_MATERIAL_GAP_M:g} m)"
            ),
            "n_points_materially_closer_to_edge": int((gap > EDGE_MATERIAL_GAP_M).sum()),
            "max_gap_m": round(float(gap.max()), 1),
            "min_edge_dist_m": round(float(edge_d.min()), 1),
            "median_edge_dist_m": round(float(np.median(edge_d)), 1),
            "edge_matches_by_radius": {
                f"{r:g}": int((edge_d <= r).sum()) for r in sorted(
                    {20.0, coupling["capture_radius_m"], 40.0, 80.0, 100.0, *cand_radii}
                )
            },
        }

        summary = {
            "stage": "wf2_gt_distance_distribution",
            "label_rules": "R1: numbers below are FINDINGS (measured this run); "
            "interpretive strings are READINGS.",
            "applied_to_config": False,
            "config_snapshot_readonly": coupling,
            "denominators": manifest["denominators"],
            "distributions": {
                denom: {
                    **per_denom[denom]["stats"],
                    "n_within_declared_radius_100m": int(
                        (per_denom[denom]["dist"] <= coupling["attribution_radius_m"]).sum()
                    ),
                }
                for denom in DENOMINATORS
            },
            "candidates": candidates,
            "edge_vs_node": edge_finding,
            "per_point_csv": str(csv_path.relative_to(REPO)),
            "percentile_method": "numpy.percentile linear interpolation, n=24",
        }
        summary_path = out_dir / "distribution_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2))

        wall = time.perf_counter() - t0
        manifest.update(
            {
                "status": "completed",
                "wall_clock_sec": round(wall, 3),
                "outputs": {
                    "per_point_csv": str(csv_path.relative_to(REPO)),
                    "distribution_summary": str(summary_path.relative_to(REPO)),
                },
                "result_headline": {
                    "distributions": summary["distributions"],
                    "candidates_radii_m": [c["radius_m"] for c in candidates],
                    "edge_vs_node_n_material": edge_finding[
                        "n_points_materially_closer_to_edge"
                    ],
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

    # human-readable stdout summary
    print(f"[wf2-gt-dist] completed in {wall:.2f}s; outputs under {out_dir.relative_to(REPO)}")
    for denom in DENOMINATORS:
        s = per_denom[denom]["stats"]
        print(
            f"  {denominator_label(denom):>22}: "
            + " ".join(f"{k}={v}" for k, v in s.items() if k != "n_points")
        )
    for c in candidates:
        print(
            f"  PROPOSED-NOT-APPLIED r={c['radius_m']:g} m: "
            f"a={c['matches_all_nodes']} b={c['matches_capacity_bearing']} "
            f"c={c['matches_outfall_terminating']} "
            f"edge={c['matches_nearest_edge_geometry']} "
            f"SUSPICIOUS(a)={c['suspicious_would_fire_all_nodes']}"
        )
    print(
        f"  edge-vs-node: {edge_finding['n_points_materially_closer_to_edge']}/24 points "
        f"materially closer to an edge; max gap {edge_finding['max_gap_m']} m; "
        f"min edge distance {edge_finding['min_edge_dist_m']} m"
    )
    return summary


def denominator_label(denom: str) -> str:
    return {
        "all_nodes": "(a) all nodes",
        "capacity_bearing": "(b) capacity-bearing",
        "outfall_terminating": "(c) outfall-termin.",
    }[denom]


app = typer.Typer(add_completion=False)


@app.command()
def main(
    out_dir: Path = typer.Option(OUT_DIR, help="Owned output directory (rule-6 run dir)."),
) -> None:
    """Measure GT->drain-node distance distributions; propose radii FOR ADJUDICATION."""
    run(out_dir)


if __name__ == "__main__":
    app()
