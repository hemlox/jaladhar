# Codex session changes — 2026-08-20

This file records the work done in this session in simple English. Nothing in
this file means that Phase 3 passed. It did not run from a clean, complete
dataset in this session. For the complete final handoff, including the actual
download and terrain-stack state, see [`codex_session.md`](codex_session.md).

## What was checked first

- Read the project handover, README, bootstrap guide, open-items ledger, and
  the standing rules in `CLAUDE.md`/`AGENTS.md`.
- Checked the local machine and environment. This laptop has CPU-only PyTorch,
  so it can do development, tests, downloads, and preprocessing. It must not
  run the full Phase 3 replay unless the explicit unmeasured CPU override is
  enabled in `configs/compute.yaml`.
- Kept the collaborator RTX 4060 as the only measured compute worker in the
  shared compute configuration. No GPU model, worker count, or throughput was
  hardcoded in solver code.
- Preserved existing uncommitted work. No reset, checkout, stash, commit, or
  push was used.

## Provenance and run safety

- Added `src/jaladhar/provenance.py` with a shared `RunManifest` lifecycle.
  It writes a `running` manifest before work starts, snapshots the resolved
  configuration, records the real Git SHA, and updates the same file on
  completion or failure.
- A provenance-bearing run now refuses a dirty Git worktree. This is required
  because a result cannot honestly claim to be reproducible from a tree that
  has changed while it was running.
- A run now refuses to overwrite an existing manifest. A caller must use a
  fresh output directory instead of silently replacing evidence from an older
  run.
- Manifest ownership is checked before updates. If another writer changes the
  manifest, the original bytes are kept and a failure sidecar is written.
- Segment validation now writes its own manifest rather than overwriting the
  Phase 3 manifest.
- Water tracing now uses the same manifest lifecycle and atomic JSON writes.
  It no longer substitutes an `unknown` Git SHA.

## Phase 3 replay and validation fixes

- The Phase 3 entry point resolves more validation keys at startup. Sentinel
  scene paths, basin class, density output, BBMP KML directory, and road
  segment raster are all read from `configs/validation.yaml`.
- Removed hardcoded Sentinel, road, basin, density, and BBMP paths from the
  replay scoring path.
- Replaced fixed 10 m coordinate rounding with `rasterio` grid transforms.
  This keeps point scoring aligned if the configured grid resolution changes.
- The replay writes a realized final-depth raster, `depth_final.tif`, and
  records it as an artifact. Water tracing reads this recorded final artifact
  instead of assuming a hardcoded 172800-second snapshot filename.
- The replay now also writes `depth_final_buffered.tif`. Catchment storage is
  calculated on the buffered routing domain, so zero-padding the canonical
  final-depth raster would have silently omitted real water stored in buffered
  catchment cells. The consumer asserts path, shape, transform, units, and
  actual dtype before loading this new producer artifact.
- The replay also records accepted-step cumulative transport, drain, and
  infiltration fields for downstream catchment accounting. These are not
  fabricated estimates.
- Added a clear CPU guard for the full Phase 3 run. With the default config a
  CPU-only host stops before the expensive run, because the project has no
  measured CPU throughput to turn its cost into a trustworthy budget.
- The segment report no longer contains old fixed CSI, POD, lift, p-value, or
  underpass figures in runtime findings. It emits the metrics produced by the
  current run and labels underpass co-location as descriptive rather than a
  causal conclusion.
- The SAR water threshold, BBMP spatial filter, road lookup, and underpass
  register paths are now configuration inputs. The report counts raw BBMP
  points from the loaded KMLs instead of repeating a historical count.
- Removed the fixed external-literature CSI/RMSE comparison from runtime Phase
  3 output. It is now explicitly `NOT_SCORED`: a literature number is not an
  artifact of the current run and cannot be a gate comparator.

## Terrain and acquisition changes

- Added a first-class `jaladhar.terrain.water` acquisition stage for the real
  OSM standing-water, quarry, and landfill polygons used to classify retained
  depressions; standing-water is also shared by water tracing and SAR
  validation. The terrain orchestrator now runs it before the
  conditioning/depression path, and that path no longer accepts an untracked
  historical run-directory fallback. An unavailable Overpass service remains
  a terminal external-data block; no empty evidence layer is fabricated.
- Added `scripts/repair_whitebox_permissions.py` for the upstream Whitebox
  installer defect triggered by this checkout's `Local Disk` path component.
  It repairs executable bits on Whitebox binaries only and verifies the real
  CLI. The local installation was repaired and `WhiteboxTools v2.4.0` ran.
- Corrected the V8 terrain-drain manifest test after a realized terrain build
  exposed that it incorrectly treated the aggregate `class_breakdown.total`
  row as a drain class. The test now verifies per-class sums and independently
  checks aggregate totals; producer output was not changed because its schema
  was internally consistent.

- Added a checked BBMP 2023 ward-boundary source to the domain configuration.
  The boundary fetch checks its configured byte size and SHA-256 before using
  it to build the grid.
- Strengthened cached DEM handling. A cached DEM is accepted only when an
  independent remote byte count agrees; an unavailable remote check no longer
  turns an unverified file into trusted input.
- Improved IMERG URL handling so duplicate CMR URLs cannot make workers race
  to write the same file.
- Improved Sentinel-1 selection so the fetcher requires exactly one of each
  needed VV object class, checks remote sizes before it skips an existing
  local file, and checks the downloaded temporary file against the S3 object
  size before promoting it to its final name.
- The real FABDEM tiles and BBMP boundary are present locally. The terrain
  build reached the OSM roads stage, then did not complete. The buildings
  request recorded an Overpass `ChunkedEncodingError`; this is an external
  service problem, not data that was replaced with a made-up fallback.
- IMERG and Sentinel acquisition are partial and resumable. The latest restart
  commands did not complete in this environment, so no download is marked
  complete.

## Solver, terrain, and analysis repairs already applied

- Fixed an undefined variable in water tracing and removed a hardcoded local
  drain-loss estimate. Catchment accounting now consumes the realized Phase 3
  transport, drain, and infiltration artifacts.
- Removed historical water-tracer causal verdicts and historical storage
  amounts from newly written reports. Until a clean run produces an
  independently reviewable point trace, each point is `UNRESOLVED`.
- Made terminal provenance manifests immutable. A completed or failed manifest
  cannot be silently transitioned again.
- Sentinel fetching now removes only stale randomized partial files after the
  final target has passed its size verification.
- Tightened the terrain conditioning residual bound and added a test for it.
- Made drain artifact publication atomic so a failed copy cannot expose a
  partial canonical file.
- Extracted the solver's drain-manifest V8 seam assertion into a pure function.
  The deliberate-red test now mutates only an in-memory manifest, never a live
  run artifact. Terrain-artifact tests now report `BLOCKED` when the real
  rasters do not exist rather than failing as if fabricated files were expected.
- Fixed small correctness/lint problems in the ACC solver and benchmark code.
- Ground-truth scoring now separates date eligibility, presence of a depth
  band, and road-snap eligibility. It does not score a point as a depth label
  when its configured road snap is too far away.

## Collaboration and setup work

- Updated README and bootstrap documentation for both CPU-only development and
  the collaborator CUDA 12.4 / RTX 4060 environment.
- Pinned the project Python/Torch setup for Python 3.11 and made the CPU/CUDA
  choices explicit.
- Added the collaborator-friendly compute configuration: CPU machines are
  development hosts; measured GPUs go in the shared pool.
- Updated the handover and open-items ledger to say that IMERG/Sentinel are
  partial and that the terrain build is externally blocked, rather than
  presenting them as complete.

## Verification performed

- Python bytecode compilation completed for all Python files under `src/` and
  `scripts/` after the final code edits.
- `git diff --check` completed with no whitespace errors.
- Focused contract suite completed: **30 passed, 19 skipped**. The skips are
  explicit data-dependent blocks, not passing acceptance checks.
- Full test suite completed after the final audit fixes: **157 passed, 89 skipped,
  0 failed**. It was run as the network-free suite (**152 passed, 86 skipped**)
  plus `tests/test_forcing.py` (**5 passed, 3 skipped**); the live Open-Meteo
  case is an explicit external-network skip. The skips are correct `BLOCKED` results for the missing
  OSM/terrain artifacts; no test was weakened to claim those artifacts exist.
- The former live Open-Meteo test is now reported as `BLOCKED`/skipped when the
  endpoint cannot be reached, rather than falsely treating unavailable DNS as
  a software failure. Its mocked adapter test still exercises the code path.
- Applied Ruff's safe whole-tree `F` fixes and removed four inspected unused
  local assignments. The whole-tree `F` check is clean. Repository-wide Ruff
  is still not clean: **254 style findings** (mostly 185 line-length and 29
  import-placement findings). This is maintenance debt, not evidence that
  Phase 3 is ready.
- `UV_CACHE_DIR=/tmp/jaladhar-uv-cache uv lock --check --offline` completed:
  the committed lockfile resolves 90 packages consistently without network
  access.

## Final audit fixes after the first review

- Found and fixed a Sentinel-1 integrity gap: a download was promoted from its
  temporary filename without comparing its local byte size to the selected S3
  object. A truncated transfer now raises an error and stays unpromoted.
- Found and fixed a configuration gap in segment validation: the SAR change
  detector had a hidden `-16 dB` threshold even though validation configuration
  exposed `water_threshold_db`. Both flood and pre-event masks now use the
  configured value, and preflight validates every later-used SAR input.
- Removed fixed `100%` underpass partition declarations. The report now
  calculates the marginal and joint partition totals from the loaded register,
  and rejects an empty register rather than emitting meaningless percentages.
- Found and fixed a mass-budget derivation gap. The segment report now derives
  residual and relative residual from the Phase 3 producer's signed terms and
  rejects disagreement. A deliberate inconsistent-manifest test demonstrates
  the check red.
- Found and fixed the buffered final-depth handoff described above. This was a
  correctness defect, not a cosmetic change: buffered catchment storage could
  otherwise be undercounted even though the run itself held the water.
- Corrected future-run tests whose expectations still described the old
  artifacts or old static historical metrics. They now verify producer output
  and explicit `MEASURED, NOT CLOSED` status instead.
- Fixed the segment-validation CLI so intentionally unavailable PU metrics
  (`FAR`, `CSI`, and CSI lift) print as `N/A` instead of raising a formatting
  exception after the report and manifest have already been written.
- Applied Ruff's safe whole-tree `F`-class cleanup and removed the four
  remaining unused local assignments after inspecting them. The final
  whole-tree `F` check is clean; broader formatting/import-order debt remains
  documented separately.

## Work still blocked or pending

1. Resume and independently validate all IMERG granules, then generate the
   Bengaluru forcing CSV.
2. Finish both Sentinel-1 scenes and validate their exact object sizes.
3. The OSM retry and terrain build now completed locally. Preserve their
   manifests and rerun the realized-raster checks only if those source or
   terrain artifacts are deliberately rebuilt.
4. Commit the reviewed source/doc/test changes before any provenance-bearing
   Phase 3 run.
5. Commit the reviewed source changes. Only after that, run a clean, complete
   Phase 3 replay on the measured RTX 4060. The CPU laptop should not be used
   for that replay by default.
6. Conduct the required independent review after the clean replay. Phase 3 is
   still a hard gate; Phases 4 to 8 remain unauthorized.

## Important honesty note

The previous historical Phase 3 output does not meet the repaired provenance
and artifact contracts. It was not rewritten. The code is ready for a new
clean run once the real datasets and collaborator GPU are available.
