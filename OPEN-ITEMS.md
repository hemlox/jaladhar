# OPEN-ITEMS.md — JALADHAR

External unknowns and open design questions. Every entry carries a **verdict**, the **evidence** behind it, and its **impact on the architecture**.

Items **1–9** and design questions **A–D** are the Phase 0 gate, opened **2026-08-14** and amended since — item 2's decision was reversed 2026-08-15, item 6 completed, item 8's EULA accepted; read each item's own date, not this header. Items **E–U** are the terrain and solver open items carried out of Phases 1 and 2, added **2026-08-17**; they are the single source of truth for what is open, and `docs/CONTEXT-HANDOVER.md` §8 points here rather than duplicating them.

**Verdict key:** RESOLVED · PARTIAL · BLOCKED · METHOD ONLY

---

## Gate verdict

> **The gate asks one question: do we have enough real, external, verifiable evidence to validate a flood prediction against a real event?**

**Answer: PROCEED WITH A NAMED DEGRADATION.**

Terrain, validation labels, and observed flood extent are all secured with real, downloadable data. **The one genuine gap in the *data* is item 1b — historical gauge rainfall for the September 2022 event.** No public programmatic route to it exists; every date parameter the live API accepts is silently ignored.

**The terrain licence is a second, separate exposure, and it is not clean.** Since the 2026-08-15 reversal FABDEM is primary and won at build time, so every downstream raster is a CC BY-NC-SA ShareAlike derivative. Item 2 carries an unexecuted precondition of its own — *"re-verify against the primary source before any public claim"* — because `data.bris.ac.uk` refused four fetches and the licence is corroborated only from secondary sources. This is tracked as item **2** and appears in the actions list below; it was previously stated here as "correctly-licensed", which the same table's terrain row contradicts.

Per the reframed gate in `PROMPT.md` §5, the fallback is being invoked and its cost stated rather than waved through:

| Precondition | Satisfied by | Status |
|---|---|---|
| **Usable terrain** | **FABDEM primary** — decision reversed 2026-08-15, see item 2. GLO-30 is the configured fallback and did not fire. | ✅ secured, **licence exposure live** |
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

**DECISION 2026-08-16 — KSNDMC is optional, not a dependency. This item no longer blocks any forcing code.**

Write the forcing layer against a clean internal interface — `(timestamp, cell, mm in interval)` — with **Open-Meteo as the working adapter** for both nowcast and forecast, and IMERG for historical. A KSNDMC adapter is then a day's work if access ever lands. Two consequences worth stating:

- The cumulative-vs-incremental question (item 1a) was previously described as blocking all forcing code. Under this design it is answered **empirically from the first data sample**, not by asking KSNDMC, and it blocks only the KSNDMC adapter rather than the forcing layer.
- Keep the adapter slot and mention KSNDMC TRG ingestion in the pitch. It reads well in a government hackathon and is honest either way.

Bhuvan was abandoned earlier (dead URLs, no useful store) — items 3 and 4.

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

**Re-verified 2026-08-15 at the start of Phase 2** (independently, against realized state rather than the page's own descriptions). Confirms the PARTIAL verdict and adds three specifics:

- The single ZIP attachment (`LIT_8570_a3d694.zip`, 7.97 MB), which the page presents as a data package, was **downloaded and extracted: it contains exactly two files, `SC120002 report.pdf` and `SC120002 summary.pdf`.** No input data. Previously inferred; now verified by extraction.
- The attachment the page labels as the ~93-page **"Test specifications" is a different study entirely** — `scho0305bixo-e-e.pdf` is *W5-105/TR1, Benchmarking of hydraulic **river** modelling software packages*, the older 1D programme. Verified from its title page. This is why "definitions obtainable" rests on the results report plus third-party write-ups and not on an official 2D spec document — there isn't one on that page.
- **NEW route to real inputs for Test 5:** Zenodo record [`4066824`](https://zenodo.org/records/4066824) (LISFLOOD-FP 8.0) publishes *"configuration files and simulation result data"* for EA **Test 4** (`ea4.zip`, 16.8 MB) and **Test 5** (`ea5.zip`, 73.7 MB; `ea5-first-hour.zip`, 10.4 MB), ESRI ASCII, per Néelz & Pender's specification. If the configs bundle the DEM, this yields genuine inputs for Test 5 **and** an independent published result to compare against — worth checking when Phase 2 reaches its validation ladder. Tests 1, 2 and 8A are not in that record.

**Test 8A remains the gap.** It is the test closest to our own problem (rainfall applied directly to an urban surface), and no public source for its DEM/rainfall inputs was found. Phase 2's plan therefore does not rest on it: the analytical ladder plus ANUGA carry solver correctness, exactly as this item already concluded.

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

## Phase 1–2 open items — terrain and solver

Carried out of the Phase 1 terrain rebuild and the Phase 2 acceptance run (2026-08-16, 39.93 min, commit `78610c2`). Lettering continues the design-question series. These postdate the Phase 0 gate above and none of them affect its verdict.

**Two of these — H and I — block the conditioning redesign. No retain/breach threshold may be set until both land.**

---

### E — Did D4 sealing cause the extreme depths? — **RESOLVED (hypothesis falsified)**

**Predicted:** max depth drops substantially from 23.86 m once D4 traps reach zero. **Realized: 23.22 m, a 2.7% drop.** D4 traps are at zero and 6,769 cells still exceed 10 m. The hypothesis is dead.

Where the water actually is:

| Metric | Value | Base rate | Enrichment |
|---|---|---|---|
| >5 m cells directly on a D8 breach cut | 8,096 / 10,422 = **77.68%** | 3.875% | **20.05×** |
| >5 m cells within 1 cell of a D8 cut | 10,422 / 10,422 = **100.00%** | 17.524% | 5.71× |

**The water was never in the D4 traps — it is in the canyons `breach_depressions` carved.** The D4 traps were spatially coincident with the deep water because the D8 pass manufactured them in the same places; causation was attributed to the correlated symptom. This is the confounding failure the standing rules now watch for, and it cost a full rebuild cycle.

**The D4 work was still necessary and is not reverted.** A 4-connected solver genuinely cannot drain a D4-sealed cell, and step count fell 44,049 → 39,051. It was not the depth mechanism.

**Evidence:** `runs/solver/manifest.json`, `docs/phase2-diagnostics.md` §1, `docs/d4-audit.md`.
**Impact:** the depth defect is a conditioning problem — item F.

---

### F — `breach_depressions` is carving out Bengaluru's tank cascade — **OPEN, highest priority**

Depression inventory on the pre-breach 10 m DEM: **27,609 closed depressions, 28,889,503.6 m³ storage, 489,032 cells (48.90 km², 3.84% of the domain).**

The top 50 hold **21,618,287 m³ = 74.83%** of all depression storage, and **33 of the 50 intersect mapped OSM water.** By name: Yele Mallappa Shetty (#1), Varthur (#2), Madiwala (#3), Bellandur (#4), Agara (#14), Ulsoor (#19), Hebbal (#20), Lalbagh (#22), Kaikondrahalli (#24), Saul Kere (#41), Yelahanka (#42), Sankey Tank (#44), Yeshwanthpur (#48), Bhattarahalli (#50).

That is the tank cascade, ranked correctly by size, reproduced unprompted from elevation alone. It is simultaneously **(a)** a strong end-to-end validity check on the whole terrain pipeline and **(b)** proof that standard hydrological conditioning destroys the dominant flood-attenuation mechanism in the city.

**Why this is a class of artifact, not a bug.** `breach_depressions` exists to make a DEM monotonically descending, because D8 flow-routing cannot traverse a depression. A 2D shallow-water solver has no such requirement — it fills a basin, the basin spills over its lowest rim, and water continues downstream. That is correct physics executed by the solver, not something conditioning must pre-arrange. Depression removal here is not merely unnecessary, it is destructive. The pre-patch D4 traps and the post-patch trenches are the same defect wearing different clothes: water trapped in sealed basins, then water trapped in the trenches dug to unseal them.

Retained, the lakes give lake-appropriate depths: Bellandur is 1.91 M m³ over 19,171 cells = **1.0 m mean, 1.08 m max**; Varthur 1.30 m mean, 3.91 m max. Not 23 m.

**What the solver needs is far weaker than monotone descent:** every cell has a D4-traversable route to the domain boundary or to a registered storage basin, and every registered basin can spill over its rim via D4 moves. That is achievable with centimetre-scale modifications — for a cell whose only descending neighbour is diagonal, the required cut is bounded by the local diagonal drop, 0.015 m at the argmax inspected.

**Consequence that did not exist while every basin was being trenched out — antecedent basin state is now first-order.** Retained storage is 28.89 M m³ against **140,021,560 m³ of rain actually delivered in the acceptance run** (`runs/solver/manifest.json` mass balance) = **20.6%**. The solver starts everything dry, so a dry Bellandur absorbs 1.91 M m³ that in reality is already occupied. Across the register that is up to a fifth of the storm silently removed before any flooding occurs. **This must be decided before the retain/breach change lands**, or the first post-fix run is interpreted against a wrong baseline. Note that item I complicates the obvious fix: if the DEM already renders tank floors at water surface, some of that antecedent volume is baked in and would be double-counted by initialising the basins wet.

*(Where a design proposal quotes 21.84% against a 132.3 M m³ "110 mm / 2 h design storm", that denominator is not traceable to any run in `runs/`. Use the measured 140.02 M m³ or state the design storm explicitly.)*

**Verdict: redesign required. Thresholds are NOT set — blocked on items H and I.**

---

### G — Excavation is extraordinarily concentrated — **RESOLVED (measured); feeds F**

Across **465,927** canonical D8 cut cells, **20,802,918 m³** buffered excavation (20,223,054 m³ canonical):

| | P50 | P90 | P95 | P99 | P99.9 | Max |
|---|---|---|---|---|---|---|
| D8 cut depth | **6.6 cm** | 0.65 m | 1.57 m | **10.08 m** | **19.80 m** | **22.94 m** |

P50 at 6.6 cm is correct noise-breaching. The tail is not.

- **Top 1 connected trench network: 10,176,564 m³ = 48.92% of all domain excavation.** Top 5 = 55.18%, top 10 = 57.67%, top 50 = 64.13%.
- Total excavation is **71.97% of all depression storage in the domain** — the breach pass dug out nearly three-quarters of the city's depression volume.

The single largest component is almost certainly the Bellandur–Varthur–Dakshina Pinakini line carved out in one incision.

**Why this is usable as an invariant rather than a parameter:** removing a depression costs a cut equal to its own depth, so the cut-depth distribution *is* the storage distribution seen from the other side. A cut deeper than the retain threshold is then not a tuning failure but proof the classifier put a real basin in the wrong bucket — surfaced as a test failure. Any replacement scheme must record the full cut and fill distributions, and the largest connected excavation component, as first-class outputs.

**Evidence:** `docs/phase2-diagnostics.md` §3, `runs/terrain_conditioning/manifest.json`.

---

### H — How many D4 pits did the D8 pass actually create? — **BLOCKED on a measurement**

Three mutually incompatible pairs are in circulation for the same quantity:

| Figure | What it actually measures | Provenance |
|---|---|---|
| 228,227 / 347,083 = **65.8%** | **adjacency** to a D8 cut cell | `runs/terrain_conditioning/manifest.json` → `d4_cardinal_breach.d4_breach_adjacency_split.adjacent_to_d8_breach`. **Verified present.** The field name says adjacency; the manifest never claimed causation. |
| 347,083 − 290,267 = 56,816 = **16.4%** | **net increase** | 290,267 is **not** in any manifest — verified absent. Its own provenance is unrecorded. |
| 195,368 / 296,912 = **65.8%** | unstated | Appears only in the 2026-08-16 conditioning design response. **Neither number occurs anywhere in this repo** — verified absent from the conditioning manifest. |

**Adjacency is not causation**, and net increase understates gross creation if the D8 pass also destroyed pre-existing pits — which it certainly did, since it resolved 125,374 D8 depressions. The two 65.8% figures agreeing is a coincidence of ratios, not corroboration: they have different numerators and different denominators.

**The resolving measurement is a set difference, not a count difference.** Label D4-pit status per cell on the pre-breach conditioned DEM and on the post-D8 DEM, then report:

- **(a) pits present post-D8 but absent pre-breach — gross created.** Only this supports "D8 artifact."
- **(b) present pre-breach but absent post-D8 — destroyed.**
- **(c) present in both — survivors.**

**This is a read-only job against artifacts already on disk — no terrain rebuild, no GPU.** `data/interim/terrain/dem_conditioned_prebreach.tif` and `dem_conditioned_postbreach.tif` were both written inside the conditioning run's 11.45 s window at SHA `be1a192`, the same run that reported 347,083, so the set difference is directly comparable to the manifest figure. Correspondence verified by mtime against `wall_clock_sec`.

**Until (a) lands, no conditioning-redesign threshold may be set,** and the deferral in item J may not rest on pit accounting.

---

### I — Does FABDEM render the large tanks as flat plateaus? — **OPEN; blocks the classifier's geometry criteria**

`dem_source = "fabdem"`, `dem_is_dtm = true` (`runs/terrain_dem/manifest.json`). FABDEM is an ML product that strips buildings and canopy; DTMs of that kind commonly render water bodies as **flat plateaus at the water surface** rather than as bathymetric holes. If Bengaluru's tanks are flat rather than concave, the retained "storage" is the volume between a plateau and its rim, not the lake's capacity.

**The fill ratio V/(A·D) says the confounder is live.** Derived here from the `docs/phase2-diagnostics.md` §2 top-50 table with A = cells × 100 m²:

| Group | n | median V/(A·D) |
|---|---|---|
| Shallow, D ≤ 1.5 m | 11 | **0.915** |
| Deep, D ≥ 5.0 m | 22 | **0.441** |

Spearman ρ(depth, fill) = **−0.424**. A concave bowl is ~0.3–0.5; a flat floor inside a steep rim approaches 1.0. **All six basins with fill ≥ 0.90 are shallow (0.35–1.08 m), large (26.8–191.7 ha), and mapped water** — ranks 4 Bellandur, 7 Kodi, 19 Ulsoor, 20 Hebbal, 23 Hoodi, 42 Yelahanka. That is the flat-plateau signature exactly where it would appear if the DTM were rendering water surface. Bellandur at 191.7 ha with a 1.08 m maximum and fill 0.922 is not bathymetry.

**Two consequences, both of which hold thresholds:**

1. **The proposed fill-ratio cut `r_min` is inert as a genuine-vs-artifact discriminator, and inverted where it does act.** Across the classification it is supposed to separate, water-evidenced median is **0.469** against no-water median **0.445** — a statistic that does not vary with the thing it is meant to detect. Worse, the stated rule ("a real basin is concave; interpolation noise is flat with a spike") makes high fill an artifact signature, which would classify the six most clearly genuine tanks in the city as artifacts.
2. **Retained storage for the big tanks is a rim-volume, not a capacity**, so item F's 20.6% is a floor of unknown tightness, and initialising basins wet would double-count whatever the plateau already encodes.

**The discriminating observable is within-basin floor σ_z** — the same statistic `response.md` computed for the 17 quarry candidates (0.14–1.88 m) and did not compute for the tanks. A true bathymetric bowl has floor σ_z rising with depth; a rendered plateau has floor σ_z near zero regardless of rim height. **Measure it for the top 50 before any geometry threshold is set.**

---

### J — D4 building-midpoint fix — **DEFERRED (justification replaced)**

D4 cardinal breach lowered 332,080 cells (mean 0.7871 m, max 25.04 m): building 46,261 / waterway 17,756 / road 121,611 / plain 146,452 — sums exactly. Lowering on building cells clusters tightly at the +3.0 m burn height (P10 2.679, P50 2.955, P90 3.136), so the pass removes the burn rather than excavating below grade. Residual height above the lowest non-building cardinal neighbour: **P10 7.5 mm, P50 26.5 cm, P90 87.4 cm** — roughly two-thirds below 0.5 m, hydraulically transparent in any flood worth simulating, on the order of 30,000 ten-metre-wide gaps concentrated along the carved drainage network rather than randomly placed.

**The original deferral reasoning — "65.8% of D4 pits were D8 artifacts, so the problem plausibly dissolves" — is withdrawn.** Item H shows the pit accounting is unreconciled, and pit count was the wrong variable regardless.

**The deferral stands on cut depth instead.** The damage is driven by how deep the cut was, not by whether a pit existed: the 25.04 m lowering happened because D8 carved a 22.94 m trench past that cell. With cut depth at P99 = 10.08 m, P99.9 = 19.80 m and one connected trench holding 48.92% of all excavation (item G), removing the deep cuts removes the mechanism that forces deep staircases **however the pit accounting in item H resolves**. That is a claim about the mechanism rather than about a correlation, and it survives H either way.

Re-measure after the conditioning rebuild. The design note in `docs/walkthrough.md` §1 stands: the real fix is class-aware midpoint selection — a diagonal has two candidate midpoints, prefer the non-building one — plus accepting genuinely sealed courtyards as legitimate pits.

---

### K — Courant overshoot — **RESOLVED (mechanism); fix pending, do before Phase 4**

Δt is selected so Courant = α exactly at start-of-step depth, therefore

```
Courant_realized = α · √(h_end / h_start)        ← exact, verified
```

Peak was step **16**, t = 160 s, Δt = 10.0 s, h_start = 0.049949 m → h_end = 0.087951 m:
`0.700 × √(0.087951/0.049949) = 0.700 × 1.3270 = 0.9289` — reproduces the observed maximum exactly.

The overshoot is large only where depth grows fast *relative to itself*, i.e. at storm onset from a dry bed.

**Magnitude, measured after the acceptance run** — and it inverts the reading originally drawn from the counts:

| Setting-cell depth | Steps | Share | Max realized Courant | P50 exceedance |
|---|---|---|---|---|
| h < 0.5 m | 47 | 0.41% | **0.9289** | 3.8e-2 |
| 0.5 ≤ h < 5 m | 1,334 | 11.70% | 0.8822 | 5.0e-4 |
| 5 ≤ h < 15 m | 3,617 | 31.71% | 0.7005 | 8.8e-5 |
| h ≥ 15 m | 6,408 | 56.18% | 0.7219 | 7.0e-6 |

**`docs/phase2-diagnostics.md` §1 states the finding "the overshoot is overwhelmingly (99.59%) situated in deep water". By count that is true; by magnitude the opposite is true** — deep-water exceedances are 1e-5-scale noise, and every consequential overshoot is in shallow water at storm onset. The count was reported where a magnitude was required. The diagnostics file is retained unedited as the historical record; this entry is the correction.

**Fix:** two-line rejection in the recording pass — compute h_new, check realized Courant, if it exceeds the ceiling halve Δt and redo. Record-then-replay makes this free; the replayed schedule just has smaller steps where needed and the differentiable graph stays fixed.

At 0.929 there is 7% of margin to instability. Not a correctness problem now — the run completed with mass rel-residual 4.105e-05 — but Phase 4 runs hundreds of storms, and an instability in scenario 237 of 500 silently poisons the surrogate training set.

---

### L — Non-negativity tolerance is global, not per-cell — **OPEN (unresolved critique of committed code)**

Committed at `20dc1d6`. The tolerance is scale-derived from the **global domain h_max**. At h_max = 23.9 m that gives tol = **1.14e-5 m**, so a cell at h = 0.001 m may reach **−1.1e-5 m — 1% of its own depth, ~10⁵× its local ULP — and pass.**

Round-off in `h_new = h_old − Σflux·dt/A` is bounded by the **local** intermediate magnitudes, not by the domain maximum. The tolerance must therefore be per-cell:

```
tol_i = 4·eps·max( h_old_i , Σ|flux_i|·dt/A )
```

**Why the existing justification does not hold.** The validating example (−2.38e-7 at h = 2.95 m) is a case where the local and global scales coincide, so it never exercised the mixed regime the guard exists to police. The assertion was right and the observable was right; only the domain it ran over could not fail. That is **V7**, and it is the reason this is filed as open rather than closed.

---

### M — `track_cell_sinks` should be deleted, not defaulted — **OPEN (unresolved critique of committed code)**

Committed at `20dc1d6`. The flag makes the per-cell sink minimum optional, justified by a measured **+2.64%** overhead.

**That measurement does not support the flag.** The CPU row of the same benchmark shows the "fast" path running *slower* than the tracked path — which means the harness cannot resolve a difference of that size, so +2.64% is noise, not a cost. Syncs are fixed-cost (~10–50 µs); at 12.7 M cells with ~30–50 ms steps the true overhead is **~0.1%**.

**What the flag disables is the detector that caught the negative-drain bug** — `torch.minimum(drain_cap*dt, h_new)` with `h_new < 0` creates water while `mass_created_by_clamping` stays 0.0 and the mass residual still balances. That failure is invisible in every other diagnostic the solver emits.

**Phase 4 would generate the entire surrogate training set with it off.** A silent water-creating clamp in an unknown subset of 500 scenarios is exactly the poisoning mode described in item K.

**Verdict: delete the flag, keep the detector unconditional.** Re-benchmark on GPU only, at full domain, if a cost claim is wanted at all.

---

### V — Conditioning verification is vacuous where it matters — **OPEN, blocks accepting item F**

The redesign itself is sound and the destructive artifact is gone. What cannot be accepted is the evidence offered for it.

**1. Part E is not an acceptance run.** `scripts/benchmark_part_e.py:48` runs `n_steps=100`; all four runs completed in ~7 s. A 6 h storm is ~39,051 steps, so this is **0.26% of duration** — the tank cascade has not filled or spilled, which is the entire mechanism under test. The recorded baselines are unreachable at that scope: Run 1 on the *old* DEM returns P99 0.0620 m against a recorded 0.989823 m, and outfall 1.46e-3 against a recorded 61.5%. All three "SURVIVED" hypotheses are untested, and the shortfall was not declared as a deviation. Textbook V7.

**2. Invariant A passes by construction.** `conditioning.py:426-430` does `residual_pits = d4_pits_mask & ~retained_basin_mask` then `basin_class_buf[residual_pits] = CLASS_UNCERTAIN` — unresolved pits are relabelled *into* the register, so "pits outside R = 0" cannot fail. **41,792** residuals were absorbed this way (the report says 48,196; the manifest says 41,792 — unreconciled). Honest statement: 100,482 D4 pits remain, of which 41,792 are unresolved failures reclassified as basins. Separate the classes so the invariant can redden.

**3. Invariant C passes by clipping.** `max_cut_m = 0.4998779` and `max_fill_m = 0.2500000` sit exactly on their caps, so "≤ cap" is guaranteed by the clamp. More telling: **mean_cut = 0.1486 m** against the ~0.015 m local-diagonal-drop scale the 0.50 m bound was justified from — 10× larger. By the design's own logic a cut approaching cut_max signals misclassification, and the *mean* is a third of the cap.

**4. The cascade check — the one V3-compliant external validation — was asserted, not measured.** `retained_basins.csv` has **no name column**; "Madiwala / Agara / Bellandur / Varthur" were assigned in prose. The derived order is Madiwala → Agara, against the documented Agara → Madiwala. And basin `85584`, labelled Agara, is **2,315 cells = 23 ha**, where Agara Lake is ~1.4 km². "Yelahanka / YMS" conflates two different lakes into one node. The check failed and was reported as verified.

**5. Filling became the primary operation.** 169,351 filled cells against 111,703 cut (**60% fill**), adding **1.21 M m³** of new terrain where the old pipeline added none. The spec ordered carve-first, fill-as-fallback. Not flagged.

**6. Two-thirds of the register is UNCERTAIN** — 17,700 of 26,090 objects, though only 10.5% of storage. The sensitivity run that bounds it is also 100 steps.

**7. Housekeeping:** `n_pits_post_d8_breach = 63715` persists in the manifest although whitebox is no longer called anywhere; the field name now misleads. And the headline excavation result (−92.0%) is absent from the report — I computed it from `n_cells_cut × mean_cut_m × 100`.

---

### N — `checkpointing.py` segmentation — **NOT IMPLEMENTED; gates the differentiable calibration path**

Single-level checkpointing is designed — peak = 2√(N·S·I), optimal L\* = √(N·S/I), N_max = B²/(4·S·I) — and **not built**. Without it a 512² differentiable run caps at B/I = **124 steps** against the ~9,665 a 6 h storm needs. Blocks skipped invariants inv17 (×2) and inv19.

**This is the next real build item after the conditioning fix (item F).** Phase 3 calibration cannot start without it.

---

### O — 22 of 51 solver invariants are skipped, and every one is the full-domain or full-duration variant — **OPEN**

Full inventory with per-test reasons in `docs/d4-audit.md` §6B. The whole suite verifies on small tiles and short runs — **V7 gone systemic**, not an isolated instance. The 23.86 m defect was a full-domain phenomenon that no tile test could have reached, which is the concrete demonstration that the gap matters.

**Run them once, against the DEM we intend to keep** — i.e. after item F's rebuild, not before, so the run is not spent on a DEM that is about to be replaced.

`tests/test_manifest_provenance.py` carries 4 further skips labelled DEBT. Items 1 and 3 of those should be closeable now against the successful acceptance run at `78610c2`, which wrote a real SHA and a 72,376 B binary sidecar instead of a 2.33 MiB inline schedule.

---

### P — `tiling.py` — **NOT BUILT**

Design question B established that VRAM does not force tiling for forward simulation (988.9 MiB of 8188 at 10 m full city). Tiling is still required for the **calibration backward pass** and for surrogate training. Not on the critical path until Phase 3. Formerly open items #20 and #27.

---

### Q — ANUGA cross-validation and the analytical ladder — **NOT BUILT**

Items 7 and 9 concluded that solver correctness rests on ANUGA plus analytical solutions (Ritter, Stoker, Thacker, MacDonald), with LISFLOOD-FP unavailable and the EA Test 8A inputs unobtainable. **That ladder is designed and not yet built.** Formerly open items #22 and #28.

Analytical cases need no external files and should come first — they are the only correctness evidence that cannot be blocked by a download.

---

### R — Grid refinement study and per-class gradient counts — **NOT BUILT**

Formerly open items #21 and #24. Grid refinement is a Phase 8 resolution-sensitivity study per design question A; per-class gradient counts support the conveyance calibration. Neither is load-bearing now.

---

### S — Phase 3 validation against September 2022 — **NOT STARTED; the binding constraint is not rainfall**

IMERG credentials, EULA and all 672 granules are done (item 8). **The binding constraint is observed flood extents, not forcing.** Validation rests on the BBMP point set (item 5) and the Sentinel-1 pair (item 6); the NRSC inundation layer that would have been the third was dropped with Bhuvan (item 4).

Note the timing subtlety recorded in item 8: the SAR scene is 06:10 IST on 5 Sept, hours *before* that day's evening peak, so it images flooding accumulated 30 Aug – 4 Sept and must be compared against modelled state at that instant, not against the event maximum.

---

### T — Are P99 = 0.99 m and P99.9 = 3.92 m too deep? — **OPEN; Phase 3 settles it**

Canonical depth percentiles from the acceptance run: P50 0.000000 m (dry), P90 0.000997 m, P99 0.989823 m, P99.9 3.920597 m, max 23.223906 m.

**12 km² above a metre is more than real Bengaluru events produce.** The canyon story (items E, F, G) explains the extreme tail; whether it also explains P99 is open. Boundary outfall at **61.5% of rainfall** is a corroborating canyon symptom — water is leaving the domain far too efficiently. Drains take 7.5%, below the 12.8% the capacity raster allows, which is consistent with most of the domain being dry.

Re-measure after item F's rebuild before drawing any conclusion; Phase 3 is what actually settles it.

---

### U — Rotate the sudo password — **OPEN (security)**

The password appeared in an early Claude Code transcript. Not project-blocking; still needs doing.

---

## Summary

| # | Item | Verdict |
|---|---|---|
| 1a | KSNDMC live API | ✅ RESOLVED — 266 gauges @ 15 min. **`RAIN` is CUMULATIVE (proven 2026-08-18)** over 477 captures: monotone rise through the 16 Aug storm, invariant after rain stops, all decreases at the 08:30–09:00 IST rain-day reset (not midnight) |
| 1b | KSNDMC historical Sept 2022 | ⛔ **BLOCKED** — the one real gap |
| 2 | FABDEM licence | 🟡 **PARTIAL** — CC BY-NC-SA, but corroborated from secondary sources only; `data.bris.ac.uk` refused four fetches. **Decision reversed 2026-08-15: FABDEM is primary and won at build time; the non-commercial exposure is live, not dodged.** Re-verification against the primary source is outstanding |
| 3 | CartoDEM / Bhuvan | ⏭ SKIPPED by decision — GLO-30 suffices |
| 4 | NRSC Sept 2022 inundation | ⏭ SKIPPED by decision — validation drops to 2 label sets |
| 5 | BBMP flood-prone list | ✅ RESOLVED — 385 pre-geolocated points |
| 6 | Sentinel-1 Sept 2022 | ✅ **RESOLVED — both scenes downloaded, cover full BBMP** |
| 7 | LISFLOOD-FP build | ⛔ BLOCKED — vendored CUB vs CUDA 12.4; **use ANUGA instead** |
| 8 | Rainfall forecast source | 🟡 PARTIAL — Open-Meteo works; **IMERG EULA accepted 2026-08-15, 672/672 granules on disk and verified**; IMD/DWR unverified |
| 9 | UK EA benchmark | 🟡 PARTIAL — definitions yes, input data no; not load-bearing |
| A | Grid resolution | ✅ RESOLVED — **10 m** |
| B | Tiling | ✅ RESOLVED — **untiled**; VRAM is not the constraint |
| C | Training scenario count | 📋 METHOD ONLY — Phase 5 |
| D | Compute pool | ✅ **Buy nothing** — overnight on the dev laptop |
| E | D4 sealing as the depth cause | ✅ RESOLVED — **falsified**, 2.7% drop against a substantial one predicted |
| F | Conditioning carves the tank cascade | 🟡 **IMPLEMENTED 2026-08-18, VERIFICATION REJECTED** — whitebox breach is gone, excavation 20.8 → **1.66 M m³ (−92.0%)**, max cut 22.94 → 0.4999 m, building cells carved 0, `connectivity: D4` asserted at the seam (V8 discharged). But three of seven invariants cannot fail as written, and Part E ran **100 steps (0.26% of duration)** against a 6 h storm. See item **V** |
| G | Excavation concentration | ✅ RESOLVED (measured) — one trench = 48.92% of all excavation |
| H | D4 pit-creation figure | ✅ **RESOLVED 2026-08-18** — set difference on a validated post-D8/pre-D4 intermediate: **131,415 gross created**, 74,599 destroyed, 215,668 survivors. Post-D8 total 347,083 reproduced the manifest exactly. All three circulating pairs are wrong for the purpose |
| I | FABDEM renders tanks as plateaus? | 🟡 **FALSIFIED (single discriminator)** — fill ratios 0.20–0.60, not >0.90. floor σ_z proved **mathematically degenerate** and cannot discriminate. Bellandur 1.08 m vs 1.78 m max depth still unreconciled |
| J | D4 building midpoints | ⏸ DEFERRED — justification replaced; re-measure after the rebuild |
| K | Courant overshoot | ✅ **RESOLVED + FIXED** — step rejection landed; `cfl_ceiling` 0.85 by measured rule (0.563% rejections vs 84.9% at 0.70) |
| L | Non-negativity tolerance | ✅ **FIXED** — per-cell `4·eps·max(h_old, Σ|flux|·dt/A)`, mixed-regime test red-then-green |
| M | `track_cell_sinks` flag | ✅ **DELETED** — per-cell sink minimum now always computes |
| N | `checkpointing.py` segmentation | ✅ **BUILT AND VERIFIED** — 9,665 steps at 512² (78× the 124 wall), peak 2,288.9 MiB vs 2,321.1 predicted, K*=25 vs 24.98 predicted, gradcheck True, exponent 0.482≈√N. **Phase 3 calibration unblocked** |
| O | Skipped invariants | 🟡 PARTIAL — inv17 and inv19 closed by N; **22 remain**, all full-domain/full-duration. Run once, against the DEM we keep (after F) |
| P | `tiling.py` | ⬜ NOT BUILT — needed for the backward pass, not the critical path |
| Q | ANUGA + analytical ladder | 🟡 PARTIAL — ladder built (Ritter/Stoker/Thacker/MacDonald). Thacker 1.38 mm at Fr 0.028. **Convergence orders invalid**: `max_dt_s` pinned dt on coarse grids. ANUGA still not built |
| R | Grid refinement, per-class gradient counts | ⬜ NOT BUILT — not load-bearing |
| S | Phase 3 validation vs Sept 2022 | ⬜ NOT STARTED — binding constraint is flood extents, not rainfall |
| T | P99 / P99.9 too deep? | 🟡 **REFRAMED** — P99 and P99.9 are timestep-converged (1.8% / 3.3% across an 8× α range). **Max depth is not** — it moves 18% with α and 31% with `cfl_ceiling`, non-monotonically. Report P99, never max. Phase 3 still settles the physics |
| V | Conditioning verification vacuous | 🔴 **OPEN** — Part E ran 100 steps; invariants A, C pass by construction; cascade check asserted not measured |
| U | Rotate the sudo password | 🔴 OPEN (security) |

**Rows N–U were missing from this table until 2026-08-17.** They had full sections below and no summary row, so the summary understated what is open as 13 items against an actual 21 — including **N**, which its own section calls the next real build item. Anything added as a section gets a row here in the same edit.

**Highest-value next actions, in order** — ordered by information value per unit cost. **Rewritten 2026-08-18: items H, I, K, L, M, N and 1a all closed overnight, and F is no longer held.**

1. **Conditioning redesign and terrain rebuild (item F).** Now unblocked — both gating measurements landed. What they constrain:
   - The D8 pass creates **131,415** D4 pits gross (37.9% of the 347,083 post-D8 total), not the 65.8% adjacency figure. Removing the deep cuts removes about **two-fifths** of D4 pits, not two-thirds — the deferral in item J must be re-argued on cut depth, which it already is.
   - Retained basins hold **real bathymetric storage** (item I): fill ratios 0.20–0.60, so DEM depression volume is capacity, not freeboard.
   - Classification **cannot rest on geometry**. Both candidate discriminators are dead: the depth/√area aspect ratio misclassifies 4 of 7 OSM-confirmed quarries and puts a lake inside the quarry cluster, and floor σ_z is mathematically degenerate. Use OSM + Sentinel-1 + BBMP, geometry only as a tie-breaker.
   - Intersect OSM water with a **30–50 m buffer, not 0 m** — DEM-derived boundaries sit 1–3 pixels off hand-drawn OSM vectors (Kannuru Lake is 27.84 m out).
   - Use a **≥4-cell (400 m²) minimum** for the object inventory; it reconciles to 26,090 objects / 29.83 M m³.
2. **Decide antecedent basin state before the rebuild lands**, or the first post-fix run is scored against a wrong baseline. Item I falsified the plateau, so occupied storage is **not** baked into the terrain and starting dry genuinely removes up to ~21.8% of the storm. Sentinel-1 permanent-water masks are on disk at three thresholds.
3. **Fix the analytical convergence study (item Q).** `runs/analytical_ladder/run_benchmarks.py` holds `max_dt_s` fixed while dx refines, pinning dt identically on the two coarsest grids, so the reported orders measure nothing. Scale `max_dt_s` with dx and re-run. Small, and it is the only thing standing between us and a real correctness claim.
4. **Run the 22 remaining full-domain invariants (item O)** — once, after F's rebuild, against the DEM we intend to keep.
5. **Re-verify the FABDEM licence (item 2).** Bristol has now timed out on five attempts. Still item 2's own precondition for any public claim, and FABDEM is primary.
6. **Phase 3 validation (item S).** Forcing and scoring both exist now and the IMERG path is validated end to end (39.15 mm against a recorded 39.2 mm). The binding constraint remains observed flood extents.
7. **ANUGA cross-validation (item Q)** and **`tiling.py` (item P)** — P is now the next real build item after F, since N is done and calibration is tiled by design.
8. **Leave the KSNDMC capture running.** It has already paid for itself by settling item 1a.
9. Send the KSNDMC data request (item 1b); verify IMD nowcast access (item 8).

*Deliberately not on this list:* buying Colab Pro (D says the numbers don't justify it yet), chasing the EA input files (item 9 is no longer load-bearing now that ANUGA and analytical cases carry correctness), and Bhuvan (items 3/4, skipped by decision).
