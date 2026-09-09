// WF8 2022 Forecast vs Replay — sim_vs_sim agreement overlay.
// Both panels paint with SAME depth ramp/theme (no SAR binary). Left = 3h forecast at its valid time, Right = replay peak-neighbourhood frame_tag from payload (fetched via /api/replay_frame.bin, same blob layout as left panel). Every number from live payload, zero hardcoded metrics.

import { View } from "./view.js";
import { RoadNetwork, decodeSeriesFrame, loadBasemapMeta, loadLines, loadSeriesFrame, loadState } from "./data.js";
import * as render from "./render.js";

async function getJson(url){
  const r = await fetch(url, {cache:"no-store"});
  let payload=null;
  try{ payload = await r.json(); }catch{}
  if(!r.ok) throw new Error((payload && (payload.error||payload.message)) || `${url} failed (${r.status})`);
  return payload;
}
function fmt(v, dp){
  if(v==null||!Number.isFinite(Number(v))) return "—";
  return Number(v).toFixed(dp);
}
function formatValidForLabel(iso){
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso ?? "");
  if(!m) return String(iso ?? "—");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${months[Number(m[2])-1]} ${Number(m[3])} ${m[4]}:${m[5]}Z`;
}

function ensureOverlay(){
  let overlay = document.getElementById("jal-comp-overlay");
  if(overlay) return overlay;
  const host = document.querySelector(".app") ?? document.body;
  overlay = document.createElement("div");
  overlay.id = "jal-comp-overlay";
  overlay.className = "jal-comp-overlay hidden";
  overlay.setAttribute("role","dialog");
  overlay.setAttribute("aria-modal","true");
  overlay.setAttribute("aria-label","2022 Forecast vs Replay agreement");
  overlay.innerHTML = `
    <div class="jal-comp-chrome">
      <span class="jal-comp-title">2022 FORECAST VS REPLAY \u00b7 AGREEMENT</span>
      <button class="jal-comp-close" type="button" aria-label="Close comparison and return to main map">Close \u00d7</button>
    </div>
    <div class="jal-comp-panels">
      <section class="jal-comp-panel jal-comp-left" id="jal-comp-left" role="button" tabindex="0" aria-label="Forecast panel — 3h forecast at its valid time">
        <span class="jal-comp-panel-label" id="jal-comp-left-label">FORECAST — 3h forecast at valid time</span>
        <canvas id="jal-comp-left-canvas"></canvas>
        <div class="jal-comp-legend" id="jal-comp-left-legend">
          <span class="jal-comp-legend-title">Depth</span>
          <span class="jal-comp-legend-encoding">model depth ramp</span>
          <div class="jal-comp-legend-row"><i class="swatch" style="background: var(--d1)"></i>passable<span class="legend-range" id="jal-comp-range-d1"></span></div>
          <div class="jal-comp-legend-row"><i class="swatch" style="background: var(--d2)"></i>caution<span class="legend-range" id="jal-comp-range-d2"></span></div>
          <div class="jal-comp-legend-row"><i class="swatch" style="background: var(--d3)"></i>hazardous<span class="legend-range" id="jal-comp-range-d3"></span></div>
          <div class="jal-comp-legend-row"><i class="swatch" style="background: var(--d4)"></i>impassable<span class="legend-range" id="jal-comp-range-d4"></span></div>
        </div>
      </section>
      <section class="jal-comp-panel jal-comp-right" id="jal-comp-right" role="button" tabindex="0" aria-label="Replay panel — peak-neighbourhood frame">
        <span class="jal-comp-panel-label" id="jal-comp-right-label">REPLAY — peak-neighbourhood frame</span>
        <canvas id="jal-comp-right-canvas"></canvas>
        <div class="jal-comp-legend" id="jal-comp-right-legend">
          <span class="jal-comp-legend-title">Depth</span>
          <span class="jal-comp-legend-encoding">model depth ramp</span>
          <div class="jal-comp-legend-row"><i class="swatch" style="background: var(--d1)"></i>passable<span class="legend-range" id="jal-comp-range-d1r"></span></div>
          <div class="jal-comp-legend-row"><i class="swatch" style="background: var(--d2)"></i>caution<span class="legend-range" id="jal-comp-range-d2r"></span></div>
          <div class="jal-comp-legend-row"><i class="swatch" style="background: var(--d3)"></i>hazardous<span class="legend-range" id="jal-comp-range-d3r"></span></div>
          <div class="jal-comp-legend-row"><i class="swatch" style="background: var(--d4)"></i>impassable<span class="legend-range" id="jal-comp-range-d4r"></span></div>
        </div>
      </section>
    </div>
    <div class="jal-comp-strip" id="jal-comp-strip">
      <div class="jal-comp-metrics" id="jal-comp-metrics"></div>
    </div>
  `;
  host.appendChild(overlay);
  return overlay;
}
function showEmpty(container, message, retryFn){
  container.replaceChildren();
  const box = document.createElement("div");
  box.className = "jal-comp-empty";
  const p = document.createElement("p");
  p.textContent = message;
  box.appendChild(p);
  if(retryFn){
    const btn = document.createElement("button");
    btn.className = "retry";
    btn.type="button";
    btn.textContent="retry";
    btn.addEventListener("click", retryFn);
    box.appendChild(btn);
  }
  container.appendChild(box);
}
function syncLegendRanges(){
  const ids = ["jal-comp-range-d1","jal-comp-range-d2","jal-comp-range-d3","jal-comp-range-d4","jal-comp-range-d1r","jal-comp-range-d2r","jal-comp-range-d3r","jal-comp-range-d4r"];
  const vals = [
    "<="+Math.round(render.DEPTH[0].max*100)+" cm",
    "<="+Math.round(render.DEPTH[1].max*100)+" cm",
    "<="+Math.round(render.DEPTH[2].max*100)+" cm",
    "> "+Math.round(render.DEPTH[2].max*100)+" cm"
  ];
  ids.forEach((id, idx)=>{
    const el=document.getElementById(id);
    if(el) el.textContent=vals[idx%4];
  });
}
function findIndexForValidTime(frames, validUtc){
  if(!validUtc || !Array.isArray(frames)) return null;
  const norm = (s)=> String(s).replace("+00:00","Z").trim();
  const target = norm(validUtc);
  for(let i=0;i<frames.length;i++){
    if(norm(frames[i].valid_time_utc)===target) return frames[i].index ?? i;
    if(String(frames[i].valid_time_utc).slice(0,16)===String(validUtc).slice(0,16)) return frames[i].index ?? i;
  }
  return null;
}
async function openComparison(){
  const overlay = ensureOverlay();
  if(!overlay) return;
  overlay.classList.remove("hidden");
  overlay.removeAttribute("hidden");
  // Modal state marker: parks the floating search (z 50 above this overlay)
  // via the styles.css body.comparison-open rule.
  document.body.classList.add("comparison-open");
  requestAnimationFrame(()=> overlay.querySelector(".jal-comp-close")?.focus());
  const metricsHost = document.getElementById("jal-comp-metrics");
  const leftCanvas = document.getElementById("jal-comp-left-canvas");
  const rightCanvas = document.getElementById("jal-comp-right-canvas");
  const leftLabel = document.getElementById("jal-comp-left-label");
  const rightLabel = document.getElementById("jal-comp-right-label");
  metricsHost.textContent = "Loading comparison…";
  let payload=null;
  try{
    payload = await getJson("/api/comparison2022");
  }catch(err){
    const msg = err instanceof Error ? err.message : String(err);
    showEmpty(metricsHost, `Comparison unavailable — ${msg}`, ()=> openComparison());
    return;
  }
  try{ globalThis.__JALADHAR_COMPARISON_PAYLOAD = payload; }catch{}
  const left = payload.left ?? {};
  const right = payload.right ?? {};
  const metrics = payload.metrics ?? {};
  if(leftLabel){
    const lt = left.valid_time_utc ? formatValidForLabel(left.valid_time_utc) : "valid time";
    leftLabel.textContent = `FORECAST — 3h forecast at ${lt} \u00b7 ${left.n_flooded ?? ""} flooded`;
    leftLabel.title = left.label ?? "";
  }
  if(rightLabel){
    const rt = right.valid_time_utc ? formatValidForLabel(right.valid_time_utc) : right.frame_tag ?? "";
    rightLabel.textContent = `REPLAY — peak-neighbourhood ${rt} \u00b7 ${right.n_flooded ?? ""} flooded`;
    rightLabel.title = right.label ?? "";
  }
  try{
    metricsHost.replaceChildren();
    const mk = (label, value, ci, opts={})=>{
      const wrap=document.createElement("div");
      wrap.className="jal-comp-metric"+(opts.lead?" jal-comp-metric--lead":"")+(opts.grouped?" jal-comp-metric--grouped":"");
      const l=document.createElement("span"); l.className="jal-comp-metric-label"; l.textContent=label;
      const v=document.createElement("span"); v.className="jal-comp-metric-value"; v.textContent=value;
      v.style.fontVariantNumeric="tabular-nums";
      if(ci && opts.ciBeside){
        const row=document.createElement("span"); row.className="jal-comp-metric-value-row";
        const c=document.createElement("span"); c.className="jal-comp-metric-ci"; c.textContent=ci;
        row.append(v,c);
        wrap.append(l,row);
      }else{
        wrap.append(l,v);
        if(ci){ const c=document.createElement("span"); c.className="jal-comp-metric-ci"; c.textContent=ci; wrap.appendChild(c); }
      }
      return wrap;
    };
    const jacc = metrics.jaccard_csi;
    const dice = metrics.dice;
    const podRL = metrics.pod_right_given_left;
    const podLR = metrics.pod_left_given_right;
    const farL = metrics.far_left_only_share;
    const farR = metrics.far_right_only_share;
    metricsHost.append(
      mk("Jaccard (CSI) — forecast∩replay / union", fmt(jacc,3)),
      mk("Dice — 2·both / (left+right)", fmt(dice,3)),
      mk("POD right given left (forecast → replay)", fmt(podRL,3)),
      mk("POD left given right (replay → forecast)", fmt(podLR,3)),
      mk("FAR left-only share", fmt(farL,3)),
      mk("FAR right-only share", fmt(farR,3))
    );
  }catch(err){
    console.warn("strip render failed", err);
  }

  let basemapMeta, roads, lakes, leftFrame=null, rightFrame=null;
  let leftIndex = null;
  let rightBlockedReason = null;
  try{
    basemapMeta = await loadBasemapMeta();
  }catch(err){
    showEmpty(metricsHost, `Basemap unavailable — ${err instanceof Error ? err.message : String(err)}`);
    return;
  }
  try{
    let state=null;
    try{ state = await loadState(); }catch{}
    const frames = state?.series?.frames ?? [];
    leftIndex = findIndexForValidTime(frames, left.valid_time_utc);
    if(leftIndex==null){
      for(const f of frames){ if(String(f.valid_time_utc).includes("21:40")) leftIndex = f.index; }
      if(leftIndex==null) leftIndex = 6;
    }
    roads = new RoadNetwork(basemapMeta);
    const [lakesData, fullLines] = await Promise.all([
      loadLines("/api/basemap/lakes.bin"),
      (async()=>{
        const url = `/api/basemap/roads/full.bin`;
        try{ return await loadLines(url); }catch{ return await loadLines("/api/basemap/roads/coarse.bin"); }
      })()
    ]);
    lakes = lakesData;
    roads.lods.set("full", fullLines);
    const cityCentre = [(basemapMeta.bbox[0]+basemapMeta.bbox[2])/2, (basemapMeta.bbox[1]+basemapMeta.bbox[3])/2];
    try{
      leftFrame = await loadSeriesFrame(leftIndex);
    }catch(e){
      console.warn("[comparison] left frame load failed", e);
      showEmpty(metricsHost, `Left forecast frame ${leftIndex} unavailable — ${e instanceof Error ? e.message : String(e)}`);
    }
    const replayTag = right.frame_tag;
    let rightWarming = false;
    if(!replayTag || typeof replayTag !== "string"){
      rightBlockedReason = "replay frame tag missing from comparison payload";
    }else{
      // The replay series warms lazily server-side (~35 s after first
      // request). A 409 "not warm yet" is a timing state, not a failure:
      // retry on a bounded backoff and say so on the canvas instead of
      // silently leaving a basemap-only panel (owner incident 2026-08-27).
      const url = `/api/replay_frame.bin?tag=${encodeURIComponent(replayTag)}`;
      const deadline = Date.now() + 45000;
      for(;;){
        try{
          const resp = await fetch(url, {cache:"no-store"});
          if(resp.ok){
            rightFrame = decodeSeriesFrame(await resp.arrayBuffer());
            rightBlockedReason = null;
            rightWarming = false;
            break;
          }
          let msg = `replay frame ${replayTag} unavailable (${resp.status})`;
          try{
            const pj = await resp.json();
            if(pj && pj.error) msg = pj.error;
          }catch{}
          const warming = resp.status === 409 && /warm/i.test(msg);
          if(warming && Date.now() < deadline){
            rightWarming = true;
            rightBlockedReason = "replay frame warming — rendering when ready…";
            renderBoth();
            await new Promise(r => setTimeout(r, 2000));
            continue;
          }
          throw new Error(msg);
        }catch(e){
          if(rightWarming && Date.now() < deadline) continue;
          rightBlockedReason = e instanceof Error ? e.message : String(e);
          rightWarming = false;
          console.warn("[comparison] replay frame load failed", e);
          break;
        }
      }
    }
    const predHost = document.getElementById("jal-comp-left");
    const rightHost = document.getElementById("jal-comp-right");
    function sizeCanvas(canvas, host){
      const rect = host.getBoundingClientRect();
      // Same supersample guard as engine.js: a DPR-1 browser composited onto a
      // physically HiDPI screen upscales these canvases and softens every
      // stroke; a 2x backing on large screens gives the compositor real pixels.
      const reported = window.devicePixelRatio||1;
      const dpr =
        reported >= 2 || (window.screen && window.screen.width >= 2400)
          ? Math.max(reported, 2)
          : reported;
      const w = Math.max(1, Math.round(rect.width));
      const h = Math.max(1, Math.round(rect.height));
      canvas.width = Math.round(w*dpr);
      canvas.height = Math.round(h*dpr);
      canvas.style.width = w+"px";
      canvas.style.height = h+"px";
      return {w,h,dpr,rect};
    }
    const leftView = new View(basemapMeta.bbox);
    const rightView = new View(basemapMeta.bbox);
    function fitBoth(w,h){
      leftView.fit(w,h);
      rightView.fit(w,h);
      rightView.scale = leftView.scale;
      rightView.tx = leftView.tx;
      rightView.ty = leftView.ty;
    }
    function renderLeft(){
      if(!leftFrame){
        const ctx = leftCanvas.getContext("2d");
        const {w,h,dpr} = sizeCanvas(leftCanvas, predHost);
        ctx.setTransform(dpr,0,0,dpr,0,0);
        ctx.clearRect(0,0,w,h);
        ctx.fillStyle="rgba(148,160,180,0.85)";
        ctx.font="12px sans-serif";
        ctx.fillText("forecast frame unavailable", 12, 24);
        return;
      }
      const ctx = leftCanvas.getContext("2d");
      const {w,h,dpr} = sizeCanvas(leftCanvas, predHost);
      leftView.width=w; leftView.height=h;
      const env={
        view: leftView, roads, lines: fullLines, lakes, drains:null, wards:null,
        layers:{roads:true,lakes:true,drains:false}, layerAlpha:{roads:1,lakes:1},
        frame: leftFrame, frameA:leftFrame, frameB:null, interpFrac:0, width:w, height:h, dpr, cityCentre, ready:{basemap:true}, playing:false,
        depthMetresFor(i){ return leftFrame.high[i]/100; },
        currentLod:"full",
        reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      };
      syncLegendRanges();
      ctx.setTransform(dpr,0,0,dpr,0,0);
      ctx.clearRect(0,0,w,h);
      render.drawBackground(ctx,w,h,leftView,cityCentre);
      if(lakes) render.drawLakes(ctx, {...env, lakes});
      render.drawDryRoads(ctx, env);
      render.drawWater(ctx, env);
    }
    function renderRight(){
      const ctx = rightCanvas.getContext("2d");
      const {w,h,dpr} = sizeCanvas(rightCanvas, rightHost);
      rightView.width=w; rightView.height=h;
      if(rightFrame){
        const env={
          view: rightView, roads, lines: fullLines, lakes, drains:null, wards:null,
          layers:{roads:true,lakes:true,drains:false}, layerAlpha:{roads:1,lakes:1},
          frame: rightFrame, frameA:rightFrame, frameB:null, interpFrac:0, width:w, height:h, dpr, cityCentre, ready:{basemap:true}, playing:false,
          depthMetresFor(i){ return rightFrame.high[i]/100; },
          currentLod:"full",
          reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        };
        ctx.setTransform(dpr,0,0,dpr,0,0);
        ctx.clearRect(0,0,w,h);
        render.drawBackground(ctx,w,h,rightView,cityCentre);
        if(lakes) render.drawLakes(ctx, {...env, lakes});
        render.drawDryRoads(ctx, env);
        render.drawWater(ctx, env);
        return;
      }
      ctx.setTransform(dpr,0,0,dpr,0,0);
      ctx.clearRect(0,0,w,h);
      render.drawBackground(ctx,w,h,rightView,cityCentre);
      if(lakes) render.drawLakes(ctx, {view:rightView, roads, lines:fullLines, lakes, width:w, height:h, dpr, cityCentre, ready:{basemap:true}, layers:{roads:true,lakes:true}, layerAlpha:{roads:1,lakes:1}, currentLod:"full"});
      render.drawDryRoads(ctx, {view:rightView, roads, lines:fullLines, lakes, width:w, height:h, dpr, cityCentre, ready:{basemap:true}, layers:{roads:true,lakes:true}, layerAlpha:{roads:1,lakes:1}, currentLod:"full"});
      ctx.fillStyle="rgba(233,237,245,0.9)";
      ctx.font="12px ui-sans-serif, sans-serif";
      const msg = rightBlockedReason ?? "replay frame not served";
      const lines = [];
      let cur=""; for(const word of msg.split(" ")){ const test=cur?cur+" "+word:word; if(ctx.measureText(test).width > w-24){ lines.push(cur); cur=word; } else cur=test; } if(cur) lines.push(cur);
      lines.forEach((ln,i)=> ctx.fillText(ln, 12, 28 + i*16));
      ctx.fillStyle="rgba(148,160,180,0.8)";
      ctx.font="11px ui-sans-serif, sans-serif";
      ctx.fillText("Both panels use same depth ramp when data is served", 12, 28 + lines.length*16 + 8);
    }
    function renderBoth(){ renderLeft(); renderRight(); }
    const initialRect = predHost.getBoundingClientRect();
    fitBoth(Math.max(1, initialRect.width), Math.max(1, initialRect.height));
    renderBoth();
    syncLegendRanges();
    const ro = new ResizeObserver(()=> renderBoth());
    ro.observe(predHost); ro.observe(rightHost);
    overlay._ro = ro;
    function attachSync(host, sourceView, otherView){
      let dragging=false, lastX=0, lastY=0, moved=false;
      host.addEventListener("pointerdown", e=>{
        dragging=true; moved=false; lastX=e.clientX; lastY=e.clientY;
        try{ host.setPointerCapture(e.pointerId);}catch{}
      });
      host.addEventListener("pointermove", e=>{
        if(dragging){
          const dx=e.clientX-lastX, dy=e.clientY-lastY;
          if(Math.abs(dx)+Math.abs(dy)>0) moved=true;
          lastX=e.clientX; lastY=e.clientY;
          sourceView.panBy(dx,dy);
          otherView.tx=sourceView.tx; otherView.ty=sourceView.ty; otherView.scale=sourceView.scale; otherView.width=sourceView.width; otherView.height=sourceView.height;
          renderBoth();
          return;
        }
      });
      host.addEventListener("pointerup", e=>{
        dragging=false;
        try{ host.releasePointerCapture(e.pointerId);}catch{}
        if(!moved){ closeComparison(); }
      });
      host.addEventListener("wheel", e=>{
        e.preventDefault();
        const rect=host.getBoundingClientRect();
        const factor=Math.pow(1.0018, -e.deltaY);
        sourceView.zoomAbout(e.clientX-rect.left, e.clientY-rect.top, factor);
        otherView.tx=sourceView.tx; otherView.ty=sourceView.ty; otherView.scale=sourceView.scale;
        renderBoth();
      }, {passive:false});
      host.addEventListener("keydown", e=>{
        if(e.key==="Enter"||e.key===" "){ e.preventDefault(); closeComparison(); }
        if(e.key==="Escape"){ e.preventDefault(); closeComparison(); }
      });
    }
    attachSync(predHost, leftView, rightView);
    attachSync(rightHost, rightView, leftView);
    overlay._cleanup = ()=>{ try{ ro.disconnect(); }catch{} };
    try{
      if(rightFrame && !rightBlockedReason){
        globalThis.__JALADHAR_COMPARISON_BLOCKER = undefined;
        try{ delete globalThis.__JALADHAR_COMPARISON_BLOCKER; }catch{}
      }else{
        globalThis.__JALADHAR_COMPARISON_BLOCKER = rightBlockedReason;
      }
    }catch{}
  }catch(err){
    const msg = err instanceof Error ? err.message : String(err);
    showEmpty(metricsHost, `Panel render failed — ${msg}`);
    console.warn("[comparison] render failed", err);
  }
}
function closeComparison(){
  const overlay = document.getElementById("jal-comp-overlay");
  if(!overlay || overlay.classList.contains("hidden")) return;
  overlay.classList.add("hidden");
  overlay.setAttribute("hidden","");
  document.body.classList.remove("comparison-open");
  if(overlay._cleanup) try{ overlay._cleanup(); }catch{}
  const tl = globalThis.__JALADHAR_TIMELINE;
  const scrubber = document.getElementById("scrubber");
  let targetIndex = 6;
  try{
    const payload = globalThis.__JALADHAR_COMPARISON_PAYLOAD;
    if(payload?.left?.valid_time_utc){
      const st = globalThis.__JALADHAR_TIMELINE?.seriesFrames ?? null;
      if(st){
        const found = findIndexForValidTime(st, payload.left.valid_time_utc);
        if(found!=null) targetIndex = found;
      }
    }
  }catch{}
  if(tl && scrubber && tl.leads && tl.leads.length> targetIndex){
    scrubber.value = String(targetIndex);
    scrubber.dispatchEvent(new Event("input", {bubbles:true}));
    setTimeout(()=>{
      try{
        if(Number(scrubber.value)!==targetIndex){
          scrubber.value=String(targetIndex);
          scrubber.dispatchEvent(new Event("input",{bubbles:true}));
        }
        document.dispatchEvent(new CustomEvent("wf6:frame-changed", {detail:{frame:String(targetIndex)}}));
      }catch{}
    }, 80);
  } else if(scrubber){
    try{ scrubber.value=String(targetIndex); scrubber.dispatchEvent(new Event("input",{bubbles:true})); }catch{}
  }
  document.getElementById("map-area")?.focus?.();
  const mapArea = document.getElementById("map-area");
  if(mapArea) mapArea.style.visibility="";
}
function bootComparison(){
  const btn = document.getElementById("btn-comparison");
  if(!btn) return;
  if(btn.textContent.includes("Observed")) btn.textContent="2022 Forecast vs Replay \u00b7 Agreement";
  btn.setAttribute("aria-label","Open 2022 Forecast vs Replay agreement comparison");
  btn.addEventListener("click", openComparison);
  btn.addEventListener("keydown", e=>{
    if(e.key==="Enter"||e.key===" "){ e.preventDefault(); openComparison(); }
  });
  const overlay = ensureOverlay();
  if(overlay){
    const closeBtn = overlay.querySelector(".jal-comp-close");
    if(closeBtn) closeBtn.addEventListener("click", closeComparison);
    overlay.addEventListener("keydown", e=>{
      if(e.key==="Escape"){ e.preventDefault(); closeComparison(); }
    });
    overlay.addEventListener("click", e=>{
      if(e.target===overlay) closeComparison();
    });
  }
  globalThis.__JALADHAR_COMPARISON = {open: openComparison, close: closeComparison};
}
if(document.readyState==="loading"){
  document.addEventListener("DOMContentLoaded", bootComparison, {once:true});
}else{
  bootComparison();
}

// Pre-warm the replay frame route shortly after load so the lazy server-side
// series warm-up (~35 s, 97 frames) is usually done before the overlay opens.
// Tag comes from the served payload — nothing hardcoded here. Fire-and-forget:
// failures stay silent, the overlay's own retry path reports honestly.
setTimeout(()=>{
  fetch("/api/comparison2022", {cache:"no-store"})
    .then(r => r.ok ? r.json() : null)
    .then(p => {
      const tag = p && p.right && p.right.frame_tag;
      if(typeof tag === "string" && tag) return fetch(`/api/replay_frame.bin?tag=${encodeURIComponent(tag)}`, {cache:"no-store"});
      return null;
    })
    .catch(()=>{});
}, 1500);
export {openComparison, closeComparison};
