# Goal 13: Accounting Reconciliation, Retained-Basin Invariant, and Fill Deviation Characterization

**Date:** 2026-08-17  
**Repository:** `/home/darshil/Desktop/sih/clginternal`  
**Commit:** `9032b55`  
**Status:** COMPLETE (V1–V8 binding compliant)

---

## Executive Summary

This goal resolved the accounting and documentation defects left by the D4 bound re-derivation without touching the frozen DEM or interim rasters, established a realized-state invariant for retained basins (replacing Invariant C's former role), and reconciled all reported metrics across manifests, rasters, and tabular registers.

1. **Item Y (Residual Accounting):** Reconciled the three reported residual counts (`basin_residuals.csv`: 65,639; canonical `basin_class.tif`: 63,903; priority-flood algorithm search-tree traversals: 106,198). Renamed the diagnostic metric to eliminate the collision, added cross-artifact assertions (V8) to Invariant A2, and derived `residual_max_bound` from a stated domain-density rule ($N \le 0.75\%$ domain cells).
2. **Part B (Retained Basin Mask Invariant):** Formalized Invariant R (`test_invariant_retained_basin_interior_unmodified`), verifying that 100% of candidate retained basin cells ($494,550$ cells) remain unmodified by the repair pass ($0$ modified cells). Demonstrated the check RED ($2,249$ cells modified by up to $3.02$ m) under a pipeline mutation omitting the mask, and GREEN after.
3. **Item Z (Fill Characterization & Deviation):** Characterized the destination of $6.7346\text{ M m}^3$ deposition: $100.00\%$ lands on Class 0 (unclassified bare-ground micro-depressions), with $0$ cells on roads, $0$ cells on waterways, $0$ cells on buildings, and $0$ cells inside retained basins. Rewrote the SPEC.md §6.1 deviation note to reflect actual pipeline operation, and stated hydrodynamic defensibility criteria.

---

## PART A — Item Y: Reconciliation and Cross-Artifact Assertion of Residuals

### 1. Artifact Measurement Breakdown
* **`data/interim/terrain/basin_residuals.csv` (65,639 rows):** Counts individual unresolved D4 pit cells (local minima) enumerated across the **buffered domain** ($3,521 \times 3,615 = 12,747,515$ cells) that could not be drained without violating conveyance/building protections or exceeding repair bounds.
* **`basin_class.tif` class 5 (63,903 cells):** Counts unresolved D4 pit cells situated strictly within the **canonical unbuffered domain** ($3,421 \times 3,515 = 12,024,815$ cells), excluding the 500 m (50-cell) perimeter buffer (which holds the remaining 1,736 cells).
* **Manifest `d4_capped_hybrid_diagnostics.n_residuals_enumerated` (106,198):** Measures **unfilled cell traversals during the Priority-Flood algorithm traversal on the buffered grid** (individual cells visited below queue spill elevation where fill was rejected due to protected masks or `fill_max`), which is an internal search-tree rejection counter, not a count of local minimum pit cells.

### 2 & 3. Naming Reconciliation and Producer Bug Fix
* `basin_residuals.csv` (65,639), `basin_class_buffered.tif` class 5 (65,639), and `retained_basins.csv` residual rows (65,639) represent the identical physical quantity: **buffered domain residual pit cells**.
* `basin_class.tif` class 5 (63,903) represents **canonical domain residual pit cells**.
* The 106,198 figure in `runs/terrain_conditioning/manifest.json` arose from a key collision in `d4_capped_hybrid_conditioning`, which returned the Priority-Flood unfilled search-tree counter under `"n_residuals_enumerated"`.
* **Producer Fix (`src/jaladhar/terrain/conditioning.py`):**
  - Renamed the search-tree counter in `d4_capped_hybrid_diagnostics` to `n_priority_flood_unfilled_traversals` (106,198).
  - Explicitly exposed distinct manifest metrics: `n_residual_pits_buffered` (65,639), `n_residual_pits_canonical` (63,903), and `n_residuals_enumerated` / `n_residuals_registered` (65,639).
* **Target for Invariant A2:**
  The ACC solver executes over the **buffered domain** (`use_buffered=True`), so any unresolved local minimum in the buffered domain acts as a physical water trap during simulation. Invariant A2 asserts against the **buffered count (65,639)** for full register consistency, while also verifying canonical raster agreement (63,903).

### 4. Cross-Artifact Assertion (V8)
Updated `test_invariant_a2_residual_pits_bounded_and_enumerated` in `tests/test_terrain_conditioning.py` to assert exact seam consistency across all five artifacts at load time:
1. `len(basin_residuals.csv) == manifest["n_residuals_enumerated"] == manifest["n_residual_pits_buffered"] == (basin_class_buffered == 5).sum() == 65,639`
2. `(basin_class == 5).sum() == manifest["n_residual_pits_canonical"] == 63,903`
3. `basin_class_buffered[50:3471, 50:3565] == basin_class` for all class 5 cells.
4. `(retained_basins["class"] == "residual").sum() == 65,639`
5. Disagreement between any raster, register CSV, or manifest field causes immediate test failure.

### 5. Re-derived `residual_max_bound` Stated Rule
* **Rule Stated Before Computation:** Residual pits are unresolved D4 local minima on protected infrastructure (roads, waterways, buildings) where filling is strictly forbidden. Under honest domain scaling, residual pit cells must not exceed **$N = 0.75\%$ of total domain cells** and must represent no more than **$25\%$ of pre-conditioning D4 pits** ($\lfloor 0.25 \times 290,267 \rfloor = 72,566$).
* **Derived Bounds:**
  - `residual_max_bound` (buffered domain: $3,521 \times 3,615 = 12,747,515$ cells): $\lfloor 0.0075 \times 12,728,415 \rfloor = \mathbf{95,463\text{ cells}}$.
  - `residual_canonical_max_bound` (canonical domain: $3,421 \times 3,515 = 12,024,815$ cells): $\lfloor 0.0075 \times 12,024,815 \rfloor = \mathbf{90,186\text{ cells}}$.
* **Realized State:** Realized buffered residuals = $65,639$ ($0.516\%$ of domain, $22.6\%$ of pre-conditioning pits) and canonical residuals = $63,903$ ($0.531\%$ of domain), strictly satisfying the derived bounds.

---

## PART B — Basin Mask Invariant (Invariant R)

### 1. Invariant R Implementation
Implemented `test_invariant_retained_basin_interior_unmodified` in `tests/test_terrain_conditioning.py`:
* Compares realized `dem_conditioned_postbreach.tif` against `dem_conditioned_prebreach.tif` across all 494,550 cells belonging to candidate retained basins (classes 1..4: Storage, Quarry, Landfill, Uncertain in `basin_class_buffered.tif`).
* Asserts $|\text{post} - \text{pre}| \le 10^{-4}$ m for all retained cells.
* Realized check: **0 cells modified across all 494,550 retained basin cells** (GREEN).

### 2. Demonstration: RED Under Mutation, GREEN After
Implemented the mutation verification check in `test_invariant_retained_basin_mask_mutation_fails_red`:

```python
# Pipeline Mutation: Pass zeroed retained_basin_mask into d4_capped_hybrid_conditioning
mutated_post, _ = d4_capped_hybrid_conditioning(
    dem=t_pre,
    valid_mask=t_valid,
    retained_basin_mask=np.zeros_like(t_valid),  # MUTATION: Mask disabled
    cost_surface=cost,
    building_mask=t_b,
    road_mask=t_r.astype(int),
    waterway_mask=t_w.astype(int),
    cut_max=CUT_MAX_M,
    fill_max=FILL_MAX_M,
)
```

* **RED Demonstration (Without Basin Mask):**
  ```
  Mutated pipeline check: 2,249 / 7,836 retained basin cells modified (max change 3.0176 m) -> RED
  AssertionError: Invariant R failed: 2,249 of 7,836 retained basin cells were modified during conditioning (max diff 3.0176 m). Retained basin interiors must be strictly immutable.
  ```
* **GREEN Demonstration (Realized State with Basin Mask):**
  ```
  Realized pipeline check: 0 / 494,550 retained basin cells modified (max change 0.0000 m) -> GREEN
  test_invariant_retained_basin_interior_unmodified PASSED [100%]
  ```

### 3. Formal Invariant C Scope and Documentation
Updated docstrings in `test_invariant_c_cut_and_fill_bounds` and `manifest.json`:
* Explicitly documented that **Invariant C is a bound on repair magnitude and terrain distortion** ($\text{cut} \le 2.60$ m, $\text{fill} \le 3.25$ m, conveyance unfilled, buildings untouched) and **NOT a misclassification detector**.
* Stated that **Invariant R (`test_invariant_retained_basin_interior_unmodified`)** replaces Invariant C's former role to formally guarantee that registered retained basins cannot be modified or drained.

---

## PART C — Item Z: Fill Characterization and Rewritten §6.1 Deviation

### 1. Fill Destination by Basin Class and Depth Distribution
Measured directly from the realized rasters on disk (`dem_conditioned_postbreach.tif` vs `dem_conditioned_prebreach.tif`):

| Basin Class | Filled Cells | Volume ($\text{m}^3$) | Volume Share | Mean Fill (m) | Max Fill (m) |
|---|---|---|---|---|---|
| **Class 0 (Unretained / Bare ground micro-depressions)** | **103,024** | **6,734,611.50** | **100.00%** | **0.6537** | **3.2064** |
| Class 1 (Storage — retained) | 0 | 0.00 | 0.00% | 0.0000 | 0.0000 |
| Class 2 (Quarry — retained) | 0 | 0.00 | 0.00% | 0.0000 | 0.0000 |
| Class 3 (Landfill — retained) | 0 | 0.00 | 0.00% | 0.0000 | 0.0000 |
| Class 4 (Uncertain — retained) | 0 | 0.00 | 0.00% | 0.0000 | 0.0000 |
| Class 5 (Residual pit) | 0 | 0.00 | 0.00% | 0.0000 | 0.0000 |

* **Fill Depth Distribution Across 103,024 Filled Cells:**
  - Min: $0.0001$ m | Mean: $0.6537$ m | Max: $3.2064$ m
  - P10: $0.0140$ m | P25: $0.0478$ m | P50 (median): $0.1622$ m | P75: $0.6029$ m
  - P90: $2.6919$ m | P95: $2.8438$ m | P99: $2.9545$ m | P99.9: $2.9962$ m

### 2. Realized Landcover / Infrastructure Checks
* **Road cells filled (`road_segment_id_buffered.tif`):** **0 cells**
* **Waterway conveyance filled (`distance_to_drain_buffered.tif`):** **0 cells**
* **Building cells filled/modified (`building_mask_buffered.tif`):** **0 cells**

### 3. Scale Comparison
* **Total Fill Deposition:** $6.7346\text{ M m}^3$
* **Against Total Depression Storage ($29.83\text{ M m}^3$):** **$22.58\%$**
* **Against Design Storm ($132.3\text{ M m}^3$ — 184 mm over 717 $\text{km}^2$):** **$5.09\%$**
* **Against Acceptance Run Mass Balance ($140.02\text{ M m}^3$ delivered):** **$4.81\%$**

### 4. Rewritten §6.1 Deviation
Updated in `src/jaladhar/terrain/conditioning.py` and `runs/terrain_conditioning/manifest.json`:

> *"Bounded Priority-Flood filling up to fill_max = 3.25 m (realized mean fill 0.6537 m, max 3.2064 m, total deposition 6.7346 M m3 across 103,024 cells) on unclassified bare-ground micro-depressions (< 400 m2 or noise pits) deviates from SPEC.md §6.1's carve-only preference. This bounded deposition eliminates the need to carve destructive 22 m drainage canyons across the city, while strictly prohibiting any fill on road conveyance (0 cells filled), waterway conveyance (0 cells filled), building footprints (0 cells filled), or retained basin interiors (0 cells filled)."*

### 5. Defensibility and Falsification Criteria
* **Why $6.73\text{ M m}^3$ of deposition is defensible:**
  1. Fill is deposited $100.00\%$ into unclassified micro-depressions outside conveyance networks and buildings, replacing $20.8\text{ M m}^3$ of destructive excavation trenches.
  2. Full-duration 6 h hydrodynamic simulation confirms hydrodynamic neutrality: non-basin cells with $h > 5$ m remained at exactly $0$, P99 moved by only $+1.3\%$ ($0.9898$ m $\rightarrow$ $1.0028$ m), and boundary outfall flux remained continuous without artificial damming.
* **Criteria that would falsify defensibility:**
  1. Any localized flooding/ponding spikes along road centrelines or rajakaluves caused by fill elevating terrain above street conveyance grade (currently 0 cells).
  2. A shift of $> 5\%$ in total boundary outfall mass balance or emergence of artificial standing water ($> 0.5$ m) on bare slopes outside retained basins.
  3. Cross-subcatchment diversion of surface runoff away from natural drainage pathways.

---

## Verification & Test Results

* **Terrain Test Suite:** 56/56 tests passing in `tests/test_terrain_conditioning.py` and `tests/test_terrain_grid.py`.
* **Full Test Suite:** 179 passed, 22 skipped across the entire repository.
* **Git Integrity:** Committed as `9032b55` (`Reconcile terrain residual accounting, add retained basin invariant, and update fill deviation documentation`) with no author attribution lines.
* **DEM/Raster Integrity:** Zero modifications made to frozen DEM or intermediate rasters.
