"""WF-2 surcharge diagnostics — falsifier compare, class split, GT attribution, G2 gate.

Spec §4.6 + §11 (``runs/wf2_design_phase/spec.md``), contract ``g2_anti_vacuity`` /
workflow CONTRACTS (``wf2-coupling.js``). This unit owns everything scored AFTER a
coupled run: the pre-registered falsifier comparison (§11.2), the component-class
split (§11.3), ground-truth attribution with the SUSPICIOUS headline rule, the
surcharge-events CSV serialization (§11.1 exact G2 schema), and the G2 anti-vacuity
verdict + ``score_g2`` CLI.

WHAT EACH NUMBER MEANS (R1 labelling discipline; spec §0/§11.3):

- **Directed node-count split** (55 outfall_terminating / 1666 dead_end on the
  realized graph [F]): a COUNT of nodes that can reach one of the 17 outfall nodes
  following DIRECTED downstream edges — computed live from
  ``DrainGraph.component_class`` at every call, never hardcoded.
- **Length-weighted weak-connectivity fraction** (0.736484 [F], owner-accepted
  2026-08-25): the OBSERVED-LENGTH-weighted share of the network lying in
  components connected to outfall-terminating reaches under WEAK (undirected)
  connectivity — read from the WF-1 build manifest's ``component_policy`` block
  when present, never recomputed here. The two measure different things and the
  difference is itself informative (spec §11.3); both are carried, distinctly
  labelled, in :func:`component_split` output.

WHY THE SPLIT IS MANDATORY: under deviation D-A (no terminal export term in v1)
every captured m³ in a dead-end component must eventually surcharge back —
dead-end-dominated surcharge is EXPECTED BY CONSTRUCTION, which is why
dead-end-dominated ground-truth reproduction is flagged SUSPICIOUS rather than
celebrated, and why :func:`g2_verdict` REFUSES an unsplit input (invariant #13).

PURITY: every public function is a pure function of its arguments plus disk reads
it declares (falsifier JSON, GT manifest+CSV). NOTHING here is tuned against any
target: the falsifier overlap is recorded, not optimized (owner directive R5/R6),
and no function takes a parameter that could move a score toward "pass".

CSV OWNERSHIP: per the build-unit dispatch, THIS module owns the
``products/surcharge_events.csv`` serialization path (:func:`write_surcharge_events_csv`)
and keeps it BYTE-COMPATIBLE with the format ``solver_hook._write_products`` already
emits (header ``node_id,total_returned_m3,first_step,last_step,max_head_m``, rows
sorted by node_id, ``repr()`` float fields, trailing newline, zero-row file allowed)
so the Smoke-phase wiring can repoint the driver here without touching any green
test. ``solver_hook.py`` is another unit's file and is not modified.

Ground-truth join reality (inspected this session, adapted — see build report):
``runs/groundtruth/manifest.json`` carries NO inline points; it declares
``csv_path`` -> ``data/raw/groundtruth/sept2022_points.csv`` with WGS84 lat/lon and
``points_count: 24`` [F]. Attribution therefore (a) reads points through the
manifest's declared pointer, (b) asserts the declared count against the parsed
rows (V8 consumer-side), (c) reprojects EPSG:4326 -> the graph's pinned CRS
(``CRS_EXPECTED``, EPSG:32643) with pyproj. Node positions come from the loaded
:class:`~jalaladhar.coupling.router.DrainGraph` (cell-centre reconstruction from
``node_cell_row/col`` + the producer-declared grid transform, disclosed quantization
<= res*sqrt(2)/2) or from explicit ``node_xy_m`` overrides.

MODES (owner ruling D-GT 2026-08-26): :func:`attribute_ground_truth_edges` is
the DEFAULT — each GT point joins its nearest drain EDGE (true multi-vertex
polyline, ties -> lowest edge_id) within an INCLUSIVE geometry-anchored radius,
and that edge's DOWNSTREAM node is the responsible node. The older
:func:`attribute_ground_truth` (nearest surcharging NODE within radius) is
RETAINED but SUPERSEDED-BY-RULING-2026-08-26: regression/A-B evidence only.
The SUSPICIOUS dead-end-share rule is byte-identical in both modes.

CPU-only throughout (V12). Python 3.11, type hints.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from jaladhar.coupling.router import DrainGraph
from jaladhar.drainage.graph_io import CRS_EXPECTED

app = typer.Typer(add_completion=False)


@app.callback()
def _root() -> None:
    """WF-2 surcharge diagnostics CLI (keeps 'score_g2' an explicit subcommand)."""


__all__ = [
    "COMPONENT_CLASSES",
    "G2_MIN_RETURNED_M3_DEFAULT",
    "SURCHARGE_EVENTS_SCHEMA",
    "SUSPICIOUS_DEADEND_SHARE_DEFAULT",
    "DiagnosticsRefusal",
    "FalsifierSet",
    "attribute_ground_truth",
    "attribute_ground_truth_edges",
    "compare_falsifier",
    "component_split",
    "g2_verdict",
    "load_drain_edges_geoms",
    "load_falsifier_set",
    "read_surcharge_events_csv",
    "score_g2",
    "sha256_file",
    "write_surcharge_events_csv",
]

# EXACT G2 per-node table schema (contract g2_anti_vacuity.per_node_table; §11.1).
# G2 consumes exactly this file; a zero-row file is allowed (then G2 fails loudly).
SURCHARGE_EVENTS_SCHEMA: tuple[str, ...] = (
    "node_id",
    "total_returned_m3",
    "first_step",
    "last_step",
    "max_head_m",
)

# Component-class labels (producer: router.DrainGraph.component_class).
COMPONENT_CLASSES: tuple[str, str] = ("outfall_terminating", "dead_end")

# Contract anti-vacuity floor (config diagnostics.g2_min_returned_m3 default).
G2_MIN_RETURNED_M3_DEFAULT: float = 1.0
# SUSPICIOUS threshold (config diagnostics.suspicious_deadend_share default).
SUSPICIOUS_DEADEND_SHARE_DEFAULT: float = 0.5


class DiagnosticsRefusal(ValueError):
    """A diagnostics input is absent or malformed — refuse loudly, never guess.

    Raised on: missing falsifier set / schema mismatch (invariant #12 red target),
    drifted CSV headers, GT manifests whose declared count disagrees with the
    parsed rows, unsplit G2 inputs (invariant #13 red target), and attribution
    calls with no node geometry. Subclasses ValueError so generic guards catch it.
    """


# ---------------------------------------------------------------------------
# Falsifier set (§11.2): loaded at run START, recorded, NEVER tuned
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FalsifierSet:
    """The pre-registered surcharge prediction set (spec §4.6 fields verbatim).

    Built ONLY by :func:`load_falsifier_set`, which validates internal
    consistency before construction — an instance of this class is therefore a
    declaration WITH a verified backing file behind it.
    """

    n_predicted_edges: int  # 70 on the realized set [F]
    predicted_node_ids: tuple[int, ...]  # 116 unique, ascending
    edges: tuple[dict[str, Any], ...]  # 70 flagged-edge records from gpkg bytes
    source_gpkg_sha256: str  # sha256 of the gpkg bytes the set was recorded from

    @property
    def n_predicted_nodes(self) -> int:
        return len(self.predicted_node_ids)


_FALSIFIER_REQUIRED_KEYS: tuple[str, ...] = (
    "n_predicted_edges",
    "predicted_node_ids",
    "edges",
    "source_gpkg_sha256",
)
_EDGE_REQUIRED_KEYS: tuple[str, ...] = ("edge_id", "from_node", "to_node")


def sha256_file(path: Path) -> str:
    """sha256 hex digest of a file's bytes (gpkg cross-check helper, §11.2)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_falsifier_set(path: Path) -> FalsifierSet:
    """Load the pre-registered prediction set; REFUSE TO START on absence/mismatch.

    Absent file, unreadable JSON, missing keys, an ``n_predicted_edges`` that
    disagrees with the actual edge list, malformed edge records, non-ascending or
    duplicate ``predicted_node_ids``, or a non-hex ``source_gpkg_sha256`` ALL
    raise :class:`DiagnosticsRefusal` — aggregated where several defects exist
    (rule-7 spirit: all problems named in one refusal).

    Args:
        path: path to ``surcharge_prediction_set.json`` (config key
            ``diagnostics.falsifier_set``).

    Returns:
        The frozen :class:`FalsifierSet`.
    """
    path = Path(path)
    if not path.exists():
        raise DiagnosticsRefusal(
            f"[diagnostics] pre-registered falsifier prediction set NOT FOUND at {path} "
            "(config diagnostics.falsifier_set) — refusing to start (invariant #12)"
        )
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticsRefusal(
            f"[diagnostics] falsifier prediction set at {path} is unreadable: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise DiagnosticsRefusal(
            f"[diagnostics] falsifier prediction set at {path} must be a JSON object, "
            f"got {type(data).__name__}"
        )

    problems: list[str] = []
    for key in _FALSIFIER_REQUIRED_KEYS:
        if key not in data:
            problems.append(f"missing required key '{key}'")

    n_edges = data.get("n_predicted_edges")
    edges_raw = data.get("edges")
    ids_raw = data.get("predicted_node_ids")
    sha_raw = data.get("source_gpkg_sha256")

    if n_edges is not None:
        if not isinstance(n_edges, int) or isinstance(n_edges, bool) or n_edges < 0:
            problems.append(f"'n_predicted_edges' must be a non-negative int, got {n_edges!r}")
    if edges_raw is None:
        pass  # 'missing required key' already recorded above
    elif not isinstance(edges_raw, list):
        problems.append(f"'edges' must be a list, got {type(edges_raw).__name__}")
    elif isinstance(n_edges, int) and not isinstance(n_edges, bool) and len(edges_raw) != n_edges:
        problems.append(
            f"'n_predicted_edges'={n_edges} disagrees with the realized edge list "
            f"length {len(edges_raw)}"
        )
    else:
        for i, e in enumerate(edges_raw):
            if not isinstance(e, dict):
                problems.append(f"'edges[{i}]' must be an object, got {type(e).__name__}")
                continue
            missing = [k for k in _EDGE_REQUIRED_KEYS if k not in e]
            if missing:
                problems.append(f"'edges[{i}]' missing key(s): {missing}")
            else:
                for k in _EDGE_REQUIRED_KEYS:
                    if not isinstance(e[k], int) or isinstance(e[k], bool):
                        problems.append(f"'edges[{i}].{k}' must be an int, got {e[k]!r}")

    if ids_raw is None:
        pass  # already recorded
    elif not isinstance(ids_raw, list) or not ids_raw:
        problems.append(f"'predicted_node_ids' must be a non-empty list, got {ids_raw!r}")
    else:
        bad = [v for v in ids_raw if not isinstance(v, int) or isinstance(v, bool)]
        if bad:
            problems.append(f"'predicted_node_ids' contains non-integers: {bad[:10]}")
        else:
            if len(set(ids_raw)) != len(ids_raw):
                dupes = sorted({i for i in ids_raw if ids_raw.count(i) > 1})
                problems.append(f"'predicted_node_ids' contains duplicates: {dupes[:10]}")
            if ids_raw != sorted(ids_raw):
                problems.append(
                    "'predicted_node_ids' must be ascending (sorted) as recorded; got an "
                    "unsorted list — refusing a mutated/uncanonical set"
                )
    if sha_raw is not None and (
        not isinstance(sha_raw, str)
        or len(sha_raw) != 64
        or any(c not in "0123456789abcdef" for c in sha_raw)
    ):
        problems.append(
            f"'source_gpkg_sha256' must be a 64-char lowercase hex sha256, got {sha_raw!r}"
        )

    if problems:
        raise DiagnosticsRefusal(
            f"[diagnostics] falsifier prediction set at {path} failed schema validation "
            f"with {len(problems)} problem(s):\n" + "\n".join(f"  - {p}" for p in problems)
        )

    return FalsifierSet(
        n_predicted_edges=int(n_edges),
        predicted_node_ids=tuple(int(i) for i in ids_raw),
        edges=tuple(dict(e) for e in edges_raw),
        source_gpkg_sha256=str(sha_raw),
    )


# ---------------------------------------------------------------------------
# Surcharge events CSV (§11.1): EXACT G2 schema, byte-compatible with solver_hook
# ---------------------------------------------------------------------------


def _normalize_event_rows(rows: Iterable[Mapping[str, Any]] | str | Path) -> list[dict[str, Any]]:
    """Coerce event rows (ledger dicts, CSV path, or CSV text source) to typed dicts.

    Ledger rows carry EXTRA continuity fields (§10.4); those are ignored here.
    Missing REQUIRED schema fields aggregate into ONE :class:`DiagnosticsRefusal`.
    """
    if isinstance(rows, (str, Path)):
        p = Path(rows)
        if not p.exists():
            raise DiagnosticsRefusal(f"[diagnostics] surcharge events CSV not found: {p}")
        return read_surcharge_events_csv(p)

    src = list(rows)
    problems: list[str] = []
    out: list[dict[str, Any]] = []
    for i, row in enumerate(src):
        if not isinstance(row, Mapping):
            problems.append(f"row {i} must be a mapping, got {type(row).__name__}")
            continue
        missing = [k for k in SURCHARGE_EVENTS_SCHEMA if k not in row]
        if missing:
            problems.append(f"row {i} missing required field(s) {missing} (exact G2 schema)")
            continue
        try:
            out.append(
                {
                    "node_id": int(row["node_id"]),
                    "total_returned_m3": float(row["total_returned_m3"]),
                    "first_step": int(row["first_step"]),
                    "last_step": int(row["last_step"]),
                    "max_head_m": float(row["max_head_m"]),
                }
            )
        except (TypeError, ValueError) as exc:
            problems.append(f"row {i} has a non-numeric field: {exc}")
    if problems:
        raise DiagnosticsRefusal(
            "[diagnostics] surcharge event rows failed schema validation with "
            f"{len(problems)} problem(s):\n" + "\n".join(f"  - {p}" for p in problems)
        )
    return out


def write_surcharge_events_csv(rows: Iterable[Mapping[str, Any]] | Path | str, path: Path) -> None:
    """Write ``products/surcharge_events.csv`` in the EXACT §11.1 G2 schema.

    Byte-format pinned to what ``solver_hook._write_products`` already emits so the
    existing suite stays green and the Smoke-phase wiring can repoint here:
    header ``node_id,total_returned_m3,first_step,last_step,max_head_m``; rows
    sorted by ``node_id``; ``repr()`` for the two float fields; plain ints for
    step fields; single trailing newline; a ZERO-ROW file is allowed (header only)
    — G2 then fails loudly on it, which is the point of the anti-vacuity gate.

    Raises:
        DiagnosticsRefusal: aggregated, if any row lacks a required schema field.
    """
    norm = _normalize_event_rows(rows)
    path = Path(path)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(SURCHARGE_EVENTS_SCHEMA)] + [
        f"{r['node_id']},{r['total_returned_m3']!r},{r['first_step']},{r['last_step']},"
        f"{r['max_head_m']!r}"
        for r in sorted(norm, key=lambda r: r["node_id"])
    ]
    path.write_text("\n".join(lines) + "\n")


def read_surcharge_events_csv(path: Path) -> list[dict[str, Any]]:
    """Read a surcharge-events CSV back, asserting the EXACT header (byte schema).

    G2 consumes exactly this file, so a drifted header/column order is refused
    rather than silently tolerated.
    """
    path = Path(path)
    text = path.read_text()
    lines = text.splitlines()
    if not lines or lines[0] != ",".join(SURCHARGE_EVENTS_SCHEMA):
        raise DiagnosticsRefusal(
            f"[diagnostics] {path} header {lines[0] if lines else '<empty>'!r} != exact G2 "
            f"schema {','.join(SURCHARGE_EVENTS_SCHEMA)!r} — refusing (G2 consumes this file)"
        )
    reader = csv.DictReader(lines)
    out: list[dict[str, Any]] = []
    problems: list[str] = []
    for i, row in enumerate(reader, start=1):
        try:
            out.append(
                {
                    "node_id": int(row["node_id"]),
                    "total_returned_m3": float(row["total_returned_m3"]),
                    "first_step": int(row["first_step"]),
                    "last_step": int(row["last_step"]),
                    "max_head_m": float(row["max_head_m"]),
                }
            )
        except (TypeError, ValueError) as exc:
            problems.append(f"line {i + 1}: non-numeric field ({exc})")
    if problems:
        raise DiagnosticsRefusal(
            f"[diagnostics] {path}: {len(problems)} malformed row(s):\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
    return out


# ---------------------------------------------------------------------------
# Falsifier comparison (§11.2) — recorded into the run manifest, never tuned
# ---------------------------------------------------------------------------


def compare_falsifier(
    events: Iterable[Mapping[str, Any]] | Path | str,
    fs: FalsifierSet,
    gpkg_sha256_of_loaded_graph: str | None = None,
) -> dict[str, Any]:
    """Predicted-vs-actual surcharge overlap — pure function of events + set.

    Args:
        events: ledger event rows (or an events-CSV path) from the coupled run.
        fs: the pre-registered :class:`FalsifierSet`.
        gpkg_sha256_of_loaded_graph: sha256 of the gpkg bytes behind the LOADED
            graph (``sha256_file(cfg.graph.gpkg)``), when known; ``None`` marks
            the cross-check explicitly unchecked rather than silently skipped.

    Returns:
        Manifest block per spec §11.2: ``n_predicted_edges`` (70 [F]),
        ``n_predicted_nodes`` (116 [F]), ``predicted_nodes_surcharged`` (int),
        ``flagged_edges_whose_downstream_node_surcharged`` (int),
        ``flagged_edges_not_surcharged`` (ids), ``unflagged_surcharging_nodes``
        (ids), and the explicit ``source_gpkg_sha256_crosscheck`` status.
    """
    rows = _normalize_event_rows(events)
    event_nodes = {r["node_id"] for r in rows}
    predicted = set(fs.predicted_node_ids)

    hit_ids = sorted(event_nodes & predicted)
    unflagged_ids = sorted(event_nodes - predicted)
    flagged_hit: list[int] = []
    flagged_miss: list[int] = []
    for e in fs.edges:
        (flagged_hit if e["to_node"] in event_nodes else flagged_miss).append(int(e["edge_id"]))

    if gpkg_sha256_of_loaded_graph is None:
        xstatus = "not_checked_no_loaded_graph_sha_provided"
    elif gpkg_sha256_of_loaded_graph.lower() == fs.source_gpkg_sha256.lower():
        xstatus = "match"
    else:
        xstatus = "MISMATCH"

    return {
        "n_predicted_edges": fs.n_predicted_edges,
        "n_predicted_nodes": fs.n_predicted_nodes,
        "predicted_nodes_surcharged": len(hit_ids),
        "predicted_nodes_surcharged_ids": hit_ids,
        "flagged_edges_whose_downstream_node_surcharged": len(flagged_hit),
        "flagged_edge_ids_whose_downstream_node_surcharged": flagged_hit,
        "flagged_edges_not_surcharged": flagged_miss,
        "unflagged_surcharging_nodes": unflagged_ids,
        "n_surcharging_events_total": len(rows),
        "source_gpkg_sha256_crosscheck": {
            "falsifier_set_source_gpkg_sha256": fs.source_gpkg_sha256,
            "loaded_graph_gpkg_sha256": gpkg_sha256_of_loaded_graph,
            "status": xstatus,
        },
        "note": (
            "pre-registered prediction set recorded BEFORE any coupled run; this "
            "comparison is RECORDED, never tuned (owner directive R5/R6). Strong "
            "overlap corroborates the mechanism; a disjoint set means the coupling "
            "is finding something else — diagnostic either way."
        ),
    }


# ---------------------------------------------------------------------------
# Component-class split (§11.3) — both headline fractions, distinctly labelled
# ---------------------------------------------------------------------------


def component_split(
    events: Iterable[Mapping[str, Any]] | Path | str, graph: DrainGraph
) -> dict[str, Any]:
    """Partition surcharge events by directed-reachability component class.

    Every event joins ``graph.component_class`` (index convention: tensor index =
    node_id - 1). Output carries per-class ``{n_events, returned_m3}``, the LIVE
    directed node-count split measured from the loaded graph, and — when the
    graph manifest carries it — the length-weighted weak-connectivity fraction
    from the WF-1 ``component_policy`` block. The two figures measure DIFFERENT
    things (see module docstring) and are labelled separately; neither is
    hardcoded (their realized values live in each run's manifest, never here).
    """
    rows = _normalize_event_rows(events)
    classes = list(graph.component_class)
    per_class: dict[str, dict[str, Any]] = {
        cls: {"n_events": 0, "returned_m3": 0.0} for cls in COMPONENT_CLASSES
    }
    for r in rows:
        idx = r["node_id"] - 1
        if not 0 <= idx < len(classes):
            raise DiagnosticsRefusal(
                f"[diagnostics] event node_id {r['node_id']} outside 1..{len(classes)} "
                "(event/graph mismatch — wrong run scored?)"
            )
        cls = classes[idx]
        bucket = per_class.setdefault(cls, {"n_events": 0, "returned_m3": 0.0})
        bucket["n_events"] += 1
        bucket["returned_m3"] += r["total_returned_m3"]

    directed_counts = {cls: classes.count(cls) for cls in COMPONENT_CLASSES}
    policy = (graph.manifest or {}).get("component_policy") or {}
    length_fraction = policy.get("outfall_terminating_observed_length_fraction")
    # WF-2 M1: when the loaded graph carries the terminal-seed echo, the labels
    # name the ACTUAL definition_version + rule counts in effect instead of the
    # stale hardcoded '55/1666 + 0.736484' narrative. Class LABELS are
    # unchanged; historical manifests without the echo keep the v1 wording.
    term_echo = (graph.manifest or {}).get("terminal_seed_resolution") or {}
    def_version = term_echo.get("definition_version")

    basis = "graph.component_class (DIRECTED reachability to an outfall node)"
    note = (
        "directed split computed live from the loaded graph; the length-weighted "
        "weak-connectivity fraction is read from the WF-1 component_policy block "
        "when present — both figures measured, never hardcoded"
    )
    seed_block: dict[str, Any] | None = None
    if def_version:
        counts_by_rule = term_echo.get("counts_by_rule") or {}
        seeds_by_rule = term_echo.get("seeds_by_rule") or {}
        basis = (
            "graph.component_class (DIRECTED reachability to a terminal seed; "
            f"terminal definition {def_version}: declared_gpkg_outfall "
            f"{counts_by_rule.get('declared_gpkg_outfall', 0)} + lake_polygon "
            f"{counts_by_rule.get('lake_polygon', 0)} + domain_boundary "
            f"{counts_by_rule.get('domain_boundary', 0)})"
        )
        note = (
            f"terminal definition {def_version} in effect at load (consumer-side "
            "seeds; see configs/terminal_definition.yaml): directed split computed "
            "live from the loaded graph under the EXTENDED seed set; the "
            "length-weighted fraction beside it stays the WF-1 component_policy "
            "value computed under DECLARED-ONLY seeds — the two figures use "
            "different definitions and are labelled as such"
        )
        seed_block = {
            "definition_version": def_version,
            "counts_by_rule": counts_by_rule,
            "seeds_by_rule": seeds_by_rule,
        }

    return {
        "basis": basis,
        "per_class": per_class,
        "totals": {
            "n_events": len(rows),
            "returned_m3": sum(r["total_returned_m3"] for r in rows),
        },
        **({"terminal_seed_resolution": seed_block} if seed_block is not None else {}),
        "headline_fractions": {
            "directed_node_count_split": {
                "counts": directed_counts,
                "measures": (
                    "COUNT of nodes reaching a terminal outfall seed following directed "
                    "downstream edges (node-count basis; computed live from the loaded graph)"
                ),
                "source": "DrainGraph.component_class (router._compute_component_classes)",
            },
            "length_weighted_weak_connectivity_outfall_fraction": length_fraction,
            "length_weighted_measures": (
                "observed-LENGTH-weighted fraction of the network weak-connected "
                "(undirected) to outfall-terminating reaches — a different quantity "
                "from the node count; the difference is itself informative (§11.3)"
            ),
            "length_weighted_source": (
                "graph manifest component_policy.outfall_terminating_observed_length_fraction"
                if length_fraction is not None
                else "ABSENT: graph manifest carries no component_policy block"
            ),
            "note": note,
        },
    }


# ---------------------------------------------------------------------------
# Ground-truth attribution (§11.3) + SUSPICIOUS headline rule
# ---------------------------------------------------------------------------

_WGS84_TO_GRAPH = None  # lazily-built pyproj transformer (module-level cache)


def _transformer():
    global _WGS84_TO_GRAPH
    if _WGS84_TO_GRAPH is None:
        from pyproj import Transformer

        _WGS84_TO_GRAPH = Transformer.from_crs("EPSG:4326", CRS_EXPECTED, always_xy=True)
    return _WGS84_TO_GRAPH


def _load_gt_points(gt_manifest_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read GT points through the manifest's declared ``csv_path`` pointer.

    The manifest declares ``csv_path`` and ``points_count`` [F]; the parsed row
    count is asserted against the DECLARED count (V8 consumer-side). Relative
    csv_paths resolve against the manifest's own directory (self-contained
    bundle). Optional ``x_m``/``y_m`` columns (projected, ``CRS_EXPECTED``) are
    used verbatim when present — the realized Sept-2022 CSV has none, so the
    production path is the lat/lon reprojection.
    """
    gt_manifest_path = Path(gt_manifest_path)
    if not gt_manifest_path.exists():
        raise DiagnosticsRefusal(
            f"[diagnostics] ground-truth manifest not found: {gt_manifest_path}"
        )
    try:
        man = json.loads(gt_manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticsRefusal(
            f"[diagnostics] ground-truth manifest {gt_manifest_path} unreadable: {exc}"
        ) from exc
    csv_rel = man.get("csv_path")
    if not csv_rel:
        raise DiagnosticsRefusal(
            f"[diagnostics] ground-truth manifest {gt_manifest_path} lacks 'csv_path'"
        )
    csv_path = Path(csv_rel)
    if not csv_path.is_absolute():
        csv_path = gt_manifest_path.parent / csv_path
    if not csv_path.exists():
        raise DiagnosticsRefusal(
            f"[diagnostics] ground-truth CSV declared at {csv_path} (via manifest csv_path) "
            "not found"
        )

    with open(csv_path, newline="") as f:
        rdr = csv.DictReader(f)
        cols = rdr.fieldnames or []
        colset = set(cols)
        has_xy = {"x_m", "y_m"}.issubset(colset)
        has_ll = {"lat", "lon"}.issubset(colset)
        if "id" not in colset or not (has_xy or has_ll):
            raise DiagnosticsRefusal(
                f"[diagnostics] GT CSV {csv_path} must carry 'id' plus either lat/lon or "
                f"x_m/y_m columns; realized header: {cols}"
            )
        pts: list[dict[str, Any]] = []
        problems: list[str] = []
        for row in rdr:
            pid = (row.get("id") or "").strip()
            if not pid:
                continue
            try:
                rec: dict[str, Any] = {
                    "point_id": pid,
                    "location_name": (row.get("location_name") or "").strip(),
                }
                if has_xy:
                    rec["x_m"] = float(row["x_m"])
                    rec["y_m"] = float(row["y_m"])
                    rec["crs"] = CRS_EXPECTED
                else:
                    lat = float(row["lat"])
                    lon = float(row["lon"])
                    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                        problems.append(f"{pid}: lat/lon out of range ({lat}, {lon})")
                        continue
                    x, y = _transformer().transform(lon, lat)
                    rec.update(lat=lat, lon=lon, x_m=x, y_m=y, crs=CRS_EXPECTED)
                pts.append(rec)
            except (TypeError, ValueError) as exc:
                problems.append(f"{pid}: bad coordinate field ({exc})")
    if problems:
        raise DiagnosticsRefusal(
            f"[diagnostics] GT CSV {csv_path}: {len(problems)} problem(s):\n"
            + "\n".join(f"  - {p}" for p in problems)
        )

    declared = man.get("points_count")
    if declared is not None and int(declared) != len(pts):
        raise DiagnosticsRefusal(
            f"[diagnostics] GT manifest declares points_count={declared} but the CSV at "
            f"{csv_path} yielded {len(pts)} parseable points — refusing a moved baseline"
        )
    return pts, {"manifest_status": man.get("status"), "csv_path": str(csv_path)}


def _resolve_node_geometry(
    graph: DrainGraph | None,
    node_xy_m: (
        Mapping[int, tuple[float, float]] | Sequence[Sequence[float]] | Sequence[float] | None
    ),
    node_class: Mapping[int, str] | Sequence[str] | None,
) -> tuple[Any, Any, str]:
    """Resolve node geometry into ``pos(node_id)`` / ``class(node_id)`` accessors.

    Three accepted sources, in priority order:
    1. the loaded :class:`DrainGraph` (preferred): cell-centre reconstruction from
       ``node_cell_row/col`` + the producer-declared grid transform; classes from
       ``component_class``;
    2. explicit DENSE ``(N,2)`` positions + per-node class list (index = id - 1);
    3. explicit SPARSE mappings ``{node_id: (x, y)}`` and ``{node_id: class}``
       for scoring a subset of nodes without materializing the full graph.

    Raises:
        DiagnosticsRefusal: when no geometry source is usable, when classes are
            missing (the dead-end share — hence SUSPICIOUS — would be
            uncomputable), or on shape disagreements.
    """
    if graph is not None:
        grid = ((graph.manifest or {}).get("config_snapshot") or {}).get("grid") or {}
        if "transform" not in grid:
            raise DiagnosticsRefusal(
                "[diagnostics] graph manifest config_snapshot.grid missing 'transform' — "
                "cannot reconstruct node positions for attribution"
            )
        tr = grid["transform"]
        res, x0, ytop = float(tr[0]), float(tr[2]), float(tr[5])
        xs = [x0 + int(c) * res + res / 2.0 for c in graph.node_cell_col.tolist()]
        ys = [ytop - int(r) * res - res / 2.0 for r in graph.node_cell_row.tolist()]
        classes = list(graph.component_class)
        note = (
            "positions reconstructed from graph.node_cell_row/col cell CENTRES "
            "(producer-declared grid transform); quantization <= res*sqrt(2)/2 vs true "
            f"node coordinates (res={res:g} m), inside the declared attribution-radius "
            "assumption band"
        )

        def pos(nid: int) -> tuple[float, float]:
            if not 1 <= nid <= len(xs):
                raise DiagnosticsRefusal(
                    f"[diagnostics] event node_id {nid} outside 1..{len(xs)} "
                    "(event/graph mismatch — wrong run scored?)"
                )
            return xs[nid - 1], ys[nid - 1]

        def cls(nid: int) -> str:
            if not 1 <= nid <= len(classes):
                raise DiagnosticsRefusal(f"[diagnostics] node_id {nid} outside graph")
            return classes[nid - 1]

        return pos, cls, note

    if isinstance(node_xy_m, Mapping):
        if not isinstance(node_class, Mapping):
            raise DiagnosticsRefusal(
                "[diagnostics] sparse node_xy_m mapping requires a sparse node_class "
                "mapping {node_id: class} — the dead-end share (and hence SUSPICIOUS) "
                "is UNCOMPUTABLE otherwise; refusing rather than reporting an unsplit "
                "number (invariant #13 spirit)"
            )

        def pos_s(nid: int) -> tuple[float, float]:
            if nid not in node_xy_m:
                raise DiagnosticsRefusal(
                    f"[diagnostics] node_id {nid} has no entry in the sparse node_xy_m "
                    "mapping — cannot attribute it without fabricating a position"
                )
            p = node_xy_m[nid]
            return float(p[0]), float(p[1])

        def cls_s(nid: int) -> str:
            if nid not in node_class:
                raise DiagnosticsRefusal(
                    f"[diagnostics] node_id {nid} missing from sparse node_class mapping"
                )
            return str(node_class[nid])

        return pos_s, cls_s, "positions/classes taken verbatim from caller-provided sparse maps"

    if node_xy_m is not None:
        pairs = [tuple(p) for p in node_xy_m]  # type: ignore[arg-type]
        if not pairs or any(len(p) != 2 for p in pairs):
            raise DiagnosticsRefusal("[diagnostics] node_xy_m must be an (N,2) sequence")
        if node_class is None or isinstance(node_class, Mapping):
            raise DiagnosticsRefusal(
                "[diagnostics] explicit node_xy_m given without node_class (per-node "
                "classes) — the dead-end share (and hence SUSPICIOUS) is UNCOMPUTABLE; "
                "refusing rather than reporting an unsplit number (invariant #13 spirit)"
            )
        if len(node_class) != len(pairs):
            raise DiagnosticsRefusal(
                f"[diagnostics] node_class length {len(node_class)} != node count {len(pairs)}"
            )
        xs_d = [float(p[0]) for p in pairs]
        ys_d = [float(p[1]) for p in pairs]
        cls_l = [str(c) for c in node_class]

        def pos_d(nid: int) -> tuple[float, float]:
            if not 1 <= nid <= len(xs_d):
                raise DiagnosticsRefusal(
                    f"[diagnostics] event node_id {nid} outside 1..{len(xs_d)} "
                    "(event/geometry mismatch — wrong run scored?)"
                )
            return xs_d[nid - 1], ys_d[nid - 1]

        def cls_d(nid: int) -> str:
            if not 1 <= nid <= len(cls_l):
                raise DiagnosticsRefusal(f"[diagnostics] node_id {nid} outside geometry")
            return cls_l[nid - 1]

        return pos_d, cls_d, "positions taken verbatim from caller-provided dense node_xy_m"

    raise DiagnosticsRefusal(
        "[diagnostics] no node geometry available: pass the loaded DrainGraph (preferred), "
        "explicit node_xy_m + node_class, or sparse {node_id: ...} maps — attributing GT "
        "points without node positions would fabricate the join"
    )


def attribute_ground_truth(
    events: Iterable[Mapping[str, Any]] | Path | str,
    gt_manifest_path: Path,
    radius_m: float = 100.0,
    *,
    graph: DrainGraph | None = None,
    node_xy_m: Mapping[int, tuple[float, float]] | Sequence[Sequence[float]] | None = None,
    node_class: Mapping[int, str] | Sequence[str] | None = None,
    suspicious_deadend_share: float = SUSPICIOUS_DEADEND_SHARE_DEFAULT,
    radius_basis: str | None = None,
) -> dict[str, Any]:
    """Match GT flood points to surcharging nodes; report the class split + flag.

    SUPERSEDED-BY-RULING-2026-08-26 for production attribution (edge mode is the
    default); RETAINED for regression/A-B evidence. A GT point is REPRODUCED iff
    it lies within ``radius_m`` (**inclusive boundary**) of at least one
    surcharging node; its recorded match is the NEAREST such node (ties ->
    lowest node_id, OQ1 pin). The DEFAULT ``radius_m=100.0`` is the HISTORICAL
    v1 node-mode pin — production callers pass the config-declared leaf
    ``diagnostics.attribution_radius_node_m`` explicitly (round-3 fix C2/D2:
    the shared ``attribution_radius_m`` leaf now carries the EDGE-mode radius,
    so dispatching on it here would silently confound every A/B against
    history). Per-point records carry the nearest-node id + its component
    class. The dead-end share is computed over the SET of GT-matched nodes
    (each node's returned volume counted ONCE, no double-counting through
    points sharing a node) and the SUSPICIOUS flag fires when that share
    STRICTLY exceeds ``suspicious_deadend_share``.

    Args:
        events: surcharge event rows (ledger dicts or CSV path).
        gt_manifest_path: the ground-truth manifest (declares ``csv_path`` +
            ``points_count``; realized file: 24 points [F]).
        radius_m: attribution radius [m] — declared assumption ‡; historical
            v1 node-mode pin 100.0 (config leaf
            ``diagnostics.attribution_radius_node_m``).
        graph: the loaded :class:`DrainGraph` (preferred geometry source).
        node_xy_m: explicit projected positions overriding ``graph`` — either a
            DENSE ``(N,2)`` sequence (index = id - 1) or a SPARSE
            ``{node_id: (x, y)}`` mapping.
        node_class: per-node classes matching ``node_xy_m``'s form (list, or
            sparse ``{node_id: class}``). Required: without classes the
            dead-end share — hence SUSPICIOUS — is uncomputable and we refuse.
        suspicious_deadend_share: SUSPICIOUS threshold (config
            ``diagnostics.suspicious_deadend_share``, default 0.5).
        radius_basis: provenance string echoed into the block (D9 symmetry:
            both modes' manifest blocks record mode + effective radius + basis).

    Returns:
        Manifest block: per-point matches, matched-node set, returned volume by
        class over matched nodes, ``dead_end_share_of_gt_matched_returned_volume``
        (``None`` when nothing matched — NOT_ASSESSED, honestly), and the
        ``suspicious`` boolean + ``suspicious_state`` string.
    """
    rows = _normalize_event_rows(events)
    pos_of, class_of, geom_note = _resolve_node_geometry(graph, node_xy_m, node_class)
    pts, gt_prov = _load_gt_points(Path(gt_manifest_path))
    surged = sorted(rows, key=lambda r: r["node_id"])  # ascending => stable lowest-id ties
    vol_by_node = {r["node_id"]: r["total_returned_m3"] for r in rows}

    per_point: list[dict[str, Any]] = []
    matched_nodes: set[int] = set()
    for pt in pts:
        best_nid: int | None = None
        best_d: float | None = None
        for r in surged:
            nid = r["node_id"]
            nx, ny = pos_of(nid)
            d = math.hypot(nx - pt["x_m"], ny - pt["y_m"])
            if d <= radius_m and (best_d is None or d < best_d):
                best_d, best_nid = d, nid
        if best_nid is None:
            per_point.append(
                {
                    "point_id": pt["point_id"],
                    "location_name": pt.get("location_name", ""),
                    "x_m": pt["x_m"],
                    "y_m": pt["y_m"],
                    "matched": False,
                    "nearest_node_id": None,
                    "nearest_distance_m": None,
                    "nearest_node_class": None,
                }
            )
        else:
            matched_nodes.add(best_nid)
            per_point.append(
                {
                    "point_id": pt["point_id"],
                    "location_name": pt.get("location_name", ""),
                    "x_m": pt["x_m"],
                    "y_m": pt["y_m"],
                    "matched": True,
                    "nearest_node_id": best_nid,
                    "nearest_distance_m": best_d,
                    "nearest_node_class": class_of(best_nid),
                }
            )

    vol_by_class = {cls: 0.0 for cls in COMPONENT_CLASSES}
    for nid in matched_nodes:
        vol_by_class[class_of(nid)] += vol_by_node[nid]
    total_matched_vol = sum(vol_by_class.values())
    share: float | None = (
        vol_by_class["dead_end"] / total_matched_vol if total_matched_vol > 0.0 else None
    )
    suspicious = bool(share is not None and share > suspicious_deadend_share)
    state = "NOT_ASSESSED" if share is None else ("TRUE" if suspicious else "FALSE")

    return {
        "attribution_mode": "node",
        "radius_m_declared_assumption": radius_m,
        "radius_basis": radius_basis or "",
        "suspicious_deadend_share_threshold": suspicious_deadend_share,
        "n_gt_points": len(pts),
        "n_gt_matched": len(per_point) - sum(1 for p in per_point if not p["matched"]),
        "points": per_point,
        "gt_matched_nodes": sorted(matched_nodes),
        "returned_m3_by_class_of_gt_matched_nodes": vol_by_class,
        "dead_end_share_of_gt_matched_returned_volume": share,
        "suspicious": suspicious,
        "suspicious_state": state,
        "ground_truth_provenance": gt_prov,
        "node_geometry_note": geom_note,
        "measure_labels": {
            "dead_end_share": (
                "share of returned volume attributed to DEAD_END-class GT-matched nodes; "
                ">threshold => SUSPICIOUS because dead-end surcharge happens BY "
                "CONSTRUCTION (D-A: no outlet) — a finding to report, never a result "
                "to celebrate (§11.3)"
            ),
        },
    }


# ---------------------------------------------------------------------------
# Ground-truth EDGE attribution (owner ruling D-GT 2026-08-26) — the DEFAULT mode
# ---------------------------------------------------------------------------


def load_drain_edges_geoms(
    gpkg_path: Path, layer: str = "drain_edges"
) -> tuple[dict[int, Any], dict[int, int], dict[int, int], str]:
    """Load TRUE multi-vertex drain-edge polylines from the WF-1 artefact.

    Returns ``(geom_by_edge_id, from_node_by_id, to_node_by_id, crs_str)``.
    NO straight-segment approximation is permitted (ruling D-GT): the gpkg's
    LineString geometries are used verbatim. Refuses a layer whose CRS is not
    metre-projected and any row with a missing/empty geometry — both aggregated
    into one :class:`DiagnosticsRefusal`.
    """
    import geopandas as gpd

    gpkg_path = Path(gpkg_path)
    if not gpkg_path.exists():
        raise DiagnosticsRefusal(f"[diagnostics] drain graph gpkg not found: {gpkg_path}")
    edges = gpd.read_file(gpkg_path, layer=layer)
    problems: list[str] = []
    crs = str(getattr(edges, "crs", None) or "")
    from pyproj import CRS as _CRS

    try:
        c = _CRS.from_user_input(crs) if crs else None
        metric = (
            bool(c)
            and (not c.is_geographic)
            and all(str(ax.unit_name).lower() in ("metre", "meter") for ax in c.axis_info)
        )
    except Exception:
        metric = False
    if not metric:
        problems.append(
            f"gpkg layer '{layer}' CRS {crs!r} is not metre-projected — edge distances "
            "would be degrees, refusing"
        )
    for col in ("edge_id", "from_node", "to_node", "geometry"):
        if col not in edges.columns:
            problems.append(f"gpkg layer '{layer}' lacks required column '{col}'")
    if problems:
        raise DiagnosticsRefusal(
            f"[diagnostics] drain-edge geometry load failed with {len(problems)} problem(s):\n"
            + "\n".join(f"  - {p}" for p in problems)
        )

    geom_by_id: dict[int, Any] = {}
    from_by_id: dict[int, int] = {}
    to_by_id: dict[int, int] = {}
    for row in edges.itertuples(index=False):
        eid = int(row.edge_id)
        g = row.geometry
        if g is None or getattr(g, "is_empty", True):
            problems.append(f"edge {eid}: missing/empty geometry — cannot attribute to it")
            continue
        geom_by_id[eid] = g
        from_by_id[eid] = int(row.from_node)
        to_by_id[eid] = int(row.to_node)
    if problems:
        raise DiagnosticsRefusal(
            f"[diagnostics] drain-edge geometries malformed ({len(problems)} problem(s)):\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
    return geom_by_id, from_by_id, to_by_id, crs


def attribute_ground_truth_edges(
    events: Iterable[Mapping[str, Any]] | Path | str,
    gt_manifest_path: Path,
    radius_m: float,
    edge_geoms: Mapping[int, Any],
    *,
    responsible_node_of_edge: Mapping[int, int],
    expected_edge_count: int | None = None,
    graph: DrainGraph | None = None,
    node_class: Mapping[int, str] | None = None,
    suspicious_deadend_share: float = SUSPICIOUS_DEADEND_SHARE_DEFAULT,
    radius_basis: str | None = None,
    edge_crs: str | None = None,
) -> dict[str, Any]:
    """Ruling D-GT 2026-08-26: match GT points to the NEAREST drain EDGE; the
    responsible node is that edge's DOWNSTREAM node (``to_node``) — "water
    surcharges at a node and runs along the reach".

    A GT point is attributed to its globally nearest edge polyline (TRUE
    multi-vertex LineStrings; ties -> lowest edge_id, the OQ1 convention
    carried over from node mode); it counts as MATCHED iff that distance is
    <= ``radius_m`` (**inclusive boundary**) AND the responsible node appears
    in the surcharge events. The dead-end share is computed over the SET of
    matched responsible nodes (each node's returned volume counted ONCE) and
    the SUSPICIOUS rule is BYTE-IDENTICAL to :func:`attribute_ground_truth`:
    fires strictly above ``suspicious_deadend_share``; zero matches =>
    NOT_ASSESSED.

    Refusals (aggregated, rule-7 spirit): ``len(edge_geoms) !=
    expected_edge_count``; missing/empty geometry under any edge id; an edge
    id absent from ``responsible_node_of_edge``; a declared ``edge_crs`` that
    is not metre-projected; an event node absent from the class source
    (graph range or ``node_class`` mapping); GT manifest count mismatch
    (inherited from the shared bundle loader).

    Args:
        events: surcharge event rows (ledger dicts or CSV path).
        gt_manifest_path: the ground-truth manifest (declares ``csv_path`` +
            ``points_count``; realized file: 24 points [F]).
        radius_m: attribution radius [m] — inclusive.
        edge_geoms: ``{edge_id: shapely LineString}`` in the graph CRS.
        responsible_node_of_edge: ``{edge_id: node_id}`` — the DOWNSTREAM node
            of each edge (pass ``from_node`` values only in the recorded V5
            mutation probe, never in production).
        expected_edge_count: refuse when the realized edge-set size differs
            (config ``graph.expected_counts.edges``).
        graph: loaded :class:`DrainGraph` — classes from ``component_class``
            (preferred).
        node_class: sparse ``{node_id: class}`` alternative when no graph is
            loaded.
        suspicious_deadend_share: SUSPICIOUS threshold (config default 0.5).
        radius_basis: provenance string echoed into the block (config
            ``diagnostics.attribution_radius_basis``).
        edge_crs: declared CRS string of ``edge_geoms``; refused when
            geographic/unprojected.

    V5 MUTATION PROBE (recorded, tests/coupling/test_diagnostics.py): passing
    ``responsible_node_of_edge`` built from ``from_node`` instead of
    ``to_node`` must redden the near-dead-end-reach test — constructed there
    so the wrong-end node is outfall-class AND unsurcharged, moving
    n_gt_matched / share / suspicious_state observably.

    V7 scope: the toy-graph tests cover <= 4 edges = PARTIAL by construction;
    the realized 24-point claim closes ONLY via
    scripts/wf2_gt_edge_attribution_eval.py on the real 1587-edge graph.
    """
    rows = _normalize_event_rows(events)
    pts, gt_prov = _load_gt_points(Path(gt_manifest_path))

    # --- structural validation, aggregated ------------------------------------
    problems: list[str] = []
    if expected_edge_count is not None and len(edge_geoms) != int(expected_edge_count):
        problems.append(
            f"realized edge-set size {len(edge_geoms)} != expected_counts.edges "
            f"{int(expected_edge_count)} — wrong/incomplete artefact, refusing"
        )
    bad_geom = sorted(
        eid for eid, g in edge_geoms.items() if g is None or getattr(g, "is_empty", True)
    )
    if bad_geom:
        problems.append(f"edge(s) {bad_geom[:10]} carry missing/empty geometry — cannot join")
    missing_resp = sorted(eid for eid in edge_geoms if eid not in responsible_node_of_edge)
    if missing_resp:
        problems.append(
            f"edge(s) {missing_resp[:10]} absent from responsible_node_of_edge — the "
            "downstream-node join would be fabricated"
        )
    if edge_crs:
        try:
            from pyproj import CRS as _CRS

            c = _CRS.from_user_input(edge_crs)
            metric = (not c.is_geographic) and all(
                str(ax.unit_name).lower() in ("metre", "meter") for ax in c.axis_info
            )
        except Exception:
            metric = False
        if not metric:
            problems.append(
                f"edge_crs {edge_crs!r} is not metre-projected — point-to-polyline "
                "distances would be degrees, refusing"
            )
    if problems:
        raise DiagnosticsRefusal(
            "[diagnostics] edge attribution inputs failed validation with "
            f"{len(problems)} problem(s):\n" + "\n".join(f"  - {p}" for p in problems)
        )

    # --- class source resolution (no positions needed in edge mode) -----------
    classes_seq: list[str] | None = None
    if graph is not None:
        classes_seq = list(graph.component_class)

        def cls_of(nid: int) -> str | None:
            if not 1 <= nid <= len(classes_seq):
                return None
            return classes_seq[nid - 1]

    elif isinstance(node_class, Mapping):

        def cls_of(nid: int) -> str | None:
            return str(node_class[nid]) if nid in node_class else None

    else:
        raise DiagnosticsRefusal(
            "[diagnostics] edge attribution needs a class source: pass the loaded "
            "DrainGraph (preferred) or a sparse node_class {node_id: class} mapping — "
            "the dead-end share (and hence SUSPICIOUS) is UNCOMPUTABLE otherwise"
        )

    vol_by_node = {r["node_id"]: r["total_returned_m3"] for r in rows}
    unclassifiable_events = sorted(nid for nid in vol_by_node if cls_of(nid) is None)
    if unclassifiable_events:
        raise DiagnosticsRefusal(
            f"[diagnostics] event node(s) {unclassifiable_events[:10]} absent from the "
            "graph/class source — event/graph mismatch (wrong run scored?), refusing"
        )

    # --- nearest-edge scan: ascending edge_id order => strict < keeps lowest id ---
    from shapely import Point as _Point
    from shapely import distance as _shp_distance

    ordered_ids = sorted(edge_geoms)
    per_point: list[dict[str, Any]] = []
    responsible_nodes: set[int] = set()
    for pt in pts:
        p_geom = _Point(pt["x_m"], pt["y_m"])
        best_eid: int | None = None
        best_d: float | None = None
        for eid in ordered_ids:
            d = float(_shp_distance(p_geom, edge_geoms[eid]))
            if best_d is None or d < best_d:
                best_d, best_eid = d, eid
        resp = responsible_node_of_edge[best_eid]
        matched = bool(best_d is not None and best_d <= radius_m and resp in vol_by_node)
        rec: dict[str, Any] = {
            "point_id": pt["point_id"],
            "location_name": pt.get("location_name", ""),
            "x_m": pt["x_m"],
            "y_m": pt["y_m"],
            "matched": matched,
            "nearest_edge_id": best_eid,
            "nearest_distance_m": best_d,
            "responsible_node_id": resp,
            "responsible_node_class": cls_of(resp),  # None honestly if unresolvable
        }
        if matched:
            responsible_nodes.add(resp)
        per_point.append(rec)

    vol_by_class = {cls: 0.0 for cls in COMPONENT_CLASSES}
    unresolved_matched: list[int] = []
    for nid in sorted(responsible_nodes):
        cls = cls_of(nid)
        if cls is None:
            # Matched => responsible node IS an event node => already validated
            # above; landing here means the two checks disagree — fail loudly.
            unresolved_matched.append(nid)
            continue
        vol_by_class[cls] += vol_by_node[nid]
    if unresolved_matched:
        raise DiagnosticsRefusal(
            f"[diagnostics] matched responsible node(s) {unresolved_matched[:10]} have no "
            "class — inconsistent class source, refusing"
        )
    total_matched_vol = sum(vol_by_class.values())
    share: float | None = (
        vol_by_class["dead_end"] / total_matched_vol if total_matched_vol > 0.0 else None
    )
    suspicious = bool(share is not None and share > suspicious_deadend_share)
    state = "NOT_ASSESSED" if share is None else ("TRUE" if suspicious else "FALSE")

    return {
        "attribution_mode": "edge",
        "radius_m_declared_assumption": radius_m,
        "radius_basis": radius_basis or "",
        "suspicious_deadend_share_threshold": suspicious_deadend_share,
        "n_gt_points": len(pts),
        "n_gt_matched": sum(1 for p in per_point if p["matched"]),
        "points": per_point,
        "gt_responsible_nodes": sorted(responsible_nodes),
        "returned_m3_by_class_of_gt_responsible_nodes": vol_by_class,
        "dead_end_share_of_gt_matched_returned_volume": share,
        "suspicious": suspicious,
        "suspicious_state": state,
        "ground_truth_provenance": gt_prov,
        "measure_labels": {
            "attribution_rule": (
                "each GT point joins its NEAREST drain EDGE (true polyline distance, ties "
                "-> lowest edge_id); the RESPONSIBLE node is that edge's DOWNSTREAM (to_node) "
                "endpoint — water surcharges at a node and runs along the reach (D-GT "
                "ruling 2026-08-26); matched iff the responsible node surcharged"
            ),
            "dead_end_share": (
                "share of returned volume attributed to DEAD_END-class matched responsible "
                "nodes; >threshold => SUSPICIOUS because dead-end surcharge happens BY "
                "CONSTRUCTION (D-A: no outlet) — a finding to report, never a result to "
                "celebrate (§11.3)"
            ),
        },
    }


# ---------------------------------------------------------------------------
# G2 verdict (§11.1) — the anti-vacuity gate
# ---------------------------------------------------------------------------


def _suspicious_state_of(inputs: Mapping[str, Any]) -> str:
    """Extract the SUSPICIOUS state string from a manifest-shaped mapping.

    Two accepted locations (item E-iii): the driver's TOP-LEVEL ``gt_attribution``
    block, and the smoke pipeline's ``diagnostics.gt_attribution_and_suspicious``
    nesting — a smoke-produced manifest must re-score to the state it actually
    carries, not degrade to NOT_ASSESSED because of where the block sits.
    """
    gt = inputs.get("gt_attribution")
    if not isinstance(gt, Mapping):
        nested = inputs.get("diagnostics")
        if isinstance(nested, Mapping):
            gt = nested.get("gt_attribution_and_suspicious")
    if isinstance(gt, Mapping):
        s = gt.get("suspicious_state")
        if s in ("TRUE", "FALSE", "NOT_ASSESSED"):
            return str(s)
        if isinstance(gt.get("suspicious"), bool):
            return "TRUE" if gt["suspicious"] else "FALSE"
    if isinstance(inputs.get("suspicious"), bool):
        return "TRUE" if inputs["suspicious"] else "FALSE"
    return "NOT_ASSESSED"


def _normalized_class_counts(split: Any) -> dict[str, int]:
    """Accept either the driver's flat ``component_class_split`` counts or this
    module's richer :func:`component_split` block; return flat counts or raise."""
    if isinstance(split, Mapping):
        if isinstance(split.get("per_class"), Mapping):
            split = split["per_class"]
        counts: dict[str, int] = {}
        ok = True
        for cls in COMPONENT_CLASSES:
            v = split.get(cls)
            entry = v.get("n_events") if isinstance(v, Mapping) else v
            if isinstance(entry, int) and not isinstance(entry, bool):
                counts[cls] = entry
            else:
                ok = False
        if ok:
            return counts
    raise DiagnosticsRefusal(
        "[diagnostics] G2 input carries NO usable component-class split "
        "(expected 'component_class_split' with outfall_terminating/dead_end counts) — "
        "REFUSING an unsplit number: dead-end-dominated surcharge is expected BY "
        "CONSTRUCTION (D-A) and G2 credit without the split is meaningless "
        "(invariant #13 red target)"
    )


def g2_verdict(
    manifest_or_inputs: Mapping[str, Any],
    g2_min_returned_m3: float = G2_MIN_RETURNED_M3_DEFAULT,
) -> tuple[str, str]:
    """The G2 anti-vacuity verdict: ``(verdict, reason)`` with verdict in pass|fail.

    FAIL if ``total_surcharging_steps == 0`` OR ``total_returned_m3 <
    g2_min_returned_m3`` — regardless of all other scores (contract
    ``g2_anti_vacuity``). REFUSES (raises :class:`DiagnosticsRefusal`) an input
    lacking the counters or the component-class split: an unsplit G2 number is
    meaningless by owner directive (invariant #13 red target). The reason text
    always carries the split numbers and the SUSPICIOUS state.

    This function is PURE and NEVER TUNED: nothing about the inputs can be
    adjusted to flip a verdict except the physics itself.
    """
    data = manifest_or_inputs
    problems: list[str] = []
    steps = data.get("total_surcharging_steps")
    returned = data.get("total_returned_m3")
    if not isinstance(steps, int) or isinstance(steps, bool):
        problems.append(f"'total_surcharging_steps' must be an int, got {steps!r}")
    if not isinstance(returned, (int, float)) or isinstance(returned, bool):
        problems.append(f"'total_returned_m3' must be a number, got {returned!r}")
    counts = _normalized_class_counts(
        data.get("component_class_split") or data.get("component_split")
    )
    if problems:
        raise DiagnosticsRefusal(
            "[diagnostics] G2 input malformed:\n" + "\n".join(f"  - {p}" for p in problems)
        )
    steps_i, returned_f = int(steps), float(returned)  # type: ignore[arg-type]
    sus = _suspicious_state_of(data)
    split_txt = (
        f"split outfall_terminating={counts['outfall_terminating']} "
        f"dead_end={counts['dead_end']} (events basis may differ; see component_split)"
    )
    floor_txt = f"G2 floor total_returned_m3 >= {g2_min_returned_m3!r}"

    if steps_i == 0:
        verdict = "fail"
        reason = (
            "NO NODE EVER SURCHARGED (total_surcharging_steps == 0): the anti-vacuity "
            f"condition fails regardless of all other scores; {floor_txt}; {split_txt}; "
            f"SUSPICIOUS={sus}. A quiet window may physically produce nothing — judged "
            "by the owner BEFORE GPU hours, never buried (§11.1)"
        )
    elif returned_f < g2_min_returned_m3:
        verdict = "fail"
        reason = (
            f"total_returned_m3={returned_f!r} < floor {g2_min_returned_m3!r} while "
            f"{steps_i} step(s) surcharged; {split_txt}; SUSPICIOUS={sus}"
        )
    else:
        verdict = "pass"
        reason = (
            f"surcharging_steps={steps_i}, total_returned_m3={returned_f!r} meets "
            f"{floor_txt}; {split_txt}; SUSPICIOUS={sus}"
            + (
                " — dead-end-dominated reproduction is EXPECTED BY CONSTRUCTION (D-A), "
                "reported as a finding, not celebrated"
                if sus == "TRUE"
                else ""
            )
        )
    return verdict, reason


# ---------------------------------------------------------------------------
# CLI (AGENTS.md style rule: every stage runnable standalone)
# ---------------------------------------------------------------------------


@app.command()
def score_g2(
    manifest_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True, help="Run manifest (JSON) to score"
    ),
    g2_min_returned_m3: float = typer.Option(
        G2_MIN_RETURNED_M3_DEFAULT, help="Contract anti-vacuity floor [m3]"
    ),
) -> None:
    """Score a coupled-run manifest against the G2 anti-vacuity gate.

    HEADLINE line is either exactly ``G2 FAIL: NO NODE EVER SURCHARGED`` (zero
    surcharging steps) or ``G2 <VERDICT> | ... | SUSPICIOUS=<state>`` carrying the
    component-split numbers — the SUSPICIOUS state is ALWAYS in the headline (owner
    directive wf2-coupling.js:145-152: dead-end-dominated GT reproduction is flagged
    IN THE HEADLINE; the flag flags, it does not fail). On a run WITH surcharging
    steps whose state is NOT_ASSESSED, an explicit warning line names the missing
    block so an unassessed contamination can never look like a clean pass. Exits
    NON-ZERO on any fail/refusal only (anti-vacuity rules) — gate runs must never
    look green through a quiet window.
    """
    try:
        man = json.loads(Path(manifest_path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"G2 REFUSED: manifest {manifest_path} unreadable: {exc}")
        raise typer.Exit(code=1) from exc
    if not isinstance(man, dict):
        typer.echo(f"G2 REFUSED: manifest {manifest_path} is not a JSON object")
        raise typer.Exit(code=1)

    steps = man.get("total_surcharging_steps")
    if steps is None:
        typer.echo(
            "G2 REFUSED: manifest lacks 'total_surcharging_steps' — not a coupled-run "
            "manifest (or written before completion); refusing to invent a verdict"
        )
        raise typer.Exit(code=1)
    sus = _suspicious_state_of(man)

    if isinstance(steps, int) and not isinstance(steps, bool) and steps == 0:
        typer.echo("G2 FAIL: NO NODE EVER SURCHARGED")
        ret = man.get("total_returned_m3")
        typer.echo(
            f"  total_returned_m3={ret!r} (floor {g2_min_returned_m3!r}) "
            f"SUSPICIOUS={sus} — anti-vacuity §11.1"
        )
        raise typer.Exit(code=1)

    try:
        verdict, reason = g2_verdict(man, g2_min_returned_m3=g2_min_returned_m3)
    except DiagnosticsRefusal as exc:
        typer.echo(f"G2 REFUSED: {exc}")
        raise typer.Exit(code=1) from exc

    counts = _normalized_class_counts(
        man.get("component_class_split") or man.get("component_split")
    )
    returned = float(man["total_returned_m3"])  # type: ignore[arg-type]
    typer.echo(
        f"G2 {verdict.upper()} | outfall_terminating={counts['outfall_terminating']} "
        f"dead_end={counts['dead_end']} (node-class counts) | "
        f"total_returned_m3={returned!r} surcharging_steps={steps} | SUSPICIOUS={sus}"
    )
    typer.echo(f"  {reason}")
    if sus == "NOT_ASSESSED":
        # Reaching here means surcharging_steps > 0: exactly the run shape where
        # dead-end contamination matters and must not pass looking clean (item E-ii).
        # The headline above still carries SUSPICIOUS=NOT_ASSESSED; this names WHY.
        typer.echo("WARNING: dead-end contamination UNASSESSED - no gt_attribution block")
    if verdict == "fail":
        raise typer.Exit(code=1)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
