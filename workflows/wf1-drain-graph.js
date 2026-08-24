export const meta = {
  name: 'wf1-drain-graph',
  description: 'Day 1: turn 370 disconnected BBMP rajakaluve reaches into a connected directed graph with cited hydraulic capacities, adversarially validated',
  whenToUse: 'After WF-0 contracts are frozen and committed. Produces the artefact WF-2 couples to.',
  phases: [
    { title: 'Design', detail: 'three independent stitching strategies, judged against each other' },
    { title: 'Build', detail: 'implement graph construction: stitch, nodes, direction, capacity' },
    { title: 'RedTest', detail: 'every invariant demonstrated red under deliberate mutation (V5)' },
    { title: 'Verify', detail: 'adversarial verification of topology, direction, and capacity provenance' },
  ],
}

const REPO = '/home/darshil/Desktop/sih/clginternal'
const PY = REPO + '/.venv/bin/python'

const RULES = `
BINDING RULES (${REPO}/CLAUDE.md is authoritative - read it first):
- NO FABRICATED DATA. The drain KMLs have geometry and length ONLY: no width, depth, invert, or
  cross-section. You may NOT invent these. Any capacity value must carry a citation in a
  capacity_basis field. The string "assumed" is not a basis.
- NO PLACEHOLDER PHYSICS.
- Rule 6: write the manifest AT RUN START with git SHA, config snapshot, status "running", and
  update it in place on completion. Provenance that only exists if nothing goes wrong is not
  provenance.
- Rule 7: resolve every config key at startup with an aggregated error listing ALL missing keys.
- V1: verify REALIZED state (the file on disk, its actual dtype/CRS/bounds), never DECLARED state.
- V5: every invariant test must be demonstrated RED under a deliberate mutation, and the mutation
  recorded beside it.
- V7: state the scope a check runs at beside the scope it claims. A correct assertion over too
  narrow a domain cannot fail.
- V8: the producer writes its guaranteed properties into the manifest; the consumer asserts them at
  load. Prose does not discharge V8.
- R1: label every claim FINDING (measured) or READING (inferred).
- Style: python 3.11, type hints, ruff + black, typer CLI entry per module, config in YAML under
  configs/. Interpreter: ${PY}. No new dependency to save five lines.
- Git: explicit staging by path, NEVER 'git add -A'. Never checkout/restore/stash a file you did not
  create. No Co-Authored-By or generated-with footers in commit messages.

ESTABLISHED FINDINGS you may rely on (all measured 2026-08-24, reproduce if you doubt them):
- data/raw/bbmp_drains/: primary 163, secondary 870, tertiary ~5800 features.
- Attributes present: OBJECTID, Length, SHAPE_Leng, Shape.STLength(). Nothing else non-null.
- primary+secondary = 1033 features, 767.3 km total, EPSG:32643.
- Endpoint-snap connectivity: 373 components at 0.5 m, 371 at 2 m, 371 at 5 m, 370 at 10 m.
  Largest component 57 nodes = 4.1%. Raising tolerance 20x removes 3 components, so this is NOT a
  precision problem - the polylines genuinely do not share endpoints.
- tertiary_drains_2022.kml raises ParseException: Unexpected EOF parsing WKB.
- The conditioned DEM is D8 (whitebox breach_depressions, 0 pits) and the solver is D4. That seam is
  already asserted; do not break it.
`

const CONTRACTS_NOTE = `
The drain-graph contract was frozen by WF-0 and lives in configs/contracts/. READ IT FIRST and build
exactly to it. If you believe the contract is wrong, report that as a deviation for owner
adjudication - implementation agents may fix bugs on their own initiative but may NEVER act on
deviations from stated objectives.
`

// --------------------------------------------------------------- Phase: Design
phase('Design')
log('WF-1: three independent stitching strategies, judged. The network is 370 components; how we connect them is a modelling decision, not a detail.')

const DESIGN_SCHEMA = {
  type: 'object',
  required: ['strategy_name', 'method', 'assumptions', 'failure_modes', 'validation_plan', 'synthesised_edge_policy'],
  properties: {
    strategy_name: { type: 'string' },
    method: { type: 'string', description: 'step by step, concrete enough to implement from' },
    assumptions: { type: 'array', items: { type: 'string' } },
    failure_modes: { type: 'array', items: { type: 'string' } },
    validation_plan: { type: 'array', items: { type: 'string' } },
    synthesised_edge_policy: {
      type: 'string',
      description: 'how connectors invented by the algorithm stay distinguishable from observed reaches',
    },
    estimated_hours: { type: 'number' },
  },
}

const STRATEGIES = [
  {
    key: 'd8-routing',
    angle: `Stitch by routing along the conditioned DEM's D8 flow direction. From each component's
lowest endpoint, walk the D8 path downslope until it reaches a cell belonging to another reach, and
create a synthesised connector edge along that path. Consider: what to do when the walk reaches a
domain boundary, a lake polygon, or loops.`,
  },
  {
    key: 'catchment-hierarchy',
    angle: `Stitch by hydrological hierarchy. Derive catchments/flow accumulation from the DEM, assign
each reach to a catchment, and connect reaches by catchment nesting and drain order
(tertiary -> secondary -> primary -> outfall). Consider: reaches that cross catchment boundaries, and
whether order in the BBMP data is reliable.`,
  },
  {
    key: 'proximity-plus-gradient',
    angle: `Stitch by constrained nearest-neighbour: connect an endpoint to the nearest endpoint of a
different component, but ONLY where the connection is downhill in the conditioned DEM and under a
maximum gap length. Consider: what maximum gap is defensible, and what happens to endpoints with no
valid downhill partner.`,
  },
]

const designs = await parallel(
  STRATEGIES.map((s) => () =>
    agent(
      `${RULES}\n${CONTRACTS_NOTE}

DESIGN TASK - stitching strategy "${s.key}".

${s.angle}

Design this strategy properly and completely. Do NOT hedge toward the other strategies; argue this
one as well as it can be argued, and be honest about where it breaks.

Mandatory in your design:
- How synthesised connectors stay permanently distinguishable from observed reaches in the artefact.
  A downstream consumer must be able to exclude them or weight them differently, and a reviewer must
  be able to see how much of the network we invented. This is a rule-1 boundary: connectors are a
  documented modelling assumption, and hiding them would make them fabricated data.
- What the strategy does at outfalls (where does water finally leave?).
- A validation plan whose checks can actually go RED. For each check, state in one line: "if this
  were broken I would observe ___". If the thing you would observe is produced by the same code path
  you are checking, you have built a mirror - find a different observable.`,
      { label: `design:${s.key}`, phase: 'Design', schema: DESIGN_SCHEMA }
    )
  )
)

const JUDGE_SCHEMA = {
  type: 'object',
  required: ['scores', 'winner', 'graft', 'reasoning'],
  properties: {
    scores: {
      type: 'array',
      items: {
        type: 'object',
        required: ['strategy_name', 'defensibility', 'buildable_in_a_day', 'failure_visibility', 'total'],
        properties: {
          strategy_name: { type: 'string' },
          defensibility: { type: 'number', description: '0-10: would a hydrologist accept it' },
          buildable_in_a_day: { type: 'number', description: '0-10' },
          failure_visibility: { type: 'number', description: '0-10: does it fail loudly or silently' },
          total: { type: 'number' },
          notes: { type: 'string' },
        },
      },
    },
    winner: { type: 'string' },
    graft: { type: 'array', description: 'best ideas from the losers to fold into the winner', items: { type: 'string' } },
    reasoning: { type: 'string' },
  },
}

const designsOk = designs.filter(Boolean)
const judges = await parallel(
  ['hydrology', 'software-risk', 'reviewer-scepticism'].map((lens) => () =>
    agent(
      `${RULES}

Judge three drain-network stitching strategies through the ${lens} lens specifically. Do not try to
be balanced across lenses - apply yours hard.

${lens === 'hydrology' ? 'Ask: would a drainage engineer accept this as a representation of a real stormwater network? Does water end up where it physically goes?' : ''}
${lens === 'software-risk' ? 'Ask: what breaks at 3am on day 3? Which strategy has the worst failure mode under a 4-day deadline with one GPU?' : ''}
${lens === 'reviewer-scepticism' ? 'Ask: which strategy would survive the independent review gate? Which one lets us silently invent the most network while looking rigorous? Note that ~370 components means a LOT of connectors get synthesised - quantify what fraction of total network length would be invented under each strategy and treat that as a first-class score.' : ''}

STRATEGIES:
${JSON.stringify(designsOk, null, 1).slice(0, 50000)}

Score each on defensibility, buildability in one day, and failure visibility. Pick a winner and name
which ideas from the losers must be grafted in.`,
      { label: `judge:${lens}`, phase: 'Design', schema: JUDGE_SCHEMA }
    )
  )
)

const judgesOk = judges.filter(Boolean)
log(`Design panel judged by ${judgesOk.length} lenses. Winners: ${judgesOk.map((j) => j.winner).join(', ')}`)

const synthesis = await agent(
  `${RULES}\n${CONTRACTS_NOTE}

Synthesise ONE implementable stitching specification from three designs and three independent judge
panels.

DESIGNS:
${JSON.stringify(designsOk, null, 1).slice(0, 40000)}

JUDGEMENTS:
${JSON.stringify(judgesOk, null, 1).slice(0, 30000)}

Produce a single specification precise enough that an implementer needs no further decisions. Take
the winner, graft the runners-up's best ideas, and where the judges disagree, resolve it explicitly
and say why.

Include a hard requirement: the artefact must report what FRACTION of total network length is
synthesised connector versus observed reach, and that number must appear in the manifest. If we are
inventing more than we observed, everyone downstream needs to know that from the artefact itself,
not from a paragraph in a writeup.`,
  { label: 'synthesis:stitching-spec', phase: 'Design' }
)

// ---------------------------------------------------------------- Phase: Build
phase('Build')
log('Building the graph. Four components in a pipeline; each verifies as soon as it lands.')

const BUILD_SCHEMA = {
  type: 'object',
  required: ['component', 'files_written', 'summary', 'realized_state', 'deviations'],
  properties: {
    component: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    realized_state: {
      type: 'object',
      description: 'V1: measured properties of what is actually on disk, not what was intended',
      additionalProperties: true,
    },
    deviations: {
      type: 'array',
      description: 'anything that departs from the stated objective - for owner adjudication, NOT to act on',
      items: { type: 'string' },
    },
    blockers: { type: 'array', items: { type: 'string' } },
  },
}

const BUILD_UNITS = [
  {
    key: 'loader',
    task: `Build src/jaladhar/drainage/loader.py: robust KML ingestion for all three drain orders into
EPSG:32643 GeoDataFrames. Must recover tertiary_drains_2022.kml or report precisely why it cannot
(try ogr2ogr, fiona drivers, raw XML/KML parse, partial recovery of valid features). Must emit
geometry-quality diagnostics: self-intersections, zero-length, duplicates, endpoint multiplicity.
Typer CLI entry. Manifest at run start per rule 6.`,
  },
  {
    key: 'stitcher',
    task: `Build src/jaladhar/drainage/stitch.py implementing the synthesised stitching specification
exactly. Produces a connected directed graph. MUST tag every edge with edge_source in
{observed, synthesised} and record the synthesised fraction of total length in the manifest. MUST
determine flow direction per edge from the conditioned DEM and record how (endpoint elevation
difference, D8 agreement, or both) plus a per-edge direction_confidence.`,
  },
  {
    key: 'capacity',
    task: `Build src/jaladhar/drainage/capacity.py assigning cross-section and hydraulic capacity per
edge using the CITED design-standard rule that WF-0 research produced. THIS IS THE RULE-1 BOUNDARY OF
THIS WORKFLOW. Every edge must carry capacity_basis holding an actual citation string, plus
capacity_low/capacity_high defining a sensitivity band. If the research did not yield a citable rule,
STOP and report - do not pick plausible numbers. Also compute upstream contributing area per edge
from the DEM flow accumulation, since the rule is keyed on it.`,
  },
  {
    key: 'writer',
    task: `Build src/jaladhar/drainage/graph_io.py writing the artefact exactly to the frozen
drain-graph contract: GeoPackage layers plus JSON adjacency, with a manifest carrying every V8
guarantee the contract names. Include the reader the coupling will use, with load-time assertions on
those guarantees. Per V8 the assertions must be able to go red - include a test that mutates a
manifest field and shows the reader rejecting it.`,
  },
]

const built = await pipeline(
  BUILD_UNITS,
  (u) =>
    agent(
      `${RULES}\n${CONTRACTS_NOTE}

BUILD TASK: ${u.key}

${u.task}

STITCHING SPECIFICATION (authoritative for this workflow):
${String(synthesis).slice(0, 30000)}

You OWN src/jaladhar/drainage/ and tests/drainage/. Do not modify any other directory; other agents
own them concurrently. Write real, runnable, type-hinted code and actually execute it against the
real data before reporting. "Shipped" is not "executed" - per V12 nothing counts until it has run
end to end at toy scale.

Report REALIZED state: what the files on disk actually contain after you ran it, measured.`,
      { label: `build:${u.key}`, phase: 'Build', schema: BUILD_SCHEMA }
    ),
  (result, orig) =>
    agent(
      `${RULES}

VERIFY the ${orig.key} component against realized state. You did not write it; do not trust it.

REPORTED:
${JSON.stringify(result, null, 1).slice(0, 24000)}

Independently verify by RUNNING things, not by reading claims:
- Do the files exist, import, and execute? Run them.
- Does the reported realized state match what you measure yourself? Re-measure it.
- V2: for each claim, name what you would observe if it were false, then check whether that thing
  would actually change. If it is produced by the same code path, it is a mirror, not a check.
- Is any number in the output untraceable to data on disk or a citation?
- For the capacity component specifically: is EVERY capacity value backed by a real citation, or did
  a plausible-looking number get typed in? This is the single highest-risk rule-1 boundary in the
  sprint. Check it hard.

Report CONFIRMED or REFUTED per claim with your own evidence.`,
      { label: `verify:${orig.key}`, phase: 'Verify' }
    )
)

// ------------------------------------------------------------- Phase: RedTest
phase('RedTest')
log('V5: every invariant must be demonstrated red under a deliberate mutation before it counts as a check.')

const INVARIANTS = [
  { key: 'connectivity', claim: 'the stitched graph has exactly one outfall-reachable component containing >95% of total reach length' },
  { key: 'direction-monotone', claim: 'every observed edge flows downhill in the conditioned DEM, or is flagged direction_confidence=low with a stated reason' },
  { key: 'no-cycles', claim: 'the directed graph is acyclic apart from explicitly flagged flat reaches' },
  { key: 'capacity-cited', claim: 'every edge carries a non-empty capacity_basis citation and a capacity_low<capacity_high band' },
  { key: 'synthesised-visible', claim: 'the synthesised fraction of total length is recorded in the manifest and every synthesised edge is tagged' },
  { key: 'crs-grid-seam', claim: 'graph geometry is EPSG:32643 and every node maps to a valid cell of the solver grid' },
  { key: 'mass-conservable', claim: 'total edge capacity into any node is finite, positive, and never NaN/inf' },
]

const redTests = await pipeline(
  INVARIANTS,
  (inv) =>
    agent(
      `${RULES}

Write an invariant test for: "${inv.claim}"

Then - and this is the part that matters - DEMONSTRATE IT RED. Deliberately mutate the artefact or
the producing code so the invariant is violated, run the test, and capture the failure output. Then
revert the mutation and show it green.

Record the mutation next to the test as a comment, exactly as the existing suite does (find the
convention first; WF-0's recon mapped it).

V7: state the scope your test actually exercises beside the scope the invariant claims. If your test
covers 3 edges and the invariant claims all 1033, rename it PARTIAL and add a BLOCKED test naming
what would close the gap - so the shortfall appears in test output, not only in a status report.

A test you have never seen fail is a claim, not a check. Do not report green without the red.

You own tests/drainage/ only.`,
      { label: `redtest:${inv.key}`, phase: 'RedTest' }
    ),
  (t, inv) =>
    agent(
      `${RULES}

Adversarially audit this invariant test for VACUITY. Three vacuous tests shipped past a list built
specifically to exclude them, so assume this one is vacuous until proven otherwise.

INVARIANT: ${inv.claim}
TEST AS BUILT:
${String(t).slice(0, 20000)}

Attack:
1. Was it actually demonstrated red, with real captured failure output? Re-run the mutation yourself.
2. Could it pass trivially? The Phase 1 precedent: "outfall flux is outward or zero" passes on an
   all-zero sealed boundary. The Phase 2 precedent: "Courant <= alpha" where alpha IS the selected
   value definitionally. Find this test's equivalent.
3. Scope vs claim (V7): what domain does it actually run over versus what it asserts? Compute both
   numbers.
4. Is the observable produced by the same code path being checked (a mirror)?

Verdict: SOUND, VACUOUS, or PARTIAL - with the specific reason and the fix.`,
      { label: `audit:${inv.key}`, phase: 'Verify' }
    )
)

// --------------------------------------------------------------- Phase: Verify
phase('Verify')

const finalCheck = await parallel([
  () =>
    agent(
      `${RULES}

END-TO-END verification of the drain graph artefact against REALIZED state only. Load the actual
artefact from disk and measure. Report, with numbers:
- node count, edge count, total length, observed vs synthesised length and the synthesised FRACTION
- connected components after stitching (was 370 before - what is it now?)
- how many edges have direction_confidence low/medium/high
- capacity distribution by drain order, and how many distinct capacity_basis citations appear
- how many nodes fall outside the solver grid
- whether the manifest carries every V8 guarantee the contract names
Then answer: is this a usable representation of Bengaluru's stormwater network, or does it look like
a plausible artefact that would not survive a hydrologist's questions? Be blunt.`,
      { label: 'verify:artefact-realized', phase: 'Verify' }
    ),
  () =>
    agent(
      `${RULES}

RULE-1 AUDIT. Your only job is to find fabricated data in the drain graph artefact.

Every number in that artefact came from somewhere. For each attribute - capacity, width, depth,
invert, Manning's n, direction, connector geometry - trace it to either (a) a measured value in
data/raw/, (b) a computation from the DEM, or (c) a cited external standard with the citation
present as data.

Anything in category (d) - a plausible number with no traceable origin - is a rule-1 violation and
you report it immediately and specifically.

Pay particular attention to the connectors: ~370 components had to be joined, so a large fraction of
the network may be synthesised. Quantify it exactly. If synthesised length exceeds observed length,
say so as the headline - that is a finding the owner needs before the presentation, not after.`,
      { label: 'verify:rule1-audit', phase: 'Verify' }
    ),
  () =>
    agent(
      `${RULES}

READINESS CHECK for WF-2 (the coupling). You are the consumer; the graph is the producer.

Load the artefact using the reader and answer:
- Can a 2D solver, given a surface cell, find the drain node it should discharge into? How fast?
- Given a node, can it route downstream and find where surcharge would emerge?
- Are capacities in units the solver can use directly (m3/s? m/s depth-equivalent?) or is a
  conversion needed - and is that conversion specified anywhere?
- Does the artefact expose everything the frozen coupling contract requires? Name any gap.
- What is the per-timestep cost of the graph operations at 359,841 steps? If it is not O(1) per node
  with precomputed indices, the coupling will be unusably slow - say so now.

This is a V8 seam check: assert the producer's manifest guarantees from the consumer's side and
report anything that would only be discovered at runtime on day 2.`,
      { label: 'verify:wf2-readiness', phase: 'Verify' }
    ),
])

log('WF-1 complete. Read the rule-1 audit and the synthesised fraction BEFORE launching WF-2.')

return {
  stitching_spec: synthesis,
  designs: designsOk,
  judgements: judgesOk,
  built: built.flat().filter(Boolean),
  red_tests: redTests.flat().filter(Boolean),
  verification: finalCheck.filter(Boolean),
  next: 'Owner reads the rule-1 audit + synthesised fraction. If acceptable, commit the artefact and launch WF-2 (wf2-coupling.js).',
}
