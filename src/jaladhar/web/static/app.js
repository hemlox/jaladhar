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
import { DEPTH, pickSegment as renderPickSegment, prewarmWaterWorld, refreshPalette } from "./js/render.js";
import { Timeline } from "./js/timeline.js";
import { Rail } from "./js/rail.js";
import * as states from "./js/states.js";

// WF-6 INT integration: the self-mounting operator panels join the import
// graph here (watchlist + search mount themselves idempotently). The causal
// panel was removed from the rail with the drain sections (owner 2026-09-10).
import "./js/watchlist.js";
import "./js/search.js";
// GAP-1 deferred routing: avoid ERR_CONNECTION_REFUSED on standalone dashboard
// routing.js auto-fetches http://127.0.0.1:8502/policies on import; defer until user interaction
let _routingLoaded = false;
let _routingLoadPromise = null;
async function _ensureRouting(){
  if(_routingLoaded) return;
  if(_routingLoadPromise) return _routingLoadPromise;
  _routingLoaded = true;
  _routingLoadPromise = import("./js/routing.js").catch(e=>{ console.warn("[routing] lazy load failed", e?.message ?? e); });
  return _routingLoadPromise;
}
// Defer fetch until user opens routing affordance: first click on watchlist or explicit route-request
document.addEventListener("wf6:route-request", (e)=>{
  if(!_routingLoaded){
    e.stopImmediatePropagation();
    _ensureRouting().then(()=> document.dispatchEvent(new CustomEvent("wf6:route-request",{detail:e.detail})));
  }
}, true);
document.addEventListener("click", (e)=>{
  const w = document.getElementById("watchlist");
  if(w && w.contains(e.target)) _ensureRouting();
}, {capture:true});
// Also trigger on first interaction anywhere that hints at routing (route button)
// GAP-1: no auto-load observer — routing stays deferred until explicit user interaction (click or route-request)
import "./js/comparison.js";
import "./js/live.js";

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

// ---- B7 mode: LIVE default, explicit toggle, localStorage persist only after click ----
const MODE_KEY = "jaladhar_mode";
const DEMO_CHIP = "DEMO \u00b7 Sept 2022 event";
const DEMO_SUB = "Sept 2022 event \u00b7 3-hour forecast issued 00:10 IST";
const LIVE_CHIP = "LIVE";
const LIVE_SUB = "no live forecast run yet";

function getStoredMode(){
  try{ const v=localStorage.getItem(MODE_KEY); if(v==="demo"||v==="live") return v; }catch{}
  return null;
}
function persistMode(m){ try{ localStorage.setItem(MODE_KEY,m);}catch{} }
function currentMode(){ return globalThis.__JALADHAR_MODE || "live"; }
function updateChip(mode){
  const main=document.getElementById("mode-chip-main");
  const sub=document.getElementById("mode-chip-sub");
  if(main) main.textContent = mode==="demo" ? DEMO_CHIP : LIVE_CHIP;
  if(sub){
    if(mode==="demo"){
      sub.textContent = "";
      sub.classList.add("hidden");
      sub.setAttribute("aria-hidden","true");
    } else {
      sub.textContent = LIVE_SUB;
      sub.classList.remove("hidden");
      sub.removeAttribute("aria-hidden");
    }
  }
  document.body.classList.toggle("mode-live", mode==="live");
  document.body.classList.toggle("mode-demo", mode==="demo");
  // demo (or any populated mode) clears the live-empty state; enterLive re-adds it
  if(mode!=="live") document.body.classList.remove("live-empty");
  const demoBtn=document.getElementById("btn-demo-toggle");
  const liveBtn=document.getElementById("btn-live-return");
  const restartBtn=document.getElementById("btn-demo-restart");
  // exactly ONE opposite-mode toggle visible
  if(demoBtn) demoBtn.classList.toggle("hidden", mode!=="live");
  if(liveBtn) liveBtn.classList.toggle("hidden", mode!=="demo");
  if(restartBtn) restartBtn.classList.toggle("hidden", mode!=="demo");
  if(elements.gateNote){ elements.gateNote.textContent=""; elements.gateNote.classList.add("hidden"); }
  // runLabel only in demo mode — single line, ellipsis, no overflow
  if(elements.runLabel){
    if(mode==="live"){
      elements.runLabel.textContent = "";
      elements.runLabel.classList.add("hidden");
      elements.runLabel.removeAttribute("title");
      elements.runLabel.setAttribute("aria-hidden","true");
    } else {
      elements.runLabel.textContent = DEMO_SUB;
      elements.runLabel.title = "Demo 8-frame forecast 18:40\u201321:40Z (+30min over-run)";
      elements.runLabel.classList.remove("hidden");
      elements.runLabel.removeAttribute("aria-hidden");
    }
  }
  // status dot: hide demo provenance dot in LIVE
  if(elements.statusDot){
    if(mode==="live") elements.statusDot.classList.add("hidden");
    else elements.statusDot.classList.remove("hidden");
  }
  // LIVE FORECAST card is a live-mode affordance only (owner 2026-08-27):
  // hidden entirely in demo mode where the demo play-out owns the rail.
  const livePanel = document.getElementById("live-panel");
  if (livePanel) livePanel.classList.toggle("hidden", mode !== "live");
  globalThis.__JALADHAR_MODE = mode;
  globalThis.__JALADHAR_STATE = globalThis.__JALADHAR_STATE || {};
  globalThis.__JALADHAR_STATE.mode = mode;
}

const timeline = new Timeline({
  slider: elements.scrubber,
  playBtn: elements.playBtn,
  tickStart: elements.tickStart,
  tickEnd: elements.tickEnd,
  nowLead: elements.nowLead,
});

const engine = new Engine($("cv-static"), $("cv-dyn"), $("cv-ui"));
// WF7 comparison integration: expose the shared render environment through a
// single global so comparison.js can return via the SAME state flow (timeline
// slider + onFrameChange) without cloning logic. No new state is introduced.
globalThis.__JALADHAR_ENGINE = engine;

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
globalThis.__JALADHAR_ENV = env;
globalThis.__JALADHAR_TIMELINE = timeline;

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
// highlight + inspector panel (the causal-link panel was removed with the
// drain sections, owner 2026-09-10).
async function selectSegmentLikePick(segmentId) {
  const index = env.roads.indexOfSegment(segmentId);
  if (index == null) return; // not in the realized network — nothing to claim
  document.body.classList.remove("hide-right"); // a selection owns the detail rail again
  env.selectedSeg = index;
  engine.invalidateUI();
  await rail.selectSegment(
    segmentId,
    currentTimelineContext(),
    env.roads,
    env.drains?.meta ?? null
  );
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
    document.body.classList.remove("hide-right"); // a pick owns the detail rail again
    const segmentId = env.roads.segmentIds[hit];
    await rail.selectSegment(
      segmentId,
      currentTimelineContext(),
      env.roads,
      env.drains?.meta ?? null
    );
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
  // GAP-4 restart proof observable
  try{
    const idx = leadIndex ?? 0;
    globalThis.__JALADHAR_DEMO_TICK = {i: idx, frac, ts: Date.now(), lead};
    globalThis.__JALADHAR_STATE = globalThis.__JALADHAR_STATE || {};
    globalThis.__JALADHAR_STATE.demoTick = globalThis.__JALADHAR_DEMO_TICK;
  }catch{}
};


// GAP-3 over-run honesty: annotate beyond-horizon valid times (last frame 22:10Z)
// Presentation-only; horizon derived from realized series frames (offset diff), NOT from comparison payload.
// Dims timeline tick and watchlist rows for valid_time == last frame's valid_time_utc, suffix '· beyond horizon (+30m)' or fallback '· verified beyond-horizon run state'
function _beyondInfo(){
  try{
    const frames = timeline.seriesFrames ?? globalThis.__JALADHAR_TIMELINE?.seriesFrames ?? null;
    if(!frames || frames.length<2) return null;
    const last = frames[frames.length-1];
    const prev = frames[frames.length-2];
    const lastValid = last?.valid_time_utc ?? last?.valid_time ?? null;
    const prevValid = prev?.valid_time_utc ?? null;
    if(!lastValid) return null;
    // compute +30m if offsets available, else fallback without time claim
    let mins = 30;
    try{
      if(Number.isFinite(last.offset_seconds) && Number.isFinite(prev.offset_seconds)){
        mins = Math.round((last.offset_seconds - prev.offset_seconds)/60);
      }
    }catch{}
    const suffix = mins ? `\u00b7 beyond horizon (+${mins}m)` : "\u00b7 verified beyond-horizon run state";
    const fallbackSuffix = "\u00b7 verified beyond-horizon run state";
    // Prefer time-derived suffix if mins>0 else fallback
    const chosen = mins>0 ? suffix : fallbackSuffix;
    return {lastValid, prevValid, suffix: chosen, lastTag: last.tag ?? null};
  }catch{ return null; }
}
function _annotateTimelineBeyond(){
  try{
    const info = _beyondInfo();
    if(!info) return;
    const tickEnd = document.getElementById("tick-end");
    if(tickEnd){
      const base = Timeline.formatValidTime(info.lastValid);
      // avoid duplicating suffix if already present
      if(!tickEnd.textContent.includes("beyond")){
        const suffixSpan = document.createElement("span");
        suffixSpan.className = "beyond-hint";
        suffixSpan.textContent = " " + info.suffix;
        suffixSpan.style.cssText = "font-size:10px;color:var(--ink-faint);margin-left:6px;font-style:italic;";
        // tickEnd currently holds formatted time; append suffix
        tickEnd.textContent = base;
        tickEnd.appendChild(suffixSpan);
        tickEnd.classList.add("tick-beyond");
        tickEnd.title = "beyond-horizon over-run ("+info.suffix+")";
        tickEnd.style.opacity = "0.55";
      }
    }
    const leadEl = document.getElementById("now-lead");
    // nowLead shows VALID time; when last frame is held, it already shows last time — add dim via class
  }catch{}
}
function _annotateWatchlistBeyond(){
  try{
    const info = _beyondInfo();
    if(!info) return;
    const rows = document.querySelectorAll("#watchlist .wf6-row, #watchlist .wf6-lead");
    // watchlist rows use .wf6-lead text containing formatted valid time
    const targetText = Timeline.formatValidTime(info.lastValid);
    document.querySelectorAll("#watchlist .wf6-row").forEach(row=>{
      const leadEl = row.querySelector(".wf6-lead");
      if(leadEl && leadEl.textContent.trim()===targetText && !leadEl.textContent.includes("beyond")){
        const hint = document.createElement("span");
        hint.className = "beyond-hint";
        hint.textContent = " " + info.suffix;
        hint.style.cssText = "font-size:10px;color:var(--ink-faint);margin-left:4px;font-style:italic;";
        leadEl.appendChild(hint);
        leadEl.classList.add("beyond-horizon");
        leadEl.style.opacity = "0.55";
        leadEl.title = info.suffix;
        row.classList.add("beyond-horizon");
        row.style.opacity = "0.55";
      }
    });
    // also dim intersection rows if any with same time (rare)
  }catch{}
}
// Observe watchlist body for row renders
function _installBeyondObserver(){
  try{
    const info = _beyondInfo();
    if(!info) return;
    _annotateTimelineBeyond();
    const host = document.getElementById("watchlist");
    if(!host) return;
    const body = host.querySelector(".wf6-body") ?? host;
    const obs = new MutationObserver(()=>{ _annotateTimelineBeyond(); _annotateWatchlistBeyond(); });
    obs.observe(body, {childList:true, subtree:true});
    // also poll after series loads
    setTimeout(()=>{ _annotateTimelineBeyond(); _annotateWatchlistBeyond(); }, 800);
    setTimeout(()=>{ _annotateTimelineBeyond(); _annotateWatchlistBeyond(); }, 2000);
    setTimeout(()=>{ _annotateTimelineBeyond(); _annotateWatchlistBeyond(); }, 5000);
  }catch{}
}
// hook after series loads: wrap bootSeries to install observer

async function boot() {
  states.loading();
  // Owner 2026-09-09: the detail rail starts HIDDEN — its startup summary
  // lives in the watchlist's Overview view instead. The rail still pops up
  // for any street/ward/intersection selection (see the selection paths
  // below); the yellow traffic light toggles it manually either way.
  document.body.classList.add("hide-right");
  env.reducedMotion = engine.reducedMotion();
  window.__bootStage = "state";
  let basemapMeta=null;
  try {
    basemapMeta = await loadBasemapMeta();
    assertDepthRule(basemapMeta.depth_rule);
    syncLegendRanges();
    env.view = new View(basemapMeta.bbox);
    env.dpr = engine.dpr;
    env.width = engine.width;
    env.height = engine.height;
    env.view.fit(engine.width, engine.height);
    await ensureBasemap(basemapMeta);
    engine.invalidateStatic();
    window.__bootStage = "mode";
    // Decide mode: stored explicit -> use it, else LIVE default WITHOUT persisting
    const stored=getStoredMode();
    const hasExplicit= stored!==null;
    let mode = hasExplicit ? stored : "live";
    updateChip(mode);
    wireModeToggles(basemapMeta);
    wireDiagToggle();
    wireTrafficLights();
    wireThemeToggle();
    if(mode==="live"){
      await enterLive(basemapMeta);
      window.__bootStage="done";
      try{ const dm=await (await fetch("/api/drains/meta",{cache:"no-store"})).json(); if(!env.drains) rail.setDrainStatus(dm); }catch{}
      if(elements.gateNote) elements.gateNote.textContent="";
      return;
    }
    // DEMO path
    window.__bootStage="series-or-lead";
    const statePayload = await loadState();
    // B9 strip G1 badge immediately after rail writes it
    rail.setRunLabel(statePayload);
    if(elements.gateNote){ elements.gateNote.textContent=""; elements.gateNote.classList.add("hidden"); }
    if(elements.runLabel){ elements.runLabel.textContent=DEMO_SUB; elements.runLabel.title="Demo 8-frame forecast 18:40–21:40Z (+30min over-run)"; }
    if(statePayload.status==="empty"){
      states.showOverlay({title:"No run loaded", message: statePayload.message});
      elements.nowLead.textContent="NO PRODUCT";
      return;
    }
    if(statePayload.status==="error"){
      states.showOverlay({title:"Product rejected", message: statePayload.message, error:true});
      return;
    }
    if(statePayload.mode==="series"){
      await bootSeries(statePayload);
      // annotate beyond-horizon last tick honestly
      try{
        const ticks=document.getElementById("tick-end");
        if(ticks) { ticks.classList.add("tick-beyond"); ticks.title="beyond-horizon over-run (+30 min)"; }
      }catch{}
      return;
    }
    // Fallback flat (should not happen in wf8, but keep honest without 48h wording)
    const leads = statePayload.leads ?? [];
    const firstFrame = await timeline.configure(leads, loadProductFrame, { horizonLabel: leads.length>1 ? DEMO_SUB : "Single realized frame" } );
    env.frameA = firstFrame;
    if(firstFrame) rail.updateStats(firstFrame, env.roads);
    states.hideOverlay();
    window.__bootStage="done";
    if(leads.length>1){ await timeline.ensureFrames(loadProductFrame); timeline.play(); }
    try{ const dm=await (await fetch("/api/drains/meta",{cache:"no-store"})).json(); if(!env.drains) rail.setDrainStatus(dm); }catch(err){ rail.setDrainStatus({available:false, reason: String(err.message ?? err)}); }
  } catch (err) {
    states.showOverlay({title:"Dashboard failed closed", message: err instanceof Error ? err.message : String(err), error:true});
  }
}

async function enterLive(basemapMeta){
  updateChip("live");
  // timeline/hyeto disabled visible
  try{ timeline.stop(); }catch{}
  if(elements.scrubber){ elements.scrubber.disabled=true; elements.scrubber.value="0"; }
  if(elements.playBtn){ elements.playBtn.disabled=true; elements.playBtn.textContent="▶"; elements.playBtn.title="Live mode — no forecast frames"; }
  if(elements.tickStart) elements.tickStart.textContent="—";
  if(elements.tickEnd){ elements.tickEnd.textContent="—"; elements.tickEnd.classList.remove("tick-beyond"); }
  if(elements.nowLead) elements.nowLead.textContent=LIVE_CHIP;
  const peakPanel=document.getElementById("peak-panel");
  if(peakPanel) peakPanel.classList.add("hidden");
  if(elements.statFlooded) elements.statFlooded.textContent="—";
  if(elements.statDeepest) elements.statDeepest.textContent="—";
  if(elements.statDeepestLoc) elements.statDeepestLoc.textContent="";
  // D1+D2+D3: hide LIVE demo leakage — timeline KPI and watchlist
  try{
    const kpi=document.getElementById("wf6tl-kpi");
    if(kpi){ kpi.style.display="none"; kpi.setAttribute("aria-hidden","true"); }
    const kpiCluster=document.getElementById("wf6tl-cluster");
    if(kpiCluster){ /* keep cluster but hide kpi text */ }
    // watchlist honest empty — dispatch so panel can react
    document.dispatchEvent(new CustomEvent("jaladhar:live-enter"));
    const wl=document.getElementById("watchlist");
    if(wl){
      const body=wl.querySelector(".wf6-body");
      const chip=wl.querySelector(".wf6-chip");
      const statusLine=wl.querySelector(".wf6-statusline");
      if(chip) chip.textContent="—";
      if(statusLine) statusLine.style.display="none";
      if(body){
        body.replaceChildren();
        const empty=document.createElement("div");
        empty.className="wf6-empty";
        empty.textContent="no live run — streets at risk appear after the first live forecast";
        body.appendChild(empty);
      }
    }
    // ensure timeline length label hidden in LIVE (demo-only)
    const len=document.getElementById("wf6tl-length");
    if(len) len.style.display="none";
  }catch{}
  // clear any flood frame
  env.frameA=null; env.frameB=null; env.interpFrac=0;
  engine.invalidateDynamic();
  engine.invalidateStatic();
  // honest empty-state over still-visible basemap
  states.showOverlay({title: LIVE_CHIP, message: "no live forecast run yet — the map shows Bengaluru streets and lakes. Use Demo: Sept 2022 event to view the 8-frame 3-hour forecast (18:40–21:40Z, +30min beyond-horizon) until a real run exists."});
  // honest live probe
  try{
    const r=await fetch("/api/forecast/latest",{cache:"no-store"});
    if(r.status===404){
      // remain empty
    }else if(r.ok){
      const payload=await r.json();
      const mp=payload.manifest_path || payload.manifestPath || "";
      const wc=payload.wall_clock_s ?? payload.wall_clock_min ?? "";
      states.showOverlay({title:"LIVE forecast available", message: "job "+(payload.job_id||"")+" "+(payload.status||"")+(mp?" manifest "+mp:"")+(wc?" wall "+wc:"")});
      globalThis.__JALADHAR_LIVE_PAYLOAD=payload;
    }
  }catch{}
  globalThis.__JALADHAR_STATE={mode:"live", liveEmpty:true, frameIndex:null};
  // Hide the empty numeric readouts so LIVE never shows bare "—" placeholders;
  // the LIVE FORECAST panel already explains the no-run state.
  document.body.classList.add("live-empty");
  globalThis.__JALADHAR_TIMELINE = timeline;
  if(elements.gateNote) elements.gateNote.textContent="";
}

async function reloadForMode(mode, basemapMeta){
  updateChip(mode);
  if(mode==="live"){
    await enterLive(basemapMeta);
    return;
  }
  // demo reload
  states.loading();
  try{
    // restore LIVE-hidden elements for demo
    try{
      const kpi=document.getElementById("wf6tl-kpi");
      if(kpi){ kpi.style.display=""; kpi.removeAttribute("aria-hidden"); }
      const statusLine=document.querySelector("#watchlist .wf6-statusline");
      if(statusLine) statusLine.style.display="";
      const len=document.getElementById("wf6tl-length");
      if(len) len.style.display="";
      document.dispatchEvent(new CustomEvent("jaladhar:demo-enter"));
    }catch{}
    const statePayload=await loadState();
    rail.setRunLabel(statePayload);
    if(elements.gateNote){ elements.gateNote.textContent=""; elements.gateNote.classList.add("hidden"); }
    if(elements.runLabel){ elements.runLabel.textContent=DEMO_SUB; elements.runLabel.classList.remove("hidden"); }
    if(statePayload.status!=="ready" || statePayload.mode!=="series"){
      states.showOverlay({title:"Demo unavailable", message: statePayload.message ?? "series error", error:true});
      return;
    }
    await bootSeries(statePayload);
    try{ const ticks=document.getElementById("tick-end"); if(ticks){ticks.classList.add("tick-beyond"); ticks.title="beyond-horizon over-run (+30 min)";}}catch{}
  }catch(err){
    states.showOverlay({title:"Dashboard failed closed", message: String(err), error:true});
  }
}

// Provenance / diagnostics disclosure. Default collapsed so the working view is
// operator-facing; the labelled toggle keeps the full provenance one click away
// (manifest paths, hashes, model notes stay reachable — never deleted).
function wireDiagToggle(){
  const btn=document.getElementById("btn-diag");
  if(!btn) return;
  const KEY="jaladhar.showDiag";
  let on=false;
  try{ on = localStorage.getItem(KEY)==="1"; }catch{}
  const apply=()=>{
    document.body.classList.toggle("show-diag", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.classList.toggle("on", on);
  };
  apply();
  btn.addEventListener("click", ()=>{
    on=!on;
    try{ localStorage.setItem(KEY, on ? "1" : "0"); }catch{}
    apply();
  });
}

// macOS Light/Dark appearance toggle. The initial data-theme was set before
// first paint by the inline <head> script (from localStorage, else the OS
// preference); here we persist the user's explicit choice and, on each flip,
// re-read the renderer palette and repaint every canvas so the map (ground,
// roads, lakes, and the water technique — additive glow in dark, source-over +
// casing in light) swaps in step with the CSS chrome.
function wireThemeToggle(){
  const btn=document.getElementById("btn-theme");
  if(!btn) return;
  const KEY="jaladhar.theme";
  const root=document.documentElement;
  const syncAria=()=> btn.setAttribute("aria-pressed", root.getAttribute("data-theme")==="dark" ? "true" : "false");
  syncAria();
  btn.addEventListener("click", ()=>{
    const next = root.getAttribute("data-theme")==="dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try{ localStorage.setItem(KEY, next); }catch{}
    syncAria();
    refreshPalette();          // re-read theme-dependent colours into the renderer
    engine.invalidateStatic(); // basemap: ground / wards / lakes / roads / drains
    engine.invalidateDynamic();// water layer (+ its composite/casing switch)
    engine.invalidateUI();     // ward labels ride the UI canvas
  });
}

// macOS traffic-light controls, wired to real, reversible actions so none is a
// dead button: red toggles the streets panel, yellow the detail rail, green
// toggles browser full screen.
function wireTrafficLights(){
  const red=document.querySelector(".tl-red");
  const yellow=document.querySelector(".tl-yellow");
  const green=document.querySelector(".tl-green");
  if(red) red.addEventListener("click", ()=>{
    document.body.classList.toggle("hide-left");
    // search.js repositions off this event — without it the search box would
    // keep sitting beside a panel that is no longer there.
    document.dispatchEvent(new CustomEvent("wf6:watchlist-toggle", {
      detail: { open: !document.body.classList.contains("hide-left") },
    }));
  });
  if(yellow) yellow.addEventListener("click", ()=> document.body.classList.toggle("hide-right"));
  if(green) green.addEventListener("click", ()=>{
    const el=document.documentElement;
    if(!document.fullscreenElement){ el.requestFullscreen?.(); }
    else { document.exitFullscreen?.(); }
  });
}

function wireModeToggles(basemapMeta){
  const demoBtn=document.getElementById("btn-demo-toggle");
  const liveBtn=document.getElementById("btn-live-return");
  const restartBtn=document.getElementById("btn-demo-restart");
  if(demoBtn && !demoBtn._wired){
    demoBtn._wired=true;
    demoBtn.addEventListener("click", async ()=>{
      const cur=globalThis.__JALADHAR_MODE;
      if(cur==="demo"){
        // B10 re-triggerable restart from 0
        try{ timeline.stop(); }catch{}
        try{
          timeline.slider.value="0";
          const p=timeline.interpolated();
          // _emit expects leadIndex/frac
          timeline._emit(p.leadIndex, p.frac);
          // small delay then play
          timeline.play();
        }catch{}
        return;
      }
      persistMode("demo");
      await reloadForMode("demo", basemapMeta);
    });
  }
  if(liveBtn && !liveBtn._wired){
    liveBtn._wired=true;
    liveBtn.addEventListener("click", async ()=>{
      persistMode("live");
      await reloadForMode("live", basemapMeta);
    });
  }
  if(restartBtn && !restartBtn._wired){
    restartBtn._wired=true;
    restartBtn.addEventListener("click", ()=>{
      if(globalThis.__JALADHAR_MODE!=="demo") return;
      try{ timeline.stop(); }catch{}
      try{
        timeline.slider.value="0";
        const p=timeline.interpolated();
        timeline._emit(p.leadIndex, p.frac);
        timeline.play();
      }catch{}
    });
  }
}

// Frame-series path
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
  try{ _installBeyondObserver(); }catch{}
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

// Ward context comes from the served meta sidecar of the ACTIVE vintage —
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
          if (ward?.name && Array.isArray(ward.centroid_m)) map.set(ward.name, ward);
        }
        return map;
      });
  }
  return wardCentroidsPromise;
}

// Per-ward geographic extent, decoded from the realized ward blob (same file
// the ward layer renders). One fetch per session, cached; the bbox is computed
// over the ward's own rings — never guessed from area or centroid.
let wardBlobPromise = null;
function wardBlob() {
  if (!wardBlobPromise) {
    const vintage =
      String(globalThis.__CONTEXT_VINTAGE ?? "2022").replace(/[^0-9]/g, "") || "2022";
    wardBlobPromise = fetch(`/api/context/wards_${vintage}.bin`, { cache: "no-store" }).then(
      (res) => {
        if (!res.ok) throw new Error(`wards bin failed (${res.status})`);
        return res.arrayBuffer();
      }
    );
  }
  return wardBlobPromise;
}

async function wardBbox(name) {
  const [wards, buffer] = await Promise.all([wardCentroids(), wardBlob()]);
  const ward = wards.get(name);
  // Meta semantics (verified against served bytes): ring_ranges hold
  // VERTEX-index ranges — e.g. Varthuru [60050,60050+657] with vertex_count
  // 657 — not ring indices. Iterate vertices directly.
  if (!ward || !Array.isArray(ward.ring_ranges)) return null;
  // Blob layout (assertWardsBundle contract): u32 nRings, u32 offsets[nRings+1]
  // (ring -> first vertex), then interleaved f32 x,y per vertex. Ranges index
  // vertices directly, so only the coords table base matters here.
  const nRings = new DataView(buffer, 0, 4).getUint32(0, true);
  const coords = new Float32Array(buffer, 4 + (nRings + 1) * 4);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, found = 0;
  for (const range of ward.ring_ranges) {
    if (!Array.isArray(range) || range.length !== 2) continue;
    for (let v = range[0]; v < range[1]; v++) {
      const x = coords[v * 2];
      const y = coords[v * 2 + 1];
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
      found++;
    }
  }
  if (!found || !(x1 > x0) || !(y1 > y0)) return null;
  return [x0, y0, x1, y1];
}

// Tight bbox for flown-to segment (C13): compute from realized LOD buffers
function segmentBbox(roads, segmentId){
  try{
    const idx = roads?.indexOfSegment(segmentId);
    if(idx==null || idx<0 || idx >= (roads.nSegments ?? 0)) return null;
    for(const [lod, ranges] of roads.segPartCache ?? []){
      const lines = roads.lods.get(lod);
      if(!lines || !ranges) continue;
      if(idx+1 >= ranges.segOffsets.length) continue;
      if(ranges.segOffsets[idx+1] === ranges.segOffsets[idx]) continue;
      let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
      let found=false;
      for(let p=ranges.segOffsets[idx]; p < ranges.segOffsets[idx+1]; p++){
        const part = ranges.partIds[p];
        const start = lines.offsets[part];
        const end = lines.offsets[part+1];
        for(let v=start; v < end; v++){
          const x = lines.coords[v*2];
          const y = lines.coords[v*2+1];
          if(!Number.isFinite(x) || !Number.isFinite(y)) continue;
          if(x<x0) x0=x; if(x>x1) x1=x; if(y<y0) y0=y; if(y>y1) y1=y;
          found=true;
        }
      }
      if(found) return [x0,y0,x1,y1];
    }
  }catch{}
  return null;
}
function animateViewToBbox(bbox, ms=420){
  const view = env.view;
  if(!view || !Array.isArray(bbox) || bbox.length!==4) return false;
  let [x0,y0,x1,y1] = bbox;
  if(!(x1>x0) || !(y1>y0)){ // point-like: fall back to midpoint pan at tight zoom
    const cx=(x0+x1)/2, cy=(y0+y1)/2;
    if(!Number.isFinite(cx) || !Number.isFinite(cy)) return false;
    // derive small padding box ~ 300m square around point
    const pad = 150;
    x0=cx-pad; x1=cx+pad; y0=cy-pad; y1=cy+pad;
  }
  const dx = Math.max(1, x1 - x0);
  const dy = Math.max(1, y1 - y0);
  const pad = 24; // small padding per spec
  const W = env.width, H = env.height;
  if(!(W>0) || !(H>0)) return false;
  const fitScale = Math.min((W - pad*2)/dx, (H - pad*2)/dy, view.maxScale);
  const targetScale = Math.min(Math.max(fitScale, view.minScale), view.maxScale);
  const cx = (x0 + x1)/2, cy = (y0 + y1)/2;
  const toTx = W/2 - cx * targetScale;
  const toTy = H/2 + cy * targetScale;
  // save target then clamp through existing logic, restore origin
  const fromScale = view.scale, fromTx = view.tx, fromTy = view.ty;
  view.scale = targetScale; view.tx = toTx; view.ty = toTy;
  view.clampToBbox();
  const clampedScale = view.scale, clampedTx = view.tx, clampedTy = view.ty;
  view.scale = fromScale; view.tx = fromTx; view.ty = fromTy;
  if(engine.reducedMotion() || (Math.abs(clampedTx-fromTx)<0.5 && Math.abs(clampedTy-fromTy)<0.5 && Math.abs(clampedScale-fromScale)<0.001)){
    view.scale = clampedScale; view.tx = clampedTx; view.ty = clampedTy;
    engine.invalidateStatic(); engine.invalidateDynamic(); engine.invalidateUI();
    engine.onViewChanged?.();
    return true;
  }
  if(viewFlyAnim) viewFlyAnim.done = true;
  const start = performance.now();
  const anim = {
    done: false, continuous: false,
    tick(now){
      const t = Math.min(1, (now-start)/ms);
      const e = t*t*(3 - 2*t);
      view.scale = fromScale + (clampedScale - fromScale)*e;
      view.tx = fromTx + (clampedTx - fromTx)*e;
      view.ty = fromTy + (clampedTy - fromTy)*e;
      engine.invalidateStatic(); engine.invalidateDynamic(); engine.invalidateUI();
      if(t>=1){ anim.done=true; engine.onViewChanged?.(); }
      return !anim.done;
    }
  };
  viewFlyAnim = anim;
  engine.animate(anim);
  return true;
}
document.addEventListener("wf6:select-street", (event) => {
  const detail = event.detail ?? {};
  const segmentId = Number(detail.segment_id);
  if (!env.ready.basemap || !Number.isFinite(segmentId)) return;
  const bbox = segmentBbox(env.roads, segmentId);
  if(bbox){
    animateViewToBbox(bbox);
  } else {
    const midpoint = rail.segmentMidpoint(env.roads, segmentId);
    if (midpoint) animateViewTo(midpoint);
  }
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
  // Owner 2026-08-27: selecting a ward (search pick or press) flies OUT to the
  // ward's vicinity — fit the ward's realized ring extent with a comfortable
  // margin, not a centroid pan at whatever zoom the map was left at.
  wardBbox(name)
    .then((bbox) => {
      if (bbox) {
        // Expand ~25% linearly per side so the ward reads as an area in
        // context, not a screen-filling polygon.
        const [x0, y0, x1, y1] = bbox;
        const mx = (x1 - x0) * 0.25;
        const my = (y1 - y0) * 0.25;
        animateViewToBbox([x0 - mx, y0 - my, x1 + mx, y1 + my]);
      } else {
        // Honest fallback: centroid pan at current zoom when the ward's ring
        // extent is unavailable from the served context.
        wardCentroids()
          .then((wards) => {
            const ward = wards.get(name);
            const xy = ward?.centroid_m;
            if (Array.isArray(xy)) animateViewTo(xy);
          })
          .catch((err) => {
            console.warn("[wf6] ward fly-to unavailable:", err instanceof Error ? err.message : err);
          });
      }
    })
    .catch((err) => {
      console.warn("[wf6] ward extent fly-to failed:", err instanceof Error ? err.message : err);
    });
});

// Escape restores hero: clear street/ward selection, reset highlight, and
// return the detail rail to its hidden start state (it belongs to a
// selection; with none, the watchlist Overview carries the summary).
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    try { env.selectedSeg = null; engine.invalidateUI(); } catch {}
    try { rail.clearSelection(); } catch {}
    try { document.body.classList.add("hide-right"); } catch {}
  }
});

// A ward or intersection selection also owns the detail rail (owner
// 2026-09-09: it must pop up for zoom-ins via map, search, or watchlist).
document.addEventListener("wf6:select-ward", () => {
  document.body.classList.remove("hide-right");
});
document.addEventListener("wf6:select-intersection", () => {
  document.body.classList.remove("hide-right");
});

boot();

// Keep the render environment honest about the reduced-motion preference.
const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
const syncMotion = () => {
  env.reducedMotion = motionQuery.matches;
};
if (motionQuery.addEventListener) motionQuery.addEventListener("change", syncMotion);
syncMotion();
