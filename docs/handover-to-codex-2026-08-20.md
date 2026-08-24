# Handover to Codex / Claude — current Phase 3 checkpoint

**Updated 2026-08-22. This replaces the stale 2026-08-20 handover.** It is written for a
**fresh session**: do not assume any prior agent, background poller, GPU process, or terminal is
alive. Read [`CLAUDE.md`](../CLAUDE.md) first; its V1–V11 and R1–R8 rules are binding.

## Executive state

Phase 3 engineering repair is complete, but the scientific gate is **OPEN AND NOT PASSED**.
Phases 4–8 remain prohibited by `docs/SPEC.md` §8. The scientific-blocker work has finished and
the project is deliberately paused on two owner decisions:

1. **Forcing:** preserve IMERG while recording that an actual 2022 KSNDMC/Bengaluru gauge field is
   unavailable (**recommended**), or explicitly authorize a remote two-station nearest-gauge IDW
   experiment. The latter must never be labelled actual Bengaluru gauge forcing.
2. **Budget:** keep the 10-GPU-hour refusal, or explicitly raise
   `configs/compute.yaml:budget.nightly_gpu_hours` to at least **12.650** (13 practical minimum)
   for one repaired-stack 48-hour **diagnostic** replay.

Do not choose either for Darshil. Do not launch a replay, modify forcing, change the budget, resume
Luna reviews, or resume history squashing/SHA rewriting unless Darshil explicitly resumes it.

At the documentation checkpoint no relevant process was observed. `nvidia-smi` could not
communicate with the host driver at that time, so a future replay must re-check the runtime device
and allocation before doing anything expensive.

## Read order

1. [`CLAUDE.md`](../CLAUDE.md)
2. `/home/darshil/Desktop/sih/p3-final/docs/HANDOVER.md` — live narrative and continuation order
3. `/home/darshil/Desktop/sih/p3-final/docs/OPEN-ITEMS.md` — live blockers and owner decisions
4. `/home/darshil/Desktop/sih/p3-final/docs/phases/phase3-writeup.md`
5. `/home/darshil/Desktop/sih/p3-final/docs/SPEC.md` §8
6. `/home/darshil/Desktop/sih/p3-final/prompts/phase3-session-transfer-claude-2026-08-21.md` —
   the detailed fresh-session transfer prompt

`docs/archive/`, `codex_session.md`, and `changes_codex.md` are history, not operating
instructions.

## Exact checkout map

| role | worktree | branch / HEAD | state |
|---|---|---|---|
| Integration docs | `/home/darshil/Desktop/sih/p3-final` | `integration/phase3-final` / `9da38a5744d0e6471b2bdac10f9317b4c392d06d` | intentionally dirty docs; evidence branches not merged |
| Sentinel evidence | `/home/darshil/Desktop/sih/p3-science` | `goal/p3-science-closure` / `b21cb7ac84fd667916f4b4a63126a1d1a9b4f932` | terminal blocker A evidence |
| Forcing, GT, preflight | `/home/darshil/Desktop/sih/p3-bc` | `goal/p3-forcing-gt` / `b69f9f6dd20ebc917eba6e204ab02bb9d65f3766` | terminal blockers B/C/D evidence |
| Original GT audit artifacts | `/home/darshil/Desktop/sih/p3-gt` | `goal/p3-gt-curation` / `de93a09bede4a7192bef988eefe37d5a6038d5c2` | curation manifest and withdrawn-run incident |
| Terrain repair | `/home/darshil/Desktop/sih/p3-terrain-repair` | `goal/p3-terrain-repair` / `0b541f7be115dc78c5527a76519a90e0ce3286e1` | completed engineering repair |
| Validation repair | `/home/darshil/Desktop/sih/p3-validation-repair` | `goal/p3-validation-repair` / `e773d308beac71f8e3fc911aaca0cf1cbc5130e8` | completed engineering repair |
| Replay/provenance forensics | `/home/darshil/Desktop/sih/p3-replay-forensics` | `goal/p3-replay-forensics` / `209a72c591d6a5129867db064ae77a811f4a7b76` | completed engineering repair |

Use `/home/darshil/Desktop/sih/clginternal/.venv/bin/python`; branch-local commands need
`PYTHONPATH=src` because the editable install can otherwise resolve another worktree. Preserve the
uncommitted documentation in `p3-final`: never reset, stash, restore, checkout, rebase, or squash
it. Explicit staging only, never `git add -A`, and no attribution footer.

## Completed engineering work — do not reopen without a new independent red observable

- Terrain conditioning now protects retained basin interiors; stale or tampered `filled.tif` is
  rejected using source/output identity and regenerated. The clean rebuild retained **29.5658 Mm³**
  and changed **0** retained cells.
- Terrain/grid provenance, D4/D8 seam checks, forcing interval identity, compute accounting, and
  manifest lifecycle were repaired and mutation-tested.
- GT classification, water tracing, and segment validation now have separate non-nested output
  ownership and terminal start-to-complete/fail manifests.
- A real-input one-hour wet GPU smoke traversed the merged path. It is engineering evidence only,
  not a Phase 3 scientific pass.

The historical 48-hour baseline/variant are also not a current verdict: the historical depth score
was 0 of 22 in-band, RMSE 0.7646 m and MAE 0.696 m. GT_06 local maximum rose from 0.0073 m to
0.9337 m in the terrain variant but stayed below its observed 1.20–1.45 m band.

## Agent A — Sentinel instrument evidence: terminal and invalid

The pre-registered instrument rule was recall strictly above 70% at **both** Varthur and Bellandur
with dry false-positive rate strictly below 1%, using held-out lakes for threshold selection.

- Dual-pol GRD terminal artifact:
  `/home/darshil/Desktop/sih/p3-science/runs/sar_dualpol_instrument_validation/manifest.json`,
  producer `e0eb63e`. It is `instrument_invalid`: best VV reaches 42.35% Varthur recall and
  49.61% Bellandur recall at 0.96% dry FPR. No family passes.
- Independent HyP3 coherence/amplitude terminal artifact:
  `/home/darshil/Desktop/sih/p3-science/runs/sar_coherence_instrument_validation/manifest.json`,
  producer `b21cb7a`. It is also `instrument_invalid`: best amplitude reaches 44.81% / 56.52% at
  0.91% dry FPR. Its successful job was
  `196924db-310c-4158-990b-d6bcd0141a97`; the product is 357,879,467 bytes, SHA-256
  `8a5e3c8a54ac493e0fabbed03799ba0942e711bd690fecf1012deb7843fc788c`.

No Sentinel-derived city-wide CSI is admissible. Do not re-tune either failed instrument; this axis
needs a different independently validated full-footprint event reference.

## Agent B — forcing evidence: terminal, decision required

- Official NWDP acquisition:
  `/home/darshil/Desktop/sih/p3-bc/runs/nwdp_rainfall_acquisition/manifest.json`, producer
  `be96286`. The accepted CSV is 83,495,207 bytes, SHA-256
  `92261aee7f521937165fbe47eff40a339838e46819f56cf6861b7b687db0f0b3`.
- Semantics audit:
  `/home/darshil/Desktop/sih/p3-bc/runs/nwdp_semantics_audit/semantics_report.json`, producer
  `7ae3c79`. It finds **zero** Bangalore Urban/Rural rows in 2021–2022. Only Byramangala_1
  (30.2 km) and Ramanagara_1 (42.7 km) report during the event: useful regional timing, not a
  defensible city forcing field.
- KSNDMC archive:
  `/home/darshil/Desktop/sih/p3-bc/runs/ksndmc_alert_archive/manifest.json`, producer `89f0b7ad`.
  It contains 104 Bengaluru-tagged threshold alerts. Varthuru 128 mm, Marathahalli 129 mm,
  Cholanayakanahalli 135 mm and Tavarekere 135.5 mm establish city storm severity, but remain
  threshold-crossing lower bounds rather than interval forcing. The dated Megha Sandesha endpoint
  returned `[]` for the event dates.

The evidence supports keeping IMERG and stating the city-gauge limitation. An IDW run from the two
remote stations is only permitted after explicit owner authorization and must be described as an
experimental remote-gauge representation.

## Agent C — ground-truth audit: terminal strict-depth shortfall

`goal/p3-gt-curation` was merged into `goal/p3-forcing-gt` at `cbc5514`. Commit `19bb5ec` froze the
annotated replay input at `data/raw/groundtruth/sept2022_points_v2.csv`, SHA-256
`7b498e6b4eb43c17044835d0091f2a34eb4c7d89318ca70e116aa1af6c31a404`, and repointed validation
configuration.

The terminal curation manifest is
`/home/darshil/Desktop/sih/p3-gt/runs/gt_curation_audit/manifest.json`, producer `dcd6973`:

- 32 rows total;
- 31 event-location eligible flood attestations;
- 15 auditable strict-depth observations; and
- `strict_depth_below_spec_target: true`.

The scorer fails closed on unannotated CSVs and excludes `UNSTATED` legacy bands from RMSE. Any
future RMSE has denominator 15 and is diagnostic only; it cannot satisfy the fixed 30–50
strict-depth scope. A pre-fix bad 33/9 run was overwritten before preservation; its defect and the
unrecoverable manifest gap are truthfully recorded in
`data/curation/gt_curation_incident_2026_08_21.yaml` at `de93a09`. Do not erase or downplay it.

## Agent B/D — CPU contract and replay budget: terminal refusal

The p3-bc branch ran its full CPU suite: **327 passed, 0 failed**; Ruff and Black were clean for 20
branch-owned files. Terminal preflight:

`/home/darshil/Desktop/sih/p3-bc/runs/phase3_final_preflight_science/manifest.json`, producer
`b69f9f6`, records:

- 12,728,415 buffered cells;
- 77,317–423,487 steps;
- 0.596 GPU-hour kernel floor;
- 12.650059 GPU-hour pessimistic end-to-end estimate; and
- 10.0 configured GPU-hour ceiling, yielding `REFUSE_FULL_REPLAY` before simulation.

This is expected safety behavior. Do not bypass it in code. A new 48-hour replay is meaningful only
if Darshil explicitly raises the budget (or a newly measured valid budget anchor lowers the bound).
Even then it is diagnostic: it cannot close the Sentinel extent, strict-depth scope, unavailable
city-gauge forcing, or lower-road-geometry acceptance axes.

## What to do after Darshil decides

1. Work from a new clean integration worktree. Inspect merge bases; deliberately merge the Sentinel
   and forcing/GT/preflight evidence branches, preserving all manifests and source branches.
2. Assert the frozen GT-v2 SHA before any preflight or replay.
3. Apply only the authorized forcing representation and budget decision. Re-run the clean preflight
   from the merged code; do not use the old manifest as a pass.
4. If the budget authorizes a replay and runtime GPU checks pass, run at most one manifest-first
   48-hour diagnostic replay with no tree changes while it runs. Score strict-depth GT only and do
   not emit Sentinel CSI.
5. Produce terminal artifacts and update `README.md`, `HANDOVER.md`, `OPEN-ITEMS.md`, and the Phase
   3 writeup from realized state. The honest Phase 3 conclusion remains constrained by the axes
   above.

## Do not do

- Do not fabricate elevation, rainfall, coordinates, or depth bands.
- Do not infer a sub-hourly storm from news totals or threshold alerts.
- Do not call the smoke test, an IDW experiment, a Sentinel CSI, or N=15 RMSE a Phase 3 pass.
- Do not start Phase 4 scenario generation or Phase 5 surrogate training.
- Do not resume independent Luna reviews or git squashing until Darshil explicitly says so.
