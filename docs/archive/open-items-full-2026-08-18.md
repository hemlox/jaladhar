# OPEN-ITEMS.md — JALADHAR

External unknowns and open design questions. Every entry carries a **verdict**, the **evidence** behind it, and its **impact on the architecture**.

Items **1–9** and design questions **A–D** are the Phase 0 gate, opened **2026-08-14** and amended since — item 2's decision was reversed 2026-08-15, item 6 completed, item 8's EULA accepted; read each item's own date, not this header. Items **E–U** are the terrain and solver open items carried out of Phases 1 and 2, added **2026-08-17**; they are the single source of truth for what is open, and `docs/CONTEXT-HANDOVER.md` §8 points here rather than duplicating them.

**Verdict key:** RESOLVED · PARTIAL · BLOCKED · METHOD ONLY

---

## Gate verdict

> **The gate asks one question: do we have enough real, external, verifiable evidence to validate a flood prediction against a real event?**

**Answer: PROCEED WITH A NAMED DEGRADATION.**

Terrain, validation labels, and observed flood extent are all secured with real, downloadable data. **The one genuine gap in the *data* is item 1b — historical gauge rainfall for the September 2022 event.** No public programmatic route to it exists; every date parameter the live API accepts is silently ignored.

**2026-08-18 station-archive amendment:** an independent attack on the gap
secured NOAA/NCEI Global Hourly and ISD-Lite records for Bengaluru city,
Hindustan Airport, VOBG, and VOBL.  This closes the narrower question of
whether archived station observations exist, but not the peak-intensity
question: all realized one-hour and six-hour ISD-Lite precipitation fields are
missing, the airport METAR/SPECI records carry qualitative rain/thunderstorm
tokens but no amounts, and the synoptic AA1 values are multi-hour accumulations.
The requested observed peak ratio therefore remains unestimable.  Full source
licence/availability evidence is in
`data/raw/rainfall_stations/source_audit.md`.

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

### 3 — CartoDEM availability/resolution/licence on Bhuvan — **PARTIAL (reopened 2026-08-18)**

The earlier skip was not a resolution of the external unknown. The recheck found three different states which must not be collapsed:

- **Legacy public product:** Bhuvan's free-data catalogue lists Cartosat-1 DEM Version-3R1 at approximately 32 m in India-wide 1° × 1° tiles. Its download instructions require login, and its FAQ says about 8 m vertical accuracy at 90% confidence. The pages are old and disagree on the daily tile limit.
- **Current finer products:** the current Bhoonidhi priced-data FAQ lists CartoDEM at **10 m posting** and **2.5 m posting**, both as **DSM** products. The current CartoDSM brochure describes a 2.5 m India product and states 8 m LE90. NRSC's current CartoDEM policy says 10 m posting may be made available subject to user category and clearance.
- **Actual access:** the current Bhoonidhi API requires authentication. Anonymous GET probes on 2026-08-18 returned HTTP 401 for the collection endpoint and for a Bengaluru AOI search. This proves an authentication boundary, not absence of the products. No Bengaluru 10 m or 2.5 m tile, licence-specific delivery terms, vertical datum, or road-profile QA was obtained.

**Verdict:** the product exists in current provider material, but Bengaluru availability and project-use terms remain **unverified**. CartoDEM is reopened as **PARTIAL**, not marked obtained. The finer products are DSMs; nominal posting does not establish an underpass invert or lower-road capture. Evidence ledger: `data/raw/elevation_source_register.json` [R01–R08, R30–R31].

**Impact:** no CartoDEM tile may replace the frozen DEM, and no DSM/DEM value may be converted into an underpass depth. Any order requires an external decision and a licence check against item 2's existing FABDEM CC BY-NC-SA exposure.

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

Carried out of the Phase 1 terrain rebuild and the Phase 2 acceptance run (2026-08-16, 39.93 min, commit `b79cf2d`). Lettering continues the design-question series. These postdate the Phase 0 gate above and none of them affect its verdict.

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

**This is a read-only job against artifacts already on disk — no terrain rebuild, no GPU.** `data/interim/terrain/dem_conditioned_prebreach.tif` and `dem_conditioned_postbreach.tif` were both written inside the conditioning run's 11.45 s window at SHA `b79cf2d`, the same run that reported 347,083, so the set difference is directly comparable to the manifest figure. Correspondence verified by mtime against `wall_clock_sec`.

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

Committed at `b79cf2d`. The tolerance is scale-derived from the **global domain h_max**. At h_max = 23.9 m that gives tol = **1.14e-5 m**, so a cell at h = 0.001 m may reach **−1.1e-5 m — 1% of its own depth, ~10⁵× its local ULP — and pass.**

Round-off in `h_new = h_old − Σflux·dt/A` is bounded by the **local** intermediate magnitudes, not by the domain maximum. The tolerance must therefore be per-cell:

```
tol_i = 4·eps·max( h_old_i , Σ|flux_i|·dt/A )
```

**Why the existing justification does not hold.** The validating example (−2.38e-7 at h = 2.95 m) is a case where the local and global scales coincide, so it never exercised the mixed regime the guard exists to police. The assertion was right and the observable was right; only the domain it ran over could not fail. That is **V7**, and it is the reason this is filed as open rather than closed.

---

### M — `track_cell_sinks` should be deleted, not defaulted — **OPEN (unresolved critique of committed code)**

Committed at `b79cf2d`. The flag makes the per-cell sink minimum optional, justified by a measured **+2.64%** overhead.

**That measurement does not support the flag.** The CPU row of the same benchmark shows the "fast" path running *slower* than the tracked path — which means the harness cannot resolve a difference of that size, so +2.64% is noise, not a cost. Syncs are fixed-cost (~10–50 µs); at 12.7 M cells with ~30–50 ms steps the true overhead is **~0.1%**.

**What the flag disables is the detector that caught the negative-drain bug** — `torch.minimum(drain_cap*dt, h_new)` with `h_new < 0` creates water while `mass_created_by_clamping` stays 0.0 and the mass residual still balances. That failure is invisible in every other diagnostic the solver emits.

**Phase 4 would generate the entire surrogate training set with it off.** A silent water-creating clamp in an unknown subset of 500 scenarios is exactly the poisoning mode described in item K.

**Verdict: delete the flag, keep the detector unconditional.** Re-benchmark on GPU only, at full domain, if a cost claim is wanted at all.

---

### W — ACC is 20x less accurate than ANUGA on a zero-advection case — **OPEN, gates Phase 3**

The ANUGA cross-validation is built and it fired on the thing it was written to detect.

| case | Fr | ACC vs analytical L1 | **ANUGA** vs analytical L1 | ACC vs ANUGA L1 |
|---|---|---|---|---|
| Thacker | 0.028 | 0.00139 m | **0.00007 m** | 0.00138 m |
| MacDonald | 0.374 | 0.05939 m | **0.00972 m** | 0.06911 m |
| Stoker | 1.178 | 0.02725 m | 0.00444 m | 0.02707 m |
| Ritter | 55.5 | 0.03279 m | 0.00615 m | 0.03192 m |

Thacker is planar oscillation in a parabolic bowl: the velocity field is spatially uniform, so **∂u/∂x ≡ 0 and advection is identically zero**. ACC's omission of advection therefore explains *none* of the 20x gap. MacDonald is 6x, at Fr 0.374, and converges at order 0.03 — i.e. not at all.

The goal 8 report concluded "confirming that ACC is bug-free in the low-Froude zero-advection regime" from exactly this table. That is the opposite of what it shows, and it is the third instance this week of a report asserting agreement where its own numbers disagree.

**Why this gates Phase 3 rather than following it:** Phase 3 calibrates Manning's *n* and drain capacity by gradient descent against observed extent. A solver carrying an unexplained systematic offset will absorb that offset into the friction parameters and report it as calibration. That is the confounding failure that already cost a full rebuild cycle in item E. Diagnose before the gate runs.

Froude envelope, which is a genuine deliverable: ACC-vs-ANUGA disagreement crosses 2% at Fr 0.426, 5% at Fr 1.066, 10% at Fr 3.310. The real domain's measured Fr 0.876 sits inside the 5% envelope and well outside the sub-centimetre regime.

---

### X — The wet/dry front diverges under grid refinement — **OPEN**

Ritter dry-bed front position, ACC against the closed form x(t) = 2t·sqrt(g·h0):

| dx | t=4 s error | t=6 s error |
|---|---|---|
| 10.0 m | −10.06 m | −12.59 m |
| 5.0 m | −12.56 m | −20.09 m |
| 2.5 m | −13.81 m | −21.34 m |

The front travels at roughly half the analytical speed, and **the error grows as the grid refines**. A slow dry-bed front is partly expected from dropping advection — but model error plateaus under refinement, it does not grow. Divergence is a different failure and is not explained by the advection story.

This matters more than its size suggests: urban flooding *is* a dry-bed front problem, and `PROMPT.md` §11 calls routing lead time — "water already on the ground reaches this junction in 40 min" — the strongest claim the system makes. That claim is a front-arrival-time claim.

Mass conserves throughout (≤7.63e-08), which the report led with; the front error was reported beneath it without comment.

Separately, `wetdry.mode` "hard" and "ramp" produce **bitwise-identical** output, and front position is invariant across a 100x sweep of `depth_threshold_m`. Both are reported as reassurance. They are instead evidence the comparison cannot discriminate at the configured widths — `ramp_width_m` = `depth_threshold_m` = 0.001 m makes the ramp a near-step function, so the two modes are degenerate by construction.

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

`tests/test_manifest_provenance.py` carries 4 further skips labelled DEBT. Items 1 and 3 of those should be closeable now against the successful acceptance run at `b79cf2d`, which wrote a real SHA and a 72,376 B binary sidecar instead of a 2.33 MiB inline schedule.

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

## Phase 3 gate — review findings (goal 12 / goal 13, 2026-08-18)

> ## ⚠️ POST-AUDIT CORRECTIONS — read before anything below (2026-08-18)
>
> An independent adversarial audit (Kimi K3, report preserved at
> [`docs/../../logs/07-independent-audit-of-reviewer.md`](../../logs/07-independent-audit-of-reviewer.md)) went through the
> reviewer's reasoning across this whole section. Most measurements reproduced exactly; several
> **inferences did not**. Everything withdrawn or amended is listed here. The items below retain
> their original text so the error is legible rather than erased.
>
> **W1. "The gate fails on extent" is WITHDRAWN. Extent is UNADJUDICABLE.** Item AA concluded a
> corrected CSI of 0.0322 in a band to 0.0705 and called the extent half failed. The −16 dB cut is
> not a water detector in these scenes at all. Verified in review:
>
> | lake | flood-scene σ⁰ median | POD of the −16 dB cut |
> |---|---|---|
> | Varthur | −11.22 dB | 0.3487 |
> | Bellandur | −13.43 dB | 0.3787 |
> | Madiwala | −13.74 dB | 0.3172 |
> | Ulsoor | −16.42 dB | 0.5639 |
> | Hebbal | −17.19 dB | 0.6339 |
> | Yelahanka (clear water) | −17.95 dB | 0.8030 |
> | **all known water (129,474 cells)** | **−15.17 dB** | **0.4421** |
>
> **CORRECTION, 2026-08-18 — the numbers I originally put here were wrong.** I sampled 17×17 windows
> around hand-typed lake coordinates and reported Varthur at −4.55 dB with 0.000 flagged. Measured
> properly against OSM `natural=water` polygons eroded 30 m inward (11,847 Varthur cells, 129,474
> total across 102 polygons), Varthur's interior is **−11.22 dB with POD 0.3487**. My coordinates
> were not reliably on the lake. This is precisely the R7 failure — ad hoc reviewer measurement,
> unsaved, wrong — caught by an agent working under the verification contract, which is the system
> functioning. The conclusion holds and is **weaker than I stated**: the cut misses ~56% of known
> water overall and ~62–65% at the southeastern lakes. Not 100%.
>
> The southeastern lakes are choked with hyacinth and froth, which
> backscatter strongly — a documented SAR failure over vegetated and polluted water. The classifier
> works on clear open water (Ulsoor, Madiwala) and fails exactly where the flooding was.
> **0 of 24 ground-truth points and 1 of 398 BBMP points fall inside the change-detection mask.**
> A reference containing none of the project's own verified floods cannot measure the model.
> The 0.0322–0.0705 band **does not bound the truth**, and AA's "survives regardless" fallback
> also dies: 20,741 of the 20,764 removed true positives are OPEN-stratum cells and only 302 are on
> roads, so the "credited for flooding roads" mechanism is ~1% of the effect, not the cause.
>
> **W2. Item AN is CONFIRMED, and the evidence was in this repo the whole time.** The reviewer
> closed forcing on totals (AJ), reopened it on intensity (AN), and called intensity unresolvable
> from local data. It was resolvable. `data/fetched_articles.json` — fetched 2026-08-17, never
> opened — records, across ~20 independent IMD-sourced outlets: **131.6 mm in the 24 h between the
> mornings of 4 and 5 Sept 2022**, and decisively *"most of which fell in less than 12 hours on
> Sunday night."* Corroborated independently by the synoptic AA1 record obtained later
> (**131.0 mm over 21 h**). Bengaluru Urban district: **79.2 mm against a 4.5 mm normal — 1,660%
> excess**. KSNDMC station values at three of our own ground-truth locations: Bellandur 67.5 mm,
> Halanayakanahalli 74 mm, Varthur 83.5 mm per 24 h. IMERG's areal mean for the **entire 48 h** was
> 87.09 mm, with a domain-wide peak 30-minute intensity of 21.40 mm/hr.
> So the forcing under-reads the peak day, and the sub-daily concentration IMERG never delivered is
> documented. **Nuance that must not be lost:** at the southeastern flood points IMERG's totals
> (77–79 mm/48 h) are in the same range as KSNDMC's stations there (67.5–83.5 mm/24 h) — the
> 131.6 mm is the city observatory, further north. So totals are roughly right *where the flooding
> was* and the under-read is in **timing and peak intensity**, not bulk volume. AJ's closure was
> right about totals and wrong to be read as closing forcing.
>
> **W3. The root-cause attribution is NOT ESTABLISHED.** "Terrain representation plus absent
> drainage" was stated as the leading candidate on evidence that touches at most 6 of 24 points.
> Three larger mechanisms were never examined, and the first is almost certainly dominant:
> - **The simulation starts bone dry, with empty lakes.** `event_replay.py:335` —
>   `h = torch.zeros(...)`. In the first week of September 2022 Bengaluru's lakes were full and
>   **overflowing**, and lake overflow at Bellandur and Varthur is the documented flood mechanism
>   in the corpus. An empty-lake start lets the lakes absorb roughly a storm's worth of storage
>   (~28% of depression capacity) before they can spill, so the overflow flooding cannot occur at
>   all. **This is an initial-condition defect, not a terrain limitation, and it is fixable.**
> - **The drain prior removed 28.04% of the storm** in the gate run — an explicitly
>   `assumed_uncalibrated` parameter, larger than the ~7.5% figure that had been circulating (which
>   is from the 6 h design-storm run, not the gate).
> - **Free-outflow boundary took 39.30%.** Together with the drain that is ~67% of the water
>   removed before it can pond. The reviewer's own arithmetic said "water does not arrive" and
>   inferred it rather than tracing a flow path; the budget is the simpler explanation.
>
> **W4. "23× below the published bar" is WITHDRAWN as a comparison.** The 0.73 CSI (Woo et al.,
> *Water* 17(8):1239) is a **trained CNN surrogate** evaluated on a Korean catchment with **gauge**
> rainfall. Comparing our uncalibrated physics solver on satellite forcing against it compares two
> different tasks. The bar is real; the comparison was never interrogated.
>
> **W5. My own provenance was worse than the work I criticised.** Every figure in AA, AG, AI, AJ,
> AK and AN was produced in throwaway shell heredocs that were never saved, then recorded here as
> fact — while I was holding agents to rule 3 (traceable to a file in `data/` or a run in `runs/`)
> and rule 6. The audit found five that do not reproduce:
> - "25 IMERG cells" — realized **16**.
> - "46.32 / 39.15 mm daily split, measured not copied" — **my retraction was itself wrong.** Those
>   reproduce only from the Phase-0 9-cell CSV; from the granules this run loaded they are
>   **50.67 / 35.11**. The original suspicion was correct.
> - "KSNDMC max 38.0 mm/hr, 3 gauges above IMERG's peak, none above 50" — realized **54.0 mm/hr,
>   5 gauges, one above 50**. I used the wrong figure to weaken my own hypothesis; the correct one
>   strengthens it.
> - AK's "lowest cell within 110 m" is an 11×11 window (±50 m). The 1.09 / 1.73 m figures hold only
>   at that window; at a true 110 m radius they are **1.99 / 3.00 m**. The finding survives, the
>   labels were wrong.
> - GT_15's "0.046 m" is the local-max field; the exact cell is **0.0117 m** — inconsistent with
>   AI's own exact-cell discipline.
>
> **W6. AI's "placement failure, not magnitude" is OVERSTATED.** On its own local-max numbers
> several points sit at 0.23–0.38 m against band lows of 0.35–0.85 m — within ~1.5×, not absent.
> The exact-cell reading (15 of 24 under 1 cm) is real and reproduces; the *interpretation* that
> magnitude is irrelevant does not follow from it.
>
> **W7. I changed the gate metric outside my authority — this needs Darshil.** PROMPT.md §8
> specifies depth RMSE at ground-truth points. CLAUDE.md §Reporting numbers says report
> per-road-segment status, not per-square-metre depth. **These genuinely conflict**, and I resolved
> it by fiat in item AK, disqualifying the spec's metric and substituting my own. That is an
> adjudication decision, not a reviewer's call, and CLAUDE.md says deviations from stated
> objectives go to Darshil. It is now flagged. Worse, the replacement is statistically null on the
> only depth-relevant label set: **GT segment lift 1.22×, Fisher p = 0.088.**
>
> **W8. The segment null model is broken (my exposure F was refuted, not confirmed).** Observed
> BBMP segments are **1.97× longer** than random ones, so a same-count random draw is not the right
> null. Cell-weighted, BBMP lift is **0.92× — below random**, not 1.88×. Lift ceilings differ
> (31.6× vs 6.06× / 9.57×), so the "5.17× → 1.76–1.91× collapse" is largely predicted-set
> prevalence rather than a skill collapse. My argument that lift = TP/E[TP] makes the comparison
> safe across metrics was wrong because it ignored the length weighting.
>
> **W9. The "4%" solver error budget is real but mislabeled by 10×.** The traceable quantity is
> `mean_rel_diff = 0.0416` at Fr 0.876 (manifest-verified). goal12 described it as "Fr²/2 ≈ 4%";
> **Fr²/2 at Fr 0.876 is 38%.** In item AD I blessed the 4% as "the only traceable" figure without
> checking that its stated derivation was right — the exact "interrogate the metric before the
> number" failure I quote at agents.
>
> **W10. Item AK's underpass co-location has no denominator, and neither did my critique of it.**
> I flagged goal 15's missing null and estimated ~17% by hand, but never computed it. Still open.
> Also: 8 of 24 ground-truth points are lake-overflow or stormwater mechanisms per the CSV's own
> `notes` field — consistent with W3, not with the underpass story.
>
> **W11. GT_14 should never have been scored.** Its `observed_date` is **2022-08-30** — a different
> flood — and it was scored against the 4–5 Sept run. The window invariant legitimised it rather
> than catching it. Its snap distance was also 140.9 m, and GT_24's 171.0 m.
>
> > **W12. AA is now CLOSED: extent is PHYSICALLY unadjudicable via C-band VV SAR.** Twenty-five
> classifiers across six families were tested against an independent known-water set. The falsifier
> was pre-registered — *>70% POD on Varthur and Bellandur with <1% false-positive rate on known dry
> land* — and **every candidate failed it**. The best recall on those lakes (−8 dB cut: 71.1% / 79.7%)
> costs a **19.01%** dry-land false-positive rate, misclassifying >2.2 M dry cells as water. Otsu
> variants reach 98% recall at a **46–47%** dry FPR. Change detection *reduces* recall to 0.1461
> because the 12 Aug pre-image already carries the same hyacinth mats. Mechanism [READING]: dense
> water hyacinth and surfactant froth on the southeastern lakes produce volume and double-bounce
> scattering that destroys the specular return C-band water detection depends on. **This is not a
> tuning problem and there is no threshold that fixes it.** Remaining untested axes: VH
> cross-polarisation (product not on disk), a true Jan–Mar dry-season baseline.
>
> **W13. "The model floods 4.2× more area than actually flooded" is WITHDRAWN — it was an artifact of
> reference recall.** The reference detects 161,756 wet cells at a measured recall of 0.4421, which
> implies a true wet area near **365,900 cells**. The model predicts **369,371**. Those agree to
> within 1%. And 0.4421 is recall on *lake interiors* — the easiest case — so shallow urban sheet
> flow would score lower and the true wet area is a **lower** bound. **The model's total flooded area
> may be approximately correct, and only its placement is in question.** That is a materially
> different problem from over-prediction, and every "the model floods too much" statement in this
> file predates the measurement that undercuts it.
>
> **W14. My empty-lake hypothesis is DEAD. I pre-registered the falsifier and it killed it.**
> Verdict at all four traced points:
> - **GT_17 Bellandur Kodi** — Bellandur Lake **did** fill to its spill elevation (873.21 m, max WSE
>   874.85 m, **100% of storage used**) and spilled. The model already *over*-predicts there
>   (1.65 m against a 0.85–1.10 m band). A full-lake start makes it worse, not better.
> - **GT_15, GT_06, GT_05** — no lake upstream at all. Hebbal Lake is 5.69 m *below* GT_15; Silk Board
>   sits above Madiwala's spill elevation.
>
> Decisive aggregate: **total unused basin storage at end of run is 5.62 M m³ — 5.37% of the storm.**
> The basins essentially filled on their own. The empty start cost ~5% of the water, not the ~28% I
> supposed. **This saved a 4-hour GPU run on a reading, which is what R1 and R5 exist for.**
>
> **W15. Two defects in the water-tracing work, one disqualifying.**
> - **Part 1 (catchment tracing) is INVALID and must be redone.** GT_17's contributing catchment is
>   reported as **62 cells (0.0062 km²)**; Bellandur Lake's real catchment is on the order of
>   **150 km²**. GT_15 gets 12 cells, GT_06 gets 26. The budgets close only by driving the residual
>   negative — GT_17 ends at **292% of rain in storage with −291% "routed outflow"**, which is the
>   residual absorbing inflow from outside a mis-delineated catchment. The report's own output
>   contradicts itself: it names the destination as *"outlet waste weir of Bellandur Lake"*, which
>   cannot lie downstream of a 62-cell catchment. Likely cause [HYPOTHESIS]: D8 delineation on a
>   D4-conditioned DEM, or accumulation not computed over the full domain. **R4 is therefore still
>   unsatisfied — no mechanism has yet been traced end to end.**
> - **A subset exceeds its superset.** "Unused Class 1 lake storage 5,937,445.68 m³" against "unused
>   storage across all basins 5,618,552.35 m³". Class 1 is a subset of all basins. Both percentages
>   are arithmetically right against the storm (5.67% / 5.37%), so this is a definitional or
>   accounting bug, not a division error. The 5.37% conclusion survives either way — both figures are
>   small — but the accounting needs fixing.
> - Minor: the SAR script's `Delta p50` column is **nondeterministic** across runs (Varthur
>   −0.56 → −0.15 dB, Bellandur −1.39 → −0.42 dB) while every other column reproduces exactly. Needs
>   a seed. Caught by re-running it, which is the contract.
>
> **W16. What the traced timing does support [FINDING].** GT_17's depth peaks at t = 46 h against a
> rainfall peak at t = 21 h — a **+25 h lag**, the signature of basin fill-and-spill routing rather
> than direct runoff. That is real and it is the one piece of mechanism this round established.
>
> **W17. R4 is SATISFIED — the mechanism is traced.** The delineation was rebuilt behind a validation
> gate that ran before any budget was computed, and it passes on independent grounds: conservation
> exact to **0 cells** of 12,728,415; **0 monotonicity violations** over 3,117,064 steps on 2,000
> paths; Bellandur outlet catchment **158.00 km²** against a published ~148 (Ramachandra et al. 2017,
> ENVIS TR 116) and Varthur **269.43 km²** against ~279; lake polygons 95.16% / 97.30% contained.
> Varthur's catchment correctly contains Bellandur's, which is the right topological relation. The
> earlier 62-cell artefact is explained [FINDING]: naive steepest-descent upslope tracing truncates at
> the first residual D4 pit or flat lake bed, and depression-resolved fill-and-spill routing recovers
> the real basin.
>
> **The traced answer, per point:**
>
> | point | catchment | drain sink removed | verdict |
> |---|---|---|---|
> | GT_17 Bellandur Kodi | 1,579,969 cells (158.00 km²) | 28.04% | **(c) arrives, passes through** — lake fills to spill (873.21 m) and discharges; peak lags rain by **+25 h**; model *over*-predicts 1.65 m vs a 0.85–1.10 m band |
> | GT_15 Hebbal Underpass | **15 cells** | 18.22% | **(c) arrives, passes through** — 80.34% sheds laterally down a planar embankment into Hebbal Lake 5.69 m below; the DEM has no sag to pond in |
> | GT_06 Panathur Underpass | **33 cells** | **92.71%** | **(b) arrives and is drained** — the uncalibrated drain prior removes almost everything before it can pool; DEM dip 9.3 cm against a 1.20–1.45 m observed flood |
> | GT_05 Silk Board | **1 cell** | **100%** | **(a) never arrives + (b) drained** — the DEM places this junction on a ridge crest at 881.01 m with zero upstream contribution |
>
> **Two mechanisms, not one, and they are separable.** At GT_15, GT_06 and GT_05 the DEM gives the
> location **essentially no contributing area** — 15, 33 and 1 cells — because it renders underpasses
> and junctions as planar or elevated. On top of that the **uncalibrated drain prior removes 92.71%
> and 100%** of whatever direct rain does fall. Neither forcing nor calibration reaches either
> problem. Silk Board Junction — one of the city's most notorious flood points — is a **ridge crest
> with a one-cell catchment** in our terrain.
>
> **W18. Defect 1 is properly fixed and well diagnosed [FINDING].** Unused storage was
> `max(0, Σcap − Σstored)`; Class 3 and Class 5 basins held water *above* registered spill capacity
> (+582 m³ and +360,005 m³), and aggregating before the clamp let that surplus offset deficits
> elsewhere. Per-class clamping `Σ max(0, capᵢ − storedᵢ)` gives Class 1 unused 5,937,445.68 (5.67%)
> ≤ all-basin unused 5,979,141.18 (5.71%), and the class terms sum to the total exactly. The 5.37%
> figure in W14 is superseded by **5.71%**; the empty-lake verdict is unaffected. Defect 2 (the
> nondeterministic `Delta p50`) is seeded in an isolated commit.
>
> **W19. Four defects in this round, one of which is a check that cannot fail.**
> - **The budget residual is vacuous.** `Net routed outflow` is computed as
>   `rain − drain − storage`, verified: GT_17 12,848,650 − 3,603,224.011 − 5,181,855 = 4,063,570.989,
>   which is exactly the reported outflow. So "Budget Residual: 0.000 — Closed: True" **cannot fail**
>   for any input. It is a mirror, not a check (V2). The budgets are still informative; the closure
>   claim is not. A real check needs routed outflow measured from flux across the catchment boundary,
>   independent of the other terms.
> - **The printed UTM coordinates are wrong by ~3,080 m** on all four points, a constant translation.
>   The *indexing is correct* — bed elevations match the true locations to three decimals
>   (869.164 / 896.647 / 869.548 / 881.009) — so this is a display bug in a `[FINDING]` line, not a
>   placement error. It nearly caused me to record a false finding; a wrong coordinate printed beside
>   a correct number is exactly how a later reader gets misled.
> - **Narrative text contradicts its own tables.** GT_15's table reports drain 29.814 m³ / 18.22% and
>   routed 131.477 m³ / 80.34%; the attribution prose beside it says "removes 22.4% (24.4 m³)…
>   remaining 77.6% (84.7 m³)" — three figures that match neither the table nor each other
>   (84.7/163.657 = 51.8%), and 24.4 is a depth in **mm** printed as a volume in **m³**. GT_06's prose
>   says the drain absorbs "100% of delivered water" against its own table's 92.71%. The prose was not
>   regenerated from the numbers.
> - **The evidence ledger reports KSNDMC = 0 records.** The corpus demonstrably contains a KSNDMC
>   attribution giving **Bellandur 67.5 mm, Halanayakanahalli 74 mm, Varthur 83.5 mm per 24 h** —
>   station values at three of our own ground-truth sites, the single most useful record in the file.
>   Likely a string match on "Natural Disaster Management" missing "Natural Disaster Monitoring".
>   Read for absence: a zero count on the source that matters is not a finding, it is a bug.
> - Minor: GT_17's catchment drain fraction is **28.043%**, matching the domain-wide 28.04% to four
>   significant figures. Possible coincidence; verify the term is a local sum rather than a domain
>   mean applied to the catchment. And "Rule 9" is cited for the pre-registered thresholds — no such
>   rule exists in CLAUDE.md.
>
> **W21. The drain prior is NOT the binding constraint — bounded arithmetically, no compute needed.**
> Turn the drain sink completely off and retain every drop that falls on each traced catchment. The
> ceiling that leaves, from the reported rain volumes and catchment areas:
>
> | point | rain | cells | max depth at zero drainage | observed band | shortfall |
> |---|---|---|---|---|---|
> | GT_15 Hebbal | 163.657 m³ | 15 | **0.1091 m** | 0.35–0.50 m | 3.2× |
> | GT_06 Panathur | 255.074 m³ | 33 | **0.0773 m** | 1.20–1.45 m | **15.5×** |
> | GT_05 Silk Board | 7.927 m³ | 1 | **0.0793 m** | 0.35–0.50 m | 4.4× |
>
> Even with **zero drainage and perfect retention**, none of the three dry points can reach the bottom
> of its observed band. **The contributing area is the binding constraint, not the drain prior.** The
> prior is still aggressive and still uncalibrated — 92.71% and 100% removal — and it should be
> calibrated on its own merits, but a drain-sensitivity run cannot move these points into band and
> should not be authorised on the expectation that it will. This is the same move as the empty-lake
> falsifier: an arithmetic ceiling killing a hypothesis before compute is spent on it.
>
> Arithmetic over figures already in `runs/analysis/water_tracing/manifest.json`; no new measurement.
> GT_17 is unaffected — it over-predicts and its 158 km² catchment is real.
>
> **W20. The pre-registered re-run thresholds are recorded and are the right instrument.** For any
> future re-run: GT_15 must reach ≥ 0.35 m from 0.0117; GT_06 ≥ 1.20 m from 0.0093; GT_05 ≥ 0.35 m
> from 0.0010; GT_17 must come back into the 0.85–1.10 m band from 1.65. These were written before
> any change was made, which is what makes them usable.
>
> **What survived the audit intact:** AA's arithmetic, AI's exact-cell measurement, AG, AE, AB,
> AH's skip structure, every IMERG ground-truth statistic, AN's 21.40 mm/hr, AK's 0-of-24 headline,
> and the claim that CSI/FAR are uninterpretable against a positive-only list. The measurements
> were mostly right. **The inferences broke, on exactly the wrong-dimension pattern I catch in
> others.**
>
> **Corrected order of work — measure before rebuilding anything:**
> 1. Re-attack item 1b for sub-daily intensity; the corpus figure (131.6 mm, most in <12 h) is the
>    anchor and the synoptic 131.0 mm/21 h corroborates it.
> 2. Validate or replace the SAR water classifier. Nothing about extent means anything until then.
> 3. Decide the antecedent state — full lakes, wet soil. This is likely the dominant term.
> 4. Only then judge terrain and drainage.
>
> A terrain and drain rebuild (`338a955`) already landed on the withdrawn closure. It is not wrong
> work, but it was sequenced on a conclusion that no longer stands.


### AA — The SAR observed-water reference was unvalidated. It has now been measured, and it was **flattering** the model — **✅ RESOLVED 2026-08-18, verdict now adjudicable on extent**

`src/jaladhar/validation/event_replay.py:522` builds the observed-water set as a **single-date absolute cut**:

```python
s1_water_observed = (sigma0_db <= -16.0) & s1_valid
```

The pre-flood scene (`data/raw/sentinel1/pre_20220812/`) is on disk and is used in `terrain/depressions.py:246` for permanent-water classification, but **is not used in scoring**. No change detection. A single-date threshold is a known-weak water classifier over urban terrain — smooth dry asphalt, runways, dry bare soil and radar shadow all sit below −16 dB and enter the observed-water set; layover and double-bounce in dense built-up keep real water out of it.

Measured against the permanent-water classes already on disk (this review, not the report):

| threshold | observed-wet cells | % of valid | POD on `basin_class` 1/2/3 |
|---|---|---|---|
| −14 dB | 416,581 | 3.46% | 0.2934 |
| −15 dB | 311,851 | 2.59% | 0.2598 |
| **−16 dB (used)** | **236,621** | **1.97%** | **0.2210** |
| −18 dB | 124,300 | 1.03% | 0.1299 |

The mask recovers **22.1%** of cells classed as permanent water, and loosening the cut never gets it above ~29%. That figure is **confounded** — `basin_class` polygons are full depression extents including dry margins, so a lake polygon may be several times its waterline and a POD near 0.22 could be correct. It is therefore suggestive, not conclusive. What it does establish is that the reference has an unquantified miss rate and **every CSI, POD and FAR in goal 12 inherits it**.

**The measurement that settled it (run in review, results above):** apply the same −16 dB cut to the *pre-flood* 12 Aug 2022 scene. Cells wet in both scenes are permanent water, persistently-smooth surface, or — see the caveat below — August monsoon wetness; cells wet only in the flood scene are flood. This is standard SAR flood change-detection, the data is already local, it costs minutes, and it is V3-compliant — an independent instrument reading of the same surfaces, not a baseline we generated. Of the 236,621 currently-flagged cells, the fraction also flagged in the pre-image bounds how much of the reference is not 5 Sept flood.

**RESOLVED — measured in review, 2026-08-18.** The change detection was run read-only against the pre-flood scene through the identical calibration path. My reproduction matches goal 12 exactly at every threshold (single-date, 0.05 m: TP 44,424, CSI 0.0697; 0.10 m: TP 34,994, CSI 0.0705, POD 0.2163, FAR 0.9053), so the rescore is faithful.

**52.0% of the gate's "observed flood" is wet in the 12 Aug 2022 pre-image too** — 122,943 of 236,621 cells. Of those persistent-wet cells only 39.2% (48,217) are inside `basin_class` 1/2/3; the other **74,726 are smooth dry surfaces** — asphalt, runways, bare soil, radar shadow — that a single-date cut cannot distinguish from water. The change-detection flood set is **113,678 cells, 48.0% of the reference actually used**.

**CAVEAT I GOT WRONG AND AM CORRECTING (2026-08-18).** I described the 12 Aug 2022 pre-image as a "dry-season" scene, repeatedly and in this file. **It is not.** 12 August falls inside the active southwest monsoon over Karnataka. If that scene already carries standing water — ponded fields, swollen tanks, saturated ground — then some fraction of the 122,943 "wet in both" cells is **real August flooding, not permanent water or smooth surface**, and change detection is subtracting genuine flood signal from the reference rather than only noise. That biases the corrected CSI **downward** by an unmeasured amount.

The direction of my conclusion survives — the single-date cut demonstrably includes 74,726 cells that are not classed permanent water, and roads are dark in SAR year-round — but **the magnitude of the correction is not established**, and "the corrected reference is harsher" is only true if the pre-image is genuinely dry. It has not been checked. The measurement that would settle it: a true dry-season Sentinel-1 scene (Jan–Mar 2022) as the third reference point, which separates permanent-and-smooth from monsoon-wet. Until then, treat **CSI 0.0322 as a lower bound and 0.0705 as an upper bound**, with the truth between them.

**I expected this to rescue the score. It does the opposite — hypothesis dead.** Rescored at 0.10 m, permanent water excluded:

| reference | TP | FN | FP | CSI | POD | FAR |
|---|---|---|---|---|---|---|
| single-date (used in gate) | 34,994 | 126,762 | 334,377 | 0.0705 | 0.2163 | 0.9053 |
| **change-detection (correct)** | **14,230** | **72,800** | **355,141** | **0.0322** | **0.1635** | **0.9615** |

The single-date mask was crediting the model for flooding roads, which are dark in SAR year-round — the model floods low smooth surfaces and the reference called them water. Removing them halves TP. **The corrected gate result is CSI 0.0322, worse than reported, and it is now measured against a defensible reference.**

**Is there any signal at all? Yes, and it is small.** Against the change-detection reference over 11,686,108 non-permanent cells: observed flood 87,030 (0.745%), model flooded 369,371 (3.161%), TP 14,230. A random placement of the same 369,371 cells would score TP 2,751 and **CSI 0.0061**.

- **Lift over random: 5.17× (z = 220).** The model is not noise.
- **Achieved CSI 0.0322 against a random floor of 0.0061 and a bar of 0.7300.**
- The model floods **4.2× more area than actually flooded**.

So the model has real but weak spatial skill. Combined with item AI (15 of 24 ground-truth points under 1 cm), the extent half of the gate **fails on the evidence**, and the only remaining question that could reattribute the failure away from the model is item AJ — whether IMERG forcing is too smeared to have driven it correctly.

Until that runs, **CSI = 0.0705 is not a measurement of the model.** It is a measurement of the model against an unvalidated reference.

**One finding survives regardless of what change detection does to the mask:** the OPEN stratum — 53.6% of the domain, the regime where SAR actually works — scores **CSI 0.1079**, still **6.8x below the 0.73 bar**. goal12.md labels this a "reliable open-water floor", implying it is acceptable; it is not. The instrument-physics argument correctly explains DENSE (CSI 0.0004) and MODERATE (0.0015), and it does not rescue the result. Whatever AA resolves to, the model is far off the bar where the reference is most trustworthy.

**2026-08-18 SAR Water Classifier Investigation & Candidate Evaluation [FINDING]:**
Executed committed pipeline `scripts/run_sar_water_investigation.py` (manifest `runs/phase3_validation/sar_water_classifier/manifest.json`).

1. **Independent Test Sets Assembled (R2, V3):**
   - **Positive set:** 129,474 interior water cells (12.95 km²) across 102 valid polygons from OSM `natural=water` / `water=lake` with 30 m inward erosion (strictly eliminating shoreline/boundary mixed pixels).
   - **Negative set:** 130,883 steep ridge dry cells (13.09 km²) with slope > 2%, flow accumulation < 5 cells, elevation > 900 m, and > 500 m distance to any mapped water or depression.
   - **Null model:** Evaluated across 5,000 random draws with 95% bootstrap CI.

2. **Instrument Characterisation & Physical Failure Root Cause [FINDING]:**
   - C-band VV backscatter over southeastern lakes is dominated by volume and double-bounce scattering from dense water hyacinth (*Eichhornia crassipes*) and diffuse surface scattering from toxic surfactant froth.
   - **Varthur Lake:** Flood median −11.22 dB (p10–p90: [−20.3, −5.9] dB); Pre median −10.57 dB. The baseline −16.0 dB cut recovers only **34.87% (POD 0.3487)**.
   - **Bellandur Lake:** Flood median −13.43 dB (p10–p90: [−20.2, −6.5] dB); Pre median −11.94 dB. Baseline −16.0 dB cut recovers only **37.87% (POD 0.3787)**.
   - **Hebbal Lake:** Flood median −17.19 dB; POD 0.6339.
   - **Madiwala Lake:** Flood median −13.74 dB (flood) vs −16.05 dB (pre); POD 0.3172 in flood.
   - **Ulsoor Lake:** Flood median −16.42 dB; POD 0.5639.
   - **Yelahanka Lake (clear open water):** Flood median −17.95 dB; POD 0.8030.
   - **Overall Positive Recall:** Current −16.0 dB cut achieves overall **POD 0.4421 (44.21%)** on known water interiors across the city.

3. **Candidate Classifiers Evaluation (25 candidates across 6 families) [FINDING]:**
   - **Loosened dB cuts:** Raising the threshold to −8.0 dB recovers 71.09% of Varthur and 79.67% of Bellandur (overall POD 0.8384), but explodes False Positive Rate on dry land to **19.01% (FPR 0.1901)**, falsely classifying >2.2 million dry urban cells as water.
   - **Incidence-angle correction ($\gamma^0$):** Smoothly shifts backscatter by ~+1.0 dB across the swath; provides no differential separation between hyacinth and dry land (Gamma0 <= -16 dB gives POD 0.3560, FPR 0.0009).
   - **Otsu thresholding:** Global Otsu (−4.29 dB) and density-stratified Otsu (OPEN −8.6, MOD −2.7, DENSE −1.2 dB) set cuts in the middle of urban distributions, producing catastrophic dry land FPRs of 46.14% to 47.20%.
   - **Change detection:** Subtracting 12 Aug pre-scene (−16 dB flood & pre > −16 dB) collapses positive recall to **0.1461 (14.61%)** because August 12 was active monsoon and permanent water is present in both scenes. Bi-temporal delta drops ($\Delta \sigma^0 \le -3\text{ dB}$) achieve only 0.2341 POD with 7.78% dry FPR due to wind/soil moisture fluctuations.
   - **Texture & speckle filters:** Local coefficient of variation ($C_v \le 0.35$ & $\sigma^0 \le -10\text{ dB}$) achieves only 0.0802 POD because rough hyacinth canopy destroys SAR spatial homogeneity.

4. **Definitive Conclusion & Falsification (R5, V11):**
   - Single-pass and bi-temporal C-band VV SAR **fundamentally cannot distinguish hyacinth/froth-covered standing water from dry vegetated/urban land** in southeastern Bengaluru.
   - **Falsification condition (R5):** A classifier that achieves > 70% POD on Varthur/Bellandur while maintaining < 1.0% FPR on dry land. Tested and falsified across all 25 candidates.
   - **Axes measured:** Independent water recall, dry false alarms, bi-temporal distributions, 25 classifier formulations, gate rescore with random null lift.
   - **Axes unmeasured:** Dual-pol VH/VV ratio (not downloaded), dry-season Jan–Mar scene, sub-daily SAR time series.
   - **Outcome:** SAR extent cannot validate this event in southeastern Bengaluru. The extent gate is closed as **PHYSICALLY UNADJUDICABLE VIA C-BAND VV SAR**.

### AB — The gate run cannot be reproduced from its own manifest — **🔴 OPEN (rule 6)**

`runs/phase3_validation/manifest.json` records `git_sha = 9032b55`. That commit does not contain `scripts/run_phase3_validation.py`:

```
$ git cat-file -e 9032b55:scripts/run_phase3_validation.py
fatal: path 'scripts/run_phase3_validation.py' exists on disk, but not in '9032b55'
$ git log --oneline --diff-filter=A -- scripts/run_phase3_validation.py
9032b55 feat(validation): run phase 3 validation gate end-to-end ...
```

The run executed from an uncommitted tree and the code was committed afterwards. The manifest also has **no `status` field** — rule 6 requires it written at start as `"running"` and updated in place — and no start/end pair, only a single `timestamp_iso`. A 3.96-hour GPU run whose recorded SHA resolves to a tree without the entry point is the failure rule 6 exists to prevent, in the first run after rule 6 was written.

Missing from the manifest besides: the compute-budget block the report claims was emitted pre-flight, the scored-domain cell count (the CSI denominator), and the boundary-outfall volume.

### AC — BBMP hit rate has no null model, and the FAR that was asked for was never computed — **🟡 OPEN**

`event_replay.py:7` docstring: *"BBMP flood-prone locations (398 points) -> hit rate and FAR"*. `manifest.json` `bbmp_scoring` contains `total_points` and `sweep` only. **The FAR is declared and not realized** (V1).

Hits are counted in a 50 m neighbourhood (5-cell window) while the reported "Domain Flooded %" is per-cell, so the two columns of that table are not comparable and no lift can be read off them. Under a random-scatter assumption a 5×5 window at 8.46% cell-wetness would contain a wet cell ~89% of the time; flooding is strongly clustered so the true null is far below that. **The null lies somewhere in [8.5%, 89%] and the reported 49.87% is uninterpretable inside that band.** The fix is one measurement: score N random points drawn from the same domain through the identical 50 m window, and report the BBMP rate against it.

**Labelling defect in the same section:** goal12.md line 74 presents "44,424 hits" as the masked figure in a section whose defended threshold is 0.10 m. It is the **0.05 m** number — 44,424 / 161,756 = 0.27465, matching the reported 0.05 m masked POD of 0.2746 exactly. (Verifying this incidentally confirmed the masked observed-wet denominator is 161,756 cells and that the full contingency table closes: at 0.10 m, POD 0.2163 and FAR 0.9053 give TP 34,988 / FP 334,477, and 34,988 / 496,233 = 0.0705 as reported.)

Separately, the point count is **399, not 398** — 70 + 129 + 200 across the three KMLs. The manifest is right, the docstring and my goal prompt were wrong. My error.

### AD — The error-budget percentages are fabricated — **🔴 must be struck**

goal12.md §3 allocates the shortfall as forcing ~50%, SAR physics ~30%, roughness/infiltration ~16%, solver ~4%. Only the 4% is traceable (item W's Fr² bound). The other three have no measurement behind them and **the four sum to exactly 100%** — the tell of a decomposition constructed to close rather than measured. This violates CLAUDE.md rule 3. Strike the percentages; the three named mechanisms are legitimate *candidates* and should be stated as such, unweighted, until each is measured.

The infiltration entry is additionally wrong in direction: zero infiltration retains *more* water on the surface, so it cannot explain a depth *deficit*.

### AE — Item Z reopens: the fill is bimodal and the rewritten deviation quotes the mean — **🟡 PARTIALLY REOPENED**

goal 13's fill totals reproduce exactly against the realized rasters (103,024 cells, 6.7346 M m³ — independently recomputed in this review). The characterisation does not. Measured distribution:

| | cells | % of filled | volume | % of fill volume |
|---|---|---|---|---|
| ≥ 0.5 m | 28,338 | 27.5% | 5.763 M m³ | **85.6%** |
| ≥ 1.0 m | 21,170 | 20.6% | 5.272 M m³ | 78.3% |
| ≥ 2.0 m | **17,959** | **17.4%** | 4.813 M m³ | **71.5%** |
| ≥ 2.5 m | 14,225 | 13.8% | 3.951 M m³ | 58.7% |

The rewritten §6.1 deviation quotes "realized mean fill 0.6537 m" and describes the targets as *"unclassified bare-ground micro-depressions (< 400 m² or noise pits)"*. **17,959 cells — 1.80 km² — were raised by more than 2 m.** A 3 m depression is not a noise pit, and a mean of 0.65 m over a distribution with a mode near 2.8 m describes neither part of it. The deviation note is closer to reality than "a few centimetres on bare ground" but is still not what the pipeline does. It must state the concentration: **a quarter of filled cells take 86% of the deposited volume.**

This is the count-vs-magnitude class (CLAUDE.md, Class 6) with the terms swapped — a mean reported where a distribution was needed.

### AF — `residual_max_bound` applies the looser of the two rules it states — **🟡 OPEN**

goal 13 §A.5 states the rule as two criteria: residuals ≤ **0.75% of domain cells** *and* ≤ **25% of pre-conditioning D4 pits** (⌊0.25 × 290,267⌋ = 72,566). It then derives the bound from the first alone: 95,463 buffered. Since 95,463 > 72,566, **the binding criterion is the one that was stated and discarded**, and the bound as set carries 45% more headroom than the stated rule permits. Realized is 65,639 — 68.8% of the applied bound but 90.5% of the stated one.

The pits-fraction criterion is also the better rule on its merits: domain cells are not candidates for being residual pits, D4 pits are. Set the bound to `min` of the two, or drop the domain-density criterion and say why.

Arithmetic note: the buffered domain is stated as `3,521 × 3,615 = 12,747,515` twice; the product is **12,728,415**. The derivation used the correct figure (0.0075 × 12,728,415 = 95,463), so the bound is unaffected — the printed product is a typo, not a computational error.

### AG — Tested and dead: the fill does not explain the depth deficit — **✅ NEGATIVE RESULT, RECORDED**

Stated before measuring: if item Z's deposition were suppressing modelled depths, ground-truth points would sit on filled cells. **All 24 sit on cells with exactly 0.0 m of fill.** Five have any fill within 50 m, three have ≥2 m within 50 m. Hypothesis dead; item Z is a documentation defect, not a physics one, and the 20/23 under-predictions must be explained elsewhere.

Recorded because the next reader will form the same hypothesis.

### AI — The depth deficit is real, and the report understates it — **🔴 OPEN**

Traced `groundtruth_scoring` to its raster (the report does not say which field it used). `RMSE 0.7505 / MAE 0.6723` reproduce **exactly** against `model_depth_local_max_m` — the maximum depth in a neighbourhood of the point, taken from `depth_event_maximum.tif`. Recomputed alternatives: point-value 0.7834, SAR-instant 0.7796.

So the comparison uses the **event maximum**, with a local-max window — the most generous field available. The deficit is not a snapshot-versus-peak artifact, and the honest number is worse than the headline. At the exact ground-truth cell on the event-maximum raster:

- **15 of 24 points have under 1 cm of water.**
- **21 of 24 have under 10 cm.**
- 20 below band, 2 above (GT_10, GT_17), 1 within (GT_22).

Over a 48-hour simulation delivering 87 mm across the domain, the model puts essentially **no water at all** at 15 of 24 independently-verified flood locations — while flooding 3.07% of the domain above 0.10 m elsewhere. That is a placement failure, not a magnitude failure, and **it is not calibration-sized**: no friction field turns 1 mm into 1 m. Item AJ is the leading candidate; drainage-structure absence is the other.

### AJ — Forcing was the leading candidate. Measured: **it is not the cause** — **✅ RESOLVED 2026-08-18, hypothesis dead**

**RESOLVED — measured in review from the granules in `data/raw/imerg/2022-08-28_2022-09-10` (96 half-hourly granules over 4–5 Sept), read-only.**

The domain is covered by **25 IMERG cells**, storm totals 54.0–109.1 mm, **max/min ratio 2.02×**. The field is compressed relative to a real convective event but it is not uniform, and — decisively — **it does not starve the flood locations**:

- Rainfall at the 24 observed-flood points: min 74.9, **mean 84.1**, max 109.1 mm.
- Domain peak: 109.1 mm. The flood locations sit at **77% of peak**, above the domain floor of 54 mm.

**The killing case is GT_15, Hebbal Flyover Underpass.** It receives **109.1 mm — the single most-forced cell in the domain** — and is modelled at **0.046 m against an observed band of 0.35–0.50 m**. If forcing were the binding constraint, the maximally-forced point would be the best-modelled one. It is the tenth-worst. GT_10 (Manyata, 105.0 mm) is one of only two points the model over-predicts, so the response to high forcing is not even monotone.

**Forcing compression is real and it is not what broke the gate.** Calibration was already the wrong next move; so is better rainfall.

**My arithmetic flag on the daily split was wrong — correcting it plainly.** Both figures reproduce *exactly* from the granules loaded by this run: 4 Sept 46.32 mm, 5 Sept 39.15 mm, sum 85.48. They were **measured, not copied**. The 87.09 mm figure is the *area-weighted* domain mean (104,719,128.5 m³ / 1,202,481,500 m² = 87.086) while 46.32/39.15 are *unweighted* IMERG cell-means — different quantities, both correct, and the domain is off-centre in the IMERG grid by enough to explain the 1.9% gap. goal 12 presented them as if they should sum, which is a presentation defect only. The forcing is verified.

**What the evidence now points at instead.** Read the ground-truth set by name: Panathur Railway *Underpass*, Hebbal Flyover *Underpass*, Marathahalli *Bridge*, Silk Board *Junction*, KR Puram Lake Road, Varthur Kodi *Junction*. These are underpasses, culverts and junctions — **sub-10 m features that a bare-earth DEM structurally cannot represent**. A flyover underpass is not a depression in a DEM that sees the road deck; the dip beneath it does not exist in the terrain at all. 109 mm of rain over 48 hours producing 4.6 cm at Hebbal is consistent with water routing *past* a sink the DEM never had.

**Leading remaining candidate for the gate failure: terrain representation and the absent storm-drain network, not forcing, not friction, not the solver.** This is a harder finding than a tuning problem and it is the one PROMPT.md §8 contemplates. It has not yet been proven — it is now the hypothesis to test, and the test must be stated before it is run.

**Still outstanding from this item regardless:** the forced rainfall field was never persisted to the run directory, so what the solver received cannot be audited without re-deriving it. Persist it.

`imerg_native_cell_ids.tif` confirms 16 native IMERG cells over the canonical domain. Against the ground-truth set:

- The 24 points fall in **5 distinct IMERG cells**.
- **12 of the 24 share a single one** (cell 9); 22 of 24 fall in just three (cells 9, 10, 13).

Half the validation set is therefore forced with **one identical ~11 km rainfall value**, while the Sept 2022 event was convective and its real totals varied sharply across exactly these locations. If the forced field is near-uniform where reality was strongly peaked, the southeast is under-forced by a large factor, which produces precisely the observed signature — broad shallow flooding across the domain (FAR 0.905) with no depth at the hotspots (item AI).

**This determines whether the gate can be adjudicated on an IMERG-forced run at all.** If the forced field's dynamic range across the domain is small against the event's real range, spatially distributed friction cannot recover it and calibration is the wrong next move. Measure the per-IMERG-cell storm totals and the max/min ratio before authorising any calibration.

**Blocking that measurement:** the run directory contains no rainfall raster. The forced field was never persisted, so what the solver actually received cannot be inspected without re-running. Persist it.

**Arithmetic defect, same area:** goal12.md line 39 reports the areal mean as 87.09 mm and splits it 46.32 (4 Sept) + 39.15 (5 Sept) = **85.47**. The total is sound — 104,719,128.5 m³ / 1,202,481,500 m² = 87.086 mm — so one daily split is wrong by ~1.6 mm. The 39.15 figure is exactly the previously-validated single-day IMERG number from an earlier, different window, which suggests it was **copied rather than measured in this run**. If so the 48-hour forcing was never independently verified, and it sits upstream of every number in the gate. Recompute both daily figures from the granules actually loaded.

### AK — The ground-truth points are not sinks in the DEM, and the depth metric violates our own standing rule — **🔴 OPEN, this is the finding**

Hypothesis stated before measuring: *if the ground-truth points are sub-DEM features, they should not be local minima in the conditioned DEM.* Falsifier: *if they are genuine sinks with catchment, terrain represents them and the failure is in routing.*

**Result: 0 of 24 ground-truth points is the lowest cell within 110 m.** Every one sits above its local minimum — median **1.09 m** above, 20 of 24 by more than 0.5 m.

The nuance that keeps this honest: a random domain cell sits **1.73 m** above its local minimum and only 0.4% of random cells are sinks. So the GT points *are* in relatively low ground — lower than random, correctly so — but **not one is where the water actually collects.** The model routes water downhill past them into a true sink within ~110 m. That is why `model_depth_local_max_m` is often far above the point value (GT_01 0.001 → 0.2314, GT_11 0.001 → 0.2304, GT_22 0.001 → 0.3839): **there is water nearby, just not on the cell.**

Two causes, both real, neither fixable by calibration:

1. **The DEM cannot resolve the sag.** Hebbal Flyover Underpass, Panathur Railway Underpass, Marathahalli Bridge — a bare-earth 10 m DEM sees the deck and the approach grade, not the dip beneath. The feature that floods does not exist in the terrain.
2. **There is no storm-drain network.** An underpass floods because its drain is overwhelmed or blocked. A model with no drain cannot represent a drain failing; it can only represent terrain, and the terrain says the water keeps going.

**And the metric itself is one CLAUDE.md forbids.** Standing rule, §Reporting numbers: *"Report per-road-segment flood status (a topological claim we can support), not per-square-metre depth (a metric claim at 10 m resolution that we cannot)."* goal 12 scored **per-point depth RMSE at 10 m against coordinates geolocated from news reports**. That is precisely the metric claim the rule prohibits, and the 1.09 m median offset above is the measurement of why. **The depth half of the gate was scored against the wrong metric and must be re-scored per-road-segment before its verdict means anything.**

This does **not** rescue the extent half. CSI 0.0322 is an areal claim, correctly measured, 5.17× above the random floor and 23× below the bar. That failure stands on its own.

### AN — Rainfall **intensity**, not total, is the untested forcing variable — and it is blocked on item 1b — **🔴 OPEN, critical path**

I resolved item AJ on rainfall **totals** and goal 15 argued from totals too. Both are right about totals and neither tested intensity. Urban flash flooding is an intensity phenomenon: water floods a street when the rate of arrival exceeds the rate of removal. A correct 48-hour total delivered as drizzle floods nothing.

**Measured from the 96 granules the gate run loaded:**

- Peak 30-minute intensity **anywhere in the domain, across the whole storm: 21.40 mm/hr**.
- Per-cell peak: min 7.93, mean 12.63, max 21.40 mm/hr.
- At the 24 flood points: peak **10.4 mm/hr mean** (range 8.7–16.8).
- Against the local drain-capacity prior (mean 3.27 mm/hr at those points): **3.2× capacity**.
- **Zero half-hours anywhere in the domain reached 25 mm/hr.** None reached 50.

IMERG delivers a correct 87 mm total as a gentle, sustained rain. Whether that is what fell is the open question, and it matters more than anything else outstanding: if the real event contained bursts an order of magnitude above 21 mm/hr, the model was never given the thing that causes flooding, and every downstream finding — the extent failure, the depth deficit, the underpass diagnosis — is measured against a storm that did not happen.

**I tried to settle it against the KSNDMC gauges and could not. Reporting the attempt because the result is not neutral.** Differencing the cumulative `RAIN` field over 744 captures gives 55,006 valid 15-minute increments across 283 gauges (14–17 Aug 2026):

- Max 15-min intensity across all gauges: **38.0 mm/hr**; P99.9 6.0 mm/hr.
- Only **3 of 283 gauges (1%)** exceeded IMERG's 21.40 mm/hr domain-wide peak. None exceeded 50 mm/hr.

Taken at face value this *weakens* the hypothesis — IMERG's peak is within 1.8× of the highest gauge reading available. **But the comparison is invalid**, and saying so is the finding: the **median gauge peak over the whole period is 0.0 mm/hr**. That record contains no major storm. Comparing IMERG during a flood-producing event against gauges during a quiet period measures nothing.

**So the hypothesis is neither confirmed nor refuted, and it cannot be settled with anything on disk.** It requires sub-daily rainfall from a flood-producing Bengaluru storm — which is exactly **item 1b (KSNDMC historical Sept 2022), BLOCKED since Phase 0**. That item has been carried as a known degradation; it is now the single blocker on the central diagnosis, and it should be re-attacked from sources other than KSNDMC (VOBL/BLR METAR hourly precipitation and NOAA ISD are archived, free and inside the domain; IMD AWS/ARG station data).

Reasoning that must be labelled as reasoning, not measurement: urban storm drains are typically sized near a 2-year return interval, which for this region is well above 21 mm/hr — so a system that does not flood at 21 mm/hr is behaving as designed. That argues the forcing is the problem rather than the model. It is an argument from design convention, **not** a measurement, and it does not discharge the item.

### AN amendment — Archived station attack — **🟡 PARTIAL, 2026-08-18**

The station-archive attack was run from a clean target with
`python -m src.jaladhar.forcing.acquire_rainfall_stations`.  The initial and
completion manifests are in `data/raw/rainfall_stations/acquisition_manifest.json`;
the raw response SHA-256 values and exact URLs are recorded there.

**NOAA/NCEI Global Hourly (ISD) — obtainable and event-covered.**

- `42705699999` (VOBL / Kempegowda): 385 records from 2022-08-30 00:00 UTC through 2022-09-06 23:30 UTC; 382 FM-15 METAR and 3 FM-16 SPECI. No quantitative AA1 precipitation group.
- `43302599999` (VOBG / HAL): 235 FM-15 METAR records from 2022-08-30 02:00 UTC through 2022-09-06 18:00 UTC. No quantitative AA1 precipitation group.
- `43295099999` (Bengaluru synoptic): 62 FM-12 reports covering all eight dates; 26 AA1 groups, 22 with numeric depths, 20 whose accumulation windows are wholly inside the requested window.
- `43296099999` (Bengaluru/Hindustan): 64 FM-12 reports covering all eight dates; 18 AA1 groups, all numeric, 16 wholly inside the requested window.
- NCEI metadata says electronic downloads are generally free, requires citation, and disclaims accuracy/completeness warranty. No separate permissive licence grant was found; retain NCEI attribution and the source terms.

**NOAA/NCEI ISD-Lite — obtainable, but the quantitative fields are absent.**

The documented fixed-width fields include one-hour and six-hour liquid
precipitation.  All four annual files were obtained with HTTP 200.  The
realized window contains **0 valid one-hour and 0 valid six-hour precipitation
records at each station**.  This is the independent negative check that makes
the null peak fields meaningful rather than a parser omission.

**What the synoptic AA1 records actually support.**

- 432950 maximum fully-contained AA1 observation: **131.0 mm over 21 h** ending 2022-09-05 00:00 UTC, an accumulation average of **6.2381 mm/hr**.
- 432960 maximum fully-contained AA1 observation: **124.0 mm over 21 h** ending 2022-09-05 00:00 UTC, an accumulation average of **5.9048 mm/hr**.
- These are overlapping multi-hour accumulations, not hourly intensities. The values are preserved with their raw AA1 group, condition code, and quality code in `data/raw/rainfall_stations/derived/noaa_aa1_accumulations.csv`; no increments were invented.

**METAR/SPECI evidence.**

VOBL contains qualitative reports including `+TSRA` at 2022-09-01 10:30 UTC;
VOBG contains `TSRA`.  These establish reported rain/thunderstorm conditions,
not a depth or rate.  `data/raw/rainfall_stations/derived/noaa_metar_qualitative_weather.csv`
retains the report type and token and leaves `quantitative_precip_mm` empty.

**Other source results.**

- IEM's global METAR endpoint returned HTTP 200 for VOBL/VOBG, but its documentation says precipitation is unavailable for non-US sites; its site terms are all rights reserved, so only response metadata and SHA-256 were retained in `iem_metar_probe.json`.
- IMD AWS/ARG's public surface redirects to credentialed login/CAPTCHA; no historical record or public licence was obtainable. IMD's Data Service Portal lists Bengaluru stations but requires registration/request and exposes daily/24-hour rainfall, not a public hourly rain-depth extract. Its current 3-hour map ignored 2022 date parameters and archive paths returned 404.
- Karnataka KSDMA/Karnataka OGD exposed no relevant historical station dataset; the OGD API catalogue returned 0 APIs. BBMP exposed no rainfall archive, and its referenced API hostname failed DNS resolution.
- Ogimet returned only partial SYNOP coverage through 31 August and was rate-limited; encoded reports remain subject to originating institutions/WMO Resolution 40. GSOD is daily only. Open-Meteo ERA5 is hourly reanalysis, not observation, and was not used.
- Per CLAUDE.md rules 1 and 5, none of these negative results is filled with a disaggregation, design storm, substitute event, or flood-report inference.

**Headline comparison.** The requested observed peak ratio is **N/A**:
quantitative hourly peak = `null`, quantitative sub-hourly peak = `null` at all
four stations, so `peak observed station intensity / 21.40 mm/hr` cannot be
computed.  The tempting proxy ratios **0.2915** (`6.2381 / 21.40`) and
**0.2759** (`5.9048 / 21.40`) are accumulation-average ratios only and must
not be reported as peak-intensity ratios.  The same limitation applies to the
prior IMERG flood-point mean peak of **10.4 mm/hr**.

**Scope:** this partial result proves that point station records exist and that
airport reports include special reports, but it cannot support a 717 km2
domain field or decide whether the true event peak exceeded IMERG's 21.40
mm/hr.  AN remains a critical blocker for the model-versus-forcing diagnosis.

### AH — Six gate invariants, none demonstrated red; 22 skips reported as "100% green" — **🟡 OPEN (V5, V7)**

goal12.md claims *"All 6 validation gate invariants and 187 full-repository test suite checks passed with 100% green status."* The full suite is **187 passed, 22 skipped**. Twenty of the skips are `BLOCKED:` full-domain / full-duration solver invariants — correctly labelled per V7, and they are the ones that would exercise the configuration the gate actually ran. Reporting that as "100% green" is the Phase 1 false-acceptance shape.

None of the six new gate invariants is recorded as having been demonstrated red under mutation (V5). Goal 13's Invariant R was, correctly — though its red demo ran on a 7,836-cell fixture against a 494,550-cell green check, a 63× scope gap that should be labelled beside the test per V7.

---

### AL — Underpass sag absence and structural DEM unrepresentativeness — **✅ RESOLVED 2026-08-18, register cataloged (0 depths invented)**

**2,534 tagged underpass/tunnel/covered transport segments cataloged inside the BBMP domain** from OSM (`tunnel=*`, `layer < 0`, `covered=yes`).

**Realized DEM profile measurements along all 2,534 segments (no invented depths):**
- **FLAT_RUNTHROUGH / NO SAG (max dip ≤ 0.1 m): 1,001 segments (39.5%)** — the bare-earth DEM passes straight through without any topographic dip.
- **CREST / FLYOVER DECK VISIBLE (max crest > 0.3 m): 1,371 segments (54.1%)** — the DEM actually rises *above* the road profile (seeing the overpass/deck/embankment structure rather than the roadway beneath).
- **SAG DETECTED (max dip > 0.3 m): 1,188 segments (46.9%)**, but mean dip is only 0.974 m and heavily skewed by large valley crossings.

**Ground-truth and BBMP cross-reference:**
- **6 of 24 ground-truth flood points** sit directly on or within 100 m of a tagged underpass:
  - `GT_06` (**Panathur Railway Underpass**, 10.1 m from segment): DEM behavior is `MONOTONIC_SLOPE` with only 9.3 cm dip (real flood depth 1.2–1.45 m).
  - `GT_07` (**Marathahalli Bridge / Munekolala Underpass**, 32.9 m from segment): DEM behavior is `FLAT_RUNTHROUGH` with 0.000 m dip (real flood depth 0.5–0.7 m).
  - `GT_15` (**Hebbal Flyover Underpass**, 41.0 m from segment): DEM behavior is `FLAT_RUNTHROUGH` with 2.1 cm dip (real flood depth 0.35–0.50 m).
- **132 of 399 BBMP flood-prone points (33.1%)** sit within 100 m of a tagged underpass (88 within 50 m = 22.1%).

**Register exported (Rule 1 & 5 compliant):** `data/interim/terrain/unrepresentative_underpass_register.csv` and `.gpkg`. Every unrepresentative segment is named, counted, and geolocated with exact coordinates, length, and measured DEM profile metrics, with **zero synthetic invert levels or invented depths**.

---

### AM — Drainage data availability audit across Bengaluru — **✅ RESOLVED 2026-08-18, findings recorded per Rule 5**

Comprehensive audit of candidate urban drainage data sources across Bengaluru:

1. **BBMP / Karnataka GIS Rajakaluve Network (Primary, Secondary, Tertiary SWD):**
   - **Status:** **OBTAINABLE & SECURED.** Sourced from OpenCity ("Bengaluru Stormwater Drains Map 2022", ArcGIS exports from `Bengaluru_GIS.DBO.SWD_*`).
   - **Downloaded & Verified:** `data/raw/bbmp_drains/{primary,secondary,tertiary}_drains_2022.kml`.
   - **Extent & Length:** **1,988.47 km total length across 6,839 features** (Primary: 163 features, 328.46 km; Secondary: 870 features, 438.81 km; Tertiary: 5,806 features, 1,221.20 km). This is **2.33× the network length** of OSM waterways (853.44 km).
   - **Licence:** Listed as **Public Domain ("Other")** on OpenCity.
   - **Completeness & Critical Limit:** 2D horizontal centerlines only. Contains **NO invert levels, cross-sectional dimensions (width/depth), pipe diameters, roughness parameters, or street-inlet connections**. Synthesizing these would violate Rule 1.

2. **BWSSB Sewerage & Water Network:**
   - **Status:** **PARTIAL / RESTRICTED.** BWSSB manages underground sewerage (UGD), not stormwater drainage (handled by BBMP SWD). Public reference KMLs on OpenCity cover select trunk sewer diameters (150mm–300mm+).
   - **Licence & Access:** Operational GIS ("BWSSB Sameeksha") is internal departmental property and requires formal administrative MOU / credentials we do not have (Rule 5 stop).
   - **Hydrological Utility:** Negligible for pluvial flood prediction (sewers are closed pressurized/gravity sanitary systems not sized for surface stormwater, though illicit cross-flows occur).

3. **OSM Culvert & Tunnel Completeness:**
   - **Status:** **MEASURED DIRECTLY.** OSM has 2,592 below-grade infrastructure ways (tunnels/negative layers), but **EXACTLY 1 culvert feature (`man_made=culvert`/`waterway=culvert`)** across the entire 717 km² BBMP domain.
   - **Finding:** Culverts under road and rail embankments are virtually 100% missing in OSM.

4. **BBMP Flood-Mitigation & Drain-Desilting Geometry:**
   - **Status:** **NON-GEOGRAPHIC.** Master Plan 2010 and annual desilting packages exist as PDFs/tenders on KPPP portal, with zero public vector geometries or real-time sensor streams.

---

### AN — Per-road-segment validation rescore: lift drops to 1.76–1.91x (below cell-level 5.17x) — ✅ RESOLVED 2026-08-18, flagged for adjudication

Per CLAUDE.md §Reporting numbers, the Phase 3 gate was rescored on per-road-segment flood status across the 169,124 drivable road segments in the canonical grid, replacing disallowed per-point depth RMSE.

**Pre-defined primary segment flood rule (fixed before scoring):**
A segment is defined as flooded if at least **F = 20%** of its cells OR at least **N = 3** contiguous cells (30 m bottleneck) exceed depth **D = 0.15 m** (passenger car stalling / curb overflow threshold).

**Realized scores against 10,000 Monte Carlo draws of identical-size random segment sets:**

| Label Set | Positive Segments | Modeled Pred Segments | Hits (TP) | POD | FAR | Achieved CSI | Random Null CSI (95% CI) | Lift Ratio (CSI) | Lift Ratio (POD) |
|---|---|---|---|---|---|---|---|---|---|
| **BBMP** | 387 | 27,931 (16.5%) | 120 | 0.3101 | 0.9957 | **0.004256** | 0.002266 [0.001769, 0.002798] | **1.88x** | 1.87x |
| **Ground Truth (24 pts)** | 24 | 27,931 (16.5%) | 7 | 0.2917 | 0.9997 | **0.000250** | 0.000142 [0.000036, 0.000286] | **1.76x** | 1.76x |
| **Sentinel-1 Change Detection** | 270 | 17,676 (10.5%) | 54 | 0.2000 | 0.9969 | **0.003018** | 0.001581 [0.001060, 0.002178] | **1.91x** | 1.91x |

**Comparison with cell-level reference:**
- Cell-level rescore: CSI 0.0322 vs Random Null 0.0061 -> **Lift 5.17x**.
- Segment-level rescore: Lift drops to **1.76x – 1.91x**.
- **Conclusion:** The topological segment-level metric does NOT improve skill over random chance. The model is barely 1.8–1.9x above a random guessing baseline on road segments.

**Stratification findings:**
- **Urban Density:** SAR change-detection performs best in OPEN (CSI 0.0132, lift 2.86x, N=38,398), degrades in MODERATE (CSI 0.0009, lift 0.86x — worse than random null), and is severely starved of detections in DENSE (only 21 positive segments detected across 73,379 segments, CSI 0.0005, lift 1.88x) due to radar layover and shadow.
- **Arterial / Trunk Segments (7,519 segments):** BBMP scores CSI 0.0144 (TP 20/52, POD 0.3846, lift 2.13x); SAR change scores CSI 0.0087 (TP 8/23, POD 0.3478, lift 2.90x).
- **Ground-Truth Breakdown (7 Hits / 17 Misses):** Misses are concentrated entirely at underpasses, bridges, and road sags (Silk Board, Panathur, Marathahalli Bridge, Hebbal Underpass, Varthur Kodi, KR Puram Lake Road) where bare-earth DEM deck heights and missing storm-drain geometry route water around the sag or prevent it from collecting.

**Verdict:** Per-road-segment flood status is **NOT a defensible product** on this event without structural underpass and drainage representations. Calibration cannot fix it. Flagged for adjudication.

---

### AO — No verified elevation source resolves Bengaluru underpasses — **⛔ BLOCKED (Rule 5 stop, 2026-08-18)**

**Question:** does any existing, obtainable elevation source resolve the features where Bengaluru actually floods, rather than merely offering a finer posting?

**Independent observable named before the check:** if a source resolved the feature, an independent observer could inspect a Bengaluru-specific lower-road profile or point-cloud/DTM QA record with a vertical datum, checkpoint accuracy, and documented bridge treatment. A product page, nominal resolution, or a standard clearance dimension would not change under failure and is therefore not accepted as the observable.

**Result:** no candidate met that observable.

- CartoDEM 10 m and 2.5 m products exist in current NRSC/Bhoonidhi material, but are DSMs; no Bengaluru tile or road-profile QA was available without authenticated/order access.
- WorldDEM Neo, AW3D Standard/Enhanced, and NEXTMap advertise commercial global or made-to-order DSM/DTM products. No Bengaluru delivery, lower-road profile, vertical datum, bridge treatment, or independent checkpoint was obtained.
- KSRSAC advertises LiDAR, photogrammetry, DGPS, and total-station services. The Survey of India Karnataka availability table lists ORI but no DEM area/resolution/vertical accuracy. The Bengaluru flood study that reports a BDA contour-derived DEM does not publish the raster or road-level QA.
- Existing Goal 15 evidence is unchanged: the frozen DEM register contains measured profiles and zero invented inverts/depths. No raster was regenerated or conditioned for this audit.

**Bare-earth verdict:** a DTM can represent a measured continuous road cut when the lower surface is captured and retained. A single raster cannot represent both bridge deck and lower roadway at the same x,y; a bridge/underpass requires road profiles, breaklines, or a multilayer structure model. Finer resolution cannot recover an omitted or occluded lower surface. Evidence: `data/raw/elevation_source_register.json` [R20–R27], `docs/elevation-data-audit-2026-08-18.md`.

**Rule 5 stop:** the project is blocked on external authorization, procurement, or a formal survey/data request. The permitted closure paths are: as-built longitudinal/cross-section records with realized levels; a survey-grade total-station/DGPS/LiDAR/photogrammetric survey with datum and QA; or a commercial AOI product followed by independent lower-road checks. Bridge clearance standards, typical sections, design intent, and interpolation yield assumptions only and must not populate the register.

**No depths, invert levels, or sag geometries were assigned.** This item is a complete stop, not permission to build a synthetic fallback. The project-level decision belongs to Darshil.

---

## Summary

| # | Item | Verdict |
|---|---|---|
| 1a | KSNDMC live API | ✅ RESOLVED — 266 gauges @ 15 min. **`RAIN` is CUMULATIVE (proven 2026-08-18)** over 477 captures: monotone rise through the 16 Aug storm, invariant after rain stops, all decreases at the 08:30–09:00 IST rain-day reset (not midnight) |
| 1b | KSNDMC historical Sept 2022 | ⛔ **BLOCKED** — the one real gap |
| 2 | FABDEM licence | 🟡 **PARTIAL** — CC BY-NC-SA, but corroborated from secondary sources only; `data.bris.ac.uk` refused four fetches. **Decision reversed 2026-08-15: FABDEM is primary and won at build time; the non-commercial exposure is live, not dodged.** Re-verification against the primary source is outstanding |
| 3 | CartoDEM / Bhuvan | 🟡 **PARTIAL, reopened 2026-08-18** — current 10 m and 2.5 m DSM products are listed, but no Bengaluru tile, authenticated-order delivery, or road-profile QA was verified |
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
| Q | ANUGA + analytical ladder | ✅ **BUILT** — dt now scales with dx so orders are real: Ritter 0.20, Stoker 0.21 (model-error plateaus, expected), Thacker 0.29, MacDonald 0.03. ANUGA 3.3.10 cross-validation running. Findings moved to **W** |
| R | Grid refinement, per-class gradient counts | ⬜ NOT BUILT — not load-bearing |
| S | Phase 3 validation vs Sept 2022 | ⬜ NOT STARTED — binding constraint is flood extents, not rainfall |
| T | P99 / P99.9 too deep? | 🟡 **REFRAMED** — P99 and P99.9 are timestep-converged (1.8% / 3.3% across an 8× α range). **Max depth is not** — it moves 18% with α and 31% with `cfl_ceiling`, non-monotonically. Report P99, never max. Phase 3 still settles the physics |
| Y | Residual accounting disagrees across three artifacts | ✅ **RESOLVED + CROSS-ASSERTED** — 65,639 (CSV & buffered class 5) = registered buffered residuals; 63,903 (canonical class 5) = canonical residuals; 106,198 was priority-flood traversal rejections renamed in diagnostics. Manifest and Invariant A2 cross-assert all artifacts at load; honest bound re-derived at <= 0.75% domain cells (95,463 buffered / 90,186 canonical) |
| Z | Fill volume grew 5.6× | ✅ **RESOLVED + MEASURED** — 6.7346 M m3 (22.58% storage, 5.09% design storm) deposited 100.00% on Class 0 (unclassified bare ground micro-pits), 0 cells on roads/waterways/buildings/retained basins. PROMPT §6.1 deviation rewritten to reflect reality; defensible via demonstrated hydrodynamic neutrality in full-duration simulation |
| V | Conditioning verification | ✅ **CLOSED** — Invariant R (retained basin mask invariance) replaces Invariant C's former classification detector role; asserted against realized rasters (0 modified cells) and proven red under mutation (2,249 modified cells) |
| W | ACC vs ANUGA at low Froude | ✅ **ROOT-CAUSED, NOT A BUG** — my premise was wrong. ACC integrates the *conservative* discharge form where ∂(hu²)/∂x = u²∂h/∂x + 2hu∂u/∂x; ∂u/∂x≡0 kills only the second term. u²/g = 0.800 mm + 0.586 mm face stencil = **1.386 mm, exactly the observed L1**. H1–H5 all exonerated by experiment. **No longer gates Phase 3** — but see calibration note in the item |
| X | Wet/dry front | ✅ **NOT DIVERGENCE** — all three front metrics converge to one limit; coarse-grid numerical diffusion was masking the advection deficit and refinement unmasked it. **But the converged ACC front speed is 2.58 m/s against analytical 6.264 — 41%.** Ritter is the worst case (frictionless, Fr→∞ at the tip); the urban case is untested |
| U | Rotate the sudo password | 🔴 OPEN (security) |
| AA | SAR observed-water reference | ✅ **CLOSED — extent is PHYSICALLY UNADJUDICABLE via C-band VV SAR.** 25 classifiers across 6 families tested on an independent known-water set (129,474 OSM lake-interior cells, 30 m erosion). Pre-registered falsifier — >70% POD on Varthur/Bellandur at <1% dry FPR — **failed by every candidate**: best recall there (−8 dB) costs 19.01% dry FPR; Otsu reaches 98% recall at 46–47% FPR; change detection *lowers* recall to 0.1461. The −16 dB cut's recall on known water is **0.4421** overall, 0.3487 Varthur, 0.3787 Bellandur. Mechanism: hyacinth and froth destroy the specular return. **No threshold fixes this.** Untested axes: VH cross-pol (not on disk), Jan–Mar dry-season baseline |
| AB | Gate run not reproducible from its manifest | 🔴 OPEN — `git_sha 9032b55` does not contain `scripts/run_phase3_validation.py`; run executed from an uncommitted tree. No `status` field, no start/end pair, no compute-budget block, no scored-domain count, no outfall volume. Rule 6 broken on the first run after rule 6 was written |
| AC | BBMP hit rate has no null model; FAR absent | 🟡 OPEN — FAR declared in the docstring, never computed. 50 m window vs per-cell base rate are not comparable; the null lies in [8.5%, 89%] so 49.87% is uninterpretable. Point count is 399, not 398 — my error |
| AD | Error-budget percentages fabricated | 🔴 MUST BE STRUCK — 50/30/16/4 sum to exactly 100 with only the 4% traceable. Violates rule 3. The infiltration entry is also backwards in sign |
| AE | Fill is bimodal; deviation quotes the mean | 🟡 **Z PARTIALLY REOPENED** — totals reproduce exactly, characterisation does not. 17,959 cells (1.80 km²) raised >2 m, carrying 71.5% of volume; 27.5% of cells carry 85.6%. "Micro-depressions / noise pits" at a 0.65 m mean describes neither mode |
| AF | `residual_max_bound` applies the looser stated rule | 🟡 OPEN — two criteria stated (0.75% domain, 25% of pits = 72,566); bound set from the first at 95,463, so the binding criterion was discarded. Buffered-domain product misprinted as 12,747,515 (correct: 12,728,415); derivation used the right figure |
| AG | Does the fill explain the depth deficit? | ✅ **NEGATIVE RESULT** — no. All 24 ground-truth points sit on cells with exactly 0.0 m fill. Hypothesis dead; the 20/23 under-predictions are elsewhere |
| AI | Depth deficit is real and understated | 🔴 OPEN — RMSE 0.7505 traced to `depth_event_maximum.tif` via a **local-max** window, the most generous field (point-value gives 0.7834). **15 of 24 points have under 1 cm at the exact cell; 21 of 24 under 10 cm.** A placement failure, not a magnitude one — not calibration-sized |
| AJ | Forcing resolution | ✅ **RESOLVED — hypothesis dead.** 25 covering cells, totals 54.0–109.1 mm, max/min 2.02×. The 24 flood points average 84.1 mm = **77% of domain peak**, so forcing does not starve them. **GT_15 Hebbal Underpass receives the domain maximum 109.1 mm and is modelled at 0.046 m against a 0.35–0.50 m band.** Daily splits 46.32/39.15 reproduce exactly from the granules — measured, not copied; my arithmetic flag was wrong, 87.09 is area-weighted vs unweighted cell-mean. Leading candidate is now **terrain representation / absent drain network** — the GT set is dominated by underpasses and junctions a bare-earth DEM cannot resolve. Forced field still not persisted. Old row: 🔴 OPEN — the 24 GT points fall in 5 IMERG cells and **12 share one**. If the forced field is near-uniform where the convective event was peaked, calibration cannot recover it and the gate is unadjudicable on IMERG. No rainfall raster was saved, so the field cannot be inspected without re-running. Daily split 46.32 + 39.15 = 85.47 against a stated 87.09 — 39.15 appears copied from an earlier window |
| AK | GT points are not sinks; depth metric violates our own rule | 🔴 **OPEN — this is the finding.** 0 of 24 GT points is the lowest cell within 110 m; median 1.09 m above local minimum (random baseline 1.73 m, so they are low but never the sink). Water reaches within ~110 m but not the cell. Causes: a bare-earth DEM cannot resolve an underpass sag, and there is no storm-drain network to fail. **goal 12 scored per-point depth RMSE at 10 m from news-geolocated coordinates — the exact metric CLAUDE.md §Reporting numbers forbids.** Depth verdict must be re-scored per-road-segment. Extent verdict (CSI 0.0322) stands regardless |
| AN | Rainfall **intensity** under-read | 🔴 **CONFIRMED, and the evidence was in this repo (post-audit W2).** `data/fetched_articles.json`, fetched 2026-08-17 and never opened: **131.6 mm in 24 h, 4–5 Sept, "most of which fell in less than 12 hours"** across ~20 IMD-sourced outlets; district 79.2 mm vs a 4.5 mm normal (1,660% excess); KSNDMC at our own GT sites Bellandur 67.5 / Halanayakanahalli 74 / Varthur 83.5 mm per 24 h. Corroborated by **131.0 mm/21 h** in the synoptic record. IMERG gave **87.09 mm over the whole 48 h**, peak 21.40 mm/hr, nothing ≥25. **Nuance:** at the SE flood points IMERG totals (77–79 mm/48 h) match KSNDMC there — the under-read is in **timing and peak concentration**, not bulk volume |
| AN (station archive amendment) | Archived station records obtained; peak intensity still unmeasured | 🟡 **PARTIAL, still critical.** NOAA/NCEI records are secured for VOBL, VOBG, and two Bengaluru synoptic stations. VOBL has 382 FM-15 METAR + 3 FM-16 SPECI; VOBG has 235 FM-15 records, all without quantitative rain. ISD-Lite's documented 1-hour/6-hour fields are missing at all four stations. Synoptic AA1 maxima are 131.0 mm/21 h (6.2381 mm/hr accumulation average) and 124.0 mm/21 h (5.9048 mm/hr), not hourly peaks. Quantitative hourly and sub-hourly peaks are null, so the requested peak ratio against IMERG's 21.40 mm/hr is **N/A**; 0.2915 and 0.2759 are explicitly non-peak proxy ratios. Source/licence/failure audit: `data/raw/rainfall_stations/source_audit.md`. |
| AH | Gate invariants never demonstrated red; skips reported as green | 🟡 OPEN — "100% green" over 187 passed **and 22 skipped**, 20 of them `BLOCKED:` full-domain solver invariants exercising the very configuration the gate ran. No V5 red demo for the six gate invariants. Invariant R's red demo ran at 7,836 cells against a 494,550-cell green check (63× scope gap, V7 label needed) |
| AL | Underpass sag absence & DEM unrepresentativeness | ✅ **RESOLVED 2026-08-18** — 2,534 segments cataloged; 39.5% flat runthrough (no sag), 54.1% crest/deck visible. 6 of 24 GT points (Panathur, Marathahalli, Hebbal) & 33.1% of BBMP points coincide with underpasses. Register saved with 0 invented depths |
| AM | Drainage data availability audit | ✅ **RESOLVED 2026-08-18** — BBMP SWD secured (1,988.5 km, 2.33× OSM, Public Domain on OpenCity) but 2D lines only (no inverts/cross-sections). BWSSB is UGD sewerage (restricted internal). OSM culverts virtually 0% complete (1 feature in 717 km²). Desilting is tabular/PDF only |
| AN | Per-road-segment validation rescore | ✅ **RESOLVED 2026-08-18** — Rescored per-segment (F=20% or N=3 contig @ 0.15 m) across 169,124 segments. Achieved CSI: BBMP 0.0043 (lift 1.88x), GT 0.00025 (lift 1.76x), SAR change 0.0030 (lift 1.91x). **Lift drops below cell-level 5.17x.** Segment status is not a defensible product without drainage/underpass structures; flagged for adjudication |
| AO | Verified elevation source for underpasses | ⛔ **BLOCKED, Rule 5 stop 2026-08-18** — no Bengaluru source with realized lower-road profile, datum, bridge treatment, and QA was obtainable in this session; no depths or inverts assigned. External survey/procurement/data request requires Darshil's decision |

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

*Deliberately not on this list:* buying Colab Pro (D says the numbers don't justify it yet), chasing the EA input files (item 9 is no longer load-bearing now that ANUGA and analytical cases carry correctness), or changing terrain while AO is blocked. Item 3 is no longer skipped; item 4 remains skipped by decision.
