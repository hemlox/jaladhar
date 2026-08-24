# Reasoning-worker continuation — finish JALADHAR Phase 3 science blockers

> **Superseded continuation checkpoint — 2026-08-21.** A/B/C/D below have completed on separate
> clean evidence branches. Do not repeat them. Read the Claude transfer prompt for the exact paused
> decision state before doing any work.

## Superseded original objective — do not execute

The following was the former execution plan for the three scientific inputs:

1. validate or reject the Sentinel-1 event instrument;
2. acquire and integrate real September 2022 Karnataka/KSNDMC hourly gauge forcing;
3. historically expand and audit the event ground-truth set; the terminal audit instead found only
   15 strict-depth rows, below the 30–50 specification target;

then run the CPU contract, recompute a measured GPU budget, execute the minimum scientifically
necessary GPU replay if authorized by that gate, and issue an evidence-backed Phase 3 pass/no-go
verdict. Phases 4–8 remain prohibited until that verdict.

## Binding rules

Read completely before acting:

1. `/home/darshil/Desktop/sih/p3-final/CLAUDE.md`
2. `/home/darshil/Desktop/sih/p3-final/docs/HANDOVER.md`
3. `/home/darshil/Desktop/sih/p3-final/docs/OPEN-ITEMS.md`
4. `/home/darshil/Desktop/sih/p3-final/docs/phases/phase3-writeup.md`
5. `/home/darshil/Desktop/sih/p3-final/docs/SPEC.md` §8

V1–V11 and R1–R8 are binding. In particular: no fabricated data; no invented temporal sharpening;
every numeric claim comes from a committed script and terminal run manifest; validate the
instrument before scoring the model; pre-register falsifiers; findings and readings stay separate;
no tree edits during an expensive or external job; one start-to-terminal manifest owner; config is
fully resolved before compute; explicit staging only; no `git add -A`; no push; no history rewrite;
no attribution footer.

The user explicitly paused the two Luna reviewers and all squash/SHA-rewrite work. Do not resume
either until the scientific blockers and replay verdict are complete.

## Exact checkout state

- integration worktree: `/home/darshil/Desktop/sih/p3-final`
- branch: `integration/phase3-final`
- HEAD: `9da38a5744d0e6471b2bdac10f9317b4c392d06d`
- latest commit: `9da38a5 Acquire both Sentinel-1 polarizations with provenance`
- the integration tree contains intentional uncommitted documentation and these handover prompts;
  preserve them. Never stash/reset/restore/checkout them.
- clean detached acquisition worktree:
  `/tmp/p3-sentinel-acq.ihgwaq` at the same HEAD
- main Python environment:
  `/home/darshil/Desktop/sih/clginternal/.venv/bin/python`
- `p3-final` has no local `.venv`; do not run `uv run` there because it will create one.
- data are shared through the existing worktree links. Treat shared `data/processed`, historical
  solver runs, and source-branch evidence as read-only unless a new stage owns a fresh output path.
- no downloader, solver, GPU replay, Androguard/JADX, or Luna process is running.

For implementation, create a fresh clean branch/worktree from `9da38a5` (for example
`goal/p3-science-closure`) so provenance-bearing runs can pass the clean-tree gate while the
integration worktree's documentation remains uncommitted. Symlink only required ignored data
subdirectories; never make the whole repository or whole `runs/` a symlink. Merge only after tests
and evidence are terminal.

## Already completed — do not redo

- Terrain repair, validation-output collision repair, replay provenance/compute instrumentation,
  relative-path repair, and wet one-hour real-input GPU smoke are integrated before `9da38a5`.
- Historical baseline and terrain variant completed 48 h; their depth result was 0/22 within bands,
  RMSE 0.7646 m, MAE 0.696 m. These are historical findings, not a final-stack verdict.
- Dual-pol Sentinel acquisition code and tests are committed at `9da38a5`.
- `runs/sentinel1_dualpol_acquisition/manifest.json` is terminal `completed` with git SHA
  `9da38a5744d0e6471b2bdac10f9317b4c392d06d`; it records 16 required objects (VV/VH measurement,
  annotation, calibration and noise for pre/event acquisitions). Do not reacquire unless identity
  validation fails.
- The current full-window preflight previously produced a 12.650 GPU-hour pessimistic end-to-end
  bound against a configured 10-hour ceiling and correctly refused. Historical realized full runs
  were about 4.78 wall/GPU hours. Re-measure; do not bypass or silently raise the ceiling.

## Blocker A — Sentinel validity

### Realized state

The original VV-only instrument failed its known-positive validation. Exploratory, uncommitted
VV/VH/ratio sweeps also appeared unable to meet the registered criterion, but those numbers are not
evidence and must not be quoted as final. Temporary arrays may still exist:

- `/tmp/flood_20220905_vh_db.npy`
- `/tmp/pre_20220812_vh_db.npy`

Attack that exploratory reading with a committed measurement script and terminal manifest.

The fixed independent falsifier is: a candidate instrument must recover **more than 70% of both
Varthur and Bellandur known-water interiors while keeping known-dry false-positive rate below 1%**.
State the exact realized pixel scope beside the claim. Threshold search must be pre-registered or
use a held-out selection/evaluation split; do not tune and score on the same pixels.

### Coherence route available

ASF HyP3 authentication was tested from an isolated temporary environment using the existing
`EARTHDATA_TOKEN`. The account was approved and showed ample credits. Never print or log the token.
The only Sep-5/Sep-17 SLC pair found is:

- `S1A_IW_SLC__1SDV_20220905T004027_20220905T004054_044862_055BB0_01DE`
- `S1A_IW_SLC__1SDV_20220917T004027_20220917T004054_045037_056191_1A0C`

Candidate HyP3 job: `INSAR_GAMMA`, `looks=10x2`, no DEM/look vectors/incidence/displacement/wrapped
phase, `phase_filter_parameter=0.6`, `apply_water_mask=false`. Official guide:
`https://hyp3-docs.asf.alaska.edu/guides/insar_product_guide/`.

Implement the HyP3 submission/poll/download stage and its unit tests first, commit it, then run it
from a clean committed worktree with a manifest written before submission. External job ID,
parameters, granule identities, product URL/size/SHA, and terminal status belong in the manifest.
Once submitted, freeze the tree and poll without editing until the job is terminal. Preserve the
downloaded reference-amplitude and coherence rasters. Validate amplitude/coherence against the
same independent known-water/known-dry contract. If nothing passes, record `instrument_invalid`;
do not invent a CSI or loosen the falsifier.

## Blocker B — real Karnataka/KSNDMC forcing

### Source discovered immediately before pause

Official source:
`https://nwdp.nwic.gov.in/dataset/rainfall-telemetry-hourly-karnataka-department`

Resource ID: `18434c62-2ed5-4083-8060-cac00be166c9`

Direct 2021–2025 CSV:
`https://nwdp.nwic.gov.in/dataset/f45260de-893a-4d95-b189-d3800379737c/resource/18434c62-2ed5-4083-8060-cac00be166c9/download/rainfall_tel_hr_karnataka_ka_2021_2025.csv`

CKAN metadata/API:

- `https://nwdp.nwic.gov.in/api/3/action/package_show?id=f45260de-893a-4d95-b189-d3800379737c`
- `https://nwdp.nwic.gov.in/api/3/action/datastore_search?resource_id=18434c62-2ed5-4083-8060-cac00be166c9&limit=2`

The SQL action is disabled; use the direct CSV or paginated datastore API. A complete exploratory
download exists at `/tmp/rainfall_tel_hr_karnataka_ka_2021_2025.csv`:

- size: `83,495,207` bytes
- SHA-256: `92261aee7f521937165fbe47eff40a339838e46819f56cf6861b7b687db0f0b3`
- CSV header includes station, agency, district, latitude, longitude, acquisition time, and hourly
  rainfall.

This `/tmp` file is **not accepted evidence** because it was downloaded outside a committed stage.
It may be used to design tests, but the final raw file/subset must come from a manifest-bearing,
atomic, streaming acquisition that records response metadata, source URL/resource ID, byte count,
SHA-256, schema and download time.

### Independent reconciliation sources

- KSNDMC alert archive:
  `https://alerts-ksndmc.blogspot.com/2022/09/`
- dated Atom feed:
  `https://alerts-ksndmc.blogspot.com/feeds/posts/default?alt=json&published-min=2022-09-03T00:00:00%2B05:30&published-max=2022-09-08T00:00:00%2B05:30&max-results=500`
- official/current Megha Sandesha API discovered by decompiling package
  `com.moserptech.meghasandesha`:
  `http://27.34.245.77:8088/api/`
- its dated observation route is `trgHobli/{code}/{yyyy-MM-dd}`. It returns current 15-minute
  cumulative/difference records, but direct probes for 2022-09-04 through 2022-09-06 returned `[]`.
  Treat that as an exhausted fallback, not the primary source.

Implement a committed acquisition/curation stage. Before constructing forcing, establish from the
realized CSV whether “Telemetry Hourly Rainfall (mm)” is an hourly increment or accumulation, its
timezone and timestamp convention, Bengaluru station coverage for the event window, duplicate and
missing-row behavior, and coordinate validity. Reconcile station totals/timing against independent
KSNDMC threshold alerts; differences are findings, not values to overwrite.

Use only observed hourly values. Spatial interpolation may be a declared hydrologic method, but do
not sharpen them into invented 15/30-minute pulses or rescale them to a news total. The exact
station set, raw rows, interpolation weights, target interval table and final tensor-expanded rate
identity must be persisted and asserted at the replay seam.

## Blocker C — ground-truth sufficiency

Current source:
`data/raw/groundtruth/sept2022_points.csv` (24 rows; historical gate yielded 22 event/date/snap/depth
eligible). The specification target is 30–50. Do not pad it.

The full local article corpus is at:
`/home/darshil/Desktop/sih/clginternal/data/fetched_articles.json`

Promising uncurated mentions found before pause include Majestic railway underbridge, Okalipuram
railway underbridge, Kasturinagar railway underbridge, Munekolalu, Yemalur–Bellandur road,
Siddapura/Whitefield, Adarsh Estate/Bellandur, Wipro/Sarjapur, RBD Layout and several named housing
complexes. These are leads, not rows.

Create a committed curation/audit script and run manifest. Audit all existing rows for source quote,
event date, duplicate location, coordinate/source match, depth-cue-to-band derivation and road snap.
For every addition persist source URL, exact short quote, publication/retrieval date, observation
date, named location, independently verified coordinates and method/source, depth cue, band mapping,
confidence, mechanism notes and road-snap distance. Red-test duplicate/date/snap exclusions. The
closure is at least 30 **eligible** depth rows after exclusions, not 30 CSV lines. If sources support
fewer, stop with the realized count; never manufacture coordinates or bands.

## Sequence after A/B/C

1. Freeze the input artifacts and producer manifests.
2. Run focused tests, the complete CPU suite with no skip flags, Ruff and Black over every owned
   Python file. Every skip must be named V7 debt; zero failures.
3. Run the no-simulation preflight against the exact final inputs. Resolve config at startup and
   print cells × step band × scenarios → GPU-hour range. Re-measure the benchmark/overhead if its
   pin is stale. Never label a kernel estimate realized.
4. If the configured gate authorizes, launch the minimum replay that answers the gate. Once it
   starts, edit nothing. Monitor manifest, GPU identity, Courant, mass, wall time and output paths.
5. Score only with validated instruments and eligible GT. Report solver-vs-observation separately
   from any future surrogate error. Label every statement FINDING or READING and name unclosed axes.
6. Write terminal manifests and a concise final Phase 3 report. Update README, HANDOVER, OPEN-ITEMS
   and phase3-writeup from artifacts, not anticipated results.

## Verifiable completion contract

- Sentinel acquisition/product and validity manifests terminal, with immutable raw/product hashes,
  realized scope, pre-registered criterion and red mutations.
- Government hourly telemetry raw/subset/forcing identities terminal and reproducible; no synthetic
  temporal construction; alert reconciliation saved.
- GT curation manifest terminal and at least 30 eligible rows, or an explicit evidence-backed stop
  if the corpus cannot support that count.
- Clean-tree CPU suite zero failures; lint/format clean for owned files.
- Final preflight records measured compute band and an authorization/refusal before GPU allocation.
- Any GPU replay writes `running` before compute and completes/fails in place; exact inputs,
  allocation, realized wall/GPU accounting, timestep schedule, mass/Courant and outputs recorded.
- Final verdict follows the fixed gate. A stable run alone is not a pass.
- No Phase 4/5 work, no Luna review, no squash, no push, and no history rewrite during this goal.

Lead with the outcome at each checkpoint and continue autonomously until the scientific verdict or
a genuine Rule-5 external stop.
