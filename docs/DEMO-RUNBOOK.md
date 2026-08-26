# JALADHAR demo runbook

Rebuilt for the WF-6 operator dashboard (2026-08-26). CPU-only, fail-closed throughout: the launcher
and the dashboard read already-produced manifests and product bytes; neither runs the solver nor
creates a product. Every number below is traceable to a file in `runs/` or `data/` — figures that
depend on which product is loaded are marked **current realized value** and move with the next run.

---

## 1. Cold start

### Option A — dashboard only

```bash
.venv/bin/python -m jaladhar.web.app serve          # http://127.0.0.1:8503 (default port)
```

No `--product` needed. The server discovers the newest valid frame-series run under `runs/` by
itself — currently `runs/wf3_replay2_uncoupled_baseline_frames_v2` (**97 frames at 30-minute
cadence covering Sep 4 00:00Z → Sep 6 00:00Z 2022 (48 h replay)**, storage-water exclusion per the
owner-adjudicated v6 decision of 2026-08-26) — preferring a series over flat
event-maximum products (`selection_order` is recorded in `/api/state`). Candidates that fail
validation are skipped with a stderr warning and named with reasons in `state.skipped_candidates`;
they do not abort the store.

> **OPEN ANOMALY (carried, never suppressed):** 61 flooded segments still read 301–369 cm AFTER
> the storage-water exclusion — unexplained by it; carried OPEN in every produced manifest until a
> measured cause exists.

What appears, in order:

1. **Boot overlay** ("Loading …"), then the dark full-city basemap. Cold boot to interactive took a
   measured ~70 s in the integration smoke (`smoke_results_final.json`: `__bootStage='done' after
   69.8 s`), most of it frame warming; the NOW heading reads `WARMING FRAMES n/97` then
   `LOADING FRAMES…` while this happens. Do not narrate over it.
2. **Mode badge** (top right): `REPLAY · SEP 2022 HINDCAST`, subline `48 h replay · valid times
   Sep 4–6 2022 · not forecast leads`. Month/year and span are computed from the payload's own
   timestamps; the clause `not forecast leads` is unconditional. The badge tooltip states plainly
   that these are valid times of a hindcast replay, not forecast leads.
3. **Watchlist panel** (left, `STREETS AT RISK`): ranked flooded streets for the held frame with
   ward chips, depth bands, and the lead-kind honesty line `hindcast offsets — not forecast leads`.
   Count chip and top rows are **current realized value** (held frame tonight: 833 flooded streets,
   top row Chikkagubbi Main Road, band shown as `0.00–8.01 m`).
4. **Intersections** section at the panel bottom: junction rows reading `blocks N approaches`.
5. **Hyetograph**: rainfall-intensity bars above the timeline scrubber (62 of 96 intervals drew
   bars in the smoke; zero-rain intervals correctly draw nothing). Click/drag on it seeks.
6. **Playback cluster** (bottom right): step back/forward, speed 1x/2x/5x, LOOP toggle, and the KPI
   line `% of network flooded · n/total`. Play auto-runs **once** across all 97 frames (~3 s) and
   holds at the final frame.

### Option B — launcher (dashboard + routing API)

```bash
./scripts/demo/run_demo.sh        # same auto-discovery; Ctrl-C stops both services
```

Dashboard on `127.0.0.1:8501`, routing API on `127.0.0.1:8502`. Child environment forces
`JALADHAR_CPU_ONLY=1`, `JALADHAR_DEVICE=cpu`, `CUDA_VISIBLE_DEVICES=''`, `NVIDIA_VISIBLE_DEVICES=''`,
`TORCH_DEVICE=cpu`.

**`ROUTING_STATE=not_ready` is EXPECTED and honest — the demo proceeds.** The cited two-class wading
policy loads (`data/curation/vehicle_wading_policy.json`: passenger car grade B, emergency/heavy
grade C-industry; `bus_truck` BLOCKED documented) and the 25.0 m snap cap is configured (internal
precedent, RATIFICATION-PENDING). Remaining routing blockers: the **scientific gate** — G1 rescore
FAIL, 104/399 hits, lift 1.30 under the frozen ≥0.60/≥3.0 thresholds
(`runs/wf3_replay2_gates_v7_excluded/g1_score.json`, bound into `/health` as path+sha256) — and the
**series-shape depth product**: routing binds the flat v6 product until a series-aware loader exists
(owner decision recorded); auto-discovery prefers the frames series. Only a dashboard failure blocks
startup.

---

## 2. The five-step flow

Click paths are exact; SAY beats are scripted. Read them aloud as written — they are the honesty
story, and improvising around them is how a fabricated claim gets into the room.

### S1 — the city as loaded

Look at the held view after the single auto-play sweep settles. Water sits where the model put it
at the final replay frame; the hero stats (flooded segments, deepest reading) were computed from
that frame's bytes.

> **SAY:** "This is a historical replay of September 2022 — Bengaluru's largest recent event — not
> a live feed. The header says so: valid times, not forecast leads. Everything on screen is model
> output read from files on this machine."

### S2 — advance time

Press **▶** (replays the whole event over ~3 s), set **speed 2x**, or drag the **scrubber**. While
it moves, water grows between frames instead of snapping, and the rainfall **hyetograph** above the
scrubber shows the storm driving it — correlate the spike clusters to the blooming on the map and
to the climbing KPI line `% of network flooded · n/total`. The KPI trend arrow compares only two
real adjacent frames — it hides mid-interpolation rather than guessing. The series peak is worth
saying out loud: **36,268 segments flooded at Sep 4 21:30Z — 03:00 IST** (the small hours of Monday
morning), shown in the rail's PEAK panel with the manifest path beneath it; the curve bottoms at
1,578 (Sep 4 09:30Z) and ends at 21,386 (final frame). All three counts are read from the frames
manifest at load — nothing on screen is typed.

> **SAY:** "The rain drives the flooding — watch the intensity spikes land on the map a few frames
> later. The percentage line is recomputed from the loaded frame's own status codes."

### S3 — read WHICH streets

Read the **STREETS AT RISK** panel top rows: named streets with ward chips and depth bands in
cm (bands above 100 cm render in metres). The status line under the sort bar names the lead kind —
tonight `hindcast offsets — not forecast leads`; a forecast run would read `leads from issued run
+N min`. Toggle **Soonest** to reorder by lead instead of severity (rows without a numeric lead
sink rather than inventing a position). Open **Intersections**: each row names crossing streets and
reads `blocks N approaches` — a topological claim we can support.

> **SAY:** "These are the streets the model floods deepest first — named streets, ward, depth band.
> And the panel tells you honestly that these times are offsets into a past event, not forecasts."

### S4 — WHY (drains and causality)

Click a watchlist row: the map **flies to it** (~420 ms eased, clamped like a pan), the segment
highlights, and the right-rail inspector fills — name, nearest-landmark-and-ward context line,
depth band, status badge with impact tooltip, and the provenance path in mono at the bottom.
Toggle the **drains** layer: the rajakaluve network fades in (~420 ms), observed reaches solid,
synthesised connectors dashed and dimmer, snapshot fingerprint named in the panel. Below the
inspector, the causal panel renders a pill and statement: hollow amber **PREDICTED**
("Predicted overcapacity nearby: drain edge N exceeds design capacity (cited design storm) — not a
measured surcharge") or faint **NONE** ("No measured surcharge for this street — coupled run
pending."). A red pulsing MEASURED pill can only come from a coupled run; none exists, so none
renders.

> **SAY:** "Predicted overcapacity from cited design-storm capacities; measured surcharge arrives
> with the coupled run — the screen distinguishes the two."

### S5 — route around it (currently blocked)

There is no route to show, deliberately. The wading policy IS cited
(`data/curation/vehicle_wading_policy.json`) and the snap cap is configured (25.0 m), but the
scientific gate has not passed — the G1 rescore is FAIL under frozen thresholds — so `/route`
refuses every request rather than faking one.

> **SAY (scripted):** "I'm not showing you a route, and that is the honest feature. Vehicle classes
> are cited; buses/trucks are blocked — no published limit exists for them. Routing awaits the
> scientific gate: the prediction does not yet clear its signed bar — we refuse to route on depths
> we cannot yet trust."

---

## 3. What this does NOT do yet

Say these before someone discovers them.

- **No forecast run.** A2/A4 are ABSENT until a run issued at time T uses only rainfall known at
  T. Every timing on screen is a hindcast offset; the badge repeats `not forecast leads`
  unconditionally.
- **No coupled run.** Surface and drains are not hydraulically coupled yet; that is why the causal
  panel carries the PREDICTED-vs-MEASURED distinction and why nothing pulses.
- **No live IMD ingestion.** The LIVE mode architecture exists end to end (badge, ingested-at
  subline, quiet-day state), but no IMD-driven simulation has been produced; the dashboard never
  borrows the replay run under a LIVE badge.
- **Routing gate closed.** See S5 and the adjudication queue.
- **Storage-water exclusion is ADOPTED** (owner adjudication 2026-08-26): deepest readings come
  from the excluded `frames_v2` series; the pre-exclusion numbers are retired-not-deleted
  (`runs/wf3_replay2_gates_v6/RETIRED.adjudicated-20260826.md`). One anomaly stays OPEN: 61
  segments at 301–369 cm, unexplained after exclusion — never suppressed.
- **Scheduler ABSENT BY DESIGN.** One pre-computed run suffices for this demo; polling IMD on a
  timer would add a moving part without adding information.
  *ROADMAP NOTE:* a scheduler is a 15–30 minute loop around the existing pipeline — discover
  forcing, run, publish product, dashboard picks it up — and it is the component that turns this
  from a demo into an operational service.

---

## 4. Adjudication queue for the owner

One list, for the morning standup. Nothing below is actionable by agents.

1. **v6 adoption (storage-water exclusion).** Before/after, recorded in
   `runs/wf3_replay2_uncoupled_baseline_v6/manifest.json` → `variant_summaries`:
   flooded segments **40,519 → 33,284**; maximum band **1,128 → 369 cm**; flooded p50/p90
   47/151 → 44/134 cm; `no_data` rows 7,047 → 16,286; segments still deeper than 300 cm after
   exclusion: 61 (`residual_anomaly` — reported, not explained away). Default variant is
   `segment_touch_class_{1,2,3}`, the audit's touch-test semantics. Manifest `owner_gate` note:
   G1 was scored on the contaminated v5 product; v6 becomes default only after owner adjudication;
   no G1/WF-4 rescore was run in that unit. Context for the decision: the standing G1 baseline —
   **182/399 hits (0.456) against a null mean of 0.323, lift 1.41, FAIL against the signed ≥0.60 /
   ≥3.0** (`runs/wf3_replay2_gates_v6/g1_score.json`) — was scored on the contaminated bytes (its
   `predicted_flooded_segments` = 40,519, the no-exclusion variant).
2. **Frames-over-flat default order.** Cold discovery ranks a valid frame series above flat
   event-maximum products (`SELECTION_ORDER =
   "frame_series_over_flat_event_maximum__newest_valid_first"`, surfaced in `/api/state`). An
   event maximum is a summary of the series; reversing the order is an owner call, not a code one.
3. **Vehicle-wading policy ADOPTED; ratification remains.** The cited two-class policy
   (`data/curation/vehicle_wading_policy.json`, grades B / C-industry, `bus_truck` BLOCKED) serves;
   owner-gated still: the 25.0 m snap basis ratification and the G1 scientific gate itself.
4. **Ward-vintage configuration out of code constants.** The product-kind → ward-vintage mapping
   (`historical_replay → "2022"`) is a literal in `src/jaladhar/web/static/app.js` with `"2022"`
   fallbacks repeated there and in `engine.js`. It belongs in backend config, served with the
   context assets.

If any preflight or rehearsal check fails, show the failure as the result and stop. A credible
empty state is preferable to a plausible-looking map built from invented input.

## KNOWN LIMITATION — routing binds flat event maximum (measured 2026-08-26)

Routing currently binds the v6 EVENT-MAXIMUM product, so it blocks every segment
that flooded AT ANY POINT in 48 h (plus unknown-state segments): 46,739 blocked
for passenger cars, 36,017 for emergency/heavy. Measured consequence
(runs/wf6_routing_feasibility/direct_results.json): dry baselines route fine
(48.4 / 33.6 / 45.5 km across NW-SE, N-S, W-E pairs), but under event-maximum
blocking ZERO cross-city routes exist — every corridor is severed. This is
demo-breaking for live route requests until fixed.

CORRECT DESIGN (recorded, not yet built): SERIES-AWARE ROUTING — block on the
SELECTED FRAME's flood state (frame 0 blocks nothing; peak frame blocks most),
so an operator routes on conditions at the time they care about. Per-frame
rescoring of G1 remains prohibited (event-level question); this limitation is
about ROUTE execution, not gate scoring. Until series-aware routing lands,
route requests honestly refuse or return no-flood-safe-route; presenters say so.

## DEMO DEFAULT PRODUCT — warm-start 3 h forecast (adjudicated 2026-08-26)

Serve `.venv/bin/python -m jaladhar.web.app serve --product
runs/wf3_uncoupled_3h_forecast_warm_product/products` for the demo: this is the
artefact that shows PREDICTION (issued 2026-08-26T11:43Z, lead 0). Header reads
the run id + `FORECAST · lead 0 min`. The event maximum (v6 / 48 H EVENT
MAXIMUM) is a VALIDATION artefact — keep it for gate scoring narratives only.

Routing reality under it (runs/wf6_routing_feasibility/): Beat-6 route-around
succeeds for ~43% of clicked flooded streets as passenger car (~57%
emergency/heavy) — better than event-maximum (20%/72%) but a majority of
clicks still honestly refuse. Cross-city pairs remain unroutable under any
product (graph fragmentation: dry baseline itself fails ~half of random pairs;
107k components, no intersection snapping). Series-aware routing stays the
correct post-demo design.

FORECAST G1: BLOCKED ON PRODUCER — the warm-run manifest lacks the results/
groundtruth-provenance block score_g1.py requires, so no complaint-bound score
exists for this product yet and its chip reads UNAVAILABLE with reason until
the producing session emits scoring provenance.
