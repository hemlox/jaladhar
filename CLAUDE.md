# CLAUDE.md — JALADHAR standing rules

Bengaluru urban flood digital twin. Full spec in [`PROMPT.md`](PROMPT.md); unresolved external unknowns in [`OPEN-ITEMS.md`](OPEN-ITEMS.md).

**Problem statement (fixed, do not reword):**
> To develop a real-time digital twin of Bengaluru's urban terrain that predicts street-level flood depths before they occur, using physics-informed neural surrogates and live rainfall telemetry.

---

## Non-negotiable

1. **No fabricated data. Ever.** If a data source is unavailable, say so and stop. Do not generate plausible-looking synthetic elevation, rainfall, or validation data and continue as if it were real. A demo built on invented inputs is worthless to us.
2. **No placeholder physics.** Do not write a function that "approximates" flooding with a blur filter or a distance transform to make the UI look alive. If the solver isn't ready, the UI shows nothing.
3. **Every numeric claim must be traceable** to a file in `data/` or a logged run in `runs/`. No hardcoded metrics in the frontend.
4. **Validate before you scale.** Phase 3 is a hard go/no-go gate. Do not build the frontend, the surrogate, or anything cosmetic before it passes.
5. **When blocked on an external unknown** (licence terms, API access, dataset availability), STOP and report it in `OPEN-ITEMS.md`. Do not work around it silently.
6. **Log everything.** Every solver run writes a JSON manifest: inputs, parameters, git SHA, wall clock, output path. Reproducibility is a grading criterion for us, not just good practice.

## Verification rules — permanent

Added after the Phase 1 review pass caught three vacuous tests, a false "acceptance passes" claim, and a stale-manifest bug that a proper clean-state run would have surfaced immediately. These apply to every phase from here — Phase 2 through 8 — and to every future session on this repo, without being reminded.

- **V1. Acceptance is realized state, never declared state.** A config value, a manifest field, a docstring, a commit message, and a variable you just assigned are all DECLARATIONS OF INTENT. The raster on disk, the file's actual dtype, the actual bounds, the actual bytes are the REALIZED STATE. When both are available, checking the declaration is not verification. If only the declaration is available, that is itself the finding — report it and build the path that exposes the realized state.

- **V2. Name the independent observable before writing the check.** Before writing any test or verification step, state in one line: "if this were broken, I would observe ___." If the thing you would observe is produced by the same code path you are checking, you have built a mirror, not a check. Find a different observable or say you cannot.

- **V3. Never verify against a baseline you produced or that can move.** Candidate-vs-candidate, never candidate-vs-a-baseline-you-generated. This is the KSNDMC `archiveDate` moving-baseline rule, generalised. A manifest you wrote, an intermediate you cached, a fixture you built from the code under test — all disqualified as baselines.

- **V4. An acceptance criterion is met only when the documented command runs to completion from a clean state** — no skip flags, no pre-existing intermediates, no manual setup, no "I ran the stages individually." Any other path is reported as "partially verified via `<exact path taken>`", never as passing.

- **V5. A test you have never seen fail is a claim, not a check.** Every invariant test must be demonstrated red at least once under a deliberate mutation, and the mutation recorded next to it.

- **V6. When a test fails and you change the test, state explicitly which of {code, test, spec} was wrong, and why.** Editing a test until it agrees with the code is how a spec violation becomes permanent and invisible.

**The mechanical trigger, since noticing you're about to accept a proxy is the hard part, not knowing the rule:** before any claim of the form "X is true" — when writing a test, when writing an acceptance check, and when reporting a phase complete — ask *what would I SEE if X were false?*, then check whether the thing you're about to look at would actually change. If it would not change, you're looking at a proxy. Stop and find the observable that would.

**The independent multi-agent review pass is a permanent gate at the end of every phase**, not a one-off. Its value comes specifically from the reviewers having no stake in the code passing — that independence is the actual mechanism, and it is not reproducible from inside the context that wrote the code, including by trying harder.

## Style

- Python 3.11+, type hints, `ruff` + `black`. No notebooks in the repo except under `notebooks/` for exploration — production code is modules.
- Config in YAML under `configs/`, never hardcoded paths.
- Every module gets a `if __name__ == "__main__"` CLI entry via `typer` so any stage can be run standalone.
- Prefer stdlib and well-known packages. Do not add a dependency to save five lines.

## Compute

Compute is **not fixed**. Do not hardcode a worker count, a GPU model, or a batch size anywhere — everything that touches compute is parameterised from `configs/compute.yaml`. Adding, removing, or upgrading a worker must be a config change, never a code change.

Before any expensive stage, emit a **compute budget estimate** (cells × timesteps × scenarios → GPU-hours against the configured pool) and print it. If a stage would exceed the budget, refuse to start and report.

**VRAM discipline:** the smallest configured device sets the limit. Autodetect available VRAM at startup and pick tile size from it. Forward-only simulation is cheap in memory; the two things that actually blow up are autograd through thousands of solver timesteps during calibration, and U-Net training on very large rasters. Handle both by tiling, not by shrinking the domain.

## Reporting numbers

- **Report measured quantities as measured, and unknowns as bands.** Where a figure depends on something not yet determined (wall clock per scenario depends on timesteps/hour, which depends on CFL, which depends on `h_max`), carry the unknown as an explicit named parameter producing a range. Never present a point estimate that silently embeds an assumption.
- **Report the two error budgets separately** — solver-vs-observation and surrogate-vs-solver. One combined number hides which part is broken.
- **Never blend routing lead time with forecast lead time** (§11). Water already on the ground is near-certain; forecast rain is conditional and the condition must be stated.
- Report depth as a **band, not a number**, at ground-truth points.
- Report per-road-segment flood status (a topological claim we can support), not per-square-metre depth (a metric claim at 10 m resolution that we cannot).

## Environment (verified 2026-08-14)

| | |
|---|---|
| Dev GPU | RTX 4060 Laptop, 8188 MiB, driver 550.163.01 (CUDA 12.4) |
| Host | 16 cores, 14 GB RAM — **host RAM is tighter than VRAM**; allocate benchmark tensors directly on device |
| Python | `.venv` pinned to **3.11** (`richdem`/`whitebox`/`osmnx` wheel coverage on 3.13 is poor) |
| torch | `2.6.0+cu124` — install with `uv pip install --torch-backend=cu124`, **not** `--index-url` (that replaces PyPI and breaks the `nvidia-*` deps) |
| gcc | System default is 14.2; CUDA 12.4 rejects gcc ≥ 14. Any CUDA build pins **gcc-13 repo-locally** (`-DCMAKE_CUDA_HOST_COMPILER`), never via `update-alternatives` |

## Secrets

Credentials live in `.env` at the repo root, `chmod 600`, gitignored from the first commit. **Never** commit them, echo them into logs, or paste them into a transcript. Sources needing auth: Bhuvan (item 3, 4), Copernicus Data Space (item 6 download), NASA Earthdata (IMERG, Phase 6).
