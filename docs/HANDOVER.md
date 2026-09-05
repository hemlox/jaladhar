# JALADHAR — Handover

**Written 2026-09-05.** This is the only current project document. It replaces every earlier
report, plan, ledger and writeup (all consolidated here; the originals survive in the pre-squash
git bundle and the workspace archive named in §7). Standing rules for anyone working here are in
[`AGENTS.md`](../AGENTS.md). Edit this file; do not create siblings.

Owner at time of writing: Darshil. Incoming owner: a teammate working primarily with Codex.
Everything below is written so that a person and an agent who have never seen this repo can pick
it up.

---

## 0. Read this first

1. **The system exists end to end and runs.** `./deploy.sh` brings up a dashboard and a routing API
   on a real 3-hour forecast product for the September 2022 Bengaluru event. Verified 2026-09-05
   after the cleanup: every endpoint 200, correct product loaded.
2. **It does not yet clear its own accuracy bar, and the reasons are measured, not guessed.**
   Street-level gate G1 is FAIL (104/399 complaint points hit, lift 1.30 against ≥60% / ≥3.0). The
   causes are properties of Bengaluru's public data (§4), not of the solver.
3. **The demo is honest about what it is — but the narrative given at the internal round was
   not, in three places.** §3 lists exactly what was shown versus what exists. That delta is the
   first to-do list.
4. **The next city is undecided.** Bengaluru is data-capped (no radar, disconnected drain map, DEM
   without lake basins). The pipeline is config-driven; §8 gives the decision matrix and the exact
   switch procedure. Do the one-week reconnaissance in §9 before committing.
5. **Timebox: 2–3 months, one consumer GPU.** §9 is a 12-week plan ordered by information value
   per unit cost, with a definition of done and an evidence file for every step.
6. **Rule 1 — no fabricated data — is the reason this project has anything defensible.** It is
   also the rule most tempting to break when a deadline is close. Read `AGENTS.md` before doing
   anything.

---

## 1. The problem statement (SIH 26085, verbatim)

**Urban Flood Nowcasting System — Drainage and Rainfall Coupling.** Ministry of Earth Sciences /
NCMRWF. Software · Disaster Management.

> Urban flooding in major Indian metros like Mumbai, Delhi, and Chennai has become an annual
> crisis. Traditional Numerical Weather Prediction (NWP) models fall short because knowing how
> much rain will fall does not automatically translate into knowing where the streets will flood.
> Urban flooding is a hyper-local phenomenon dictated by micro-topography, concrete
> imperviousness, and heavily strained, invisible drainage networks. Currently, municipal bodies
> lack real-time, street-level predictive systems...
>
> The challenge is to design a high-resolution, real-time Urban Flood Nowcasting System (0–3 hour
> lead time) capable of predicting street-level inundation before it happens.
>
> Participants must move away from isolated weather models and instead build a coupled
> framework. This system must fuse real-time rainfall nowcasts with high-resolution Digital
> Elevation Models (DEM) and a graph-based mathematical model of the city's underground drainage
> network. By mapping how water flows, accumulates, and surcharges across concrete surfaces and
> drainage nodes, the solution should pinpoint exactly which streets or intersections will flood.
> Develop a pipeline that takes high-resolution rainfall nowcasts (from Doppler Weather Radars)
> and instantly routes that volume across a 2D surface terrain model. Represent the city's
> stormwater drain network as a directed graph (nodes as manholes/inlets, edges as pipes/canals).
> The model must calculate hydraulic capacity and predict where blockages or overcapacity will
> cause backflow onto the streets. A dynamic, web-based GIS dashboard showing real-time,
> street-by-street flooding projections (e.g., water depth estimations in centimeters) with a 0–3
> hour forward-looking window. An API utility that can interface with navigation maps to suggest
> flood-safe alternative routes for emergency services, public transit, and commuters during
> heavy downpours.

**Reading of "nowcasts (from Doppler Weather Radars)":** the *nowcast* is the input; DWR is its
provenance. Consuming IMD's issued nowcast product satisfies this reading. Building radar
extrapolation from volumetric reflectivity is a possible differentiator later, not a requirement,
and raw DWR volumes are not openly available. Both readings are recorded; the first is the
working assumption.

---

## 2. Where the project stands — component by component

Every row states realized state (what is on disk and has executed), not intent. Evidence paths
are relative to the repo; "archive" means the workspace tar in §7.

| Component | Realized state | Evidence |
|---|---|---|
| Terrain conditioning (DEM → hydrologically conditioned 10 m grid, roads/drains burned, buildings as blockage) | **Built, deterministic, seam-asserted (D8 producer ↔ solver stencil).** Bengaluru domain 3615×3521 buffered = 12,728,415 valid cells; canonical 3421×3515 = 12,024,815. Elevation surface fingerprinted before promotion. | `src/jaladhar/terrain/`, `runs/terrain_*/manifest.json`, `docs/elevation-surface-witness-2026-08-24.json` (sha 46dff442…, 36,920,652 B), `data/processed/buffered/elevation.tif` |
| 2D shallow-water solver (local-inertial / ACC, CFL-adaptive, GPU, mass-conserving) | **Built, measured.** Closed water balance residual 1.79e-05 of inflow on the 48 h replay; analytical ladder and ANUGA cross-validation run. | `src/jaladhar/solver/`, `runs/solver/`, `bench/` |
| Forcing adapters | **Built.** `historical` = GPM IMERG v07 Final half-hourly, KSNDMC-alert-anchored (2.66× domain amplification recorded, labelled as anchored, never "gauge forcing"). `forecast` = Open-Meteo NWP (exists, unused in demo). `nowcast` = KSNDMC live gauges (source dead). IMD WFS nowcast adapter: **written, contract-tested, never fetched a real feature — no endpoint configured** (§4.6). | `src/jaladhar/forcing/`, `configs/forcing.yaml`, `configs/contracts/nowcast_input.json` |
| Drain graph | **Built.** BBMP rajakaluve primary+secondary (1,033 raw features, 767.3 km, 370 disconnected components at any snap tolerance) stitched via conditioned-DEM D8 routing into **1,721 nodes / 1,587 edges**: 1,301 observed + 286 synthesised connectors, every edge tagged. 110 components remain post-stitch; outfall-terminating components carry 73.65% of observed length (policy target 95% — not met, recorded). Tertiary layer (~5,800 features) excluded: its KML is corrupt and stitching it would synthesise >90% of its connectivity. | `src/jaladhar/drainage/`, `runs/drain_graph_build/manifest.json` (`node_count`, `edge_count`), `configs/contracts/drain_graph.json` |
| Hydraulic capacity | **Built, cited, bounded.** Rational method Q = C·i·A/360 with i = 62.5 mm/h (Bengaluru 60-min, T=25 yr; CPHEEO 2019 companion table + IMD-station study), checked by Manning at a depth bounded per drain order (owner-supplied design bound, CPHEEO framework). 1,208 edges carry a capacity (p50 0.24, p99 197.35, max 276.55 m³/s); 61 zero-slope edges routing-only; 32 edges outside rational-method validity carry no claim. 70 edges exceed capacity under the design storm. An earlier unbounded solve gave 21,118 m³/s — physically impossible, caught and fixed. | `configs/drainage.yaml`, `runs/drain_graph_build/manifest.json` → `capacity_metrics`, gpkg `capacity_basis` fields |
| Surface ↔ drain coupling | **Built and executed, at tile scope.** Capture → capacity-limited routing → surcharge return, inside the solver timestep loop, with anti-double-count guard and total-water closure (2.7e-07 on the smoke). G4 tile runs pass G2 anti-vacuity. **Not used in the demo product** — owner decision 2026-08-26, because 96.87% of returned surcharge volume lands on reaches with no mapped outlet (§4.4), so no G2 number would be interpretable for Bengaluru. | `src/jaladhar/coupling/`, `configs/coupling.yaml`, `configs/contracts/coupling_iface.json`, `runs/wf2_coupled_smoke/`, `runs/wf2_tile_throughput/manifest.json`, `runs/wf2_coupled_resmoke/manifest.json` + `products/return_ratio_split_v2.json` (restored from archive) |
| 3 h forecast product | **Built.** Issued 2022-09-04T18:40Z (start of the peak-rain window), valid 21:40Z, warm start from the replay state at issue time, 25.94 mm over the window, **17,520 flooded segments**, uncoupled. Driven by rainfall that actually fell (`kind: perfect_rainfall_nowcast`, `rainfall_is_predicted: false`) — it isolates flood-model error from rainfall-forecast error; it is not a nowcast. An earlier window started 10 min after the peak (3.34 mm) and was corrected. | `runs/wf3_uncoupled_3h_forecast_warm_product/manifest.json`; source run manifest (restored) `runs/wf3_uncoupled_3h_forecast_warm/manifest.json` |
| 8-frame series (the thing the dashboard plays) | **Built.** 30-min cadence, t0000000 → t0012600 (last frame is 30 min beyond horizon, labelled), peak t0009000 = 17,953 flooded segments. | `runs/wf8_3h_forecast_frames/manifest.json` |
| Per-street product contract | **Frozen.** Segment status D/F/N, depth band in cm, lead minutes, drain node when measured. Consumed identically by dashboard and routing. | `configs/contracts/depth_product.json`, `src/jaladhar/validation/segment_status_frames.py` |
| Dashboard | **Built.** Self-rendered Canvas basemap (no tile service), LIVE-first with honest empty state, DEMO toggle plays the 8-frame series, watchlist/search/ward context/per-street detail, drains layer (observed solid, synthesised dashed), comparison overlay (§3 item 1), live-refresh POST that runs the tiled solver on a fixed tile. Browser-verified 2026-08-27 (playwright, 0 console errors). 14 UI defects fixed 2026-08-30. | `src/jaladhar/web/`, `runs/wf8_ui/` (archive) |
| Routing API | **Built, refuses by design.** Undirected road graph from OSM centrelines (no one-way/turn data), endpoint-snapped at 2.0 m, no intersection splitting (107k components — cross-city pairs mostly unroutable even dry). Vehicle policy cited (ARR Project 10; passenger car / emergency-heavy; bus_truck BLOCKED — no published limit). Behind the scientific gate: G1 FAIL → `POST /v1/route` → 503. | `src/jaladhar/routing/`, `configs/routing.yaml`, `data/curation/vehicle_wading_policy.json`, `runs/wf6_routing_feasibility/` |
| Latency (G4) | **Measured, PASS at tile scope, FAIL full-domain.** Same window: 12,728,415 cells → 1,569.6 s; 1/16 tile 795,664 cells → **23.07 s** (68× for 16× fewer cells — fewer cells and larger adaptive steps stack). 1/9 tile 51.5 s; 1/4 tile 214.5 s. The full-domain 3 h warm forecast took **58.03 min** (`latency_g4.wall_clock_min`, 3,481.7 s, 12,017,880 cells). | `runs/wf2_tile_throughput/manifest.json` → `anchors`, `tiles.*.driver_report.wall_clock_s`; `runs/wf3_uncoupled_3h_forecast_tile/` |
| Validation harness & gate scoring | **Built.** G1 scorer with 10,000-draw spatial null (seed 42), G3 dual-convention depth bands, ground-truth curation with quote-backed strict rows (15 of 32). | `scripts/score_g1.py`, `runs/wf3_replay2_gates_v7_excluded/g1_score.json`, `data/raw/groundtruth/` |
| Provenance | **Built and enforced.** Manifest written at run start, updated in place; dirty tree refused; producer→consumer seam assertions; every dashboard number resolves to a file. | `src/jaladhar/provenance.py` |
| Neural surrogate, scenario generation | **Not built. Optional.** Tiling already meets latency; the surrogate is behind the gate. | `src/jaladhar/surrogate/`, `src/jaladhar/scenarios/` (stubs) |
| Scheduler (15–30 min refresh loop), storm-tile auto-selection | **Not built.** Both are wrappers around measured components; listed in §9. | — |

---

## 3. What the internal-round demo showed vs what the system does

The internal round (college, late August 2026) was passed. Three things were said that the system
does not do. The incoming owner must know them so that no external presentation repeats them, and
because they are the shortest description of the remaining work.

| # | Shown / said | What is actually there | Closes when |
|---|---|---|---|
| 1 | Comparison panel: "3 h forecast vs Sentinel-observed extent" | The right panel is our own 48 h replay at 21:30Z (`t0077400`, 29,350 flooded). It is a **sim-vs-sim** agreement: every segment the 3 h forecast floods, the replay floods too (POD 1.00); the replay floods 11,830 more (40.3% right-only). A real Sentinel-1 comparison exists (`runs/wf7_obs_vs_pred_20220905/`) but the SAR instrument was ruled invalid for urban flooding (§4.7), so it is not a validation. | An extent instrument validated on urban known positives for the chosen city's event (G5). |
| 2 | "Nowcast-driven 3 h simulation" | Driven by IMERG rainfall that actually fell over the window (perfect nowcast). No issued nowcast has ever forced a run. | IMD WFS wired and a run forced by an issued product (§9 week 1); later, a radar-derived field. |
| 3 | LIVE mode "pulls the live nowcast" | LIVE refresh runs `scripts/run_windowed_forecast_3h.py` on a fixed tile (`LIVE_TILE` hardcoded in `web/app.py`) using the historical forcing config. The IMD adapter is never instantiated; `configs/forcing.yaml` has no `nowcast_input:` section. Verified live on 2026-08-28 that the IMD WFS endpoint itself works and that Bengaluru's five stations report one identical category (§4.6). | Same as 2. |
| 4 | Surcharge / backflow modelled | True in code and executed at tile scope; the shipped product is the **uncoupled** baseline (owner decision, §4.4). The causal panel correctly shows PREDICTED overcapacity, never MEASURED surcharge. | A coupled event run in a city whose drain map reaches outfalls. |
| 5 | Routing "suggests safe routes" | Refuses (503) while G1 fails — scripted as the honest feature. Also structurally weak: 107k road components, no intersection splitting. | G1 pass + series-aware routing + intersection splitting. |
| 6 | "72% agreement" | No such number exists in any manifest. The nearest real figure is POD(left|right) = 0.597 in the sim-vs-sim comparison. Do not use 72%. | — |

---

## 4. Measured findings (Bengaluru)

These are findings (measured, file-backed), each with the reading kept separate (R1).

### 4.1 The original gate did not pass, and the deficit is structural

Two 48 h replays of the 4–6 Sept 2022 event: BBMP complaint points hit 45.86% → 59.90% (fail on
rate, one hit short); ground-truth depth in band 0/16; RMSE 0.92 m against ~0.25 m bands. The
calibration loop (18.61 GPU-h) moved the depth loss term 1.3%; every parametric lever at its
physical bound buys <10% of the gap. Anchored forcing over-supplied the Varthur cell 2.74× against
the gauge alert with infiltration disabled and 15/16 points still read below band. *Reading:* no
tuning closes this; the input data lacks something the physics needs. §4.2 names it.

### 4.2 The DEM encodes lake water surfaces, not basins

Varthur: rim 859.72 m, interior p50 861.00 m (above the rim), interior std **0.16 m**, fraction of
interior below rim 0.000. Bellandur: std 0.20 m, 0.000 below rim. Identical in raw, pre- and
post-conditioning — conditioning exonerated. An acquisition-time water surface, not a basin: the
fill→spill mechanism is structurally absent. Applies to FABDEM and GLO-30 alike. **Acceptance check
for any new city's DEM: interior-below-rim fraction and interior std at every major water body.**

### 4.3 The public drain map has geometry only and is not a network

Attributes: `OBJECTID`, `Length` — no width, depth, invert, material, gradient. 370 disconnected
components at snap tolerances 0.5–10 m (raising tolerance 20× removes 3). Stitched via D8 to
1,721/1,587 (§2). Capacity had to come from a cited design standard, not measurement. **Acceptance
check for a new city: component count and outfall-terminating length share before any coupling.**

### 4.4 Most surcharge lands where the map ends

Coupled re-smoke: 485 nodes surcharged, 188,014 m³ returned. Split by whether the receiving reach
connects to an outfall: **3.13% outfall-terminating / 96.87% no-outlet** (5,892.6 / 182,121.5 m³),
definition-proof — extending "outfall" to include lakes and the domain boundary and sweeping five
distance tolerances moved it from 2.72/97.28 to 3.13/96.87. Ground-truth attribution radius 40 m
(pre-registered from geometry, no sweep): 1/24 points within a drain node. *Reading:* the reaches
genuinely terminate (consistent with documented rajakaluve encroachment). Consequence: a coupled
Bengaluru demo would be measuring network truncation, so the demo ships uncoupled.

### 4.5 Speed: tile to the storm

Numbers in §2 (Latency). *Design:* the operational unit is a tile sized to the active rain cell
and its downstream catchments, never the whole city; never coarsen the grid (a street is one cell
at 20 m). Storm-tile selection from the rainfall field is unbuilt (§9).

### 4.6 What a nowcast actually looks like here

IMD's public nowcast (`https://reactjs.imd.gov.in/geoserver/ows`, WFS layers
`imd:NowcastWarningDistrict`, `imd:NowcastWarningStation`, geometry field `geom`, axis order
lat/lon in WFS 2.0) issues **one category per district or station, held constant over a
`toi`→`vupto` window** (e.g. 1600→1900). Fields `cat1…cat19` are flags — the public UI renders
`cat1` as "Light rain: < 5 mm/hr"; other stations that day carried `cat2, cat4, cat6`
simultaneously; `Color` 1–4 maps to No warning / Watch / Alert / Warning. A bounding-box query on
2026-08-28 returned **five stations in the Bengaluru metro (HAL, BIAL, City, GKV Kendra,
Ramanagara) — all identical**. So for Bengaluru the nowcast is spatially and temporally flat: any
street-to-street variation in output comes from terrain and drains acting on uniform forcing.
Bengaluru has no Doppler radar (nearest ~290 km). The named cities in the problem statement do.
This is the single strongest argument for the city switch.

### 4.7 The extent instrument is invalid for urban flooding

Sentinel-1 dual-pol GRD and HyP3 coherence/amplitude both fail the pre-registered rule (recall
>70% at Varthur and Bellandur, dry FPR <1%): best candidates 42–57% recall. Spatial join showed
only 7.6% of predicted-flooded segments within 50 m of open water (median 1.4 km) — a hypothesis
that contamination explained the low CSI was tested and refuted. No Sentinel-derived CSI is
admissible (gate G5 stays open). A new city needs an instrument validated on *urban* known
positives — municipal complaint points, geotagged reports, or a SAR method proven on that event.

### 4.8 Ground truth is scarce and must not be padded

32 curated rows, 31 event-location eligible, **15 with a quote that supports the depth band**.
Below the 30–50 target; recorded, not padded. GT_14 is from a different flood (2022-08-30) and
must not be scored. BBMP's 399 flood-prone points are the segment-level classification truth.

---

## 5. The gate — G1–G5 (signed 2026-08-24, carried unchanged)

Thresholds were written before any number existed and each must be able to fail (V5).

| | Criterion | Bengaluru status |
|---|---|---|
| **G1** street classification | per-segment hit rate ≥ 60% against complaint points **and** null-model lift ≥ 3.0 (N = 10,000 spatial null, seed recorded) | **FAIL** — 104/399 = 26.1%, null mean 20.0%, lift 1.30 (segment-mediated; point-mediated 239 hits is a different metric, disclosed in the file) |
| **G2** surcharge mechanism | ≥ 50% of GT points on/adjacent to a drain reach reproduced as surcharge-driven, traceable to a named node; any result carries the outfall / no-outlet split beside it | anti-vacuity PASS at tile scope; **not scoreable** (§4.4) |
| **G3** depth | per-segment bands in cm; ≥ 40% of held-out strict rows in band under both 3×3 local-max and exact-cell conventions; disagreement is the reported result | **BLOCKED** — retrospective only, 0–1 of 16 in band |
| **G4** latency | rainfall input → published depth field, 3 h horizon, ≤ 10 min wall clock | **PASS at tile scope** (23–215 s), FAIL full-domain |
| **G5** extent | unclosed until an instrument is validated on urban flooding | **OPEN** |

Dashboard and API may render realized state alongside the gate; they may not ship a number the
gate has not produced.

---

## 6. Architecture and repo map

### 6.1 Source (`src/jaladhar/`)

| module | files | does |
|---|---|---|
| `terrain/` | 15 | boundary + DEM fetch (config-driven source order, `dem_is_dtm` flag), OSM roads/water/buildings/drains, conditioning (whitebox breach, D4 capped-hybrid), derived rasters, build orchestrator, QC |
| `solver/` | 13 | ACC stencil, CFL step control with rejection, boundary outflux, checkpointing, mass ledger, config resolution pre-flight |
| `forcing/` | 9 | IMERG adapter (+anchoring), Open-Meteo adapter, KSNDMC adapter, **IMD WFS adapter (`nowcast.py`, unwired)**, interval-rate table shared with the solver |
| `drainage/` | 8 | KML ingest, endpoint snapping, D8 stitching, cycle break, junction split, capacity (rational + Manning), graph I/O + fingerprint |
| `coupling/` | 7 | capture, routing, surcharge return, closure ledger, kill thresholds, falsifier |
| `validation/` | 11 | event replay driver, segment status frames, G1/G3 scoring, GT classification, Sentinel-1 fetch/mask (instrument invalid, kept) |
| `web/` | 9 | dashboard server (`app.py`), frame series loader (`series.py`), forcing view, static Canvas UI (`static/`) |
| `routing/` | 5 | road graph from centrelines, vehicle policy, gate check, API (`api.py`) |
| `analysis/` | 3 | water tracing / catchment budgets |
| `provenance.py` | 1 | manifest lifecycle, dirty-tree refusal, seam assertions |

### 6.2 Configuration (`configs/`)

`domain_bengaluru.yaml` (CRS, resolution, boundary source + SHA, DEM tiles + licence, roads,
conditioning depths, buildings treatment, roughness, water, paths) · `solver.yaml` ·
`forcing.yaml` (modes are mutually exclusive) · `drainage.yaml` (capacity rule + citations) ·
`coupling.yaml` · `routing.yaml` · `compute.yaml` (device, VRAM, budget ceiling) ·
`validation.yaml` · `analysis.yaml` · `context.yaml` · `terminal_definition.yaml` (outfall
definition used in §4.4). **`contracts/`** — four frozen JSON interfaces: `drain_graph`,
`coupling_iface`, `depth_product`, `nowcast_input`. Change a contract only with a version bump
and a consumer-side assertion.

### 6.3 Data tiers

- **Ships in git** — `data/seed/` (5.7 MB): BBMP flood-prone points (399, one at null island —
  real defect, left in), BBMP drains 2022 tarball, 24-point legacy GT, news corpus, SHA256SUMS.
  `python scripts/bootstrap_data.py` unpacks and verifies.
- **Re-fetchable** — boundary + DEM (`python -m jaladhar.terrain.fetch`), OSM layers
  (`python -m jaladhar.terrain.{water,roads,drains,buildings}`), IMERG (`EARTHDATA_TOKEN` + GES
  DISC EULA click), Sentinel-1 (`CDSE_*`). Raw inputs for Bengaluru are on disk under
  `data/raw/` (~170 MB) in the archived checkout.
- **Derived** — `data/interim/` (827 MB: roads centrelines gpkg, segment lookup, ward context,
  DEM intermediates), `data/processed/` (1.2 GB: buffered elevation and rasters). Rebuilt by
  `python -m jaladhar.terrain.build`.
- **Curation** — `data/curation/vehicle_wading_policy.json` + evidence.

### 6.4 Runs kept on disk (12 GB) and why

| run | why kept |
|---|---|
| `wf8_3h_forecast_frames/` (793 MB) | the series the dashboard plays |
| `wf3_replay2_uncoupled_baseline_frames_v2/` (11 GB, 97 frames) | the comparison overlay's right side; `FrameSeries` warms all frames on init, so it cannot be pruned to one frame without a code change |
| `wf3_uncoupled_3h_forecast_warm_product/` | routing API's depth product; the 3 h forecast |
| `wf3_replay2_gates_v7_excluded/` | G1 score bound into `/health` |
| `wf8_sim_vs_sim_3h_vs_replay/`, `wf7_obs_vs_pred_20220905/` | comparison products (§3 item 1) |
| `drain_graph_build/` | the drain graph gpkg + manifest |
| `wf2_coupled_smoke/`, `wf2_tile_throughput/`, `wf2_falsifier_preregistration/`, `wf3_uncoupled_3h_forecast_tile/` | coupling execution and G4 evidence |
| `live_3h_20260826T185826Z/` | one real LIVE-refresh tile run |
| `terrain_*`, `solver*`, `forcing*`, `groundtruth`, `validation_scoring`, `promote_phase3_elevation`, `wf2_gt_*`, `wf6_*`, `wf7_routing_fix` | small provenance manifests for the data on disk |

Everything else (48 h replay depth rasters, SAR products, underpass investigations, superseded
gate versions, UI screenshots) is in the archive only. Manifests carry pre-squash `git_sha`
values — they resolve in the bundle, not in this history (see `AGENTS.md` → Git).

### 6.5 Scripts (`scripts/`, 25)

Demo: `demo/launch_demo.py`, `demo/run_demo.sh`, `demo/rehearse_demo.py`,
`demo/smoke_route_around.py`. Pipeline: `bootstrap_data.py`, `run_phase3_validation.py`,
`run_segment_validation.py`, `run_gt_classification.py`, `run_water_tracing.py`,
`run_sar_water_investigation.py`, `run_windowed_forecast_3h.py` (the 3 h forecast, tile-capable),
`build_frame_series_admission.py`, `score_g1.py`, `adjudicate_v6_adoption.py`,
`sim_vs_sim_comparison.py`. Coupling evidence: `wf2_*.py`. Tooling:
`repair_whitebox_permissions.py`. Twenty Bengaluru-only one-offs (underpass search, Mapillary,
KGIS, SAR segment product) were removed on 2026-09-05; they are in the bundle.

### 6.6 Tests (`tests/`, 67 files)

Run: `CUDA_VISIBLE_DEVICES="" .venv/bin/python -m pytest -q -p no:cacheprovider`. Some tests are
intentionally BLOCKED/PARTIAL (V7) and print what would close them.

**Result on the trimmed workspace, 2026-09-05** (`pytest_final.txt`, 9 min 39 s CPU-only):
**826 passed, 58 skipped, 1 xfailed, 20 failed, 20 errors.** The 40 non-passes split exactly:

| n | class | what | action |
|---|---|---|---|
| 25 | **missing artifact** | realized-state tests bound to run directories that now live only in the archive: `runs/goal_d_replay2_final_config/` (6 errors + 1 failure), the v5/v6 flat event-maximum products (`tests/web/test_wf6_watchlist.py`, `test_wf6_intersections.py`, 14 errors; `test_wf6_storage_exclusion.py`, `test_wf6_dropin.py`), IMERG granules (`tests/web/test_wf6_forcing.py`, 3). On a fresh clone every such test errors by design (V1: they check bytes, not declarations). | do not delete them; when the new city's runs exist, repoint the fixtures; until then mark BLOCKED with the missing path (V7) |
| 11 | **terrain invariants vs the promoted surface** | `tests/test_terrain_conditioning.py` (7) and `tests/test_terrain_grid.py` (4) were written against clginternal's D4 capped-hybrid conditioning; they now run against the elevation surface promoted from `p3-final` on 2026-08-24 and fail on substance: 67,781 D4 pits outside retained basins (expected 0), max cut 4.50 m against a 2.6 m limit, retained volume 70.83 Mm³ against a 20–35 band, 227,908 of 681,100 retained-basin cells modified, manifest `connectivity` None and no `d4_capped_hybrid_diagnostics` block. **This is a real, pre-existing seam debt** (open item O-13): the surface every 2026 result stands on does not satisfy the Phase 1 invariants as written, and its manifest lacks the V8 fields. Nothing today touched `data/processed`. | resolve for the new city, not for Bengaluru: the new terrain build must pass these tests or the invariants must be re-derived and re-signed before the first solver run |
| 4 | **drain loader test/code disagreement** | `tests/drainage/test_loader.py`: feature count 5 vs 6 (×2), `reach_id` must follow OBJECTID sort, one coordinate-transform approx. Pre-existing; `src/` was not changed on 2026-09-05. | fix in week 1 (V6: say which of code/test/spec was wrong) |

The suite is deliberately a mix of unit tests and realized-state checks; a green run requires the
artifacts, not just the code.

---

## 7. How to run, restore, and back up

```bash
# in a restored checkout
./deploy.sh --check          # verifies all artifacts the demo loads; starts nothing
./deploy.sh                  # dashboard 127.0.0.1:8501, routing 127.0.0.1:8502 (routing binds ~40 s after start)
./deploy.sh --rebuild-venv   # if the venv is broken (needs network, ~10 min)
```

**Archives (kept by Darshil, outside the repo):**

- `jaladhar-backup-2026-08-29.tar.zst` (6.3 GB → 23 GB): full checkout incl. `runs/`, `data/`,
  `.venv`, git. Verified to extract and serve the demo from a different path. Extract to real disk
  (`/tmp` is tmpfs and will fail): `tar -I zstd -xf <tar> -C <dir>`, then copy `.env` in.
- `jaladhar-pre-squash-2026-09-05.bundle` (40 MB): every branch and commit before the history
  squash. `git clone <bundle> old-history` to browse.
- `.env` is excluded from both. Keys: `CDSE_ACCESS_KEY`, `CDSE_SECRET_KEY`, `EARTHDATA_TOKEN`,
  `MAPILLARY_TOKEN`. Needed only for satellite re-acquisition.

**Fresh clone without the archive:** `uv venv --python 3.11 && uv pip install -e .` (pick a torch
backend first: `--torch-backend=cpu` or `cu124`), `python scripts/bootstrap_data.py`, then the
fetchers in §6.3. The demo needs the `runs/` products, which are not in git — build them
(`scripts/run_windowed_forecast_3h.py`, `build_frame_series_admission.py`) or take them from the
archive.

---

## 8. The city switch

### 8.1 What the city must have (in order of how hard it is to fake — none may be)

1. **Doppler radar coverage and IMD station-level nowcasts** — the only route to a spatially
   varying 0–3 h rainfall input. Verify with the WFS bounding-box query in §4.6 and IMD's DWR
   network list.
2. **A public storm-drain GIS that reaches outfalls.** Measure §4.3's two numbers on day one:
   component count at 2 m snap, and outfall-terminating length share. If the share is
   Bengaluru-like (~74%), coupling results will again be truncation.
3. **A DEM whose water bodies have basins** — §4.2's check. FABDEM/GLO-30 cover every candidate;
   the check decides, not the source name.
4. **A documented flood event with street-level ground truth** — municipal complaint points
   (the G1 truth), dated depth reports with quotes (G3), and rainfall for the event (IMERG works
   anywhere; gauge data is a bonus).
5. **Administrative boundary + wards** for the dashboard context layer.
6. **Boundary conditions Bengaluru never needed.** Every candidate below is coastal or riverine.
   Mumbai and Chennai flooding is tide-gated (outfalls lock at high tide); Guwahati is Brahmaputra
   backwater. The solver has an open-outflow boundary only. **A stage-hydrograph boundary
   condition (tide or river level) is new solver work — budget it.**

### 8.2 Candidates

| | Radar / IMD stations | Drain GIS | Terrain | Ground truth events | Boundary | Notes |
|---|---|---|---|---|---|---|
| **Chennai** | DWR present (public knowledge — verify with WFS query) | GCC stormwater drain layers exist; openness **unverified** | flat coastal plain — drains dominate, which is the coupling story | 2015, 2023 (Michaung) extensively documented | tide + Adyar/Cooum river stages | lean: the physics the problem statement describes is most visible here |
| **Mumbai** | two DWRs (public knowledge — verify) | BMC/BRIMSTOWAD network; openness **unverified** | steep hills + reclaimed flats; tide-dominated | 2005, 2017, 2019, 2021 | tide-gated outfalls, Mithi river | alternate: best rainfall input, hardest boundary condition |
| Delhi | DWR present | unknown | inland, Yamuna floodplain | 2023 | Yamuna stage | named in the statement; drain data unknown |
| Assam / Guwahati | Mohanbari radar is far; station coverage thin | unknown | riverine | annual | Brahmaputra backwater | **rejected 2026-08-22**: an operational FEWS already exists for Guwahati and Brahmaputra hydrological data is classified |
| "General approach" | — | — | — | — | — | make every fetcher bbox/config-driven and ship a `domain_<city>.yaml` template — **do this anyway**, but it demonstrates nothing without one validated city (V7) |

**Recommendation:** run the one-week reconnaissance in §9 on Chennai and Mumbai in parallel, decide
on evidence, and build the general `domain_<city>.yaml` path while doing it. Lean Chennai unless
its drain GIS turns out closed and Mumbai's open.

### 8.3 Exactly what changes

Stays untouched: `solver/`, `coupling/`, `drainage/` algorithms, `web/`, `routing/`, contracts,
provenance, tests. Bengaluru-specific literals to hunt down and move to config (known: `LIVE_TILE`
in `web/app.py`; ward-vintage `"2022"` fallbacks in `web/static/app.js` and `engine.js`; district
name filter in `forcing/nowcast.py`; `bengaluru_bbox` in `forcing.yaml`).

Changes, in order:

1. `configs/domain_<city>.yaml` — copy `domain_bengaluru.yaml`; replace CRS (UTM zone), boundary
   source + SHA + expected feature count/area, DEM tile ids/urls for the bbox, expected median
   elevation band, roads/buildings quadkey.
2. `configs/forcing.yaml` — add `nowcast_input:` (endpoint, layer, station filter or bbox,
   `interval_minutes`, courtesy interval) per `contracts/nowcast_input.json`; set the historical
   IMERG window for the validation event.
3. Drain ingest — a new reader in `drainage/` for the city's format (shapefile/GeoJSON/KML) mapping
   to the `drain_graph` contract; everything downstream is unchanged.
4. Ground truth — `data/raw/groundtruth/<city>_points_v2.csv` in the curated schema (quote-backed
   strict rows only), complaint points for G1.
5. Boundary condition — stage hydrograph adapter + solver boundary type (new).
6. `configs/context.yaml` — ward layer and vintage for the dashboard.
7. Rebuild: fetch → terrain build → drain graph → capacity → 3 h forecast on a tile → products →
   `./deploy.sh --check`.

### 8.4 Definition of done for the switch

The two Bengaluru findings are the acceptance tests: (a) DEM interior-below-rim fraction > 0 with
interior std that varies at every major water body; (b) drain graph outfall-terminating share
≥ 95% of observed length **or** a recorded owner decision on what to do with the remainder. Plus:
an issued IMD nowcast has forced at least one tile run end to end, and `./deploy.sh --check` passes
on the new city's products.

---

## 9. Roadmap — 12 weeks, one GPU

Ordered by information value per unit cost. Every step has a definition of done and the file that
proves it. Weeks are elapsed time for one person plus agents; the GPU is the serial resource.

| Wk | Work | Done when | Evidence |
|---|---|---|---|
| 1 | **Reconnaissance, two cities in parallel.** For each: WFS station count inside the metro bbox and whether they differ on a rainy day; drain GIS located, licence read, first-pass component count and outfall share; DEM §4.2 check on the two largest water bodies; GT event chosen and complaint-point source identified; tide/river data source identified. **Wire the IMD adapter** (`nowcast_input:` + endpoint) and make one real fetch for Bengaluru — cheap, city-independent, closes §3 items 2–3 at the ingestion level. **Decide the city.** | decision table with a number in every cell; one real IMD feature in a manifest | `docs/HANDOVER.md` §8 updated; `runs/forcing_nowcast/<ts>/manifest.json` |
| 2–3 | **Acquire and build terrain.** Boundary, DEM, OSM layers, buildings; `terrain.build`; §4.2 check on the built surface; elevation witness written before promotion. Start GT curation (quote-backed). | terrain manifests complete; water-body check recorded; ≥ 30 strict GT rows or the shortfall recorded | `runs/terrain_*/`, `docs/elevation-surface-witness-<city>.json` |
| 3–4 | **Drain graph.** New reader → stitch → capacity with a cited local design intensity (IDF for the city; if none, CPHEEO with the uplift band). Measure components and outfall share (§8.4 b). Observed/synthesised tagging as before. | graph manifest with `node_count`, `edge_count`, outfall share; capacity partition closes arithmetically | `runs/drain_graph_build/manifest.json` |
| 4–5 | **Boundary condition.** Stage-hydrograph adapter (tide table or river gauge) and solver boundary type; red-tested on an analytical case (V5). | test red under mutation, then green; smoke on a coastal tile | `tests/solver/test_stage_boundary.py`, `runs/solver_probe/` |
| 5–7 | **Event replay + coupled runs.** Historical IMERG for the event; uncoupled 48 h replay for the baseline (budget-gated); coupled runs at tile scope first, then event scope if the outfall share allows. G2 scored with the split beside it. | replay manifest completed; G2 number with outfall split; total-water closure ≤ 1e-4 | `runs/<city>_replay/`, `runs/<city>_coupled/` |
| 7–8 | **Validation.** G1 with spatial null; G3 dual-convention; G5 instrument attempt validated on urban known positives (complaint points or geotagged reports) before any CSI is quoted. Verdict written with a falsifier (R5). | `g1_score.json`, `g3` block, G5 instrument validation record | `runs/<city>_gates/` |
| 8–9 | **Operations.** Storm-tile selection from the nowcast field (categories → bbox of affected stations + downstream catchments); scheduler loop (fetch → tile → run → product → dashboard picks up), 15–30 min cadence; dashboard city context; routing series-aware + intersection splitting. | one unattended 6 h scheduler soak with ≥ 12 products; routing returns a route on a dry frame and refuses on a flooded one | `runs/live_3h_*/`, soak log |
| 10 | **Independent review gate** (AGENTS.md). Reviewer has no stake; every number re-derived from files. | review report with reconciled arithmetic | `docs/HANDOVER.md` §10 updated |
| 11–12 | **Presentation + buffer.** Rewrite the pitch from realized state only. Rehearse the honest answers in §3. | — | — |

**If G1 still fails in week 8:** that is a result, not a failure of the project. The deliverable
becomes "coupled framework + the measured reason the public data caps accuracy in city X too" —
which is what Bengaluru already proved once, and which the problem statement's own framing
("invisible drainage networks") predicts.

---

## 10. Open items

| # | Item | State | Owner action |
|---|---|---|---|
| O-1 | IMD WFS adapter unwired; LIVE mode uses historical forcing on a hardcoded tile | OPEN — week 1 | wire `nowcast_input:`, remove `LIVE_TILE` literal |
| O-2 | City decision | OPEN — week 1 | evidence table per §8 |
| O-3 | Stage-hydrograph boundary condition | NOT BUILT — needed for every candidate city | §9 wk 4–5 |
| O-4 | G5 extent instrument | OPEN since Phase 3 | validate on urban known positives before quoting any CSI |
| O-5 | Routing graph: 107k components, no one-way/turn data, event-max blocking | KNOWN LIMITATION | intersection splitting; series-aware blocking |
| O-6 | 61 Bengaluru segments read 301–369 cm after storage-water exclusion | ANOMALY, carried, unexplained | do not suppress; irrelevant after city switch but keep the pattern check |
| O-7 | Drain-graph outfall policy (≥95% observed length) not met for Bengaluru (73.65%) | RECORDED | becomes §8.4 acceptance for the new city |
| O-8 | Tertiary drain KML corrupt | LOW | irrelevant after switch |
| O-9 | Scheduler, storm-tile selection | NOT BUILT | §9 wk 8–9 |
| O-10 | Rotate the sudo password once exposed in a historical transcript | OPEN — security | owner, outside the repo |
| O-11 | Legacy manifests reference pre-squash SHAs | BY DESIGN | resolve in the bundle; never rewrite |
| O-12 | `anuga` in `pyproject.toml` dependencies is heavy and only used by the analytical ladder | DEBT | move to an optional extra |
| O-13 | Promoted elevation surface fails 11 Phase 1 terrain invariants and its manifest lacks the V8 seam fields (§6.6) | PRE-EXISTING DEBT since 2026-08-24 | new-city terrain build must pass them or re-sign the invariants before any solver run |

---

## 11. Working with Codex — kickoff

Paste this as the first message of the incoming owner's Codex session (edit the city once decided):

> You are joining JALADHAR, an urban flood nowcasting system for SIH 26085. Read `AGENTS.md` fully
> — its rules are binding, especially rule 1 (no fabricated data) and V1 (realized state, not
> declared). Then read `docs/HANDOVER.md` end to end. Do not create any new document; edit the
> handover. Before changing anything run `./deploy.sh --check` and the test suite and paste both
> outputs. Your first task is §9 week 1: wire the IMD WFS nowcast adapter (`nowcast_input:` in
> `configs/forcing.yaml` per `configs/contracts/nowcast_input.json`, endpoint
> `https://reactjs.imd.gov.in/geoserver/ows`, layer `imd:NowcastWarningStation`, geometry field
> `geom`, lat/lon axis order) and make one real fetch that lands in a manifest under
> `runs/forcing_nowcast/`. Report what was measured separately from what was asserted. Stage
> explicit paths only; never `git add -A`; commit only when I say.

How Darshil worked with agents, so the incoming owner can keep what worked: the reviewer never
implements (if the reviewer edits solver code, nobody is left to catch invented evidence); agents
report in two sections — what was measured, what was asserted — and the reviewer checks the
arithmetic before the conclusions; every workflow ended with a scheduled-vs-realized audit (what
did the plan say would exist, what exists on disk, what is the delta); anything an agent claims to
have run is re-verified from files, not from the report.

---

## 12. Appendix

### 12.1 Numbers registry

| number | meaning | file |
|---|---|---|
| 12,728,415 / 12,024,815 | buffered / canonical valid cells, Bengaluru 10 m | terrain manifest, `elevation-surface-witness` |
| 1,721 / 1,587 / 1,301 / 286 | drain nodes / edges / observed / synthesised | `runs/drain_graph_build/manifest.json` |
| 1,208 · max 276.55 m³/s · 70 | capacity edges · max capacity · edges over design capacity | same, `capacity_metrics` |
| 370 → 110 | drain components raw → post-stitch | same |
| 73.65% | observed length on outfall-terminating components | same, `component_policy` |
| 3.13% / 96.87% | surcharge volume on outfall / no-outlet reaches | `runs/wf2_coupled_resmoke/products/return_ratio_split_v2.json` (restored from archive) |
| 17,520 · 25.94 mm | 3 h forecast flooded segments · window rainfall | `runs/wf3_uncoupled_3h_forecast_warm_product/manifest.json` |
| 17,953 | peak flooded segments in the 8-frame series (t0009000) | `runs/wf8_3h_forecast_frames/manifest.json` |
| 104/399 · 1.30 | G1 hits · lift (segment-mediated) | `runs/wf3_replay2_gates_v7_excluded/g1_score.json` |
| 23.07 s / 1,569.6 s | 1/16 tile / full domain, same window | `runs/wf2_tile_throughput/manifest.json` |
| ~58 min | full-domain 3 h warm forecast | `runs/wf3_uncoupled_3h_forecast_warm/manifest.json` → `latency_g4.wall_clock_min` |
| 1.00 / 0.597 | sim-vs-sim POD(replay\|forecast) / POD(forecast\|replay) | `runs/wf8_sim_vs_sim_3h_vs_replay/comparison.json` |
| 0.16 m · 0.000 | Varthur interior std · fraction below rim | archive: water-tracing report |
| 1.79e-05 | 48 h replay mass residual / inflow | archive: replay #2 manifest |
| 5 identical stations | IMD WFS bbox 12.6–13.3 N, 77.2–77.95 E on 2026-08-28 | live query, §4.6 |

### 12.2 Dashboard design tokens (for whoever continues the UI)

Ground `#05070D` / `#0A0E17` / panels `#10151F` / hairline `#1E2634`; ink `#E9EDF5` /
`#94A0B4` / `#5A6579`; dry road `#232B3A`, lake `#0C2438`, drain `#33415A`. Depth ramp is the
only saturated colour: `#22D3EE` (0.10–0.15 m) → `#FACC15` (–0.30) → `#FB923C` (–0.50) →
`#EF4444` (>0.50); surcharge `#F43F5E`; accent `#38BDF8`. Glow width/blur scale with depth,
additive compositing. System font stack; **every number `tabular-nums`**. Canvas 2D, no map
library, no tile service.

### 12.3 References actually used

Bates, Horritt & Fewtrell (2010) J. Hydrol. 387:33–45 — local-inertial formulation ·
Fan et al. (2017) Adv. Meteorol. 2819308 — coupled 1D–2D urban flooding · CPHEEO (2019) Manual on
Storm Water Drainage Systems, MoHUA — capacity basis · Rossman (2015) SWMM 5.1 User's Manual,
EPA/600/R-14/413 · Shand, Cox, Blacka & Smith (2011) ARR Project 10 Stage 2 — vehicle stability ·
Pulkkinen et al. (2019) GMD 12:4185 — pysteps, if radar extrapolation is ever built.

### 12.4 History note

Development ran 2026-08-14 → 2026-09-05 in 54 commits, squashed on 2026-09-05 into phase-level
commits for a readable history. Contributors: Darshil T. (owner), Adithya D Rao (provenance /
validation hardening, 2026-08-20), with Claude Code and Codex agents under the review discipline
in `AGENTS.md`. The full pre-squash history is in the bundle (§7).
