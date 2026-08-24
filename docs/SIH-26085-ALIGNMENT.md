# SIH 26085 alignment — scope change, gap analysis, and the new gate

**Written 2026-08-24.** This document re-scopes JALADHAR from its self-authored problem statement
onto **SIH 26085 — Urban Flood Nowcasting System (Drainage and Rainfall Coupling)**, MoES /
NCMRWF, Software, Disaster Management.

It does three separate things, deliberately kept apart:

1. Records the **Phase 3 result as measured**, without softening it (§2).
2. States the **gap to 26085** with the two decisive data findings (§3, §4).
3. Pre-registers a **new gate** whose thresholds are signed before any new number exists (§6).

The temptation this document exists to resist is: *"new problem statement, so the old gate doesn't
apply — build the dashboard."* That is the strictness ratchet running backwards. The old gate is
not discarded; it is **discharged as measured** and replaced by one written against the new
deliverable under R5 and V5.

---

## 1. What 26085 asks for

> Design a high-resolution, real-time Urban Flood Nowcasting System (0–3 hour lead time) capable of
> predicting street-level inundation before it happens.

Required components, verbatim from the statement:

| # | Requirement | Status |
|---|---|---|
| R1 | Take high-resolution rainfall nowcasts (from Doppler Weather Radars) | **gap** — we use IMERG satellite |
| R2 | Instantly route that volume across a 2D surface terrain model | **have it** |
| R3 | Represent the stormwater drain network as a **directed graph** (nodes = manholes/inlets, edges = pipes/canals) | **gap — the centrepiece** |
| R4 | Calculate hydraulic capacity; predict where blockage/overcapacity causes **backflow onto streets** | **gap — physically absent** |
| R5 | Web-based GIS dashboard, street-by-street, depth in cm, 0–3 h forward window | **not started** |
| R6 | API interfacing with navigation maps for flood-safe routing | **not started** |

The statement's own framing —

> *knowing how much rain will fall does not automatically translate into knowing where the streets
> will flood*

— is the thesis JALADHAR was already built on. This is not an adjacent problem; it is the same one,
scoped by a different author.

**City.** The statement says "major Indian metros **like** Mumbai, Delhi, and Chennai" —
illustrative, not restrictive. Bengaluru is retained. Switching cities discards Phases 0–3 entirely
(terrain, forcing, ground truth, validation harness) for no gain in fit; this is the same reasoning
that rejected the Assam pivot on 2026-08-22.

---

## 2. Phase 3 result, recorded as measured

**Phase 3 did not pass.** This section is not revised, softened, or re-thresholded by the scope
change. Every figure below is manifest-backed.

### 2.1 The two replays

| | Replay #1 (`4b91363`) | Replay #2 (`b96dc97`) |
|---|---|---|
| Configuration | repaired stack, raw IMERG, dry start | KSNDMC-alert-anchored IMERG, storage-at-spill IC |
| BBMP points @ 0.10 m | 183/399 = **45.86%** | 239/399 = **59.90%** |
| Domain flooded | 7.4% | 14.2% |
| GT depth in band | **0/16** (all below) | **0/16** (15 below, 1 above) |
| Depth RMSE | 0.9764 m | 0.9232 m |
| GPU | 4.879 h, 161,347 steps | 10.576 h, 359,841 steps |
| Mass residual | 1.79e-05 | 1.79e-05 |

**59.90% is recorded as a fail on rate** — one hit short of 60.15%. No rounding argument is made.
Note also that the hit-rate gain was bought with area: null lift *fell* from 45.86/7.4 = 6.20× to
59.90/14.2 = 4.22×, so the real improvement is roughly a third of what +14.04 points suggests.

Depth bands are ~0.25 m wide. An RMSE of 0.92 m is not a near miss; the model is systematically
about a metre too shallow.

### 2.2 The calibration proved the parametric levers are dead

SPEC §8's calibration loop ran for the first time (`4c13f4f`, 18.61 GPU-h, 6 evaluations,
3 coordinates accepted). Loss is `0.5·(1−CSI) + 0.5·mean band-hinge`, no BBMP term — the points
axis is fully out-of-sample.

Loss decomposes exactly:

```
baseline  0.5(1 − 0.109632) + 0.5(3.232745) = 0.445184 + 1.616373 = 2.061557
θ*        0.5(1 − 0.109910) + 0.5(3.191088) = 0.445045 + 1.595544 = 2.040589
```

- Total improvement **0.020968**, of which the depth term supplied **99.34%**.
- The depth term is **1.616** of the baseline loss. Calibration moved it **1.3%**.
- Extrapolating each coordinate's measured gradient to its bound: dense_urban (−0.0397 × ln2) ≈ 0.027,
  drain (0.0239 × ln4) ≈ 0.033, vegetated_open ≈ 0.014, plus two unevaluated coordinates of similar
  order. **Every lever slammed to its physical bound buys ≈ 0.11 of loss against the 1.62 needed —
  under 10%.**

**This is the most valuable thing Goal D produced.** It converts "we think the deficit is
structural" into "we measured that it is." No Manning's-n or drain-capacity tuning closes a gap
this size.

### 2.3 The mechanism, traced

- **Lakes are encoded as flat plateaus in the source DEM.** Varthur: rim 859.72 m, interior p50
  861.00 m (1.28 m *above* rim), interior standard deviation **0.16 m**, fraction of interior below
  rim **0.000**. Bellandur: rim 871.20, p50 872.00, std 0.20 m, fraction below rim 0.000. A 16 cm
  interior std is an acquisition-time **water surface**, not a basin. Identical across raw source →
  pre-conditioning → conditioned stack, so conditioning is exonerated.
  Consequence: the fill → spill → overflow mechanism is structurally absent from this terrain.
- **Water does arrive; it just doesn't pond.** 92%/98% of the Bellandur/Varthur polygons go wet,
  event-max means 0.10/0.175 m, maxima 1.58/1.73 m — shallow throughflow across a plateau, never
  buffering.
- **The model was over-supplied and still fell short.** Anchored forcing delivered 351.13 mm at the
  Varthur cell against a 128 mm gauge alert (**2.74×**), with infiltration disabled, and 15/16
  points still read below band.

### 2.4 What remains unresolved from Phase 3

- **Extent is not closed and the instrument is the blocker.** The 0.11 CSI figure is scored against
  a SAR reference Goal A validated on **lakes** — open water, high contrast — used to judge urban
  street flooding. Per R2, that measures the instrument at least as much as the model. Do not quote
  CSI 0.11 as "the model is 7× off on extent."
- The scored calibrated run was launched and **killed at owner instruction**; its manifest is marked
  `status: killed` and no claim rests on it.

---

## 3. Finding: the drain network has geometry but no hydraulic capacity

Measured 2026-08-24 against `data/raw/bbmp_drains/` (BBMP rajakaluve 2022 — primary 163, secondary
870, tertiary ~5,800 features).

**Attributes present:** `OBJECTID`, `Length`, `SHAPE_Leng`, `Shape.STLength()`.
**Attributes absent:** width, depth, invert level, cross-section, material, gradient, node type.

Everything else in the KML is empty display boilerplate (`Name`, `description`, `icon` — all null
across all 1,033 primary+secondary features).

This is what the existing terrain manifest already flagged as `assumed_uncalibrated: true`.

**Consequence.** 26085's R4 — "calculate hydraulic capacity" — has **no measured source in the data
we hold**. Under rule 1 (no fabricated data) and rule 2 (no placeholder physics), cross-sections
cannot be invented. The legitimate path is a **documented, cited capacity assignment rule** keyed to
drain order and upstream catchment area, drawn from published design standards (CPHEEO manual), and
reported as a stated assumption with a sensitivity band — never as measured geometry. That is an
assumption with provenance, which is admissible; a number typed in because it looked right is not.

**Also to note:** `tertiary_drains_2022.kml` (24.7 MB) fails to parse — `ParseException: Unexpected
EOF parsing WKB`. Data-quality item, logged in OPEN-ITEMS.

---

## 4. Finding: the drain network is not topologically connected

Measured 2026-08-24 on primary + secondary (1,033 features, **767.3 km** total length, EPSG:32643):

| endpoint snap tolerance | nodes | edges | connected components | largest component |
|---|---|---|---|---|
| 0.5 m | 1,392 | 1,033 | **373** | 57 nodes (4.1%) |
| 2.0 m | 1,389 | 1,033 | 371 | 57 (4.1%) |
| 5.0 m | 1,389 | 1,033 | 371 | 57 (4.1%) |
| 10.0 m | 1,385 | 1,033 | **370** | 57 (4.1%) |

Raising the tolerance 20× removes only 3 components. **This is not a coordinate-precision problem —
the polylines genuinely do not share endpoints.** The dataset is drawn as ~370 independent reaches,
not as a network.

**Consequence.** 26085's R3 — "represent the network as a directed graph" — cannot be satisfied by
loading the shapefile. The reaches must be **stitched into a connected directed graph using the
conditioned DEM's flow direction**: route downstream from each component's outlet along the D8 path
until it meets another reach. The terrain stack already supplies exactly this (whitebox
`breach_depressions`, 0 pits, D8 verified at the seam), so the input exists.

This is tractable, not blocked — but it is real work and it must be counted.

---

## 5. Why the biggest gap is also the most likely fix

In replay #2, drains removed **79.16 Mm³ — 25.57% of the 309.56 Mm³ total inflow — one-way, with no
return path.** Full closed water balance (residual 5,255 m³ = 1.7e-05 of inflow):

| term | Mm³ | share of inflow |
|---|---|---|
| boundary outflow | 144.76 | 46.76% |
| Δstorage at t_end (the flood itself) | 85.64 | 27.67% |
| **drains (one-way sink)** | **79.16** | **25.57%** |
| infiltration | 0.00 | disabled |

The current drain model is a per-cell sink: water that enters leaves the domain permanently. In
reality that water surcharges back through manholes onto the street — **which is the flooding**, and
which is precisely the mechanism 26085 asks to be modelled.

Independent corroboration from the calibration: of three accepted coordinates, one was
**`drain −0.25`** — the optimiser moved to reduce drain capacity.

**Labelling this properly under R1.** The *finding* is that a quarter of all water leaves one-way and
that the optimiser reduced drain capacity. That surcharge modelling closes the depth gap is a
**reading**, not established. Honest counter-evidence: the drain gradient is small (0.0239), so
simply reducing capacity buys little. But capacity reduction leaves water on the surface *diffusely*,
whereas surcharge returns it *concentrated at nodes* — a different spatial signature that could
produce local depths diffuse water never will. The small gradient does not settle it either way.

**No goal may be premised on this reading.** It is the leading hypothesis and it gets tested, not
assumed.

---

## 6. The new gate — signed 2026-08-24, before any new number exists

SPEC §8's thresholds (CSI ≈ 0.73, RMSE ≈ 0.17 m) were written against a different deliverable and
are **retired, not failed-forward**. The following replaces them for 26085 work. Per R5 these are
written while the outcome is unknown, and per V5 each must be demonstrated capable of failing.

**G1 — Street-level classification (primary).** Per-road-segment flood/no-flood against BBMP
complaint points: **hit rate ≥ 60% with null-model lift ≥ 3.0×** (N = 10,000 spatial null,
seed recorded). Both conditions, no rounding. Lift is mandatory because hit rate alone is buyable by
flooding more area — as replay #2 demonstrated.

**G2 — Surcharge mechanism (the new physics).** The coupled drain graph must reproduce
**≥ 50% of ground-truth flood points that sit on or adjacent to a drain reach** as surcharge-driven,
with the surcharge volume traceable to a named node. A run where no node ever surcharges fails G2
regardless of its scores — this is the anti-vacuity condition, per V7 and the Phase 1 #15 sealed-outfall
precedent.

**G3 — Depth, honestly bounded.** Per-segment depth **bands in cm**, not point depths.
**≥ 40% of held-out strict rows in band**, reported under both the 3×3 local-max and exact-cell
conventions (declared in `data/curation/goal_d_depth_convention.json`); if the two disagree on the
verdict, **the disagreement is the reported result**.

**G4 — Nowcast latency.** End-to-end from rainfall input to published depth field for a 3-hour
horizon: **≤ 10 minutes wall clock** on the configured pool. Measured, not estimated.

**G5 — Extent.** Remains **unclosed** until an instrument is validated on *urban* flooding rather
than on lakes. No CSI figure is admissible as a gate number before then. This is a declared open
axis, not a silent omission.

**Mandatory alongside any verdict:** a V11 axis list naming what was measured and what was left open
on each variable, and a diagnosis section if any axis fails.

**What is NOT gated:** dashboard and API work may proceed in parallel with G1–G4 provided they render
only realized model state and hardcode no metric (rule 3). They are prohibited from shipping any
number the gate has not produced.

---

## 7. Work remaining

Reusable as-is: terrain conditioning, 2D ACC solver, forcing adapter interface, validation harness,
manifest/provenance machinery. That is the expensive part and it is done — roughly 60% of the system.

| Work | Effort | Risk |
|---|---|---|
| Stitch drain reaches into a connected directed graph (DEM flow-direction routing) | ~1 week | Medium — 370 components, method is standard |
| Capacity assignment rule from published design standards + sensitivity band | ~1 week | **Rule-1 sensitive** — must be cited, never invented |
| Surface ↔ drain-graph coupling with surcharge return | 2–3 weeks | **High — the actual research contribution** |
| DWR / nowcast ingestion + extrapolation | 1–2 weeks | **Blocked on data access** (OPEN-ITEMS) |
| Nowcast-speed path (subdomain tiling at 10 m, not coarsening) | ~1 week | Low |
| Web GIS dashboard | ~2 weeks | Low |
| Routing API (OSMnx road graph already in stack) | ~1 week | Low |
| Re-validate against G1–G4 | 1–2 weeks | Medium |

**6–10 weeks focused**, contingent on DWR access and the coupling behaving. The neural surrogate
(SPEC Phase 5) is now **optional** rather than prerequisite — see §8.

Sunk to date: ~34.1 GPU-hours across two replays and the calibration session. All of it produced the
diagnosis in §2, which is what tells us where the next hours go.

---

## 8. Two methodology corrections

**Do not coarsen the grid for speed.** The statement stresses that urban flooding is "a hyper-local
phenomenon dictated by micro-topography." At 20 m a street is one cell — coarsening spends exactly
the property being claimed. Get speed instead from (a) the nowcast window being 16× shorter than the
48-hour replay and (b) **subdomain tiling** — a nowcast needs the catchments under the active storm
cell, not all 1,202 km². That is minutes at full 10 m resolution.

**Read R1 carefully before committing to radar engineering.** The statement says the pipeline "takes
high-resolution rainfall nowcasts (**from** Doppler Weather Radars)" — the *nowcast* is the input and
DWR is its provenance. Consuming IMD's published nowcast product satisfies that reading and is far
cheaper than building radar nowcasting from volumetric reflectivity we may not be able to obtain.
Establish which is actually available before choosing.

---

## 9. Standing rules that still bind

Nothing in this re-scope relaxes CLAUDE.md. Specifically re-affirmed:

- **Rule 1 / Rule 2** apply directly to §3 — drain cross-sections are the next place fabricated data
  would be tempting, and the capacity rule must carry a citation.
- **Rule 5** applies to DWR access: blocked on an external unknown means stop and report, not work
  around silently.
- **V7** applies to G2: an assertion that cannot fail is not a check.
- **R1** applies to §5: the surcharge hypothesis is a reading and nothing may be sequenced on it.
- **The independent review gate at the end of every phase is unchanged.**

## 10. Owner decision required

CLAUDE.md's problem-statement block is marked **"(fixed, do not reword)"**. Under 26085 it genuinely
changes — a new statement from a new source is a replacement, not a rewording, so it is flagged here
rather than edited silently. Proposed replacement recorded in CLAUDE.md with the original preserved
beneath it. **Darshil to confirm.**
