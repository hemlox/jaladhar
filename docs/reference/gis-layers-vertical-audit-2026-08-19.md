# Bengaluru open-GIS layers — vertical-data audit

**Date:** 2026-08-19. **Audits:** item AO (no verified elevation source resolves Bengaluru underpasses),
plus the user's hypothesis that a free Bengaluru GIS layer might carry vertical data.
**Measurements:** this session's committed `scripts/measure_kgis_dtm.py` (manifest:
`data/raw/underpass_search/kgis_dtm/manifest.json`), live ArcGIS REST field enumeration,
and the OpenCity KML checks logged in `data/raw/underpass_search/gis_layer_notes.csv`.

## Headline

**The hypothesis is half-true and changes the AO source table.** A public, unauthenticated KSRSAC KGIS
service serves a real elevation raster (`KA_DTM_BBMP.img`) over the whole BBMP region — the first
public Bengaluru elevation product found in three audits. But it is a **bare-earth DTM at 5–10 m cells**,
and it does **not** resolve underpass road levels (measured below). It also does **not** displace the
"no per-location realized road level" conclusion: the register stays unpopulated (Rule 1).

## Measured results

### KA_DTM_BBMP.img — the new source [FINDING]

- Served at `https://kgis.ksrsac.in/kgismaps2/rest/services/BBMP/FloodRisk_GIS/MapServer/26?f=json`
  (ArcGIS REST identify, no auth), extent 748210–820370 E / 1393790–1466680 N (UTM 43N).
- **75/77** probed points (50 register + 24 GT + 3 benchmarks) returned realized pixel values.
- **Vertical datum:** values are WGS84 **ellipsoidal** heights. After EGM96 geoid conversion
  (undulation ≈ −86.4 m in Bengaluru, computed with `egm96_15.gtx`), residual vs FABDEM at
  75 paired points is **mean +0.91 m, stdev 2.44 m, range −5.80 to +8.84 m**. The datum
  hypothesis is confirmed: KA_DTM + |N| ≈ orthometric, agreeing with FABDEM within FABDEM's own error.
- **Cell size:** 1 m-step probe at Silk Board shows pixel values change at ~5 m boundaries → 5–10 m cells.
- **Underpass resolution:** a 200 m transect across the Panathur railway underpass (GT_06, 10 m steps)
  shows a smooth 3.16 m regional slope and **no road dip** — the DTM does not see the road under the bridge.
- Examples (raw ellipsoidal values, from the manifest CSV): Panathur GT_06 786.776 m, Silk Board GT_05 795.317 m, Hebbal GT_15 810.869 m.

### What this unblocks

- An **independent citywide terrain check** for our FABDEM pipeline (V3-clean: KGIS is not derived
  from FABDEM). Residual stats above are the agreement.
- Ground-level context at each named underpass (approach terrain), which bounds the possible road
  level from above — still not the road level itself.
- Nothing for the carve pipeline's dips: those still need engineering/per-location sources.

## Per-layer table (full evidence in `gis_layer_notes.csv`)

| Layer | Vertical data? | Evidence |
|---|---|---|
| **KA_DTM_BBMP.img** | **YES** — ellipsoidal DEM, 5–10 m cells | identify pixel values at 75 points; datum + cell probes |
| BBMP_FloodLayers Major/Minor_Contour | **YES** — ELEVATION attr (10 m interval) | 10 features sampled, values 680–700+ |
| BGIS_Roads | NO — 2D planimetry (widths only) | field enumeration |
| Road_Line_2023 | NO — 2D planimetry (ROW widths) | field enumeration |
| FloodRisk_GIS "DEM.tif" | NO — categorical raster misnamed "DEM" | pixel value 6 constant, count 1,799,636 |
| Transit_GIS DULT_Services | transport layers (BRTS_Stops, Bus_Route…) — expected 2D | service list; fields not enumerated |
| OpenCity Railway/Metro KML | NO — 2-tuple coords, z_index = render order | file inspection |
| OpenCity Stormwater Drains | NO — Elevation = 0 on 5,804/5,806 | file inspection |
| Flooding/Flooding | NO — modelled 2/4/6 m extents | layer list |
| Bhuvan KA road WFS | reachable, attribute schema unverified | HTTP 200 |
| BBMP GIS (gisapp) | unreachable (502) this session | HTTP 502 |
| BDA portal | unreachable (SSL error) this session | SSLEOFError |

## Verdict on the user's hypothesis

[FINDING] Free Bengaluru GIS **does** include one genuinely vertical layer (KSRSAC KGIS KA_DTM, public,
queryable). [READING] The rest of the transport/road catalogue is 2D planimetry, matching the established
pattern. The DTM is a new independent terrain baseline for the project but does not resolve item AO's
core need (road surface levels beneath bridges); those remain engineering-document territory.

## Failed search terms / dead ends

- Bhuvan vector WFS: attribute schema not enumerated (timebox).
- data.gov.in: no Bengaluru underpass/road-level dataset identified in the timebox.
- gisapp.bbmpgov.in: HTTP 502; bdabengaluru.org: SSL error — both need another network.
- Bing/DDG/Mojeek/SearXNG: rate-limited or geo-broken from this sandbox IP (recorded in the
  underpass-search manifest notes); Google News RSS used instead where discovery was needed.