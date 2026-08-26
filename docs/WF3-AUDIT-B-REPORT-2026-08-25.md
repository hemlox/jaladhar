# Auditor B report — WF-3 read-only adversarial audit, 2026-08-25

Dispatched after Auditor A with a disjoint scope (routing/dashboard/demo/integration/handoff).
Zero repository modifications; HEAD and porcelain byte-identical before/after; all probes
CPU-only; every child process terminated and verified gone.

## Overall

Items B1–B12 audited; one MAJOR defect found (fail-closed direction, but a false displayed
verdict), four MINOR, five NOTE. All 12 documented SHA-256 values reproduced exactly; the G1
identities, focused-suite counts, rehearsal transcript, and fallback selection reproduce
verbatim.

## Findings

| id | severity | location | finding | fix direction |
|---|---|---|---|---|
| B6-1 | MAJOR | src/jaladhar/web/app.py:613,709-711 | Dashboard scientific-gate binding structurally dead: store keeps only the JSON twin snapshot while every realized gate report binds the CSV twin SHA (gates_v5 input_sha256.depth_product = d04539… = CSV bytes). Realized v5 dashboard renders scientific_gate invalid ("not bound to a selected product twin") for the legitimately bound report and can never display loaded_not_accepted | Accept the gate hash against either twin (twins already enforced row-identical at load) |
| B8-1 | MINOR | src/jaladhar/web/app.py:765,791 | GET /api/segments with offset beyond row count raises IndexError -> dropped connection instead of typed JSON error (reproduced offset=200000&limit=10) | Guard empty slice; return 4xx JSON |
| B7-1 | MINOR | scripts/demo/_demo_common.py:698-699 | Walker skips whole geometry-keyed subtrees while docstring claims every numeric leaf checked (fabricated depth_cm under geometry ACCEPTED) — instrument gap, not realized leak | Narrow docstring claim and record exemption deliberately |
| B11-1 | MINOR | launch_demo.py options; DEMO-RUNBOOK step 4 | Launcher has no --server-max-snap-distance-m, so even with blockers closed the default-launched API would refuse all routes; runbook step 4 implies presenting a route through it | Add owner-supplied option or document the override requirement |
| B10-1 | MINOR | mirror commit 25dafa6 tree | Production snapshot omits the entire tests/ tree; demo launcher defaults drifted post-snapshot (20s→120s, matching decision log); producer/runtime files semantically identical (style-only ruff joins) | Record divergence explicitly in FINAL-VERIFY §3 |
| B9-1 | NOTE | _demo_common.py:78; DEMO-RUNBOOK.md:30 | Boundary regex passes 'xgpu', '/dev/nvidia0', 'cuda12', 'gpuserver'; runbook sentence overstates the filter; forced env vars remain the enforcing layer | Align runbook wording |
| B1-1 | NOTE | routing/api.py:386-397 vs 257-261 | Non-positive server cap yields 400 snap_limit_exceeds_server_policy while health says unavailable_owner_gate — inconsistent codes, fail-closed either way | Normalize to 503 snap_policy_unavailable |
| B7-2 | NOTE | _demo_common.py:700,739 | Numeric strings and boolean leaves escape the provenance walk ('123456', true ACCEPTED) | Document the JSON-number definition of numeric leaf |
| B5-1 | NOTE | src/jaladhar/web/__init__.py:1 | Stale 'flood nowcast' docstring | Reword to realized flood product |
| B11-2 | NOTE | WF3-AUDIT-RECONCILIATION §A1 vs depth_product_contract.py:170-188 | Doc wording imprecise: validator prefers nested 'event' dict and falls back to top-level 'event_window' dict; realized manifests use event_window.{start,end} | Correct field-name wording |

## Verified-clean highlights

- B1: 21 adversarial payloads cannot bypass the snap ceiling (strings, bools, NaN, Infinity,
  1e308, boundary equality correct); passthrough behaviorally proven.
- B2: routing_lead_seconds null with honest status; product_age_seconds exactly issue-derived;
  zero-lead historical route never described as nowcast analysis.
- B3: 16-case policy battery all refused correctly; no hardcoded cm threshold anywhere in
  routing sources.
- B4: no-safe-route exact triple verified; cross-component distinction correct; operator
  precedence confirmed safe (AST + empirical).
- B5: labels propagate through health/route/state/segments; badge composes the exact honest
  wording; zero 'nowcast' in static assets; formatLead(0)='Rainfall lead 0'.
- B6: five readiness axes independent; launch gates reject HTTP-success-not-ready (realized
  DEMO_BLOCKED observed).
- B7: inheritance genuinely blocked (deep-chain and list-provenance attacks rejected);
  realized counts 16/19 reproduced.
- B12: R1/R3/R4 HONORED; R2 PARTIAL (instrument blind spots above); R5 MOSTLY (B6-1 display);
  R6 BORDERLINE-HONORED (result-shaped instructions are logged owner adjudications, not
  anticipated answers).

## Could-not-verify

Browser-pixel rendering (no browser; rehearsal declares this scope); past-session GPU absence
(declaration, manifests record cpu); tests/-tree bytes at snapshot 25dafa6 (omitted from tree);
full-suite residual classification not independently re-derived; G1 raw-input recomputation left
to Auditor A item H.

