export const meta = {
  name: 'wf4-gate',
  description: 'Day 4: score gates G1-G5 mechanically against realized run output, adversarially verify every number, produce the V11 axis list and the presentation with full provenance',
  whenToUse: 'After a coupled GPU run has produced scoreable output. This is the review gate - it must not be skipped because the deadline is close.',
  phases: [
    { title: 'Score', detail: 'mechanical G1-G5 scoring from realized state only' },
    { title: 'Audit', detail: 'adversarial verification of every reported number' },
    { title: 'Axes', detail: 'V11 - what was closed, on which axis, and what remains open' },
    { title: 'Assemble', detail: 'presentation with every figure carrying its manifest path' },
  ],
}

const REPO = '/home/darshil/Desktop/sih/clginternal'
const PY = REPO + '/.venv/bin/python'

const RULES = `
BINDING RULES (${REPO}/CLAUDE.md is authoritative).

THIS WORKFLOW IS THE REVIEW GATE. CLAUDE.md is explicit that the independent review pass is a
PERMANENT gate at the end of every phase, not a one-off, and that its value comes specifically from
reviewers having no stake in the code passing. Two recorded incidents are why it does not get
graduated out of: a 25-invariant list built specifically to exclude vacuous tests still shipped one;
and the in-place-write incident, where the rule was in the plan, restated in the docstring three
paragraphs above the violating line, and violated anyway by the same context that wrote it. One from
outside, one from inside on a maximally-salient rule. The failure is STRUCTURAL, not attentional -
it is not fixed by more care, and confidence is the state in which it occurs.

The deadline is not a reason to soften this. A gate that passes because time ran out is not a gate.

- V1: score REALIZED state only - rasters and manifests on disk. A config value, a manifest field
  someone wrote by hand, or a variable assigned in a report are DECLARATIONS OF INTENT.
- V3: never verify against a baseline you produced or that can move.
- V7: state the scope each check runs at beside the scope it claims.
- V9: any number that lands in a doc or the presentation needs a saved script and a logged run
  behind it. A figure computed in a throwaway shell heredoc has WORSE provenance than the agent
  reports being audited with it - five reviewer figures failed to reproduce this way once already.
- V10: verifying a claim is not the same as checking that its stated derivation is right. A number
  can be traceable, correct, and labelled with a formula that does not produce it.
- V11: state which axis you closed an item on and name the axes you did not.
- R1: FINDING vs READING. R5: write what would falsify a verdict before issuing it.
- Rule 3: every numeric claim traceable to data/ or runs/.
- Interpreter ${PY}. Git: explicit staging by path, never 'git add -A'. No self-attribution footers.

THE SIGNED GATE (docs/SIH-26085-ALIGNMENT.md section 6, signed 2026-08-24 BEFORE any number existed
- do not move a threshold now that numbers exist):
  G1 street-level classification: per-segment hit rate >= 60% AND null lift >= 3.0x (N=10,000,
     seed recorded). BOTH conditions. No rounding argument.
  G2 surcharge mechanism: >= 50% of GT flood points on or adjacent to a drain reach reproduced as
     surcharge-driven, with volume traceable to a NAMED node. A run where no node ever surcharges
     FAILS G2 regardless of any other score. This is the anti-vacuity condition.
  G3 depth: per-segment bands in cm, >= 40% of held-out strict rows in band, reported under BOTH the
     3x3 local-max and exact-cell conventions. If the two disagree on the verdict, THE DISAGREEMENT
     IS THE REPORTED RESULT.
  G4 nowcast latency: <= 10 minutes wall clock for a 3-hour horizon, MEASURED not estimated.
  G5 extent: UNCLOSED by declaration - the SAR instrument was validated on lakes, not urban
     flooding, so no CSI is admissible as a gate number. Report it open; do not quietly omit it.
`

phase('Score')
log('WF-4: mechanical scoring from realized state. Five gates, scored independently and in parallel.')

const SCORE_SCHEMA = {
  type: 'object',
  required: ['gate', 'verdict', 'measured', 'scope_run', 'scope_claimed', 'evidence_paths'],
  properties: {
    gate: { type: 'string' },
    verdict: { type: 'string', enum: ['PASS', 'FAIL', 'UNCLOSED', 'BLOCKED'] },
    measured: { type: 'object', additionalProperties: true },
    scope_run: { type: 'string', description: 'V7: the domain the check actually exercised' },
    scope_claimed: { type: 'string', description: 'V7: the domain the gate asserts over' },
    evidence_paths: { type: 'array', items: { type: 'string' } },
    derivation: { type: 'string', description: 'V10: how each number was computed, so the derivation can be checked separately from the value' },
    caveats: { type: 'array', items: { type: 'string' } },
  },
}

const GATES = [
  { key: 'G1', task: 'Score per-segment hit rate against the 399 BBMP complaint points AND the null-model lift (N=10,000, seed recorded). BOTH conditions must be reported. Report the flooded-area fraction alongside, because lift is the guard against buying hit rate with area - replay #2 went 45.86% at 7.4% flooded (6.20x lift) to 59.90% at 14.2% flooded (4.22x lift), rate up and lift down.' },
  { key: 'G2', task: 'Score the surcharge mechanism. Which GT flood points sit on or adjacent to a drain reach (state the adjacency rule and report a BAND across at least two defensible rules, not a point estimate). For each, did a NAMED node surcharge, when, and what volume. If no node surcharged anywhere in the run, that is a FAIL and it is the headline of the whole gate - report it in the first sentence.' },
  { key: 'G3', task: 'Score depth as per-segment bands in cm against held-out strict rows, under BOTH the 3x3 local-max convention (primary) and exact-cell (declared sensitivity), per data/curation/goal_d_depth_convention.json. Report both counts AND both RMSEs - historically only the local-max RMSE was ever reported. If the conventions disagree on the verdict, report the disagreement as the result.' },
  { key: 'G4', task: 'MEASURE nowcast latency end to end for a 3-hour horizon: rainfall input to published depth product. Wall clock, measured on the real device, not extrapolated. Report the breakdown by stage so the bottleneck is visible, and state the domain/tiling configuration used - per V7, a latency measured on a subdomain does not claim the full domain.' },
  { key: 'G5', task: 'Report extent as UNCLOSED with the specific reason: Goal A validated the SAR instrument on lakes (open water, high contrast), and it is being used to judge urban street flooding at 0.10 m - a different detection problem (R2). State exactly what instrument validation would close this axis. Do NOT compute a CSI and present it as a gate number.' },
]

const scores = await parallel(
  GATES.map((g) => () =>
    agent(
      `${RULES}

SCORE GATE ${g.key}.

${g.task}

Score from REALIZED STATE ONLY - open the rasters and manifests and measure. Do not read a number
out of a summary someone wrote.

V10: report your DERIVATION separately from your value, so a reviewer can check that the stated
formula actually produces the stated number. A "4% solver error budget" once passed review as
"Fr^2/2 ~= 4%" when Fr^2/2 at Fr 0.876 is 38% - the number was real and the label was wrong by 10x,
and the review checked that the number existed rather than that its derivation held.

V7: report scope_run and scope_claimed as two explicit strings. If there is a gap, the verdict is
PARTIAL-flavoured and you say so in caveats.

The thresholds were signed before any number existed. Do not move one now.`,
      { label: `score:${g.key}`, phase: 'Score', schema: SCORE_SCHEMA }
    )
  )
)

const scoresOk = scores.filter(Boolean)
log(`Gates scored: ${scoresOk.map((s) => s.gate + '=' + s.verdict).join(' ')}`)

phase('Audit')

const AUDIT_SCHEMA = {
  type: 'object',
  required: ['gate', 'reproduces', 'derivation_holds', 'issues'],
  properties: {
    gate: { type: 'string' },
    reproduces: { type: 'boolean' },
    derivation_holds: { type: 'boolean' },
    recomputed: { type: 'object', additionalProperties: true },
    issues: { type: 'array', items: { type: 'string' } },
  },
}

const audits = await parallel(
  scoresOk.map((s) => () =>
    agent(
      `${RULES}

INDEPENDENTLY RE-DERIVE gate ${s.gate}. You did not score it. Do not trust the report; recompute
from the rasters and manifests yourself.

REPORTED:
${JSON.stringify(s, null, 1).slice(0, 22000)}

1. Reproduce every number from realized state. Do they match?
2. V10: does the stated derivation actually produce the stated value? Check the formula, not just
   the provenance.
3. Check the arithmetic: sum the splits, divide the volumes, verify percentages against their base
   rates. If they reconcile, SAY SO - that is evidence of good faith and it is worth reporting.
4. V7: is scope_run really equal to scope_claimed? Compute both as numbers.
5. Never accept a count where a magnitude is needed. How many points exceeded a threshold is not how
   much they exceeded it - this is the single most frequent defect class in reports on this project.
6. Is the metric itself right? A correct measurement against the wrong metric is more dangerous than
   a missing one, because it looks like an answer.
7. Read for absence: was anything asked for that did not come back?`,
      { label: `audit:${s.gate}`, phase: 'Audit', schema: AUDIT_SCHEMA }
    )
  )
)

const auditsOk = audits.filter(Boolean)
const failed = auditsOk.filter((a) => !a.reproduces || !a.derivation_holds)
log(`Audit: ${auditsOk.length - failed.length}/${auditsOk.length} gates reproduce with sound derivations.`)

phase('Axes')

const axes = await agent(
  `${RULES}

Produce the V11 AXIS LIST. For every variable the project touches, name the axis measured and the
axes left open on that SAME variable. This exists because an item was once closed on rainfall totals,
reopened on intensity, and two further instances of the same shape were then found - and the evidence
that reopened it had been sitting unread on disk in this repository for a day.

GATE SCORES:
${JSON.stringify(scoresOk, null, 1).slice(0, 40000)}

AUDITS:
${JSON.stringify(auditsOk, null, 1).slice(0, 25000)}

Cover at minimum: forcing (totals / intensity / timing / spatial structure / antecedent state),
terrain (elevation / storage / sub-grid geometry), drainage (topology / capacity / surcharge),
depth, extent, latency, and the network-synthesis fraction from WF-1.

For each: variable, axis MEASURED, axes still OPEN, and what would close each open axis.

Then state the known structural ceilings plainly, because they do not move:
- the source DEM encodes lakes as flat plateaus (Varthur interior std 0.16 m, 0.000 of interior
  below rim) - an acquisition-time water surface, not a basin;
- underpass lower-road geometry is unresolved at 10 m;
- both are one root cause - a global-DEM-class product encoding what it can see rather than what
  governs the flow - and the closure path is measured bathymetry and surveyed profiles, not more
  modelling.`,
  { label: 'axes:v11', phase: 'Axes' }
)

phase('Assemble')

const assembly = await parallel([
  () =>
    agent(
      `${RULES}

Write runs/sih26085_gate/REPORT.md - the terminal gate report.

GATES: ${JSON.stringify(scoresOk, null, 1).slice(0, 35000)}
AUDITS: ${JSON.stringify(auditsOk, null, 1).slice(0, 20000)}
AXES: ${String(axes).slice(0, 20000)}

Structure: mechanical verdict per gate first, then the diagnosis for anything that failed, then the
V11 axes, then what would close each open axis.

Every number carries its manifest path inline. Lead with failures, not with successes. If a gate
failed, the first sentence about it says so - no cushioning. A diagnosed, measured, mechanism-level
failure is a legitimate scientific result and reads far better to an MoES panel than a claim that
cannot be defended when someone asks where a number came from.`,
      { label: 'assemble:gate-report', phase: 'Assemble' }
    ),
  () =>
    agent(
      `${RULES}

Write docs/PRESENTATION-2026-08-28.md - the internal presentation narrative.

Tell the real story, in this order:
1. The problem (SIH 26085's own framing: knowing how much rain will fall does not tell you where the
   streets will flood).
2. What we built: 2D surface routing at 10 m over 12.7M cells, a directed drain graph with cited
   capacities, coupled surcharge physics, nowcast forcing, dashboard, routing API.
3. What we measured - the honest numbers, with provenance visible.
4. What we found that is genuinely interesting: the depth deficit is STRUCTURAL and we proved it
   rather than asserting it - the calibration moved the depth term 1.3%, and every parametric lever
   extrapolated to its bound buys ~0.11 of loss against the 1.62 needed. Root cause: the source DEM
   captured lake water surfaces, not basins (16 cm interior std at Varthur).
5. What is still open, named precisely, with the closure path for each.

Include the three hardest questions a panel could ask and the truthful answer to each.

Do not oversell. A team that names its limits precisely is more credible than one claiming
centimetres it cannot support - and every number here must survive "where did that come from?"`,
      { label: 'assemble:presentation', phase: 'Assemble' }
    ),
  () =>
    agent(
      `${RULES}

Update the living docs from realized state: README.md status table, docs/HANDOVER.md current
disposition, docs/OPEN-ITEMS.md (close what closed, name the axis it closed on per V11, open what
this run opened), and docs/SIH-26085-ALIGNMENT.md section 6 with the realized gate results appended
BENEATH the signed thresholds - never edited into them.

Do not restate intent as achievement. Do not soften a failed gate. Explicit staging by path when
committing; never 'git add -A'; no self-attribution footer in the commit message.`,
      { label: 'assemble:docs', phase: 'Assemble' }
    ),
  () =>
    agent(
      `${RULES}

FINAL ADVERSARIAL PASS over the assembled outputs. You are the last reviewer before this is shown to
people. Assume something is wrong.

Hunt specifically for:
- Any number in the report or presentation without a resolvable manifest path (V9).
- Any claim whose stated derivation does not produce its value (V10).
- Any gate verdict that rests on a threshold that moved after numbers existed.
- Any place a READING is presented as a FINDING (R1) - especially "surcharge closes the depth gap",
  which is the hypothesis this sprint TESTED and which may or may not have survived.
- Any assertion that could not have failed (V7 vacuity).
- Any place the presentation claims a scope the run did not cover.
- Read for absence: what was promised in the plan and quietly did not appear?

Report every instance with file, line, and the correction. This is the last chance to catch it
before it is said out loud in a room.`,
      { label: 'assemble:final-adversarial', phase: 'Assemble' }
    ),
])

log('WF-4 complete. Read the final adversarial pass before presenting.')

return {
  gate_scores: scoresOk,
  audits: auditsOk,
  audit_failures: failed,
  v11_axes: axes,
  assembly: assembly.filter(Boolean),
  next: 'Owner reads the final adversarial pass, applies corrections, commits, presents.',
}
