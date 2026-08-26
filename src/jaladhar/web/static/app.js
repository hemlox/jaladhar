// JALADHAR presentation dashboard bootstrap.
// Owns the shared render environment and wires data -> engine -> UI.
// Rules 2 and 3 bind everything here: the screen shows exactly what the
// realized payloads contain, and every displayed number traces to a file.

import { View } from "./js/view.js";
import { Engine } from "./js/engine.js";
import {
  RoadNetwork,
  loadState,
  loadBasemapMeta,
  loadLines,
  loadProductFrame,
  loadSeriesFrame,
} from "./js/data.js";
import { DEPTH, pickSegment as renderPickSegment, prewarmWaterWorld } from "./js/render.js";
import { Timeline } from "./js/timeline.js";
import { Rail } from "./js/rail.js";
import * as states from "./js/states.js";

// WF-6 INT integration: the self-mounting operator panels join the import
// graph here (watchlist + search mount themselves idempotently), and the
// causal panel is driven explicitly after every segment selection below.
import "./js/watchlist.js";
import "./js/search.js";
import "./js/routing.js";
import { CausalPanel } from "./js/causal.js";

const $ = (id) => document.getElementById(id);

const elements = {
  statusDot: $("status-dot"),
  runLabel: $("run-label"),
  gateNote: $("gate-note"),
  nowLead: $("now-lead"),
  statFlooded: $("stat-flooded"),
  statDeepest: $("stat-deepest"),
  statDeepestLoc: $("stat-deepest-loc"),
  selectedBody: $("selected-body"),
  drainStatus: $("drain-status"),
  drainFingerprint: $("drain-fingerprint"),
  scrubber: $("scrubber"),
  playBtn: $("btn-play"),
  tickStart: $("tick-start"),
  tickEnd: $("tick-end"),
};

const rail = new Rail(elements);
// fetch bound to the global: CausalPanel invokes fetchImpl as a method, and
// an unbound `fetch` with a non-global `this` throws Illegal invocation in
// Chromium, which would silently degrade every causal payload to its empty
// state (observed live: predicted hits rendered as none_measured).
const causalPanel = new CausalPanel({ fetchImpl: fetch.bind(globalThis) });
const timeline = new Timeline({
  slider: elements.scrubber,
  playBtn: elements.playBtn,
  tickStart: elements.tickStart,
  tickEnd: elements.tickEnd,
  nowLead: elements.nowLead,
});

const engine = new Engine($("cv-static"), $("cv-dyn"), $("cv-ui"));

// Swap level of detail after the view settles — full geometry at street
// zoom, coarse at city zoom. Cached after first fetch of each level.
let lodTimer = 0;
engine.onViewChanged = () => {
  clearTimeout(lodTimer);
  lodTimer = setTimeout(async () => {
    if (!env.ready.basemap) return;
    const lod = pickLod();
    if (lod !== env.currentLod) {
      await refreshLines();
      engine.invalidateStatic();
      engine.invalidateUI();
    }
  }, 120);
};

const env = {
  view: null,
  roads: null,
  lines: null,
  currentLod: "full",
  lakes: null,
  drains: null,
  layers: { roads: true, lakes: true, drains: false },
  layerAlpha: { roads: 1, lakes: 1 },
  reducedMotion: false,
  frameA: null,
  frameB: null,
  interpFrac: 0,
  get frame() {
    return this.frameA;
  },
  depthMetresFor(segIndex) {
    const a = this.frameA ? this.frameA.high[segIndex] : 0;
    if (!this.frameB || !this.interpFrac) return a / 100;
    const b = this.frameB.high[segIndex];
    return (a + (b - a) * this.interpFrac) / 100;
  },
  drainsAlpha: 0,
  selectedSeg: null,
  hoverSeg: null,
  cityCentre: [0, 0],
  dpr: 1,
  width: 0,
  height: 0,
  ready: { basemap: false },
};
engine.env = env;

// Depth boundaries are DATA: the first band edge must equal the frozen
// contract's flood threshold served by the backend. Mismatch refuses to
// render rather than silently drawing the wrong bands.
function assertDepthRule(metaDepthCm) {
  const guideFirstBandCm = DEPTH[0].max * 100;
  if (Number(metaDepthCm) !== guideFirstBandCm) {
    throw new Error(
      "depth band schema mismatch: contract flood threshold " +
        metaDepthCm + " cm vs presentation ramp " + guideFirstBandCm + " cm"
    );
  }
}

// Legend writer convergence (V8 seam). render.js still writes the four range
// spans directly inside its internal ramp recalibration (file owned by
// another lane), so BOTH writers are converged through rail.showLegendRange:
// this module emits every range here at boot and again whenever render's
// __JALADHAR_RAMP telemetry reports newly applied bounds. range-d4 is passed
// as the "any" sentinel — rail translates it live from DEPTH. With both
// paths flowing through rail, its interim MutationObserver sentinel guard is
// retired (removed in rail.js by this unit).
function syncLegendRanges() {
  const ramp = globalThis.__JALADHAR_RAMP ?? null;
  const bounds =
    ramp && Array.isArray(ramp.bounds_cm)
      ? ramp.bounds_cm
      : [DEPTH[0].max, DEPTH[1].max, DEPTH[2].max].map((m) => Math.round(m * 100));
  rail.showLegendRange("range-d1", "<=" + bounds[0] + " cm");
  rail.showLegendRange("range-d2", "<=" + bounds[1] + " cm");
  rail.showLegendRange("range-d3", "<=" + bounds[2] + " cm");
  rail.showLegendRange("range-d4", "any");
}

// Ramp derivation is once-per-session but its timing follows the first water
// paint, which can land long after module eval (frame warming delays boot).
// Poll until the derived bounds are observed AND every range node holds
// rail's canonical text: render's direct write lands in the same task as the
// telemetry update and can be the last toucher, so convergence is asserted
// per-node and re-emitted through rail on any divergence, with a wide
// safety cap.
(function watchRampForLegend() {
  let seenSource = null;
  let settledTicks = 0;
  let ticks = 0;
  syncLegendRanges();
  const timer = setInterval(() => {
    ticks++;
    const ramp = globalThis.__JALADHAR_RAMP ?? null;
    const source = ramp?.source ?? null;
    const bounds =
      ramp && Array.isArray(ramp.bounds_cm)
        ? ramp.bounds_cm
        : [DEPTH[0].max, DEPTH[1].max, DEPTH[2].max].map((m) => Math.round(m * 100));
    if (source !== seenSource) {
      seenSource = source;
      syncLegendRanges();
    }
    const canonical = [
      ["range-d1", "<=" + bounds[0] + " cm"],
      ["range-d2", "<=" + bounds[1] + " cm"],
      ["range-d3", "<=" + bounds[2] + " cm"],
      ["range-d4", "> " + Math.round(DEPTH[2].max * 100) + " cm"],
    ];
    const converged = canonical.every(
      ([id, text]) => document.getElementById(id)?.textContent === text
    );
    if (!converged && seenSource !== null) syncLegendRanges();
    const derived = source !== null && String(source).startsWith("derived");
    settledTicks = converged && derived ? settledTicks + 1 : 0;
    if (settledTicks >= 10 || ticks >= 4000) clearInterval(timer);
  }, 300);
})();

const lodUrl = (lod) => "/api/basemap/roads/" + lod + ".bin";

async function ensureBasemap(meta) {
  env.roads = new RoadNetwork(meta);
  env.cityCentre = [
    (meta.bbox[0] + meta.bbox[2]) / 2,
    (meta.bbox[1] + meta.bbox[3]) / 2,
  ];
  env.lakes = await loadLines("/api/basemap/lakes.bin");
  await refreshLines();
  env.ready.basemap = true;
  $("legend").classList.remove("hidden");
}

function pickLod() {
  return env.view.metresPerPixel() > 4 ? "coarse" : "full";
}

async function refreshLines() {
  const lod = pickLod();
  env.currentLod = lod;
  env.lines = await env.roads.ensureLod(lod, lodUrl);
  // Warm the segment->part index off the interaction path; selection and
  // water caching use it.
  env.roads.segPartRanges(lod, env.lines);
}

let drainLoad = null;
async function ensureDrains() {
  if (!drainLoad) {
    drainLoad = (async () => {
      const meta = await (
        await fetch("/api/drains/meta", { cache: "no-store" })
      ).json();
      let lines = null;
      let sourceCodes = null;
      if (meta.available) {
        lines = await loadLines("/api/drains/edges.bin");
        sourceCodes = Int8Array.from(meta.edge_source_codes ?? []);
      }
      return { meta, lines, sourceCodes, pulseNodes: null };
    })();
  }
  return drainLoad;
}

function setLayer(name, on) {
  if (name === "drains") {
    if (on) {
      ensureDrains()
        .then((drain) => {
          env.drains = drain;
          rail.setDrainStatus(drain.meta);
          fadeInDrains();
        })
        .catch((err) => {
          rail.setDrainStatus({ available: false, reason: String(err.message ?? err) });
        });
    } else {
      fadeOutDrains();
    }
  } else {
    fadeLayerAlpha(name, on); // 420ms cross-fade (guide section 4)
  }
}

const easeInOut = (t) => t * t * (3 - 2 * t);

// One active fade per layer: starting a new one cancels the previous, so a
// rapid re-toggle can never leave a layer stuck hidden while its checkbox
// reads on.
const layerAnims = {};
function cancelLayerAnim(name) {
  if (layerAnims[name]) layerAnims[name].done = true;
}

function fadeLayerAlpha(name, on) {
  cancelLayerAnim(name);
  const from = env.layerAlpha[name];
  const to = on ? 1 : 0;
  if (on) env.layers[name] = true;
  const start = performance.now();
  const anim = {
    done: false,
    continuous: false,
    tick(now) {
      const t = Math.min(1, (now - start) / 420);
      env.layerAlpha[name] = from + (to - from) * (engine.reducedMotion() ? 1 : easeInOut(t));
      engine.invalidateStatic();
      if (t >= 1) {
        anim.done = true;
        if (!on) env.layers[name] = false;
      }
      return !anim.done;
    },
  };
  layerAnims[name] = anim;
  engine.animate(anim);
  engine.invalidateStatic();
}

function fadeInDrains() {
  cancelLayerAnim("drains");
  const from = env.drains.alpha ?? 0;
  const start = performance.now();
  env.layers.drains = true;
  const anim = {
    done: false,
    continuous: false,
    tick(now) {
      const t = Math.min(1, (now - start) / 420); // 420ms cross-fade (guide §4)
      env.drains.alpha = engine.reducedMotion() ? 1 : from + (1 - from) * easeInOut(t);
      // Drains paint on the STATIC canvas — every fade tick must dirty it.
      engine.invalidateStatic();
      if (t >= 1) anim.done = true;
      return !anim.done;
    },
  };
  layerAnims.drains = anim;
  engine.animate(anim);
}

function fadeOutDrains() {
  if (!env.drains) {
    env.layers.drains = false;
    engine.invalidateStatic();
    return;
  }
  cancelLayerAnim("drains");
  const start = performance.now();
  const from = env.drains.alpha;
  const anim = {
    done: false,
    continuous: false,
    tick(now) {
      const t = Math.min(1, (now - start) / 420);
      env.drains.alpha = engine.reducedMotion() ? 0 : from * (1 - easeInOut(t));
      engine.invalidateStatic();
      if (t >= 1) {
        anim.done = true;
        env.layers.drains = false;
      }
      return t < 1;
    },
  };
  layerAnims.drains = anim;
  engine.animate(anim);
  engine.invalidateStatic();
}

for (const label of document.querySelectorAll(".layer-toggle")) {
  const input = label.querySelector("input");
  input.addEventListener("change", () => {
    label.classList.toggle("on", input.checked);
    setLayer(input.dataset.layer, input.checked);
  });
}

// Pointer interaction: pan, zoom-about-cursor, hover lift, click select.
// Timeline context for a segment inspection — identical for pointer picks
// and watchlist/search fly-tos.
function currentTimelineContext() {
  return timeline.mode === "series"
    ? { mode: "series", index: env.frameA?.index ?? 0 }
    : { mode: "lead", lead: timeline.leads[timeline.leads.length - 1] ?? 0 };
}

// The SAME selection path as a pointer pick, exposed to wf6:select-street:
// highlight, inspector panel, then the causal-link panel (A5).
async function selectSegmentLikePick(segmentId) {
  const index = env.roads.indexOfSegment(segmentId);
  if (index == null) return; // not in the realized network — nothing to claim
  env.selectedSeg = index;
  engine.invalidateUI();
  await rail.selectSegment(
    segmentId,
    currentTimelineContext(),
    env.roads,
    env.drains?.meta ?? null
  );
  await causalPanel.show(segmentId);
}

let hoverPending = false;
engine.attachPointerHandlers({
  onHover(cssX, cssY) {
    if (hoverPending || !env.ready.basemap) return;
    hoverPending = true;
    requestAnimationFrame(() => {
      hoverPending = false;
      const hit = renderPickSegment(env, cssX, cssY);
      if (hit !== env.hoverSeg) {
        env.hoverSeg = hit;
        $("map-area").classList.toggle("cursor-pointer", hit != null);
        engine.invalidateUI();
      }
    });
  },
  async onPick(cssX, cssY) {
    if (!env.ready.basemap) return;
    const hit = renderPickSegment(env, cssX, cssY);
    env.selectedSeg = hit;
    engine.invalidateUI();
    if (hit == null) {
      rail.clearSelection();
      return;
    }
    const segmentId = env.roads.segmentIds[hit];
    await rail.selectSegment(
      segmentId,
      currentTimelineContext(),
      env.roads,
      env.drains?.meta ?? null
    );
    await causalPanel.show(segmentId);
  },
});

timeline.onFrameChange = ({ leadIndex, frac }) => {
  const lead = timeline.leads[leadIndex];
  const frameA = timeline.frames.get(lead);
  if (!frameA) return; // frame not fetched yet — ensureFrames covers it before play
  env.frameA = frameA;
  const nextLead = timeline.leads[Math.min(leadIndex + 1, timeline.leads.length - 1)];
  env.frameB = frac > 0 && nextLead !== lead ? timeline.frames.get(nextLead) : null;
  env.interpFrac = frac;
  engine.invalidateDynamic(); // water grows with the interpolated frame
  env.playing = timeline.playing; // glow drops during the sweep, restores at rest
  if (timeline.mode === "series") {
    const frameMeta = timeline.seriesFrames[leadIndex];
    if (frameMeta) elements.nowLead.textContent = "VALID " + Timeline.formatValidTime(frameMeta.valid_time_utc);
  }
  // Hero stats are realized-only: refresh when the sweep crosses into a new
  // discrete lead, and on the final hold.
  if (leadIndex !== timeline.lastStatsLead) {
    timeline.lastStatsLead = leadIndex;
    rail.updateStats(env.frameA, env.roads);
  } else if (frac === 0 || !env.frameB) {
    rail.updateStats(env.frameA, env.roads);
  }
  // WF-6 INT: forward discrete frame positions to the operator panels.
  // Mid-interpolation sweeps are suppressed — the panels are realized-only
  // and must not refetch per animation tick. configure()'s hold-at-end emit
  // fires through here too, so the panels track the boot hold as well.
  if (frac === 0 || !env.frameB) {
    const frameTag =
      timeline.mode === "series" ? timeline.seriesFrames?.[leadIndex]?.tag : null;
    document.dispatchEvent(
      new CustomEvent("wf6:frame-changed", {
        detail: { frame: String(frameTag ?? lead) },
      })
    );
  }
};

async function boot() {
  states.loading();
  env.reducedMotion = engine.reducedMotion();
  window.__bootStage = "state";
  try {
    const [statePayload, basemapMeta] = await Promise.all([
      loadState(),
      loadBasemapMeta(),
    ]);
    // Ward-vintage resolution (configs/context.yaml ward_vintage_by_product_kind):
    // a historical-replay product labels with the 2022 layer. Set BEFORE any
    // engine/wards fetch — the toggle reads this global when first enabled.
    const forcingKind =
      statePayload.series?.forcing_kind ?? statePayload.snapshots?.[0]?.forcing_kind;
    globalThis.__CONTEXT_VINTAGE = forcingKind === "historical_replay" ? "2022" : "2023";
    window.__bootStage = "basemap";
    assertDepthRule(basemapMeta.depth_rule);
    syncLegendRanges();

    env.view = new View(basemapMeta.bbox);
    env.dpr = engine.dpr;
    env.width = engine.width;
    env.height = engine.height;
    env.view.fit(engine.width, engine.height);
    await ensureBasemap(basemapMeta);
    window.__bootStage = "series-or-lead";

    rail.setRunLabel(statePayload);
    engine.invalidateStatic();
    window.__bootStage = "timeline";

    if (statePayload.status === "empty") {
      states.showOverlay({
        title: "No run loaded",
        message: statePayload.message,
      });
      elements.nowLead.textContent = "NO PRODUCT";
      return;
    }
    if (statePayload.status === "error") {
      states.showOverlay({
        title: "Product rejected",
        message: statePayload.message,
        error: true,
      });
      return;
    }

    if (statePayload.mode === "series") {
      await bootSeries(statePayload);
      return;
    }

    const leads = statePayload.leads ?? [];
    // Item-2 residual (owner adjudication): a flat product realises a single
    // event-maximum frame — a hindcast window, never a forecast lead. Lead-mode
    // words ("NOW", "+3h", "now") are therefore banned from its display path:
    // the hero reads STATUS 48 H EVENT MAXIMUM and the ticks name the event
    // window instead of formatLead(0)="now".
    const temporalAggregation =
      statePayload.snapshots?.[0]?.temporal_aggregation ?? null;
    const singleEventMaximum =
      temporalAggregation === "event_maximum" && leads.length === 1;
    const firstFrame = await timeline.configure(
      leads,
      loadProductFrame,
      singleEventMaximum
        ? {
            horizonLabel: "48 H EVENT MAXIMUM",
            tickStartLabel: "EVENT WINDOW START",
            tickEndLabel: "48 h EVENT MAXIMUM",
          }
        : {}
    );
    if (singleEventMaximum) {
      // Re-asserted after configure() so the label survives any future
      // reordering inside configure's branch logic.
      elements.nowLead.textContent = "48 H EVENT MAXIMUM";
    }
    // Forecast products (producer run ids carry "forecast"; label field may be
    // absent): name the stream honestly instead of the generic single-frame
    // fallback. Lead value comes from the realized snapshot, never typed.
    const snap0 = statePayload.snapshots?.[0] ?? {};
    const isForecastRun = /forecast/i.test(String(snap0.run_id ?? ""));
    if (!singleEventMaximum && leads.length === 1 && isForecastRun) {
      elements.nowLead.textContent =
        "FORECAST · lead " + (snap0.lead_minutes ?? 0) + " min";
    }
    env.frameA = firstFrame;
    rail.updateStats(firstFrame, env.roads);
    states.hideOverlay();
    window.__bootStage = "done";
    if (leads.length > 1) {
      await timeline.ensureFrames(loadProductFrame);
      timeline.play(); // auto-play once on load, then hold at +3h (guide §6)
    }

    // Drain snapshot meta for the rail panel (blobs stay lazy until toggled).
    try {
      const drainMeta = await (await fetch("/api/drains/meta", { cache: "no-store" })).json();
      if (!env.drains) rail.setDrainStatus(drainMeta);
    } catch (err) {
      rail.setDrainStatus({ available: false, reason: String(err.message ?? err) });
    }
  } catch (err) {
    states.showOverlay({
      title: "Dashboard failed closed",
      message: err instanceof Error ? err.message : String(err),
      error: true,
    });
  }
}

// Frame-series path (WF-3b replay series): values are series indices, labels
// are valid times, play sweeps the whole realized event.
async function bootSeries(statePayload) {
  const series = statePayload.series;
  rail.setPeakClaim(series);
  $("peak-panel").classList.remove("hidden");
  const indices = series.frames.map((f) => f.index);
  const describe = (i) => Timeline.formatValidTime(series.frames[i].valid_time_utc);
  const fetchFrame = loadSeriesFrame;

  if (!series.all_warm) {
    elements.nowLead.textContent =
      "WARMING FRAMES " + series.warm_frames + "/" + series.n_frames;
    // Poll until every frame is warm — play never runs on a partial series.
    for (;;) {
      await new Promise((r) => setTimeout(r, 2000));
      const state = await loadState();
      if (state.status !== "ready" || state.mode !== "series") {
        states.showOverlay({ title: "Series unavailable", message: state.message ?? "series error", error: true });
        return;
      }
      const s = state.series;
      elements.nowLead.textContent = "WARMING FRAMES " + s.warm_frames + "/" + s.n_frames;
      if (s.all_warm) break;
      if (Object.keys(s.invalid_frames ?? {}).length) {
        states.showOverlay({
          title: "Frames rejected",
          message: "Some frames failed verification and the series is refused: " +
            Object.values(s.invalid_frames)[0],
          error: true,
        });
        return;
      }
    }
  }

  const firstFrame = await timeline.configure(indices, fetchFrame, {
    mode: "series",
    describe,
    horizonLabel: describe(indices[indices.length - 1]),
  });
  timeline.seriesFrames = series.frames;
  env.frameA = firstFrame;
  rail.updateStats(firstFrame, env.roads);
  states.hideOverlay();
  window.__bootStage = "loading-frames";
  if (indices.length > 1) {
    elements.nowLead.textContent = "LOADING FRAMES…";
    // Fetch each frame and pre-build its world paths while the network is
    // busy, so the 3-second sweep itself only strokes cached paths (60fps).
    for (const index of indices) {
      const frame = await fetchFrame(index);
      timeline.frames.set(index, frame);
      prewarmWaterWorld(env, frame);
    }
    elements.nowLead.textContent = describe(indices[indices.length - 1]);
    timeline.play(); // auto-play once on load, then hold at the final frame
  }
  window.__bootStage = "done";

  try {
    const drainMeta = await (await fetch("/api/drains/meta", { cache: "no-store" })).json();
    if (!env.drains) rail.setDrainStatus(drainMeta);
  } catch (err) {
    rail.setDrainStatus({ available: false, reason: String(err.message ?? err) });
  }
}

// ---- WF-6 INT: fly-to + operator-panel selection wiring -------------------

// Eased ~420ms guide motion centring the target world point (same duration
// and easing curve as the layer cross-fades). The DESTINATION is clamped
// through the view's own bbox-overlap constraint exactly like a pan, so a
// fly-to can never push the city off-screen; reduced motion jumps.
let viewFlyAnim = null;
function animateViewTo(worldXy, ms = 420) {
  const view = env.view;
  if (!view || !Array.isArray(worldXy) || !worldXy.every(Number.isFinite)) return;
  const fromTx = view.tx;
  const fromTy = view.ty;
  // Compute and clamp the destination through the same bbox-overlap
  // constraint every pan passes through, then restore the origin.
  view.tx = env.width / 2 - worldXy[0] * view.scale;
  view.ty = env.height / 2 + worldXy[1] * view.scale;
  view.clampToBbox();
  const toTx = view.tx;
  const toTy = view.ty;
  view.tx = fromTx;
  view.ty = fromTy;
  if (
    engine.reducedMotion() ||
    (Math.abs(toTx - fromTx) < 0.5 && Math.abs(toTy - fromTy) < 0.5)
  ) {
    view.tx = toTx;
    view.ty = toTy;
    engine.invalidateStatic();
    engine.invalidateDynamic();
    engine.invalidateUI();
    engine.onViewChanged?.();
    return;
  }
  if (viewFlyAnim) viewFlyAnim.done = true; // one active fly at a time
  const start = performance.now();
  const anim = {
    done: false,
    continuous: false,
    tick(now) {
      const t = Math.min(1, (now - start) / ms);
      const e = t * t * (3 - 2 * t);
      view.tx = fromTx + (toTx - fromTx) * e;
      view.ty = fromTy + (toTy - fromTy) * e;
      engine.invalidateStatic();
      engine.invalidateDynamic();
      engine.invalidateUI();
      if (t >= 1) {
        anim.done = true;
        engine.onViewChanged?.(); // LOD policy re-evaluates after the motion
      }
      return !anim.done;
    },
  };
  viewFlyAnim = anim;
  engine.animate(anim);
}

// Ward centroids come from the served meta sidecar of the ACTIVE vintage —
// fetched once, by name only; a miss or failure flies nowhere (no guessed
// coordinate).
let wardCentroidsPromise = null;
function wardCentroids() {
  if (!wardCentroidsPromise) {
    const vintage =
      String(globalThis.__CONTEXT_VINTAGE ?? "2022").replace(/[^0-9]/g, "") || "2022";
    wardCentroidsPromise = fetch(`/api/context/wards_${vintage}.meta.json`, {
      cache: "no-store",
    })
      .then((res) => {
        if (!res.ok) throw new Error(`wards meta failed (${res.status})`);
        return res.json();
      })
      .then((meta) => {
        const map = new Map();
        for (const ward of meta.wards ?? []) {
          if (ward?.name && Array.isArray(ward.centroid_m)) map.set(ward.name, ward.centroid_m);
        }
        return map;
      });
  }
  return wardCentroidsPromise;
}

document.addEventListener("wf6:select-street", (event) => {
  const detail = event.detail ?? {};
  const segmentId = Number(detail.segment_id);
  if (!env.ready.basemap || !Number.isFinite(segmentId)) return;
  // Street midpoint via the road network's own LOD buffers — same approach
  // as rail.js's N10 context line; no second geometry source is invented.
  const midpoint = rail.segmentMidpoint(env.roads, segmentId);
  if (midpoint) animateViewTo(midpoint);
  selectSegmentLikePick(segmentId);
});

document.addEventListener("wf6:select-intersection", (event) => {
  const xy = event.detail?.world_xy;
  if (!env.ready.basemap) return;
  // Only a realized coordinate flies the map; absent world_xy stays a no-op
  // rather than deriving a position the payload did not carry.
  if (Array.isArray(xy) && xy.length === 2 && xy.every(Number.isFinite)) {
    animateViewTo(xy);
  } else {
    console.info(
      "[wf6] intersection",
      event.detail?.node_id ?? "",
      "carries no world_xy — no fly-to performed"
    );
  }
});

document.addEventListener("wf6:select-ward", (event) => {
  const name = event.detail?.name;
  if (!env.ready.basemap || typeof name !== "string") return;
  wardCentroids()
    .then((centroids) => {
      const xy = centroids.get(name);
      if (xy) animateViewTo(xy);
    })
    .catch((err) => {
      console.warn("[wf6] ward fly-to unavailable:", err instanceof Error ? err.message : err);
    });
});

boot();

// Keep the render environment honest about the reduced-motion preference.
const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
const syncMotion = () => {
  env.reducedMotion = motionQuery.matches;
};
if (motionQuery.addEventListener) motionQuery.addEventListener("change", syncMotion);
syncMotion();
