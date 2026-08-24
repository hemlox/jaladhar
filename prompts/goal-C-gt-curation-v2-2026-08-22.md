# Goal C — Ground-truth curation standard v2 and expansion (CPU + network only)

**Read first:** `prompts/phase3-roadblock-resolution-2026-08-22.md` (§C, §D, decisions block),
then `CLAUDE.md`, `docs/HANDOVER.md`. Curation standard v2 is already authorized (decision 4).

## Question

Under the spec's own method — SPEC §8.4: "approximate depth from visual cues (kerb height, car
wheels, doorsteps)… report depth as a band" — how many auditable depth rows does the evidence
actually support? Target ≥ 30 strict rows; if the evidence tops out lower, the realized count IS
the result. **Do not pad. No invented coordinates. No invented bands.**

## Worktree and ownership

- Fresh branch/worktree from `goal/p3-forcing-gt` @ `b69f9f6dd20e…` (suggest `goal/p3-gt-v3`).
  Python `clginternal/.venv/bin/python`, `PYTHONPATH=src`. Explicit staging only; no
  `git add -A`; no footers; no push. CPU only.
- **You own:** `data/raw/groundtruth/**`, `data/curation/**`, curation modules under `src/`,
  `runs/gt_curation_v2/`, `runs/event_area_lists/`. Disjoint from Goal B by path — do not touch
  forcing modules/configs or `runs/forcing_*`; do not touch SAR/terrain; existing `runs/` are
  read-only evidence.

## Method

1. **Commit curation standard v2 + rubric config.** A depth row is auditable iff it carries
   EITHER a supporting text quote (v1 standard) OR archived visual evidence: media URL + frame
   timestamp/photo + the named cue visible + a fixed cue→band mapping. Derive the rubric table
   from the bands the frozen v2 rows already use (kerb / wheel-centre / door-sill / bonnet /
   waist / chest classes visible in `sept2022_points_v2.csv`, SHA `7b498e6b…`) so v1 and v2 rows
   stay mutually consistent; commit it as config, not code constants. Record the V6 adjudication
   in the run manifest: the v1 quote-only test was stricter than the spec; spec is authority.
2. **Red-test the rubric (V5):** one deliberately mis-banded and one cue-absent row must redden
   the audit; record the mutations.
3. **Re-audit the 17 excluded rows** under v2; recover media via archive.org where links died.
   Preserve every v1 eligibility field; nothing is silently upgraded — each promotion records
   the exact evidence that justified it.
4. **Curate the known leads** (from `clginternal/data/fetched_articles.json`, 153 articles):
   Majestic / Okalipuram / Kasturinagar railway underbridges, Munekolalu, Yemalur–Bellandur
   road, Siddapura/Whitefield, Adarsh Estate/Bellandur, Wipro/Sarjapur, RBD Layout, named
   housing complexes. Apply the same date/duplicate/snap exclusions as v1 (red-tested).
5. **Freeze `sept2022_points_v3.csv`** with SHA in the terminal manifest; report the new SHA in
   your completion summary (Goal D asserts it before any replay). Tag every row underpass vs
   surface so Goal D can stratify (SPEC §14 known-limitations reporting).
6. **Separate artifact, not depth rows:** harvest official event-specific affected-area lists
   (BBMP zone lists, control-room reports quoted in the corpus) into an extent/hit-rate evidence
   table with per-item source quotes — this feeds the points axis (§D of the resolution plan),
   distinct from depth truth.

## Attack block

The resolving session expects many of the 17 exclusions to be recoverable and ≥30 to be
reachable. **Attack it:** a cue that is narratively claimed but not visible in archived media
does not count; a location that cannot be pinned without invention does not count. If the
realized count is 19, the answer is 19, stated plainly with the shortfall named — the 31
event-location attestations already satisfy the point-count axis regardless.

## Completion contract

Terminal manifests; v3 CSV + SHA; per-row provenance complete; rubric + recorded red mutations;
realized strict-depth count with the promotion/rejection ledger; the affected-area evidence
table; FINDING/READING labels; 10-line summary with still-open axes (V11).
