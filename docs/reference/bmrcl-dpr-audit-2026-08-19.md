# BMRCL & road-authority DPR audit for per-location vertical levels

**Date:** 2026-08-19. **Auditor:** implementation agent (research only).
**Audits:** item AO continuation — per-location vertical levels (rail level, ground level, clearance, chainage) along flood corridors, from DPRs and engineering documents.
**Companion to:** [`underpass-sources-audit-2026-08-18.md`](underpass-sources-audit-2026-08-18.md) (component audit, R38 = Phase 3 exec summary rail level ≥ 7.50 m) and `data/raw/underpass_search/bmrcl_dpr_notes.csv` + `bmrcl_dpr_manifest.json` (this pass).

Label legend: **[FINDING]** = measured from a fetched document (URL + quoted value). **[BLOCKED]** = document exists or should exist but is not public/scriptable; exact request path given. **[READING]** = interpretation, not measured.

## 0. Method

The BMRCL website (`bmrc.co.in`) renders its DPR list through a JS app. The list is served by an unauthenticated JSON API that the site itself calls:

```
GET https://www.bmrc.co.in:8282/api/users/projects/togetDPRProjectProgress
Authorization: Bearer 7a3ac55ef3482d34682eb75d52b44f44
```

This returned the **complete BMRCL DPR catalogue** (10 documents). Files are served from
`https://www.bmrc.co.in:8282/English/uploads/projectprogress/english/<path>` and the environment-document catalogue from `/api/users/env/togetEnviroments` (34 EIA/ESIA volumes + misc). All 10 DPR PDFs were downloaded and text-extracted; 6 are text-searchable, 4 are pure raster scans. Two EIA volumes (2A vol 1, 2B vol 1) were downloaded and checked. URLs and HTTP statuses are in the manifest.

## 1. Per-document table

| # | Document | Authority | Public URL | Vertical data present | Per-location values quoted |
|---|---|---|---|---|---|
| 1 | Phase-1 DPR (May 2003) | BMRCL | `https://www.bmrc.co.in:8282/English/uploads/projectprogress/english/fileuploads/BMRCL%20DPR%20May%202003%201.pdf` | unknown — **raster scan, 0/500 pages have a text layer** | none extractable without OCR |
| 2 | Phase 2 DPR — Four Extensions | BMRCL | `.../FileUploads/c82a1969-bdad-432c-b3d9-96dab67aec96$@!!@$Phase%202%20DPR%20Four%20Extensions.pdf` | unknown — **raster scan, 0/432 pages** | none extractable without OCR |
| 3 | Phase 2 DPR — RV Road to Bommasandra | BMRCL | `.../FileUploads/dc7abb52-428c-40d2-8241-4f651860f08c$@!!@$Phase%202%20DPR%20R%20V%20ROAD%20TO%20BOMMASANDRA%20CORRIDOR.pdf` | unknown — **raster scan, 0/291 pages** | none extractable without OCR |
| 4 | Phase 2 DPR — Gottigere–IIMB–Nagavara | BMRCL | `.../FileUploads/dbd7e46e-7343-4581-9d70-b191eecae306$@!!@$Phase%202%20DPR%20Gottigere-IIMB-Nagavara%20Corridor.pdf` | unknown — **raster scan, 0/383 pages** | none extractable without OCR |
| 5 | Phase 2A DPR Executive Summary | BMRCL/RITES | `.../FileUploads/48e8b4e1-3436-4d0a-9d4e-b2642758ed73$@!!@$Phase-2A%20DPR%20Executive%20Summary.pdf` | chainage only (Table 0.3, 13 stations) | Silk Board `+413.840`, Bellandur `+7165.144`, Marathahalli `+11416.461`, KR Puram `+17133.392` m |
| 6 | Phase 2B DPR Executive Summary | BMRCL/RITES | `.../FileUploads/7c4b3963-1d6c-4610-a6be-2b8a4a23ac43$@!!@$Phase-2B%20DPR%20Executive%20Summary.pdf` | chainage only | chainage table, no levels |
| 7 | **Phase 2A DPR (full)** — Central Silk Board → KR Puram (ORR) | BMRCL/RITES | `.../FileUploads/e9a9bfe2-cca0-4d4b-a72b-93f50cab97b3$@!!@$Phase-2A%20DPR.pdf` | **partial** — gradient statement + vertical-curve geometry + station chainages + underpass inventory; **no RL values in text** | rail level ~13.5 m above road (design std, §5.3.4); Table 5.3 vertical curves have chainage/grade/radius but **no elevation column**; Table 2.4 lists 59 BBMP/BDA/NHAI flyovers & underpasses incl. `Madiwala junction on Hosur road`, `Kadirenhalli on ORR`, `Marathahalli on ORR`, `ROB at K.R. Puram` (no levels) |
| 8 | **Phase 2B DPR (full)** — KR Puram → KIA | BMRCL/RITES | `.../FileUploads/54221702-c7a4-468e-96d6-9a09e55d8d52$@!!@$PHASE-2B-RDPR.pdf` | **YES — the single best rail-level dataset found** | see §2 |
| 9 | **Phase 3 DPR Executive Summary** | BMRCL/RITES | `.../fileuploads/1731666580085$@!!Executive%20summary%20-%20Phase%203.pdf` | **YES — station-level GL/RL table** | see §3 |
| 10 | Phase 3 Double Decker FSR cum DPR Exec Summary | BMRCL | `.../fileuploads/1762228738238$@!!Executive%20Summary%20of%20FSR%20cum%20DPR%20of%20Phase-3%20Double%20Decker.pdf` | chainage only (ramps/loops, flyover spans) | flyover CH `0+631…29+117` (JP Nagar–Hebbal), `1+040…5+120`, `6+400…10+955` (Magadi Rd–Kadabagere); no levels |
| 11 | EIA Phase 2A (18 vols) + Phase 2B (16 vols) | BMRCL | `https://www.bmrc.co.in:8282/English/uploads/environment/english/FileUploads/<uuid>…` (from `/api/users/env/togetEnviroments`) | none — design standards only | "vertical clearance of 5.50m above road level … rail level … generally 13.5 m above the road level" (2A EIA §55; 2B EIA §46); KIA underground section 2B |
| 12 | Parivesh EC filings for BMRCL | MoEFCC | `https://parivesh.nic.in/` | **[BLOCKED]** SPA, no scriptable search; metro rail is **exempt from EC** by the EIA Notification Schedule (quoted in Phase 2A exec summary EC section) | — |
| 13 | AIIB "Bangalore Metro Rail Project – Line R6" | AIIB | `https://www.aiib.org/en/projects/list/index.html` | **[BLOCKED]** JS SPA; project URLs redirect to generic list; no API found | — |
| 14 | JICA loan documents (Phase 2 2017, Phase 3 2026) | JICA | `https://www.jica.go.jp` | **[BLOCKED]** no project-doc deep link found; ex-ante evaluations carry route-level, not per-location, data | — |
| 15 | NHAI Bengaluru corridor DPRs (NH-44/75, ORR, Silk Board, Hebbal, KR Puram) | NHAI/MoRTH | various | **[BLOCKED]** no public DPR PDF located (0 Google-News hits for two targeted queries); IRC:SP:84 / realized VUP 5.5 m clearance already on record (prior audit R34) | — |
| 16 | KRDCL/PWD/BBMP corridor DPRs (Varthur elevated, ORR facelift, tunnel roads) | KRDCL/PWD/BBMP | KRDCL/BBMP portals | **[BLOCKED]** not public; news-only (Varthur elevated Dec-2026 deadline; ORR facelift draft DPR 10-lane; KR Puram–Nayandahalli & Hebbal–Silk Board tunnel-road DPRs under controversy, not released) | — |
| 17 | SWR ROB/RUB sanctioned drawings (Panathur RUB, Kino UB, Shivananda UB, Lowry UB, KR Puram railway UB) | SWR | — | **[BLOCKED]** drawings not public; Panathur RUB under construction, Lowry Under Bridge closed (news-verified) | — |
| 18 | BMRCL station/viaduct profile docs (rail level ≥ 7.50 m) | BMRCL | = Phase 3 exec summary (rows 9) | re-confirmed, **no new per-location figures beyond Table 0.7 standard + Table 0.19** | Table 0.7 (below) |

## 2. [FINDING] Phase 2B DPR — Statement of Vertical Curves (Table 5.4): rail level at 77 chainages

**Document:** Phase 2B DPR (full), RITES, Chapter 5 (Civil Engineering – Alignment Details), PDF pp. 5-17/5-18 of 5-42.
**URL:** `https://www.bmrc.co.in:8282/English/uploads/projectprogress/english/FileUploads/54221702-c7a4-468e-96d6-9a09e55d8d52$@!!@$PHASE-2B-RDPR.pdf`

This table lists every vertical curve with **PVI station (chainage, m)** and **PVI elevation (m)** — i.e. the proposed **rail (deck) level** along the KR Puram → KIA corridor, which follows the ORR median from Kasturi Nagar to Hebbal and NH-44 thereafter (route description §5.1.1). 77 rows, chainage 312.860 m → 36417.950 m. Selected verbatim rows (No. / Start CH / End CH / PVI CH / **PVI Elevation (m)** / grade in / grade out / type / length / radius):

| No. | Start CH | End CH | PVI CH | **PVI Elev (m)** | Grade in | Grade out | Type | Curve len (m) | Radius (m) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | — | — | 312.860 | **916.073** | — | −1.71% | — | — | — |
| 2 | 602.330 | 637.671 | 620.000 | **910.812** | −1.71% | +0.63% | Sag | 35.341 | 1510 |
| 4 | 1490.177 | 1541.924 | 1516.050 | **927.680** | +3.43% | 0.00% | Crest | 51.747 | 1510 |
| 12 | 4090.179 | 4132.841 | 4111.510 | **911.479** | −2.83% | 0.00% | Sag | 42.662 | 1510 |
| 18 | 5868.661 | 5904.300 | 5886.480 | **920.112** | −0.54% | −2.91% | Crest | 35.639 | 1510 |
| 31 | 10578.067 | 10616.293 | 10597.180 | **901.609** | −0.47% | +1.06% | Sag | 38.226 | 2500 |
| 33 | 11138.311 | 11159.409 | 11148.860 | **902.796** | −0.33% | 0.00% | Sag | 21.098 | 6500 |
| 34 | 11337.880 | 11387.840 | 11362.860 | **902.788** | 0.00% | +3.31% | Sag | 49.960 | 1510 |
| 35 | 12223.333 | 12287.487 | 12255.410 | **932.304** | +3.31% | −0.94% | Crest | 64.154 | 1510 |
| 55 | 22207.889 | 22285.271 | 22246.580 | **915.218** | −2.15% | +2.97% | Sag | 77.382 | 1510 |
| 61 | 26966.993 | 26993.007 | 26980.000 | **911.475** | −1.70% | −0.66% | Sag | 26.014 | 2500 |
| 66 | 29743.148 | 29817.233 | 29780.190 | **929.460** | −3.75% | +1.16% | Sag | 74.085 | 1510 |
| 72 | 34019.379 | 34055.321 | 34037.350 | **911.855** | 0.00% | −2.38% | Crest | 35.942 | 1510 |
| 76 | 35399.876 | 35451.884 | 35425.880 | **893.394** | −3.45% | 0.00% | Sag | 52.008 | 1510 |
| 77 | 36417.950 | 36417.950 | 36417.950 | **893.394** | 0.00% | Level | — | — | — |

**Chainage → place mapping (Table 6.1 station chainages):** KR Puram 0.000 (Phase 2B datum, 80 m beyond ORP 658), Kasturi Nagar 1600.160, Horamavu 2751.700, HRBR Layout 4201.230, Kalyan Nagar 5303.820, HBR Layout 6560.920, Nagawara 7508.790, Veeranna Palya 8314.000, Kempapura 9964.250, **Hebbal 11223.430**, Kodigehalli 12699.060, Jakkur Cross 14120.040, Yelahanka 17842.930, Bagalur Cross 20022.280, Bettahalasuru 23826.472, Doddajala 28736.530, Airport City 33705.170, KIA Terminals 36267.250.

**[READING]** Rail level at flood-relevant locations on this corridor, read from the grade sequence (level sections carry the PVI elevation): **Hebbal station** (CH 11223.430) lies in the level section between curves 33/34 → **rail level 902.788–902.796 m**; **Horamavu station** (CH 2751.700) lies in the level section after curve 8 → **rail level 938.680 m**; **Kasturi Nagar** (CH 1600.160) lies in the level section after curve 4 → **rail level 927.680 m**. These are the **metro deck levels**, not road-surface levels under an underpass; they bound clearance from above but do not resolve road surface elevation (that is still the DEM/validation gap, item AO).

## 3. [FINDING] Phase 3 DPR Executive Summary — station GL/RL table (Table 0.19) and rail-height standard (Table 0.7)

**URL:** `https://www.bmrc.co.in:8282/English/uploads/projectprogress/english/fileuploads/1731666580085$@!!Executive%20summary%20-%20Phase%203.pdf` (re-fetched over the site's valid HTTPS; prior audit R38 fetched the same file with `curl -sk` over expired TLS).

**Table 0.19 — LIST OF STATIONS**, per-station **Chainage (m)**, **Existing Ground Level GL (m)**, **Proposed Rail Level RL (m)**. All 31 stations, verbatim (reconstructed by coordinate-ordered extraction):

*Corridor 1: JP Nagar 4th Phase → Kempapura*
| SN | Station | Chainage (m) | GL (m) | RL (m) |
|---|---|---|---|---|
| 1 | JP Nagar 4th Phase | 42 | 900.018 | 913.184 |
| 2 | JP Nagar 5th Phase | 1789 | 900.354 | 917.500 |
| 3 | JP Nagar | 3089 | 900.698 | 924.500 |
| 4 | Kadirenahalli | 4261 | 903.464 | 918.000 |
| 5 | Kamakya Junction | 6247 | 867.670 | 897.500 |
| 6 | Hosakerehalli | 7472 | 872.062 | 892.000 |
| 7 | Dwaraka Nagar | 8658 | 838.247 | 860.000 |
| 8 | Mysore Road | 10162 | 813.002 | 839.500 |
| 9 | Nagarbhavi Circle | 11651 | 844.815 | 858.500 |
| 10 | Vinayaka Layout | 13091 | 839.528 | 858.000 |
| 11 | Papireddy Palya | 14381 | 867.982 | 882.000 |
| 12 | BDA Complex Nagarbhavi | 15711 | 888.577 | 904.000 |
| 13 | Sumanahalli Cross | 17290 | 858.940 | 879.000 |
| 14 | Chowdeshwari Nagar | 18576 | 863.616 | 878.000 |
| 15 | Freedom Fighter's Colony | 19675 | 883.368 | 903.000 |
| 16 | Kanteerva Nagar | 21067 | 896.720 | 910.000 |
| 17 | Peenya | 22797 | 910.787 | 940.000 |
| 18 | Muthyala Nagar | 25064 | 909.976 | 924.000 |
| 19 | Bel Circle | 26302 | 904.323 | 924.000 |
| 20 | Nagashetty Halli | 27722 | 897.781 | 911.000 |
| 21 | Hebbal Railway Station | 30149 | 894.316 | 907.500 |
| 22 | Kempapura | 31581 | 888.126 | 902.500 |

*Corridor 2: Hosahalli → Kadabagere*
| SN | Station | Chainage (m) | GL (m) | RL (m) |
|---|---|---|---|---|
| 1 | Hosahalli | 0 | 877.172 | 896.500 |
| 2 | KHB Colony | 1648 | 895.114 | 909.000 |
| 3 | Kamakshipalya | 2971 | 880.003 | 900.000 |
| 4 | Sumanahalli Cross | 3907 | 858.985 | 886.500 |
| 5 | Sunkadakatte | 5660 | 906.949 | 920.000 |
| 6 | Herohalli | 6981 | 883.399 | 897.500 |
| 7 | Byadarahalli | 8562 | 868.691 | 881.500 |
| 8 | Kamath Layout | 10006 | 874.050 | 887.000 |
| 9 | Kadabagere | 11590 | 867.254 | 880.000 |

**Table 0.7 — TRACK CENTRE AND HEIGHT IN ELEVATED SECTION** (verbatim): "Mid Section — Minimum Track Centre **4.20 m\***, Minimum Rail Level above Ground Level **7.50 m\*\***"; "Station — 4.20 m / **12.50 m**". Notes: "*Track centre … for Double U girder minimum 4.85 m…"; "\*\*For I girder and Box girder, Minimum Rail Level above Ground Level shall be **8.50 m**".

## 4. Design-standard statements (all corridors)

- **Phase 2A** (§5.3.4 and EIA §55): "Track supporting structures on elevated sections are to permit a vertical clearance of **5.50 m** above road level … the rail level is planned to be generally **13.5 m above the road level**."
- **Phase 2B** (station planning, EIA §46): "…rail level … about **14.50 meters above the ground level**" at elevated stations.
- These are standards, not per-location values (Rule 1). They do **not** enter the underpass register.

## 5. Failed search terms and dead ends

| Search / probe | Result | Reason |
|---|---|---|
| `BMRCL Detailed Project Report`, `BMRCL Phase 3 DPR PDF`, `BMRCL environmental clearance`, `BMRCL DPR PDF`, `Bangalore Metro EIA` (Google News RSS) | news articles only, no DPR PDFs | DPR PDFs are not linked in news; found instead via BMRCL's own JSON API |
| `JICA Bangalore Metro airport line`, `BMRCL Phase 2B environmental clearance Parivesh`, `Parivesh BMRCL environmental clearance letter` | 0 results | JICA doesn't publish PDFs in feeds; metro rail exempt from EC |
| `NHAI Bengaluru elevated corridor DPR PDF`, `NHAI NH-44 Bengaluru flyover DPR PDF` | 0 results | road DPRs not public for Bengaluru |
| `KRDCL Bengaluru elevated corridor` | news only (Varthur elevated, tender cancellation) | DPR not released |
| `Silk Board junction flyover DPR`, `Hebbal flyover grade separator DPR`, `KR Puram elevated corridor NHAI DPR` | news only | DPRs not public |
| `Panathur railway underpass`, `Kino theatre railway underbridge`, `BBMP underpass DPR Bengaluru PDF` | news only (Panathur RUB construction, Lowry UB closure) | SWR/BMRCL sanctioned-plan drawings not public |
| parivesh.nic.in API probes (5 URL patterns) | all 404 | JS SPA, no scriptable search endpoint |
| AIIB project-page URL guesses (6 variants) | all return generic SPA shell | AIIB site is JS-gated |
| Phase 1 / Four Extensions / Gottigere / RV Road DPR text extraction | 0 text pages in all four | pure raster scans, OCR required |

## 6. Verdict and highest-value next steps

- **[FINDING]** BMRCL publishes its **entire DPR catalogue** through its own JSON API; all 10 DPR PDFs are directly downloadable. This closes the "DPR not public" block for the DPR **text** volumes.
- **[FINDING]** The Phase 2B DPR's Table 5.4 provides **per-chainage rail (deck) levels** for the KR Puram–Hebbal–KIA corridor (77 PVIs, 916.073 → 893.394 m), and the Phase 3 exec summary provides **per-station GL and RL** for 31 stations. Both are new per-location vertical data on record.
- **[BLOCKED]** The **flood corridor** for the ORR underpasses is Phase 2A (Silk Board–KR Puram), and its rail-level profile exists **only in the L-section drawing sheets**, which are not embedded in the public DPR PDF (the text volume has gradient geometry but no elevations). Same for the scanned Phase 1/2 volumes.
- **Highest-value next step:** RTI to BMRCL (PIO Nandini Soundarraj, 080-22969200/080-22969400, `rti@bmrc.co.in`) for: (a) Phase 2A L-section drawing sheets (chainage + rail level + existing ground level), (b) Phase 2A/2B bore-log annexures (ground-level RL at borehole chainages along ORR — 101 boreholes on 2A), (c) Phase 2A station GL/RL table mirroring Phase 3's Table 0.19. Second: OCR of the Phase 2A scanned volumes only if the RTI route stalls.
- **Lower value:** SWR RUB/ROB drawings (would give clearance at railway underbridges — Panathur, Kino, Shivananda, KR Puram — but requires RTI to SWR Bengaluru Division); NHAI/KRDCL/PWD DPRs are not public.