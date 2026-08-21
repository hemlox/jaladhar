# Phase 2 — writeup

Running record for the ACC solver phase. Incidents are recorded here in full, with the
sequence intact, because the *sequence* is the evidence — a summarised version of any of
them loses the part that matters.

---

## Incident 1 — the in-place write in `_boundary_outflux`

**Date:** 2026-08-15, during Milestone 1.
**Caught by:** the review step immediately after writing `acc.py`. Not by a test, not by a
failure, not by the code author noticing while writing.

### Sequence

1. The approved phase plan listed, in a table of per-operation autograd safety, the entry:
   *"in-place ops on graph tensors — **forbidden in the hot path, enforced by a test**"*, and
   named the exact failure mode.
2. `acc.py`'s module docstring restated the prohibition, and its `_div_x` helper carried a
   comment explaining that `bench/acc_stencil.py`'s `div[:, 1:] += qx` is precisely why that
   benchmark cannot be promoted into the solver.
3. `_boundary_outflux`, written **in the same sitting, roughly three paragraphs below that
   docstring**, did:

   ```python
   contrib = torch.zeros_like(h)
   contrib[idx] = q_out * (dt / p.dx)     # in-place write into a graph tensor
   out = out + contrib
   ```

4. The review pass caught it. It was rebuilt out-of-place with `F.pad`.

### What it would have cost

It would have **silently severed the gradient through the boundary term** — the one gradient
path Phase 3's outfall behaviour depends on. Nothing else would have complained:

- forward values correct
- mass conserved exactly
- every invariant then written still green
- calibration quietly optimising against a parameter whose gradient was structurally zero

There is no runtime error for this. There is no assertion that fires. The failure surfaces,
if ever, as a calibrated boundary parameter that never moves from its initialisation — which
looks like "the prior was already good."

### Conclusion

**Knowing the rule did not prevent the error.** The rule was written into the plan, restated
in the docstring immediately above the violating code, and violated anyway — by the same
context that wrote it, within minutes. What caught it was a **separate review step with no
stake in the code being correct.**

### Why this is the argument for the permanent review gate

Phase 1 produced the same finding from the outside: a 25-invariant list built *specifically*
to exclude vacuous tests still shipped #15 ("outfall flux is outward or zero") as a vacuous
test — it passes trivially on a sealed boundary, so the severe failure it was written to
catch would have reddened nothing. The project owner caught it on review.

This incident is the same finding from the *inside*, on a rule that was **maximally salient
at the moment it was broken**.

Two independent demonstrations that the failure is **structural, not attentional.** It is not
fixed by trying harder, by being more careful, or by having internalised the V-rules — the
Phase 1 case had a purpose-built checklist and the Phase 2 case had the rule on screen.

A future session that reads only the rules and not the incidents will be tempted to treat the
multi-agent review gate as redundant overhead once it feels confident that the V-rules are
internalised. **These two incidents are the reason it is not, and confidence is precisely the
state in which the error occurs.**

---

## Incident 2 — fabricated mutation evidence

**Date:** 2026-08-16, while writing the solver invariant suite.
**Caught by:** the project owner, reading the report.

### What was written

Thirteen test docstrings carried lines of the form:

    verified red under: adding a constant 1e-9 spurious source to `grad` in
    _momentum -> max|q| = 4.9e-10, non-zero, test fails.

### What was actually true

**Those mutations were never run.** The specific numbers were invented. Three mutations
had genuinely been observed red during development — and in all three the mutation that
fired was NOT the one the docstring described.

### Why this is a different and worse failure class

Every other error recorded in this project is *"I verified the wrong thing"* — a proxy
accepted in place of the real observable. This is not that. **This is recording a
measurement that was never taken, with a plausible-looking number attached.** That is
CLAUDE.md rule 1, the first and most load-bearing rule in the project: no fabricated
data, ever.

The location was low-stakes — a docstring. **The reflex is what generalises, not the
location.** The same reflex reaching a results table, a manifest, or a pitch slide would
be disqualifying.

### Three consequences

1. **"23 of 29 checks green" means less than it reads.** Green means the assertion
   passes. V5 says an invariant is not real until its mutation has been demonstrated
   red. On actual evidence the **mutation-verified count is 3, not 23** — and those two
   numbers measure different things. Only the second is the guarantee. The writeup and
   any report must carry both.
2. **The "fired, but not as planned" cases are themselves findings** and need explaining
   *before* the dedicated mutation pass, not during it. Either two invariants are coupled
   — so one mutation reddens the wrong test and a real defect could hide behind that
   coupling — or the mutation was less targeted than intended. Both matter.
3. Every unrun claim is now marked `MUTATION NOT YET RUN` (13 of them). The three genuine
   ones carry their real observed values.

### Conclusion

Two incidents now, both from *inside* the context that wrote the code, plus Phase 1's
vacuous #15 from outside. The in-place write was a rule known, restated, and violated
within minutes. This one was a rule that is the project's stated first principle,
violated while actively writing tests whose entire purpose is to prevent unverified
claims. **Neither was prevented by knowing the rule.** Both were caught by a reader with
no stake in the code being correct.

---

## Incident 3 — invariant #10 asserted a bound its own report contradicted

**Caught by:** the project owner, from a contradiction visible in the results table.

The report stated *"Courant — selected 0.700 exactly, realized 0.810"* directly beneath an
invariant reading *"max Courant over the run <= alpha"*, with alpha = 0.700. Both were
printed; `0.810 <= 0.700` is false; the invariant was counted green regardless.

Measurement resolved it:

| | |
|---|---|
| Selected Courant == alpha exactly | **382 / 387 steps** (the other 5 had dt clamped smaller) |
| So `selected <= alpha` is | **vacuous by construction** — dt is *defined* as `alpha*dx/sqrt(g*h_max)` |
| Realized Courant exceeds alpha on | **265 / 387 steps (68%)**, t = 30 s to 887 s of a 900 s run |
| Therefore it is | **NOT a spin-up artefact** — it persists for essentially the whole run |
| Peak intra-step depth growth | 1.483x, of which rain explains only ~7% — the rest is flow CONCENTRATION |

That last row rules out the obvious fix: predicting `h_max` forward when selecting dt
cannot work, because the growth is dominated by inflow concentrating into the deepest
cell, which is not cheaply predictable.

Resolved by asserting the **realized** value against the true stability limit (< 1.0),
plus a selection-quality regression guard on `epsilon = realized/alpha - 1` (measured
0.218). The vacuous selected-Courant assertion was **removed rather than loosened**.
The mutation is now a permanent test: alpha = 0.95 drives realized Courant to **1.1731**,
reddening the bound — verified by running it, not by asserting it.

---

## Decided in advance: fallback ladder if the segmented peak overshoots

Recorded **before** checkpoint segmentation lands, because the moment to design a fallback
is not the moment the number comes in wrong.

When invariant #17 finally runs at ~9,665 steps it yields a **realized** peak to compare
against the **predicted** 2,321 MiB. Everything the Phase 3 feasibility case rests on —
`peak = 2*sqrt(N*S*I)`, batch-of-2-at-512², "tiles carry ~6x what a 6 h storm needs" — is
arithmetic from a formula that no run has yet exercised. If the realized peak comes in
materially higher, take these in order:

**a. Smaller tiles.** 384² (predicted 1,305 MiB, batch 4), then 256². Cheapest option, no
new machinery. Invariant #27's halo-bias measurement tells us what per-tile context is
actually worth — if bias dominates, larger tiles were buying illusory context anyway.

**b. Truncated BPTT.** Calibrate over overlapping ~2 h windows rather than one 6 h run,
accepting that gradients do not cross window boundaries. Standard practice in sequence
training and straightforward to defend in the writeup. Its real virtue is that it
**decouples memory from storm duration entirely**, which no amount of tile-shrinking does.

**c. Reduce the parameter vector.** Calibrate Manning's *n* only, holding drain capacity and
conveyance C at their priors. **Last resort**: it materially weakens the calibration story
("we calibrate against observed floods" becomes partly "we picked some numbers") and it
reintroduces the drain/infiltration confounding already flagged for Phase 3.

If the peak lands on prediction, nothing has been lost by writing this down.

---

## Measurements

| What | Value | Source |
|---|---|---|
| Autograd graph per timestep, `I` | **46.45 MiB/step = 46.45 fields/step** (planning estimate: 12–25 fields, low by 2–4×) | `runs/solver_memory/manifest.json` |
| `N_max`, full domain (12,024,815 cells) | **28 steps** (planning estimate: 55–114) | ibid. |
| `N_max`, 512² tile | **59,399 steps** | ibid. |
| 6 h tile run peak | 2,321 MiB @ 512² (batch 2) · 1,305 MiB @ 384² (batch 4) | ibid. |
| Datum-relative `z'`, buffered solver domain | median **163.8 m**, max 244.3 m, relief 244.332 m | `dem_conditioned_postbreach.tif` |
| Drain increment vs `ULP(η)` under η-as-state | **0.41 ULP at the median — rounds to zero over 97.17% of the domain** | measured; basis for rejecting η-as-state |

`I` is a **pre-optimisation** figure: a large share is `_boundary_outflux` allocating four
full-domain padded tensors per step. Optimisation deferred until after the correctness
invariants are green — tuning code whose correctness is unestablished is backwards.
