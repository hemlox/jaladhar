# Codex session handoff — 2026-08-20

> **Historical session record.** Superseded by `docs/HANDOVER.md` and
> `docs/phases/phase3-writeup.md` on 2026-08-21. Statements below about missing data, absent replays,
> test counts, or pending integration describe this earlier session and are not current status.

This is the complete handoff for the repair and setup work done in this
session. It says what changed, what was checked, and what is still blocked.
It does not claim that the project is ready for Phase 3.

## Bottom line

All work that could be completed without inventing data or bypassing an
external service has been completed. The codebase has been audited, repaired,
tested at unit/contract scope, and documented. The real data and terrain
pipeline are not complete, so a clean Phase 3 replay has not been run.

## What the project can do now

- A CPU-only laptop can run installation, unit tests, downloads, and terrain
  preprocessing.
- A collaborator with the RTX 4060 can use the same lockfile and the shared
  `configs/compute.yaml` to run the measured GPU workload.
- Phase 3 refuses the default full replay on an unmeasured CPU host. This
  avoids presenting an invented CPU runtime as a compute budget.
- Every repaired provenance-bearing runner starts its manifest before work,
  captures its resolved configuration and Git SHA, and updates the same
  manifest when the run finishes or fails.
- The Phase 3 implementation now produces the artifacts needed for honest
  downstream catchment accounting. A new real run is still required to
  materialize and verify them.

## Code changes made

### Provenance

Created `src/jaladhar/provenance.py`.

- `RunManifest` writes atomically.
- It refuses dirty source trees for provenance-bearing runs.
- It refuses to replace an existing manifest. A new run must use a new output
  location rather than destroy prior evidence.
- It checks that no other process has overwritten its manifest before an
  update.
- If ownership is lost during a failure, it preserves the foreign manifest and
  writes a separate failure sidecar.
- It rejects a second terminal transition, so a completed or failed manifest
  cannot be silently rewritten as another terminal result.
- It exposes atomic JSON writing for reports that belong to a run.

### Phase 3 event replay

Updated `src/jaladhar/validation/event_replay.py`.

- The startup resolver now validates all validation inputs used later in the
  run: ground truth, BBMP KML directory, road segment raster, Sentinel scene,
  basin class, and density output.
- Paths that had been embedded in Python are now read from
  `configs/validation.yaml`.
- The SAR water threshold, BBMP WGS84 filter, road lookup path, and underpass
  register path are also configuration inputs, resolved before use.
- Geographic points are placed using the raster transform, rather than a
  hardcoded 10 metre rounding formula.
- The final realized water-depth field is written as `depth_final.tif` and is
  included in the run artifact manifest.
- The buffered final solver state is separately written as
  `depth_final_buffered.tif`. This is required because D8 catchments can
  include the 500 m solver buffer; zero-padding the canonical final raster
  would discard real end-of-event storage from those cells.
- Accepted solver steps accumulate transport, drain, and infiltration depth
  fields. These are written as rasters and are the only fields water tracing
  may use for those accounting terms.
- The configured GPU budget is printed before launch. The actual execution
  device is recorded in the manifest.
- A CPU-only full Phase 3 run is refused by default because no measured CPU
  throughput exists in the project configuration. The override is explicit in
  `configs/compute.yaml`; it is intentionally not enabled by default.

### Water tracing

Updated `src/jaladhar/analysis/water_tracer.py` and `configs/analysis.yaml`.

- Replaced the assumed `depth_t172800s.tif` filename with the recorded final
  depth artifact path.
- Added the final-depth path to analysis preflight validation.
- Removed an old hardcoded local drain-loss estimate.
- Catchment accounts use realized cumulative transport, drain, infiltration,
  and buffered final-depth rasters. Their producer-consumer seam checks path,
  shape, transform, units, grid role, manifest dtype, and actual raster dtype.
- Replaced handwritten manifest writes and the `unknown` Git SHA fallback with
  `RunManifest`.
- Evidence ledger output is atomic.

### Per-road-segment validation

Updated `src/jaladhar/validation/segment_validation.py` and
`scripts/run_segment_validation.py`.

- Sentinel flood/pre-event paths, basin class, density class, BBMP KMLs, road
  raster, and domain config are resolved from `configs/validation.yaml`.
- The segment rescore writes a separate manifest and no longer overwrites the
  Phase 3 manifest.
- The JSON report is written atomically.
- Fixed runtime verdict text that contained historical fixed metrics. It now
  reports values generated by the current run and avoids treating underpass
  co-location as causal proof.
- The report counts loaded BBMP points rather than copying a historical count.
- External literature CSI/RMSE figures are not emitted as a run comparator.
  The report labels that comparison `NOT_SCORED` until a source extraction and
  a separate adjudication define a valid instrument.

### Terrain and data fetchers

Updated terrain and acquisition modules.

- Added the BBMP 2023 ward boundary source, source name, licence, byte size,
  and SHA-256 to the domain config.
- The boundary is fetched only when missing and must match the configured
  byte-level contract before grid construction.
- Cached DEM files are trusted only when a remote byte count independently
  matches. An unavailable remote check no longer turns a cached file into
  verified data.
- IMERG CMR URLs are de-duplicated before concurrent download, preventing two
  workers from writing the same file.
- Sentinel-1 fetching requires exactly one object for each required VV object
  class, validates object size before skipping an existing local file, and
  validates a completed temporary download against the listed S3 object size
  before atomically promoting it to the final filename.
- Drain artifact publication is atomic.
- Terrain-conditioning residual bounds were tightened.
- The Sentinel fetcher removes stale randomized partial files only after the
  final target has passed exact remote-size verification.

### Solver and validation correctness

- Fixed an undefined variable in water tracing.
- Fixed ACC/benchmark lint and correctness issues found during the audit.
- Ground-truth depth scoring now requires all of: correct event date, an
  observed depth band, and a road snap inside the configured maximum distance.
- The analysis and validation configs now carry the extra artifact/path keys
  used by these checks.
- Water tracing no longer emits old causal verdicts or historical lake-storage
  values as current output. Each point remains `UNRESOLVED` until a clean run
  produces a trace that can be independently reviewed.
- The solver's drain-manifest V8 contract is a pure assertion function. Its
  red-under-mutation test uses in-memory data and no longer writes into a live
  run manifest.
- Segment validation now resolves every later-used rescore input before doing
  derived work, including both SAR scene/calibration pairs and the configured
  water threshold. The change classifier no longer retains a hidden `-16 dB`
  literal when the configuration says otherwise.
- The closing Phase 3 water-budget report independently recomputes both the
  signed mass residual and its relative value from producer terms. It rejects
  a manifest whose stated residual does not follow the solver's formula.

## Collaboration and environment changes

- README and bootstrap documentation now describe CPU-only and CUDA 12.4
  installation separately.
- Torch is pinned for Python 3.11. The same `uv.lock` serves CPU and CUDA
  machines with backend selection at install time.
- `configs/compute.yaml` treats the RTX 4060 laptop as the sole measured GPU
  worker. CPU machines remain development hosts, not invented compute workers.
- `.env.example` was extended for the Copernicus endpoint configuration.
- `AGENTS.md` is present so the standing `CLAUDE.md` project rules are visible
  to coding agents.

## Documentation changes

Updated:

- `README.md`
- `docs/BOOTSTRAP.md`
- `docs/HANDOVER.md`
- `docs/OPEN-ITEMS.md`
- `changes_codex.md`

The docs now state that:

- Phase 3 historical outputs are not valid acceptance evidence for the
  repaired contract.
- Passing focused tests is not a Phase 3 pass.
- IMERG is partial and resumable.
- The two required Sentinel-1 scene sets are present but need final
  fetcher-led verification and temporary-file cleanup.
- The DEM and BBMP seed inputs are present.
- The OSM retry completed. Standing-water, quarry, landfill, road, and
  building sources are present; the earlier road/building Overpass failure is
  no longer the current acquisition state.
- The processed 11-layer terrain stack is realized under `data/processed/`.
  There is still no new clean-commit Phase 3 run.

## Actual local data state at the end of this session

Present:

- BBMP ward boundary.
- Two FABDEM DEM tiles.
- BBMP flood-prone KMLs and BBMP drain KMLs.
- Ground-truth CSV.
- Partial GPM IMERG archive. It is resumable, but not complete.
- The flood and pre-event Sentinel-1 scene target files.

Still not realized:

- Complete IMERG archive and derived Bengaluru forcing CSV.
- A clean, committed Phase 3 replay.

The `runs/terrain_roads/manifest.json` and
`runs/terrain_buildings/manifest.json` record the real external failure:
Overpass returned `ChunkedEncodingError`. These failures were not hidden with
empty layers or fake rasters.

## Verification performed

- Compiled all Python files under `src/` and `scripts/` successfully after the
  final code changes.
- Ran `git diff --check` successfully.
- Focused suite after the final review fixes: **30 passed, 19 skipped**. The
  skipped tests name their missing external/data prerequisites.
- Final suite: **157 passed, 89 skipped, 0 failed**. It ran in two portions:
  the network-free suite (**152 passed, 86 skipped**) and
  `tests/test_forcing.py` (**5 passed, 3 skipped**). Missing drain/terrain
  artifacts and unavailable live endpoints are reported as `BLOCKED`, not as
  passing acceptance evidence or failures that imply data should be invented.
- The live Open-Meteo test now skips as `BLOCKED` when DNS/network access is
  unavailable. Its mocked test still checks the adapter logic.
- Applied Ruff's safe whole-tree `F` fixes and removed four inspected unused
  local assignments. The whole-tree `F` check is clean. Repository-wide Ruff
  remains non-green with **254** broad style findings, mainly 185 line-length
  and 29 import-placement findings. This session did not claim a lint-clean
  repository.
- `UV_CACHE_DIR=/tmp/jaladhar-uv-cache uv lock --check --offline` completed.
  The committed lockfile resolves 90 packages consistently without network
  access.

## Final diff-review corrections

The final review was performed against the actual dirty-tree diff, not the
handoff claims. It found and fixed the following concrete defects.

- **Sentinel-1 promotion integrity:** remote size checks before a skip did not
  prove that a newly downloaded temporary file was complete. The fetcher now
  compares the finished temporary file with the S3 object size before rename.
  The regression test writes a deliberately short download and verifies that
  it cannot become a final target file.
- **Segment classifier configuration:** the score path accepted
  `water_threshold_db` in YAML but still used a hardcoded threshold. Both SAR
  masks now use the resolved value. The segment preflight also includes both
  SAR scene/calibration pairs, so an absent pre-event input fails before
  road/BBMP/SAR work begins.
- **Water-budget derivation:** the old report copied a producer residual. It
  now recomputes the exact `MassBudget.residual()` formula and relative scale,
  and a deliberately inconsistent manifest is rejected.
- **Buffered storage boundary:** water tracing previously padded the canonical
  end-depth raster with zeros to serve a buffered catchment mask. The code now
  requires a separately recorded buffered final-state raster and asserts its
  metadata and actual dtype before use. This prevents silent omission of real
  buffer-cell storage.
- **Future-run integration tests:** some BLOCKED tests still asserted old
  fixed Phase 3 values or the absence of now-recorded transport terms. They
  now check current producer-consumer contracts and state precisely why the
  account remains `MEASURED, NOT CLOSED`.
- **CLI handling of PU labels:** BBMP and eligible ground-truth reports
  correctly expose FAR/CSI as unavailable, but the command-line printer tried
  to render those `null` values numerically. It now prints `N/A` and completes
  normally after a real rescore.
- **Static error cleanup:** applied Ruff's safe whole-tree `F` fixes, then
  removed four demonstrably unused local assignments. `ruff check --select F`
  is now clean across `src`, `scripts`, and `tests`; the remaining broader
  style debt is line length and import-placement/formatting, not undefined
  names or unused executable code.

## What must happen next

1. Resume IMERG until the fetcher itself reports completion, then validate the
   resulting forcing CSV.
2. Run the Sentinel fetcher once more so it verifies the completed targets and
   handles/removes the stale temporary file safely.
3. Preserve the realized OSM sources and terrain stack with their manifests.
   Rebuild them only deliberately, through `python -m jaladhar.terrain.build`.
4. Commit the source/doc/test work after review. No commit was made in this
   session.
5. On the collaborator RTX 4060, run a clean Phase 3 replay in a fresh run
   directory. Do not run it on the CPU laptop by default.
6. Perform the mandatory independent multi-agent review of that realized run.
   Only then can Phase 3 be adjudicated. Phases 4–8 remain blocked until then.

## Follow-up: Whitebox path-with-spaces repair and terrain-water dependency

The local checkout path contains `Local Disk`. The upstream `whitebox`
installer unpacked `WhiteboxTools_linux_amd64.zip`, but invoked `chmod` on
unquoted absolute paths. It therefore left the package-root
`.venv/.../whitebox/whitebox_tools` binary at mode `0644`. Whitebox invokes
that binary, so it failed with permission denied even though the download
itself completed.

- Repaired the installed root binary, nested `WBT/whitebox_tools`, and binary
  plugins to executable mode. Plugin JSON metadata remains non-executable.
- Verified realized state: `WhiteboxTools v2.4.0` launched successfully.
- Added `scripts/repair_whitebox_permissions.py`. It locates the installed
  package without importing it, repairs only binary execute bits, leaves JSON
  metadata alone, and executes `WhiteboxTools().version()` as its health
  check. `--check` is non-mutating.
- Added a focused test that creates a `Local Disk` fixture, proves binary
  files become executable, and proves JSON metadata does not. The test passed.
- Added `jaladhar.terrain.water`: a manifest-bearing OSM acquisition stage
  that writes `data/raw/osm/osm_water.gpkg`, `osm_quarries.gpkg`, and
  `osm_landfills.gpkg`. The full terrain build now runs it before conditioning.
  `depressions.py` no longer accepts an untracked historical
  `runs/osm_water_requery` fallback.
- Added a focused mocked-Overpass test. It proves only clipped polygon
  geometry reaches the canonical shared source and that an empty response is
  fatal. Both tests passed. The subsequent real OSM retry succeeded; its
  completed manifest is now the acquisition evidence.

## Follow-up: realized terrain build and drain-manifest test correction

The user reran the new OSM evidence stage and the terrain build. The resulting
terrain-water, roads, buildings, roughness, drains, conditioning, and derived
stage manifests are completed, and `runs/terrain_build/manifest.json` lists
the assembled stack. This changes local acquisition state only; it does not
make Phase 3 adjudicable because IMERG/forcing and the clean Phase 3 replay
remain outstanding.

The post-build test run exposed one test defect in
`test_drain_manifest_v8_guaranteed_properties`: it treated the aggregate
`class_breakdown.total` row as though it were a class row and attempted to
read a nonexistent `features_valid` key. The producer is correct: class rows
provide `features_valid` and `length_km`; the aggregate provides
`features_total` and `total_length_km`. The corrected test separately sums
class rows and asserts the aggregate against the manifest's top-level totals.
The original run supplied the red demonstration; the corrected targeted test
and the complete suite had no failures.

## Safety and honesty rules followed

- No synthetic elevation, rainfall, or validation data was created.
- No UI or placeholder flood physics was added.
- No historical manifest or output was edited to look compliant.
- No commit, push, reset, stash, checkout, or deletion of user work was done.
- No credentials were printed or added to source control.
