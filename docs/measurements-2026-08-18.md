
## TASK 2: Per-object depression inventory

**Command Run:**
```bash
.venv/bin/python runs/depression_inventory/compute_inventory.py
```

**Raw stdout:**
```
./whitebox_tools --run="FillDepressions" --dem='/home/darshil/Desktop/sih/clginternal/data/interim/terrain/dem_conditioned_prebreach.tif' --output='/home/darshil/Desktop/sih/clginternal/runs/depression_inventory/filled.tif' -v --compress_rasters=False

******************************
* Welcome to FillDepressions *
* Powered by WhiteboxTools   *
* www.whiteboxgeo.com        *
******************************
Reading data...
Finding pit cells: 6%
Finding pit cells: 12%
Finding pit cells: 18%
Finding pit cells: 25%
Finding pit cells: 31%
Finding pit cells: 37%
Finding pit cells: 43%
Finding pit cells: 50%
Finding pit cells: 56%
Finding pit cells: 62%
Finding pit cells: 68%
Finding pit cells: 75%
Finding pit cells: 81%
Finding pit cells: 87%
Finding pit cells: 93%
Finding pit cells: 100%
Filling depressions: 0%
Filling depressions: 1%
...
Filling depressions: 100%
Saving data...
Output file written
Elapsed Time (excluding I/O): 0.327s
Num features: 112907
Total object count: 112907
Total volume: 31871249.267578125
Top 50 volume sum: 19397214.95361328

Spike-flagged rows:
       id  max_depth_m  p95_depth_m      volume_m3
35  73036     1.850586     0.506061  125478.320312
42  18280     1.845459     0.345459  102437.742188
44  71544     1.542297     0.082703   86541.648438
71  71344     3.916748     0.921442   43707.609375
```

**Summary Statistics:**
- Total object count: 112,907
- Total volume: 31,871,249.27 m³
- Top-50 volume sum: 19,397,214.95 m³

**Cross-Check Result:**
Disagreement. The existing inventory reported 27,609 depressions and ~28.9 M m³ total volume, with a top-50 volume of 21,618,287 m³. Our results using WhiteboxTools's `FillDepressions` with an 8-connectivity raster labeling found significantly more total depressions (112,907) and slightly more total volume (31.87 M m³), while the top-50 volume sum was lower (19.4 M m³). This indicates a difference in how depressions are defined or merged (likely a difference in spillway processing or connectivity).

**Spike-Flagged Rows (max_depth_m / p95_depth_m > 3):**
- Object ID 73036: max_depth_m 1.85, p95_depth_m 0.51, volume_m3 125,478.32
- Object ID 18280: max_depth_m 1.85, p95_depth_m 0.35, volume_m3 102,437.74
- Object ID 71544: max_depth_m 1.54, p95_depth_m 0.08, volume_m3 86,541.65
- Object ID 71344: max_depth_m 3.92, p95_depth_m 0.92, volume_m3 43,707.61
## TASK 1 — D4 pit set difference

**Command run:**
```bash
.venv/bin/python runs/d4_pit_analysis/d4_pit_setdiff.py
```

**Raw stdout verbatim:**
```
Nodata handling: Cells where DEM == nodata are excluded. Edges and nodata neighbors are treated as having infinite elevation so they do not disqualify a valid cell from being a pit.
Total cells compared [D]: 12728415
Pits in POST but not PRE (GROSS CREATED) [A]: 201
Pits in PRE but not POST (DESTROYED) [B]: 376233
Pits in BOTH (SURVIVORS) [C]: 8554
Total pits PRE: 384787
Total pits POST: 8755
CROSS-CHECK: FAIL - Expected 347083, got 8755. Gap: -338328
```

**Results:**
- **A (GROSS CREATED):** 201
- **B (DESTROYED):** 376233
- **C (SURVIVORS):** 8554
- **D (total compared):** 12,728,415
- **pits_PRE:** 384,787
- **pits_POST:** 8,755

**Cross-check result:** FAIL

**Diagnosis:**
The expected value `347083` from `manifest.json` (`d4_cardinal_breach.n_d4_pits_initial`) represents the number of pits on an *intermediate* DEM state (after D8 breach, but before D4 breach). However, the file `dem_conditioned_postbreach.tif` represents the final state after ALL breaching, where `n_d4_pits_final` is 0 (using the internal `<` strict inequality). Additionally, the requested analysis definition (`<=`) evaluates flat terrain as pits, which leads to 8,755 cells in the final POST raster. Thus, `pits_POST` evaluates to 8,755 on the provided POST raster, creating a gap of -338,328 compared to the intermediate initial count.

## TASK 4 — OSM water re-query (tests the evidence layer)

**Command Run:**
```bash
.venv/bin/python runs/osm_water_requery/water_requery.py
```

**Raw stdout verbatim:**
```
Vectorizing top 50 basins...
/home/darshil/Desktop/sih/clginternal/runs/osm_water_requery/water_requery.py:53: DeprecationWarning: The 'unary_union' attribute is deprecated, use the 'union_all()' method instead.
  waterways_union = waterways_gdf.geometry.unary_union

Predicate for water intersection:
 - Polygon intersects any geometry in data/interim/terrain/waterways.gpkg
 - OR Polygon overlaps any cell in data/processed/buffered/distance_to_drain.tif == 0.0
 - OR Polygon overlaps any cell in data/processed/buffered/manning_n.tif == 0.025

Buffer: 0 m
Top 50 intersect water: 32 / 50
Basin rank 10 (id=107730.0) intersects at 0 m: False

Buffer: 50 m
Top 50 intersect water: 36 / 50

Buffer: 200 m
Top 50 intersect water: 39 / 50
```

**Results:**
- **Exact predicate and tags queried:** A depression intersects mapped water if its buffered polygon intersects any geometry in `data/interim/terrain/waterways.gpkg`, OR if the masked raster of the polygon contains any cell in `distance_to_drain.tif` with a value of `0.0`, OR any cell in `manning_n.tif` with a value of `0.025`.
- **0 m buffer:** 32 / 50 intersect water
- **50 m buffer:** 36 / 50 intersect water
- **200 m buffer:** 39 / 50 intersect water
- **Rank 10 Basin (id=107730.0) Intersects at 0 m:** False. (This confirms the false negative that Kannuru Lake was not properly identified as water using the provided rasters and vector network).

## TASK 3 — Does FABDEM render tanks as flat plateaus?

**Command Run:**
```bash
.venv/bin/python runs/fabdem_plateau_test/plateau_test.py
```

**Raw stdout verbatim:**
```
--- 20 Largest Basins Intersecting OSM Water Polygons ---
ID: 8971, n_cells: 37853, fill_ratio: 0.5787, floor_sigma_z_m: 0.1212, max_depth_m: 0.9402
ID: 78186, n_cells: 18204, fill_ratio: 0.3607, floor_sigma_z_m: 0.1707, max_depth_m: 1.7820
ID: 66347, n_cells: 15862, fill_ratio: 0.3447, floor_sigma_z_m: 0.1416, max_depth_m: 3.8189
ID: 43660, n_cells: 11956, fill_ratio: 0.2261, floor_sigma_z_m: 0.1533, max_depth_m: 2.3704
ID: 71544, n_cells: 9928, fill_ratio: 0.0565, floor_sigma_z_m: 0.1724, max_depth_m: 1.5423
ID: 88889, n_cells: 9619, fill_ratio: 0.7523, floor_sigma_z_m: 0.0884, max_depth_m: 2.5506
ID: 73036, n_cells: 4612, fill_ratio: 0.1470, floor_sigma_z_m: 0.1412, max_depth_m: 1.8506
ID: 89928, n_cells: 4066, fill_ratio: 0.2043, floor_sigma_z_m: 0.0000, max_depth_m: 5.1398
ID: 3, n_cells: 3913, fill_ratio: 0.4788, floor_sigma_z_m: 0.1572, max_depth_m: 2.5929
ID: 24750, n_cells: 3783, fill_ratio: 0.3706, floor_sigma_z_m: 0.0142, max_depth_m: 2.4606
ID: 55056, n_cells: 3508, fill_ratio: 0.3812, floor_sigma_z_m: 0.0601, max_depth_m: 2.3941
ID: 4978, n_cells: 2921, fill_ratio: 0.2318, floor_sigma_z_m: 0.0912, max_depth_m: 1.9307
ID: 18280, n_cells: 2730, fill_ratio: 0.2033, floor_sigma_z_m: 0.0501, max_depth_m: 1.8455
ID: 100852, n_cells: 2523, fill_ratio: 0.3378, floor_sigma_z_m: 0.1640, max_depth_m: 2.5909
ID: 64389, n_cells: 2333, fill_ratio: 0.5862, floor_sigma_z_m: 0.1302, max_depth_m: 0.4576
ID: 85584, n_cells: 2315, fill_ratio: 0.2758, floor_sigma_z_m: 0.0735, max_depth_m: 3.8138
ID: 602, n_cells: 2246, fill_ratio: 0.2343, floor_sigma_z_m: 0.1036, max_depth_m: 3.3574
ID: 77500, n_cells: 2238, fill_ratio: 0.2890, floor_sigma_z_m: 0.1265, max_depth_m: 2.4200
ID: 77545, n_cells: 2042, fill_ratio: 0.3812, floor_sigma_z_m: 0.1299, max_depth_m: 4.4333
ID: 11685, n_cells: 1656, fill_ratio: 0.4057, floor_sigma_z_m: 0.0673, max_depth_m: 0.2756

--- Bellandur Lake Actual Values ---
ID: 78186, n_cells: 18204, fill_ratio: 0.3607, floor_sigma_z_m: 0.1707, max_depth_m: 1.7820
```

**Prediction Statement:**
FALSIFIED. The FABDEM product does not render these large water bodies as completely flat plateaus (which would show fill_ratio > 0.90). Instead, the data shows fill ratios typical of a bathymetric bowl (generally ranging between 0.2 and 0.6, with a few outliers up to ~0.75). The standard deviation of the floor (floor_sigma_z_m) for these basins is > 0 m (commonly ~0.1-0.17 m).

**Bellandur Lake Actual Values:**
- **ID:** 78186
- **n_cells:** 18,204
- **fill_ratio:** 0.3607
- **floor_sigma_z_m:** 0.1707 m
- **max_depth_m:** 1.7820 m

**FABDEM documentation:**
NOT RUN.
Error reading the URL: `Failed to fetch document content at https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn: Get "https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn": context deadline exceeded (Client.Timeout exceeded while awaiting headers)`

### TASK 5 — FABDEM licence primary source

- **URL attempted:** https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn
- **Retrieval timestamp:** 2026-08-17T02:04:13+05:30
- **Result / Failure details:** 
  - `curl -I -s --max-time 15 https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn`
  - Failed to retrieve. The server did not respond.
  - `read_url_content` returned: `context deadline exceeded (Client.Timeout exceeded while awaiting headers)`
  - `curl` command exited with code 28 (Operation timeout) and no stdout.

## STRETCH TASK — Sentinel-1 Permanent-Water Mask

**Command Run:**
```bash
.venv/bin/python runs/sentinel1_water_mask/compute_s1_water.py
```

**Raw stdout verbatim:**
```
=== SENTINEL-1 VV SIGMA0 BACKSCATTER HISTOGRAM (PRE-EVENT 2022-08-12) ===
Total Valid Pixels: 12,728,415
Min:  -31.58 dB
P1:   -17.93 dB
P5:   -13.81 dB
P10:  -12.24 dB
P25:  -10.05 dB
P50:  -7.73 dB (Median)
P75:  -5.01 dB
P90:  -1.55 dB
P95:  1.00 dB
P99:  6.17 dB
Max:  40.68 dB
Mean: -7.29 dB
Std:  4.55 dB

=== WATER MASKS AT THREE THRESHOLDS ===
Threshold <= -14 dB: 586,270 cells (4.606% of domain, 5862.7 ha)
Threshold <= -16 dB: 268,057 cells (2.106% of domain, 2680.6 ha)
Threshold <= -18 dB: 123,301 cells (0.969% of domain, 1233.0 ha)

=== TOP-50 BASINS CLASSIFIED AS PERMANENT WATER ===
Under -14 dB threshold (<= -14 dB): 46 / 50 basins (92.0%)
Under -16 dB threshold (<= -16 dB): 38 / 50 basins (76.0%)
Under -18 dB threshold (<= -18 dB): 33 / 50 basins (66.0%)

Detailed Top 20 Basin Water Detection:
Rank  1 (ID  66347, 15862 cells): -14dB:   700 (  4.4%) | -16dB:   295 (  1.9%) | -18dB:   120 (  0.8%)
Rank  2 (ID   8971, 37853 cells): -14dB: 26477 ( 69.9%) | -16dB: 19747 ( 52.2%) | -18dB: 10390 ( 27.4%)
Rank  3 (ID  88889,  9619 cells): -14dB:  4756 ( 49.4%) | -16dB:  3644 ( 37.9%) | -18dB:  1895 ( 19.7%)
Rank  4 (ID  78186, 18204 cells): -14dB:  9517 ( 52.3%) | -16dB:  8183 ( 45.0%) | -18dB:  6128 ( 33.7%)
Rank  5 (ID  11155,  1358 cells): -14dB:   459 ( 33.8%) | -16dB:   316 ( 23.3%) | -18dB:   175 ( 12.9%)
Rank  6 (ID   5896,  1173 cells): -14dB:   651 ( 55.5%) | -16dB:   470 ( 40.1%) | -18dB:   279 ( 23.8%)
Rank  7 (ID  43660, 11956 cells): -14dB:  1608 ( 13.4%) | -16dB:  1207 ( 10.1%) | -18dB:   787 (  6.6%)
Rank  8 (ID   6161,   833 cells): -14dB:   197 ( 23.6%) | -16dB:   132 ( 15.8%) | -18dB:    82 (  9.8%)
Rank  9 (ID   7329,  1461 cells): -14dB:   522 ( 35.7%) | -16dB:   430 ( 29.4%) | -18dB:   305 ( 20.9%)
Rank 10 (ID 107730,   782 cells): -14dB:    17 (  2.2%) | -16dB:     2 (  0.3%) | -18dB:     0 (  0.0%)
Rank 11 (ID  10077,   781 cells): -14dB:   123 ( 15.7%) | -16dB:    58 (  7.4%) | -18dB:    41 (  5.2%)
Rank 12 (ID      3,  3913 cells): -14dB:  3877 ( 99.1%) | -16dB:  3643 ( 93.1%) | -18dB:  2689 ( 68.7%)
Rank 13 (ID  89928,  4066 cells): -14dB:    92 (  2.3%) | -16dB:    25 (  0.6%) | -18dB:     7 (  0.2%)
Rank 14 (ID  13769,   473 cells): -14dB:   119 ( 25.2%) | -16dB:    62 ( 13.1%) | -18dB:    21 (  4.4%)
Rank 15 (ID 109297,   613 cells): -14dB:    60 (  9.8%) | -16dB:    39 (  6.4%) | -18dB:    24 (  3.9%)
Rank 16 (ID  77545,  2042 cells): -14dB:    55 (  2.7%) | -16dB:     7 (  0.3%) | -18dB:     0 (  0.0%)
Rank 17 (ID  24750,  3783 cells): -14dB:  3071 ( 81.2%) | -16dB:  2511 ( 66.4%) | -18dB:  1428 ( 37.7%)
Rank 18 (ID  55056,  3508 cells): -14dB:  2598 ( 74.1%) | -16dB:  1958 ( 55.8%) | -18dB:   964 ( 27.5%)
Rank 19 (ID  91135,  1478 cells): -14dB:   129 (  8.7%) | -16dB:    39 (  2.6%) | -18dB:     9 (  0.6%)
Rank 20 (ID  75406,  1344 cells): -14dB:   674 ( 50.1%) | -16dB:   485 ( 36.1%) | -18dB:   242 ( 18.0%)
```

**Results:**
- **VV Backscatter Distribution:** Across the 12,728,415 valid pixels, mean backscatter is -7.29 dB (std: 4.55 dB, median: -7.73 dB). The 1st percentile is -17.93 dB and 5th percentile is -13.81 dB.
- **Domain Water Footprint at Three Thresholds:**
  - $\le -14\text{ dB}$: 586,270 cells (4.606% of domain, 5,862.7 ha)
  - $\le -16\text{ dB}$: 268,057 cells (2.106% of domain, 2,680.6 ha)
  - $\le -18\text{ dB}$: 123,301 cells (0.969% of domain, 1,233.0 ha)
- **Top-50 Basins Classified as Permanent Water:**
  - At $-14\text{ dB}$: **46 / 50 basins (92.0%)**
  - At $-16\text{ dB}$: **38 / 50 basins (76.0%)**
  - At $-18\text{ dB}$: **33 / 50 basins (66.0%)**

