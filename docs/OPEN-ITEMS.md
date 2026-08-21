# OPEN-ITEMS — live ledger

**Last reconciled: 2026-08-20.** This file holds only what is **still open**. Everything resolved,
falsified or superseded is in [`archive/open-items-full-2026-08-18.md`](archive/open-items-full-2026-08-18.md)
— 1,518 lines, including every withdrawn claim beside its correction. Do not delete it; commit
messages and `docs/` prose reference its item letters.

New to this project? Start at [`HANDOVER.md`](HANDOVER.md), not here.

Item letters are historical and non-contiguous. Two items were both labelled `AN`; they are
disambiguated below as **AN-intensity** and **AN-segment**.

**Status 2026-08-20** — independent review of `origin/codex/phase3-hardening` (`c541f5d`) complete:
`runs/codex_phase3_review/REPORT.md` (manifest `runs/codex_phase3_review/manifest.json`). **AB and AF:
CODE FIXED (verified)**. **L and M: closed in code, but status-table flips only** — the cited code
predates the branch. Clean-worktree reproduction: **156 passed / 88 skipped / 0 failed** (244 items;
claimed 157/89/0 reconciles with runs/ state). One test defect found (drains test boundary
precondition ungated — fails hard instead of BLOCKED-skip; fix merged with the branch).
**Data decision:** the friend's realized data is *not* being transferred; everything is re-fetched
and rebuilt locally with the now-hardened machinery (IMERG, Sentinel-1, OSM, DEM, `terrain.build`).
**The Phase 3 gate replay has NOT been run** — that is the critical path now.

---

## Where the project actually is

Phase 3 (the September 2022 validation gate) has **run**. Its verdict is **not adjudicable as
specified**, for reasons now measured rather than suspected:

- **Extent cannot be measured at all** with the instrument we have. C-band VV SAR fails on the
  southeastern lakes — 25 classifiers across 6 families tested against 129,474 known-water cells,
  none reached the pre-registered bar. Item **AA**, closed.
- **Depth is genuinely deficient**, and the cause is traced: at three of four traced ground-truth
  points the DEM gives the location **15, 33 and 1 contributing cells**. Even at zero drainage with
  perfect retention those catchments cap at 0.077–0.109 m against observed bands of 0.35–1.45 m.
- **The fix is blocked on terrain data that does not exist publicly.** Item **AO**, Rule 5 stop.
- **The model is not uniformly broken.** Where terrain is right it works: GT_17 Bellandur has a real
  158 km² catchment, fills to spill, and its depth peak lags rainfall by +25 h. It *over*-predicts
  there (1.65 m vs a 0.85–1.10 m band) — a calibration problem, not a capability one.

**The problem statement is fixed and submitted** — "street-level flood depths" — so narrowing the
claim is not available. The open engineering problem is how to deliver street-level depth given a
bare-earth DEM that cannot see underpasses.

---

## 🔴 Live and blocking

### 1b — KSNDMC historical records, 1–10 Sept 2022 — **BLOCKED, now the critical path**
Sub-daily rainfall for the event. Escalated from "accepted degradation" to blocker: the model was
forced with IMERG at a domain-wide peak 30-minute intensity of **21.40 mm/hr**, while the event
delivered **131.6 mm in 24 h with most of it inside 12 hours** (~20 IMD-sourced outlets in
`data/fetched_articles.json`; corroborated at 131.0 mm/21 h in the synoptic record). Urban flooding
is an intensity phenomenon. See **AN-intensity**.

### AO — No verified elevation source resolves Bengaluru underpasses — **BLOCKED (Rule 5); re-audited 2026-08-18**
No obtainable public product gives underpass lower-road profiles with datum, bridge treatment and QA.
Detail in [`reference/elevation-data-audit-2026-08-18.md`](reference/elevation-data-audit-2026-08-18.md).
**Rule 1 forbids inventing the sag depths.** This is the binding constraint on the whole project.

**Re-audit (2026-08-18, [`reference/underpass-sources-audit-2026-08-18.md`](reference/underpass-sources-audit-2026-08-18.md)):**
the "no source" conclusion survives for realized levels, but the block is now attackable from four named
paths. Measured this pass: OSM `maxheight` covers 0.2274% of highway ways (0 `maxheight:physical`;
0/2,534 register segments within 25 m; GT_06 Panathur = `maxheight 4` at 0.2 m — `data/raw/osm/maxheight_coverage.json`,
manifested). Bounded this pass: IRC:54-1974 §8 pins underpass clearance at 5.0 m / 5.5 m urban (realized
5.5 m in NHAI VUP contracts), BMRCL rail levels ≥7.50 m — a design band, not a level; register stays
unpopulated. New request paths: (1) SOI Karnataka GDC G2G 3 cm ORI + national 10 m DEM —
`karn.gdc.soi@gov.in` (needs government sponsor); (2) KSNDMC history of 124 BBMP water-level sensors —
draft request exists; (3) BMRCL Phase 3 DPR ground profiles via RTI; (4) NGM 25 cm urban DEM (announced).
**Needs Darshil:** which path to authorise.

**Update (2026-08-19, underpass carve goal): the mechanism the block was costing is now PROVEN.** The
underpass goal ran end-to-end with honest data: full Overpass requery of all 2,534 register segments
(`runs/underpass_clearance/manifest.json`) gives only **24 (0.9%)** with usable `maxheight`
(2,491 MISSING_TAG, 19 UNPARSEABLE); the 22 carved of 23 carve-eligible segments (measured clearance) carved into a
variant DEM reproduce GT_06 Panathur at **1.763 m** vs baseline 0.009 m against observed [1.20, 1.45]
(inside pre-registered flood window [1.20, 2.95]); leave-one-out 16/16 consistent (geometry rule —
implied dip ≥ band_low; solver flood test n=1 at GT_06); no side effects at
the other 23 GT points; deepest carved-cell pond 4.90 m, water surface 0.97 m below the deck (no overfill). So a *single measured
clearance* turns a 100x under-prediction into a pass. The open engineering problem is unchanged — the
other 2,510 underpasses need real levels — but the value of solving AO is now measured, not assumed:
a quantified acquisition spec in `runs/underpass_goal/REPORT.md`, and `scripts/run_underpass_goal_postanalysis.py`
is the reproducible measurement. Any of the four paths above, once it yields levels for the 2,510
unmeasured segments, feeds the existing carve pipeline directly.

### AN-intensity — Rainfall intensity is under-read and untested — **OPEN, critical path**
IMERG peak 21.40 mm/hr; nothing anywhere reached 25. At the southeastern flood points IMERG's
*totals* (77–79 mm/48 h) match KSNDMC's stations there (67.5–83.5 mm/24 h), so the under-read is in
**timing and peak concentration**, not bulk volume. NOAA/NCEI METAR for VOBL/VOBG carries no
quantitative rain and ISD-Lite hourly fields are absent at all four stations tried.

### AK — Ground-truth points are not sinks in the DEM — **OPEN, this is the finding**
0 of 24 points is the lowest cell in its neighbourhood. 2,534 tagged below-grade segments measured:
39.5% show ≤10 cm of dip, 54.1% show the flyover deck rather than the road beneath. Register at
`data/interim/terrain/unrepresentative_underpass_register.csv`, **zero invented depths**.

### AB — The gate run cannot be reproduced from its own manifest — **CODE FIXED (verified 2026-08-20)**
The old manifest defect (`git_sha 9032b55`, no status/start/end/budget/scored-domain) is fixed by the
`RunManifest` lifecycle in `src/jaladhar/provenance.py`: write-at-start, in-place update, dirty-tree
refusal, no-overwrite, immutable terminal states; `event_replay` resolver validates every config key
at startup. Verified realized by the independent review. **RERUN still BLOCKED**: no clean-commit
Phase 3 replay exists — that is the gate's critical path.

---

## 🟡 Live, not blocking

| item | what | state |
|---|---|---|
| **3** | CartoDEM availability/resolution/licence on Bhuvan | PARTIAL, reopened 2026-08-18 |
| **6** | Sentinel-1 GRD download | PARTIAL — re-fetch planned locally (2026-08-20 decision; friend's copy not transferred) |
| **7** | LISFLOOD-FP 8.1 build | BLOCKED, timeboxed and abandoned |
| **8** | Rainfall *forecast* source | PARTIAL — needed for the live product, not for the gate |
| **9** | UK EA 2D benchmark input data | PARTIAL |
| **C** | Surrogate training-scenario count | METHOD ONLY by design; settles in Phase 5 |
| **D** | Compute pool | first-pass estimate only |
| **L** | Non-negativity tolerance is global, not per-cell | CLOSED (verified 2026-08-20) — per-cell 4-ULP tolerance present in committed code; status-table flip only, not branch work |
| **M** | `track_cell_sinks` should be deleted, not defaulted | CLOSED (verified 2026-08-20) — absent from branch, merge-base and master; status-table flip only, not branch work |
| **O** | 22 of 51 solver invariants skipped — every one the full-domain/full-duration variant | OPEN (V7) — to be run with the Phase 3 replay (post-data) |
| **P** | `tiling.py` | NOT BUILT |
| **R** | Grid refinement study, per-class gradient counts | NOT BUILT |
| **T** | Are P99 = 0.99 m / P99.9 = 3.92 m too deep? | OPEN — Phase 3 was meant to settle it and could not |
| **AC** | BBMP hit rate has no null model; the FAR asked for was never computed | OPEN |
| **AE** | Conditioning fill is bimodal — 17,959 cells (1.80 km²) raised >2 m, 71.5% of volume | OPEN; the §6.1 deviation still understates it |
| **AF** | `residual_max_bound` applies the looser of two stated rules (95,463 vs 72,566) | CODE FIXED (verified 2026-08-20) — `enforce_residual_bound` uses `min(...)` of both bounds |
| **AH** | Six gate invariants, none demonstrated red; 22 skips reported as "100% green" | OPEN (V5, V7) |
| **AI** | Depth deficit real and understated — 15 of 24 points under 1 cm at the exact cell | OPEN |
| **AN-segment** | Per-road-segment rescore; null model broken (observed segments 1.97× longer than random; cell-weighted BBMP lift 0.92×, below random) | OPEN |
| **U** | Rotate the sudo password | OPEN (security) |

### The metric conflict — **needs Darshil, not an agent**
SPEC.md §8 specifies depth RMSE at ground-truth points. CLAUDE.md §Reporting numbers says report
per-road-segment status, **not** per-square-metre depth at 10 m. These genuinely conflict. A previous
reviewer resolved it unilaterally and substituted the segment metric — outside its authority, and the
substitute is statistically null on the only depth-relevant label set (GT segment lift 1.22×, Fisher
p = 0.088). **Unresolved.**

---

## Defects in the most recent round — small, real, unfixed

- **The catchment budget residual is vacuous.** `Net routed outflow` is computed as
  `rain − drain − storage`, so "Budget Residual: 0.000 — Closed: True" cannot fail for any input.
  Verified: 12,848,650 − 3,603,224.011 − 5,181,855 = 4,063,570.989, exactly the reported outflow. A
  real check needs outflow measured as flux across the catchment boundary.
- **Printed UTM coordinates are wrong by ~3,080 m** in `water_tracer.py`'s `[FINDING]` lines. The
  *indexing is correct* — bed elevations match true locations to three decimals — so this is a
  display bug. Fix it; a wrong coordinate beside a correct number is how a later reader gets misled.
- **Narrative text contradicts its own tables.** GT_15's prose reports "22.4% (24.4 m³)" against its
  table's 18.22% / 29.814 m³, with a depth in mm printed as a volume in m³.
- **The evidence ledger reports KSNDMC = 0 records** while the corpus contains KSNDMC station values
  for Bellandur (67.5 mm), Halanayakanahalli (74) and Varthur (83.5) — the most useful record in the
  file. Likely matching "Natural Disaster Management" and missing "Monitoring".
- **GT_17's catchment drain fraction is 28.043%**, matching the domain-wide 28.04% to four
  significant figures. Verify it is a local sum, not a domain mean applied to the catchment.
- **GT_14 should never have been scored** — its `observed_date` is 2022-08-30, a different flood.
  Snap distances of 140.9 m and 171.0 m (GT_14, GT_24) are indefensible against a 5.7 m median.

---

## Closed this cycle — do not reopen without new evidence

| item | outcome |
|---|---|
| **AA** | Extent is **physically unmeasurable** via C-band VV SAR. 25 classifiers, 6 families; the pre-registered falsifier (>70% POD on Varthur/Bellandur at <1% dry FPR) failed for all. Untested axis: VH cross-pol, product not on disk |
| **AJ** | Forcing **totals** are not the cause. Flood points get 84.1 mm = 77% of domain peak; GT_15 gets the domain maximum 109.1 mm and models at 0.0117 m |
| **AG** | Conditioning fill does **not** explain the depth deficit — all 24 points sit on cells with 0.0 m fill |
| **empty-lake start** | **Dead.** Bellandur filled to spill on its own; total unused basin storage at end of run is 5.71% of the storm, not the ~28% supposed |
| **drain prior** | **Not the binding constraint.** At zero drainage the traced catchments cap at 0.109 / 0.077 / 0.079 m against bands starting at 0.35 / 1.20 / 0.35 m |
| **W, X** | ACC-vs-ANUGA and the wet/dry front — root-caused as documented ACC limitations, not bugs |
| **AL, AM** | Underpass register built (0 invented depths); drainage audit complete; BBMP rajakaluve network acquired (1,988.47 km, Public Domain, 2.33× OSM) |

---

## Reading order for the withdrawn claims

Several confident conclusions were issued and later withdrawn. They are preserved with their
corrections in the archive because the pattern matters more than the instances — see
`docs/archive/open-items-full-2026-08-18.md`, corrections **W1–W21**, and the independent audit at
[`../logs/07-independent-audit-of-reviewer.md`](../logs/07-independent-audit-of-reviewer.md).
