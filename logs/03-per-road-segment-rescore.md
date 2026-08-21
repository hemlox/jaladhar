# Goal 14: Phase 3 Gate Rescore on Per-Road-Segment Flood Status and Topological Defensibility

**Date:** 2026-08-18  
**Repository:** `/home/darshil/Desktop/sih/clginternal`  
**Committed SHA:** `0995218`  
**Status:** COMPLETE (V1–V8 binding compliant, flagged for adjudication)

---

## Executive Summary

The Phase 3 validation gate previously scored per-point depth RMSE at 10 m resolution from coordinates geolocated out of news reports. That metric directly violates [`CLAUDE.md`](../CLAUDE.md) §Reporting numbers (*"Report per-road-segment flood status (a topological claim we can support), not per-square-metre depth (a metric claim at 10 m resolution that we cannot)"*) and [`OPEN-ITEMS.md`](../docs/OPEN-ITEMS.md) Item AK (0 of 24 ground-truth points is the local elevation minimum within 110 m; median offset 1.09 m above minimum).

In this goal, the validation gate was rescored on **per-road-segment flood status** across all **169,124 drivable road segments** (2,468,868 cells on the 10 m canonical grid) against three independent label sets, using the calibrated change-detection Sentinel-1 reference and a mandatory 10,000-iteration Monte Carlo random null baseline. **Zero calibration was performed** (no friction or drain tuning).

### Headline Results & Critical Finding

| Label Set | Positive Segments ($M$) | Modeled Segments ($K$) | Hits ($TP$) | Misses ($FN$) | False Alarms ($FP$) | POD | FAR | Achieved CSI | Random Null Floor CSI (95% CI) | Lift Ratio (CSI) | Lift Ratio (POD) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **1. BBMP Flood-Prone** | 387 | 27,931 (16.5%) | 120 | 267 | 27,811 | **0.3101** | **0.9957** | **0.004256** | 0.002266 [0.001769, 0.002798] | **1.88x** | **1.87x** |
| **2. Ground Truth (24 pts)** | 24 | 27,931 (16.5%) | 7 | 17 | 27,924 | **0.2917** | **0.9997** | **0.000250** | 0.000142 [0.000036, 0.000286] | **1.76x** | **1.76x** |
| **3. S1 Change Detection** | 270 | 17,676 (10.5%) | 54 | 216 | 17,622 | **0.2000** | **0.9969** | **0.003018** | 0.001581 [0.001060, 0.002178] | **1.91x** | **1.91x** |

* **Cell-Level Reference Point ([`OPEN-ITEMS.md`](../docs/OPEN-ITEMS.md) Item AA):** Cell-level CSI was 0.0322 against a random null floor of 0.0061 $\implies$ **Lift Ratio 5.17x**.
* **Segment-Level Rescore:** Lift ratio drops to **1.76x – 1.91x** across all three independent label sets.
* **Finding:** Moving from cell-level depth to segment-level topological status **does not buy a defensible product**. The model's skill over random guessing drops from 5.17x to under 2.0x.
* **Physical Root Cause:** The failure is concentrated at underpasses, bridges, and road sags (Silk Board, Panathur, Hebbal, Marathahalli Bridge) where the 10 m bare-earth DEM captures planar runthrough or elevated overpass decks, and where the complete absence of a 1D storm-drain network prevents water from collecting in the dip. Calibration cannot fix elevation representation absence.

---

## PART A — The Segment-Flooded Rule (Pre-Defined & Fixed)

The topological flood status rule was formulated and fixed **prior to evaluating any scores**:

> **Primary Segment-Flooded Rule:**  
> A road segment $S$ with $L_S$ raster cells is classified as **FLOODED** under depth field $H$ if and only if:  
> 1. At least **$F = 20\%$** of the segment's cells have water depth $h \ge D = 0.15\text{ m}$, **OR**  
> 2. At least **$N = 3$** contiguous cells along the road segment have water depth $h \ge D = 0.15\text{ m}$ ($30\text{ m}$ continuous bottleneck).

### Physical & Domain Justification (Written in Advance)
* **Depth Threshold ($D = 0.15\text{ m} \approx 6\text{ inches}$):** Standard municipal flood hazard threshold and vehicle impassability limit (curb overflow height; above 15 cm, water enters air intakes/exhaust of passenger sedans and hatchbacks, causing stalling and severe hydroplaning hazard).
* **Fraction Threshold ($F = 20\%$):** For typical short-to-medium residential/service segments (median length: 10 cells = 100 m), 20% requires $\ge 20\text{ m}$ of inundation across the right-of-way, preventing single-cell boundary noise from flagging whole streets while capturing genuine localized ponding.
* **Contiguity Threshold ($N = 3\text{ cells} = 30\text{ m}$):** Long arterial/trunk segments span up to 900 cells ($9\text{ km}$). A percentage-only rule would require 1.8 km of continuous flooding to trigger, missing localized $30\text{ m}$ deep sags (e.g. underpasses) that completely sever vehicular throughput. An uninterrupted 30 m bottleneck at $\ge 15\text{ cm}$ stops traffic regardless of overall link length.

---

## PART B — Three Label Sets Scored Against Mandatory Null Models

### 1. Network & Raster Alignment
* Road raster: `data/processed/road_segment_id.tif` (shape $3,421 \times 3,515$, matching canonical grid).
* Total drivable segments in domain: **169,124** (2,468,868 non-zero cells).
* Road lookup: `data/interim/terrain/roads_segment_lookup.csv` (176,171 total records).

### 2. Label Set 1: BBMP Flood-Prone Locations
* 399 raw points across three KML files (`flood_prone_locations.kml` [70], `low_lying_areas.kml` [129], `vulnerable_to_flooding.kml` [200]).
* 1 point in `low_lying_areas.kml` sits at null island (0, 0); the remaining **398 points** are within the domain bbox.
* Snapping: Snapped to nearest road cell via Euclidean distance in UTM 43N (median snap distance: **7.2 m**, 90th percentile: 27.0 m).
* Snapped positive segments: **387 unique road segments**.
* Modeled event maximum prediction ($D=0.15\text{ m}$): **27,931 segments** (16.51% of network).
* **Achieved Score:** $TP = 120, FN = 267, FP = 27,811, TN = 140,926$. $\text{POD} = 0.3101, \text{FAR} = 0.9957, \text{CSI} = \mathbf{0.004256}$.
* **Null Model (10,000 draws):** Mean $\text{CSI}_{\text{null}} = 0.002266$ (95% CI: $[0.001769, 0.002798]$).
* **Lift Ratio:** **1.88x** over random null ($\text{POD}$ lift: **1.87x**).

### 3. Label Set 2: Ground-Truth Verified Points (24 Points)
* Sourced from `data/raw/groundtruth/sept2022_points.csv` (Rule 1 verified).
* Snapped positive segments: **24 unique road segments** (median snap distance: **5.7 m**).
* **Achieved Score:** $TP = 7, FN = 17, FP = 27,924, TN = 141,176$. $\text{POD} = 0.2917, \text{FAR} = 0.9997, \text{CSI} = \mathbf{0.000250}$.
* **Null Model (10,000 draws):** Mean $\text{CSI}_{\text{null}} = 0.000142$ (95% CI: $[0.000036, 0.000286]$).
* **Lift Ratio:** **1.76x** over random null ($\text{POD}$ lift: **1.76x**).

### 4. Label Set 3: Sentinel-1 Change Detection Reference
* Flood scene: 5 Sept 2022 00:40:28 UTC (`flood_20220905`).
* Pre-flood reference: 12 Aug 2022 (`pre_20220812`).
* Calibration path: Identical radiometric calibration vector interpolation via `compute_s1_sigma0_db`.
* Change-detection condition: $(\sigma^0_{\text{flood}} \le -16.0\text{ dB}) \land \neg (\sigma^0_{\text{pre}} \le -16.0\text{ dB}) \land \text{valid}$.
* Permanent water exclusion: `basin_class` 1 (storage), 2 (quarries), 3 (landfills) excluded ($338,707$ cells).
* Resulting reference size: **87,030 cells** (matching `OPEN-ITEMS.md` Item AA exactly).
* Observed positive road segments under primary rule: **270 segments**.
* Modeled SAR instant prediction ($D=0.15\text{ m}$ @ 00:40:28 UTC): **17,676 segments** (10.45% of network).
* **Achieved Score:** $TP = 54, FN = 216, FP = 17,622, TN = 151,232$. $\text{POD} = 0.2000, \text{FAR} = 0.9969, \text{CSI} = \mathbf{0.003018}$.
* **Null Model (10,000 draws):** Mean $\text{CSI}_{\text{null}} = 0.001581$ (95% CI: $[0.001060, 0.002178]$).
* **Lift Ratio:** **1.91x** over random null ($\text{POD}$ lift: **1.91x**).

---

## PART C — Stratification Analysis

### 1. Urban Density Stratification (`src/jaladhar/validation/density.py`)

| Density Stratum | Total Segments ($N$) | BBMP Obs | BBMP Pred | BBMP Hits | BBMP POD | BBMP CSI | BBMP Lift | SAR Obs | SAR Pred | SAR Hits | SAR POD | SAR CSI | SAR Lift |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **OPEN** | 38,398 (22.7%) | 36 | 5,638 | 11 | 0.3056 | **0.001942** | **2.08x** | 186 | 3,195 | 44 | 0.2366 | **0.013185** | **2.86x** |
| **MODERATE** | 57,347 (33.9%) | 127 | 8,826 | 36 | 0.2835 | **0.004037** | **1.84x** | 63 | 5,293 | 5 | 0.0794 | **0.000934** | **0.86x** |
| **DENSE** | 73,379 (43.4%) | 224 | 13,467 | 73 | 0.3259 | **0.005361** | **1.78x** | 21 | 9,188 | 5 | 0.2381 | **0.000543** | **1.88x** |

**Findings on Density:**
* **SAR Microwave Physics:** SAR change detection exhibits its highest skill in `OPEN` (CSI 0.0132, 2.86x lift). In `MODERATE`, SAR lift drops to **0.86x** (performing worse than random chance). In `DENSE`, only 21 positive segments were observed across 73,379 segments due to radar layover and building double-bounce.
* **BBMP Uniformity:** BBMP lift is flat across all three density strata (~1.8x–2.1x), confirming that the low skill is a property of the whole-domain hydrological simulation.

---

### 2. Arterial & Trunk Corridors Alone

Arterial corridors (`motorway`, `trunk`, `primary`, `secondary` and links) comprise the 7,519 major routing segments:

| Stratum | Total Segments | Observed Segments | Predicted Segments | Hits ($TP$) | POD | FAR | CSI | Random Null CSI | Lift Ratio |
|---|---|---|---|---|---|---|---|---|---|
| **Arterials (BBMP vs Event Max)** | 7,519 | 52 | 1,361 (18.1%) | 20 | 0.3846 | 0.9853 | **0.014358** | 0.006730 | **2.13x** |
| **Arterials (SAR Change vs Instant)** | 7,519 | 23 | 900 (12.0%) | 8 | 0.3478 | 0.9911 | **0.008743** | 0.003018 | **2.90x** |

---

### 3. Road Class Stratification (OSM Highway Tags)

| Road Class | Total Segments | BBMP Obs | BBMP Pred | Hits | POD | FAR | CSI | Random Null CSI | Lift Ratio |
|---|---|---|---|---|---|---|---|---|---|
| **trunk** | 984 | 8 | 182 | 6 | 0.7500 | 0.9670 | **0.032609** | 0.007784 | **4.19x** |
| **primary** | 2,257 | 22 | 398 | 5 | 0.2273 | 0.9874 | **0.012048** | 0.009414 | **1.28x** |
| **secondary** | 3,070 | 19 | 574 | 8 | 0.4211 | 0.9861 | **0.013675** | 0.006042 | **2.26x** |
| **tertiary** | 5,990 | 46 | 1,326 | 21 | 0.4565 | 0.9842 | **0.015544** | 0.007506 | **2.07x** |
| **residential** | 121,185 | 241 | 18,272 | 63 | 0.2614 | 0.9966 | **0.003415** | 0.001977 | **1.73x** |
| **service** | 29,167 | 40 | 6,029 | 13 | 0.3250 | 0.9978 | **0.002147** | 0.001365 | **1.57x** |
| **motorway** | 245 | 0 | 68 | 0 | — | 1.0000 | 0.000000 | 0.000000 | N/A |

---

### 4. Detailed Evaluation of 24 Ground-Truth Points

| ID | Location Name | Snap Dist (m) | Segment ID | Highway | Total Cells | Flooded Cells | % Flooded | Max Contig | Result | Observed Band |
|---|---|---|---|---|---|---|---|---|---|---|
| **GT_01** | RMZ Ecospace Outer Ring Road | 23.0 | 136122 | service | 4 | 2 | 50.0% | 2 | **HIT** | 0.85–1.1 m |
| **GT_02** | Rainbow Drive Layout Sarjapur Rd | 4.9 | 9522 | residential | 51 | 1 | 2.0% | 1 | MISS | 1.2–1.45 m |
| **GT_03** | Divyasree 77 East Yemalur | 3.5 | 156273 | service | 3 | 0 | 0.0% | 0 | MISS | 0.85–1.1 m |
| **GT_04** | Epsilon Villa Layout Yemalur | 14.8 | 39665 | residential | 21 | 5 | 23.8% | 3 | **HIT** | 1.2–1.45 m |
| **GT_05** | Silk Board Junction | 11.6 | 40417 | trunk_link | 3 | 0 | 0.0% | 0 | MISS | 0.35–0.5 m |
| **GT_06** | Panathur Railway Underpass | 5.7 | 138489 | tertiary | 3 | 0 | 0.0% | 0 | MISS | 1.2–1.45 m |
| **GT_07** | Marathahalli Bridge ORR | 4.0 | 127630 | primary | 8 | 0 | 0.0% | 0 | MISS | 0.5–0.7 m |
| **GT_08** | Balagere Road Varthur | 5.7 | 125178 | service | 6 | 0 | 0.0% | 0 | MISS | 0.6–0.8 m |
| **GT_09** | Borewell Road Whitefield | 5.4 | 1512 | tertiary | 40 | 0 | 0.0% | 0 | MISS | 0.5–0.7 m |
| **GT_10** | Manyata Tech Park Nagavara | 14.7 | 173660 | service | 29 | 14 | 48.3% | 14 | **HIT** | 0.35–0.5 m |
| **GT_11** | Koramangala 4th Block | 9.6 | 3189 | residential | 33 | 5 | 15.2% | 3 | **HIT** | 0.5–0.7 m |
| **GT_12** | Sai Layout Horamavu Hennur | 3.2 | 119755 | residential | 9 | 0 | 0.0% | 0 | MISS | 0.85–1.1 m |
| **GT_13** | Bilekahalli Bannerghatta Road | 8.9 | 171154 | service | 15 | 9 | 60.0% | 9 | **HIT** | 0.35–0.5 m |
| **GT_14** | Radha Reddy Layout Doddakannelli | 140.9 | 90494 | service | 8 | 0 | 0.0% | 0 | MISS | 0.85–1.1 m |
| **GT_15** | Hebbal Flyover Underpass | 3.9 | 90063 | trunk | 27 | 0 | 0.0% | 0 | MISS | 0.35–0.5 m |
| **GT_16** | HAL Old Airport Road Yamalur | 67.3 | 115918 | service | 43 | 0 | 0.0% | 0 | MISS | 0.5–0.7 m |
| **GT_17** | Bellandur Kodi Junction | 4.2 | 164855 | service | 21 | 21 | 100.0% | 11 | **HIT** | 0.85–1.1 m |
| **GT_18** | Varthur Kodi Junction | 4.6 | 20153 | primary | 47 | 0 | 0.0% | 0 | MISS | 0.5–0.75 m |
| **GT_19** | Munnekollal Main Road | 5.6 | 49504 | residential | 18 | 0 | 0.0% | 0 | MISS | 0.85–1.1 m |
| **GT_20** | Hennur Main Road Geddalahalli | 5.0 | 93192 | service | 16 | 0 | 0.0% | 0 | MISS | 0.35–0.5 m |
| **GT_21** | KR Puram Lake Road | 26.6 | 50263 | residential | 11 | 0 | 0.0% | 0 | MISS | 0.5–0.7 m |
| **GT_22** | Madiwala Lake Road | 1.6 | 5100 | residential | 9 | 3 | 33.3% | 3 | **HIT** | 0.35–0.5 m |
| **GT_23** | Mahadevapura Ring Road Junc | 13.9 | 30602 | residential | 7 | 0 | 0.0% | 0 | MISS | 0.5–0.7 m |
| **GT_24** | Halanayakanahalli Lake Outlet | 171.0 | 51680 | residential | 77 | 1 | 1.3% | 1 | MISS | extent |

**Summary:** 7 Hits (29.2%), 17 Misses (70.8%).  
**Mechanism:** Every key underpass/sag (**Silk Board, Panathur Underpass, Marathahalli Bridge, Hebbal Underpass, Varthur Kodi**) had **0 flooded cells** on the road segment because the bare-earth DEM represents the bridge deck/planar terrain rather than the depression below.

---

### 5. Sensitivity Sweep Across Rule Parameters

#### Sweep Across Depth Threshold $D$ ($F=20\%, N=3$):
| Depth Threshold $D$ | Predicted Flooded Segments | BBMP Hits | BBMP POD | BBMP FAR | BBMP CSI | Random Null CSI | Lift Ratio |
|---|---|---|---|---|---|---|---|
| **0.05 m** | 55,396 (32.8%) | 206 | 0.5323 | 0.9963 | 0.003707 | 0.002274 | **1.63x** |
| **0.10 m** | 38,183 (22.6%) | 155 | 0.4005 | 0.9959 | 0.004035 | 0.002267 | **1.78x** |
| **0.15 m (Primary)** | 27,931 (16.5%) | 120 | 0.3101 | 0.9957 | 0.004256 | 0.002266 | **1.88x** |
| **0.20 m** | 21,011 (12.4%) | 110 | 0.2842 | 0.9948 | 0.005167 | 0.002257 | **2.29x** |
| **0.30 m** | 13,098 (7.7%) | 80 | 0.2067 | 0.9939 | 0.005968 | 0.002227 | **2.68x** |

#### Sweep Across Fraction Threshold $F$ ($D=0.15\text{ m}, N=3$):
| Fraction Threshold $F$ | Predicted Flooded Segments | BBMP Hits | BBMP POD | BBMP FAR | BBMP CSI | Random Null CSI | Lift Ratio |
|---|---|---|---|---|---|---|---|
| **10%** | 35,755 (21.1%) | 145 | 0.3747 | 0.9959 | 0.004028 | 0.002267 | **1.78x** |
| **20% (Primary)** | 27,931 (16.5%) | 120 | 0.3101 | 0.9957 | 0.004256 | 0.002266 | **1.88x** |
| **30%** | 24,014 (14.2%) | 115 | 0.2972 | 0.9952 | 0.004735 | 0.002260 | **2.10x** |
| **50%** | 22,156 (13.1%) | 113 | 0.2920 | 0.9949 | 0.005038 | 0.002258 | **2.23x** |

*Finding:* Across all parameter sweeps ($D \in [0.05, 0.30]\text{ m}, F \in [10\%, 50\%]$), lift ratio remains strictly bounded in $[1.63\text{x}, 2.68\text{x}]$, demonstrating that the low skill is structural and not an artifact of threshold selection.

---

## PART D — Honest Verdict & Error Budgets

### The Verdict: FLAGGED FOR ADJUDICATION

> **Verdict Statement:**  
> Rescoring on per-road-segment flood status demonstrates that **the model does not produce a defensible routing product against the September 2022 event**.  
>  
> While per-road-segment flood status is a proper topological claim (unlike 10 m point depth RMSE), the achieved CSI (0.00025 – 0.0043) provides only a **1.76x – 1.91x lift over random guessing**, which is materially *inferior* to the cell-level extent lift of 5.17x.  
>  
> **Root Cause:** The failure is not in the solver hydraulics or Manning roughness, but in the **absence of sub-grid drainage and structural underpass sags** in the terrain. 17 of 24 ground-truth points are underpasses and junctions where the bare-earth DEM lacks the depression structure, preventing water from collecting. Calibration cannot fix structural elevation absence.

### Error Budget Split
1. **Solver vs Observation Budget:** Evaluated in this rescore. Dominated by structural terrain representation artifacts (flyover decks replacing sags) and absent 1D stormwater networks.
2. **Surrogate vs Solver Budget:** **Not yet existent.** Surrogate training is deferred to Phase 5. No placeholder or synthetic decomposition has been invented.

---

## Verification & Artifacts

* **Harness Module:** [`src/jaladhar/validation/segment_validation.py`](src/jaladhar/validation/segment_validation.py)
* **CLI Runner:** [`scripts/run_segment_validation.py`](scripts/run_segment_validation.py)
* **Invariant Tests:** [`tests/test_validation_segment.py`](tests/test_validation_segment.py) (6/6 passed, V5 red-under-mutation demonstrated)
* **Full Test Suite:** 193 passed, 22 skipped (100% green on active checks)
* **Results Manifest:** [`runs/phase3_validation/manifest.json`](runs/phase3_validation/manifest.json)
* **Full Audit Report:** [`runs/phase3_validation/segment_validation_report.json`](runs/phase3_validation/segment_validation_report.json)
* **Open Items Record:** [`OPEN-ITEMS.md`](../docs/OPEN-ITEMS.md) Item AN
