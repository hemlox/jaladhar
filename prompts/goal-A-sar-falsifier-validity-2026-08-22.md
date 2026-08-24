# Goal A — Was the SAR falsifier's "known water" actually water? (CPU + network only)

**Read first:** `prompts/phase3-roadblock-resolution-2026-08-22.md` (§A and the decisions block),
then `CLAUDE.md` (V1–V11, R1–R8 binding), `docs/HANDOVER.md`. All four owner decisions are already
made and recorded in the resolution plan — do not re-open them or wait on Darshil.

## Question (never assume the answer)

1. What fraction of each of the 9 criterion lakes' OSM polygons was open water at the event
   epoch (September 2022), measured from evidence independent of Sentinel-1?
2. Under truth masks corrected to realized open water, does any pre-registered instrument family
   satisfy the unchanged rule (recall > 0.70 at each target, dry FPR < 0.01)?

## Worktree and ownership

- Fresh branch/worktree from `goal/p3-science-closure` @ `b21cb7ac84fd…` (suggest
  `goal/p3-sar-criterion-v2`). Python: `/home/darshil/Desktop/sih/clginternal/.venv/bin/python`,
  `PYTHONPATH=src`. Explicit staging only; never `git add -A`; no attribution footers; no push.
- **You own:** new `src/` stage(s) for S2 acquisition / water classification / mask build;
  `configs/sar_instrument_criterion` v2; `runs/s2_lake_state/`, `runs/sar_dualpol_instrument_validation_v2/`,
  `runs/sar_coherence_instrument_validation_v2/`.
- **You must not touch:** forcing/GT/terrain code or data (Goals B/C own those), `p3-final`
  uncommitted docs, any existing `runs/` directory (read-only evidence), the GPU (CPU only).

## Method

1. **Committed S2 acquisition stage.** Copernicus Data Space (credentials in
   `clginternal/.env`; never print/log them). Sentinel-2 L2A, June–August 2022, tiles covering
   all 9 lakes (Varthur, Bellandur, Hebbal, Madiwala, Ulsoor, Agara, Lalbagh, Kaikondrahalli,
   Yelahanka), per-lake cloud screening (a partially clear scene over a lake counts for that
   lake). **Pre-event scenes only** for the primary result: post-event scenes are contaminated by
   flood refill of any drawdown. Manifest: scene IDs, dates, cloud, byte hashes.
2. **Committed water-classification stage.** NDWI (or an equivalently standard index — declare
   it, cite the threshold convention) → per-lake `open_water_fraction_of_osm_polygon`, plus the
   binary realized-open-water submask per lake. Corroborate with JRC GSW / Dynamic World where
   available and with the dated desilting articles already in
   `clginternal/data/fetched_articles.json` (Indian Express "lake-cat" explainer; The Hindu
   "overflowing-bellandur-and-varth…"). **Never use the Sentinel-1 scenes under test to build or
   trim masks — that is circular (V3).**
3. **Adjudicate in writing (V6).** If the target-lake fractions are far below the held-out
   lakes', record: the falsifier's truth masks were wrong (test wrong; code and spec not). Then
   commit **criterion amendment v2**: identical rule, identical families, identical held-out
   threshold-selection procedure; targets = realized-open-water submasks; held-out masks get the
   same correction. Commit the amendment and rationale BEFORE running any target evaluation (R5).
4. **Re-run both instrument validations** (dual-pol GRD, HyP3 product — both inputs already on
   disk with hashes in the terminal v1 manifests) under v2. Mutation-test (V5): a deliberately
   wrong mask must redden the stage. State realized pixel scope beside every claim (V7).
5. **If v2 passes:** build the city-wide extent reference and the urban-density stratification
   layer per SPEC §8.3 so Goal D can score stratified CSI (dense-urban stratum flagged
   instrument-unreliable). **If v2 fails, or optical shows the interiors WERE open water:** say
   so plainly in the first sentence — `instrument_invalid` stands, the axis closes as externally
   unresolvable, and Goal D scores extent from points only.

## Attack block

The resolving session's hypothesis is that the Varthur/Bellandur OSM masks overstate open water
(desilting drawdown since June 2020 + hyacinth/froth), which would explain both instruments
passing all seven held-out lakes (0.7596 / 0.8125 held-out recall, manifest-verified) while
failing only the targets (~0.42–0.57). **Attack this hypothesis.** If the optical evidence
contradicts it, that is the finding. Do not select scenes, thresholds, or masks toward a pass.

## Completion contract

Terminal manifests for every stage; per-lake realized fractions; criterion v2 + rationale +
recorded mutation; v2 validation manifests with verdicts; every claim labelled FINDING or
READING; a 10-line summary naming which axes this closes and which stay open (V11).
