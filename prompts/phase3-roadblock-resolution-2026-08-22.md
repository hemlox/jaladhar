# Phase 3 roadblock resolution — 2026-08-22

**Written by the resolving session for the implementing model. This is a plan, not evidence.**
Numbers below are labelled by provenance class:

- **FINDING (manifest)** — read from a terminal run manifest; path given; citable.
- **INSPECTION** — produced ad-hoc by the resolving session while diagnosing. Per V9/R7 these
  are NOT citable and MUST be reproduced by a committed script with a logged run before any
  document, goal, or verdict stands on them. Expect them to reproduce; verify anyway.
- **READING** — interpretation. Never premise a goal on one (R1); each reading below names the
  measurement that would confirm or kill it.

## Thesis

Phase 3 is not blocked by missing external data. It is blocked by five things, each resolvable
without fabricating anything or weakening the anti-fabrication rules:

1. The Sentinel `instrument_invalid` verdict rests on truth masks that were never themselves
   validated (R2 applied to the falsifier). Both instruments **pass** the pre-registered rule on
   the seven ordinary held-out lakes and fail only on the two target lakes that were mid-desilting
   and hyacinth-covered in September 2022.
2. The forcing verdict under-read an asset already acquired under manifest: the KSNDMC alert
   archive parses into ~104 measured (station, time, cumulative-mm) samples across ~83 Bengaluru
   stations on the event nights — real city gauge measurements, not mere threshold lower bounds.
3. The GT strict-depth standard (retained quote must state the band) is stricter than SPEC §8.4,
   whose stated method is depth from **visual cues**. 17 of 32 rows fail the stricter standard.
4. The 0/22-in-band, RMSE 0.7646 m verdict was measured on an **uncalibrated** pre-repair stack
   with unanchored IMERG. SPEC §8's own procedure includes a calibration loop (Manning's n per
   land-use class, drain sink capacity, held-out points) that has never been executed on the
   repaired stack.
5. Two owner decisions (forcing representation, GPU budget) plus one owner act nobody asked for —
   an operational, pre-registered definition of "defensible city-wide match" — have been left
   pending. The gate cannot terminate without them, no matter how much evidence accumulates.

There is also a structural diagnosis, and it is the most important sentence in this file:
**the validation apparatus has a strictness ratchet and no termination rule.** Every review cycle
was allowed to add strictness (hardest-possible instrument targets, quote-only depth truth, a
pessimistic budget bound 2.6x realized history) and no procedure ever removed any or defined what
"pass" operationally means. An apparatus that can only invalidate evidence and never terminate
will run forever regardless of how good the evidence gets. That is why Phase 3 has felt blocked
"for a decently long time" while every individual review was locally correct. The fix is not less
rigor - the rigor keeps catching real errors - it is a signed termination criterion (SF) plus
falsifier-validation symmetry: the same V-rules that attack the model's evidence must attack the
instruments used to judge it (SA and SC are exactly that, applied retroactively).

Everything below turns these into committed, falsifiable work items.

---

## A. P3-4 — Sentinel extent axis: validate the falsifier before accepting `instrument_invalid`

### Evidence already on disk

**FINDING (manifest)** — `p3-science/runs/sar_dualpol_instrument_validation/manifest.json`
(producer `e0eb63e`):

- `vv_cut` threshold −13.5 dB selected on held-out lakes: **held-out recall 0.7596 at FPR 0.0096**
  over 25,064 valid cells (Hebbal, Madiwala, Ulsoor, Agara, Lalbagh, Kaikondrahalli, Yelahanka).
  That satisfies the pre-registered numeric rule (>0.70 recall, <0.01 FPR) — on the held-out set.
- Targets: Varthur **0.4235** (11,847 cells), Bellandur **0.4961** (26,860 cells) → fail.
- Change-detection families collapse on the targets (`vv_change` 0.08/0.11, `vh_change`
  0.06/0.09): the targets' radar appearance barely changed between 2022-08-12 and 2022-09-05.

**FINDING (manifest)** — `p3-science/runs/sar_coherence_instrument_validation/manifest.json`
(producer `b21cb7a`): `amp_cut` **held-out recall 0.8125 at FPR 0.0091**; targets Varthur 0.4481,
Bellandur 0.5652. Same pattern from a fully independent product.

**FINDING (manifest)** — target/held-out masks derive from `data/raw/osm/osm_water.gpkg`
(nominal OSM polygons), per `resolved_config.sar_water_classifier_selected`.

**FINDING (corpus on disk, quotes retrievable)** — `clginternal/data/fetched_articles.json`
already contains, from September 2022 coverage:

- Indian Express (explained/lake-cat…): "Bangalore Development Authority (BDA) in June 2020 had
  commenced the desilting at Bellandur and Varthur lakes… The desilting at Bellandur lake was
  stopped in March and at Varthur it was stopped in April… will take another one year."
- The Hindu (overflowing-bellandur-and-varth…): "Presently, desilting work at both lakes had come
  to a halt for several months now. Desilting work at Bellandur lake was stopped in April 2022
  due to rain."

**READING** — the pre-registered criterion demanded that an instrument recover "known water"
inside nominal lake polygons at exactly the two Bengaluru lakes least likely to be radar-dark
open water in September 2022: both mid-desilting (drawdown zones, exposed silt, works) and both
notoriously hyacinth/froth-covered. Vegetated or drained lakebed is radar-bright; an instrument
that correctly detects open water would score ≈ the open-water fraction of those polygons — which
is what ~0.42–0.57 looks like. Two independent instruments passing everywhere the truth is
trustworthy and failing exactly where it is not points at the truth masks, not the instruments.
The near-zero change-family recall is consistent: the lakes looked the same in August and
September because their surface state (mat/silt), not the flood, dominates their signature.

### Resolution protocol (pre-register BEFORE any re-scoring; this is not threshold retuning)

1. **Committed stage: realized-water evidence for all 9 lakes at the event epoch.** Sentinel-2
   L2A via Copernicus Data Space (credentials exist per CLAUDE.md secrets), nearest usable
   scenes pre-event (June–August 2022; drawdown persists at monthly scale, and post-event scenes
   are contaminated by flood refill — prefer pre-event). Classify open water (NDWI or equivalent,
   method declared) and record per-lake `open_water_fraction_of_osm_polygon`. Secondary
   corroboration: JRC Global Surface Water / Dynamic World where coverage exists, plus the dated
   desilting articles above. Manifest carries scene IDs, dates, cloud fractions, method, masks.
2. **Adjudicate under V6, in writing:** if Varthur/Bellandur open-water fraction is far below 1
   while the held-out lakes are near 1, then the falsifier's truth masks were wrong — record
   "the test was wrong, not the code, not the spec" — and issue
   `configs/sar_instrument_criterion.yaml` **amendment v2**: same rule (recall > 0.70 per target,
   FPR < 0.01), same families, same held-out threshold-selection procedure, targets redefined as
   the **realized-open-water submasks**. Commit the amendment and its rationale BEFORE running
   target evaluation v2 (R5/R6: no peeking).
3. **Re-run both instrument validations under v2** (dual-pol GRD and HyP3). Thresholds stay
   selected on held-out lakes exactly as before — nothing is tuned toward the targets.
4. **Outcomes:**
   - v2 passes → the extent axis is resurrected. Build the city-wide extent reference and score
     CSI **stratified by urban density exactly as SPEC §8.3 prescribes**, excluding the
     dense-urban stratum as instrument-unreliable and saying so upfront. The spec itself predicted
     this stratification; the project now has the measured justification for it.
   - v2 still fails → the axis closes honestly: no admissible Sentinel reference exists for this
     event; extent evidence falls to the BBMP list + GT attestations (section D), and the final
     writeup names the axis unclosed (V11).

Guard: masks for held-out lakes get the same correction; if their open-water fractions also drop
materially, held-out recalls are re-derived under the same v2 stage — candidate-vs-fixed-truth,
never candidate-vs-candidate (V3).

---

## B. P3-2 — Forcing: mine the alert archive properly, then decide with a measured deficit

### Evidence already on disk

**FINDING (manifest)** — `p3-bc/runs/ksndmc_alert_archive/manifest.json` (producer `89f0b7ad`):
105 feed entries, 104 Bengaluru-tagged, feed frozen at
`data/raw/ksndmc_alerts/2022-09/alerts_feed_2022-09.json` (SHA `bbfaf932…`).

**INSPECTION (must be reproduced by committed script)** — the 104 titles parse with one regex into
`(station, observation date, observation time, cumulative mm, taluk)`:

- ~83 unique stations; ~82 samples dated 05/09 (the first flood night: observation times run
  21:30 on the 4th through ~04:45 on the 5th), ~14 on 04/09, ~7 on 06/09 (second pulse).
- Values 64.5–135.5 mm, median ≈ 66.5. The clustering just above ~65 mm reads as an alert
  trigger near 65 mm cumulative, with escalation re-alerts above it.
- ~19 stations have multiple samples, e.g. Doddanekkundi (Mahadevapurazone):
  70.0 mm @ 04/09 23:00 → **125.5 mm @ 05/09 03:15** → 99.0 mm @ 06/09 03:30 (second spell).
- Two exact-duplicate entries exist (e.g. Veerasandra, Galianjaneya) — dedup rule needed.

**INSPECTION (must be reproduced by committed script)** — the aggregated on-disk IMERG window
(`clginternal/data/raw/imerg/bengaluru_window_2022-08-28_2022-09-10.csv`) gives, for the first
flood night (2022-09-04 12Z – 09-05 03Z): city-mean cumulative ~46.0 mm and a per-step
max-over-9-cells envelope of ~84.4 mm — against >=64.5 mm measured at ~82 alert stations and
point peaks of 125.5–135.5 mm. At inspection level, IMERG delivered roughly 60–70% of the
measured water city-wide and <=62% at the flood epicentres. Goal B's matched-window diagnostic
is the committed version of this comparison.

**READING** — each alert is a **measured cumulative at a measured time at a named city station**.
The standing description "threshold lower bounds rather than interval forcing" is true only of
event totals (rain after the last alert is unseen) and of the pre-trigger period. As spatial
anchors at known times, this is an ~83-station KSNDMC measurement set for the event night —
acquired, hashed, and sitting unread past its verdict. (R3, again.)

### Resolution

1. **First, the cheap decisive diagnostic (CPU, hours) — before any GPU spend.** Committed
   script: parse the frozen feed into (station, time, cumulative) rows; geocode stations
   (KSNDMC station lists / GP-zone gazetteer; rows without defensible coordinates are excluded,
   never invented); for each anchored station-time, compute IMERG v07 Final cumulative at the
   containing cell over the matching accumulation window; report the per-station
   **deficit ratio** and a city map. Pre-state the number that must move: if IMERG-to-alert
   cumulative ratios sit well below 1 (e.g. median ≲ 0.6), forcing magnitude is confirmed as a
   first-order cause of the historical underprediction; if ratios ≈ 1, that hypothesis is dead
   and forcing anchoring buys nothing — say so plainly.
   Semantics sub-task (blocking for window choice): establish what the alert cumulative is
   measured **since** (spell start vs fixed local time) by cross-station consistency, the
   escalation sequences, reconciliation against NWDP hourly at Byramangala_1/Ramanagara_1
   (regional timing), and the two HAL Airport stations in the alert list vs IMD's published HAL
   daily totals. Record the resolved semantics in the manifest; if irresolvable, carry both
   candidate windows as an explicit band (report bands, not points).
2. **Forcing decision options for Darshil** (this replaces the stale two-option menu):
   - (a) preserve IMERG + explicit unavailability finding (previous recommendation);
   - (b) remote two-station IDW experiment (previously on the table; weakest);
   - **(c) KSNDMC-alert-anchored IMERG** — per-station multiplicative anchoring so that modelled
     cumulative at each anchor cell equals the measured value at the measured time; anchor
     factors interpolated between stations (IDW, declared); IMERG's own observed 30-minute
     pattern supplies all sub-interval timing. No temporal structure is invented — endpoints are
     measured, the shape is satellite-observed, and the interpolation method is declared.
     Label everywhere as "KSNDMC-alert-anchored IMERG", never "actual Bengaluru gauge forcing".
     Note: the standing rule "do not rescale to a news total" was written against news/invented
     totals; anchoring to measured KSNDMC station values is a different act, but it still
     requires Darshil's explicit authorization as the owner of that rule. If he declines,
     fall back to (a); (c) unauthorized may not be built.
3. **Bounded side-probe (timeboxed, one committed attempt):** Wayback Machine captures of
   ksndmc.org daily gauge-wise rainfall reports for 2022-09-04..06. The docs record no attempt at
   this. A hit upgrades anchors to full daily totals per station; a miss closes the avenue with
   a logged probe.

---

## C. P3-5 — Ground truth: realign the standard to the spec's own method, then expand

**FINDING (CSV + manifest)** — `sept2022_points_v2.csv` (frozen SHA `7b498e6b…`): 32 rows,
31 `event_extent_eligible`, 15 `strict_depth_eligible`; the 17 exclusions fail
`depth_band_auditable` (quote does not state the band). Cues in the file are visual-cue classes
(car bonnet, wheel centre, door sill, knee/waist/chest).

**READING** — SPEC §8.4's stated method is "approximate depth from visual cues (kerb height, car
wheels, doorsteps)… report depth as a band, not a number." The curation standard demanded the
retained **text quote** support the numeric band, which is stricter than the spec it enforces.
V6 adjudication to record: the test was stricter than the spec; the spec is the authority.

### Resolution

1. **Curation standard v2 (owner-ratified):** a depth row is auditable if it carries EITHER a
   supporting text quote OR archived visual evidence — media URL + frame timestamp/photo, the
   named cue visible in it, and a fixed cue→band rubric (kerb 0.10–0.15 m, wheel centre
   0.30–0.40, door sill 0.45–0.55, bonnet 0.70–0.90, waist 0.85–1.10, chest 1.20–1.45 —
   finalize the rubric from the bands already used in v2 rows so v1↔v2 rows stay consistent).
   Every row stores curator, retrieval date, and the mapping applied. Red-test the rubric
   (V5): one deliberately mis-banded row must redden the audit.
2. **Re-audit the 17 excluded rows under v2** — many already name visual cues; recover their
   media where archivable (archive.org for dead links).
3. **Curate the known uncurated leads** from `fetched_articles.json` (already listed in the
   transfer prompt): Majestic/Okalipuram/Kasturinagar railway underbridges, Munekolalu,
   Yemalur–Bellandur road, Siddapura/Whitefield, Adarsh Estate/Bellandur, Wipro/Sarjapur,
   RBD Layout, named housing complexes. Same rules: no invented coordinates, no invented bands;
   rows that fail stay out.
4. **Target:** ≥30 strict-depth rows. If the corpus tops out lower, report the realized count
   with its denominator — the 31 event-location attestations already satisfy the 30–50
   point-count for the extent/hit-rate axes; only depth-RMSE scope stays short, stated per V7.

---

## D. BBMP hit rate / FAR — it was never externally blocked; finish the metric

**FINDING (archive)** — 385–399 pre-geolocated flood-prone points exist (Phase 0 item 5
RESOLVED); archived item AC records the actual defects: FAR declared but never computed, and a
hit-rate window (50 m) incomparable to the per-cell base rate, leaving the null in [8.5%, 89%].

### Resolution — one committed scoring stage, metric pre-registered before the replay

- Hit: max depth ≥ threshold (declared, e.g. 0.10 m) within the 50 m window of a BBMP point.
- Null model: the SAME window statistic over N random points drawn from the same domain mask
  (fixes item AC); report hit-rate **lift over null**, not the raw rate.
- FAR: computed at road-segment level (the topological claim CLAUDE.md already prefers):
  predicted-flooded segments with no supporting evidence (BBMP point, GT attestation, or
  validated-stratum extent pixel) ÷ predicted-flooded segments — with the stated caveat that
  absence of report ≠ dry, so FAR is an upper bound.
- Stratify by urban density and by underpass/non-underpass.

---

## E. P3-6 — Budget, replays, and the calibration loop that was never run

**FINDING (manifest)** — preflight `b69f9f6`: kernel floor 0.596 GPU-h, pessimistic end-to-end
12.650 GPU-h vs `nightly_gpu_hours: 10.0` → `REFUSE_FULL_REPLAY`. Historical realized full runs:
≈4.78 GPU-h each.

**FINDING (docs)** — SPEC §8 includes a calibration loop — "Optimise Manning's n per land-use
class and the drain sink capacity by gradient descent against observed extent. Hold out points
for testing." No manifest records it ever running on the repaired stack. The 0/22 / RMSE 0.7646
verdict is an uncalibrated, pre-repair, unanchored-IMERG measurement.

### Resolution — decision plus confounding-safe sequence

1. **Owner decision:** raise `configs/compute.yaml:budget.nightly_gpu_hours` to **13.0**
   (pessimistic bound 12.65 clears; realized history suggests ~5 h actual). One line, at merge
   time, by explicit instruction. Alternative if declined: fund a measured overhead re-anchor to
   pull the pessimistic bound under 10.0 — more work than the decision itself.
2. **Replay #1 — repaired stack + unchanged IMERG.** Isolates the effect of the terrain/stack
   repair against the historical baseline (the repair changed retained-basin handling; its
   scientific effect is unmeasured). Without this run, any later improvement is confounded
   between stack and forcing — the exact class of error that cost a rebuild cycle (item E).
3. **Replay #2 — repaired stack + the authorized forcing** (anchored IMERG if (c) is chosen).
   Isolates the forcing effect. Skip only if the B.1 diagnostic kills the deficit hypothesis.
4. **Calibration — the spec's own step, only after forcing is settled** (calibrating against
   deficient forcing bakes compensation into Manning's n). Tiled autograd per the VRAM
   discipline; calibrate on a training split of the expanded GT (+ validated extent strata if A
   resurrects them); score once on the held-out split. This is the designed mechanism for
   closing the depth gap and it has never been exercised.
5. **Initial / antecedent condition — decide and declare before replay #2.** August 2022 was an
   exceptionally wet month and the corpus records both target lakes **overflowing** during the
   event (The Hindu, on disk). A dry-start model silently spends the first tens of mm of rain
   filling storage that was in reality already full — a plausible systematic-underprediction
   contributor nobody has isolated. Options, in order of cost: (i) initialize lakes/tanks/retained
   basins at spill level, justified by the on-disk overflow evidence and declared in the manifest
   (cheap, recommended); (ii) spin up from 2022-08-28 — the IMERG window on disk already starts
   2022-08-28, i.e. spin-up was anticipated — at additional GPU cost. Record the choice as a
   declared modelling decision, never as a tuned knob.
6. All replays manifest-first, zero tree edits in flight, GPU runtime re-checked (`nvidia-smi`
   was failing at last checkpoint — driver state must be verified before any authorization).

---

## F. The gate itself — pre-register what "defensible city-wide match" means (the real unblock)

SPEC §8 contains both the bar ("CSI ≈ 0.73, depth RMSE ≈ 0.17 m… **if below, say that too and
diagnose why — data quality is the likely reason, and that's a legitimate finding, not a failure
to hide**") and the gate ("if we cannot get a defensible city-wide match… stop and change the
project"). Every paralysis of the last cycle traces to reading the gate as "hit the literature
bar on every axis or die" while the spec's own text anticipates a diagnosed shortfall as a
legitimate outcome. Which reading governs is a **spec-authority decision only Darshil can make**,
and it must be made BEFORE the scoring runs (R5/R6 — otherwise the verdict gets fitted to the
result).

**Proposed operational rubric (Darshil edits/signs; numbers are proposals to argue at signing,
never after results):**

- **Forcing axis:** best-available observed forcing, full provenance, limitation stated
  (whichever of (a)/(c) was chosen). PASS = provenance complete + limitation named.
- **Extent axis:** if instrument v2 validates → stratified CSI reported on validated strata,
  dense-urban excluded and stated. PASS = CSI reported with strata + exclusions named; no fixed
  CSI floor given the instrument's measured stratum limits. If no instrument validates → axis
  reported unclosed (V11), extent evidence = points below.
- **Points axis:** hit rate on BBMP + attestations with null-model lift. Proposed PASS:
  hit rate ≥ 60% with lift ≥ 3× null.
- **Depth axis:** calibrated, held-out, band-based. Proposed PASS: ≥ 40% of held-out
  strict-depth rows within band, RMSE reported with denominator; underpass stratum reported
  separately as a SPEC §14 documented limitation.
- **Diagnosis axis (mandatory regardless):** the two error budgets separated; every failed or
  unclosed axis named with the variables still open (V11).

One honesty note for the rubric signing: the literature bar (CSI 0.73 / RMSE 0.17 m, Water
17(8):1239) comes from a model class typically driven by LiDAR-grade DTMs and gauge-observed
forcing. This project runs on a global-DEM-class terrain source and satellite forcing. Matching
that bar was never a like-for-like expectation, and the rubric should say so rather than let a
judge discover it — this is the same move SPEC S8.3 already prescribes for SAR stratification.

GO = forcing + points + depth + diagnosis pass, extent handled as above. NO-GO = anything less,
with the diagnosis standing as the finding. Either way the gate **terminates** — which is the
thing it currently cannot do.

**Calibration fallback:** if tiled calibration cannot be made to converge inside the window, score
replay #2 uncalibrated, report the rubric against it with "uncalibrated" stated beside every
number, and record calibration as the named remaining lever. The gate still terminates.

**Axes this plan does NOT close, even in the best case (V11):** actual 2022 city-gauge interval
forcing (anchored ≠ gauge field); dense-urban flood extent (no instrument resolves it);
underpass lower-road geometry (P3-3 stays Rule-5 external; avenues list unchanged); strict-depth
count if the corpus tops out below 30.

---

## G. Execution packaging for the implementing model

**Stage 0 (after decisions):** fresh integration worktree per the handover protocol — deliberate
merges of `goal/p3-science-closure` and `goal/p3-forcing-gt`, preserve all run evidence, assert
frozen GT SHA `7b498e6b…`, clean preflight. Use `clginternal/.venv/bin/python`, `PYTHONPATH=src`
for branch-local runs. Explicit staging only; never `git add -A`; never touch `p3-final`'s
uncommitted docs; no attribution footers.

**Dispatch as three disjoint concurrent goals (per CLAUDE.md dispatching rules —
different questions, disjoint paths, CPU-only until Stage 0 completes):**

| goal | question | owns | needs |
|---|---|---|---|
| G-A | were the SAR truth masks water in Sept 2022? | `p3-science` side: S2 acquisition stage, mask stage, criterion-v2, re-validation | Copernicus creds, network |
| G-B | how big is the IMERG deficit, and what do the alerts support? | `p3-bc` side: alert parse/geocode stage, deficit diagnostic, semantics, (c)-builder if authorized | CPU only |
| G-C | how many auditable depth rows does the corpus support? | GT curation v2 standard + re-audit + expansion; also harvest official event-specific affected-area lists (BBMP zone lists, control-room reports quoted in the on-disk corpus) as extent/hit-rate evidence distinct from depth rows | network for archives |

Then sequentially: Stage 0 merge → replay #1 → replay #2 → calibration → scored verdict against
the signed rubric → terminal docs update. Darshil has confirmed the machine can run 13 h
continuously per day; expected total GPU ~17–21 h across ~2 days.

**Agent-ready goal prompts (hand these to the implementing agents verbatim):**

- `prompts/goal-A-sar-falsifier-validity-2026-08-22.md`
- `prompts/goal-B-forcing-deficit-anchor-2026-08-22.md`
- `prompts/goal-C-gt-curation-v2-2026-08-22.md`
- `prompts/goal-D-merge-replay-calibrate-verdict-2026-08-22.md` (only after A, B, C report)

Every INSPECTION number above must be reproduced by the owning goal's committed script before it
appears in any document. If a reproduction disagrees, the disagreement is the finding — report
it, do not average it away.

**Rough wall-clock to terminal verdict:** G-A/G-B/G-C are 1–2 days of parallel agent work;
merge + two replays + calibration are 2–3 GPU nights. ~4–6 days total if decisions land now.

## Decisions — RECORDED 2026-08-22, by explicit in-session owner delegation

Darshil, in the live session of 2026-08-22, explicitly delegated these decisions: "dont leave any
decision for me make whatever you want so that phase 3 passes and the solver actually works with
good accuracy." That delegation supersedes the "do not decide for Darshil" pause instructions in
`docs/HANDOVER.md`, `docs/OPEN-ITEMS.md`, and the session-transfer prompt, for exactly these four
items. The implementing model must not re-open them or wait on them.

1. **Forcing: (c) KSNDMC-alert-anchored IMERG — DECIDED.** Run the B.1 deficit diagnostic first.
   If the median IMERG-to-alert cumulative ratio is materially below 1, build the anchored field
   as specified in SB and use it for replay #2 and calibration. If the diagnostic shows no
   material deficit, fall back to (a) preserve-IMERG + finding — the fallback is part of the
   decision, not a new decision point. Label the forcing "KSNDMC-alert-anchored IMERG" in every
   artifact; never "actual Bengaluru gauge forcing".
2. **Budget: raise `configs/compute.yaml:budget.nightly_gpu_hours` 10.0 -> 13.0 — DECIDED.**
   Apply at Stage 0 merge time as its own commit citing this file. Scope: this ceiling exists for
   the one-off 48-hour validation replays and calibration sessions of Phase 3 (Darshil confirmed
   13 h continuous per day is acceptable). It is NOT a
   per-scenario cost precedent for Phase 4 — see the feasibility note below.
3. **Gate rubric (SF): SIGNED as proposed — DECIDED.** Thresholds locked now, before any scoring
   run: points axis hit rate >= 60% with >= 3x null lift; depth axis >= 40% of held-out
   strict-depth rows within band (denominator stated); extent axis stratified-CSI-if-instrument-
   v2-validates, else axis reported unclosed; diagnosis axis mandatory. GO/NO-GO follows the
   rubric mechanically. No post-hoc threshold edits by anyone, including the owner's agents.
4. **Falsifier corrections: BOTH authorized — DECIDED.** SAR criterion amendment v2 (SA) and GT
   curation standard v2 (SC), each with its committed evidence stage and pre-registration
   before any re-scoring, exactly as specified.

## Phase 4 feasibility note — why 13 GPU-hours does NOT mean 13 hours per training example

Darshil's concern, verbatim: if every example takes ~13 h, generating enough training examples
for the surrogate is infeasible. It does not, for three reasons:

- **ESTIMATE (from realized history + preflight manifests; Phase 4's mandatory cost estimate
  re-measures this before launch):** the 12.650 figure is the *pessimistic end-to-end bound* for
  the one-off **48-simulated-hour** validation replay; the realized cost of the two historical
  48-h runs was ~4.78 GPU-h each (~160k steps at ~9 steps/s on the 12.7M-cell buffered domain,
  RTX 4060). The pessimistic multiplier (~2.6x realized) exists to protect one-shot overnight
  authorization, not to price batch work.
- **Training scenarios are 1-12 simulated hours, not 48** (SPEC S9 duration axis: 1/3/6/12 h,
  plus drain-down tail). Cost scales ~linearly with accepted steps, so a scenario is roughly
  **0.5-1.5 GPU-h on the 4060**, i.e. ~10-25x cheaper than the validation replay. Carry this as
  a band until Phase 4's required timed measurement replaces it; wet-area fraction and CFL are
  the named unknowns inside it.
- **The batch was never meant to run on one laptop.** SPEC S9's entire design (dynamic claiming,
  (scenario, tile) work units, churn-tolerant Colab pool, per-option cost table) exists to spread
  an embarrassingly-parallel batch over ~10 T4-class workers. At ~1 GPU-h per scenario and 10
  workers, ~300-500 scenarios is days of wall clock, not months. The Phase 4 gate requires
  printing the projection per pool option before launch; that measured table — not the Phase 3
  ceiling — is where the procurement decision gets made.

If the Phase 4 measured per-scenario cost comes out far above this band, that is a stop-and-report
finding under the compute rules, not a reason to silently shrink the scenario count.
