"""WF-1 drain-graph artefact IO: candidate writer + load-time asserting reader.

This module IS the V8 seam made mechanical: the producer's guaranteed properties are
written into the artefact manifest (finalize_manifest), and the consumer asserts
against those exact fields at load (read_artefact). Every refusal raises
RefuseLoadError carrying the specific contract clause and the observed values.

Implements consumer_assertions_at_load 1-9 of configs/contracts/drain_graph.json
(assertion 10, Grid.assert_aligned over rasterized exports, belongs to the raster
seam and is exercised elsewhere per dispatch; the elevation-SHA assertion 9 is
enforced here through the manifest's elevation_surface_sha256_binding, whose
production values are pinned by configs/drainage.yaml to
data/processed/buffered/elevation.tif == 46dff442a739...fe7).

CPU-only; no torch/CUDA imports. Rule-6 manifest section written at run start
(init_manifest) and updated in place (finalize_manifest/write_candidate).

Scope note (V7): the reader's assertions are schema-domain checks over whatever
artefact is handed to them; full-domain WF-1 behaviour is exercised by
tests/drainage/test_stitch.py end-to-end and the future capacity integration.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import typer
from shapely.geometry import LineString, Point

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------- frozen contract pins

CONNECTIVITY_EXPECTED = "D8_stitched"
CRS_EXPECTED = "EPSG:32643"
OBSERVED_NETWORK_DENOMINATOR_KM = 767.3
ELEVATION_SHA_PIN = "46dff442a73945f4bb8d7ee01352eefe4bdfb801b88ac0fa6b5c97d830f56fe7"
ELEVATION_PIN_PATH_SUFFIX = "data/processed/buffered/elevation.tif"

NODE_TYPES_EMITTED = ("junction", "outfall")
OUTFALL_REASONS_EMITTED = ("pit", "domain_exit")
ORDERS_VALID = ("primary", "secondary", "tertiary", "synthetic_connector")
EDGE_SOURCES_VALID = ("observed", "synthesised")
CONFIDENCES_VALID = ("high", "medium", "low")

CAPACITY_NUMERIC_FIELDS = (
    "width_m",
    "width_low_m",
    "width_high_m",
    "n_manning",
    "depth_m_solved",
    "q_capacity_nom_m3s",
    "q_capacity_low_m3s",
    "q_capacity_high_m3s",
)
CAPACITY_TEXT_FIELDS = (
    "width_basis",
    "n_basis",
    "depth_basis",
)
CAPACITY_FIELDS = CAPACITY_NUMERIC_FIELDS + CAPACITY_TEXT_FIELDS + ("capacity_basis",)
SYNTHETIC_CAPACITY_BASIS = (
    "synthetic connector: no surveyed cross-section; routing conduit only, no capacity claim"
)
DEPTH_BASIS_EXPECTED = "solved:rational+Manning"
N_MANNING_PAIRS = {
    0.013: "CPHEEO 2019 Ch5 concrete as-new low",
    0.015: "CPHEEO 2019 Ch5 open-concrete design",
    0.030: "CPHEEO 2019 Ch5 unlined/grass high",
}
CAPACITY_BASIS_REQUIRED_KEYS = (
    "width",
    "shape",
    "side_slope",
    "assumption",
    "n",
    "slope",
    "depth",
    "design_return_period_yr",
    "climate_uplift",
    "runoff_C",
    "safety_factor",
    "sources",
)
SAFETY_FACTOR_RANGE = (0.85, 0.95)
STUB_COUNTER_KEYS = ("stub_count", "stub_total_length_m", "stub_overlength_count")

UNITS_BLOCK = {
    "q_capacity_units": "m3/s",
    "contrib_area_units": "m2=cells*100.0",
    "conversion_formulas": (
        "sink_mm_per_hr = q_capacity_m3s * 3600000 / contrib_area_m2 ; "
        "rate_m_s = q_capacity_m3s / contrib_area_m2"
    ),
}
CAPACITY_RULE_VERSION = (
    "'rational+Manning design v1: Q=C.i.A/360, A=contrib_area_ha at from_node, "
    "i per CPHEEO Ch1 return-period x uplift 1.10-1.30, C in [0.65,0.80], Manning n=0.015, "
    "nom=safety_factor(0.85-0.95) x q_full'"
)
ASSUMED_UNCALIBRATED_COEXISTENCE_NOTE = (
    "coexists deliberately: legacy prior flag stays on the LEGACY drain_capacity artefact; "
    "THIS manifest declares capacity_basis instead - solver assert_drain_manifest_contract "
    "amended (WF-1 task) to accept either legacy prior OR new basis form"
)
GRAPH_FINGERPRINT_SERIALIZATION = (
    "sha256 over UTF-8 JSON array, one entry per edge sorted by edge_id: "
    "[from_node, to_node, order, round(length_m,6), sha256(wkb_hex)] with "
    "json.dumps(..., separators=(',',':')) - byte-exact recomputable by consumer"
)
NODE_GEOMETRY_ASSUMPTION_NOTE = (
    "elev_m is DEM proxy not surveyed invert - consumers must not treat as pipe invert"
)
ENUM_FORWARD_DESIGN_NOTE = (
    "node_type 'inlet' and outfall_reason 'lake_boundary' are forward-design enum members: "
    "wave-1 stitcher rules produce only junction and outfall(pit|domain_exit); producers must "
    "not emit inlet/lake_boundary until WF-2 boundary-exchange agreement lands"
)
DEM_SOURCE_MANIFEST_STATUS = (
    "REPLACED by elevation_surface_sha256_binding per owner adjudication 2026-08-24 "
    "(V1: manifest status is declaration, SHA is realized state). Terrain-conditioning "
    "adjudication RESOLVED: ACCEPT the interim postbreach artefact (failed conditioning run "
    "wrote nothing - fail-closed working as designed); the separate question of whether the "
    "349-cell retained-basin guard is a real defect or too strict is investigated OFF the "
    "critical path (run cost ~59.6 s)"
)
POINTER_PROVENANCE_CAVEAT = (
    "pointer predates its stage-manifest overwrite; "
    "provenance code-path+mtime - recorded as known_gap"
)


class RefuseLoadError(RuntimeError):
    """Consumer-side load refusal: a load-time contract assertion failed."""


class PlacementGuardError(RuntimeError):
    """Publication refused: nothing ships (or sits at consumer paths) disconnected."""


# ---------------------------------------------------------------- small utilities


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git_state() -> tuple[str, bool, list[str]]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        sha = "unknown"
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        )
        paths = [ln[3:].strip() for ln in out.splitlines() if ln.strip()]
    except Exception:
        paths = []
    return sha, bool(paths), paths


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _repo_path(p: Any) -> Path:
    path = Path(str(p))
    return path if path.is_absolute() else REPO / path


def _rec(obj: Any, key: str, default: Any = None) -> Any:
    """Field access over either a dataclass record or a DataFrame record dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _nn(v: Any) -> Any:
    """Normalise SQL NULL: None or NaN -> None."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _require(cond: Any, msg: str, exc: type[RuntimeError]) -> None:
    if not cond:
        raise exc(msg)


# ------------------------------------------------------------- fingerprint + fractions


def graph_fingerprint_payload(edges: list[Any]) -> tuple[str, str]:
    """Pinned serialization: one entry per edge sorted by edge_id, compact JSON, sha256."""
    entries = []
    for e in sorted(edges, key=lambda x: int(_rec(x, "edge_id"))):
        entries.append(
            [
                int(_rec(e, "from_node")),
                int(_rec(e, "to_node")),
                str(_rec(e, "order")),
                round(float(_rec(e, "length_m")), 6),
                hashlib.sha256(str(_rec(e, "geometry").wkb_hex).encode()).hexdigest(),
            ]
        )
    payload = json.dumps(entries, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), payload


def synthesised_fraction(edges: list[Any]) -> float:
    """RULE-1 HEADLINE: synthesised length over the pinned observed-network denominator.

    Deliberately UNCLAMPED (deviation D13: the flat-resolved composite measures 101.3%).
    Sums in edge_id order so producer and consumer float-sums agree bitwise."""
    total = 0.0
    for e in sorted(edges, key=lambda x: int(_rec(x, "edge_id"))):
        if str(_rec(e, "edge_source")) == "synthesised":
            total += float(_rec(e, "length_m"))
    return total / (OBSERVED_NETWORK_DENOMINATOR_KM * 1000.0)


def _topo_order(node_ids: list[int], edges: list[Any]) -> list[int]:
    """Kahn over the DAG, deterministic smallest-id-first frontier."""
    adj_out: dict[int, list[int]] = {nid: [] for nid in node_ids}
    indeg: dict[int, int] = {nid: 0 for nid in node_ids}
    for e in edges:
        f, t = int(_rec(e, "from_node")), int(_rec(e, "to_node"))
        adj_out[f].append(t)
        indeg[t] += 1
    heap = [nid for nid in node_ids if indeg[nid] == 0]
    heapq.heapify(heap)
    order: list[int] = []
    while heap:
        u = heapq.heappop(heap)
        order.append(u)
        for v in sorted(adj_out[u]):
            indeg[v] -= 1
            if indeg[v] == 0:
                heapq.heappush(heap, v)
    if len(order) != len(node_ids):
        raise ValueError(
            "graph contains a directed cycle (Kahn remainder "
            f"{sorted(set(indeg) - set(order))}) - refusing to emit a cyclic graph"
        )
    return order


def _build_adjacency(nodes: list[Any], edges: list[Any], counts: dict) -> dict:
    """Adjacency JSON EXACTLY per contract determinism block; keys and lists sorted."""
    loops = int(counts.get("self_loop_dropped_count", 0) or 0)
    zeros = int(counts.get("zero_length_dropped_count", 0) or 0)
    node_ids = [int(_rec(n, "node_id")) for n in nodes]
    return {
        "nodes": {
            str(int(_rec(n, "node_id"))): {
                "x_m": float(round(float(_rec(n, "x_m")), 3)),
                "y_m": float(round(float(_rec(n, "y_m")), 3)),
            }
            for n in sorted(nodes, key=lambda x: int(_rec(x, "node_id")))
        },
        "edges": {
            str(int(_rec(e, "edge_id"))): {
                "from": int(_rec(e, "from_node")),
                "to": int(_rec(e, "to_node")),
                "order": str(_rec(e, "order")),
                "edge_source": str(_rec(e, "edge_source")),
            }
            for e in sorted(edges, key=lambda x: int(_rec(x, "edge_id")))
        },
        "topo_order": _topo_order(sorted(node_ids), edges),
        "cycles": [],
        "dropped": {
            "self_loops": (
                []
                if loops == 0
                else [
                    x
                    for x in (counts.get("dropped_edge_ids", []) or [])
                    if isinstance(x, str) and x.startswith("self_loop:")
                ]
                or [f"count={loops}"]
            ),
            "zero_length": [] if zeros == 0 else [f"count={zeros}"],
            "duplicates": [str(x) for x in (counts.get("dropped_edge_ids", []) or [])],
        },
    }


# ------------------------------------------------------------------ rule-6 lifecycle


def init_manifest(run_dir: str, cfg: dict) -> dict:
    """Rule 6: manifest EXISTS at run start (status running, git envelope, config
    snapshot, wall clock) and is updated in place afterwards. Input SHA256s for the
    four WF-1 inputs are hashed NOW, not claimed later."""
    rd = Path(run_dir)
    rd.mkdir(parents=True, exist_ok=True)
    sha, dirty, porcelain = _git_state()
    inputs = cfg.get("inputs", {}) or {}
    input_sha: dict[str, str] = {}
    unresolved: list[str] = []
    for key, label in (
        ("waterways_gpkg", "waterways_bbmp.gpkg"),
        ("retained_basins_csv", "retained_basins.csv"),
        ("kml_primary", "kml_primary"),
        ("kml_secondary", "kml_secondary"),
    ):
        raw = inputs.get(key)
        path = _repo_path(raw) if raw else None
        if path is not None and path.exists():
            input_sha[label] = _sha256_file(path)
        else:
            unresolved.append(label)
    manifest: dict[str, Any] = {
        "stage": "wf1_graph_io",
        "status": "running",
        "started_utc": _utc_now(),
        "cpu_only": True,
        "git_sha": sha,
        "git_dirty": dirty,
        "git_porcelain_paths": porcelain,
        "config_snapshot": json.loads(json.dumps(cfg, default=str)),
        "input_sha256": input_sha,
        "inputs_unresolved_at_init": unresolved,
        "elevation_surface_sha256_binding": {
            "path": inputs.get("elevation_surface"),
            "expected_sha256": inputs.get("elevation_sha256"),
            "binding_source": (
                "configs/drainage.yaml inputs.elevation_sha256 " "(owner adjudication 2026-08-24)"
            ),
        },
        "_perf_t0": time.perf_counter(),
    }
    (rd / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
    )
    return manifest


def finalize_manifest(
    manifest: dict,
    *,
    nodes: list,
    edges: list,
    stitch_metrics: dict,
    capacity_metrics: dict | None = None,
    extra_counts: dict | None = None,
) -> dict:
    """Merge EVERY manifest_guarantees_producer_writes field into the run-start
    manifest (updated in place, returned). Computes the graph fingerprint and the
    UNCLAMPED synthesised fraction FROM THE EDGES THEMSELVES, never from claims."""
    counters = dict(stitch_metrics.get("counters", {}) or {})
    if extra_counts:
        counters.update(extra_counts)
    fingerprint, _payload = graph_fingerprint_payload(edges)
    fraction = synthesised_fraction(edges)
    post = int(stitch_metrics.get("component_count_post_stitch", 1))
    pre = int(stitch_metrics.get("component_count_pre_stitch", post))
    capacity = capacity_metrics if capacity_metrics is not None else {"capacity_status": "not_run"}

    # Component policy (owner adjudication 2026-08-25, replaces the ==1 pin):
    # components terminating at a valid outfall must carry >= 95% of total
    # observed reach length; the outfall set is named and justified.
    parent: dict[int, int] = {}

    def _pfind(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    obs_len_total = 0.0
    obs_len_by_comp: dict[int, float] = {}
    for e in edges:
        # union over ALL edges (connectors reach the outfalls; observed reaches
        # join via split junction nodes) - skipping synthesised edges here is
        # what produced the fraction==0 defect caught by independent recompute.
        a, b = _pfind(int(_rec(e, "from_node"))), _pfind(int(_rec(e, "to_node")))
        if a != b:
            parent[a] = b
    for e in edges:
        if str(_rec(e, "edge_source")) != "observed":
            continue
        L = float(_rec(e, "length_m"))
        obs_len_total += L
    outfall_nodes = {
        int(_rec(n, "node_id")) for n in nodes if str(_rec(n, "node_type")) == "outfall"
    }
    outfall_roots = {_pfind(nid) for nid in outfall_nodes if nid in parent}
    for e in edges:
        if str(_rec(e, "edge_source")) != "observed":
            continue
        root = _pfind(int(_rec(e, "from_node")))
        if root in outfall_roots:
            obs_len_by_comp[root] = obs_len_by_comp.get(root, 0.0) + float(_rec(e, "length_m"))
    obs_len_outfall = sum(obs_len_by_comp.values())
    fraction_obs = (obs_len_outfall / obs_len_total) if obs_len_total > 0 else 0.0
    POLICY_TARGET = 0.95
    policy_met = bool(fraction_obs >= POLICY_TARGET)
    snap_consumer = (manifest.get("config_snapshot", {}) or {}).get("consumer") or {}
    policy_accepted = bool(snap_consumer.get("component_policy_accepted"))
    acceptance_reason = str(snap_consumer.get("component_policy_acceptance_reason", ""))
    if policy_accepted and not policy_met:
        policy_met = True

    reasons = [
        str(r)
        for r in (stitch_metrics.get("reasons", []) or [])
        if r != "expected connectivity_post_stitch_gt_1"
    ]
    if not policy_met:
        reasons.append(
            f"component_policy_unmet_outfall_terminating_observed_length_fraction_"
            f"{fraction_obs:.4f}_below_{POLICY_TARGET}"
        )
    cap_status = str(capacity.get("capacity_status", ""))
    if cap_status and cap_status not in ("ok", "completed"):
        reasons.append(cap_status)
    if int(counters.get("cycle_break_observed_edge_dropped_count", 0) or 0) > 0:
        reasons.append("cycle_break_dropped_observed_edge_owner_attention_menu_M4")
    if fraction > 0.5:
        reasons.append(
            "synthesised_fraction_of_total_length_gt_0p5_owner_informed_pre_presentation"
        )

    emitted_node_types = sorted({str(_rec(n, "node_type")) for n in nodes})
    emitted_outfall_reasons = sorted(
        {str(r) for r in (_rec(n, "outfall_reason") for n in nodes) if r is not None}
    )
    observed_km = (
        sum(
            float(_rec(e, "length_m"))
            for e in sorted(edges, key=lambda x: int(_rec(x, "edge_id")))
            if str(_rec(e, "edge_source")) == "observed"
        )
        / 1000.0
    )

    t0 = float(manifest.pop("_perf_t0", time.perf_counter()))
    node_map_rows = [
        [
            int(_rec(n, "node_id")),
            round(float(_rec(n, "x_m")), 3),
            round(float(_rec(n, "y_m")), 3),
        ]
        for n in sorted(nodes, key=lambda x: int(_rec(x, "node_id")))
    ]
    node_map_payload = json.dumps(node_map_rows, separators=(",", ":"))
    counters["synthesised_fraction_of_total_length"] = fraction
    counters["stub_survived_count"] = sum(
        1
        for e in edges
        if str(_rec(e, "edge_source")) == "synthesised"
        and str(_rec(e, "direction_basis", "")).find("junction_stub") >= 0
    )
    manifest.update(
        {
            "finished_utc": _utc_now(),
            "wall_clock_sec": round(time.perf_counter() - t0, 3),
            "status": (
                "completed"
                if policy_met and cap_status in ("ok", "completed", "")
                else "stopped_owner_adjudication"
            ),
            "status_note": (
                (
                    "owner adjudication WF-1b accepts the outfall fraction; "
                    "stopped_owner_adjudication persists only while the synthesised "
                    "fraction headline (>0.5) stands"
                )
                if policy_accepted
                else None
            ),
            "connectivity": CONNECTIVITY_EXPECTED,
            "component_policy": {
                "name": "outfall_terminating_components_ge_95pct_observed_length",
                "target": POLICY_TARGET,
                "outfall_terminating_observed_length_fraction": round(fraction_obs, 6),
                "outfall_terminating_observed_length_km": round(obs_len_outfall / 1000.0, 3),
                "observed_length_total_km": round(obs_len_total / 1000.0, 3),
                "met": policy_met,
                "accepted_by_owner": policy_accepted,
                "owner_acceptance_reason": acceptance_reason,
                "replaces": (
                    "component_count_post_stitch == 1 (owner adjudication 2026-08-25: "
                    "three-valley city, ==1 not physically achievable)"
                ),
            },
            "named_outfalls": stitch_metrics.get("named_outfalls", []),
            "component_count_pre_stitch": pre,
            "component_count_post_stitch": post,
            "stranded_component_ids": stitch_metrics.get("stranded_component_ids", []),
            "stitcher_params": stitch_metrics.get("stitcher_params", {}),
            "output_sha256": stitch_metrics.get("output_sha256", {}),
            "dem_source_manifest_status": DEM_SOURCE_MANIFEST_STATUS,
            "pointer_provenance": "derived_in_run",
            "pointer_provenance_caveat": POINTER_PROVENANCE_CAVEAT,
            "counts": counters,
            "stub_counters": {k: counters[k] for k in STUB_COUNTER_KEYS if k in counters},
            "walk_outcome_histogram": stitch_metrics.get("walk_outcome_histogram", {}),
            "lost_walk_audit": stitch_metrics.get("lost_walk_audit", {}),
            "cycle_break_rounds": stitch_metrics.get("cycle_break_rounds", []),
            "cycle_break_justification": (
                "M4 adjudication 2026-08-25: pinned rule drops lowest-slope SYNTHETIC "
                "edge per cycle; where a cycle contained NO synthetic member an "
                "OBSERVED edge was dropped instead (pool="
                "'any_fallback_no_synthetic_in_cycle', named per-round below). The "
                "edge cannot be restored without leaving the cycle; WCC before/after "
                "is recorded per round and was unchanged, so the drop did not "
                "re-fragment the graph. Owner may re-adjudicate via menu M4."
            ),
            "unresolved_outfall_count": int(counters.get("unresolved_outfall_count", 0) or 0),
            "capacity_metrics": capacity,
            "units_block": dict(UNITS_BLOCK),
            "capacity_rule_version": CAPACITY_RULE_VERSION,
            "assumed_uncalibrated_flag_note": ASSUMED_UNCALIBRATED_COEXISTENCE_NOTE,
            "deterministic_ids": True,
            "graph_is_dag": bool(stitch_metrics.get("graph_is_dag", False)),
            "graph_fingerprint": fingerprint,
            "graph_fingerprint_serialization_pinned": GRAPH_FINGERPRINT_SERIALIZATION,
            "node_id_map_sha256": hashlib.sha256(node_map_payload.encode("utf-8")).hexdigest(),
            "node_id_map_serialization_pinned": (
                "sha256 over UTF-8 json.dumps([[node_id, round(x_m,3), round(y_m,3)] ...] "
                "sorted by node_id, separators=(',',':')) - consumer-recomputable"
            ),
            "synthesised_fraction_of_total_length": fraction,
            "synthesised_fraction_definition": {
                "numerator": "sum(length_m where edge_source=='synthesised'), metres",
                "denominator": (
                    f"observed network denominator pinned at {OBSERVED_NETWORK_DENOMINATOR_KM} km "
                    "(frontier-table convention; deviation D13)"
                ),
                "unclamped": True,
            },
            "observed_length_km_measured_from_edges": round(observed_km, 6),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_geometry_assumption_note": NODE_GEOMETRY_ASSUMPTION_NOTE,
            "enum_forward_design_note": {
                "note": ENUM_FORWARD_DESIGN_NOTE,
                "emitted_node_types": emitted_node_types,
                "emitted_outfall_reasons": emitted_outfall_reasons,
                "emitted_orders": sorted({str(_rec(e, "order")) for e in edges}),
                "emitted_edge_sources": sorted({str(_rec(e, "edge_source")) for e in edges}),
            },
            "lake_receiving_water_names": [],
            "policy_frontier_table": stitch_metrics.get("policy_frontier_table", []),
            "realized_policy_row": stitch_metrics.get("realized_policy_row"),
            "reasons": reasons,
            "metrics_headline": {
                "component_count_pre_stitch": pre,
                "component_count_post_stitch": post,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "synthesised_fraction_of_total_length": fraction,
                "graph_fingerprint": fingerprint,
            },
        }
    )
    return manifest


# ------------------------------------------------------------------------- writer


def _validate_for_write(nodes: list, edges: list) -> None:
    seen_nodes: set[int] = set()
    for n in nodes:
        nid = int(_rec(n, "node_id"))
        _require(nid >= 1 and nid not in seen_nodes, f"bad/duplicate node_id {nid}", ValueError)
        seen_nodes.add(nid)
        nt = str(_rec(n, "node_type"))
        _require(
            nt in NODE_TYPES_EMITTED,
            f"node {nid}: node_type {nt!r} must not be emitted "
            f"(forward-design block; emit only {NODE_TYPES_EMITTED})",
            ValueError,
        )
        reason = _rec(n, "outfall_reason")
        if nt == "outfall":
            _require(
                reason in OUTFALL_REASONS_EMITTED,
                f"node {nid}: outfall_reason {reason!r} not emittable",
                ValueError,
            )
        else:
            _require(reason is None, f"node {nid}: outfall_reason set on non-outfall", ValueError)
        z = float(_rec(n, "elev_m"))
        _require(math.isfinite(z), f"node {nid}: elev_m not finite", ValueError)
    seen_edges: set[int] = set()
    for e in edges:
        eid = int(_rec(e, "edge_id"))
        _require(eid >= 1 and eid not in seen_edges, f"bad/duplicate edge_id {eid}", ValueError)
        seen_edges.add(eid)
        f, t = int(_rec(e, "from_node")), int(_rec(e, "to_node"))
        _require(
            f in seen_nodes and t in seen_nodes,
            f"edge {eid}: endpoint not among nodes ({f}->{t})",
            ValueError,
        )
        _require(f != t, f"edge {eid}: self-loop", ValueError)
        order, src = str(_rec(e, "order")), str(_rec(e, "edge_source"))
        _require(order in ORDERS_VALID, f"edge {eid}: bad order {order!r}", ValueError)
        _require(src in EDGE_SOURCES_VALID, f"edge {eid}: bad edge_source {src!r}", ValueError)
        _require(
            (src == "synthesised") == (order == "synthetic_connector"),
            f"edge {eid}: bidirectional tag violated ({src}/{order})",
            ValueError,
        )
        conf = str(_rec(e, "direction_confidence"))
        _require(
            conf in CONFIDENCES_VALID, f"edge {eid}: bad direction_confidence {conf!r}", ValueError
        )
        length = float(_rec(e, "length_m"))
        slope = float(_rec(e, "slope_m_per_m"))
        _require(
            math.isfinite(length) and length > 0.0,
            f"edge {eid}: length_m must be >0 and finite (got {length})",
            ValueError,
        )
        _require(
            math.isfinite(slope) and slope >= 0.0,
            f"edge {eid}: slope_m_per_m must be >=0 and finite (got {slope})",
            ValueError,
        )
        _require(
            isinstance(_rec(e, "geometry"), LineString),
            f"edge {eid}: geometry must be a LineString",
            ValueError,
        )


def _records_to_frames(nodes: list, edges: list) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    nrows = []
    for n in nodes:
        nid = int(_rec(n, "node_id"))
        cells_raw = _nn(_rec(n, "contrib_area_cells"))
        cells = int(cells_raw) if cells_raw is not None else None
        _require(
            cells is None or float(cells_raw) == float(cells),
            f"node {nid}: contrib_area_cells must be an integer",
            ValueError,
        )
        nrows.append(
            {
                "node_id": nid,
                "node_type": str(_rec(n, "node_type")),
                "outfall_reason": _rec(n, "outfall_reason"),
                "x_m": float(round(float(_rec(n, "x_m")), 3)),
                "y_m": float(round(float(_rec(n, "y_m")), 3)),
                "elev_m": float(_rec(n, "elev_m")),
                "contrib_area_cells": cells,
                "contrib_area_m2": (
                    float(_nn(_rec(n, "contrib_area_m2")))
                    if _nn(_rec(n, "contrib_area_m2")) is not None
                    else (cells * 100.0 if cells is not None else None)
                ),
                "contrib_area_ha": (
                    float(_nn(_rec(n, "contrib_area_ha")))
                    if _nn(_rec(n, "contrib_area_ha")) is not None
                    else (cells * 0.01 if cells is not None else None)
                ),
            }
        )
    ngdf = gpd.GeoDataFrame(
        nrows, geometry=[Point(r["x_m"], r["y_m"]) for r in nrows], crs=CRS_EXPECTED
    )
    erows = []
    geoms = []
    for e in edges:
        erows.append(
            {
                "edge_id": int(_rec(e, "edge_id")),
                "from_node": int(_rec(e, "from_node")),
                "to_node": int(_rec(e, "to_node")),
                "order": str(_rec(e, "order")),
                "edge_source": str(_rec(e, "edge_source")),
                "direction_confidence": str(_rec(e, "direction_confidence")),
                "direction_basis": str(_rec(e, "direction_basis")),
                "length_m": float(_rec(e, "length_m")),
                "slope_m_per_m": float(_rec(e, "slope_m_per_m")),
                **{fld: _nn(_rec(e, fld)) for fld in CAPACITY_FIELDS},
            }
        )
        geoms.append(_rec(e, "geometry"))
    egdf = gpd.GeoDataFrame(erows, geometry=geoms, crs=CRS_EXPECTED)
    return ngdf, egdf


def write_candidate(
    nodes: list, edges: list, manifest: dict, out_dir: str, publish: bool = False
) -> dict:
    """Write the candidate artefact (gpkg + adjacency + updated-in-place manifest).

    publish=True copies to config_snapshot.outputs.publish_{gpkg,adjacency} BUT ONLY
    when the component policy is MET; otherwise PlacementGuardError and NOTHING is
    copied (nothing ships unadjudicated)."""
    _require(
        manifest.get("graph_is_dag") is True,
        "write_candidate refuses: manifest.graph_is_dag is not True - never write a "
        "cyclic graph",
        ValueError,
    )
    _require(
        "component_count_post_stitch" in manifest,
        "write_candidate refuses: manifest not finalized " "(missing component_count_post_stitch)",
        ValueError,
    )
    _validate_for_write(nodes, edges)
    fingerprint, _ = graph_fingerprint_payload(edges)
    declared = manifest.get("graph_fingerprint")
    _require(
        declared is None or declared == fingerprint,
        f"write_candidate refuses: edges disagree with manifest graph_fingerprint "
        f"(declared {declared}, recomputed {fingerprint})",
        ValueError,
    )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    print(f"[graph_io] writing candidate: nodes={len(nodes)} edges={len(edges)} -> {out}")
    ngdf, egdf = _records_to_frames(nodes, edges)
    gpkg_path = out / "drain_graph.gpkg"
    ngdf.to_file(gpkg_path, layer="drain_nodes", driver="GPKG")
    egdf.to_file(gpkg_path, layer="drain_edges", driver="GPKG")

    adj = _build_adjacency(nodes, edges, manifest.get("counts", {}) or {})
    adj_path = out / "drain_graph_adjacency.json"
    adj_path.write_text(json.dumps(adj, indent=2, sort_keys=True) + "\n")

    manifest = dict(manifest)
    manifest["artefact_paths"] = {"gpkg": str(gpkg_path), "adjacency": str(adj_path)}
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")

    result = {"gpkg": str(gpkg_path), "adjacency": str(adj_path), "manifest": str(manifest_path)}

    post = int(manifest["component_count_post_stitch"])
    policy_met = bool((manifest.get("component_policy", {}) or {}).get("met", post == 1))
    if publish:
        outputs = (manifest.get("config_snapshot", {}) or {}).get("outputs", {}) or {}
        pub_gpkg, pub_adj = outputs.get("publish_gpkg"), outputs.get("publish_adjacency")
        pol = manifest.get("component_policy", {}) or {}
        _require(
            policy_met,
            "publish refused: component policy unmet "
            f"(fraction {pol.get('outfall_terminating_observed_length_fraction')}) "
            f"- NOTHING ships unadjudicated; candidate stays under {out}",
            PlacementGuardError,
        )
        _require(
            bool(pub_gpkg) and bool(pub_adj),
            "publish requested but config_snapshot.outputs.publish_* missing",
            ValueError,
        )
        pg, pa = _repo_path(pub_gpkg), _repo_path(pub_adj)
        pg.parent.mkdir(parents=True, exist_ok=True)
        pa.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(gpkg_path, pg)
        shutil.copyfile(adj_path, pa)
        result.update({"published_gpkg": str(pg), "published_adjacency": str(pa)})
    return result


# ------------------------------------------------------------------------- reader


def _load_json(path: str, what: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise RefuseLoadError(f"[files] {what} not found: {path}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise RefuseLoadError(f"[files] {what} is not valid JSON ({path}): {exc}") from exc


def _source_ok(entry: Any) -> bool:
    if not isinstance(entry, str) or not entry.strip():
        return False
    t = entry.strip()
    if t.lower().startswith(("http://", "https://")):
        return bool(re.fullmatch(r"https?://\S+", t))
    return "://" not in t and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,:;\-/()&']*", t))


def _assumed_scan(basis: dict) -> str | None:
    """Case-insensitive 'assumed' hunt; exemption only for key startswith 'assumption'
    whose value is the strict one-colon form 'assumption:<label>'."""
    one_colon = re.compile(r"assumption:[A-Za-z0-9_\-.]+")
    for key, val in basis.items():
        if isinstance(val, str):
            texts = [val]
        elif isinstance(val, list):
            texts = [str(x) for x in val]
        elif isinstance(val, dict):
            texts = [json.dumps(val)]
        else:
            texts = [str(val)]
        for text in texts:
            if "assumed" in text.lower():
                exempt = (
                    isinstance(key, str)
                    and key.startswith("assumption")
                    and isinstance(val, str)
                    and bool(one_colon.fullmatch(val))
                )
                if not exempt:
                    return f"key={key!r} value={text[:120]!r}"
    return None


def _check_observed_capacity(eid: int, row: dict) -> None:
    n_val = _nn(row.get("n_manning"))
    n_key = round(float(n_val), 3)
    _require(
        n_key in N_MANNING_PAIRS and N_MANNING_PAIRS[n_key] == str(row.get("n_basis")),
        f"[consumer_assertion_5] edge {eid}: (n_manning,n_basis)="
        f"({n_val},{row.get('n_basis')!r}) not in allowed pairs {N_MANNING_PAIRS}",
        RefuseLoadError,
    )
    w = float(row.get("width_m"))
    lo, hi = float(row.get("width_low_m")), float(row.get("width_high_m"))
    _require(
        lo <= w <= hi,
        f"[consumer_assertion_5] edge {eid}: width band violated ({lo} <= {w} <= {hi})",
        RefuseLoadError,
    )
    needle = f"{w:.2f}"
    _require(
        needle in str(row.get("width_basis")),
        f"[consumer_assertion_5] edge {eid}: width_basis does not contain exact rounded "
        f"width {needle}",
        RefuseLoadError,
    )
    q_lo = _nn(row.get("q_capacity_low_m3s"))
    q_nom = _nn(row.get("q_capacity_nom_m3s"))
    q_hi = _nn(row.get("q_capacity_high_m3s"))
    vals = (float(q_lo), float(q_nom), float(q_hi))
    _require(
        all(math.isfinite(v) and v > 0 for v in vals),
        f"[consumer_assertion_5] edge {eid}: q capacities must be finite >0 (got {vals})",
        RefuseLoadError,
    )
    _require(
        vals[0] <= vals[1] <= vals[2],
        f"[consumer_assertion_5] edge {eid}: q ordering violated "
        f"(low={vals[0]} nom={vals[1]} high={vals[2]})",
        RefuseLoadError,
    )
    _require(
        str(row.get("depth_basis")) == DEPTH_BASIS_EXPECTED,
        f"[consumer_assertion_5] edge {eid}: depth_basis {row.get('depth_basis')!r} != "
        f"{DEPTH_BASIS_EXPECTED!r}",
        RefuseLoadError,
    )

    raw = row.get("capacity_basis")
    try:
        basis = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RefuseLoadError(
            f"[consumer_assertion_6] edge {eid}: capacity_basis does not " f"parse as JSON: {exc}"
        ) from exc
    _require(
        isinstance(basis, dict),
        f"[consumer_assertion_6] edge {eid}: capacity_basis must be a JSON object",
        RefuseLoadError,
    )
    missing = [k for k in CAPACITY_BASIS_REQUIRED_KEYS if k not in basis]
    _require(
        not missing,
        f"[consumer_assertion_6] edge {eid}: capacity_basis missing keys {missing}",
        RefuseLoadError,
    )
    for key, val in basis.items():
        empty = (
            val is None
            or (isinstance(val, (str, list, dict)) and len(val) == 0)
            or (isinstance(val, str) and not val.strip())
        )
        _require(
            not empty,
            f"[consumer_assertion_6] edge {eid}: capacity_basis[{key!r}] is empty",
            RefuseLoadError,
        )
    offender = _assumed_scan(basis)
    _require(
        offender is None,
        f"[consumer_assertion_6] edge {eid}: undeclared 'assumed' language in "
        f"capacity_basis at {offender}",
        RefuseLoadError,
    )
    sources = basis["sources"]
    _require(
        isinstance(sources, list) and len(sources) > 0,
        f"[consumer_assertion_6] edge {eid}: sources[] must be a non-empty list",
        RefuseLoadError,
    )
    bad = [s for s in sources if not _source_ok(s)]
    _require(
        not bad,
        f"[consumer_assertion_6] edge {eid}: sources[] entries not http-or-doc-name "
        f"legal: {bad}",
        RefuseLoadError,
    )
    _require(
        str(basis["depth"]) == DEPTH_BASIS_EXPECTED,
        f"[consumer_assertion_6] edge {eid}: capacity_basis.depth "
        f"{basis['depth']!r} != {DEPTH_BASIS_EXPECTED!r}",
        RefuseLoadError,
    )
    sf = float(basis["safety_factor"])
    _require(
        SAFETY_FACTOR_RANGE[0] <= sf <= SAFETY_FACTOR_RANGE[1],
        f"[consumer_assertion_6] edge {eid}: safety_factor {sf} outside " f"{SAFETY_FACTOR_RANGE}",
        RefuseLoadError,
    )


def read_artefact(
    gpkg: str,
    adjacency: str,
    manifest_path: str,
    *,
    allow_disconnected_load: bool = False,
    owner_adjudication_ref: str = "",
) -> dict:
    """Load a drain-graph artefact or refuse with a SPECIFIC reason.

    Enforces consumer_assertions_at_load 1-9 of configs/contracts/drain_graph.json
    (10 is the raster seam, tested elsewhere), the tightened >1-component refusal,
    and the placement guard against publish paths while disconnected."""
    # -- files + manifest envelope -------------------------------------------
    for p, what in ((gpkg, "gpkg"), (adjacency, "adjacency")):
        if not Path(p).exists():
            raise RefuseLoadError(f"[files] {what} not found: {p}")
    man = _load_json(manifest_path, "manifest")

    status = str(man.get("status", ""))
    _require(
        status != "running",
        f"[manifest] still status='running' (rule 6: incomplete run) at {manifest_path}",
        RefuseLoadError,
    )

    # -- assertion 1 (+ tightened >1 gate and placement guard) ----------------
    _require(
        man.get("connectivity") == CONNECTIVITY_EXPECTED,
        f"[consumer_assertion_1] manifest.connectivity={man.get('connectivity')!r} != "
        f"{CONNECTIVITY_EXPECTED!r}",
        RefuseLoadError,
    )
    post = int(man.get("component_count_post_stitch", -1))
    _require(
        post >= 1,
        f"[consumer_assertion_1] component_count_post_stitch={post} < 1 (vacuous-graph " "refusal)",
        RefuseLoadError,
    )
    outputs = (man.get("config_snapshot", {}) or {}).get("outputs", {}) or {}
    policy = man.get("component_policy", {}) or {}
    policy_met = bool(policy.get("met", post == 1))
    if not policy_met:
        pub_gpkg, pub_adj = outputs.get("publish_gpkg"), outputs.get("publish_adjacency")
        placed = [str(_repo_path(p)) for p in (pub_gpkg, pub_adj) if p and _repo_path(p).exists()]
        _require(
            not placed,
            f"[placement_guard] component policy unmet "
            f"(outfall-terminating observed-length fraction "
            f"{policy.get('outfall_terminating_observed_length_fraction')}) yet artefact found "
            f"at consumer publish path(s) {placed} - nothing ships unadjudicated",
            RefuseLoadError,
        )
        sanctioned = (
            status == "stopped_owner_adjudication"
            and allow_disconnected_load
            and bool(owner_adjudication_ref.strip())
        )
        _require(
            sanctioned,
            f"[component_policy] outfall-terminating observed-length fraction "
            f"{policy.get('outfall_terminating_observed_length_fraction')} < target "
            f"{policy.get('target', 0.95)}: load requires manifest.status="
            "'stopped_owner_adjudication' AND allow_disconnected_load AND non-empty "
            f"owner_adjudication_ref (got status={status!r}, allow={allow_disconnected_load}, "
            f"ref={owner_adjudication_ref!r})",
            RefuseLoadError,
        )

    # -- assertions 3-counts, 8, 4-declarative half ---------------------------
    counts = man.get("counts", {}) or {}
    self_loops = int(counts.get("self_loop_dropped_count", 0) or 0)
    zeros = int(counts.get("zero_length_dropped_count", 0) or 0)
    # M5b adjudication 2026-08-25: the 12 input-geometry self-loops are a data
    # fact, not a build defect; a sanctioned consumer may load when the dropped
    # ids are enumerated in the manifest (traceability) and the owner
    # adjudication reference is present.
    enumerated_self_loops = [
        x
        for x in counts.get("dropped_edge_ids", [])
        if isinstance(x, str) and x.startswith("self_loop:")
    ]
    _require(
        self_loops == 0
        or (bool(owner_adjudication_ref.strip()) and len(enumerated_self_loops) >= 1),
        f"[consumer_assertion_3] self_loop_dropped_count={self_loops} != 0; M5b override "
        "requires non-empty owner_adjudication_ref AND enumerated self_loop entries in "
        f"counts.dropped_edge_ids (found {len(enumerated_self_loops)})",
        RefuseLoadError,
    )
    _require(
        zeros == 0,
        f"[consumer_assertion_3] zero_length_dropped_count={zeros} != 0",
        RefuseLoadError,
    )
    unresolved = int(counts.get("unresolved_outfall_count", 0) or 0)
    _require(
        unresolved == 0, f"[v-outfall] unresolved_outfall_count={unresolved} != 0", RefuseLoadError
    )
    _require(
        man.get("graph_is_dag") is True,
        f"[consumer_assertion_8] manifest.graph_is_dag={man.get('graph_is_dag')!r} != True",
        RefuseLoadError,
    )
    _require(
        man.get("deterministic_ids") is True,
        f"[consumer_assertion_4] manifest.deterministic_ids="
        f"{man.get('deterministic_ids')!r} != True",
        RefuseLoadError,
    )

    # -- layers ----------------------------------------------------------------
    try:
        nodes_gdf = gpd.read_file(gpkg, layer="drain_nodes")
        edges_gdf = gpd.read_file(gpkg, layer="drain_edges")
    except Exception as exc:
        raise RefuseLoadError(f"[layers] cannot read gpkg layers from {gpkg}: {exc}") from exc

    req_node_cols = [
        "node_id",
        "node_type",
        "outfall_reason",
        "x_m",
        "y_m",
        "elev_m",
        "contrib_area_cells",
        "contrib_area_m2",
        "contrib_area_ha",
    ]
    req_edge_cols = [
        "edge_id",
        "from_node",
        "to_node",
        "order",
        "edge_source",
        "direction_confidence",
        "direction_basis",
        "length_m",
        "slope_m_per_m",
        *CAPACITY_FIELDS,
    ]
    missing_n = [c for c in req_node_cols if c not in nodes_gdf.columns]
    missing_e = [c for c in req_edge_cols if c not in edges_gdf.columns]
    _require(not missing_n, f"[layers] drain_nodes missing columns {missing_n}", RefuseLoadError)
    _require(not missing_e, f"[layers] drain_edges missing columns {missing_e}", RefuseLoadError)
    for gdf, what in ((nodes_gdf, "drain_nodes"), (edges_gdf, "drain_edges")):
        _require(
            gdf.crs is not None and gdf.crs.to_string() == CRS_EXPECTED,
            f"[layers] {what} CRS {gdf.crs} != {CRS_EXPECTED}",
            RefuseLoadError,
        )

    node_recs = nodes_gdf.sort_values("node_id").to_dict("records")
    edge_recs = edges_gdf.sort_values("edge_id").to_dict("records")
    _require(
        [int(r["node_id"]) for r in node_recs] == list(range(1, len(node_recs) + 1)),
        "[consumer_assertion_4] deterministic_ids=true but node_id not contiguous 1..N",
        RefuseLoadError,
    )
    _require(
        [int(r["edge_id"]) for r in edge_recs] == list(range(1, len(edge_recs) + 1)),
        "[consumer_assertion_4] deterministic_ids=true but edge_id not contiguous 1..E",
        RefuseLoadError,
    )

    # -- per-node schema + assertion 7 ----------------------------------------
    for r in node_recs:
        nid = int(r["node_id"])
        nt = str(r["node_type"])
        _require(
            nt in NODE_TYPES_EMITTED,
            f"[enum_discipline] node {nid}: node_type {nt!r} must never be emitted "
            "(forward-design block)",
            RefuseLoadError,
        )
        reason = _nn(r["outfall_reason"])
        if nt == "outfall":
            _require(
                reason in OUTFALL_REASONS_EMITTED,
                f"[layers] node {nid}: outfall_reason {reason!r} invalid",
                RefuseLoadError,
            )
        else:
            _require(
                reason is None,
                f"[layers] node {nid}: outfall_reason set on non-outfall",
                RefuseLoadError,
            )
        x, y, z = float(r["x_m"]), float(r["y_m"]), float(r["elev_m"])
        _require(
            round(x, 3) == x and round(y, 3) == y,
            f"[layers] node {nid}: x_m/y_m not rounded to 3dp ({x},{y})",
            RefuseLoadError,
        )
        _require(math.isfinite(z), f"[layers] node {nid}: elev_m null/not finite", RefuseLoadError)
        pt = r["geometry"]
        _require(
            pt is not None and pt.geom_type == "Point",
            f"[layers] node {nid}: geometry not a Point",
            RefuseLoadError,
        )
        cells_raw, m2, ha = (
            _nn(r["contrib_area_cells"]),
            _nn(r["contrib_area_m2"]),
            _nn(r["contrib_area_ha"]),
        )
        if cells_raw is None:
            _require(
                m2 is None and ha is None,
                f"[consumer_assertion_7] node {nid}: area fields set without " "contrib_area_cells",
                RefuseLoadError,
            )
        else:
            cells = float(cells_raw)
            _require(
                cells == int(cells),
                f"[consumer_assertion_7] node {nid}: contrib_area_cells {cells} not integral",
                RefuseLoadError,
            )
            _require(
                abs(m2 - cells * 100.0) <= 1e-6,
                f"[consumer_assertion_7] node {nid}: contrib_area_m2 {m2} != cells*100.0 "
                f"({cells * 100.0}) within 1e-6",
                RefuseLoadError,
            )
            _require(
                abs(ha - cells * 0.01) <= 1e-6,
                f"[consumer_assertion_7] node {nid}: contrib_area_ha {ha} != cells*0.01 "
                f"({cells * 0.01}) within 1e-6",
                RefuseLoadError,
            )

    # -- per-edge assertions 2, 3, synth-separability, 5, 6 --------------------
    for r in edge_recs:
        eid = int(r["edge_id"])
        src, order = str(r["edge_source"]), str(r["order"])
        f, t = int(r["from_node"]), int(r["to_node"])
        _require(
            f != t, f"[consumer_assertion_3] edge {eid}: from_node==to_node=={f}", RefuseLoadError
        )
        length, slope = float(r["length_m"]), float(r["slope_m_per_m"])
        _require(
            math.isfinite(length) and length > 0.0,
            f"[consumer_assertion_3] edge {eid}: length_m={length} must be >0 finite",
            RefuseLoadError,
        )
        _require(
            math.isfinite(slope) and slope >= 0.0,
            f"[consumer_assertion_3] edge {eid}: slope_m_per_m={slope} must be >=0 finite",
            RefuseLoadError,
        )
        _require(
            (src == "synthesised") == (order == "synthetic_connector"),
            f"[consumer_assertion_2] edge {eid}: bidirectional tag violated "
            f"(edge_source={src!r}, order={order!r})",
            RefuseLoadError,
        )
        _require(
            order in ORDERS_VALID and src in EDGE_SOURCES_VALID,
            f"[layers] edge {eid}: enum violation ({src!r}/{order!r})",
            RefuseLoadError,
        )
        conf = str(r["direction_confidence"])
        _require(
            conf in CONFIDENCES_VALID,
            f"[layers] edge {eid}: direction_confidence {conf!r} invalid",
            RefuseLoadError,
        )
        _require(
            r["geometry"] is not None and r["geometry"].geom_type == "LineString",
            f"[layers] edge {eid}: geometry not a LineString",
            RefuseLoadError,
        )

        cap_present = {fld: _nn(r.get(fld)) for fld in CAPACITY_FIELDS}
        any_cap = any(v is not None for v in cap_present.values())
        if src == "synthesised":
            filled = [k for k, v in cap_present.items() if v is not None and k != "capacity_basis"]
            _require(
                not filled,
                f"[synth_separability] edge {eid}: synthetic connector carries invented "
                f"hydraulics {filled} (rule 1)",
                RefuseLoadError,
            )
            _require(
                cap_present["capacity_basis"] == SYNTHETIC_CAPACITY_BASIS,
                f"[synth_separability] edge {eid}: capacity_basis must be the exact "
                f"disclaimer text, got {cap_present['capacity_basis']!r}",
                RefuseLoadError,
            )
        elif any_cap:
            unfilled = [k for k, v in cap_present.items() if v is None]
            _require(
                not unfilled,
                f"[capacity_block_consistency] edge {eid}: partially-filled capacity fields "
                f"{unfilled} (either all NULL/blocked or fully populated)",
                RefuseLoadError,
            )
            _check_observed_capacity(eid, r)
        elif str(r.get("capacity_basis") or "").startswith("zero measured slope"):
            pass  # M11 round 2026-08-25: zero-slope observed segment, routing-only, no claim
        # else: fully-NULL observed edge = blocked_missing_design_intensity mode, legal

    # -- V-SYNTHVISIBLE: fraction recomputed FROM THE WRITTEN GPKG -------------
    # Field-level schema first, then this cross-artefact integrity check: the
    # manifest's RULE-1 headline must reproduce from the realized gpkg bytes.
    recomputed_fraction = synthesised_fraction(edge_recs)
    declared_fraction = float(man.get("synthesised_fraction_of_total_length", float("nan")))
    _require(
        abs(recomputed_fraction - declared_fraction) <= 1e-9,
        f"[v-synthvisible] synthesised_fraction_of_total_length: gpkg recompute "
        f"{recomputed_fraction!r} != manifest {declared_fraction!r} (tol 1e-9)",
        RefuseLoadError,
    )

    # -- adjacency --------------------------------------------------------------
    adj = _load_json(adjacency, "adjacency")
    expected_adj_keys = {"nodes", "edges", "topo_order", "cycles", "dropped"}
    _require(
        set(adj.keys()) == expected_adj_keys,
        f"[adjacency] schema keys {sorted(adj.keys())} != {sorted(expected_adj_keys)}",
        RefuseLoadError,
    )
    adj_nodes = adj["nodes"]
    adj_edges = adj["edges"]
    _require(
        set(adj_nodes) == {str(int(r["node_id"])) for r in node_recs},
        "[adjacency] nodes map disagrees with gpkg drain_nodes",
        RefuseLoadError,
    )
    for r in node_recs:
        entry = adj_nodes[str(int(r["node_id"]))]
        _require(
            abs(float(entry["x_m"]) - float(r["x_m"])) <= 1e-9
            and abs(float(entry["y_m"]) - float(r["y_m"])) <= 1e-9,
            f"[adjacency] node {r['node_id']} coordinates disagree with gpkg",
            RefuseLoadError,
        )
    _require(
        set(adj_edges) == {str(int(r["edge_id"])) for r in edge_recs},
        "[adjacency] edges map disagrees with gpkg drain_edges",
        RefuseLoadError,
    )
    for r in edge_recs:
        entry = adj_edges[str(int(r["edge_id"]))]
        _require(
            entry["from"] == int(r["from_node"])
            and entry["to"] == int(r["to_node"])
            and entry["order"] == str(r["order"])
            and entry["edge_source"] == str(r["edge_source"]),
            f"[adjacency] edge {r['edge_id']} endpoints/tags disagree with gpkg",
            RefuseLoadError,
        )
    _require(adj["cycles"] == [], "[adjacency] cycles must be [] post-break", RefuseLoadError)
    _require(
        adj["dropped"]["self_loops"] == [] and adj["dropped"]["zero_length"] == [],
        "[adjacency] dropped.self_loops/zero_length must be [] on a loadable artefact",
        RefuseLoadError,
    )
    topo = [int(x) for x in adj["topo_order"]]
    _require(
        sorted(topo) == sorted(int(r["node_id"]) for r in node_recs),
        "[adjacency] topo_order is not a permutation of the nodes",
        RefuseLoadError,
    )
    pos = {nid: i for i, nid in enumerate(topo)}
    _require(
        all(pos[int(r["from_node"])] < pos[int(r["to_node"])] for r in edge_recs),
        "[adjacency] topo_order not consistent with loaded edge directions",
        RefuseLoadError,
    )

    # -- assertions 4 (recomputed half) + 9 --------------------------------------
    recomputed_fp, _payload = graph_fingerprint_payload(edge_recs)
    _require(
        man.get("graph_fingerprint") == recomputed_fp,
        f"[consumer_assertion_4] graph_fingerprint mismatch: manifest "
        f"{man.get('graph_fingerprint')} != recomputed-from-gpkg {recomputed_fp}",
        RefuseLoadError,
    )

    binding = man.get("elevation_surface_sha256_binding", {}) or {}
    rel, expected_sha = binding.get("path"), binding.get("expected_sha256")
    _require(
        bool(rel) and bool(expected_sha),
        "[consumer_assertion_9] elevation_surface_sha256_binding absent from manifest",
        RefuseLoadError,
    )
    surf = _repo_path(rel)
    _require(
        surf.exists(),
        f"[consumer_assertion_9] bound elevation surface missing: {surf}",
        RefuseLoadError,
    )
    got_sha = _sha256_file(surf)
    _require(
        got_sha == expected_sha,
        f"[consumer_assertion_9] elevation surface bytes changed: expected "
        f"{expected_sha}, got {got_sha} at {surf} (silent-swap guard)",
        RefuseLoadError,
    )
    if str(surf).endswith(ELEVATION_PIN_PATH_SUFFIX):
        _require(
            expected_sha == ELEVATION_SHA_PIN,
            f"[consumer_assertion_9] production surface {ELEVATION_PIN_PATH_SUFFIX} must "
            f"carry the pinned sha {ELEVATION_SHA_PIN}, manifest binds {expected_sha}",
            RefuseLoadError,
        )

    return {
        "nodes_gdf": nodes_gdf,
        "edges_gdf": edges_gdf,
        "adjacency": adj,
        "manifest": man,
    }


# ------------------------------------------------------------------------------- CLI


def _print_rule6_header() -> None:
    sha, dirty, porcelain = _git_state()
    typer.echo(
        f"[rule6] stage=wf1_graph_io_inspect git_sha={sha} git_dirty={dirty} "
        f"porcelain_entries={len(porcelain)} started_utc={_utc_now()} cpu_only=true"
    )


@app.callback()
def _root() -> None:
    """WF-1 drain-graph artefact IO: candidate writer + asserting reader."""


@app.command()
def inspect(
    gpkg: Path = typer.Option(..., "--gpkg", help="Candidate drain_graph.gpkg"),
    adjacency: Path = typer.Option(..., "--adjacency", help="Candidate adjacency JSON"),
    manifest: Path = typer.Option(..., "--manifest", help="Run manifest JSON"),
    allow_disconnected: bool = typer.Option(False, "--allow-disconnected"),
    adjudication_ref: str = typer.Option("", "--adjudication-ref"),
) -> None:
    """Load-time contract inspection: prints PASS or the RefuseLoadError reason.

    Exit codes: 0 pass, 2 refused."""
    _print_rule6_header()
    try:
        art = read_artefact(
            str(gpkg),
            str(adjacency),
            str(manifest),
            allow_disconnected_load=allow_disconnected,
            owner_adjudication_ref=adjudication_ref,
        )
    except RefuseLoadError as exc:
        typer.echo(f"REFUSED: {exc}")
        raise typer.Exit(code=2) from exc
    man = art["manifest"]
    typer.echo("PASS")
    typer.echo(
        f"nodes={len(art['nodes_gdf'])} edges={len(art['edges_gdf'])} "
        f"post_stitch={man.get('component_count_post_stitch')} "
        f"fingerprint={str(man.get('graph_fingerprint'))[:12]}... "
        f"synth_fraction={man.get('synthesised_fraction_of_total_length')}"
    )


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":
    app()
