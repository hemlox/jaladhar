# Phase 2 Acceptance Diagnostics: Courant Localisation, Depression Inventory & Cut Attribution

**Date:** 2026-08-16  
**Repository:** `/home/darshil/Desktop/sih/clginternal`  
**Run Commit:** `78610c2fccfcac58c3a2ec84f19ff753496c884b`  
**Domain Grid:** $3521 \times 3615$ buffered grid ($12,728,415$ cells), $3421 \times 3515$ canonical subgrid ($12,024,815$ cells), $10.0$ m resolution.

---

## 1. Courant Localisation Analysis

### Numerical Parameters in Use
- **`physics.depth_threshold_m`**: `0.001 m` ($1.0$ mm)
- **`physics.hf_floor_m`**: `0.001 m` ($1.0$ mm)
- **`wetdry.ramp_width_m`**: `0.001 m` ($1.0$ mm)
- **`wetdry.mode`**: `"hard"`
- **`timestep.cfl_alpha` (Selected Courant Ceiling)**: `0.7000`

### Depth Distribution of Cell Setting Realized Max Courant on Exceeding Steps
In the 2D local-inertial scheme, $\text{Courant}(r, c) = \frac{\Delta t \sqrt{g \cdot h(r, c)}}{\Delta x}$. Across any given step, the cell that sets the realized maximum Courant is **identically the cell with domain maximum depth $h_{\max}$**.

For the **`11,443`** timesteps ($29.30\%$ of the run) where realized Courant exceeded selected $\alpha = 0.7000$:
- **$P_{10}$ Depth:** **`4.067358 m`**
- **$P_{50}$ (Median) Depth:** **`17.946772 m`**
- **$P_{90}$ Depth:** **`23.303246 m`**
- **$P_{99}$ Depth:** **`23.388235 m`**
- **Min Depth:** `0.087951 m`
- **Max Depth:** `25.028871 m`

### Thin-Film vs. Deep Water Breakdown
- **Shallow / Thin-film cells ($h < 0.5$ m):** **`47` steps (`0.41%`)**
- **Deep water ($h \ge 0.5$ m):** **`11,359` steps (`99.59%`)**

> **Finding:** The Courant overshoot is **overwhelmingly ($99.59\%$) situated in deep water ($> 0.5$ m, median $17.95$ m)** in the carved drainage channels during peak storm accumulation.

### The Single Step Reaching Peak Courant $0.9289$
- **Step Index:** Step `16` ($t = 160.0$ s, 2 min 40 s into the storm)
- **Timestep $\Delta t$:** Chosen at $\Delta t_{16} = 10.000000$ s (maximum allowable timestep)
- **Depth at Step Start:** $h_{\max,\text{start}} = 0.049949$ m ($5.0$ cm)
- **Selected Courant:** $\text{Courant}_{\text{selected}} = 0.700000$
- **Depth at Step End:** $h_{\max,\text{end}} = 0.087951$ m ($8.8$ cm)
- **Realized Courant:** $\text{Courant}_{\text{realized}} = \mathbf{0.928871}$ ($\Delta t_{17} = 7.536033$ s)
- **Physical Cause:** At $t = 160$ s, the domain transitions from the initial dry state under uniform rain. Water concentrating into the primary drainage depression rapidly increased local depth from $5.0$ cm to $8.8$ cm across a single 10-second step, raising $\sqrt{h}$ by $32.7\%$ before adaptive $\Delta t$ throttled down to $7.54$ s.

---

## 2. Depression Inventory on Pre-Breach DEM

Evaluated on the raw $10$ m DEM prior to building/road burning and breach conditioning ($3521 \times 3615$ buffered grid):

### Summary Statistics (27,609 Closed Depressions)
- **Total Closed Depressions:** **`27,609`** distinct basins
- **Total Depression Footprint:** **`489,032` cells** ($48.90\text{ km}^2$, $3.84\%$ of the domain)
- **Total Depression Storage Volume:** **`28,889,503.6 m³`**

| Metric | Area (cells) | Max Depth Below Outlet ($m$) | Volume ($m³$) |
|---|---|---|---|
| **Min** | $1.0$ | $0.00$ m | $0.01$ m³ |
| **$P_{10}$** | $1.0$ | $0.01$ m | $0.71$ m³ |
| **$P_{50}$ (Median)** | $2.0$ | $0.06$ m | $9.40$ m³ |
| **$P_{90}$** | $11.0$ | $0.34$ m | $124.21$ m³ |
| **$P_{99}$** | $221.0$ | $1.33$ m | $6,360.45$ m³ |
| **Max** | $39,141.0$ | $21.67$ m | $2,221,792.89$ m³ |
| **Mean** | $17.71$ | $0.16$ m | $1,046.30$ m³ |

### Top 50 Depressions by Volume
*The 50 largest depressions account for **`21,618,287.0 m³` (74.83%)** of all depression storage volume in the domain. **33 of the 50 (66.0%)** directly intersect mapped OSM water bodies or waterways.*

| Rank | Volume (m³) | Area (cells) | Area (ha) | Max Depth (m) | Centroid WGS84 (Lat, Lon) | Intersects Mapped Water/Waterway |
|---|---|---|---|---|---|---|
| 1 | 2,221,792.9 | 39,141 | 391.4 | 0.98 | (13.08077° N, 77.76733° E) | **Yes** (Hoskote / Yele Mallappa Shetty valley) |
| 2 | 2,146,419.2 | 16,555 | 165.6 | 3.91 | (12.95617° N, 77.75834° E) | **Yes** (Varthur Lake) |
| 3 | 1,911,347.3 | 9,687 | 96.9 | 2.60 | (12.90722° N, 77.61659° E) | **Yes** (Madiwala Lake) |
| 4 | 1,908,379.7 | 19,171 | 191.7 | 1.08 | (12.93442° N, 77.66549° E) | **Yes** (Bellandur Lake) |
| 5 | 1,055,829.7 | 1,359 | 13.6 | 15.25 | (13.08738° N, 77.69682° E) | No |
| 6 | 977,863.1 | 1,173 | 11.7 | 15.11 | (13.11015° N, 77.68251° E) | **Yes** |
| 7 | 777,915.8 | 10,980 | 109.8 | 0.76 | (12.94769° N, 77.73799° E) | **Yes** (Kodi Lake / Varthur feeder) |
| 8 | 595,268.1 | 11,688 | 116.9 | 1.30 | (12.99527° N, 77.77394° E) | **Yes** (Whitefield wetland basin) |
| 9 | 591,787.0 | 833 | 8.3 | 18.23 | (13.10942° N, 77.66081° E) | No |
| 10 | 578,691.2 | 1,461 | 14.6 | 12.58 | (13.10316° N, 77.65011° E) | No |
| 11 | 547,889.4 | 796 | 8.0 | 15.02 | (13.09264° N, 77.68312° E) | No |
| 12 | 532,730.5 | 781 | 7.8 | 16.89 | (12.85182° N, 77.62713° E) | No |
| 13 | 486,127.5 | 3,914 | 39.1 | 2.59 | (13.14559° N, 77.48785° E) | **Yes** (Hesaraghatta boundary basin) |
| 14 | 442,305.4 | 3,343 | 33.4 | 3.10 | (12.92072° N, 77.64112° E) | **Yes** (Agara Lake) |
| 15 | 415,216.9 | 4,181 | 41.8 | 5.14 | (12.90529° N, 77.48021° E) | **Yes** (Kengeri / Vrishabhavathi basin) |
| 16 | 376,256.3 | 2,172 | 21.7 | 4.60 | (12.93876° N, 77.77699° E) | **Yes** (Gunjur Lake) |
| 17 | 372,536.8 | 441 | 4.4 | 21.67 | (13.07727° N, 77.69231° E) | **Yes** |
| 18 | 369,859.1 | 620 | 6.2 | 12.36 | (12.84549° N, 77.56177° E) | No |
| 19 | 339,273.7 | 3,607 | 36.1 | 1.00 | (12.98258° N, 77.61977° E) | **Yes** (Ulsoor Lake) |
| 20 | 326,915.3 | 3,765 | 37.6 | 0.94 | (13.04681° N, 77.58674° E) | **Yes** (Hebbal Lake) |
| 21 | 291,105.7 | 1,447 | 14.5 | 3.42 | (12.90780° N, 77.45743° E) | **Yes** |
| 22 | 274,168.0 | 1,340 | 13.4 | 2.46 | (12.94625° N, 77.58343° E) | **Yes** (Lalbagh Lake) |
| 23 | 271,913.5 | 8,079 | 80.8 | 0.35 | (13.02381° N, 77.72841° E) | **Yes** (Hoodi / Mahadevapura lake) |
| 24 | 253,544.6 | 3,091 | 30.9 | 2.33 | (12.93977° N, 77.68175° E) | **Yes** (Kaikondrahalli / Kasavanahalli) |
| 25 | 226,961.6 | 326 | 3.3 | 16.13 | (12.89802° N, 77.67106° E) | **Yes** |
| 26 | 225,082.9 | 534 | 5.3 | 6.97 | (13.10662° N, 77.64610° E) | No |
| 27 | 195,158.2 | 303 | 3.0 | 14.25 | (13.11110° N, 77.65277° E) | No |
| 28 | 188,043.3 | 2,400 | 24.0 | 1.67 | (12.87520° N, 77.77306° E) | **Yes** (Muthanallur Lake) |
| 29 | 187,975.3 | 2,374 | 23.7 | 2.09 | (13.14069° N, 77.66186° E) | **Yes** (Bagalur Lake) |
| 30 | 173,144.9 | 382 | 3.8 | 9.86 | (13.12044° N, 77.65951° E) | **Yes** |
| 31 | 172,361.5 | 751 | 7.5 | 10.83 | (13.09627° N, 77.66728° E) | No |
| 32 | 157,840.1 | 428 | 4.3 | 7.56 | (12.84357° N, 77.62932° E) | No |
| 33 | 154,755.0 | 289 | 2.9 | 12.13 | (13.10981° N, 77.64939° E) | No |
| 34 | 147,080.3 | 1,121 | 11.2 | 2.53 | (12.91758° N, 77.46135° E) | **Yes** |
| 35 | 139,080.2 | 356 | 3.6 | 6.56 | (12.83454° N, 77.62317° E) | No |
| 36 | 127,930.2 | 958 | 9.6 | 2.51 | (13.11810° N, 77.65082° E) | No |
| 37 | 125,966.7 | 159 | 1.6 | 18.59 | (13.11375° N, 77.65182° E) | No |
| 38 | 121,868.4 | 222 | 2.2 | 12.88 | (12.83209° N, 77.60325° E) | No |
| 39 | 121,648.9 | 393 | 3.9 | 7.02 | (13.10475° N, 77.64722° E) | No |
| 40 | 110,416.9 | 283 | 2.8 | 9.32 | (13.10344° N, 77.66876° E) | **Yes** |
| 41 | 105,452.7 | 1,456 | 14.6 | 1.41 | (12.91908° N, 77.72149° E) | **Yes** (Saul Kere) |
| 42 | 105,369.9 | 2,679 | 26.8 | 0.43 | (13.11173° N, 77.59476° E) | **Yes** (Yelahanka Lake) |
| 43 | 104,120.8 | 1,818 | 18.2 | 1.24 | (13.14388° N, 77.73527° E) | **Yes** |
| 44 | 103,207.2 | 461 | 4.6 | 4.54 | (13.00230° N, 77.58621° E) | **Yes** (Sankey Tank) |
| 45 | 100,968.3 | 5,097 | 51.0 | 0.47 | (12.94610° N, 77.69472° E) | **Yes** |
| 46 | 97,137.7 | 669 | 6.7 | 3.43 | (12.94010° N, 77.45681° E) | **Yes** |
| 47 | 93,562.2 | 252 | 2.5 | 8.34 | (13.11532° N, 77.65159° E) | No |
| 48 | 93,033.4 | 1,674 | 16.7 | 3.36 | (13.03582° N, 77.55208° E) | **Yes** (Yeshwanthpur Lake) |
| 49 | 87,943.0 | 1,122 | 11.2 | 2.04 | (12.94918° N, 77.45951° E) | **Yes** |
| 50 | 87,040.9 | 799 | 8.0 | 1.91 | (13.08947° N, 77.71672° E) | **Yes** (Bhattarahalli Lake) |

---

## 3. Cut-Depth Attribution & Excavation Concentration

Evaluated across the **`465,927`** canonical D8 cut cells ($20,223,054.0\text{ m}^3$ canonical excavation volume; $20,802,918.0\text{ m}^3$ buffered):

### Distribution of D8 Cut Depth
$$\text{Mean: } 0.4340\text{ m} \quad\mid\quad \text{Std: } 1.6769\text{ m} \quad\mid\quad \text{Median } (P_{50}): 0.0657\text{ m} \quad\mid\quad \text{Max: } 22.9444\text{ m}$$

| Percentile | Cut Depth (m) | Percentile | Cut Depth (m) |
|---|---|---|---|
| **$P_0$ (Min)** | $0.0001$ m | **$P_{60}$** | $0.0997$ m |
| **$P_{10}$** | $0.0071$ m | **$P_{70}$** | $0.1526$ m |
| **$P_{20}$** | $0.0150$ m | **$P_{80}$** | $0.2624$ m |
| **$P_{30}$** | $0.0283$ m | **$P_{90}$** | $0.6528$ m |
| **$P_{40}$** | $0.0444$ m | **$P_{95}$** | $1.5679$ m |
| **$P_{50}$ (Median)** | **`0.0657 m`** ($6.6$ cm) | **$P_{99}$** | **`10.0784 m`** |
| | | **$P_{99.9}$** | **`19.8026 m`** |
| | | **$P_{100}$ (Max)** | **`22.9444 m`** |

### Fraction of Total Cut Volume by Depression Basin Size
Total D8 excavation volume across all cut trenches: **`20,802,918.0 m³`**

| Depression Area Threshold | Cut Volume Attributable (m³) | Share of Total D8 Excavation | Intersecting Cut Trenches |
|---|---|---|---|
| **Basin Area $> 0$ cells** | $20,802,918.7$ m³ | **$100.00\%$** | $124,420$ trenches |
| **Basin Area $> 10$ cells** | $20,792,890.6$ m³ | **$99.95\%$** | $123,044$ trenches |
| **Basin Area $> 50$ cells** | $20,766,526.1$ m³ | **$99.83\%$** | $121,634$ trenches |
| **Basin Area $> 100$ cells** | **`20,755,378.0 m³`** | **`99.77%`** | $121,392$ trenches |
| **Basin Area $> 500$ cells** | $20,733,622.1$ m³ | **$99.67\%$** | $120,953$ trenches |
| **Basin Area $> 1,000$ cells** | **`20,725,412.0 m³`** | **`99.63%`** | $120,810$ trenches |
| **Basin Area $> 5,000$ cells** | $20,694,014.2$ m³ | **$99.48\%$** | $120,512$ trenches |
| **Basin Area $> 10,000$ cells** | **`20,690,446.5 m³`** | **`99.46%`** | $120,455$ trenches |

### Pareto Concentration across Primary Breach Trenches
- **Top 1 largest cut trench network:** **`10,176,564.4 m³`** (**`48.92%`** of all domain excavation)
- **Top 5 largest cut trenches:** **`11,478,311.6 m³`** (**`55.18%`** of all domain excavation)
- **Top 10 largest cut trenches:** **`11,997,309.1 m³`** (**`57.67%`** of all domain excavation)
- **Top 50 largest cut trenches:** **`13,341,305.0 m³`** (**`64.13%`** of all domain excavation)
- **Top 100 largest cut trenches:** **`13,916,659.7 m³`** (**`66.90%`** of all domain excavation)
