"""WF-2 exchange: surface<->drain weir/orifice coupling (spec §4.3/§6 verbatim).

Implements :func:`couple_step` — the frozen contract
``configs/contracts/coupling_iface.json`` v1.1.0 ``exchange_call.producer``:
ONE module owns the FULL exchange for each host ``dt``, in the pinned order

    (1) CAPTURE   surface -> nodes      (per allocated cell, weir/orifice regime)
    (2) ROUTE     node -> node          (:func:`jaladhar.coupling.router.route`)
    (3) RETURN    nodes -> surface      (surcharge through the inverted forms)

Physics is spec §6 word for word. ``h_eff := max(0, h_before_coupling)``; the
regime switch ``h_eff < 0.1 m -> weir else orifice`` is INCLUSIVE at the lower
bound (weir), and the SAME switch applies to the return path on excess head
``h_excess = h_node_m - assumed_freeboard_m``. Capture carries the panel-unique
NO-REVERSE-CAPTURE guard: ``Q = 0`` whenever ``head_diff <= 0`` — return is the
ONLY node->surface path. ``hf_floor = 0.001 m`` is applied CLAMP-FIRST, before
every pow/sqrt — the same discipline as the solver hf clamp (``acc.py:204``),
because a NaN produced in a dead ``torch.where`` branch still poisons
``.backward()``. Both stability caps run through ``torch.minimum`` (extends the
``acc.py`` sink-cap pattern), which is what makes ``h_new >= 0`` ALGEBRAIC:
``captured <= 0.9*(h_before - hf_floor) < h_before`` and ``returned >= 0``, so
the single contract expression ``h_new = h_before - captured + returned``
cannot invert a depth — asserted loud at exit, never clamped silently.

Node storage: the contract pins ``NodeState`` to (f32 head, two f64 cumulative
books) with NO explicit volume field, and defines ``h_node_m =
vol_m3/node_plan_area_m2``. couple_step therefore reconstructs the f64 node
volume each step as ``h_node_m * node_plan_area`` — the contract's own
definition run backwards. The f32 head quantisation this introduces is a
bounded per-step dither (ULP(1.5 m) x plan area), re-derived fresh every step,
not an accumulating drift; the f64 books stay exact.

Autograd rules (contract ``autograd_rules``): out-of-place only; no in-place
mutation of ``h/qx/qy/node_state`` or the frozen ``StaticFields`` (the Phase-2
``_boundary_outflux`` incident class — ``qx``/``qy`` are bitwise-compared
against entry clones at exit as a hard guarantee); gradients MUST flow through
capture+return for ``replay()`` calibration; clamp first, divide second,
select third; ``.detach()`` appears ONLY on the explicitly-detached diagnostic
result fields, exactly like ``acc.py`` detaches diagnostics after the state is
formed — never in the gradient path.

Pinned constants: the frozen ``couple_step`` signature has NO config channel,
so the contract-frozen coefficient values live here as named module constants,
each carrying its citation or declared-assumption label. Drift between these
constants and ``configs/coupling.yaml`` is asserted impossible by
``tests/coupling/test_exchange.py::test_constants_match_resolved_config``
(V8-style seam check; the resolver independently asserts hf_floor parity with
the solver physics block).

Coefficients and labels (rule 1: cited or they do not ship):
- ``Cw = 1.7`` — FHWA HEC-22 / EPA SWMM Reference Manual Vol II weir forms;
  derived check ``(2/3)**1.5 * sqrt(g) = 1.705`` reproduces it [spec §6].
- ``Cd = 0.65`` — FHWA HEC-22 / EPA SWMM Reference Manual Vol II orifice forms.
- ``regime_switch = 0.1 m`` — contract ``stability_constraints.h_eff_definition_pinned``.
- ``hf_floor = 0.001 m`` — parity with solver ``physics.hf_floor_m`` (resolver cross-key).
- ``g = 9.81 m/s2`` — parity with solver ``physics.gravity_m_s2``.
- ``capture/return cap fractions 0.9`` — contract ``stability_constraints``.
- ``A_open = width_mean_m * 0.1`` — ‡ DECLARED ASSUMPTION v1 (unsourced,
  pinned, versioned via coupling_version, never tuned).
- ``assumed_freeboard_m = 1.5`` — ‡ DECLARED ASSUMPTION v1, NOT attributed to
  any standard (contract ``node_storage_pinned.freeboard_basis``).
- ``cell_area = 100.0 m2`` — contract ``units`` block; the loader refuses a
  producer grid declaring any other cell area.

CPU-only (V12): any CUDA tensor or device is refused outright so no CUDA
context is ever initialised from this module.

Documented semantic choices (stated, not silent):
- ``max_node_head_m`` is the PEAK node head this step — after capture and
  routing but BEFORE return removes volume (post-return head understates the
  surge peak that event recording wants).
- ``surcharging_nodes`` counts nodes with ``R_node > 0`` this step (contract:
  "nodes with returned contribution > 0 this step").
- ``cap_binding_this_step`` is 1 iff any cell's hydraulic capture depth or any
  node's hydraulic return volume strictly exceeded its stability cap.
- Node continuity reconstructs gross edge in/out volumes from ``q_edge``
  beside ``route()``'s sequentially-updated state; the fp REORDER between the
  two is ULP-level and the in-function continuity guard tolerates exactly
  that (a dropped term is O(volume) and trips it loudly).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import torch
import typer

from jaladhar.coupling.router import DrainGraph, build_drain_graph, route
from jaladhar.solver.state import StaticFields

__all__ = [
    "A_OPEN_WIDTH_FRACTION",
    "ASSUMED_FREEBOARD_M",
    "CAPTURE_CAP_FRACTION",
    "CELL_AREA_M2",
    "CoupleResult",
    "GRAVITY_M_S2",
    "HF_FLOOR_M",
    "NodeState",
    "ORIFICE_COEFF_CD",
    "REGIME_SWITCH_M",
    "RETURN_CAP_FRACTION",
    "WEIR_COEFF_CW",
    "build_node_state",
    "couple_step",
    "zero_drain_cap_out_of_place",
]

app = typer.Typer(add_completion=False)

# Contract-pinned constants (see module docstring for citations/labels).

WEIR_COEFF_CW: float = 1.7
ORIFICE_COEFF_CD: float = 0.65
REGIME_SWITCH_M: float = 0.1
HF_FLOOR_M: float = 0.001
GRAVITY_M_S2: float = 9.81
CAPTURE_CAP_FRACTION: float = 0.9
RETURN_CAP_FRACTION: float = 0.9
A_OPEN_WIDTH_FRACTION: float = 0.1
ASSUMED_FREEBOARD_M: float = 1.5  # ‡ declared assumption v1: surcharge activation head
CELL_AREA_M2: float = 100.0


@dataclass(frozen=True)
class NodeState:

    h_node_m: torch.Tensor
    vol_in_m3_cum: torch.Tensor
    vol_out_m3_cum: torch.Tensor


@dataclass(frozen=True)
class CoupleResult:

    h_new: torch.Tensor
    node_state_new: NodeState
    captured_depth_m: torch.Tensor
    returned_depth_m: torch.Tensor
    capture_m3: float
    return_m3: float
    surcharging_nodes: int
    max_node_head_m: float
    cap_binding_this_step: int
    edge_flow_m3s: torch.Tensor


# Guards and hot-path helpers (module-level so the V5 red demos can mutate


def zero_drain_cap_out_of_place(static: StaticFields) -> StaticFields:
    return replace(static, drain_cap_m_s=torch.zeros_like(static.drain_cap_m_s))


def build_node_state(graph: DrainGraph, device: str = "cpu") -> NodeState:
    dev = torch.device(device)
    if dev.type != "cpu":

        raise ValueError(f"[exchange] CPU-only (V12); got device {device!r}")
    n = graph.num_nodes
    return NodeState(
        h_node_m=torch.zeros(n, dtype=torch.float32, device=dev),
        vol_in_m3_cum=torch.zeros(n, dtype=torch.float64, device=dev),
        vol_out_m3_cum=torch.zeros(n, dtype=torch.float64, device=dev),
    )


def _validated_dt(dt: Any) -> float:
    if (
        not isinstance(dt, (int, float))
        or isinstance(dt, bool)
        or not math.isfinite(float(dt))
        or float(dt) <= 0.0
    ):
        raise ValueError(f"[exchange] dt must be a finite number > 0, got {dt!r}")
    return float(dt)


def _require_cpu(t: torch.Tensor, what: str) -> None:
    if t.device.type != "cpu":
        raise ValueError(f"[exchange] {what} lives on {t.device}; this module is CPU-only (V12)")


def _no_reverse_gate(head_diff: torch.Tensor) -> torch.Tensor:
    """NO-REVERSE-CAPTURE gate: 1 where ``head_diff > 0`` else 0.

    Applied by MULTIPLICATION (the acc.py gate-weight pattern: identical
    forward values, zero gradient contribution from sealed cells, no dead
    branch behind a select). Module-level so the V5 red demo can remove the
    guard and watch capture fire backwards onto the street.
    """
    return (head_diff > 0).to(head_diff.dtype)


def _regime_q(
    is_weir: torch.Tensor,
    weir_head: torch.Tensor,
    orifice_head: torch.Tensor,
    length_m: torch.Tensor,
    opening_area_m2: torch.Tensor,
) -> torch.Tensor:
    weir_safe = torch.clamp(weir_head, min=HF_FLOOR_M)
    orifice_safe = torch.clamp(orifice_head, min=HF_FLOOR_M)
    q_weir = WEIR_COEFF_CW * length_m * weir_safe.pow(1.5)
    q_orifice = ORIFICE_COEFF_CD * opening_area_m2 * torch.sqrt(2.0 * GRAVITY_M_S2 * orifice_safe)
    return torch.where(is_weir, q_weir, q_orifice)


def _apply_capture_cap(hydraulic_rate_depth: torch.Tensor, cap_depth: torch.Tensor) -> torch.Tensor:
    """``min(hydraulic rate depth, stability cap)`` — the pinned
    ``per_step_capture_cap`` via ``torch.minimum``. Module-level so the V5 red
    demos can sever the gradient with ``.detach()`` or bypass the cap and
    watch the observables move."""

    return torch.minimum(hydraulic_rate_depth, cap_depth)


def _apply_return_cap(q_ret_dt: torch.Tensor, cap_vol: torch.Tensor) -> torch.Tensor:

    return torch.minimum(q_ret_dt, cap_vol)


def couple_step(
    h: torch.Tensor,
    qx: torch.Tensor,
    qy: torch.Tensor,
    static: StaticFields,
    node_state: NodeState,
    graph: DrainGraph,
    dt: float,
) -> CoupleResult:
    dt_f = _validated_dt(dt)

    if not isinstance(h, torch.Tensor) or h.dtype is not torch.float32 or h.dim() != 2:
        raise ValueError(
            "[exchange] h must be a 2-D float32 tensor, got "
            f"{type(h).__name__}/{getattr(h, 'dtype', None)}"
        )
    _require_cpu(h, "h")
    if bool((h < 0).any()):

        raise ValueError(
            "[exchange] h contains negative depths — out of contract: the host mass "
            "update guarantees h >= 0 before coupling; fix upstream, do not couple"
        )
    nmap = graph.node_id_map
    if tuple(h.shape) != tuple(nmap.shape):
        raise ValueError(
            f"[exchange] h shape {tuple(h.shape)} != graph node_id_map shape {tuple(nmap.shape)}"
        )
    if tuple(static.drain_cap_m_s.shape) != tuple(h.shape):
        raise ValueError(
            f"[exchange] static.drain_cap_m_s shape {tuple(static.drain_cap_m_s.shape)} "
            f"!= h shape {tuple(h.shape)}"
        )
    if not isinstance(node_state, NodeState):
        raise ValueError("[exchange] node_state must be a coupling.exchange.NodeState")
    if tuple(node_state.h_node_m.shape) != (graph.num_nodes,):
        raise ValueError(
            f"[exchange] node_state head shape {tuple(node_state.h_node_m.shape)} "
            f"!= ({graph.num_nodes},)"
        )
    if (
        node_state.vol_in_m3_cum.dtype is not torch.float64
        or node_state.vol_out_m3_cum.dtype is not torch.float64
    ):
        raise ValueError("[exchange] node_state volume books must be float64")
    for _book_name in ("h_node_m", "vol_in_m3_cum", "vol_out_m3_cum"):
        _book = getattr(node_state, _book_name)
        if not bool(torch.isfinite(_book).all()):

            _n_bad = int((~torch.isfinite(_book)).sum())
            raise ValueError(
                f"[exchange] node_state.{_book_name} contains {_n_bad} non-finite value(s) "
                "(NaN/Inf) — refusing to exchange on broken books; fix upstream, do not "
                "couple (silent-NaN-manifest incident class)"
            )

    if not bool((static.drain_cap_m_s == 0).all()):
        n_bad = int((static.drain_cap_m_s != 0).sum())
        raise ValueError(
            f"[exchange] guard (a): static.drain_cap_m_s is not zeroed ({n_bad} non-zero "
            "cells); zero it OUT OF PLACE via zero_drain_cap_out_of_place() before coupled "
            "stepping — a live legacy sink here double-counts drainage against capture"
        )

    qx_enter, qy_enter = qx.clone(), qy.clone()

    dev = h.device
    n = graph.num_nodes
    pa_safe = torch.clamp(graph.node_plan_area_m2, min=torch.finfo(torch.float64).tiny)

    mapped = nmap >= 0
    owner = (nmap[mapped] - 1).to(torch.int64)
    counts64 = graph.node_cell_count.to(torch.float64)[owner]
    if bool((counts64 < 1.0).any()):

        raise RuntimeError("[exchange] an allocated cell's node reports zero owned cells")

    h_cells64 = h[mapped].to(torch.float64)
    h_eff = torch.clamp(h_cells64, min=0.0)
    width_cell = graph.width_mean_m[owner]
    h_node_cell = node_state.h_node_m.to(torch.float64)[owner]
    head_diff = h_eff - h_node_cell

    is_weir = h_eff < REGIME_SWITCH_M
    q_regime = _regime_q(
        is_weir=is_weir,
        weir_head=h_eff,
        orifice_head=head_diff,
        length_m=width_cell,
        opening_area_m2=width_cell * A_OPEN_WIDTH_FRACTION,
    )
    q_applied = q_regime * _no_reverse_gate(head_diff)

    hydraulic_rate_depth = q_applied * dt_f / CELL_AREA_M2
    cap_depth = CAPTURE_CAP_FRACTION * torch.clamp(h_cells64 - HF_FLOOR_M, min=0.0)
    captured64 = _apply_capture_cap(hydraulic_rate_depth, cap_depth)
    capture_cap_bound = bool((hydraulic_rate_depth > cap_depth).any())

    capture_at_node = torch.index_add(
        torch.zeros(n, dtype=torch.float64, device=dev),
        0,
        owner,
        captured64 * CELL_AREA_M2,
    )

    vol_prev = node_state.h_node_m.to(torch.float64) * pa_safe
    vol_after_capture = vol_prev + capture_at_node

    vol_routed, q_edge, _route_diag = route(vol_after_capture, graph, dt_f)
    transfer = q_edge * dt_f
    edge_in_vol = torch.index_add(
        torch.zeros(n, dtype=torch.float64, device=dev), 0, graph.edge_to, transfer
    )
    edge_out_vol = torch.index_add(
        torch.zeros(n, dtype=torch.float64, device=dev), 0, graph.edge_from, transfer
    )

    h_node_pre = vol_routed / pa_safe
    h_excess = h_node_pre - ASSUMED_FREEBOARD_M
    ret_active = (h_excess > 0).to(torch.float64)
    is_weir_ret = h_excess < REGIME_SWITCH_M
    q_ret = (
        _regime_q(
            is_weir=is_weir_ret,
            weir_head=h_excess,
            orifice_head=h_excess,
            length_m=graph.width_mean_m,
            opening_area_m2=graph.width_mean_m * A_OPEN_WIDTH_FRACTION,
        )
        * ret_active
    )
    node_available_vol = torch.clamp(
        vol_routed - ASSUMED_FREEBOARD_M * graph.node_plan_area_m2, min=0.0
    )
    r_cap_vol = RETURN_CAP_FRACTION * node_available_vol
    r_node = _apply_return_cap(q_ret * dt_f, r_cap_vol)
    return_cap_bound = bool((q_ret * dt_f > r_cap_vol).any())
    surcharging_nodes = int((r_node > 0).sum().item())

    vol_final = vol_routed - r_node

    recon = vol_after_capture + edge_in_vol - edge_out_vol - r_node
    tol = 1e-9 * torch.clamp(vol_routed.abs(), min=1.0)
    worst = float(((recon - vol_final).abs() / tol).max().item()) if n > 0 else 0.0
    if worst > 1.0:
        raise RuntimeError(
            f"[exchange] node continuity violated: max |recon - routed|/tol = {worst:.3e} "
            "(capture/route/return bookkeeping disagrees with the router's own state)"
        )

    h_node_new = (vol_final / pa_safe).to(torch.float32)
    node_state_new = NodeState(
        h_node_m=h_node_new,
        vol_in_m3_cum=node_state.vol_in_m3_cum + capture_at_node + edge_in_vol,
        vol_out_m3_cum=node_state.vol_out_m3_cum + edge_out_vol + r_node,
    )

    captured_full = torch.zeros_like(h).masked_scatter(mapped, captured64.to(torch.float32))
    returned_inc = (r_node[owner] / (counts64 * CELL_AREA_M2)).to(torch.float32)
    returned_full = torch.zeros_like(h).masked_scatter(mapped, returned_inc)
    h_new = h - captured_full + returned_full

    with torch.no_grad():
        if bool((h_new < 0).any()):

            neg_rows, neg_cols = (h_new < 0).nonzero(as_tuple=True)
            r, c = int(neg_rows[0]), int(neg_cols[0])
            raise RuntimeError(
                f"[exchange] negative surface depth after coupling at {(r, c)}: "
                f"{float(h_new[r, c]):.6e} m — the capture cap invariant is "
                "broken; refusing to hand negative depths to the solver"
            )

    if not torch.equal(qx, qx_enter) or not torch.equal(qy, qy_enter):
        raise RuntimeError(
            "[exchange] qx/qy were modified inside couple_step — in-place write on an "
            "input (Phase-2 _boundary_outflux incident class); refusing"
        )

    capture_m3 = float(captured_full.to(torch.float64).sum().item()) * CELL_AREA_M2
    return_m3 = float(returned_full.to(torch.float64).sum().item()) * CELL_AREA_M2
    return CoupleResult(
        h_new=h_new,
        node_state_new=node_state_new,
        captured_depth_m=captured_full.detach(),
        returned_depth_m=returned_full.detach(),
        capture_m3=capture_m3,
        return_m3=return_m3,
        surcharging_nodes=surcharging_nodes,
        max_node_head_m=float(h_node_pre.max().item()),
        cap_binding_this_step=int(capture_cap_bound or return_cap_bound),
        edge_flow_m3s=q_edge.to(torch.float32).detach(),
    )


@app.command()
def selfcheck(
    steps: int = typer.Option(12, help="Coupled exchange steps to run"),
    dt: float = typer.Option(0.48, help="Host timestep [s]"),
) -> None:
    torch.manual_seed(11)
    edges = [(1, 2, 8.0), (2, 3, None)]
    num_nodes = 3
    edge_from = torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64)
    edge_to = torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64)
    cb = torch.tensor([e[2] is not None for e in edges], dtype=torch.bool)
    q_cap = torch.where(
        cb,
        torch.tensor([0.0 if e[2] is None else float(e[2]) for e in edges], dtype=torch.float64),
        torch.full((len(edges),), float("nan"), dtype=torch.float64),
    )
    widths = torch.tensor([50.0, 50.0, 2.285], dtype=torch.float64)
    g = build_drain_graph(
        edge_from=edge_from,
        edge_to=edge_to,
        capacity_bearing=cb,
        q_cap_nom_m3s=q_cap,
        width_mean_m=widths,
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(10.0, 0.0, num_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(num_nodes, dtype=torch.float64),
        outfall_node=torch.tensor([False, False, True]),
        node_cell_row=torch.arange(num_nodes, dtype=torch.int32),
        node_cell_col=torch.arange(num_nodes, dtype=torch.int32),
        node_id_map=torch.tensor([[1, -1], [2, 3]], dtype=torch.int32),
        node_cell_count=torch.tensor([1, 1, 1], dtype=torch.int64),
    )
    static = zero_drain_cap_out_of_place(_cli_static((2, 2)))
    state = build_node_state(g)
    h = torch.full((2, 2), 0.5, dtype=torch.float32)
    h = h.clone()
    h[0, 0] = 1.25
    cum_cap = cum_ret = 0.0
    for s in range(steps):
        cr = couple_step(h, torch.zeros(2, 1), torch.zeros(1, 2), static, state, g, dt)
        h, state = cr.h_new, cr.node_state_new
        cum_cap += cr.capture_m3
        cum_ret += cr.return_m3
        print(
            f"step {s:02d}: captured={cr.capture_m3:.6f} m3 returned={cr.return_m3:.6f} m3 "
            f"surcharging={cr.surcharging_nodes} cap_bound={cr.cap_binding_this_step} "
            f"max_head={cr.max_node_head_m:.4f} m min_h={float(h.min()):.6f} m"
        )
    net = cum_cap - cum_ret
    print(f"totals: captured={cum_cap:.6f} m3 returned={cum_ret:.6f} m3 net={net:.6f} m3")


def _cli_static(shape: tuple[int, int]) -> StaticFields:
    hh, ww = shape

    def z32(*s):
        return torch.zeros(*s, dtype=torch.float32)

    return StaticFields(
        dz_x=z32(hh, ww - 1),
        dz_y=z32(hh - 1, ww),
        n_x=z32(hh, ww - 1),
        n_y=z32(hh - 1, ww),
        c_x=z32(hh, ww - 1),
        c_y=z32(hh - 1, ww),
        drain_cap_m_s=z32(hh, ww),
        infil_rate_m_s=z32(hh, ww),
        edge_w_n=z32(hh),
        edge_e_n=z32(hh),
        edge_n_n=z32(ww),
        edge_s_n=z32(ww),
        edge_w_s=z32(hh),
        edge_e_s=z32(hh),
        edge_n_s=z32(ww),
        edge_s_s=z32(ww),
        shape=shape,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
