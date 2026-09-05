"""WF-2 router: DrainGraph load (V8 seam) + level-synchronous capacity-limited routing.

Implements spec §4.2/§7 (``runs/wf2_design_phase/spec.md``) verbatim against the
frozen contract ``configs/contracts/coupling_iface.json`` (``edge_routing_pinned``,
``node_storage_pinned``). The routing algorithm is the D-C pin, word for word:

1. **Topology order** — strict downstream topological order, executed
   LEVEL-SYNCHRONOUSLY over the recomputed topo levels (24 on the real graph).
   Within a level no edges exist between members (DAG layering property), so a
   per-level vectorized transfer preserves strict topological semantics: every
   edge points strictly forward in level.
2. **Edge discharge** — ``Q_edge = min(available_vol_upstream/dt, q_cap_e)``,
   instantaneous, zero delay within dt; volume-conservative
   ``vol_up -= Q*dt; vol_down += Q*dt``. Multi-hop propagation within one call
   is intended (infinite-celerity abstraction).
3. **NULL-capacity edges carry Q_edge = 0 ALWAYS** — all 379 non-capacity-bearing
   edges (286 synthetic + 61 zero-slope + 32 area-capped) are topology only.
   Null stays null in code: ``q_cap_nom_m3s`` carries NaN sentinels plus the
   boolean ``capacity_bearing`` mask; coercing NaN->0 is a defect (invariant #11;
   demonstrated red in ``tests/coupling/test_router.py``).
4. **Multi-inflow** sums before outgoing capacity is applied — guaranteed by
   level order (all upstream levels are fully routed first).
5. **Multi-outflow** distributes proportional to ``q_capacity_nom`` over
   CAPACITY-BEARING outgoing edges ONLY. With fractions ``f_e = cap_e / S``
   (``S`` = sum of capacity-bearing outgoing caps), the pinned form
   ``Q_e = min(f_e * V/dt, cap_e)`` is *exactly* proportional-below-capacity and
   each-edge-at-capacity above it (``f_e*S == cap_e``), so one expression
   satisfies both contract sentences. If ``S == 0``, all outgoing Q_edge = 0
   (the node retains its volume).
6. **No substepping** — the graph advances on the host adaptive dt only.

Purity / autograd discipline (contract ``autograd_rules``): :func:`route` is
out-of-place everywhere — the input ``vol`` tensor is never written; per-level
updates rebuild the volume vector through functional ``torch.index_add``;
clamp-before-divide is applied to available volume before the ``/dt``; there is
no ``.detach()`` and no dead ``torch.where`` branch in the hot path, so
gradients flow through routing for replay()-style calibration.

Load path (V8 seam): :func:`load_drain_graph` calls the existing asserting
reader :func:`jaladhar.drainage.graph_io.read_artefact` (its
:class:`~jaladhar.drainage.graph_io.RefuseLoadError` propagates unmodified),
THEN asserts the consumer-side guarantees on top: DAG flag, expected counts
(1721/1587) and edge partition (1208 capacity-bearing / 61 zero-slope /
32 area-capped / 286 synthetic), recomputes topo levels level-synchronously,
computes the directed-reachability component classes against the outfall set,
builds the node->cell maps for the requested window, records the ``topo_order``
sha256, and applies the falsifier activity gate: a subwindow refuses when fewer
than ``cfg.smoke.min_predicted_nodes_in_window`` predicted targets are active in
it (the spec §14 smoke window holds 14/116), a full-domain load still requires
ALL of them active; the realized counts land on the graph either way.

Declared assumptions honoured here (labelled, never tuned): node plan areas are
the vertical-shaft proxy ``width_mean_m * shaft_length_proxy_m`` where
``width_mean_m`` averages ``width_m`` over CAPACITY-BEARING incident edges only
and isolated nodes fall back to the contract-pinned tertiary nominal width
2.285 m (contract ``node_storage_pinned``). ``capture_radius_m`` is the pinned
declared-assumption v1 value from config (‡, unsourced).

CPU-only: the device comes from ``cfg.device`` which the rule-7 resolver pins
to ``"cpu"``; this module additionally refuses any non-cpu device outright so
no CUDA context is ever initialised (V12).

Known conflict resolved explicitly (deviation reported, not silent): spec §4.2
guarantees ``node_cell_count >= 1`` for active nodes while OQ1 pins
nearest-node-wins capture allocation with stable node-id tie-break. On the real
graph the pure nearest-wins paint leaves 42 nodes with zero allocated cells
(dense junction clusters dominate them). The ``>= 1`` guarantee is binding on
every downstream consumer (uniform surcharge-return divides by this count), so
this loader enforces it by assigning each starved active node its OWN cell.
Capture attribution therefore shifts by at most one cell per starved node;
reported here and in the build unit report rather than taken silently.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import typer

from jaladhar.coupling.config import CouplingConfig, resolve_config
from jaladhar.drainage.graph_io import RefuseLoadError, read_artefact
from jaladhar.drainage.terminal import (
    classify_reachability,
    load_terminal_definition,
    resolve_terminal_nodes,
)

__all__ = [
    "DrainGraph",
    "RoutePlan",
    "build_drain_graph",
    "load_drain_graph",
    "route",
]

app = typer.Typer(add_completion=False)

# Capacity-basis prefixes separating the two NULL-capacity observed classes
# (measured against runs/drain_graph_build/drain_graph.gpkg bytes 2026-08-25:
# 61 edges 'zero measured slope ...', 32 edges 'contributing area ... ha
# exceeds the rational method validity limit ...'). Synthetic connectors are
# separated structurally by edge_source == 'synthesised'.
_ZERO_SLOPE_BASIS_PREFIX = "zero measured slope"
_AREA_CAPPED_BASIS_PREFIX = "contributing area"

_TIE_EPS_M = 1e-9  # slack on the declared capture radius boundary comparison

# M5b adjudication (2026-08-25, recorded in graph_io.read_artefact): the
# input-geometry self-loops are a DATA FACT, not a build defect; a consumer may
# load when the dropped ids are enumerated in manifest counts.dropped_edge_ids
# (verified by the reader: found 11, all enumerated) AND the owner adjudication
# reference is present. This is that reference — plumbing a recorded owner
# decision, not bypassing the gate; with the ids unenumerated the reader still
# refuses.
OWNER_ADJUDICATION_REF_M5B = (
    "M5b-adjudication-2026-08-25:self_loop_dropped_count_is_a_data_fact;"
    "ids_enumerated_in_manifest.counts.dropped_edge_ids;consumer=wf2-coupling-router"
)


# ---------------------------------------------------------------------------
# Internal routing plan: static per-level edge tables, built once at load so
# the hot path is O(E_active) tensor work per step with no Python edge loops.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LevelEdges:
    """Edges whose SOURCE sits at one topo level (all strictly forward-pointing)."""

    level: int
    src: torch.Tensor  # (e,) int64 source node indices (node_id - 1)
    dst: torch.Tensor  # (e,) int64 destination node indices
    cap: torch.Tensor  # (e,) float64 nominal capacities (finite > 0)
    frac: torch.Tensor  # (e,) float64 proportional share cap_e/S_src; 0 where S_src == 0
    edge_pos: torch.Tensor  # (e,) int64 positions in the full edge array


@dataclass(frozen=True)
class RoutePlan:
    """Static routing tables for :func:`route` (derived, read-only)."""

    levels: tuple[_LevelEdges, ...]
    n_levels: int  # number of topo levels (empty levels contribute nothing)
    edges_routed: int  # number of active capacity-bearing edges carrying flow
    nodes_without_outflow: int  # nodes with no active capacity-bearing outgoing edge


def _build_route_plan(
    edge_from: torch.Tensor,
    edge_to: torch.Tensor,
    q_cap_nom_m3s: torch.Tensor,
    routeable_mask: torch.Tensor,
    topo_level: torch.Tensor,
    num_nodes: int,
    device: torch.device,
) -> RoutePlan:
    """Group active capacity-bearing edges by their source node's topo level.

    ``routeable_mask`` is ``capacity_bearing & active_edge``; anything else is
    structurally absent from the plan and therefore carries Q_edge = 0 ALWAYS
    (D-C pin) — it cannot appear in any transfer even under a routing bug.
    """
    src_all = edge_from.to(device)
    lvl_src = topo_level.to(device)[src_all]
    routeable = routeable_mask.to(device)
    levels: list[_LevelEdges] = []
    routed_positions: list[torch.Tensor] = []
    n_levels = int(topo_level.max().item()) + 1 if num_nodes > 0 else 0
    for level in range(n_levels):
        sel = routeable & (lvl_src == level)
        pos = sel.nonzero(as_tuple=True)[0]  # ascending edge-id order (deterministic)
        if pos.numel() == 0:
            continue
        src = src_all[pos]
        dst = edge_to.to(device)[pos]
        cap = q_cap_nom_m3s.to(device)[pos]
        out_sum = torch.index_add(
            torch.zeros(num_nodes, dtype=torch.float64, device=device), 0, src, cap
        )
        s_src = out_sum[src]
        # clamp-before-divide discipline: floor the denominator, select after.
        denom = torch.clamp(s_src, min=torch.finfo(torch.float64).tiny)
        frac = torch.where(s_src > 0, cap / denom, torch.zeros_like(cap))
        levels.append(
            _LevelEdges(level=int(level), src=src, dst=dst, cap=cap, frac=frac, edge_pos=pos)
        )
        routed_positions.append(pos)
    # Nodes with at least one active capacity-bearing outgoing edge:
    has_outflow = torch.zeros(num_nodes, dtype=torch.float64, device=device)
    if routed_positions:
        out_src = src_all[torch.cat(routed_positions)]
        has_outflow = torch.index_add(
            has_outflow,
            0,
            out_src,
            torch.ones(out_src.numel(), dtype=torch.float64, device=device),
        )
    nodes_without_outflow = int((has_outflow == 0).sum().item())
    return RoutePlan(
        levels=tuple(levels),
        n_levels=n_levels,
        edges_routed=int(sum(p.numel() for p in routed_positions)),
        nodes_without_outflow=nodes_without_outflow,
    )


def _level_transfer(rate: torch.Tensor, frac: torch.Tensor, cap: torch.Tensor) -> torch.Tensor:
    """Q_e = min(frac_e * available_rate, cap_e) — the single pinned discharge form.

    Module-level so the V5 red demonstration can monkeypatch it with a leak
    multiplier and show the conservation identity going red (mutation recorded
    beside the test; reverted afterwards).
    """
    return torch.minimum(rate * frac, cap)


# ---------------------------------------------------------------------------
# Topology: level-synchronous topo levels + directed-reachability classes
# ---------------------------------------------------------------------------


def _compute_topo_levels(
    edge_from: torch.Tensor,
    edge_to: torch.Tensor,
    num_nodes: int,
) -> tuple[torch.Tensor, int]:
    """Kahn layering, level-synchronous: level 0 = sources, level(v) grows by 1
    per layer. Equivalent to longest-path-from-source depth; on the real graph
    this yields the 24 recorded levels with 699 nodes at level 0 [spec §0].

    Returns (levels int32 (N,), n_levels int). Raises ValueError on a cycle
    (cannot happen behind the reader's DAG assertion; kept fail-loud anyway).
    """
    indeg = torch.index_add(
        torch.zeros(num_nodes, dtype=torch.float64),
        0,
        edge_to,
        torch.ones(edge_to.numel(), dtype=torch.float64),
    )
    level = torch.full((num_nodes,), -1, dtype=torch.int32)
    frontier = indeg == 0
    n_levels = 0
    while bool(frontier.any()):
        level[frontier] = n_levels  # load-time scratch tensor owned here, not an input
        n_levels += 1
        dec = edge_to[frontier[edge_from]]
        indeg = torch.index_add(indeg, 0, dec, torch.full_like(dec, -1.0, dtype=torch.float64))
        frontier = (indeg == 0) & (level < 0)
    if int((level < 0).sum()) != 0:
        stranded = (level < 0).nonzero(as_tuple=True)[0].tolist()
        raise RefuseLoadError(
            f"[topo] {len(stranded)} node(s) unreachable by Kahn layering — directed cycle "
            f"(node_ids {[i + 1 for i in stranded[:10]]}...); refusing"
        )
    # Strict monotonicity along every edge: the property that makes per-level
    # vectorized transfers equivalent to a strict topological walk (spec §7.1).
    if not bool((level[edge_to] > level[edge_from]).all()):
        raise RefuseLoadError("[topo] recomputed levels not strictly increasing along edges")
    return level, n_levels


def _compute_component_classes(
    outfall_node: torch.Tensor,
    edge_from: torch.Tensor,
    edge_to: torch.Tensor,
    num_nodes: int,
) -> list[str]:
    """Directed reachability to ANY outfall node (memoized BFS walk over the
    reversed edge set — deterministic; result is order-independent).

    'outfall_terminating' := the node can reach an outfall following directed
    edges downstream (an outfall reaches itself, path length 0);
    'dead_end' otherwise. Spec §0 expects 55 / 1666 on the real graph; the
    realized split is asserted by the integration test, not hardcoded here.
    """
    reached = outfall_node.clone()
    while True:
        candidates = edge_from[reached[edge_to] & ~reached[edge_from]]
        if candidates.numel() == 0:
            break
        reached[candidates.unique()] = True  # visited-set memoization; scratch tensor
    return ["outfall_terminating" if bool(reached[i]) else "dead_end" for i in range(num_nodes)]


# ---------------------------------------------------------------------------
# DrainGraph
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class DrainGraph:
    """Read-only topology + capacity + node geometry for the coupled run.

    Field-for-field per spec §4.2, plus the documented extras the workflow
    needs: ``outfall_node`` (component-class input, diagnostics join),
    ``active_node``/``active_edge``/``n_active_*`` (subwindow induced-subgraph
    bookkeeping) and ``plan`` (static routing tables so :func:`route` does no
    per-call grouping). Node index convention: tensor index i corresponds to
    node_id i+1 (the reader asserts node_id contiguous 1..N).

    Shapes: N = num_nodes, E = num_edges; node maps cover the ACTIVE GRID VIEW
    (full buffered grid H×W when ``window=None``, else the window crop).
    """

    num_nodes: int
    num_edges: int
    topo_order: list[int]  # strict downstream order (adjacency's, validated by reader)
    topo_level: torch.Tensor  # (N,) int32, recomputed level-synchronously
    edge_from: torch.Tensor  # (E,) int64
    edge_to: torch.Tensor  # (E,) int64
    capacity_bearing: torch.Tensor  # (E,) bool
    q_cap_nom_m3s: torch.Tensor  # (E,) float64; NaN sentinels NEVER coerced to 0
    width_mean_m: torch.Tensor  # (N,) float64; mean width over CB incident edges; isolated 2.285
    node_plan_area_m2: torch.Tensor  # (N,) float64 = width_mean_m * shaft_length_proxy_m
    node_elev_m: torch.Tensor  # (N,) float64 DEM proxy invert (manifest note honoured)
    contrib_area_m2: torch.Tensor  # (N,) float64; 0.0 where the gpkg had SQL NULL
    component_class: list[str]  # (N,) 'outfall_terminating' | 'dead_end'
    outfall_node: torch.Tensor  # (N,) bool  [extra: component-class + diagnostics input]
    node_cell_row: torch.Tensor  # (N,) int32 indices into the BUFFERED grid
    node_cell_col: torch.Tensor  # (N,) int32 indices into the BUFFERED grid
    node_id_map: torch.Tensor  # (Hv,Wv) int32, -1 = no active node within capture_radius_m
    node_cell_count: torch.Tensor  # (N,) int64 cells allocated (>= 1 for active nodes)
    active_node: torch.Tensor  # (N,) bool  [extra: subwindow induced-subgraph mask]
    active_edge: torch.Tensor  # (E,) bool  [extra: both endpoints active]
    n_active_nodes: int  # [extra]
    n_active_edges: int  # [extra]
    graph_fingerprint: str
    topo_sha256: str  # sha256 over compact-JSON topo_order (UTF-8, separators (',',':'))
    manifest: dict[str, Any]
    plan: RoutePlan  # [extra: static routing tables]
    # [extra] Defect-C bookkeeping: realized falsifier-target activity in the
    # loaded view, recorded by the loader on EVERY load. -1 = not assessed
    # (assembly path without a prediction set; the loader always assesses).
    n_active_predicted_targets: int = -1
    n_inactive_predicted_targets: int = -1


def build_drain_graph(
    *,
    edge_from: torch.Tensor,
    edge_to: torch.Tensor,
    capacity_bearing: torch.Tensor,
    q_cap_nom_m3s: torch.Tensor,
    width_mean_m: torch.Tensor,
    shaft_length_proxy_m: float,
    node_elev_m: torch.Tensor,
    contrib_area_m2: torch.Tensor,
    outfall_node: torch.Tensor,
    node_cell_row: torch.Tensor,
    node_cell_col: torch.Tensor,
    node_id_map: torch.Tensor,
    node_cell_count: torch.Tensor,
    active_node: torch.Tensor | None = None,
    topo_order: list[int] | None = None,
    topo_sha256: str = "",
    graph_fingerprint: str = "",
    manifest: dict[str, Any] | None = None,
    device: str = "cpu",
    n_active_predicted_targets: int = -1,
    n_inactive_predicted_targets: int = -1,
) -> DrainGraph:
    """Assemble a :class:`DrainGraph` from realized arrays.

    Shared constructor for the loader (which feeds it artefact-derived arrays)
    and the toy-graph unit tests (which feed synthetic arrays through the SAME
    topology/component/plan machinery — one code path, per V2).
    """
    dev = torch.device(device)
    if dev.type != "cpu":
        # V12: this workflow allocates no CUDA context, ever.
        raise RefuseLoadError(f"[device] router is CPU-only (V12); got device {device!r}")

    edge_from = edge_from.to(dev, torch.int64)
    edge_to = edge_to.to(dev, torch.int64)
    capacity_bearing = capacity_bearing.to(dev, torch.bool)
    q_cap = q_cap_nom_m3s.to(dev, torch.float64).clone()
    num_nodes = int(node_elev_m.numel())
    num_edges = int(edge_from.numel())
    if edge_to.numel() != num_edges or capacity_bearing.numel() != num_edges:
        raise RefuseLoadError("[assemble] edge array lengths disagree")
    if q_cap.numel() != num_edges or width_mean_m.numel() != num_nodes:
        raise RefuseLoadError("[assemble] node/edge field lengths disagree")
    if num_nodes > 0 and (
        int(edge_from.max().item()) >= num_nodes or int(edge_to.max().item()) >= num_nodes
    ):
        # Fail loud here rather than as an opaque index error deep in topology
        # (caught live: 1-based gpkg ids fed unconverted produce exactly this).
        raise RefuseLoadError(
            "[assemble] edge endpoint index exceeds num_nodes-1 — are these raw 1-based "
            "node_ids? DrainGraph edge tensors are 0-based (index = node_id - 1)"
        )

    # Sentinel integrity at the assembly seam: capacity-bearing edges carry
    # finite positive capacities; everything else stays NaN — coercion to 0
    # anywhere upstream of here would be invisible later, so it is refused NOW.
    cb = capacity_bearing
    if not bool(torch.isfinite(q_cap[cb]).all()) or not bool((q_cap[cb] > 0).all()):
        raise RefuseLoadError("[assemble] capacity-bearing q_cap must be finite > 0")
    if bool((~torch.isnan(q_cap[~cb])).any()):
        raise RefuseLoadError(
            "[assemble] non-capacity-bearing edges must carry NaN sentinels (null stays "
            "null in code; coercing NaN->0 is invariant #11's defect)"
        )

    active_node_t = (
        active_node.to(dev, torch.bool)
        if active_node is not None
        else torch.ones(num_nodes, dtype=torch.bool, device=dev)
    )
    topo_level, _n_levels = _compute_topo_levels(edge_from, edge_to, num_nodes)
    active_edge = active_node_t[edge_from] & active_node_t[edge_to]
    component_class = _compute_component_classes(
        outfall_node.to(dev), edge_from, edge_to, num_nodes
    )
    plan = _build_route_plan(
        edge_from, edge_to, q_cap, cb & active_edge, topo_level, num_nodes, dev
    )
    if topo_order is None:
        # Stable topological order derived from the recomputed levels.
        order = sorted(range(num_nodes), key=lambda i: (int(topo_level[i]), i))
        topo_order = [i + 1 for i in order]

    return DrainGraph(
        num_nodes=num_nodes,
        num_edges=num_edges,
        topo_order=topo_order,
        topo_level=topo_level,
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=cb,
        q_cap_nom_m3s=q_cap,
        width_mean_m=width_mean_m.to(dev, torch.float64),
        node_plan_area_m2=width_mean_m.to(dev, torch.float64) * float(shaft_length_proxy_m),
        node_elev_m=node_elev_m.to(dev, torch.float64),
        contrib_area_m2=contrib_area_m2.to(dev, torch.float64),
        component_class=component_class,
        outfall_node=outfall_node.to(dev, torch.bool),
        node_cell_row=node_cell_row.to(dev, torch.int32),
        node_cell_col=node_cell_col.to(dev, torch.int32),
        node_id_map=node_id_map.to(dev, torch.int32),
        node_cell_count=node_cell_count.to(dev, torch.int64),
        active_node=active_node_t,
        active_edge=active_edge,
        # BugHunt round-1 item B: this used to hardcode num_nodes while the
        # sibling edge count was mask-derived — on a windowed load every
        # consumer then read "all nodes active" no matter the view. Forward the
        # realized count, same source as n_active_edges.
        n_active_nodes=int(active_node_t.sum().item()),
        n_active_edges=int(active_edge.sum().item()),
        graph_fingerprint=graph_fingerprint,
        topo_sha256=topo_sha256,
        manifest=dict(manifest or {}),
        plan=plan,
        n_active_predicted_targets=int(n_active_predicted_targets),
        n_inactive_predicted_targets=int(n_inactive_predicted_targets),
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _normalize_window(
    window: tuple[slice, slice] | tuple[int, int, int, int] | None, height: int, width: int
) -> tuple[int, int, int, int]:
    """Normalize the window argument to (row0, row1, col0, col1) absolute bounds.

    Accepted forms: ``None`` (full domain), a ``(rows_slice, cols_slice)`` pair,
    or an explicit ``(row0, row1, col0, col1)`` int tuple. Slice steps must be 1.
    Any malformed window raises :class:`RefuseLoadError` — never a bare
    TypeError/ValueError (defect D: the documented slice-pair form used to die
    with ``TypeError: slice indices must be integers`` inside
    ``slice(rows).indices(...)`` before the refusal gates were reached).
    """
    if window is None:
        return 0, height, 0, width
    if not isinstance(window, tuple):
        raise RefuseLoadError(f"[window] unsupported window form: {window!r}")
    if len(window) == 4 and all(isinstance(v, int) and not isinstance(v, bool) for v in window):
        r0, r1, c0, c1 = window  # type: ignore[misc]
        if not (0 <= r0 < r1 <= height and 0 <= c0 < c1 <= width):
            raise RefuseLoadError(
                f"[window] invalid bounds {(r0, r1, c0, c1)} for grid {(height, width)}"
            )
        return int(r0), int(r1), int(c0), int(c1)
    if len(window) == 2 and all(isinstance(part, slice) for part in window):
        rows, cols = window  # type: ignore[misc]
        r0, r1, rs = rows.indices(height)
        c0, c1, cs = cols.indices(width)
        if rs != 1 or cs != 1:
            raise RefuseLoadError(f"[window] slice steps must be 1: rows={rows!r} cols={cols!r}")
        if r1 <= r0 or c1 <= c0:
            raise RefuseLoadError(f"[window] empty window rows={rows!r} cols={cols!r}")
        return int(r0), int(r1), int(c0), int(c1)
    raise RefuseLoadError(
        f"[window] unsupported window form: {window!r}; expected None, "
        "(rows_slice, cols_slice), or (row0, row1, col0, col1) ints"
    )


def _paint_node_id_map(
    x_m: np.ndarray,
    y_m: np.ndarray,
    node_row: np.ndarray,
    node_col: np.ndarray,
    active: np.ndarray,
    *,
    view_r0: int,
    view_c0: int,
    view_h: int,
    view_w: int,
    res_m: float,
    x_origin_m: float,
    y_top_m: float,
    radius_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Allocate every view cell within ``radius_m`` of an ACTIVE node to its
    nearest such node; exact ties resolve to the LOWEST node_id (OQ1 pin).

    Mechanism: paint nodes in ascending node_id order, overwriting only on a
    STRICTLY smaller distance — earlier (lower-id) nodes therefore win ties by
    construction and the final owner of each cell is its min-(distance, id)
    node. Cells beyond the radius from every active node stay -1 ("cells
    beyond radius from every node never capture", spec §5 exchange key).

    Returns (node_id_map int32 (view_h, view_w), node_cell_count int64 (N,)),
    enforcing the spec §4.2 guarantee ``node_cell_count >= 1`` for active
    nodes: any active node left with zero cells by nearest-wins (measured: 42
    on the real graph) is assigned its own cell. See module docstring for the
    reported deviation this resolves.
    """
    num_nodes = x_m.shape[0]
    best = np.full((view_h, view_w), np.inf, dtype=np.float64)
    nmap = np.full((view_h, view_w), -1, dtype=np.int32)
    k = int(math.ceil(radius_m / res_m))
    r_limit = radius_m + _TIE_EPS_M
    for i in np.nonzero(active)[0]:  # ascending node order => lowest-id wins ties
        gr, gc = int(node_row[i]), int(node_col[i])
        ni_x, ni_y = x_m[i], y_m[i]
        for rr in range(max(gr - k, 0), min(gr + k + 1, view_r0 + view_h)):
            cy = y_top_m - rr * res_m - res_m / 2.0
            vrr = rr - view_r0
            if vrr < 0:
                continue
            for cc in range(max(gc - k, 0), min(gc + k + 1, view_c0 + view_w)):
                vcc = cc - view_c0
                if vcc < 0:
                    continue
                cx = x_origin_m + cc * res_m + res_m / 2.0
                d = math.hypot(cx - ni_x, cy - ni_y)
                if d <= r_limit and d < best[vrr, vcc]:
                    best[vrr, vcc] = d
                    nmap[vrr, vcc] = i + 1  # node_id = index + 1
    counts = np.bincount(nmap[nmap >= 0] - 1, minlength=num_nodes).astype(np.int64)
    starved = np.nonzero(active & (counts == 0))[0]
    for i in starved:
        nmap[int(node_row[i]) - view_r0, int(node_col[i]) - view_c0] = int(i) + 1
    if starved.size:
        counts = np.bincount(nmap[nmap >= 0] - 1, minlength=num_nodes).astype(np.int64)
    return nmap, counts


def _load_predicted_node_ids(path: Path) -> list[int]:
    """Read the pre-registered falsifier prediction set's node targets."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RefuseLoadError(
            f"[falsifier] cannot read pre-registered prediction set at {path} "
            f"(config key diagnostics.falsifier_set): {exc}"
        ) from exc
    try:
        return [int(i) for i in data["predicted_node_ids"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise RefuseLoadError(
            f"[falsifier] prediction set at {path} lacks a usable integer "
            f"'predicted_node_ids' list: {exc}"
        ) from exc


def _falsifier_activity_gate(
    targets: list[int],
    active_np: np.ndarray,
    *,
    min_active_required: int,
) -> tuple[int, int]:
    """Refuse unless >= ``min_active_required`` pre-registered falsifier target
    nodes are active in the loaded view; return ``(n_active, n_inactive)``.

    Defect-C adjudication (2026-08-26): a SUBWINDOW load passes
    ``cfg.smoke.min_predicted_nodes_in_window`` — the spec §14 smoke window
    (first 10 predicted nodes + pad) holds only 14/116 targets, so the former
    require-ALL gate refused that path forever — while a FULL-domain load
    passes ``len(targets)`` (ALL targets active). Out-of-range target node_ids
    refuse regardless of window.
    """
    num_nodes = int(active_np.shape[0])
    oob = [t for t in targets if not (1 <= t <= num_nodes)]
    if oob:
        raise RefuseLoadError(f"[falsifier] target node_ids outside 1..{num_nodes}: {oob[:10]}")
    inactive = [t for t in targets if not bool(active_np[t - 1])]
    n_active = len(targets) - len(inactive)
    if n_active < min_active_required:
        raise RefuseLoadError(
            f"[window] only {n_active}/{len(targets)} falsifier target node(s) active "
            f"(required >= {min_active_required}); first inactive: {inactive[:10]}"
        )
    return n_active, len(inactive)


def load_drain_graph(
    cfg: CouplingConfig,
    repo_root: Path,
    window: tuple[slice, slice] | tuple[int, int, int, int] | None = None,
) -> DrainGraph:
    """Load the WF-1 drain-graph artefact with the full V8 assertion stack.

    Order of operations (spec §4.2): the existing asserting reader runs FIRST
    (schema, fingerprint-vs-bytes, elevation binding — its
    :class:`RefuseLoadError` propagates unmodified); THEN this module's
    consumer assertions: DAG flag, expected counts and partition, sentinel
    integrity, topo-level recomputation, component classes, node->cell maps
    for ``window``, and the subwindow falsifier-activity refusal.

    Args:
        cfg: resolved coupling configuration (paths, expectations, storage
            declared assumptions, capture radius).
        repo_root: repository root (kept for signature parity / future
            relative-path resolution; configured paths arrive absolute).
        window: ``None`` for the full buffered domain, or ``(rows, cols)``
            slices / ``(row0, row1, col0, col1)`` bounds selecting the ACTIVE
            GRID VIEW. The graph tensors always stay full-size (N/E as
            asserted); the window induces the routed subgraph
            (``active_edge`` = both endpoints in-window) and crops
            ``node_id_map`` to the view. Falsifier-gate refusal semantics
            (defect C, adjudicated 2026-08-26): a windowed load REFUSES if
            fewer than ``cfg.smoke.min_predicted_nodes_in_window`` of the
            pre-registered falsifier target nodes are active in it; a
            full-domain load still requires ALL of them active. Either way the
            realized counts are recorded on ``DrainGraph`` as
            ``n_active_predicted_targets`` / ``n_inactive_predicted_targets``.

    Returns:
        The assembled :class:`DrainGraph`.
    """
    del repo_root  # configured artefact paths are already absolute (rule-7 resolver)

    if cfg.device != "cpu":
        raise RefuseLoadError(f"[device] router is CPU-only (V12); cfg.device={cfg.device!r}")
    if cfg.router.null_edge_policy != "q_zero_always":  # D-C pin; resolver also refuses
        raise RefuseLoadError(
            f"[router] null_edge_policy {cfg.router.null_edge_policy!r} != 'q_zero_always'"
        )

    art = read_artefact(
        str(cfg.graph.gpkg),
        str(cfg.graph.adjacency),
        str(cfg.graph.manifest),
        owner_adjudication_ref=OWNER_ADJUDICATION_REF_M5B,
    )
    man = art["manifest"]
    nodes = art["nodes_gdf"]
    edges = art["edges_gdf"]

    # --- explicit consumer assertions on top of the reader (spec §4.2) -------
    if man.get("graph_is_dag") is not True:
        raise RefuseLoadError(f"[consumer] manifest.graph_is_dag={man.get('graph_is_dag')!r}")
    exp_counts = cfg.graph.expected_counts
    if len(nodes) != exp_counts.nodes or len(edges) != exp_counts.edges:
        raise RefuseLoadError(
            f"[consumer] realized graph size nodes={len(nodes)} edges={len(edges)} != "
            f"expected ({exp_counts.nodes}, {exp_counts.edges})"
        )
    if int(man.get("node_count", -1)) != len(nodes) or int(man.get("edge_count", -1)) != len(edges):
        raise RefuseLoadError("[consumer] manifest node_count/edge_count disagree with gpkg")

    # --- partition from realized bytes ---------------------------------------
    edge_source = edges["edge_source"].astype(str)
    basis = edges["capacity_basis"].astype(str)
    q_notna = edges["q_capacity_nom_m3s"].notna().to_numpy()
    is_syn = (edge_source == "synthesised").to_numpy()
    is_obs = (edge_source == "observed").to_numpy()
    is_cb = is_obs & q_notna
    obs_null = is_obs & ~q_notna
    is_zero = obs_null & basis.str.startswith(_ZERO_SLOPE_BASIS_PREFIX).to_numpy()
    is_area = obs_null & basis.str.startswith(_AREA_CAPPED_BASIS_PREFIX).to_numpy()
    remainder = ~(is_syn | is_cb | is_zero | is_area)
    part = cfg.graph.expected_partition
    problems = []
    if int(is_cb.sum()) != part.capacity_bearing:
        problems.append(f"capacity_bearing {int(is_cb.sum())} != {part.capacity_bearing}")
    if int(is_zero.sum()) != part.zero_slope:
        problems.append(f"zero_slope {int(is_zero.sum())} != {part.zero_slope}")
    if int(is_area.sum()) != part.area_capped:
        problems.append(f"area_capped {int(is_area.sum())} != {part.area_capped}")
    if int(is_syn.sum()) != part.synthetic:
        problems.append(f"synthetic {int(is_syn.sum())} != {part.synthetic}")
    if int(remainder.sum()) != 0:
        problems.append(f"{int(remainder.sum())} edges outside all four partition classes")
    if problems:
        raise RefuseLoadError("[consumer] edge partition mismatch: " + "; ".join(problems))

    # --- core tensors (copies; the gdf frames are release-grade scratch) -----
    # gpkg node_id is 1-based; DrainGraph edge tensors are 0-BASED INDEXED
    # (index = node_id - 1, per the dataclass contract).
    edge_from_raw = edges["from_node"].to_numpy(dtype=np.int64)
    edge_to_raw = edges["to_node"].to_numpy(dtype=np.int64)
    if int(edge_from_raw.min()) < 1 or int(edge_to_raw.min()) < 1:
        raise RefuseLoadError("[consumer] node_ids below 1 in gpkg edges")
    edge_from = torch.tensor(edge_from_raw - 1)
    edge_to = torch.tensor(edge_to_raw - 1)
    q_cap = torch.tensor(edges["q_capacity_nom_m3s"].to_numpy(dtype=np.float64))  # NaN kept
    capacity_bearing = torch.tensor(is_cb)
    num_nodes = len(nodes)

    # width_mean over CAPACITY-BEARING incident edges only (contract
    # node_storage_pinned); isolated -> pinned tertiary nominal 2.285 m.
    width_edge = edges["width_m"].to_numpy(dtype=np.float64)
    cb_w = torch.tensor(np.where(is_cb, width_edge, 0.0))
    cb_sel = torch.tensor(is_cb)
    w_sum = torch.index_add(
        torch.index_add(
            torch.zeros(num_nodes, dtype=torch.float64), 0, edge_from[cb_sel], cb_w[cb_sel]
        ),
        0,
        edge_to[cb_sel],
        cb_w[cb_sel],
    )
    cb_ones = torch.ones(int(cb_sel.sum()), dtype=torch.float64)
    w_cnt = torch.index_add(
        torch.index_add(torch.zeros(num_nodes, dtype=torch.float64), 0, edge_from[cb_sel], cb_ones),
        0,
        edge_to[cb_sel],
        cb_ones,
    )
    safe_cnt = torch.clamp(w_cnt, min=torch.finfo(torch.float64).tiny)
    width_mean = torch.where(
        w_cnt > 0,
        w_sum / safe_cnt,
        torch.full_like(w_sum, float(cfg.storage.isolated_node_width_m)),
    )

    node_elev = torch.tensor(nodes["elev_m"].to_numpy(dtype=np.float64))
    ca_raw = nodes["contrib_area_m2"].to_numpy(dtype=np.float64)
    contrib = torch.tensor(np.where(np.isnan(ca_raw), 0.0, ca_raw))
    # Declared outfall set (rule 0): gpkg node_type == 'outfall', exactly as
    # before WF-2 M1. Extended seeds (lake_polygon / domain_boundary) are added
    # BELOW, after the producer grid block is parsed — consumer-side only.
    declared_outfall_np = (nodes["node_type"].astype(str) == "outfall").to_numpy()

    # --- grid geometry: producer-declared block from the graph manifest (V8) --
    grid = ((man.get("config_snapshot") or {}).get("grid") or {}) if man else {}
    for key in ("height", "width", "transform", "cell_area_m2"):
        if key not in grid:
            raise RefuseLoadError(f"[grid] graph manifest config_snapshot.grid missing {key!r}")
    tr = grid["transform"]
    res, x_origin, y_top = float(tr[0]), float(tr[2]), float(tr[5])
    if float(tr[1]) != 0.0 or float(tr[3]) != 0.0 or abs(float(tr[4])) != res:
        raise RefuseLoadError(f"[grid] transform {tr} is not axis-aligned at resolution {res}")
    gh, gw = int(grid["height"]), int(grid["width"])
    if float(grid["cell_area_m2"]) != 100.0:
        raise RefuseLoadError(
            f"[grid] cell_area_m2 {grid['cell_area_m2']} != 100.0 (contract units block)"
        )
    x_arr = nodes["x_m"].to_numpy(dtype=np.float64)
    y_arr = nodes["y_m"].to_numpy(dtype=np.float64)
    col_abs = np.floor((x_arr - x_origin) / res).astype(np.int64)
    row_abs = np.floor((y_top - y_arr) / res).astype(np.int64)
    inside = (col_abs >= 0) & (col_abs < gw) & (row_abs >= 0) & (row_abs < gh)
    if not bool(inside.all()):
        raise RefuseLoadError("[grid] a node falls outside the producer-declared buffered grid")

    # --- WF-2 M1 terminal-seed extension (v2-lake-boundary; consumer-side) ----
    # Owner directive: a rajakaluve discharging into Bellandur/Varthur Lake HAS
    # reached a terminal sink; a reach crossing the domain boundary HAS left
    # the domain. The definition lives in cfg.graph.terminal_definition
    # (configs/terminal_definition.yaml, visible + justified); seeds are derived
    # HERE at load, never written into producer artefacts (the drain-graph
    # contract forbids producers emitting node_type=inlet/lake_boundary — flag:
    # a contract amendment note may be wanted once boundary exchange lands).
    tdef = load_terminal_definition(Path(cfg.graph.terminal_definition))
    terminal = resolve_terminal_nodes(
        node_xy=np.column_stack([x_arr, y_arr]),
        edge_pairs=list(zip(edge_from_raw.tolist(), edge_to_raw.tolist(), strict=True)),
        declared_outfall_ids=nodes.loc[declared_outfall_np, "node_id"].astype(int).tolist(),
        tdef=tdef,
        grid_block=grid,
    )
    outfall_node = torch.zeros(len(nodes), dtype=torch.bool)
    for nid in terminal.seed_union:
        outfall_node[nid - 1] = True

    # V8 cross-assertion AT THE SEAM: the torch BFS that feeds
    # graph.component_class and the canonical stdlib traversal in
    # jaladhar.drainage.terminal must partition the graph IDENTICALLY over the
    # extended seed set. Two traversals exist by design; this is what keeps
    # their DEFINITION single. Divergence refuses the load.
    classes_torch = _compute_component_classes(outfall_node, edge_from, edge_to, len(nodes))
    torch_ot = {i + 1 for i, c in enumerate(classes_torch) if c == "outfall_terminating"}
    canon_ot = classify_reachability(
        list(zip(edge_from_raw.tolist(), edge_to_raw.tolist(), strict=True)),
        terminal.seed_union,
    )
    if torch_ot != canon_ot:
        diff = sorted(torch_ot.symmetric_difference(canon_ot))
        raise RefuseLoadError(
            f"[terminal] cross-traversal partition mismatch on {len(diff)} node(s) "
            f"(e.g. {diff[:10]}): router torch-BFS={len(torch_ot)} vs canonical "
            f"classify_reachability={len(canon_ot)} — refusing"
        )

    # Thread the realized seed resolution into the manifest echo so run
    # manifests and diagnostics.component_split carry definition_version +
    # counts_by_rule (labels only; historical run manifests are NOT rewritten).
    man = {**man, "terminal_seed_resolution": terminal.as_manifest_block()}

    # --- window / induced subgraph / falsifier gate ---------------------------
    vr0, vr1, vc0, vc1 = _normalize_window(window, gh, gw)
    active_np = (row_abs >= vr0) & (row_abs < vr1) & (col_abs >= vc0) & (col_abs < vc1)
    active_node_t = torch.tensor(active_np)
    n_active_nodes = int(active_node_t.sum().item())

    # Defect-C semantics (adjudicated 2026-08-26): a SUBWINDOW load refuses when
    # FEWER than cfg.smoke.min_predicted_nodes_in_window pre-registered target
    # nodes are active in it — the spec §14 smoke window (first 10 predicted
    # nodes + cfg.smoke.window_pad_cells pad) holds only 14/116 targets, so the
    # former require-ALL gate made that path unloadable permanently. A
    # FULL-domain load still requires ALL targets active. The realized
    # active/inactive counts are recorded on the graph either way.
    n_active_pred = n_inactive_pred = -1  # -1: not assessed (no usable set)
    fs_path = Path(cfg.diagnostics.falsifier_set)
    targets: list[int] = []
    if window is not None:
        if not fs_path.exists():
            raise RefuseLoadError(
                f"[window] falsifier prediction set missing at {fs_path} "
                "(diagnostics.falsifier_set); a windowed load must verify target activity"
            )
        targets = _load_predicted_node_ids(fs_path)
        min_required = int(cfg.smoke.min_predicted_nodes_in_window)
    elif fs_path.exists():
        # Full domain: strict ALL-targets-active requirement retained. A missing
        # set here is diagnostics.refuse_start_on_missing_falsifier's refusal to
        # own (resolver-side), not this loader's — hence exists() gated.
        targets = _load_predicted_node_ids(fs_path)
        min_required = len(targets)
    else:
        min_required = 0
    if targets:
        n_active_pred, n_inactive_pred = _falsifier_activity_gate(
            targets, active_np, min_active_required=min_required
        )

    # --- node->cell maps for the active view ---------------------------------
    nmap_np, counts_np = _paint_node_id_map(
        x_arr,
        y_arr,
        row_abs,
        col_abs,
        active_np,
        view_r0=vr0,
        view_c0=vc0,
        view_h=vr1 - vr0,
        view_w=vc1 - vc0,
        res_m=res,
        x_origin_m=x_origin,
        y_top_m=y_top,
        radius_m=float(cfg.exchange.capture_radius_m),
    )
    if n_active_nodes > 0 and int(counts_np[active_np].min()) < 1:
        raise RefuseLoadError("[maps] active node left with zero allocated cells")

    topo_order = [int(i) for i in art["adjacency"]["topo_order"]]
    topo_sha = hashlib.sha256(
        json.dumps(topo_order, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return build_drain_graph(
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=capacity_bearing,
        q_cap_nom_m3s=q_cap,
        width_mean_m=width_mean,
        shaft_length_proxy_m=float(cfg.storage.shaft_length_proxy_m),
        node_elev_m=node_elev,
        contrib_area_m2=contrib,
        outfall_node=outfall_node,
        node_cell_row=torch.tensor(row_abs.astype(np.int32)),
        node_cell_col=torch.tensor(col_abs.astype(np.int32)),
        node_id_map=torch.tensor(nmap_np),
        node_cell_count=torch.tensor(counts_np),
        active_node=active_node_t,
        topo_order=topo_order,
        topo_sha256=topo_sha,
        graph_fingerprint=str(man.get("graph_fingerprint", "")),
        manifest=man,
        device=cfg.device,
        n_active_predicted_targets=n_active_pred,
        n_inactive_predicted_targets=n_inactive_pred,
    )


# ---------------------------------------------------------------------------
# Routing (hot path)
# ---------------------------------------------------------------------------


def route(
    vol: torch.Tensor, graph: DrainGraph, dt: float
) -> tuple[torch.Tensor, torch.Tensor, dict]:
    """One host-dt routing pass over the drain graph (spec §7, D-C pin verbatim).

    Args:
        vol: (N,) float64 node volumes [m³]. Never mutated.
        graph: loaded :class:`DrainGraph`.
        dt: host timestep [s], finite > 0. No substepping.

    Returns:
        (vol_new (N,) float64, q_edge (E,) float64, diag dict). ``q_edge`` is
        positive downstream, EXACTLY 0.0 on every non-capacity-bearing edge and
        every inactive (outside the induced subgraph) edge; gradients flow
        through both returns (no detach, no in-place writes).

    Raises:
        ValueError: on dt/vol validation failures.
    """
    if (
        not isinstance(dt, (int, float))
        or isinstance(dt, bool)
        or not math.isfinite(float(dt))
        or float(dt) <= 0.0
    ):
        raise ValueError(f"[route] dt must be a finite number > 0, got {dt!r}")
    dt = float(dt)
    if not isinstance(vol, torch.Tensor) or vol.dtype is not torch.float64:
        raise ValueError(
            f"[route] vol must be a float64 torch.Tensor (contract units), got "
            f"{type(vol).__name__}/{getattr(vol, 'dtype', None)}"
        )
    if vol.shape != (graph.num_nodes,):
        raise ValueError(f"[route] vol shape {tuple(vol.shape)} != ({graph.num_nodes},)")
    if vol.device != graph.edge_from.device:
        raise ValueError(
            f"[route] vol device {vol.device} != graph device {graph.edge_from.device}"
        )
    if not bool(torch.isfinite(vol).all()):
        raise ValueError("[route] vol contains NaN/inf")

    dev = graph.edge_from.device
    v = vol  # never written; every level produces a NEW tensor
    q_pieces: list[torch.Tensor] = []
    pos_pieces: list[torch.Tensor] = []
    transferred_total = torch.zeros((), dtype=torch.float64, device=dev)
    capped_edges = 0
    for lv in graph.plan.levels:
        available_rate = torch.clamp(v[lv.src], min=0.0) / dt  # clamp-before-divide
        q = _level_transfer(available_rate, lv.frac, lv.cap)
        transferred = q * dt
        flow_out = torch.index_add(torch.zeros_like(v), 0, lv.src, transferred)
        flow_in = torch.index_add(torch.zeros_like(v), 0, lv.dst, transferred)
        v = v + (flow_in - flow_out)  # volume-conservative, out-of-place
        q_pieces.append(q)
        pos_pieces.append(lv.edge_pos)
        transferred_total = transferred_total + transferred.sum()
        capped_edges += int(torch.isclose(q, lv.cap, rtol=0.0, atol=0.0).sum().item())

    if q_pieces:
        q_edge = torch.index_add(
            torch.zeros(graph.num_edges, dtype=torch.float64, device=dev),
            0,
            torch.cat(pos_pieces),
            torch.cat(q_pieces),
        )
    else:
        q_edge = torch.zeros(graph.num_edges, dtype=torch.float64, device=dev)

    diag = {
        "levels_processed": graph.plan.n_levels,
        "edges_routed": graph.plan.edges_routed,
        "volume_transferred_m3": float(transferred_total.item()),
        "capacity_bound_edges": capped_edges,
        "max_edge_q_m3s": float(q_edge.max().item()) if q_edge.numel() else 0.0,
    }
    return v, q_edge, diag


# ---------------------------------------------------------------------------
# CLI (AGENTS.md style rule: every stage runnable standalone)
# ---------------------------------------------------------------------------


@app.command()
def selfcheck(
    config: Path = typer.Option(
        Path("configs/coupling.yaml"), "--config", help="Coupling YAML (resolved via rule 7)"
    ),
    vol_m3: float = typer.Option(1000.0, help="Uniform initial volume per node [m3]"),
    dt: float = typer.Option(0.48, help="Routing timestep [s]"),
) -> None:
    """Load the REAL drain graph with all V8 assertions and route once.

    Prints the realized counts, partition, topo levels, component split, NaN
    sentinel statistics, and the end-to-end volume conservation identity —
    the     V12 execution evidence for this unit."""
    repo_root = Path(__file__).resolve().parents[3]
    cfg = resolve_config(config, repo_root)
    graph = load_drain_graph(cfg, repo_root)
    split = {
        "outfall_terminating": graph.component_class.count("outfall_terminating"),
        "dead_end": graph.component_class.count("dead_end"),
    }
    level_sizes = torch.bincount(graph.topo_level)
    print(
        f"nodes={graph.num_nodes} edges={graph.num_edges} "
        f"fingerprint={graph.graph_fingerprint[:12]}..."
    )
    null_all_nan = bool(torch.isnan(graph.q_cap_nom_m3s[~graph.capacity_bearing]).all())
    print(
        f"partition: cb={int(graph.capacity_bearing.sum())} "
        f"null={int((~graph.capacity_bearing).sum())} "
        f"(null q_cap all NaN: {null_all_nan})"
    )
    print(
        f"topo_levels={graph.plan.n_levels} largest_level0={int(level_sizes.max())} "
        f"topo_sha256={graph.topo_sha256[:12]}..."
    )
    print(f"component_split={split}")
    print(
        f"node maps: allocated_cells={int(graph.node_cell_count.sum())} "
        f"min_count={int(graph.node_cell_count.min())}"
    )
    vol = torch.full((graph.num_nodes,), float(vol_m3), dtype=torch.float64)
    before = float(vol.sum().item())
    vol_new, q_edge, diag = route(vol, graph, dt)
    after = float(vol_new.sum().item())
    null_q = q_edge[~graph.capacity_bearing]
    print(
        f"route(dt={dt}): transferred={diag['volume_transferred_m3']:.6f} m3 "
        f"capped_edges={diag['capacity_bound_edges']} max_q={diag['max_edge_q_m3s']:.3f} m3/s"
    )
    print(f"volume_conservation: before={before!r} after={after!r} diff={after - before!r}")
    print(f"null_edge_q_exact_zero={bool((null_q == 0).all())}")


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":
    main()
