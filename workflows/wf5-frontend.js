export const meta = {
  name: 'wf5-frontend',
  description: 'Build the presentation-grade dashboard to docs/FRONTEND-DESIGN-GUIDE.md: dark operational console, local OSM basemap, glowing depth-ramped water, animated 3h nowcast, surcharging drain nodes, full provenance',
  whenToUse: 'Runs CPU-only against the existing depth product. Fully parallel with WF-1 and WF-2 - touches only src/jaladhar/web/. This is the highest-visibility deliverable for the internal round.',
  phases: [
    { title: 'Foundation', detail: 'layout shell, palette, type, dark ground, local OSM basemap' },
    { title: 'Water', detail: 'depth ramp with additive glow, timeline scrub, play animation' },
    { title: 'Drains', detail: 'rajakaluve layer and surcharging node pulses - the differentiator' },
    { title: 'Polish', detail: 'interaction, DPR, reduced motion, designed empty state' },
    { title: 'Verify', detail: 'rule-3 audit, guide conformance, and a human visual checklist' },
  ],
}

const REPO = '/home/darshil/Desktop/sih/clginternal'
const PY = REPO + '/.venv/bin/python'

const GUIDE = `${REPO}/docs/FRONTEND-DESIGN-GUIDE.md`

const RULES = `
READ ${GUIDE} FIRST AND IN FULL. It is the specification, not a suggestion - its hex values, type
scale, timings and layout dimensions are meant to be typed in verbatim. Where it gives a number, use
that number.

BINDING RULES (${REPO}/CLAUDE.md is authoritative):
- Rule 3: NO HARDCODED METRICS. Every number displayed is read from a product file at load time. The
  audit is mechanical: grep the JS for numeric literals; anything that is not a pixel, a duration, an
  opacity, or a unit conversion is a violation. Depth thresholds come from the product's own schema,
  not from constants you typed.
- Rule 2: NO PLACEHOLDER PHYSICS. Never synthesise a flood field to make the map look alive. If no
  product is loaded, the DESIGNED empty state shows.
- Pinning the demo to a specific run directory IS allowed and expected - that is real model output
  loaded from a manifest. Label which run it is in the header. That is not fabrication.
- Depth is shown as a BAND IN CM per road segment. Never a per-square-metre point depth.
- V12: nothing counts until it has actually run. Start the server, load the page, exercise it.
- Style: python 3.11 + type hints for backend; vanilla ES modules for frontend. Interpreter ${PY}.
- Git: explicit staging by path, never 'git add -A'. No self-attribution footer.

OWNERSHIP: you own src/jaladhar/web/ ONLY. WF-1 owns src/jaladhar/drainage/ and WF-2 owns
src/jaladhar/coupling/ - both may be running concurrently. Do not touch either. Do not touch
configs/contracts/.

TECHNICAL DECISION ALREADY MADE - do not relitigate it: Canvas 2D, self-rendered, NO map library.
No MapLibre, no Leaflet, no deck.gl, no CDN, no tile server. The reasons are in guide section 3: zero
new failure modes three days from a demo, the basemap data is already local, canvas does bloom
natively via shadowBlur plus globalCompositeOperation='lighter', and we keep total pixel control.
If you believe a library is necessary, report it as a deviation for owner adjudication - do not add
one.

DATA ON HAND:
- Road network: src/jaladhar/routing/graph.py already loads OSM from data/raw/osm/. 176,171 segments,
  279,627 nodes. Reuse its loader; do not write a second one.
- Depth product: the frozen depth_product contract in configs/contracts/depth_product.json, produced
  by WF-3. Current realized product is the UNCOUPLED BASELINE from replay #2 -
  runs/wf3_replay2_uncoupled_baseline_v5/ and gates runs/wf3_replay2_gates_v6/.
- Lakes and water bodies: data/raw/osm/ and the terrain water layer.
- Drain network: WF-1's candidate at runs/drain_graph_build/drain_graph.gpkg. It is
  status=stopped_owner_adjudication with capacity blocked, so render GEOMETRY ONLY unless a later
  run supplies capacity and surcharge. Never render a surcharge that was not measured.
`

const BUILD_SCHEMA = {
  type: 'object',
  required: ['unit', 'files_written', 'executed', 'guide_conformance', 'deviations'],
  properties: {
    unit: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    executed: { type: 'boolean' },
    execution_evidence: { type: 'string', description: 'real command, real output - server started, page loaded' },
    guide_conformance: {
      type: 'array',
      description: 'per guide section: which specified values were used verbatim, which were not, and why',
      items: { type: 'string' },
    },
    deviations: { type: 'array', items: { type: 'string' } },
    blockers: { type: 'array', items: { type: 'string' } },
  },
}

// ---------------------------------------------------------- Phase: Foundation
phase('Foundation')
log('WF-5: guide section 9 build order. Moment 1 is 80% of the impression and needs no data - it lands first.')

const FOUNDATION = [
  {
    key: 'shell',
    task: `Build the layout shell and visual system exactly per guide sections 4 and 5. The full
palette as CSS custom properties on :root, the type scale, the 8px grid, radii, hairline borders, and
the motion tokens. Layout: 56px header with product name left and run label right; map filling all
remaining space; 320-360px right rail; 96px bottom bar.

NO drop shadows on panels - depth comes from surface lightness. Every numeric element gets
font-variant-numeric: tabular-nums; the guide calls this the highest-ratio detail in the document and
it is not optional. Implement prefers-reduced-motion.

This step alone must already look expensive with zero data loaded.`,
  },
  {
    key: 'canvas-engine',
    task: `Build the two-canvas rendering engine per guide section 3. A STATIC layer (background wash,
lakes, dry roads, drain network) redrawn only on pan/zoom, and a DYNAMIC layer (flooded roads, nodes,
selection) redrawn per frame. At 176,171 segments this separation is the difference between 60 fps
and a slideshow.

Required: devicePixelRatio scaling done correctly (canvas.width = cssW * dpr, then scale the
context); hairlines at exactly 1/dpr canvas units; round lineCap and lineJoin; pointer-drag pan;
wheel zoom ABOUT THE CURSOR, not the canvas centre; a single affine transform applied before drawing.

Background is a radial gradient slightly lighter under the city centre, never flat black.`,
  },
  {
    key: 'basemap',
    task: `Render the basemap from LOCAL data only - this is guide Moment 1. Dry roads in --road-dry
with arterials in --road-major, lakes filled --lake with --lake-edge outlines, all from
data/raw/osm/ and the terrain water layer via the existing routing/graph.py loader.

Serve as GeoJSON from the backend, simplified per zoom level so 176k segments stay interactive.
Bellandur and Varthur must be visible and recognisable - they carry the project's story.

Add a handful of restrained landmark labels (ORR, Marathahalli, Bellandur, Varthur, Hebbal) in
--ink-faint. A judge has to recognise the city.`,
  },
]

const foundation = await pipeline(
  FOUNDATION,
  (u) => agent(`${RULES}\n\nBUILD: ${u.key}\n\n${u.task}`, { label: `build:${u.key}`, phase: 'Foundation', schema: BUILD_SCHEMA }),
  (r, u) =>
    agent(
      `${RULES}

VERIFY "${u.key}" against the guide. You did not build it.

REPORTED:
${JSON.stringify(r, null, 1).slice(0, 20000)}

- Start the server and load the page yourself. Does it run?
- Open ${GUIDE} and check the SPECIFIED VALUES were used verbatim: every hex in the palette, the type
  scale, the radii, the motion timings, the layout dimensions. List any that drifted and why.
- Is tabular-nums applied to every numeric element? Grep for it.
- Is devicePixelRatio scaling actually correct, or will it be soft on retina?
- Is zoom about the cursor or the centre? Read the transform maths.
- RULE 3 AUDIT: grep the JS for numeric literals; classify each as pixel/duration/opacity/conversion
  or VIOLATION.

Report per-item CONFIRMED or REFUTED with your own evidence.`,
      { label: `verify:${u.key}`, phase: 'Verify' }
    )
)

// --------------------------------------------------------------- Phase: Water
phase('Water')
log('Moment 2 - the beat that wins the room. Water blooming across the city over the 3h horizon.')

const water = await parallel([
  () =>
    agent(
      `${RULES}

Build the WATER LAYER - guide section 4 depth ramp and section 6 Moment 2.

Four depth bands, each with its own colour, stroke width and shadowBlur exactly as the guide's DEPTH
array specifies. Glow scales with depth: shallow is thin and dim, deep is thick and blooming. Use
ctx.shadowColor + ctx.shadowBlur, and globalCompositeOperation = 'lighter' so overlapping flooded
segments bloom brighter where flooding concentrates - both prettier and truer.

CRITICAL under rule 3: the band boundaries come from the depth_product schema, NOT from constants you
type. If the product declares its own thresholds, read them. The DEPTH array in the guide gives
colour/width/blur per band, which are presentation values and legitimately constants - the DEPTHS
themselves are data.

Render per-segment depth BANDS in cm. Never per-square-metre point depth.`,
      { label: 'build:water-layer', phase: 'Water', schema: BUILD_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}

Build the TIMELINE and PLAY animation - guide Moment 2, the single most important interaction.

- Scrubber across the 0-3h nowcast horizon, reading real lead times from the product.
- Play button that animates the horizon; auto-play once on load, then hold at +3h.
- INTERPOLATE depth between frames so water GROWS rather than snapping. Ease it. The guide is
  explicit that snapping between frames is what makes it look cheap.
- The two hero stats update live with tabular digits that do not jitter as they climb.
- Respect prefers-reduced-motion.

Three seconds of this communicates "nowcast" better than any slide. Make it smooth.`,
      { label: 'build:timeline', phase: 'Water', schema: BUILD_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}

Build the RIGHT RAIL - guide section 5 and Moment 4.

- Exactly TWO hero stats: flooded segment count, and deepest reading with its location. The guide is
  explicit that more than two and none of them land.
- Selected-segment detail: street name, depth band in cm, status, and the responsible drain node when
  one exists.
- THE PROVENANCE LINE: the manifest path the displayed number came from, in mono 11px --ink-faint at
  the bottom of the panel. It must resolve to a real file. This is unusual and technical judges
  notice it - every number on screen traceable to a file.
- Legend floating bottom-left over the map, --panel at 92% opacity.

Every value here is read from the product. Nothing typed.`,
      { label: 'build:rail', phase: 'Water', schema: BUILD_SCHEMA }
    ),
])

// -------------------------------------------------------------- Phase: Drains
phase('Drains')
log('Moment 3 - the differentiator. Nobody else in the room will render the drainage network.')

const drains = await agent(
  `${RULES}

Build the DRAIN LAYER and SURCHARGE PULSES - guide Moment 3. This is the differentiator: SIH 26085
says urban flooding is dictated by "heavily strained, INVISIBLE drainage networks". We make them
visible.

- Toggleable rajakaluve layer from runs/drain_graph_build/drain_graph.gpkg, rendered --drain, dashed,
  BENEATH the roads, fading in over 420ms.
- Distinguish OBSERVED reaches from SYNTHESISED connectors visually. The artefact tags every edge with
  edge_source, and roughly 3.5x the observed trajectory length is synthesised - a viewer must be able
  to see which parts of the network we walked rather than observed. Dashed-dimmer for synthesised,
  and say so in the legend. This is honesty rendered, and it is defensible in a way that hiding it
  would not be.
- Surcharging nodes: 1.4s pulse on radius and alpha, --surcharge, additive blend. ONLY surcharging
  nodes pulse; nothing else on screen moves at rest.

HARD CONSTRAINT: render a surcharge ONLY where one was MEASURED. WF-1's graph is currently
status=stopped_owner_adjudication with capacity_status=blocked_missing_design_intensity, so if no
coupled run has produced surcharge volumes, render the NETWORK GEOMETRY ONLY and show an honest layer
note that the surcharge model is in progress. Never animate an invented surcharge. The network alone
is still more than anyone else shows.

Design it so surcharge data drops in later without a rewrite.`,
  { label: 'build:drain-layer', phase: 'Drains', schema: BUILD_SCHEMA }
)

// -------------------------------------------------------------- Phase: Polish
phase('Polish')

const polish = await parallel([
  () =>
    agent(
      `${RULES}

INTERACTION POLISH - guide section 7. Work through the numbered list and implement every item:
DPR scaling, 1/dpr hairlines, round caps and joins, radial background, additive water blending, eased
timeline, crosshair cursor over map and pointer over segments, zoom about the cursor.

Plus: hover lift on segments at 160ms, click-to-select with a visible highlight, keyboard
accessibility on the timeline, and focus rings in --accent.

Everything eased with cubic-bezier(0.4, 0, 0.2, 1) at the guide's durations.`,
      { label: 'polish:interaction', phase: 'Polish', schema: BUILD_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}

DESIGNED EMPTY AND ERROR STATES - guide section 7 item 8.

The empty state WILL be seen if anything fails in the room, so it must look deliberate rather than
broken: centred, --ink-dim, one clear line explaining what is absent, the product name still in the
header, the basemap still rendered behind it. A dark map of Bengaluru with "no run loaded" reads as
composed; a blank white page reads as broken.

Also: loading state, product-fetch failure, and a partial-product state. Each designed, none a raw
stack trace.

Per rule 2, none of these may substitute synthetic data to appear alive.`,
      { label: 'polish:states', phase: 'Polish', schema: BUILD_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}

DEMO INTEGRATION. Wire the dashboard into scripts/demo/ so a single command brings up the whole
thing against a chosen run directory, and update docs/DEMO-RUNBOOK.md with the four-moment sequence
from guide section 6: cold open, press play, reveal drains, click a street.

The runbook must carry the exact click path, what to say at each beat, and the honest answer to
"what does this not do yet". Label the loaded run in the header - "Uncoupled baseline - replay #2" or
"Coupled - run <id>" - so the screen never overclaims.

Verify the whole path runs from a cold start and record the timing.`,
      { label: 'polish:demo-integration', phase: 'Polish', schema: BUILD_SCHEMA }
    ),
])

// -------------------------------------------------------------- Phase: Verify
phase('Verify')

const verify = await parallel([
  () =>
    agent(
      `${RULES}

FULL GUIDE CONFORMANCE AUDIT. Open ${GUIDE} and check the built dashboard against it section by
section. For every specified value - each palette hex, each type size and weight and tracking, each
radius, each duration, each layout dimension - report USED VERBATIM, DRIFTED (with the actual value),
or ABSENT.

Then the render order from section 3: is it back-to-front exactly as specified? Are there genuinely
two canvases with static and dynamic separated?

Be pedantic. The guide exists because the difference between good and expensive is exactly these
values.`,
      { label: 'verify:guide-conformance', phase: 'Verify' }
    ),
  () =>
    agent(
      `${RULES}

RULE 3 AUDIT, done independently. Do not accept any track's claim that it has no hardcoded metrics.

Grep every file under src/jaladhar/web/ for numeric literals. Classify each: pixel dimension,
duration, opacity, colour, unit conversion, array index - or VIOLATION (a depth, threshold, count,
rate, score, or any other metric that should have been read from a product file).

Then verify the provenance line: does the displayed manifest path RESOLVE to a real file on disk, and
does the number shown actually appear in it? Follow it end to end.

Report every violation with file and line.`,
      { label: 'verify:rule3', phase: 'Verify' }
    ),
  () =>
    agent(
      `${RULES}

PERFORMANCE AND ROBUSTNESS. Load the real product with all 176,171 segments and measure:
- frame time during timeline playback, and whether it holds 60fps
- initial load time from cold start
- memory footprint
- behaviour at maximum zoom-out with every layer enabled

If playback drops frames, diagnose it concretely - it is almost certainly the static/dynamic canvas
split being violated, or per-frame GeoJSON reparsing.

Then robustness: kill the backend mid-session, load with a missing product, load with a truncated
product. Does it degrade into a designed state or throw into the console?`,
      { label: 'verify:performance', phase: 'Verify' }
    ),
  () =>
    agent(
      `${RULES}

Write docs/FRONTEND-VISUAL-QA.md - a HUMAN visual checklist, because no agent in this workflow can
see the screen and structural conformance is not the same as looking good.

Produce a numbered checklist the owner walks through in front of the running dashboard, grouped by
the four moments in guide section 6. Each item is a single yes/no a person can answer in seconds -
"do the digits stay still as the count climbs", "does water grow rather than snap", "is the deepest
segment visibly blooming brighter than a shallow one", "can you recognise Bengaluru without labels".

Include the three most likely ways this still looks amateur despite passing every automated check,
and what to look for in each.`,
      { label: 'verify:visual-qa-checklist', phase: 'Verify' }
    ),
])

log('WF-5 complete. Structural checks are done; the visual QA checklist needs human eyes on the screen.')

return {
  foundation: foundation.flat().filter(Boolean),
  water: water.filter(Boolean),
  drains,
  polish: polish.filter(Boolean),
  verification: verify.filter(Boolean),
  next: 'Owner walks docs/FRONTEND-VISUAL-QA.md in front of the running dashboard. Agents verified mechanics; only a person can verify that it looks expensive.',
}
