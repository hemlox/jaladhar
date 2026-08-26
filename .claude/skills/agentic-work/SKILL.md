---
name: agentic-work
description: How to execute the JALADHAR SIH-26085 sprint workflows (wf0-wf4) correctly - sequencing, the one-GPU constraint, how to read agent reports without being fooled, and the failure modes this specific repo has already hit. Load before running any workflow in workflows/, before dispatching implementation agents, or when deciding whether a reported result can be trusted.
---

# Executing the JALADHAR agentic workflows

You are running a 4-day sprint to a working SIH 26085 system. The workflows in `workflows/` do the
building. This document is how to run them without producing something that looks finished and
isn't.

Read [`../../CLAUDE.md`](../../CLAUDE.md) first — it is binding and this document assumes it.
Read [`../../docs/SPRINT-4DAY-PLAN.md`](../../docs/SPRINT-4DAY-PLAN.md) for the day-by-day shape.

---

## 1. The constraint everything bends around

**There is one RTX 4060, 8 GB.** Unlimited agents does not mean unlimited GPU. A full 48-hour city
replay costs **10.58 GPU-hours**. Across four days you get roughly **six to eight slots**, and they
cannot be spent on iteration.

So the operating rule is:

> **Agents build and verify on CPU in parallel. The GPU only runs code that has already been
> red-tested, contract-checked, and adversarially reviewed by agents that never touched the device.**

Practical consequences:

- Never let a workflow launch a GPU run itself. Workflows produce *verified, executable* code and a
  smoke result; **the owner authorises each GPU slot by hand.**
- Before any slot: confirm V12 was satisfied (an end-to-end toy-scale execution actually ran, with
  real command and real output pasted). This check has already caught two never-executed modules on
  this project.
- Before any slot: confirm `_resolve_config()` touches every key the run needs **including
  post-simulation reporting and output paths**. A `KeyError` on `buffer_m` in `write_depth_series()`
  destroyed 37.8 GPU-minutes because that line only executes after a complete simulation.
- **Nothing touches the tree while a GPU run is in flight.** Provenance first.

## 2. Run order, and what happens between

```
WF-0  recon + contracts     ~14 agents   day 0, ~2h
WF-1  drain graph           ~28 agents   day 1
WF-2  coupling (THE BIG ONE) ~46 agents  day 2
WF-3  products              ~34 agents   day 3, concurrent with GPU validation
WF-4  gate                  ~22 agents   day 4
```

Invoke as `Workflow({ scriptPath: "workflows/wf0-recon-contracts.js" })`, and so on.

**They are separate workflows on purpose.** Read each result before launching the next. That gap is
where the review gate lives, and CLAUDE.md is explicit that the independent review pass is permanent
and does not get graduated out of — its value comes from the reviewer having no stake in the code
passing, which is not reproducible from inside the context that wrote the code, including by trying
harder.

**Between every pair of workflows, do this:**

1. Read the workflow's returned object. Actually read it, including the adversarial phases.
2. Check the arithmetic in any table it produced. **If it reconciles, say so** — that is evidence of
   good faith and worth recording.
3. Ask what is *missing*, not just what is wrong. If six things were asked for and five came back,
   the sixth is the one that matters.
4. Decide explicitly: proceed, re-run with amendments, or stop. Write the decision down.

**Never chain workflows unattended.** A workflow premised on the previous one's unread output is how
a reading becomes a foundation.

## 3. The one hypothesis under test

The sprint's central bet is: **coupling the drain network with surcharge closes the depth deficit.**

That is a **reading**, not a finding. The measured facts behind it:

- Drains removed **79.16 Mm³ = 25.57%** of total inflow one-way, with no return path.
- Of three coordinates the calibration accepted, one was `drain −0.25` (reduce capacity).
- Counter-evidence: the drain gradient is small (0.0239), so simply reducing capacity buys little.

Under **R1, no goal may be premised on a reading.** WF-2 is written to *test* this, and its design
phase explicitly instructs agents to attack the hypothesis rather than confirm it — per **R6**, a
goal prompt carries the question and the method, never the anticipated answer. One prompt on this
project once told an agent in advance what to conclude if a number was not above a threshold; the
agent said exactly that, and it was read as independent confirmation. It was an echo.

**A negative result is a valid and valuable outcome.** If surcharge does not close the gap, that is a
real finding about a real city's drainage, and it is far more presentable than a fudge.

## 3a. The scheduled-vs-realized audit — run this after EVERY workflow

A workflow's report describes what it *thinks* it did. The script describes what it was *told* to do.
Auditing one against the other is mechanical, takes five minutes, and has already caught both a
badly-undersold report and an R3 violation. Do it every time, before reading the prose.

1. **Extract the spec.** Open the workflow script and list every concrete deliverable each track was
   given — file paths it owns, named features, required properties. The `task:` strings are the
   checklist; they were written to be checkable.
2. **Check existence against disk.** `ls`/`wc -c` every promised path. A track reporting PARTIAL with
   40 KB of code on disk is a reporting problem, not a build problem — and the reverse is worse.
3. **Grep each named feature.** For every "must have X" in the spec, grep for X in the files that
   would contain it. **Grep the file that would actually hold the feature**, not the nearest one — a
   UI requirement lives in `static/`, not in `app.py`, and getting that wrong produces false alarms.
4. **Run the rule-3 audit yourself.** Do not accept a track's own report that it has no hardcoded
   metrics. Grep for numeric literals that look like depths, thresholds, counts, or scores, then
   filter out legitimate constants (pixels, timeouts, unit conversions). What survives is the finding.
5. **Check ownership.** `git diff --numstat` every changed file against the track's declared `owns`.
   Edits outside it are deviations for owner adjudication, not bug fixes — even when they are
   correct, and even when you decide to keep them.
6. **Read for absence.** Anything in the spec with no corresponding artefact is the finding. It will
   not appear in the report, by definition.

**Before accepting any BLOCKED status, apply R3: inventory what is already on disk.** A track is not
blocked on an input that already exists somewhere in the repo. Completed run directories, cached
fetches, and prior replay outputs are all products. This is the single most common false blocker
here, and CLAUDE.md records the precedent — `data/fetched_articles.json` sat unread for a day while
forcing was declared resolved and an acquisition goal was written to go find what was already there.

## 4. How to read what the agents send back

Most of what you receive is a **report**, not code you wrote. In this order:

**Check the arithmetic before the conclusions.** Sum the splits, divide the volumes, verify
percentages against base rates. If they don't reconcile, nothing above them is usable.

**Separate measured from asserted.** "Updated the module to handle the wider bbox" is intent.
"900,600 elements against 866,426" is evidence. Reports routinely present the first as the second.

**Never accept a count where a magnitude is needed.** How many nodes surcharged is not how much they
surcharged. This is the single most frequent defect class on this project.

**Interrogate the metric before the number.** A correct measurement against the wrong metric is more
dangerous than a missing one, because it looks like an answer.

**Reject statistics that cannot vary.** A fraction with no denominator; a table constant across its
own parameter; a claim that a mechanism didn't fire when the mechanism doesn't exist.

**"Shipped" is not "executed."** Standing rule on this project (V12), and it is 2-for-2 on catching
never-executed modules. If a report says a module was built, look for the real command and real
output. If they aren't there, it hasn't run.

**Sanity-check against physical reality**, not just internal consistency. A model can be perfectly
self-consistent and describe nothing that exists.

## 5. Failure modes this repo has actually hit

Pattern-match against these when reviewing. They are not hypotheticals.

| Incident | Shape | What to grep for |
|---|---|---|
| `_boundary_outflux` in-place write | Rule stated in the plan, restated in the docstring three paragraphs above the violating line, violated anyway — within minutes, by the same context | in-place tensor mutation on anything a gradient or flux depends on |
| `for p in (basin_path, elev_path)` | Loop variable shadowed `SolverParams`; dry start never entered its branch; tests stayed green; state was wrong downstream | shadowed names, branches that never execute |
| Sealed-outfall invariant | "Outfall flux is outward or zero" passes trivially on an all-zero boundary — the severe silent failure it existed to catch would have reddened nothing | any assertion satisfied by the null case |
| Courant ≤ α | The selected Courant is *definitionally* α on 382/387 steps | a check whose observable is set by the code under test |
| Scope inflation | "Finite non-zero gradients over a full-duration run" — ran 40 steps on 24×24 against a claimed 9,665 on 512²: 0.41% of duration, 1/455 of domain | scope run vs scope claimed, as two numbers |
| Moving baseline | Verifying against something you produced or that can move | candidate-vs-candidate only |
| Adapter `source_name` | Reported as shipped; had never executed end to end | real command, real output |

**The mechanical trigger**, since noticing you're about to accept a proxy is the hard part: before
any claim of the form "X is true," ask *what would I SEE if X were false?* — then check whether the
thing you're about to look at would actually change. If it would not, you're looking at a proxy.

## 6. Where the rule-1 boundaries are in this sprint

"No fabricated data, ever" bites in exactly three places. Watch these specifically:

1. **Drain cross-sections (WF-1, `capacity` unit).** The BBMP KMLs have `OBJECTID` and `Length` and
   nothing else — no width, depth, or invert. Capacity must come from a **cited design standard**
   with the citation stored as data in `capacity_basis`, plus a sensitivity band. The string
   `"assumed"` is not a basis. This is the most likely place a plausible number gets typed in.
2. **Synthesised connectors (WF-1, `stitcher`).** ~370 disconnected components have to be joined, so
   a large fraction of the network may be invented. Every synthesised edge must be tagged and the
   synthesised **fraction of total length** must appear in the manifest. If we synthesised more than
   we observed, that is the headline — the owner needs it before the presentation, not after.
3. **Nowcast forcing (WF-3, `nowcast-adapter`).** If the source is unavailable the adapter **raises**.
   It never substitutes climatology or a last-observed value. A blocked axis honestly reported is a
   valid day-4 outcome; a fabricated feed is not.

Rule 2's boundary is the dashboard: **if the solver produced nothing, the UI shows nothing.** No blur,
no distance transform, no "approximate" flood to look alive.

## 7. Dispatching your own agents between workflows

If you need work outside the scripted workflows:

- **Disjoint by file path and by device.** State the ownership split inside every prompt — which
  directories that agent owns and which another session owns and must not touch.
- If one agent needs the GPU, **the others are CPU-only, and say so explicitly.**
- **Pair goals so they answer different questions** rather than racing the same one — "is the graph
  right" alongside "is the coupling right," not two attempts at the same thing.
- **Do not manufacture a second goal to fill a slot.** If the only remaining work depends on the goal
  in flight, one goal is the correct answer.
- Files have been accidentally reverted in this repo **twice**. The guard is explicit staging paths,
  never `git add -A`, and never `git checkout`/`restore`/`stash` on a file the agent did not create.
- **Implementation agents may fix bugs on their own initiative. They may never act on deviations
  from stated objectives** — those go to the owner in a consolidated report.

## 8. When a workflow fails or returns something odd

- **Read `<transcriptDir>/journal.jsonl`** before diagnosing an empty or surprising result. It records
  each agent's actual return value. Do not assume cached results are non-empty.
- **Resume rather than restart:** `Workflow({ scriptPath, resumeFromRunId })`. The longest unchanged
  prefix of `agent()` calls returns cached instantly; the first edited call and everything after runs
  live. Same script + same args = 100% cache hit. Stop the prior run with `TaskStop` first.
- To iterate, **edit the persisted script file** and re-invoke with `scriptPath` — don't resend the
  whole script inline.
- If a phase produced nothing useful, that is information. Ask whether the prompt carried an
  anticipated answer (R6) or whether the agents lacked a file they needed.

## 9. What "no sacrifices" means when the deadline is close

The distinction is **scoping** versus **faking**, and it is the whole difference between a system and
a demo:

| Faking (never) | Scoping (correct) |
|---|---|
| Invent drain widths so capacity works | Cited standard + sensitivity band |
| Blur the depth field to look alive | Real physics or the UI shows nothing |
| Hardcode a metric in the frontend | Every number reads from a manifest |
| Claim validation we didn't run | Validate on short windows, name the open axis (V11) |
| Ship a test never seen fail | Demonstrate every invariant red under mutation (V5) |
| Move a threshold once numbers exist | Thresholds signed 2026-08-24, before any number |

**Known and accepted, stated up front so nobody discovers it in the room:** there will be no
calibrated, city-wide, 48-hour validated replay of the coupled model by day 4. That is 10.58 GPU-h
per iteration. It is a declared open axis, not a hidden gap.

**The temptation to watch for** is the strictness ratchet running backwards — *"new problem
statement, so the old gate doesn't apply."* It doesn't apply because it was **discharged as
measured**, and a replacement was signed before any new number existed. Those are different things,
and only one of them is honest.

A diagnosed, measured, mechanism-level limitation presented plainly beats an undefendable claim. When
someone on the panel asks "where did that number come from?", every number must have an answer.
