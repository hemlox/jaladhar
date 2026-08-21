# Academic audit: field-surveyed per-location underpass levels/depths for Bengaluru

**Date:** 2026-08-19. **Scope:** find academic theses and papers carrying FIELD-SURVEYED
per-location underpass road levels, vertical clearances, sump depths, or flood depths for
named Bengaluru underpass locations. Research only; no terrain/solver/runs touched.

**Headline result:** **No academic thesis or paper with per-location field-surveyed
underpass levels for Bengaluru was found.** What exists is (a) papers that name flood-prone
localities or validate models against *recorded* depths without printing the per-location
values, and (b) a set of restricted full-text theses whose abstracts do not state whether
field-surveyed levels are inside. Every item below is labelled per CLAUDE.md
rules (V1, V9): findings are what I measured/fetched with a logged run; readings are my
inference; blocked items name the exact gate that stopped me.

Evidence base: 24 Google News RSS queries (442 results), 19 Crossref queries (285 rows),
50 per-location-name Crossref queries (300 rows), 6+ OpenAlex search sweeps, Shodhganga
metadata for 8 theses, IISc eprints via Wayback, and direct HTTP probes of 31 endpoints.
All URLs probed with status codes are in
[`academic_manifest.json`](../../data/raw/underpass_search/academic_manifest.json).
Search queries, results and PDF text extractions were saved under `/tmp/opencode/academic/`
during this session (not in the repo).

## 1. Per-paper / per-thesis table

| # | Paper / thesis | Year | Inst. | URL | Carries per-loc level? | Verbatim evidence | Status |
|---|---|---|---|---|---|---|---|
| 1 | Ramachandra & Mujumdar, "Urban Floods: Case Study of Bangalore", Disaster & Development 3(2) | 2009 | IISc | [eprints.iisc.ac.in/43235](http://eprints.iisc.ac.in/43235/1/DandD_Special_Issue_3-2_2009.pdf) (403 direct; [Wayback 2024-09-14](https://web.archive.org/web/20240914190713id_/http://eprints.iisc.ac.in/43235/1/DandD_Special_Issue_3-2_2009.pdf)) | **NO** | Table 6 "Low-lying and flood-prone regions" names localities only — incl. Binny Mill Tank Area, Miller Tank, Kamakshipalya Tank Slum Area, Krishnappa Garden behind Byrasandra Tank D/s, Sampangiram Nagar, City Market Area. Plate 4.2 caption: "SUBWAY BETWEEN MAGESTIC AND RAILWAY STATION RISING WATER LEVEL AND VEHICLES TRAFFIC JAM TO HEAVY RAINS" — anecdotal, no value. | **[FINDING]** fetched full 98 pp; location names overlap register anchors (Sangam Subway, Millers Rd, Laggere, Dairy Circle, Malleshwaram/Star Circle) but **no levels/depths anywhere** |
| 2 | Mujumdar et al., "Development of an urban flood model for Bengaluru city, Karnataka, India", Current Science 120(9):1441–1448 | 2021 | ICWaR-IISc + KSNDMC | [currentscience.ac.in/Volumes/120/09/1441.pdf](https://www.currentscience.ac.in/Volumes/120/09/1441.pdf) (open) | **NO values printed** | "The model-simulated inundation depths at the flood vulnerable areas (Figure 4b) were validated with the recorded depths at the respective locations for these two events" (7–8 May 2019; 25–26 Jun 2020). "The telemetric WLSs are set up on the storm water drains (SWDs) at various significant locations based on the identified flood-vulnerable areas" — 15-min resolution. | **[FINDING]** recorded validation depths EXIST in KSNDMC WLS telemetry (project item R40, already flagged) but are not published per-location; no underpass named |
| 3 | Prasad & Narayanan, "Vulnerability assessment of flood-affected locations of Bangalore by using multi-criteria evaluation", Annals of GIS 22(2) | 2016 | Annals of GIS (T&F) | [doi.org/10.1080/19475683.2016.1144649](https://doi.org/10.1080/19475683.2016.1144649) | **UNKNOWN** | Abstract: "around 280 locations are vulnerable to flash floods in the Bangalore city of India"; "under-potential culverts" cited as a cause. GIS multi-criteria evaluation, map-based. | **[BLOCKED]** full text Cloudflare-gated (T&F 403 from this IP); whether the 280-location appendix carries levels unknown. Same author as thesis #8 |
| 4 | Meena Y R, PhD thesis "Applications of Geo Spatial Modeling Techniques for Urban Storm Water Flood Control of Bengaluru City Using Best Management Practices" | 2020 | VTU Belagavi | [shodhganga 10603/457950](https://shodhganga.inflibnet.ac.in/handle/10603/457950) | **UNKNOWN** | Abstract: "UFVZ map was compared with the reported flood affected areas (28 numbers) existing in Vrishabhavathi valley watershed"; 57% (16/28) co-location. "Reported" = secondary data, not stated as field survey. | **[BLOCKED]** full text login-gated (bitstream 302 → pdfToThesis.jsp) |
| 5 | Jagadeesh C B, PhD thesis "Urban flooding: Problem and feasible solution for flooding at Gali Anjaneya Temple, Bangalore city" | 2017 | Jain University | [shodhganga 10603/204295](https://shodhganga.inflibnet.ac.in/handle/10603/204295) | **UNKNOWN, HIGH POTENTIAL** | Abstract: "identification of sustainable flood mitigation policies and measures in Vrishabhavathi River Catchment on the upstream side of Gali Anjaneya Temple, Bengaluru". 257 pp, 9 chapters (ch.6 3.78 MB, ch.7 2.12 MB). | **[BLOCKED]** full text login-gated; **highest-value thesis candidate** — site-specific flood study in a Bengaluru catchment, most likely of the set to contain field-collected site data |
| 6 | Bharadwaja A S, PhD thesis "Bangalore city flood risk assessment and management using remote sensing and GIS" | 2014 | Kuvempu University | [shodhganga 10603/87674](https://shodhganga.inflibnet.ac.in/handle/10603/87674) | **UNKNOWN** | Abstract not exposed in metadata page ("Abstract available" only). 268 pp. | **[BLOCKED]** full text login-gated |
| 7 | Naidu B N K, PhD thesis "Flood sensitive guidelines for inland cities: A case of Bengaluru" | 2023 | SPA Vijayawada | [shodhganga 10603/614000](https://shodhganga.inflibnet.ac.in/handle/10603/614000) | **UNKNOWN** | Abstract not exposed. | **[BLOCKED]** full text login-gated |
| 8 | Ramaprasad N N, PhD thesis "Evaluating the Role of Lakes in Mitigating the urban Floods of Bengaluru Metropolitan City Applying Geo Spatial Techniques" | 2022 | Central Univ. of Karnataka | [shodhganga 10603/461452](https://shodhganga.inflibnet.ac.in/handle/10603/461452) | **UNKNOWN** | Abstract not exposed. Author = Neeraj Prasad of paper #3. | **[BLOCKED]** full text login-gated |
| 9 | Sowmya D R, PhD thesis "Remote sensing satellite image processing for urban flood detection" | 2020 | Bangalore University | [shodhganga 10603/366363](https://shodhganga.inflibnet.ac.in/handle/10603/366363) | **UNKNOWN** | Computer-science thesis (image processing), not a field survey. | **[BLOCKED]** full text login-gated; low relevance |
| 10 | Rinisha Kartheeshwari M, PhD thesis "Integrated modelling of surface and groundwater systems of an urban catchment…flood mitigation" | 2026 | Anna University | [shodhganga 10603/713703](https://shodhganga.inflibnet.ac.in/handle/10603/713703) | **UNKNOWN** | Integrated surface–groundwater modelling for flood mitigation (Bengaluru catchment). | **[BLOCKED]** full text login-gated |
| 11 | "Underpass Water Logging System Using IOT and ML", IJSREM | 2024 | IJSREM | [doi.org/10.55041/ijsrem33359](https://doi.org/10.55041/ijsrem33359) | **NO** | Abstract: generic IoT sensor framework to "continuously monitor the water levels within underpasses". No Bengaluru site, no named underpass, no measured values. | **[READING]** generic design paper; article body JS-gated on ijsrem.com |
| 12 | "Development of Hydrological Criteria for the Hydraulic Design of Stormwater Pumping Stations", Water 17 | 2025 | MDPI | [doi.org/10.3390/w17203007](https://doi.org/10.3390/w17203007) | **NO** | General pumping-station sizing criteria; not Bengaluru-specific. | **[READING]** context only, no per-location data |

**FALSE LEAD (read the abstract before trusting the search hit):** Prasad Rentachintala L R N,
PhD thesis "Sustainable and Resilient Integrated Urban Stormwater Management: a Case Study"
(2022, Andhra University, [shodhganga 10603/458080](https://shodhganga.inflibnet.ac.in/handle/10603/458080))
surfaced in every Bengaluru-stormwater query, but its abstract states the case study is
"proposed **Amaravati city of Andhra Pradesh**". **Not a Bengaluru source — do not cite as one.**

## 2. Non-academic leads worth pursuing (news of measured incidents, from Google News RSS)

These are NOT academic sources; they are named here because they report measured/photographed
flooding at the exact register locations, and the academic trail above yields nothing per-location.
All article bodies are **[BLOCKED]** — Google News RSS served titles/outlets/dates but the
redirect URLs are JS-encoded and were not decodable from this IP (no headless browser).

| Outlet | Date | Title (verbatim from RSS) | Register relevance |
|---|---|---|---|
| The Hindu | 2023-05-24 | "Audit report of 13 underpasses in Bengaluru reveals 3 vulnerable to flooding" | BBMP audit of 13 underpasses — the single most direct route to a per-underpass vulnerability dataset |
| The Hindu | 2023-06-06 | "Explained \| Why underpasses in Bengaluru get flooded in monsoon" | mechanism explainer (pumping/sump design) |
| Hindustan Times | 2022 | "Bengaluru rain: Five dangerous underpasses to avoid during showers in city. Full details" | names 5 flood-prone underpasses |
| The Hindu | 2026-04-30 | "Bengaluru rains: Three years after fatal flooding, K.R. Circle underpass submerged again" | KR Circle (register row) recurrence |
| The Indian Express | 2026 | "After techie's death in Bengaluru, BBMP to close faulty underpasses for repair; audit underway" | BBMP audit underway |
| Times of India | — | "Rs9cr Flood Project Fails First Big Test As Narendra Nagar Underpass Flooded Again" / "Rs9cr pump & sump system keeps Narendra Nagar underpass flood-free…" | pump+sump flood project — [READING] city unverified from title alone; verify before use |

## 3. What carries a NUMBER today (measured, not academic)

- **KSNDMC water-level sensors** (R40): 15-min WLS telemetry on storm-water drains at
  flood-vulnerable locations (confirmed by Current Science paper #2). This is the only
  live measured per-location depth stream known; access is gated (formal request draft
  exists in `docs/archive/ksndmc_data_request_DRAFT.md`).
- **Groundtruth Sept 2022** (`data/raw/groundtruth/sept2022_points.csv`): per-location
  depth BANDS from photographic cues (car-bonnet/waist/chest) at 24 points incl. GT_05
  Silk Board 0.35–0.5 m, GT_06 Panathur 1.2–1.45 m, GT_15 Hebbal 0.35–0.5 m,
  GT_16 HAL airport road 0.5–0.7 m, GT_21 KR Puram 0.5–0.7 m. News-sourced, not academic.
- **No academic source found adds a measured per-underpass level or clearance.**
  Per CLAUDE.md Rule 1 (no fabricated data) and the register rule (a band, not a number,
  until a source carries a realized level), the register stays unpopulated by this audit.

## 4. Dead ends with reasons

| Attempt | Result | Reason |
|---|---|---|
| Google Scholar | 429 on every probe | rate-limited from this IP |
| Semantic Scholar API | 429 / timeouts | rate-limited; intermittent success, no Indian thesis coverage in the responses that did return |
| CORE API | 429 / timeouts | rate-limited without API key |
| IISc eprints PDFs (eprints.iisc.ac.in) | 403 | server blocks this IP; recovered one PDF (2009 paper) via Wayback snapshot 2024-09-14 |
| Shodhganga full-text bitstreams | 302 → `pdfToThesis.jsp` | all 8 thesis downloads login-gated (INFLIBNET account/campus required); metadata pages open |
| NITK ethesis search | 404 / timeouts | DSpace search endpoint unreachable this session |
| IISc ETD (etd.iisc.ac.in) | slow/timeouts; REST `query` param ignored | server issues; open-search returned unrelated thesis list |
| T&F Annals of GIS full text | 403 Cloudflare "Just a moment…" | bot wall |
| Google HTML search | 200 but JS-only shell, zero SERP links | served a consent/JS variant, not results |
| r.jina.ai reader proxy | 403 | proxy blocked |
| DuckDuckGo / Bing | — | stated rate-limited/geo-broken from this IP (task brief); not relied upon |
| IJERT paper page | 200 but site is a JS SPA; old paper pages redirect to "Call for Papers" | paper PDF not retrievable |
| IJSREM article page | 200 but JS-rendered; no API exposed | content not retrievable |
| Google News RSS article URLs | redirects stay inside news.google.com | URLs are JS-encoded (protobuf); undecodable without a browser |

## 5. Failed search terms (queried, zero or non-relevant results)

Per-location-name Crossref searches (quoted, 50 locations — all returned dictionary
entries, Indonesian underpass-construction papers, or unrelated: e.g. "underpass, n.",
"KAJIAN TINGKAT RISIKO…", "STUDI KELAYAKAN UNDERPASS CANGUK"). Crossref query set (19):
Bengaluru underpass flood; Bengaluru subway flood depth; Bengaluru road underpass stormwater;
Bengaluru urban flooding depth measurement; Bengaluru railway underpass hydrology; Bangalore
underpass flood study; Bengaluru low lying areas flooding road; Bengaluru urban floods 2022
depth; Bangalore stormwater underpass pumping; Panathur underpass; Silk Board underpass;
Hebbal underpass; KR Puram underpass; Varthur Kodi; Maharani College underpass; Bengaluru
flood inundation depth validation; Bengaluru flash flood hydraulic modelling; Bangalore
subways flooded rainfall; Bengaluru underpass road level elevation. OpenAlex sweeps (10+):
underpass flood Bengaluru; underpass water logging Bangalore; railway underpass flood India
case study; urban underpass flooding depth measurement India; subway flooding Bangalore case
study; Bengaluru underpass flood depth; Bangalore urban flood field survey; Bengaluru subway
flood; + 6 author/derivative queries. Google News RSS (24 queries, 442 results) — news
coverage only, no academic hits. **No query returned a paper with a measured per-location
underpass level.**

## 6. Recommendation: highest-value next step

1. **Jagadeesh C B (2017), Jain University PhD thesis** (Shodhganga 10603/204295) — a
   site-specific flood study in the Vrishabhavathi catchment (Gali Anjaneya Temple). Of the
   eight restricted theses it is the most likely to contain field-collected site data
   (levels/water marks) at named locations. Unblock: an INFLIBNET Shodhganga account
   (free registration) or a Jain University library request; alternatively contact the
   guide (Nagaraj Sitaram) or the author.
2. **BBMP 13-underpass audit** (The Hindu 2023-05-24) — a public-authority audit of exactly
   our object class; obtainable via the article body (decode the Google News URL from a
   browser session / Indian IP) or via RTI to BBMP.
3. **KSNDMC WLS history** — the only live measured per-location depth stream; the Current
   Science paper (#2) independently confirms recorded validation depths exist in it. The
   existing draft request (`docs/archive/ksndmc_data_request_DRAFT.md`) should be sent.

**Axes still open on every item above (V11):** clearance vs. road-surface level vs. flood
depth are three different quantities; no academic source found settles any of them at a named
underpass. The blocked items close only the "does a paper exist" axis; the "does it carry a
realized number" axis is untested until the gates open.