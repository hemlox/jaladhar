# OPEN-ITEMS.md — JALADHAR Phase 0

External unknowns and open design questions. Every entry carries a **verdict**, the **evidence** behind it, and its **impact on the architecture**.

Status as of **2026-08-14**. Environment verified the same day (see `CLAUDE.md`).

**Verdict key:** RESOLVED · PARTIAL · BLOCKED · METHOD ONLY

---

## Gate verdict

> **The gate asks one question: do we have enough real, external, verifiable evidence to validate a flood prediction against a real event?**

**Answer: PROCEED WITH A NAMED DEGRADATION.**

Terrain, validation labels, and observed flood extent are all secured with real, downloadable, correctly-licensed data. **The one genuine gap is item 1b — historical gauge rainfall for the September 2022 event.** No public programmatic route to it exists; every date parameter the live API accepts is silently ignored.

Per the reframed gate in `PROMPT.md` §5, the fallback is being invoked and its cost stated rather than waved through:

| Precondition | Satisfied by | Status |
|---|---|---|
| **Usable terrain** | Copernicus GLO-30 (open) — FABDEM available but non-commercial (item 2) | ✅ secured |
| **Sept 2022 event forcing** | **GPM IMERG, downloaded and verified to capture the event** — gauge records unavailable (item 1b) | ⚠️ **degraded but confirmed working** |
| **Validation labels** | 385 pre-geolocated BBMP flood points (item 5) + Sentinel-1 pair on disk (item 6) | ✅ secured, two independent sources |

Everything above is now **on disk and verified**, not merely identified. The forcing fallback has been tested rather than assumed: IMERG records 39.2 mm areal-mean over BBMP on 5 Sept 2022, peaking at 14.5 mm/hr in the evening (item 8). The event is visible in the data we will actually use.

**What the degradation costs, measured exactly.** The 717 km² BBMP extent falls inside a **3 × 3 block of IMERG cells — nine values for 198 wards**, ~22 wards each. So:

- **Per-ward rainfall attribution is not defensible.** Ward-level *depth* output remains meaningful (terrain routes water at 10 m from whatever rain falls), but ward-level *rainfall input* does not. Say this before a judge asks.
- September 2022 was convective with strong spatial variation — precisely the structure nine cells cannot resolve.
- The city-wide CSI in Phase 3 will be depressed by forcing error that is not the solver's fault. **This is why `CLAUDE.md` requires the two error budgets reported separately** — solver-vs-observation carries forcing error that surrogate-vs-solver does not.

**The degradation has a live path to closure, already running.** Continuous 15-minute capture of the 266-gauge network began 2026-08-14 (item 1a). It is monsoon season. **If any significant flooding occurs in Bengaluru during the capture window, we get a validation event at full gauge resolution** — which does not depend on September 2022, on IMERG, or on item 1b in any way, and would be a strictly *better* validation than the one this gate currently rests on.

So the degradation above is the **floor**, not the expectation. Three things could raise it, in descending likelihood: our own capture catching an event; KSNDMC answering a data request; the NRSC inundation layer (item 4) arriving with credentials.

**Not a blocker:** the gate does not fail. We can force a real event with real observed rainfall, and score it against two independent real label sets.

---

## Items

### 1a — Programmatic API for KSNDMC *live* gauge readings? — **RESOLVED**

**A working public JSON API exists.** Undocumented, but unauthenticated and stable.

```
GET https://ksndmc.org/en/DailyReport/getCurrentRainData?drpVal=<district_code>
```

Sibling endpoints: `getRainFallReport` (daily totals @08:30), `getWeatherReport`, `getLast15MinutesWeather` (temperature/humidity/wind).

- Response is a **double-encoded JSON string** — parse twice.
- Fields: `DISTRICT`, `TALUKNAME`, `HOBLINAME`, `RAINGAUGE`, `RAIN` (mm), `RAINDATE`, `RAINTIME`.
- Bengaluru district codes: **`01`** Urban, **`02`** Rural, **`03`** South.

**Measured:** 266 unique gauges across the three Bengaluru districts, reporting at **15-minute granularity** (observed timestamps 21:30 / 21:45 / 22:00). Live rainfall present at capture (max 28.0 mm on one gauge).

**Evidence:** `data/raw/ksndmc/getCurrentRainData_d{01,02,03}_2026-08-14.json`

**Corrections to the spec's assumptions:** `dashboard.ksndmc.org` and `varunamitra.karnataka.gov.in` **no longer resolve in DNS**. The live surface is `ksndmc.org`, an ASP.NET MVC app.

**Gauge geolocation — and a trap worth recording.** Station geometry is Public Domain KML on OpenCity (5,929 gauges statewide): `data/raw/ksndmc/stations/rain_gauges.kml`.

- ✅ **Correct join:** live `HOBLINAME` → KML `TM_RainGauge_LocationName`. Yields **235/266 (88.3%)** unambiguously located inside the Bengaluru bbox; 17 ambiguous, 14 unmatched.
- ❌ **Do NOT join `RAINGAUGE` → `KGISTM_RainGauge_LocationID`.** It produces 178 apparent matches of which **100% disagree on station name**. The ID ranges differ (`RAINGAUGE` 308–20175 vs `LocationID` 1–5929) and the overlap is pure coincidence. This join would silently mis-locate two-thirds of the network.
- 344 station names are duplicated statewide (`HEBBAL` appears 7×), so any name join **requires** a bbox filter.

**Impact:** Nowcast mode runs on real 15-minute gauge telemetry, not IMERG. The strongest claim in `PROMPT.md` §11 — routing lead time from measured input — is supported by real data.

**⏺ CONTINUOUS CAPTURE IS RUNNING (started 2026-08-14 18:05 UTC).**

`src/jaladhar/forcing/ksndmc_logger.sh`, cron `*/15 * * * *`, appending raw JSON to `data/raw/ksndmc/live/<date>/`. Deliberately dumb — no parsing, no schema — because parsing can be redone later and an unrecorded 15-minute window is gone forever. Atomic `.part`→`mv`, and a guard that rejects the site's HTML error shell masquerading as a 200.

Three reasons this matters more than it looks:

1. **The endpoint is undocumented and can vanish without notice.** Capture now, ask questions later.
2. **It is monsoon season.** Every logged hour is real gauge data accumulating ahead of the SIH round, at 15-minute, 266-gauge resolution.
3. **It could close the named degradation outright.** If any significant flooding occurs in Bengaluru during the capture window, we get a validation event at **full gauge resolution** — no IMERG fallback, no dependence on item 1b or the September 2022 archive at all. That would be a strictly better validation than the one the gate is currently resting on.

Verified to survive reboot: `cron` is enabled at boot and the crontab is persisted on disk.

**⚠ The feed is DAY-SCOPED in IST — established 2026-08-15, and it changes how the data must be used.**

Observed directly: full payloads at 23:35 and 23:45 IST (≈20 KB, 107 gauges), then `"[]"` (4 bytes) from 00:05 IST onward. The endpoint serves *same-day* rainfall and empties at IST midnight, refilling as gauges report for the new day.

Consequences:

- **Empty payloads are real information, not failures.** The logger now records them as `empty` rather than `ok`, so a genuine outage (a long empty run during IST daytime) stays visible instead of hiding behind a success line.
- **`RAIN` is almost certainly a daily accumulation, not a 15-minute increment** — but this is **NOT yet proven**. A comparison of two snapshots 10 minutes apart found all 104 common gauges unchanged, which is consistent with cumulative *and* with "no rain fell in that window"; it does not discriminate. **The distinction is critical** — forcing a solver with cumulative totals as if they were increments would inflate rainfall enormously. Resolve it on the first rainy capture window, before any forcing code is written.
- **The 15-minute cadence still matters** even for a daily accumulator: differencing consecutive snapshots recovers the intensity curve. That is exactly what the running capture is collecting.

**Not attempted:** Meghasandesha APK inspection. No first-party download was located, and third-party APK mirrors were excluded as a supply-chain risk. Available to authorise if the live API ever proves insufficient.

---

### 1b — KSNDMC *historical* records for 1–10 Sept 2022? — **BLOCKED**

**This is the near-blocker, and it is the only precondition resting on a degraded fallback.**

**Routes attempted, all negative:**

| Route | Result |
|---|---|
| **Systematic parameter fuzz** — 28 parameter names × 4 date formats × 4 endpoints (~450 requests) | **No date parameter exists.** See the false-positive note below. |
| `drpVal` mutation (`0`, `99`, `-1`, `ALL`, `01,02`, date strings) | All return `"[]"`. It is a district selector and nothing more. |
| KSNDMC site navigation | No archive or historical section in the public site map (54 sections enumerated). |
| OpenCity historical rainfall | "Bengaluru Rainfall from 1901" is **monthly** IMD data — useless for a 1–6 h event replay. |

> **Methodology note — a false positive worth recording.** The fuzz initially flagged `archiveDate` as live, because responses differed from the captured baseline. They differed for the wrong reason: **the endpoint's own output changes every 15 minutes**, so hash-versus-baseline diffing has a moving target and manufactures hits. Verified negative three ways: the returned `RAINDATE` was the *current* date, not the requested one; `2022-09-01`, `2023-05-15` and `1999-01-01` all returned the **identical** hash `50dfd7f8cdcd`; and response size matched the live payload exactly. Any future fuzz of this API must compare candidate-vs-candidate across different parameter *values*, never candidate-vs-baseline across time.

**Impact:** The September 2022 replay must be forced with **GPM IMERG at ~10 km** instead of 266-gauge telemetry. See the gate verdict above for what that costs.

**Two routes forward, and the second may make the first moot:**

1. **A direct data request to KSNDMC.** A public-safety student project asking for a 10-day historical extract is a reasonable ask, and an official data-sharing arrangement is a stronger pitch line than a discovered endpoint. It also hedges the risk that the undocumented endpoint disappears. **Requires the user to send it.**
2. **Wait for our own capture to produce an event.** Continuous logging began 2026-08-14 (item 1a) during monsoon season. Any significant flooding in Bengaluru during the capture window yields a validation event at **full 266-gauge, 15-minute resolution** — better than the September 2022 archive would have been, and with no dependence on this item at all.

Route 2 costs nothing and is already running. Route 1 is still worth sending, because it is the only thing that recovers the *specific* September 2022 event and because it de-risks the endpoint vanishing.

---

### 2 — FABDEM licence — **RESOLVED (restrictive)**

**FABDEM is licensed CC BY-NC-SA 4.0** — **non-commercial** and **ShareAlike**.

> "FABDEM may not be used for commercial purposes, and if it is remixed, transformed or built upon you must redistribute your contributions under the same license."

**Evidence caveat:** `data.bris.ac.uk` refused direct fetches on four attempts (`http=000`, ECONNRESET, and one 60 s timeout), so this is corroborated from two independent secondary sources rather than quoted from the Bristol landing page. The licence identification is consistent across both. **Re-verify against the primary source before any public claim.**

**Impact — a real constraint, not a footnote.** ShareAlike is viral: a derived DEM would have to be redistributed under CC BY-NC-SA, and non-commercial forecloses any future commercial or municipal-contract path.

**Decision reversed 2026-08-15, at the start of Phase 1 planning, after direct user adjudication — not a silent override.** The recommendation above (GLO-30 primary, to dodge the licence) turned out to trade away something this session determined actually matters more: GLO-30 is a Digital *Surface* Model — its own filenames say `Copernicus_DSM_COG_…` — so buildings and tree canopy are baked into its elevation values. Using it as primary would have meant either double-counting building heights when §6.1's conditioning step burns buildings up again, or silently skipping that burn and eating GLO-30's fake tree-canopy ridges. This was put to the user directly, with the CC BY-NC-SA tradeoff stated in the question, not assumed: **user chose FABDEM primary, GLO-30 fallback** (`~/.claude/plans/read-prompt-md-execute-phase-cached-anchor.md`, "DEM source" question). Implemented 2026-08-15 in `configs/domain_bengaluru.yaml`'s `dem.sources` list — FABDEM tried first, GLO-30 falls back only if every FABDEM tile fails. `fetch.py` records which source actually won and the `dem_is_dtm` flag in every run's manifest, so the licence exposure is always visible in the output, never silent. Reverting to GLO-30-primary is a one-line reorder of that config list, no code change.

---

### 3 — CartoDEM availability/resolution/licence on Bhuvan — **SKIPPED (decision, 2026-08-15)**

Dropped by explicit decision: Bhuvan was a backup DEM source and Copernicus GLO-30 already satisfies the terrain precondition. A 10 m Indian DEM would have improved the terrain layer and carried pitch value in an SIH context, so this is worth revisiting if time permits — but nothing depends on it.

---

### 4 — NRSC/Bhuvan inundation layer for Bengaluru Sept 2022 — **SKIPPED (decision, 2026-08-15)**

Dropped with item 3, same Bhuvan dependency.

**Note the cost honestly:** `PROMPT.md` §5 calls this the single strongest validation artefact available, and it would have independently corroborated item 6's SAR extent. Validation now rests on **two** independent label sets (BBMP points + Sentinel-1) rather than three. That is still sufficient for the gate — but if the SAR flood mapping later proves ambiguous in dense urban areas (a known failure mode, §14.5), this is the first thing to reopen.

---

### 5 — BBMP flood-prone location list, machine readable? — **RESOLVED (better than expected)**

**Not a PDF problem at all.** OpenCity publishes the locations as Public Domain KML, **already geolocated** — which eliminates the geocoding-precision risk entirely (no Nominatim, no city-centroid fallbacks, no per-point granularity triage).

| File | Named points | Inside BBMP bbox |
|---|---|---|
| `flood_prone_locations.kml` | 70 | 70/70 |
| `low_lying_areas.kml` | 129 | 128/129 |
| `vulnerable_to_flooding.kml` | 200 (attributed, see below) | 200/200 |

**385 unique locations** after ~50 m dedupe (399 raw). Evidence: `data/raw/bbmp/*.kml`

**Bonus:** `vulnerable_to_flooding.kml` carries `WARD_NAME`, `WARDNO`, `ZONE`, and `LocationName` in `SimpleData` — ward-level attribution built in, which feeds the §12 ward severity table directly.

**Impact:** Primary binary validation label set secured, at full spatial precision. This is one of the two independent label sources holding up the gate.

---

### 6 — Sentinel-1 GRD over Bengaluru, 1–10 Sept 2022 — **PARTIAL** (availability RESOLVED, download BLOCKED)

**Availability confirmed with no credentials** via the anonymous CDSE OData catalogue.

**The flood-epoch scene:**
```
S1A_IW_GRDH_1SDV_20220905T004028_20220905T004053_044862_055BB0
```
IW mode, GRD High-res, dual-pol **VV+VH**, descending, 00:40 UTC (≈06:10 IST), 5 Sept 2022. SLC and RAW exist for the same pass.

**Repeat-cycle finding (matters for change detection).** All scenes on this track, 12-day cadence:

```
7 Jul → 19 Jul → 31 Jul → 12 Aug → [24 Aug MISSING] → 5 Sept → 17 Sept → 29 Sept
```

The expected **24 Aug 2022 acquisition is absent from the catalogue**. The nearest pre-event reference is therefore **12 Aug 2022 — 24 days before the flood image**, not the ideal 12. That doubles the window for non-flood change (vegetation, land use) to contaminate a before/after difference. Mitigation: use 17 Sept as a post-recession reference too, and treat agreement between the two references as the confidence signal.

**Second limitation:** a single SAR snapshot at 06:10 IST on 5 Sept captures whatever standing water existed *at that instant*, not the flood peak. SAR gives us extent at one moment, never a time series.

**✅ DOWNLOADED 2026-08-15.** Both scenes are on disk via CDSE S3 (`eodata` bucket, access-key auth):

| Scene | File | Size |
|---|---|---|
| Flood epoch, 5 Sept 2022 | `data/raw/sentinel1/flood_20220905/measurement/…-vv-…cog.tiff` | 639 MB |
| Pre-event, 12 Aug 2022 | `data/raw/sentinel1/pre_20220812/measurement/…-vv-…cog.tiff` | 625 MB |

VV polarisation only, per §8's "VV backscatter thresholding". VH is present in the same products if urban-density stratification later needs it. Calibration and noise XMLs downloaded alongside — **not optional**: thresholding raw DNs instead of calibrated σ⁰ would make the cut incomparable between the two scenes.

**Verified, not assumed:**
- Both rasters open: 25554×16748 and 25555×16748, uint16, real DN ranges (non-zero, non-truncated).
- Footprints from the annotation GCPs (210 points each): lon 76.11–78.72, lat 11.77–13.73. **Both contain the full BBMP extent with margin.**
- Ungeoreferenced, as expected for Level-1 GRD — terrain correction using the GCPs is Phase 3 work.

Fetcher: `src/jaladhar/validation/fetch_s1.py` (idempotent, atomic `.part`→rename).

**Impact:** Second independent validation label set, and the only one covering peri-urban areas where the BBMP point list is sparse. Per §8, score it **stratified by urban density** — SAR degrades badly in dense built-up areas from layover and double-bounce.

---

### 7 — Does LISFLOOD-FP 8.1 build cleanly here? — **BLOCKED** (attempted, well within the 2 h timebox)

**The `lisflood` solver binary does not build against CUDA 12.4.** Root cause is specific and not fixable by configuration.

Toolchain was fully prepared and is *not* the problem:

| Component | Status |
|---|---|
| `nvcc` | **12.4.131** — exact match to driver 550.163.01 |
| `gcc-13` / `g++-13` | installed; pinned repo-locally via `-DCMAKE_CUDA_HOST_COMPILER` |
| `libnetcdf-dev` 4.9.3, `libnuma-dev` 2.0.19 | installed |
| CMake | 3.31.6 — **configure succeeded** (CUDA, OpenMP, NetCDF, NUMA all found) |

**Three obstacles, in order encountered:**

1. The distributed zip ships a **stale `build/CMakeCache.txt`** pointing at the original author's path (`/home/msharifian/GitHub/LISFLOOD-FP`). Fixed by deleting `build/`.
2. `config.default.cmake` hardcodes `-gencode=arch=compute_37,code=sm_37` — **Kepler, removed in CUDA 12**. Worked around cleanly via the build system's own `_CONFIG` override (`config.ada.cmake`, `sm_89` for the Ada RTX 4060) rather than editing the shipped file.
3. **The blocker.** LISFLOOD-FP 8.1 vendors its own 2019-era **CUB** under `cuda/cub/`, which collides irreconcilably with the CUB bundled inside CUDA 12.4's Thrust at `/usr/include/cub/`. Compilation fails with dozens of syntax errors inside the *toolkit's* headers (`device_synchronize.cuh`, `thrust/system/cuda/detail/util.h`, `triple_chevron_launch.h`). Fixing this means porting the vendored CUB to CUDA 12 — far outside a reference-implementation timebox.

**What did build:** the auxiliary DG2 tools — `DG2downscale`, `generateDG2start`, `generateDG2DEM` (build reached 47%). **There is no CPU-only escape hatch**: the main `lisflood` target unconditionally compiles CUDA sources (`cuda/cuda_dem.cu`), so it fails even when the GPU solvers aren't wanted.

**Impact: low, and it vindicates the plan.** §7 designates LISFLOOD-FP a cross-validation *reference*, never the workhorse, and explicitly says "if it doesn't build fast, we write our own solver, which is arguably the better choice anyway." That is now the measured outcome rather than a preference. Question A's benchmark confirms our own PyTorch ACC kernel runs the full city at 10 m in 26 ms/timestep, so nothing downstream depends on this.

**Consequence for §7's correctness proof: none of substance.** An earlier draft of this file claimed cross-validation was "off the table" and that item 9 became the only route to proving solver correctness. **That was wrong**, and it overstated the impact of this failure. LISFLOOD-FP is one possible reference, not the only one:

- **ANUGA** — Python, GPL, full 2D shallow-water (not just local-inertial, so it is an *independent* formulation rather than a reimplementation of ours). `pip`-installable, pure CPU, no CUDA, no CMake — it sidesteps the entire vendored-CUB problem that blocked LISFLOOD-FP. This is the natural cross-validation reference and should be tried in Phase 2.
- **Analytical solutions** — closed-form, generated locally, **no external files required at all**, which makes them immune to item 9's missing-input-data problem:
  - **Ritter** (dry-bed) and **Stoker** (wet-bed) dam-break — exact solutions to the shallow-water equations
  - **Thacker's parabolic bowl** — oscillating flow with a moving wet/dry front and an exact periodic solution; a genuinely hard test of wetting/drying, which is precisely where urban pluvial solvers fail
  - **MacDonald steady flows** — analytical steady-state profiles with prescribed bed slope and friction, exercising the Manning term directly

**Revised standing:** between ANUGA and the analytical suite, the solver-correctness story is sound. Item 9's unobtainable input files are an inconvenience, not a hole — a reconstructed EA Test 8A remains valuable for the *pitch* (it is the recognised industry reference), but it is no longer load-bearing for correctness.

---

### 8 — Is a usable rainfall *forecast* source available? — **PARTIAL**

**Open-Meteo: RESOLVED and working.** Live call returned 72 hourly precipitation values for Bengaluru with correct model elevation (910 m), `precipitation` and `precipitation_probability` in mm and %, no API key.

**Evidence:** `data/raw/forcing/open-meteo_forecast_bengaluru_2026-08-14.json` (sha256 `30e32de37364bb50…`)

**NASA GPM IMERG: ✅ RESOLVED — full window downloaded and verified complete.**

`GPM_3IMERGHH` v07 (Final run, half-hourly, 0.1°). EULA accepted 2026-08-15. Fetcher: `src/jaladhar/forcing/fetch_imerg.py` (idempotent, atomic, streams to disk). Window **28 Aug – 10 Sept 2022** — deliberately starting before the flood, since the event began ~30 Aug.

**672/672 granules on disk** (14 days × 48 half-hourly steps). First pass hit 8 transient network errors (ReadTimeout/ConnectionError/SSLError, two short clusters on 3 and 8 Sept); a second idempotent run — which skips existing files and fetches only what's missing — filled the gap. Extracted CSV verified with a continuous 30-minute timeline, **zero gaps**, `2022-08-28T00:00:00` → `2022-09-10T23:30:00`. Full-window areal-mean total: **189.7 mm** over 14 days.

**The critical gate check: does IMERG actually see the flood? Yes.**

5 Sept 2022 over the BBMP extent, from 48 half-hourly granules:

| Quantity | Value |
|---|---|
| Areal-mean daily total | **39.2 mm** |
| Peak cell intensity | **14.5 mm/hr** at 14:30 UTC (20:00 IST) |
| Wettest window | 19:00–21:30 IST |

A textbook Bengaluru evening convective storm. The fallback is **demonstrated**, not merely available — this was the one assumption the gate rested on that had not been tested.

**The degradation, now measured exactly.** The full 717 km² BBMP extent falls inside a **3 × 3 block — 9 IMERG cells**. With 198 wards that is ~22 wards sharing each rainfall value. Per-ward rainfall attribution is not defensible from this source, full stop. Quote this figure rather than the vaguer "~10 km" wherever the limitation is stated.

**⚠ Timing subtlety for Phase 3, easy to get wrong.** The Sentinel-1 acquisition is 00:40 UTC = **06:10 IST on 5 Sept — hours BEFORE that day's evening peak**. The SAR therefore images flooding accumulated from roughly 30 Aug – 4 Sept, *not* the 5 Sept storm. Consequences: the solver run must spin up well before 5 Sept (hence the 28 Aug window start), and the SAR extent must be compared against modelled state at 06:10 IST on 5 Sept, not against the event maximum.

**Token expiry: ~2026-10-13** (`exp=1791915869`). Regenerate before any demo that depends on live IMERG.

**IMD nowcast and Indian DWR radar: NOT VERIFIED.** Not yet probed for public programmatic access.

**Impact:** Forecast mode exists and has a guaranteed baseline — build against Open-Meteo first, as §11 directs. But at NWP resolution the forecast-driven horizon is coarse relative to a 10 m grid, which is exactly why §11 requires it rendered **visibly provisional** and §12 requires the routing/forecast boundary marked on the time scrubber. The 0–3 h radar nowcast band — the range that matters most for urban flooding — remains unverified and is the highest-value remaining upgrade.

---

### 9 — UK EA 2D benchmark definitions *and input data* obtainable? — **PARTIAL**

**Definitions: obtainable.** Néelz & Pender (2013), *Benchmarking the latest generation of 2D hydraulic modelling packages*, project **SC120002**. Report PDF downloaded (8.8 MB). Third-party implementations (e.g. Tygron's wiki) document individual test setups in reproducible detail — grid sizes, domain dimensions, inflow hydrographs, filenames such as `test2DEM.asc`.

**Input data: NOT downloadable.** The gov.uk publication page carries 20 attachments — 19 PDFs and one ZIP — and **none is the benchmark input dataset**. No public route to the DEM and boundary-condition files was found.

**Impact on §7's correctness proof: limited.** "Our solver reproduces the standard industry benchmark" cannot be claimed from a verbatim re-run of Test 8A without the official inputs. But correctness does not depend on this file (see item 7 — ANUGA plus analytical solutions carry that load). Three complementary paths, in priority order:

1. **Analytical test cases** — the rigorous core, and they need **no external files**: Ritter (dry-bed) and Stoker (wet-bed) dam-break, Thacker's parabolic bowl (exact periodic solution with a moving wet/dry front), MacDonald steady flows (exercises the Manning term). Exact answers, no reconstruction ambiguity.
2. **ANUGA cross-validation** — an independent full shallow-water code, `pip`-installable, no build friction.
3. **Reconstruct** Test 8A from the published specification — the rainfall-driven urban geometry is documented well enough to rebuild, and published results give comparison curves. Label it **"reconstructed from specification"**, never "the official benchmark". This is the *pitch* artefact, not the correctness argument.

Also worth requesting the official inputs from `fcerm.evidence@environment-agency.gov.uk` — same low-cost, high-value ask as item 1b, and it would upgrade path 3 from "reconstructed" to genuine.

---

## Open design questions

### A — Grid resolution for the full-city domain — **RESOLVED (benchmarked)**

**Real BBMP extent, measured** from the 2023 final ward boundaries (225 features, Public Domain, `data/raw/boundary/bbmp_wards_2023_final.kml`):

- **Union area: 717.0 km²** — consistent with the ~740–800 km² in §6.0
- Bounding box: **35,139 m × 34,199 m** (EPSG:32643, UTM 43N)
- WGS84: lon 77.4601–77.7844, lat 12.8336–13.1427

> *Geocoding note:* `osmnx` geocoding of "Bruhat Bengaluru Mahanagara Palike" returns **BBMP's headquarters building** (0.0 km²), and "Bengaluru Urban" returns the **district** (2194 km²). Neither is the municipal boundary. Caught only by checking area — the same silent-wrong-answer failure mode flagged for item 5.

**Measured kernel cost.** RTX 4060 Laptop (8188 MiB), torch 2.6.0+cu124, float32, 50 timed iterations after 10 warm-up, tensors allocated directly on device.

| Resolution | Grid | Cells (bbox) | Cells (in BBMP) | ms/timestep | Peak VRAM |
|---|---|---|---|---|---|
| 5 m | 7028 × 6840 | 48.07 M | 28.7 M | **104.86** | 3955.7 MiB |
| 10 m | 3514 × 3420 | 12.02 M | 7.17 M | **26.23** | 988.9 MiB |
| 20 m | 1757 × 1710 | 3.00 M | 1.79 M | **3.56** | 257.7 MiB |
| 30 m | 1172 × 1140 | 1.34 M | 0.80 M | **0.81** | 109.5 MiB |

*The "Grid" column is this benchmark's own ceil-from-fixed-origin estimate, not the canonical Phase 1 grid — kept as-is here because it is what was actually benchmarked. `src/jaladhar/terrain/grid.py`'s later exact computation (snapping both bbox corners outward independently) gives **3515 × 3421 = 12,024,815 cells** at 10 m, one cell wider per axis; see that module's docstring for why the two methods legitimately differ.*

**Evidence:** `runs/bench_phase0/manifest.json` (git SHA, device, params, all timings). Rerun with `python -m bench.acc_stencil`.

**What this is and is not.** These are **hardware throughput measurements on synthetic tensors** — the real ACC stencil, real shapes, real dtype, real memory traffic. Stencil arithmetic cost is independent of elevation *values*, so ms/timestep is a genuine measurement. **No value here is a hydrological claim about Bengaluru.**

**The band.** Wall clock per scenario = ms/timestep × timesteps/hour, and timesteps/hour comes from CFL (`Δt = α·Δx/√(g·h_max)`) where `h_max` is Phase 2 physics. Carried explicitly over α ∈ {0.5, 0.7}, h_max ∈ {0.3, 1.0, 3.0} m:

| Resolution | Timesteps / simulated hour | Seconds per simulated hour |
|---|---|---|
| 5 m | 1,765 – 7,812 | **185 – 819** |
| 10 m | 882 – 3,906 | **23.1 – 102.4** |
| 20 m | 441 – 1,953 | 1.6 – 7.0 |
| 30 m | 294 – 1,302 | 0.2 – 1.1 |

**Recommendation: 10 m.**

- §14.1 and §6.0 put a **physical floor** at ~10 m — coarser cannot resolve a 10 m street and the street-level claim in the problem statement stops being defensible. That rules out 20 m and 30 m on defensibility, whatever their cost advantage.
- 5 m is affordable in VRAM (3956 MiB, 48% of the card) but costs **4× the wall clock** of 10 m for a resolution gain the DEM cannot support — GLO-30 is 30 m native, and §6.1 recovers fine structure from *vector* conditioning, not from interpolating elevation. Resampling to 5 m would invent detail the source data does not contain.
- At 10 m a 6-hour storm over the full city runs in **2.3–10.2 minutes** — comfortably inside §9's batch budget, and notably *faster* than the "10–30 minutes per run" §1 assumes.

Revisit at Phase 8 as a resolution-sensitivity study (§13), which turns "how do you know 10 m is enough?" into a plot.

---

### B — City partitioning and tiling — **RESOLVED (and it overturns the assumed rationale)**

**VRAM does not force tiling.** This was the expected driver and the measurement contradicts it:

- 10 m full city: **988.9 MiB peak of 8188 MiB — 12% of the card.**
- Even 5 m full city fits at 3955.7 MiB (48%).

**So the full BBMP domain is solvable untiled on the smallest configured device.** Halo exchange, seam artefacts, and hydrological-divide seam placement — the whole §6.0 apparatus — are **not needed for forward simulation**.

**What actually drives partitioning, then:**

1. **Colab churn (§3, §9).** Free-tier sessions die at ~4–5 h. The unit of work must be small enough that a dead session loses little. At 10 m a whole-city 6 h scenario is 2.3–10.2 min — **already small enough to be its own unit**. This is the strongest argument that unit-of-work = (scenario, whole city), *not* (scenario, tile), which simplifies §9's batch runner considerably: no reassembly step, no seam diagnostic, no tile index.
2. **Autograd during calibration (§8).** Backpropagating through thousands of solver timesteps is where memory genuinely explodes — forward is cheap, the tape is not. **This is the real tiling driver**, and it bites in Phase 3, not Phase 2. Handle with gradient checkpointing or tile-wise calibration.
3. **U-Net training (§10).** Independent of solver tiling; a U-Net can train on tiles and apply convolutionally.

**First-pass recommendation:** run the solver **untiled at 10 m**; tile only the calibration backward pass and surrogate training. Record the VRAM headroom measurement as the justification. Re-check if the smallest configured worker has less than ~4 GB VRAM.

---

### C — How many training scenarios does the surrogate need? — **METHOD ONLY (by design)**

Deferred to Phase 5, as `PROMPT.md` §10 requires. **Do not pick a number here.**

Method: fix architecture, loss, and a held-out test set including the real historical events. Train identical models on nested subsets (10, 25, 50, 100, 200 …) using the prefix property of the Sobol/LHS ordering from §9, so every subset is a well-spread sample. Plot validation CSI and depth RMSE against training-set size with error bars over several seeds. Read the plateau off the curve. Write to `runs/learning_curve/`.

Two caveats to carry: scenario count ≠ sample count (one run yields many timesteps), and samples from the same scenario must not leak across the train/test split.

---

### D — What compute pool to assemble? — **FIRST-PASS ESTIMATE**

Anchored on the **measured** 10 m figure: **2.3–10.2 minutes per whole-city 6 h scenario** on an RTX 4060 Laptop.

For *N* scenarios on *W* workers of relative throughput *r*:
```
wall_clock = N × (139 … 614 s) / (W × r)
```

Worked at **N = 200 scenarios** (illustrative; C sets the real number):

| Pool | W | Wall clock | Cost |
|---|---|---|---|
| Dev laptop alone | 1 | **7.7 – 34 h** | ₹0 |
| ~10 free Colab (T4) | 10 | **1.3 – 5.7 h** before churn | ₹0 |
| Colab Pro (L4-class) | 1–2 | **1.9 – 17 h** | ~₹1,000/seat/mo |
| Mixed (Pro train + free solve) | — | solver on free tier, training on Pro | ~₹1,000 |

**Honesty flags on this table:**
- Only the **RTX 4060 row is measured**. T4 and L4 throughput are *unmeasured scaling assumptions* (`r`), not benchmarks. Do not quote them as results — measure on the actual workers before the procurement decision, per §3's "buy decision against numbers".
- Churn is not yet quantified. Free-tier workers lose partial work when a session dies; with a ~10 min unit of work that loss is small, which **favours the free tier** — but it must be measured, not assumed.

**Recommendation: buy nothing yet. Run the solver batch on the dev laptop overnight.**

Question B's VRAM result largely answers D for free. At ~5 min per whole-city scenario (band midpoint), **200 scenarios is a single overnight run on the 4060 alone** — 7.7–34 h, and the low end of that band is one night. There is no queue to distribute, no tiles to reassemble, and no reason to spend money to make an overnight job finish sooner when it can run while nobody is awake.

Deliberate consequences:

- **Do not buy Colab Pro until there is a reason.** The plausible reasons are (a) the learning curve in question C demands far more than a few hundred scenarios, or (b) surrogate *training* — one long job that hates being killed — proves slow on the 4060. Neither is known yet. §3 asks for the buy decision to follow from numbers; the numbers currently say "not yet".
- **Free Colab accounts stay a scale-out option**, not a dependency. The dynamic claiming design in §9 is still worth building — it is what makes adding workers a config change — but it is no longer on the critical path, so it need not be built before the first batch runs.
- **Revisit when C produces a number.** If the learning curve is still climbing at 500+ scenarios, re-run this table with the measured per-worker throughput of the actual pool rather than the assumed `r` above.

`configs/compute.yaml` to be written with the measured 4060 figure as the anchor and a single-worker default.

---

## Summary

| # | Item | Verdict |
|---|---|---|
| 1a | KSNDMC live API | ✅ RESOLVED — 266 gauges @ 15 min |
| 1b | KSNDMC historical Sept 2022 | ⛔ **BLOCKED** — the one real gap |
| 2 | FABDEM licence | ✅ RESOLVED — CC BY-NC-SA, use GLO-30 instead |
| 3 | CartoDEM / Bhuvan | ⏭ SKIPPED by decision — GLO-30 suffices |
| 4 | NRSC Sept 2022 inundation | ⏭ SKIPPED by decision — validation drops to 2 label sets |
| 5 | BBMP flood-prone list | ✅ RESOLVED — 385 pre-geolocated points |
| 6 | Sentinel-1 Sept 2022 | ✅ **RESOLVED — both scenes downloaded, cover full BBMP** |
| 7 | LISFLOOD-FP build | ⛔ BLOCKED — vendored CUB vs CUDA 12.4; **use ANUGA instead** |
| 8 | Rainfall forecast source | 🟡 PARTIAL — Open-Meteo works; IMERG needs 1 EULA click; IMD/DWR unverified |
| 9 | UK EA benchmark | 🟡 PARTIAL — definitions yes, input data no; not load-bearing |
| A | Grid resolution | ✅ RESOLVED — **10 m** |
| B | Tiling | ✅ RESOLVED — **untiled**; VRAM is not the constraint |
| C | Training scenario count | 📋 METHOD ONLY — Phase 5 |
| D | Compute pool | ✅ **Buy nothing** — overnight on the dev laptop |

**Highest-value next actions, in order:**
1. **One EULA click** — `https://urs.earthdata.nasa.gov/approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ`. This is the last thing standing between us and the September 2022 event forcing that the gate's degradation rests on. Thirty seconds, and it unblocks 480 granules.
2. **Leave the KSNDMC capture running.** Already live; costs nothing; may close the named degradation outright by catching a real event at full gauge resolution.
3. **Resolve cumulative-vs-incremental on the first rainy window** (item 1a) — must be settled before any forcing code is written, or rainfall will be inflated by a large factor.
4. Send the KSNDMC data request (item 1b) — the only route to the *specific* September 2022 event, and a hedge against the undocumented endpoint vanishing.
5. Verify IMD nowcast access (item 8) — the 0–3 h radar band is the highest-value forecast upgrade.

*Deliberately not on this list:* buying Colab Pro (D says the numbers don't justify it yet), chasing the EA input files (item 9 is no longer load-bearing now that ANUGA and analytical cases carry correctness), and Bhuvan (items 3/4, skipped by decision).
