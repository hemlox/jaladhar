export const meta = {
  name: 'wf6-operator-dashboard',
  description: 'Rebuild the dashboard as the operator-facing system SIH 26085 actually specifies: a named ranked street/intersection watchlist with lead times and cm depths, backed by the map - plus the product and render fixes the first audit wave confirms',
  whenToUse: 'After WF-5/WF-5c. Fresh context - do not load WF-5 history. CPU-only, fully parallel with WF-2.',
  phases: [
    { title: 'Audit', detail: 'first wave: establish what exists, what is missing, what is broken - measured, not assumed' },
    { title: 'Product', detail: 'permanent-water exclusion, classified-road filter, frame-series discovery' },
    { title: 'Watchlist', detail: 'the named ranked street/intersection list - the primary interface' },
    { title: 'Render', detail: 'pan clamp, partial-render fix, ramp recalibration, LOD correctness' },
    { title: 'Verify', detail: 'requirement-by-requirement trace against the issuing organisation spec' },
  ],
}

const REPO = '/home/darshil/Desktop/sih/clginternal'
const PY = REPO + '/.venv/bin/python'

// ─────────────────────────────────────────────────────────────── shared context

const SPEC = `
════════════════════════════════════════════════════════════════════════════════
WHAT THE ISSUING ORGANISATION ACTUALLY ASKS FOR — SIH 26085, MoES / NCMRWF
Every row below is a REQUIREMENT DERIVED FROM THEIR OWN WORDS (quoted). This is
the acceptance list. Build to it.
════════════════════════════════════════════════════════════════════════════════

THE SENTENCE THAT DEFINES THE PRODUCT:
  "pinpoint exactly which streets or intersections will flood"

That is a NAMED, RANKED, ACTIONABLE LIST. It is not a heatmap. A city-wide extent
map alone does not satisfy it and never will, no matter how good it looks. The
watchlist is the primary interface; the map is its context.

  # | their words                                    | what the UI must show
 ---|------------------------------------------------|---------------------------------
 A1 | "pinpoint exactly which streets OR             | named ranked list of streets AND
    |  INTERSECTIONS will flood"                     | intersections as distinct objects
 A2 | "predicting street-level inundation BEFORE it  | a lead time per row - "floods in
    |  happens" / "0-3 hour lead time"               | +90 min", counting forward
 A3 | "water depth estimations in CENTIMETERS"       | a cm figure per row, not a colour
 A4 | "0-3 hour FORWARD-LOOKING window"              | horizon explicit and scrubbable
 A5 | "where blockages or overcapacity will cause    | WHICH NODE surcharges, and which
    |  BACKFLOW onto the streets"                    | street it backs onto - the causal
    |                                                | link stated in words on screen
 A6 | "directed graph (nodes as manholes/inlets,     | graph rendered with node/edge
    |  edges as pipes/canals)"                       | semantics, capacity vs load
 A7 | "flood-safe alternative routes for EMERGENCY   | route-around action per row, and
    |  SERVICES, PUBLIC TRANSIT, and COMMUTERS"      | the three user classes visible
 A8 | "municipal bodies lack real-time systems"      | OPERATOR-facing, control-room
    |                                                | shaped - not researcher-facing
 A9 | "fuse rainfall nowcasts + high-resolution DEM  | all THREE inputs visibly present
    |  + graph model of the drainage network"        | and visibly coupled
 A10| "INSTANTLY routes that volume"                 | latency/freshness shown
 A11| "traffic gridlocks, economic disruption"       | impact framing - an intersection
    |                                                | that floods blocks N approaches

THE DEMO FLOW THEY WOULD EXPECT, IN ORDER:
  1. See the city as it is now.
  2. Advance the forecast across the 0-3 h window and watch it develop.
  3. Read WHICH streets and intersections will flood - named, ranked, with cm and
     lead time.
  4. Understand WHY - the drain node that surcharges onto that street.
  5. Get a flood-safe route around it.

If a viewer cannot do those five things in sequence, the build has not met the
brief regardless of how the map looks.
`

const BUGS = `
════════════════════════════════════════════════════════════════════════════════
OWNER-OBSERVED DEFECTS, 2026-08-26. Some are measured; some are estimated from
screenshots and MUST BE CONFIRMED OR REFUTED BY THE AUDIT WAVE BEFORE ANY FIX.
Do not fix an unconfirmed bug - confirm it first, then fix.
════════════════════════════════════════════════════════════════════════════════

RENDER / INTERACTION
 B1  Pan is UNBOUNDED. static/js/view.js is 64 lines with no clamp, no bounds,
     no reset - grep confirms no "clamp"/"bounds"/"reset". Scrolling drifts the
     map off into empty space with no way to recover. MEASURED.
 B2  PARTIAL RENDER after panning: large regions of the canvas go blank and the
     data does not repaint. Owner observed twice. Suspect the static canvas is
     not redrawn on pan, or an LOD/culling path uses a stale viewport. ESTIMATED
     - reproduce it first.
 B3  No fit-to-extent on load and no reset control, so there is no recovery path
     once panned. MEASURED (follows from B1).

DATA CORRECTNESS
 B4  "1128 cm deepest reading" = 11.28 METRES of water on a road segment.
     MEASURED: band_high_cm max = 1128; 310 segments claim >300 cm. Root cause is
     almost certainly road segments crossing lakes/tanks inheriting the water
     body's depth - the calibration loss excludes permanent water, the segment
     product does not. THIS NUMBER ALONE ENDS THE DEMO'S CREDIBILITY.
 B5  Colour ramp SATURATED: 18,962 of 40,519 flooded segments (46.8%) exceed
     50 cm and render in the top "impassable" band, so red carries no
     information. Flooded p50 = 47 cm, p90 = 151 cm. MEASURED.
 B6  86% of segments are UNNAMED - 24,620 named of 176,171. The set includes
     every OSM way: driveways, footways, service lanes. The geometry file has a
     'highway' column to filter on. MEASURED. "Unnamed segment 18395" cannot
     satisfy requirement A1.
 B7  Event MAXIMUM is displayed under the label "NOW". It is the 48-hour maximum
     - every road that flooded at any point, lit simultaneously - which is why
     23.0% of the city is red. Semantically wrong for a nowcast. MEASURED.

FLOW / WIRING
 B8  Frame-series discovery FAILS: the launcher wants a flat
     products/segment_status.csv but the 97-frame run stores products under
     products/frames/<tag>/. So play stays disabled and there is no cold open.
     MEASURED - DEMO_BLOCKED message reproduced.
 B9  Demo launcher BLOCKED end to end: the routing API fail-closes with
     SERVER_MAX_SNAP_DISTANCE_M=UNAVAILABLE_OWNER_GATE (the un-adjudicated
     vehicle-wading policy), and the launcher requires both services, so
     ./scripts/demo/run_demo.sh --fallback never comes up. MEASURED.

COSMETIC
 B10 Bottom bar renders the label "now" twice.
 B11 Legend reads "impassable — any" where it should read a bound (> 50 cm).
 B12 Band width p50 = 36 cm, p90 = 130 cm. A "0-50 cm" band per street is too
     wide to act on. May be inherent to per-segment aggregation over long
     segments - the audit must determine whether shorter segments or a different
     statistic (e.g. the segment's flooded-cell median) fixes it. MEASURED.
`

const RULES = `
BINDING RULES (${REPO}/CLAUDE.md is authoritative - read it first):
- Rule 3: NO HARDCODED METRICS. Every number displayed is read from a product
  file at load. Grep audit: any numeric literal that is not a pixel, duration,
  opacity, colour or unit conversion is a violation.
- Rule 2: NO PLACEHOLDER PHYSICS and no synthetic flood field to look alive. If
  no product is loaded, the designed empty state shows. Surcharge renders ONLY
  where a coupled run measured it - none exists yet, so the causal link in A5
  must be BUILT AND WIRED but shows "none measured" until one does.
- V1: realized state, never declared state. Measure the bytes.
- V5: every invariant demonstrated RED under mutation, mutation recorded.
- V12: nothing counts until it has actually run. Start the server, load the page.
- Depth is a per-segment BAND IN CM. Never a per-square-metre point depth.
- Style: python 3.11 + type hints; vanilla ES modules. Interpreter ${PY}.
- Git: explicit staging by path, NEVER 'git add -A'. No self-attribution footer.

OWNERSHIP: you own src/jaladhar/web/, src/jaladhar/validation/segment_status*.py,
src/jaladhar/routing/, and scripts/demo/. WF-2 owns src/jaladhar/coupling/ and
WF-1 owns src/jaladhar/drainage/ - DO NOT TOUCH EITHER. Do not touch
configs/contracts/.

DESIGN AUTHORITY: docs/FRONTEND-DESIGN-GUIDE.md remains the visual spec - palette,
type scale, motion, glow. This workflow does not restyle; it changes WHAT is shown
and fixes what is broken. Canvas 2D stays; no map library.
`

// ────────────────────────────────────────────────────────────── Phase 1: Audit
phase('Audit')
log('WF-6 wave 1: establish what exists, what is missing, what is broken. Nothing is fixed in this phase.')

const AUDIT_SCHEMA = {
  type: 'object',
  required: ['area', 'confirmed', 'refuted', 'newly_found', 'gap_to_spec'],
  properties: {
    area: { type: 'string' },
    confirmed: {
      type: 'array',
      description: 'listed defects you reproduced, with the evidence',
      items: {
        type: 'object',
        required: ['id', 'evidence'],
        properties: { id: { type: 'string' }, evidence: { type: 'string' }, root_cause: { type: 'string' } },
      },
    },
    refuted: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'why'],
        properties: { id: { type: 'string' }, why: { type: 'string' } },
      },
    },
    newly_found: {
      type: 'array',
      items: {
        type: 'object',
        required: ['description', 'evidence', 'severity'],
        properties: {
          description: { type: 'string' },
          evidence: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'major', 'minor'] },
        },
      },
    },
    gap_to_spec: {
      type: 'array',
      description: 'per requirement A1-A11: MET / PARTIAL / ABSENT with what is missing',
      items: {
        type: 'object',
        required: ['requirement', 'status', 'missing'],
        properties: {
          requirement: { type: 'string' },
          status: { type: 'string', enum: ['MET', 'PARTIAL', 'ABSENT'] },
          missing: { type: 'string' },
        },
      },
    },
  },
}

const AUDIT_AREAS = [
  {
    key: 'render-interaction',
    ask: `Reproduce or refute B1, B2, B3. Start the dashboard, load it in a headless
browser, and actually pan and zoom. Where exactly does the transform lose track? Read
static/js/view.js and the canvas engine: is the static layer redrawn on pan, is there
LOD/culling using a stale viewport, is devicePixelRatio applied consistently after a
transform? B2 is ESTIMATED from screenshots - your job is to make it reproducible with
an exact gesture sequence, or refute it.`,
  },
  {
    key: 'data-correctness',
    ask: `Reproduce or refute B4, B5, B6, B7, B12 from the product bytes at
runs/wf3_replay2_uncoupled_baseline_v5/products/. For B4 specifically: take the
segments with band_high_cm > 300 and determine geometrically whether they intersect
lakes, tanks, or drain polygons. That is the root-cause test - do not assume it, prove
it. Report how many flooded segments survive a permanent-water exclusion, and what the
depth distribution becomes.`,
  },
  {
    key: 'flow-wiring',
    ask: `Reproduce B8 and B9. Then map the ACTUAL data path end to end: which
discovery function selects a product, what shape it requires, and exactly what the
97-frame run at runs/wf3_replay2_uncoupled_baseline_frames_v1/ provides instead
(manifest["frames"] entries with tag, valid_time_utc, twin paths, SHAs, flooded
counts). State the minimal change that makes multi-frame discovery work without
touching the frozen contract.`,
  },
  {
    key: 'spec-gap',
    ask: `Ignore the bug list. Go through requirements A1 to A11 in the spec block
above and score each MET / PARTIAL / ABSENT against the RUNNING dashboard, with
evidence. Be harsh on A1, A2, A3, A5 and A7 - those are the ones that define the
product. Then answer directly: can a viewer today perform the five-step demo flow in
order? At which step does it break?`,
  },
  {
    key: 'watchlist-feasibility',
    ask: `The primary interface change is a named ranked watchlist of streets AND
intersections. Establish feasibility from realized data, and do not design it - just
report what is available. Specifically: how many CLASSIFIED named road segments exist
by 'highway' class; can adjacent segments of the same street be merged into one named
street entity; can road-network intersections be derived as distinct objects with a
count of approaches blocked; what lead-time field exists per row in the product; and
what field would carry the responsible drain node once a coupled run exists. Report
counts, not opinions.`,
  },
]

const audit = await parallel(
  AUDIT_AREAS.map((a) => () =>
    agent(`${RULES}\n${SPEC}\n${BUGS}\n\nAUDIT AREA: ${a.key}\n\n${a.ask}\n\nYou are READ-ONLY in this phase. Fix nothing. Measure everything and report realized state.`, {
      label: `audit:${a.key}`,
      phase: 'Audit',
      schema: AUDIT_SCHEMA,
    })
  )
)

const auditOk = audit.filter(Boolean)
const auditDigest = JSON.stringify(auditOk, null, 1).slice(0, 70000)
log(`Audit wave complete: ${auditOk.reduce((n, a) => n + a.confirmed.length, 0)} confirmed, ${auditOk.reduce((n, a) => n + a.refuted.length, 0)} refuted, ${auditOk.reduce((n, a) => n + a.newly_found.length, 0)} newly found.`)

const plan = await agent(
  `${RULES}\n${SPEC}\n${BUGS}

Synthesise the audit into ONE ordered work plan. An implementer must need no further
decisions.

AUDIT FINDINGS:
${auditDigest}

Requirements:
- Order strictly by VALUE TO THE FIVE-STEP DEMO FLOW, not by how easy things are.
- For every listed defect, state CONFIRMED (with root cause) or REFUTED (with why).
  A refuted defect is not worked on.
- For every requirement A1-A11 scored PARTIAL or ABSENT, name the concrete change
  that moves it to MET.
- Name anything that CANNOT be met before a coupled run exists (A5's causal link is
  the obvious one) and specify how it is built and wired now so it lights up later
  without a rewrite.
- Flag any newly-found defect the owner must adjudicate rather than have an agent
  decide.`,
  { label: 'plan:synthesis', phase: 'Audit' }
)

const PLAN = `\nAUTHORITATIVE WORK PLAN FROM THE AUDIT WAVE:\n${String(plan).slice(0, 40000)}\n`

const BUILD_SCHEMA = {
  type: 'object',
  required: ['unit', 'files_written', 'executed', 'requirements_moved', 'deviations'],
  properties: {
    unit: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    executed: { type: 'boolean' },
    execution_evidence: { type: 'string' },
    requirements_moved: {
      type: 'array',
      description: 'which of A1-A11 this unit moved, and to what status',
      items: { type: 'string' },
    },
    deviations: { type: 'array', items: { type: 'string' } },
    blockers: { type: 'array', items: { type: 'string' } },
  },
}

// ──────────────────────────────────────────────────────────── Phase 2: Product
phase('Product')
log('Data correctness first - a watchlist built on contaminated depths is worse than no watchlist.')

const product = await parallel([
  () =>
    agent(
      `${RULES}${PLAN}

PERMANENT-WATER EXCLUSION (B4). Road segments crossing lakes, tanks and drain
polygons currently inherit the water body's depth, producing "1128 cm deepest
reading" - 11.28 m on a road. The calibration loss already excludes permanent water;
the segment product does not. Reuse the SAME permanent-water definition the loss uses,
do not invent a second one.

Exclude permanent-water cells from per-segment depth aggregation. Emit a new product
version beside the existing one - never overwrite v5. Report the new maximum, the new
flooded count, and how many segments changed band.

Rule 6 manifest, and a V5 red test: re-include water cells and show the maximum jump
back above 300 cm.`,
      { label: 'product:water-exclusion', phase: 'Product', schema: BUILD_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}${PLAN}

CLASSIFIED ROAD FILTER (B6, requirement A1). 86% of segments are unnamed because the
set includes every OSM way. data/interim/terrain/roads_centrelines.gpkg carries a
'highway' column and a 'name' column.

Produce a classified-network view: keep the classes a municipal operator acts on
(motorway, trunk, primary, secondary, tertiary, residential and their _link variants -
confirm the realized class values first rather than assuming this list). Merge adjacent
segments sharing a name into single named STREET entities, so the watchlist can say
"Marathahalli Bridge Road" rather than four segment ids.

Keep the full set available for the map; the classified set drives the watchlist.
Report: class counts before/after, named-street entity count, and mean segments merged
per street.`,
      { label: 'product:road-filter', phase: 'Product', schema: BUILD_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}${PLAN}

FRAME-SERIES DISCOVERY (B8, requirements A2/A4/A7-flow). Discovery wants a flat
products/segment_status.csv; the 97-frame run stores products under
products/frames/<tag>/ with manifest["frames"] carrying tag, valid_time_utc, twin
paths, SHAs and flooded counts.

Make discovery accept BOTH shapes. Single-frame behaviour must not regress - v5 still
resolves exactly as today. With the frame run selected, the dashboard gets 97 leads,
play enables, and the cold open is frame 0 (1,921 flooded, roughly 1% of the network)
rather than the 48-hour maximum.

Do not touch the frozen contract. If the contract genuinely cannot express a
multi-frame product, that is a DEVIATION - report it, do not reinterpret it.`,
      { label: 'product:frame-discovery', phase: 'Product', schema: BUILD_SCHEMA }
    ),
])

// ────────────────────────────────────────────────────────── Phase 3: Watchlist
phase('Watchlist')
log('The primary interface. "Pinpoint exactly which streets or intersections will flood" is a named ranked list, not a heatmap.')

const watchlist = await pipeline(
  [
    {
      key: 'street-watchlist',
      task: `Build the STREET WATCHLIST - the primary interface, satisfying A1, A2, A3, A8.

A ranked list of named streets predicted to flood. Each row carries: street name,
predicted depth in CM, LEAD TIME ("floods in +90 min" - forward-looking per A2), status,
and the responsible drain node when one exists. Ranked by severity, with a toggle to
rank by soonest.

It is the LEFT-HAND primary panel, not a footnote. The map becomes its context.
Clicking a row selects and flies the map to that street. This is control-room shaped -
an operator scans it top to bottom and acts.

Every value read from the product. The lead time comes from the product's own
forecast_lead_minutes; do not compute a fake one.`,
    },
    {
      key: 'intersections',
      task: `Build INTERSECTIONS as distinct objects (A1 says "streets OR INTERSECTIONS",
A11 is the gridlock framing).

Derive intersections from the road network as nodes where classified streets meet.
For each flooded intersection report: name (from the streets that meet there), depth in
cm, lead time, and HOW MANY APPROACHES ARE BLOCKED - that is the traffic-gridlock number
the problem statement opens with.

Intersections get their own section in the watchlist and their own marker on the map. A
flooded intersection blocking four approaches is a bigger operational event than a
flooded road segment, and the UI should say so.`,
    },
    {
      key: 'causal-link',
      task: `Build the CAUSAL LINK - requirement A5, "where blockages or overcapacity
will cause backflow onto the streets". This is the differentiator and the whole point
of the coupled framework.

For a selected street, show which drain NODE surcharges onto it, that node's capacity
versus load, and state it in words on screen: "floods because NODE-1042 exceeds
capacity at +75 min".

NO COUPLED RUN EXISTS YET, so no surcharge has been measured. Build and wire the entire
path, and render "none measured - coupled run pending" until one lands. Rule 2: never
animate or assert a surcharge that was not measured. Design it so a coupled product
drops in and it lights up with no rewrite.

The 70 edges already flagged demand_exceeds_capacity in
runs/drain_graph_build/manifest.json are a legitimate PREDICTED-surcharge set from the
cited design storm - you may show those as PREDICTED, clearly distinguished from
MEASURED, since they are derived from cited capacities rather than invented.`,
    },
    {
      key: 'routing',
      task: `Wire FLOOD-SAFE ROUTING into the watchlist - requirement A7, and unblock B9.

Each watchlist row gets a route-around action that calls the existing routing API and
shows the alternative with the avoided streets NAMED. The three user classes the
statement names - emergency services, public transit, commuters - must be selectable,
since impassability thresholds differ between them.

B9: the routing API fail-closes on SERVER_MAX_SNAP_DISTANCE_M=UNAVAILABLE_OWNER_GATE
and the launcher requires both services, so the whole demo path dies. Make the launcher
start the dashboard even when routing is degraded, and surface routing's state honestly
in the UI ("routing unavailable - awaiting vehicle-wading policy") rather than blocking
the entire demo. Do NOT invent a wading threshold to unblock it - that is an owner
adjudication.`,
    },
  ],
  (u) => agent(`${RULES}${PLAN}\n${SPEC}\n\nBUILD: ${u.key}\n\n${u.task}`, { label: `build:${u.key}`, phase: 'Watchlist', schema: BUILD_SCHEMA }),
  (r, u) =>
    agent(
      `${RULES}\n${SPEC}

VERIFY "${u.key}". You did not build it. Load the running dashboard and use it.

REPORTED:
${JSON.stringify(r, null, 1).slice(0, 18000)}

- Does it actually run? Exercise it in a headless browser.
- Which of A1-A11 does it genuinely move, and to what status? Be harsh - PARTIAL is
  not MET.
- Rule 3: grep for numeric literals that should have been read from a product.
- Rule 2: is anything shown that was not measured? The causal link is the highest-risk
  place for this.
- Would a municipal operator know what to DO after reading this panel? That is the A8
  test.`,
      { label: `verify:${u.key}`, phase: 'Verify' }
    )
)

// ───────────────────────────────────────────────────────────── Phase 4: Render
phase('Render')

const render = await parallel([
  () =>
    agent(
      `${RULES}${PLAN}

FIX PAN AND PARTIAL RENDER (B1, B2, B3) - confirmed root causes from the audit.

- Clamp panning so the data bounding box always overlaps the viewport; the map can
  never be dragged into empty space.
- Fit-to-extent on load, plus a visible RESET VIEW control.
- Fix the partial render: whatever the audit identified - static layer not redrawn on
  pan, stale viewport in an LOD or culling path, or DPR applied inconsistently after a
  transform.
- Verify by scripted gesture: pan hard in all four directions, zoom in and out, and
  assert non-zero rendered pixel counts in every quadrant after each gesture. This must
  be a real automated check, not an eyeball.`,
      { label: 'render:pan-fix', phase: 'Render', schema: BUILD_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}${PLAN}

RECALIBRATE THE DEPTH RAMP (B5, B11) against the realized post-exclusion distribution.

Currently 46.8% of flooded segments land in the top band, so red carries no
information. Set band boundaries from the ACTUAL distribution of the corrected product
so the ramp discriminates across the range an operator cares about, and keep them read
from the product schema rather than typed as constants where the schema declares them.

Fix the legend: "impassable — any" must read as a bound. Fix the duplicated "now"
label in the bottom bar (B10).

Keep the guide's glow behaviour - blur and stroke width scale with depth - just with
boundaries that separate real data.`,
      { label: 'render:ramp', phase: 'Render', schema: BUILD_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}${PLAN}

MAP AS CONTEXT, NOT AS THE PRODUCT. With the watchlist primary, the map's job changes:
it shows WHERE the listed streets are, not every flooded lane in the city.

- Selected watchlist row highlights on the map and the view flies to it.
- Classified network prominent; the unclassified mesh recedes so the map is legible.
- Intersection markers distinct from street lines.
- Keep the full-extent view available as a layer toggle for the "whole city" beat.

Also fix B7's semantics: an event-maximum product must be LABELLED as such - "48 h
event maximum", never "NOW". A frame-series product shows the frame's valid time.`,
      { label: 'render:map-context', phase: 'Render', schema: BUILD_SCHEMA }
    ),
])

// ───────────────────────────────────────────────────────────── Phase 5: Verify
phase('Verify')

const final = await parallel([
  () =>
    agent(
      `${RULES}\n${SPEC}

FINAL REQUIREMENT TRACE. Score A1 through A11 against the running dashboard, with
evidence for each: MET / PARTIAL / ABSENT, and for anything not MET, exactly what is
missing and whether it is blocked on the coupled run.

Then walk the five-step demo flow in order and report whether each step is possible:
see the city now; advance the forecast; read which streets and intersections will
flood; understand why; get a route around it.

Be harsh. PARTIAL is not MET. This trace goes to the owner as the honest statement of
what the build does and does not do.`,
      { label: 'verify:requirement-trace', phase: 'Verify' }
    ),
  () =>
    agent(
      `${RULES}

REGRESSION AND ROBUSTNESS. Confirm nothing that worked before is now broken:
- v5 single-frame discovery still resolves exactly as before;
- the 97-frame series plays end to end at 60 fps;
- the provenance line still resolves to a real manifest;
- the drain panel still shows the correct 1,301 observed / 286 synthesised split;
- designed empty, loading and error states still render.

Then break it deliberately: kill the backend mid-session, load with a missing product,
load with a truncated product, pan hard during playback. Does it degrade into a designed
state or throw into the console?`,
      { label: 'verify:regression', phase: 'Verify' }
    ),
  () =>
    agent(
      `${RULES}\n${SPEC}

Update docs/FRONTEND-VISUAL-QA.md and docs/DEMO-RUNBOOK.md for the new interface.

The runbook must carry the five-step flow with the exact click path, what to say at
each beat, and the honest answer to "what does this not do yet" - including that no
coupled run exists, so the causal link shows predicted rather than measured surcharge.

The visual QA checklist must cover the watchlist as the primary interface, and list
which items only a human can judge.`,
      { label: 'verify:docs', phase: 'Verify' }
    ),
])

log('WF-6 complete. The requirement trace is the honest statement of what was met - read it before the demo rehearsal.')

return {
  audit: auditOk,
  plan,
  product: product.filter(Boolean),
  watchlist: watchlist.flat().filter(Boolean),
  render: render.filter(Boolean),
  verification: final.filter(Boolean),
  next: 'Owner reads the requirement trace, then walks docs/FRONTEND-VISUAL-QA.md in front of the running dashboard.',
}
