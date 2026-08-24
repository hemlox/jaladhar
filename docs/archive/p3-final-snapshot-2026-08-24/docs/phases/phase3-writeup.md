# Phase 3 — September 2022 validation gate

**Disposition, 2026-08-22: OPEN, NOT PASSED; paused on forcing and budget decisions.** The
engineering path is reproducible and completes a wet real-input GPU smoke. The science-blocker
investigations are terminal: both tested Sentinel routes are invalid instruments; the accepted
official hourly source has no Bengaluru 2022 gauge field; and 15 auditable depth observations remain
below the 30–50 target. Per [`../SPEC.md`](../SPEC.md) §8, stop before Phase 4.

This document separates three different objects that earlier writeups conflated:

1. the historical full 48-hour baseline/variant, which measures scientific behavior on the pinned
   pre-repair stack;
2. the repaired integrated stack and one-hour GPU smoke, which verifies execution/provenance; and
3. the Phase 3 gate verdict, which depends on independent observational evidence.

A successful smoke is not a scientific replay. A stable solver is not an accurate city model.

## 1. Fixed gate

The specification requires one city-wide September 2022 replay, forced by actual KSNDMC gauge
records and scored against the complete BBMP list, a validated full-footprint Sentinel-1 extent
reference, and 30–50 sourced ground-truth points. Required metrics are CSI, hit rate, false alarm
ratio, and depth RMSE. The approximate comparison bar is CSI 0.73 and depth RMSE 0.17 m.

The terminal rule is explicit: if there is no defensible city-wide match, stop and change the
project. The gate does not permit “engineering complete” to substitute for observational agreement.

## 2. Historical full-city replay — finding, not current-stack acceptance

The completed baseline and elevation variant each integrated 48 simulated hours over the full
buffered Bengaluru domain. They ran sequentially on the RTX 4060 and each took about 4.78 wall/GPU
hours under the project's realized accounting definition.

| quantity | baseline | terrain variant |
|---|---:|---:|
| accepted steps | 160,081 | 160,081 |
| max Courant | < 0.85 | < 0.85 |
| mass relative residual | 4.323e-6 | 4.41e-6 |
| GT depth-eligible | 22 | 22 |
| within / below / above band | 0 / 20 / 2 | no gate pass |
| depth RMSE / MAE | 0.7646 / 0.696 m | no material city-wide closure |
| GT_06 3x3 event max | 0.0073 m | 0.9337 m |

GT_06's observed band is 1.20–1.45 m. The variant proves a strong local response to measured
underpass geometry, but remains 0.266 m below the band's lower edge and leaves most other points
unchanged. It is evidence that the mechanism matters at GT_06, not evidence that terrain explains
the city-wide error.

The replay-forensics audit corrected two reporting errors:

- the old/new GT_06 headline mixed exact-cell and 3x3-local-max definitions; and
- the previously reported 1.284 “realized GPU-hours” was a kernel-equivalent estimate. Realized
  accounting is wall time times the actually allocated measured worker.

Historical producer/input identity gaps mean this pair is retained as scientific evidence, not
relabeled as a final repaired-stack run.

## 3. Engineering repairs

### 3.1 Terrain

- Staircase cuts may not modify a retained-basin intermediate cell.
- Depression-fill cache reuse requires source and filled-raster byte/grid identity; same-shape
  changed bytes and truncated rasters redden and regenerate.
- Depression classification receives the configured pre-breach path explicitly.
- The clean depression/conditioning rebuild has zero retained-basin modifications and retained
  volume inside the pre-registered 20–35 Mm3 band.
- The exact buffered building-conveyance test rasterizes the co-produced roads/waterways and
  requires mismatch count zero; percentage proxies were removed.

### 3.2 Validation ownership

- GT classification and water tracing have distinct, non-nested output directories.
- Segment validation receives a solver input directory and owns a separate output directory.
- Equal/nested paths fail before compute; post-start failures produce terminal failed manifests.
- The previously blocked validation/water tests now execute; residual skips are named provenance
  debt rather than passed checks.

### 3.3 Replay provenance and compute

- Terrain and drain manifests bind the exact selected raster SHA-256, shape, dtype, CRS, transform,
  and nodata.
- The D4 conditioning guarantee is asserted against the D4 solver stencil.
- Validation rasters and XML/CSV/KML files use type-appropriate identities.
- Native IMERG cell IDs and the exact interval-rate table are hashed; that same table feeds GPU
  tensors.
- The benchmark anchor has a start-to-terminal manifest and must hash-match the configured semantic
  row and device/Torch identity.
- Budgeting uses an end-to-end overhead policy and refuses above the configured nightly ceiling.
- Runtime GPU observation is recorded separately from configured allocation and reconciled before
  completion.
- Relative CLI paths resolve once against the selected repository.

These repairs close reproducibility defects. They do not improve or recalibrate the scientific
score by declaration.

## 4. Final integrated checks

### 4.1 Full-window preflight

The real-input CPU-only preflight traverses configuration, producer seams, validation identities,
terrain/drain bytes, benchmark provenance, event duration, and budget without launching a
simulation.

| quantity | realized value |
|---|---:|
| buffered cells | 12,728,415 |
| step band | 77,317–423,487 |
| kernel-equivalent floor | 0.596 GPU-h |
| pessimistic end-to-end bound | 12.650 GPU-h |
| configured ceiling | 10.0 GPU-h |
| decision | `REFUSE_FULL_REPLAY` |

The refusal is correct behavior. Raising the budget is an owner/config decision, not a code bypass.

### 4.2 Wet real-input GPU smoke

The selected one-hour event window contains nonzero realized IMERG forcing and exercises adaptive
routing, sinks, snapshots, scoring, artifact writes, runtime allocation checks, and terminal
provenance.

| quantity | realized value |
|---|---:|
| areal rainfall | 10.33 mm |
| total rain input | 12,427,494.5 m3 |
| steps | 1,694 |
| wall time | 160.2 s |
| max Courant | 0.782 |
| mass relative residual | 4.465e-7 |
| event P99 depth | 0.119 m |

This verifies the merged solver path with active wet physics. Its one-hour, dry-initial-condition
depth scores are not event validation and are excluded from the gate verdict.

## 5. Acceptance-axis audit

| required axis | finding | gate consequence |
|---|---|---|
| City-wide numerical integration | historical full run completed stably | engineering PASS only |
| Actual KSNDMC forcing | accepted source has zero Bengaluru Urban/Rural 2021–2022 rows; two remote gauges only; alerts are lower bounds | FAIL / owner forcing decision |
| BBMP hit rate and FAR | no defensible complete metric pair under final contract | FAIL |
| Sentinel-1 city-wide extent | dual-pol GRD and HyP3 coherence/amplitude both fail the registered instrument criterion | FAIL / no admissible CSI |
| 30–50 sourced GT points | 31 event-location attestations, 15 auditable strict-depth rows | FAIL depth-scope target |
| Depth agreement | 0/22 within; RMSE 0.7646 m | FAIL stated bar |
| Calibration/held-out test | no defensible calibrated city-wide result | FAIL |
| Repaired-stack 48-hour rerun | refused by configured budget | BLOCKED, and insufficient alone |

No arithmetic combination of the historical findings or completed blocker evidence yields a pass.
The only paused actions are Darshil's forcing and budget decisions; a replay, if explicitly funded,
is diagnostic and cannot erase the failed Sentinel or strict-depth-scope axes.

## 6. Findings and readings

### Findings

- The solver is numerically stable on the completed historical event and active wet smoke.
- The historical depth score is substantially below the target.
- A measured underpass carve strongly changes GT_06.
- Both registered Sentinel reference routes are invalid for the claimed extent score.
- The accepted official telemetry source does not provide a Bengaluru September 2022 gauge field;
  KSNDMC alerts verify event severity but cannot become interval forcing.
- The curation audit preserves 31 event attestations but only 15 auditable depth bands; its strict
  depth scope is below the 30–50 target.
- The final full replay exceeds the current pessimistic budget ceiling.

### Readings, not load-bearing

- Underpass geometry is likely important at additional below-grade sites.
- Rainfall concentration may explain part of the underprediction.
- Drain representation may redistribute water materially.

Those readings name experiments/data acquisitions; they do not authorize a causal verdict or Phase
4 work.

## 7. Reproduction and evidence

```bash
# Complete CPU suite, no skip flags
CUDA_VISIBLE_DEVICES="" python -m pytest -q -p no:cacheprovider

# No-simulation full-window contract
CUDA_VISIBLE_DEVICES="" python scripts/run_phase3_final_preflight.py --out <fresh-dir>

# Final terrain setup; source checkout is read-only
python scripts/run_phase3_final_terrain.py \
  --source-repo /path/to/source-checkout \
  --scratch scratch/phase3_final_terrain \
  --audit-out runs/phase3_final_terrain_setup
```

There is no terminal `runs/phase3_final/REPORT.md`: a full repaired-stack replay is paused behind
the documented forcing and budget decisions. The current authoritative evidence is the terminal
blocker/preflight artifacts named in `docs/HANDOVER.md` and `docs/OPEN-ITEMS.md`; earlier branch
reports remain preserved for audit history rather than current-status conclusions.

## 8. Continuation checkpoint

Phase 3 is not finished scientifically. Engineering repair and the A/B/C evidence work are
complete. It is paused awaiting an explicit forcing representation decision and an explicit budget
decision. Detailed executable state is in
`../../prompts/phase3-session-transfer-claude-2026-08-21.md`. Phase 4 scenario generation and
Phase 5 surrogate training remain prohibited.
