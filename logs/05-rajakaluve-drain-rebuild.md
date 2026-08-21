# Drain Representation Rebuild on BBMP Rajakaluve Network & Quantification

**Repository:** `/home/darshil/Desktop/sih/clginternal`  
**Committed SHA:** `338a955`  
**Date:** 2026-08-18  
**Producer Module:** [`src/jaladhar/terrain/drains.py`](../src/jaladhar/terrain/drains.py)  
**Consumer Seam:** [`src/jaladhar/solver/state.py`](../src/jaladhar/solver/state.py)  
**Execution Manifest:** [`runs/terrain_drains/manifest.json`](../runs/terrain_drains/manifest.json)  
**Test Suite:** [`tests/test_terrain_drains.py`](../tests/test_terrain_drains.py) (6 passing tests, 101/101 total invariant suite green)

---

## 1. Executive Summary & Context

Goal 15 secured and audited the **BBMP Stormwater Drains (SWD) Rajakaluve network (2022)** from OpenCity (Public Domain). The network contains **6,839 features** across **1,988.47 km** ($2.33\times$ the $853.44\text{ km}$ OSM network previously used).

In this stage:
1. The drainage representation was rebuilt from the BBMP SWD network on the canonical and buffered $10\text{ m}$ grids ($3421 \times 3515$ and $3521 \times 3615$ cells).
2. Existing OSM-derived rasters and vectors were **preserved without deletion or overwriting**; new BBMP-derived rasters were created side-by-side.
3. Domain-wide and ground-truth distance transforms were computed and compared against the baseline OSM representation.
4. The capacity assignment rationale was established: open administrative data provides 2D horizontal centerlines without standardized channel cross-sections, depths, or hydraulic parameters. Per **CLAUDE.md Non-negotiable 1**, no arbitrary capacity ratios were invented across Primary/Secondary/Tertiary tiers; a single class-independent uniform prior is maintained.
5. Invariant **V8** contract assertions were established between the producer manifest and consumer state loader.

---

## 2. Coexisting Artefacts (Side-by-Side)

| Artefact Description | OSM Version (Preserved) | BBMP Version (New) | Resolution / Shape |
|---|---|---|---|
| **Interim Canonical Distance** | [`data/interim/terrain/distance_to_drain.tif`](../data/interim/terrain/distance_to_drain.tif) (14.8 MB) | [`data/interim/terrain/distance_to_drain_bbmp.tif`](../data/interim/terrain/distance_to_drain_bbmp.tif) (16.9 MB) | $10\text{ m}$ / $(3421, 3515)$ |
| **Interim Buffered Distance** | [`data/interim/terrain/distance_to_drain_buffered.tif`](../data/interim/terrain/distance_to_drain_buffered.tif) (16.1 MB) | [`data/interim/terrain/distance_to_drain_bbmp_buffered.tif`](../data/interim/terrain/distance_to_drain_bbmp_buffered.tif) (18.8 MB) | $10\text{ m}$ / $(3521, 3615)$ |
| **Interim Canonical Capacity** | [`data/interim/terrain/drain_capacity.tif`](../data/interim/terrain/drain_capacity.tif) (15.3 MB) | [`data/interim/terrain/drain_capacity_bbmp.tif`](../data/interim/terrain/drain_capacity_bbmp.tif) (18.0 MB) | $10\text{ m}$ / $(3421, 3515)$ |
| **Interim Buffered Capacity** | [`data/interim/terrain/drain_capacity_buffered.tif`](../data/interim/terrain/drain_capacity_buffered.tif) (16.6 MB) | [`data/interim/terrain/drain_capacity_bbmp_buffered.tif`](../data/interim/terrain/drain_capacity_bbmp_buffered.tif) (20.1 MB) | $10\text{ m}$ / $(3521, 3615)$ |
| **Processed Canonical Stack** | [`data/processed/distance_to_drain.tif`](../data/processed/distance_to_drain.tif), [`drain_capacity.tif`](../data/processed/drain_capacity.tif) | [`data/processed/distance_to_drain_bbmp.tif`](../data/processed/distance_to_drain_bbmp.tif), [`drain_capacity_bbmp.tif`](../data/processed/drain_capacity_bbmp.tif) | $10\text{ m}$ / $(3421, 3515)$ |
| **Processed Buffered Stack** | [`data/processed/buffered/distance_to_drain.tif`](../data/processed/buffered/distance_to_drain.tif), [`drain_capacity.tif`](../data/processed/buffered/drain_capacity.tif) | [`data/processed/buffered/distance_to_drain_bbmp.tif`](../data/processed/buffered/distance_to_drain_bbmp.tif), [`drain_capacity_bbmp.tif`](../data/processed/buffered/drain_capacity_bbmp.tif) | $10\text{ m}$ / $(3521, 3615)$ |
| **Waterway Vector Network** | [`data/interim/terrain/waterways.gpkg`](../data/interim/terrain/waterways.gpkg) (2,724 features, 853.44 km) | [`data/interim/terrain/waterways_bbmp.gpkg`](../data/interim/terrain/waterways_bbmp.gpkg) (6,835 features, 1,986.26 km) | EPSG:32643 |

---

## 3. Distance-to-Drain Quantified Changes

### Domain-Wide & Ground-Truth Distance Metrics

| Evaluation Domain | Metric | Old OSM Network | New BBMP Network | Absolute Change | Relative Change |
|---|---|---|---|---|---|
| **24 Ground-Truth Points**<br>*(September 2022 flood points)* | **Mean distance**<br>**Median distance** | **$304.5\text{ m}$** ($307.1\text{ m}$ geom)<br>**$240.8\text{ m}$** ($256.0\text{ m}$ geom) | **$102.8\text{ m}$** ($105.1\text{ m}$ geom)<br>**$80.5\text{ m}$** ($82.4\text{ m}$ geom) | $-201.7\text{ m}$<br>$-160.3\text{ m}$ | **$-66.2\%$** (3.0× closer)<br>**$-68.6\%$** (3.1× closer) |
| **BBMP Municipal Boundary**<br>*(716.96 km², 7,169,550 cells)* | **Mean distance**<br>**Median distance** | **$347.2\text{ m}$**<br>**$300.8\text{ m}$** | **$159.3\text{ m}$**<br>**$111.8\text{ m}$** | $-187.9\text{ m}$<br>$-189.0\text{ m}$ | **$-54.1\%$** (2.2× closer)<br>**$-62.8\%$** (2.7× closer) |
| **Full Canonical Grid Bbox**<br>*(12,024,815 cells, 35.15 × 34.21 km)* | **Mean distance**<br>**Median distance** | **$555.9\text{ m}$**<br>**$400.0\text{ m}$** | **$1473.1\text{ m}$**<br>**$254.6\text{ m}$** | $+917.2\text{ m}$<br>$-145.4\text{ m}$ | $+165.0\%$ (bounding box artifact)<br>**$-36.4\%$** (1.6× closer) |

*Bounding Box Phenomenon:* The BBMP dataset is strictly bounded to the municipal corporation boundary. OSM includes natural rural stream centerlines in the peri-urban corners of the rectangular bounding box ($35.15 \times 34.21\text{ km}$). Because the municipal dataset terminates at the administrative border, the unpopulated outer corners reach distance transforms up to $13.9\text{ km}$, shifting the bounding-box mean while the municipal interior median and mean plunge by $54.1\%\text{ to }68.6\%$.

---

## 4. Domain Coverage by Buffer Threshold

Evaluated on the full canonical raster grid ($N = 12,024,815$ cells):

| Buffer Distance | Old OSM Cells | Old OSM Domain % | New BBMP Cells | New BBMP Domain % | Difference (pct pt) | Expansion Ratio |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **$\le 0\text{ m}$ (on mapped drain)** | 101,562 | 0.845% | 245,737 | **2.044%** | $+1.199\%$ | **2.42×** |
| **$\le 50\text{ m}$** | 900,348 | 7.487% | 1,978,283 | **16.452%** | $+8.964\%$ | **2.20×** |
| **$\le 100\text{ m}$** | 1,698,852 | 14.128% | 3,392,437 | **28.212%** | $+14.084\%$ | **2.00×** |
| **$\le 200\text{ m}$ ($1\times$ decay length)** | 3,242,345 | 26.964% | 5,349,724 | **44.489%** | $+17.525\%$ | **1.65×** |
| **$\le 500\text{ m}$ ($2.5\times$ decay length)** | 7,177,159 | 59.686% | 7,325,005 | **60.916%** | $+1.230\%$ | **1.02×** |
| **$\le 1000\text{ m}$ ($5\times$ decay length)** | 10,348,068 | 86.056% | 8,203,527 | **68.222%** | $-17.834\%$ | **0.79×** |

---

## 5. Per-Point Distance & Capacity Comparison at the 24 Ground-Truth Points

At 23 of 24 points, distance to the nearest mapped drain dropped significantly (up to $947.6\text{ m}$ reduction at HAL Old Airport Road, $496.2\text{ m}$ at Hebbal Flyover Underpass, and $352.5\text{ m}$ at Marathahalli Bridge):

| ID | Location Name | Old OSM Dist (m) | New BBMP Dist (m) | Change (m) | Old Prior (mm/h) | New Prior (mm/h) |
|---|---|:---:|:---:|:---:|:---:|:---:|
| **GT_01** | RMZ Ecospace Outer Ring Road | 220.0 | 40.0 | $-180.0$ | 3.329 | 8.187 |
| **GT_02** | Rainbow Drive Layout Gate | 386.0 | 70.7 | $-315.3$ | 1.451 | 7.022 |
| **GT_03** | Divyasree 77 East Yemalur | 120.4 | 10.0 | $-110.4$ | 5.477 | 9.512 |
| **GT_04** | Epsilon Villa Layout Yemalur | 380.8 | 36.1 | $-344.7$ | 1.490 | 8.350 |
| **GT_05** | Silk Board Junction | 80.0 | 64.0 | $-16.0$ | 6.703 | 7.260 |
| **GT_06** | Panathur Railway Underpass | 190.0 | 70.7 | $-119.3$ | 3.867 | 7.022 |
| **GT_07** | Marathahalli Bridge ORR | 487.0 | 134.5 | $-352.5$ | 0.876 | 5.103 |
| **GT_08** | Balagere Road Varthur | 291.5 | 76.2 | $-215.4$ | 2.328 | 6.833 |
| **GT_09** | Borewell Road Whitefield | 250.8 | 56.6 | $-194.2$ | 2.854 | 7.536 |
| **GT_10** | Manyata Tech Park Nagavara | 70.7 | 28.3 | $-42.4$ | 7.022 | 8.681 |
| **GT_11** | Koramangala 4th Block | 120.4 | 10.0 | $-110.4$ | 5.477 | 9.512 |
| **GT_12** | Sai Layout Horamavu Hennur | 165.5 | 117.0 | $-48.5$ | 4.371 | 5.570 |
| **GT_13** | Bilekahalli Bannerghatta Road | 150.0 | 100.5 | $-49.5$ | 4.724 | 6.050 |
| **GT_14** | Radha Reddy Layout Doddakannelli | 223.6 | 63.2 | $-160.4$ | 3.269 | 7.289 |
| **GT_15** | Hebbal Flyover Underpass | 596.2 | 100.0 | $-496.2$ | 0.508 | 6.065 |
| **GT_16** | HAL Old Airport Rd / Yamalur | 1032.5 | 84.9 | $-947.6$ | 0.057 | 6.543 |
| **GT_17** | Bellandur Kodi Junction | 361.4 | 140.0 | $-221.4$ | 1.642 | 4.966 |
| **GT_18** | Varthur Kodi Junction | 89.4 | 150.0 | $+60.6$ | 6.394 | 4.724 |
| **GT_19** | Munnekollal Main Road | 53.9 | 36.1 | $-17.8$ | 7.639 | 8.350 |
| **GT_20** | Hennur Main Road / Geddalahalli | 230.9 | 170.0 | $-60.9$ | 3.153 | 4.274 |
| **GT_21** | KR Puram Lake Road | 348.3 | 158.1 | $-190.2$ | 1.753 | 4.536 |
| **GT_22** | Madiwala Lake Road | 311.1 | 98.5 | $-212.6$ | 2.111 | 6.111 |
| **GT_23** | Mahadevapura Ring Road | 335.4 | 90.0 | $-245.4$ | 1.869 | 6.376 |
| **GT_24** | Halanayakanahalli Lake Outlet | 813.0 | 561.4 | $-251.6$ | 0.172 | 0.604 |

---

## 6. Capacity Assignment by Class & Prior Preservation

1. **Why Capacity Is Class-Independent:**
   - The BBMP SWD dataset is classified into **Primary (163 lines, 328.5 km)**, **Secondary (870 lines, 438.8 km)**, and **Tertiary (5,802 lines, 1,219.0 km)**.
   - Comprehensive audit of BBMP Master Plan documentation, administrative KPPP tenders, and KML schemas reveals that dimensions (cross-sectional width/depth, culvert sizes, roughness, invert elevations) are site-specific per Detailed Project Report (DPR). There is no published, standardized hydraulic capacity ratio across the hierarchy.
   - Per **CLAUDE.md Non-negotiable 1** and Task 4, fabricating synthetic capacity multipliers across classes is strictly prohibited. A single uniform baseline ($10.0\text{ mm/hr}$) and decay length ($200\text{ m}$) are applied across all classes.
2. **Preservation of Prior Status:**
   - The status of `drain_capacity.tif` as `"the Phase 1 PRIOR, never a measurement"` is strictly preserved in `configs/solver.yaml` line 96, `configs/domain_bengaluru.yaml`, and [`runs/terrain_drains/manifest.json`](../runs/terrain_drains/manifest.json) (`assumed_uncalibrated: true`, `is_prior: true`).

---

## 7. V8 Boundary Contract & Consumer-Side Assertion

1. **Producer Side:** [`src/jaladhar/terrain/drains.py`](../src/jaladhar/terrain/drains.py) guarantees and writes the following metadata into [`runs/terrain_drains/manifest.json`](../runs/terrain_drains/manifest.json):
   - `source`: `"bbmp_rajakaluve_2022"`
   - `feature_count`: `6835`
   - `total_length_m`: `1986259.32` ($1,986.26\text{ km}$)
   - `crs`: `"EPSG:32643"`
   - `class_breakdown`: complete feature counts and lengths for primary, secondary, and tertiary tiers.
   - `assumed_uncalibrated`: `True`
2. **Consumer Side:** [`src/jaladhar/solver/state.py`](../src/jaladhar/solver/state.py) (`load_domain`) asserts these exact fields at load time when drains are enabled.
3. **Verification (V5 Invariant):** [`tests/test_terrain_drains.py`](../tests/test_terrain_drains.py):: `test_consumer_side_drain_manifest_assertion_red_under_mutations` demonstrates that deliberate corruptions of `source`, `crs`, or `assumed_uncalibrated` fail loudly with descriptive exceptions.
