# OPEN ITEMS — live ledger

**Last reconciled: 2026-08-22.** This file contains only live blockers, unresolved axes, and owner
decisions. Historical items and withdrawn claims remain in
[`archive/open-items-full-2026-08-18.md`](archive/open-items-full-2026-08-18.md).

## Phase 3 disposition — active hard gate, not passed

The engineering replay path is integrated and has completed a wet real-input GPU smoke. The
scientific gate has not yet produced the defensible city-wide match required by
[`SPEC.md`](SPEC.md) §8. Phases 4–8 remain unauthorized. Work is paused at the exact continuation
point described in `HANDOVER.md`; Luna review and history squashing are deliberately deferred.

## Blocking items

### P3-1 — Scientific closure sequence

**State: PAUSED, owner decisions required.** The Sentinel, telemetry/alert, GT-curation and
preflight investigations are terminal. Luna review and history squashing remain deliberately
deferred. No implementation agent may silently weaken any acceptance axis.

### P3-2 — KSNDMC event forcing

**State: DECISION REQUIRED; actual city-gauge forcing unavailable in the accepted source.** The
manifest-bearing NWDP acquisition accepted the 83,495,207-byte source CSV at SHA
`92261aee7f521937165fbe47eff40a339838e46819f56cf6861b7b687db0f0b3`. Its completed semantics audit
finds zero Bangalore Urban or Bangalore Rural rows in 2021–2022 despite the resource title. The
only event reporters within 60 km are Byramangala_1 (30.2 km) and Ramanagara_1 (42.7 km).

The separately acquired KSNDMC archive has 104 Bengaluru-tagged threshold alerts. Values including
Varthuru 128 mm, Marathahalli 129 mm, Cholanayakanahalli 135 mm and Tavarekere 135.5 mm establish
that the event occurred in the model's city cluster, but those messages are threshold-crossing lower
bounds rather than a complete interval field.

Darshil must choose: preserve IMERG and record actual KSNDMC city-gauge forcing as unavailable
(recommended), or explicitly authorize an experimental nearest-gauge IDW from the two remote
stations. The latter may not be reported as actual Bengaluru gauge forcing. Never manufacture a
sub-hourly storm from alerts, news, or totals.

### P3-3 — Underpass lower-road geometry

**State: BLOCKED externally (Rule 5).** The DEM does not resolve the lower road at most underpasses.
The measured GT_06 carve proves local sensitivity, but only a tiny fraction of the 2,534 registered
segments has usable measured clearance and clearance is not itself a lower-road elevation.

Closure requires surveyed/photogrammetric lower-road profiles with vertical datum and QA, or a
government/municipal source that supplies equivalent data. Open avenues remain SOI/Karnataka GDC,
KSNDMC/BBMP sensor history, BMRCL DPR ground profiles, and a defensible street-imagery survey.

### P3-4 — Valid city-wide extent reference

**State: CLOSED — `instrument_invalid`; external replacement required.** The pre-registered rule
required recall strictly above 70% at both Varthur and Bellandur, with dry false-positive rate
strictly below 1%. The completed dual-pol GRD stage at `e0eb63e` has no passing family: its best VV
candidate reaches 42.35%/49.61% target recall at 0.96% dry FPR. The completed HyP3
coherence/amplitude stage at `b21cb7a` also has no passing family; its best amplitude candidate
reaches 44.81%/56.52% at 0.91% dry FPR.

No Sentinel-derived city-wide CSI is admissible. Further threshold tuning is prohibited. Closure
would require a different independently validated full-footprint event reference, not a new score
against either failed instrument.

### P3-5 — Ground-truth target and metric authority

**State: CLOSED — strict-depth target shortfall.** The terminal curation audit has 32 total rows,
31 event-location eligible flood attestations, and only 15 strict-depth rows whose retained quote
supports the stored numerical band. The curation stage records `strict_depth_below_spec_target:
true`; 15 is below the 30–50 depth target.

The annotated replay input is `data/raw/groundtruth/sept2022_points_v2.csv`, frozen at SHA
`7b498e6b4eb43c17044835d0091f2a34eb4c7d89318ca70e116aa1af6c31a404`. The scorer fails closed for
unannotated inputs and excludes numeric legacy bands marked `UNSTATED` from depth RMSE. Any replay
RMSE therefore has a 15-row diagnostic denominator and cannot satisfy the depth-scope criterion.

### P3-6 — Fixed-stack 48-hour rerun budget

**State: PAUSED, owner budget decision required.** The completed clean-tree preflight at `b69f9f6`
records a 0.596 GPU-hour kernel floor and a 12.650 GPU-hour pessimistic end-to-end bound for one
48-hour scenario, above the configured 10-hour ceiling. The gate correctly refuses before
simulation. The branch CPU suite had 327 passed / 0 failed; Ruff and Black were clean over the 20
branch-owned files.

Closure is either a newly measured lower overhead/anchor that brings the full bound below budget,
or Darshil explicitly raising `configs/compute.yaml:budget.nightly_gpu_hours` to at least 12.650
(13 hours practical minimum). Do not override the refusal in code. A rerun alone cannot close P3-2
through P3-5.

## Open engineering debt, not sufficient to reverse the gate

| item | state | closure |
|---|---|---|
| Full-domain/full-duration skipped invariants | OPEN / V7 | run each named blocked node at its claimed domain and duration; do not count a skip as green |
| Legacy manifests without surviving producer commits | GRANDFATHERED DEBT | clean rerun or explicit supersession; never rewrite historical SHA fields |
| Repository-wide style debt outside Phase 3 scope | OPEN | repair separately; Phase 3-owned Python is Ruff/Black clean, while legacy files still contain style findings |
| Compute pool beyond the RTX 4060 | UNMEASURED | benchmark the exact kernel and end-to-end runner on each added device before configuring throughput |
| Surrogate scenario count | METHOD ONLY | remains a Phase 5 learning-curve question and is blocked by P3-1 |
| Forecast rainfall source | PARTIAL | required for operations, not a substitute for P3 event validation |
| Rotate exposed historical sudo password | OPEN / security | owner action outside source code |

## Closed engineering failures — do not reopen without a new red observable

- Retained-basin staircase carving: guarded; clean rebuild modifies 0 retained cells.
- Stale/tampered `filled.tif`: source and output identities validated; mismatch regenerates.
- Hardcoded depression intermediate path: configured path is passed explicitly.
- GT/water manifest collision: distinct, non-nested ownership enforced.
- Segment validation writing into solver run directories: split input/output ownership enforced.
- Post-start manifest casualty: repaired runners transition to completed or failed with end time.
- D8 terrain / D4 solver mismatch: producer-consumer seam asserted.
- Path-only terrain/drain provenance: exact producer raster byte/grid identity asserted.
- Movable/missing compute anchor: benchmark lifecycle and semantic row/hash checks enforced.
- Kernel estimate labelled “realized”: end-to-end wall-clock accounting and reconciliation enforced.
- Validation XML opened as raster: typed file identity implemented and mutation-tested.
- Forcing identity/tensor divergence: one interval-rate table is built and shared.
- Relative replay output paths: normalized once at the gate boundary.
- Analysis and segment test plumbing: current three-file suite executes with zero failures; remaining
  provenance skips are named debt.

These closures establish reproducible engineering. They do not claim observational agreement.
