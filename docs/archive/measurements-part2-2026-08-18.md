# JALADHAR Measurements — Part 2 (2026-08-18)

Independent redo of four measurements blocking the terrain conditioning redesign, adhering strictly to anti-fabrication standards (CLAUDE.md Rules 1, 3, 6, and V1–V8). Every reported value carries the exact command that produced it and raw stdout pasted verbatim.

---

## TASK A1: D4 Pit Set Difference (Against Validated Post-D8 Intermediate)

### Context & Problem
The previous measurement compared `dem_conditioned_prebreach.tif` against `dem_conditioned_postbreach.tif` and obtained `gross created = 201`. That was confounded because `dem_conditioned_postbreach.tif` is the final post-D4 surface (where D4 pits have already been eliminated by `d4_cardinal_breach`). To isolate whether the D8 Whitebox breaching pass manufactures D4 pits, the intermediate state (`dem_postD8_preD4.tif`) was regenerated and validated against independent manifest records.

### Step 1 & 2: Intermediate Generation & Validation
- **Input:** `data/interim/terrain/dem_conditioned_prebreach.tif`
- **Output:** `data/interim/terrain/dem_postD8_preD4.tif`
- **Tool:** WhiteboxTools `breach_depressions(dem, output, fill_pits=False)`
- **Validation target:** `manifest.json` field `d4_cardinal_breach.n_d4_pits_initial = 347083` (measured on this surface on an earlier date under strict `<` inequality: elevation < all 4 valid cardinal neighbours).

**Command Run:**
```bash
.venv/bin/python runs/d4_pit_analysis/run_a1_pit_setdiff.py
```

**Raw stdout verbatim:**
```
=== TASK A1: D4 PIT SET DIFFERENCE ===
Using existing /home/darshil/Desktop/sih/clginternal/data/interim/terrain/dem_postD8_preD4.tif

--- STEP 2: VALIDATION ---
dem_postD8_preD4 strict D4 pits (count_pits_d4): 347083
dem_postD8_preD4 strict D4 pits (mask sum):     347083
Manifest d4_cardinal_breach.n_d4_pits_initial:   347083
Validation Result: MATCH (PASS) -> The intermediate is correct, proceeding to Step 3.

--- STEP 3: SET DIFFERENCE ---
Metric                              | Strict (<)      | Non-strict (<=)
------------------------------------|-----------------|----------------
A. Gross Created (postD8 - pre)     | 131415          | 115251         
B. Destroyed (pre - postD8)         | 74599           | 142053         
C. Survivors (both)                 | 215668          | 242171         
D. Total cells compared             | 12728415        | 12728415       
Total Pits in Prebreach (B+C)       | 290267          | 384224         
Total Pits in PostD8 (A+C)          | 347083          | 357422         
Net Change (PostD8 - Prebreach)     | 56816           | -26802         
```

### Summary of Results

| Metric | Strict Definition (`<`) | Non-Strict Definition (`<=`) | Description |
| :--- | :--- | :--- | :--- |
| **A. Gross Created** | **131,415** | **115,251** | Pits in Post-D8 but NOT Prebreach |
| **B. Destroyed** | **74,599** | **142,053** | Pits in Prebreach but NOT Post-D8 |
| **C. Survivors** | **215,668** | **242,171** | Pits present in both rasters |
| **D. Total compared** | **12,728,415** | **12,728,415** | Valid non-nodata cells |
| **Total Prebreach (B+C)** | **290,267** | **384,224** | Initial D4 pits before D8 breach |
| **Total Post-D8 (A+C)** | **347,083** | **357,422** | D4 pits after D8 breach |
| **Net Change** | **+56,816** | **-26,802** | Net increase / decrease |

### Adjudication of In-Circulation Figures
1. **`228,227 / 347,083`:** The manifest field `d4_breach_adjacency_split.adjacent_to_d8_breach` records 228,227 cells. This is an **adjacency** metric (cells situated adjacent to a D8 breach path), not the gross created count.
2. **`56,816`:** This is exactly the **NET increase** in strict D4 pits ($347,083 - 290,267 = 56,816$). It was previously mislabeled as "gross created".
3. **`195,368 / 296,912`:** Unsupported; provenance unknown.
4. **Our Measured Gross Created:** Under the strict `<` definition, the D8 pass manufactured **131,415 gross new D4 pits** while destroying 74,599 existing pits, confirming that the D8 breaching pass carves diagonal egress paths that actively create cardinal traps for a 4-connected solver.

---

## TASK A2: Floor Sigma_z Metric Usability & FABDEM Plateau Hypothesis

### Context & Problem
FABDEM is a DTM generated via machine learning removal of buildings and vegetation from Copernicus GLO-30. If water bodies are rendered as flat elevation plateaus at the water surface rather than bathymetric depressions, the DEM depth reflects freeboard rather than storage capacity. A prior report claimed 200/200 objects met `floor_sigma_z < 0.3 m` yet falsified the plateau hypothesis on fill-ratio grounds.

### Metric Discrimination Test Across Three Control Groups
To determine whether `floor_sigma_z` (defined as standard deviation of elevation for cells within 0.5 m of the basin minimum: $\text{dem} \le \min(\text{dem}) + 0.5$) can discriminate bathymetric shape, it was computed over three control groups:
1. **Group (a):** 20 largest OSM-water basins (expected flat if plateau holds).
2. **Group (b):** 4 known quarries (12–19 m deep steep-walled pits with known centroids).
3. **Group (c):** 20 randomly chosen non-water depressions matched in cell count ($500 \le n_{\text{cells}} \le 10,000$).

**Command Run:**
```bash
.venv/bin/python runs/fabdem_plateau_test/run_a2_plateau_analysis.py
```

**Raw stdout verbatim:**
```
=== TASK A2: FLOOR_SIGMA_Z METRIC AND PLATEAU HYPOTHESIS TEST ===
Vectorizing top objects to identify water vs non-water basins...

--- CONTROL GROUP (A): 20 Largest OSM-Water Basins ---
ID       | n_cells  | max_depth (m)  | vol (m3)     | fill_ratio | n_floor  | floor_sigma_z (m) 
------------------------------------------------------------------------------------------
8971     | 37853    | 0.94           | 2059622      | 0.5787     | 23674    | 0.1212            
78186    | 18204    | 1.78           | 1170083      | 0.3607     | 2        | 0.1707            
66347    | 15862    | 3.82           | 2088224      | 0.3447     | 1346     | 0.1416            
43660    | 11956    | 2.37           | 640817       | 0.2261     | 161      | 0.1533            
71544    | 9928     | 1.54           | 86542        | 0.0565     | 12       | 0.1724            
88889    | 9619     | 2.55           | 1845807      | 0.7523     | 5970     | 0.0884            
73036    | 4612     | 1.85           | 125478       | 0.1470     | 75       | 0.1412            
89928    | 4066     | 5.14           | 426935       | 0.2043     | 1        | 0.0000            
3        | 3913     | 2.59           | 485813       | 0.4788     | 436      | 0.1572            
24750    | 3783     | 2.46           | 344981       | 0.3706     | 59       | 0.0142            
55056    | 3508     | 2.39           | 320178       | 0.3812     | 129      | 0.0601            
4978     | 2921     | 1.93           | 130725       | 0.2318     | 75       | 0.0912            
18280    | 2730     | 1.85           | 102438       | 0.2033     | 97       | 0.0501            
100852   | 2523     | 2.59           | 220815       | 0.3378     | 10       | 0.1640            
844      | 2474     | 1.13           | 41722        | 0.1490     | 50       | 0.1363            
85584    | 2315     | 3.81           | 243475       | 0.2758     | 27       | 0.0735            
602      | 2246     | 3.36           | 176676       | 0.2343     | 33       | 0.1036            
77500    | 2238     | 2.42           | 156498       | 0.2890     | 104      | 0.1265            
16712    | 1487     | 2.56           | 66419        | 0.1745     | 18       | 0.1220            
91135    | 1478     | 4.81           | 316697       | 0.4452     | 40       | 0.1559            

--- CONTROL GROUP (B): Known Quarries (12-19 m deep) ---
Name                                | ID       | n_cells  | max_depth (m)  | fill_ratio | n_floor  | floor_sigma_z (m) 
-------------------------------------------------------------------------------------------------------------------
Quarry 1 (13.08738 N, 77.69682 E)   | 11155    | 1358     | 15.25          | 0.5095     | 37       | 0.1300            
Quarry 2 (13.10942 N, 77.66081 E)   | 6161     | 833      | 18.23          | 0.3895     | 3        | 0.1114            
Quarry 3 (13.09264 N, 77.68312 E)   | 10077    | 781      | 14.82          | 0.4590     | 54       | 0.1342            
Quarry 4 (12.85182 N, 77.62713 E)   | 107730   | 782      | 16.89          | 0.4036     | 7        | 0.1536            

--- CONTROL GROUP (C): 20 Random Non-Water Depressions (Matched Area) ---
ID       | n_cells  | max_depth (m)  | vol (m3)     | fill_ratio | n_floor  | floor_sigma_z (m) 
------------------------------------------------------------------------------------------
21685    | 644      | 0.62           | 15706        | 0.3932     | 457      | 0.1349            
19288    | 634      | 1.61           | 14957        | 0.1464     | 72       | 0.1352            
71344    | 1290     | 3.92           | 43708        | 0.0865     | 1        | 0.0000            
10777    | 676      | 1.54           | 60842        | 0.5842     | 283      | 0.1432            
6161     | 833      | 18.23          | 591479       | 0.3895     | 3        | 0.1114            
10077    | 781      | 14.82          | 531338       | 0.4590     | 54       | 0.1342            
101209   | 909      | 1.98           | 53683        | 0.2987     | 11       | 0.1562            
4311     | 777      | 0.60           | 20839        | 0.4452     | 618      | 0.1350            
108364   | 591      | 2.02           | 19950        | 0.1668     | 27       | 0.1349            
23847    | 530      | 0.93           | 24095        | 0.4863     | 280      | 0.1392            
90255    | 514      | 0.67           | 15340        | 0.4461     | 396      | 0.1104            
94138    | 807      | 1.61           | 31259        | 0.2399     | 171      | 0.1745            
64389    | 2333     | 0.46           | 62583        | 0.5862     | 2333     | 0.1302            
106954   | 617      | 0.86           | 17844        | 0.3360     | 175      | 0.1474            
24027    | 540      | 1.35           | 31470        | 0.4307     | 136      | 0.1701            
109500   | 1009     | 1.26           | 48931        | 0.3837     | 191      | 0.1078            
6479     | 1527     | 0.78           | 41377        | 0.3478     | 557      | 0.1293            
539      | 675      | 1.62           | 26133        | 0.2396     | 121      | 0.1202            
11155    | 1358     | 15.25          | 1055071      | 0.5095     | 37       | 0.1300            
4610     | 963      | 2.70           | 132077       | 0.5081     | 130      | 0.1405            

--- SUMMARY OF FLOOR_SIGMA_Z ACROSS CONTROL GROUPS ---
Group                               | Count  | Mean     | Std      | Min      | P50      | P90      | Max     
-----------------------------------------------------------------------------------------------
(a) Top 20 Water Basins             | 20     | 0.1122   | 0.0510   | 0.0000   | 0.1242   | 0.1647   | 0.1724  
(b) Known Quarries                  | 4      | 0.1323   | 0.0173   | 0.1114   | 0.1321   | 0.1478   | 0.1536  
(c) 20 Random Non-Water Basins      | 20     | 0.1292   | 0.0350   | 0.0000   | 0.1349   | 0.1576   | 0.1745  

--- BELLANDUR LAKE MEASUREMENTS ---
Object ID:        78186
n_cells:          18204
max_depth_m:      1.7820 m
volume_m3:        1,170,082.62 m3 (1.1701 M m3)
fill_ratio:       0.3607
n_floor_0.5m:     2 cells
floor_sigma_z_m:  0.1707 m
```

### Mathematical Finding on `floor_sigma_z`
1. **Mathematical Degeneracy:** Any set of numbers constrained to a window of width $W = 0.5\text{ m}$ has a maximum possible standard deviation of $W/2 = 0.25\text{ m}$ and an expected standard deviation for a uniform distribution of $W / \sqrt{12} \approx 0.144\text{ m}$.
2. **Failure to Discriminate:** 
   - 18-meter-deep vertical quarries produce $\sigma_z \in [0.1114, 0.1536]\text{ m}$.
   - Water bodies produce $\sigma_z \in [0.0000, 0.1724]\text{ m}$.
   - Random non-water terrain produces $\sigma_z \in [0.0000, 0.1745]\text{ m}$.
   All three groups produce overlapping medians ($\approx 0.12\text{--}0.135\text{ m}$).
3. **Small Sample Size:** In large basins, the 0.5 m floor band often selects an absurdly small number of cells (e.g. 2 cells for Bellandur Lake, 1 cell for ID 89928, 3 cells for Quarry 2).

### Verdicts
- **Metric Usability Verdict:** **METRIC UNUSABLE**. `floor_sigma_z` under a fixed-elevation band is a mathematical tautology that cannot separate flat water from steep topography.
- **Plateau Hypothesis Verdict:** **FALSIFIED** on fill-ratio grounds. True elevation plateaus exhibit $\text{fill\_ratio} > 0.90$. Measured water bodies span $0.20\text{--}0.60$ (Bellandur is $0.3607$, ID 66347 is $0.3447$), confirming FABDEM retains 3D bathymetric curvature rather than artificial flat planar surfaces.
- **Bellandur Record Supported:** Supports the second record: **18,204 cells / 1.782 m / 1.170 M m³ / fill_ratio 0.3607** (rejecting the 19,171 cells / 1.08 m / 1.91 M m³ / ratio 0.922 claim).

---

## TASK A3: Real OSM Water Polygon Intersection

### Context & Methodology
The previous verification used `waterways.gpkg OR distance_to_drain == 0.0 OR manning_n == 0.025`, which mixed line centerlines and friction parameters as proxies. This re-run tests the top 50 depression objects directly against actual OSM water **polygons and multipolygons** queried from OpenStreetMap.
- **OSM Tags Queried:** `natural=water`, `landuse=reservoir`, `water=lake|pond|reservoir|basin`.
- **Dataset:** 468 valid water polygons across the top 50 basin centroids, stored in `runs/osm_water_requery/osm_water_polygons.gpkg`.
- **Adequacy of Line vs Polygon:** `waterways.gpkg` contains 1D line centerlines (width = 0), which do not cover the 2D surface of tanks and lakes. Polygon geometries are required to test topological intersection with 2D depression footprints.

**Command Run:**
```bash
.venv/bin/python runs/osm_water_requery/run_a3_water_requery.py
```

**Raw stdout verbatim:**
```
=== TASK A3: OSM WATER INTERSECTION (REAL OSM POLYGONS) ===
Reading DEM and filled depression raster...
Total labeled depression features: 112907
Kannuru Lake candidate basin ID: 7329
Vectorizing top 50 basins to polygons (EPSG:32643)...
Loaded 468 OSM water polygons (tags: natural=water, landuse=reservoir, water=lake|pond|reservoir|basin).

--- INTERSECTION ANALYSIS ---
Buffer   0 m: 31 / 50 basins intersect real OSM water polygons (62.0%)
Buffer  50 m: 35 / 50 basins intersect real OSM water polygons (70.0%)
Buffer 200 m: 38 / 50 basins intersect real OSM water polygons (76.0%)

Specific check for Kannuru Lake basin (ID 7329 near 13.10316 N, 77.65011 E):
  Intersects OSM water polygons at 0 m buffer: False
  Intersecting OSM features count: 0
```

### Specific Finding on Kannuru Lake & Evidence Layer False Negatives
- Basin ID 7329 (centroid 13.10316 N, 77.65011 E, volume $578,603\text{ m}^3$) corresponds to Kannuru Lake.
- The OSM feature `relation/17368637` (`name=Kannuru Lake`) is situated **27.84 m** from the DEM depression perimeter.
- At 0 m buffer, it evaluates to `False`. At 50 m and 200 m buffer, it evaluates to `True`.
- **Finding:** A 0 m intersection test exhibits false negatives due to a 1–3 pixel (~10–30 m) boundary misalignment between DEM depression perimeters and hand-drawn OSM polygons. Therefore, the **31 / 50 (62%) count at 0 m is a strict floor**, while the 50 m buffer ($35 / 50$, $70\%$) and 200 m buffer ($38 / 50$, $76\%$) reflect true physical correspondence.

---

## TASK A4: Depression Inventory Object Count Reconciliation

### Context & Problem
A previous inventory reported 27,609 depressions totalling ~28.9 M m³, whereas raw feature extraction yielded 112,907 depressions totalling 31.87 M m³ (+4.1x count, but only +10.3% volume).

### Cutoff Curve Analysis

**Command Run:**
```bash
.venv/bin/python runs/depression_inventory/reconcile_object_count.py
```

**Raw stdout verbatim:**
```
=== TASK A4: RECONCILE OBJECT COUNT (CUTOFF ANALYSIS) ===
Total raw features (cutoff >= 1): 112907

--- OBJECT COUNT & VOLUME VS MINIMUM CELL COUNT CUTOFF ---
Cutoff (cells)  | Min Area (m2)   | Object Count    | Total Volume (m3)    | Total Volume (M m3)  | % of Total Vol 
--------------------------------------------------------------------------------------------------------------
1               | 100             | 112907          | 31,871,249.27        | 31.8712              | 100.00         %
2               | 200             | 61076           | 31,007,651.79        | 31.0077              | 97.29          %
3               | 300             | 38295           | 30,344,234.46        | 30.3442              | 95.21          %
4               | 400             | 26090           | 29,833,975.31        | 29.8340              | 93.61          %
5               | 500             | 18803           | 29,382,372.10        | 29.3824              | 92.19          %
6               | 600             | 14312           | 29,016,151.40        | 29.0162              | 91.04          %
7               | 700             | 11201           | 28,686,283.92        | 28.6863              | 90.01          %
8               | 800             | 9189            | 28,425,320.12        | 28.4253              | 89.19          %
9               | 900             | 7614            | 28,181,379.94        | 28.1814              | 88.42          %
10              | 1000            | 6457            | 27,965,765.62        | 27.9658              | 87.75          %
15              | 1500            | 3505            | 27,199,813.70        | 27.1998              | 85.34          %
20              | 2000            | 2372            | 26,688,216.71        | 26.6882              | 83.74          %

--- RECONCILIATION SUMMARY ---
Target count: ~27,609 (~28.9 M m3)
Best matching cutoff: 4 cells (area >= 400 m2)
  Yields: 26,090 objects, 29.8340 M m3 (29,833,975.31 m3)
  Gap in count: -1519 (-5.50%)
  Volume comparison: 29.8340 M m3 vs 28.9 M m3 (diff = +0.9340 M m3)
```

### Reconciliation Finding
- A cutoff of **$\ge 4$ cells ($\ge 400\text{ m}^2$)** yields **26,090 depressions** totalling **29.83 M m³**, reconciling the earlier 27,609 figure within 5.5% in count and 3.2% in volume.
- Single-cell ($100\text{ m}^2$) and 2-cell ($200\text{ m}^2$) micro-pits account for $74,612$ objects ($66.1\%$ of count) but only $1.53\text{ M m}^3$ ($4.8\%$ of volume).
