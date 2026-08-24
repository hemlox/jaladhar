# 4-day sprint plan — SIH 26085 working system

**Written 2026-08-24. Internal presentation: 2026-08-28.**

## The one constraint that shapes everything

**There is one RTX 4060.** Unlimited agents does not mean unlimited GPU. Every solver run
serializes through a single 8 GB device, and the full 48-hour city replay costs **10.58 GPU-hours**.
You get roughly **six to eight GPU slots in four days**, and they cannot be spent on iteration.

Therefore the whole sprint is organised around one rule:

> **Agents build and verify on CPU in parallel. The GPU runs only things that are already verified.**

Every workflow below is designed so that when a GPU slot opens, the code going into it has already
been red-tested, contract-checked, and adversarially reviewed by agents that never touched the
device.

## What "no sacrifices" means here, precisely

It does **not** mean doing 6 weeks of work in 4 days. It means the difference between **scoping** and
**faking**, which is the distinction CLAUDE.md exists to protect:

| Sacrificing (forbidden) | Scoping (what we do) |
|---|---|
| Invent drain widths so capacity "works" | CPHEEO design-standard rule, cited, with a sensitivity band |
| Blur/distance-transform to make the map look alive | Real surcharge physics or the dashboard shows nothing |
| Hardcode a CSI in the frontend | Every number reads from a manifest (rule 3) |
| Claim city-wide validation we didn't run | Validate on short windows + subdomains, name the open axis (V11) |
| Ship a test that has never failed | Every invariant demonstrated red (V5) |

**What we will NOT have by day 4, stated up front so nobody discovers it in the room:** a calibrated,
city-wide, 48-hour validated replay of the coupled model. That is 10.58 GPU-h per iteration and
cannot be tuned inside four days. It is a declared open axis, not a hidden gap.

**What we WILL have:** a real coupled surface–drain solver with surcharge, built on a real network
with cited capacities, validated on short windows against real ground truth, driven by a real
nowcast, behind a real dashboard and a real routing API — with every number traceable.

---

## Day 0 — tonight, ~2 hours: unblock and lock contracts

Nothing expensive starts until these are answered, because both change what gets built.

1. **Resolve N-3 (DWR / nowcast availability).** Rule 5: blocked on an external unknown means stop
   and report. Two readings — consume IMD's published nowcast product, or build from volumetric
   reflectivity. This changes the forcing workstream by a week of effort. **Run `WF-0`.**
2. **Lock the four interface contracts.** Drain-graph schema, coupling interface, depth-product
   schema, segment-status schema. Once these are frozen, all five workstreams parallelize
   completely and nothing blocks on anything else. **`WF-0` produces these.**

## Day 1 — the drain network becomes a graph

**`WF-1` (CPU, massively parallel).** Stitch 370 disconnected reaches into a connected directed
graph using the conditioned DEM's D8 flow direction; extract junction nodes; assign hydraulic
capacity from cited design standards; validate topology adversarially.

Deliverable: `data/processed/drain_graph.gpkg` + manifest, with **every capacity value carrying its
citation** and a `capacity_basis` field that is never the string "assumed".

**GPU slot 1 (evening):** 30-minute toy-scale coupling smoke on a subdomain. Per **V12** — no GPU
hours before an end-to-end toy-scale execution.

## Day 2 — the coupling, which is the actual research contribution

**`WF-2` (the big one — design panel → implement → red-test → adversarial verify).** Three
independent coupling designs judged against each other, winner implemented, every invariant
demonstrated red under deliberate mutation.

The physics that must exist: inlet capture limited by capacity, routing along the graph, **and
surcharge return to the surface when a node exceeds capacity**. G2 fails any run where no node ever
surcharges — that is the anti-vacuity condition, and it is there because of the Phase 1
sealed-outfall incident.

**GPU slots 2–3:** 6-hour night-1 window on the Bellandur–Varthur corridor subdomain, uncoupled vs
coupled. This is the first evidence the mechanism does anything.

## Day 3 — products, in parallel with GPU validation

**`WF-3` (CPU, five independent tracks).** Dashboard, routing API, nowcast adapter, segment-status
scorer, demo scenario packaging. All build against day-0 contracts, so none of them waits on the
solver.

**GPU slots 4–5:** full-domain 3-hour nowcast run, timed for **G4** (≤10 min wall clock via
subdomain tiling at 10 m — *not* by coarsening the grid, which would spend the micro-topography
claim the statement rests on).

## Day 4 — integration, gate, and the honest number set

**`WF-4`.** Score G1–G5 mechanically, produce the V11 axis list, assemble the presentation with
every figure carrying its manifest path.

**GPU slots 6–8:** contingency and the final demo run.

---

## Workflow inventory and dependency graph

| id | file | depends on | agents | device |
|---|---|---|---|---|
| WF-0 | `workflows/wf0-recon-contracts.js` | — | ~14 | CPU |
| WF-1 | `workflows/wf1-drain-graph.js` | WF-0 contracts | ~28 | CPU |
| WF-2 | `workflows/wf2-coupling.js` | WF-1 artefact | ~46 | CPU (GPU by owner) |
| WF-3 | `workflows/wf3-products.js` | **WF-0 contracts only** | ~34 | CPU |
| WF-4 | `workflows/wf4-gate.js` | WF-2 + a coupled GPU run | ~22 | CPU |

```
WF-0 ──┬── WF-1 ──── WF-2 ──── [GPU slots] ──── WF-4
       └── WF-3 ─────────────────┘
          (parallel from day 1)
```

**WF-3 does not depend on WF-1 or WF-2.** That is the entire reason contracts are frozen on day 0 —
the dashboard, routing API, nowcast adapter and segment scorer are built against *schemas*, not
against solver output. Launch WF-3 concurrently with WF-1 and it comes off the critical path
entirely, which is worth roughly a full day out of four.

The two are disjoint by file path, which is what makes it safe:

| workflow | owns |
|---|---|
| WF-1 | `src/jaladhar/drainage/`, `tests/drainage/` |
| WF-2 | `src/jaladhar/coupling/`, `tests/coupling/` |
| WF-3 | `src/jaladhar/web/`, `src/jaladhar/routing/`, `src/jaladhar/forcing/nowcast.py`, `src/jaladhar/validation/segment_status.py`, `scripts/demo/` |

Both are CPU-only, so there is no device contention either.

**Everything else is strictly sequential**, and you read each result before launching the next. That
gap is where the review gate lives, and CLAUDE.md is explicit that the independent review is not
graduated out of — its value comes from the reviewer having no stake in the code passing, which is
not reproducible from inside the context that wrote the code.

**Revised critical path:** WF-0 (day 0 evening) → WF-1 ∥ WF-3 (day 1) → WF-2 (day 2) → GPU
validation (day 2–3) → WF-4 (day 4), leaving day 3 as slack. On a four-day deadline with one GPU,
slack is the thing most likely to save you.

## Standing rules that bind every agent in every workflow

Each workflow embeds these in its agent prompts. They are repeated here because they are the
difference between a system and a demo:

1. **No fabricated data.** If a source is unavailable, stop and report it. This bites hardest on
   drain cross-sections (N-1).
2. **No placeholder physics.** No blur, no distance transform, no "approximate" surcharge.
3. **Manifest at run start**, updated in place on completion — never only on success.
4. **Resolve every config key at startup** with an aggregated error.
5. **Every invariant demonstrated red** under a deliberate mutation, with the mutation recorded.
6. **State scope beside claim** (V7). A correct assertion over too narrow a domain cannot fail.
7. **Producer writes guarantees into the manifest; consumer asserts them at load** (V8).
8. **Findings vs readings** (R1). Nothing is sequenced on a reading.
