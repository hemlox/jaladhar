// Canvas rendering per docs/FRONTEND-DESIGN-GUIDE.md.
// The DEPTH ramp below keeps the guide's colour / width / blur presentation
// values verbatim; its BOUNDARIES are recalibrated at load against the
// realized product's flooded band_high_cm distribution (audit B5: with the
// guide's 15/30/50 cm edges, 46.8% of flooded segments saturated the top
// band). The FIRST boundary is always the frozen contract flood threshold
// served by /api/basemap/meta depth_rule — app.js asserts that equality at
// boot before any frame exists; js/ramp.js re-asserts the full seam shape
// when data-derived upper boundaries replace the guide's typed ones.
// Render order follows guide section 3 exactly, extended with the ward
// context layer (N2) beneath lakes.

import { queryGrid as queryParts } from "./data.js";
import { assertRampShape, deriveDepthBands } from "./ramp.js";

// Light-mode ramp: crisp solid strokes, minimal glow. On a light basemap a
// shadow-blur halo spreads the colour into the ground and washes it out, so the
// glow that reads as luminosity on a dark map is dialled almost to zero here.
export const DEPTH = [
  { max: 0.15, color: "#0EA5C4", width: 1.9, blur: 0 },
  { max: 0.30, color: "#F3A008", width: 2.7, blur: 0 },
  { max: 0.50, color: "#F97316", width: 3.5, blur: 1 },
  { max: Infinity, color: "#DC2626", width: 4.6, blur: 1 },
];

// Basemap chrome colours are sourced from the CSS :root tokens so the palette
// has a single source of truth (styles.css). The literals are fallbacks only.
const _ROOT_STYLE = getComputedStyle(document.documentElement);
function _cssVar(name, fallback) {
  const v = _ROOT_STYLE.getPropertyValue(name).trim();
  return v || fallback;
}
// These are cached at load and re-read by refreshPalette() whenever the
// Light/Dark appearance changes — the whole basemap chrome swaps from one call.
let INK_FAINT, ROAD_DRY, ROAD_MAJOR, LAKE_FILL, LAKE_EDGE, DRAIN, ACCENT;
// Ward context (N2): the LAKE_* aesthetic one step dimmer — administrative
// ground tint, never competing with the water layer for attention.
let WARD_FILL, WARD_EDGE;
// Flood-line casing: a dark under-stroke that gives every water line
// edge-contrast against the LIGHT basemap. A thin caution-amber (#F3A008) line
// on the map ground (#E9E7E0) is only ~2:1 on its own — below legibility — so
// each band is stroked twice (dark casing, then colour on top), the standard
// cartographic answer. A mid-dark NEUTRAL (not ink-black): a black casing bleeds
// into thin amber lines under anti-aliasing and turns them olive. In DARK mode
// the water is drawn additively and glows on the dark ground, so the casing is
// skipped entirely (a dark edge on a dark ground does nothing) — see strokeBand.
let CASING;
// waterAdditive: DARK mode composites the water "lighter" (additive glow on a
// dark ground, the data-optimal look); LIGHT mode composites source-over with a
// casing. Driven by the --water-additive token so it follows the theme however
// it was set (explicit toggle or OS preference).
let waterAdditive = false;
// Casing edge girth, in CSS px, added to each flood line's colour width. Tight
// (0.5): a thin, higher-alpha rim reads as a crisp cartographic edge; a wide
// low-alpha rim reads as a halo and is what made the light basemap look blurry.
const CASING_EDGE_PX = 0.5;

// Re-read every theme-dependent colour. Call after the appearance changes, then
// repaint (app.js wires this to the theme toggle). Cheap: a handful of
// getPropertyValue reads off the live root style.
export function refreshPalette() {
  INK_FAINT = _cssVar("--ink-faint", "#5A6579");
  ROAD_DRY = _cssVar("--road-dry", "#232B3A");
  ROAD_MAJOR = _cssVar("--road-major", "#2C3546");
  LAKE_FILL = _cssVar("--lake", "#0C2438");
  LAKE_EDGE = _cssVar("--lake-edge", "#16405E");
  DRAIN = _cssVar("--drain", "#33415A");
  ACCENT = _cssVar("--accent", "#38BDF8");
  WARD_FILL = _cssVar("--ward-fill", "#081019");
  WARD_EDGE = _cssVar("--ward-edge", "#10263C");
  CASING = _cssVar("--flood-casing", "#48484D");
  waterAdditive = _cssVar("--water-additive", "0") === "1";
}
refreshPalette();

// Presentation constants (pixels / counts — allowed literal classes).
const HAIRLINE_PX = 1;
const WATER_CHUNK = 4096;
const PICK_RADIUS_PX = 10;
const LABEL_FONT_PX = 11;
// The guide's DEPTH widths and blurs are street-zoom values. At city zoom
// tens of thousands of additive strokes would blow out into a single glow,
// so width and blur scale from a reference view scale and water alpha
// attenuates when zoomed far out. At m/px <= WATER_REF_MPP the guide values
// apply exactly.
const WATER_REF_MPP = 4;
// City-zoom width floor. Raised twice for the light basemap (0.22 → 0.30 →
// 0.45): source-over colour needs enough girth to read AS a solid colour — a
// 1 px anti-aliased core inside a translucent casing reads as a soft smear,
// not data. Governs the whole-city view and the comparison panels alike
// (both sit past WATER_REF_MPP, so both floor here).
const WATER_MIN_SCALE = 0.45;
// Road-class hierarchy (N1): arterials stay thick and prominent at every
// zoom; the residential/service mesh recedes (dimmer stroke) and is HIDDEN
// beyond 2.5 m/px, where tens of thousands of hairlines collapse into visual
// noise over the city-wide view. 2.5 sits between the LOD swap (pickLod at
// 4 m/px) and street detail, measured against the audit screenshots.
const MINOR_HIDE_MPP = 2.5;
// Ward name labels (N2) read at mid-zoom outward: hidden at street zoom,
// where they would only clutter block-level inspection.
const WARD_LABEL_MIN_MPP = 1.2;

// N1 forward-compatibility note: the served payload encodes classes BINARY
// today (basemap.py MAJOR_HIGHWAYS -> 1, everything else incl. tertiary ->
// 0), so the client hierarchy is two-tier until the server ships per-segment
// highway ranks. Intended rank table for that upgrade, kept beside the code
// that will consume it:
//   motorway/trunk/primary/secondary(+_link) -> 2 (arterial)
//   tertiary(+_link)                         -> 1 (collector)
//   residential/service/living_street/...    -> 0 (local, hideable)
// The renderer already treats any classes[i] >= 1 as arterial, so a richer
// payload lights this up without further client change. Coarse LOD shares
// part_owners/classes with full, so per-segment class survives simplification.

function waterScale(env) {
  const mpp = env.view.metresPerPixel();
  if (mpp <= WATER_REF_MPP) return 1;
  return Math.max(WATER_MIN_SCALE, WATER_REF_MPP / mpp);
}

function bandFor(depthMetres) {
  const m = depthMetres;
  return m <= DEPTH[0].max ? 0 : m <= DEPTH[1].max ? 1 : m <= DEPTH[2].max ? 2 : 3;
}

function setRound(ctx) {
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
}

// 1. background wash — radial gradient, slightly lighter under the city
// centre; never flat black (guide section 7 item 5).
export function drawBackground(ctx, width, height, view, centre) {
  const [cx, cy] = view.toScreen(centre[0], centre[1]);
  const radius = Math.max(width, height) * 0.75;
  const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
  grad.addColorStop(0, _cssVar("--map-g0", "#141A26")); // lighter under city centre
  grad.addColorStop(0.55, _cssVar("--map-g1", "#0A0E17"));
  grad.addColorStop(1, _cssVar("--map-g2", "#05070D"));
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, width, height);
}

// 2. lakes — layer alpha carries the 420ms toggle cross-fade.
export function drawLakes(ctx, env) {
  const lakes = env.lakes;
  if (!lakes || !env.layers.lakes) return;
  const view = env.view;
  const alpha = env.layerAlpha?.lakes ?? 1;
  if (alpha <= 0) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = LAKE_FILL;
  const { offsets, coords } = lakes;
  for (let ring = 0; ring < lakes.n; ring++) {
    const start = offsets[ring];
    const end = offsets[ring + 1];
    if (end - start < 3) continue;
    ctx.beginPath();
    for (let v = start; v < end; v++) {
      const [sx, sy] = view.toScreen(coords[v * 2], coords[v * 2 + 1]);
      if (v === start) ctx.moveTo(sx, sy);
      else ctx.lineTo(sx, sy);
    }
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = LAKE_EDGE;
    // Exactly 1 device pixel (guide §7 item 3).
    ctx.lineWidth = HAIRLINE_PX / env.dpr;
    setRound(ctx);
    ctx.stroke();
  }
  ctx.restore();
}

// 2b. ward boundaries (N2) — subtle fill + hairline beneath lakes/roads,
// carrying the 420ms toggle cross-fade through layerAlpha.wards like every
// other static layer. Geometry comes from /api/context/wards_<vintage>.bin
// (basemap-lake blob layout) with its .meta.json sidecar; engine.js asserts
// the V8 seam at load and refuses to attach a bundle that disagrees.
export function drawWards(ctx, env) {
  const wards = env.wards;
  if (!wards || !env.layers.wards) return;
  const alpha = env.layerAlpha?.wards ?? 1;
  if (alpha <= 0) return;
  const view = env.view;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = WARD_FILL;
  ctx.strokeStyle = WARD_EDGE;
  // Exactly 1 device pixel (guide §7 item 3), matching the lake hairline.
  ctx.lineWidth = HAIRLINE_PX / env.dpr;
  setRound(ctx);
  const { offsets, coords } = wards;
  // evenodd: the blob flattens exterior AND hole rings per ward, so holes
  // (enclosed enclaves) must not be painted over.
  for (const ward of wards.meta.wards) {
    if (!ward.wf6BBox) ward.wf6BBox = wardBBox(ward, coords);
    const [x0, y0, x1, y1] = ward.wf6BBox;
    const rect = view.viewportRect(env.width, env.height);
    if (x1 < rect[0] || x0 > rect[2] || y1 < rect[1] || y0 > rect[3]) continue;
    ctx.beginPath();
    for (const [start, end] of ward.ring_ranges) {
      for (let v = start; v < end; v++) {
        const [sx, sy] = view.toScreen(coords[v * 2], coords[v * 2 + 1]);
        if (v === start) ctx.moveTo(sx, sy);
        else ctx.lineTo(sx, sy);
      }
      ctx.closePath();
    }
    ctx.fill("evenodd");
    ctx.stroke();
  }
  ctx.restore();
}

function wardBBox(ward, coords) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [start, end] of ward.ring_ranges) {
    for (let v = start; v < end; v++) {
      const x = coords[v * 2];
      const y = coords[v * 2 + 1];
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
    }
  }
  return [x0, y0, x1, y1];
}

// 3. dry roads — class-hierarchical (N1): arterials thick and prominent,
// residential/service thin, receding, hidden beyond MINOR_HIDE_MPP.
// Far view: stroke cached world-space paths through the view transform
// (culling is pointless when the whole city is visible). Near view: build a
// culled path for just the viewport.
export function drawDryRoads(ctx, env) {
  const view = env.view;
  const alpha = env.layerAlpha?.roads ?? 1;
  if (alpha <= 0) return;
  const rect = view.viewportRect(env.width, env.height);
  const [bx0, by0, bx1, by1] = env.roads.bbox;
  const visible = Math.min(rect[2], bx1) - Math.max(rect[0], bx0);
  const visibleY = Math.min(rect[3], by1) - Math.max(rect[1], by0);
  const fraction =
    (visible * visibleY) / Math.max((bx1 - bx0) * (by1 - by0), 1);
  const showMinor = view.metresPerPixel() <= MINOR_HIDE_MPP;
  if (fraction > 0.6) {
    const [minor, major] = env.roads.worldPaths(env.currentLod, env.lines);
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.setTransform(
      env.dpr * view.scale, 0, 0, -env.dpr * view.scale,
      env.dpr * view.tx, env.dpr * view.ty
    );
    setRound(ctx);
    if (showMinor) {
      // Local mesh recedes: same ink, reduced alpha (N1).
      ctx.globalAlpha = alpha * 0.8;
      ctx.strokeStyle = ROAD_DRY;
      // Target 0.8 css px, floored at exactly 1 device pixel (guide §7 item 3).
      ctx.lineWidth = Math.max(HAIRLINE_PX, 0.8 * env.dpr) / env.dpr;
      ctx.stroke(minor);
    }
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = ROAD_MAJOR;
    ctx.lineWidth = 2.2 / view.scale; // arterial prominence in css px (N1)
    ctx.stroke(major);
    ctx.restore();
    return;
  }
  ctx.save();
  ctx.globalAlpha = alpha;
  strokeRoadParts(ctx, env, env.lines, () => true, showMinor);
  ctx.restore();
}

function strokeRoadParts(ctx, env, lines, filter, showMinor = true) {
  const view = env.view;
  const owners = env.roads.partOwners;
  const rect = view.viewportRect(env.width, env.height);
  const grid = env.roads.grid(env.currentLod, lines);
  const parts = queryParts(grid, rect);
  const paths = [new Path2D(), new Path2D()];
  let minorCount = 0;
  const { offsets, coords } = lines;
  for (const part of parts) {
    const segIndex = owners[part];
    if (!filter(segIndex)) continue;
    const cls = env.roads.classes[segIndex] >= 1 ? 1 : 0; // >=1: binary today, rank-ready (N1)
    if (cls === 0 && !showMinor) continue; // local mesh hidden beyond MINOR_HIDE_MPP
    if (cls === 0) minorCount++;
    const path = paths[cls];
    const start = offsets[part];
    const end = offsets[part + 1];
    let first = true;
    for (let v = start; v < end; v++) {
      const [sx, sy] = view.toScreen(coords[v * 2], coords[v * 2 + 1]);
      if (first) {
        path.moveTo(sx, sy);
        first = false;
      } else {
        path.lineTo(sx, sy);
      }
    }
  }
  const baseAlpha = ctx.globalAlpha;
  if (minorCount > 0) {
    ctx.globalAlpha = baseAlpha * 0.8; // local mesh recedes (N1)
    ctx.strokeStyle = ROAD_DRY;
    // Target 0.8 css px, floored at exactly 1 device pixel (guide §7 item 3).
    ctx.lineWidth = Math.max(HAIRLINE_PX, 0.8 * env.dpr) / env.dpr;
    setRound(ctx);
    ctx.stroke(paths[0]);
  }
  ctx.globalAlpha = baseAlpha;
  ctx.strokeStyle = ROAD_MAJOR;
  ctx.lineWidth = 2.2; // arterial prominence in css px (N1)
  setRound(ctx);
  ctx.stroke(paths[1]);
}

// 4. drain network — dim, beneath the water layer; observed reaches solid,
// synthesised connectors dashed and dimmer (honesty rendered).
export function drawDrains(ctx, env) {
  const drains = env.drains;
  if (!drains || !drains.lines || drains.alpha <= 0 || !env.layers.drains) return;
  const view = env.view;
  const sources = drains.sourceCodes;
  const rect = view.viewportRect(env.width, env.height);
  ctx.save();
  ctx.globalAlpha = drains.alpha;
  const solid = new Path2D();
  const dashed = new Path2D();
  const { offsets, coords } = drains.lines;
  for (let edge = 0; edge < drains.lines.n; edge++) {
    const start = offsets[edge];
    const end = offsets[edge + 1];
    let first = true;
    const target = sources && sources[edge] === 1 ? dashed : solid;
    for (let v = start; v < end; v++) {
      const wx = coords[v * 2];
      const wy = coords[v * 2 + 1];
      if (wx < rect[0] || wx > rect[2] || wy < rect[1] || wy > rect[3]) {
        first = true;
        continue;
      }
      const [sx, sy] = view.toScreen(wx, wy);
      if (first) {
        target.moveTo(sx, sy);
        first = false;
      } else {
        target.lineTo(sx, sy);
      }
    }
  }
  setRound(ctx);
  ctx.strokeStyle = DRAIN;
  ctx.lineWidth = 1.4;
  ctx.stroke(solid);
  ctx.setLineDash([5, 4]);
  ctx.globalAlpha = drains.alpha * 0.55;
  ctx.lineWidth = 1.1;
  ctx.stroke(dashed);
  ctx.setLineDash([]);
  ctx.restore();
}

// 6. surcharging nodes — ONLY where a coupled run measured surcharge.
// Drawn on the dynamic layer; nothing here can fire until the server payload
// carries measured events (today it never does — geometry-only render).
export function drawPulses(ctx, env) {
  const drains = env.drains;
  if (!drains || !drains.meta?.surcharge?.available || !drains.pulseNodes) return;
  if (!env.layers.drains) return;
  // Radius and alpha pulse on a 1.4s ease-in-out cycle (guide section 4).
  // prefers-reduced-motion freezes the wave to a steady marker.
  const reduced = env.reducedMotion === true;
  const t = reduced ? 0.25 : (performance.now() % 1400) / 1400;
  const wave = 0.5 - 0.5 * Math.cos(t * Math.PI * 2);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (const node of drains.pulseNodes) {
    const [sx, sy] = env.view.toScreen(node[0], node[1]);
    const r = 5 + (reduced ? 0 : 2.5 * wave);
    ctx.beginPath();
    ctx.arc(sx, sy, r, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(244, 63, 94, " + (0.15 + 0.45 * wave).toFixed(3) + ")";
    ctx.shadowColor = "#F43F5E";
    ctx.shadowBlur = 12 * env.dpr; // guide formula: blur * dpr
    ctx.fill();
  }
  ctx.restore();
}

// 5. flooded roads — depth-ramped glow, additive blending.
// Far view strokes cached per-band WORLD paths through the view transform.
// The cache is keyed per realized FRAME object (LRU, 4 entries): with more
// than one realized lead the far branch cross-fades frame A and frame B so
// water grows continuously while scrubbing/playing — never a snap.
const waterWorldCache = new Map();
const WATER_WORLD_CACHE_MAX = 128; // room for a full 97-frame series
let waterPlaying = false; // shadows drop during the sweep, restore at rest

// ---- depth-band recalibration (B5 / C1) --------------------------------
// Flooded band_high_cm values are pooled from every realized frame fed via
// prewarmWaterWorld/drawWater; boundaries derive once the pooled sample is
// trustworthy (ramp.js MIN_FLOODED_SAMPLES). Until then DEPTH keeps its
// contract-anchored fallback edges and the legend says so. The pooled
// histogram is exact (integer cm), never a resample.
const rampHist = new Map(); // band_high_cm -> count (flooded segments only)
let rampSamplesSeen = 0;
let rampSamplesAtAttempt = -1;
let rampAppliedSource = null; // null | 'contract' | 'derived:flooded_p50_p90'
let rampFailedReported = false;

function feedRampHistogram(frame) {
  if (!frame) return;
  const high = frame.high;
  const n = frame.n ?? high.length;
  for (let seg = 0; seg < n; seg++) {
    if (!frame.isFlooded(seg)) continue;
    const cm = high[seg];
    rampHist.set(cm, (rampHist.get(cm) ?? 0) + 1);
    rampSamplesSeen++;
  }
}

// Threshold: app.js's assertDepthRule has already pinned DEPTH[0].max*100 to
// /api/basemap/meta depth_rule by the time any frame renders, so the first
// boundary IS the contract value by construction.
function thresholdCm() {
  return DEPTH[0].max * 100;
}

function updateLegendRanges() {
  if (typeof document === "undefined") return;
  const rows = [
    ["range-d1", "<=" + Math.round(DEPTH[0].max * 100) + " cm"],
    ["range-d2", "<=" + Math.round(DEPTH[1].max * 100) + " cm"],
    ["range-d3", "<=" + Math.round(DEPTH[2].max * 100) + " cm"],
    ["range-d4", "any"],
  ];
  for (const [id, text] of rows) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
}

function applyRampBands(bands) {
  const [b0, b1, b2] = bands.bounds_cm;
  const changed =
    Math.round(DEPTH[0].max * 100) !== b0 ||
    Math.round(DEPTH[1].max * 100) !== b1 ||
    Math.round(DEPTH[2].max * 100) !== b2;
  DEPTH[0].max = b0 / 100;
  DEPTH[1].max = b1 / 100;
  DEPTH[2].max = b2 / 100;
  // Band assignment changes => cached per-band world paths are stale (V8:
  // the cache carries an assumption about the boundaries it was built with).
  if (changed) waterWorldCache.clear();
  rampAppliedSource = bands.source;
  updateLegendRanges();
  // Verification/ops telemetry (V1): the realized boundaries the renderer
  // actually strokes with, readable by automated checks.
  try {
    window.__JALADHAR_RAMP = {
      threshold_cm: bands.threshold_cm,
      bounds_cm: [...bands.bounds_cm],
      source: bands.source,
      n_flooded_samples: bands.n_flooded_samples,
    };
  } catch {
    // non-browser import (node --check / tests)
  }
}

// Attempt derivation from the pooled flooded distribution. Returns false
// only on a seam-contract violation — the caller then refuses to render
// water rather than stroke bands it cannot vouch for.
function ensureRampCalibrated() {
  const bands = deriveDepthBands(thresholdCm(), rampHist);
  if (!bands || !assertRampShape(bands)) {
    if (!rampFailedReported) {
      rampFailedReported = true;
      console.error("depth-ramp derivation violated the seam contract; refusing to render water");
    }
    return false;
  }
  applyRampBands(bands);
  return true;
}

// Feed the displayed frame's flooded depths into the pool and recalibrate
// when the sample has grown meaningfully (first attempt unconditional; then
// >=25% growth, so an auto-playing sweep never rebuilds its path cache per
// frame). Once data-derived bounds are applied, later frames cannot move
// them — one stable calibration per session.
let rampLastFedFrame = null;
function syncRamp(frame) {
  if (frame !== rampLastFedFrame) {
    rampLastFedFrame = frame;
    feedRampHistogram(frame);
  }
  if (
    rampAppliedSource !== "derived:flooded_p50_p90" &&
    rampSamplesSeen !== rampSamplesAtAttempt &&
    (rampSamplesAtAttempt < 0 || rampSamplesSeen >= rampSamplesAtAttempt * 1.25)
  ) {
    rampSamplesAtAttempt = rampSamplesSeen;
    return ensureRampCalibrated();
  }
  return true;
}

function waterWorldFor(env, frame) {
  let entry = waterWorldCache.get(frame);
  if (entry && entry.lod === env.currentLod) return entry;
  // Re-insert to refresh recency.
  waterWorldCache.delete(frame);
  const bands = [new Path2D(), new Path2D(), new Path2D(), new Path2D()];
  const counts = [0, 0, 0, 0];
  if (frame) {
    const { segOffsets, partIds } = env.roads.segPartRanges(env.currentLod, env.lines);
    const lines = env.lines;
    for (let seg = 0; seg < env.roads.nSegments; seg++) {
      if (!frame.isFlooded(seg)) continue;
      const b = bandFor(frame.high[seg] / 100);
      const path = bands[b];
      for (let p = segOffsets[seg]; p < segOffsets[seg + 1]; p++) {
        const part = partIds[p];
        const start = lines.offsets[part];
        const end = lines.offsets[part + 1];
        path.moveTo(lines.coords[start * 2], lines.coords[start * 2 + 1]);
        for (let v = start + 1; v < end; v++) {
          path.lineTo(lines.coords[v * 2], lines.coords[v * 2 + 1]);
        }
        counts[b]++;
      }
    }
  }
  entry = { lod: env.currentLod, bands, counts };
  waterWorldCache.set(frame, entry);
  if (waterWorldCache.size > WATER_WORLD_CACHE_MAX) {
    waterWorldCache.delete(waterWorldCache.keys().next().value);
  }
  return entry;
}

// Pre-build a frame's world paths outside the play loop (called while frames
// stream in), so the sweep itself only strokes cached paths. The frame's
// flooded depths also join the ramp-recalibration pool here, so a warmed
// series calibrates from its full realized distribution.
export function prewarmWaterWorld(env, frame) {
  if (frame) {
    feedRampHistogram(frame);
    waterWorldFor(env, frame);
  }
}

export function drawWater(ctx, env) {
  const frame = env.frame;
  if (!frame) return; // the water layer IS the product — never gated on basemap toggles
  if (!syncRamp(frame)) return; // seam-contract violation: fail closed, no water
  waterPlaying = env.playing === true;
  const view = env.view;
  const rect = view.viewportRect(env.width, env.height);
  const [bx0, by0, bx1, by1] = env.roads.bbox;
  const fraction =
    ((Math.min(rect[2], bx1) - Math.max(rect[0], bx0)) *
      (Math.min(rect[3], by1) - Math.max(rect[1], by0))) /
    Math.max((bx1 - bx0) * (by1 - by0), 1);
  const zoomScale = waterScale(env);
  if (fraction > 0.6) {
    const frameA = env.frameA;
    const frameB = env.interpFrac > 0 ? env.frameB : null;
    const frac = frameB ? env.interpFrac : 0;
    // Each entry is [waterWorld, alpha]. At rest we crossfade A->B so slow
    // hand-scrubbing is smooth. While PLAYING we draw only the dominant frame
    // at full alpha — that halves the stroke count so the casing pass (restored
    // for legibility of moving floods on the light ground) fits the frame
    // budget; the crossfade is imperceptible at sweep speed.
    let draws;
    if (waterPlaying && frameB) {
      const dom = frac >= 0.5 ? frameB : frameA;
      draws = [[waterWorldFor(env, dom), 1]];
    } else {
      draws = [[waterWorldFor(env, frameA), 1 - frac]];
      if (frameB) draws.push([waterWorldFor(env, frameB), frac]);
    }
    ctx.save();
    ctx.globalCompositeOperation = waterAdditive ? "lighter" : "source-over";
    ctx.setTransform(
      env.dpr * view.scale, 0, 0, -env.dpr * view.scale,
      env.dpr * view.tx, env.dpr * view.ty
    );
    for (const [entry, alpha] of draws) {
      for (let b = 0; b < 4; b++) {
        if (entry.counts[b]) {
          strokeBand(ctx, entry.bands[b], b, zoomScale, env.dpr, view.scale, alpha);
        }
      }
    }
    ctx.restore();
    return;
  }
  const grid = env.roads.grid(env.currentLod, env.lines);
  const parts = queryParts(grid, rect);
  const owners = env.roads.partOwners;
  const bands = [new Path2D(), new Path2D(), new Path2D(), new Path2D()];
  const counts = [0, 0, 0, 0];
  const { offsets, coords } = env.lines;
  for (const part of parts) {
    const segIndex = owners[part];
    if (!frame.isFlooded(segIndex)) continue;
    const highM = env.depthMetresFor(segIndex);
    const b = bandFor(highM);
    const path = bands[b];
    if (++counts[b] % WATER_CHUNK === 0) {
      strokeBand(ctx, path, b, zoomScale, env.dpr); // flush chunks so overlaps keep blooming
    }
    const start = offsets[part];
    const end = offsets[part + 1];
    let first = true;
    for (let v = start; v < end; v++) {
      const [sx, sy] = view.toScreen(coords[v * 2], coords[v * 2 + 1]);
      if (first) {
        path.moveTo(sx, sy);
        first = false;
      } else {
        path.lineTo(sx, sy);
      }
    }
  }
  ctx.save();
  ctx.globalCompositeOperation = waterAdditive ? "lighter" : "source-over";
  for (let b = 0; b < bands.length; b++) {
    if (counts[b] === 0) continue;
    strokeBand(ctx, bands[b], b, zoomScale, env.dpr);
  }
  ctx.restore();
}

function strokeBand(ctx, path, band, zoomScale, dpr, worldDiv = 1, alphaMul = 1) {
  const spec = DEPTH[band];
  const colorWidth = Math.max(HAIRLINE_PX / dpr, spec.width * zoomScale) / worldDiv;
  // Water renders source-over (see drawWater): on the light basemap colours sit
  // ON the ground. Alpha now stays near-full at every zoom (floor 0.7 + 0.3·z):
  // the old 0.5-floor left city-scale strokes at ~65% over a pale ground,
  // which is exactly the washed-out "blurry" look (owner 2026-09-09, second
  // report) — solid colour is what reads as sharp.
  const base = zoomScale < 1 ? 0.7 + 0.3 * zoomScale : 1;
  const edge = CASING_EDGE_PX / worldDiv;
  setRound(ctx);
  // LIGHT-mode casing pass — a neutral-dark under-stroke, COLOUR-DOMINANT. The
  // added girth is a fixed ~0.7 CSS-px edge (CASING_EDGE_PX / worldDiv keeps it
  // constant in screen px across the world-transformed and untransformed draw
  // paths), never a multiple of colorWidth — which is already world-scaled and
  // would blow the casing up if divided by worldDiv again. Thin amber cores keep
  // their colour; the casing is only an edge, and carries no bloom.
  //
  // Drawn ALWAYS in light (incl. while playing): the dark edge is exactly what
  // keeps moving amber/orange floods legible on the light ground (without it
  // they wash to pale mush). Playback affords the second stroke because drawWater
  // drops the A/B crossfade while playing (one frame, not two). In DARK mode the
  // water composites additively and glows on the dark ground, so a dark casing
  // would be invisible — skip it and let the bloom (below) do the separating.
  if (!waterAdditive) {
    ctx.shadowBlur = 0;
    ctx.strokeStyle = CASING;
    ctx.lineWidth = colorWidth + edge;
    ctx.globalAlpha = 0.6 * alphaMul;
    ctx.stroke(path);
  }
  // Colour pass on top. LIGHT gets NO bloom whatsoever — shadowBlur is a
  // same-colour halo under every stroke, and even the old 1-device-px floor
  // softened ~30k edges into a visible haze on the pale ground (that floor is
  // gone; shadow glow is a dark-ground technique). DARK keeps its additive
  // bloom so floods read as luminous water. Guide types shadowBlur =
  // blur * dpr; dropped while playing at city zoom so the sweep holds 60fps.
  ctx.shadowColor = spec.color;
  if (waterAdditive) {
    const bloom = spec.blur + 2;
    const blurPx = Math.max(2, bloom * zoomScale * dpr);
    ctx.shadowBlur = zoomScale < 0.5 && waterPlaying ? 0 : blurPx;
  } else {
    ctx.shadowBlur = 0;
  }
  ctx.strokeStyle = spec.color;
  ctx.lineWidth = colorWidth;
  ctx.globalAlpha = base * alphaMul;
  ctx.stroke(path);
}

// 7. selection highlight + hover lift
export function drawSelection(ctx, env) {
  const highlights = [];
  if (env.hoverSeg != null && env.hoverSeg !== env.selectedSeg) {
    highlights.push([env.hoverSeg, 0.35, 1.2]);
  }
  if (env.selectedSeg != null) highlights.push([env.selectedSeg, 1, 2]);
  if (!highlights.length) return;
  const view = env.view;
  const lines = env.lines;
  const { segOffsets, partIds } = env.roads.segPartRanges(env.currentLod, env.lines);
  ctx.save();
  setRound(ctx);
  ctx.globalCompositeOperation = "lighter";
  for (const [segIndex, alpha, extraWidth] of highlights) {
    const path = new Path2D();
    for (let p = segOffsets[segIndex]; p < segOffsets[segIndex + 1]; p++) {
      const part = partIds[p];
      const start = lines.offsets[part];
      const end = lines.offsets[part + 1];
      path.moveTo(lines.coords[start * 2], lines.coords[start * 2 + 1]);
      for (let v = start + 1; v < end; v++) {
        path.lineTo(lines.coords[v * 2], lines.coords[v * 2 + 1]);
      }
    }
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = ACCENT;
    ctx.shadowColor = ACCENT;
    ctx.shadowBlur = 8 * env.dpr; // guide formula: blur * dpr
    ctx.lineWidth = 1.8 + extraWidth;
    ctx.stroke(path);
  }
  ctx.restore();
}

// 8. labels / landmarks — drawn last so they stay legible over water.
// Ward names (N2) share the landmark font/ink, visible from mid-zoom
// outward (WARD_LABEL_MIN_MPP) where ward-level reading makes sense.
export function drawLabels(ctx, env) {
  const landmarks = env.roads?.meta?.landmarks ?? [];
  ctx.save();
  ctx.font = LABEL_FONT_PX + "px ui-sans-serif, -apple-system, 'Segoe UI', Roboto, sans-serif";
  if ("letterSpacing" in ctx) ctx.letterSpacing = "0.08em";
  ctx.fillStyle = INK_FAINT;
  for (const lm of landmarks) {
    const [sx, sy] = env.view.toScreen(lm.x, lm.y);
    if (sx < -60 || sx > env.width + 60 || sy < -20 || sy > env.height + 20) continue;
    ctx.fillText(lm.name, sx + 6, sy - 6);
  }
  if (env.wards && env.layers.wards && env.view.metresPerPixel() >= WARD_LABEL_MIN_MPP) {
    for (const ward of env.wards.meta.wards) {
      const [sx, sy] = env.view.toScreen(ward.centroid_m[0], ward.centroid_m[1]);
      if (sx < -80 || sx > env.width + 80 || sy < -20 || sy > env.height + 20) continue;
      ctx.fillText(ward.name, sx, sy);
    }
  }
  ctx.restore();
}

// Hit-testing: nearest line part to the cursor inside the pick radius.
export function pickSegment(env, cssX, cssY) {
  const view = env.view;
  const [wx, wy] = view.toWorld(cssX, cssY);
  const r = PICK_RADIUS_PX / view.scale;
  const rect = [wx - r, wy - r, wx + r, wy + r];
  const grid = env.roads.grid(env.currentLod, env.lines);
  const parts = queryParts(grid, rect);
  const owners = env.roads.partOwners;
  const { offsets, coords } = env.lines;
  let bestPart = -1;
  let bestD2 = r * r;
  for (const part of parts) {
    const start = offsets[part];
    const end = offsets[part + 1];
    for (let v = start; v < end - 1; v++) {
      const d2 = pointSegmentDist2(wx, wy,
        coords[v * 2], coords[v * 2 + 1], coords[v * 2 + 2], coords[v * 2 + 3]);
      if (d2 < bestD2) {
        bestD2 = d2;
        bestPart = part;
      }
    }
  }
  return bestPart >= 0 ? owners[bestPart] : null;
}

function pointSegmentDist2(px, py, ax, ay, bx, by) {
  const abx = bx - ax;
  const aby = by - ay;
  const len2 = abx * abx + aby * aby;
  let t = len2 > 0 ? ((px - ax) * abx + (py - ay) * aby) / len2 : 0;
  t = Math.max(0, Math.min(1, t));
  const dx = px - (ax + t * abx);
  const dy = py - (ay + t * aby);
  return dx * dx + dy * dy;
}
