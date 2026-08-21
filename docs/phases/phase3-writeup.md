# Phase 3 — the September 2022 validation gate

Status as of **2026-08-18**: the gate has **run**, and its verdict is **not adjudicable**. This is
not the same as failing, and the distinction is the whole content of this document.

Companion documents: [`../OPEN-ITEMS.md`](../OPEN-ITEMS.md) items AA–AO plus the post-audit
corrections W1–W11; [`phase2-writeup.md`](phase2-writeup.md) for what preceded it; raw agent
reports in [`logs/`](logs/).

---

## 1. What Phase 3 is

Per [`../SPEC.md`](../SPEC.md) §8, Phase 3 is a hard go/no-go: replay the September 2022 flood,
score against BBMP flood-prone locations, Sentinel-1 observed extent, and geolocated ground-truth
points. If no defensible city-wide match is achievable, the instruction is to stop and change the
project.

**Phase 3 is one event.** Phase 4 (§9) is the hundreds-of-scenarios phase. This was misremembered
at least once and it matters, because "validate before you scale" (CLAUDE.md rule 4) puts the
expensive work strictly after this gate.

---

## 2. What ran

A 48-hour simulation, 4–5 September 2022, on the conditioned 10 m canonical grid
(3,421 × 3,515 = 12,024,815 cells), forced by GPM IMERG v07 half-hourly granules.

| | |
|---|---|
| Steps | 166,588 |
| Wall clock | 14,270.7 s (3.96 h) on the RTX 4060 |
| Max realized Courant | 0.759 (ceiling 0.85) |
| Mass relative residual | 5.545 × 10⁻⁵ |
| Areal mean rainfall | 87.09 mm (104,719,128.5 m³) |
| IMERG native cells over domain | 16 |

The solver behaved. Mass conserved, Courant respected, no instability. **Nothing in what follows is
a solver defect.**

---

## 3. What was measured, and what it turned out to measure

### 3.1 Extent — closed as physically unmeasurable

The gate scored a Sentinel-1 extent CSI of 0.0705, corrected to 0.0322 by change detection, then
withdrawn by audit, and now **closed**: C-band VV SAR cannot measure this event here.

Twenty-five classifiers across six families were evaluated against an independent known-water set —
129,474 lake-interior cells from OSM `natural=water` polygons eroded 30 m inward, against 130,883
known-dry ridge cells. The falsifier was registered before the test: *a classifier reaching >70% POD
on Varthur and Bellandur while holding <1% false-positive rate on dry land.* **Nothing passed.**

| classifier | POD on known water | dry-land FPR |
|---|---|---|
| −16 dB cut (used in the gate) | 0.4421 | 0.0020 |
| −8 dB cut | 0.8384 | **0.1901** |
| Global Otsu (−4.29 dB) | 0.9818 | **0.4614** |
| change detection (−16 dB) | **0.1461** | 0.0016 |
| texture Cv ≤ 0.35 & σ⁰ ≤ −10 dB | 0.0802 | 0.0083 |

Per-lake recall of the −16 dB cut: Varthur 0.3487 (σ⁰ median −11.22 dB), Bellandur 0.3787 (−13.43),
Madiwala 0.3172, against Yelahanka's clear water at 0.8030 (−17.95). **Mechanism** *[reading]*: dense
water hyacinth and surfactant froth on the southeastern lakes produce volume and double-bounce
scattering, destroying the specular return that C-band water detection relies on. The 12 August
pre-image carries the same mats, which is why change detection *lowers* recall rather than raising it.

This is not a threshold problem. Extent cannot be adjudicated for this event in southeastern
Bengaluru — where the flooding was — without a different instrument. VH cross-polarisation is the
obvious next axis and the product is not on disk.

**One consequence that changes the earlier reading of the model.** The reference detects 161,756 wet
cells at a measured recall of 0.4421, implying a true wet area near **365,900** cells against the
model's **369,371** — agreement within 1%, and 0.4421 is recall on the easiest possible target, so
the true area is a lower bound. **The claim that the model floods 4.2× more area than reality is
withdrawn.** Total flooded area may be approximately right; placement is the open question.

### 3.2 Depth — the deficit is real

This half reproduces and survives audit. Against the 24 ground-truth points, using
`depth_event_maximum.tif` through a local-max window (the most generous field available):

- RMSE 0.7505 m, MAE 0.6723 m against band midpoints (point-value 0.7834, SAR-instant 0.7796).
- 20 below band, 2 above, 1 within.
- **At the exact ground-truth cell: 15 of 24 points hold under 1 cm; 21 of 24 under 10 cm.**

Two caveats the audit imposed. The interpretation "placement failure, not magnitude" is
**overstated** — several local-max readings sit at 0.23–0.38 m against band lows of 0.35–0.85 m,
within ~1.5× rather than absent. And **GT_14 should never have been scored**: its `observed_date`
is 2022-08-30, a different flood.

### 3.3 The metric conflict — unresolved, needs Darshil

SPEC.md §8 specifies depth RMSE at ground-truth points. CLAUDE.md §Reporting numbers says report
per-road-segment status, **not** per-square-metre depth at 10 m. These genuinely conflict.

The reviewer resolved it unilaterally, disqualified the spec's metric, and commissioned a
segment-level rescore. That was an adjudication decision taken outside the reviewer's authority.
The substitute is also statistically null on the only depth-relevant label set: **GT segment lift
1.22×, Fisher p = 0.088**. And the segment null model is itself broken — observed BBMP segments are
1.97× longer than random ones, so a same-count draw is the wrong null; cell-weighted, BBMP lift is
**0.92×, below random**.

**Open for Darshil's decision.**

---

## 4. The forcing finding, which is the important one

Forcing was closed on rainfall **totals** (item AJ) and reopened on **intensity** (item AN), where
it was called unresolvable from local data. It was resolvable, and the evidence was already in the
repository.

`data/fetched_articles.json` — fetched 2026-08-17, never opened — records across ~20 independent
IMD-sourced outlets:

- **131.6 mm in the 24 h between the mornings of 4 and 5 September 2022**, the wettest September
  day since 2014.
- **"most of which fell in less than 12 hours on Sunday night."**
- Bengaluru Urban district **79.2 mm against a 4.5 mm normal — 1,660% excess**.
- KSNDMC station values at three of our own ground-truth locations: Bellandur 67.5 mm,
  Halanayakanahalli 74 mm, Varthur 83.5 mm per 24 h.

Independently corroborated by **131.0 mm over 21 h** in the synoptic AA1 record obtained later.

Against that, IMERG delivered **87.09 mm across the entire 48 h**, with a domain-wide peak
30-minute intensity of **21.40 mm/hr** and no half-hour anywhere reaching 25 mm/hr.

**The nuance that must not be lost.** At the southeastern flood points IMERG's totals
(77–79 mm/48 h) sit in the same range as KSNDMC's stations there (67.5–83.5 mm/24 h). The 131.6 mm
is the city observatory, further north. So bulk volume is roughly right *where the flooding was*,
and the under-read is in **timing and peak concentration** — the model received as sustained rain
what fell as a night-long burst. Urban flash flooding is an intensity phenomenon.

---

## 5. Root cause: not established

"Terrain representation plus absent drainage" was the reviewer's leading candidate. It rests on
evidence touching at most 6 of 24 points, and 8 of 24 are lake-overflow or stormwater mechanisms
per the ground-truth CSV's own `notes` field. Three larger mechanisms were never examined:

1. ~~**The simulation starts bone dry, with empty lakes.**~~ **TESTED AND DEAD.** The hypothesis was
   that an empty-lake start (`validation/event_replay.py:335`, `h = torch.zeros(...)`) prevented the
   lake-overflow flooding the corpus describes. Its falsifier was registered — *if the basins
   upstream of the dry points never fill, a full start changes nothing there* — and it failed at all
   four traced points. Bellandur Lake **did** fill to its spill elevation (873.21 m, max WSE 874.85 m,
   100% of storage used) and spilled; the model already *over*-predicts there. GT_15, GT_06 and GT_05
   have no lake upstream at all. Decisively: **total unused basin storage at end of run is 5.62 M m³,
   5.37% of the storm.** The basins filled on their own. This killed a 4-hour GPU run before it was
   authorised, which is what the falsifier-before-verdict rule exists for.
2. **The drain prior removed 28.04% of the storm** in the gate run — an explicitly
   `assumed_uncalibrated` parameter. (The ~7.5% figure that circulated is from the 6 h design-storm
   run, not this one.)
3. **The free-outflow boundary took 39.30%.**

Together ~67% of the water is removed before it can pond. The reviewer's "water does not arrive"
conclusion was **inferred arithmetically and never traced** along a flow path; the water budget is
the simpler explanation and it was available.

**R4 is now satisfied — the mechanism is traced.** The first attempt was invalid (a 62-cell
catchment for Bellandur's ~150 km²). Rebuilt behind a validation gate that ran *before* any budget:
conservation exact to 0 cells of 12,728,415, zero monotonicity violations over 3.1 M steps,
Bellandur outlet catchment **158.00 km²** against a published ~148 and Varthur **269.43 km²**
against ~279 (Ramachandra et al. 2017, ENVIS TR 116), lake polygons 95–97% contained. The old
artefact is explained: naive steepest-descent tracing truncates at the first residual D4 pit.

| point | catchment | drain removed | traced verdict |
|---|---|---|---|
| GT_17 Bellandur Kodi | 158.00 km² | 28.04% | arrives, passes through — lake fills to spill, peak lags rain **+25 h**, model *over*-predicts |
| GT_15 Hebbal Underpass | **15 cells** | 18.22% | arrives, passes through — sheds laterally off a planar embankment; no sag to pond in |
| GT_06 Panathur Underpass | **33 cells** | **92.71%** | arrives and is drained — DEM dip 9.3 cm against a 1.20–1.45 m flood |
| GT_05 Silk Board | **1 cell** | **100%** | never arrives — the DEM puts this junction on a ridge crest |

**Two separable mechanisms.** At three of four points the DEM gives the location almost no
contributing area — 15, 33 and 1 cells — because it renders underpasses and junctions as planar or
elevated. On top of that the uncalibrated drain prior removes 92.71% and 100% of the direct rain
that does fall. Neither forcing nor calibration reaches either. Silk Board Junction, one of the
city's most notorious flood points, is a **one-cell ridge crest** in our terrain.

Caveats on this round: the reported "budget residual 0.000 — closed: true" is **vacuous** — routed
outflow is computed as `rain − drain − storage`, so closure cannot fail; the printed UTM coordinates
are off by ~3,080 m although the indexing is correct (bed elevations match to three decimals); some
narrative text contradicts its own tables; and the evidence ledger reports zero KSNDMC records when
the corpus contains the KSNDMC station values for Bellandur, Halanayakanahalli and Varthur.

Unused basin storage is corrected to **5.71%** of the storm (per-class clamping); the empty-lake
verdict is unaffected.

What *is* solidly established about terrain:What *is* solidly established about terrain: 0 of 24 ground-truth points is the lowest cell in its
neighbourhood, and goal 15 measured 2,534 tagged below-grade segments of which 39.5% show ≤ 10 cm
of dip and 54.1% show the flyover deck rather than the road beneath. That is a real limitation. It
is not yet shown to be *the* limitation.

---

## 6. What is genuinely gained

- **BBMP rajakaluve network acquired** — 6,839 features, 1,988.47 km, Public Domain via OpenCity,
  2.33× the OSM network. Drain representation rebuilt on it (`338a955`), OSM rasters preserved
  alongside. No capacities invented.
- **A register of 2,534 locations where the DEM is known to be unrepresentative**, with zero
  invented depths. Defensible as-is.
- **Rule 5 stops recorded** on BWSSB, desilting data, and (item AO) every elevation source
  attempted — no obtainable product resolves underpass lower-road profiles.
- **OSM has exactly one culvert feature** in all 717 km². Measured, not assumed.
- The solver, the mass budget, the checkpointing and the conditioning all held up.

---

## 7. Corrected order of work

Measure before rebuilding. The terrain and drain rebuild that already landed was sequenced on a
conclusion that no longer stands — not wrong work, wrongly ordered.

0. ~~Redo the catchment tracing.~~ **DONE — mechanism traced (§5).** Two causes, separable: near-zero
   contributing area at underpasses and junctions, plus a drain prior removing 92–100% of direct rain.
1. **Sub-daily intensity** — re-attack item 1b. The corpus figure (131.6 mm, most in under 12 h) is
   the anchor; the synoptic 131.0 mm/21 h corroborates it.
2. ~~Validate or replace the SAR classifier.~~ **DONE — closed as physically unmeasurable (§3.1).**
   Reopening requires a VH product, not more thresholds.
3. ~~Decide the antecedent state.~~ **DONE — hypothesis dead (§5).** Basins filled on their own;
   unused storage is 5.37% of the storm.
4. **Then judge terrain and drainage**, once step 0 gives a valid trace.

---

## 8. On the review process itself

An independent adversarial audit ([`logs/07-independent-audit-of-reviewer.md`](../../logs/07-independent-audit-of-reviewer.md))
found that the reviewer's **measurements mostly reproduced exactly** while its **inferences broke**
— on the same wrong-dimension pattern it was catching in implementation agents. Five reviewer
figures did not reproduce, produced in throwaway scripts that were never saved, while agents were
being held to rule 3 and rule 6.

The concrete lesson, now a standing rule: **the reviewer's own measurements are subject to rules 3
and 6 exactly as agent work is.** Anything that lands in `OPEN-ITEMS.md` as a number needs a saved
script and a logged run behind it.

The audit is the mechanism CLAUDE.md already describes — independence is the thing that works, and
it is not reproducible from inside the context that formed the conclusions, including by trying
harder.

---

## 9. Report index

| file | what it is |
|---|---|
| [`logs/01-phase3-gate-uncalibrated-baseline.md`](../../logs/01-phase3-gate-uncalibrated-baseline.md) | first end-to-end gate run, uncalibrated baseline |
| [`logs/02-terrain-residual-accounting-and-fill.md`](../../logs/02-terrain-residual-accounting-and-fill.md) | residual accounting, Invariant R, fill characterisation |
| [`logs/03-per-road-segment-rescore.md`](../../logs/03-per-road-segment-rescore.md) | per-road-segment rescore with null models |
| [`logs/04-drain-diagnosis-and-underpass-register.md`](../../logs/04-drain-diagnosis-and-underpass-register.md) | drain diagnosis, underpass register, data availability |
| [`logs/05-rajakaluve-drain-rebuild.md`](../../logs/05-rajakaluve-drain-rebuild.md) | drain rebuild on the BBMP rajakaluve network |
| [`logs/08-sar-water-classifier-investigation.md`](../../logs/08-sar-water-classifier-investigation.md) | 25-classifier SAR investigation; extent closed as unmeasurable |
| [`logs/09-water-tracing-v1-invalid.md`](../../logs/09-water-tracing-v1-invalid.md) | catchment budgets (Part 1 invalid), empty-lake falsifier, evidence ledger |
| [`logs/10-water-tracing-v2-validated.md`](../../logs/10-water-tracing-v2-validated.md) | validated delineation, closed budgets, traced mechanism |
| [`water-tracing-report.md`](../reference/water-tracing-report.md) | water-tracing detail |
| [`logs/06-measurement-corrections-and-trunk-signal.md`](../../logs/06-measurement-corrections-and-trunk-signal.md) | measurement-defect corrections, trunk-road signal |
| [`logs/07-independent-audit-of-reviewer.md`](../../logs/07-independent-audit-of-reviewer.md) | independent audit of the reviewer's reasoning |
| [`elevation-data-audit-2026-08-18.md`](../reference/elevation-data-audit-2026-08-18.md) | elevation source availability (item AO) |
