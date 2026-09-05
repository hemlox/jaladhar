"""WF-2 coupling ledger — NEW mass accumulators + indirect-CFL monitor (spec §4.4, §10).

Everything the coupled run accumulates BEYOND the legacy :class:`~jaladhar.solver.mass.MassBudget`
lives here. ``mass.py`` is never modified (owner decision, spec §2): the host budget is driven
exactly as ``run.py:158-159`` drives it and sees ``drained_m == 0.0`` every step, so its
negative-drainage runtime guards (``mass.py:82-97``) cannot observe a signed net and cannot
false-trip. Its residual is REPORTED verbatim (it shows in-flight node storage as apparent
loss — expected, §10.2); it is never the judged number.

The JUDGED quantity is the total-water reconciliation residual (§10.3):

    residual_total_water = [v_current + V_nodes(t)] - v_initial - rain_in
                           + legacy_drain_out_m3(=0) + infil_out_m3 + boundary_out_m3
                           - created_by_clamping_m3
    relative form        = |residual| / max(|rain_in|, |v_initial|, 1e-12)   hard bar 1e-4

reported beside the contract-form residual (§10.2) whose derivation silently assumes EMPTY node
storage — while water sits in the graph ``residual_contract ~= -V_nodes(t)``, so judging the raw
contract form against the realized 1.79e-5 bar would false-fail. The two reconcile identically:
``residual_contract + V_nodes(t) = residual_total_water + drain_out_net_m3`` up to the
reconciliation identity error below, because edge fluxes telescope globally (each edge subtracts
upstream, adds downstream) so ``delta(V_nodes) = captured - returned`` exactly in exact
arithmetic.

That reconciliation identity is checked here on every host mass-check cadence: the ledger's
cumulative volumes arrive as the CONTRACT scalars ``cr.capture_m3`` / ``cr.return_m3`` (computed
ONCE inside couple_step from the realized f32 fields — the host never re-multiplies), while
``V_nodes(t)`` comes from the f64 node books (``vol_in_m3_cum - vol_out_m3_cum``). Raster scatter
vs node bookkeeping — two different code paths (V2 non-mirror) — so agreement is evidence, and
disagreement beyond the f32-field rounding envelope means a dropped TERM (O(volume)), which the
tolerance is sized to catch loudly (:class:`CouplingMassBreach` halts the run — mass.py's breach
asymmetry: recoverable conditions are retried, broken schemes are stopped).

Guard (c) runtime teeth: every ``accumulate`` re-reads the HOST budget's ``drain_out`` mirror and
raises :class:`AntiDoubleCountError` unless it is within the f32-noise envelope. Coupling ON =>
legacy drain_out ~= 0 AND captured_to_drains_m3 > 0; a live legacy sink here double-counts drainage
against capture (the raster's degenerate case makes "capacity zero everywhere" unreachable, so
the zeroing must be asserted, never assumed).

Guard-(c) acceptance algebra (V6 record — chosen formulation and why, BugHunt round-1 item A):
the legacy drained term is ``sum(min(0*dt, h_new)) * cell_area`` (``mass.py`` accumulator over the
zeroed sink), which is SIGN-DEFINITE: a live double-counting sink can only move it POSITIVE,
while tolerated negative depths contribute NEGATIVE f32 dust. The refuter measured both arms on
production-shaped runs: genuine double-count ~ +0.819 m3 by step ~12k; conserving storm-then-dry
dust ~ -1.8e-6 m3/step of PURE accumulation (halt at step 12,471 with drain_out =
-0.021501892301856174 m3 against the frozen ``4 x eps32 x max(|v_current|,|rain_in|)`` bound of
2.150e-02 m3 — after rain stops the scale freezes while the dust keeps accruing linearly). The
acceptance bound is therefore ASYMMETRIC:

    positive side:  mirror >  4*eps32*scale                          -> AntiDoubleCountError
                    (unchanged strict static bound: positive drift is a real sink)
    negative side: -mirror >  4*eps32*scale + DUST_ALLOWANCE_SAFETY_FACTOR * dust_observed
                    where dust_observed accumulates every NEGATIVE per-step drift the run
                    itself exhibits (mirror_t - mirror_{t-1} < 0)

Rationale: f32 rounding cannot mint negative volume out of nothing — new dust in a step is
bounded by the rounding of that step's state, so the only honest way cumulative dust grows is
incrementally, and the run's own exhibited increments are the physical measurement of its dust
rate. IMPLEMENTED ORDER (round-2 regression fix, V6 record): the round-1 code updated
``dust_observed`` / ``dust_allowance`` BEFORE the negative check, which made that arm
algebraically dead (``-mirror_t <= D_t <= 2*D_t <= static + 2*D_t`` on every step) while this
docstring still promised a first-occurrence trip — the CODE was wrong, not the promise. Both
negative-side guards now read the STANDING allowance (the value as of the PREVIOUS accepted
step) BEFORE the current step's negative drift is absorbed, so a single-step negative jump
LARGER than the static bound plus any prior allowance trips on its first occurrence, and
gradual exhibited dust — whose increments fund the allowance only on LATER steps — never does.
The safety factor keeps the >=10x margin against a real signal: at the measured 12k-step point
the standing negative bound is ~0.0215 (static) + 2x0.0224 (allowance) = 0.066 m3, 10x below
the 0.819 m3 double-count, while 13k-step dust (~0.0234 m3) clears it ~2.9x.

Residual hole closed honestly: because the allowance is funded by the run's own increments, a
slow STEADY leak re-funds it within ~2 steps and would never trip the allowance arm. An
ABSOLUTE CAP therefore bounds the accepted negative mirror: ``-mirror >
max(static_bound, DUST_ABSORB_CAP_M3)`` trips regardless of exhibited dust. The cap is sized
against MEASURED dust magnitudes — largest realized cumulative min(0,h) dust 0.0234 m3 (13k-step
toy replay); production-slope extrapolation ~0.08 m3 over a full 3 h storm-then-dry window —
both >=10x below 1.0 m3, so genuine dust can never trip it; conversely any sustained NEGATIVE
leak steeper than ~13x the measured dust rate crosses it within one such window. Below the cap
a negative signal is DECLARED indistinguishable from accumulated f32 min(0,h_new) dust at city
scale; it is reported verbatim in the manifest, never halted on.

The mirror is stored and reported VERBATIM either way (never clamped): reporting -1e-9 as 0.0
would be a V1 violation. A NON-FINITE mirror cannot trip either arm (NaN compares False against
every tolerance), so it is marked at ``accumulate`` and HALTS at the next ``mass_check``
(r2-numerics fix); every numeric surface of :meth:`as_dict` renders non-finite values as loud
strings instead of literal NaN/Infinity tokens (invalid strict JSON).

Surcharge events (spec §11.1): a node is surcharging in a step iff its returned contribution
> 0 that step. Contiguous surcharging steps of one node form ONE event recorded as
``{node_id, first_step, last_step, total_returned_m3, max_head_m}`` — the EXACT G2 per-node
schema — plus the §10.4 continuity components (vol_in_during / vol_out_routed_during /
returned_during / delta_storage) the diagnostics unit's second ledger consumes. Per-node
returned volumes are reconstructed from ``returned_depth_m`` through the graph's node map
(detached, no_grad: pure bookkeeping). Event CSV serialization is the diagnostics unit's job;
this class only exposes the event list interface.

:class:`IndirectCflMonitor` implements §10.5 verbatim: no new CFL term, the host
:class:`~jaladhar.solver.timestep.TimestepController` is unchanged PROVIDED both 0.9 caps are
enforced; per step ``factor = sqrt(h_max/(h_max+dH))`` with ``dH = max(max(captured),
max(returned)))``, alarm when ``factor < 0.902`` (~ +23% h_max <=> -10% dt). Alarm != halt;
the halt is K1 and lives in the driver.

CPU-only throughout (V12); every tensor touch here is a detached f64 reduction under no_grad.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import torch

from jaladhar.coupling.exchange import CELL_AREA_M2, CoupleResult
from jaladhar.coupling.router import DrainGraph
from jaladhar.solver.mass import MassBudget

__all__ = [
    "AntiDoubleCountError",
    "CouplingMassBreach",
    "CouplingMassLedger",
    "DUST_ABSORB_CAP_M3",
    "DUST_ALLOWANCE_SAFETY_FACTOR",
    "IndirectCflMonitor",
]

# Reconciliation identity tolerance: a dropped term is O(volume); accumulated f32
# field-rounding on the contract scalars over a full run is ~1e-5 relative. Anything
# beyond 1e-3 relative is a bookkeeping defect, not rounding (V7: sized against the
# failure it exists to catch, not tightened until it lies about precision).
IDENTITY_TOLERANCE_RELATIVE: float = 1.0e-3

# Guard-c zero bound, same discipline as mass.py's own sink guards: the host mass update
# may leave cells at TOLERATED negative depths (acc.py's 4-ULP local guard), and
# ``min(0*dt, h_new)`` then contributes that f32 dust to the legacy drained term. The
# bound is therefore 4 x eps32 x scale, NOT bitwise zero — a real double-count leaks
# sink-rate x dt x domain volumes (many orders larger) and still trips loudly.
LEGACY_ZERO_BOUND_RELATIVE: float = 4.0 * float(torch.finfo(torch.float32).eps)

# Safety factor on the accumulated negative-dust allowance (guard c, negative side only —
# see the module docstring for the algebra and the measured slopes it is sized against:
# ~ -1.8e-6 m3/step exhibited dust vs +0.819 m3 genuine double-count at ~12k steps).
DUST_ALLOWANCE_SAFETY_FACTOR: float = 2.0

# Absolute cap on guard c's NEGATIVE side (round-2 fix r2-mass-1): the self-funded
# allowance alone cannot bound a slow steady leak (each increment re-funds 2x itself one
# step later), so the accepted negative mirror is additionally capped at
# max(static bound, DUST_ABSORB_CAP_M3) regardless of exhibited dust. Sized against
# MEASURED dust, never assumed: largest realized cumulative min(0,h) dust = 0.0234 m3
# (13k-step toy replay) and the production-slope extrapolation over a full 3 h
# storm-then-dry window (~43k steps x 1.8e-6 m3/step) ~= 0.08 m3 — both >=10x below 1.0,
# so real dust can never trip it, while any sustained NEGATIVE leak steeper than ~13x the
# measured dust rate crosses the cap within one such window. Below the cap a negative
# signal is DECLARED indistinguishable from accumulated f32 min(0,h_new) dust at city
# scale; the verbatim mirror remains in the manifest (config-free module constant).
DUST_ABSORB_CAP_M3: float = 1.0


class CouplingMassBreach(RuntimeError):
    """The judged total-water residual or the reconciliation identity breached its bar.

    Same action policy as ``mass.py``: a mass breach means the scheme is broken; halt,
    never continue-and-report (contrast the recoverable stale-dt retry in timestep.py).
    """


class AntiDoubleCountError(RuntimeError):
    """Guard (c): the legacy sink moved volume while coupling owned drainage."""


@dataclass
class IndirectCflMonitor:
    """§10.5 indirect-CFL monitor — record, never tune.

    ``factor = sqrt(h_max / (h_max + dH_couple))`` per step; ``min_factor`` and the count of
    steps with ``factor < alarm_factor`` land in the manifest. Alarm != halt.
    """

    alarm_factor: float = 0.902  # sqrt(1/1.23); config dt.indirect_cfl_alarm_factor parity
    min_factor: float = 1.0
    alarm_steps: int = 0
    steps_observed: int = 0
    factors: list[float] = field(default_factory=list)

    def observe(self, h_max: float, d_h_couple: float) -> float:
        """Record one step's coupling depth perturbation; returns the realized factor.

        ``dH <= 0`` (nothing exchanged) and ``h_max <= 0`` (dry domain) are factor 1.0 by
        definition — no perturbation of a CFL dt that does not exist. Non-finite inputs are
        refused: they mean upstream broke, not that CFL got better.
        """
        h = float(h_max)
        dh = float(d_h_couple)
        if not math.isfinite(h) or not math.isfinite(dh):
            raise ValueError(f"[cfl-monitor] non-finite observation h_max={h!r} dH={dh!r}")
        if dh <= 0.0 or h <= 0.0:
            factor = 1.0
        else:
            factor = math.sqrt(h / (h + dh))
        self.steps_observed += 1
        self.factors.append(factor)
        self.min_factor = min(self.min_factor, factor)
        if factor < self.alarm_factor:
            self.alarm_steps += 1
        return factor

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps_observed": self.steps_observed,
            "min_factor": self.min_factor,
            "alarm_factor": self.alarm_factor,
            "alarm_steps": self.alarm_steps,
            "note": "sqrt(h_max/(h_max+dH)); alarm ~ +23% h_max <=> -10% dt; alarm != halt",
        }


class CouplingMassLedger:
    """Cumulative f64 coupling accumulators + judged residual + surcharge events.

    Args:
        cell_area_m2: host cell area [m²] (parity-reported; exchange volumes arrive
            pre-multiplied and are NEVER re-multiplied here).
        host_budget: the legacy :class:`MassBudget` driven by the driver exactly as
            ``run.py`` drives it. Read lazily at report time for v/vol/rain/infil/
            boundary/clamping; its ``drain_out`` is mirrored and asserted 0.0 on every
            ``accumulate`` (guard c).
        graph: when provided, per-node surcharge events are tracked from
            ``returned_depth_m`` through the graph's node map; without it only aggregate
            counters are maintained.
        judged_bar_relative: hard bar on the total-water relative residual (§10.3).
        contract_tolerance: contract relative tolerance, reported beside, never the pass
            line (§10.3).
        identity_tolerance: relative tolerance on the captured-minus-returned-vs-books
            reconciliation identity (module docstring carries the sizing rationale).
    """

    def __init__(
        self,
        cell_area_m2: float,
        host_budget: MassBudget,
        graph: DrainGraph | None = None,
        *,
        judged_bar_relative: float = 1.0e-4,
        contract_tolerance: float = 1.0e-3,
        identity_tolerance: float = IDENTITY_TOLERANCE_RELATIVE,
    ) -> None:
        self.cell_area_m2 = float(cell_area_m2)
        self.host_budget = host_budget
        self.graph = graph
        self.judged_bar_relative = float(judged_bar_relative)
        self.contract_tolerance = float(contract_tolerance)
        self.identity_tolerance = float(identity_tolerance)

        # --- cumulative f64 accumulators (spec §10.1) ---------------------------
        self.captured_to_drains_m3: float = 0.0
        self.surcharge_returned_m3: float = 0.0
        self.cap_binding_steps: int = 0
        self.steps: int = 0
        self.total_surcharging_steps: int = 0
        self.legacy_drain_out_m3: float = 0.0  # host mirror, VERBATIM (never clamped)

        # Guard-c negative-dust accounting (module docstring algebra): previous mirror
        # reading, cumulative exhibited dust, and its allowance. The baseline is the host
        # budget's mirror AT CONSTRUCTION — before any coupled step — so pre-existing
        # dust is grandfathered as baseline, never misread as a per-step drift.
        self._prev_mirror: float | None = float(self.host_budget.drain_out)
        self.dust_observed_m3: float = 0.0
        self.dust_allowance_m3: float = 0.0
        # r2-numerics: a NaN mirror compares False against every guard-c tolerance, so it
        # must be MARKED here and turned into a loud breach at the next mass_check.
        self._mirror_nonfinite: bool = False

        # --- derived state -------------------------------------------------------
        self._v_nodes_m3: float = 0.0
        self._identity_worst_rel: float = 0.0
        self._prev_books: tuple[torch.Tensor, torch.Tensor] | None = None
        self.history: list[dict[str, float]] = []
        # Event list interface (consumed by the diagnostics unit; §11 schema fields).
        self.events: list[dict[str, Any]] = []
        self._open_events: dict[int, dict[str, Any]] = {}

        if graph is not None:
            nmap = graph.node_id_map
            mapped = nmap >= 0
            self._map_mask = mapped
            self._map_owner = (nmap[mapped] - 1).to(torch.int64)
            self._num_nodes = graph.num_nodes
            # Books start at REST (build_node_state zeros); step 1's deltas are therefore
            # the full books, and step 1 CAN surcharge — seed prev_books to zeros so the
            # first accepted step opens events like any other.
            self._prev_books = (
                torch.zeros(graph.num_nodes, dtype=torch.float64),
                torch.zeros(graph.num_nodes, dtype=torch.float64),
            )

    # -- accumulation ----------------------------------------------------------

    def _legacy_zero_bound_m3(self) -> float:
        """4 x eps32 x max(|v_current|, |rain_in|, 1e-12) — the f32-noise floor of the very
        state mass.py's own sink guards tolerate (see LEGACY_ZERO_BOUND_RELATIVE)."""
        scale = max(
            abs(float(self.host_budget.v_current)),
            abs(float(self.host_budget.rain_in)),
            1e-12,
        )
        return LEGACY_ZERO_BOUND_RELATIVE * scale

    def accumulate(self, cr: CoupleResult, dt: float) -> None:
        """Fold one accepted coupled step into the ledgers.

        Volumes come from the CONTRACT scalars (never re-multiplied); ``dt`` is accepted for
        signature parity with the frozen sketch and future rate reporting. Raises
        :class:`AntiDoubleCountError` when the mirrored HOST drainage leaves its asymmetric
        f32-noise envelope (module docstring algebra) — guard c's runtime teeth behind the
        driver-level zeroing. The mirror itself is always stored VERBATIM (never clamped):
        reporting -1e-9 as 0.0 would be a V1 violation. POSITIVE drift past the static bound
        is a live sink and trips immediately. NEGATIVE drainage is judged BEFORE this step's
        dust is absorbed, against (i) the static bound plus the STANDING allowance (value as
        of the previous accepted step) — so a single-step jump larger than that bound trips
        on first occurrence — and (ii) the absolute cap ``max(static_bound,
        DUST_ABSORB_CAP_M3)``, which bounds cumulative absorbed negative drainage where the
        self-funded allowance alone cannot (slow steady leaks). Only after both checks pass
        is the step's negative drift folded into ``dust_observed_m3`` / the allowance. A
        NON-FINITE mirror is recorded verbatim, marked, and breaches at the next
        ``mass_check`` (NaN compares False against every tolerance here — r2-numerics).
        """
        del dt  # frozen sketch signature; volumes are exact scalars on CoupleResult
        self.steps += 1
        self.captured_to_drains_m3 += float(cr.capture_m3)
        self.surcharge_returned_m3 += float(cr.return_m3)
        self.cap_binding_steps += int(cr.cap_binding_this_step)
        if cr.surcharging_nodes > 0:
            self.total_surcharging_steps += 1

        self.legacy_drain_out_m3 = float(self.host_budget.drain_out)

        static_bound = self._legacy_zero_bound_m3()
        if self.legacy_drain_out_m3 > static_bound:
            raise AntiDoubleCountError(
                "[ledger] guard (c): legacy MassBudget.drain_out is "
                f"{self.legacy_drain_out_m3!r} m3, beyond the f32-noise bound "
                f"{static_bound:.3e} m3, while coupling owns the sink — drainage would be "
                "double-counted against capture; refusing"
            )

        # Negative arms run BEFORE this step's dust is absorbed (round-2 fix r2-mass-1):
        # the round-1 order refunded the allowance with the very drift being judged, which
        # made -mirror_t <= D_t <= 2*D_t <= static + 2*D_t hold identically every step and
        # the arm structurally unable to fire.
        neg_bound = static_bound + self.dust_allowance_m3  # STANDING allowance only
        if -self.legacy_drain_out_m3 > neg_bound:
            raise AntiDoubleCountError(
                "[ledger] guard (c): legacy MassBudget.drain_out is "
                f"{self.legacy_drain_out_m3!r} m3, beyond the f32-noise bound "
                f"{static_bound:.3e} m3 PLUS the accumulated negative-dust allowance "
                f"{self.dust_allowance_m3:.3e} m3 (= {DUST_ALLOWANCE_SAFETY_FACTOR:g}x the "
                f"{self.dust_observed_m3:.3e} m3 of exhibited min(0,h) dust) — negative "
                "drainage of this magnitude is not rounding; refusing"
            )
        absorb_cap = max(static_bound, DUST_ABSORB_CAP_M3)
        if -self.legacy_drain_out_m3 > absorb_cap:
            raise AntiDoubleCountError(
                "[ledger] guard (c): legacy MassBudget.drain_out is "
                f"{self.legacy_drain_out_m3!r} m3, beyond the ABSOLUTE negative-absorb cap "
                f"{absorb_cap:.3e} m3 (= max(f32-noise bound {static_bound:.3e}, "
                f"DUST_ABSORB_CAP_M3={DUST_ABSORB_CAP_M3!r})): cumulative negative drainage "
                "this large cannot be exhibited f32 dust and outpaces any self-funded "
                "allowance — refusing"
            )

        if not math.isfinite(self.legacy_drain_out_m3):
            # NaN comparisons are always False: without this marker both arms above would
            # pass it silently forever and as_dict() would emit an invalid NaN literal.
            self._mirror_nonfinite = True
        elif self._prev_mirror is not None:
            drift = self.legacy_drain_out_m3 - self._prev_mirror
            if drift < 0.0:
                # Sign-definite min(0*dt, h_new) f32 dust (mass.py accumulator form over
                # the zeroed sink); track what THIS run actually exhibits — absorbed AFTER
                # the checks so it can never fund the judgement of its own step.
                self.dust_observed_m3 += -drift
                self.dust_allowance_m3 = DUST_ALLOWANCE_SAFETY_FACTOR * self.dust_observed_m3
        self._prev_mirror = self.legacy_drain_out_m3

        vin = cr.node_state_new.vol_in_m3_cum.detach()
        vout = cr.node_state_new.vol_out_m3_cum.detach()
        self._v_nodes_m3 = float((vin - vout).sum().item())

        # Reconciliation identity (V2 observable): contract scalars vs f64 books.
        net = self.drain_out_net_m3
        err = abs(net - self._v_nodes_m3)
        scale = max(abs(net), abs(self._v_nodes_m3), 1e-12)
        rel = err / scale
        if not math.isfinite(rel):
            # NaN/Inf books must surface as a BREACH at the next cadence check, never as
            # a NaN that silently compares False against every tolerance (item F).
            self._identity_worst_rel = math.inf
        elif rel > self._identity_worst_rel:
            self._identity_worst_rel = rel

        if self.graph is not None:
            self._track_events(cr, vin, vout)
        self._prev_books = (vin, vout)

    def _track_events(self, cr: CoupleResult, vin: torch.Tensor, vout: torch.Tensor) -> None:
        assert self._prev_books is not None
        prev_in, prev_out = self._prev_books
        d_in = (vin - prev_in).tolist()
        d_out = (vout - prev_out).tolist()
        heads = cr.node_state_new.h_node_m.detach().tolist()

        with torch.no_grad():
            ret_cells = cr.returned_depth_m.detach().to(torch.float64)[self._map_mask]
            per_node = torch.zeros(self._num_nodes, dtype=torch.float64)
            per_node = torch.index_add(per_node, 0, self._map_owner, ret_cells * CELL_AREA_M2)
        returned_by_node = per_node.tolist()

        step = self.steps
        for i, ret_vol in enumerate(returned_by_node):
            if ret_vol > 0.0:
                ev = self._open_events.get(i)
                if ev is None:
                    ev = {
                        "node_id": i + 1,  # DrainGraph index convention: id = index + 1
                        "first_step": step,
                        "last_step": step,
                        "total_returned_m3": 0.0,
                        "max_head_m": 0.0,
                        "vol_in_during_m3": 0.0,
                        "vol_out_routed_during_m3": 0.0,
                        "returned_during_m3": 0.0,
                        "delta_storage_m3": 0.0,
                    }
                    self._open_events[i] = ev
                ev["last_step"] = step
                ev["total_returned_m3"] += ret_vol
                ev["max_head_m"] = max(ev["max_head_m"], float(heads[i]))
                ev["returned_during_m3"] += ret_vol
                ev["vol_in_during_m3"] += float(d_in[i])
                # books' out lumps edge-outflow WITH return; routed-out is the remainder.
                ev["vol_out_routed_during_m3"] += float(d_out[i]) - ret_vol
                ev["delta_storage_m3"] += float(d_in[i]) - float(d_out[i])
            else:
                ev = self._open_events.pop(i, None)
                if ev is not None:
                    self.events.append(ev)

    def flush_open_events(self) -> list[dict[str, Any]]:
        """Close still-open events into :attr:`events` (report time). Idempotent."""
        for i in sorted(self._open_events):
            self.events.append(self._open_events.pop(i))
        return self.events

    # -- residuals (§10.2 contract form REPORTED, §10.3 total-water form JUDGED) --

    @property
    def drain_out_net_m3(self) -> float:
        """``captured - returned`` — MAY BE NEGATIVE: net return is physical (D-E)."""
        return self.captured_to_drains_m3 - self.surcharge_returned_m3

    def _relative_scale(self) -> float:
        """Contract relative scale ``max(|rain_in|, |v_initial|, 1e-12)``."""
        return max(abs(self.host_budget.rain_in), abs(self.host_budget.v_initial), 1e-12)

    def contract_residual_m3(self) -> float:
        """§10.2 closure form — REPORTED, derivation stated (V10), never judged.

        ``v_current - v_initial - rain_in + drain_out_net_m3 + infil_out + boundary_out
        - created_by_clamping``. While water sits in the graph this is ~ ``-V_nodes(t)``
        and is EXPECTED not to vanish; see the module docstring.
        """
        b = self.host_budget
        return (
            b.v_current
            - b.v_initial
            - b.rain_in
            + self.drain_out_net_m3
            + b.infil_out
            + b.boundary_out
            - b.created_by_clamping
        )

    def contract_residual(self) -> float:
        """Relative contract-form residual."""
        return abs(self.contract_residual_m3()) / self._relative_scale()

    def total_water_residual_m3(self) -> float:
        """§10.3 total-water reconciliation — THE JUDGED quantity.

        ``[v_current + V_nodes(t)] - v_initial - rain_in + legacy_drain_out_m3(=0)
        + infil_out + boundary_out - created_by_clamping``. Surface + node storage change
        must equal inputs minus outputs to round-off; in-flight volume cannot masquerade
        as net drainage because it is counted ON THE INVENTORY SIDE.
        """
        b = self.host_budget
        return (
            b.v_current
            + self._v_nodes_m3
            - b.v_initial
            - b.rain_in
            + self.legacy_drain_out_m3
            + b.infil_out
            + b.boundary_out
            - b.created_by_clamping
        )

    def total_water_relative_residual(self) -> float:
        """Relative total-water residual — judged against ``judged_bar_relative`` (1e-4)."""
        return abs(self.total_water_residual_m3()) / self._relative_scale()

    # -- cadence check -----------------------------------------------------------

    @staticmethod
    def _metric_jsonable(x: float) -> float | str:
        """JSON-safe metric: non-finite floats become loud strings instead of literal
        NaN/Infinity tokens (invalid strict JSON — silent-NaN-manifest incident class)."""
        return x if math.isfinite(x) else f"non-finite({x!r})"

    def mass_check(self, step: int) -> dict[str, float]:
        """Run at the host mass-check cadence; HALTS on breach (never continue-and-report).

        Checks BOTH structural invariants: the reconciliation identity (a dropped term is
        O(volume) and cannot hide inside rounding) and the judged total-water bar. A
        NON-FINITE residual is itself a breach: NaN compares False against every ``>``
        tolerance, so letting it through would silently green-light broken books.
        """
        tw = self.total_water_relative_residual()
        ident = self._identity_worst_rel
        row = {
            "step": float(step),
            "total_water_relative_residual": self._metric_jsonable(tw),
            "contract_relative_residual": self._metric_jsonable(self.contract_residual()),
            "v_nodes_t_m3": self._v_nodes_m3,
            "reconciliation_identity_rel": self._metric_jsonable(ident),
        }
        self.history.append(row)
        if self._mirror_nonfinite or not math.isfinite(self.legacy_drain_out_m3):
            # r2-numerics: named FIRST so the halt names the actual defect — a non-finite
            # mirror cannot trip either guard-c arm (NaN compares False) and poisons every
            # residual it touches.
            raise CouplingMassBreach(
                f"legacy drain_out mirror is NON-FINITE ({self.legacy_drain_out_m3!r}) at "
                f"step {step} — NaN/Inf entered the host budget's sink accumulator; NaN "
                "compares False against every tolerance (both guard-c arms included), so "
                "this is a HALT, not a number to report"
            )
        if not math.isfinite(ident):
            raise CouplingMassBreach(
                f"reconciliation identity residual is NON-FINITE ({ident!r}) at step {step} "
                "— NaN/Inf entered the node books; a NaN compares False against every "
                "tolerance so this is a HALT, not a number to report"
            )
        if not math.isfinite(tw):
            raise CouplingMassBreach(
                f"judged total-water relative residual is NON-FINITE ({tw!r}) at step "
                f"{step} — the budget contains NaN/Inf; halting rather than emitting a "
                "manifest whose gates silently pass on comparisons that are always False"
            )
        if ident > self.identity_tolerance:
            raise CouplingMassBreach(
                f"reconciliation identity |(captured-returned) - V_nodes| relative = "
                f"{self._identity_worst_rel:.3e} exceeded tolerance "
                f"{self.identity_tolerance:.3e} at step {step} — a coupling bookkeeping "
                "term has been dropped (O(volume)), this is not rounding"
            )
        if tw > self.judged_bar_relative:
            raise CouplingMassBreach(
                f"judged total-water relative residual {tw:.3e} exceeded the hard bar "
                f"{self.judged_bar_relative:.3e} at step {step}. Per-term ledger: "
                f"{self.as_dict()}"
            )
        return row

    # -- reporting ----------------------------------------------------------------

    def v_nodes_m3(self) -> float:
        """Node-storage inventory V_nodes(t) from the f64 books (reported beside both)."""
        return self._v_nodes_m3

    def as_dict(self) -> dict[str, Any]:
        """All manifest budget lines with the D-E separation explicit — NO alias."""
        self.flush_open_events()
        return {
            # D-E: these are DISTINCT reported quantities; the contract's
            # backward_compat_alias ("drain_out_m3 == drain_out_net_m3") is NOT applied.
            # r2-numerics: EVERY float metric surface renders via _metric_jsonable so a
            # non-finite value becomes a loud string, never a literal NaN/Infinity token
            # (invalid strict JSON on the early-halt terminal path).
            "captured_to_drains_m3": self._metric_jsonable(self.captured_to_drains_m3),
            "surcharge_returned_m3": self._metric_jsonable(self.surcharge_returned_m3),
            "drain_out_net_m3": self._metric_jsonable(self.drain_out_net_m3),
            "legacy_drain_out_m3": self._metric_jsonable(self.legacy_drain_out_m3),
            "cap_binding_steps": self.cap_binding_steps,
            "steps": self.steps,
            "total_surcharging_steps": self.total_surcharging_steps,
            "v_nodes_t_m3": self._metric_jsonable(self._v_nodes_m3),
            "residual_contract_m3": self._metric_jsonable(self.contract_residual_m3()),
            "relative_residual_contract": self._metric_jsonable(self.contract_residual()),
            "residual_total_water_m3": self._metric_jsonable(self.total_water_residual_m3()),
            "total_water_relative_residual": self._metric_jsonable(
                self.total_water_relative_residual()
            ),
            "judged_bar_relative": self.judged_bar_relative,
            "contract_relative_tolerance": self.contract_tolerance,
            "realized_reference_residual_note": (
                "replay#2 1.79e-05 reported beside per config budget.realized_reference_residual"
            ),
            "reconciliation_identity_worst_rel": self._metric_jsonable(self._identity_worst_rel),
            "identity_tolerance": self.identity_tolerance,
            # Guard-c negative-dust accounting (item A algebra; mirror stays verbatim above).
            "guard_c_negative_dust": {
                "observed_m3": self._metric_jsonable(self.dust_observed_m3),
                "allowance_m3": self._metric_jsonable(self.dust_allowance_m3),
                "safety_factor": DUST_ALLOWANCE_SAFETY_FACTOR,
                "static_bound_m3": self._metric_jsonable(self._legacy_zero_bound_m3()),
                "absorb_cap_m3": DUST_ABSORB_CAP_M3,
                "max_absorbed_negative_m3": self._metric_jsonable(
                    max(self._legacy_zero_bound_m3(), DUST_ABSORB_CAP_M3)
                ),
            },
            "surcharge_events_count": len(self.events),
            "cell_area_m2": self.cell_area_m2,
            "mass_check_history_tail": self.history[-5:],
        }
