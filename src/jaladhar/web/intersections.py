"""WF-6 build lane L2, module 2 — street-intersection nodes with approaches.

Derives intersection nodes from the classified named centrelines (THE subset
imported from :mod:`jaladhar.web.watchlist`, itself pinned to
:mod:`jaladhar.web.wards`' ``CLASSIFIED_HIGHWAYS``) and reports how many named
streets APPROACH each node and how many of those are FLOODED in the selected
realised product/frame.

Method (``method`` field emitted verbatim):
``"endpoint-cluster-1.0m+passthrough-12m"``

1. ENDPOINT CLUSTERING at ENDPOINT_TOL_M = 1.0 m over every classified named
   segment's endpoints.  A cluster incident to >= 2 distinct street entities is
   a junction candidate.  This reproduces the audit baseline node count
   EXACTLY (2,142; asserted in tests).
2. PASS-THROUGH AWARENESS: the audit's caveat was that some crossings have a
   through-road passing the node at distance ~0 with NO endpoint there, so an
   endpoint-only count under-counts its approaches.  At every candidate node,
   any DISTINCT named street whose geometry passes within PASSTHROUGH_TOL_M =
   12 m (road-width tolerance; stated value) of the node without already
   contributing an endpoint arm adds ONE approach for its entity.

Reconciliation posture (V3/V7): the audit's approach histogram was produced by
a method not recorded in either repository, so this module reports ITS realised
histograms BESIDE the audit figures rather than claiming equality.  Deltas are
data, recorded in counts.audit_baseline and asserted only as sanity bands in
the tests.

``approaches_blocked`` counts distinct street ENTITIES among a node's
approaches whose entity has ANY member segment flooded in the selected
product/frame — consistent with the watchlist's street-level status.

No HTTP here; plain JSON-safe dict contracts only.

Run standalone:
``python -m jaladhar.web.intersections build --product <dir> [--frame TAG|INDEX] --out <json>``
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import typer

from jaladhar.web.watchlist import (
    ENDPOINT_TOL_M,
    load_classified_named_segments,
    load_segment_status_rows,
    load_ward_join,
    merge_street_entities,
    resolve_product_source,
)

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _main() -> None:
    """Street-intersection deriver (dashboard A1/A11 consumers)."""


REPO_ROOT = Path(__file__).resolve().parents[3]

#: Road-width tolerance for pass-through approaches (metres).  STATED VALUE:
#: a carriageway plus margins; a centreline passing closer than this to the
#: node crosses it even when OSM split the ways so no endpoint lands there.
PASSTHROUGH_TOL_M = 12.0

METHOD = "endpoint-cluster-1.0m+passthrough-12m"

#: Audit-measured baseline figures (WF-6 goal prompt), reported beside our
#: realised values.  The audit's per-node counting method for the histogram is
#: not recoverable from either repository; deltas are reported, never hidden.
AUDIT_BASELINE: dict[str, Any] = {
    "endpoint_only_nodes": 2142,
    "approach_distribution": {"2": 413, "3": 998, "4": 701, "5": 27, "6": 2, "7": 1},
}


def cluster_endpoints(
    segments: list[dict[str, Any]],
) -> tuple[dict[int, list[int]], list[tuple[float, float]], list[int]]:
    """Endpoint clusters over all classified named segments.

    Returns ``(clusters, points, point_owner)``: clusters maps a representative
    point index -> member point indices; ``point_owner[i]`` is the index of the
    segment owning point i.
    """

    from scipy.spatial import cKDTree

    points: list[tuple[float, float]] = []
    point_owner: list[int] = []
    for i, seg in enumerate(segments):
        for pt in seg["endpoints"]:
            points.append(pt)
            point_owner.append(i)

    tree = cKDTree(np.asarray(points))
    parent = list(range(len(points)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p, q in tree.query_pairs(ENDPOINT_TOL_M, output_type="ndarray"):
        a, b = find(int(p)), find(int(q))
        if a != b:
            parent[a] = b

    clusters: dict[int, list[int]] = {}
    for i in range(len(points)):
        clusters.setdefault(find(i), []).append(i)
    return clusters, points, point_owner


def derive_junction_nodes(
    segments: list[dict[str, Any]],
    status: dict[str, Any],
    *,
    passthrough_tol_m: float = PASSTHROUGH_TOL_M,
    ward_by_segment: dict[int, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pure junction computation over prepared inputs.

    ``segments`` is :func:`jaladhar.web.watchlist.load_classified_named_segments`
    output (segment dicts carrying segment_id/name/endpoints/geometry);
    ``status`` is :func:`jaladhar.web.watchlist.load_segment_status_rows`
    output (keys rows / lead_minutes / valid_time_utc, optional kind);
    ``ward_by_segment`` is :func:`jaladhar.web.watchlist.load_ward_join`
    output.  Split from :func:`build_intersections` so tests drive the exact
    same code path with synthetic geometry — no mocks, no parallel
    reimplementation.
    """
    entities = merge_street_entities(segments)
    # Per-name merging gives each entity exactly one name; keep it explicit.
    entity_name = [segments[members[0]]["name"] for members in entities]
    entity_of_segment: dict[int, int] = {}
    for ei, members in enumerate(entities):
        for i in members:
            entity_of_segment[i] = ei
    # Entity flooded flag: ANY member segment flooded (watchlist-consistent).
    entity_flooded = [
        any(
            status["rows"].get(segments[i]["segment_id"], {}).get("flood_status") == "flooded"
            for i in members
        )
        for members in entities
    ]

    clusters, points, point_owner = cluster_endpoints(segments)

    from shapely import geometry as sgeom
    from shapely.strtree import STRtree

    geometries = [seg["geometry"] for seg in segments]
    segment_index = STRtree(geometries)

    nodes_out: list[dict[str, Any]] = []
    hist_endpoint_entity: Counter[int] = Counter()
    hist_endpoint_arms: Counter[int] = Counter()
    hist_passthrough_entity: Counter[int] = Counter()
    passthrough_nodes = 0

    for member_points in clusters.values():
        arm_segments = {point_owner[pi] for pi in member_points}
        arm_entities = {entity_of_segment[i] for i in arm_segments}
        if len(arm_entities) < 2:
            continue  # dead end / same-street continuation: not a junction
        hist_endpoint_entity[len(arm_entities)] += 1
        hist_endpoint_arms[len(member_points)] += 1

        cx = sum(points[pi][0] for pi in member_points) / len(member_points)
        cy = sum(points[pi][1] for pi in member_points) / len(member_points)

        # Pass-through: distinct named streets whose geometry comes within
        # passthrough_tol_m of the node WITHOUT an arm ending there.
        probe = sgeom.Point(cx, cy)
        pt_entities: set[int] = set()
        pt_segments: set[int] = set()
        for gi in segment_index.query(probe, predicate="dwithin", distance=passthrough_tol_m):
            gi = int(gi)
            if gi in arm_segments:
                continue
            eid = entity_of_segment[gi]
            if eid in arm_entities:
                continue  # same street already counted through its own arm
            pt_entities.add(eid)
            pt_segments.add(gi)
        if pt_entities:
            passthrough_nodes += 1
        hist_passthrough_entity[len(arm_entities | pt_entities)] += 1

        approach_entities = arm_entities | pt_entities

        # Worst band over the segments physically at/through this node.
        cand_segments = sorted(arm_segments | pt_segments)
        best_seg = -1
        best_row: dict[str, Any] | None = None
        for i in cand_segments:
            row = status["rows"].get(segments[i]["segment_id"])
            if row is None:
                continue
            if (
                best_row is None
                or row["band_high_cm"] > best_row["band_high_cm"]
                or (
                    row["band_high_cm"] == best_row["band_high_cm"]
                    and segments[i]["segment_id"] < segments[best_seg]["segment_id"]
                )
            ):
                best_seg, best_row = i, row

        node: dict[str, Any] = {
            "node_id": "",
            "x": round(cx, 3),
            "y": round(cy, 3),
            "streets": sorted({entity_name[e] for e in approach_entities}),
            "approaches_endpoint_only": len(arm_entities),
            "approaches_total": len(approach_entities),
            "approaches_blocked": len({e for e in approach_entities if entity_flooded[e]}),
            "passthrough_streets": sorted({entity_name[e] for e in pt_entities}),
        }
        lead_fields = {
            "lead_minutes": status["lead_minutes"],
            "lead_kind": status.get("lead_kind")
            or ("hindcast_offset" if status.get("kind") == "frame" else "forecast_lead"),
            "valid_time_utc": status["valid_time_utc"],
        }
        if best_row is not None:
            node["depth_band_cm"] = {
                "low": int(best_row["band_low_cm"]),
                "high": int(best_row["band_high_cm"]),
            }
            node["worst_depth_cm"] = int(best_row["band_high_cm"])
            node["worst_segment_id"] = int(segments[best_seg]["segment_id"])
        else:
            node["depth_band_cm"] = None
            node["worst_depth_cm"] = None
            node["worst_segment_id"] = None
        # Node status: OR over the candidate segments at this node (same
        # street-level semantics as the watchlist rows).
        cand_statuses = {
            row["flood_status"]
            for i in cand_segments
            if (row := status["rows"].get(segments[i]["segment_id"])) is not None
        }
        if "flooded" in cand_statuses:
            node["status"] = "flooded"
        elif "unknown" in cand_statuses:
            node["status"] = "unknown"
        elif best_row is not None:
            node["status"] = best_row["flood_status"]
        else:
            node["status"] = "unknown"
        node.update(lead_fields)
        # Ward of the worst segment when the realised join covers it.
        ward = (
            ward_by_segment.get(node["worst_segment_id"])
            if ward_by_segment and node["worst_segment_id"] is not None
            else None
        )
        node["ward_name"] = ward["ward_name"] if ward else None
        node["ward_number"] = ward["ward_number"] if ward else None
        nodes_out.append(node)

    # Deterministic spatially-ordered node ids.
    nodes_out.sort(key=lambda n: (n["x"], n["y"]))
    for idx, node in enumerate(nodes_out):
        node["node_id"] = f"n{idx:05d}"

    counts = {
        "classified_named_segments": len(segments),
        "street_entities": len(entities),
        "junction_nodes_endpoint_only": sum(hist_endpoint_entity.values()),
        "junction_nodes_passthrough_aware": sum(hist_passthrough_entity.values()),
        "nodes_with_passthrough_added": passthrough_nodes,
        "approach_histogram_endpoint_only_by_entity": {
            str(k): v for k, v in sorted(hist_endpoint_entity.items())
        },
        "approach_histogram_endpoint_only_by_arm": {
            str(k): v for k, v in sorted(hist_endpoint_arms.items())
        },
        "approach_histogram_passthrough_aware_by_entity": {
            str(k): v for k, v in sorted(hist_passthrough_entity.items())
        },
    }
    return nodes_out, counts


def build_intersections(
    product_source: str | Path | Any,
    frame: str | int | None = None,
    *,
    passthrough_tol_m: float = PASSTHROUGH_TOL_M,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build junction nodes with approach counts against a realised product."""

    root = repo_root if repo_root is not None else REPO_ROOT
    source = (
        product_source
        if not isinstance(product_source, (str, Path))
        else resolve_product_source(product_source, repo_root=root)
    )
    status = load_segment_status_rows(source, frame)
    status.setdefault("kind", source.kind)
    segments = load_classified_named_segments(repo_root=root)
    nodes_out, computed_counts = derive_junction_nodes(
        segments,
        status,
        passthrough_tol_m=passthrough_tol_m,
        ward_by_segment=load_ward_join(repo_root=root),
    )

    return {
        "nodes": nodes_out,
        "method": METHOD,
        "counts": {
            **computed_counts,
            "audit_baseline": AUDIT_BASELINE,
            "delta_vs_audit_nodes": (
                computed_counts["junction_nodes_endpoint_only"]
                - AUDIT_BASELINE["endpoint_only_nodes"]
            ),
        },
        "parameters": {
            "endpoint_tol_m": ENDPOINT_TOL_M,
            "passthrough_tol_m": passthrough_tol_m,
        },
    }


# ------------------------------------------------------------------------- CLI


@app.command()
def build(
    product: Path = typer.Option(..., help="Flat products dir OR frame-series run dir"),
    frame: str = typer.Option(None, help="Frame TAG or INDEX when the product is a frame series"),
    out: Path = typer.Option(..., help="Scratch JSON output path"),
    passthrough_tol_m: float = typer.Option(
        PASSTHROUGH_TOL_M, help="Road-width tolerance for pass-through approaches"
    ),
) -> None:
    """Derive intersections and print reconciliation counts."""

    payload = build_intersections(
        product, frame if frame is not None else None, passthrough_tol_m=passthrough_tol_m
    )
    out = out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    summary = {k: v for k, v in payload["counts"].items() if k != "audit_baseline"}
    typer.echo(json.dumps({"out": str(out), **summary}, indent=2))


if __name__ == "__main__":
    app()
