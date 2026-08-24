# GOAL — Carve the measured per-location vertical data into the underpass pipeline

**Agent:** dpsk v4 flash (opencode). **Repo:** /home/darshil/Desktop/sih/clginternal.
**Precondition:** the underpass carve pipeline (`solver/state.py` elevation_override,
`terrain/underpass_carve.py`, `terrain/underpass_invert.py`, `analysis/gt_classification.py`)
is committed before you start. First thing you do: `git status`. If any of those files
shows as modified/uncommitted, **STOP and report** — do not commit them, do not revert them.

## Context (facts to verify, not to trust)

The data-search goal (commit 65f7679) found ~15 of 50 named underpass locations with
per-location vertical data from news/DPR: clearance quotes (e.g. Madiwala 5.5 m), DPR
dimensions (Panathur vents 9 m × 5 m tall), relative verticals (Yelahanka AFB ~6 m below
road level), flood-depth observations (KR Circle >3.7 m, 2026 event). The register of those
locations is the output of `scripts/search_underpass_levels.py` — locate it via that
script's run manifest at commit 65f7679, and re-open the source URLs yourself.

The carve pipeline exists and exactly one location is proven end-to-end: GT_06 Panathur,
baseline 0.009 m → carved variant 1.763 m vs observed [1.20, 1.45] m, full 48 h solver run
at `runs/underpass_goal/full_48h_variant/` (273.8 min wall, 3.27 GPU-hours; the manifest
holds the exact invocation).

## Question

Which of the ~15 locations carry a measured clearance or an invert-derivable vertical, and
what does the solver show at those locations when they are carved?

## Method

**0. Reproduce the existing carve first.** Re-run invert derivation + carving with the
existing `underpass_register_clearance.csv` on the parent conditioned DEM. Realized must
match the committed manifest: **109 cells modified, 42,373 m³ removed, 22 segments carved**.
If it does not, STOP and report — nothing proceeds on a non-reproducing pipeline.

**1. Inventory.** For each of the ~15 locations record: data type (clearance / relative
vertical / DPR dimension / flood depth), the exact quote, the source URL, the event year the
data describes, the register segment osm_id (if any), and the GT point (if any). Re-verify
every clearance quote against its URL — the summary's numbers are claims until you check.

**2. Classify.** Carve-grade = a measured clearance, or a relative vertical that maps into
the invert formula `invert_z = z_deck − clearance − deck_depth` using the measured z_deck —
state the mapping per row ("6 m below road level" maps to clearance+deck_depth, not
clearance). **Not carve-grade:** flood-depth-only data (a depth observation is not a
clearance — never carve from it); planned/barrier heights (a planned 4.5 m barrier is not a
clearance); any location with no matching register segment geometry (nothing to carve).
Depth observations from events other than Sept 2022 (e.g. KR Circle 2026) cannot be compared
to the 2022 run — record them, do not score them against it, do not carve from them.

**3. Dedup** against the 22 already-carved osm_ids (`runs/underpass_carve/manifest.json`
per_segment). Skip them.

**4. Carve the augmented set.** Write a NEW clearance CSV (existing rows + your new rows,
each new row carrying the quote, the source, the URL). Zero IRC-fallback rows for these
locations — **no measured clearance means no carve**, even if the location demonstrably
floods. Write a NEW orchestration script that imports `derive_inverts` and
`carve_corridors` — do not edit the pipeline files — and carves the augmented set from the
PARENT conditioned DEM into a NEW variant raster (new filename, own manifest, provenance:
parent DEM, per-segment clearance + source, git SHA).

**5. Pre-register the bars, timestamped, before the run** (a file, not a paragraph in the
report afterwards). For each carve-grade location that maps to a Sept-2022 GT point with a
band: the baseline depth at that point (read from
`runs/phase3_validation/depth_event_maximum.tif`), the observed band, the flood window
[band_low, band_high + 1.5] (the GT_06 convention), and the falsifier — a carve that moves
depth < 0.1 m from baseline falsifies the mechanism at that location; a carve landing
outside the window is an overshoot. For carve-grade locations with no Sept-2022 band, state
in advance that no validation claim may be made: carve + ponding numbers only.

**6. ONE full 48 h run** on the new variant (same invocation as the GT_06 variant run;
change only out_dir and elevation_override). Print the compute budget estimate before
starting: carve/derive are CPU; the run is ~3.3 GPU-hours against the pool in
`configs/compute.yaml`. Refuse to start if it exceeds budget. If no GPU is available, stop
after step 5 and report that — never claim a run that did not run.

**7. Postanalysis.** All 24 GT points vs baseline: any point not in your carve set must be
Δ ≤ 0.01 m (non-regression). Realized vs pre-registered bars per validated location.
Carved-cell ponding and water-surface-vs-deck check (the existing postanalysis script's
check, on your variant).

## Non-negotiables

- **Zero invented clearances.** The register's entire value is that it contains no invented
  depths; your augmented CSV keeps that property. If the only number for a location is a
  flood depth, the location is not carved from it.
- Read-only on: `src/jaladhar/**` (import only), the frozen parent DEM and existing variant,
  `runs/phase3_validation/`, `runs/underpass_goal/`, the existing clearance/invert CSVs.
- You own only: your clearance CSV, your orchestration + postanalysis scripts, your run
  directories. Commit only those with explicit `git add <paths>`; never `git add -A`; never
  checkout/restore/stash anything you did not create.
- Manifests written at run start, updated in place on completion (rule 6). Every number in
  your report traces to a committed script and a manifest (V9).

## Output

Report with: the classification table for all ~15 (data type, source URL, event year,
carve-grade, mapped segment/GT, dedup result), pre-registered bars vs realized per validated
location, the stated validation scope (k validated against Sept-2022 bands, j carved without
validation, m not carveable — and why, for each), and the non-regression table. Say plainly
which locations converted and which did not, and why. Do not extend the set with IRC
fallbacks to pad the count.
