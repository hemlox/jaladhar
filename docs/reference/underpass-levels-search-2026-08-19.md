# Underpass vertical-level search — 2026-08-19

**Item:** OPEN-ITEMS.md **AO** — do any obtainable sources give per-location vertical levels
(clearance height / road surface level / realized flood depth) for Bengaluru's named underpasses?

**Method:** 50-location register (`scripts/build_underpass_search_register.py` →
`data/raw/underpass_search/search_register.csv`) × 4 Google News RSS query templates
(`scripts/search_underpass_levels.py`) = **200 queries, 705 result rows, 0 failures**,
45/50 locations with ≥1 hit (`results/manifest.json`). High-value articles fetched to text,
quoted verbatim below with URL + date. All measurements re-runnable post-commit for final SHA.

**Answer up front:** [FINDING] Per-location **vertical data exists** for a subset of the register —
clearance heights (Madiwala 5.5 m, Panathur 5 m vents), a realized flood depth (KR Circle >12 ft,
2026-04-29), and railway station GL/RL (BMRCL DPRs, previous audit). [FINDING] **No source** gives
per-location MSL road-surface levels for the register's 50 locations — the register stays unpopulated
(Rule 1). **AO partially closed on the axis "does obtainable vertical data exist" (yes), open on the
axis "absolute per-location road levels" (still no).** One new independent terrain source (KSRSAC KGIS
KA_DTM) found and datum-verified this session — details in the GIS-layers audit.

---

## 1. Realized per-location values

Legend: **[F]** = journalist-reported fact with URL+date; **[E]** = engineering figure quoted to a named
official; **[M]** = our own measurement (manifest-verified). All depths/clearances are event- or
structure-specific, NOT register fields.

| Location (register) | Realized value | Source, date | Type |
|---|---|---|---|
| KR Circle Underpass | **>12 ft (~3.7 m) of rainwater** in underpass after 2026-04-29 downpour; pumping ongoing Thu; Lokayukta reopened probe; GBA plans cut drain + sheet cover (High Grounds model) | TOI 2026-05-01 `timesofindia.indiatimes.com/city/bengaluru/three-years-after-fatal-drowning-kr-circle-underpass-floods-again-in-bengaluru/articleshow/130649313.cms`; The Hindu 2026-04-30 `thehindu.com/news/cities/bangalore/bengaluru-rains-three-years-after-fatal-flooding-kr-circle-underpass-submerged-again/article70923673.ece` | [F] depth |
| KR Circle Underpass | 2023-05-21: car **"almost completely submerged with only its roof visible"**, "neck-deep water"; water rose and car flooded **"within two minutes"**; barricade collapsed | India Today 2023-05-21 `indiatoday.in/cities/bengaluru/story/infosys-techie-dies-after-car-enters-flooded-underpass-bengaluru-2382303-2023-05-21`; The Quint 2023-05-26 `thequint.com/south-india/bengaluru-techie-death-underpass-problem-waterlogging` | [F] depth |
| KR Circle Underpass | U-shaped design, steep ramps, gratings only way out (Prof M.N. Sreehari, HC inspection team); built 2009 ("magic box") | The Hindu 2023-06-06 `thehindu.com/news/cities/bangalore/explained-why-underpasses-in-bengaluru-see-flooding-in-monsoon/article66919145.ece` | [E] design |
| Madiwala Underpass | **Vertical clearance 5.5 m**; BBMP CE B.S. Prahalad: older underpasses were 4.5 m; height barrier at 4.5 m planned; HTVs get stuck | Bangalore Mirror 2022-07-24 `bangaloremirror.indiatimes.com/bangalore/civic/whats-eating-madiwala-underpass-big-vehicles/articleshow/93079717.cms` | [E] clearance |
| Madiwala Ayyappa Temple Underpass | submerged "for hours", Oct 2024; cars stuck in water (commuter quote) | The Hindu 2024-10-23 `thehindu.com/news/national/karnataka/flooded-underpasses-continue-to-be-death-traps-during-rain/article68783713.ece` | [F] depth |
| Panathur railway underpass | **new RUB: two vents, 9 m wide × 5 m tall**; submerged "for hours" Oct 2024 | Deccan Herald 2024-08-11 `deccanherald.com/india/karnataka/bengaluru/panathur-underbridge-is-a-crossing-to-bear-3145928`; Hindu 2024-10-23 (above) | [F]/[E] clearance |
| Okalipuram Underpass | submerged "for hours", Oct 2024 | The Hindu 2024-10-23 | [F] depth |
| Silk Board Junction | flooded 2026-05-29 evening, "even the newly constructed metro station area completely marooned"; Vrishabhavathi overflow → flood-like at Nayandahalli | Deccan Herald 2026-05-29 `deccanherald.com/india/karnataka/bengaluru/vrishabhavathi-overflows-silk-board-flooded-bengaluru-grinds-to-a-haltdue-to-heavy-rain-4021174` | [F] depth |
| Silk Board Junction | knee-deep water, 2025-05-19 (104 mm in 24 h, 2nd highest in a decade) | Economic Times 2025-05-21 `economictimes.indiatimes.com/news/bengaluru-news/bengalurus-rain-crisis-city-records-heaviest-deluge-since-2017-leaves-five-dead-hundreds-of-homes-submerged-in-indias-it-capital/articleshow/121303422.cms` | [F] depth |
| Nayandahalli Junction | Vrishabhavathi overflow → flood-like situation, 2026-05-29; BBMP annual maintenance contract holder | DH 2026-05-29 (above); Hindu 2023-06-06 (contracts) | [F] depth |
| Kuvempu Underpass | waterlogged 2025-09-07 (towards Bhadrappa layout); private ad-contract maintenance | TNIE 2025-09-07 `newindianexpress.com/cities/bengaluru/2025/Sep/07/heavy-rain-floods-roads-disrupts-traffic-across-bengaluru`; Hindu 2023-06-06 | [F] depth |
| Mekhri Circle Underpass | first vehicle underpass in Bengaluru (2001-02); waterlogging near CQAL Cross 2025-09-07; private maintenance | Hindu 2023-06-06; TNIE 2025-09-07 | [E]+[F] |
| Ullal Junction Underpass | waterlogged 2025-09-07 | TNIE 2025-09-07 | [F] depth |
| Dairy Circle Underpass | waterlogged via Sagar Junction, 2025-09-07 and 2026-06-20 | TNIE 2025-09-07; DH 2026-06-20 `deccanherald.com/india/karnataka/bengaluru/bengaluru-flooded-roads-paralyse-the-it-corridor-4046771` | [F] depth |
| Kino theatre railway underpass | waterlogging problems common; repair work stalled (BBMP/BWSSB buck-passing) | DH `deccanherald.com/india/karnataka/bengaluru/repair-work-kino-theatre-stuck-2012434`; BTP Facebook post (traffic restricted due to waterlogging) | [F] depth |
| Babusapalya RUB (Horamavu corridor) | nearly completed but **inoperational — 25 m land dispute pending since May 2024**; closed for months (TOI 2026-03-10) | TNIE 2025-07-01 `newindianexpress.com/cities/bengaluru/2025/Jul/01/stalled-by-land-dispute-road-under-bridge-turns-into-a-makeshift-bar`; TOI 2026-03-10 `timesofindia.indiatimes.com/city/bengaluru/unsafe-roads-and-garbage-dumpsrwas-flag-civic-woes-at-horamavu/articleshow/129411105.cms` | [F] status |
| Horamavu Underpass | railway underpass under construction — delayed 2021 and 2025 ("two months more", Jun 2025) | Deccan Chronicle `deccanchronicle.com/nation/current-affairs/250617/horamavu-underpass-2-more-months-to-go.html`; 210517 article | [F] status |
| Yelahanka AFB (new NHAI, not register) | **underpass ~6 m below main road level**, 320 m cut-and-cover, two lanes; ₹51.27 cr; bids opened Feb 2026 | DNA 2026-01-29 `dnaindia.com/health/report-bengaluru-nhai-to-built-city-s-longest-tunnel-style-underpass-on-airport-highway-3198567`; DH 2026-01-27 | [E] relative level |
| Yelahanka underpass | "deep floodwater", 2026-06-20 | DH 2026-06-20 | [F] depth |
| Hebbal (Anandnagar) | annual maintenance contract holder; 2025-10-12 long jams | Hindu 2023-06-06; Mathrubhumi 2025-10-12 | [E]+[F] |
| Seshadripuram underpass | waterlogged (photo caption), 2025-09-07 | TNIE 2025-09-07 | [F] depth |
| KR Puram / Hebbal corridor (rail) | **BMRCL Phase 3 Exec Summary Table 0.19: Hebbal RS CH 30149, existing GL 894.316, proposed RL 907.500** (31 stations listed; see BMRCL DPR audit) | BMRCL Phase 3 DPR exec summary, `bmrcl.co.in:8282` (2026-08-19 audit) | [E] GL/RL |
| Khoday Junction underpass (Majestic) | waterlogged 2025-09-07 | TNIE 2025-09-07 | [F] depth |
| Binny Mill railway underpass | waterlogged (Hunasemara Junction) 2025-09-07 | TNIE 2025-09-07 | [F] depth |

**Our own measurement, city-wide (this session):** KSRSAC KGIS `KA_DTM_BBMP.img` — public
WGS84-ellipsoidal DTM, 5–10 m cells; 75 points sampled; after EGM96 conversion residual vs FABDEM
mean +0.91 m, stdev 2.44 m (`scripts/measure_kgis_dtm.py`, manifest `kgis_dtm/manifest.json`).
Bare-earth: does NOT resolve underpass road dips (Panathur transect: smooth 3.16 m/200 m, no dip).
Full analysis: `docs/reference/gis-layers-vertical-audit-2026-08-19.md`.

## 2. Per-location coverage table (50 register rows)

Hit counts from `results/search_results.csv`. **45/50 locations have ≥1 hit; 5 have zero:**
Bellandur Kodi Junction, Kaderanahali, LuLu Global Mall, NIMHANS, RMZ Eco World.
Worst-hit (noise-inflated): Wind Tunnel Rd 79, Silk Board 63, Millers Rd 58, KR Puram Lake Rd 56
(many are same-name matches — Bicester UK, Manila PH, Pune). Values column: ✔ = realized value in
§1; ✘ = coverage only (flood events/status/design, no level figure).

| Location | Hits | Value | | Location | Hits | Value |
|---|---|---|---|---|---|---|
| Annapoorneshwari Main Rd Jn | 12 | ✘ | | Magadi Underpass road | 31 | ✘ |
| Bellandur Kodi Junction | 0 | — | | Maharani College | 13 | ✘ |
| Cauvery Junction | 11 | ✘ | | Madiwala | 11 | ✔ 5.5 m |
| CNR Rao | 4 | ✘ | | Malleshwaram | 11 | ✘ |
| Dairy Circle | 6 | ✔ | | Marathahalli | 22 | ✘ |
| Dr. Rajkumar | 8 | ✘ | | Mekhri Circle | 9 | ✔ |
| Hebbal Flyover | 35 | ✔ | | Mill Corner Rd | 10 | ✘ |
| Hebbal Twin Tunnel | 4 | ✘ | | Millers Road | 58 | ✘ |
| Horamavu | 19 | ✔ | | Munekolala | 10 | ✘ |
| Jayadeva Hospital | 15 | ✘ | | Nagarabhavi | 6 | ✘ |
| Kaderanahali | 0 | — | | Nayandahalli | 20 | ✔ |
| Kasturinagar Rd UB | 8 | ✘ | | NIMHANS | 0 | — |
| KR Circle | 38 | ✔ >12 ft | | Old Airport Rd Rail X-ing | 7 | ✘ |
| KR Puram Lake Road | 56 | ✘ | | Old Mysuru Rd | 7 | ✘ |
| Kundalahalli | 12 | ✘ | | Rajajinagar Entrance | 6 | ✘ |
| Kuvempu | 14 | ✔ | | RMZ Eco World | 0 | — |
| Laggere | 10 | ✘ | | Sangam Subway | 8 | ✘ |
| Lazar Road | 13 | ✘ | | Sankey (×2) | 16 | ✘ |
| LuLu Global Mall | 0 | — | | Shivajinagar Bus Stn Subway | 7 | ✘ |
| (Kino theatre RUB, Shivananda RUB, Yeshwanthpur, Silk Board, Varthur, Bellandur — see register; Silk Board 63 ✔, Varthur 8 ✘, Yeshwanthpur 6 ✘) | | | | Star Circle | 7 | ✘ |
| | | | | Suranjandas Rd | 9 | ✘ |
| | | | | Thanisandra Main Rd RUB | 7 | ✘ |
| | | | | Ullal Junction | 7 | ✔ |
| | | | | Vatal Nagaraj Rd | 8 | ✘ |
| | | | | Wind Tunnel Road | 79 | ✘ |

(full 50-row table is `results/search_results.csv`, reproducible from the search script)

## 3. Per-source table (what each source yields for AO)

| Source | Vertical data | Per-location? | Obtainable | Licence | Verdict |
|---|---|---|---|---|---|
| **News reporting** (TOI/Hindu/DH/BMirror/TNIE/DC/ET/IndiaToday/Quint) | clearance heights, event flood depths, status | ~15 locations of 50 | Public web (paywalls partial) | editorial copyright; quotes only | **the only source of per-location realized values so far** |
| **KSRSAC KGIS KA_DTM_BBMP.img** | ellipsoidal DTM 5–10 m, datum verified vs FABDEM (mean +0.91 m resid) | city-wide terrain, NOT road level | Public ArcGIS REST, no auth | ToS unread (flag) | new independent terrain baseline; bare-earth only |
| **BMRCL DPRs (Phase 2B T5.4, Phase 3 T0.19)** | rail PVI levels; station GL/RL | rail corridor stations (Hebbal etc.), not underpasses | Public PDF | BMRCL | per-station GL/RL values exist |
| **KSRSAC contours (BBMP_FloodLayers L10/L11)** | ELEVATION attr, 10 m interval | terrain contours | Public | ToS unread | coarse |
| Mapillary | street-level photos w/ clearance signage | all 50 — **token-gated** | registration required | Mapillary ToS | BLOCKED_NO_TOKEN (all 50) |
| TransitGIS / OpenCity / BGIS_Roads / Road_Line_2023 | none (2D, verified) | — | Public | — | no vertical |
| everybodywiki "List of flyovers and under-passes in Bengaluru" | lengths/lanes/years with refs | many underpasses | Public | CC (wiki) | discovery-only; refs point to primary articles |
| Academic literature | none found (per-location) | — | — | — | no vertical |
| BBMP GIS portal / BDA | unreachable this session | — | 502/SSL | — | recheck other network |
| KPPP tenders | design params login-gated | — | 401 | — | blocked |

## 4. Axis accounting (V11)

- **Axis CLOSED (existence):** per-location vertical data — clearance heights, event depths, one
  relative level — is obtainable for ~15 of 50 locations via public reporting + engineering quotes.
- **Axis OPEN (completeness):** absolute MSL road-surface level per location for the register
  remains unavailable; the register itself is not populated (Rule 1). KA_DTM validates terrain but
  is bare-earth. Remaining avenue for full closure: RTI to BBMP Road Infrastructure Dept
  (41-underpass audit 2023, vertical clearance drawings) — flag as a formal-request item (RULE 5).

## 5. Files

- Scripts: `scripts/build_underpass_search_register.py`, `scripts/search_underpass_levels.py`,
  `scripts/measure_kgis_dtm.py` (+ manifests in `data/raw/underpass_search/`).
- Data: `search_register.csv`, `results/search_results.csv` (705 rows), `results/manifest.json`,
  `kgis_dtm/dtm_values.csv` + `manifest.json`, `gis_layer_notes.csv`.
- Reports: this file + `docs/reference/gis-layers-vertical-audit-2026-08-19.md` +
  the four 2026-08-19 agent audits (transitgis-opencity, bmrcl-dpr, procurement-legal, academic).
- Manifests re-run post-commit, all carrying final SHA `65f76792feddb82eb4cef7b47e991b6a1f2d85b3`
  (V9): `search_register_manifest.json`, `results/manifest.json`, `kgis_dtm/manifest.json`.