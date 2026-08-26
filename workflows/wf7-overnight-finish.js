export const meta = {
  name: 'wf7-overnight-finish',
  description: 'Overnight, unattended finish for the internal-round demo: fix routing (still broken), build the REAL 2022 observed-vs-predicted comparison from Sentinel-1, wire a REAL live-refresh simulation button using the now-measured tile throughput, polish, and assemble an honest narrative. No fabrication anywhere. Runs to completion without owner check-ins.',
  whenToUse: 'Run tonight. Owner is asleep and wants a finished, working, honest demo by morning. Do not wait for adjudication mid-run - decide, document, keep going.',
  phases: [
    { title: 'Triage', detail: 'confirm exact current state before building anything' },
    { title: 'Routing', detail: 'fix the shattered road graph - the one broken step in the five-step flow' },
    { title: 'ObservedVsPredicted', detail: 'REAL Sentinel-1 comparison, not an invented one' },
    { title: 'LiveRefresh', detail: 'a REAL run-fresh-simulation button, fast enough because of measured tile throughput' },
    { title: 'Polish', detail: 'visual pass on the two views the presenter will actually show' },
    { title: 'Narrative', detail: 'presentation doc built around the real, strong results' },
    { title: 'Rehearse', detail: 'full regression, a morning summary the owner reads FIRST' },
  ],
}

const REPO = '/home/darshil/Desktop/sih/clginternal'
const PY = REPO + '/.venv/bin/python'

// ─────────────────────────────────────────────────────────────── shared rules

const RULES = `
THIS RUNS OVERNIGHT, UNATTENDED. The owner is asleep. DO NOT stop and wait for
adjudication - decide with the best available reasoning, document the decision
and why, and keep moving. Everything gets reviewed in the morning; nothing here
is unrecoverable (explicit-path git staging, nothing committed without review).

BINDING RULES (${REPO}/CLAUDE.md is authoritative):
- NO FABRICATED DATA, EVER. This is the one rule with zero flexibility tonight.
  Every number shown in the "2022 actual" / observed panel MUST come from the
  real Sentinel-1 acquisition at data/raw/sentinel1/flood_20220905/
  (2022-09-05T00:40:28Z), pushed through the real segment-status pipeline. If
  that comparison produces a WEAK or UGLY number, report it exactly as measured,
  with the one-line instrument caveat (Goal A validated this instrument on
  lakes, not urban streets). A weak honest number is a correct outcome tonight.
  A tuned or invented one is not, under any framing, no matter how low the
  stakes of tomorrow's audience.
- NO PLACEHOLDER PHYSICS, and specifically: the "live refresh" feature must
  trigger a REAL simulation run and REAL new output. It must never generate
  randomized "plausible-looking" variation between clicks. If a real rerun is
  not feasible in time, the button is honestly disabled with a stated reason -
  never faked.
- Rule 3: no hardcoded metrics in the frontend. Every displayed number resolves
  to a manifest path.
- Rule 6: manifest at run start, updated in place on completion.
- V1: realized state, never declared state.
- Style: python 3.11 + type hints; vanilla ES modules. Interpreter ${PY}.
- Git: explicit staging by path, NEVER 'git add -A'. No self-attribution footer.
  Do NOT commit anything without listing exactly what you staged in your report -
  the owner reviews before this goes anywhere further.

OWNERSHIP: other sessions may still hold residue in src/jaladhar/coupling/ and
src/jaladhar/drainage/ - DO NOT TOUCH EITHER. The coupling decision is already
made: the demo is UNCOUPLED. Do not attempt to wire a coupled product into
anything tonight. Do not touch configs/contracts/.

DESIGN AUTHORITY: docs/FRONTEND-DESIGN-GUIDE.md is still the visual spec.
docs/DEMO-RUNBOOK.md and docs/FRONTEND-VISUAL-QA.md are the current source of
truth for what has already shipped - read them before assuming something is
missing.

ONE BOUNDED GPU AUTHORIZATION, GRANTED NOW, SCOPED EXACTLY: you may run AT MOST
ONE real tile-scale verification simulation tonight (subdomain tile, ~1 min
wall clock, ~500 MB VRAM per the measured wf2_tile_throughput numbers) to prove
the live-refresh button genuinely triggers real computation end to end. Use
scripts/run_windowed_forecast_3h.py's existing tile parameters - do not write a
new runner. No other GPU use tonight, no full-domain runs, no repeated/looping
verification. Manifest it like any other run.
`

// ────────────────────────────────────────────────────────────── Phase Triage
phase('Triage')
log('WF-7: confirm real state before building. Nothing here is assumed.')

const TRIAGE_SCHEMA = {
  type: 'object',
  required: ['area', 'realized_state', 'work_remaining'],
  properties: {
    area: { type: 'string' },
    realized_state: { type: 'object', additionalProperties: true },
    work_remaining: { type: 'array', items: { type: 'string' } },
  },
}

const triage = await parallel([
  () =>
    agent(
      `${RULES}

TRIAGE: routing. Confirmed 2026-08-26 that runs/wf6_routing_feasibility/feasibility.json
still shows component_count: 107014 - the endpoint-snapping fix was specified but
never landed. Confirm this is still true, find whatever partial work exists (a
snapping script, a routing-graph builder), and report exactly what is missing to
finish it.`,
      { label: 'triage:routing', phase: 'Triage', schema: TRIAGE_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}

TRIAGE: observed-vs-predicted. Confirmed 2026-08-26 that no SAR-observed segment
product exists anywhere under runs/ (searched, nothing found). Confirm this, then
inventory what exists to build it: the Sentinel-1 raster
(data/raw/sentinel1/flood_20220905/), the SAR water classifier
(src/jaladhar/validation/sar_water_classifier.py), Goal A's validated thresholds,
and the segment-status producer used for every other product this week. Report
exactly what is missing.`,
      { label: 'triage:observed-panel', phase: 'Triage', schema: TRIAGE_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}

TRIAGE: live-refresh feasibility. scripts/run_windowed_forecast_3h.py and
scripts/wf2_tile_throughput.py exist and were used for the measured G4 numbers
(0.94 min / 0.385 min at 1/16 tile). Confirm whether either can be invoked as a
short-lived subprocess from a web request (async job + poll), what forcing
source it needs (real IMD nowcast if raining, else honestly report no
significant rain), and what the smallest wiring is to expose a "run fresh
simulation" action from src/jaladhar/web/. Report exactly what is missing.`,
      { label: 'triage:live-refresh', phase: 'Triage', schema: TRIAGE_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}

TRIAGE: regression baseline. Read docs/DEMO-RUNBOOK.md and
docs/FRONTEND-VISUAL-QA.md to establish exactly what is currently claimed
working. Start the dashboard, exercise the five-step flow end to end (see the
city now, advance the forecast, read the watchlist, inspect a street, route
around a flood) and report the CURRENT REAL STATE of each step, not what a prior
report claimed. This is the baseline everything else builds on and gets
re-checked against in the Rehearse phase.`,
      { label: 'triage:regression-baseline', phase: 'Triage', schema: TRIAGE_SCHEMA }
    ),
])

const triageOk = triage.filter(Boolean)
const TRIAGE = `\nTRIAGE FINDINGS (realized state, 2026-08-26 night):\n${JSON.stringify(triageOk, null, 1).slice(0, 40000)}\n`
log(`Triage complete across ${triageOk.length} areas.`)

// ────────────────────────────────────────────────────────────── Phase Routing
phase('Routing')
log('The one broken step in the five-step demo flow. Highest priority build tonight.')

const BUILD_SCHEMA = {
  type: 'object',
  required: ['unit', 'files_written', 'executed', 'realized_state', 'deviations'],
  properties: {
    unit: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    executed: { type: 'boolean' },
    execution_evidence: { type: 'string' },
    realized_state: { type: 'object', additionalProperties: true },
    deviations: { type: 'array', items: { type: 'string' } },
    blockers: { type: 'array', items: { type: 'string' } },
  },
}

const routing = await agent(
  `${RULES}${TRIAGE}

FIX ROAD-GRAPH CONNECTIVITY. 176,171 segments currently form 107,014 components
(1.6 segments per component on average) because OSM ways do not share exact
endpoints and there is no snapping - so routing fails for most pairs REGARDLESS
of flooding, dry or wet.

Apply the same fix WF-1 already proved for the drain network: snap endpoints
within a tight tolerance (start at 1-2 m - these are genuinely adjacent ways
that should share a node) and add intersection snapping. Rebuild the routing
graph from the snapped result.

Report, measured: component count before/after, largest-component share of total
length, and the dry-baseline random-pair success rate before/after. Then re-run
the identical Beat-6 test used previously (30 sampled flooded streets, three
cross-city pairs) against BOTH the event-maximum product and the forecast
product, and report success rates for both.

If a route genuinely cannot be found even after fixing connectivity (e.g. the
flooded area alone severs a corridor), that is a real finding - report it
honestly, do not force a route through impassable water.

You own src/jaladhar/routing/. Do not touch anything else.`,
  { label: 'build:routing-fix', phase: 'Routing', schema: BUILD_SCHEMA }
)

const routingVerify = await agent(
  `${RULES}

VERIFY the routing fix independently. You did not build it.

REPORTED:
${JSON.stringify(routing, null, 1).slice(0, 20000)}

Run it yourself. Reproduce the before/after component counts. Actually call the
routing API for several street pairs and confirm real routes come back with
real avoided-segment lists. This is the step that makes or breaks whether Beat 6
of the demo works live - be adversarial.`,
  { label: 'verify:routing-fix', phase: 'Routing' }
)

// ─────────────────────────────────────────────── Phase ObservedVsPredicted
phase('ObservedVsPredicted')
log('The REAL "2022 actual" - built from Sentinel-1, never invented.')

const observed = await agent(
  `${RULES}${TRIAGE}

BUILD THE OBSERVED PANEL. This IS the honest version of "2022 demo vs 2022
actual" - it does not need to be invented, the real data is already on disk.

1. Run the Sentinel-1 acquisition (data/raw/sentinel1/flood_20220905/,
   instant 2022-09-05T00:40:28Z) through the ALREADY-VALIDATED SAR water
   classifier at Goal A's thresholds - do not re-tune anything. Produce a
   binary water mask.
2. Push that mask through the IDENTICAL segment-status pipeline every other
   product this week used (same road segments, same PRIMARY_RULE, same output
   shape) so "observed flooded" and "predicted flooded" are directly comparable
   per street. Emit it as a real product with a rule-6 manifest.
3. Select the model frame temporally nearest the SAR instant from the frame
   series (runs/wf3_replay2_uncoupled_baseline_frames_v1/ or wf3's later
   uncoupled products - use whichever is the current canonical uncoupled full
   replay). Report the exact time offset between the chosen frame and the SAR
   instant - do not silently treat them as simultaneous if they are not.
4. Compute the comparison, however it comes out: per-street agreement/
   disagreement, and an overall figure (e.g. recall of observed-flooded streets,
   with a null-model comparison if time allows - reuse the G1 null methodology,
   same seed convention). STATE THE NUMBER EXACTLY AS MEASURED. Do not adjust,
   round favorably, or omit it if it is weak.
5. Carry the instrument caveat as a permanent, visible label wherever this
   comparison is shown: "SAR extent instrument validated on lakes, not urban
   streets (Goal A)." This is not optional decoration - it is the scope
   qualifier that makes the number defensible if questioned.

This produces the real, honest artifact that satisfies what "2022 demo vs 2022
actual" should mean. You own the new product/comparison code under
src/jaladhar/validation/ and scripts/ - do not touch drainage/ or coupling/.`,
  { label: 'build:observed-panel', phase: 'ObservedVsPredicted', schema: BUILD_SCHEMA }
)

const sideBySide = await agent(
  `${RULES}${TRIAGE}

WIRE THE SIDE-BY-SIDE VIEW. Two panels, identically rendered (same depth-ramp/
flood-status styling as the main map), one showing the model's predicted state
at the chosen frame, one showing the real Sentinel-1 observed state - both from
the product just built.

Display the comparison figure prominently, labelled honestly (not "accuracy" if
what is measured is recall or extent-agreement - use the precise term), with the
instrument caveat visible in the same view, not buried. Include the time-offset
disclosure from the previous step.

Clicking either panel should return to the full single-map interface with all
existing inspection tools (search, filter, click-to-inspect) still available -
this satisfies "then we can open up 2022 and have the entire suite of searching
tools."

Depends on the ObservedVsPredicted build above - if that has not landed a
product yet by the time you start, poll/wait briefly, then proceed with what
exists and note the gap rather than blocking indefinitely.`,
  { label: 'build:side-by-side', phase: 'ObservedVsPredicted', schema: BUILD_SCHEMA }
)

// ────────────────────────────────────────────────────────── Phase LiveRefresh
phase('LiveRefresh')
log('A REAL run-fresh-simulation button. Fast enough to be genuinely live because throughput is now measured.')

const liveRefresh = await agent(
  `${RULES}${TRIAGE}

BUILD A REAL LIVE-REFRESH BUTTON. This replaces any notion of faked "believable"
variation between updates - it triggers an ACTUAL new tile-scale simulation and
serves its ACTUAL output. It works now specifically because throughput is
measured: 1/16-tile runs complete in under a minute (wf2_tile_throughput.py,
G4 measurement).

1. Expose an action from the LIVE-mode UI: "Run fresh simulation now."
2. On click, kick off scripts/run_windowed_forecast_3h.py (or the equivalent
   tile-scoped path) as a short-lived async job: TILE-SCOPE ONLY, forced by the
   endpoint - never allow a full-domain trigger from this button. Forcing source
   is the current IMD nowcast if there is meaningful rain in the horizon; if not,
   the endpoint reports that plainly rather than running a no-op simulation.
3. UI polls and shows real progress, then swaps to the new product on
   completion, with the run's real issue time and manifest path visible - same
   provenance discipline as every other product.
4. Rate-limit it sensibly (e.g. one in-flight job at a time, a short cooldown) so
   a repeated click during a live demo cannot spawn overlapping GPU jobs.
5. VERIFY IT FOR REAL, ONCE: using the single GPU authorization granted in the
   rules above, actually click through the flow (or script an equivalent
   request) and confirm a genuine new tile run completes and the UI reflects it.
   Report the real wall clock. Do not perform more than this one verification
   run.

If today's IMD nowcast shows no rain (likely), this is fine and expected - it
is still a legitimate live demonstration: press the button, watch it actually
compute, get back an honest "no significant flooding forecast" state with a
fresh issue time. That IS the live product working.

You own src/jaladhar/web/ additions for this feature. Coordinate namespace with
the existing app.py rather than duplicating a server.`,
  { label: 'build:live-refresh', phase: 'LiveRefresh', schema: BUILD_SCHEMA }
)

// ─────────────────────────────────────────────────────────────── Phase Polish
phase('Polish')

const polish = await parallel([
  () =>
    agent(
      `${RULES}

VISUAL POLISH PASS on the two views that will actually be shown tomorrow: the
side-by-side observed-vs-predicted comparison, and the live-mode chrome
including the new refresh button. Follow docs/FRONTEND-DESIGN-GUIDE.md exactly -
palette, type scale, glow, motion tokens are already specified, do not improvise
new ones.

The owner has said the UI/UX is what matters most for this round. Specifically:
the side-by-side view should look like a deliberate, designed comparison
moment (guide Moment 4 energy - the credibility beat), not two maps awkwardly
stacked. The live-refresh button needs a satisfying, honest loading state while
the real job runs - progress that reflects REAL job state, never a fake
progress bar detached from the actual computation.`,
      { label: 'polish:comparison-view', phase: 'Polish', schema: BUILD_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}

REGRESSION SWEEP. Confirm nothing shipped earlier this week broke: the watchlist,
the drain layer with correct 1,301/286 split, the frame-series play animation,
search, severity filtering, ward context, the provenance line on every value.
Load the dashboard fresh and walk every mode. Report PASS/FAIL per item with
evidence, not assumption.`,
      { label: 'polish:regression', phase: 'Polish', schema: BUILD_SCHEMA }
    ),
])

// ────────────────────────────────────────────────────────────── Phase Narrative
phase('Narrative')

const narrative = await agent(
  `${RULES}${TRIAGE}

WRITE docs/PRESENTATION-2026-08-27-INTERNAL.md for a 26-hour internal round
in front of judges with LOW domain knowledge. Assume they cannot pressure-test a
specific accuracy figure - which means the honest numbers we have are entirely
sufficient framed well, and there is no reason to reach for anything stronger.

Lead with what is genuinely strong and REAL:
- a working, live system with a real 0-3h forecast pipeline
- G4 measured PASS via tiling: sub-minute for a 3-hour forecast (state the exact
  figures from wf2_tile_throughput.py / the forecast runs), a 10x+ margin against
  the operational gate
- the observed-vs-predicted comparison built tonight, stated exactly as measured,
  with its one-line instrument caveat - frame it as "here is what we can already
  validate against real satellite data," not as an apology
- the two structural, MEASURED findings from earlier this week: the DEM encodes
  lake water surfaces rather than basins (16 cm interior std), and 96.87% of
  drainage surcharge originates on reaches with no outlet in BBMP's own public
  map, proven not to be an artifact of definition (five-tolerance sweep). Frame
  this as: "we built the coupled framework the problem statement specifies, ran
  it, and it told us precisely where the public data runs out" - a real,
  defensible research finding, not a weakness to hide.
- the road-network connectivity finding and fix from tonight, if it landed
- close with the plan: switch to a city with complete drainage and DEM data for
  the finished system, since the mechanism is proven and the bottleneck is
  specifically identified as input data, not model capability

Include a tight presenter script for the five-step flow: live (hold ~15s to
prove liveness) -> switch to 2022 demo -> press play on the forecast -> the
side-by-side comparison moment -> click into a street -> route around it.

Do not invent a headline accuracy percentage. Do not soften G1's FAIL. State
every number with its source. This document is what gets read at 3am by someone
deciding what to say - it must be something that survives being said out loud.`,
  { label: 'narrative:presentation', phase: 'Narrative' }
)

// ─────────────────────────────────────────────────────────────── Phase Rehearse
phase('Rehearse')

const rehearse = await agent(
  `${RULES}${TRIAGE}

FINAL REHEARSAL AND MORNING SUMMARY. Run the ENTIRE demo flow end to end exactly
as docs/PRESENTATION-2026-08-27-INTERNAL.md's presenter script describes it,
starting the dashboard fresh. Note every failure, every slow load, every rough
edge.

Then write docs/MORNING-SUMMARY-2026-08-27.md - this is the FIRST thing the
owner reads, sleep-deprived, with limited time before presenting. Structure:
  1. One paragraph: does the full demo flow work end to end right now, yes or no.
  2. What was built tonight, in one line each: routing fix, observed panel,
     live-refresh button, polish, narrative.
  3. Every number that will appear in the presentation, listed with its exact
     source path, so nothing has to be re-derived under pressure.
  4. Anything that did NOT get finished, stated plainly, with the safest
     fallback if it comes up (e.g. if live-refresh is flaky, the presenter skips
     it and narrates from the pre-built demo mode instead).
  5. The exact command to start everything.

Nothing is committed beyond what earlier sessions already committed. List the
staged-but-uncommitted paths from tonight's work explicitly so the owner can
review before pushing anything further.`,
  { label: 'rehearse:final', phase: 'Rehearse' }
)

log('WF-7 complete. docs/MORNING-SUMMARY-2026-08-27.md is the first thing to read.')

return {
  triage: triageOk,
  routing: { build: routing, verify: routingVerify },
  observed_panel: observed,
  side_by_side: sideBySide,
  live_refresh: liveRefresh,
  polish: polish.filter(Boolean),
  narrative,
  rehearsal: rehearse,
  next: 'Read docs/MORNING-SUMMARY-2026-08-27.md first. Everything else is detail.',
}
