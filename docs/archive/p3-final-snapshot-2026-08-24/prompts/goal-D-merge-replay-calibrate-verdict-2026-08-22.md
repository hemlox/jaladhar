# Goal D — Merge, replays, calibration, terminal Phase 3 verdict (GPU authorized, runs AFTER A/B/C)

**Preconditions:** Goals A, B, C have each posted terminal completion summaries. Read them, plus
`prompts/phase3-roadblock-resolution-2026-08-22.md` in full, `CLAUDE.md`, `docs/HANDOVER.md`.
All owner decisions are recorded in the resolution plan (delegated 2026-08-22): forcing (c) with
fallback, budget 13.0, gate rubric signed, falsifier corrections authorized. Do not re-open them.

Darshil has confirmed the machine can run **13 h continuously per day**; the ceiling is per
authorized run, not a per-night scheduling constraint.

## Sequence — strictly in order, single agent, nothing else touches the tree

1. **Fresh integration worktree** from `9da38a5`. Deliberately merge, preserving all run
   evidence and source branches: `goal/p3-science-closure` (`b21cb7a`), `goal/p3-forcing-gt`
   (`b69f9f6`), then the three new goal branches (A: criterion v2 + extent reference if green;
   B: anchored forcing or fallback finding; C: GT v3). Inspect merge bases first; resolve
   conflicts by hand; explicit staging only; no `git add -A`; no push; no history rewrite.
2. **Budget commit:** `configs/compute.yaml:budget.nightly_gpu_hours: 10.0 -> 13.0`, its own
   commit, message citing `prompts/phase3-roadblock-resolution-2026-08-22.md` (owner-delegated
   decision 2). Do not change the overhead policy ceiling (3.874) — it is doing its job.
3. **Assert inputs:** GT v3 SHA from Goal C's summary (fail closed on mismatch); forcing artifact
   identity chain from Goal B; criterion v2 artifacts from Goal A if green. Full CPU suite, no
   skip flags, zero failures; Ruff/Black clean on owned files; every remaining skip named V7 debt.
4. **GPU runtime check:** `nvidia-smi` could not reach the driver at the last checkpoint. Verify
   device, driver, VRAM before any authorization; if broken, stop and report — that is a real
   Rule-5 block, the only kind that pauses this goal.
5. **Clean preflight** from the merged tree (never reuse the old manifest as a pass). Expect
   authorization under the 13.0 ceiling.
6. **Replay #1 — stack isolation.** Repaired stack + raw IMERG + dry start (matching the
   historical baseline's forcing and initial condition exactly). Purpose: measure what the
   engineering repair alone did to the score. Manifest written at start; zero tree edits while
   any replay runs.
7. **Replay #2 — final configuration.** Repaired stack + Goal B's forcing (anchored, or raw
   IMERG if B's fallback fired) + antecedent storage at spill level (declared per the on-disk
   overflow evidence — resolution plan §E.5; record the choice in the manifest as a modelling
   decision, not a tuned knob). Optional attribution run (anchored + dry start) ONLY if #2's
   result is surprising enough that the diagnosis axis cannot be written without separating
   forcing from antecedent — name the trigger in the manifest if used.
8. **Calibration — SPEC §8's own loop, never yet executed.** Tiled autograd per the VRAM
   discipline (8 GB device). Parameters: Manning's n per land-use class, drain sink capacity.
   Fit against the training split of GT v3 (+ Goal A's validated extent strata if green);
   train/holdout split fixed by a committed seed BEFORE any scoring; holdout never touched
   during fitting. Bounded budget (state epochs/tiles up front). **Fallback:** if it will not
   converge inside the budget, score replay #2 uncalibrated with "uncalibrated" beside every
   number — the gate still terminates.
9. **Scored run:** one full-city replay with calibrated parameters (skip if fallback fired —
   then #2 is the scored run). Score ONLY: strict-depth GT v3 holdout (bands), points axis
   (BBMP 385–399 + 31 attestations, 50 m window, null model per resolution plan §D — N random
   same-domain points through the identical window; FAR at segment level as a stated upper
   bound), stratified extent CSI only if criterion v2 validated (dense-urban stratum excluded
   and stated). Stratify depth by underpass/surface. Never emit a CSI from either v1 instrument.
10. **Mechanical verdict** against the signed rubric (resolution plan §F): points hit rate ≥ 60%
    with ≥ 3× null lift; ≥ 40% of holdout strict rows in band; extent as above; diagnosis axis
    mandatory — two error budgets separated, every unclosed axis named (V11: anchored ≠ gauge
    field; dense-urban extent; underpass geometry; strict-depth count if < 30). GO or NO-GO
    exactly as the rubric computes it — no post-hoc threshold edits, no softening, no "stable
    run = pass".
11. **Terminal docs from realized artifacts only:** `runs/phase3_final/REPORT.md`, then update
    `README.md`, `docs/HANDOVER.md`, `docs/OPEN-ITEMS.md`, `docs/phases/phase3-writeup.md`.
    Luna review and history squashing stay paused. Phases 4–8 unlock only on a rubric GO, and
    Phase 4's first act is the mandatory measured per-scenario cost estimate (resolution plan
    feasibility note).

## GPU accounting

Expected realized: ~4.8 h (#1) + ~4.8 h (#2) + calibration tiles (~2–6 h) + ~4.8 h scored run
≈ 17–21 GPU-h total across 2 days at ≤13 h/day. Manifest-first on every run; realized wall/GPU
accounting reconciled; kernel estimates never labelled realized.

## Completion contract

Terminal manifests for every stage; the rubric table with each axis PASS/FAIL and its realized
numbers with denominators; both error budgets; the V11 unclosed-axes list; updated docs; a
10-line executive summary for Darshil leading with the verdict.
