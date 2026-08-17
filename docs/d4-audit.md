# D4 / Buffer Rebuild Audit

**Reconstructed 2026-08-16** from the agent report transcript after the original untracked file was lost in a working-tree shuffle. Content is verbatim from the report; formatting normalised. Figures unverified against source since the reconstruction — re-derive before relying on any single number for a design decision.

Repo: `/home/darshil/Desktop/sih/clginternal`
Grid: 3521 × 3615 buffered (12,728,415 cells) / 3421 × 3515 canonical (12,024,815 cells), 10.0 m resolution.

---

## 1. Fetch provenance

### Roads (`roads.py`)
- Query bbox: `(77.454657, 12.827005, 77.790989, 13.148424)` — buffered extent
- Network fetch → OSMnx cache, 2026-08-16 09:25:06 UTC
- Cache key `132cd3df27e1806c8959078555da987fab63ccd9`, 90,790,654 B, 900,600 OSM elements
- Artifacts: `roads_centrelines.gpkg` (40,927,232 B, 09:25:25), `roads_segment_lookup.csv` (5,466,682 B, 09:25:25)
- **Classification: re-fetched over the buffered bbox** (900,600 elements vs 866,426 canonical)

### Buildings (`buildings.py`)
- OSM query bbox: same buffered extent
- Network fetch → OSMnx cache, 2026-08-16 09:26:11 UTC
- Cache key `92a24b6d587e7686684232bdf1155cc1f7e42328`, 366,848,324 B, 4,193,373 OSM elements
- Microsoft ML footprints: quadkey 123303312 from local raw archive `data/raw/microsoft_buildings/quadkey_123303312.geojsonl.gz` (92,204,346 B, 2026-08-15 04:07:40 UTC), streamed and filtered over the buffered bbox — 738,122 survivors vs 689,864 canonical
- Artifact: `data/interim/terrain/buildings.gpkg` (247,099,392 B, 09:28:16, 1,068,931 features)
- **Classification: re-fetched over the buffered bbox**

### Roughness (`roughness.py`)
- Query bbox: same buffered extent
- Vegetated open: `00ac5f8a1fabbb7f2c8aa70ac030ce56f247c16b`, 9,833,545 B, 09:28:43, 109,883 elements, network fetch
- Dense urban: `77acaf9ca5d9fcc3da800a654ae91883bf5a9286`, 11,024,094 B, 09:28:59, 116,041 elements, network fetch
- Water: `83e547b5edd59ec6d01dd4128298e0b956b2edb9`, 4,324,867 B, 09:29:08, 48,381 elements, network fetch
- Roads paved: cache hit on `132cd3df…` from the roads fetch four minutes earlier
- **Classification: re-fetched over the buffered bbox**

### Drains (`drains.py`)
- Query bbox: same buffered extent
- Network fetch → OSMnx cache, 2026-08-16 09:29:36 UTC
- Cache key `08f150c2f97f940eb1d4347eadfe36b047544a24`, 2,025,757 B, 22,485 OSM elements
- Artifact: `data/interim/terrain/waterways.gpkg` (864,256 B, 09:29:37, 2,724 features)
- **Classification: re-fetched over the buffered bbox** (22,485 vs 21,852 canonical)

---

## 2. Ring test — 50-cell border ring vs interior

Canonical interior slice `[50:3471, 50:3565]`. Ring = 703,600 cells (5.53% of grid).

| Layer | Ring mean ± std | Interior mean ± std | Ring nonzero | Int nonzero | Ring unique | Int unique | Nearest-edge exact match | Edge-replicated rows/cols |
|---|---|---|---|---|---|---|---|---|
| elevation.tif | 863.71 ± 44.89 m | 884.36 ± 31.94 m | 100.00% | 100.00% | 581,961 | 2,273,895 | 0.01% | No — all 100 border rows/cols distinct |
| building_mask.tif | 0.0484 ± 0.2146 | 0.1485 ± 0.3556 | 4.84% | 14.85% | 2 | 2 | 92.00% | No — 34,070 ring building cells; match is background 0 = 0 |
| building_height_delta.tif | 0.1452 ± 0.6437 m | 0.4456 ± 1.0669 m | 4.84% | 14.85% | 2 | 2 | 92.00% | No — same 34,070 cells |
| building_conveyance_factor.tif | 0.9957 ± 0.0546 | 0.9851 ± 0.1011 | 100.00% | 100.00% | 2 | 2 | 98.83% | No — 8,233 ring reduced cells; match is background 1 = 1 |
| manning_n.tif | 0.0453 ± 0.0237 | 0.0475 ± 0.0277 | 100.00% | 100.00% | 5 | 5 | 59.46% | No — 5 classes active in ring; match is background 0.035 |
| drain_capacity.tif | 1.6301 ± 2.5595 mm/h | 2.4743 ± 2.7205 mm/h | 100.00% | 100.00% | 44,477 | 45,051 | 0.35% | No — all 100 border rows/cols distinct |
| distance_to_drain.tif | 1148.96 ± 1092.36 m | 555.94 ± 572.40 m | 99.40% | 99.16% | 44,477 | 45,051 | 0.35% | No — all 100 border rows/cols distinct |
| road_segment_id.tif | 11976.4 ± 36154.8 | 14998.8 ± 37775.6 | 12.32% | 20.53% | 5,526 | 169,125 | 77.97% | No — 86,657 ring road cells; match is background 0 = 0 |
| slope.tif | 2.6980 ± 2.7000 | 3.8608 ± 3.5209 | 100.00% | 100.00% | 697,655 | 10,478,406 | 0.00% | No — all 100 border rows/cols distinct |
| flow_accumulation.tif | 1345.2 ± 67415.0 | 1724.1 ± 53317.6 | 100.00% | 100.00% | 8,018 | 65,868 | 11.33% | No — matches natural zero/ridge lines |

All 10 canonical rasters in `data/processed/` match the interior slice bitwise (`int_matches_can = True`).

**Verdict: the buffer is genuine data, not padding.** Elevation, slope and drain layers show near-zero edge replication; the high match percentages on mask layers are background-zero cells trivially matching zero. Ring elevation is 20 m lower with higher variance than the interior — consistent with the edge of a plateau city.

---

## 3. Building and road counts

| Metric | Buffered (3521 × 3615) | Canonical (3421 × 3515) | Difference |
|---|---|---|---|
| Distinct building footprints (vector) | 1,068,931 | 912,456 | +156,475 (+17.15%) |
| — OSM footprints | 786,982 | 735,463 | +51,519 |
| — Microsoft gap-fill kept | 281,949 | 176,993 | +104,956 |
| Road segments (vector gpkg) | 176,171 | 140,296 | +35,875 (+25.57%) |
| Road segments rasterized | 173,859 | 169,124 (134,859 at Phase 0) | +4,735 |
| Total building cells | 1,820,100 | 1,786,056 | +34,044 |
| Building override cells (uncontested, +3.0 m) | 1,559,490 | 1,529,753 | +29,737 |
| Contested cells (conveyance C = 0.3) | 260,610 | 256,303 | +4,307 |

**Note for review:** the ring adds 34,044 building cells but 156,475 footprints — 0.218 cells per footprint against 1.96 in the canonical area. Explanation offered: peri-urban OSM coverage is sparser so more Microsoft ML gap-fill survives, and those are small structures that mostly fail to rasterize at 10 m. Consistent with the gap-fill keep rate rising from 25.7% to 38%. Not independently verified.

Unresolved: canonical rasterized road segments read 169,124 here against 134,859 at Phase 0 — same extent, 25% more segments. Bookkeeping discrepancy, cause not established.

---

## 4. Blockage-loss audit — D4 cardinal breach

Of 1,559,490 building override cells, 46,261 (2.97%) were lowered.

- Blockage fully lost (z_cell ≤ min of non-building cardinal neighbours): **1,531 cells** (3.31% of lowered)
- Reduced but still above surrounding grade: 44,730 cells (96.69%)
- Cells with only building cardinal neighbours: 456
- Distinct footprints with ≥1 fully-breached cell: **1,448** (0.1355% of 1,068,931 — 1 in 738)
- Distinct footprints with ≥1 lowered cell at any depth: 43,402 (4.0603%)

Lowering depth on building cells (m) — mean 2.911305, min 0.007568, max 25.043274:

| P0 | P10 | P20 | P30 | P40 | P50 | P60 | P70 | P80 | P90 | P100 |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.007568 | 2.678650 | 2.813477 | 2.878967 | 2.921326 | 2.954834 | 2.983582 | 3.015442 | 3.065735 | 3.136047 | 25.043274 |

The distribution clusters tightly at the +3.0 m burn height — the pass removes the burn rather than excavating below bare earth.

**Follow-up measured separately.** Remaining height above the lowest non-building cardinal neighbour: P10 0.007507 m, P50 0.265442 m, P90 0.874451 m. Roughly two-thirds sit below 0.5 m and are hydraulically transparent in any flood worth simulating. The Δz ≤ 0 cut used above is too lenient; the correct threshold is Δz below design flood depth.

---

## 5. D8 baseline in isolation

`whitebox breach_depressions` on the pre-breach surface:

- 482,479 cells lowered (3.79% of domain), mean 0.4312 m, max 22.9444 m, total 20,802,918.0 m³
- Building override cells: 4,997 (1.04%) — mean 1.9096 m, **max 3.4851 m**
- Road burn cells: 178,554 (37.01%) — mean 0.4678 m, max 22.9444 m
- Waterway burn cells: 34,307 (7.11%) — mean 0.6214 m, max 12.3045 m
- Plain natural terrain: 269,168 (55.79%) — mean 0.3831 m, max 21.4118 m

D8 touches 9.3× fewer building cells than the D4 pass and essentially never digs below the burn (max 3.485 m vs D4's 25.04 m), while being *more* aggressive than D4 on roads and plain terrain.

---

## 6. Loose ends

### A. D4 pit count change

1. Raw conditioned DEM: **290,267 initial D4 pits** (282,118 canonical)
2. Whitebox D8 breaching resolved all D8 depressions (125,374 → 0) by carving diagonal staircases
3. Under the 4-connected solver stencil each diagonal cut leaves cardinal neighbours uncarved, synthesising new D4 traps
4. Per `runs/terrain_conditioning/manifest.json`, **228,227 of the 347,083** post-D8 D4 pits (65.8%) are directly adjacent to D8 breach cuts; net increase **+56,816**

> **DISPUTED — read before using either figure.** 65.8% is an *adjacency* fraction; 16.4% (56,816 / 347,083) is the *net increase*. They are different quantities and were conflated in the handover. Adjacency does not establish causation, and net increase understates gross creation if the D8 pass also destroyed pre-existing pits. A set-difference measurement (pits present post-D8 but absent pre-breach, and vice versa) is required before either number supports a design decision.

### B. Skipped test inventory

`tests/test_solver_invariants.py` — 22 skipped, all full-domain or full-duration variants:

| # | Test | Reason |
|---|---|---|
| 1 | `test_inv2_BLOCKED_full_domain_real_relief_still_water` | Full-domain well-balancedness on real DEM (12M cells, 244 m relief, 6 h) |
| 2 | `test_inv3_BLOCKED_full_domain_datum_shift` | Full-duration datum shift invariance on real DEM |
| 3 | `test_inv4_BLOCKED_full_domain_non_negative_depth` | Full-domain non-negativity with building walls and extreme drain sinks |
| 4 | `test_inv14_BLOCKED_full_domain_sink_budget` | Full-domain combined sink budget across varying rasters |
| 5 | `test_inv16_BLOCKED_full_domain_volume_non_increasing` | Full-domain volume monotonicity, closed boundaries, 6 h |
| 6 | `test_inv5_BLOCKED_full_duration_full_domain_mass_conservation` | 6 h, ~10⁴ steps, 12M cells |
| 7 | `test_inv23_BLOCKED_full_domain_ramp_mode_mass_conservation` | Full-domain ramp vs hard comparison |
| 8 | `test_inv6_BLOCKED_full_domain_dry_domain_zero_rain` | 12M cells over 10⁴ steps |
| 9 | `test_inv7_BLOCKED_full_duration_varying_rain_source` | Full-duration varying hyetograph |
| 10 | `test_inv8_BLOCKED_long_duration_symmetry` | 10⁴ steps on large symmetric bowl |
| 11 | `test_inv9_BLOCKED_gpu_full_domain_bitwise_reproducibility` | Full-domain bitwise reproducibility, 6 h |
| 12 | `test_inv10_BLOCKED_full_domain_full_duration_courant_stability` | Courant stability on 12M cells, 6 h |
| 13 | `test_inv11_BLOCKED_full_duration_full_domain_nan_inf_gradients` | Gradient backprop NaN/inf check, full tile |
| 14 | `test_inv12_BLOCKED_real_bengaluru_building_conveyance_impact` | Full domain with real conveyance raster (228k cells) |
| 15 | `test_inv13_BLOCKED_multi_step_full_domain_conveyance_flux_reduction` | Multi-step dynamic routing, full domain |
| 16 | `test_inv15a_BLOCKED_full_perimeter_full_duration_outfall_flux` | 12,000+ perimeter cells over 6 h |
| 17 | `test_inv15b_BLOCKED_real_boundary_standing_water_outflow` | Standing water outfall on real boundary profile |
| 18 | `test_inv17_BLOCKED_full_duration_needs_checkpoint_segmentation` | **Needs checkpoint segmentation (L\* = √(N·S/I)), not implemented.** Without it a 512² differentiable run caps at B/I = 124 steps against the ~9,665 a 6 h storm requires |
| 19 | `test_inv17_BLOCKED_full_duration_conveyance_floor_gradient` | Full-duration differentiable run with parameters at bounds |
| 20 | `test_inv18_BLOCKED_flooded_multi_step_analytic_vs_fd_gradient` | Multi-step analytic vs central FD gradient comparison |
| 21 | `test_inv19_BLOCKED_full_tile_segmented_checkpoint_gradient_match` | Segmented checkpointing gradient match, ~9,665 steps on 512² |
| 22 | `test_inv26_BLOCKED_full_band_kinematic_steady_state_sweep` | Full kinematic agreement band across all 10 slopes, 10⁻⁴ to 3×10⁻² |

`tests/test_manifest_provenance.py` — 4 skipped, all labelled DEBT with closure conditions:

1. `test_every_manifest_records_a_real_commit[solver/manifest.json]` — Phase 2 acceptance run, `git_sha: None` and a 2.33 MiB inline dt schedule, the two defects this file exists to catch. Kept as evidence. Closed by re-running acceptance with the fixed `run.py`.
2. `test_every_manifest_records_a_real_commit[solver_probe/manifest.json]` — 1 h probe, same pre-fix path. Closed by deletion once the re-run supersedes it; retained meanwhile because its mass residual at 4,887 steps is the datapoint tolerance scaling rests on.
3. `test_no_manifest_is_pathologically_large[solver/manifest.json]` — same debt as 1.
4. `test_no_manifest_is_pathologically_large[solver_probe/manifest.json]` — same debt as 2.

Items 1 and 3 should be closeable against the successful acceptance run at commit `78610c2`, which wrote a real SHA and a 72,376 B binary sidecar.
