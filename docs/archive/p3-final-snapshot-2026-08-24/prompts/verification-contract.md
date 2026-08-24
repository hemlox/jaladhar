# The verification contract

Paste this block at the top of every implementation goal. It is the mechanism that actually worked
here — it converted "please be careful" into a checkable consequence.

Its limit, learned the hard way: it catches invented numbers, **not a broken method producing real
ones**. A catchment delineation once returned a reproducible, correctly-labelled 62-cell answer for
a 150 km² basin. Pair this with a method-validation gate (see `goal-template.md`, Part 0).

---

```
=== VERIFICATION CONTRACT ===
Every number must be reproducible by running ONE COMMITTED SCRIPT from a clean
checkout. I will run them. A number I cannot reproduce is treated as fabricated,
not as an error.

No throwaway heredocs. Every measurement lives in a module under src/ or
scripts/, is committed, and writes a run manifest with git SHA and status
"running" at start, updated on completion (rule 6), behind a config pre-flight
that resolves every key (rule 7). Re-run your own script before reporting and
paste the RAW stdout, not a summary. Every number carries its command.

Label every claim:
  [FINDING]     measured, script + logged run behind it
  [READING]     your interpretation of a finding
  [HYPOTHESIS]  untested
A conclusion resting on a [READING] must say so.

Failure modes that have all occurred here. Do not repeat them:
  - A percentage decomposition summing to exactly 100 with one component
    measured. Split an error budget only if each share is measured separately;
    unweighted candidate mechanisms are the honest form.
  - A rate with no denominator. Report the null from >= 5,000 random draws
    with a CI, or do not report the rate.
  - Overlapping categories presented as a partition. Check your classes sum to
    your total; if they overlap, give the joint counts.
  - A correct number labelled with a formula that does not produce it. One
    report cited "Fr^2/2 ~= 4%" for a measured 0.0416; Fr^2/2 at that Froude is
    38%. Verify the derivation, not only the provenance.
  - A check that cannot fail. "Residual = 0.000, closed: true" where the last
    term is computed as the residual is a mirror, not a check.

Before any verdict, write its falsifier and check whether that check exists.
If it does not, your output is "unresolved, and here is the check" — not a
verdict. Unresolved is acceptable and common. A withdrawn verdict is not.

When you close a question, name the axis you measured and the axes you did not.
"Forcing is fine" was closed on rainfall totals while timing, intensity and
antecedent state went untested, and had to be reopened.

You may fix bugs on your own initiative. You may NOT act on deviations from the
stated objectives — list them for Darshil in a consolidated section.
No Co-Authored-By lines, no self-attribution in commits. COMMIT YOUR WORK.
```
