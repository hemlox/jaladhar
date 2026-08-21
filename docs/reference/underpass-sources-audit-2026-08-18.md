# Underpass road-level sources — component and validation audit

**Date:** 2026-08-18. **Companion to:** [`elevation-data-audit-2026-08-18.md`](elevation-data-audit-2026-08-18.md).
**Audits:** item AO (no verified elevation source resolves Bengaluru underpasses).

This pass attacks the AO conclusion from three sides: (1) direct elevation sources not covered
last time, (2) **components** that combine into an underpass road level, and (3) non-elevation
validation sources. Its measurement is a scripted, manifest-logged OSM `maxheight` coverage
study (`scripts/measure_osm_maxheight.py`, artifacts in `data/raw/osm/`). No raster was touched.
No standard was converted into a per-location depth (Rule 1).

## 1. Source-by-source result

### 1a. Direct elevation (new items; prior rows unchanged)

| Source | Status | What it provides | What it would unblock |
|---|---|---|---|
| SOI Karnataka Large Scale Mapping ([R39](https://www.surveyofindia.gov.in/pages/availability-of-ori-and-dem)) | **exists, G2G, offline** | 4,587 km² ORI at 0.03 m, ±0.125 m horizontal (Karnataka GDC, Bengaluru, `karn.gdc.soi@gov.in`); Karnataka DEM columns empty | 3 cm ortho imagery is sharp enough to read clearance signage and flood markers; a G2G 10 m national DEM exists per PIB 2025-12-10 but Karnataka DEM coverage unverified |
| National Geospatial Mission ([R39](https://pib.gov.in/PressReleasePage.aspx?PRID=2201543)) | announced, not delivered | 25 cm DEM for all plains/urban areas incl. urban India | The definitive fix if it becomes public; monitor, no date |
| GDI/GSX catalogue (catalogue.gsx.org.in) | unreachable this session | claimed SOI datasets under NGP-2022 ([R42]) | re-check from another network |
| Bhoonidhi finer CartoDEM, KSRSAC, commercial rows | unchanged from the prior audit | — | — |

### 1b. Components (design/measured geometry that bounds the road level)

| Source | Status | Value it carries |
|---|---|---|
| **OSM `maxheight` — measured** ([R32], this session) | measured, near-total absence | 0.2274% of DEM-bbox highway ways carry any tag; **0** `maxheight:physical`; **0/2,534** register segments within 25 m of a tagged way; 1/507 tunnel ways; GT_06 Panathur Railway Underpass sits 0.2 m from a way tagged `maxheight=4, tunnel=yes, layer=-1` |
| **IRC:54-1974 §8** ([R33]) | exists, clause verified | "Vertical clearance at underpasses shall be at least **5 metres**. However, in urban areas, this should be increased to **5.50 metres**…" |
| IRC:SP:84-2019 §2.10 + realized NHAI VUP ([R34]) | exists, realized instance | NHAI TechnicalSchedulesB §2.6.1 defers to SP:84 §2.10; its VUP tables carry **5.5 m** minimum vertical clearance (NH-39 RFP) |
| IRC:SP:87-2019 ([R35]) | exists | "vertical clearance available across the roadway shall be minimum **5 m**" |
| IRC:SP:90-2010 §4.19 ([R36]); IRC:5-2024 ([R37]) | exists / absence | vertical-clearance definition; 5-2024 has no clearance clause |
| BMRCL Phase 3 DPR exec summary ([R38]) | exists, fetched (`curl -sk`, expired TLS) | Table 0.7: rail level ≥ **7.50 m** mid-section / 12.50 m at stations; §0.5.2 DGPS topo surveys; full DPR volumes not public (RTI path) |
| Mapillary ([R42]) | token-gated | Bengaluru has community coverage; automated counting needs an API token |

**The component chain:** `road level ≈ deck level − clearance`, with clearance pinned at
5.0–5.5 m (urban) by IRC:54 §8 and realized at 5.5 m in NHAI VUP contracts, and deck level
readable from the existing DEM (1,371/2,534 register segments already classify as crest/deck-visible,
item AK). This yields a **bounded band** per underpass — it does **not** yield a measured level, and
the register stays unpopulated (Rule 1).

### 1c. Non-elevation validation

| Source | Status | Value |
|---|---|---|
| KSNDMC 124 BBMP water-level sensors ([R40]) | deployed; history gated | per-underpass inundation readings possible; formal request (draft at `docs/archive/ksndmc_data_request_DRAFT.md`) |
| OpenCity KSNDMC datasets ([R40]) | public domain, rainfall only | AWS/station inventory + taluk/hobli rainfall — forcing support, no levels |
| News closure records ([R41]) | exists | 2022-08-29: Sangabasavana Doddi / Byrapatna NH underpasses inundated (occurrence, no depth); BTP 54 flood-prone roads (snippet citation) |
| Insurance loss data ([R43]) | not found | no public per-address claims dataset |

## 2. Measured OSM maxheight coverage

Method: Overpass count queries per bbox, then a full fetch of tagged ways/nodes; register and
ground-truth joins at 25 m radius. One bbox-order bug (west/east swapped) produced a false zero
and was corrected before the final run. Manifest: `data/raw/osm/maxheight_coverage.json`
(git SHA, `status: completed`); raw payloads `overpass_maxheight_ways.json` (439 ways),
`overpass_maxheight_nodes.json`, `overpass_tunnel_ways.json`.

| Metric | DEM bbox | District bbox |
|---|---|---|
| highway ways | 194,378 | 270,163 |
| `maxheight` ways | 390 | 393 |
| `maxheight:physical` ways | **0** | **0** |
| `maxheight:signed` ways | 52 | 56 |
| `maxheight` nodes | 35 | 41 |
| any-tag fraction | **0.2274%** | **0.1662%** |
| tunnel ways / with maxheight within 25 m | 507 / **1** | 575 / — |

Register join: **0/2,534** segments within 25 m of a `maxheight` way. GT join: GT_06
Panathur Railway Underpass **0.2 m** from `maxheight=4`; GT_15 171.9 m from `maxheight:signed=no`;
GT_22 172.0 m from bridge `maxheight=4.5`; GT_24 189.5 m from `maxheight=none`.
Value mix of the 442 fetched ways: 5.5 m (104), none (104), default (103), no (49), 4.5 (30),
5.7 (10), 4 (9); 183 numeric/feet, 259 non-numeric.

**If OSM maxheight were a usable source, this number would be large. It is 0.23%.** The absence
is itself the finding: signage-derived clearance cannot populate the register, and the one true
positive (GT_06, 4 m — consistent with a railway underpass) only validates the method.

## 3. Rule 5 stops with exact request paths

| Stop | Path | Owner |
|---|---|---|
| SOI G2G: 3 cm ORI coverage of Bengaluru villages; Karnataka national-10 m DEM | email `karn.gdc.soi@gov.in` (080-25533595) | needs a government agency sponsor (BBMP/KSNDMC) |
| KSNDMC sensor history (124 BBMP sensors) | draft at `docs/archive/ksndmc_data_request_DRAFT.md` | formal request |
| BMRCL Phase 3 DPR ground profiles | RTI to BMRCL | RTI application |
| Mapillary automated counts | free API token; coverage endpoint returned a Facebook shell without one | registration |
| NGM 25 cm urban DEM | monitor PIB/DST announcements | none yet |

Licence flags for Darshil: IRC mirror copies are **CC BY-NC 4.0** (law.resource.org / archive.org)
— quoting only, no redistribution; this compounds the existing FABDEM CC BY-NC-SA exposure.

## 4. Verdict on item AO

**PARTIAL — the "no source" conclusion survives for realized levels, but the block is no longer
uninformative, and it is now attackable from four named paths.**

- **Measured (this pass):** OSM `maxheight` is effectively absent (0.2274% ways, 0 physical tags,
  0/2,534 register segments). This was asserted before; it is now a scripted, manifested measurement.
- **Bounded (this pass):** clearance is pinned at 5.0/5.5 m (urban) by IRC:54-1974 §8 and realized
  at 5.5 m in NHAI VUP contracts; metro viaducts sit ≥7.50 m up. With deck level from the DEM,
  road level is a band, not a number — Rule 1 keeps it out of the register.
- **Open (unchanged):** no per-location realized lower-road level, datum, and QA. 0/24 GT points
  are lowest-cell (AK); the depth deficit stands.
- **Attackable (new):** SOI Karnataka GDC (3 cm ORI + national 10 m DEM, G2G), KSNDMC 124-sensor
  history, BMRCL DPR via RTI, NGM 25 cm urban DEM.

Recommendation to Darshil: authorise the KSNDMC request (draft exists) and identify a government
partner for the SOI G2G path; both are the only routes to realized levels, and neither requires
spending.