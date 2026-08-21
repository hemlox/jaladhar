# TransitGIS & OpenCity India — Vertical-Data Audit (per-underpass levels/clearances/Z)

**Date:** 2026-08-19 (UTC run window 2026-08-18T21:2x–21:3x)
**Agent:** implementation agent, research-only task
**Scope:** audit two GIS/data-source families for **per-underpass VERTICAL data** (road levels, clearances, Z values). No files outside `data/raw/underpass_search/` and this report were created or modified.
**Labels:** `[FINDING]` = verified live response; `[READING]` = interpretation; `[BLOCKED]` = gate (unreachable/needs auth/error).
**Manifest:** `data/raw/underpass_search/transitgis_opencity_manifest.json`
**Raw notes:** `data/raw/underpass_search/transitgis_opencity_notes.csv`

---

## Executive summary

- **TransitGIS** is a real DULT initiative (`[FINDING]`, dult.karnataka.gov.in/138/transit-gis/en), built with KSRSAC assistance, intended to hold Bengaluru BMRCL/BMTC/BBMP/BTP data. **The portal itself is unreachable**: all candidate hostnames (`transitgis.in`, `transitgis.karnataka.gov.in`, `transitgis.ksrsac.in`) have **no public DNS records** (DoH-verified), and the only server-side app path found, `https://kgis.ksrsac.in/transitgis/`, returns **HTTP 500** (`.aspx` subpaths 404). **Layer contents and any vertical fields therefore cannot be verified — `[BLOCKED]`.** The reachable DULT/K-GIS artefacts (DULT dashboard `/dult/`, K-GIS base layers metro/rail/road, ArcGIS services) are **2D planimetry with no documented level/clearance/Z attributes**.
- **OpenCity India** resolves at **opencity.in / data.opencity.in** (CKAN), *not* opencityproject.in (no DNS — the task brief's domain is wrong). All Bengaluru spatial datasets audited are **2D planimetry**. The "Bengaluru Railway and Metro Lines Map" KML is OSM-derived (`osm_id`, `layer`, `railway`, `z_index` where `z_index` is a rendering-order integer, not elevation); coordinates carry **no altitude**. Metro stations and taluk roadways KMLs carry hardcoded `0` altitude. No OpenCity Bengaluru dataset provides per-underpass clearance/level/Z.
- **Net result for JALADHAR:** neither source family currently unblocks per-underpass vertical data. TransitGIS would be the natural consolidated source *if* it ever becomes reachable, but there is zero evidence it carries vertical fields. The only vertical-ish artefacts are **engineering DPR/design PDFs** (e.g., Baiyyappanahalli ROB + IOC rotary DPR on OpenCity) containing section drawings for specific structures — PDF, not machine-readable GIS.

---

## A. TransitGIS (DULT + KSRSAC)

### What exists (`[FINDING]`)

| URL | HTTP | What it is |
|---|---|---|
| https://dult.karnataka.gov.in/138/transit-gis/en | 200 | Official DULT page: "DULT has developed TransitGIS... developed with the technical assistance of KSRSAC... started with Bengaluru city specific data, obtained from BMRCL, BMTC, BBMP, BTP". **Does not publish a portal URL.** |
| https://dult.karnataka.gov.in/137/gis-and-spatial-mapping/en | 200 | DULT "GIS & Spatial Mapping" initiative page; links to the Transit GIS page above. |
| https://kgis.ksrsac.in/dult/ | 200 | K-GIS "DULT Dashboard". Layers: Tree Mapping, LPA Boundary, HDMC Municipal Boundary, Location. **No transit layers.** |
| https://kgis.ksrsac.in/kgis/metadata.aspx | 200 | K-GIS metadata tree — Base Layers: Railway (NRVRAI), Railway Station (NRVRSN), METRO_STATION (NRVMSN), Metro_Corridor (NRVMRY), Road (NH/SH/RoadLine/RoadPoly/RoW), **DULT Proposed Road Network (DPRN)**. No vertical attributes documented. |
| https://kgis.ksrsac.in/kgis/sectors.aspx | 200 | "Transport and Allied" sector: Bus Stops, Bus Route, KSRTC assets. No vertical attributes documented. |
| https://kgis.ksrsac.in/arcgis/rest/services | 200 | ArcGIS 11.5 directory; folders BMRDA/LULC/Utilities/WWM only. BMRDA/BMRDA MapServer = 6 non-transport layers. **No TransitGIS service.** |

### Portal URLs attempted (`[BLOCKED]`)

| URL | Result |
|---|---|
| https://transitgis.in | **No DNS** (system resolver + Google DoH) |
| https://transitgis.karnataka.gov.in | **No DNS** (DoH) |
| https://transitgis.ksrsac.in | **No DNS** (DoH) |
| https://kgis.ksrsac.in/transitgis/ | **HTTP 500** (app path exists, server error) |
| https://kgis.ksrsac.in/transitgis/{default,home,login,portal}.aspx | **HTTP 404** |
| transitgis.dult.karnataka.gov.in, gis.dult.karnataka.gov.in, transit.kar.nic.in, transitgis.kar.nic.in, transitgis.nic.in, transitgis.dult.gov.in | **No DNS** (DoH) |

**Verdict (TransitGIS):** programme exists; portal unreachable; **no layer inventory and no vertical-data determination possible.** `[BLOCKED]` on every reachable avenue. The only evidence of intended content is DULT's prose (BMRCL/BMTC/BBMP/BTP datasets), which says nothing about Z/clearance/level.

---

## B. OpenCity India Bengaluru catalogue

### Portal facts (`[FINDING]`)

- Landing: **https://opencity.in** (Oorvani Foundation). Footer licence: **"CC BY-NC-SA 4.0 and ODbL"**.
- CKAN data portal: **https://data.opencity.in** (API: `/api/3/action/package_search` works).
- Bengaluru group: https://data.opencity.in/group/bengaluru (50+ datasets).
- **The task-brief domain `opencityproject.in` does not exist in public DNS** (`[BLOCKED]`/corrected to opencity.in).

### Per-dataset table (vertical-data verdict)

| Dataset (OpenCity) | URL slug | Format | Licence (per dataset) | Vertical data? |
|---|---|---|---|---|
| **Bengaluru Railway and Metro Lines Map** | `/dataset/bengaluru-railway-and-metro-lines-map` | KML | CC BY (BMRCL) | **NO** — fields `osm_id, layer, railway, z_index`; coords 2-tuple lon,lat (no altitude); `z_index` = OSM render order |
| Bengaluru Metro Stations Map – Apr 2023 | `/dataset/bengaluru-metro-stations` | KML | Public Domain (BMRCL) | **NO** — points at (lon,lat,0); Z hardcoded 0 |
| Bengaluru Road Width Map | `/dataset/bengaluru-road-width-map` | KML | Public Domain (BBMP) | **NO** — width/hierarchy fields only |
| Bengaluru Transport Network by Taluks | `/dataset/bengaluru-transport-network-by-taluks` | KML+PDF | Public Domain (GoK) | **NO** — 654,975 coords all Z=0.0 |
| Bengaluru Stormwater Drains Maps 2022 | `/dataset/bengaluru-stormwater-drains-maps` | KML+PDF | Public Domain (BBMP) | **NO** — `Elevation` attr is 0 on 5,804/5,806 (2 values ≈ −3.2) |
| BMRCL Station-wise Ridership (RTI) | `/dataset/bmrcl-station-wise-ridership-data` | XLSX/CSV | CC BY-NC (BMRCL) | **NO** — tabular ridership only |
| Bengaluru Mobility Indicators 2011 (DULT) | `/dataset/bengaluru-mobility-indicators` | CSV | Public Domain (DULT) | **NO** — zone-level stats, no geometry |
| Bengaluru One Way Roads (BTP) | `/dataset/bengaluru-one-way-roads` | CSV | unstated | **NO** — text descriptions |
| Bengaluru Metro Documents (DPRs) | `/dataset/bengaluru-metro-documents` | PDF | unstated (BMRCL) | **UNVERIFIED** — PDFs, not spatial |
| DPR Elevated Rotary IOC + ROB Baiyyappanahalli | `/dataset/documents-elevated-rotary-flyover-at-ioc-junction-and-rob-at-baiyyappanahalli-railway-level-crossing` | PDF (incl. drawings, topo survey, sections) | Public Domain (BBMP) | **UNVERIFIED** — engineering drawings; clearance/level values for these two structures only, PDF-only |

### Explicit check requested: metro/railway line KML altitude/Z/clearance

**Result: none.** The KML (`[FINDING]`, downloaded, 412 KB) Schema = `osm_id`, `layer`, `railway`, `z_index`. All `<LineString><coordinates>` are `lon,lat` pairs (no third component). `z_index` values observed (e.g. `5`, `15`) are OSM z-ordering for cartography, not elevations. No `altitudeMode`, no clearance/level fields. **2D planimetry, OSM-sourced.**

---

## C. Which source would unblock what

| Need | Source that could supply it | Status |
|---|---|---|
| Consolidated per-corridor metro/rail/route spatial layers (BMRCL/BMTC/BBMP/BTP) | **TransitGIS portal** (by design) | `[BLOCKED]` — unreachable (no DNS; kgis.ksrsac.in/transitgis/ → 500). DULT publishes no URL. |
| 2D alignments: metro lines, metro stations, rail lines, roads | OpenCity KMLs (CC BY / PD) and K-GIS base layers (reachable) | Available, **but 2D only** — usable for locating which underpasses sit under metro/rail/road corridors, not for clearances. |
| Underpass/ROB design levels & clearances | Engineering DPR PDFs (OpenCity: Baiyyappanahalli ROB+IOC rotary; BMRCL Phase DPRs) | PDF-only, per-structure; requires manual PDF extraction; not a systematic per-underpass dataset. |
| Terrain Z (for underpass bed/approach elevations) | Already in-project DEM pipeline (out of scope here) | — |
| Per-underpass clearance/level machine-readable GIS | **None found** in either family | This audit's negative result. |

`[READING]` TransitGIS is the highest-information-value target *if* DULT ever publishes a working URL — but per V-rules it carries no current evidence of vertical fields, so no build decision should depend on it.

---

## D. Dead ends (with reasons)

1. `transitgis.in`, `transitgis.karnataka.gov.in`, `transitgis.ksrsac.in`, `opencityproject.in` — **no public DNS records** (verified both via system resolver and Google DoH `dns.google/resolve`). `[BLOCKED]`
2. `kgis.ksrsac.in/transitgis/` — HTTP 500; `.aspx` subpaths 404. App path exists but is broken. `[BLOCKED]`
3. KSRSAC ArcGIS REST directory — no transit service exposed (only BMRDA/LULC/Utilities/WWM). `[FINDING]` dead end.
4. K-GIS "DULT Dashboard" (`/dult/`) — exists but only tree-mapping/LPA/HDMC layers; not the TransitGIS content. `[FINDING]`
5. Bing ("TransitGIS" Karnataka) — returns unrelated Microsoft results (query not honoured); DuckDuckGo html timed out; Google web search blocked by JS redirect; Google News RSS (3 query variants) returned 0 relevant items. Search engines unusable from this IP for this string.
6. OCR of the 2022 DULT TransitGIS screenshot (800×445 PNG) — no OCR tooling available in the environment; image not machine-readable here. Noted, not load-bearing.

---

## E. Failed search terms

- `"transitgis"` (Google News RSS, hl=en-IN) → 0 results
- `"Transit GIS" DULT` (Google News RSS) → 0 results
- `TransitGIS DULT Karnataka GIS` (Google News RSS) → 0 results
- `OpenCity India Urban Data Portal Bengaluru` (Google News RSS) → 1 irrelevant item (Ahmedabad heat)
- `"TransitGIS" Karnataka` (Bing) → generic Microsoft results only
- `"TransitGIS" Bengaluru` (DuckDuckGo html) → request timeout
- `TransitGIS DULT KSRSAC Bengaluru` (Google web) → JS-redirect block

---

## F. Licence summary (for the record)

- **DULT/KSRSAC/K-GIS**: GoK/KSRSAC copyright. KSRSAC layers page states data "developed by KSRSAC with GoK budget... any data leakage/misuse... subject to legal proceedings"; KSRSAC disclaimer: data are "representational... not for legal use... to be revalidated with respective agencies". **Not open-licensed; not bulk-downloadable.**
- **OpenCity**: portal-wide **CC BY-NC-SA 4.0 + ODbL**; per-dataset licences vary (CC BY, CC BY-NC, Public Domain/Other). KMLs carry no explicit field-level licence; attribute the portal per its attribution FAQ.

## G. Provenance

- Every claim above is backed by a URL with a recorded HTTP status in the manifest (`data/raw/underpass_search/transitgis_opencity_manifest.json`), and by downloaded/inspected files for the KML analyses (metro/rail, metro stations, road width, stormwater drains, taluk roadways — coordinate/Z-field checks done on the downloaded files).
- No fabricated data. Anything not verifiable is labelled `[BLOCKED]`/`UNKNOWN` with the request path.
