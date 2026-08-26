# WF-3 audit reconciliation — 2026-08-25

This log records deviations and corrective work from the independent scheduled-vs-realized WF-3
audit. It does not change the signed G1-G5 gate or reclassify blocked external inputs.

## Authorized realized baseline

Replay #2 at `p3-integration/runs/goal_d_replay2_final_config/` is admitted only as
**UNCOUPLED BASELINE**. Its completed source manifest records commit `b96dc975`, 10.5759795237075
realized GPU-hours, and canonical-grid `depth_event_maximum.tif`, `depth_final.tif`, and
`depth_sar_instant_20220905_004028Z.tif`. None of those artifacts is a coupled drain-graph result.
Every WF-3 consumer must expose the exact label **UNCOUPLED BASELINE** and must not imply surcharge,
backflow, or a live nowcast.

The N-3 nowcast axis remains blocked. No historical rainfall, climatology, or cached observation is
substituted for a live DWR/nowcast source.

## Cross-workflow deviation retained and flagged to WF-2

**DEVIATION — KEEP, WF-2 notification required.** WF-3 added 124 lines and removed 3 lines in
`src/jaladhar/forcing/interface.py`, which is outside WF-3's original ownership. The change adds
nowcast-specific fail-closed errors and producer/consumer assertions, parameterises interval-depth
conversion, and changes `ForcingMode(str, Enum)` to `ForcingMode(StrEnum)`.

The independent audit verified the enum change is behaviorally safe in the realized repository:
`.mode()` is a return type on four adapters and no consumer stringifies the enum into a manifest.
The change is retained; WF-2 must account for the shared-interface delta before integrating its
forcing consumer.

## Corrective checks

- A routing-service regression exercises a connected graph whose only path is entirely above the
  cited vehicle limit and requires `status=disconnected`, `reason=no_flood_safe_route`, and
  `route=null`.
- The provenance parser preserves the first character of the first porcelain path.
- The segment lifecycle mutation skips with the dirty-tree reason when provenance would stop it
  before the deliberate post-start failure; it still executes in a clean tree.
- T5 uses a precomputed product derived from Replay #2 and never launches a solver or GPU process.

## Fixer-1 completion — 2026-08-25

The five Sol-xhigh audit findings (product/scorer/provenance; nowcast seam; routing;
dashboard/demo/geometry; documentation) were repaired and re-verified from a clean source
snapshot. Deviations and dispositions:

### Expanded cross-workflow deviation (WF-2 notification updated)

`src/jaladhar/forcing/interface.py` is now **218 lines added / 3 removed** relative to `master`
(previously recorded as 124/3). The growth is Fixer 1's mandated tightening of
`assert_nowcast_metadata_contract`, the named `assert_rate_conversion_parameterised` consumer
seam, and the `interval_depth_mm_to_rate_m_s` cadence parameterisation. The `StrEnum` change and
fail-closed error hierarchy are retained. WF-2 must account for the full 218/3 delta.

### Deterministic repairs realized

- **Admission binding (A1):** `validate_uncoupled_baseline_admission` binds the admission to BOTH
  the source manifest SHA and the realized event-max raster bytes, and reads the event window from
  the manifest's realized top-level `event_window` dict (`{start, end}`); a nested `event` dict is
  preferred for forward-compatible producers, falling back to that top-level block. (The produced
  product manifests themselves carry the flat `event_window_start_utc`/`_end_utc` fields.)
- **Producer time semantics (A2):** new product runs stamp `issue_time_utc` from actual producer
  start/write time; source valid/event time retained separately. v2 is preserved unmodified.
- **Temporal honesty (A3):** fresh manifests carry `forcing_kind=historical_replay`,
  `temporal_aggregation=event_maximum`, exact event window, exact `UNCOUPLED BASELINE` label.
- **G1 terminology (A4):** report exposes `fixed_denominator_points`,
  `coordinate_realized_points`, `within_snap_modeled_points`, `outside_grid_fixed_misses`,
  `out_of_snap_fixed_misses`; the misleading raw-row label is gone.
- **Fresh superseding pair (A5):** produced only from a temporary clean mirror at mirror-only
  commit `25dafa625fca910795444e88e5605e28ca37a49f`; objects imported into this repository's
  object database via bundle fetch with **no branch/ref/history mutation** (ref count unchanged,
  FETCH_HEAD only). Runs: `runs/wf3_replay2_uncoupled_baseline_v5` and
  `runs/wf3_replay2_gates_v5`. v2/v4 and all failed attempts are byte-preserved.
- **Nowcast seam (B):** native-cell sentinel `-1` with one real district native cell; aggregated
  startup gate fires before any fetch; bare in-memory `raw_response` refused as unprovenanced;
  byte-bound source envelopes (path+SHA+fetch time+HTTP status+cache decision+poll ledger+
  courtesy interval+freshness) required for any production event. N-3 stays BLOCKED; CLI exits 2
  before source access and creates no rainfall.
- **Routing (C):** fabricated `routing_lead_seconds` removed (`null` +
  `unavailable_no_realized_arrival_time_series`); owner-configured server snap ceiling enforced
  (absent cap → refusal; caller escalation → rejection); vehicle policy fail-closed unchanged;
  product label/temporal mode propagated; artifact vs scientific vs policy vs graph-capability vs
  operational readiness separated; runtime software provenance exposed including dirty paths.
- **Dashboard/demo (D):** spread-based extrema removed (V8 RangeError class at 176k features);
  bounded geometry preview (server-refused beyond 2,000 features); lead zero rendered as
  "Rainfall lead 0", never "Now"; badge composes the exact honest wording
  `UNCOUPLED BASELINE — HISTORICAL EVENT MAXIMUM · product bytes loaded — gate not accepted`;
  default launcher supplies realized road geometry; rehearsal readiness window raised to a
  measured bound (~26 s twin validation).

### Measured gate state after completion

G1 measured FAIL on all 399 BBMP points: hits 182/399 = 0.45614035087719296; null (10,000 draws,
seed 42) mean rate 0.3233576441102757, 95% CI [0.2606516290726817, 0.39598997493734334]; lift
1.4106372902742759 — both signed conditions unmet. Signed G3 remains BLOCKED; the 0/16
(local-max 3×3) and 1/16 (exact-cell) point readings are retrospective diagnostics reproduced
exactly by the v5 scorer run. N-3, vehicle policy, coupling/surcharge (G2), G4, and G5 remain
BLOCKED on external inputs or owner decisions.

