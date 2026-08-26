// Rainfall-intensity sparkline behind/above the timeline scrubber (WF-6 N3).
//
// Data contract: GET /api/context/forcing_series.json ->
//   { interval_minutes: 30, n_intervals: 96, areal_mean_mm_hr: [96 numbers] }
// Frame<->interval alignment: interval i covers frame i's leading 30 minutes;
// the last frame is the end state at the right edge. The playhead maps the
// scrubber value linearly onto the full plot width, and seeking maps a click
// back through the identical mapping, so marker and seek always agree
// (sub-bar skew <= half an interval width is inherent to overlaying
// instantaneous frames on interval bars).
//
// Rules 1 and 2 bind: if the endpoint is absent, malformed, or fails its V8
// contract assertion, this module renders NOTHING — a DOM placeholder whose
// title attribute says "no forcing series" and nothing else. On a validated
// load the tooltip instead describes the realized series (intervals, cadence,
// sha prefix). No synthetic curve, ever.
//
// Decoupled by design: this module observes the #scrubber element directly
// (input event + a rAF poll that also catches programmatic value writes made
// during playback, which fire no input event). It imports nothing from the
// timeline and receives nothing but the scrubber element.

const SRC_URL = "/api/context/forcing_series.json";
const N_INTERVALS = 96;
const HEIGHT = 34; // px
const PAD_TOP = 11; // label strip above the bars
const PAD_BOTTOM = 3;

const NS = "http://www.w3.org/2000/svg";

function palette() {
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    bar: v("--d1", "#22D3EE"),
    line: v("--line", "#1E2634"),
    faint: v("--ink-faint", "#5A6579"),
    accent: v("--accent", "#38BDF8"),
  };
}

export async function mountHyetograph({ scrubber }) {
  if (!scrubber) throw new Error("hyetograph: scrubber element required");
  if (document.querySelector(".wf6tl-hy-wrap")) return null; // idempotent mount

  let ro = null;
  let rafId = 0;
  function makeDispose(extra) {
    let done = false;
    return () => {
      if (done) return;
      done = true;
      cancelAnimationFrame(rafId);
      ro?.disconnect();
      window.removeEventListener("resize", layout);
      extra();
    };
  }

  const wrap = document.createElement("div");
  wrap.className = "wf6tl-hy-wrap";
  wrap.title = "no forcing series";
  // Designed absence until data proves otherwise: present in the DOM so the
  // reason is inspectable, visually empty, never interactive.
  Object.assign(wrap.style, {
    position: "fixed",
    zIndex: "35",
    display: "none",
    pointerEvents: "none",
    cursor: "ew-resize",
  });
  document.body.appendChild(wrap);

  let series = null;
  try {
    const res = await fetch(SRC_URL, { cache: "no-store" });
    if (!res.ok) {
      console.info(
        `hyetograph: ${SRC_URL} -> ${res.status}; designed absence (no curve invented)`
      );
    } else {
      const payload = await res.json();
      // V8 seam assertion against the producer's documented guarantees.
      const values = payload?.areal_mean_mm_hr;
      const contractOk =
        payload?.n_intervals === N_INTERVALS &&
        Array.isArray(values) &&
        values.length === N_INTERVALS &&
        values.every((x) => typeof x === "number" && Number.isFinite(x));
      if (!contractOk) {
        console.warn(
          `hyetograph: forcing series contract violated ` +
          `(n_intervals=${JSON.stringify(payload?.n_intervals)}, ` +
          `areal_mean_mm_hr len=${Array.isArray(values) ? values.length : "not-an-array"})` +
          " — refusing to draw"
        );
      } else {
        series = payload;
        // Success path: the tooltip must describe the REALIZED series (V-trace
        // finding — it used to keep the absence wording forever).
        const sha = typeof payload.sha256 === "string" ? payload.sha256.slice(0, 12) : "";
        wrap.title =
          `Forcing series · ${payload.n_intervals} × ${payload.interval_minutes ?? 30} min ` +
          `areal-mean intensity` + (sha ? ` · sha ${sha}…` : "");
      }
    }
  } catch (err) {
    console.warn("hyetograph: forcing series fetch failed:", err?.message ?? err);
  }

  if (!series) return makeDispose(() => wrap.remove());

  // ------------------------------------------------------------ drawing

  const ink = palette();
  let plotW = 0;
  let svg = null;
  let playhead = null;

  function buildSvg(width) {
    plotW = width;
    const el = document.createElementNS(NS, "svg");
    el.classList.add("wf6tl-hyetograph");
    el.setAttribute("width", String(width));
    el.setAttribute("height", String(HEIGHT));
    el.setAttribute("viewBox", `0 0 ${width} ${HEIGHT}`);
    el.setAttribute("role", "img");
    el.setAttribute(
      "aria-label",
      "Areal mean rainfall intensity, 30-minute intervals, millimetres per hour"
    );

    const values = series.areal_mean_mm_hr;
    let maxV = 0;
    for (const x of values) if (x > maxV) maxV = x;
    const baseY = HEIGHT - PAD_BOTTOM;
    const plotH = baseY - PAD_TOP;
    const bw = width / N_INTERVALS;

    for (let i = 0; i < N_INTERVALS; i++) {
      const v = values[i];
      if (v <= 0) continue; // no rain, no bar — zero is drawn as zero
      const h = Math.max(1, (v / maxV) * plotH);
      const rect = document.createElementNS(NS, "rect");
      rect.setAttribute("x", (i * bw).toFixed(2));
      rect.setAttribute("y", (baseY - h).toFixed(2));
      rect.setAttribute("width", Math.max(1, bw - 1).toFixed(2));
      rect.setAttribute("height", h.toFixed(2));
      rect.setAttribute("fill", ink.bar);
      rect.setAttribute("fill-opacity", "0.55");
      el.appendChild(rect);
    }

    const baseline = document.createElementNS(NS, "line");
    baseline.setAttribute("x1", "0");
    baseline.setAttribute("x2", String(width));
    baseline.setAttribute("y1", String(baseY + 0.5));
    baseline.setAttribute("y2", String(baseY + 0.5));
    baseline.setAttribute("stroke", ink.line);
    el.appendChild(baseline);

    const unit = document.createElementNS(NS, "text");
    unit.setAttribute("x", String(width));
    unit.setAttribute("y", "8");
    unit.setAttribute("text-anchor", "end");
    unit.setAttribute("font-size", "9");
    unit.setAttribute("fill", ink.faint);
    unit.textContent = "mm/hr";
    el.appendChild(unit);

    playhead = document.createElementNS(NS, "g");
    const phLine = document.createElementNS(NS, "line");
    phLine.setAttribute("y1", String(PAD_TOP - 2));
    phLine.setAttribute("y2", String(baseY));
    phLine.setAttribute("stroke", ink.accent);
    phLine.setAttribute("stroke-width", "1");
    playhead.appendChild(phLine);
    playhead.style.display = "none";
    el.appendChild(playhead);

    return el;
  }

  function layout() {
    const r = scrubber.getBoundingClientRect();
    if (r.width < 40) return;
    const footer = scrubber.closest("footer") ?? scrubber.closest(".bottombar");
    const fr = footer?.getBoundingClientRect();
    // Sit just above the scrubber track; clamp into the bottombar band so the
    // sparkline never floats over the map or the legend.
    let top = r.top - HEIGHT - 3;
    if (fr && Number.isFinite(fr.top) && top < fr.top + 2) top = fr.top + 2;
    wrap.style.left = `${Math.round(r.left)}px`;
    wrap.style.top = `${Math.round(top)}px`;
    wrap.style.width = `${Math.round(r.width)}px`;
    wrap.style.height = `${HEIGHT}px`;

    const w = Math.round(r.width);
    if (!svg || plotW !== w) {
      svg?.remove();
      svg = buildSvg(w);
      wrap.appendChild(svg);
      lastSeen = null; // force playhead re-place after rebuild
    }
  }

  // ------------------------------------------------- scrubber observation

  let lastSeen = null;
  function tick() {
    const min = parseFloat(scrubber.min) || 0;
    const max = parseFloat(scrubber.max);
    if (Number.isFinite(max) && max > min && svg) {
      const v = parseFloat(scrubber.value);
      if (v !== lastSeen) {
        lastSeen = v;
        const x = ((v - min) / (max - min)) * plotW;
        playhead.setAttribute("transform", `translate(${x.toFixed(2)} 0)`);
        playhead.style.display = "";
      }
    }
    rafId = requestAnimationFrame(tick);
  }

  // Click/drag on the sparkline seeks the scrubber through the same linear
  // mapping; the dispatched input event drives the Timeline exactly like a
  // native scrub (which also stops playback, by its own rule).
  let dragging = false;
  function seekFromEvent(e) {
    const r = wrap.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - r.left) / Math.max(1, r.width)));
    const min = parseFloat(scrubber.min) || 0;
    const max = parseFloat(scrubber.max);
    if (!(max > min)) return;
    scrubber.value = String(min + frac * (max - min));
    scrubber.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function onDown(e) {
    dragging = true;
    try {
      wrap.setPointerCapture(e.pointerId);
    } catch {
      // capture unsupported — drag still works while the pointer stays inside
    }
    seekFromEvent(e);
  }
  function onMove(e) {
    if (dragging) seekFromEvent(e);
  }
  function onUp() {
    dragging = false;
  }

  wrap.addEventListener("pointerdown", onDown);
  wrap.addEventListener("pointermove", onMove);
  wrap.addEventListener("pointerup", onUp);
  wrap.addEventListener("pointercancel", onUp);
  window.addEventListener("resize", layout);
  if (typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver(layout);
    ro.observe(scrubber);
  }

  Object.assign(wrap.style, { display: "block", pointerEvents: "auto" });
  layout();
  tick();

  return makeDispose(() => wrap.remove());
}
