# Bengaluru Elevation Data Audit

**Date:** 2026-08-18  
**Scope:** determine whether an elevation source exists and is obtainable with enough realized evidence to represent the road features that flood, especially underpasses.  
**Frozen-input rule:** the existing DEM was read conceptually only; no raster was regenerated, resampled, conditioned, or overwritten.  
**Evidence ledger:** [`data/raw/elevation_source_register.json`](../data/raw/elevation_source_register.json)  
**Existing diagnosis:** [`goal15.md`](../../logs/04-drain-diagnosis-and-underpass-register.md), [`OPEN-ITEMS.md`](../OPEN-ITEMS.md) items AK, AL, and AN.

## Verdict

1. **A finer Indian product does exist on paper.** Current NRSC/Bhoonidhi material lists CartoDEM at 10 m and 2.5 m posting, both as DSM products. The legacy Bhuvan public page exposes approximately 30 m/32 m CartoDEM. This reopens item 3; it is not evidence that a 10 m or 2.5 m Bengaluru tile is currently downloadable by this project. [R01-R08]
2. **No public, verifiable Bengaluru source was found that demonstrates road-sag capture.** The current Bhoonidhi API requires authentication; the anonymous collection and Bengaluru AOI probes returned HTTP 401. Commercial products advertise global coverage or made-to-order delivery, but no Bengaluru tile, point cloud, road profile, vertical datum, bridge treatment, or independent checkpoint was obtained. [R06, R20-R24, R30-R31]
3. **A bare-earth DEM is not sufficient as the sole representation of a bridge-underpass system.** A measured continuous road cut can appear in a DTM if the lower roadway is captured and retained. But a single raster stores one elevation per x,y and cannot represent the upper bridge deck and lower road at the same x,y. Bridge/underpass topology therefore needs separate road profiles, bridge/culvert breaklines, or a multilayer structure model. Resolution alone cannot recover a lower surface that was omitted or occluded. [R25-R27]
4. **The only credible path to a defensible underpass level is a survey or an as-built record that contains realized levels.** KSRSAC advertises LiDAR, photogrammetry, DGPS, and total-station services, and the 2021 Bengaluru flood study reports BDA contour input. Neither is a public, QA-documented Bengaluru road-level dataset in the evidence reviewed. [R09-R15]
5. **Rule 5 stop:** the project has no source that is both verified for the required Bengaluru features and ready to load. External authorization, procurement, or a formal data request is required before any elevation change. Item AO was added to [`OPEN-ITEMS.md`](../OPEN-ITEMS.md). The existing zero-depth underpass register remains the complete honest output.

This is not a claim that no vendor or government office could ever supply a survey. It is the narrower and actionable finding: **no existing source was verified as obtainable and feature-resolving for this project without an external decision, and no standard or product nominal resolution justifies inventing a lower roadway.**

## Source Survey

The table separates product existence from the evidence needed for use. “Vertical accuracy” is a provider or publication specification, not a Bengaluru-specific validation unless explicitly stated. “Underpass evidence” is deliberately stricter than resolution.

### Indian and Public Sources

| Source | Exists | Obtainability | Licence / terms | Resolution and type | Stated vertical accuracy | Bengaluru coverage | Underpass conclusion |
|---|---|---|---|---|---|---|---|
| **CartoDEM legacy, Bhuvan** | Yes; public legacy catalogue | Login-based legacy download is documented; current status is not guaranteed | Legacy page does not establish a redistribution licence; current Bhoonidhi terms apply to current access | Approximately 30-32 m; DEM/DSM naming is inconsistent across legacy material | About 8 m at 90% confidence in FAQ | India, 1 degree by 1 degree legacy tiles; no current Bengaluru tile was verified | Cannot support a 30 m underpass sag; resampling does not create survey detail. [R03-R04] |
| **CartoDEM 10 m, Bhoonidhi** | Yes as a current catalogue product | Product is listed for order; anonymous catalogue access rejected with HTTP 401; no Bengaluru AOI result | Bhoonidhi registration and order terms; commercial redistribution of original data is restricted; exact delivery terms must be retained | 10 m posting, explicitly listed as **DSM** | CartoDEM family documentation states 8 m LE90; no Bengaluru checkpoint | India-level product claim, no Bengaluru tile list exposed | Finer posting is not proof of lower-road capture; DSM semantics and 8 m nominal vertical error do not establish a road invert. [R01-R08, R30-R31] |
| **CartoDEM 2.5 m / updated CartoDSM** | Yes as a current product and brochure | Priced/order path exists; no unauthenticated Bengaluru tile or delivery was verified | Bhoonidhi terms; not treated as open redistribution | 2.5 m posting, **DSM**, not hydrologically conditioned | 8 m LE90 in brochure; no Bengaluru checkpoint | Brochure says India coverage with source-image gaps; no Bengaluru tile list exposed | This is the most important recheck result, but it is still a DSM. It may show structures rather than the lower road, and no road-sag QA exists. [R05-R08, R30-R31] |
| **KSRSAC / K-GIS** | State capability and public GIS portals exist | KSRSAC advertises data acquisition and custom surveys by request; public portal offers some spatial data | Government service terms; no dataset-specific licence for a Bengaluru elevation product found | Site-specific: LiDAR, drone photogrammetry, DGPS, total station, DEM/DSM | Site-specific; not published for a Bengaluru terrain delivery | Karnataka/Bengaluru institutional capability is evidenced, not a released road-level dataset | Potential acquisition path, not an existing realized source. Demand point cloud, ground classification, bridge treatment, checkpoints, datum, and raw-survey licence. [R09-R11] |
| **Survey of India, Large Scale Mapping Karnataka** | ORI coverage is listed | Government-to-government offline request | Government data sharing; not public download | ORI 0.03 m is listed; DEM fields are blank for Karnataka | DEM vertical accuracy is blank for Karnataka | 4,587 square kilometres of ORI listed; no DEM area listed | Does not establish an elevation source. ORI is not a road invert or DEM. [R12] |
| **BDA contour / DEM input reported in flood study** | A study reports a BDA 1 m contour map processed into a DEM | The paper does not provide the raster or a public request path | Unknown; no delivery terms | 1 m contour-map source; resulting DEM metadata not given | Not stated | Bengaluru study input; coverage/actual release unverified | A lead for a formal BDA request, not evidence that the current lower roadway is measured. [R15] |
| **BBMP stormwater GIS / OpenCity drain lines** | Yes; existing project data | Obtained as public 2D lines | Public Domain as recorded by Goal 15 | Horizontal centerlines only | None | Bengaluru network | No levels, inverts, cross-sections, or road sags. It cannot establish an underpass invert. [goal15.md](../../logs/04-drain-diagnosis-and-underpass-register.md) |

### Commercial and Restricted Products

| Source | Exists | Obtainability | Licence / terms | Resolution and type | Stated vertical accuracy | Bengaluru coverage | Underpass conclusion |
|---|---|---|---|---|---|---|---|
| **Airbus WorldDEM Neo** | Yes; provider advertises global off-the-shelf availability | Commercial order | Proprietary commercial EULA; raw-product redistribution is not assumed | 5 m DSM and derived DTM | 2 m relative, 2.5 m absolute | Provider-level global claim; no Bengaluru tile or sample was obtained | Candidate product, not evidence. DTM may improve terrain but does not prove the lower road was captured beneath a bridge. [R20] |
| **AW3D Standard** | Yes; provider advertises global coverage | Commercial order, minimum order stated by provider | Proprietary commercial terms | 2.5 m/5 m DSM or DTM | 5 m RMSE, 7 m LE90 without GCP | Global provider claim; no Bengaluru delivery was verified | Nominal resolution and accuracy do not demonstrate an underpass profile. [R21] |
| **AW3D Enhanced** | Yes; made-to-order product | Commercial order, minimum order stated by provider | Proprietary commercial terms | 0.5 m/1 m/2 m DSM or DTM | Without GCP: 2 m RMSE, 3 m LE90 absolute; 1 m RMSE, 1.5 m LE90 relative | Area is on demand; India/Bengaluru availability was not verified | The strongest nominal commercial candidate, but it still requires an AOI delivery and independent road-profile QA. [R22] |
| **Intermap NEXTMap** | Yes; provider advertises global DSM/DTM | Commercial Data Store/API/custom service | Proprietary commercial terms | Up to 1 m posting, DSM or DTM | Up to 1 m LE90 depending on area | “Global” is a provider claim; no Bengaluru delivery was verified | Product availability is not feature resolution. Require the actual tile and a lower-road/bridge QA report before use. [R23] |
| **EarthDEM** | Yes; 2 m DSM release | Restricted NASA CSDA access for eligible federal/federally funded users | Access restricted by source-imagery licence | 2 m DSM; 10 m resampled mosaic also exists | Not stated on provider page | Domain-level coverage exists but provider says not all areas are covered; Bengaluru tile not verified | Not an open project path, not a DTM, and no underpass QA. [R24] |
| **FABDEM, frozen baseline** | Yes; existing project input | Already obtained in the project; no new fetch was made | **CC BY-NC-SA 4.0** per item 2. A second non-commercial layer would compound the exposure; this audit does not decide the licensing question. | Approximately 30 m bare-earth DTM | No Bengaluru-specific accuracy established here | Global product and Bengaluru input already used | Goal 15 measured the failure on the realized frozen DEM. No finer resampling is allowed to masquerade as new information. [R28, OPEN-ITEMS item 2](../OPEN-ITEMS.md) |

The commercial rows are not “available data” in the project sense. They are **procurement leads**. A vendor page does not establish that the AOI was acquired, that the lower roadway was visible, or that the licence permits the intended public/municipal use.

## What Bare-Earth Can and Cannot Represent

### It can represent a measured road cut

A continuous road that is physically cut below surrounding ground is part of the ground surface. A sufficiently detailed survey can retain that road surface in a DTM. This is a conditional capability, not an inference rule: the source must have sampled the road, classified it as ground/road surface, and documented the vertical datum and quality.

### It cannot encode crossing topology in one raster

At a bridge crossing, the upper deck and the lower roadway occupy the same horizontal footprint. A raster DEM has one z value per x,y. It must choose one surface, interpolate across the bridge, or be conditioned with breaklines and separate structure semantics. USGS documentation explicitly defines bare earth as free of man-made structures, distinguishes bridge decks, and discusses bridge/culvert conditioning; the older technical memorandum states the one-elevation-per-x,y limitation. [R25-R27]

Therefore:

- **More pixels do not restore a missing lower surface.** A 2.5 m DSM can be finer while still recording the deck or canopy rather than the road under it.
- **A bare-earth DTM is not automatically the underpass roadway.** Bridge filtering may remove the deck and interpolate terrain; occlusion may leave no lower-road return; an upstream product may preserve the wrong surface.
- **A hydrologically conditioned DEM can encode a chosen flow connection, but that is a modelling edit, not a measured invert.** It cannot be used here to invent the missing level.
- **The correct representation for this feature class is layered:** a terrain surface plus road/bridge/underpass geometry, or a road centreline profile and structure breaklines with measured levels.

This explains the existing result without changing it: Goal 15 found 1,001 of 2,534 tagged segments with no material DEM dip and 1,371 with a visible crest/deck pattern. Those are realized observations of the frozen input, not depths to be filled in. [R28]

## Alternatives That Could Establish the Sag

No value below is a proposed substitute for a missing level. The table states what each record can prove if it is obtained.

| Record or survey | Can establish a measured invert/sag? | What must be present | What it cannot establish |
|---|---|---|---|
| **As-built longitudinal road profile** | **Yes**, if it contains surveyed reduced levels along chainage and the vertical datum | Chainage, centreline/edge points, RL/elevation, datum, survey date, and as-built status | A design-only profile is not proof of what was built; a profile without datum is not joinable to the DEM |
| **Published road cross-sections** | **Yes**, only when sections are as-built survey sections with realized levels | Section station, spot levels, road crown/edge/invert, datum, and bridge/approach limits | A typical section, design template, or unlabelled drawing yields no realized underpass level |
| **Bridge or underpass clearance standard** | **No** | None; a standard specifies a design envelope or clearance requirement | It does not give the road invert, deck RL, approach grade, drainage low point, or as-built deviation. It must not be converted into a sag |
| **Bridge/road design drawing** | **Only if explicitly as-built and levelled** | Datum, spot elevations, chainage, and construction/as-built survey status | A design intent can differ from construction; a clearance dimension is not an invert |
| **Total-station, digital-level, or DGPS/RTK survey** | **Yes**, if the instrument survey actually observes the road low point or drain invert | Control points, datum/geoid convention, point coordinates, RLs, uncertainty, and survey date | A sparse topographic survey that misses the low point cannot be repaired by interpolation under Rule 1 |
| **Airborne/terrestrial LiDAR or photogrammetry** | **Potentially**, if the lower road has returns or surveyed breaklines and QA | Point cloud, classifications, intensity/coverage, bridge separation, ground checkpoints, vertical accuracy, datum, and occlusion report | A DSM, roof/deck surface, or occluded bridge footprint does not establish the lower roadway |
| **Municipal drain survey with invert levels** | **Yes for the drain invert**, not automatically for the road surface | Invert RL, manhole/culvert chainage, pipe/box dimensions, datum, and as-built status | Existing 2D drain centrelines have no levels and cannot establish an underpass sag |

### Standards are not measurements

Bridge clearance standards can constrain how a structure was designed, but they do not identify the realized low point. Using a typical clearance, a standard road section, or an interpolation to populate the 2,534-record register would create exactly the fabricated terrain this goal is intended to prevent. The register therefore remains geometry-and-measurement only, with no assigned invert or depth.

## Rule 5 Stop and Required External Decision

**Blocked input:** no public or already-held source was verified to provide a Bengaluru underpass road profile with the metadata needed to use it as terrain.

**What can close the blocker:** one of the following must be authorized and returned with provenance:

1. A formal KSRSAC/BBMP/BDA/Survey of India request for existing as-built road, bridge, and drain level records.
2. Procurement of a commercial AOI product, followed by independent checks at representative underpass sites before any raster is considered.
3. A commissioned total-station/DGPS/terrestrial or airborne survey that captures the lower road and drain geometry, with datum, checkpoints, uncertainty, and licensing terms.

Until one path returns realized levels, the terrain branch must not be changed and no surrogate or solver result may consume invented underpass geometry. This is the Rule 5 stop recorded as item AO, not a reason to fabricate a fallback.

## Sources

The compact source ledger with retrieval date, exact product claims, limitations, and the two HTTP 401 observations is [`data/raw/elevation_source_register.json`](../data/raw/elevation_source_register.json). Key primary sources are linked inline by evidence ID. Existing project measurements are linked to `goal15.md` and `OPEN-ITEMS.md`; they were not rerun for this audit.
