export const meta = {
  name: 'wf2-coupling',
  description: 'Day 2 - THE BIG ONE. Build the surface<->drain coupling with surcharge return: design panel, implementation, red-tested invariants, toy-scale execution, and loop-until-dry adversarial bug hunt',
  whenToUse: 'After WF-1 produces a validated drain graph. This is the research contribution of SIH 26085 and the most likely fix for the Phase 3 depth deficit.',
  phases: [
    { title: 'Design', detail: 'four independent coupling schemes, judged by three lenses' },
    { title: 'Spec', detail: 'synthesise one implementable specification' },
    { title: 'Build', detail: 'implement exchange, routing, surcharge, solver integration' },
    { title: 'RedTest', detail: 'nine invariants, each demonstrated red under mutation (V5)' },
    { title: 'Smoke', detail: 'V12 toy-scale end-to-end execution before any GPU hour is spent' },
    { title: 'BugHunt', detail: 'loop-until-dry adversarial finders with perspective-diverse verification' },
  ],
}

const REPO = '/home/darshil/Desktop/sih/clginternal'
const PY = REPO + '/.venv/bin/python'

const RULES = `
BINDING RULES (${REPO}/CLAUDE.md is authoritative - read it first).

THE TWO THAT MATTER MOST HERE:
- NO PLACEHOLDER PHYSICS. Do not "approximate" surcharge with a blur, a distance transform, or a
  fudge that makes output look plausible. If the coupling is not ready, it produces nothing. A demo
  built on invented physics is worthless.
- NO FABRICATED DATA. Every coefficient in an exchange equation carries a citation or it does not
  ship.

ALSO BINDING:
- Rule 6: manifest written AT RUN START (git SHA, config snapshot, status "running"), updated in
  place on completion. Three consecutive provenance casualties earned this rule.
- Rule 7: _resolve_config() touches EVERY key the run will need, including post-simulation reporting
  and output paths, and fails in the first second with an aggregated error listing all missing keys.
  A KeyError in write_depth_series() destroyed 37.8 minutes of GPU because that line only runs after
  a complete simulation.
- V1: realized state, never declared state.
- V5: every invariant demonstrated RED under deliberate mutation, mutation recorded beside it.
- V7: state the scope a check runs at beside the scope it claims.
- V8: producer writes guarantees into the manifest; consumer asserts them at load.
- V12: no GPU hours before an end-to-end toy-scale execution. "Shipped" is not "executed" - this has
  already caught two never-executed modules in this project.
- R1: label claims FINDING or READING.
- Implementation agents may fix bugs on their own initiative. They may NEVER act on deviations from
  stated objectives - those go to the owner in a consolidated report.
- Style: python 3.11, type hints, ruff + black, typer CLI, YAML config. Interpreter ${PY}.
- Git: explicit staging by path, never 'git add -A'. No self-attribution footers.

WHY THIS WORKFLOW EXISTS - the measured context:
- Phase 3 failed on depth: 0/16 points in band, RMSE 0.9232 m against ~0.25 m bands.
- It is STRUCTURAL, not parametric: the calibration loop moved the depth term 1.3%, and every
  parametric lever extrapolated to its bound buys ~0.11 of loss against the 1.62 needed.
- In replay #2 the drains removed 79.16 Mm3 = 25.57% of total inflow ONE-WAY, with no return path.
  Real drains surcharge that water back onto the street, and that IS the flooding.
- Corroboration: of three coordinates the calibration accepted, one was drain -0.25 (reduce capacity).
- LABEL: "surcharge closes the depth gap" is a READING, not a finding. It is the leading hypothesis
  and this workflow TESTS it. Nothing may be built as though it were already true, and an honest
  negative result is a valid and valuable outcome.
`

const CONTRACTS = `
The coupling interface contract was frozen by WF-0 (configs/contracts/). The drain graph artefact and
its reader were produced by WF-1. Build exactly to both. Report contract problems as deviations for
owner adjudication; do not silently reinterpret them.

MEASURED FACTS FROM THE WF-0 RECON AND THE WF-1/WF-3 RUNS (2026-08-25). These are realized state,
verified against disk. Build against them, not against assumptions:

DRAIN CONSUMPTION AS IT EXISTS TODAY (the code you are replacing):
- Loaded ONLY via state.load_domain() from data/processed/buffered/drain_capacity.tif
  (state.py:240-247). The config key data/processed/drain_capacity.tif is NOT what is read.
- Converted ONCE at static-field build: MM_PER_HR_TO_M_PER_S = 1/1000/3600 (state.py:35) into
  StaticFields.drain_cap_m_s (m/s). The hot path multiplies by dt only.
- Applied at acc.py:312-316, AFTER the flux update and AFTER the negative-depth guard, in fixed
  order drain then infiltration:
      drained = torch.minimum(static.drain_cap_m_s * dt, h_new); h_new = h_new - drained
  The min-cap means drainage can never invert depth.
- Mass budget: drained.sum(float64) at acc.py:337, times cell_area at mass.py:104.

THREE THINGS THAT WILL BITE YOU IF YOU ASSUME OTHERWISE:
1. sinks.drain.enabled GATES ONLY A MANIFEST ASSERTION (state.py:275). The raster is read and
   applied UNCONDITIONALLY. Setting it false does NOT disable drainage. The coupling must remove the
   legacy sink IN CODE at acc.py:312-316. If you assume the flag works you get DOUBLE COUNTING -
   legacy sink and coupled capture both removing the same water.
2. The mass-residual tolerance is 1e-3 relative, but replay #2 realized 1.79e-05 - the guard is 56x
   looser than reality, so a coupling defect leaking 0.1% of mass passes silently. Check against the
   realized historical residual, not the tolerance.
3. drain_capacity.tif has min 1.94e-31 with ALL 12,728,415 cells non-zero. "Capacity zero everywhere"
   for the uncoupled-equivalence invariant is NOT reachable from the existing raster; zero it
   explicitly.
Also: StaticFields is a frozen dataclass and drain_cap_m_s is a live calibration parameter, so any
coupling modification must be OUT-OF-PLACE.

THE GRAPH YOU ARE COUPLING TO (as WF-1 left it, status stopped_owner_adjudication):
- Synthesised connector trajectories total 2,732.86 km against 767.3 km observed (3.56x), BUT unique
  footprint is only 364.5-515.5 km (0.48-0.67x observed) because connectors converge into shared
  corridors at ~7.5 crossings per cell. TREAT THIS AS A DEFECT TO HANDLE, NOT A STATISTIC: many
  parallel connector edges represent the SAME physical flow path, so naive routing double-counts
  conveyance. State how your scheme avoids that.
- capacity_status is blocked_missing_design_intensity - ZERO hydraulic numbers were written, per
  rule 1. If capacity is still absent when you start, you cannot compute surcharge and you say so
  rather than inventing an intensity.
- Whitebox fill was found VALUE-NONDETERMINISTIC (3 runs, 3 fingerprints) and was replaced with an
  in-repo deterministic priority-flood. Do not reintroduce a nondeterministic terrain step.

THE BASELINE YOU MUST BEAT (WF-3, uncoupled, measured on replay #2):
- G1 segment-mediated: 182/399 = 45.61%, seeded toroidal null (10,000 draws, seed 42) mean rate
  32.34%, lift 1.41. Both signed conditions unmet - as EXPECTED for an uncoupled baseline.
- Note the metric gap: replay #2's manifest reports 239/399 = 59.90% POINT-mediated at 0.10 m. The
  segment-mediated G1 rule (D=0.15 m, F=20%, N=3) is structurally harsher and loses 57 hits. Compare
  coupled-vs-uncoupled on the SAME metric; never mix the two.
`

// --------------------------------------------------------------- Phase: Design
phase('Design')
log('WF-2: four independent coupling designs. This is the research contribution - the design decision is not a detail.')

const DESIGN_SCHEMA = {
  type: 'object',
  required: ['scheme_name', 'exchange_equations', 'surcharge_mechanism', 'dt_constraint', 'mass_accounting', 'assumptions', 'failure_modes', 'falsifier'],
  properties: {
    scheme_name: { type: 'string' },
    exchange_equations: { type: 'string', description: 'the actual equations, with cited coefficients' },
    surcharge_mechanism: { type: 'string', description: 'exactly how water returns from an over-capacity node to the surface' },
    dt_constraint: { type: 'string', description: 'what the coupling does to the adaptive timestep' },
    mass_accounting: { type: 'string', description: 'where capture and surcharge enter the mass budget so it still closes' },
    assumptions: { type: 'array', items: { type: 'string' } },
    failure_modes: { type: 'array', items: { type: 'string' } },
    falsifier: { type: 'string', description: 'R5: what observation would prove this scheme wrong' },
    citations: { type: 'array', items: { type: 'string' } },
    estimated_hours: { type: 'number' },
  },
}

const SCHEMES = [
  {
    key: 'swmm-coupled',
    angle: `Couple to EPA SWMM via pyswmm. The drain graph becomes a SWMM .inp; the 2D solver exchanges
with SWMM nodes each coupling step. SWMM handles routing and surcharge natively and is the reference
implementation an NCMRWF panel would recognise. Address: .inp generation from our graph, the
step-synchronisation between an adaptive-dt GPU solver and SWMM's own timestep, per-step IPC cost at
~360k steps, and what happens if pyswmm will not install.`,
  },
  {
    key: 'inhouse-1d-kinematic',
    angle: `Implement a simplified 1D kinematic-wave router over the drain graph, in-repo, on GPU
alongside the 2D solver. Capacity-limited flow along edges, storage at nodes, surcharge when node
storage exceeds capacity. Address: numerical stability against the explicit 2D scheme, why kinematic
wave is or is not adequate for a surcharging network, and what it cannot represent.`,
  },
  {
    key: 'weir-orifice-exchange',
    angle: `Focus on the EXCHANGE as the physics and keep routing minimal: inlet capture as weir flow
at low surface depth transitioning to orifice flow when submerged, with a node continuity equation
and instantaneous surcharge return. Cite the standard weir and orifice coefficients. Address: what
this deliberately does not model, and whether minimal routing is defensible.`,
  },
  {
    key: 'storage-cell-network',
    angle: `Treat each drain node as a storage cell with a stage-storage relation, edges as
capacity-limited links, and let the same ACC scheme the 2D solver already uses run on the 1D network -
one solver, two topologies. Address: reusing the existing verified numerics, the seam between D4
surface and graph topology, and whether stage-storage can be derived without measured cross-sections.`,
  },
]

const designs = await parallel(
  SCHEMES.map((s) => () =>
    agent(
      `${RULES}\n${CONTRACTS}

DESIGN TASK - coupling scheme "${s.key}".

${s.angle}

Argue this scheme as well as it can be argued. Do not hedge toward the others. Be ruthlessly honest
about where it breaks.

MANDATORY:
- Write the ACTUAL equations, with every coefficient carrying a citation. An uncited coefficient is
  fabricated data.
- Specify the surcharge mechanism concretely. This is the whole point: a scheme where no node ever
  surcharges fails gate G2 regardless of its other scores.
- State what the coupling does to the adaptive dt. The 2D solver runs dt~0.48 s flat over 359,841
  steps; if your exchange forces dt down by 10x the sprint is over. Compute the constraint.
- State exactly where capture and surcharge enter the mass budget. Replay #2 closes to 1.7e-05 of
  inflow and it must still close.
- R5: pre-register your falsifier. What observation would prove this scheme wrong?

ATTACK THIS FRAMING (do not accept it): the reviewer believes surcharge will close the depth deficit.
That is a READING with a small measured drain gradient (0.0239) arguing against it. Argue the case
that surcharge will NOT close the gap and say what your scheme would show if so.`,
      { label: `design:${s.key}`, phase: 'Design', schema: DESIGN_SCHEMA }
    )
  )
)

const designsOk = designs.filter(Boolean)

const JUDGE_SCHEMA = {
  type: 'object',
  required: ['scores', 'winner', 'graft', 'kill_reasons'],
  properties: {
    scores: {
      type: 'array',
      items: {
        type: 'object',
        required: ['scheme_name', 'physical_fidelity', 'buildable_in_one_day', 'numerical_risk', 'reviewability', 'total'],
        properties: {
          scheme_name: { type: 'string' },
          physical_fidelity: { type: 'number' },
          buildable_in_one_day: { type: 'number' },
          numerical_risk: { type: 'number', description: '0-10, higher is SAFER' },
          reviewability: { type: 'number', description: 'would an MoES/NCMRWF panel recognise and accept it' },
          total: { type: 'number' },
          notes: { type: 'string' },
        },
      },
    },
    winner: { type: 'string' },
    graft: { type: 'array', items: { type: 'string' } },
    kill_reasons: { type: 'array', items: { type: 'string' } },
  },
}

const LENSES = [
  { key: 'hydraulics', ask: 'Would a drainage engineer accept these equations? Are the coefficients real and correctly applied? Does surcharge emerge where physics says it should?' },
  { key: 'numerics', ask: 'What does each scheme do to stability and to dt? Which one silently breaks mass conservation? The 2D scheme is explicit, D4, CFL-limited, adaptive dt ~0.48 s over 359,841 steps on 12.7M cells.' },
  { key: 'deadline-risk', ask: 'Four days, one RTX 4060, presentation on day 4. Which scheme is actually finishable? Which has a dependency that might not install? Which fails in a way we could not recover from on day 3?' },
  { key: 'reviewer', ask: 'Which scheme would survive the independent review gate? Which one lets us ship something that looks like physics but is a fudge? Which produces numbers we could not defend when asked where a coefficient came from?' },
]

const judged = await parallel(
  LENSES.map((l) => () =>
    agent(
      `${RULES}

Judge four surface<->drain coupling schemes through the ${l.key} lens ONLY. Apply your lens hard;
do not try to be balanced.

YOUR QUESTION: ${l.ask}

SCHEMES:
${JSON.stringify(designsOk, null, 1).slice(0, 60000)}

Score each, pick a winner, name what must be grafted from the losers, and list kill_reasons - any
scheme that should be eliminated outright and why.`,
      { label: `judge:${l.key}`, phase: 'Design', schema: JUDGE_SCHEMA }
    )
  )
)

const judgedOk = judged.filter(Boolean)
log(`Design panel: ${judgedOk.length} lenses judged. Winners: ${judgedOk.map((j) => j.winner).join(' | ')}`)

// ----------------------------------------------------------------- Phase: Spec
phase('Spec')

const spec = await agent(
  `${RULES}\n${CONTRACTS}

Synthesise ONE implementable coupling specification from four designs and four independent judge
panels. An implementer must need no further decisions after reading it.

DESIGNS:
${JSON.stringify(designsOk, null, 1).slice(0, 50000)}

JUDGEMENTS:
${JSON.stringify(judgedOk, null, 1).slice(0, 40000)}

Requirements:
- Take the winner; graft the runners-up's best ideas explicitly.
- Where lenses disagree, resolve it and say why the losing lens was outweighed. Do not average them.
- Include a FALLBACK: if the primary scheme's key dependency fails to install or the numerics blow
  up on day 2, what is the degraded-but-honest path? Name the trigger condition for switching, now,
  before anyone is invested.
- Include the exact module layout, function signatures, and config keys (rule 7: enumerate EVERY key
  the run will need, including post-simulation reporting and output paths).
- Include the nine invariants the RedTest phase will implement, each stated as a falsifiable claim.
- Pre-register the G2 anti-vacuity condition: what counts as "a node surcharged", how it is measured,
  and how a run where nothing surcharges is detected and reported as a FAILURE rather than a pass.`,
  { label: 'spec:coupling', phase: 'Spec' }
)

// ---------------------------------------------------------------- Phase: Build
phase('Build')
log('Implementing. Five units in a pipeline, each verified by an agent that did not write it.')

const BUILD_SCHEMA = {
  type: 'object',
  required: ['unit', 'files_written', 'executed', 'realized_state', 'deviations'],
  properties: {
    unit: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    executed: { type: 'boolean', description: 'V12: did you actually RUN it end to end, not just write it' },
    execution_evidence: { type: 'string', description: 'actual command and actual output' },
    realized_state: { type: 'object', additionalProperties: true },
    deviations: { type: 'array', items: { type: 'string' } },
    blockers: { type: 'array', items: { type: 'string' } },
  },
}

const UNITS = [
  { key: 'config', task: 'src/jaladhar/coupling/config.py - _resolve_config() touching EVERY key the coupled run will need including post-simulation reporting and output paths, failing in the first second with an AGGREGATED error listing all missing keys. This is rule 7 and it exists because a KeyError after a complete simulation destroyed 37.8 GPU-minutes. Write the red test that proves the aggregation works.' },
  { key: 'exchange', task: 'src/jaladhar/coupling/exchange.py - inlet capture and surcharge return between surface cells and drain nodes. Every coefficient cited. Vectorised for GPU; per-step cost must be O(nodes), not O(cells).' },
  { key: 'router', task: 'src/jaladhar/coupling/router.py - routing along the drain graph with capacity limits and node storage. Must produce, per node per step, whether it is surcharging and by how much.' },
  { key: 'integration', task: 'src/jaladhar/coupling/solver_hook.py - integrate exchange+router into the existing 2D solver timestep, replacing the one-way drain sink. MUST preserve mass-budget closure (replay #2 closes to 1.7e-05) and MUST add capture/surcharge as explicit budget terms. Do not modify solver internals beyond the documented hook point; report anything else as a deviation.' },
  { key: 'diagnostics', task: 'src/jaladhar/coupling/diagnostics.py - per-run surcharge diagnostics: which nodes surcharged, when, how much volume, and the mapping from surcharge events to nearby ground-truth points. This is what gate G2 is scored from, so it must be able to report "no node ever surcharged" loudly.' },
]

const built = await pipeline(
  UNITS,
  (u) =>
    agent(
      `${RULES}\n${CONTRACTS}

BUILD UNIT: ${u.key}
${u.task}

AUTHORITATIVE SPECIFICATION:
${String(spec).slice(0, 45000)}

You OWN src/jaladhar/coupling/ and tests/coupling/. Other agents own other directories concurrently -
do not touch them.

V12 IS NOT OPTIONAL: write the code, then ACTUALLY RUN IT at toy scale against real data and paste
the real command and real output into execution_evidence. Reporting "shipped" without "executed" has
already burned this project twice. If it does not run, that is the report.`,
      { label: `build:${u.key}`, phase: 'Build', schema: BUILD_SCHEMA }
    ),
  (r, u) =>
    agent(
      `${RULES}

INDEPENDENT VERIFICATION of coupling unit "${u.key}". You did not write it. Trust nothing.

REPORTED:
${JSON.stringify(r, null, 1).slice(0, 26000)}

Verify by EXECUTING:
- Run it yourself. Does the claimed execution_evidence reproduce?
- V1: measure realized state independently; does it match the report?
- Check the specific failure classes this repo has actually hit:
  * in-place tensor writes that silently sever a gradient or a boundary flux (the Phase 2 incident:
    the rule was in the docstring three paragraphs above the violating line)
  * loop-variable shadowing (the 'for p in (basin_path, elev_path)' incident shadowed SolverParams;
    dry start never entered the branch, tests stayed green, state was wrong downstream)
  * config keys that only resolve on the success path
  * uncited coefficients
- Does mass balance still close? Compute it, do not read it.

Report CONFIRMED or REFUTED per claim, with your own evidence.`,
      { label: `verify:${u.key}`, phase: 'Build' }
    )
)

// ------------------------------------------------------------- Phase: RedTest
phase('RedTest')

const INVARIANTS = [
  { key: 'mass-closes', claim: 'coupled mass budget closes to <= 1e-4 relative, with capture and surcharge as explicit terms' },
  { key: 'surcharge-fires', claim: 'under a storm exceeding network capacity, at least one node surcharges and the volume is non-zero (G2 ANTI-VACUITY - this is the test that must not be able to pass trivially)' },
  { key: 'capture-bounded', claim: 'inlet capture never exceeds available surface water in a cell, and never exceeds node capacity' },
  { key: 'no-negative-depth', claim: 'no surface cell reaches negative depth through exchange' },
  { key: 'surcharge-conserves', claim: 'volume surcharged to the surface exactly equals volume removed from node storage' },
  { key: 'dt-not-collapsed', claim: 'coupling does not force dt below 50% of the uncoupled schedule over the same window' },
  { key: 'graph-seam', claim: 'V8: the solver asserts the drain-graph manifest guarantees at load and rejects a mutated manifest' },
  { key: 'config-aggregates', claim: 'rule 7: a run missing three config keys fails in the first second naming ALL THREE, not the first one' },
  { key: 'uncoupled-equivalence', claim: 'with capacity set to zero everywhere, the coupled solver reproduces the uncoupled result to within solver tolerance' },
]

const redTests = await pipeline(
  INVARIANTS,
  (inv) =>
    agent(
      `${RULES}

Write an invariant test for: "${inv.claim}"

Then DEMONSTRATE IT RED. Mutate the code or data so it is violated, run it, capture the real failure
output, revert, show green. Record the mutation beside the test.

V2 - before writing the check, state in one line: "if this were broken I would observe ___". If that
observable is produced by the same code path you are checking, you built a mirror. Find another
observable or say you cannot.

V7 - state the scope your test exercises beside the scope the invariant claims, with both numbers. If
there is a gap, rename it PARTIAL and add a BLOCKED test naming what would close it, so the shortfall
shows up in test output rather than in a status report.

${inv.key === 'surcharge-fires' ? 'THIS ONE IS THE ANTI-VACUITY TEST AND IT IS THE MOST IMPORTANT IN THE SPRINT. The Phase 1 precedent: "outfall flux is outward or zero" passes trivially on a sealed boundary, so the severe silent failure it existed to catch would have reddened nothing. Design this test so that a coupling which never surcharges FAILS LOUDLY. Then prove it by disabling surcharge and showing red.' : ''}

You own tests/coupling/ only.`,
      { label: `redtest:${inv.key}`, phase: 'RedTest' }
    ),
  (t, inv) =>
    agent(
      `${RULES}

VACUITY AUDIT. Assume this test is vacuous until proven otherwise - three vacuous tests already
shipped past a list built specifically to exclude them, and none were found internally.

INVARIANT: ${inv.claim}
TEST:
${String(t).slice(0, 22000)}

1. Re-run the claimed red mutation yourself. Did it actually go red?
2. Can it pass trivially? Precedents to pattern-match against: all-zero satisfies "flux outward or
   zero"; "Courant <= alpha" where alpha is definitionally the selected value; "finite non-zero
   gradients" run on 0.41% of duration and 1/455 of the domain.
3. Compute scope-run versus scope-claimed as two numbers.
4. Is the observable a mirror of the code under test?

Verdict SOUND / VACUOUS / PARTIAL, with the specific defect and the fix.`,
      { label: `audit:${inv.key}`, phase: 'RedTest' }
    )
)

// --------------------------------------------------------------- Phase: Smoke
phase('Smoke')
log('V12 gate: end-to-end toy-scale execution BEFORE any GPU hour is authorised.')

const smoke = await agent(
  `${RULES}

V12 SMOKE - the gate that stands between this code and the owner's scarce GPU hours. There is ONE
RTX 4060 and roughly six to eight slots left before the presentation. Your job is to make sure the
first real slot is not wasted.

Run the FULL coupled machinery end to end at toy scale on REAL data: config resolution -> drain graph
load with V8 assertions -> forcing -> initial condition -> coupled timestep loop -> exchange ->
routing -> surcharge -> mass budget -> diagnostics -> manifest terminal state.

Use a small subdomain and a short window so it completes in minutes. Then report:
- The exact command and its full real output.
- Whether every stage executed, named individually.
- Whether the manifest was written at START with status "running" and updated in place at the end.
- Whether ANY node surcharged (if none did, say so as the headline - it may be correct for a small
  quiet window, but the owner must know before spending 10 GPU-hours).
- The realized mass-budget closure.
- Measured cost per step, extrapolated to (a) a 3-hour full-domain run and (b) a 6-hour corridor
  subdomain run, with the arithmetic shown.

If ANYTHING fails, that is the report. Do not paper over it. Two never-executed modules have already
been caught by exactly this check.`,
  { label: 'smoke:toy-scale-e2e', phase: 'Smoke' }
)

// ------------------------------------------------------------- Phase: BugHunt
phase('BugHunt')
log('Loop-until-dry adversarial bug hunt. Simple counters miss the tail; we stop after two dry rounds.')

const BUG_SCHEMA = {
  type: 'object',
  required: ['bugs'],
  properties: {
    bugs: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'file', 'description', 'failure_scenario', 'severity'],
        properties: {
          id: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'number' },
          description: { type: 'string' },
          failure_scenario: { type: 'string', description: 'concrete inputs -> wrong output' },
          severity: { type: 'string', enum: ['critical', 'major', 'minor'] },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['refuted', 'reasoning'],
  properties: {
    refuted: { type: 'boolean', description: 'true if this is NOT a real bug' },
    reasoning: { type: 'string' },
    evidence: { type: 'string' },
  },
}

const FINDERS = [
  { key: 'mass', ask: 'Hunt for mass-conservation defects. Every place water is created, destroyed, double-counted, or lost between the surface, the exchange, node storage, and the surcharge return.' },
  { key: 'inplace', ask: 'Hunt for in-place tensor mutation that silently severs a gradient or a flux. This repo has already had exactly this bug in _boundary_outflux, with the prohibition written in the docstring three paragraphs above the violating line.' },
  { key: 'shadowing', ask: 'Hunt for shadowed variables, mis-scoped loops, and branches that never execute. This repo has already had "for p in (basin_path, elev_path)" shadow SolverParams, so a dry start never entered its branch while tests stayed green.' },
  { key: 'units', ask: 'Hunt for unit and sign errors across the coupling boundary: m vs m3, per-step vs per-second, depth vs volume, capture positive vs negative, and CRS/grid index mismatches.' },
  { key: 'config', ask: 'Hunt for config keys that resolve only on the success path, and for any key used after the simulation completes that _resolve_config does not touch. Rule 7 exists because of exactly this.' },
  { key: 'numerics', ask: 'Hunt for stability defects: dt constraints violated by the exchange, stiff node storage, oscillation between capture and surcharge in adjacent steps, NaN/inf propagation.' },
  { key: 'vacuity', ask: 'Hunt for code paths that CANNOT fire - a surcharge branch unreachable under any realistic input, a capacity comparison that is always false, a diagnostic that would report success on an empty result set.' },
]

const seen = new Set()
const confirmed = []
let dry = 0
let round = 0

while (dry < 2 && round < 5) {
  round += 1
  const found = (
    await parallel(
      FINDERS.map((f) => () =>
        agent(
          `${RULES}

ADVERSARIAL BUG HUNT round ${round}, lens: ${f.key}.

${f.ask}

Read the coupling implementation under src/jaladhar/coupling/ and its integration into the solver.
RUN things to confirm - a bug you cannot demonstrate is a guess.

${confirmed.length ? 'ALREADY CONFIRMED (do not re-report these, find NEW ones):\n' + confirmed.map((b) => '- ' + b.file + ': ' + b.description).join('\n').slice(0, 8000) : ''}

Report only defects you can describe with concrete inputs producing concrete wrong output.`,
          { label: `hunt-r${round}:${f.key}`, phase: 'BugHunt', schema: BUG_SCHEMA }
        )
      )
    )
  )
    .filter(Boolean)
    .flatMap((r) => r.bugs || [])

  const fresh = found.filter((b) => {
    const k = `${b.file}:${b.line || 0}:${(b.description || '').slice(0, 60)}`
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })

  if (!fresh.length) {
    dry += 1
    log(`Round ${round}: nothing new. Dry streak ${dry}/2.`)
    continue
  }
  dry = 0
  log(`Round ${round}: ${fresh.length} candidate defects, verifying each with 3 diverse lenses.`)

  const judgedBugs = await parallel(
    fresh.map((b) => () =>
      parallel(
        ['does-it-reproduce', 'is-it-actually-wrong', 'does-it-matter-at-scale'].map((lens) => () =>
          agent(
            `${RULES}

REFUTE this claimed defect through the "${lens}" lens. Default to refuted=true if uncertain - a
plausible-but-wrong finding wastes a GPU slot we cannot spare.

CLAIM: ${b.description}
FILE: ${b.file}${b.line ? ':' + b.line : ''}
SCENARIO: ${b.failure_scenario}

${lens === 'does-it-reproduce' ? 'Actually construct the scenario and run it. Does the described wrong output occur?' : ''}
${lens === 'is-it-actually-wrong' ? 'Is the described behaviour genuinely incorrect, or is it correct behaviour the reporter misread? Check the spec and the physics.' : ''}
${lens === 'does-it-matter-at-scale' ? 'Would this change any number in a real coupled run on the real domain? A defect with no observable consequence at scale is not worth a fix before the presentation.' : ''}`,
            { label: `refute:${lens}`, phase: 'BugHunt', schema: VERDICT_SCHEMA }
          )
        )
      ).then((vs) => {
        const votes = vs.filter(Boolean)
        const survives = votes.filter((v) => !v.refuted).length >= 2
        return { bug: b, survives, votes }
      })
    )
  )

  for (const j of judgedBugs.filter(Boolean)) if (j.survives) confirmed.push(j.bug)
  log(`Round ${round}: ${confirmed.length} confirmed defects total.`)
}

const fixes = confirmed.length
  ? await parallel(
      confirmed
        .filter((b) => b.severity !== 'minor')
        .map((b) => () =>
          agent(
            `${RULES}

FIX this confirmed defect, then prove the fix.

FILE: ${b.file}${b.line ? ':' + b.line : ''}
DEFECT: ${b.description}
SCENARIO: ${b.failure_scenario}

1. Write a test that reproduces it and SHOW IT RED.
2. Fix the code.
3. Show the test green and the rest of the suite still green.
4. V6: state explicitly which of {code, test, spec} was wrong and why. Editing a test until it agrees
   with the code is how a spec violation becomes permanent and invisible.`,
            { label: `fix:${(b.file || '').split('/').pop()}`, phase: 'BugHunt' }
          )
        )
    )
  : []

const completeness = await agent(
  `${RULES}

COMPLETENESS CRITIC. Everything above has been built and hunted. Your job is to name what is MISSING.

CONFIRMED DEFECTS: ${confirmed.length}
SMOKE RESULT:
${String(smoke).slice(0, 20000)}

Ask, specifically:
- Which coupling behaviour has never been executed even once?
- Which invariant was claimed but never demonstrated red?
- Which claim in the spec has no corresponding test?
- Which failure mode named in the DESIGN phase was never checked for?
- Is there any path by which a coupled run could produce plausible-looking output while the surcharge
  mechanism silently never fires? Trace it concretely - this is the failure that would survive to the
  presentation and be found in the room.
- What would you need to see before spending a 10.6 GPU-hour slot on this?

Whatever you find here becomes the next round of work. Be exhaustive and be blunt.`,
  { label: 'critic:completeness', phase: 'BugHunt' }
)

log('WF-2 complete. Owner MUST read the smoke result and the completeness critic before authorising a GPU slot.')

return {
  spec,
  designs: designsOk,
  judgements: judgedOk,
  built: built.flat().filter(Boolean),
  red_tests: redTests.flat().filter(Boolean),
  smoke,
  confirmed_defects: confirmed,
  fixes: fixes.filter(Boolean),
  completeness_critic: completeness,
  rounds_run: round,
  next: 'Owner reviews smoke + critic. If green: GPU slot 2 = 6h corridor subdomain, uncoupled vs coupled. Then WF-3 (products) runs on CPU in parallel with GPU validation.',
}
