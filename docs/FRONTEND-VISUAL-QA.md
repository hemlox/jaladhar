# JALADHAR frontend — human visual QA

**Walk this checklist in front of the RUNNING dashboard.** Agents verified mechanics, structure and
rule-3 conformance (coverage mapped in §Automated coverage); only a person can verify that it looks
expensive. Every item is a yes/no answerable in seconds. Dense, honest, no marketing — a "no" here
is a defect report, not an opinion.

Date rewritten: 2026-08-26 · Product: `runs/wf3_replay2_uncoupled_baseline_frames_v2` (**current
realized value** — 97-frame Sep 2022 replay at 30-minute cadence, uncoupled baseline,
storage-water exclusion ADOPTED per owner adjudication 2026-08-26; frame 0 floods zero segments by
design — it is the replay start, Sep 4 00:00Z; figures move with the loaded run). Cold discovery
prefers a valid frame series over flat event-maximum products (`selection_order` in `/api/state`).
Visual spec is `docs/FRONTEND-DESIGN-GUIDE.md` — referenced, not restated.

Start with `.venv/bin/python -m jaladhar.web.app serve` or `./scripts/demo/run_demo.sh`
(see `docs/DEMO-RUNBOOK.md` for what appears at cold start). The launcher also brings up the
routing API (`127.0.0.1:8502`) — required for the §Route around items; dashboard-only serving
leaves that section's selector in its designed-absence state.

---

## Header & mode badge

1. Badge reads `REPLAY · SEP 2022 HINDCAST` and the subline **ends** with `not forecast leads` —
   derived from the payload's own timestamps, unconditional clause last.
2. Hover the badge: the tooltip says these are valid times of a hindcast replay, not forecast leads,
   and names A2/A4 as absent until a genuine IMD-driven forecast run exists.
3. Status dot is grey (`replay` tone), never green, never labelled LIVE; its tooltip reads
   "realized product loaded — not a live feed".
4. Gate note (`G1 FAIL`) appears only when the server was started with a bound gate report (the
   launcher passes one — tonight `runs/wf3_replay2_gates_v7_excluded/g1_score.json`, bound into
   `/health` as path+sha256); absent report → empty note, not a guessed verdict.
5. Badge text stays legible while the KPI/hero numbers climb beside it — no layout shift when the
   count goes 4→5 digits.
6. Kill the backend: within one poll (~5 s) the badge dims (`data-stale`) keeping its LAST GOOD
   text — a dead link is visible, not silent.

## Watchlist panel

7. Row anatomy left→right: rank number (tabular), street name, ward chip beneath; right-aligned
   depth band over lead cell; 7 px status dot far right. Nothing else competes.
8. Depth bands read in integer cm up to 100, metres at two decimals above (`0.00–8.01 m` was the
   top row's band in the integration smoke, confirmed on tonight's frames_v2 held frame). Raw cm
   stay on the row tooltip ("worst cell N cm · M segments").
9. Honesty line under the sort bar names the lead kind verbatim: tonight
   `hindcast offsets — not forecast leads`. It must never read as a forecast promise.
10. Sort toggle Severity/Soonest: Soonest reorders by lead; rows without a numeric lead sink to the
    bottom rather than inventing a position; the active side is visually `on`.
11. List renders the first 200 rows with a `show all N rows` expander — no 800-row DOM dump, no
    silent truncation.
12. Empty/error states are designed, not blank: loading shows skeleton bars; failure shows
    `watchlist unavailable — <server message>` with a working retry; a frame with zero flooded
    streets shows "No streets forecast to flood in this frame". No fabricated rows anywhere.
13. Intersections section (bottom): summary chip = node count (value recorded under the earlier
    frames_v1 product line: 2,142 on the then-held frame — moves with the loaded series; read the
    served chip tonight rather than trusting the recorded figure), each row `street × street` with
    red `blocks N approaches`, depth band, lead. Its own endpoint failing degrades to a note — the
    street list survives.
14. Collapse chevron: panel shrinks to a 40 px rail, content hides, search box repositions to the
    freed edge; re-expand restores exactly.

## Location search

15. Type "bellandur": ward and street kinds return with kind pills; Enter on the top hit flies to
    the ward centroid (or first realized segment for streets) — verified mechanically; you judge
    that the dropdown reads calm over the map.
16. A query with no match shows "no matching ward or street"; a dead index endpoint shows
    `search index unavailable — <reason>`. Never a stale or invented suggestion list.

## Inspector (right rail)

17. Click a flooded segment: depth row formats each bound independently — ≤100 cm stays
    `N cm`, >100 cm becomes `X.YY m`; raw cm pair remains on the dd tooltip.
18. Context line under the name: `<name>, near <landmark> (Ward N <ward name>)`; components drop
    out individually when missing; the line disappears entirely rather than rendering empties.
19. Status badge replaces the bare pill: `Flooded — impassable risk for passenger vehicles` with a
    tooltip noting heavy-vehicle/emergency thresholds differ; `unknown` maps to "No modeled cells"
    (band 0/0), never styled as danger.
20. **Provenance-line visibility rule:** the per-value provenance line sits at the inspector bottom
    and is NEVER hidden — it lives OUTSIDE the Diagnostics accordion and outside any collapsed
    section. Check it resolves to a real file on disk.
21. Drain network panel: status line + fingerprint render inside a collapsed-by-default
    `Diagnostics` accordion together with the product-schema line; expanding it shows the snapshot
    manifest status and graph fingerprint (or `<fingerprint absent at read>` — honesty, not a bug).
22. Hero stats: tabular digits hold perfectly still while climbing; deepest reading above 100 cm
    renders in metres with the unit span emptied, raw cm on hover.
23. PEAK panel states the measured peak with UTC+IST time and the manifest path beneath
    (**current realized value**: 36,268 at Sep 4 21:30Z).
24. The rail's first panel heading reads `STATUS` — never `NOW`. The lead span beside it carries
    only realized states: `WARMING FRAMES n/97`, then `LOADING FRAMES…` during boot, then valid
    times / the horizon label. Lead-mode words (`NOW`, `+3h`, `now`) are banned from this display
    path by owner adjudication — a replay has no "now".
25. Flat event-maximum products render the horizon label `48 H EVENT MAXIMUM` with event-window
    tick labels instead of any forecast-lead wording — an event maximum is a hindcast summary, not
    a lead. Tonight's cold open takes the series path; serve a flat product to see this state
    (e.g. `serve --product runs/wf3_replay2_uncoupled_baseline_v6/products`, whose manifest carries
    `temporal_aggregation: event_maximum`).

## Timeline

26. Hyetograph bars sit above the scrubber track, clamped inside the footer band (never floating
    over map or legend); zero-rain intervals draw nothing; unit label `mm/hr` bottom-right.
27. Playhead tracks the scrubber linearly including during playback (rAF poll catches programmatic
    writes); click/drag on the sparkline seeks through the same mapping — marker and seek agree.
28. STEP «/», speed 1x/2x/5x (sweep 3000 ms ÷ speed), LOOP toggle default off = hold-at-end.
    All four disable honestly when fewer than two frames exist.
29. **KPI arrow rule:** trend arrow renders ONLY from two real adjacent loaded frames — hidden at
    frame 0, mid-interpolation, or whenever the previous frame isn't loaded. Verify it appears at
    discrete stops and vanishes mid-scrub. If the frame contract is violated the share itself is
    withheld with a console warning.
30. Scrubbing stops playback (the scrub rule); ArrowLeft/Right anywhere in the footer steps one
    discrete frame; reduced-motion collapses the sweep to a jump-cut and disables looping.
31. Play disabled state (single-frame products) carries the honest tooltip naming why.

## Map

32. Controls cluster top-right: zoom ± (factor 1.5 about centre), ⤢ reset restoring the boot fit
    EXACTLY (asserted bit-exact in smoke), static north indicator. Control clicks never pan/drag
    the map underneath.
33. Pan hard toward any edge: ≥15 % of the data bbox keeps intersecting both axes — the city can
    never be dragged off-screen (measured floor 88.1 % across 24 hard pans).
34. Wheel zooms about the cursor; the point under the cursor stays put. LOD swaps coarse/full at
    ~4 m/px without a visible pop.
35. Hierarchical stroke widths: arterials prominent (~2.2 css px) at every zoom; local mesh thinner
    and dimmer, hidden beyond ~2.5 m/px so far view is arterial-only whisper.
36. Ward layer (toggle in the layers group): subtle dark fill + hairline beneath lakes/roads, ward
    labels from mid-zoom outward only, none at street zoom. Failure mode is honest: toggle disables
    itself with the reason on its title.
37. Drains: observed reaches solid, synthesised connectors dashed and dimmer, legend says so;
    snapshot counts in the rail panel. **No pulsing anywhere at rest** — pulses require measured
    surcharge events, and none exists.
38. Glow preserved (guide C1): deep segments bloom brighter than shallow ones via additive
    blending; the fix for band saturation was MOVING boundaries onto data, never dimming glow.
39. **Ramp bands are data-derived — check against REALIZED bounds, not hardcoded values.** Run
    `window.__JALADHAR_RAMP` in the console: `bounds_cm[0]` must equal `/api/basemap/meta`
    `depth_rule` (frozen contract threshold); upper bounds are p50/p90 of the loaded product's
    flooded distribution; legend ranges and the d4 footnote must match the telemetry. Current
    realized value: `[15, 44, 129]` cm from 1,498,647 flooded samples, source
    `derived:flooded_p50_p90` — recheck against live telemetry whenever serving, since a different
    product ⇒ different bounds; a hardcoded expectation here is a QA defect, not a UI one.
40. Colours separable where it matters: bands run cyan→yellow→orange→red AND thicken monotonically
    — width redundancy means greyscale or squint must still order them by thickness alone.

### Colour separability — recorded as DONE, spot-check only

Machine-verified against Machado et al. 2009 (severity 1.0) simulation, CIE76 ΔE in Lab: worst
adjacent pair (d2 yellow vs d3 orange) **ΔE 21.3 deuteranopia / 29.6 protanopia**, all other pairs
≥ 21.6, plus 0.8–1.0 px width redundancy between adjacent bands (`color-separability.json`).
Spot-check for the human: at street zoom over a cyan/yellow boundary, desaturate the display or
squint — confirm band ordering survives on thickness alone.

---

## Route around (M2) — refused by design tonight

Runs only under the launcher (`./scripts/demo/run_demo.sh`), which brings the routing API up beside
the dashboard. **Tonight every `/route` request is refused, and that is the pass condition**: the
scientific gate stands FAIL — G1 rescore **104/399 hits, lift 1.30064** under the frozen ≥0.60/≥3.0
thresholds (`runs/wf3_replay2_gates_v7_excluded/g1_score.json`, bound into `/health` as path+sha256)
— while the wading policy IS cited (passenger car **30 cm**, grade B; emergency/heavy **46 cm**,
grade C-industry; `bus_truck` BLOCKED) and the **25.0 m** snap cap serves (internal precedent,
RATIFICATION-PENDING). What you are QA-ing is the honesty of a refusal surface, not a route drawing.

41. Selector strip sits inside the watchlist panel under the sort bar (`route` label + three
    segment buttons **Passenger car / Emergency/heavy / Bus/truck**). All three boot disabled with
    tooltip `loading policy…`; a class enables ONLY from the realized `/policies` payload — never
    by default, never on timeout.
42. Bus/truck stays disabled and its tooltip is the SERVED `blocked_classes.bus_truck.reason`
    verbatim — "no published stationary-depth wading limit exists (ARR P10 lists commercial
    vehicles unassessed); moving-water figures are different physics". Kill the routing API and
    reload: the tooltip must degrade to the designed-absence wording (`routing policy unavailable
    at <base> — designed absence`). The negative-evidence sentence must never render from a local
    fallback when nothing was served.
43. Every watchlist row carries a ⚡ action: `aria-label` = `route around <street name>`
    (`route around this street` when the row is unnamed), title `route around this street`;
    clicking it must not also trigger the row's fly-to/select.
44. With no class selected, ⚡ answers
    `select a vehicle class above before requesting a route` — a designed message, not a console
    error and not a silent no-op.
45. With a class selected, ⚡ lands the refusal banner VERBATIM:
    `⚠ routing refused: scientific gate not accepted (G1 FAIL)` — mapped 1:1 from the API code
    `scientific_gate_not_accepted`, rendered in the error tone (`--surcharge` red). It must NEVER
    be recoloured, restyled, or reworded softer: an honest refusal looks like a refusal.
46. Other refusals keep their own words: API unreachable →
    `routing API unreachable at <base> — no route fabricated`; snap over server cap →
    `routing refused: requested snap limit exceeds server policy`; unmapped codes surface their own
    detail text. No code path may render a fabricated route.
47. Result-box honesty (ok-path, gated behind the scientific gate tonight): segments list in route
    order as `#id name · low–high cm · status`; avoided segments render as their own entries
    `avoided #id · low–high cm · reason` with ids and bands taken straight from the payload;
    street-name joins come only from currently served watchlist rows and unknown names render
    `unnamed`, never guessed. Judge this the first time an ok payload exists; until then its
    contract is exercised headlessly (see §Automated coverage).

---

## Automated coverage — what machines already proved vs what needs your eyes

Smoke scripts live under session scratch paths (not committed): `/tmp/opencode/wf6-build/*/` and
`/tmp/opencode/wf6-verify/`. They are evidence of what was checked, at the stated scope — viewport
1340×711, this product, held frame — per V7 name-the-scope discipline.

**Covered by CDP smokes (do not re-verify manually, spot-check only):**
- `wf6-build/int/smoke_int_final.mjs` → boot done; badge/subline text; context vintage; watchlist
  real rows + chip; intersections count; hyetograph rendered vs independently computed nonzero
  intervals; KPI vs wire-decoded frame; search Enter→select-ward event; row click fly-to + select;
  causal predicted-hit and honest-miss statements; legend d4 bound converged through the rail
  writer; retired sentinel guard absent from served rail.js; gesture overlap/ink; exact reset.
- `wf6-build/l1/gestures-l1.mjs` → ramp telemetry present/monotone/contract-anchored; legend
  matches realized bands; controls installed; pan overlap held ×4 directions; clamp mutation
  reddens.
- `wf6-build/l1/wards-seam.mjs` → ward bundle accepted (249 rings / 73,246 vertices / 243 wards);
  six seam mutations redden; fill/labels/toggle-off pixel checks.
- `wf6-build/l1/color_separability.py` → ΔE table above.
- `wf6-build/l4/smoke_l4.mjs` → transport controls, autoplay hold-at-end, disabled patterns, step,
  keyboard stepping, speed sweep timing, loop restore, KPI arrow visibility rules, hyetograph
  designed outcome.
- `wf6-build/l3/causal.mjs` → JS↔Python statement parity (one sentence, two runtimes).
- `wf6-l6/smoke.mjs` → inspector metre formatting on stubbed frames, badge expansion, legend
  reload round-trip, Diagnostics accordion collapsed + schema line, click-select resolution.
- `wf6-l8/smoke.mjs` → watchlist/search designed empty states live; mock-driven row rendering,
  sort reorder, event details, render cap, search ranking.
- `wf6-verify/` → served-file regression probes and server logs backing the runs above.

**Covered for ROUTING by committed repo artefacts (HTTP/policy level — not browser DOM):**
- `tests/test_m2_vehicle_policy.py` → cited-policy loader surface: verbatim `blocked_classes`
  pass-through and shape refusal; sha256 evidence-binding refusals (V5-red MUT-1/MUT-2 recorded in
  the module docstring); validate-CLI exit-0 against the curated pair, pinning 30 cm/provenance-B,
  46 cm/C-industry and the exact bus_truck reason string; launcher `ROUTING_STATE` derivation for
  all-green / G1-FAIL / missing-policy banner outcomes; opt-in CORS behaviour on a live ephemeral
  server incl. mismatched-origin refusal. Scope (V7): fixture repos + curated files + pure
  functions + HTTP handler against a stub service. It does NOT exercise browser DOM — its own scope
  statement says so.
- `scripts/demo/smoke_route_around.py` → real stack end-to-end, headless: boots dashboard +
  routing API (curated policy file, `--server-max-snap-distance-m 25.0`, bound gate report), picks
  a flooded pair from the LIVE dashboard watchlist, resolves origin/destination from served segment
  geometry, POSTs `/route` per configured class asserting EITHER ok-with-avoided_segments OR the
  exact honest refusal code expected under the realized gate state. Latest recorded run
  `runs/m2_smoke_20260826T072444Z`: verdict PASS; all three probes exercised the refusal branch
  `scientific_gate_not_accepted`; launcher banner captured (`SERVER_MAX_SNAP_DISTANCE_M=25.0`,
  `ROUTING_BLOCKERS=scientific_gate=not_accepted`). This proves what the SERVER answers — it does
  not prove what the BROWSER renders.
- **Browser-harness note:** no committed CDP smoke drives routing.js DOM — the M2 browser harness
  ran beside the work uncommitted and is PARTIAL by construction (the test module's scope statement
  records this). Items 41–47 are therefore eyes-on.

**Needs HUMAN JUDGEMENT — enumerate honestly, machines cannot score these:**
- Does the cold open read as "an operational system a city actually runs"? (Guide §1 — three
  seconds, no interaction.)
- Does the quiet dry-day state read composed? Unreachable with today's product (needs a measured
  zero-flood series) — judge it the first time a dry LIVE run exists; until then BLOCKED, not N/A.
  (Frame 0 of frames_v2 flooding zero segments is the replay START, not a dry-day product — do not
  score the dry-day feel off it.)
- Is PREDICTED visually distinct enough from MEASURED at projection distance — hollow amber vs red
  pulse readable from the back of the room?
- Does the routing refusal banner read as honest rather than broken — exact words, error tone, no
  softening? The demo's S5 beat depends on this reading correctly at two metres.
- Is the disabled bus/truck explanation comprehensible to a BBMP operator — a deliberate cited
  absence ("no published stationary-depth wading limit exists…"), not an unfinished feature?
- Do the disabled-until-cited selector buttons read as "waiting on policy", not "dead page",
  through the loading window?
- Route-result avoided-segments block: does id + band + reason read as defensible accounting
  rather than decoration? (First ok payload — currently gate-blocked.)
- Glow beauty at street zoom: bloom without soup; far view whispers, street view blooms.
- Panel overlap cosmetics at 1280×720: watchlist + search + legend + rail coexistence on a small
  projector window.
- Digit-jitter feel during the climb (tabular-nums is measurable; *perceived* steadiness isn't).
- Whether water GROWS rather than snaps during scrub — interpolation is verified; the feel is yours.
- Hover lift blink (~160 ms) and drain fade (~420 ms) reading as motion-that-means-something.
- Cursor correctness (crosshair over map, pointer over segments) in real hand-on-trackpad use.

## Known cosmetic items (recorded, not defects)

- **Legend overlaps the expanded watchlist bottom-left at some widths** — #legend floats over the
  map area's bottom-left corner; with the watchlist open both occupy the left band. Collapse the
  panel, or accept the overlap; fixing it is a layout change, not a bug fix.
- **fit() MIN_SCALE band on unusual window sizes** — `View.fit` clamps to MIN_SCALE 0.05 px/m; at
  the smoke viewport (1340×711) the boot fit saturates the clamp, so the city extends slightly past
  the viewport instead of fitting whole. The pan clamp guarantees recovery (≥15 % overlap) and ⤢
  re-runs the fit; wider windows fit naturally. Cosmetic unless it bothers *you*.
