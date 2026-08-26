# JALADHAR frontend — design guide

**Written 2026-08-25 for the 2026-08-28 internal round.** This is the specification for what the
dashboard must look like and feel like. It is not a suggestion document; the values here are meant to
be typed in.

---

## 1. The goal, in one sentence

> A judge who has never heard of this project should look at the screen for three seconds and think
> *"that is an operational system a city actually runs"* — not *"that is a student project."*

Everything below serves that sentence.

The second goal, which is what actually wins: **make the invisible visible.** SIH 26085 says urban
flooding is dictated by "heavily strained, **invisible** drainage networks." Nobody else in the room
will render the drainage network. We will, with nodes lighting up as they surcharge. That single view
*is* the problem statement, drawn.

---

## 2. What we are emulating

Reference points, and the specific thing to steal from each:

| Reference | Steal this |
|---|---|
| **Windy.com** | Dark ground, luminous data layer. The data glows; the map recedes. |
| **Google Flood Hub** | Restraint and authority. Few colours, enormous confidence, no clutter. |
| **deck.gl / Uber urban demos** | Depth-as-light. Intensity encoded by brightness, not just hue. |
| **Grafana / Datadog dark consoles** | Tabular numerals, tight grid, one accent colour, everything else grey. |
| **Air-traffic and grid-operator displays** | Motion means something. Nothing animates decoratively. |

The common thread: **dark background, one luminous data layer, ruthless restraint everywhere else.**
Colour is a scarce resource spent only on water.

---

## 3. Technical approach — and why not a map library

**Recommendation: keep the self-rendered Canvas 2D approach. Do not add MapLibre/Leaflet/deck.gl.**

Reasoning, in order of weight:

1. **Zero new failure modes three days before a demo.** A vendored library that fails to load, a
   tile request that needs wifi, a CSP surprise — each is a way the screen goes blank in the room.
   The current renderer already works.
2. **We already have the basemap data locally.** `src/jaladhar/routing/graph.py` loads an OSM road
   network from `data/raw/osm/`. Roads, lakes and the drain network all render from local GeoJSON.
   No tile server, no network, works on a plane.
3. **Canvas 2D does glow natively.** `shadowBlur` + `globalCompositeOperation = 'lighter'` gives
   real bloom. That is the entire visual signature and it is about fifteen lines.
4. **Total pixel control.** Every effect below is reachable; none needs a library.

The one thing you lose is free pan/zoom inertia. Implement it: pointer drag to pan, wheel to zoom
about the cursor, a single affine transform applied before drawing. Roughly forty lines.

**Render order matters** — draw back to front:

```
1. background wash        (radial gradient, near-black)
2. lakes                  (filled, deep blue, very low alpha)
3. roads: dry             (thin, dim grey)
4. drain network          (dim, dashed, only when layer is on)
5. roads: flooded         (thick, depth-ramped, GLOWING)
6. surcharging nodes      (pulsing, additive blend)
7. selection highlight
8. labels / landmarks
```

Two canvases stacked, not one: a **static layer** (background, lakes, dry roads, drains) redrawn only
on pan/zoom, and a **dynamic layer** (flooded roads, nodes, selection) redrawn every frame. At
176,171 segments this is the difference between 60 fps and a slideshow.

---

## 4. The visual system

### Palette — type these values

```css
:root {
  /* ground */
  --bg-deep:      #05070D;   /* page, outermost */
  --bg:           #0A0E17;   /* map ground */
  --panel:        #10151F;   /* panels */
  --panel-hi:     #161C28;   /* hover / raised */
  --line:         #1E2634;   /* hairlines, borders */

  /* ink */
  --ink:          #E9EDF5;   /* primary text */
  --ink-dim:      #94A0B4;   /* secondary */
  --ink-faint:    #5A6579;   /* tertiary, captions */

  /* geography */
  --road-dry:     #232B3A;   /* dry road */
  --road-major:   #2C3546;   /* arterials, slightly brighter */
  --lake:         #0C2438;   /* lake fill */
  --lake-edge:    #16405E;   /* lake outline */
  --drain:        #33415A;   /* rajakaluve network */

  /* water depth ramp — the only saturated colours on screen */
  --d1:           #22D3EE;   /* 0.10–0.15 m  passable */
  --d2:           #FACC15;   /* 0.15–0.30 m  caution */
  --d3:           #FB923C;   /* 0.30–0.50 m  hazardous */
  --d4:           #EF4444;   /* > 0.50 m     impassable */
  --surcharge:    #F43F5E;   /* node discharging to surface */

  --accent:       #38BDF8;   /* UI accent: focus, active tab, slider */
}
```

**Discipline:** the depth ramp and the accent are the only saturated colours. Everything structural is
grey-blue. If a panel needs to stand out, use spacing or a hairline — not colour.

**Glow scales with depth.** Shallow water is thin and dim; deep water is thick and blooming.

```js
const DEPTH = [
  { max: 0.15, color: '#22D3EE', width: 1.6, blur:  4 },
  { max: 0.30, color: '#FACC15', width: 2.4, blur:  8 },
  { max: 0.50, color: '#FB923C', width: 3.2, blur: 14 },
  { max: Infinity, color: '#EF4444', width: 4.2, blur: 22 },
];
// ctx.shadowColor = color; ctx.shadowBlur = blur * dpr;
// ctx.globalCompositeOperation = 'lighter';  // additive — overlaps bloom
```

### Type

System stack — no font file to fail to load:

```css
font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
```

**Every number uses `font-variant-numeric: tabular-nums`.** Without it, digits jitter as the timeline
scrubs and the whole thing reads as amateur. This is the single highest-ratio detail in the document.

| Role | Size | Weight | Tracking |
|---|---|---|---|
| Hero stat | 44–56 px | 300 | −0.02em |
| Hero unit / suffix | 16 px | 400 | 0 |
| Panel heading | 11 px | 600 | **+0.14em, uppercase** |
| Body | 13–14 px | 400 | 0 |
| Caption / provenance | 11 px | 400 | 0 |
| Mono (paths, hashes) | 11 px | 400 | `ui-monospace, "SF Mono", Menlo, monospace` |

Uppercase micro-headings with wide tracking are the strongest single signal of "operational console."

### Space and shape

- 8 px base grid. Panel padding 16–20 px. Gaps 12 px.
- Radius: 10 px panels, 6 px controls, 999 px pills.
- Borders are `1px solid var(--line)` — never thicker.
- **No drop shadows on panels.** Depth comes from surface lightness (`--panel` vs `--panel-hi`).
  Shadows on dark UI read as muddy.

### Motion

```css
--ease: cubic-bezier(0.4, 0, 0.2, 1);
--fast: 160ms; --base: 240ms; --slow: 420ms;
```

Rules:
- **Nothing animates decoratively.** Motion encodes state change only.
- Timeline scrub: interpolate depth between frames — water should *grow*, never snap.
- Surcharging nodes: 1.4 s pulse, radius and alpha, `ease-in-out`. Only surcharging nodes pulse.
  Nothing else on screen moves at rest.
- Hover: 160 ms. Panel open: 240 ms. Layer toggle: 420 ms cross-fade.
- `@media (prefers-reduced-motion: reduce)` → all durations to 0.01ms.

---

## 5. Layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│  JALADHAR   Bengaluru · Urban Flood Nowcast        ● LIVE   [run label]  │  56px
├───────────────────────────────────────────────┬──────────────────────────┤
│                                               │  NOW +45 MIN             │
│                                               │  ┌────────────────────┐  │
│                                               │  │      2,341         │  │
│                  MAP                          │  │ segments flooded   │  │
│            (fills all space)                  │  └────────────────────┘  │
│                                               │  ┌────────────────────┐  │
│                                               │  │      38 cm         │  │
│                                               │  │ deepest, ORR       │  │
│  ┌─ legend ──────────┐                        │  └────────────────────┘  │
│  │ ▁ 10  ▃ 15  ▅ 30  │                        │                          │
│  │ ▇ 50+ cm          │                        │  SELECTED SEGMENT        │
│  └───────────────────┘                        │  Marathahalli Bridge Rd  │
├───────────────────────────────────────────────┤  depth   25–30 cm        │
│  ◀▶  ├────●──────────────────────┤  +3h       │  status  IMPASSABLE      │
│      now  +45m                              │  surcharge  NODE-1042      │
│  [▶ play]   layers: ⬢roads ⬡drains ⬡lakes    │  ─────────────────────    │
└───────────────────────────────────────────────┤  source  runs/…/man.json │
                                                └──────────────────────────┘
```

- **Map dominates.** Right rail 320–360 px, bottom bar 96 px, everything else map.
- Header 56 px, hairline bottom border, product name left, run label right.
- **Two hero stats maximum.** Flooded segment count and deepest reading. More than two and none of
  them land.
- Legend floats bottom-left over the map, `--panel` at 92% opacity.
- The provenance line lives at the bottom of the detail panel in mono, 11 px, `--ink-faint`.

---

## 6. The four moments that make it land

Build in this order. Each is a beat in the demo.

### Moment 1 — the cold open
Dark Bengaluru. Dim road mesh, lakes as deep blue voids, nothing flooded. Held for two seconds it
should already look expensive. **This is 80% of the impression and it is pure styling** — no data
needed. Build it first.

### Moment 2 — press play *(the one that wins)*
Hit play and water blooms across the city over the 3-hour horizon. Segments light up cyan, deepen
through yellow and orange to red, glow spreading as depth grows. The hero count climbs with tabular
digits that don't jitter.

Three seconds of animation communicates "nowcast" better than any slide. Auto-play on load, loop
once, then hold at +3h.

### Moment 3 — reveal the drains *(the differentiator)*
Toggle the drain layer. The rajakaluve network fades in beneath the roads. Then surcharging nodes
begin to pulse `--surcharge`.

Say out loud: *"the water on that street came out of this drain."*

Nobody else will have this. It is 26085's actual problem statement, rendered. If the coupling lands,
the nodes pulse from measured surcharge volume; if it doesn't, show the network without pulses and
say plainly that the surcharge model is in progress — the network alone is still more than anyone
else shows.

### Moment 4 — click a street *(the credibility beat)*
Click a flooded segment. The panel fills: street name, depth band in cm, status, the drain node
responsible — and **the manifest path the number came from**, in mono.

That last line is unusual and technical judges notice it. Every number on screen can be traced to a
file. Most projects cannot say that; say it out loud.

---

## 7. Details that separate good from expensive

1. **`tabular-nums` on every number.** Repeated because it is the most-skipped, highest-impact line.
2. **Device-pixel-ratio scaling.** `canvas.width = cssW * devicePixelRatio` and scale the context, or
   everything is soft on a retina screen and looks cheap.
3. **Hairlines at exactly 1 device pixel** — `1/dpr` in canvas units.
4. **Round line caps and joins** on roads: `ctx.lineCap = 'round'; ctx.lineJoin = 'round'`.
5. **Background is a radial gradient**, not flat — slightly lighter under the city centre. Subtle,
   but flat black reads as unfinished.
6. **Additive blending on the water layer.** Overlapping flooded segments bloom brighter where
   flooding concentrates, which is both prettier and truer.
7. **Ease the timeline**, don't snap between frames.
8. **Empty state must be designed**, not a bare sentence. Centred, `--ink-dim`, one line explaining
   what is missing. It will be seen if something fails, so make it look deliberate.
9. **Cursor `crosshair` over the map**, `pointer` over segments.
10. **Zoom about the cursor**, not the canvas centre. Everyone notices when this is wrong and nobody
    can say why.

---

## 8. Rules that do not bend

The dashboard is under CLAUDE.md like everything else:

- **Rule 3 — no hardcoded metrics.** Every number is read from a product file at load. The provenance
  line must resolve to a real manifest. The audit is: grep the JS for numeric literals; anything that
  is not a pixel, a duration, or a unit conversion is a violation.
- **Rule 2 — no placeholder physics.** No invented flood field to make the map look alive. If no
  product is loaded, the designed empty state shows.
- **Pinning the demo to a specific run directory is fine and expected.** That is real model output,
  loaded from a manifest, labelled with which run it is. It is not fabrication and needs no apology.
- **Label the run honestly in the header.** "Uncoupled baseline — replay #2" or "Coupled — run
  <id>". The label changes; nothing else does.
- Depth is shown as a **band in cm** per segment, never as a per-square-metre point depth.

---

## 9. Build order if time runs out

Strictly this order. Each step is independently presentable.

1. **Palette, type, layout shell, dark ground.** Moment 1. No data needed.
2. **Basemap from local OSM** — dry roads and lakes. Now it is recognisably Bengaluru.
3. **Flooded segments with the depth ramp and glow.** The core visual.
4. **Timeline scrub + play.** Moment 2, the winner.
5. **Detail panel with provenance.** Moment 4.
6. **Drain layer + surcharge pulses.** Moment 3, the differentiator.
7. Polish: hover states, zoom-about-cursor, reduced-motion, empty state.

If only 1–3 land, it still looks like a real system. Steps 4 and 6 are what make it memorable.
