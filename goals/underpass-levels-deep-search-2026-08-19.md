# GOAL — Deep per-location vertical-level search for Bengaluru underpasses (parallel session 2)

**Agent:** your choice (opencode session). **Repo:** /home/darshil/Desktop/sih/clginternal.
**Precondition:** this is a RESEARCH session. You own only the paths listed under
"Your outputs" below. Everything else in the repo is read-only for you. First
thing: `git status` — if anything under `src/`, `data/interim/terrain/`,
`runs/underpass_*`, `runs/phase3_validation/`, `configs/` shows as
modified/uncommitted, STOP and report; do not commit, do not revert.

## Context (verified by a parallel session on 2026-08-19, do not re-derive)

The 50-location underpass register is `data/raw/underpass_search/search_register.csv`.
A 200-query news sweep (commit 65f7679, `docs/reference/underpass-levels-search-2026-08-19.md`)
found per-location vertical data for ~15 of 50 locations. A verification pass
re-opened every source URL and confirmed exactly **one** new carve-grade figure
among those 15 — Madiwala Underpass vertical clearance **5.5 m** (existing
structure, built 2010; Bangalore Mirror 2022-07-24, BBMP CE B.S. Prahalad quote).
Everything else was flood-depth-only, status-only, a planned structure, a
barrier height, or a length/width (not a clearance).

The carve pipeline (committed at a93c58d) derives `invert_z = z_deck − clearance − deck_structural_depth`
per register segment and carves the corridor into a variant DEM. A location is
**carve-grade** iff it has a MEASURED clearance of the existing structure, an
engineered dimension (vent width/height), or a relative vertical ("x m below
road level" maps to clearance + deck_depth, NOT clearance) AND a matching
register segment geometry. Flood-depth observations are NOT clearances and can
never be carved from. A planned/under-construction structure cannot be carved
into a Sept-2022 event DEM (anachronism).

Your job: close the gaps that keep the other 49 locations non-carve-grade, by
going deeper than RSS title sweeps.

## Question

For each of the 50 named underpasses: is there a MEASURED clearance, an
engineered dimension, or a relative vertical anywhere in (a) in-repo documents,
(b) OCR-able scanned audits, (c) DPRs/tenders, (d) street-level imagery
signage, or (e) news archives — and if so, what is the number, its exact source
sentence, and its event year?

## Method (ordered by information value per unit cost)

**0. Read the in-repo corpus first (R3 — inventory what is already on disk):**
   - `docs/reference/underpass-sources-audit-2026-08-18.md` (the previous audit)
   - `docs/reference/underpass-levels-search-2026-08-19.md`
   - the four 2026-08-19 agent audits (`data/raw/underpass_search/*_notes.csv` + `*_manifest.json`)
   - `data/fetched_articles.json` (153 fetched articles, full text — grep for
     underpass/clearance/height/vertical/metres)
   - `data/raw/underpass_search/results/cag_disaster_mgmt_full.pdf` — CAG
     Karnataka Disaster Management Report 08/2025 (10 MB). `pdftotext` the whole
     file and grep for underpass/clearance/vertical clearance/height/vent.
   - `data/raw/underpass_search/results/suranjan_das_tender.pdf` — BBMP 2017
     grade-separator tender (Suranjandas Rd–Old Madras Rd). "Key Parameters
     fixed by the BBMP" in such tenders normally include the vertical clearance
     and span values. Extract them verbatim.
   - `runs/underpass_measured/geometry_match.csv` — the 50 locations matched to
     nearest register osm_id, distance, carve status (READ-ONLY for you).

**1. OCR the OpenCity BBMP audit PDFs (single highest-value artifact):**
   Dataset: https://data.opencity.in/dataset/bbmp-underpasses-audit-reports-2023
   — resources "BBMP Audit Report on 14 Underpasses" and "Report on KR Circle
   Underpass Flooding". Both are scanned images (JPEG pages, no text layer; the
   KR Circle report is in Kannada). OCR with tesseract (`-l eng+kan`) if
   installed, else `pip install` easyocr or paddleocr (CPU only — see the
   device rule below). Extract EVERY per-underpass dimension: width, height,
   vertical clearance, drain size, pump capacity, approach levels. A
   14-underpass audit closes several gaps at once.

**2. Per-location deep pass (for every location unresolved after steps 0–1):**
   - **Street-level signage:** Google Street View at the underpass entrance
     (view from each approach road). Clearance signage reads "max height
     X.X m". Record the sign value, the viewing position, and the URL.
   - **OSM tags + history:** for the register segment osm_ids in
     `geometry_match.csv`, query `maxheight`, `maxheight:physical`, `hgv`
     restrictions and tag history via osm.org API or overpass-api.de. A signed
     limit is still a measurement (record `signed_limit_bias`).
   - **Archives:** Google News RSS (`https://news.google.com/rss/search?q=<enc>&hl=en-IN&gl=IN&ceid=IN:en`)
     and websearch with site: queries for `<name> underpass` + "vertical
     clearance" / "height restriction" / "width and height" / "vent" / "RUB"
     / "below road level". Follow the pre-2010 era too (Mekhri 2000, Magadi
     2009, KR Circle 2009-11, Millers Road) — Bangalore Mirror ran an
     "Underpass story" series and TOI covered each construction.
   - **Tenders/DPRs:** BBMP/BDA/KRCL/SWR/RITES/RITES completion reports on
     opencity.in and data.opencity.in; eproc.karnataka.gov.in (login-gated —
     record as BLOCKED_LOGIN, do not work around).
   - **IRC standards** (law.resource.org, IRC:54 1974, IRC:86 1983, IRC:5 2015)
     are context only — they give the *default* clearance, never a per-location
     measurement. A location whose only figure is an IRC default is NOT
     carve-grade.

**3. Classify every one of the 50** into exactly one of:
   carve-grade (measured clearance / dimension / relative vertical + register
   geometry exists) with the number; or not-carve-grade with the single reason:
   flood-depth-only / planned-structure / barrier-height / status-only /
   no-matching-geometry / nothing-found. Every flood depth carries its event
   date; depths from events other than Sept 2022 are recorded but never scored
   against the 2022 run.

**4. For every carve-grade candidate, state:** register osm_id (from
   `geometry_match.csv`), nearest GT point + Sept-2022 depth band if any, and
   the explicit mapping into `invert_z = z_deck − clearance − deck_structural_depth`
   ("6 m below road level" maps to clearance + deck_depth = 6.0 m, not to
   clearance). If the number would be an IRC fallback rather than a measured
   value, say so plainly — no measured clearance means no carve.

## Non-negotiables

- **Zero fabricated numbers.** Every figure = verbatim quote + URL + publication
  date + event year. An article you could not fetch is NOT_REVERIFIED, never
  paraphrased into a number.
- **Every measurement you make** (OCR extractions, contour reads, tag queries)
  is produced by a committed script and logged with a manifest (git SHA, inputs,
  status updated in place — repo rules 6 and V9).
- **Read-only everywhere except your own outputs.** You may import
  `src/jaladhar/**` but never edit it. Never `git add -A`; never
  `checkout/restore/stash` anything you did not create.
- **Device:** CPU-only. A 48 h solver run may be in flight in the other
  session — check `nvidia-smi` before anything; you must never allocate GPU
  memory or queue GPU work. No torch CUDA, no benchmarks.
- **Do not treat your findings as part of the run in flight.** The in-flight
  carve run is frozen at its pre-registered set. Your results feed the NEXT
  carve cycle (a follow-up goal), never a mid-run revision.

## Your outputs (files you own, commit with explicit `git add <paths>`)

- `data/raw/underpass_search/deep/search_results.csv` + `manifest.json`
- `data/raw/underpass_search/deep/bbmp_audit_ocr/` (OCR text + extracted
  dimension table + the OCR script)
- `docs/reference/underpass-levels-deep-search-2026-08-19.md` — the report:
  the 50-row classification table, per-location carve-grade recommendations,
  OCR extraction summary, and a BLOCKED list (login/paywall/token) with the
  exact blocker named.

## Output section of your final report

1. The 50-row table: location | figure | type (clearance/dimension/relative/
   depth/status) | exact quote | URL | pub date | event year | confidence.
2. Carve-grade recommendations: osm_id, number, mapping into the invert
   formula, GT mapping, Sept-2022 band availability.
3. What OCR yielded from the BBMP 14-underpass audit (dimension table).
4. BLOCKED items with exact blockers; RTI items to log in OPEN-ITEMS.md
   (BBMP Road Infrastructure Dept, 41-underpass audit 2023, vertical clearance
   drawings — flag as formal-request items, do not work around).
5. Say plainly which locations you converted to carve-grade and which you could
   not, and why. Do not pad the count with IRC fallbacks or planned structures.