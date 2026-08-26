// Canvas stack per guide section 3's static/dynamic split, plus a UI
// overlay: STATIC (background, wards, lakes, dry roads, drains) redraws only
// on view/layer change; DYNAMIC (water glow, pulses) redraws on frame/view
// change; UI (selection, hover, labels) redraws on interaction — so hovering
// a segment never rebuilds the water layer.
// This module also self-installs the map controls (N6: zoom, reset, north)
// and the ward context toggle (N2) — both are created at runtime because
// index.html is owned by another lane.
import * as render from "./render.js";

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");

const easeInOut = (t) => t * t * (3 - 2 * t);
const FADE_MS = 420; // layer toggle cross-fade (guide section 4)
const ZOOM_BUTTON_FACTOR = 1.5;

export class Engine {
  constructor(staticCanvas, dynCanvas, uiCanvas) {
    this.staticCanvas = staticCanvas;
    this.dynCanvas = dynCanvas;
    this.uiCanvas = uiCanvas;
    this.staticCtx = staticCanvas.getContext("2d");
    this.dynCtx = dynCanvas.getContext("2d");
    this.uiCtx = uiCanvas.getContext("2d");
    this.dpr = 1;
    this.width = 0;
    this.height = 0;
    this.env = null; // supplied by app.js
    this.staticDirty = true;
    this.dynamicDirty = true;
    this.uiDirty = true;
    this.anims = new Set(); // active animation handles
    this._raf = 0;
    this._loop = this._loop.bind(this);
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(staticCanvas.parentElement);
    this.resize();
    installMapControls(this);
    installWardsToggle(this);
  }

  resize() {
    const host = this.staticCanvas.parentElement;
    const cssW = Math.max(1, host.clientWidth);
    const cssH = Math.max(1, host.clientHeight);
    // devicePixelRatio scaling done correctly (guide section 7 item 2).
    this.dpr = window.devicePixelRatio || 1;
    for (const canvas of [this.staticCanvas, this.dynCanvas, this.uiCanvas]) {
      canvas.width = Math.round(cssW * this.dpr);
      canvas.height = Math.round(cssH * this.dpr);
      canvas.style.width = cssW + "px";
      canvas.style.height = cssH + "px";
    }
    this.width = cssW;
    this.height = cssH;
    if (this.env) {
      this.env.dpr = this.dpr;
      this.env.width = cssW;
      this.env.height = cssH;
    }
    this.invalidateStatic();
    this.invalidateDynamic();
  }

  invalidateStatic() {
    this.staticDirty = true;
    this.ensureLoop();
  }

  invalidateDynamic() {
    this.dynamicDirty = true;
    this.ensureLoop();
  }

  invalidateUI() {
    this.uiDirty = true;
    this.ensureLoop();
  }

  animate(handle) {
    this.anims.add(handle);
    this.invalidateDynamic();
  }

  // Called (debounced by the app) after pan/zoom so the app can swap the
  // level of detail. Kept as a callback because the LOD policy lives there.
  _notifyViewChanged() {
    this.onViewChanged?.();
  }

  stopAnimate(handle) {
    this.anims.delete(handle);
    if (!this.hasContinuousAnim()) this.invalidateDynamic();
  }

  hasContinuousAnim() {
    return [...this.anims].some((a) => a.continuous);
  }

  reducedMotion() {
    return REDUCED_MOTION.matches;
  }

  ensureLoop() {
    if (!this._raf) this._raf = requestAnimationFrame(this._loop);
  }

  _loop() {
    this._raf = 0;
    let needMore = false;

    // Advance registered animations first.
    const now = performance.now();
    for (const anim of this.anims) {
      if (anim.tick(now)) needMore = true;
    }
    for (const anim of [...this.anims]) {
      if (anim.done) this.anims.delete(anim);
    }
    if (this.hasContinuousAnim()) needMore = true;

    // env.view is null between construction and boot's first await — skip
    // drawing rather than throwing out of the loop (a throw here kills the
    // rAF chain until the next invalidate).
    if (this.staticDirty && this.env && this.env.view) {
      this._drawStatic();
      this.staticDirty = false;
      // Static layer feeds the dynamic one (drain fade alpha etc).
      this.dynamicDirty = true;
      this.uiDirty = true;
    }
    if ((this.dynamicDirty || needMore) && this.env && this.env.view) {
      this._drawDynamic(now);
      this.dynamicDirty = false;
      this.uiDirty = true;
    }
    if (this.uiDirty && this.env && this.env.view) {
      this._drawUI();
      this.uiDirty = false;
    }
    if (needMore || this.staticDirty || this.dynamicDirty || this.uiDirty) this.ensureLoop();
  }

  _setup(ctx) {
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
  }

  _drawStatic() {
    const env = this.env;
    const ctx = this.staticCtx;
    this._setup(ctx);
    render.drawBackground(ctx, this.width, this.height, env.view, env.cityCentre);
    if (env.ready.basemap) {
      render.drawWards(ctx, env); // beneath lakes/roads; self-guards layer+alpha
      if (env.layers.lakes) render.drawLakes(ctx, env);
      if (env.layers.roads) render.drawDryRoads(ctx, env);
      if (env.layers.drains) render.drawDrains(ctx, env);
    }
  }

  _drawDynamic(now) {
    const env = this.env;
    const ctx = this.dynCtx;
    this._setup(ctx);
    if (!env.ready.basemap) return;
    render.drawWater(ctx, env);
    render.drawPulses(ctx, env);
  }

  _drawUI() {
    const env = this.env;
    const ctx = this.uiCtx;
    this._setup(ctx);
    if (!env.ready.basemap) return;
    render.drawSelection(ctx, env);
    render.drawLabels(ctx, env);
  }

  attachPointerHandlers({ onPick, onHover }) {
    const host = this.staticCanvas.parentElement;
    let dragging = false;
    let moved = false;
    let lastX = 0;
    let lastY = 0;

    host.addEventListener("pointerdown", (e) => {
      dragging = true;
      moved = false;
      lastX = e.clientX;
      lastY = e.clientY;
      try {
        host.setPointerCapture(e.pointerId);
      } catch {
        // Synthetic or already-released pointers carry no active capture id.
      }
    });
    host.addEventListener("pointermove", (e) => {
      if (dragging) {
        const dx = e.clientX - lastX;
        const dy = e.clientY - lastY;
        if (Math.abs(dx) + Math.abs(dy) > 0) moved = true;
        lastX = e.clientX;
        lastY = e.clientY;
        this.env.view.panBy(dx, dy);
        this.invalidateStatic();
        this._notifyViewChanged();
        return;
      }
      if (onHover) onHover(e.offsetX, e.offsetY);
    });
    host.addEventListener("pointerup", (e) => {
      dragging = false;
      try {
        host.releasePointerCapture(e.pointerId);
      } catch {
        // See pointerdown — synthetic pointers carry no capture id.
      }
      if (!moved && onPick) onPick(e.offsetX, e.offsetY);
    });
    // Wheel zoom about the cursor — passive false so we can preventDefault.
    host.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        const rect = host.getBoundingClientRect();
        const factor = Math.pow(1.0018, -e.deltaY);
        this.env.view.zoomAbout(e.clientX - rect.left, e.clientY - rect.top, factor);
        this.invalidateStatic();
        this._notifyViewChanged();
      },
      { passive: false }
    );
  }
}

// ---- map controls (N6) ---------------------------------------------------
// Zoom +/-, fit-to-extent/reset and a north indicator, created at runtime
// into the map area (index.html is another lane's file). Styling rides in a
// scoped injected <style> using only the guide palette variables —
// styles.css is never touched.

const WF6_CONTROL_CSS = `
.wf6ctl-cluster{position:absolute;top:12px;right:12px;display:flex;flex-direction:column;align-items:center;gap:6px;z-index:6;}
.wf6ctl-btn{width:34px;height:34px;border-radius:6px;border:1px solid var(--line);background:rgba(16,21,31,0.92);color:var(--ink-dim);font-size:15px;line-height:1;cursor:pointer;padding:0;transition:background var(--fast) var(--ease),border-color var(--fast) var(--ease),color var(--fast) var(--ease);}
.wf6ctl-btn:hover:not(:disabled){background:var(--panel-hi);border-color:var(--ink-faint);color:var(--ink);}
.wf6ctl-north{display:flex;flex-direction:column;align-items:center;gap:1px;padding:5px 9px;border:1px solid var(--line);border-radius:6px;background:rgba(16,21,31,0.92);color:var(--ink-dim);font-size:10px;font-weight:600;letter-spacing:0.14em;user-select:none;}
.wf6ctl-arrow{font-size:12px;font-weight:400;}
`;

function afterViewChange(engine) {
  engine.invalidateStatic();
  engine.invalidateDynamic();
  engine.invalidateUI();
  engine._notifyViewChanged(); // LOD policy re-evaluates after reset/zoom
}

function nudgeZoom(engine, factor) {
  const env = engine.env;
  if (!env?.view || !env.ready?.basemap) return;
  env.view.zoomAbout(env.width / 2, env.height / 2, factor);
  afterViewChange(engine);
}

function installMapControls(engine) {
  const host = engine.staticCanvas.parentElement;
  if (!host || host.querySelector(":scope > .wf6ctl-cluster")) return;
  const style = document.createElement("style");
  style.textContent = WF6_CONTROL_CSS;
  document.head.appendChild(style);

  const cluster = document.createElement("div");
  cluster.className = "wf6ctl-cluster";

  const north = document.createElement("div");
  north.className = "wf6ctl-north";
  north.setAttribute("aria-hidden", "true");
  const nLabel = document.createElement("span");
  nLabel.textContent = "N";
  const nArrow = document.createElement("span");
  nArrow.className = "wf6ctl-arrow";
  nArrow.textContent = "↑";
  north.append(nLabel, nArrow);

  const zoomIn = document.createElement("button");
  zoomIn.id = "wf6ctl-zoom-in";
  zoomIn.className = "wf6ctl-btn";
  zoomIn.textContent = "+";
  zoomIn.setAttribute("aria-label", "Zoom in");
  zoomIn.addEventListener("click", () => nudgeZoom(engine, ZOOM_BUTTON_FACTOR));

  const zoomOut = document.createElement("button");
  zoomOut.id = "wf6ctl-zoom-out";
  zoomOut.className = "wf6ctl-btn";
  zoomOut.textContent = "−";
  zoomOut.setAttribute("aria-label", "Zoom out");
  zoomOut.addEventListener("click", () => nudgeZoom(engine, 1 / ZOOM_BUTTON_FACTOR));

  const reset = document.createElement("button");
  reset.id = "wf6ctl-reset";
  reset.className = "wf6ctl-btn";
  reset.textContent = "⤢";
  reset.title = "Reset view to full city extent";
  reset.setAttribute("aria-label", "Reset view");
  reset.addEventListener("click", () => {
    const view = engine.env?.view;
    if (!view) return;
    view.reset(); // recovery control (audit B3): re-run the boot fit
    afterViewChange(engine);
  });

  cluster.append(north, zoomIn, zoomOut, reset);
  // Control clicks must never reach the map's pan/pick/wheel handlers.
  cluster.addEventListener("pointerdown", (e) => e.stopPropagation());
  cluster.addEventListener("pointermove", (e) => e.stopPropagation());
  cluster.addEventListener("pointerup", (e) => e.stopPropagation());
  cluster.addEventListener("wheel", (e) => e.stopPropagation());
  host.appendChild(cluster);
}

// ---- ward context layer (N2) ---------------------------------------------
// /api/context/wards_<vintage>.bin + .meta.json, lazily fetched on first
// enable. The V8 seam is asserted here against the REALIZED producer schema
// (src/jaladhar/web/wards.py): the blob is the basemap-lake layout
// (n_rings:u32 | offsets:u32[n+1] | interleaved f32 xy), and the sidecar
// carries n_rings/n_vertices plus per-ward name/centroid/ring_ranges. Note
// rings >= wards by construction (holes and multipolygons add rings), so the
// assertion binds wards[].length <= n_rings rather than equality with it.
// Any mismatch refuses to attach the layer — the toggle reports why.

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} failed (${response.status})`);
  return response.json();
}

// Exported for the seam verification harness (V8): the ward consumer's load
// assertions must be exercisable against the producer's realized bytes.
export function assertWardsBundle(buffer, meta) {
  if (!(buffer.byteLength > 4)) throw new Error("ward blob truncated (no header)");
  const nRings = new DataView(buffer, 0, 4).getUint32(0, true);
  let offsets;
  let coords;
  try {
    offsets = new Uint32Array(buffer, 4, nRings + 1);
    coords = new Float32Array(buffer, 4 + (nRings + 1) * 4);
  } catch (err) {
    if (err instanceof RangeError) {
      throw new Error("ward blob length disagrees with its ring count");
    }
    throw err;
  }
  if (offsets[nRings] !== coords.length / 2) {
    throw new Error(
      `ward blob offsets end at ${offsets[nRings]} but bytes realise ${coords.length / 2} vertices`
    );
  }
  if (!meta || typeof meta !== "object") throw new Error("ward meta sidecar missing");
  if (meta.n_rings !== nRings) {
    throw new Error(`ward meta declares ${meta.n_rings} rings, blob realises ${nRings}`);
  }
  const vertexCount = coords.length / 2;
  if (!(meta.n_vertices > 0) || vertexCount !== meta.n_vertices) {
    throw new Error(
      `ward meta declares ${meta.n_vertices} vertices, blob realises ${vertexCount}`
    );
  }
  if (!Array.isArray(meta.wards) || meta.wards.length < 1) {
    throw new Error("ward meta carries no ward entries");
  }
  if (meta.n_rings < meta.wards.length) {
    throw new Error(
      `ward meta has fewer rings (${meta.n_rings}) than wards (${meta.wards.length})`
    );
  }
  for (const ward of meta.wards) {
    if (typeof ward.name !== "string" || !ward.name.trim()) {
      throw new Error(`ward ${ward.ward_no} has no name`);
    }
    if (
      !Array.isArray(ward.ring_ranges) ||
      ward.ring_ranges.length < 1 ||
      !Array.isArray(ward.centroid_m) ||
      ward.centroid_m.length !== 2 ||
      !ward.centroid_m.every(Number.isFinite)
    ) {
      throw new Error(`ward ${ward.ward_no} (${ward.name}) lacks ranges or centroid`);
    }
  }
  return { n: nRings, offsets, coords, meta };
}

function ensureWards(engine) {
  if (!engine._wardsLoad) {
    engine._wardsLoad = (async () => {
      const vintage = String(globalThis.__CONTEXT_VINTAGE ?? "2022").replace(/[^0-9]/g, "") || "2022";
      const binUrl = `/api/context/wards_${vintage}.bin`;
      const [meta, buffer] = await Promise.all([
        fetchJson(`/api/context/wards_${vintage}.meta.json`),
        fetch(binUrl, { cache: "no-store" }).then((r) => {
          if (!r.ok) throw new Error(`${binUrl} failed (${r.status})`);
          return r.arrayBuffer();
        }),
      ]);
      return assertWardsBundle(buffer, meta);
    })();
  }
  return engine._wardsLoad;
}

function fadeWards(engine, on) {
  const env = engine.env;
  if (!env) return;
  // Env field shims: app.js owns the env object literal, so defaults for
  // fields that postdate it are attached defensively here.
  if (!env.layers) env.layers = {};
  if (!env.layerAlpha) env.layerAlpha = {};
  if (env.layerAlpha.wards === undefined) env.layerAlpha.wards = env.layers.wards ? 1 : 0;
  if (on) env.layers.wards = true;
  const from = env.layerAlpha.wards ?? 0;
  if (engine._wardsFade) engine._wardsFade.done = true; // one active fade, ever
  const start = performance.now();
  const anim = {
    done: false,
    continuous: false,
    tick(now) {
      const t = Math.min(1, (now - start) / FADE_MS);
      env.layerAlpha.wards = from + ((on ? 1 : 0) - from) * (engine.reducedMotion() ? 1 : easeInOut(t));
      engine.invalidateStatic();
      engine.invalidateUI(); // ward labels ride the UI canvas
      if (t >= 1) {
        anim.done = true;
        if (!on) env.layers.wards = false;
      }
      return !anim.done;
    },
  };
  engine._wardsFade = anim;
  engine.animate(anim);
  engine.invalidateStatic();
}

function installWardsToggle(engine) {
  const layersDiv = document.querySelector(".bottombar .layers");
  if (!layersDiv || layersDiv.querySelector("#toggle-wards")) return;

  // Markup mirrors #toggle-lakes exactly; styles.css classes apply unchanged.
  const label = document.createElement("label");
  label.id = "toggle-wards";
  label.className = "layer-toggle";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.dataset.layer = "wards";
  const glyph = document.createElement("span");
  glyph.className = "glyph";
  glyph.textContent = "⬚";
  const text = document.createTextNode("wards");
  label.append(input, glyph, text);
  layersDiv.appendChild(label);

  input.addEventListener("change", () => {
    label.classList.toggle("on", input.checked);
    if (input.checked) {
      ensureWards(engine)
        .then((wards) => {
          // Race guard (verify-wave B3): the user may have unchecked while
          // the first lazy fetch was in flight — honour the CURRENT toggle,
          // never fade a layer back in behind an off checkbox.
          if (!input.checked || !label.classList.contains("on")) return;
          const env = engine.env;
          if (!env) return; // boot has not attached the render environment yet
          env.wards = wards;
          fadeWards(engine, true);
        })
        .catch((err) => {
          // Honest unavailability: endpoint not served yet or seam violation.
          input.disabled = true;
          input.checked = false;
          label.classList.remove("on");
          const reason = err instanceof Error ? err.message : String(err);
          label.title = "ward context unavailable: " + reason;
          console.warn("[wf6] ward layer disabled:", reason);
        });
    } else {
      fadeWards(engine, false);
    }
  });
}
