"""Terminal-seed resolution for the WF-2 drain graph — definition v2-lake-boundary.
Lake HAS reached a terminal sink; a reach crossing the domain boundary HAS left
- ``domain_boundary`` — a SINK node within ``boundary_tolerance_m`` of any edge
Priority is ``declared_gpkg_outfall > lake_polygon > domain_boundary`` so every
CONSUMER-SIDE ONLY: configs/contracts/drain_graph.json forbids PRODUCERS
emitting node_type=inlet / outfall_reason=lake_boundary until a boundary-
CROSS-ASSERT their traversal against it at the seam (V8)."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "RULE_BOUNDARY",
    "RULE_DECLARED",
    "RULE_LAKE",
    "EXPECTED_BBOX_SOURCE",
    "GridBlock",
    "NodeTerminalRecord",
    "TerminalDefinition",
    "TerminalDefinitionError",
    "TerminalResolution",
    "classify_reachability",
    "load_lake_union",
    "load_terminal_definition",
    "parse_grid_block",
    "resolve_terminal_nodes",
    "with_tolerances",
]

# Rule ids (stable labels carried into manifests/products).
RULE_DECLARED = "declared_gpkg_outfall"
RULE_LAKE = "lake_polygon"
RULE_BOUNDARY = "domain_boundary"
_RULE_PRIORITY: tuple[str, ...] = (RULE_DECLARED, RULE_LAKE, RULE_BOUNDARY)

# The ONLY legal bbox source: the producer grid block already parsed and refused
EXPECTED_BBOX_SOURCE = "graph_manifest.config_snapshot.grid"


class TerminalDefinitionError(ValueError):

    def __init__(self, problems: list[str], config_path: Path | None = None) -> None:
        self.problems = list(problems)
        self.config_path = config_path
        header = (
            f"{config_path}: terminal definition failed validation with "
            f"{len(problems)} problem(s); fix ALL of them before retry "
            "(aggregated error, CLAUDE.md rule 7):"
        )
        body = "\n".join(f"  {i}. {p}" for i, p in enumerate(problems, start=1))
        super().__init__(f"{header}\n{body}")


@dataclass(frozen=True)
class TerminalDefinition:
    """The resolved, validated v2 terminal definition (one per YAML file)."""

    definition_version: str
    lake_enabled: bool
    lake_require_sink: bool
    lake_source_path: str
    lake_layer: str
    lake_crs: str
    lake_name_field: str
    lake_selection_names: tuple[str, ...]
    lake_snap_tolerance_m: float
    boundary_enabled: bool
    boundary_require_sink: bool
    boundary_bbox_source: str
    boundary_tolerance_m: float
    sensitivity_tolerances_m: tuple[float, ...]
    provenance_pointer: str
    raw: Mapping[str, Any]


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_terminal_definition(path: Path) -> TerminalDefinition:
    """tolerance band, and the provenance pointer (existence-checked). A missing"""
    path = Path(path)
    problems: list[str] = []
    if not path.exists():
        raise TerminalDefinitionError([f"file not found: {path}"], config_path=path)
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise TerminalDefinitionError([f"unreadable YAML: {exc}"], config_path=path) from exc
    if not isinstance(raw, Mapping):
        raise TerminalDefinitionError(
            [f"top-level document must be a mapping, got {type(raw).__name__}"],
            config_path=path,
        )

    def need(d: Mapping[str, Any], key: str, where: str) -> Any:
        if key not in d:
            problems.append(f"'{where}.{key}' missing")
            return None
        return d[key]

    version = need(raw, "definition_version", "<top>")
    if version is not None and (not isinstance(version, str) or not version.strip()):
        problems.append(f"'definition_version' must be a non-empty string, got {version!r}")

    rules = need(raw, "rules", "<top>")
    if not isinstance(rules, Mapping):
        problems.append(f"'rules' must be a mapping, got {type(rules).__name__!s}")
        rules = {}

    lake = rules.get(RULE_LAKE) if isinstance(rules, Mapping) else None
    lake_enabled = False
    lake_require_sink = True
    lake_source_path = ""
    lake_layer = ""
    lake_crs = ""
    lake_name_field = ""
    lake_names: tuple[str, ...] = ()
    lake_tol = -1.0
    if not isinstance(lake, Mapping):
        problems.append(f"'rules.{RULE_LAKE}' must be a mapping, got {type(lake).__name__!s}")
    else:
        en = lake.get("enabled")
        if not isinstance(en, bool):
            problems.append(f"'rules.{RULE_LAKE}.enabled' must be a bool, got {en!r}")
        else:
            lake_enabled = en
        rs = lake.get("require_sink")
        if not isinstance(rs, bool):
            problems.append(
                f"'rules.{RULE_LAKE}.require_sink' must be declared visibly as a bool, got {rs!r}"
            )
        else:
            lake_require_sink = rs
        sp = lake.get("source_path")
        if not isinstance(sp, str) or not sp.strip():
            problems.append(f"'rules.{RULE_LAKE}.source_path' must be a non-empty string")
        else:
            lake_source_path = sp
            if not Path(sp).exists():
                problems.append(f"'rules.{RULE_LAKE}.source_path': file not found: {sp}")
        ly = lake.get("layer")
        if not isinstance(ly, str) or not ly.strip():
            problems.append(f"'rules.{RULE_LAKE}.layer' must be a non-empty string")
        else:
            lake_layer = ly
        crs = lake.get("crs")
        if not isinstance(crs, str) or not crs.strip():
            problems.append(f"'rules.{RULE_LAKE}.crs' must be a non-empty string")
        else:
            lake_crs = crs
        nf = lake.get("name_field")
        if not isinstance(nf, str) or not nf.strip():
            problems.append(f"'rules.{RULE_LAKE}.name_field' must be a non-empty string")
        else:
            lake_name_field = nf
        sel = lake.get("selection")
        if not isinstance(sel, Mapping):
            problems.append(
                f"'rules.{RULE_LAKE}.selection' must be a mapping "
                "{{mode: exact-name, names: [...]}}"
            )
        else:
            mode = sel.get("mode")
            if mode != "exact-name":
                problems.append(
                    f"'rules.{RULE_LAKE}.selection.mode' must be 'exact-name', got {mode!r}"
                )
            names = sel.get("names")
            if not isinstance(names, list) or not names:
                problems.append("'rules.lake_polygon.selection.names' must be a non-empty list")
            elif not all(isinstance(n, str) and n.strip() for n in names):
                problems.append("'rules.lake_polygon.selection.names' entries must be strings")
            else:
                lake_names = tuple(str(n) for n in names)
        tol = lake.get("snap_tolerance_m")
        if not _is_num(tol) or tol < 0:
            problems.append(
                f"'rules.{RULE_LAKE}.snap_tolerance_m' must be a number >= 0, got {tol!r}"
            )
        else:
            lake_tol = float(tol)
        just = lake.get("justification")
        if not isinstance(just, str) or not just.strip():
            problems.append(
                f"'rules.{RULE_LAKE}.justification' must quote the owner suspicion — "
                "a visible definition needs its reason on disk, not in chat history"
            )

    # --- boundary rule -------------------------------------------------------
    bnd = rules.get(RULE_BOUNDARY) if isinstance(rules, Mapping) else None
    bnd_enabled = False
    bnd_require_sink = True
    bnd_src = ""
    bnd_tol = -1.0
    if not isinstance(bnd, Mapping):
        problems.append(f"'rules.{RULE_BOUNDARY}' must be a mapping, got {type(bnd).__name__!s}")
    else:
        en = bnd.get("enabled")
        if not isinstance(en, bool):
            problems.append(f"'rules.{RULE_BOUNDARY}.enabled' must be a bool, got {en!r}")
        else:
            bnd_enabled = en
        rs = bnd.get("require_sink")
        if not isinstance(rs, bool):
            problems.append(
                f"'rules.{RULE_BOUNDARY}.require_sink' must be declared visibly as a bool, "
                f"got {rs!r}"
            )
        else:
            bnd_require_sink = rs
        src = bnd.get("bbox_source")
        if src != EXPECTED_BBOX_SOURCE:
            problems.append(
                f"'rules.{RULE_BOUNDARY}.bbox_source' must be {EXPECTED_BBOX_SOURCE!r} "
                f"(single bbox definition), got {src!r}"
            )
        else:
            bnd_src = str(src)
        tol = bnd.get("tolerance_m")
        if not _is_num(tol) or tol < 0:
            problems.append(
                f"'rules.{RULE_BOUNDARY}.tolerance_m' must be a number >= 0, got {tol!r}"
            )
        else:
            bnd_tol = float(tol)
        just = bnd.get("justification")
        if not isinstance(just, str) or not just.strip():
            problems.append(f"'rules.{RULE_BOUNDARY}.justification' must quote the owner suspicion")

    sens = need(raw, "sensitivity_tolerances_m", "<top>")
    sens_tols: tuple[float, ...] = ()
    if not isinstance(sens, list) or not sens:
        problems.append("'sensitivity_tolerances_m' must be a non-empty list")
    elif not all(_is_num(t) and t >= 0 for t in sens):
        problems.append("'sensitivity_tolerances_m' entries must be numbers >= 0")
    else:
        sens_tols = tuple(float(t) for t in sens)

    prov = need(raw, "provenance", "<top>")
    prov_ptr = ""
    if not isinstance(prov, Mapping):
        problems.append("'provenance' must be a mapping with a 'pointer' key")
    else:
        ptr = prov.get("pointer")
        if not isinstance(ptr, str) or not ptr.strip():
            problems.append("'provenance.pointer' must be a non-empty string")
        else:
            prov_ptr = ptr
            if not Path(ptr).exists():
                problems.append(f"'provenance.pointer': file not found: {ptr}")

    if problems:
        raise TerminalDefinitionError(problems, config_path=path)

    return TerminalDefinition(
        definition_version=str(version),
        lake_enabled=lake_enabled,
        lake_require_sink=lake_require_sink,
        lake_source_path=lake_source_path,
        lake_layer=lake_layer,
        lake_crs=lake_crs,
        lake_name_field=lake_name_field,
        lake_selection_names=lake_names,
        lake_snap_tolerance_m=lake_tol,
        boundary_enabled=bnd_enabled,
        boundary_require_sink=bnd_require_sink,
        boundary_bbox_source=bnd_src,
        boundary_tolerance_m=bnd_tol,
        sensitivity_tolerances_m=sens_tols,
        provenance_pointer=prov_ptr,
        raw=dict(raw),
    )


def load_lake_union(tdef: TerminalDefinition):
    """Asserts, against the REALIZED bytes: the layer exists, its CRS matches the
    found (a silently-shrinking selection would move seeds without a trace)."""
    import geopandas as gpd

    gdf = gpd.read_file(tdef.lake_source_path, layer=tdef.lake_layer)
    problems: list[str] = []
    got_crs = str(getattr(gdf, "crs", None) or "").upper()
    want_crs = tdef.lake_crs.upper()
    if got_crs.replace(":", "") != want_crs.replace(":", ""):
        problems.append(f"layer '{tdef.lake_layer}' crs {got_crs!r} != declared {tdef.lake_crs!r}")
    if tdef.lake_name_field not in gdf.columns:
        problems.append(
            f"layer '{tdef.lake_layer}' lacks name field '{tdef.lake_name_field}' "
            f"(columns: {list(gdf.columns)})"
        )
    sel = (
        gdf[gdf[tdef.lake_name_field].isin(tdef.lake_selection_names)]
        if tdef.lake_name_field in gdf.columns
        else gdf.iloc[0:0]
    )
    found = sorted(set(sel[tdef.lake_name_field].astype(str))) if len(gdf) else []
    missing = [n for n in tdef.lake_selection_names if n not in found]
    if missing:
        sample = sorted(set(gdf[tdef.lake_name_field].astype(str)))[:10]
        problems.append(
            f"selection exact-name match missed {missing!r}; found {found!r}; "
            f"available names include {sample}"
        )
    if problems:
        raise TerminalDefinitionError(problems, config_path=Path(tdef.lake_source_path))
    return sel.geometry.union_all()


@dataclass(frozen=True)
class GridBlock:
    """The producer-declared buffered grid (runs/drain_graph_build manifest block)."""

    res_m: float
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    height: int
    width: int

    def distance_to_boundary(self, x: float, y: float) -> float:
        """Euclidean distance to the NEAREST domain-boundary edge."""
        return min(
            abs(x - self.x_min), abs(self.x_max - x), abs(y - self.y_min), abs(self.y_max - y)
        )


def parse_grid_block(grid_block: Mapping[str, Any]) -> GridBlock:
    """and refuses on — one bbox definition, no second implementation of its semantics."""
    problems: list[str] = []
    for key in ("height", "width", "transform"):
        if key not in grid_block:
            problems.append(f"grid block missing {key!r}")
    if problems:
        raise TerminalDefinitionError(problems)
    tr = grid_block["transform"]
    res, x_origin, y_top = float(tr[0]), float(tr[2]), float(tr[5])
    if float(tr[1]) != 0.0 or float(tr[3]) != 0.0 or abs(float(tr[4])) != res:
        raise TerminalDefinitionError([f"transform {tr} is not axis-aligned at resolution {res}"])
    gh, gw = int(grid_block["height"]), int(grid_block["width"])
    return GridBlock(
        res_m=res,
        x_min=x_origin,
        y_min=y_top - gh * res,
        x_max=x_origin + gw * res,
        y_max=y_top,
        height=gh,
        width=gw,
    )


@dataclass(frozen=True)
class NodeTerminalRecord:
    """Per-node terminal record (V1: realized distances, not declarations)."""

    node_id: int
    rule_fired: str | None
    dist_to_lake_m: float
    dist_to_boundary_m: float
    is_sink: bool
    declared: bool


@dataclass(frozen=True)
class TerminalResolution:
    """the internal invariant is asserted at construction time inside"""

    definition_version: str
    seeds_by_rule: dict[str, list[int]]
    counts_by_rule: dict[str, int]
    seed_union: list[int]
    records: dict[int, NodeTerminalRecord]

    def as_manifest_block(self) -> dict[str, Any]:
        """JSON-safe echo threaded into graph manifests / split products."""

        def rec(r: NodeTerminalRecord) -> dict[str, Any]:
            return {
                "rule_fired": r.rule_fired,
                "dist_to_lake_m": r.dist_to_lake_m,
                "dist_to_boundary_m": r.dist_to_boundary_m,
                "is_sink": r.is_sink,
                "declared": r.declared,
            }

        return {
            "definition_version": self.definition_version,
            "counts_by_rule": dict(self.counts_by_rule),
            "seeds_by_rule": {k: list(v) for k, v in self.seeds_by_rule.items()},
            "seed_union_count": len(self.seed_union),
            "records": {str(nid): rec(rec_) for nid, rec_ in sorted(self.records.items())},
            "note": (
                "consumer-side seed derivation (configs/terminal_definition.yaml); "
                "producer artefacts untouched — contract forbids producers emitting "
                "node_type=inlet/lake_boundary"
            ),
        }


def _within_tolerance(dist_m: float, tol_m: float) -> bool:
    """Inclusive boundary predicate (d <= tol) — same convention as the
    attribution-radius seam (matched AT the radius counts). Mutation probe"""
    return dist_m <= tol_m


def _point(x: float, y: float):
    from shapely import Point

    return Point(x, y)


def resolve_terminal_nodes(
    node_xy: Sequence[Sequence[float]],
    edge_pairs: Sequence[tuple[int, int]],
    declared_outfall_ids: Sequence[int],
    tdef: TerminalDefinition,
    grid_block: Mapping[str, Any],
    *,
    lake_union: Any | None = None,
) -> TerminalResolution:
    """Derive the extended terminal seed set under the v2 definition.
    producer gpkg (rule 0; unchanged from v1).
    grid_block: the producer manifest's ``config_snapshot.grid`` mapping."""
    declared_set = {int(i) for i in declared_outfall_ids}
    outgoing = {int(u) for u, _ in edge_pairs}
    gb = parse_grid_block(grid_block)

    n = len(node_xy)
    xs = [float(p[0]) for p in node_xy]
    ys = [float(p[1]) for p in node_xy]

    lake_geom = None
    if tdef.lake_enabled:
        lake_geom = lake_union if lake_union is not None else load_lake_union(tdef)

    seeds_by_rule: dict[str, list[int]] = {r: [] for r in _RULE_PRIORITY}
    records: dict[int, NodeTerminalRecord] = {}
    for i in range(n):
        nid = i + 1
        x, y = xs[i], ys[i]
        dist_bnd = gb.distance_to_boundary(x, y)
        if lake_geom is not None:
            dist_lake = lake_geom.distance(_point(x, y))
        else:
            dist_lake = math.inf
        is_declared = nid in declared_set
        is_sink = nid not in outgoing

        rule_fired: str | None = None
        if is_declared:
            rule_fired = RULE_DECLARED
        elif (
            tdef.lake_enabled
            and _within_tolerance(dist_lake, tdef.lake_snap_tolerance_m)
            and (is_sink or not tdef.lake_require_sink)
        ):
            rule_fired = RULE_LAKE
        elif (
            tdef.boundary_enabled
            and _within_tolerance(dist_bnd, tdef.boundary_tolerance_m)
            and (is_sink or not tdef.boundary_require_sink)
        ):
            rule_fired = RULE_BOUNDARY

        records[nid] = NodeTerminalRecord(
            node_id=nid,
            rule_fired=rule_fired,
            dist_to_lake_m=dist_lake if math.isfinite(dist_lake) else float("inf"),
            dist_to_boundary_m=dist_bnd,
            is_sink=is_sink,
            declared=is_declared,
        )
        if rule_fired is not None:
            seeds_by_rule[rule_fired].append(nid)

    seed_union = sorted(nid for ids in seeds_by_rule.values() for nid in ids)
    counts_by_rule = {r: len(ids) for r, ids in seeds_by_rule.items()}
    # Partition invariant: priority ordering guarantees each seeded node appears
    # under EXACTLY one rule — assert the realized bookkeeping agrees.
    if sum(counts_by_rule.values()) != len(seed_union) or len(set(seed_union)) != len(seed_union):
        raise TerminalDefinitionError(
            [
                f"counts_by_rule {counts_by_rule} do not partition seed_union "
                f"({len(seed_union)} unique of {sum(counts_by_rule.values())}) — "
                "priority leak in resolve_terminal_nodes"
            ]
        )
    return TerminalResolution(
        definition_version=tdef.definition_version,
        seeds_by_rule={r: sorted(v) for r, v in seeds_by_rule.items()},
        counts_by_rule=counts_by_rule,
        seed_union=seed_union,
        records=records,
    )


def classify_reachability(edge_pairs: Sequence[tuple[int, int]], seeds: Sequence[int]) -> set[int]:
    """Reference implementation both existing traversals cross-assert against
    (V8): router._compute_component_classes (torch BFS) and"""
    succ: dict[int, list[int]] = {}
    for u, v in edge_pairs:
        succ.setdefault(int(v), []).append(int(u))
    reached = {int(s) for s in seeds}
    queue = deque(reached)
    while queue:
        cur = queue.popleft()
        for prev in succ.get(cur, ()):
            if prev not in reached:
                reached.add(prev)
                queue.append(prev)
    return reached


def with_tolerances(tdef: TerminalDefinition, tolerance_m: float) -> TerminalDefinition:
    return replace(
        tdef,
        lake_snap_tolerance_m=float(tolerance_m),
        boundary_tolerance_m=float(tolerance_m),
    )
