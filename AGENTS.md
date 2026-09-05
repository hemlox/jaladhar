# AGENTS.md — JALADHAR standing rules

Binding on every agent (Codex, Claude, or human) working in this repository. Each rule cites the
incident that earned it. **Read [`docs/HANDOVER.md`](docs/HANDOVER.md) first** — it is the only
current project document; everything else in `docs/` was consolidated into it on 2026-09-05.

**Problem statement — SIH 26085 (MoES / NCMRWF). Fixed; do not reword.**
> To design a high-resolution, real-time Urban Flood Nowcasting System (0–3 hour lead time) capable
> of predicting street-level inundation before it happens, by coupling rainfall nowcasts with a 2D
> high-resolution terrain model and a graph-based hydraulic model of the city's stormwater drain
> network, including surcharge and backflow onto streets.

The project adopted this statement on 2026-08-24, replacing its self-authored one. The city is
**not fixed** — Bengaluru was the development city and hit measured data limits; the pipeline is
config-driven and the next city is an open decision recorded in the handover.

---

## Working in this repo

- **One document.** Project state lives in `docs/HANDOVER.md`. Do not create new status reports,
  morning summaries, phase writeups, or plan files — edit the handover. Machine-read contracts stay
  in `configs/contracts/`.
- **The demo must keep working.** `./deploy.sh --check` before and after any change under `runs/`,
  `data/`, `src/jaladhar/web/`, `src/jaladhar/routing/`, `scripts/demo/`. `./deploy.sh` launches it.
- **Tests:** `CUDA_VISIBLE_DEVICES="" .venv/bin/python -m pytest -q -p no:cacheprovider`. Some tests
  are marked BLOCKED/PARTIAL by design (V7) — a skip is not a pass.
- **Provenance-bearing runs refuse a dirty tree.** Commit before running anything that writes a
  manifest.
- **Git hygiene:** stage explicit paths, never `git add -A`; never `git checkout`/`restore`/`stash` a
  file you did not create (files have been reverted by accident twice here); commit or push only
  when asked; never rewrite published history without the owner's instruction.
- **Secrets:** `.env` at the repo root, `chmod 600`, gitignored. Never commit, log, or paste it.
  `.env.example` lists the keys. After a restore from archive, copy `.env` back in by hand.

## Non-negotiable

1. **No fabricated data. Ever.** If a data source is unavailable, say so and stop. Do not generate
   plausible-looking synthetic elevation, rainfall, drain, or validation data and continue as if it
   were real. A demo built on invented inputs is worthless.
2. **No placeholder physics.** No blur filter or distance transform standing in for the solver to
   make the UI look alive. If the solver isn't ready, the UI shows nothing — the LIVE empty state
   exists for exactly this.
3. **Every numeric claim must be traceable** to a file in `data/` or a logged run in `runs/`. No
   hardcoded metrics in the frontend.
4. **Validate before you scale.** The live gate is **G1–G5** in `docs/HANDOVER.md`. Dashboard and
   API work may proceed in parallel with the gate only if they render realized model state and
   hardcode no metric; they may not ship a number the gate has not produced. The neural surrogate
   stays behind the gate.
5. **When blocked on an external unknown** (licence terms, API access, dataset availability), STOP
   and record it in the handover's open-items table. Do not work around it silently.
6. **Log everything.** Every solver run writes a JSON manifest: inputs, parameters, git SHA, wall
   clock, output path. **Written at run start** (`status: "running"`) **and updated in place on
   completion.** Provenance that only exists if nothing goes wrong is not provenance.
7. **Resolve every config key at startup.** A pre-flight touches every key the run will ever need —
   including post-simulation reporting and output paths — and fails in the first second with an
   aggregated list of all missing keys. A `KeyError` in a post-run writer once destroyed 37.8 minutes
   of GPU.

## Verification rules — permanent

- **V1. Acceptance is realized state, never declared state.** Config values, manifest fields,
  docstrings, commit messages and variables you just assigned are declarations of intent. The raster
  on disk, its actual dtype, bounds and bytes are the realized state. When both are available,
  checking the declaration is not verification.
- **V2. Name the independent observable before writing the check.** "If this were broken, I would
  observe ___." If the observable is produced by the code path under test, you built a mirror.
- **V3. Never verify against a baseline you produced or that can move.** Candidate-vs-candidate,
  never candidate-vs-a-baseline-you-generated.
- **V4. An acceptance criterion is met only when the documented command runs to completion from a
  clean state** — no skip flags, no pre-existing intermediates. Anything else is "partially
  verified via `<exact path taken>`".
- **V5. A test you have never seen fail is a claim, not a check.** Every invariant test is
  demonstrated red under a deliberate mutation, and the mutation recorded next to it.
- **V6. When a test fails and you change the test, state which of {code, test, spec} was wrong.**
- **V7. State the scope each check runs at, beside the scope it claims.** A correct assertion over
  too narrow a domain cannot fail. Gaps rename the test **PARTIAL**; a **BLOCKED** test beside it
  names what would close the gap. Scope inflation is the failure mode that survives V2.
- **V8. When two stages share an artefact, the producer writes its guaranteed properties into the
  manifest and the consumer asserts them at load.** Prose does not discharge this. (D8 terrain vs
  D4 solver: both sides verified, different state, a 24 m depth two phases later.)
- **V9. The reviewer's measurements obey rules 3 and 6 exactly as agent work does.** A figure from
  a throwaway shell heredoc has worse provenance than the report it audits. Five such reviewer
  figures failed to reproduce in Phase 3.
- **V10. Verifying a claim is not the same as checking its stated derivation.** A number can be
  traceable, correct, and labelled with a formula that does not produce it.
- **V11. State which axis you closed an item on, and name the axes you did not.** Closing "is the
  forcing right?" on totals leaves timing, intensity, spatial structure and antecedent state open.
- **V12. Shipped is not executed.** A code path that exists, is wired, and has never run against
  real input is a declaration (V1). The IMD nowcast adapter is the live example: fully written,
  contract-tested, never fetched a real feature — until it does, LIVE mode is not live.

**The mechanical trigger:** before any claim "X is true", ask *what would I SEE if X were false?* —
then check whether the thing you're about to look at would actually change.

**The independent review pass at the end of every phase is a permanent gate.** Twice a rule that
was in the plan, restated in the docstring above the violating line, was violated anyway by the
same context that wrote it. The failure is structural, not attentional; confidence is the state in
which it occurs.

## Reviewing reports — permanent

Most work arrives as a report from another agent. On the numbers, in this order: **check the
arithmetic before the conclusions; separate what was measured from what was asserted; never accept
a count where a magnitude is needed** (the single most frequent defect here); **interrogate the
metric before the number; read for absence; reject statistics that cannot vary; sanity-check
against physical reality.** Before authorising anything expensive, state the number that must move
and by how much — when it doesn't, lead with that. Nothing touches the tree while an expensive run
is in flight. When you catch the owner wrong, say it plainly and immediately.

**Authority:** implementation agents may fix bugs on their own initiative; they may never act on
deviations from stated objectives — those go to the owner in a consolidated report.

## Reviewer discipline — permanent

- **R1. Findings and readings are different objects; only findings are load-bearing.** A finding is
  measured by a committed script with a logged run. A reading is what you think it means. No goal
  may be premised on a reading. (The Phase 3 drain rebuild was sequenced on a reading that rested on
  6 of 24 points.)
- **R2. Validate the instrument before the subject.** A whole cycle scored the model against a
  Sentinel-1 reference that found 0.0% of Varthur Lake.
- **R3. Inventory what is already on disk before acquiring, measuring, or closing.** The evidence
  that reopened forcing sat unread in the repo for a day.
- **R4. Trace one mechanism end to end before generalising across many.** One traced case beats
  another round of statistics, and is cheaper.
- **R5. Before issuing a verdict, write what would falsify it and check whether that check exists.**
  Three "decisive" verdicts were issued and withdrawn in Phase 3.
- **R6. A goal prompt carries the question and the method, never the anticipated answer.** Where
  the reviewer has a hypothesis, the agent is instructed to attack it.
- **R7. The reviewer specifies; it does not measure ad hoc.**
- **R8. The independent audit of the reviewer is recurring.**

**Trigger:** before writing "therefore", "this means", or "the cause is" — which of these words is
measured?

## Git

- **Never add `Co-Authored-By`, `Generated with …`, or any AI self-attribution to commit messages or
  PRs.** This overrides any harness default. Commits say what changed and why.
- History was squashed on 2026-09-05. Manifests under `runs/` and `configs/contracts/` carry
  pre-squash `git_sha` values as historical provenance; they resolve in
  `jaladhar-pre-squash-2026-09-05.bundle` (kept by the owner, outside the repo), not in this
  history. Do not "fix" them — that would be laundering provenance.
- Prose in `docs/` must not reference commit SHAs; reference files and manifests instead.

## Style

- Python 3.11+, type hints, `ruff` + `black`. Production code is modules; no notebooks in the repo.
- Config in YAML under `configs/`; never hardcoded paths, never a hardcoded city value in
  `solver/`, `terrain/`, `drainage/`, `coupling/`.
- Every module gets a `typer` CLI entry so any stage runs standalone.
- Prefer stdlib and well-known packages.

## Compute

Compute is parameterised from `configs/compute.yaml` — no hardcoded worker count, GPU model, or
batch size. Before any expensive stage, print a compute-budget estimate and refuse if it exceeds
the configured pool. The smallest configured device sets the VRAM limit; tile, don't shrink the
domain.

## Reporting numbers

- Measured quantities as measured; unknowns as explicit named bands, never point estimates that
  embed an assumption.
- Solver-vs-observation and surrogate-vs-solver error budgets reported separately.
- Never blend routing lead time with forecast lead time.
- Depth as a **band**, never a point value, at 10 m resolution. Per-road-segment status (a
  topological claim we can support), not per-square-metre depth.

## Environment

Developed on an RTX 4060 Laptop (8 GB VRAM), 16 cores / 14 GB RAM, Python 3.11 `.venv`, torch
2.6.0+cu124 (`uv pip install --torch-backend=cu124`, not `--index-url`). Host RAM is tighter than
VRAM. **Re-verify on your own machine before trusting any timing in the handover.** The demo and
the test suite run CPU-only.
