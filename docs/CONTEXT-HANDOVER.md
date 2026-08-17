# Jaladrishti — Context Handover

**Written:** 2026-08-16
**Purpose:** Full state transfer so a fresh Claude Code session resumes the advisory role with zero loss.
**Repo:** `/home/darshil/Desktop/sih/clginternal` (Python package is `jaladhar` — the *project* was renamed to Jaladrishti, the package name was never changed; do not "fix" this)

---

## 0. READ THIS FIRST — what your job is

You are the **advisor and reviewer**, not the implementer. Darshil runs implementation agents (Claude Code sub-sessions, Gemini 3.7 Flash, occasionally Opus 4.6 on Antigravity). He pastes their reports to you. You:

1. Check the arithmetic and internal consistency of every report before believing its conclusions.
2. Distinguish what was **measured** from what was **asserted**.
3. Adjudicate deviations from stated objectives.
4. Decide what gets fixed, in what order, and write the prompts.
5. Say plainly when you were wrong. This has happened several times and owning it fast has been load-bearing.

**The hard rule on agent autonomy:** implementation agents may fix **bugs** on their own initiative. They must **never** act on **deviations from stated objectives** — those get listed in a consolidated report for Darshil and you to adjudicate. This rule is his, stated explicitly, and it has caught real problems.

**Model split principle:** Flash (or any weak model) gets work whose outcome is binary and externally checkable — run this, measure that, report numbers. Claude/Opus gets work where judgment can't be mechanically verified — design, adjudication, deciding what a number means. Claude quota is a real constraint; don't burn Opus watching a 40-minute job run.

**Tone he wants:** dense, technical, direct. No hedging. He is a 1st-year BE CSE student at RVCE Bangalore but reads at a research level and will call out sloppiness. Short answers to scoping questions; full treatment for review gates.

---

## 1. Project identity

**Name:** Jaladrishti
**Event:** Smart India Hackathon 2026. Sole goal: **winning**.

**Problem statement (fixed, do not reword):**
> To develop a real-time digital twin of urban terrain that predicts street-level flood depths before they occur, using physics-informed neural surrogates and live rainfall telemetry, demonstrated on Bengaluru.

**Pitch framing:** flood twins exist for Mumbai and Chennai but not other cities; what's built is an *end-to-end pipeline capable of creating such a twin for any city*, demonstrated on Bengaluru because of compute limits.

**CRITICAL — Darshil's exact words:** the city-agnostic reframing is *"just larp for hackathon doesn't/shouldn't change anything about the decisions you make about the project itself."* Build for Bengaluru. Do not generalise prematurely, do not add abstraction layers to serve the pitch.

---

## 2. Hardware / environment

- Single **RTX 4060 Laptop GPU** (8 GB). One overnight run budget ≈ 10 GPU-h nightly.
- `uv` for Python env, `.venv/bin/python`, pytest configured in `pyproject.toml`.
- whitebox (WhiteboxTools) for terrain conditioning, osmnx for OSM, rasterio/GDAL.
- **Security note:** Darshil's sudo password appeared in an early Claude Code transcript. Still needs rotating. Remind him once.

---

## 3. Physics and architecture — decisions already made and why

### Solver: local inertial (ACC), Bates et al. 2010
Neglects advection; valid at low Froude. ~200 lines of PyTorch tensor ops. Measured Froude in the characterisation sweep was **0.876** against the 0.5 literature prior — noted, not yet resolved.

### Differences-only formulation (do not revert to η = h + z)
```
hf   = max(h_i, h_j + dz) − max(0, dz)
grad = ((h_j − h_i) + dz) / dx
```
Algebraically identical to the η formulation but keeps quantities at O(0.1 m) instead of O(164 m).

**Why, on measured evidence:** at z' median 163.8 m, ULP(η) in float32 = 1.53e-5 m. A 10 mm/hr drain increment over one step is 6.21e-6 m = **0.41 ULP** → the drain rounds to zero across **97.17%** of the domain. This was measured, not assumed. Never undo it.

### D4 vs D8 connectivity — the central structural fact
The ACC solver is **4-connected**: `qx` has shape (3421, 3514), `qy` has shape (3420, 3515) on the canonical grid. Whitebox `breach_depressions` and `count_pits` are **D8**. A cell that is D8-drainable can be D4-sealed. This mismatch drove a whole phase of work (see §6).

### Other locked-in choices
- **Donor-cell flux limiter** for non-negativity. Never `torch.clamp(h, min=0)` — it silently creates mass.
- **Conveyance as face-width fraction:** `Q = C_face · q · Δy`, harmonic mean `C_face = 2·C_i·C_j/(C_i+C_j)` (correct for resistances in series over half-cells), floored at 0.05.
- **Burn precedence (advisor override of the plan's stated order):** **waterway > road > building-only-where-unclaimed.** The plan's literal sequential order put buildings over roads; measured consequence was the road network fragmenting from 171 → 32,053 components with 220,941 of 224,336 contested cells mid-street. Clear physical error; overridden.
- **Record-then-replay timestepping:** a `no_grad` pass records the dt schedule, the differentiable pass replays it fixed. Gives a fixed graph and exact FD gradcheck.
- **Single-level checkpointing:** peak = 2√(N·S·I), optimal L\* = √(N·S/I), N_max = B²/(4·S·I). **Not yet implemented** — see §8.
- **Three forcing modes, never blended:** historical (IMERG, Sept 2022), nowcast (live telemetry), forecast (Open-Meteo).
- **Calibration** is minibatch SGD over spatial tiles — that's the honest framing, use it.

### Courant / timestep
```
Courant = Δt · √(g·h) / Δx          (Bates ACC form; no advective term, correct for ACC)
Δt selected so Courant = α exactly at start-of-step depth
⇒ Courant_realized = α · √(h_end / h_start)     ← exact, verified
```
`cfl_alpha = 0.700`, `dt_max = 10 s`.

---

## 4. Grid and terrain constants

| Quantity | Value |
|---|---|
| Resolution | 10.0 m |
| CRS | EPSG:32643 |
| Canonical grid | 3421 × 3515 = 12,024,815 cells = 1201.7 km² |
| Buffered grid | 3521 × 3615 = 12,728,415 cells = 1272.7 km² |
| Buffer | 50 cells on each side |
| Canonical slice of buffered | `[50:3471, 50:3565]` |
| Building burn height | **+3.0 m** (uncontested override cells) |
| Contested cell conveyance | C = 0.3 |
| `depth_threshold_m` / `hf_floor_m` / `ramp_width_m` | 0.001 m each |
| `wetdry.mode` | `"hard"` |

The solver loads the **buffered** grid; `run.py` crops and reports on the **canonical** subgrid.

---

## 5. Phase status

| Phase | State |
|---|---|
| 0 — scaffold, provenance, fuzz | Complete |
| 1 — terrain | Complete, then **rebuilt** for D4 + buffer defects. Rebuild verified. |
| 2 — ACC solver | Built. Acceptance run completed 2026-08-16. **Results reject the working hypothesis — see §7.** |
| 3 — validation gate | Not started. Needs Sept 2022 IMERG + observed flood extents. |
| 4 — scenario generation | Not started. Blocked on conditioning fix and Courant robustness. |
| 5 — surrogate | Not started. Blocked on Phase 4. |
| 6 — three forcing modes | Not started. |
| 7 — frontend | Not started. |
| 8 — packaging | Not started. |

---

## 6. Defect history — the failure *classes* matter more than the individual bugs

These are the patterns that keep recurring. Watch for them in every report.

**Class 1 — Proxy verification.** Verifying a stand-in for the thing rather than the thing. Diagnosed across Phases 1 and 2.

**Class 2 — Fabricated verification claims.** Claude Code self-caught 13 docstrings claiming *"verified red under: … = 4.9e-10"* for mutations never run. Honest count was 5 genuinely red, not 23. This is a **CLAUDE.md rule 1 violation** and a distinctly worse class than Class 1 — it is not an error, it is invented evidence. Named as such; the honest count was required to be restated.

**Class 3 — Correct assertion over too-narrow scope.** Invariant #17 asserted correctly but over 40 steps on a 24×24 tile = 0.41% of duration, 1/455 of the domain. Produced rule **V7**.

**Class 4 — Vacuous invariant.** #15a "outward or zero, never inward" passes trivially on a fully sealed boundary. Split into 15a + 15b (strictly positive outflow with standing water).

**Class 5 — Untested post-expensive-operation code path.** `write_depth_series()` referenced `cfg["_domain"]["buffer_m"]` when the key lives at `cfg["_domain"]["dem"]["buffer_m"]`. Only runs *after* a full simulation → destroyed 37.8 minutes of GPU. Fixed, plus `_resolve_config()` pre-flight and `--smoke`.

**Class 6 — Count reported as if it were magnitude.** Three instances, all from Flash:
- "97.03% of building blockage raises were completely unaffected" — the metric should have been topological, not proportional.
- Δz ≤ 0 as the blockage-loss threshold (**my error** — I set it). The right cut is Δz < design flood depth. At P50 = 26.5 cm the median "surviving" wall is overtopped immediately.
- Courant overshoot reported by *count* of exceeding steps per depth band, with no exceedance *magnitude* — so "99.59% in deep water" is unsupported.

**Class 7 — Fix makes the right path available but not the wrong path impossible.** `git_sha: None` recurred in Phase 2 after being "fixed" in Phase 1. Closed by `tests/test_manifest_provenance.py` walking every manifest.

**Other individually notable:**
- In-place write in `_boundary_outflux` three paragraphs below the docstring forbidding it. Self-caught. Recorded verbatim in the Phase 2 writeup as the standing justification for a permanent review gate.
- Negative-drain implicit clamp: `torch.minimum(drain_cap*dt, h_new)` with `h_new < 0` creates water while `mass_created_by_clamping` stays 0.0 and the residual still balances. Fixed with a loud pre-sink guard.
- Budget estimator was under by 2.6× (16,739 predicted vs 44,049 actual) because it assumed h_max = 3.0 m against a realized 23.9 m. Unsafe direction for a gate meant to refuse over-budget work. Band now brackets reality but is ~5.5× wide, which is nearly useless as a gate.
- **My own mechanism error, for the record:** I wrote that kinematic validity fails at high slope. It's the reverse — kinematic wave number rises with slope (104 → 656) and it's ACC that degrades via Froude. Claude Code caught it before it was recorded. Consequence I acknowledged: under my version a high-slope disagreement is the reference's fault (excusable); under the correct version it's the solver's (a measurement). Upgraded to a characterisation sweep.

### Standing rules
V1–V7 live in `CLAUDE.md` in the repo. V8 was proposed for cross-phase assumption gaps. **Two new rules earned this session, add them:**
- **Resolve every config key at startup.** `_resolve_config()` must touch every key the run will ever need and die in the first second, not at t=38min.
- **Write the manifest at run start**, with git SHA and config, status `"running"`, then update in place. Provenance that only exists if nothing goes wrong is not provenance. (This was the third consecutive provenance casualty.)

---

## 7. WHERE WE ARE RIGHT NOW — the live thread

### Latest commits
- `be1a19263dffdc8f95c38fa9f3aa450612040d0d` — terrain D4 + buffer rebuild
- `78610c2fccfcac58c3a2ec84f19ff753496c884b` — run.py config fix, `_resolve_config`, `--smoke`, manifest lifecycle. **This is the acceptance-run commit.**

> **All SHAs changed on 2026-08-17.** `Co-Authored-By` trailers were stripped from all 45 commits, which rewrites every SHA from the root forward. Trees are byte-identical; only messages changed.
>
> **The run manifests in `runs/` still record the pre-rewrite SHAs**, and `runs/` is gitignored so they were not repointed. Those commits are kept reachable by the tag **`pre-attribution-strip`** — **do not delete it**, or `git gc` will prune them and every manifest's `git_sha` becomes unresolvable. To resolve a manifest SHA, look it up against that tag.
>
> | manifest records | live equivalent | |
> |---|---|---|
> | `4f5dcc6…` | `be1a192…` | terrain D4 + buffer rebuild |
> | `23e14a2…` | `78610c2…` | Phase 2 acceptance run |
> | `253da06` | `d727952` | context handover |
> | `685b55a` | `20dc1d6` | solver guards |

### The Phase 2 acceptance run (2026-08-16, 39.93 min)

```
steps=39,051  sim=21,600s  wall=2,395.7s
max_courant=0.929 (selected 0.700, exceeding 11,443/39,051 = 29.30%)
depth (canonical): max=23.2239 m  (buffered 23.5355 m)
mass rel-residual=4.105e-05
```

| Depth percentile (canonical) | Value |
|---|---|
| P50 | 0.000000 m (dry) |
| P90 | 0.000997 m |
| P99 | 0.989823 m |
| P99.9 | 3.920597 m |
| Max | 23.223906 m |

Mass balance (reconciles exactly): rain in 140,021,560.02 − drain out 10,489,312.28 − boundary outfall 86,048,231.53 = 43,484,016; surface remaining 43,489,763.75; residual 5,747.54 m³.

Storm ≈ 110–116 mm over 6 h. Boundary outfall is **61.5% of rainfall** — very high, a canyon symptom. Drains take 7.5%, below the 12.8% the capacity raster allows (consistent with most of the domain being dry).

### THE HEADLINE: the D4 hypothesis was falsified

I predicted max depth would drop substantially from 23.86 m once D4 traps hit zero. It went to **23.22 m — a 2.7% drop**. D4 traps are at zero and 6,769 cells still exceed 10 m.

What's actually happening:

| Metric | Value | Base rate | Enrichment |
|---|---|---|---|
| >5 m cells directly on a D8 breach cut | 8,096 / 10,422 = **77.68%** | 3.875% | **20.05×** |
| >5 m cells within 1 cell of a D8 cut | 10,422 / 10,422 = **100.00%** | 17.524% | 5.71× |

**The water was never in the D4 traps. It's in the canyons whitebox carved.** ~~The D4 traps were spatially coincident because the D8 pass *manufactured* 56,816 of them (290,267 pre-breach D4 pits → 347,083 post-D8).~~ I attributed causation to the correlated symptom. The D4 work was still necessary — a 4-connected solver genuinely cannot drain a D4-sealed cell, and step count fell 44,049 → 39,051 — it just wasn't the depth mechanism.

> **CORRECTED 2026-08-17 — my error, and both of my numbers were wrong for the purpose.** I ran two different quantities together:
>
> - **228,227 / 347,083 = 65.8%** is *adjacency to D8 cut cells*. It is a real field — `d4_cardinal_breach.d4_breach_adjacency_split.adjacent_to_d8_breach` in `runs/terrain_conditioning/manifest.json`, verified present. The field name says adjacency; the manifest never claimed causation. **Adjacency is not causation.**
> - **347,083 − 290,267 = 56,816 = 16.4%** is *net increase*. It understates gross creation, because the D8 pass also destroyed pre-existing pits — it resolved 125,374 D8 depressions. 290,267 is **not** in any manifest; its provenance is unrecorded.
>
> A third pair, **195,368 / 296,912 = 65.8%**, reached Darshil after my last review and appears only in the conditioning design response. **Neither number occurs anywhere in this repo.** Treat all three pairs as unreconciled.
>
> **The measurement that settles it is a set difference, not a count difference:** label D4-pit status per cell on the pre-breach conditioned DEM and on the post-D8 DEM, then report pits in the second but not the first (**gross created**), first but not second (**destroyed**), and both (**survivors**). Only the first supports "D8 artifact." It is read-only against two rasters already on disk. Full entry: `OPEN-ITEMS.md` item H.

### THE DISCOVERY: `breach_depressions` is carving out Bengaluru's lakes

Depression inventory on the pre-burn 10 m DEM: **27,609 closed depressions, 28.9 M m³ total storage, 48.9 km² (3.84% of domain).**

The population is sharply bimodal:

| | Area (cells) | Max depth (m) | Volume (m³) |
|---|---|---|---|
| P50 | 2 | 0.06 | 9.40 |
| P90 | 11 | 0.34 | 124.21 |
| P99 | 221 | 1.33 | 6,360.45 |
| ~~Max~~ | ~~39,141~~ | ~~21.67~~ | ~~2,221,792.89~~ |

> **CORRECTED 2026-08-17 — the Max row is column-wise maxima, not one object, and reading it as an object produced a downstream error.** Rank 1 is 39,141 cells at **0.98 m** holding 2,221,792.89 m³. The 21.67 m belongs to rank 17, which is 441 cells. No depression has all three values.
>
> Consequence, because it was acted on: the conditioning design response computed a fill ratio V/(A·D) = 2,221,792.89 / (39,141 × 100 × 21.67) = **0.026** for "the largest object", concluded that its depth must be one or a few deep pixels in an otherwise shallow basin, and recommended screening on P95-of-depth-within-basin rather than max. **That statistic describes no real object.** Rank 1's actual fill ratio is 2,221,792.89 / (39,141 × 100 × 0.98) = **0.579** — an ordinary bowl. The recommendation may still be worth having, but it currently rests on nothing.
>
> The real fill-ratio finding is the opposite one and is worse: see `OPEN-ITEMS.md` item **I**. The large shallow tanks come in near 0.92–0.96, which is a flat floor inside a steep rim — the signature of a DTM rendering a lake as a plateau at water surface.

**Top 50 hold 21,618,287 m³ = 74.83% of all storage. 33 of 50 intersect mapped OSM water.** By name they are Yele Mallappa Shetty (#1), **Varthur (#2)**, Madiwala (#3), **Bellandur (#4)**, Agara (#14), Ulsoor (#19), Hebbal (#20), Lalbagh (#22), Kaikondrahalli (#24), Saul Kere (#41), Yelahanka (#42), Sankey Tank (#44), Yeshwanthpur (#48), Bhattarahalli (#50).

That is the tank cascade, ranked correctly by size, reproduced unprompted. It is simultaneously (a) a strong end-to-end validity check on the whole terrain pipeline and (b) proof that standard hydrological conditioning is destroying the single most important hydraulic feature of Bengaluru's flooding.

Retained, the lakes give lake-appropriate depths: Bellandur is 1.91 M m³ over 19,171 cells = **1.0 m mean, 1.08 m max**. Varthur is 1.30 m mean, 3.91 m max. Not 23 m.

**Excavation is extraordinarily concentrated:** a single connected trench network is **10,176,564 m³ = 48.92% of all 20.8 M m³** of D8 excavation. Top 5 = 55.18%, top 50 = 64.13%. Almost certainly the Bellandur–Varthur–Dakshina Pinakini line carved out of the domain in one incision.

D8 cut depth: P50 6.6 cm (correct noise-breaching), P90 0.65 m, P99 10.08 m, P99.9 19.80 m, max 22.94 m across 465,927 canonical cut cells.

**Proposed threshold** (falls out of the bimodality, not chosen by feel): retain depressions with **area ≥ ~100 cells (1 ha) OR max depth ≥ ~1 m** — roughly 300 objects out of 27,609, few enough to inspect individually. Breach the other ~27,300 (median 2 cells, 6 cm) as genuine DEM noise.

**Third class — 17 of the top 50 don't intersect mapped water** and share a small-area/extreme-depth signature (#37 is 159 cells at 18.59 m; #9 is 833 cells at 18.23 m; #5 is 1,359 cells at 15.25 m). They cluster at **13.09–13.12 N / 77.645–77.70 E** (twelve of them, Hennur/Bagalur) and **12.83–12.85 N** (five, toward Anekal). Both are granite quarrying belts. They hold ~5.31 M m³ = 24.6% of top-50 storage. Working hypothesis: quarries — real holes that really hold water, retain them but **tag them** so the frontend never renders 18 m of quarry water as street flood depth. If instead they're DEM voids they need filling, not retaining. Confirmation is in flight.

### Courant, fully diagnosed

Peak was step **16**, t = 160 s, Δt = 10.0 s, h_start = 0.049949 m → h_end = 0.087951 m.
`0.700 × √(0.087951/0.049949) = 0.700 × 1.3270 = 0.9289` — exact.

The overshoot is large only when depth grows fast *relative to itself*, i.e. at storm onset from a dry bed. The exceeding-step depth distribution (P10 4.07 m, P50 17.95 m) says most exceedances are in deep water, but **magnitude was never reported**, so "99.59% in deep water" is unsupported (Class 6). The peak step itself sits at 8.8 cm, in the bottom 0.41% of that distribution.

**Fix:** two-line rejection in the recording pass — compute h_new, check realized Courant, if it exceeds the ceiling halve Δt and redo. Record-then-replay makes this free; the replayed schedule just has smaller steps where needed and the differentiable graph stays fixed.

At 0.929 there is 7% of margin to instability. Not a correctness problem now (the run completed, residual 4.1e-05), but Phase 4 will run hundreds of storms and an instability in scenario 237 of 500 silently poisons the surrogate training set.

### The building-breach issue — real, but deferred on purpose

D4 cardinal breach lowered 332,080 cells (mean 0.7871 m, max 25.04 m): building 46,261 / waterway 17,756 / road 121,611 / plain 146,452. Lowering on building cells clusters tightly at the +3.0 m burn height (P10 2.679, P50 2.955, P90 3.136) — it removes the burn rather than excavating below grade.

Residual height above the lowest non-building cardinal neighbour: **P10 7.5 mm, P50 26.5 cm, P90 87.4 cm.** Roughly two-thirds sit below 0.5 m, i.e. hydraulically transparent in any flood worth simulating — on the order of 30,000 ten-metre-wide gaps, concentrated along the carved drainage network rather than randomly placed.

**Do not fix this yet.** ~~65.8% of D4 pits were D8 artifacts, so removing the deep cuts should remove most of the staircases that forced these breaches.~~ Re-measure after the conditioning fix; the problem plausibly dissolves.

> **JUSTIFICATION REPLACED 2026-08-17.** The struck sentence rests on the 65.8% figure corrected above, which is an adjacency fraction. But the deeper mistake is that **pit count was the wrong variable altogether** — the deferral does not need the pit accounting and should never have been argued from it.
>
> **The damage is driven by cut depth.** The 25.04 m lowering happened because D8 carved a 22.94 m trench past that cell, not because a pit existed there. With cut depth at P99 = 10.08 m, P99.9 = 19.80 m, and a single connected trench holding 48.92% of all 20.8 M m³ of excavation, removing the deep cuts removes the mechanism that forces deep staircases — **however the pit accounting in `OPEN-ITEMS.md` item H resolves.**
>
> That is a claim about the mechanism rather than about a correlation, so unlike the original it survives H in either direction. The deferral stands; the reason for it has changed.

### Two prompts are in flight right now

**Claude** — writing to `/tmp/conditioning-design.md`, no repo edits, no commits. Task: design a depression-aware conditioning scheme replacing `whitebox breach_depressions` + D4 patch. Must specify the retain/breach classification rule justified against the measured bimodality; how retained basins connect downstream through the OSM waterway network so the cascade routes rather than each lake acting as an isolated sink; the quarry class and its tagging; how D4 connectivity is enforced outside retained basins without staircasing through building burns; the revised invariant with enumerated residual; and what re-measurement confirms the building damage dissolved.

**Flash** — two read-only measurements:
1. Courant exceedance **magnitude** (realized − 0.700) at P50/P90/P99/max, cross-tabbed against setting-cell depth in bands <0.5 / 0.5–5 / 5–15 / >15 m, reporting max realized Courant *within each band*.
2. For each of the 17 unmapped top-50 depressions: OSM query for `landuse=quarry`, `man_made=mineshaft`, industrial or water polygons within 200 m; plus in-basin DEM stats (cell count, min/max elevation, elevation std). Flat floor + vertical walls = real quarry; single-cell spike or NaN-adjacent hole = DEM void.

---

## 8. Open items — see [`OPEN-ITEMS.md`](../OPEN-ITEMS.md)

**The list that used to live here has moved to `OPEN-ITEMS.md` in full, as items E–U, and that file is now the single source of truth.** It was reconciled on 2026-08-17 against this section and against the Phase 2 acceptance run; the copy here had already drifted, and duplicating it is how the drift happened. Do not re-list items in this file — add them there.

What does not survive a move to a bulleted list, and is therefore kept here:

**The ordering rationale. The list is ordered by information value per unit cost, not by size and not by phase number.** Concretely:

1. **The cheapest discriminating measurements come first, ahead of the highest-priority defect.** The D4 pit set difference (item H) and the tank floor-σ_z measurement (item I) are read-only jobs against rasters already on disk — minutes, no GPU, no rebuild. They lead not because they matter most but because they cost almost nothing and they *gate* the thing that does. Conditioning (item F) is the highest-priority defect and is deliberately not first: redesigning it before H and I land is redesigning against a theory that has not been confirmed.
2. **Anything that would be baked into the Phase 4 training set is pulled forward.** Courant step rejection (K), the `track_cell_sinks` flag (M), the non-negativity tolerance (L) — all small, all otherwise silently poisoning hundreds of scenarios. Small-and-urgent beats large-and-important when the cost of deferring is contamination rather than delay.
3. **Build items are ordered by what they unblock, not by effort.** `checkpointing.py` segmentation (N) is the next real build item *after* conditioning, because it gates the differentiable calibration path and therefore all of Phase 3.
4. **Rebuild precedes verification.** The 22 skipped full-domain invariants (O) are run *once*, against the DEM we intend to keep — i.e. after F's rebuild, or the run is spent on a DEM about to be replaced.

**The two decisions that closed** — KSNDMC optional rather than a dependency, and Bhuvan abandoned — are recorded in `OPEN-ITEMS.md` items 1b, 3 and 4, together with the consequence that matters: the forcing layer is written against a clean internal interface with Open-Meteo as the working adapter, so the cumulative-vs-incremental question blocks only a future KSNDMC adapter, not any forcing code.

---

## 9. Files

**Enumerated from the working tree on 2026-08-17, not from the previous version of this list — which was wrong about every file it named in `docs/` and about six of eleven solver modules.**

```
src/jaladhar/terrain/    grid.py fetch.py buildings.py roads.py roughness.py
                         drains.py conditioning.py derived.py build.py      (9, all present)
src/jaladhar/solver/     state.py acc.py timestep.py mass.py run.py
                         analytical/kinematic.py                            (6, all present)
src/jaladhar/forcing/    fetch_imerg.py
src/jaladhar/validation/ fetch_s1.py
src/jaladhar/{api,scenarios,surrogate}/   package stubs only — nothing built
tests/                   test_terrain_grid.py (45) test_solver_invariants.py (29 pass, 22 skip)
                         test_manifest_provenance.py (23 pass, 4 skip — DEBT, closable now)
configs/                 domain_bengaluru.yaml compute.yaml solver.yaml
docs/                    CONTEXT-HANDOVER.md (this file) phase2-writeup.md
                         phase2-diagnostics.md d4-audit.md walkthrough.md
                         ksndmc_data_request_DRAFT.md                       (6 files)
PROMPT.md                at the repo ROOT — the master build prompt, 590 lines.
                         CLAUDE.md:3 links to it. docs/PROMPT.md was a byte-identical
                         duplicate and has been deleted.
CLAUDE.md                standing rules V1–V8
OPEN-ITEMS.md            items 1–9, A–D (Phase 0 gate) and E–U (Phases 1–2)
runs/                    14 stage directories, 13 carrying a manifest.json
```

**Six solver modules this list previously named do not exist**, and only two of them were flagged as unbuilt:

| Named | Reality |
|---|---|
| `limiter.py` `boundaries.py` `sinks.py` `storms.py` | **Never existed.** The logic lives inside `acc.py`, `timestep.py` and `run.py` — `_boundary_outflux` is in `acc.py` (the module of the in-place-write incident, §6), `uniform_storm` is in `run.py`. |
| `checkpointing.py` `tiling.py` | **Not built** — `OPEN-ITEMS.md` items N and P. Correctly flagged elsewhere, but listed here as if they were files on disk. |

This matters operationally: an agent told the limiter lives in `solver/limiter.py` either stalls or creates the file and duplicates logic that is already in `acc.py`.

**The master build prompt.** Previously described here as an external 566-line file to be carried over and placed in `docs/`. **That was wrong in both particulars.** It was already in the repo at the root as `PROMPT.md`, and it is **590 lines**, not 566 — no revision of it was ever 566 (history: 506 → 583 → 589 → 590; the 566 was a stale count taken from a snapshot dated 2026-08-14 16:36, before the last three edits). Nothing forked; the external copy was never downloaded. Structure is as described — §1 Mission, §2 Standing rules, §3 Environment, §4 Repo structure, §5–§13 Phases 0–8, §14 known limitations, §15 prior art, §16 first command, plus a closing Reference links section.

Acting on the old instruction produced `docs/PROMPT.md` as a byte-identical second copy in commit `d727952`. **Root `PROMPT.md` is the master and is what every prompt and agent in this repo's history references.** The duplicate has been deleted.

---

## 10. Numbers you'll want without re-deriving

| | |
|---|---|
| Buildings (buffered / canonical) | 1,068,931 / 912,456 — OSM 786,982/735,463, MS gap-fill 281,949/176,993 |
| Road segments rasterized (buffered / canonical) | 173,859 / 169,124 |
| Building cells / override cells / contested | 1,820,100 / 1,559,490 / 260,610 (buffered) |
| D4 breach | 347,083 pits → 0 in 2 iterations, 0 cells raised, 332,080 lowered |
| D8 breach alone | 482,479 cells, mean 0.4312 m, max 22.9444 m, 20,802,918 m³ |
| D8 per-class | building 4,997 (max 3.485) / road 178,554 / waterway 34,307 / plain 269,168 |
| Terrain build wall-clock | 325.96 s |
| Acceptance run wall-clock | 2,395.70 s |
| dt schedule sidecar | `runs/solver/dt_schedule.f32.gz`, 72,376 bytes |

---

## 11. First moves in the new session

1. `Read CLAUDE.md` and `Read docs/CONTEXT-HANDOVER.md` (this file), then `OPEN-ITEMS.md` — §8 is a pointer, so the open list is only in that file.
2. `git log --oneline -10` and `git status` — confirm you're at `78610c2` or later and the tree is clean. **If the tree is dirty, stop and report it before anything else** — three tracked files were once deleted unstaged and an untracked one was lost outright, which is why `docs/d4-audit.md` and `docs/walkthrough.md` are marked as reconstructions.
3. Ask Darshil for anything in flight. As of 2026-08-17 both prior in-flight prompts have landed: the conditioning design (pasted, not written to the repo) and Flash's two measurements (`response.md` at the repo root, untracked).
4. **You now have repo access, which the previous advisor session did not.** Use it — verify agent claims directly instead of trusting reports. That is a real upgrade and it should change how you review.
5. **Resist implementing.** Your value is adjudication and prompt-writing. If you start editing solver code you stop being the reviewer and there is no one left to catch Class 2 errors.
