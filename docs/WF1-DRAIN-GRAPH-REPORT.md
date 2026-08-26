# WF-1 Drain Graph — Phase Report (2026-08-25)

> **UNBLOCK ROUND 2026-08-25 (post owner adjudications M11/M2-policy/M3/M5b/M4/M2/M3) — see §Unblock Round below. Current realized state supersedes the numbers in the sections above wherever they conflict.**

## WF-1b — capacity depth bound (2026-08-25, blocks WF-2)

Owner finding confirmed: unbounded rational demand drove solved depth (and therefore
Manning capacity) to physically impossible values — max q_capacity 21,118 m3/s
implying a 1,678 km2 contributing area, 1.32x the buffered domain.

Fix shipped (correct physics, per owner directive):
1. **Depth bounded per drain order** (deviation D23): primary 3.0 m, secondary
   2.25 m, tertiary 1.5 m. Authority: OWNER-SUPPLIED DESIGN BOUND (rajakaluve
   band 1.5-3.0 m; no surveyed depth data exists anywhere in-repo - PROMPT.md
   section 14.2 note). Recorded in every capacity_basis under `depth_bound`.
2. **Demand vs supply split**: rational method = DEMAND; Manning at the bounded,
   real section = SUPPLY; capacity IS the supply. Where demand exceeds it the
   edge is flagged `demand_exceeds_capacity` (basis key + metrics counter) and
   surcharges. Supersedes deviation D22.
3. **Rational validity cap**: contributing area > 5,000 ha (50 km2 small-catchment
   limit, CPHEEO 2019 Ch.3 framework / standard hydrology practice) -> NO capacity
   claim, routing-only basis identical to zero-slope edges.

Post-fix realized state (independent recount from gpkg bytes):

| metric | value |
|---|---|
| q_capacity percentiles (n=1208) | p50=0.24 · p75=5.63 · p90=40.81 · p95=81.62 · p99=197.35 · max=276.55 m3/s |
| demand_exceeds_capacity | 70 edges surcharge under the cited design storm |
| area-exceeds-validity no-claim | 32 edges (was inflating the tail) |
| zero-slope routing-only | 61 edges (unchanged) |
| partition closure | 1208 written + 61 zs + 32 area = 1301 observed ✓ |
| policy | outfall fraction 0.7365 ACCEPTED by owner; met=true via accepted flag; status=completed |
| fingerprint | 207907373348… (unchanged - topology untouched; determinism rerun: fp equal, adjacency bytes equal, q_nom series equal) |

## Unblock Round — results

| owner directive | outcome | evidence |
|---|---|---|
| M11 design intensity | **CITED + UNBLOCKED**: i = 62.5 mm/hr (midpoint of 55–70 mm/hr, Bangalore 60-min, T=25 yr). Sources: InfraLens CPHEEO 2019 SW Manual Ch.2 companion table + IRJET V4(I6) 2017 IMD-station study; uplift band [1.10,1.30] per frozen contract (=CPHEEO/AMRUT 10–30%). Reported to owner BEFORE computing. Coverage: **1,240/1,301 observed edges solved (95.3%)**; 61 zero-measured-slope edges skipped honestly (`zero measured slope…routing only` basis); 28 edges flagged `surcharge_by_definition` (D22 depth-envelope extension to 25 m, disclosed in basis) | `configs/drainage.yaml`, `capacity_metrics` block, gpkg capacity fields |
| Component policy | ==1 replaced by **outfall-terminating components ≥95% of observed reach length**. Realized fraction: **0.7365 (73.65%)** → policy NOT met → status stays `stopped_owner_adjudication`; 17 outfalls named with nearest receiving water + justification (valley assignment `unmapped` where name→valley is not unambiguous — owner may supply mapping) | manifest `component_policy`, `named_outfalls`; independently recomputed by audit (match ≤1e-6) |
| Connector redundancy | corridor merge (Jaccard ≥0.35 union-find, keep-longest) dropped **1,417** connectors; synthesised edges now **286**; headline fraction **0.5604** (was 3.5617 trajectory-length); unique footprint **339.8–480.5 km** | manifest counts + edge arithmetic closes: 1240 written + 61 zs + 286 synth = 1587 |
| M5b self-loops | reader loads WITH `allow_disconnected_load` + `owner_adjudication_ref` when `counts.dropped_edge_ids` enumerates the self-loop entries; without ref still refuses | graph_io reader + test_m5b red demo |
| M4 cycle-break | BOTH observed-edge drops (pool `any_fallback_no_synthetic_in_cycle`) justified by name in `cycle_break_justification` + reasons[]; WCC unchanged per round (98→98) | manifest |
| M2 self-hit | re-entry = CONTINUATION (D20): `self_hit_abort`=0, `self_reentry_count`=35,063 | walk histogram |
| M3 junction join | split-at-projection replaces stubs (D21): **328 junction splits, 0 unresolved, 0 stubs**; observed geometry length conserved via substring segmentation | counters |

Post-round topology: **110 post-stitch components** (was 269), 1,721 nodes / 1,587 edges, fingerprint
`207907373348…` **byte-deterministic across independent reruns** (adjacency bytes equal).
Root-cause fix shipped: stitch's rule-6 manifest path now derives from `run_dir`
(kills the two-key one-file overwrite trap that bit wave-3).

Tertiary correction for the record: WF-0 recon figures **4,443 comps / 121 junction
candidates were computed at 0.5–2 m tolerances on a permissive parse**; at the pinned
10 m semantics the correct values are **4,234 / 266** (loader diagnostics; KML and
waterways_bbmp.gpkg agree exactly). The EXCLUDE ruling stands (>93% invented).

Consolidated deep audit executed orchestrator-side after both ox providers rate-limited
(hard-block ladder): all acceptance criteria PASS with independent recomputation
(fraction, zero-slope, surcharge counts, arithmetic closure, determinism fingerprints,
65-test suite green). One residual: component-count definition differs between
reach-level DSU (manifest post=110) and node-level WCC over the artefact — no
reach_id on final edges bridges them; published as RECONCILIATION-GAP in test output.

Executed per `workflows/wf1-drain-graph.js` against the frozen contract
`configs/contracts/drain_graph.json` @ c47e107, CPU-only end to end. No WF-2
work was started.

## Headline numbers (owner asked first — read before anything else)

| quantity | value | provenance |
|---|---|---|
| **Synthesised connector length / pinned observed network (767.3 km)** | **3.5617× (356.17%)** | `runs/drain_graph_build/manifest.json.synthesised_fraction_of_total_length` (unclamped per deviation D13), recomputed from gpkg bytes by two independent verifiers |
| Same, vs realised in-graph observed length (766.062 km) | 3.5674× | verifier recompute |
| Synthesised length total | 2,732.86 km across 791 edges | gpkg `length_m` sum |
| Unique-footprint coverage of those connectors | 364.5 – 515.5 km (0.48–0.67× observed) | `counts.synthesised_walked_unique_cells`=36,450 × step bounds; ~7.5 mean crossings/cell — the walked-trajectory numerator is inflated by corridor convergence |
| Components | 365 pre-snap → **269 post-stitch** | manifest + independent union-find |
| Graph | 1,738 nodes / 1,805 edges / DAG / fingerprint `b582e4ede8a1b0ae…` byte-reproducible | adjacency + two independent clean reruns |
| Determinism | whitebox fill replaced by in-repo deterministic priority-flood (deviation D17); fingerprint + adjacency bytes identical across independent reruns | wave-3 independent rerun |

**Reading, stated plainly:** under THE LITERAL-PIN BUILD POLICY (all-walks-write,
flat increments masked inside retained basins, reach-granularity self-hit), the
build invents more conduit length than the observed network carries. The number
measures walk trajectory-length, not network built; the unique-footprint band is
the honest network-magnitude figure. Both are in the manifest.

## Addendum answer — the 349 contested retained-basin-interior cells

Script `scripts/wf1_contested_overlay.py`, logged run
`runs/wf1_contested_overlay/manifest.json`.

- Exact enumeration of the 349 is **BLOCKED**: its defining inputs (stale
  `filled.tif` sha `5e38…`, p3-condinvest scratch) no longer exist on disk.
  Closure: restore stale bytes + re-run instrumented staircase.
- Documented trace cell (114,1347): crossed by an OBSERVED edge, by NO
  synthesised connector.
- Signature superset (retained-basin interiors ∩ waterway footprints on the
  current stack, 187,258 cells): **488 of 791 connectors (62%) cross it**
  — necessary-not-sufficient evidence, guard recorded in the manifest.
  **Answer: yes, synthesised routing extensively traverses known-contested
  terrain; exact per-cell confirmation needs the blocked restoration.**

## Adjudication menu (decisions needed before WF-2 can consume anything)

M1 connector-writing policy vs pinned ==1 (unsatisfiable; frontier table in
manifest). M2 self-hit semantics (1,031 aborts). M3 junction join: split-at-
projection vs built stubs (791 refusals >25 m drive the 269-component residual).
M4 cycle-break dropped one OBSERVED edge (disclosed in reasons[]). M5 frozen
consumer >=1 vs producer ==1 contradiction. **M5b NEW: 12 input-geometry
self-loops make the candidate refuse-load under EVERY flag combination — no
override path exists; contract amendment or pre-drop needed.** M6 pntr input pin
→ in-run derivation. M7 elev_m schema text points at superseded bytes. M8 v1.2.0
additive fields. M9 lake-transit semantics. M10 fraction threshold never fires
where fiction occurs. **M11 capacity BLOCKED: no design-intensity source exists
in-repo (rule 1 held — zero hydraulic numbers written); supply return period +
IDF or intensity+source.** M12/M13 lake_boundary block + tertiary exclusion hold.

## Work assigned vs status (complete ledger)

| # | Assigned unit | Dispatch / executor | Status | Evidence |
|---|---|---|---|---|
| 1 | design:d8-routing strategy | worker-ox-alpha | **Done** | designs.json; measured frontier table, pointer provenance probe |
| 2 | design:catchment-hierarchy strategy | worker-ox-alpha-go (failover after openrouter rate-limit) | **Done** | designs.json; premise gauge refuted primary emitter |
| 3 | design:proximity-plus-gradient strategy | worker-ox-alpha | **Done** | designs.json; GREEN-LIGHT TRAP quantified |
| 4 | judge:hydrology lens | worker-ox-alpha | **Done** | judgements.json; winner d8 24/30 |
| 5 | judge:software-risk lens | worker-ox-alpha | **Done** | judgements.json; winner d8 24/30 |
| 6 | judge:reviewer-scepticism lens | worker-ox-alpha | **Done** | judgements.json; found frozen >=1-vs-==1 contradiction |
| 7 | synthesis:stitching-spec | worker-ox-alpha | **Done** | design_phase/spec.md; implementable literal-pin spec + menu M1–M13 |
| 8 | build:loader (KML ingestion + diagnostics) | oneshot-build | **Done** | loader.py; 14 tests; real-data CLI 163/870/767.275 km/1376 nodes |
| 9 | build:stitcher (snap/D8-walk/DAG/ids) | oneshot-build | **Done** | stitch.py + graph.py shim; census reproduces 365/1376 exactly |
| 10 | build:capacity (rational+Manning, cited) | oneshot-build | **Done** | capacity.py; 21 tests; blocked mode honors rule 1 |
| 11 | build:graph_io (writer + asserting reader) | oneshot-build | **Done** | graph_io.py; 18 tests incl 7 red demos |
| 12 | pipeline integration + full-domain run | orchestrator | **Done** | pipeline.py; 71 s CPU; deterministic fingerprint b582e4ed… |
| 13 | verify:stitcher (wave 1) | worker-ox-alpha | **Done** | 10 verdicts; surfaced determinism defect + convergence inflation |
| 14 | verify:loader (wave 1) | worker-ox-alpha | **Done** | independent parser agreement <1e-9; contract tertiary-figure misattribution found |
| 15 | verify:capacity (wave 1) | worker-ox-alpha → rate-limited; worker-ox-alpha-go → provider down; **orchestrator self-executed** per hard-block ladder | **Done (self-executed)** | contrib areas exact via corrected reverse-BFS; rule-1 sweep clean; false-alarm caught by discriminator before reporting |
| 16 | verify:graph_io (wave 1) | worker-ox-alpha | **Done** | fingerprint byte-exact recompute; refusal battery fires; silent-load gap quantified (M5) |
| 17 | Fix round 1 (determinism D17, node_id_map_sha256, labels, counters, coverage bounds, grid assertion) | orchestrator | **Done** | wave-2: F1/F2/F4/F6/F9 CONFIRMED |
| 18 | verify wave 2 | worker-ox-alpha | **Done** | independent rerun byte-identical rasters; F3/F5/F7/F8 residuals fed round 2 |
| 19 | Fix round 2 (label swap, dropped-id positions, M4 reason, footprints assertion, float64 fill, coverage note, whitebox cosmetics) | orchestrator | **Done** | wave-3: D1–D6 all CONFIRMED |
| 20 | verify wave 3 (final gate, within 3-wave cap) | worker-ox-alpha | **Done** | deltas confirmed; readiness R1–R6 measured; incident disclosed+remediated |
| 21 | RedTest: 7 workflow invariants demonstrated red (V5) | orchestrator (consolidated suite instead of 7 agent round-trips — time-bounded deviation) | **Done (consolidated)** | test_wf1_invariants.py: 13 tests, 5 mutation-proven reds, scope statements beside claims |
| 22 | Vacuity audit of invariant tests | worker-ox-alpha (wave 3) | **Done** | PASS_WITH_NOTES; independent spot-mutation moved observable; two named gaps (test_06 delegated demo, test_02 bookkeeping-not-physics) |
| 23 | verify:artefact-realized (final numbers from bytes) | worker-ox-alpha (waves 2–3) | **Done** | fraction/comps/nodes/edges/km recomputed independently twice |
| 24 | verify:rule-1 audit (fabrication hunt) | workers + orchestrator | **Done** | zero hydraulic numbers anywhere; disclaimers exact 791/791; no IDF source exists (R3) |
| 25 | verify:wf2-readiness (consumer seam) | worker-ox-alpha (wave 3) | **Partial** — read-only seam answers delivered; deliverable gaps remain | topo traversal works (~1.7 s/359k steps array-based); units block verbatim; GAPS: no reach_id→node LUT shipped, topo_order sha256 unpinned, empty capacity-bearing set degenerates coupling |
| 26 | Owner addendum: connectors vs 349 contested cells | orchestrator script + run | **Partial** — superset bound + trace-cell exact check DONE; **exact 349 enumeration BLOCKED** | runs/wf1_contested_overlay/: 488/791 connectors cross signature superset; trace cell observed-only; stale bytes (filled.tif 5e38…, p3-condinvest scratch) gone from disk — restore to close |
| 27 | Tertiary report-only recovery diagnostics | loader builder | **Done** | 5811 parsed / 4 failed / 0 reaches emitted; recon figures repointed (4234 comps @10 m) |
| 28 | Rule-6 manifests at run start everywhere | all builders | **Done** | loader/census/stage/overlay manifests present; one overwrite incident disclosed+restored (outputs.manifest divergence — design flaw noted for owner) |
| 29 | Rule-7 aggregated config resolution | all builders | **Done** | resolve_config per module; single ValueError listing all keys |
| 30 | Commit artefacts | — | **Blocked on owner** | tree left dirty by design; explicit-path staging only |
| 31 | Promote candidate to data/processed consumer path | — | **Blocked on owner** | requires M1/M5/M5b adjudication; placement guard holds (nothing ships disconnected) |
| 32 | Capacity unblock (design intensity) | — | **Blocked on owner (M11)** | no IDF/return-period source exists in-repo; supply return period + idf_table_path OR intensity_mm_hr + idf_source |

Legend: Done = realized-state verified by a verifier that did not write the code.
Partial = some sub-deliverable missing or bounded; Blocked = external input required.

## Verification record

- Wave 1 (4 audits): loader fully CONFIRMED (independent parser agrees <1e-9;
  census 163+870/767.275 km/1376 nodes reproduced twice); graph_io seam strong
  (fingerprint byte-exact recomputes; all refusals fire); stitcher honest-but
  surfaced determinism defect + convergence inflation; capacity executed by
  orchestrator after dual-provider rate-limit — contrib areas CONFIRMED exact by
  corrected reverse-BFS + production agreement (my first BFS instrument was
  wrong; discriminator experiment caught it before reporting).
- Fix round 1 → Wave 2: determinism fix proven; node_id_map_sha256 added;
  labels/counters/coverage published. PARTIALs fed round 2.
- Fix round 2 → Wave 3: six deltas CONFIRMED; suite audited PASS_WITH_NOTES;
  readiness measured (R5 feasible: ~1.7 s/359k steps array-based; R4 gaps listed).
- Disclosed incident: wave-3's own /tmp rerun overwrote the repo manifest via
  `outputs.manifest` (config keys diverge from run_dir — design flaw noted);
  restored from byte-identical rerun; only volatile fields differ.

## RedTest

`tests/drainage/test_wf1_invariants.py` — 13 tests over realized bytes
(connectivity refusal, uphill-count traceability, DAG, rule-1 NULL sweep,
fraction/tags, grid seam, mass identities, fingerprint witness), 5 red demos via
/tmp-copy mutations, scope statements beside every claim. Whole drainage suite:
**74 passed**.

## Artefact & log inventory (everything produced this run)

Design phase: `runs/drain_graph_build/design_phase/{designs.json,judgements.json,spec.md}`
(mirrors `/tmp/opencode/wf1/`). Code: `src/jaladhar/drainage/{__init__,loader,stitch,graph,capacity,graph_io,pipeline}.py`.
Tests: `tests/drainage/test_{loader,stitcher→test_stitch,capacity,graph_io}.py` +
`tests/drainage/test_wf1_invariants.py`. Config: `configs/drainage.yaml`.
Candidate artefacts: `runs/drain_graph_build/{drain_graph.gpkg,drain_graph_adjacency.json,manifest.json,derived_pntr_d8.tif,hybrid_filled_dem.tif,reach_footprints.tif,loader_manifest.json,census_manifest.json}`.
Overlay: `scripts/wf1_contested_overlay.py`, `runs/wf1_contested_overlay/manifest.json`.
This report: `docs/WF1-DRAIN-GRAPH-REPORT.md`. Scratch (volatile):
`/tmp/opencode/wf1*`, `/tmp/opencode/wf1_det`, `/tmp/opencode/wf1_wave2`.

Nothing committed — tree left dirty for owner staging (`git add` by explicit path only).

Decision requested: adjudicate M1/M2/M3/M5/M5b/M11 (+M4 ratify) — then either
re-run the stitcher under the chosen policy or promote the candidate; only after
that does WF-2 have a loadable graph.
