# Goal template

Structure that produced the two best rounds of work on this project.

```
[VERIFICATION CONTRACT — see verification-contract.md]

GOAL: <one sentence. The question, never the anticipated answer.>

REPO: /path   PYTHON: .venv/bin/python
Read CLAUDE.md (V1-V11, R1-R8) and OPEN-ITEMS.md items <X, Y> first.

=== OWNERSHIP ===
You own <dirs>. Other sessions own <dirs> — do not touch them.
CPU only / GPU. Do NOT re-run <expensive thing>.
Stage explicit paths, never `git add -A`.

=== PART 0 — VALIDATE THE METHOD BEFORE YOU USE IT. THIS IS THE GATE. ===
<Known-answer checks the tool must pass before producing any result.
 Compute nothing downstream until they pass.>
Include at least one EXTERNAL known answer — a published figure, an
independent dataset — not only internal consistency. Internal checks pass
happily on a broken method.

=== PART 1..N — the actual work ===
<what to measure, and what to report>

=== HYPOTHESES ===
<State yours in a block the agent is instructed to ATTACK, never in the
 framing. Give each one its falsifier.>

=== DO NOT ===
<Bound the blast radius. Name the tempting shortcut explicitly.>

=== DONE LOOKS LIKE ===
<Concrete artefacts. Include the acceptable negative outcome — "or Part 0
 failing, with the reason, and no results computed".>
```

## What made the difference

- **Part 0.** The single highest-value addition. Validating the instrument against a known answer
  caught a four-order-of-magnitude error the reproducibility contract could not.
- **Pre-registered falsifiers.** Two hypotheses were killed before compute was spent on them.
- **Naming the acceptable negative outcome.** Agents will otherwise manufacture a positive result.
- **Not stating the expected answer.** A prompt that said "if X is not above 5.17×, the change
  bought nothing and you say so plainly" got exactly that back, and it was read as confirmation.
