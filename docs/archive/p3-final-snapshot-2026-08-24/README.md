# JALADHAR

> To develop a real-time digital twin of Bengaluru's urban terrain that predicts street-level flood
> depths before they occur, using physics-informed neural surrogates and live rainfall telemetry.

Smart India Hackathon 2026. Private repository.

---

## Start here

**New agent or collaborator → [`docs/HANDOVER.md`](docs/HANDOVER.md).** It explains where the project
actually is, what is established with evidence, and what the open engineering problem is. Do not
start from `SPEC.md` — it is the spec, not the state.

| file | what it is |
|---|---|
| [`docs/HANDOVER.md`](docs/HANDOVER.md) | orientation, current state, the open problem |
| [`docs/BOOTSTRAP.md`](docs/BOOTSTRAP.md) | how to get a runnable checkout — `data/` does not ship |
| [`CLAUDE.md`](CLAUDE.md) | standing rules — binding on every agent. Each rule cites the incident that earned it |
| [`docs/OPEN-ITEMS.md`](docs/OPEN-ITEMS.md) | live ledger, open items only |
| [`docs/SPEC.md`](docs/SPEC.md) | the full 8-phase spec |
| [`docs/phases/`](docs/phases/) | phase writeups |
| [`docs/reference/`](docs/reference/) | ground truth, data audits, walkthroughs |
| [`docs/archive/`](docs/archive/) | superseded material; commits reference it, so it stays |
| [`logs/`](logs/) | raw agent reports and the independent audit, chronological |
| [`prompts/`](prompts/) | reusable goal scaffolding and the verification contract |

## Status

Phase 3 engineering integration is complete: the merged terrain/provenance/replay path passes the
CPU suite and a wet real-input GPU smoke. **The scientific Phase 3 gate is paused, open, and not
passed.** The completed science-blocker evidence is adverse: both tested Sentinel-1 reference
routes are instrument-invalid; official hourly telemetry contains no Bengaluru Urban/Rural rows for
the September 2022 event; and curation yields 31 flood-location attestations but only 15 auditable
depth observations, below the 30–50 depth target. A clean preflight also refuses the repaired
48-hour replay at 12.650 GPU-hours against the configured 10-hour ceiling. Phases 4–8 remain
unauthorized under the fixed spec.

See [`docs/HANDOVER.md`](docs/HANDOVER.md) for the exact continuation point and
[`docs/phases/phase3-writeup.md`](docs/phases/phase3-writeup.md) for the finding/reading split. Do
not interpret a successful smoke, stable mass balance, or passing unit tests as observational
validation.

## Setup

```bash
uv venv --python 3.11 && source .venv/bin/activate
# Choose exactly one Torch backend:
uv pip install --torch-backend=cpu torch==2.6.0       # CPU / no NVIDIA dGPU
# uv pip install --torch-backend=cu124 torch==2.6.0  # RTX 4060 / CUDA 12.4
uv pip install -e .
cp .env.example .env && chmod 600 .env              # fill values required by your fetches
python scripts/bootstrap_data.py                    # unpack the 5.8 MB seed bundle
```

Python 3.11 is pinned — `richdem`, `whitebox` and `osmnx` wheel coverage on 3.13 is poor.
If installing `whitebox` under an absolute path containing spaces prints `chmod: cannot access`, run
`python scripts/repair_whitebox_permissions.py`; see [`docs/BOOTSTRAP.md`](docs/BOOTSTRAP.md).

Production stages expose `typer` CLIs either as `python -m jaladhar...` modules or scripts. Runtime
paths and compute choices belong in `configs/`; current known exceptions are tracked as code debt,
not treated as a configuration guarantee. The CPU laptop and collaborator RTX 4060 use the same
lockfile but install different Torch backends; see [`docs/BOOTSTRAP.md`](docs/BOOTSTRAP.md).

## Data

`data/` is 8.8 GB and does not ship. **`data/seed/` does** — 5.8 MB of inputs that are irreplaceable
or expensive to re-source: the 24 hand-built ground-truth points, the 399 BBMP locations, the
1,988 km BBMP rajakaluve network, the news corpus, and the 2,534-entry underpass register.
`python scripts/bootstrap_data.py` verifies and unpacks it, then lists what to fetch. Everything
else — FABDEM/GLO-30, GPM IMERG, Sentinel-1, OSM, Microsoft buildings, all derived rasters — comes
from CLIs under `src/jaladhar/`. See [`docs/BOOTSTRAP.md`](docs/BOOTSTRAP.md).

Licence note: FABDEM is **CC BY-NC-SA**. That non-commercial exposure is live and deliberate, not
overlooked — see `docs/archive/open-items-full-2026-08-18.md`, item 2.

## Secrets

Credentials live in `.env` at the repo root, `chmod 600`, gitignored since the first commit and
never present in git history. Only `.env.example` ships. Never echo them into logs, agent reports,
or transcripts.

## Reproducibility

Current provenance-bearing runners must refuse dirty trees, write one JSON manifest at run start,
snapshot resolved config, and update that same manifest to a terminal state. Historical manifests
that violate this contract are explicit debt and do not prove acceptance. Numeric claims require a
file in `data/` or a logged run in `runs/`; see `CLAUDE.md` rules 3, 6, 7 and V9.
