export const meta = {
  name: 'wf0-recon-contracts',
  description: 'Day 0: parallel recon of the existing stack, resolve the DWR blocker, and freeze the four interface contracts that let every later workstream run in parallel',
  whenToUse: 'Run FIRST, before any other SIH-26085 workflow. Everything downstream consumes its contracts.',
  phases: [
    { title: 'Recon', detail: 'read the existing stack in parallel - terrain, solver, data, validation' },
    { title: 'Research', detail: 'resolve N-3 DWR availability and find cited capacity standards' },
    { title: 'Contracts', detail: 'freeze the four schemas that unblock parallel work' },
    { title: 'Verify', detail: 'adversarial check that the contracts are actually sufficient' },
  ],
}

const REPO = '/home/darshil/Desktop/sih/clginternal'
const PY = REPO + '/.venv/bin/python'

const RULES = `
BINDING RULES (from ${REPO}/CLAUDE.md - read it before you start):
- NO FABRICATED DATA, EVER. If a source is unavailable, say so and stop. Do not invent elevations,
  rainfall, drain dimensions, coordinates, or depth bands.
- NO PLACEHOLDER PHYSICS. No blur filters, no distance transforms standing in for flow.
- Every numeric claim must be traceable to a file in data/ or a logged run in runs/.
- V1: acceptance is REALIZED state (bytes on disk), never DECLARED state (a config value, a
  docstring, a variable you just assigned).
- V7: state the scope a check runs at beside the scope it claims.
- R1: label every claim a FINDING (measured) or a READING (what you think it means).
- R3: inventory what is already on disk BEFORE acquiring or measuring anything.
You are READ-ONLY in this workflow. Do not modify, create, or delete any file under ${REPO}
except where a step explicitly says to write. Use ${PY} as the interpreter.
`

const RECON_SCHEMA = {
  type: 'object',
  required: ['area', 'findings', 'files_read', 'blockers'],
  properties: {
    area: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'evidence_path', 'kind'],
        properties: {
          claim: { type: 'string' },
          evidence_path: { type: 'string', description: 'exact file path + line or key that proves it' },
          kind: { type: 'string', enum: ['finding', 'reading'] },
        },
      },
    },
    files_read: { type: 'array', items: { type: 'string' } },
    blockers: { type: 'array', items: { type: 'string' } },
    interfaces_exposed: {
      type: 'array',
      description: 'functions/classes/paths that later work must call or produce',
      items: { type: 'string' },
    },
  },
}

// ---------------------------------------------------------------- Phase: Recon
phase('Recon')
log('WF-0 starting: 8 parallel readers over the existing stack. Nothing is written in this phase.')

const RECON_AREAS = [
  {
    key: 'terrain',
    prompt: `Map the terrain stack. Specifically: the conditioned DEM and where it lives; the grid
definition (rows, cols, cell size, CRS, transform) and which module builds it; whether a D8 flow
direction raster or pointer grid already exists on disk or can be produced, and by which function;
what runs/terrain_conditioning/manifest.json and runs/terrain_drains/manifest.json guarantee.
CRITICAL for downstream: we need D8 flow direction to stitch a disconnected drain network. Report
exactly how to obtain it and whether it already exists.`,
  },
  {
    key: 'solver_drains',
    prompt: `Trace EXACTLY how the 2D solver currently consumes drains. Find where
data/processed/buffered/drain_capacity.tif is read, how the per-cell sink is applied inside the
timestep, where drain_out is accumulated into the mass budget, and what the sign conventions are.
Quote the actual lines. This is the code a surcharge coupling must modify, so the report must be
precise enough to implement against. Also report the solver's state variables, its stencil (D4),
its adaptive-dt mechanism, and how the mass budget closes.`,
  },
  {
    key: 'drain_data',
    prompt: `Characterise data/raw/bbmp_drains/ completely. Three KMLs: primary (163), secondary
(870), tertiary (~5800). Already established: attributes are OBJECTID/Length/SHAPE_Leng only - no
width, depth, invert, or cross-section - and primary+secondary form ~370 disconnected components at
every snap tolerance from 0.5 to 10 m. VERIFY BOTH independently, do not take them on trust.
Additionally: diagnose why tertiary_drains_2022.kml raises "ParseException: Unexpected EOF parsing
WKB" and whether it is recoverable (try ogr2ogr, fiona with different drivers, raw XML parse).
Report geometry quality: self-intersections, zero-length segments, duplicate geometries, and whether
reach direction in the coordinate order is consistent with downhill.`,
  },
  {
    key: 'validation',
    prompt: `Map the validation harness. How BBMP complaint points (399) are scored at 0.10 m; how
GT depth points are scored and where the 16 strict rows and the 8/8 train/holdout split live; what
scripts/goal_d_score_rubric.py computes and which rasters it opens; how road segments are derived
and scored; where the null model lives and how its seed is set. We need to re-point all of this at a
coupled model, so report the exact call signatures and input contracts.`,
  },
  {
    key: 'forcing',
    prompt: `Map the forcing subsystem. The RainfallAdapter interface in src/jaladhar/forcing/, the
IMERG adapter, the KSNDMC anchored adapter, and src/jaladhar/forcing/open_meteo.py. Report the exact
interface a NEW nowcast adapter must implement (method signatures, RainfallInterval/RainfallEvent
shapes, how intervals are validated, how the fail-closed chain verification works). Also report what
the forecast config path in configs/ currently expects.`,
  },
  {
    key: 'roads',
    prompt: `Map everything needed for a routing API. Is there an OSM road network on disk under
data/raw/osm/? Is osmnx installed in the venv? How are road segments currently built and how are
they joined to the depth raster for per-segment status? Report the segment identifier scheme, since
the dashboard and the routing API must both key on it.`,
  },
  {
    key: 'compute',
    prompt: `Report the compute picture from realized state. configs/compute.yaml contents; the
budget-estimate mechanism and where it refuses; measured throughput from
runs/goal_d_replay2_final_config/manifest.json (steps, wall clock, GPU hours, dt); VRAM headroom
observed. Then compute, showing your arithmetic: wall-clock cost of (a) a 3-hour simulation on the
full 12,728,415-cell buffered domain, (b) the same on a 1/16 subdomain tile. We need to know whether
a 3-hour nowcast fits in 10 minutes.`,
  },
  {
    key: 'tests',
    prompt: `Map the test suite and the invariant/red-test conventions. How many tests, how they are
organised, how BLOCKED/PARTIAL tests are marked per V7, and where the recorded red-mutations live.
Report the exact pattern a new invariant test must follow to satisfy V5 (demonstrated red under a
deliberate mutation, mutation recorded next to it). Give a concrete example from the existing suite.`,
  },
]

const recon = await parallel(
  RECON_AREAS.map((a) => () =>
    agent(`${RULES}\n\nRECON TASK: ${a.key}\n\n${a.prompt}`, {
      label: `recon:${a.key}`,
      phase: 'Recon',
      schema: RECON_SCHEMA,
    })
  )
)

const reconOk = recon.filter(Boolean)
log(`Recon complete: ${reconOk.length}/${RECON_AREAS.length} areas mapped, ${reconOk.reduce((n, r) => n + (r.blockers || []).length, 0)} blockers raised`)

// ------------------------------------------------------------- Phase: Research
phase('Research')

const RESEARCH_SCHEMA = {
  type: 'object',
  required: ['question', 'answer', 'confidence', 'sources', 'recommendation'],
  properties: {
    question: { type: 'string' },
    answer: { type: 'string' },
    confidence: { type: 'string', enum: ['established', 'likely', 'uncertain', 'blocked'] },
    sources: {
      type: 'array',
      items: {
        type: 'object',
        required: ['url_or_path', 'what_it_establishes'],
        properties: { url_or_path: { type: 'string' }, what_it_establishes: { type: 'string' } },
      },
    },
    recommendation: { type: 'string' },
    cost_implication: { type: 'string' },
  },
}

const research = await parallel([
  () =>
    agent(
      `${RULES}

RESOLVE BLOCKER N-3 - rainfall nowcast availability. This gates a whole workstream, so the answer
must be sourced, not guessed.

SIH 26085 requires "high-resolution rainfall nowcasts (from Doppler Weather Radars)". There are two
readings and they differ by a week of effort:
  (1) The nowcast is the INPUT and DWR is merely its provenance -> consume IMD's published nowcast
      product. Cheap if accessible.
  (2) We must build nowcasting from volumetric reflectivity (Z-R relation + optical-flow
      extrapolation, e.g. pySTEPS). Requires raw radar access that may not be public.

Determine, WITH SOURCES: does IMD publish a machine-consumable nowcast product (API, WMS, GRIB,
NetCDF, or even georeferenced imagery) covering Bengaluru? Is there a DWR at/near Bengaluru and is
its data accessible? What does mausam.imd.gov.in actually expose programmatically? Are there
alternatives with radar-derived nowcast (Open-Meteo, RainViewer, IMD's own Nowcast portal)?

FIRST, per R3, inventory what is already on disk: check data/raw/ and data/fetched_articles.json
before searching outward.

If access is genuinely blocked, say so plainly - that is the correct answer and it goes to
OPEN-ITEMS. Do not propose a workaround that fabricates rainfall.`,
      { label: 'research:dwr-access', phase: 'Research', schema: RESEARCH_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}

RESOLVE BLOCKER N-1 - a CITED basis for stormwater drain hydraulic capacity.

Established finding: BBMP rajakaluve data has geometry and length only. No width, depth, invert
level, or cross-section exists in our data. Under rule 1 we may NOT invent these numbers.

Find a defensible, citable basis for assigning cross-section and capacity by drain order. Look for:
- CPHEEO Manual on Sewerage and Sewage Treatment / Storm Water Drainage design standards (India)
- IS codes for stormwater drain design
- BBMP / BDA rajakaluve remodelling DPRs or tender documents that publish channel dimensions
- Published academic work on Bengaluru's SWD network with measured cross-sections
- Whether primary rajakaluve widths are measurable from the Sentinel-2 imagery we already hold
  (10 m bands) or from OSM waterway width tags

Deliver a CONCRETE assignment rule: given drain order (primary/secondary/tertiary) and upstream
contributing area, what cross-section and what Manning's n, with the citation for each number, and
what sensitivity band should be reported around it.

The output must be usable as: capacity_basis = "<citation>", never "assumed".`,
      { label: 'research:capacity-standards', phase: 'Research', schema: RESEARCH_SCHEMA }
    ),
  () =>
    agent(
      `${RULES}

Research the ENGINEERING APPROACH for 1D-2D coupled drainage with surcharge, so the design panel in
WF-2 starts from known practice rather than inventing one.

Cover:
- EPA SWMM: is pyswmm/swmm-toolkit installable in this venv (python 3.11)? What does the .inp format
  require that we would have to supply? Does SWMM handle surcharge and node flooding natively? What
  exactly would a 2D-surface <-> SWMM-node coupling look like at each exchange step?
- The standard alternatives: dual-drainage / bidirectional inlet exchange, weir-and-orifice inlet
  equations, capacity-limited capture with surcharge return.
- What the inlet exchange equation should be (weir flow at low depth, orifice flow when submerged),
  with citations.
- Numerical stability: how coupling terms interact with an explicit adaptive-dt 2D scheme, and what
  constraints the exchange places on dt.

Deliver a comparison with a clear recommendation for a 4-day build on an existing custom GPU 2D
solver: couple to SWMM, or implement a simplified 1D graph router in-repo? Be honest about what each
costs and what each risks.`,
      { label: 'research:coupling-approach', phase: 'Research', schema: RESEARCH_SCHEMA }
    ),
])

const researchOk = research.filter(Boolean)
for (const r of researchOk) log(`Research [${r.question.slice(0, 60)}]: ${r.confidence.toUpperCase()} - ${r.recommendation.slice(0, 110)}`)

// ------------------------------------------------------------ Phase: Contracts
phase('Contracts')
log('Freezing interface contracts. Once these land, all five day-1..3 workstreams parallelize.')

const reconDigest = JSON.stringify(reconOk, null, 1).slice(0, 60000)
const researchDigest = JSON.stringify(researchOk, null, 1).slice(0, 30000)

const CONTRACT_SCHEMA = {
  type: 'object',
  required: ['name', 'path', 'schema_json', 'producer', 'consumers', 'manifest_guarantees', 'rationale'],
  properties: {
    name: { type: 'string' },
    path: { type: 'string', description: 'where the artefact will live on disk' },
    schema_json: { type: 'string', description: 'the full JSON Schema or table spec as a string' },
    producer: { type: 'string' },
    consumers: { type: 'array', items: { type: 'string' } },
    manifest_guarantees: {
      type: 'array',
      description: 'V8: fields the producer writes into the manifest that consumers assert at load',
      items: { type: 'string' },
    },
    rationale: { type: 'string' },
    open_questions: { type: 'array', items: { type: 'string' } },
  },
}

const CONTRACTS = [
  {
    key: 'drain_graph',
    spec: `The DRAIN GRAPH artefact. A directed graph: nodes (junctions, inlets, outfalls) and edges
(reaches). Must carry per-edge hydraulic capacity WITH its citation, per-node type, connectivity
after DEM-based stitching, and flow direction. Must record which edges were observed vs which
connectors were synthesised by D8 routing - a consumer has to be able to tell them apart, and a
synthesised connector is a modelling assumption that must be visible. Format: GeoPackage layers plus
a JSON adjacency, EPSG:32643 matching the solver grid.`,
  },
  {
    key: 'coupling_iface',
    spec: `The SURFACE<->DRAIN COUPLING INTERFACE. What the 2D solver calls each timestep and what it
gets back. Must cover: inlet capture (surface -> node, capacity limited), node routing along edges,
and SURCHARGE RETURN (node -> surface when capacity is exceeded). Must specify sign conventions,
units, where the exchange enters the mass budget so it still closes, and what the exchange does to
the dt constraint. This is the contract the coupling implementation in WF-2 is written against.`,
  },
  {
    key: 'depth_product',
    spec: `The DEPTH PRODUCT the dashboard and API consume. Per-road-segment flood status and depth
BAND in cm (never per-square-metre point depth - CLAUDE.md is explicit that per-segment topological
status is what we can support at 10 m). Must carry: segment id, band low/high cm, confidence,
timestamp, forecast lead time, and the manifest path the value came from. Rule 3: the frontend
hardcodes no number, so every field must be sourced.`,
  },
  {
    key: 'nowcast_input',
    spec: `The NOWCAST FORCING contract - what a new nowcast adapter must produce to satisfy the
existing RainfallAdapter interface. Must handle a 0-3 hour horizon, record provenance (which product,
which issue time, which lead times), and fail closed when the source is unavailable rather than
silently substituting climatology. Shape it so both possible answers to N-3 fit behind it.`,
  },
]

const contracts = await pipeline(
  CONTRACTS,
  (c) =>
    agent(
      `${RULES}

You are freezing an INTERFACE CONTRACT for a 4-day parallel build. Five workstreams will be built
simultaneously against these contracts, so ambiguity here becomes an integration failure on day 4.

RECON OF THE EXISTING STACK:
${reconDigest}

RESEARCH RESULTS:
${researchDigest}

CONTRACT TO DEFINE: ${c.key}
${c.spec}

Requirements:
- Be concrete and complete. Field names, types, units, CRS, nullability.
- Apply V8: name the properties the producer guarantees in its manifest and that consumers assert at
  load. A prose note does not discharge V8 - it must be a manifest field and an assertion.
- Where the contract encodes an assumption (e.g. capacity basis), the schema must carry the citation
  as data, not as a comment.
- List open questions rather than guessing. An open question is cheap now and expensive on day 4.`,
      { label: `contract:${c.key}`, phase: 'Contracts', schema: CONTRACT_SCHEMA }
    ),
  (contract, orig) =>
    agent(
      `${RULES}

ADVERSARIAL REVIEW of a frozen interface contract. Your job is to find the ambiguity that causes an
integration failure on day 4, not to approve it.

CONTRACT (${orig.key}):
${JSON.stringify(contract, null, 1).slice(0, 24000)}

Attack it:
- Name every field where two competent implementers would make different choices.
- Find every place a unit, sign convention, CRS, or nullability is unstated.
- Check the V8 manifest guarantees actually constrain anything - would a consumer assertion on them
  ever go red? If not, they are decorative.
- Check whether an assumption is encoded as data (citable) or hidden in prose (not).
- Is the contract sufficient to build the consumer WITHOUT the producer existing? If not, the
  parallelism this workflow exists to enable does not work. Say so.

Return the contract with concrete amendments applied, plus a list of what you changed and why.`,
      { label: `contract-review:${orig.key}`, phase: 'Verify', schema: CONTRACT_SCHEMA }
    )
)

// --------------------------------------------------------------- Phase: Verify
phase('Verify')

const finalContracts = contracts.filter(Boolean)

const sufficiency = await agent(
  `${RULES}

FINAL SUFFICIENCY CHECK across all four contracts together. Individual contracts can each be fine
and still not compose.

CONTRACTS:
${JSON.stringify(finalContracts, null, 1).slice(0, 70000)}

RECON:
${reconDigest.slice(0, 30000)}

Answer, concretely:
1. Can the drain-graph producer, the coupling implementation, the dashboard, the routing API, and the
   nowcast adapter ALL be built in parallel with zero further coordination? If not, name the exact
   missing coupling point.
2. Trace one unit of water end to end: rainfall interval -> surface cell -> inlet capture -> node ->
   edge -> downstream node -> surcharge back to surface -> depth product -> segment status ->
   dashboard. Does every handoff have a defined contract? Name any gap.
3. Does the mass budget still close across the coupling boundary? Where exactly do capture and
   surcharge enter the accounting?
4. What is the single most likely day-4 integration failure given these contracts, and what one
   amendment now would prevent it?

Be blunt. A gap named now costs an hour; found on day 4 it costs the presentation.`,
  { label: 'verify:sufficiency', phase: 'Verify' }
)

log('WF-0 complete. Read the sufficiency check before launching WF-1.')

return {
  recon: reconOk,
  research: researchOk,
  contracts: finalContracts,
  sufficiency,
  next: 'Write the contracts to configs/contracts/ and commit them, then launch WF-1 (wf1-drain-graph.js).',
}
