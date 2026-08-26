# Auditor A report — WF-3 read-only adversarial audit, 2026-08-25

Dispatched per the Fixer-1 continuation workflow (Sol-xhigh-equivalent reasoning tier,
worker-ox-alpha). Verbatim findings preserved below; the auditor made zero repository
modifications (verified byte-identical `git status --porcelain` and HEAD before/after).

## Overall verdict

No CRITICAL or MAJOR findings. All five audited areas (nowcast seam, provenance/admission,
product contract, G1 scorer/gates, signed-G3 classification) hold under adversarial probing;
`docs/WF3-FINAL-VERIFY-2026-08-25.md` reproduced on every claim reachable with committed
artifacts (9 reproductions), with one MINOR reproducibility finding and five NOTEs.

## Scope results (A–J)

- **A conversion seam — DONE.** Frozen formula implemented bit-exactly
  (`interface.py:201-210` vs `nowcast_input.json`); dry-sentinel double guard each load-bearing
  (mutation probe: dropping both reproduces aliasing; dropping either stays safe); named
  consumer assertion reddens under factor-2.0@15min, shape, sentinel, and coverage mutants.
- **B startup-before-fetch — DONE.** No ordering constructible where a fetch precedes gap
  aggregation via the public API (`nowcast.py:691-699` ordering; injected-fetcher probe proves
  the source callback is never invoked).
- **C envelope/poll-log/cache — DONE.** Hash binding regex + re-hash of disk bytes; payload
  parsed only from the sha-bound file; bool-rejecting http_status; cache_decision enum;
  freshness sign-checked; staleness beyond one validity window refused; raw_response stored but
  never readable as production data.
- **D no-fallback/N-3 exit 2 — DONE.** CLI exit 2, six aggregated gaps on stderr, zero file
  differences under data/ and runs/ (4838 files hashed before/after).
- **E admission binding — DONE.** Both manifest SHA and realized depth bytes bound at
  validation time; coupled declarations refused; producer threading verified end-to-end;
  realized hashes recomputed equal to admission records.
- **F product time/temporal semantics — DONE.** v5 issue_time_utc == start_time_iso byte-equal;
  v2 preserved (hash matches) and demonstrably carries the old defect; temporal fields + label +
  demo_fallback honest; independent consumer re-validation passed.
- **G full product validation — DONE.** Independent raw-CSV recount: 176171 rows == lookup set;
  40519/128605/7047 split 4735+2312 reconciled against the frozen contract file itself;
  legality matrix zero violations; twins equal; v2 vs v5 identical realized state as required.
- **H G1 arithmetic/null/reproducibility — DONE.** Recount from point_details matches headline
  under the documented rule; all identities exact; null uses same snap rule and keeps fixed
  misses; seed/draws recorded; reduced scope cannot PASS (`_gate_verdict` BLOCKED path).
- **I signed G3 BLOCKED — DONE.** status BLOCKED hardcoded (`score_g1.py:1055`), holdout marked
  RETROSPECTIVE_NOT_HELD_OUT; diagnostics recounted (0/16 local-max with GT_03 ABOVE at 2.16 m;
  1/16 exact-cell with GT_03 0.9349 m in [0.85,1.10]); reproduction CONFIRMED 16/0; no PASS/FAIL
  emission path exists.
- **J evidence quality — DONE.** 9 numeric claims independently reproduced including the road
  census (exact) and all nine preserved hashes; V10 derivation checks clean; V11 axes closed
  only where measured.

## Consolidated findings

| id | severity | location | finding | fix direction |
|---|---|---|---|---|
| F1 | MINOR | tests/test_forcing.py:229-242 | Live-network test makes the focused suite non-deterministic (observed 67/9 once vs stable 68/8) | Gate behind explicit opt-in env flag or mock transport |
| N1 | NOTE | src/jaladhar/validation/event_replay.py:466,478 | Latent `interval_s=1800.0` hardcode in timestep-loop interval selection; unreachable today (historical mode enforced) | Derive from event intervals or add a seam assertion if the driver is ever reused |
| N2 | NOTE | src/jaladhar/forcing/nowcast.py:394-404,668 | Courtesy/cache values validated+recorded but no polling client exists to enforce them | Acceptable while N-3 blocked; enforce red-first when a client lands |
| N3 | NOTE | scripts/score_g1.py:369-383 | Toroidal null is distributionally less road-adjacent than the observed pattern; bias favors PASS, gate FAILED anyway so verdict robust | Consider road-conditioned translation for future gates; do NOT change the recorded FAIL; out-of-snap asymmetry already reported in the null block |
| N4 | NOTE | docs/WF3-FINAL-VERIFY-2026-08-25.md:112 | Road-census figures lack a cited producing invocation despite the doc's V9 header (figures verified correct) | Cite `jaladhar.routing.api health` as the producing command in the doc |
| N5 | NOTE | src/jaladhar/forcing/nowcast.py:809-827 | Request-side ValueError (naive timestamp, >3 h horizon) exits 1 via traceback rather than the sanctioned exit 2 | Catch ValueError alongside NowcastUnavailableError in main() |

## Could-not-verify (declared, not hidden)

Disposable-mirror runs, static checks, clean-tree lifecycle mutation, and the full CPU suite were
not re-executed under the read-only constraint; downstream artifacts were verified instead where
possible. Production-session GPU absence is a declaration the auditor cannot counterfactually
observe (all realized manifests record cpu). Envelope capture authenticity is owner action by
design.

## Modification statement

Zero modifications: no file created, edited, staged, committed, stashed, reset, checked out, or
restored; HEAD and porcelain status byte-identical to audit start; probe scripts only under
/tmp/opencode/auditA/; every command CPU-only.
