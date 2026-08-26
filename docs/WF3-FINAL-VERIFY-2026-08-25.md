# WF-3 final verification — 2026-08-25

This document records only realized measurements: exact commands, exact artifact paths, SHA-256
hashes, and command outputs. Every number below was produced by a committed script or a logged run
in this repository during the Fixer-1 completion session of 2026-08-25. Historical context lives in
[`WF3-AUDIT-RECONCILIATION-2026-08-25.md`](WF3-AUDIT-RECONCILIATION-2026-08-25.md).

## 1. Product identity and label

The only admitted Replay #2 product is labelled exactly **`UNCOUPLED BASELINE`**, with
`forcing_kind: historical_replay`, `temporal_aggregation: event_maximum`, event window
`2022-09-04T00:00:00Z` – `2022-09-05T23:30:00Z`. It is real uncoupled solver output for the
September 2022 replay. It is not a coupled result, not surcharge/backflow, and not a live nowcast.

## 2. Preserved historical artifacts (byte-verified this session)

| artifact | SHA-256 |
|---|---|
| `runs/goal_d_replay2_final_config/manifest.json` | `acdf78ea1ff04d5a2248115c450e0fb8c36610a8d64f8d8ed9cefb5aab911f7f` |
| `runs/goal_d_replay2_final_config/depth_event_maximum.tif` | `05024e4663695048e7498ea2f04f43f3e02d54a7ec5cad2e5b9a836ba3f43ff4` |
| `runs/goal_d_replay2_final_config/depth_final.tif` | `466fbb85bbb0e49c444d9b55c8f30dee311159767f6308827b05f2314be53ee2` |
| `runs/goal_d_replay2_final_config/depth_sar_instant_20220905_004028Z.tif` | `3b650ff87b35e9c28d1d01bb789369aa6845046cfec5e9fbaaa180e2b0243a84` |
| `runs/wf3_replay2_uncoupled_baseline_v2/manifest.json` | `7ba6591b71044681ef97c7d65354a004bb429bf916a31fd221c30ca27c76f304` |
| `runs/wf3_replay2_uncoupled_baseline_v2/products/segment_status.csv` | `7db65f785b03f39a7e33d14bc13b864a9f7d09cdce9f825f6a8a9ffea11f41d2` |
| `runs/wf3_replay2_uncoupled_baseline_v2/products/segment_status.json` | `3c228f47fb9a9ac6e5f0ebd5a16aad3b2839a7a42d3d82bbbb609b83c3dce41c` |
| v2 + v4 `provenance/source.bundle` (identical) | `ce51a08066755270212fe18ced205effc69a9c8aeed41f0679225d09afd9f3ab` |
| `runs/wf3_replay2_gates_v4/g1_score.json` | `8ce958b197d302c83b186993d76a41c7e5a14e19b4e2ef5064b39fb95ef3be93` |

Both bundles verify (`git bundle verify`) and record complete history at the original WF-3 source
snapshot `b250de7af46a3f348a185a97ebb9ddc0d55f35bf`, which is present in this repository's object
database and referenced by no branch. The older attempt directories
(`wf3_replay2_uncoupled_baseline`, `wf3_replay2_gates`, `_v2`, `_v3`) are preserved unmodified as
audit evidence of superseded attempts.

## 3. Fresh superseding runs (clean-source production)

Produced inside a temporary clean mirror at mirror-only commit
`25dafa625fca910795444e88e5605e28ca37a49f` ("Temporary WF-3 Fixer-1 completion snapshot",
tree verified clean). Bundle objects were imported into this repository by bundle fetch:
ref count before = after = 21, FETCH_HEAD written, **no branch/ref/history mutation**.

### `runs/wf3_replay2_uncoupled_baseline_v5/`

- `manifest.json` SHA-256 `2c229c343bb8331376ba78c65a36cf638a3518b06bc25cace5b9b49e9613b0cd`
  — `status: completed`, `git_tree_clean: true`, start `2026-08-25T08:17:43.103335+00:00`,
  producer-stamped `issue_time_utc` equal to actual start time (fixes A2),
  `source_snapshot_bundle_sha256 e81fb3057fc93bd96f2bcad657a3ba3fe6d10167164a6c801621d68cf13aa53c`.
- `products/segment_status.csv` SHA-256 `d04539d71989f9a28160406f86f7e87cdfc565daf7b80e420e4f17eb7f59e155`
- `products/segment_status.json` SHA-256 `df067eee899fea74f6173a0e21fccfe4b3e83dd53bb06e3406e5f66d6af8257e`
- Realized rows: **176,171** total; **40,519** flooded; **128,605** not_flooded; **7,047**
  unknown/no-data (split 4,735 buffered-margin-only + 2,312 fully clipped) — identical to the v2
  realized split, as required by the same input raster and frozen rule.
- Consumer validation: `python -m jaladhar.routing.product <csv>` → `status: available`,
  contract assertions PASS from the main checkout.

### `runs/wf3_replay2_gates_v5/`

- `g1_score.json` SHA-256 `fbace6fd21bab1bba544857c1a0fbfefc253d1912e95cc1d41ca743504504120`
- `manifest.json` SHA-256 `92229491cb0535f6bbb4b17b9404e3309e3d6fb24c0af0ab2d797be9cc12f230`

An earlier same-session attempt wrote both manifests with
`source_snapshot_bundle_sha256: null` because the bundles had not been pre-staged; that attempt was
deleted inside the disposable mirror before any preservation boundary and rerun correctly. No
preserved run directory was touched.

Snapshot scope note (Auditor B finding B10-1): the mirror commit's tree contains the WF-3
producer/runtime sources and scripts but not the `tests/` tree, and the demo-launcher startup
timeout default was raised 20 s → 120 s AFTER the snapshot (recorded in `.goal/state.md`
decisions log). Producer/runtime files diff against live code as style-only ruff line-joins;
artifact integrity is unaffected (`wall_clock_sec` 4.72 for the product run). The separate
red→green mirrors contain the tests tree.

## 4. Measured G1 result — FAIL (applies ONLY to UNCOUPLED BASELINE)

From `runs/wf3_replay2_gates_v5/g1_score.json`, reproduced exactly from the clean snapshot:

| field | value |
|---|---|
| `fixed_denominator_points` | 399 |
| `coordinate_realized_points` | 398 |
| `within_snap_modeled_points` | 394 |
| `outside_grid_fixed_misses` | 1 |
| `out_of_snap_fixed_misses` | 4 |
| unique within-snap complaint segments | 383 |
| hits / misses | 182 / 217 |
| hit rate | `182 / 399 = 0.45614035087719296` |
| null draws / seed | 10,000 / 42 (toroidal spatial translation of the realized pattern) |
| null mean hits / mean rate | 129.0197 / `0.3233576441102757` |
| null 95% CI | `[0.2606516290726817, 0.39598997493734334]` |
| lift | `1.4106372902742759` |
| verdict | **FAIL** — hit rate < 0.60 AND lift < 3.0 (both signed conditions unmet) |

Arithmetic check: 398 in-grid + 1 outside-grid fixed miss = 399; 394 within snap + 4 out-of-snap
+ 1 outside-grid = 399; 182 + 217 = 399; 0.45614035087719296 ÷ 0.3233576441102757 =
1.4106372902742759. Reconciles exactly.

### Metric framing — owner-acknowledged anchor error (recorded 2026-08-25)

The signed 60% hit-rate minimum was anchored to Replay #2's **POINT-mediated** BBMP score
(point depth ≥ 0.10 m at the complaint cell): `runs/goal_d_replay2_final_config/manifest.json`
`results.bbmp_scoring.sweep[threshold_m==0.1]` records **hits 239 / 399 = 0.599**. This report
scores **SEGMENT-mediated** flood_status under the frozen product rule (D=0.15 m / F=20% / N=3):
**182/399**. Same run, same points — **57 hits of pure metric difference**. Per R5 the threshold
does NOT move; the mismatch is disclosed as an owner-acknowledged error in the gate's framing,
derived from realized manifest bytes inside every fresh gate report (`g1.metric_framing`),
never retuned to fit either metric.

Superseding gate report carrying this block:
`runs/wf3_replay2_gates_v6/g1_score.json` SHA-256
`638413625863a955506064222685b287eaa6389632b5c79f25253ee3336786c7`, manifest `d771e947…a148`,
produced from clean mirror commit `bfd4e3f6979e3957ff6b04bbfe71d66fd132f3ee`
(bundle `d2601c79…`, objects imported ref-free). All G1/G3 numbers are identical to gates_v5;
only the framing block and provenance differ. Demo fallback discovery now binds gates_v6
automatically (newest completed report binding the v5 product bytes).

## 5. G3 — signed gate BLOCKED; point diagnostics retrospective

Signed G3 status is **BLOCKED** (`holdout_status: RETROSPECTIVE_NOT_HELD_OUT`): no owner-signed
per-segment-band comparison predicate and no independent holdout exist. Required to unblock: an
owner-signed segment-band predicate plus a newly frozen held-out strict-depth set.

Retrospective point-depth diagnostics (16 strict rows), reproduced exactly:

- local-max 3×3 convention: **0/16** in band (15 below, 1 above)
- exact-cell convention: **1/16** in band (15 below, 0 above; GT_03 point value 0.93 m inside its
  0.85–1.10 m band)
- historical reproduction vs the source manifest's rounded point records: **CONFIRMED**
  (compared rows 16, mismatches 0)

These numbers apply only to the UNCOUPLED BASELINE replay.

## 6. Blocked axes (external / owner)

| axis | state | what closes it |
|---|---|---|
| N-3 live nowcast | BLOCKED | owner-verified IMD/DWR source, licence, schema, rate/courtesy policy, captured payload |
| Vehicle policy | BLOCKED | cited policy file under `data/curation/` binding a locally captured source-evidence file by SHA-256, then owner authenticity check |
| Directed navigation graph | BLOCKED/PARTIAL | endpoint-based undirected road graph: 176,171 segments / 279,627 nodes / 177,077 edge parts / 107,014 components; lacks one-way/access/turn fields. Producing command: `python -m jaladhar.routing.api health --depth-product-file runs/wf3_replay2_uncoupled_baseline_v5/products/segment_status.csv --gate-report-file runs/wf3_replay2_gates_v5/g1_score.json` (realized output excerpt in §7); independently recomputed by both auditors via `jaladhar.routing.graph.RoadNetwork.from_files` on the committed GeoPackage + lookup |
| Coupling / surcharge (G2) | BLOCKED | coupled solver run per frozen `coupling_iface` contract |
| Signed G3 | BLOCKED | owner-signed segment-band predicate + independent holdout |
| G4 latency | BLOCKED | measured ≤10 min pipeline latency on a realized coupled nowcast run |
| G5 extent instrument | OPEN | instrument validated on urban flooding (lakes-validated SAR remains invalid per P3-4/N-5) |

## 7. Command evidence (this session)

CPU-only environment on every executable command: `JALADHAR_CPU_ONLY=1 JALADHAR_DEVICE=cpu
CUDA_VISIBLE_DEVICES= NVIDIA_VISIBLE_DEVICES=`. No GPU was used; no CUDA allocation occurred.

- Focused WF-3 suites: `pytest tests/test_forcing.py tests/forcing/test_nowcast.py
  tests/test_wf3_products.py tests/test_provenance.py tests/test_validation_segment.py`
  → **72 passed, 9 skipped** after Fixer 2 (skips: 7 fixture-blocked on absent realized Phase-3
  segment report; 1 dirty-tree lifecycle skip that executes green in a clean mirror; 1 live-network
  probe gated behind `JALADHAR_ALLOW_LIVE_NETWORK=1` so offline counts are deterministic).
- `pytest tests/test_manifest_provenance.py` → **399 passed, 8 skipped** (grandfathered debt);
  includes the fresh v5 manifests' `git_sha 25dafa6…` resolving in this repository's object DB.
- Clean-tree lifecycle mutation (disposable mirror at `6227ec392ac07bc4a20dd1cdb615a98fb3a5f8aa`,
  tree clean): deliberate post-start failure left
  `status=failed, error_type=RuntimeError, error="deliberate post-start failure for V5 red"`,
  with `end_time_iso` present.
- Nowcast CLI: exit code **2** before any source access; aggregated gaps listed
  (`nowcast_input` section, interval, poll log, cache, courtesy, N-3); no rainfall created and no
  poll-log written.
- Routing health (v5 product + v5 gate report): `status not_ready`,
  `artifact_readiness ready`, `operational_routing_ready false`, `snap_policy
  unavailable_owner_gate`, `scientific_readiness {g1 FAIL, signed_g3 BLOCKED}`,
  `vehicle_policies unavailable_external_unknown`; road-graph census as in §6.
- Dashboard CPU check (`web.app check --product v5/json --gate-report v5/g1_score.json`):
  `CPU_ONLY_CHECK: PASS`, `realized_product_state: ready`, `contract_assertions: PASS`.
- Demo fallback discovery: selects `runs/wf3_replay2_uncoupled_baseline_v5`, label and row count
  echoed manifest-backed; default dashboard command supplies
  `--geometry {repo_root}/data/interim/terrain/roads_centrelines.gpkg`; launch then stops honestly
  at routing `not_ready`.
- Full rehearsal (`rehearse_demo.py --run-dir …v5 --max-snap-distance-m 110`): PASS dashboard
  HTML; PASS `/api/state` (manifest reference + 16 numeric leaves owned); PASS `/api/segments`
  (19 numeric leaves owned); REHEARSAL_FAIL at `api_health not_ready` — the sanctioned external
  vehicle-policy BLOCKED stop, exit 2.
- Static: scoped `ruff check` clean over the 20 changed WF-3 files; `ruff format` applied (10
  reformatted, style-only) and re-checked clean; `compileall src scripts` OK; `node --check app.js`
  OK; `bash -n scripts/demo/run_demo.sh` OK; `git diff --check` clean; staged files **0**;
  `git diff c47e107 -- configs/contracts/` empty (frozen contracts byte-identical).
- Full CPU suite (`pytest tests/`): **708 passed, 39 skipped, 20 failed** after Fixer 2.
  Residual-failure classification — 4 `tests/drainage/test_loader.py` (WF-1-owned; pass standalone,
  order-dependent isolation issue), 6 `tests/test_terrain_conditioning.py` +
  5 `tests/test_terrain_grid.py` (terrain/WF-1 artifact tests), 5 `tests/test_water_tracer.py`
  (water-tracer artifacts). Zero failures in WF-3-owned files.

## 8. Independent audits and Fixer 2

Both audit reports are preserved verbatim:
[`WF3-AUDIT-A-REPORT-2026-08-25.md`](WF3-AUDIT-A-REPORT-2026-08-25.md) and
[`WF3-AUDIT-B-REPORT-2026-08-25.md`](WF3-AUDIT-B-REPORT-2026-08-25.md).

| id | severity | disposition | evidence |
|---|---|---|---|
| A-F1 live-network nondeterminism | MINOR | FIXED — test gated behind `JALADHAR_ALLOW_LIVE_NETWORK=1`, skips with stated reason offline | tests/test_forcing.py; focused suite now deterministic |
| A-N1 latent `interval_s=1800.0` | NOTE | DEFERRED with rationale — unreachable today (historical mode enforced); auditor's own direction conditions the fix on future reuse of the driver for non-30-min forcing | event_replay.py:466,478 recorded here and in `.goal/state.md` |
| A-N2 courtesy/cache machinery aspirational | NOTE | DOCUMENTED — acceptable while N-3 blocked; enforcement required red-first when a client lands | nowcast config-gate records the values |
| A-N3 null road-affinity asymmetry | NOTE | ALREADY REPORTED — the null block carries `mean_out_of_snap_points_per_draw` + range beside lift; verdict robust either way (FAIL at lift 1.41 vs threshold 3.0) | g1_score.json null block |
| A-N4 census provenance citation | NOTE | FIXED — producing command cited in §6 | this doc |
| A-N5 CLI ValueError exit code | NOTE | FIXED + V5 red→green (mutation M4) | tests/forcing/test_nowcast.py::test_cli_request_side_violation_exits_2_before_source |
| B6-1 gate binding dead (MAJOR) | MAJOR | FIXED + V5 red→green (mutation M1) — dashboard accepts a report bound to either row-identical twin; realized v5 now renders `loaded_not_accepted` with g1 FAIL / signed G3 BLOCKED bound to `d04539d7…` | src/jaladhar/web/app.py; test_dashboard_gate_report_binds_either_product_twin |
| B8-1 offset IndexError | MINOR | FIXED + V5 red→green (mutation M2); also fixed silent offset-ignore when geometry absent (pagination now unconditional) | test_dashboard_offset_beyond_rows_is_typed_error |
| B7-1 geometry-key exemption undocumented | MINOR | FIXED (documentation) — walker docstring states numeric-leaf definition (JSON int/float excl. bool/numeric strings) and the by-design geometry-artifact exemption (B7-2 covered in same edit) | scripts/demo/_demo_common.py |
| B11-1 launcher lacks snap-cap flag | MINOR | FIXED + V5 red→green (mutation M5) — owner-supplied `--server-max-snap-distance-m` option added via tested `append_snap_cap` helper | launch_demo.py; runbook step 4 updated |
| B10-1 snapshot scope/divergence | MINOR | FIXED (documentation) — §3 snapshot-scope note added | this doc |
| B9-1 regex wording overstatement | NOTE | FIXED (documentation) — runbook sentence aligned to boundary semantics; env vars named primary control | DEMO-RUNBOOK |
| B1-1 non-positive cap inconsistent codes | NOTE | FIXED + V5 red→green (mutation M3, corrected fixture) — non-finite/non-positive caps refuse as 503 unavailable_owner_gate | routing/api.py; test_non_positive_server_snap_cap_refuses_like_missing |
| B5-1 stale web __init__ docstring | NOTE | FIXED | src/jaladhar/web/__init__.py |
| B11-2 event-window wording imprecise | NOTE | FIXED (documentation) — reconciliation wording corrected | WF3-AUDIT-RECONCILIATION |

### Fixer-2 V5 mutation matrix (disposable mirror `/tmp/opencode/v5check`, refreshed)

| mutation | reverted behavior | observed red | restored green |
|---|---|---|---|
| M1 gate binding JSON-twin-only | single-twin hash set | gate-twin test FAILED | passed |
| M2 offset guard removed | geometry-only slicing | offset test FAILED | passed |
| M3 cap normalization reverted | None-check only | cap −5 test FAILED (400 escalation path) — first attempt with an incorrect fixture did NOT redden and was corrected before counting | passed |
| M4 ValueError exit mapping removed | CLI traceback exit 1 | exit-2 test FAILED | passed |
| M5 launcher helper renamed | import failure | launcher test FAILED | passed |

## 9. Fixer-1 V5 evidence (disposable mirror `/tmp/opencode/v5check`)

| invariant | mutation applied | observed red | restored green |
|---|---|---|---|
| bare `raw_response` refused pre-fetch | removed the `raw_response` gap from `_find_config_gaps` | test failed (no refusal raised) | passed |
| admission binds realized depth bytes | stopped comparing `depth_raster_sha256` to bytes | test failed at tampered-hash case | passed |
| event window read from realized `event_window` | deleted the fallback branch | test failed on realized-shape manifest | passed |

Clean-tree lifecycle red→green is realized by the mirror run in §7 (deliberate post-start failure
produces `failed`, never running).

## 10. Deviations and integrity statements

- `src/jaladhar/forcing/interface.py` deviation grew to **218 added / 3 removed** (from the
  recorded 124/3); retained deliberately, flagged to WF-2 (see reconciliation doc).
- Frozen `configs/contracts/**` are byte-identical to `c47e107` — verified.
- No WF-1 path touched: `configs/drainage.yaml`, `src/jaladhar/drainage/**`,
  `tests/drainage/**`, `docs/WF1-DRAIN-GRAPH-REPORT.md`, `scripts/wf1_contested_overlay.py`,
  terrain implementation all untouched.
- No fabricated rainfall, terrain, drainage, policy, geometry, or validation data anywhere in this
  work. No placeholder flooding physics. No GPU use.
- No commit, stage, push, history rewrite, reset, checkout, restore, stash, or clean occurred in
  the main repository. The single temporary commit `25dafa6…` (and lifecycle-test amendment
  `6227ec3…`) exists only in the disposable mirror under `/home/darshil/Desktop/sih/
  wf3-clean-mirror`; its objects were imported into this repository object-database-only via
  bundle fetch (FETCH_HEAD; ref count unchanged at 21).
- Worktree remains intentionally dirty: 7 tracked modifications plus the untracked WF-3/WF-1 file
  sets listed by `git status --short`.

## 11. WF-3b — multi-frame depth product (added 2026-08-25, owner-directed)

The demo timeline was disabled because the admitted product carried a single event-maximum
frame. The per-frame data exists in the same historical run:
`runs/goal_d_replay2_final_config/depth_rasters/` — **97 instantaneous solver frames**
(`depth_t{seconds}s.tif`), t=0 → 172,800 s (48 h) at a uniform **1,800-s cadence**, canonical
3421×3515 EPSG:32643 float32 (imported byte-for-byte from the p3-integration copy; import was
add-only beside the preserved artifacts).

Realized product: `runs/wf3_replay2_uncoupled_baseline_frames_v1/`

- manifest SHA-256 `b5d1b1b94e1959aaae4e4fe902282f3d21a4fb2bb13e5c5877cfc48ebfb68d51`,
  `status=completed`, git `59e1dbf9cae568c82bbefb7ff18279aa57033468` (clean mirror commit,
  objects imported ref-free; preserved bundle at
  `provenance/source.bundle`, SHA-256 `5d29e906dcd1fcdf0630243d72c4ca484de0ba32d931251a46eedfd054128c4a`)
- **97 frames × 176,171 segment rows each**, every CSV/JSON twin SHA-256-bound to its manifest
  entry and re-verified from disk after copy-back
- valid times strictly monotonic `2022-09-04T00:00:00Z … 2022-09-06T00:00:00Z` @1800 s
  (`event_window.start` + frame offset; the final frame is the post-final-interval state one
  cadence past `event_window.end` — stated in the admission's `valid_time_rule`)
- flooded-segment curve: min **1,578** → peak **36,268** @ 2022-09-04T21:30Z → final **21,386**
  — a real temporal progression, not a resampled maximum
- labelled exactly **UNCOUPLED BASELINE**, `forcing_kind=historical_replay`,
  `temporal_aggregation=frame_series`; `demo_fallback=false` so single-product discovery
  continues to select v5 (+ gates_v6); the event-max product is untouched
- provenance binding: retrospective sidecar
  `data/curation/wf3_replay2_frame_series_admission.json` (per-frame SHA-256, count, cadence,
  valid-time rule; generated by committed `scripts/build_frame_series_admission.py`) validated
  by `validate_frame_series_admission`
- contract fit, disclosed not hidden: all frozen row/consumer assertions apply verbatim to each
  per-frame twin; the one run-level artifact-location rule
  (`runs/<run_id>/products/segment_status.*`) cannot hold per frame, so frames live under
  `products/frames/<tag>/` — recorded as `artifact_location_deviation` in the run manifest.
  The frozen contract file itself is untouched (still byte-identical to `c47e107`).
- producer: `src/jaladhar/validation/segment_status_frames.py` (CPU-only, ~11 s/frame measured,
  running→completed lifecycle manifest)
- V5 red→green: frame-admission hash-skip mutation reddens the binding test; timestamp-collapse
  mutation (all frames stamped with frame 0's time) reddens the builder test; both green restored.
