# ADVERSARIAL AUDIT — the reviewer's Phase 3 reasoning
**Repo state audited:** commits through `0995218` (OPEN-ITEMS items AA–AN, goal12–15 as recorded). Two later commits (`338a955`, `338a955`) landed while this audit ran; they postdate the reviewed artifacts and are noted where relevant.
**Method:** every figure below was recomputed read-only from `runs/phase3_validation/`, `data/raw/`, `data/interim/`, `data/processed/` with `.venv/bin/python`. Scratch scripts in `/tmp/opencode/audit/`, not the repo.

---

## The two verdicts, first

**1. Does the reviewer's conclusion hold?** Split it. *"The gate fails"* — **yes in substance, but not as measured.** The depth deficit at 24 known flood locations is independently reproduced (I get the identical numbers: 15/24 < 1 cm, RMSE 0.7505), and nothing on disk supports a defensible city-wide match, so the no-go stands. But the evidence chain the reviewer built to support it is largely invalid: the extent half was scored against a reference that contains **0 of the project's own 24 ground-truth floods**, the depth half was disqualified by the reviewer's own (overreached) rule application, and the replacement segment metric is statistically indistinguishable from random on the only depth-relevant label set (GT: Fisher p = 0.088). *"The cause is terrain representation plus absent drainage"* — **not established.** The underpass mechanism directly touches ≤6 of 24 points by the project's own cross-reference; three larger, unexamined mechanisms (arbitrary drain prior removing 28.0% of the storm, free-outflow boundary removing 39.3%, dry-start lakes holding ~28% of storm volume in empty storage) and a forcing field that under-reads the real event per the project's own news corpus are at least as well supported. Verdict: **the fail conclusion holds; the causal diagnosis does not follow from the evidence.**

**2. The single most important thing nobody noticed.** The forcing question — the load-bearing closure — was resolved on the wrong axis, and the correct axis was measured and sitting in this repo the whole time. `data/fetched_articles.json` (Down To Earth, 5 Sept 2022, fetched by the project itself in Phase 0): *"The city received 131.6 mm of rainfall between the mornings of September 4 and September 5"* and *"Bengaluru Urban district received 79.2 mm during September 4 to 5."* The gate run's IMERG delivered 87.09 mm over the **entire 48 hours** (~46 mm on 4 Sept). The real peak-day total alone (131.6 mm) is ~3× IMERG's daily figure and ~1.5× IMERG's two-day total — and more rain fell on 5 Sept night. Item AJ's "killing case" (GT_15 receives IMERG's maximum 109.1 mm and still under-models, therefore forcing is exonerated) is an *internal-consistency* argument about IMERG's own structure; it never asked whether IMERG matches what actually fell. The project is now rebuilding the terrain and drain network (commits `338a955`, conditioning redesign as "highest-value action 1") on the strength of that closure. That is the wrong next move if the storm the model was given is half the real one — and the evidence for that is on disk, unreferenced by anyone, reviewer included.

---

# FINDINGS — ranked by what would change a decision

## F1. The "observed flood" reference does not contain the flood. Extent verdict: unmeasured, not failed.

**Claims attacked:** 1 ("gate fails on extent, corrected CSI 0.0322, band 0.0322–0.0705"), 2 ("the SAR reference was flattering the model"), exposure G (−16 dB never validated).

**What is wrong.** The reviewer tested exactly one failure mode of the −16 dB VV classifier: false positives from persistent smooth surfaces (item AA's change-detection work). It never tested whether the cut detects *real water* in these scenes — and the on-disk data says it catastrophically doesn't:

| check | realized |
|---|---|
| Lake cores in the flood scene (σ⁰ median / % ≤ −16 dB) — Hebbal / Varthur / Bellandur / Ulsoor | −5.9 / −7.6 / −10.6 / −9.5 dB; 10% / 6% / 30% / 27% flagged |
| Same lakes in the pre-scene | identically bright (−7.4 / −8.2 / −10.3 / −10.1 dB) — not flood-day wind |
| The 24 ground-truth flood points inside the change-detection mask | **0 / 24** (1/24 in the single-date mask) |
| The 398 in-domain BBMP flood-prone points inside the change-detection mask | **1 / 398** |

A water classifier that misses 70–94% of the city's own lakes — in *both* scenes — is not measuring water, and the reference built from it contains essentially none of the event the project independently verified. The reviewer's own AA table showed POD 0.2210 on permanent water and it dismissed that as polygon geometry ("could be correct"); the lake-core measurement discriminates and it was never run.

**Correct version.** The band [0.0322, 0.0705] does **not** bound the truth — the miss-rate over real water is unmeasured, so the corrected CSI could be an underestimate (real flood invisible to the mask → model hits on it counted as false alarms) by an unknown amount. The honest state of the extent half: **unadjudicable with this classifier**, pending a validated water classifier (dual-pol, coherence, a calibrated threshold per scene, or an independent inundation map — item 4, skipped). Even the reviewer's "survives regardless" fallback is wrong: the OPEN stratum, where it says SAR "actually works," is precisely where the classifier's smooth-surface false positives concentrate — **20,741 of the 20,764 TPs removed by change detection are in OPEN cells**, and only **302 of 34,994 single-date TPs are on road cells** (the "crediting the model for flooding roads" mechanism named in AA is ~1% of the effect; the removed credit is non-road smooth surfaces).

**Does it change the verdict?** Yes — it converts "extent fails, CSI 0.0322" into "extent unmeasured." The overall gate fail survives on the independent depth deficit (below), but the headline number of the entire Phase 3 review does not.

## F2. Forcing was closed on the wrong axis, and the correct axis contradicts the closure.

**Claims attacked:** 3 ("forcing totals are not the cause"), 4 ("intensity may be the whole story"), exposure D (second wrong-dimension instance).

**What is wrong.** Item AJ's resolution is airtight *against IMERG's own structure*: I reproduce GT totals min/mean/max 74.92/84.13/109.10 mm exactly, GT_15 = 109.10 = domain max exactly, peak intensity 21.40 mm/hr exactly, zero half-hours ≥ 25 exactly. But "the flood points sit at 77% of the domain peak, so forcing does not starve them" answers "is the IMERG field internally compressed?" — not "is the IMERG field right?" The project's own news corpus (`data/fetched_articles.json`, DTE 5 Sept 2022) records 131.6 mm in the 24 h ending 5 Sept morning and 79.2 mm district-wide for 4–5 Sept. IMERG gave 46.32 mm for 4 Sept and 87.09 mm for the whole 48 h. Real 48-h totals plausibly 150–200 mm against 87 mm delivered. Under that reality, GT_15 Hebbal was *not* the maximally-forced point — its true input was ~2–3× IMERG's 109.1 mm — and the AJ "killing case" collapses. Note the irony of the sequence: AJ's original draft *correctly* suspected this ("39.15 appears copied from an earlier window", "if the forced field is near-uniform where the event was peaked, the gate is unadjudicable on IMERG") — and the reviewer retracted the right flag and asserted the wrong answer.

**Correct version.** Forcing is not merely *possibly* wrong on intensity (item AN's open critical-path item) — it is *demonstrably* wrong on totals against the project's own corpus. AN's "unresolvable on disk" is too pessimistic: the daily-total evidence was on disk since Phase 0. The reviewer's own earlier framing should have been kept: the gate is unadjudicable on IMERG forcing, and re-attacking item 1b (gauge rainfall) outranks the terrain rebuild.

**Does it change the verdict?** The fail conclusion stands, but the *cause* attribution and the **next actions** change: "better rainfall is the wrong move" (AJ) is refuted; better rainfall is the first move.

## F3. Root cause (claim 10) is not established — and the model's own unvalidated water-removal mechanisms are larger than the named one.

**Claim attacked:** 10 ("root cause is terrain representation and the absent drain network").

**What is wrong.**
1. **Coverage.** Underpass co-location is 6 of 24 GT points (AL's own register); only 2 points are underpass-named in the GT CSV's own notes. 8 of 24 points are lake-overflow/stormwater-overflow mechanisms per the CSV's own notes (GT_02 Halanayakanahalli overflow, GT_03 Yemalur SW overflow, GT_08 Varthur runoff, GT_17 Bellandur weir, GT_18 Varthur weir, GT_21/GT_22 lake roads, GT_24 lake outlet). goal14's "misses concentrated entirely at underpasses, bridges and road sags" is contradicted by its own table (misses include Rainbow Drive Layout 1.2–1.45 m, Sai Layout, HAL Old Airport Rd, Munnekollal — residential sites) and by AL's own 6/24 count.
2. **The alternative mechanisms the reviewer never put in the causal chain, all from the run's own realized budget** (`runs/phase3_validation/segment_validation_report.json`, `phase3_gate_closing_water_budget`): drain sink removes **28.04%** of the storm — but the drain prior is *explicitly arbitrary* (`configs/domain_bengaluru.yaml`: baseline 10 mm/hr, `assumed_uncalibrated: true`, uniform across landuse, exponential decay 200 m); boundary outfall removes **39.30%** — a free-outflow edge of an arbitrary bbox rectangle; the simulation starts **completely dry** (`event_replay.py:335`, `h = torch.zeros`) so lakes hold ~29.8 M m³ of empty storage ≈ 28% of the delivered storm, in an event whose antecedent reality was a flooded week (29–30 Aug flood, 71.2 mm vs 2.4 mm normal — same DTE article) with lakes full and overflowing. Together, 67.3% of the delivered rain is removed before it can pond, and ~28% of the rest can hide in empty lakes. Every one of these is a candidate for "water does not arrive," and none is a terrain-representation failure.
3. The reviewer's own highest-value-action list contains the dry-start fact ("starting dry genuinely removes up to ~21.8% of the storm") yet the causal conclusion never mentions antecedent state.

**Correct version.** "Terrain representation + absent drainage" is one candidate among at least four, covering a minority of the observations directly. The supported statement is: the gate run cannot adjudicate the model because its own synthetic removal terms (drain prior, boundary, antecedent state) and its forcing are unvalidated.

**Does it change the verdict?** The "cause" half of the conclusion — yes, from "resolved" to "not established."

## F4. The segment-level rescore's null model is broken; its headline lifts are inflated ~2×, and the GT result is not statistically significant. The 5.17→1.76–1.91 comparison is confounded.

**Claim attacked:** 8 ("segment-level rescoring buys nothing — lift falls from 5.17× to 1.76–1.91×"), exposure F (lift comparability).

**What is wrong.**
1. **Length-biased null.** The harness draws observed segments *uniformly* (`segment_validation.py:459`, hypergeometric). But label points land on cells, and observed segments are systematically long: BBMP observed segments average **28.8 cells vs 14.6 domain-wide (1.97×)**; GT 21.2 (1.45×). Under a cell-weighted null (the one that matches the generating process), expected TP for BBMP is **130.0, not 63.9** — the reported "lift 1.88×" becomes **0.92×: the model is below random**. GT lift 1.76× becomes 1.22×.
2. **The GT result is noise.** The report's own exact tests give Fisher p = **0.0877** (binom 0.0877); the achieved CSI 0.000250 sits inside the null's 95% CI. "1.76× lift" over 24 points is not a measured skill level.
3. **The cross-metric comparison fails.** Lift is bounded by 1/(predicted prevalence): cell-level ceiling 31.6× (K/N = 3.16%) vs segment-level ceilings 6.06× (16.5%) and 9.57× (10.5%). The reviewer's argument ("lift = TP/E[TP], so the comparison holds") ignores that the two lifts live on different scales. By fraction-of-achievable-lift, segment BBMP = 31% vs cell 16% — the metric did not "lose skill." The claim "skill drops from 5.17× to under 2.0×" is largely a mechanical consequence of the predicted set growing from 3.16% of cells to 16.5% of segments — the effect the reviewer's own sensitivity sweep documents (lift 1.63→2.68 as predicted fraction falls 32.8%→7.7%).
4. **Internal contradiction with claim 9.** The report JSON itself marks BBMP and GT `csi_interpretable: false` (positives-only lists), yet goal14.md's headline table and item AN report "Achieved CSI 0.004256 / 0.000250" and CSI-lift for exactly those sets.

**Correct version.** On a matched null, the segment product is *worse* than reported (BBMP 0.92×), GT is non-significant — so the qualitative verdict ("no defensible segment product, no calibration fix") happens to survive, but the quantitative narrative (1.76–1.91× vs 5.17×) is built on a broken null and an invalid cross-metric comparison. The priming expectation — "if segment-level lift is not materially above 5.17×, the metric change bought nothing" — embedded a threshold the metric could barely reach by construction (max 6.06× on BBMP/GT).

**Does it change the verdict?** No (direction survives), but it changes the stated evidence and exposes the reviewer reporting uninterpretable metrics it had just ruled out.

## F5. Provenance (exposure A): the process violation is real — and it is *not* harmless. Five figures don't reproduce; one claim of "measured, not copied" is false.

**What is wrong.** Rule 3/6 apply to the reviewer's own measurements; none were saved as scripts or manifests. The user's suspicion is confirmed at the process level. But the accuracy bet is better than feared: **most** load-bearing numbers reproduce exactly (ledger below). The failures are concentrated and instructive:

| claimed (location) | realized (my recomputation) |
|---|---|
| "The domain is covered by **25 IMERG cells**, storm totals **54.0–109.1**" (AJ) | 16 native cells — canonical *and* buffered (`imerg_native_cell_ids.tif`; AJ itself says 16 two paragraphs later). Min total 64.85 mm. The 25/54.0 reproduce only from a 5×5 superset window offset from the domain (offset (0,−1): min 54.03) |
| "4 Sept 46.32, 5 Sept 39.15 — **measured, not copied**" (AJ) | Reproduce exactly from the **Phase-0 9-cell CSV** (`bengaluru_window_2022-08-28_2022-09-10.csv`). From the 96 granules over the run's 16-cell domain: 50.67/35.11. The retracted suspicion was right; the retraction was wrong |
| "Max 15-min intensity 38.0 mm/hr; only 3 of 283 gauges exceeded 21.40; none exceeded 50" (AN) | Realized: 13.5 mm/15 min = **54.0 mm/hr** (MAGADI, 16 Aug 2026); 5 gauges > 21.4; one > 50. The reviewer measured the second-largest increment and understated the record's storminess |
| "0 of 24 is the lowest cell **within 110 m**; median 1.09 m vs random 1.73 m" (AK) | Reproduces only as an **11×11-cell square (±55 m half-width)**: 1.11/1.77. At a true 110 m radius: 1.99/3.00. The headline (0/24) survives at every window; the numbers don't match the stated window |
| GT_15 "modelled at 0.046 m" (AJ) | That is the 3×3 local-max field; exact-cell realized value is 0.0117 m (goal15's own table). AJ uses the field AI calls "the most generous available" while AI's discipline is exact-cell — undisclosed |

Also: item AN says the gauge record "contains no major storm," while the same file's 1a summary row (written the same day) says the 16 Aug storm is what proved the cumulative semantics. The record contains a genuine convective burst (multiple gauges 7–13.5 mm in 15 min); the reviewer under-reported it and over-dismissed it.

**Does it change the verdict?** The five errors don't flip the gate verdict (the GT-statistics core of AJ, all of AA's arithmetic, AI, AG, AE reproduce exactly), but they falsify the implicit claim that the reviewer's heredocs are reliable measurement, and they are exactly the kind of errors the reviewer would flag in an agent report (Class 6, V1).

## F6. The depth-metric ruling (claim 6) is a rule-stretch that silently amends the spec — and the metric it banned was the only significant one left.

**Claim attacked:** 6 ("the depth metric violates the project's own rule").

**What is wrong.** CLAUDE.md's rule is a *reporting* rule: don't report per-square-metre depth maps. PROMPT.md §8 — the fixed spec — *requires* "RMSE in depth at ground-truth points" as a gate metric, with depth "as a band, not a number." goal12 scored point RMSE against band midpoints, which is what the spec asks. The reviewer declared it "precisely the metric claim the rule prohibits," rescored the gate, and recorded the new metric as the verdict — without flagging the PROMPT-vs-CLAUDE conflict or adjudicating it with the owner (whose authority it is, per CLAUDE.md's own rule on deviations from stated objectives). The replacement has one measurable consequence: on the 24-point GT set it is statistically null (p = 0.088, F4), so the "re-score before the verdict means anything" move replaced a weak but spec-mandated metric with noise. The underlying judgment — 10 m point depths at news-geolocated coordinates (snap distances up to 171 m, GT_24) are not defensible — is right; the claim that the *spec's own* metric violates the *reporting* rule is not the rule's letter, and the process by which the gate's metric was changed is outside the reviewer's authority.

**Does it change the verdict?** No (both metrics agree the model fails the points), but it's a spec-governance finding.

## F7. Exposure H: the 24 GT bands were treated as truth, and they are not clean.

**What is wrong.** (a) **GT_14's observed_date is 2022-08-30** — a different flood (the 29–30 Aug event, per its own source quote). It is scored against the 4–5 Sept simulation as one of the 24. The guard written to catch this (`tests/test_groundtruth.py` invariant 2: window "2022-08-30 .. 2022-09-06") *legitimizes* the mismatch. (b) The set is 24 points (1 extent-only, 1 wrong event, 2 with ≥140 m geolocation uncertainty) against PROMPT §8's 30–50. Nobody flagged the shortfall. (c) Depth cues ("car bonnet/waist" → 0.85–1.1 m) are judgment mappings — reasonable, but the reviewer used the bands as fact for "20 below, 2 above" while simultaneously ruling depths at these same coordinates unmeasurable (F6). The consistency is: the bands are reliable enough to condemn the model and unreliable enough to disqualify the metric. Both can't hold at the same confidence.

**Does it change the verdict?** Not the fail, but it shrinks the evidentiary base further: the two non-SAR halves of the gate rest on 23 points, one of which is from a different event.

## F8. The "4%" solver budget figure was endorsed with a wrong mechanism label.

**Claim attacked:** (audit brief: is the Fr² error bound used correctly downstream?) — goal12 §3's 4% was the only budget line AD kept as "traceable."

**What is wrong.** The 4% is real — `runs/anuga_crossval/manifest_froude_envelope.json`, mean_rel_diff = 0.0416 at Fr 0.876 (the domain's measured Froude). But goal12 labeled it "ACC local inertial approximation error … bounded at Fr²/2 ≈ 4%." At Fr 0.876, **Fr²/2 = 38%** — the label and the number disagree by ~10×. The 4% is a *mean* ACC-vs-ANUGA relative difference on benchmark cases at the domain's representative Fr — not a bound, not Fr²/2. The reviewer's AD struck three fabricated budget lines and blessed this one without noticing the mechanism label is wrong. (Minor: as an error-budget share, a mean benchmark difference still isn't a solver-vs-reality error bound.)

**Does it change the verdict?** No — but it's a "right number, wrong axis" instance in the reviewer's own endorsement.

## F9. Exposure C (priming): prompts aren't in the repo; the pre-commitment and the echo are.

**What is wrong.** The goal prompt texts are not recorded anywhere in the repo (only the reports, `goal12–15.md`), so the exact quoted line ("if segment-level lift is not materially above 5.17×…") cannot be verified from the record. What is verifiable: item AK (written *before* goal14 was dispatched) already pre-commits the conclusion — "must be re-scored per-road-segment before its verdict means anything" — and goal14.md is structured as a comparison against the cell-level 5.17× reference with a verdict that arrives exactly at the anticipated answer; goal15's report frames its mechanism ("underpass sag missing") in the reviewer's words. The returned reports are consistent with echo, not independent falsification, and the one place an independent number could have falsified the direction — the GT p-value (0.088) and the cell-weighted BBMP lift (0.92×) — was present in the reports' own outputs and went unremarked.

**Does it change the verdict?** No, but it means the "independent evidence" for claims 6/8/10 was partially the reviewer's own conclusion round-tripped through agents.

## F10. The bar itself (0.73 / 0.17 m) was never interrogated.

**Claim attacked:** 1's framing ("23× below the bar").

**What is wrong.** The citation is real — Woo, Choi, Kim, Noh, *Water* 17(8):1239, 2025, verified via Crossref. But it reports CSI 0.79/0.73 for a **trained CNN surrogate** of a calibrated physics model over the Oncheon-cheon catchment (Busan), validated on a historical event with **observed ground-based rainfall**; the depth-RMSE 0.17 m figure is not in the abstract at all. The gate compares an *uncalibrated solver*, forced by ~11 km satellite rain, against an unvalidated SAR reference. "23× below the bar" is a comparison across two different tasks and two different error budgets — the exact conflation CLAUDE.md's two-budget rule exists to prevent. The reviewer applied the bar without ever checking what it measured.

**Does it change the verdict?** No (the gate would fail even against a much lower bar), but the rhetorical spine of the review is a category error.

## F11. Exposure B (reviewer-as-implementer): the rule's predicted failure materialized.

The "verification, not implementation" distinction is partially honest — the reviewer's analyses were read-only. But the rule's *rationale* is not about edits; it's about leaving no one to catch invented or wrong evidence. The reviewer became the sole analyst of every load-bearing measurement (SAR change detection, IMERG totals/intensity, DEM sink test, null models), then judged its own numbers and dispatched rebuilds on them. The consequence is exactly what the rule anticipates: the errors in F5 (five non-reproducing figures, one false "measured, not copied" claim, one mislabeled window) survived precisely because no independent pass saw them. The distinction was a rationalization in the sense the rule names.

## F12. Exposure E's generalization: the August-monsoon fact has three consequences; the reviewer followed only one.

The reviewer corrected itself once (12 Aug ≠ dry season, AA). The same fact — a wet August — also implies: (a) lakes and groundwater were full on 4 Sept (reality: flooded a week earlier), yet the model starts dry with ~28% of the storm's volume in empty lake storage (F3); (b) the GT set includes the August event (GT_14); (c) the 5 Sept daily rain (131.6 mm) is documented in the project's own corpus and contradicts IMERG (F2). The reviewer followed the SAR-mask consequence and stopped. Unchecked definitional/seasonal assumptions remain live in the causal chain.

## F13. Remaining exposure adjudications (quick verdicts)

- **D (wrong-dimension closures) — confirmed, two additional instances.** (i) AA resolved the SAR question on the change-detection axis; the load-bearing axis was classifier validity (F1). (ii) AI concluded "a placement failure, not a magnitude failure" — wrong on its own numbers: even the local-max field reads 0.23–0.38 m at three of its own example points against band lows of 0.35–0.85 m; both placement *and* magnitude fail. The totals-vs-intensity instance (AJ→AN) makes three.
- **E** — F12.
- **F** — F4.
- **G** — F1.
- **H** — F7.
- **I ("water does not arrive" inferred, not traced)** — the arithmetic is right, and no flow path was ever traced. The realized budget (F3) shows two synthetic removal mechanisms accounting for 67% of the delivered storm before ponding — a simpler explanation for "water does not arrive" than terrain routing, never examined.
- **J (missing denominator in its own work)** — the reviewer's own fractions carry their denominators; the denominator it *never computed* is the SAR reference's recall against the project's own independent labels: **0/24 GT, 1/398 BBMP** (F1). It measured the false-positive side of its reference (52.0% persistent-wet) and never the false-negative side, though the data was on disk.
- **Claims 5, 7, 9 — survive, with one relabel.** AK's 0/24-sink result and AG's zero-fill result reproduce exactly; claim 9 (BBMP uninterpretable for CSI/FAR) is correct and was even implemented in the harness — which makes item AN's reporting of BBMP/GT CSI-lift a contradiction (F4). Claim 5's *numbers* carry the mislabeled window (F5), and its random baseline should have been road cells (road-cell control: 1.58 m median at the realized window — the "1.09 vs 1.73" gap halves), but the direction holds.

## Also-checked (the unasked questions)

- **Is Sept 2022 the right target?** The right event (spec-mandated, biggest recent flood), documented thinly: no gauge rainfall (item 1b blocked), 23 usable points of a 30–50 spec, SAR snapshot at 06:10 IST 5 Sept — *before* the heaviest rain (5 Sept evening) — and an unvalidated classifier. The reviewer's verdict never acknowledged how much of the event the evidence base doesn't cover.
- **Does the domain make sense?** The 61.45% boundary-outflow figure is from goal15's *6-hour design-storm acceptance run*, not the gate run; the gate run's realized budget is 39.3% out + 28.0% drain + 32.7% retained (`segment_validation_report.json`). The boundary is a free-outflow rectangle around a city that genuinely straddles a watershed divide, so large outflow is physically plausible — but it is still an arbitrary boundary with unvalidated conditions, and neither the reviewer nor goal15 analyzed the gate-run budget in these terms. (Also: goal15's "120.25 km²" drain-influence area is a 10× misprint of 1,202.5 km²; the volume math used the right number. Not caught in review.)
- **Is the ACC solver appropriate / is the Fr² figure used correctly?** Solver correctness was adjudicated in item W; the downstream *use* of the 4% figure is mislabeled (F8).
- **Are V1–V8 coherent?** Individually yes. Two live conflicts: (a) PROMPT §8's required depth-RMSE metric vs the CLAUDE.md reporting rule (F6) — adjudicated by the reviewer unilaterally; (b) V3 was claimed for the change-detection reference, but the reference is built by the same unvalidated classifier as the scoring code under review (F1) — "V3-compliant" was an overclaim. V5 was applied to every agent test and never to the reviewer's own measurements.
- **Is there a simpler explanation nobody proposed?** Yes. One single explanation covers most of the signature: *the run's inputs and reference are unvalidated, and both bias against the model flooding the right places.* Under-read forcing (87 vs ≥131.6 mm), arbitrary drain prior (28% removed), free boundary (39% out), dry-start lakes (28% storage) all suppress the depths at GT points regardless of terrain; and the reference contains none of the event (F1), so the extent "failure" is unmeasured. The underpass mechanism is real for ~6 points and cannot be the story for the other 17.

---

## Reproduction ledger (exposure A, claims 1–8)

**Reproduced exactly** (computed this audit, matched the reviewer's figures): AA threshold table (416,581 / 311,851 / 236,621 / 124,300 and PODs); 122,943 persistent-wet (51.96% ≈ 52.0%); 113,678 change set; 48,217 / 74,726; 87,030; corrected contingency TP 14,230 / FN 72,800 / FP 355,141 / CSI 0.0322; random floor 0.0061; lift 5.17×; AI's three RMSEs (0.7505 / 0.7834 / 0.7796) and 15/24, 21/24 counts; AG's 24/24 zero-fill and the 5-nearby/3-≥2m counts; AE's fill thresholds and 6.7346 M m³; AJ's GT totals (74.92/84.13/109.10), GT_15=109.10, GT_10=105.04; AN's 21.40 mm/hr peak, zero ≥25, GT peak stats (10.41, 8.68–16.75), 5 IMERG cells [12,6,4,1,1]; AN's drain-capacity mean 3.263 mm/hr; AK's 0/24 sink result; AB's manifest-SHA claim; AC's 89% window arithmetic; AH's 20 BLOCKED solver invariants.

**Not reproducible / wrong as stated**: the five F5 rows (25 cells & 54.0 min; 46.32/39.15 provenance; 38.0 mm/hr & "none >50" & 3-gauge count; 110 m window numbers; GT_15's 0.046 m field choice), plus the "roads" mechanism in AA (302 TPs) and the "25-cell" buffered count (realized 16).

---

## What the review got right (says so plainly)

- The arithmetic of AA, AI, AG, AE and the IMERG GT statistics is exact — the reviewer's core measurements are not fabricated, and the user's provenance fear (exposure A) is real as a *process* violation but the numbers mostly check out.
- Claim 9 (BBMP/GT are positives-only; CSI/FAR uninterpretable) is correct and materially improved the segment harness.
- AB (manifest `git_sha` resolves to a tree without the entry script; no `status` field) — verified directly against git.
- The dry-season self-correction in AA is honest, if incomplete (F12).
- AN's intensity item, minus its gauge statistics, is the right question and is now partly answerable (F2).
- The overall gate **fail** conclusion is correct: nothing on disk supports a defensible city-wide match, and the reproduced depth deficit at 24 known flood locations is decisive independent evidence.

**What this means for the current plan.** The project is proceeding on: extent "fails at CSI 0.0322" (unmeasured — F1), "forcing is not the cause" (refuted by its own corpus — F2), and "terrain/drainage is the root cause" (covers ≤6/24 points — F3), and has already committed a drain-network rebuild on that basis. The order that follows from the evidence instead: (1) re-attack historical gauge rainfall (item 1b via VOBL/NOAA ISD, and read the 131.6 mm corpus entry), (2) validate or replace the SAR water classifier (dual-pol/coherence or the NRSC inundation layer), (3) decide antecedent lake state, and (4) then — and only then — judge whether the terrain needs rebuilding. Everything downstream of Phase 3 depends on that order.
