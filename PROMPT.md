# Claude Code Build Prompt — JALADHAR: Bengaluru Urban Flood Digital Twin

> **How to use this file:** Drop it in the repo root as `PROMPT.md` (and optionally symlink/copy the "Standing rules" section into `CLAUDE.md`). Start Claude Code and say: *"Read PROMPT.md. Execute Phase 0. Stop at the gate and report."* Work phase by phase. Do not let it run ahead — the gates exist because a wrong answer early makes everything downstream worthless.

---

## 1. Mission

Build a working real-time urban flood forecasting system for Bengaluru.

**Problem statement (fixed, do not reword):**
> To develop a real-time digital twin of Bengaluru's urban terrain that predicts street-level flood depths before they occur, using physics-informed neural surrogates and live rainfall telemetry.

**What the finished system does, end to end:**

1. Ingests live rainfall (KSNDMC telemetric gauges → fallback GPM IMERG / Open-Meteo).
2. Feeds it to a trained neural network that has learned how water moves across Bengaluru's actual terrain.
3. Emits a predicted water-depth raster for the next 1–6 hours, in **under one second**.
4. Renders it on a live 2D web map with per-road-segment flood status, a time scrubber, and ward-level severity tables.
5. Can replay the September 2022 Bengaluru flood and be scored against what actually happened.

**Why the neural network exists:** the physics solver takes 10–30 minutes per run. That is useless for real-time warning. We run the solver hundreds of times *offline* to build a training set, then train an image-to-image network to reproduce its output in under a second. The surrogate is what turns a study into a service. This is the central technical claim of the project — protect it.

---

## 2. Standing rules (put these in `CLAUDE.md`)

**Non-negotiable:**

1. **No fabricated data. Ever.** If a data source is unavailable, say so and stop. Do not generate plausible-looking synthetic elevation, rainfall, or validation data and continue as if it were real. A demo built on invented inputs is worthless to us.
2. **No placeholder physics.** Do not write a function that "approximates" flooding with a blur filter or a distance transform to make the UI look alive. If the solver isn't ready, the UI shows nothing.
3. **Every numeric claim must be traceable** to a file in `data/` or a logged run in `runs/`. No hardcoded metrics in the frontend.
4. **Validate before you scale.** Phase 3 is a hard go/no-go gate. Do not build the frontend, the surrogate, or anything cosmetic before it passes.
5. **When blocked on an external unknown** (licence terms, API access, dataset availability), STOP and report it in `OPEN-ITEMS.md`. Do not work around it silently.
6. **Log everything.** Every solver run writes a JSON manifest: inputs, parameters, git SHA, wall clock, output path. Reproducibility is a grading criterion for us, not just good practice.

**Style:**

- Python 3.11+, type hints, `ruff` + `black`. No notebooks in the repo except under `notebooks/` for exploration — production code is modules.
- Config in YAML under `configs/`, never hardcoded paths.
- Every module gets a `if __name__ == "__main__"` CLI entry via `typer` so any stage can be run standalone.
- Prefer stdlib and well-known packages. Do not add a dependency to save five lines.

---

## 3. Environment

**Compute is NOT fixed and the procurement decision is deliberately open.** Do not hardcode a worker count, a GPU model, or a batch size anywhere. Everything that touches compute must be parameterised and driven from `configs/compute.yaml`.

Fixed:

| | |
|---|---|
| Dev machine | Laptop with an **RTX 4060, 8 GB VRAM**, CUDA available |
| Shared storage | Google Drive folder mounted into every worker |
| Team | 6 people, strong at LLM-assisted development, weak on geospatial and hydraulics — code must be legible and docs must explain *why*, not just *what* |

**Open question D — what pool do we actually buy/assemble?** Both of these are genuinely on the table and the choice should follow from the Phase 4 cost estimate, not from a preference:

| Option | Shape | Notes |
|---|---|---|
| **~10 free Colab accounts** | ~10× T4 16 GB, sessions die at ~4–5 h, GPU availability not guaranteed per account | Zero cost, high worker count, **high churn**. Only viable with the dynamic claiming design in §9 — with static assignment this option is unusable. |
| **Colab Pro** (1 or more seats) | Longer sessions (~24 h), better GPUs (L4/A100 class), higher reliability | Costs money, fewer parallel workers per rupee, but each worker is faster and doesn't vanish mid-run. Materially better for the *training* stage, where a single long job beats many short ones. |
| **Mixed** | Pro for training + free accounts for the embarrassingly-parallel solver batch | Probably the right answer. Solver runs are independent units and tolerate churn; surrogate training is one long job that hates being killed. |

**These have different failure modes, and the code must tolerate both simultaneously.** A heterogeneous pool where an A100 and a T4 are pulling from the same queue is the normal case, not an edge case — so units of work must be small enough that a slow worker taking one doesn't stall the batch, and the claiming system must not assume workers are interchangeable in speed.

**Deliverable for D:** once the Phase 4 cost estimate exists, write a short recommendation — projected wall clock and rupee cost under each option — so the buy decision is made against numbers. **Write everything so that adding, removing, or upgrading a worker is a config change, never a code change.**

**Hard rule:** before any expensive stage, emit a **compute budget estimate** — cells × timesteps × scenarios → estimated GPU-hours, against the currently configured worker pool — and print it. If a stage would exceed the budget in `configs/compute.yaml`, refuse to start and report. We are not discovering a 400-hour job by watching it run.

**VRAM discipline:** the smallest configured device sets the limit. Autodetect available VRAM at startup and pick tile size from it rather than assuming. Note that forward-only simulation is comparatively cheap in memory; the two things that actually blow up are (a) autograd through thousands of solver timesteps during calibration — use gradient checkpointing or calibrate on tiles, and (b) U-Net training on very large rasters. Handle both by tiling, not by shrinking the domain.

---

## 4. Repo structure

```
jaladhar/
├── PROMPT.md                  # this file
├── CLAUDE.md                  # standing rules (§2)
├── OPEN-ITEMS.md              # unresolved external unknowns, updated continuously
├── configs/
│   ├── domain_bengaluru.yaml  # full BBMP extent, resolution, CRS, tiling scheme
│   ├── compute.yaml           # worker pool, VRAM limits, GPU-hour budget
│   ├── solver.yaml            # physics params, timestep, roughness defaults
│   ├── scenarios.yaml         # rainfall scenario generation grid
│   └── train.yaml             # surrogate hyperparameters
├── data/
│   ├── raw/                   # downloaded, never modified
│   ├── interim/               # reprojected, clipped
│   └── processed/             # final model-ready rasters
├── src/jaladhar/
│   ├── terrain/               # DEM fetch, conditioning, roughness, buildings
│   ├── solver/                # local-inertial ACC solver (PyTorch)
│   ├── scenarios/             # hyetograph generation, batch runner
│   ├── surrogate/             # U-Net, dataset, training, eval
│   ├── forcing/               # live rainfall adapters
│   ├── validation/            # metrics, BBMP list, SAR, event replay
│   └── api/                   # FastAPI serving layer
├── colab/
│   └── worker.ipynb           # the notebook teammates run
├── frontend/                  # React + MapLibre GL
├── runs/                      # solver + training outputs, manifests
├── notebooks/                 # exploration only
└── tests/
```

---

## 5. PHASE 0 — Resolve unknowns. **STOP GATE.**

Before writing any modelling code, resolve these and write findings to `OPEN-ITEMS.md`. Several are load-bearing: the answer changes the architecture.

| # | Question | How to check | Why it matters |
|---|---|---|---|
| 1a | **Is there a programmatic API for KSNDMC *live* gauge readings?** | Inspect network traffic. Note `dashboard.ksndmc.org` and `varunamitra.karnataka.gov.in` **no longer resolve**; `ksndmc.org` is live and serving ASP.NET (`/Rainfall_Data.aspx`). **Also try the Meghasandesha Android app** — it almost certainly hits a cleaner JSON API than the WebForms site, and APK inspection or a proxy will surface it faster than fighting postbacks. Station geometry is confirmed open on `data.opencity.in`. | Determines whether **nowcast mode** runs on real gauges or on ~10 km IMERG. Degrades the product; does not kill it. |
| 1b | **Can we get KSNDMC *historical* records for 1–10 Sept 2022?** | A different question from 1a and possibly a different access path: an archive page, a date-parameterised endpoint, or a direct data request. If no public route exists, **email KSNDMC** — this is a public-safety student project, and an official data-sharing arrangement is a better pitch line than a discovered endpoint. | **Higher stakes than 1a, and the closest thing to a true blocker in this table.** The Sept 2022 replay must be forced with rain that actually fell (§8). Record the *temporal resolution* and *station count* actually recovered — daily totals cannot drive a 1–6 h validation the way hourly can. |
| 2 | **FABDEM licence for this use** | Read University of Bristol terms at `data.bris.ac.uk` directly. | If restrictive, fall back to Copernicus GLO-30 and handle buildings ourselves. |
| 3 | **CartoDEM availability / resolution / licence on Bhuvan** | Register on Bhuvan, attempt download for our bbox. | A 10 m Indian DEM would materially improve everything *and* is politically valuable in an SIH pitch. |
| 4 | **NRSC/Bhuvan published inundation layer for Bengaluru Sept 2022** | Bhuvan disaster services archive. | Would be the single strongest validation artefact we could have. |
| 5 | **BBMP flood-prone location list — machine readable?** | It's published as PDFs. Try `pdfplumber`/`camelot`. Geocode the location names. | This is our primary binary validation label set. |
| 6 | **Sentinel-1 GRD scenes covering Bengaluru, 1–10 Sept 2022** | Copernicus Data Space Ecosystem / ASF search. | Observed flood extent for the peri-urban part of the domain. |
| 7 | **Does LISFLOOD-FP 8.1 build cleanly here?** | Clone from Zenodo `10.5281/zenodo.6912932`, attempt CMake+CUDA build. Timebox to **2 hours.** | See §7 — if it doesn't build fast, we write our own solver, which is arguably the better choice anyway. |
| 8 | **Is a usable rainfall *forecast* source available?** | Confirm **Open-Meteo** returns Bengaluru precipitation today (cache a real response as evidence). Then check **IMD nowcast** and **Indian DWR radar** for public programmatic access. | Determines whether we have a forecast mode at all beyond routing lead time (§11). Open-Meteo alone gives a forecast mode at NWP resolution — record what that costs. |
| 9 | **Are the UK EA 2D benchmark test definitions *and input data* obtainable?** | Néelz & Pender (2013). Prioritise **Test 8A — rainfall-driven urban flooding**, our closest analogue. Published results without downloadable inputs is a PARTIAL, not a pass. | §7's solver correctness proof depends on it. If unavailable we need a different way to prove the solver is right — analytical cases (dam-break, tilted-plane rainfall) — and we need to know that **now, not in Phase 2.** |

### Open design questions — resolve with numbers, not opinions

These are deliberately unfixed. Each needs an **empirical answer written into `OPEN-ITEMS.md`**, not a guess.

| # | Open question | How to answer it |
|---|---|---|
| **A** | **Grid resolution for the full-city domain.** 5 m, 10 m, 20 m, 30 m? | Compute cell counts for each over the full BBMP extent. Benchmark the solver at each on a representative tile: wall clock per simulated hour, peak VRAM. Cross against §14 — a 30 m cell cannot resolve a 10 m street, so there is a physical floor on how coarse we can go and still make a street-level claim. Produce a table of resolution vs cost vs defensibility and recommend. |
| **B** | **How the city is partitioned into parts, and how those parts are split across workers.** Number of tiles, tile size, overlap/halo width, whether tiles are solved independently or coupled. | This is the central engineering decision of the project — see §6.0. Answer it with a benchmark, not a preference. |
| **C** | **How many training scenarios the surrogate actually needs.** | Do **not** pick a number. Measure it with a learning curve — see §10. |
| **D** | **What compute pool to assemble** — ~10 free Colab accounts, Colab Pro seats, or a mix (§3). | Cannot be answered before the Phase 4 cost estimate exists. Set up `configs/compute.yaml` now with whatever is available today, make every stage quote its cost against it, and produce the wall-clock-and-cost-per-option comparison as soon as real solver timings exist. |

**Deliverable:** `OPEN-ITEMS.md` with a verdict on items 1a, 1b and 2–9, plus a reasoned, benchmarked recommendation on A–D, plus any files successfully downloaded into `data/raw/`.

### The gate is one question

> **Do we have enough real, external, verifiable evidence to validate a flood prediction against a real event?**

Judge it **in the round**, not item by item. Items 1a, 2 and 5 each have a documented fallback — no live API → IMERG; restrictive FABDEM → Copernicus GLO-30; no BBMP list → Sentinel-1 plus geolocated ground truth. **None of them is individually fatal**, so a single red item must not hard-fail the phase.

Two failure modes to avoid, in both directions:

- **Do not hard-fail on one blocked item** when a documented fallback covers it.
- **Do not soft-pass by treating a fallback as equivalent to the thing it replaces.** State explicitly which fallback is being invoked and what it costs in accuracy.

**Weight item 1b accordingly** — no historical rainfall for a real event means there is nothing to validate against. IMERG at ~10 km is a genuinely degraded substitute for gauge records: usable, but it leaves the spatial structure of the storm unresolved across the city, which weakens per-ward attribution. *Some* observed historical rainfall is non-negotiable; gauge-resolution rainfall is strongly preferred; say plainly which one we ended up with.

If item 1b is awaiting a reply from KSNDMC when Phase 0 reports, issue the verdict on the **IMERG-fallback branch** with that cost named, mark it provisional, and flag it for revision if KSNDMC responds. Do not leave the highest-weighted item indeterminate.

The verdict names the evidence actually in hand for terrain, event forcing, and validation labels, and reaches one of three explicit recommendations: **proceed to Phase 1**, **proceed with a named degradation**, or **stop and change the project** (§8).

A, B and D must each have at least a first-pass estimate before Phase 1 begins.

---

## 6. PHASE 1 — Terrain pipeline

### 6.0 Domain: the whole city

**The deliverable is all of Bengaluru — the full BBMP administrative extent (~740–800 km², 198 wards).** Not a corridor, not a study area. A system that covers one neighbourhood is a demo; a system that covers the city is the project.

Exact extent, resolution, and partitioning are **open questions A and B from Phase 0** and must be settled with benchmarks. Constraints that bound the answer:

- Full BBMP extent at 10 m is on the order of **8 million cells**; at 5 m, ~32 million. Every cost figure in this document is to be recomputed against the real extent — no number here is a scope statement.
- Cost scales worse than linearly with cell count: more cells, and a finer grid also forces a smaller CFL timestep. Measure this, don't extrapolate.
- Coarser than ~10 m and the street-level claim in the problem statement stops being defensible (§14.1).

**Tiling.** The city almost certainly has to be partitioned. The design must answer:

- **Tile size and count** — driven by VRAM on the smallest worker and by wall clock per scenario.
- **Halo width.** Tiles are not independent: water crosses tile boundaries. Solve tiles with an overlapping halo and exchange boundary state between steps, OR pick tile seams along **hydrological divides** (ridgelines from flow-accumulation analysis) where cross-boundary flux is genuinely near zero. The second is cheaper and more defensible — investigate it first.
- **Seam artefacts.** Whatever the scheme, produce a diagnostic: run one storm tiled and (on a subregion small enough to fit) untiled, and map the difference. If seams are visible in the output, the scheme is wrong. This diagnostic is also a good slide.
- **Whether the surrogate tiles the same way as the solver.** It does not have to. A U-Net can be trained on tiles and applied convolutionally across the full raster. Decide deliberately.

Whatever is chosen goes in `configs/domain_bengaluru.yaml` with the benchmark that justified it recorded alongside.

### 6.1 Layers

**Build `src/jaladhar/terrain/`:**

1. **`fetch.py`** — download DEM (FABDEM primary, Copernicus GLO-30 fallback), clip to bbox, reproject to a metric CRS (**EPSG:32643, UTM 43N**). Cache aggressively; never re-download.

2. **`buildings.py`** — pull Microsoft Global ML Building Footprints (`github.com/microsoft/GlobalMLBuildingFootprints`, ODbL) and OSM buildings via `osmnx`. Rasterise to the grid. Implement **both** treatments behind a config flag:
   - `blockage`: raise building cells by a fixed height, water routes around
   - `porosity`: reduce cell storage/conveyance without full blocking
   Blockage is more physical at fine resolution; porosity is more stable at coarse. Being able to explain the tradeoff is a real answer to a technical judge.

3. **`conditioning.py`** — the important one. A 30 m cell cannot resolve a 10 m street. We fix this with **vector-driven structure, not interpolation**:
   - Resample DEM to target resolution (5 m or 10 m)
   - Burn OSM road centrelines in as shallow conditioned channels (~0.15–0.3 m below surrounding grade)
   - Burn OSM `waterway=drain|ditch|stream` (the primary rajakaluve channels) as deeper channels
   - Burn building footprints up as blockages
   - Remove spurious pits that would trap water unphysically (`richdem` or `whitebox` breach-depressions — prefer breaching over filling, it preserves flow paths)

   **Burn precedence where a cell is claimed by more than one of the above: waterway > road > building.** Building only claims a cell that neither a road nor a waterway burn already claims — never the other way around, and never decided by which burn happens to run last in the code. Two real, common (not edge-case) situations force this:
   - Buildings routinely front directly onto a road's centreline; at 10 m resolution with a line burn, real road/building cell overlap is common. A road cell must stay a conveyance path — a building at the property line does not mean the street is structurally blocked.
   - Where a building has physically encroached on a rajakaluve (a real, common problem in this city — §14.2), the model represents that as the channel being constricted or overtopped by the encroachment, not as the channel ceasing to exist. Backfilling a drain to building height because a building sits on it would erase the one flow path most likely to matter during a flood.

   **The fine structure comes from vector data, not from inventing elevation.** Say exactly that in the docs and in the pitch.

4. **`roughness.py`** — Manning's *n* raster from OSM landuse + road polygons:
   - roads/paved: 0.013–0.02
   - dense urban: 0.05–0.15
   - vegetated/open: 0.03–0.08
   - water: 0.02–0.03

   Treat urban *n* as a **calibration parameter**, not a constant. This is where sub-grid effects we can't resolve get absorbed.

5. **`drains.py`** — Bengaluru's stormwater drain network **is not open data.** We model the subsurface system as a **calibrated sink term**: a per-cell removal rate capped by an assumed capacity, calibrated against observed flooding in Phase 3. Document this honestly — it is the largest single source of error, and it's a *data availability* problem, not a modelling one.

**Acceptance:** `python -m jaladhar.terrain.build --config configs/domain_bengaluru.yaml` produces a tiled, aligned stack in `data/processed/` covering the full BBMP extent — elevation, building mask, building height delta, building conveyance factor, Manning's n, drain sink capacity, road segment IDs — same grid, same CRS, no NaNs in the interior, tile index written to a manifest. Write a QC figure per layer to `runs/terrain_qc/` plus a whole-city mosaic to eyeball for seams and voids.

`building_height_delta` (float32, metres) is buildings.py's own per-cell height output — always written regardless of `buildings.treatment`, since conditioning.py picks which burn strategy consumes it at ITS config-read time. `building_conveyance_factor` (float32, fraction retained — 1.0 = unaffected, `buildings.porosity_conveyance_factor` = reduced) is conditioning.py's output, not buildings.py's: a genuine separate conveyance-reduction layer for cells where a building's effect on elevation had to be withheld in favour of road/waterway conveyance (§6.1 item 3's burn precedence), or — under `treatment: porosity` — for every building cell, since porosity never raises elevation at all. Phase 2's solver reads this as a multiplicative reduction on local conveyance/storage; it is never folded into elevation.

---

## 7. PHASE 2 — The physics solver

### Decision: write our own, in PyTorch

Unless LISFLOOD-FP built cleanly in Phase 0 item 7, **implement the local-inertial (ACC) scheme ourselves in PyTorch.** Reasons:

- The scheme is ~200 lines of stencil operations on a grid — it is genuinely not hard
- Runs natively on the 4060 and on Colab T4s with zero build friction
- **Differentiable**, which lets us calibrate roughness and drain capacity by gradient descent against observed flood extent. That is a real technical differentiator, not a hackathon trick
- Same tensor library as the surrogate — one stack, one debugging surface

If LISFLOOD-FP *did* build, keep it as a **cross-validation reference**, not the workhorse.

### The scheme — Bates et al. (2010) local inertial

Neglects the advection term of the full shallow water equations. This is the accepted standard for urban/pluvial inundation.

Flux between adjacent cells (per unit width, x-direction shown; y is symmetric):

```
h_flow = max(h_i + z_i, h_j + z_j) - max(z_i, z_j)

              q_t - g·h_flow·Δt·(∂(h+z)/∂x)
q_{t+Δt} =  ─────────────────────────────────────
            1 + g·Δt·n²·|q_t| / h_flow^(7/3)
```

Depth update by mass balance:

```
∂h/∂t = (Σ q_in - Σ q_out)/Δx  +  R(t)  -  I  -  D
```

where `R` = rainfall rate, `I` = infiltration, `D` = drain sink.

Adaptive timestep (CFL):

```
Δt = α · Δx / sqrt(g · h_max),    α ≈ 0.7
```

**Implementation notes:**

- Everything is a 2D tensor op. `torch.roll` for neighbour access, or explicit slicing. No Python loops over cells.
- Guard `h_flow` with a depth threshold (~1 mm) — below it, set flux to zero. This is essential for stability and for not wasting compute on dry cells.
- Use `float32`; `float64` will not fit in 8 GB at useful grid sizes and isn't needed.
- **Store and compute elevation relative to a datum, not raw MSL.** Store and compute elevation as `z' = z - datum`, where `datum` is the domain minimum, carried in the manifest and added back only for display/reporting. Bengaluru's absolute elevation is ~725–970 m; float32 gives ~7 significant decimal digits total, so at that magnitude the ULP is already ~0.1 mm, and every `h + z` addition or `(h+z)_i - (h+z)_j` flux gradient the ACC scheme computes rounds to that same ~0.1 mm grid every timestep, regardless of how finely `h` itself could otherwise be resolved — a small loss per operation that accumulates across thousands of timesteps in a multi-hour simulation, for no physics reason, only an arbitrary absolute-elevation convention. Datum-relative (`z'` in the 0–245 m range for this domain) buys roughly 4x finer ULP on every one of those additions and subtractions, at zero cost — the ratio scales with `domain_max_elevation / domain_elevation_span`, so it is worth re-deriving per domain rather than assumed.
- Write out depth rasters at a fixed cadence (every 5 or 15 simulated minutes) to NetCDF via `xarray`.

### Correctness proof — do not skip this

Our solver is worthless if we can't show it's right. Validate against the **UK Environment Agency 2D benchmark suite** (Néelz & Pender, 2013), which has published results from many commercial and research codes:

- **Test 1** — flooding a disconnected water body
- **Test 2** — filling of floodplain depressions
- **Test 5** — valley flooding
- **Test 8A** — **rainfall-driven urban flooding** ← this is exactly our case, prioritise it

*(Verify the exact test definitions and download the input data before relying on them; flag in `OPEN-ITEMS.md` if unavailable.)*

Matching published results on Test 8A is a slide in the deck: *"our solver reproduces the standard industry benchmark."*

**Acceptance:** solver runs a synthetic 110 mm / 2 h storm, produces a depth time series, conserves mass to within 1% (including across tile seams), and matches at least one EA benchmark test. Wall clock and peak VRAM logged, at every candidate resolution from open question A, so the cost table is real rather than extrapolated.

---

## 8. PHASE 3 — Validation. **GO / NO-GO GATE.**

**This decides whether the project is viable. Do it before anything cosmetic.**

Replay the **September 2022 Bengaluru flood** and score it.

**Validation is city-wide.** Score across the full BBMP extent — all 198 wards, the complete BBMP flood-prone location list, the full Sentinel-1 footprint. The headline CSI we quote is the city-wide number.

While *debugging* the solver, it is obviously fine to iterate on a few tiles rather than re-running the city on every code change — that is ordinary development practice, not a scope decision. But no result is reported, and no gate is passed, on a subset.

1. **Force the model with what actually fell** — KSNDMC gauge records for the event, spatially interpolated (IDW or kriging) across the domain. Not a design storm.
2. **Score against BBMP's flood-prone location list** — geocoded from Phase 0 item 5. Compute hit rate and false alarm ratio. Binary labels, but defensible and quantitative.
3. **Score against Sentinel-1 SAR observed extent** — VV backscatter thresholding or coherence change detection on a before/after pair, over the whole city footprint. **Report it stratified by urban density**, because SAR flood mapping degrades badly in dense built-up areas from layover, shadow, and double-bounce off buildings, while working well over open water, lake margins and peri-urban land. A single citywide SAR score would blend a reliable measurement with an unreliable one; splitting it is both more honest and more informative. State this ourselves before a judge finds it.
4. **Geolocated ground truth** — pin 30–50 confirmed flood points from news and social imagery, with approximate depth from visual cues (kerb height, car wheels, doorsteps). Report depth as a **band, not a number.**

**Metrics to report, per event:** CSI (critical success index) on extent, hit rate, false alarm ratio, RMSE in depth at ground-truth points.

**The bar:** published state of the art for this class of model on real events is **CSI ≈ 0.73, depth RMSE ≈ 0.17 m** (Water 17(8):1239, 2025). If we're near that, say so plainly. If below, say that too and diagnose why — data quality is the likely reason, and that's a legitimate finding, not a failure to hide.

**Calibration loop:** this is where the differentiable solver earns its place. Optimise Manning's *n* per land-use class and the drain sink capacity by gradient descent against observed extent. Hold out points for testing.

**GATE:** if we cannot get a defensible city-wide match against the 2022 event, **stop and change the project.** Everything downstream — the surrogate, the dashboard, the pitch — is built on the assumption that the solver reproduces reality. Verify that assumption before investing in anything that depends on it.

---

## 9. PHASE 4 — Scenario generation at scale

### 4a. Hyetograph generation

`src/jaladhar/scenarios/generate.py` — programmatically produce rainfall scenarios, not hand-designed ones. **How many is open question C** (§10) — build the generator so the count is a config parameter and the sampler can produce any number without changing code. Vary along four axes:

| Axis | Values |
|---|---|
| Total depth | 20, 40, 60, 80, 100, 130, 160, 200 mm |
| Duration | 1 h, 3 h, 6 h, 12 h |
| Temporal pattern | front-loaded, centre-peaked, back-loaded, multi-peak (Huff quartiles) |
| Spatial pattern | uniform, NW-concentrated, SE-concentrated, band-moving-E, band-moving-N |

Sample the cross product, keep IDF-curve-consistent combinations, add **replays of real historical events**. **Hold real events out of training entirely** — they are the test set.

**Sample in an order that degrades gracefully.** Generate the scenario list so that any prefix of it is a well-spread sample of the parameter space (Latin hypercube or Sobol sequence, not nested loops). This matters because the training-set size is deliberately open: we want to be able to stop after whatever number of runs we can afford and still have coverage, rather than discovering we ran every low-rainfall case and no extreme ones.

Each scenario writes a config to `runs/scenarios/scenario_NNN.yaml`.

### 4b. Distributed batch execution

`colab/worker.ipynb` — the notebook a teammate runs. Must be trivial: mount Drive, Run All. Nothing to configure by hand.

`src/jaladhar/scenarios/batch.py` — the runner. **Worker count, scenario count, and tile assignment are all runtime parameters. Nothing about the split may be hardcoded.** Requirements:

- **Dynamic work claiming, not static assignment.** Do *not* pre-assign "worker 3 gets scenarios 60–89" — that breaks the moment someone adds or loses a machine. Instead, maintain a claim directory in shared storage: a worker atomically creates `claims/scenario_N.claim` (exclusive create, fails if it exists) to take the next unclaimed unit of work, then does it. Workers can join or leave at any time and the pool self-balances. This is the whole reason compute doesn't need to be fixed in advance.
- **Stale claim reclamation.** A claim file carries a heartbeat timestamp. If a claim is older than a threshold with no heartbeat and no output, another worker may steal it. Otherwise a dead Colab session orphans its work forever.
- **Unit of work = (scenario, tile).** If the domain is tiled, the queue is the cross product, so a small worker can take single tiles while a bigger one takes several. Do not make the unit of work "whole city, one scenario" unless a single worker can actually hold that.
- **Idempotent and resumable.** Before starting, check whether the output already exists. If yes, skip. When Colab kills a session, reopen and Run All — no lost work, no duplicates.
- **Atomic writes.** Write to `.tmp`, then rename. A half-written file must never look complete.
- **Per-run manifest** with git SHA, params, wall clock, peak VRAM, worker identity.
- **Live progress** to a shared `status.json`: units done, in flight, remaining, current throughput, and a projected finish time against the *currently active* worker count. Whoever is coordinating should be able to see at a glance whether to bring more machines online.

**Cost estimation is required before launch, and it feeds the procurement decision.** Print: units of work × measured seconds per unit ÷ active workers → projected wall clock, with the measurement taken from a real timed run at the chosen resolution, not a guess. If the projection exceeds the budget in `configs/compute.yaml`, stop and report rather than starting.

Emit the same projection **for each pool option in open question D** — ~10 free Colab accounts, N Colab Pro seats, and a mix — with a rupee cost against each. That table is how the buy decision gets made. Include the effect of churn: free-tier workers lose partial work when a session dies mid-unit, so their effective throughput is below their nominal throughput, and the gap widens as the unit of work gets longer. If the numbers say free accounts are fine, don't spend the money; if they say a Pro seat halves the wall clock for ₹900, that is worth knowing before the deadline rather than after.

**Acceptance:** every scenario × tile present in the shared folder with a valid manifest, no gaps in the index, and a reassembly step that stitches tiles back into whole-city depth rasters with no visible seams.

---

## 10. PHASE 5 — The neural surrogate

**`src/jaladhar/surrogate/`**

### Inputs / outputs

**Input channels (static, same every scenario):** elevation, slope, flow accumulation, Manning's n, building mask, building conveyance factor, distance-to-drain, drain capacity.

**Input channels (dynamic):** rainfall hyetograph — either as a spatially distributed sequence or as a conditioning vector, plus antecedent wetness.

**Output:** depth field per timestep over the forecast horizon.

### Architecture

**Start with U-Net.** It is the proven baseline and the published benchmark uses a plain CNN. Get numbers first.

**Then try FNO** (Fourier Neural Operator, `neuraloperator` library) as an upgrade — resolution-invariant, so train at 10 m and infer at 5 m. Higher risk, better story if it works.

**Report both.** *"We tried the fancy thing and here's how it compared"* is a stronger result than only showing the fancy thing.

### Loss — this matters more than architecture

Plain MSE over the depth field is dominated by the vast dry area and will train a model that predicts "dry everywhere" quite successfully. Use a weighted combination:

```
L = λ₁ · MSE(wet cells only)
  + λ₂ · [BCE or Dice] (wet/dry mask)
  + λ₃ · |∫h dA − (rainfall in − drain out)|      ← mass conservation
```

That mass term is the cheapest genuine "physics-informed" claim available and it is a real one. The published benchmark explicitly notes their model is *not* physics-informed and flags it as future work — so this is an open contribution we can plausibly make. **Do not overclaim it as a full PINN.** It's a soft physics constraint. Say that.

### How many training scenarios? — open question C, answer it empirically

**Do not pick a number and do not take one from this document.** The honest way to answer this is a **learning curve**, and it is cheap because it reuses runs we already have:

1. Fix the architecture, the loss, and a held-out test set (including the real historical events).
2. Train identical models on nested subsets — 10, 25, 50, 100, 200, … scenarios — using the prefix property of the Sobol/LHS ordering from §9 so every subset is a well-spread sample rather than an arbitrary slice.
3. Plot **validation CSI and depth RMSE against training-set size**, with error bars over a few seeds.
4. Read off where the curve flattens. That plateau, not a guess, is the answer. If it hasn't flattened at the largest subset, we need more runs and now we know it — and we know roughly how many more from the slope.

Write the curve to `runs/learning_curve/` and put it in `OPEN-ITEMS.md`. **This plot belongs in the deck**: "we didn't guess how much training data we needed, we measured it" is exactly the kind of thing that separates a real engineering project from a hackathon submission.

Two caveats to respect while doing this:

- Scenario count and *sample* count are not the same thing. One simulation yields many timesteps and, if tiled, many tiles — so the effective dataset is much larger than the scenario count. Report both, and be careful that samples from the same scenario don't leak across the train/test split.
- The curve is architecture-dependent. If the architecture changes materially, the curve is stale.

### Targets

| Metric | Target | Source |
|---|---|---|
| Depth RMSE | ≤ 0.15 m vs held-out solver runs | Water 17(8):1239 |
| Extent CSI | ≥ 0.73 | same |
| Inference | **< 1 s** for the **full city**, full horizon | our requirement |
| Speedup vs solver | report the wall clock honestly | — |

The sub-second target is for the whole city, not a tile. If the surrogate is applied tile-wise, the timing that gets quoted is the full reassembled forecast including stitching.

**Report two error budgets separately:** solver-vs-observation, and surrogate-vs-solver. One combined number hides which part is broken.

**Acceptance:** trained checkpoint, eval report in `runs/surrogate_eval/` with metrics table and side-by-side depth maps (solver vs surrogate vs difference) for held-out scenarios.

---

## 11. PHASE 6 — Rainfall forcing: historical, nowcast, forecast

**`src/jaladhar/forcing/`** — adapters behind one interface. There are **three distinct modes**, drawing on different sources and carrying very different confidence. Do not collapse them into one.

| Mode | What it is | Drives |
|---|---|---|
| **Historical** | Observed rain from a past event, gauge by gauge | The September 2022 replay. **Validation depends entirely on this.** |
| **Nowcast / live** | Rain fallen in the last minutes to hours | Current-state estimate; the live map |
| **Forecast** | Rain that has **not fallen yet** — NWP or radar extrapolation | Extended lead time |

### The lead-time distinction — the core honesty rule

**Observed rain alone already gives us lead time**, because water takes time to route across terrain. Rain that fell 20 minutes ago is still moving downhill; we can say with high confidence where it will pool and when. Call this **routing lead time** — roughly **30–90 minutes** in a city.

This is the strongest claim the system makes: **pure physics on measured input, no meteorology in the loop.**

Forecast rain buys more lead time at much lower confidence. **A flood forecast can be no better than the rainfall forecast driving it.**

**Never blend the two into one number:**

- *"Water already on the ground reaches this junction at 1.2 m in 40 min"* → **near-certain**
- *"If the forecast rain materialises, this junction reaches 1.8 m by 16:30"* → **conditional, and the condition must be stated**

### Observation sources

| Priority | Source | Notes |
|---|---|---|
| 1 | **KSNDMC telemetric gauges** | ~900 across Karnataka, dense in Bengaluru. Live access = Phase 0 item **1a**; historical archive = item **1b**. |
| 2 | **IMD** | National authority, cite it. Machine access inconsistent. |
| 3 | **NASA GPM IMERG Early** | ~4 h latency, ~10 km, 30 min. Coarse for a city but dependable. Needs Earthdata login. |
| 4 | **Open-Meteo** | No API key, trivial integration. |

### Forecast sources

| Source | Notes |
|---|---|
| **Open-Meteo** | Free, no key, serves ECMWF/GFS/ICON. Guaranteed baseline — **build against it first.** |
| **IMD nowcast** | Radar-based 0–3 h, **the range that matters most**. A real upgrade if publicly accessible. Verify (Phase 0 item 8). |
| **Indian DWR radar** | Best possible input, access limited. Verify before designing any dependency on it. |

If multiple forecast members are cheap to run, **run several and show a range**. A flood forecast driven by a single deterministic rainfall forecast projects false precision. Stretch goal, not a requirement.

### Rules

- **Always surface which source *and which mode* is driving the current forecast** — a badge in the UI. Never silently fall back.
- Poll every 15 minutes. Interpolate gauge point readings to the grid (IDW, or kriging if the variogram behaves).
- **Cache forecasts as-issued, not just observations.** Store the forecast at *issue* time alongside its *valid* time, in `data/raw/forcing/`. This is what later lets us score forecast skill honestly rather than reconstructing it retroactively — and it is the difference between being able to say "our forecasts verified at X" and not being able to say anything.
- **The surrogate does not care whether its rainfall input is observed or predicted** — same model, same tensor. The distinction lives in the **data layer and the UI**, not in the network.

---

## 12. PHASE 7 — Frontend

**Stack:** React + **MapLibre GL** (deck.gl if we need heavy raster layers). FastAPI backend serving depth rasters as tiles or as compressed arrays.

**2D map, not 3D.** 3D adds visual complexity without adding information and is harder to build well. Depth is conveyed by a colour ramp plus an on-click readout.

### Screens

1. **Live mode** — current rainfall overlay + KSNDMC gauge dots with readings + predicted depth raster + source badge. Refreshes every 15 min.
2. **Scenario mode** — rainfall slider (total depth, duration, pattern). Drag it, watch water spread down actual named streets. Time scrubber across the forecast horizon.
3. **Validation mode — the credibility beat.** Load September 2022, run it, overlay BBMP flood-prone points and our geolocated ground truth. Show the overlap. **Put the CSI on screen.**
4. **Speed beat.** Same scenario, two buttons: "run physics solver" (watch the wall clock tick) vs "run surrogate" (done in under a second). Show the difference map between the two outputs. This lands the technical claim without anyone needing to understand a PDE.

### Lead-time rendering — do not paper over the distinction

The forecast horizon is **not uniform in confidence** (§11), and the UI must not imply that it is:

- The **routing horizon** — driven by rain already on the ground — renders **solid**.
- The **forecast-driven horizon** renders **visibly provisional**: hatched, faded, or dashed. Pick one and be consistent.
- **Mark the boundary between them on the time scrubber.**

Separating what we *know* from what we *predict* is both more honest and a better pitch beat than one confident-looking line. A judge who notices the distinction unprompted will trust everything else on the screen more.

### Operational layers (steal these from iFLOWS-Mumbai)

- **Ward-level severity table.** Map depth predictions onto BBMP's 198 ward boundaries. Ranked table: ward name, locality, severity (MODERATE / HIGH / VERY HIGH), predicted peak depth, time to peak. This is what makes it look operational rather than academic.
- **Flood Atlas tab.** We're already generating 200+ scenarios for training — package the illustrative ones (50 mm / 100 mm / 150 mm / 200 mm, plus the 2022 replay) as a pre-computed ready-reckoner. Near-free to add.
- **Action overlays.** Rule-based, no ML needed: road segments with predicted depth > 0.3 m flagged for closure; simple routing around flooded segments; pump-station activation priority ranked by predicted severity.

### Per-road-segment reporting

Report **per-road-segment flood status** (a topological claim we can support) rather than per-square-metre depth (a metric claim at 10 m resolution that we can't). Join the depth raster to OSM road segments, take a robust statistic (75th percentile depth along the segment), classify into passable / caution / impassable.

---

## 13. PHASE 8 — Only if time remains

- Resolution sensitivity comparison across the candidate grids from open question A — a judge asking *"how do you know 10 m is enough?"* gets a plot instead of a shrug
- FNO upgrade — and note its resolution-invariance is worth more here than in a single-tile project: train coarse across the city, infer fine
- Second city (Hyderabad, Chennai) to prove the pipeline generalises — the strongest possible answer to *"why not just use iFLOWS?"*
- RVCE campus as a familiar-ground zoom target inside the city-wide map for the demo opening

---

## 14. Known limitations — build these into the docs, don't hide them

State each of these ourselves before anyone asks:

1. **30 m DEM cannot resolve a 10 m street.** Mitigated by vector-driven conditioning; not eliminated. Hence per-road-segment reporting.
2. **Bengaluru's drain network is not open data.** Represented as a calibrated sink. Largest single error source. This is a data availability problem, not a modelling one — and surfacing it is arguably the highest-value civic ask this project makes.
3. **Urban flooding is frequently drain-capacity-limited, not terrain-limited.** A blocked drain floods a street that terrain says is safe. We capture topography-driven flooding well and blockage-driven flooding only through calibration.
4. **Infiltration and antecedent soil moisture** are crudely parameterised.
5. **SAR validation degrades in dense urban areas.**
6. **The surrogate inherits every error of the solver that trained it**, plus its own.
7. **Bengaluru is inland**, so we correctly exclude tidal and storm-surge terms that iFLOWS-Mumbai needs. For a coastal city you'd add a tidal boundary condition.
8. **A flood forecast is only as good as the rainfall forecast driving it.** Within the routing horizon (§11) our error is our own — terrain, drains, solver. Beyond it, error is dominated by meteorology we do not control and cannot improve. This belongs **in the UI**, not just in the docs: the provisional rendering of the forecast-driven horizon *is* this limitation, made visible.

---

## 15. Prior art — get this right, it's a likely judge question

| System | What it is | Gap we fill |
|---|---|---|
| **iFLOWS-Mumbai** (MoES/NCCR, 2020) | Full flood warning system, all 24 wards, up to 3-day inundation forecasts. Built with crores of funding, a 20 cm proprietary DEM from MCGM, and field bathymetric surveys. | Exists for Mumbai and Chennai only. Not replicable without a government lab. We use only open data. |
| **Meghasandesha** (KSNDMC, Bengaluru) | Mobile app: live rainfall from ~100 telemetric gauges, drain water-level sensors with colour-coded alerts, safe-route suggestions. | **Monitoring, not prediction.** No physics model, no depth estimation, no forecast. Described in press as *"good for rescue, not prevention."* |
| **Varunamitra** (Karnataka) | Web portal, zone-wise weather dashboard, colour-coded flood severity. | Real-time observation dashboard. Does not forecast where water will accumulate. |
| **UFM Bangalore** (IISc/KSNDMC research) | Urban flood model using proprietary LiDAR terrain. | Research tool. Not real-time, not sub-second, outputs don't reach the public app. |

**The line:** *Bengaluru can tell you it is raining. It cannot tell you which street will be under two feet of water in forty-five minutes. That's the gap.*

---

## 16. First command

```
Read PROMPT.md and CLAUDE.md.

Execute PHASE 0 only.

For open items 1a, 1b and 2-9: actually attempt each check — download the
file, hit the endpoint, try the build. Do not speculate about what is
probably available. Weight 1b highest: it is the closest thing to a true
blocker, because without historical rainfall for a real event there is
nothing to validate against.

For open design questions A-D: these are deliberately unfixed. Do not pick
values because they seem reasonable. Produce benchmarked recommendations —
real cell counts over the real BBMP extent, real timings, real VRAM
measurements. Where a figure depends on physics not yet available (timesteps
per hour depends on CFL, which depends on h_max), report the measured part as
measured and carry the unknown as an explicit named parameter producing a
band — never a point estimate dressed up as a measurement. For C, note the
method (learning curve) and defer the number to Phase 5.

Write findings to OPEN-ITEMS.md with, for each item: verdict (RESOLVED /
BLOCKED / PARTIAL), evidence (URL, file path, error message, benchmark
numbers), and impact on the architecture.

Then judge the gate as ONE question, in the round — do we have enough real,
external, verifiable evidence to validate a flood prediction against a real
event? Name every fallback being invoked and what it costs. Reach one of:
proceed to Phase 1, proceed with a named degradation, or stop and change the
project.

Then STOP and report. Do not begin Phase 1.
```

---

## Reference links

- LISFLOOD-FP 8.1 GPU solvers, GMD 16:2391 (2023) — https://gmd.copernicus.org/articles/16/2391/2023/
- Bates, Horritt & Fewtrell (2010), local inertial formulation — J. Hydrology 387(1–2):33–45
- Physics-Guided Deep Learning for Urban Pluvial Flooding, Water 17(8):1239 (2025) — https://www.mdpi.com/2073-4441/17/8/1239
- FABDEM — https://www.fathom.global/product/global-terrain-data-fabdem/
- Microsoft Global ML Building Footprints — https://github.com/microsoft/GlobalMLBuildingFootprints
- KSNDMC telemetric gauge locations (public domain) — https://data.opencity.in/dataset/karnataka-telemetric-weather-stations-and-rain-gauges
- Bangalore Floods — A Call for Open Data (OpenCity) — https://opencity.in/bangalore-floods-a-call-for-open-data/
- Neural Operator library — https://github.com/neuraloperator/neuraloperator
- Copernicus Data Space (Sentinel-1) — https://dataspace.copernicus.eu/
