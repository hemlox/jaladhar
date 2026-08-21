# HANDOVER — read this first

## Checkout status — 2026-08-20

The data-independent repair batch is implemented but not yet committed or realized on the full
datasets. IMERG acquisition is partial and resumable; its latest restart did not complete in this
environment. Both required Sentinel-1 scene sets are present, but their download directory still
contains an interrupted temporary file and needs a final fetcher-led verification. The terrain DEM,
BBMP seed drain data, OSM sources, and processed 11-layer terrain stack are now present locally.
`python -m jaladhar.terrain.water` is the manifest-bearing acquisition stage for the canonical
standing-water, quarry, and landfill files used by terrain and validation. The corrected
Phase 3 gate now has one start-to-terminal
manifest owner, clean-tree refusal, resolved-config snapshots, event-date eligibility, explicit
depth-band eligibility, buffered final depth, and accepted-step cumulative transport, drain, and infiltration artifacts
for independent catchment accounting. Transport includes realized open-domain outfalls. Historical
Phase 3 outputs predate those contracts and cannot verify them; a clean committed rerun is required.
Phase 3 remains a hard gate, so Phases 4–8 are not authorized by these code changes.

The latest local suite is **157 passed, 89 BLOCKED/skipped, 0 failed**. The
blocked tests require absent real terrain artifacts; they are not acceptance checks. Full-domain
acceptance remains blocked on Tier 2 data and reruns; unit-test success is not a phase-pass claim
(V4/V7).

The exact local state and all code/doc changes made during this repair session are recorded in
[`../codex_session.md`](../codex_session.md). It is a handoff record, not acceptance evidence.

You are picking up **JALADHAR**, a Bengaluru urban flood digital twin built for Smart India
Hackathon 2026. A previous agent took it from nothing to a validated (and partly failed) physics
model. You are being brought in for a **fresh perspective**, deliberately, because the last one
converged on a conclusion the owner does not accept.

**Investigate independently. You are not here to ratify what follows.** Everything below is a
claim with evidence attached; check the evidence. The previous reviewer issued several confident
verdicts that were later withdrawn under independent audit — the pattern is documented in
[`../logs/07-independent-audit-of-reviewer.md`](../logs/07-independent-audit-of-reviewer.md) and it is the single most useful
thing you can read about how this project goes wrong.

---

## 0. First thirty minutes

```bash
uv venv --python 3.11 && source .venv/bin/activate
# Choose the CPU or CUDA 12.4 Torch command in BOOTSTRAP.md, then:
uv pip install -e .
python scripts/repair_whitebox_permissions.py  # only if whitebox printed chmod path errors
python scripts/bootstrap_data.py --check    # what the seed bundle contains, writes nothing
```

Then read §3's list in order. Nothing in this repo runs end-to-end until Tier 2 data is fetched
(`BOOTSTRAP.md`), but the whole argument — what was measured, what was withdrawn, what is still
open — is readable from the documents alone, and that is the part worth your first hour.

---

## 1. The problem statement — fixed, submitted, not negotiable

> To develop a real-time digital twin of Bengaluru's urban terrain that predicts street-level flood
> depths before they occur, using physics-informed neural surrogates and live rainfall telemetry.

Do not reword it and do not propose narrowing it. It has been submitted. **"Street-level flood
depths" is a hard requirement**, which matters because the central finding below is precisely that
street-level depth is what the current terrain cannot deliver. That is the engineering problem you
are inheriting, not a reason to change scope.

---

## 2. Where things stand, in one paragraph

The solver works. The terrain conditioning works. Phase 3 — replaying the September 2022 flood —
**ran end to end and could not be adjudicated**. Flood *extent* turned out to be unmeasurable with
the only instrument available (C-band VV SAR fails on Bengaluru's hyacinth-choked southeastern
lakes). Flood *depth* is genuinely deficient at the ground-truth points, and the cause has been
traced to the terrain: at three of four traced points the DEM gives the location **15, 33 and 1
upstream contributing cells**, because a bare-earth DEM renders an underpass as the flyover deck
above it rather than the road beneath. No obtainable public elevation source fixes this. Meanwhile
where the terrain *is* correct the model works — and over-predicts.

---

## 3. Read in this order

| # | file | why |
|---|---|---|
| 1 | [`../CLAUDE.md`](../CLAUDE.md) | standing rules. **Binding on you.** Non-negotiables 1–7, verification rules V1–V11, reviewer discipline R1–R8. Each rule cites the incident that earned it |
| 2 | this file | orientation |
| 3 | [`OPEN-ITEMS.md`](OPEN-ITEMS.md) | the live ledger — 143 lines, only what is still open |
| 4 | [`phases/phase3-writeup.md`](phases/phase3-writeup.md) | what the gate did and what it means |
| 5 | [`../logs/07-independent-audit-of-reviewer.md`](../logs/07-independent-audit-of-reviewer.md) | independent audit of the previous reviewer's reasoning. Read it before trusting any conclusion here |
| 6 | [`BOOTSTRAP.md`](BOOTSTRAP.md) | **how to get a runnable checkout.** `data/` is 8.8 GB and does not ship; 5.8 MB of irreplaceable seed inputs do. Read before you try to run anything |
| 7 | [`SPEC.md`](SPEC.md) | the full 8-phase spec. Long. Reference it, don't read it cover to cover on day one |

Deeper history is in [`archive/`](archive/) — including the full 1,518-line OPEN-ITEMS
with every withdrawn claim beside its correction (`W1`–`W21`).

---

## 4. Repository map

```
CLAUDE.md              standing rules — binding
HANDOVER.md            this file
OPEN-ITEMS.md          live ledger (open items only)
SPEC.md                the 8-phase spec
.env.example           credentials template; real .env is gitignored and never committed

src/jaladhar/
  terrain/             DEM conditioning, depressions, drains, buildings, roads, underpass register
  solver/              ACC local-inertial solver (PyTorch, 4-connected), checkpointing, analytical tests
  forcing/             IMERG / Open-Meteo / KSNDMC adapters, station acquisition
  validation/          scoring, density stratification, SAR classifiers, event replay, segment scoring
  analysis/            catchment tracing, water budgets
bench/                 solver benchmarks
scripts/               CLI entry points; start with bootstrap_data.py
configs/               YAML — operational paths and compute configuration
tests/                 unit and invariant suite; full-domain/full-duration gaps are tracked in item O

docs/phases/           phase writeups
docs/reference/        ground truth, audits, walkthroughs
docs/archive/          superseded material, kept because commits reference it
logs/                  raw agent reports, chronological
prompts/               reusable goal scaffolding
runs/                  run manifests and outputs (gitignored below the manifest level)
data/                  gitignored entirely — see §7
```

---

## 5. What is established, with the evidence

Treat each as a claim you can re-run. Scripts are committed; that is a standing requirement here.

**The solver is sound.** 48-hour gate run: 166,588 steps, mass relative residual 5.545 × 10⁻⁵, max
realized Courant 0.759 against a 0.85 ceiling, no instability. Nothing found so far is a solver
defect. ACC-vs-ANUGA disagreement is bounded and explained (archive items W, X).

**Extent is physically unmeasurable with what we have.** 25 classifiers across 6 families tested
against 129,474 known-water cells (OSM polygons eroded 30 m) and 130,883 known-dry cells. The
falsifier was registered *before* testing — >70% recall on Varthur and Bellandur at <1% dry
false-positive rate — and nothing passed. The −16 dB cut recovers 0.3487 of Varthur and 0.3787 of
Bellandur; loosening to −8 dB reaches 71%/80% recall at a **19% dry FPR**. Hyacinth and surfactant
froth destroy the specular return. Re-run: `scripts/run_sar_water_investigation.py`.

**Historical depth finding; mechanism readings require rerun.** The old event-maximum raster puts
15 of 24 ground-truth points under 1 cm at the exact cell. That count includes points now excluded
by event-date and road-snap eligibility, so it is not the repaired gate score. Catchment delineation
was rebuilt behind a validation gate
(conservation exact to 0 cells of 12,728,415; 0 monotonicity violations over 3.1 M steps; Bellandur
outlet catchment 158.00 km² against a published ~148; Varthur 269.43 against ~279). The traced
result:

The following table is retained as the historical run's report, not as a current verdict. Its drain
fractions were not independently integrated per catchment; the repaired runner records realized
sink rasters and must replace them.

| point | catchment | historical reported drain | historical reading |
|---|---|---|---|
| GT_17 Bellandur Kodi | 158.00 km² | 28.04% | lake fills to spill; peak lags rain **+25 h**; model **over**-predicts 1.65 m vs 0.85–1.10 m |
| GT_15 Hebbal | **15 cells** | 18.22% | sheds laterally off a planar embankment; no sag to pond in |
| GT_06 Panathur | **33 cells** | **92.71%** | drained before it pools; DEM dip 9.3 cm vs a 1.20–1.45 m flood |
| GT_05 Silk Board | **1 cell** | **100%** | DEM puts this junction on a ridge crest |

**Historical reading, not a load-bearing finding.** At zero drainage with perfect retention those catchments
cap at 0.109 / 0.077 / 0.079 m against bands starting at 0.35 / 1.20 / 0.35 m. No parameter reaches
that. Re-run: `scripts/run_water_tracing.py`.

**Forcing totals are fine; intensity is not.** IMERG gives the flood points 84.1 mm — 77% of the
domain peak — and GT_15 gets the domain *maximum* 109.1 mm while modelling 0.0117 m. But IMERG's
peak 30-minute intensity anywhere in the domain is **21.40 mm/hr**, and the real event delivered
**131.6 mm in 24 h with most of it inside 12 hours**. That evidence sat unread in
`data/fetched_articles.json` for a day while forcing was declared resolved.

**Historical eliminations; recheck eligibility and accounting before relying on them.** Conditioning fill (all 24 points sit
on 0.0 m of fill); empty-lake initial condition (Bellandur filled to spill on its own; unused basin
storage at end of run is 5.71% of the storm); the drain prior (bounded above).

---

## 6. The open engineering problem — this is your job

Street-level depth is required. The terrain cannot see the features that flood. Reconcile that.

Directions, none endorsed — **investigate and form your own view**:

1. **Get real underpass geometry.** Item AO recorded a Rule 5 stop on every public source tried. A
   fresh look at procurement, municipal survey records, photogrammetry from street imagery, or
   crowd-sourced levelling may find something the last pass missed. **You may not invent depths**
   (Non-negotiable 1) — but a *measured* depth from any defensible source unblocks everything.
2. **Sub-grid parameterisation.** Standard practice in urban flood modelling: treat underpasses as
   point features with a fitted storage-depth relation. This is legitimate *only if reported as
   fitted, not predicted*. The catch is sample size — ~6 underpass ground-truth points against 2,534
   tagged locations, leaving nothing to validate against and no basis to generalise.
3. **Attack intensity first (item 1b).** The model may be under-performing in the regime it *can*
   represent, simply because it never received the burst. Cheapest test of a real hypothesis.
4. **Something nobody has tried.** Four causes have been eliminated by measurement. That is progress
   by elimination, and elimination is not evidence for whatever remains. The audit's closing
   question was *"what is this project getting wrong that nobody in it has noticed?"* — it is still
   a live question.

Before building anything, note that **Phase 3 is a hard go/no-go gate** (CLAUDE.md rule 4) and it
has not been passed. Phase 4 (hundreds of scenarios) and Phase 5 (the surrogate) sit behind it.

---

## 7. Practical notes that will save you time

- **`data/` is 8.8 GB and `runs/` is 2.4 GB — neither ships.** What ships is `data/seed/`, 5.8 MB
  of inputs that are irreplaceable or expensive to re-source. **Run `python scripts/bootstrap_data.py`
  first** — it verifies the bundle against SHA256SUMS, unpacks it into the paths the pipeline
  expects, and prints exactly what still has to be fetched. Full detail in
  [`BOOTSTRAP.md`](BOOTSTRAP.md).

  The seed carries: the **24 ground-truth points** (hand-built from news research, ~a day of work,
  **no script can regenerate them** — do not overwrite or pad the file); the 399 BBMP flood-prone
  locations; the BBMP rajakaluve network (1,988 km, Public Domain); the **news corpus containing the
  131.6 mm evidence**; and the 2,534-entry underpass register. Everything else — the BBMP boundary, DEM, IMERG,
  Sentinel-1, OSM, all derived rasters — re-fetches from CLIs under `src/jaladhar/`. Two credentials
  needed; see `../.env.example`.

---

## 8. How work is run here

The owner (Darshil) dispatches implementation agents and reviews their reports. If you are the
reviewer, **R1–R8 in CLAUDE.md are binding on you specifically** — they were written after an audit
found the reviewer's own measurements had worse provenance than the agent work it was auditing.

The short version:

- **Findings vs readings.** A finding is measured with a committed script and a logged run. A
  reading is interpretation. Label both. **No goal may be premised on a reading.**
- **Validate the instrument before the subject.** A whole cycle was spent scoring the model against
  a SAR classifier nobody had checked. It contained 0 of the project's own 24 verified floods.
- **Write the falsifier before the verdict.** Three verdicts were issued and withdrawn.
- **Never state the anticipated answer in a goal prompt.** It was done once; the agent agreed; the
  agreement was read as confirmation.
- **Inventory what is on disk before acquiring anything.** See §5's last paragraph.

Reusable scaffolding — the verification contract, a goal template, and the audit prompt — is in
[`prompts/`](../prompts/). The verification contract is the mechanism that actually worked: every
number must reproduce from one committed script, and a number that does not reproduce is treated as
fabricated rather than as an error.

---

## 9. What the owner wants

To win the hackathon. That means a working, honest demo and a defensible story about why it works.
It does **not** mean a demo built on invented data — Non-negotiable 1 exists because a plausible
looking synthetic result is worse than no result, and the failure modes here have all been silent
ones. The strongest asset this project currently has is that it knows *exactly* where and why it
fails, with measurements behind every claim. Most entries cannot say that.
