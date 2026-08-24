# HANDOVER — read this first

## Current disposition — 2026-08-22, paused pending owner decisions

Phase 3 engineering integration is complete. The merged terrain, provenance, validation,
compute-budget, and replay paths pass the repository-wide CPU suite and a real-input wet GPU
smoke. The scientific Phase 3 gate is **OPEN AND NOT PASSED**. The three planned science-blocker
investigations have now completed on clean evidence branches; their findings close neither the
forcing, extent, nor depth-scope acceptance axis.

The fixed specification still requires a defensible city-wide match to the September 2022 event.
The evidence is now:

- the historical full-city replay scored **0 of 22 depth-eligible points within their observed
  bands**, with RMSE **0.7646 m** and MAE **0.696 m**;
- the terrain variant lifted GT_06's 3x3 local maximum from **0.0073 m to 0.9337 m**, but it remained
  below the observed **1.20–1.45 m** band and barely changed the other points;
- both registered Sentinel-1 routes are instrument-invalid: dual-pol GRD and the independent HyP3
  coherence/amplitude product each fail the held-out/known-water criterion. No Sentinel-derived
  city-wide CSI is admissible;
- curation has 31 event-location eligible flood attestations, but just 15 strict-depth rows whose
  stored quotation explicitly supports the numerical band. The strict depth scope remains below the
  specification's 30–50 target;
- the replay uses complete IMERG forcing, not the KSNDMC gauge field required by the specification;
  the accepted National Water Data Portal telemetry file has zero Bengaluru Urban or Rural rows in
  2021–2022. Only two event reporters lie within 60 km of the city, at 30.2 km and 42.7 km, which
  cannot defensibly create a city-wide gauge field. KSNDMC threshold alerts independently confirm
  the city storm but are lower bounds, not interval forcing;
- verified lower-road elevations remain unavailable for almost all underpasses; and
- the corrected full-replay budget gate estimates a **12.650 GPU-hour pessimistic bound** against
  the configured **10-hour** ceiling and therefore refuses a new fixed-stack 48-hour run.

Per [`SPEC.md`](SPEC.md) §8, Phases 4–8 remain unauthorized. No replay is running. The work is
paused on two explicit owner decisions, recorded below; no agent may replace either decision with an
implicit forcing approximation or a silent budget change.

### Exact checkout continuation point

- integration worktree: `/home/darshil/Desktop/sih/p3-final`, branch
  `integration/phase3-final`, HEAD `9da38a5744d0e6471b2bdac10f9317b4c392d06d`; its docs are
  intentionally uncommitted and evidence branches have not been merged into it
- Sentinel evidence worktree: `/home/darshil/Desktop/sih/p3-science`, branch
  `goal/p3-science-closure`, HEAD `b21cb7ac84fd667916f4b4a63126a1d1a9b4f932`
- forcing/GT/preflight worktree: `/home/darshil/Desktop/sih/p3-bc`, branch
  `goal/p3-forcing-gt`, HEAD `b69f9f6dd20ebc917eba6e204ab02bb9d65f3766`
- no solver, GPU replay, downloader, Luna reviewer, or history-rewrite process was observed at the
  documentation checkpoint. `nvidia-smi` could not communicate with the host driver at that point;
  re-check the runtime device before any later replay.
- the current documentation edits are intentionally uncommitted; preserve them
- copy-paste continuation prompts are in `prompts/phase3-science-blockers-reasoning-2026-08-21.md`
  and `prompts/phase3-session-transfer-claude-2026-08-21.md`

Historical and integration-smoke evidence remains separate; a one-hour smoke is not presented as
a scientific replay.

---

## 1. Problem statement — fixed

> To develop a real-time digital twin of Bengaluru's urban terrain that predicts street-level flood
> depths before they occur, using physics-informed neural surrogates and live rainfall telemetry.

Do not narrow or reword this without Darshil's explicit decision. “Street-level flood depths” is
why an unresolved underpass geometry problem is gate-blocking rather than a cosmetic limitation.

## 2. What is now established

### Engineering findings

- Terrain conditioning no longer carves staircase intermediates through retained basins.
- `filled.tif` reuse is guarded by source and output byte/grid identity; a stale or tampered cache
  regenerates.
- The clean depression/conditioning rebuild has zero retained-basin modifications and a retained
  volume inside the pre-registered 20–35 Mm3 band.
- Terrain and drain producer manifests bind the exact raster bytes and grid semantics consumed by
  the solver. D4 producer/consumer connectivity is asserted at the seam.
- GT classification and water tracing own distinct output directories; segment validation owns
  outputs separate from solver runs. Equal or nested ownership fails at startup.
- Every repaired runner owns a start-to-terminal manifest. Existing manifests are not overwritten.
- The replay hashes solver inputs, validation inputs, native IMERG cell IDs, and the interval-rate
  table actually expanded onto the GPU.
- Compute authorization uses a lifecycle-bearing RTX 4060 benchmark, a runtime device check, and
  end-to-end overhead policy. Kernel estimates are never labelled realized GPU time.
- Relative CLI paths resolve once against the selected repository.
- A real-input wet one-hour smoke completed the entire merged path, including scoring and terminal
  provenance, with active adaptive routing and mass conservation.

### Scientific findings

- The historical 48-hour baseline and elevation variant are numerically stable and mass-conserving.
  That establishes solver execution, not observational validity.
- Depth agreement is far below the stated bar: 0/22 within bands and 0.7646 m RMSE against the
  specification's approximate 0.17 m reference.
- A measured underpass carve produces a large local lift at GT_06, proving that lower-road geometry
  matters there. It does not establish city-wide terrain causation.
- Complete IMERG totals do not close forcing timing, intensity, spatial structure, or antecedent
  state. The accepted NWDP telemetry source has no city rows for the event, while KSNDMC alerts
  provide only threshold lower bounds.
- Neither dual-pol GRD nor the HyP3 coherence/amplitude reference detects known water reliably
  enough to validate flood extent. No city-wide Sentinel CSI is load-bearing.
- The curation audit distinguishes 31 event-location flood attestations from 15 auditable depth
  observations; numeric legacy bands without a retained supporting quote are not valid depth truth.

Interpretations remain **readings** unless a logged run directly tests them. In particular, the
city-wide cause of the depth deficit remains unresolved.

## 3. Read in this order

| # | file | purpose |
|---|---|---|
| 1 | [`../CLAUDE.md`](../CLAUDE.md) | binding non-negotiables, V1–V11 and R1–R8 |
| 2 | this file | current state and stop condition |
| 3 | [`OPEN-ITEMS.md`](OPEN-ITEMS.md) | only live blockers and owner decisions |
| 4 | [`phases/phase3-writeup.md`](phases/phase3-writeup.md) | complete Phase 3 evidence and verdict |
| 5 | [`SPEC.md`](SPEC.md) §8 | fixed gate criteria |
| 6 | [`BOOTSTRAP.md`](BOOTSTRAP.md) | reproducible checkout/data setup |

`docs/archive/`, `logs/`, `codex_session.md`, and `changes_codex.md` preserve earlier states. They
are historical evidence, not current instructions.

## 4. Reproduce the current engineering checks

Use Python 3.11 and the CUDA 12.4 environment described in `BOOTSTRAP.md`.

```bash
# CPU-only complete source suite; no skip flags
CUDA_VISIBLE_DEVICES="" python -m pytest -q -p no:cacheprovider

# Phase 3-owned lint/format scope
git diff --name-only --diff-filter=ACMR <phase3-base>..HEAD -- '*.py' > /tmp/p3-python.txt
xargs -a /tmp/p3-python.txt ruff check --no-cache
xargs -a /tmp/p3-python.txt black --check

# Real-input contract only; launches no simulation
CUDA_VISIBLE_DEVICES="" python scripts/run_phase3_final_preflight.py --out <fresh-run-dir>
```

The default 48-hour preflight should return `REFUSE_FULL_REPLAY` under the current 10-hour budget.
That is the configured safety behavior, not a software failure. Never raise the ceiling silently.

The final terrain rebuild command is:

```bash
python scripts/run_phase3_final_terrain.py \
  --source-repo /path/to/read-only/source-checkout \
  --scratch scratch/phase3_final_terrain \
  --audit-out runs/phase3_final_terrain_setup
```

It is a clean depression extraction and conditioning rebuild using realized upstream DEM, roads,
buildings, and BBMP drains; it is not a raw-data Phase 1 rebuild.

## 5. Immediate continuation order

1. **Forcing decision — Darshil.** Choose either (a) preserve IMERG as the historical forcing and
   state that actual KSNDMC city-gauge forcing is unavailable, which is the evidence-supported
   recommendation, or (b) explicitly authorize a nearest-gauge IDW experiment from the two
   30.2/42.7 km Ramanagar gauges. The latter is a different, low-representativeness experiment and
   cannot be labelled actual Bengaluru gauge forcing.
2. **Budget decision — Darshil.** Either keep the 10-hour refusal, or explicitly raise
   `configs/compute.yaml:budget.nightly_gpu_hours` to at least the measured 12.650-hour pessimistic
   bound (13 hours is the practical minimum) for one repaired-stack 48-hour replay.
3. Only after both decisions: merge the two evidence branches deliberately, assert the frozen GT
   CSV SHA `7b498e6b4eb43c17044835d0091f2a34eb4c7d89318ca70e116aa1af6c31a404`, re-run preflight, and
   launch no more than the authorized diagnostic replay.
4. That replay cannot close the current Sentinel, KSNDMC, strict-depth-scope, or lower-road-geometry
   acceptance failures. Its result is diagnostic evidence, not a path to silently declare a pass.
5. Update the terminal Phase 3 report from realized artifacts. Luna review and history squashing
   remain paused until Darshil explicitly resumes them.

## 6. Do not do next

- Do not call the one-hour GPU smoke a Phase 3 pass.
- Do not quote a Sentinel-derived city-wide CSI as truth.
- Do not invent or infer missing underpass elevations.
- Do not replace KSNDMC with a synthetic “corrected” storm or sharpen hourly observations into an
  invented sub-hourly burst.
- Do not start Phase 4 scenario generation or Phase 5 surrogate training.
- Do not resume Luna review, history squashing, or SHA rewriting before scientific closure.
- Do not rewrite the preserved source branches or push the integration branch without Darshil's
  instruction.

## 7. Decision boundary

The evidence phase is complete and has produced two explicit owner decisions, not implementation
work: forcing representation and budget authorization. A product/spec change still requires
Darshil's explicit decision. Neither an invalid instrument nor unavailable city-gauge forcing may
be converted into a pass by a test/configuration edit.
