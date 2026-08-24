# BOOTSTRAP — getting from a fresh clone to a working checkout

`data/` is **8.8 GB** and `runs/` is **2.4 GB**. Neither ships. What ships is `data/seed/` —
5.8 MB of inputs that are either irreplaceable or expensive to re-source — plus every fetcher
needed to rebuild the rest.

```bash
uv venv --python 3.11 && source .venv/bin/activate
# Choose exactly one backend first; the editable install then reuses it.
uv pip install --torch-backend=cpu torch==2.6.0 # CPU / no NVIDIA dGPU
# uv pip install --torch-backend=cu124 torch==2.6.0  # NVIDIA + CUDA 12.4
uv pip install -e .
cp .env.example .env && chmod 600 .env          # fill every required value
python scripts/bootstrap_data.py                # unpack the seed bundle
```

### Whitebox on a checkout path containing spaces

The upstream `whitebox` installer can print `chmod: cannot access ...` after it downloads its
Linux binaries if the absolute checkout path contains a space (for example, `Local Disk`). It is
an installer quoting defect, not a failed download. Repair and verify the installed binaries with:

```bash
python scripts/repair_whitebox_permissions.py
```

Run this after every reinstall only when that installer error appears. The command changes execute
bits on Whitebox's downloaded binaries and plugins only, then runs `WhiteboxTools().version()`.
It does not change project source or data. `python scripts/repair_whitebox_permissions.py --check`
verifies the permissions without changing them.

## Two-machine collaboration

Use the same committed `uv.lock` and Python 3.11 environment on both machines. Install
`torch==2.6.0` with the CPU command above on the laptop and with `--torch-backend=cu124` on the
RTX 4060 machine; neither backend is the project's universal default.

- Exchange source through clean commits. Provenance-bearing runs refuse a dirty tree.
- Keep `data/` and `runs/` local and ignored. Share only the manifest and explicitly required
  artifacts for a review or downstream stage; never copy an entire machine-specific checkout.
- Use repository-relative paths only. Do not commit absolute paths from either machine.
- Configure device selection, workers, tile sizes and budgets in `configs/compute.yaml`. Never
  hardcode a GPU model or machine-specific worker count in source.
- When transferring an artifact, transfer its manifest with it and verify the manifest's commit
  exists in the receiving checkout before using the artifact.

`bootstrap_data.py --check` reports without writing anything. It verifies the bundle against
`data/seed/SHA256SUMS` before it touches the tree, and prints the fetch checklist afterwards.

---

## The three tiers

### Tier 1 — ships in `data/seed/` (5.8 MB)

| file | size | why it ships |
|---|---|---|
| `sept2022_points.csv` | 12 KB | **Legacy source set.** 24 ground-truth flood points hand-built from news research, preserved unmodified for provenance. The active audited file is `data/raw/groundtruth/sept2022_points_v2.csv`: 32 rows, 31 event-location eligible, but only 15 auditable strict-depth rows. The strict-depth scope is below the specification's 30–50 target and was not padded — see Non-negotiable 1 |
| `bbmp/*.kml` | 172 KB | 399 BBMP flood-prone locations across three lists, manually sourced. One sits at null island (0,0) — a real data defect, left in deliberately so the next reader finds it the way we did |
| `bbmp_drains_2022.tar.gz` | 5.0 MB | BBMP rajakaluve stormwater network, 6,839 features / 1,988.47 km, Public Domain via OpenCity. 2.33× the OSM network. Took a full goal to locate and licence-check |
| `fetched_articles.json.gz` | 487 KB | Contemporaneous news corpus for the event. **This is where the 131.6 mm / "most of it in under 12 hours" evidence lives** — the measurement that reopened the forcing question after it had been closed. It sat unread for a day; do not let that happen twice |
| `unrepresentative_underpass_register.csv.gz` | 123 KB | 2,534 locations where the DEM demonstrably cannot see the feature that floods, with measured profile metrics and **zero invented depths**. A headline deliverable in its own right |

### Tier 2 — re-fetchable, scripted, needs bandwidth and two credentials

| what | command | needs |
|---|---|---|
| BBMP 2023 ward boundary + DEM (FABDEM primary, GLO-30 fallback) | `python -m jaladhar.terrain.fetch` | none — fetcher downloads and SHA-256 verifies the configured 225-ward boundary, then uses config-driven DEM source order; GLO-30 takes over automatically if FABDEM tiles fail |
| OSM standing-water, quarry, landfill polygons / roads / waterways / buildings | `python -m jaladhar.terrain.{water,roads,drains,buildings}` | none |
| GPM IMERG half-hourly granules | `python -m jaladhar.forcing.fetch_imerg` | `EARTHDATA_TOKEN` **and** a one-time GES DISC EULA click. A 403 "EULA Acceptance Failure" means the click wasn't done — it is not a bad token |
| Sentinel-1 dual-pol GRD | `python -m jaladhar.validation.fetch_s1` | `CDSE_S3_ENDPOINT`, `CDSE_ACCESS_KEY`, `CDSE_SECRET_KEY`; the current stage fetches/verifies VV, VH, annotation, calibration and noise objects |
| Karnataka hourly gauge telemetry (2021–2025) | `python scripts/run_fetch_nwdp.py` after merging `goal/p3-forcing-gt` | official National Water Data Portal CSV/API; accepted source has no Bengaluru Urban/Rural 2021–2022 rows, so it cannot supply the Phase 3 city forcing |

**Watch the DEM source.** FABDEM is bare-earth; GLO-30 is a *surface* model with buildings and
canopy baked into the elevations. `fetch.py` writes a `dem_is_dtm` flag into its manifest and
`conditioning.py` branches on it. Burning building footprints onto a DSM double-counts every
building. If the fallback silently wins, that flag is the only thing standing between you and a
badly wrong terrain.

**Licence:** FABDEM is **CC BY-NC-SA**. That non-commercial exposure is live and deliberate, not
overlooked — archive item 2.

### Tier 3 — derived, regenerable, expensive

Conditioned DEM, basin classification, road/drain/building rasters, roughness, and replay outputs.
Terrain construction is CPU work; replay uses the measured RTX 4060 allocation. Historical timing
is not a promise for the repaired runner.

```bash
python -m jaladhar.terrain.build                       # complete raw-to-processed terrain
python scripts/run_phase3_final_terrain.py --help      # isolated final conditioning setup
python scripts/run_phase3_final_preflight.py --help    # contract + budget, no simulation
python -m jaladhar.validation.event_replay --help      # replay; fresh output dir required
```

The current default 48-hour preflight refuses because its 12.650 GPU-hour pessimistic bound exceeds
the configured 10-hour ceiling. Do not bypass that refusal or silently increase the budget. Re-run
the measured budget only after the three scientific inputs in `HANDOVER.md` are closed. The one-hour
wet GPU run recorded for integration is a smoke test, not Phase 3 acceptance.

If an OSM/Overpass stage fails with a connection or chunked-read error, it is an external-data
block. Re-run the same CLI after the service recovers. Do not replace the missing roads, buildings,
or waterways with an empty or synthetic layer. A successful command must produce its terminal
manifest and realized raster files before its dependent tests are meaningful.

---

## What you cannot rebuild, and what that means

The 24-point ground-truth seed set is the only input with no scripted path. Do not overwrite it.
The committed curation stage produces a separate annotated v2 CSV; its final audit has 31
event-location attestations but only 15 source-supported depth bands, below the 30–50 target. Do
not extend either file with unsourced points or use `UNSTATED` legacy bands for depth RMSE.

Two known defects in it, both recorded rather than quietly fixed: **GT_14's `observed_date` is
2022-08-30**, a different flood from the one being replayed, so it should not have been scored; and
GT_14 / GT_24 snap 140.9 m and 171.0 m to their road segments against a 5.7 m median.

---

## Reproducing a specific published number

Every numeric claim in `docs/` traces to a committed script and a run manifest. The two most
load-bearing:

```bash
python scripts/run_sar_water_investigation.py   # historical VV-only instrument investigation
python scripts/run_water_tracing.py --config configs/analysis.yaml   # catchment budgets, traced mechanism
```

Both need Tier 2 fetched first. The VV-only SAR run does not settle the newly acquired VH/coherence
axes. If a number does not reproduce, that is a finding — see `CLAUDE.md` V9, and the five reviewer
figures that failed exactly this test.
