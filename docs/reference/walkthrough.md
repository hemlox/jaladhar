# Walkthrough — Phase 1 D4 Conditioning & Full Buffer Pipeline

**Reconstructed 2026-08-16** from the agent report transcript after the original file was lost. It appears never to have been committed — it is absent from git history entirely. Content is verbatim from the report; figures unverified against source since reconstruction.

---

## 1. Overview of changes

Both Phase 1 defects identified during the Phase 2 acceptance review were resolved without inventing terrain or modifying solver stencils.

### Defect 1 — D4 / solver connectivity mismatch

- Implemented `d4_cardinal_breach()` and `count_pits_d4()` in `conditioning.py`.
- Applied the P → M → D staircase conversion using relative interior midpoints:

  `z_M,new = z_D + 0.5·(z_P − z_D) = 0.5·(z_P + z_D)`

  guaranteeing `z_D < z_M,new < z_P` with no hardcoded epsilon.
- Recorded the D4/D8-breach adjacency split and the lowering breakdown across building, road, waterway and plain terrain classes.

> **Design note added on review:** the midpoint is valid but non-minimal — any `z_M` in `(z_D, z_P)` satisfies the constraint, and the midpoint gratuitously removes half the elevation difference. Where M is a burned building between two street cells, this removes half the building. Minimal lowering does not fix the general case either, since any valid `z_M` sits at street level when both P and D do. The real fix is class-aware midpoint selection (a diagonal has two candidate midpoints; prefer the non-building one) plus accepting genuinely sealed courtyards as legitimate pits.

### Defect 2 — genuine 10-layer buffer construction

- Updated `roads.py`, `buildings.py`, `roughness.py`, `drains.py` to fetch, process and rasterize over the full buffered bounding box (3521 × 3615).
- `build.py` assembles all 10 layers at buffered extent and crops all 10 to canonical.
- `state.py` loads the buffered domain for simulation; `run.py` crops depth rasters and reports metrics on the canonical domain.

### Invariants and testing

- Added `test_invariant_d4_pit_count_zero_on_conditioned_dem` and V5 mutation test `test_d4_pit_count_mutation_pre_d4_breach_shows_pits` in `test_terrain_grid.py`.
- Extended `test_invariant_1_all_layers_aligned` to cover all 10 buffered layers.

---

## 2. Results

### D4 cardinal breach metrics (from `manifest.json`)

- Initial D4 pits: 347,083
- Final D4 pits: 0, resolved in 2 iterations
- D8 pits post-breach: 0
- Cells raised by breach: 0 — pure lowering preserved everywhere
- Total cells lowered: 332,080, mean 0.7871 m, max 25.04 m

### D4 / D8-breach adjacency split

- Adjacent to D8 breach cells: 228,227 (65.76%) — artefacts of diagonal-preferring least-cost breaching
- Natural diagonal terrain: 118,856 (34.24%)

> See the DISPUTED note in `d4-audit.md` §6A: this adjacency fraction is not the same quantity as the net pit increase (+56,816, 16.4%) and the two were conflated downstream.

### Lowering impact by class

| Class | Cells lowered | Mean | Max | Notes |
|---|---|---|---|---|
| Building override | 46,261 (2.97% of 1,559,490) | 2.91 m | 25.04 m | 13.47 M m³ volume removed |
| Waterway burn | 17,756 | — | — | deepens conveyance channels |
| Road burn | 121,611 | — | — | |
| Plain terrain | 146,452 | — | — | |

Per-class counts sum to exactly 332,080.

### Tests

- `tests/test_terrain_grid.py`: 45 passed in 6.45 s
- `tests/test_manifest_provenance.py`: 23 passed, 4 skipped
- Commit SHA at build: `b79cf2d`
- `runs/terrain_build/manifest.json` wall-clock: 325.96 s (~5.4 min)
- All 10 buffered and 10 canonical layers verified pixel-aligned

### Mutation demonstration

Prior to the re-run the new invariant failed with `AssertionError: D4 pit count on conditioned DEM is 290,107, expected 0`. Both tests passed on the regenerated DEM.
