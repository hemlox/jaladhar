# Goal B — Alert semantics, IMERG deficit, anchored forcing (CPU + network only)

**Read first:** `prompts/phase3-roadblock-resolution-2026-08-22.md` (§B and the decisions block),
then `CLAUDE.md` (V1–V11, R1–R8), `docs/HANDOVER.md`. The forcing decision is already made:
**(c) KSNDMC-alert-anchored IMERG, diagnostic-first, fallback preserve-IMERG.** Do not wait on
Darshil.

## Questions, in order

1. What exactly does a KSNDMC alert value measure — cumulative since when, in what timezone?
2. How large is the IMERG deficit at the alert stations/times (the number that must move)?
3. If material, what anchored forcing field do the measurements support?

## Worktree and ownership

- Fresh branch/worktree from `goal/p3-forcing-gt` @ `b69f9f6dd20e…` (suggest
  `goal/p3-forcing-anchor`). Python `/home/darshil/Desktop/sih/clginternal/.venv/bin/python`,
  `PYTHONPATH=src`. Explicit staging only; no `git add -A`; no footers; no push. CPU only.
- **You own:** new alert-parsing/geocoding/deficit/anchoring modules under `src/`; additions to
  `configs/forcing.yaml`; `runs/ksndmc_alert_parse/`, `runs/imerg_deficit_diagnostic/`,
  `runs/forcing_anchored/`, `runs/wayback_ksndmc_probe/`.
- **You must not touch:** `data/raw/groundtruth/**` and curation code (Goal C owns), SAR/terrain
  paths (Goal A owns), existing `runs/` (read-only), `p3-final` uncommitted docs.

## Method

1. **Committed alert parse.** Input: the frozen feed
   `data/raw/ksndmc_alerts/2022-09/alerts_feed_2022-09.json` (SHA `bbfaf932…`, terminal manifest
   `runs/ksndmc_alert_archive`). Parse titles to (station, obs_date, obs_time, cumulative_mm,
   taluk); dedup exact duplicates with a recorded rule; persist row count, station count,
   per-station sequences. The resolving session's INSPECTION says ~104 rows / ~83 stations /
   values 64.5–135.5 mm / 19 multi-sample stations — **reproduce; a mismatch is a finding.**
2. **Geocode stations.** Check the accepted NWDP CSV FIRST (R3): the acquisition at
   `runs/nwdp_rainfall_acquisition` (83.5 MB, SHA `92261aee…`) carries station/lat/lon columns
   for Karnataka telemetry — Bengaluru stations may appear there with coordinates even though
   their 2021–22 event rows are absent. Fall back to KSNDMC station directories (live or
   Wayback). Record source + confidence per station; **exclude un-geocodable stations, never
   invent coordinates.**
3. **Semantics.** Establish what the cumulative is measured since (spell start vs fixed local
   time) using: escalation sequences (e.g. Doddanekkundi 70.0@04-09 23:00 → 125.5@05-09 03:15 →
   99.0@06-09 03:30 — the reset between nights is informative), cross-station consistency, NWDP
   hourly at Byramangala_1/Ramanagara_1 for regional timing, and the two HAL Airport stations in
   the alert list vs IMD's published HAL daily totals. If irresolvable, carry both candidate
   windows as an explicit band through everything downstream.
4. **Deficit diagnostic (the decision number, pre-registered here):** per station, IMERG v07
   Final cumulative at the containing native cell over the semantics-matched window vs the alert
   value. Report the ratio distribution, per-station table, city map. **Material deficit :=
   median ratio < 0.85.** The resolving session's INSPECTION on the aggregated window CSV found
   night-1 city-mean ≈ 46 mm vs ≥ 64.5 mm measured at ~82 stations — attack the window matching
   before trusting any ratio.
5. **If material → build the anchored forcing (authorized).** Per-station factor =
   measured / IMERG over the matched window; spatial interpolation of factors by IDW (declare
   radius/power); factor → 1 outside gauge influence; sub-interval timing is IMERG's own observed
   30-min pattern — **no invented temporal structure, no rescaling to news totals.** Declare and
   sensitivity-note any factor cap where IMERG ≈ 0. Persist the full identity chain (station
   set, raw rows, weights, interval-rate table, tensor-expansion hash) per the replay-seam
   contract. Label the artifact `KSNDMC-alert-anchored IMERG` everywhere; it must never be
   called actual Bengaluru gauge forcing. **If NOT material:** say so in the first sentence,
   fall back to preserve-IMERG + finding, skip the builder.
6. **Timeboxed side-probe (≤ half a day):** Wayback captures of ksndmc.org daily gauge-wise
   rainfall reports for 2022-09-04..06. Hit → upgraded anchors (daily totals per station);
   miss → close the avenue with a logged probe manifest.

## Attack block

The resolving session believes the deficit is material and forcing magnitude is a first-order
cause of the historical 20/22-below-band underprediction. **Attack it:** the alert set is
left-censored (only ≥65 mm stations alert), IMERG cells are ~10 km while gauges are points, and
the semantics window is unproven. If the honest matched-window ratio is ≈ 1, the hypothesis is
dead — lead with that.

## Completion contract

Terminal manifests for parse/geocode/diagnostic/(builder)/probe; the ratio distribution with
denominators and window band; the anchored artifact + identity chain (or the explicit fallback
finding); FINDING/READING labels; 10-line summary naming closed and still-open axes (V11).
