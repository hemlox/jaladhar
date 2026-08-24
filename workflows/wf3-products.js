export const meta = {
  name: 'wf3-products',
  description: 'Day 3: five independent product tracks built in parallel against frozen contracts - dashboard, routing API, nowcast adapter, segment scorer, demo packaging. CPU only, runs alongside GPU validation.',
  whenToUse: 'After WF-0 contracts are frozen. Can run CONCURRENTLY with GPU validation runs since it touches no device.',
  phases: [
    { title: 'Build', detail: 'five product tracks in parallel against contracts, not against outputs' },
    { title: 'Verify', detail: 'independent verification plus a rule-3 hardcoded-number audit' },
    { title: 'Integrate', detail: 'end-to-end wiring check and demo rehearsal' },
  ],
}

const REPO = '/home/darshil/Desktop/sih/clginternal'
const PY = REPO + '/.venv/bin/python'

const RULES = `
BINDING RULES (${REPO}/CLAUDE.md is authoritative).

THE ONE THAT GOVERNS THIS WORKFLOW:
- Rule 3: EVERY numeric claim must be traceable to a file in data/ or a logged run in runs/.
  NO HARDCODED METRICS IN THE FRONTEND. Not one. If a number appears on screen it was read from a
  manifest at load time, and the UI can name the manifest it came from.
- Rule 2: no placeholder physics. If the solver has produced nothing, the UI shows nothing. It does
  NOT show a plausible-looking blur to seem alive.

ALSO BINDING:
- Report per-road-segment flood STATUS and depth BANDS in cm. Never per-square-metre point depth -
  that is a metric claim at 10 m resolution that this project cannot support. SIH 26085 asks for
  "water depth estimations in centimeters"; per-segment bands in cm satisfy that honestly.
- Rule 6: manifest at run start, updated in place.
- V1 realized state; V7 scope beside claim; V8 producer guarantees asserted by consumer.
- R1: label claims FINDING or READING.
- Style: python 3.11, type hints, ruff + black, typer CLI, YAML config. Interpreter ${PY}.
- Git: explicit staging by path, never 'git add -A'. No self-attribution footers.

CRITICAL DESIGN CONSTRAINT: build against the FROZEN CONTRACTS in configs/contracts/, not against
actual solver output. The solver may still be changing on day 3. If your track cannot be built
without real output, the contract is insufficient and you report that immediately as a blocker -
that is exactly the failure WF-0's sufficiency check existed to prevent.
`

const BUILD_SCHEMA = {
  type: 'object',
  required: ['track', 'files_written', 'executed', 'realized_state', 'contract_gaps', 'deviations'],
  properties: {
    track: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    executed: { type: 'boolean' },
    execution_evidence: { type: 'string' },
    realized_state: { type: 'object', additionalProperties: true },
    contract_gaps: { type: 'array', description: 'anything the frozen contract failed to specify', items: { type: 'string' } },
    deviations: { type: 'array', items: { type: 'string' } },
    blockers: { type: 'array', items: { type: 'string' } },
  },
}

phase('Build')
log('WF-3: five product tracks, fully parallel. Each owns its own directory; none blocks on another.')

const TRACKS = [
  {
    key: 'dashboard',
    owns: 'src/jaladhar/web/ and static assets under src/jaladhar/web/static/',
    task: `Build the web GIS dashboard SIH 26085 requires: street-by-street flood projection with a
0-3 hour forward-looking window, depth in cm.

- Map with road segments coloured by flood status and depth band.
- A time slider across the 0-3 h nowcast horizon.
- Every displayed number sourced from a manifest, with the manifest path visible in the UI (a
  provenance panel or per-feature popup). This is rule 3 made visible, and it is a genuine
  differentiator in front of an MoES panel - most entries cannot show where a number came from.
- An honest empty state: when no run has produced output, the map says so. It does not invent.
- Read the depth-product contract for the exact schema you consume.

Prefer stdlib and well-known packages; a small FastAPI or Flask app serving GeoJSON plus MapLibre or
Leaflet is right. Do not add a heavy framework to save five lines.`,
  },
  {
    key: 'routing-api',
    owns: 'src/jaladhar/routing/',
    task: `Build the flood-safe routing API SIH 26085 requires: "interface with navigation maps to
suggest flood-safe alternative routes for emergency services, public transit, and commuters".

- Load the OSM road graph (osmnx is already in the stack - check what is on disk under data/raw/osm/
  first, per R3).
- Join segment flood status from the depth-product contract onto graph edges.
- Route avoiding flooded edges, with a configurable depth threshold for impassability, and cite what
  that threshold is based on (vehicle wading depth is a real published number - find it, do not
  invent it).
- Expose a clean HTTP API: origin, destination, vehicle class, forecast lead time -> route plus the
  avoided segments, so a caller can see WHY the route changed.
- Handle the disconnected case honestly: if no flood-safe route exists, say so rather than returning
  a route through water.`,
  },
  {
    key: 'nowcast-adapter',
    owns: 'src/jaladhar/forcing/nowcast.py and tests/forcing/test_nowcast.py',
    task: `Build the rainfall nowcast adapter implementing the existing RainfallAdapter interface,
per the nowcast-input contract and WF-0's answer to blocker N-3.

- 0-3 hour horizon, provenance recorded (product, issue time, lead times).
- FAIL CLOSED: if the source is unavailable, raise. Never silently substitute climatology, the last
  observed value, or anything else. Rule 1.
- If N-3 came back BLOCKED, build the adapter against the interface with a clearly-labelled
  unavailable state, and report that the axis is blocked rather than faking a feed. A blocked axis
  honestly reported is a valid day-4 outcome; a fabricated feed is not.
- Include the red test: mutate the source to unavailable and show the adapter refusing rather than
  returning data.`,
  },
  {
    key: 'segment-scorer',
    owns: 'src/jaladhar/validation/segment_status.py and scripts/score_g1.py',
    task: `Build the per-segment status scorer that produces gate G1's number.

- Per-road-segment flood/no-flood from a depth raster, at the declared threshold.
- Hit rate against the 399 BBMP complaint points.
- Null-model lift with N=10,000 spatial nulls and a RECORDED seed. Lift is mandatory: hit rate alone
  is buyable by flooding more area, which replay #2 demonstrated (45.86% at 7.4% flooded = 6.20x
  lift, versus 59.90% at 14.2% flooded = 4.22x lift - the rate rose while the lift fell).
- Report BOTH depth conventions per data/curation/goal_d_depth_convention.json: 3x3 local-max as
  primary, exact-cell as declared sensitivity. If they disagree on the verdict, the disagreement IS
  the reported result - neither may be chosen after both are seen.
- Manifest per rule 6.`,
  },
  {
    key: 'demo-packaging',
    owns: 'scripts/demo/ and docs/DEMO-RUNBOOK.md',
    task: `Build the demo runbook and packaging so the presentation cannot fail on mechanics.

- A single command that brings up dashboard + API against a chosen run directory.
- A rehearsal script that verifies every service starts, every endpoint answers, and every number on
  screen resolves to a manifest.
- A precomputed fallback run directory so the demo does not depend on a live GPU run finishing.
- A one-page runbook: what to show, in what order, what each number means, and - importantly - the
  honest answer to "what does this NOT do yet", with the open axes named. A team that states its
  limits precisely reads as more credible to an MoES panel than one claiming centimetres it cannot
  support.`,
  },
]

const results = await pipeline(
  TRACKS,
  (t) =>
    agent(
      `${RULES}

PRODUCT TRACK: ${t.key}
YOU OWN: ${t.owns}. Four other agents are building other tracks concurrently - touch nothing outside
your directory.

${t.task}

Build it, then RUN it (V12) and paste the real command and real output. Report any place the frozen
contract failed to specify something you needed - that is a contract_gap and it is urgent, because
four other tracks are consuming the same contracts right now.`,
      { label: `build:${t.key}`, phase: 'Build', schema: BUILD_SCHEMA }
    ),
  (r, t) =>
    agent(
      `${RULES}

VERIFY track "${t.key}" independently. You did not build it.

REPORTED:
${JSON.stringify(r, null, 1).slice(0, 24000)}

- Run it yourself; does the execution evidence reproduce?
- RULE 3 AUDIT (the important one for this workflow): grep the track for hardcoded numeric literals
  that represent metrics, depths, thresholds, counts, or scores. For each, determine whether it is a
  legitimate constant (a unit conversion, a CSS pixel) or a metric that should have been read from a
  manifest. Report every violation with file and line.
- Rule 2 audit: is there anywhere the UI or API would show plausible-looking output when the
  underlying model produced nothing? Trace it.
- V1: measure realized state yourself rather than accepting the report.

CONFIRMED or REFUTED per claim, with your own evidence.`,
      { label: `verify:${t.key}`, phase: 'Verify' }
    )
)

phase('Integrate')

const integration = await parallel([
  () =>
    agent(
      `${RULES}

END-TO-END WIRING CHECK. Trace one unit of water from rainfall to a rendered pixel and a routing
decision, naming every handoff and every file:

rainfall nowcast interval -> forcing adapter -> solver surface cell -> inlet capture -> drain node ->
edge routing -> surcharge return -> depth raster -> segment status -> depth product JSON ->
dashboard render -> routing API edge weight -> returned route.

At EVERY handoff report: does the contract exist, is it implemented on both sides, and does the
consumer assert the producer's V8 manifest guarantees? Name every gap concretely. This is the day-4
integration failure surfaced 24 hours early.`,
      { label: 'integrate:e2e-trace', phase: 'Integrate' }
    ),
  () =>
    agent(
      `${RULES}

DEMO REHEARSAL. Actually run the demo runbook end to end as if presenting. Start every service, open
every view, call every endpoint, click through the story.

Report: what worked, what broke, how long startup took, and every place a number appeared without a
resolvable manifest path. Then write the three questions an MoES/NCMRWF panel is most likely to ask
that this demo currently cannot answer well - and what a truthful answer to each would be.

Do not soften. Finding the broken thing now is the entire point.`,
      { label: 'integrate:rehearsal', phase: 'Integrate' }
    ),
  () =>
    agent(
      `${RULES}

REQUIREMENTS TRACE against SIH 26085. For each of the six stated requirements, report what exists,
where it lives, and what is genuinely missing:

R1 rainfall nowcasts from Doppler Weather Radar
R2 instant routing of that volume across a 2D surface terrain model
R3 stormwater drain network as a DIRECTED GRAPH (nodes manholes/inlets, edges pipes/canals)
R4 hydraulic capacity, predicting where blockage/overcapacity causes BACKFLOW onto streets
R5 dynamic web GIS dashboard, street-by-street, depth in cm, 0-3 h forward window
R6 API interfacing with navigation maps for flood-safe alternative routes

For each: DONE / PARTIAL / MISSING, with the evidence path. For every PARTIAL, state precisely what
is partial about it - per V7, name the scope achieved beside the scope claimed. This trace becomes
the honesty section of the presentation, so it must be accurate rather than flattering.`,
      { label: 'integrate:requirements-trace', phase: 'Integrate' }
    ),
])

log('WF-3 complete. Read the requirements trace and the rehearsal before day 4.')

return {
  tracks: results.flat().filter(Boolean),
  integration: integration.filter(Boolean),
  next: 'Fix what the rehearsal broke, then run WF-4 (wf4-gate.js) once a coupled GPU run has produced scoreable output.',
}
