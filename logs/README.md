# logs — agent reports, chronological

Raw reports from implementation agents, in the order they were produced (2026-08-17 to 08-18).
Kept as provenance: numbers cited in `OPEN-ITEMS.md` and `docs/phases/` trace back to these, and
several contain the raw stdout of a committed script.

**They are primary sources, not conclusions.** Several claims in them were later withdrawn or
corrected — read `07-independent-audit-of-reviewer.md` and the `W1`–`W21` corrections in
`docs/archive/open-items-full-2026-08-18.md` before relying on anything here.

| # | report | outcome |
|---|---|---|
| 01 | Phase 3 gate, uncalibrated baseline | first end-to-end gate run. Its error-budget split was later found fabricated |
| 02 | Terrain residual accounting and fill | residual counts reconciled, Invariant R added, fill characterised |
| 03 | Per-road-segment rescore | segment-level scoring with null models. Null later found broken (segment length bias) |
| 04 | Drain diagnosis and underpass register | 2,534-segment register with zero invented depths; BBMP rajakaluve network acquired |
| 05 | Rajakaluve drain rebuild | drain rasters rebuilt on 1,988 km of BBMP network |
| 06 | Measurement corrections and trunk signal | CSI/FAR marked uninterpretable against a positive-only list |
| 07 | **Independent audit of the reviewer** | the most important file here. Found the reviewer's measurements reproduced while its inferences broke |
| 08 | SAR water classifier investigation | 25 classifiers, 6 families. Closed extent as physically unmeasurable |
| 09 | Water tracing v1 | **invalid** — 62-cell catchment for a ~150 km² basin. Kept to document the failure mode |
| 10 | Water tracing v2 | validated delineation behind a method gate; traced the mechanism |
