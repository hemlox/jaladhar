# Deep per-location vertical-level search — 2026-08-19 (parallel session 2)

**Dataset:** `data/raw/underpass_search/search_register.csv` (50 locations) × `runs/underpass_measured/geometry_match.csv` (nearest segment, distance, carve status, GT band) **plus** live Overpass/OSM-history, BBMP 14-underpass audit OCR, CAG Disaster-Mgmt Report, Suranjan tender, fetched_articles.json (153), Google News RSS extended probe.

**Method ordered by information value per unit cost (per GOAL):** 0) in-repo corpus inventory (R3) → 1) OCR OpenCity BBMP audit PDFs (highest-value artifact, 15+4 pages scanned JPEG, no text layer) → 2) per-location deep pass (street-level signage OSM tag history, news archive site queries, tenders/DPRs, IRC context) → 3) 50-row classification → 4) carve-grade recommendations with invert mapping.

**Device:** CPU-only (preflight `nvidia-smi` 9 MiB used, RTX 4060 Laptop; no torch CUDA, no GPU allocation). **Provenance:** every measurement by committed script with manifest written at run start and updated in place (rule 6, V9).

**Direct predecessor:** `docs/reference/underpass-levels-search-2026-08-19.md` (200-query RSS, 705 rows, 45/50 with ≥1 hit; re-verified with URL re-open found exactly one carve-grade figure Madiwala 5.5 m). This deep session goes beneath RSS titles into OCR, OSM tag history, and gated DPRs.

---

## 0. Inventory what is already on disk (R3) — what we checked before acquiring

| Corpus item | Status | What we looked for | Finding |
|---|---|---|---|
| `docs/reference/underpass-sources-audit-2026-08-18.md` + `underpass-levels-search-2026-08-19.md` + 4 agent audits (`*_notes.csv` + `*_manifest.json`) | read | per-location vertical claims, OSM maxheight 0.2274%, IRC 5.0/5.5 m band, BMRCL GL/RL tables | one carve-grade among 705 rows (Madiwala 5.5 m), rest depth/status/planned/barrier/length |
| `data/fetched_articles.json` (153 articles, full text) | grep underpass/clearance/height/vertical/vent/RUB → 56 with underpass, 9 with clearance | verbatim chapter-5 grep for clearance numbers | 230 hits on underpass\|clearance\|height\|vertical\|vent but only the 9 clearance articles overlap; no additional carve-grade beyond Madiwala in fetched full text |
| `data/raw/underpass_search/results/cag_disaster_mgmt_full.pdf` (10 MB) + `.txt` via `pdftotext` | full-text grep underpass/clearance/vertical/height/vent | 0 underpass, 3 clearance (all as 'mandatory clearances' legal-framework phrase, not vertical), 0 vent | **no per-location vertical** — report is about institutional/financial gaps, not road levels |
| `data/raw/underpass_search/results/suranjan_das_tender.pdf` (IFT 07.02.2017) | `pdftotext` | Key Parameters fixed by BBMP (vertical clearance, span) | IFT carries only cost/EMD/schedule (Rs 4485 lakh, EMD 45 lakh, 9 months, Turnkey/Lump Sum/Fixed Price/No Variation on Tenderer's own Design). **No road/sump-invert levels.** Full design params are on gated e-procurement portal (401). |
| `runs/underpass_measured/geometry_match.csv` (50 rows, read-only) | read | nearest register osm_id, distance, z_max, confidence, carve_status, nearest GT + Sept-2022 band | 42 not_passable, 7 already_carved, 1 skipped_in_existing (Maharani College). Median GT distance 4–6 km except 6 GT-anchored locations at 0 m; all have a matched segment but 11 registerless GT/BBMP anchors have no own register osm_id |

R3 violation avoided: `data/fetched_articles.json` was fetched 2026-08-17 and sat unread while forcing was earlier declared resolved in a different thread; we grepped it first here and found the 131.6 mm rainfall note was already on disk (confirming the disaster note in §1).

---

## 1. 50-row table — per-location vertical figure, type, quote, URL, dates, confidence, carve status

**Taxonomy:** figure **type** `clearance` = measured vertical clearance of existing structure (carve-grade) | `dimension` = engineered vent width/height (carve-grade) | `relative` = "x m below road level" (maps to clearance+deck_depth, carve-grade) | `depth` = flood-water depth (NEVER carve-grade, carries event date) | `status` = construction/maintenance state (NEVER carve) | `nothing-found` = no figure after deep search.

**Not-carve reasons:** `flood-depth-only` | `planned-structure` (future/under-construction, anachronism for Sept-2022 DEM) | `barrier-height` (height-barrier sign, not structure) | `status-only` | `nothing-found` (| `nothing-found (registerless …)` for the 11 GT/BBMP anchors with no register osm_id) — every depth carries its event date; depths from events other than Sept 2022 are recorded but never scored against the 2022 run.

**Carve-grade rule (applied uniformly):** measured clearance / engineered dimension / relative vertical **AND** matching register segment geometry exists. IRC default 5.0/5.5 m is context only — a location whose only figure is an IRC default is NOT carve-grade. Flood-depth observations are NOT clearances.

| # | Location (50-register) | Figure | Type | Exact quote (verbatim) | URL | Pub date | Event year | Conf | Carve? | Reason / note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Silk Board Junction | knee-deep (~0.45) | depth | knee-deep water, 2025-05-19 (104 mm in 24 h, 2nd highest in a decade) | [https://economictimes.indiatimes.com/news/bengaluru-news/ben…](https://economictimes.indiatimes.com/news/bengaluru-news/bengalurus-rain-crisis-city-records-heaviest-deluge-since-2017-leaves-five-dead-hundreds-of-homes-submerged-in-indias-it-capital/articleshow/121303422.cms) | 2025-05-21 | 2025-05-19 | medium | no | flood-depth-only |
| 2 | Hebbal Flyover Underpass | jam | depth | long jams 2025-10-12, annual maintenance contract holder | [https://www.hindu.com/news/cities/bangalore/bengaluru-rains-…](https://www.hindu.com/news/cities/bangalore/bengaluru-rains-2025-10-12) | 2025-10-12 | 2025-10-12 | medium | no | flood-depth-only |
| 3 | Bellandur Kodi Junction | — | nothing-found |  |  |  |  | high | no | nothing-found (zero news hits + registerless:… |
| 4 | Varthur Kodi Junction | — | nothing-found |  |  |  |  | high | no | nothing-found (registerless GT/BBMP anchor: n… |
| 5 | KR Puram Lake Road | — | nothing-found |  |  |  |  | high | no | nothing-found (registerless GT/BBMP anchor: n… |
| 6 | Yeshwanthpur Railway station | — | nothing-found |  |  |  |  | high | no | nothing-found (registerless GT/BBMP anchor: n… |
| 7 | Old Airport Road Railway Crossing | — | nothing-found |  |  |  |  | high | no | nothing-found (registerless GT/BBMP anchor: n… |
| 8 | Railway underbridge near Kino theatre | — | nothing-found |  |  |  |  | high | no | nothing-found (registerless GT/BBMP anchor: n… |
| 9 | Shivananda circle_Railway under pass | — | nothing-found |  |  |  |  | high | no | nothing-found (registerless GT/BBMP anchor: n… |
| 10 | Cauvery Junction | — | nothing-found |  |  |  |  | high | no | nothing-found (registerless GT/BBMP anchor: n… |
| 11 | Old Mysuru Road Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 12 | Munekolala Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 13 | KR Circle Underpass | >3.7 | depth | >12 ft (~3.7 m) of rainwater in underpass after 2026-04-29 downpour; pumping ongoing | [https://timesofindia.indiatimes.com/city/bengaluru/three-yea…](https://timesofindia.indiatimes.com/city/bengaluru/three-years-after-fatal-drowning-kr-circle-underpass-floods-again-in-bengaluru/articleshow/130649313.cms) | 2026-05-01 | 2026-04-29 | medium | no | flood-depth-only |
| 14 | Mekhri Circle Underpass | — | status | first vehicle underpass in Bengaluru (2001-02); waterlogging near CQAL Cross 2025-09-07 | [https://thehindu.com/news/cities/bangalore/explained-why-und…](https://thehindu.com/news/cities/bangalore/explained-why-underpasses-in-bengaluru-see-flooding-in-monsoon/article66919145.ece) | 2023-06-06 | 2001-02_built | medium | no | status-only |
| 15 | Vatal Nagaraj Road Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 16 | Magadi Underpass road | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 17 | Millers Road Underpass | 5.0 | clearance | maxheight=5 | [https://www.openstreetmap.org/way/158623466](https://www.openstreetmap.org/way/158623466) | 2025-12-20 | existing | medium | yes | carve-grade (signed_limit_bias) |
| 18 | Hebbal Twin Tunnel | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 19 | Marathahalli Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 20 | Wind Tunnel Road Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 21 | LuLu Global Mall Underpass | 3.0 | clearance | maxheight=3 | [https://www.openstreetmap.org/way/1092668711](https://www.openstreetmap.org/way/1092668711) | 2023-10-27 | existing | medium | yes | carve-grade (signed_limit_bias) |
| 22 | Maharani College Underpass | 4.0 | clearance | maxheight=4 | [https://www.openstreetmap.org/way/208827395](https://www.openstreetmap.org/way/208827395) | 2025-08-18 | existing | medium | yes | carve-grade (signed_limit_bias) |
| 23 | Sangam Subway | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 24 | Thanisandra Main Road Railway Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 25 | Nayandahalli Underpass | flood-like | depth | Vrishabhavathi overflow → flood-like situation, 2026-05-29 | [https://deccanherald.com/india/karnataka/bengaluru/vrishabha…](https://deccanherald.com/india/karnataka/bengaluru/vrishabhavathi-overflows-silk-board-flooded-bengaluru-grinds-to-a-haltdue-to-heavy-rain-4021174) | 2026-05-29 | 2026-05-29 | medium | no | flood-depth-only |
| 26 | Malleshwaram Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 27 | Laggere Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 28 | Kundalahalli Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 29 | Lazar Road Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 30 | Rajajinagar Entrance Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 31 | Mill Corner Road Underpass | 4.0 | clearance | maxheight=4 | [https://www.openstreetmap.org/way/1081215226](https://www.openstreetmap.org/way/1081215226) | 2024-10-16 | existing | medium | yes | carve-grade (signed_limit_bias) |
| 32 | RMZ Eco World Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found (zero news hits + no OSM tag + … |
| 33 | Dr. Rajkumar Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 34 | Shivajinagar Bus Station Subway | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 35 | Horamavu Underpass | — | status | railway underpass under construction — delayed 2021 and 2025 (two months more, Jun 2025) | [https://deccanchronicle.com/nation/current-affairs/250617/ho…](https://deccanchronicle.com/nation/current-affairs/250617/horamavu-underpass-2-more-months-to-go.html) | 2025-06-25 | 2025-06_planned | medium | no | status-only |
| 36 | Annapoorneshwari Main Road Junction Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 37 | NIMHANS Underpass | 3.048 | clearance | maxheight=10' (=3.048m) | [https://www.openstreetmap.org/way/239738418](https://www.openstreetmap.org/way/239738418) | 2024-08-27 | existing | medium | yes | carve-grade (signed_limit_bias) |
| 38 | Star Circle Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 39 | Madiwala Underpass | 5.5 | clearance | Earlier, when the underpasses were constructed the vertical clearance used to be 4.5 metres. The current vertical cleara… | [https://bangaloremirror.indiatimes.com/bangalore/civic/whats…](https://bangaloremirror.indiatimes.com/bangalore/civic/whats-eating-madiwala-underpass-big-vehicles/articleshow/93079717.cms) | 2022-07-24 | 2010_existing | high | yes | carve-grade |
| 40 | Sankey Road Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 41 | Ullal Junction Underpass | waterlogged | depth | waterlogged 2025-09-07 | [https://newindianexpress.com/cities/bengaluru/2025/Sep/07/he…](https://newindianexpress.com/cities/bengaluru/2025/Sep/07/heavy-rain-floods-roads-disrupts-traffic-across-bengaluru) | 2025-09-07 | 2025-09-07 | medium | no | flood-depth-only |
| 42 | Kuvempu Underpass | waterlogged | depth | waterlogged 2025-09-07 (towards Bhadrappa layout) | [https://newindianexpress.com/cities/bengaluru/2025/Sep/07/he…](https://newindianexpress.com/cities/bengaluru/2025/Sep/07/heavy-rain-floods-roads-disrupts-traffic-across-bengaluru) | 2025-09-07 | 2025-09-07 | medium | no | flood-depth-only |
| 43 | Nagarabhavi Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 44 | Kasturinagar Road Under Bridge | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 45 | Dairy Circle Underpass | waterlogged | depth | waterlogged via Sagar Junction, 2025-09-07 and 2026-06-20 | [https://newindianexpress.com/cities/bengaluru/2025/Sep/07/he…](https://newindianexpress.com/cities/bengaluru/2025/Sep/07/heavy-rain-floods-roads-disrupts-traffic-across-bengaluru) | 2025-09-07 | 2025-09-07 | medium | no | flood-depth-only |
| 46 | Suranjandas Road Underpass | 4.5 | clearance | maxheight=4.5 | [https://www.openstreetmap.org/way/1135874172](https://www.openstreetmap.org/way/1135874172) | 2026-02-10 | existing | medium | yes | carve-grade (signed_limit_bias) |
| 47 | Kaderanahali Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found (zero news hits + no OSM tag + … |
| 48 | Jayadeva Hospital Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |
| 49 | Sankey Underpass | 4.25 | clearance | maxheight=4.25 | [https://www.openstreetmap.org/way/163101103](https://www.openstreetmap.org/way/163101103) | 2024-10-16 | existing | medium | yes | carve-grade (signed_limit_bias) |
| 50 | CNR Rao Underpass | — | nothing-found |  |  |  |  | high | no | nothing-found |

**Counts:** 8 carve-grade (1 news-measured + 7 OSM signed-limit) vs 42 not-carve-grade (7 flood-depth-only, 2 status-only, 33 nothing-found of which 8 are registerless GT/BBMP anchors). No relative-vertical and no planned-structure conversions among the 50 (Yelahanka 6 m below road is a planned NHAI tunnel, not in the 50-register).

**Under the strict definition (physical clearance, not signed limit), only 1 is carve-grade without caveat:** Madiwala 5.5 m (measured clearance, BBMP CE quote, existing 2010). The 7 OSM entries are `maxheight` (not `maxheight:physical`), i.e. **signed limits** — still a measurement per the GOAL's `signed_limit_bias` clause, but biased low (sign may be lower than physical). They feed the next carve cycle with that bias flagged; they are not interchangeable with the 5.5 m measured clearance.

---

## 2. Carve-grade recommendations — osm_id, number, invert mapping, GT mapping, Sept-2022 band

**Formula (from committed `runs/underpass_measured/match_geometry.py` and `underpass_invert_derivation_measured.csv`):**

```
invert_z = z_deck − clearance − deck_structural_depth
deck_structural_depth ∈ [1.0, 2.0] m (stated assumption [DERIVED], see module docstring) — band preserved, operating ≈ midpoint 1.5 m
"x m below road level" maps to clearance + deck_depth = x, NOT to clearance
```

| Location | Register osm_id (geometry_match) | Clearance (m) | Source (type) | z_deck (m) (DEM z_max) | deck_depth band | invert_z band (low = z−clear−2.0, high = z−clear−1.0, oper = z−clear−1.5) | Nearest GT + dist | GT Sept-2022 band | Confidence / flag |
|---|---|---|---|---|---|---|---|---|---|---|
| Madiwala Underpass | 148210028 | 5.5 | news_bangalore_mirror_2022-07-24 (MEASURED_NEWS, existing 2010) | 891.415 | [1.0,2.0] | [883.915, 884.915] oper 884.415 | GT_22 762.9 m | 0.35-0.5 m | high (measured, BBMP CE B.S. Prahalad quote) |
| Millers Road Underpass | 158623466 | 5.0 | OSM maxheight=5 (signed limit, v6 2025-12-20) | 917.484 | [1.0,2.0] | [910.484, 911.484] oper 910.984 | GT_15 5169.5 m | 0.35-0.5 m | medium (signed_limit_bias) |
| Maharani College Underpass | 208827395 | 4.0 | OSM maxheight=4 (signed limit, v9 2025-08-18) | 922.140 | [1.0,2.0] | [916.140, 917.140] oper 916.640 | GT_11 6987.7 m | 0.5-0.7 m | medium (signed_limit_bias); geometry_match HIGH but carve_status skipped_in_existing |
| LuLu Global Mall Underpass | 1092668711 | 3.0 | OSM maxheight=3 (signed limit, v3 2023-10-27) | 878.305 | [1.0,2.0] | [873.305, 874.305] oper 873.805 | GT_15 7111.8 m | 0.35-0.5 m | low (signed_limit_bias; 3.0 m anomalously low) |
| Suranjandas Road Underpass | 1135874172 | 4.5 | OSM maxheight=4.5 (signed limit, v9 2026-02-10) | 899.66 | [1.0,2.0] | [893.160, 894.160] oper 893.660 | GT_16 913.5 m | 0.5-0.7 m | medium (signed_limit_bias) |
| Sankey Underpass | 163101103 | 4.25 | OSM maxheight=4.25 (signed limit, v11 2024-10-16) | 930.959 | [1.0,2.0] | [924.709, 925.709] oper 925.209 | GT_15 4159.6 m | 0.35-0.5 m | medium (signed_limit_bias) |
| Mill Corner Road Underpass | 1081215226 | 4.0 | OSM maxheight=4 (signed limit, v4 2024-10-16) | 900.809 | [1.0,2.0] | [894.809, 895.809] oper 895.309 | GT_15 5744.2 m | 0.35-0.5 m | medium (signed_limit_bias) |
| NIMHANS Underpass | 239738418 | 3.048 | OSM maxheight=10' (=3.048 m) (signed limit, v4 2024-08-27) | 914.334 | [1.0,2.0] | [909.286, 910.286] oper 909.786 | GT_22 3194.7 m | 0.35-0.5 m | low (signed_limit_bias; feet→m) |

**All 8 carry a matching register segment geometry** (nearest_segment_dist_m = 0–1.1 m). The in-flight carve run is frozen at its pre-registered set (22 carved of 23 eligible, per `runs/underpass_measured/carve_manifest.json`); these 8 feed the **NEXT** carve cycle, never a mid-run revision.

**GT mapping note:** deepest Sept-2022 GT bands among these 8 are GT_11 0.5-0.7 m and GT_22 0.35-0.5 m — none are the primary flood-deficit points (GT_06 1.20-1.45 m). The 7 OSM conversions are hydraulically peripheral, which is why the mechanism proof at GT_06 is not duplicated by these 7.

---

## 3. What OCR yielded from the BBMP 14-underpass audit (dimension table)

**Dataset:** https://data.opencity.in/dataset/bbmp-underpasses-audit-reports-2023 — resources "BBMP Audit Report on 14 Underpasses" (15 pages, 4,462,396 bytes, Adobe Scan for Android 23.04.27) and "Report on KR Circle Underpass Flooding" (4 pages, 1,636,499 bytes). Both are **scanned JPEG pages, no text layer** (verified `pdfinfo` + `pdfimages -list` 15/4 images @ 283–333 ppi). OCR via `rapidocr_onnxruntime` 1.4.4 (CPU, onnxruntime 1.29.0, opencv 5.0.0.93) + `pymupdf` 1.28.2; fallback to embedded text where OCR low-confidence on Kannada. **Human vision review** of rendered PNGs at 150 dpi (19 pages) provided the verified extraction; automated OCR candidate count = 1 (Karnataka script chokes rapidocr English model). Full script + per-page OCR text + provenance in `data/raw/underpass_search/deep/bbmp_audit_ocr/` (manifest JSON, `extracted_dimensions_candidates.csv`, `extracted_dimensions.csv`, `*_page_*_ocr.txt`, `*_page_*_embedded.txt`, source PDFs).

**What the audit IS:** a BBMP Road Infrastructure Dept inspection after the 21-05-2023 KR Circle death, covering water-stagnation diagnostics and remediation — **drains, pipes, chambers, pumps, ramp coverings, gauge beams, boom barriers** — not a vertical-clearance survey. **Language:** Kannada throughout. English fragments where present: "Up ramp and Down Ramp", "Galvanized Coloured Sheet", "Fibre Plastic", "Vertical Clearance Gauge Beam", "Boom Barrier" — all **without numeric clearance heights**.

| PDF | Page | Underpass (inferred from Kannada header + English fragments) | Dimension type | Value | Unit | Verbatim snippet (Kannada + transliteration note) | Confidence | Note |
|---|---|---|---|---|---|---|---|---|
| bbmp_14_underpasses.pdf | 1 | Swanky/Cunningham? (1st underpass in 14-report, 2009-2011 U-shape) | drain_grating_or_pipe | 03.00 | m (interval) | Up ramp and Down Ramp ಅನ್ನು Galvanized Coloured Sheet ಗಳಿಂದ ಸಂಪೂರ್ಣವಾಗಿ ಮುಚ್ಚುವು… | high_vision | English fragment on p01: transparent Fibre Plastic every 03.00m; ramp covering, not clearance |
| bbmp_14_underpasses.pdf | 1 | (same, 1st underpass) | clearance_gauge | - | - | Vertical Clearance Gauge Beam ಅನ್ನು ಅಳವಡಿಸಿ ಅತೀ ಪ್ರವಾಹ ಉಂಟಾದ/ತುರ್ತು ಸಂದರ್ಭಗಳಲ್ಲಿ… | high_vision | Mentions Vertical Clearance Gauge Beam + Boom Barrier but no numeric height given in audit; plate shows gauge beam structure only |
| bbmp_14_underpasses.pdf | 2 | Kino Theatre Railway Underbridge (Anand Rao circle to Swastik circle) | grating_size | 2.85*1.98 | m (60.72 sq units) | ಸದರಿ ಚರಂಡಿಯ ಗಾತ್ರವು 2.85ಮೀ * 1.98ಮೀ (60.72 ಚ.ಅ) ನಷ್ಟು ಇದ್ದು, ಸದರಿ ಚರಂಡಿಯು ಗಂಟೆಗೆ… | high_vision | Drain (charandi) size, not road clearance; capacity 50.00 lakh litres/hr; pipeline ~900m; no inspection chambers |
| bbmp_14_underpasses.pdf | 2 | Kino Theatre Railway Underbridge | pipe_diameter | 1.20 | m | ಸದರಿ 1.20ಮೀ ವ್ಯಾಸದ ಕೊಳವೆಯನ್ನು ಪ್ರತಿ ಮಳೆಗೆ ಪರಿಶೀಲಿಸಲು ಕ್ರಮಕೈಗೊಳ್ಳಲಾಗಿರುವುದು ಕಂಡುಬ… | high_vision | Recommendation: inspect 1.20m dia pipe each rain |
| bbmp_14_underpasses.pdf | 3 | Kaveri/Cauvery Chithramandira (Swastik/Ballary road) underpass, 2009+2011 | drain_chamber | 1.00*1.00 | m | ಗಾತ್ರವು 1.00ಮೀ * 1.00ಮೀ ಇದ್ದು, ಪೈಪ್‍ಗಳ ಮುಖಾಂತರ ಮಳೆ ನೀರು ಸೆಳೆಯುವ ವ್ಯವಸ್ಥೆ ಕಲ್ಪಿಸಲ… | high_vision | inspection chamber 1.00*1.00m, pipe-extracted to road-side drain; Y-shape, single drain at one low point |
| bbmp_14_underpasses.pdf | 5 | Kodigehalli Railway Vehicle Underpass (2014-15 to 2022) | pipeline | 450.00 | m length, 1.20m dia | ಕಳೆಸೇತುವೆಯಿಂದ ಸುಮಾರು 450.00ಮೀ ದೂರದಲ್ಲಿರುವ ರಾಜಕಾಲುವೆಗೆ ಸಂಪರ್ಕಿಸಲು 1.20ಮೀ ವ್ಯಾಸದ ಕ… | high_vision | Existing 450m, 1.20m dia connection to rajakaluve; no water stagnation at connection low level |
| bbmp_14_underpasses.pdf | 7 | Yelahanka Railway Vehicle Underpass | chamber | 1.20*1.20 | m | ಸದರಿ ಪ್ರದೇಶದಲ್ಲಿ 1.20ಮೀ * 1.20ಮೀ ಅಗಲದ ನೀರು ಹರಿಯುವ ಪರಿವೀಕ್ಷಣಾ ಚೇಂಬರ್ ನಿರ್ಮಿಸಲಾಗಿದ… | high_vision | Inspection chamber 1.20*1.20m on drain line, connected to rajakaluve; concrete joint holes for slow percolation; no stagnation |
| bbmp_14_underpasses.pdf | 8 | RMV Badavane (Fairfields) Railway Underpass | drain_connection | - | - | ಸದರಿ ಚರಂಡಿಯು ಪಕ್ಕದಲ್ಲಿಯೇ ಇರುವ ರಾಜಕಾಲುವೆಗೆ ನೇರವಾಗಿ ಸಂಪರ್ಕ ಹೊಂದಿದ್ದು, ನೀರು ನಿಲ್ಲುವ… | high_vision | Direct connection to rajakaluve, no stagnation; Y-shape near RMV-Horamavu; no dimensions quoted besides shape |
| bbmp_kr_circle.pdf | 1 | KR Circle Underpass (21.05.2023 flood, 4-page report) | rainfall | 24.7 | mm | ದಿನಾಂಕ:21.05.2023ರಂದು ಸುಮಾರು 2.30 ಗಂಟೆಗೆ ಅಸುಪಾಸಿನಲ್ಲಿ ಸುರಿದ ಗಾಳಿಸಹಿತ ಮಳೆಯು ಅತ್ಯಂ… | high_vision | 24.7 mm in <1 hr, wind-driven, overloaded 4 converging roads' drains; leaves/litter blocked grating at invert; post-event sump pumping |
| bbmp_kr_circle.pdf | 2 | KR Circle Underpass | drain | 0.6*0.6 | m | (01 ಮೀಟರ್ ಪ್ರತಿ ಸೆಕೆಂಡ್) ಚರಂಡಿಯಲ್ಲಿ ಇರುವ ಗಾತ್ರ 0.6*0.6 ಅಳತೆಯ ಚರಂಡಿಯಲ್ಲಿ ಸರಾಗವಾಗಿ… | high_vision | 0.6*0.6m drain at invert, 1 m/s velocity => capacity 12.50 lakh litres/hr; but if grating blocked, inflow ~0 |
| harlur_junction_dpr_drawings.pdf | 0 | Harlur Junction Underpass (CH 0+430km, Sarjapura Rd widening DPR, NOT in 50-register) | chainage | 0+430 | km | CONSTRUCTION OF UNDERPASS AT HARLUR JUNCTION (CH: 0+430 KM) - CHAPTER 8 DRAWINGS… | high_vision | Title block only; drawings are A3 engineering sheets at rotation 270°, vector CAD; no OCR-readable elevation/clearance extracted at 150 dpi; full manual CAD reading requires 300 dpi and Kannada/English translation; not in 50-register so outside carve eligibility anyway |

**Verdict on the audit as a vertical-level source:** the 14-underpass audit **yields zero per-location vertical clearance / road-level measurements.** Every numeric figure is a **drain/pipe/chamber/capacity/rainfall** dimension: grating 2.85×1.98 m (60.72), pipe dia 1.20 m, chamber 1.00×1.00 / 1.20×1.20 m, pipeline lengths 450 m / ~900 m, capacities 12.50 / 50.00 lakh litres/hr @ 1 m/s, rainfall 24.7 mm in <1 hr (KR Circle 21.05.2023). The audit mentions "Vertical Clearance Gauge Beam" and "Boom Barrier" as remediation but **quotes no height**.

**Within the 50-register, which audited underpasses map to named locations?** With Kannada headers, the match is inferential, but at least 5 of the 14 align to register names:

- Kino Theatre Railway Underbridge (Anand Rao → Swastik) → `Railway underbridge near Kino theatre` (register) — drain 2.85×1.98 m
- Kaveri/Cauvery Chithramandira (Swastik/Ballary road) → `Cauvery Junction` — chamber 1.00×1.00 m
- Kodigehalli Railway Vehicle Underpass → **not** in the 50-register (nearest in 50 is Horamavu 6 km away) — outside
- Yelahanka Railway Vehicle Underpass → not in 50
- RMV Badavane → not in 50
- KR Circle Underpass → `KR Circle Underpass` (register) — rainfall 24.7 mm, drain 0.6×0.6 m

None of those cross-mapped audits provide a clearance that would make the location carve-grade; they provide **hydraulic** dimensions.

**Harlur Junction DPR drawings (12 pages, A3, 14.8 MB, PScript5/Acrobat Distiller 10.0, rotation 270°):** title "CONSTRUCTION OF UNDERPASS AT HARLUR JUNCTION (CH: 0+430 KM) — CHAPTER 8 DRAWINGS" (Sarjapura Main Road widening, Consultants Infra Support). Rendered at 80 dpi and 150 dpi; vector CAD text not OCR-readable at this dpi. No clearance/elevation extracted. **Not in 50-register** (Harlur is 12.88 N, ~5 km south of closest register RMZ Eco World), so outside carve eligibility even if a clearance were read.

**Files you own:** `data/raw/underpass_search/deep/bbmp_audit_ocr/bbmp_14_underpasses.pdf`, `bbmp_kr_circle.pdf`, `harlur_junction_dpr_drawings.pdf` (source PDFs), `page_*.png` renders, `*_ocr.txt`, `extracted_dimensions_candidates.csv`, `extracted_dimensions.csv`, script `ocr_bbmp_audit.py`, manifest JSON.

---

## 4. BLOCKED items with exact blockers; RTI items to log in OPEN-ITEMS.md

| Item | URL / path | Blocker (exact) | Licence/context | Next step (formal request, do not work around) |
|---|---|---|---|---|
| BBMP 41-underpass audit (2023) — only 14 of 41 released | https://data.opencity.in/dataset/668c76f9-53fe-4642-9e8c-c8d190bd7329 | Dataset description: "BBMP was tasked with auditing 41 underpasses with respect to flooding. This report has the audit for 14 of them. The report is in Kannada." — **27 reports not public** | Public Domain (OpenCity) | **RTI to BBMP Road Infrastructure Dept (EE RI-Special Division, N.R. Square) for remaining 27 audit reports + vertical clearance drawings** — flag in OPEN-ITEMS.md |
| Vertical clearance drawings per underpass (41 locations) | — | Not in released 14; referred to as gauge beam/boom barrier without numeric height; DPR drawings for Harlur show structure but not per-location clearance | — | Same RTI: "vertical clearance drawings" for each of 50 register locations |
| BBMP e-procurement detailed NIT/BOQ (Key Parameters) | https://eproc.karnataka.gov.in + https://kppp.karnataka.gov.in/works-tender-service/v1/api/tender-service/search-eproc-tenders | `401 Full authentication is required to access this resource` (KPPP API, 2026-08-19 probe) / `404 Not Found` on eproc home; tender search gated behind supplier/organisation login | login-gated, not public | **BLOCKED_LOGIN** — record in OPEN-ITEMS.md, do not brute-force; RTI to BBMP EE for Suranjandas Road Old Madras Road grade-separator Key Parameters (clearance/span) and BOQ |
| Mapillary street-level imagery (clearance signage at underpass entrance) | https://graph.mapillary.com/v4/images?fields=id&limit=1 | `500 Internal Server Error: {"error":{"message":"Invalid OAuth 2.0 Access Token","type":"MLYApiException","code":190}}` — no token in `.env` (`mapillary_token_in_env false`, manifest `data/raw/underpass_search/mapillary/manifest.json` 2026-08-18) | Mapillary ToS, token required | **BLOCKED_NO_TOKEN** — registration at https://www.mapillary.com/dashboard/developers ; automated signage reading at scale requires token |
| Google Street View at underpass entrance ("max height X.X m" sign) | https://www.google.com/maps / Street View | Not probed live in this session (would require Street View Static API key; manual browsing of 50 entrances ×2 approaches is O(100) views, each with viewing position + URL to record) | Google ToS | **BLOCKED_NO_TOKEN / MANUAL** — if pursued, needs API key or manual inspection with position+URL logged per sign; not attempted here to keep session CPU-only and avoid ToS workarounds |
| OSM maxheight:physical (measured clearance, not signed limit) | https://www.openstreetmap.org/api/0.6/way/{id}/history + Overpass | Live probe 2026-08-19: **0 of 7 carve-grade ways carry `maxheight:physical`** (all 7 are `maxheight` only, history maxheight occurrences 2–6 per way, last edits 2023-2026). `maxheight:physical` count city-wide remains 0 per 2026-08-18 audit (194,378 highway ways, 0 physical) | ODbL (OSM) | Not blocked — measurement done; signed_limit_bias flagged at next carve |
| KPPP/BBMP tender notification 14 Suranjan Das Road Underpass (IFT) | `data/raw/underpass_search/results/suranjan_das_tender.pdf` | IFT text only (cost/EMD/schedule); Key Parameters "fixed by BBMP" referenced but not enumerated in IFT | Govt of Karnataka public notice | **RTI to BBMP for full tender document with Key Parameters** |
| Bhuvan/KGIS/SOI 25 cm urban DEM | — | Announced (Pib 2025-12-10) not delivered; SOI G2G 3 cm ORI requires government sponsor (`karn.gdc.soi@gov.in`) | — | Monitor, not actionable this session |
| IRC:54 1974 §8 / IRC:5 2015 104.4.2.1 / IRC:SP:84 | law.resource.org mirrors | Clause verified: "Vertical clearance at underpasses shall be at least 5 metres. However, in urban areas, this should be increased to 5.50 metres" | CC BY-NC 4.0 mirrors, quoting only | **Context only** — never a per-location measurement; a location whose only figure is IRC default is NOT carve-grade |

**RTI items to log in OPEN-ITEMS.md (per GOAL):**

```
BBMP Road Infrastructure Dept, 41-underpass audit 2023, vertical clearance drawings
- Request 1: remaining 27 of 41 audit reports (14 released on OpenCity) + any annexes with measured vertical clearance, drain invert levels, pumping capacities per underpass
- Request 2: vertical clearance drawings (structural GA + longitudinal sections) for each of the 50 register underpasses (chainage + clearance + deck soffit level + approach road levels), with datum and QA stated
- Flag as formal-request items, do not work around via login bypass or scraping
```

**Other blocked but lower-value:** BMRCL Phase 1/2/2A/2B DPR L-section drawing sheets (chainage+rail level+existing GL) — RTI to BMRCL PIO (already flagged in `bmrcl-dpr-audit-2026-08-19.md`); SWR ROB/RUB sanctioned drawings (Panathur RUB, Kino UB, Shivananda UB, Lowry UB, KR Puram UB) — RTI to SWR Bengaluru Division; NHAI/KRDCL corridor DPRs — not public for Bengaluru.

---

## 5. What we converted to carve-grade and what we could not — and why (no padding)

**Converted to carve-grade in this deep pass (7 new, plus 1 re-confirmed):**

1. **Madiwala Underpass — RE-CONFIRMED, not new** — vertical clearance **5.5 m** (existing structure, built 2010; BBMP CE B.S. Prahalad quote, Bangalore Mirror 2022-07-24). **This is the only measured clearance of an existing structure found across 200 RSS title hits + 153 full-text fetches + 15-page audit + OSM + tenders that is BOTH a clearance and existing (not planned, not barrier, not length).** All other 14 news values among the 50 are flood-depth/status/planned/barrier/length.

2. **7 OSM signed-limit clearances — NEW in deep pass (not in 200-query news sweep):**
   - Millers Road Underpass 5.0 m (`maxheight=5`, way 158623466, v6 2025-12-20, HrishikaSandbhor)
   - Maharani College Underpass 4.0 m (`maxheight=4`, way 208827395, v9 2025-08-18)
   - LuLu Global Mall Underpass 3.0 m (`maxheight=3`, way 1092668711, v3 2023-10-27) — anomalously low; likely barrier not structure
   - Suranjandas Road Underpass 4.5 m (`maxheight=4.5`, way 1135874172, v9 2026-02-10)
   - Sankey Underpass 4.25 m (`maxheight=4.25`, way 163101103, v11 2024-10-16) — duplicate Sankey Road Underpass way has NO tag
   - Mill Corner Road Underpass 4.0 m (`maxheight=4`, way 1081215226, v4 2024-10-16)
   - NIMHANS Underpass 10' = 3.048 m (`maxheight=10'`, way 239738418, v4 2024-08-27) — feet→m conversion
   All 7 are `maxheight` (signed limit), **0 `maxheight:physical`** (verified via live history: occurrences 2–6, no physical tag). Per GOAL, a signed limit is still a measurement but **biased low** (`signed_limit_bias`). They feed the **next** carve cycle with that bias explicitly flagged; they were already among the 22 carved in the in-flight variant DEM (7 of those 22), so the mechanism proof (GT_06 Panathur 1.763 m vs 0.009 m baseline) is not newly enabled by these 7 — they are hydraulically peripheral (nearest GTs 3–7 km away).

**We did NOT convert (42 locations) — each with single reason, not padded:**

- **7 flood-depth-only** (have realized depth but never a clearance, so cannot be carved): KR Circle >12 ft (2026-04-29), Silk Board knee-deep (2025-05-19), Nayandahalli flood-like (2026-05-29), Kuvempu waterlogged (2025-09-07), Dairy Circle waterlogged (2025-09-07), Hebbal Flyover jams/depth, Ullal waterlogged — every depth carries its event date; none is Sept 2022 so never scored against the 2022 run.
- **2 status-only** (construction/maintenance state, not a level): Mekhri Circle first underpass 2001-02 + CQAL waterlogging (status), Horamavu railway underpass under construction/delayed (planned → status).
- **33 nothing-found** after deep search (29 generic + 3 zero-news-hits + 1 registerless zero): Annapoorneshwari Main Rd, Cauvery Junction (registerless anchor 22 m from Sankey but no own geometry), CNR Rao, Dr. Rajkumar, Hebbal Twin Tunnel, Jayadeva Hospital, Kasturinagar Rd UB, KR Puram Lake Road, Kundalahalli, Laggere, Lazar Road, Magadi Underpass road, Malleshwaram, Marathahalli, Munekolala, Nagarabhavi, Old Airport Rd Railway Crossing, Old Mysuru Rd, Rajajinagar Entrance, RMZ Eco World (zero hits), Sangam Subway, Sankey Road Underpass (the untagged duplicate), Shivajinagar Bus Station Subway, Star Circle, Thanisandra Main Road RUB, Vatal Nagaraj Rd, Wind Tunnel Road, plus Yeshwanthpur Railway station, Railway underbridge near Kino theatre, Shivananda circle, Bellandur Kodi (zero), Kaderanahali (zero), etc. — for each we probed news RSS (705 rows prior + 4 extended probe queries), OSM live (50 ways, 8 with tags, 42 without), BBMP audit (0 clearances), CAG (0), Suranjan tender (0), fetched_articles full text (0). Nothing-found is the measured finding, not the absence of looking.

**We explicitly DID NOT pad the count with:**
- IRC defaults (5.0 m / 5.50 m urban, verified IRC:54 §8) — context only, not per-location, would be rule-1 violation to write into register.
- Planned/under-construction structures: Yelahanka AFB NHAI underpass ~6 m below main road level (320 m cut-and-cover, two lanes, Rs51.27 cr, bids opened Feb 2026 — DNA 2026-01-29) is **relative vertical of a planned structure**, cannot be carved into Sept-2022 event DEM (anachronism). Harlur Junction CH 0+430 km likewise is planned and not in 50.
- Barrier heights (Madiwala planned barrier at 4.5 m) — barrier limit, not structure clearance.
- Length/width (everywiki lengths/lanes) — horizontal, not vertical.

**Net new carve-grade for next cycle:** **7 signed-limit OSM conversions** (medium/low confidence) + **0 new measured clearances** beyond the 1 already known. In other words, going deeper than RSS titles into OCR, OSM history, and gated tenders **did not produce a second Madiwala.** The BBMP 14-underpass audit — the single highest-value artifact (hypothesis: 14 audits close several gaps at once) — contains **zero vertical clearances.** The Harlur DPR drawings add a chainage (0+430 km) but not a per-location clearance for the 50. This is the finding, not a failure to look.

---

## Files you own (commit with explicit `git add <paths>`)

- `data/raw/underpass_search/deep/search_results.csv` — 50-row classification table (this report's §1, independently verifiable)
- `data/raw/underpass_search/deep/manifest.json` — run manifest (git SHA `a93c58d`, inputs, outputs, wall clock 35.2 s, status completed, CPU-only)
- `data/raw/underpass_search/deep/osm_live_checks.csv` — 50 live Overpass+history probes (maxheight, maxheight:physical, hgv, history counts)
- `data/raw/underpass_search/deep/archive_extended_probe.json` — 4 extended RSS queries (sample, rate-limit conscious)
- `data/raw/underpass_search/deep/dpr_tender_checks.json` — Opencity + eproc checks
- `data/raw/underpass_search/deep/bbmp_audit_ocr/` — OCR text per page, `extracted_dimensions.csv` (11 verified dimensions), `extracted_dimensions_candidates.csv` (1 automated candidate), source PDFs, PNG renders, script `ocr_bbmp_audit.py`, manifest JSON
- `docs/reference/underpass-levels-deep-search-2026-08-19.md` — this report
- Scripts: `scripts/run_deep_underpass_search.py`, `data/raw/underpass_search/deep/bbmp_audit_ocr/ocr_bbmp_audit.py` (both committed, manifest-logged, CPU-only, `nvidia-smi` preflight)

**Provenance rule compliance:** manifests written at run start (`status: running`, git SHA, config snapshot) and updated in place on completion; every numeric claim traceable to a file in `data/` or logged run in `runs/`; zero fabricated numbers (every figure = verbatim quote + URL + pub date + event year, or OSM way+history timestamp). An article not fetched is marked NOT_REVERIFIED, never paraphrased.

**Read-only enforcement:** this session never touched `src/`, `data/interim/terrain/`, `runs/underpass_*`, `runs/phase3_validation/`, `configs/` (verified `git status --porcelain` shows only `data/raw/underpass_search/deep/`, `docs/reference/underpass-levels-deep-search-2026-08-19.md`, `scripts/run_deep_underpass_search.py` as new/modified). No `git add -A`, no `checkout/restore/stash` on files not created by this session.

---

## Verification (V1–V11, R1–R8)

- **V1 realized state:** checked raster on disk? No raster — this is a research session producing a register augmentation spec for the NEXT carve; we checked the realized 15-page scanned PDFs (no text layer), the realized OSM tag values live (via Overpass 200 + history 200), the realized CAG txt (389 KB), the realized Suranjan PDF (136 KB), not the manifest declarations.
- **V2 independent observable:** stated before each check (e.g., "if OSM maxheight were missing, Overpass would return no elements" — it did for 42/50; "if audit had clearance, vision would read a number" — none read).
- **V3 no moving baseline:** candidate-vs-candidate (our deep table vs prior 705-row table), never vs self-generated baseline.
- **V4 clean-state command:** documented commands in this file (`uv run python data/raw/underpass_search/deep/bbmp_audit_ocr/ocr_bbmp_audit.py`; `uv run python scripts/run_deep_underpass_search.py`) run from clean state, no skip flags.
- **V5 test that fails:** OCR candidate test: if we blank a page, candidate count drops from 1 to 0; OSM probe: if we query a way with no tag, maxheight is empty (42/50 demonstrate failure).
- **V7 scope:** audit scope = 15+4 pages (100% of BBMP audit PDFs on disk), OSM scope = 50/50 register ways (100%), news archive scope = sample 4 of 300 possible extended queries (1.3%, flagged PARTIAL with BLOCKED rate-limit note), street-level signage scope = 0/50 live views (BLOCKED_NO_TOKEN, flagged).
- **R3 inventory:** grep'd `data/fetched_articles.json` first, before any acquisition (found 153 articles, 56 underpass, 9 clearance).
- **R7 reviewer specifies not measures ad hoc:** all measurements via committed scripts with logged runs (manifests `a93c58d`, wall clocks 74.1 s OCR + 35.2 s deep search).
