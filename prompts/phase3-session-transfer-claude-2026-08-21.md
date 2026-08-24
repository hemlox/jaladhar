# Session transfer to Claude — continue exactly where Codex paused

Take over the JALADHAR executive/implementation session **from this exact state**. Do not re-plan
from scratch, re-review completed repair branches, resume history squashing, or ask Darshil to
repeat context. Darshil is tired of review iterations and explicitly authorized this session to do
the remaining fixes itself.

## User's current instruction

Temporarily pause Luna review and git-quash/SHA-rewrite work. The Sentinel, KSNDMC and GT evidence
tasks are now complete. Phase 3 is paused until Darshil decides (1) whether to preserve IMERG with
an explicit KSNDMC-unavailability finding or authorize a remote-gauge IDW experiment, and (2)
whether to raise the 10-hour GPU ceiling to authorize one repaired-stack 48-hour diagnostic replay.
Phases 4–8 remain blocked.

## First reads

Read all of these, in order:

1. `/home/darshil/Desktop/sih/p3-final/CLAUDE.md` — binding standing rules
2. `/home/darshil/Desktop/sih/clginternal/docs/handover-to-codex-2026-08-20.md` — authoritative
   fresh-session checkpoint, completed-agent outputs, exact branches, and owner decision boundary
3. `/home/darshil/Desktop/sih/p3-final/docs/HANDOVER.md` — reconciled current state
4. `/home/darshil/Desktop/sih/p3-final/docs/OPEN-ITEMS.md` — live blockers
5. `/home/darshil/Desktop/sih/p3-final/docs/phases/phase3-writeup.md`
6. `/home/darshil/Desktop/sih/p3-final/prompts/phase3-science-blockers-reasoning-2026-08-21.md`

The last file is historical now: its A/B/C/D execution instructions are superseded by the update
immediately below.

## Authoritative fresh-session update — 2026-08-22

This is a fresh session. Do not assume a prior worker, poller, downloader, GPU job, or reviewer is
still alive. Verify processes yourself. At the last checkpoint no relevant process was observed, but
`nvidia-smi` could not communicate with the host driver; re-check GPU runtime before any replay.

### Exact worktree map

| role | worktree | branch / HEAD | state |
|---|---|---|---|
| integration/docs | `/home/darshil/Desktop/sih/p3-final` | `integration/phase3-final` / `9da38a5744d0e6471b2bdac10f9317b4c392d06d` | intentionally dirty docs; evidence branches not merged |
| Sentinel evidence | `/home/darshil/Desktop/sih/p3-science` | `goal/p3-science-closure` / `b21cb7ac84fd667916f4b4a63126a1d1a9b4f932` | terminal A evidence |
| forcing, GT, preflight | `/home/darshil/Desktop/sih/p3-bc` | `goal/p3-forcing-gt` / `b69f9f6dd20ebc917eba6e204ab02bb9d65f3766` | terminal B/C/D evidence |
| original GT audit artifacts | `/home/darshil/Desktop/sih/p3-gt` | `goal/p3-gt-curation` / `de93a09` | curation manifest and incident record |

Use `/home/darshil/Desktop/sih/clginternal/.venv/bin/python`; `p3-final` has no local `.venv`.
The editable installation can resolve another worktree, so use `PYTHONPATH=src` for branch-local
runs. Preserve all uncommitted docs/prompts in `p3-final`; never reset, stash, restore, checkout,
push, rebase, or squash them. Explicit staging only; no `git add -A` or attribution footer.

### A — Sentinel evidence complete: both routes are invalid instruments

The pre-registered rule requires recall strictly above 70% at both Varthur and Bellandur while dry
false-positive rate is strictly below 1%, with threshold selection on held-out lakes.

- Dual-pol GRD, terminal `p3-science/runs/sar_dualpol_instrument_validation/manifest.json`, producer
  `e0eb63e`: `instrument_invalid`. Best VV is 42.35% Varthur / 49.61% Bellandur recall at 0.96% dry
  FPR; no family passes.
- HyP3 coherence/amplitude, terminal
  `p3-science/runs/sar_coherence_instrument_validation/manifest.json`, producer `b21cb7a`:
  `instrument_invalid`. The successful job was `196924db-310c-4158-990b-d6bcd0141a97`; product SHA
  `8a5e3c8a54ac493e0fabbed03799ba0942e711bd690fecf1012deb7843fc788c`, 357,879,467 bytes. Best
  amplitude is 44.81% / 56.52% at 0.91% dry FPR. A poller transport failure was recovered by a
  committed attach-by-job-ID path, without resubmission.

No Sentinel-derived city-wide CSI is admissible. Do not retune either failed instrument. A new
independently validated full-footprint event reference is required for this axis.

### B — forcing evidence complete: accepted source has no 2022 Bengaluru gauge field

- Accepted NWDP acquisition: `p3-bc/runs/nwdp_rainfall_acquisition/manifest.json`, producer
  `be96286`, CSV 83,495,207 bytes, SHA
  `92261aee7f521937165fbe47eff40a339838e46819f56cf6861b7b687db0f0b3`.
- Semantics audit: `p3-bc/runs/nwdp_semantics_audit/semantics_report.json`, producer `7ae3c79`.
  It finds zero Bangalore Urban/Rural rows in 2021–2022. Only Byramangala_1 (30.2 km) and
  Ramanagara_1 (42.7 km) report in the event window; they establish regional timing, not a
  defensible city field.
- KSNDMC archive: `p3-bc/runs/ksndmc_alert_archive/manifest.json`, producer `89f0b7ad`, 104
  Bengaluru-tagged threshold alerts. Varthuru 128 mm, Marathahalli 129 mm, Cholanayakanahalli
  135 mm, and Tavarekere 135.5 mm independently prove city severity, but are threshold lower bounds
  rather than interval forcing. The dated Megha Sandesha endpoint returns `[]` for the event dates.

### C — GT evidence complete: 31 flood attestations, 15 strict-depth rows

`goal/p3-gt-curation` merged into `goal/p3-forcing-gt` at `cbc5514`; `19bb5ec` froze the annotated
replay input `data/raw/groundtruth/sept2022_points_v2.csv`, SHA
`7b498e6b4eb43c17044835d0091f2a34eb4c7d89318ca70e116aa1af6c31a404`, and repointed validation
config. The terminal curation manifest is `p3-gt/runs/gt_curation_audit/manifest.json`, producer
`dcd6973`: 32 rows total, 31 event-location eligible, 15 strict-depth eligible, and
`strict_depth_below_spec_target: true`.

The scorer fails closed on unannotated CSVs and excludes `UNSTATED` legacy bands from RMSE. A replay
depth score therefore has denominator 15 and is diagnostic only. The overwritten pre-fix 33/9 run
is documented, including its irrecoverable manifest gap, in
`data/curation/gt_curation_incident_2026_08_21.yaml` at `de93a09`.

### D — contract evidence complete; replay refused

`goal/p3-forcing-gt` ran 327 CPU tests with 0 failures; Ruff and Black were clean for 20 owned
files. Terminal preflight `p3-bc/runs/phase3_final_preflight_science/manifest.json`, producer
`b69f9f6`, records 12,728,415 buffered cells, 77,317–423,487 steps, 0.596 GPU-hour kernel floor,
12.650059 GPU-hour pessimistic end-to-end cost, 10.0 configured GPU-hour ceiling, and
`REFUSE_FULL_REPLAY` before simulation.

### Pause state — wait for Darshil's two decisions

1. **Forcing:** preserve IMERG with an explicit finding that actual KSNDMC city-gauge forcing is
   unavailable (recommended), or explicitly authorize a remote-station nearest-gauge IDW experiment.
   The latter can never be labelled actual Bengaluru gauge forcing.
2. **Budget:** retain the 10-hour refusal, or explicitly raise
   `configs/compute.yaml:budget.nightly_gpu_hours` to at least 12.650 (13 hours practical minimum)
   for one repaired-stack 48-hour diagnostic replay.

Do not decide for Darshil. Do not run replay, edit forcing, or change the budget while paused. Even
an authorized replay cannot repair the failed Sentinel, strict-depth-scope, or lower-road-geometry
acceptance axes.

### If Darshil later resumes execution

Merge evidence branches deliberately into a fresh integration worktree, preserve their run evidence,
and assert the frozen v2 GT SHA before a clean preflight. Apply only the explicit forcing/budget
decision. If authorized and GPU checks pass, run exactly one manifest-first 48-hour diagnostic replay
without edits while it is in flight; score only strict-depth GT and never publish CSI from either
failed Sentinel mask. Then write terminal evidence and update the project docs. Luna review and
history squashing remain paused until Darshil explicitly resumes them.

## Original pre-completion context — superseded; do not execute

- worktree: `/home/darshil/Desktop/sih/p3-final`
- branch: `integration/phase3-final`
- HEAD: `9da38a5744d0e6471b2bdac10f9317b4c392d06d`
- latest commit: dual-polarization Sentinel acquisition and provenance
- intentional uncommitted docs:
  `README.md`, `changes_codex.md`, `codex_session.md`, `docs/BOOTSTRAP.md`,
  `docs/HANDOVER.md`, `docs/OPEN-ITEMS.md`, `docs/phases/phase3-writeup.md`, plus the two new prompt
  files. Preserve all of them; never reset/stash/restore/checkout them.
- clean detached worktree `/tmp/p3-sentinel-acq.ihgwaq` is also at `9da38a5`.
- main Python environment is
  `/home/darshil/Desktop/sih/clginternal/.venv/bin/python`; `p3-final` has no `.venv`.
- no solver, GPU replay, downloader, APK decompiler, Luna agent, or squash job is running.
- two Luna agents were closed; do not recreate them before scientific closure.
- no background terminal currently needs killing.

Use a new clean science-closure worktree/branch from `9da38a5` for code and provenance-bearing runs,
because the integration tree's documentation is intentionally dirty. Explicit staging only; no
`git add -A`, no push, no history rewrite, no generated-by/co-author footer. Shared data and
historical solver runs are read-only; every new stage owns a fresh output directory.

## What is already complete

The terrain repair, stale-fill fix, validation manifest collision fix, segment ownership/lifecycle,
replay input/provenance contract, compute accounting and wet one-hour GPU smoke have already been
implemented and reviewed. Do not reopen them without a new independent red observable.

The historical 48-hour baseline/variant were stable but scientifically poor: 0/22 depth-eligible
points within observed bands, RMSE 0.7646 m, MAE 0.696 m. The GT_06 terrain carve lifted a 3x3 local
maximum from 0.0073 m to 0.9337 m but remained below 1.20–1.45 m. These are historical findings,
not the final repaired-stack verdict.

`runs/sentinel1_dualpol_acquisition/manifest.json` is terminal completed at full SHA `9da38a5...`.
It acquired/verified the complete VV+VH measurement, annotation, calibration and noise set. Tests
for the acquisition stage passed before commit.

## Exact pause point and discoveries

### Sentinel

VV is known invalid as a city-wide truth instrument. Uncommitted exploratory VV/VH/ratio sweeps did
not appear to meet the pre-registered criterion (>70% recall on each of Varthur and Bellandur with
<1% known-dry FPR), but those sweeps are not evidence and must be attacked by a committed script.

ASF HyP3 authentication worked using the existing Earthdata credential. Implement and commit a
manifest-bearing HyP3 `INSAR_GAMMA` stage before submitting anything. Use the Sep-5/Sep-17 SLC
granules and exact job guidance recorded in the reasoning prompt. Freeze the tree while the cloud
job runs. Validate amplitude/coherence with the same independent falsifier; never tune until green.

### KSNDMC/Karnataka telemetry — new source found

The official Government of India National Water Data Portal now provides Karnataka hourly
telemetry for 2021–2025. This discovery happened immediately before pause and supersedes the stale
claim that real gauge forcing is externally unavailable.

Direct resource URL and IDs are in the reasoning prompt. A complete exploratory CSV is already at:

`/tmp/rainfall_tel_hr_karnataka_ka_2021_2025.csv`

- bytes: `83,495,207`
- SHA-256: `92261aee7f521937165fbe47eff40a339838e46819f56cf6861b7b687db0f0b3`
- rows on disk: one header plus the portal's records

Do not promote or cite that file as accepted evidence. Build a committed atomic streaming fetch and
event-subset stage, record HTTP/resource metadata and identities, then determine timestamp timezone,
increment-vs-accumulation semantics, Bengaluru station coverage, duplicates and missingness. The
CKAN `datastore_search` action works; `datastore_search_sql` is disabled. Reconcile the September
event against the independent KSNDMC Blogger alert archive. Never infer an unobserved sub-hourly
burst.

The decompiled Megha Sandesha API has a real dated observation endpoint
`http://27.34.245.77:8088/api/trgHobli/{code}/{yyyy-MM-dd}`. It returns 15-minute current records but
returned `[]` for 2022-09-04 through 2022-09-06. Do not spend another cycle treating it as the
primary archive unless a new observable says otherwise.

### Ground truth

Current CSV has 24 rows; 22 were event/date/snap/depth eligible. The full article corpus is at
`/home/darshil/Desktop/sih/clginternal/data/fetched_articles.json`. Candidate locations were found
but not curated: Majestic, Okalipuram and Kasturinagar railway underbridges; Munekolalu;
Yemalur–Bellandur road; Siddapura/Whitefield; Adarsh Estate/Bellandur; Wipro/Sarjapur; RBD Layout;
and named housing complexes. Audit old and new rows with a committed curation script. The target is
at least 30 eligible rows after date/duplicate/snap rules, not 30 lines. No invented coordinates or
depth bands.

## Execution order

1. Create clean science-closure worktree/branch and inventory exact inputs.
2. Finish Sentinel committed validation and, if needed, HyP3 acquisition/coherence evaluation.
3. Commit and run official hourly telemetry acquisition/subset/semantics/reconciliation; integrate
   only observed hourly forcing with persisted station/interpolation/rate identities.
4. Commit and run GT audit/expansion to >=30 eligible, or report an evidence-backed shortfall.
5. Freeze inputs; run focused and full CPU tests, Ruff, Black, preflight and measured budget.
6. If authorized, run the minimum scientifically sufficient GPU replay with zero tree edits while
   running.
7. Score only against validated instruments and eligible rows; issue terminal pass/no-go; update all
   current docs from machine artifacts.
8. Keep Luna review and squash work paused until Darshil explicitly resumes them after the verdict.

## Communication

Darshil wants dense, direct updates, not iterative nitpicking. Lead with what changed and what the
evidence now permits. Label FINDING versus READING. Continue autonomously through obvious next steps.
Stop only for a genuine Rule-5 external block, a credential that is actually absent, or an owner
decision that changes the submitted objective. Do not soften a dead hypothesis and do not call a
stable run a scientific pass.
