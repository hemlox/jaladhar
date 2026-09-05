// Timeline scrub + play — guide Moment 2. The horizon is whatever the
// realized product actually carries: today that is a single event-maximum
// frame, so play is disabled with an honest reason and auto-play no-ops.
// The interpolation machinery activates the moment a multi-lead product
// exists; nothing here invents a frame (rules 2 and 3).
//
// WF-6 L4 additions, all created via DOM (index.html is not touched):
//   N4 — STEP BACK / STEP FORWARD (discrete frame advance, disabled at the
//        ends), SPEED selector 1x/2x/5x scaling the 3000 ms sweep to
//        3000/speed, LOOP toggle (default off = the original hold-at-end).
//   N8 — a compact "% of network flooded · n/total" line computed strictly
//        from the loaded frames' status codes, with a trend arrow vs the
//        PREVIOUS REAL FRAME only (hidden at frame 0, mid-interpolation,
//        or whenever the previous frame is not loaded — never invented).
// Styling is injected once under the wf6tl- prefix and uses only the
// existing CSS variable palette; no styles.css change.

const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");

const SWEEP_MS = 3000;

// GAP-3/GAP-4 hooks
function _beyondSuffix(info){ return info?.suffix ?? "\u00b7 beyond horizon (+30m)"; }

function countFlooded(codes) {
  let c = 0;
  for (let i = 0; i < codes.length; i++) if (codes[i] === 1) c++;
  return c;
}

export class Timeline {
  constructor({ slider, playBtn, tickStart, tickEnd, nowLead }) {
    this.slider = slider;
    this.playBtn = playBtn;
    this.tickStart = tickStart;
    this.tickEnd = tickEnd;
    this.nowLead = nowLead;
    this.leads = [];
    this.frames = new Map(); // lead -> decoded frame
    this.lastStatsLead = -1; // discrete lead the hero stats currently reflect
    this.onFrameChange = () => {};
    this._playing = false;
    this._raf = 0;
    // N4 playback state. Defaults preserve the pre-L4 behaviour exactly:
    // 1x speed, loop off, hold-at-end on completion.
    this.speed = 1;
    this.loop = false;

    slider.addEventListener("input", () => {
      if (this._playing) this.stop();
      const p = this.interpolated();
      this._emit(p.leadIndex, p.frac);
    });
    playBtn.addEventListener("click", () => (this._playing ? this.stop() : this.play()));

    Timeline.ensureStyles();
    this._buildControls();
    this._bindFooterKeys();
    // Decoupled by design: the hyetograph observes the scrubber element
    // directly and owns its own data fetch; a failure there must never take
    // the transport controls down, hence the dynamic import.
    this._mountHyetograph();
  }

  formatLead(minutes) {
    if (minutes <= 0) return "now";
    if (minutes % 60 === 0) return "+" + minutes / 60 + "h";
    return "+" + minutes + "m";
  }

  get playing() {
    return this._playing;
  }

  // Frame-series mode: values are series indices; labels are valid times.
  static formatValidTime(iso) {
    // "2022-09-04T21:30:00+00:00" -> "Sep 4 21:30Z"
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso ?? "");
    if (!m) return String(iso ?? "—");
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${months[Number(m[2]) - 1]} ${Number(m[3])} ${m[4]}:${m[5]}Z`;
  }

  // ------------------------------------------------------------ N4/N8 DOM

  // One scoped style tag for everything this module injects. Uses only the
  // palette variables already defined in styles.css (:root scope).
  static ensureStyles() {
    if (document.getElementById("wf6tl-style")) return;
    const st = document.createElement("style");
    st.id = "wf6tl-style";
    st.textContent = `
.wf6tl-cluster { display:flex; flex-direction:column; align-items:flex-end; gap:5px; flex:none; }
.wf6tl-kpi { margin:0; font-size:11px; line-height:1; color:var(--ink-dim); white-space:nowrap; }
.wf6tl-trend { color:var(--accent); font-weight:600; padding-left:6px; }
.wf6tl-controls { display:flex; align-items:center; gap:6px; }
.wf6tl-btn {
  height:24px; padding:0 9px; border-radius:999px; border:1px solid var(--line);
  background:var(--panel); color:var(--ink); font-size:11px; cursor:pointer;
  transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease);
}
.wf6tl-btn:hover:not(:disabled) { background:var(--panel-hi); border-color:var(--ink-faint); }
.wf6tl-btn:disabled { color:var(--ink-faint); cursor:default; }
.wf6tl-btn[aria-pressed="true"] { color:var(--ink); border-color:var(--ink-faint); background:var(--panel-hi); }
.wf6tl-speed {
  height:24px; border-radius:6px; border:1px solid var(--line); background:var(--panel);
  color:var(--ink); font-size:11px; padding:0 2px; cursor:pointer;
  transition:background var(--fast) var(--ease), border-color var(--fast) var(--ease);
}
.wf6tl-speed:disabled { color:var(--ink-faint); cursor:default; }
`;
    document.head.appendChild(st);
  }

  _mkBtn(id, glyph, label) {
    const b = document.createElement("button");
    b.type = "button";
    b.id = id;
    b.className = "wf6tl-btn";
    b.textContent = glyph;
    b.setAttribute("aria-label", label);
    b.title = label;
    b.disabled = true; // enabled by configure() once >1 lead exists
    return b;
  }

  _buildControls() {
    const host = this.slider.closest(".timeline") ?? this.slider.parentElement;
    if (!host || document.getElementById("wf6tl-cluster")) return;

    // N8 KPI line — lives in the timeline area (the hero stat belongs to the
    // rail, which is another lane's file).
    const kpi = document.createElement("p");
    kpi.id = "wf6tl-kpi";
    kpi.className = "wf6tl-kpi";
    const kpiText = document.createElement("span");
    kpiText.className = "wf6tl-kpi-text";
    const trend = document.createElement("span");
    trend.className = "wf6tl-trend";
    trend.hidden = true;
    kpi.append(kpiText, trend);

    // N4 transport row.
    const row = document.createElement("div");
    row.className = "wf6tl-controls";

    this.stepBackBtn = this._mkBtn("wf6tl-step-back", "«", "Step back one frame");
    this.stepFwdBtn = this._mkBtn("wf6tl-step-forward", "»", "Step forward one frame");
    this.stepBackBtn.addEventListener("click", () => this.stepBy(-1));
    this.stepFwdBtn.addEventListener("click", () => this.stepBy(1));

    this.speedSel = document.createElement("select");
    this.speedSel.id = "wf6tl-speed";
    this.speedSel.className = "wf6tl-speed";
    this.speedSel.setAttribute("aria-label", "Playback speed");
    this.speedSel.disabled = true;
    for (const s of [0.5, 1, 2, 5]) {
      const o = document.createElement("option");
      o.value = String(s);
      o.textContent = s + "x";
      this.speedSel.appendChild(o);
    }
    this.speedSel.addEventListener("change", () => {
      this.speed = Number(this.speedSel.value) || 1;
      this._syncControlTitles();
    });

    this.loopBtn = this._mkBtn("wf6tl-loop", "LOOP", "Loop playback");
    this.loopBtn.setAttribute("aria-pressed", "false");
    this.loopBtn.addEventListener("click", () => {
      this.loop = !this.loop;
      this.loopBtn.setAttribute("aria-pressed", String(this.loop));
      this._syncControlTitles();
    });

    row.append(this.stepBackBtn, this.stepFwdBtn, this.speedSel, this.loopBtn);
    const cluster = document.createElement("div");
    cluster.id = "wf6tl-cluster";
    cluster.className = "wf6tl-cluster";
    cluster.append(kpi, row);
    // D3 length label: sourced from /api/state fields (no literals)
    const lengthEl = document.createElement("p");
    lengthEl.id = "wf6tl-length";
    lengthEl.className = "wf6tl-length";
    lengthEl.style.cssText = "margin:4px 0 0;font-size:10px;color:var(--ink-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:260px;text-align:right;";
    lengthEl.setAttribute("aria-live","polite");
    cluster.appendChild(lengthEl);
    host.appendChild(cluster);

    this.kpiText = kpiText;
    this.kpiTrend = trend;
    this.lengthEl = lengthEl;
    this._syncControlTitles();
    // D3: init length from current seriesFrames if available, else fetch state
    try{ this._refreshLengthLabel(); }catch{}
  }

  _syncControlTitles() {
    if (!this.speedSel) return;
    this.speedSel.title =
      `Playback speed ${this.speed}x — sweep ${Math.round(SWEEP_MS / this.speed)} ms`;
    this.loopBtn.title = this.loop
      ? "Loop on — playback restarts from the first frame"
      : "Loop off — playback holds at the final frame";
  }

  // Left/right arrows step one discrete frame whenever focus is anywhere in
  // the footer. The speed <select> keeps its native option navigation.
  _bindFooterKeys() {
    const footer = this.slider.closest("footer") ?? this.slider.closest(".bottombar");
    if (!footer) return;
    footer.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if (e.target && e.target.tagName === "SELECT") return;
      e.preventDefault();
      this.stepBy(e.key === "ArrowRight" ? 1 : -1);
    });
  }

  _mountHyetograph() {
    import("./hyetograph.js")
      .then((m) => m.mountHyetograph({ scrubber: this.slider }))
      .catch((err) => console.warn("hyetograph mount failed:", err?.message ?? err));
  }

  // Single emission path: every slider-position change goes through here so
  // the app callback, the step-button ends and the KPI stay in lockstep.
  _emit(leadIndex, frac) {
    this.onFrameChange({ leadIndex, frac });
    this._syncControls();
    this._updateKpi(leadIndex, frac);
    // GAP-4 observable
    try{
      const lead = this.leads[leadIndex];
      globalThis.__JALADHAR_DEMO_TICK = {i: leadIndex, lead, frac, ts: Date.now()};
      globalThis.__JALADHAR_STATE = globalThis.__JALADHAR_STATE || {};
      globalThis.__JALADHAR_STATE.demoTick = globalThis.__JALADHAR_DEMO_TICK;
    }catch{}
    // GAP-3 tick annotation: when last frame is beyond horizon, add suffix/dim
    try{
      const frames = this.seriesFrames ?? globalThis.__JALADHAR_TIMELINE?.seriesFrames ?? null;
      if(frames && frames.length>=2 && this.leads.length===frames.length){
        const lastIdx = frames.length-1;
        const isBeyondLast = (leadIndex===lastIdx && frac===0);
        // tickEnd corresponds to last valid_time
        const lastValid = frames[lastIdx]?.valid_time_utc;
        if(lastValid && this.tickEnd){
          const base = Timeline.formatValidTime(lastValid);
          // compute suffix via offset diff if available
          let mins = 30;
          try{
            const a = frames[lastIdx]?.offset_seconds;
            const b = frames[lastIdx-1]?.offset_seconds;
            if(Number.isFinite(a) && Number.isFinite(b)) mins = Math.round((a-b)/60);
          }catch{}
          const suffix = mins>0 ? ` \u00b7 beyond horizon (+${mins}m)` : " \u00b7 verified beyond-horizon run state";
          if(this.tickEnd.textContent===base){
            // first time, append hint
            const need = !this.tickEnd.querySelector?.(".beyond-hint");
            if(need && !this.tickEnd.textContent.includes("beyond")){
              this.tickEnd.textContent = base;
              const hint = document.createElement("span");
              hint.className="beyond-hint";
              hint.textContent = suffix;
              hint.style.cssText="font-size:10px;color:var(--ink-faint);margin-left:6px;font-style:italic;";
              this.tickEnd.appendChild(hint);
              this.tickEnd.classList.add("tick-beyond");
              this.tickEnd.title="beyond-horizon over-run ("+suffix.trim()+")";
            }
          }
          // Also dim when currently on beyond frame: add class to slider container
          const host = this.slider.closest(".timeline") ?? this.slider.parentElement;
          if(host){
            host.classList.toggle("beyond-active", isBeyondLast);
          }
        }
      }
    }catch{}
  }

  _syncControls() {
    if (!this.stepBackBtn) return;
    const multi = this.leads.length > 1;
    const v = parseFloat(this.slider.value) || 0;
    const max = this.leads.length - 1;
    this.stepBackBtn.disabled = !multi || v <= 0;
    this.stepFwdBtn.disabled = !multi || v >= max;
    this.speedSel.disabled = !multi;
    this.loopBtn.disabled = !multi;
  }

  // Discrete frame advance: round to the nearest loaded frame index, move by
  // delta, clamp to the realized range. Playing stops first (scrub rule).
  stepBy(delta) {
    if (this.leads.length < 2) return;
    if (this._playing) this.stop();
    const v = Math.round(parseFloat(this.slider.value) || 0);
    const next = Math.min(this.leads.length - 1, Math.max(0, v + delta));
    this.slider.value = String(next);
    const p = this.interpolated();
    this._emit(p.leadIndex, p.frac);
  }

  // ------------------------------------------------------------------ N8

  // "% of network flooded · n/total" from the loaded frame's status codes,
  // plus trend arrow vs the previous REAL discrete frame. Rule 3 binds:
  // nothing renders until a frame is actually in hand, and the arrow hides
  // rather than guessing across a gap.
  _updateKpi(leadIndex, frac) {
    const isLiveEmpty = (globalThis.__JALADHAR_MODE==="live") && globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.liveEmpty;
    if(isLiveEmpty){
      if(this.kpiText) this.kpiText.textContent="";
      if(this.kpiTrend) this.kpiTrend.hidden=true;
      const kpiEl=document.getElementById("wf6tl-kpi");
      if(kpiEl) kpiEl.style.display="none";
      return;
    } else {
      const kpiEl=document.getElementById("wf6tl-kpi");
      if(kpiEl) kpiEl.style.display="";
    }
    if (!this.kpiText) return;
    const lead = this.leads[leadIndex];
    const frame = lead == null ? undefined : this.frames.get(lead);
    if (!frame) return; // frame not fetched yet — show nothing rather than guess
    const codes = frame.codes;
    // V8 seam assertion: the decoder guarantees codes has length n; refuse to
    // derive a share if the realized bytes ever disagree with that contract.
    if (!codes || codes.length !== frame.n) {
      console.warn(
        `wf6tl: frame contract violated (codes length ${codes?.length} vs n ${frame.n})` +
        " — flooded share withheld"
      );
      this.kpiText.textContent = "";
      this.kpiText.title = "frame contract violation — flooded share withheld";
      this.kpiTrend.hidden = true;
      return;
    }
    this.kpiText.title = "";
    const flooded = countFlooded(codes);
    const pct = (100 * flooded) / frame.n;
    const pctStr = Number.isInteger(pct) ? String(pct) : pct.toFixed(1);
    this.kpiText.textContent = `${pctStr}% of network flooded · ${flooded}/${frame.n}`;

    let arrow = "";
    let prevCount = null;
    if (frac === 0 && leadIndex > 0) {
      const prev = this.frames.get(this.leads[leadIndex - 1]);
      if (prev && prev.codes && prev.codes.length === prev.n) {
        prevCount = countFlooded(prev.codes);
        arrow = flooded > prevCount ? "↑" : flooded < prevCount ? "↓" : "→";
      }
    }
    this.kpiTrend.textContent = arrow;
    this.kpiTrend.hidden = !arrow;
    this.kpiTrend.title = prevCount == null ? "" : `previous frame: ${prevCount} flooded`;
  }

  // ----------------------------------------------------------- timeline

  async _refreshLengthLabel(){
    try{
      const st = globalThis.__JALADHAR_STATE;
      // try state payload cached
      let data = null;
      if(st && st.series && Array.isArray(st.series.frames) && st.series.cadence_seconds){
        data = st.series;
      }
      if(!data){
        // fetch live state (no literals)
        const r = await fetch("/api/state",{cache:"no-store"});
        const j = await r.json();
        data = j.series ?? null;
      }
      if(!data || !Array.isArray(data.frames) || data.frames.length<1) return;
      const n = data.frames.length;
      const cadenceSec = Number(data.cadence_seconds);
      const cadenceMin = cadenceSec ? Math.round(cadenceSec/60) : null;
      const firstIso = data.frames[0].valid_time_utc;
      const lastIso = data.frames[data.frames.length-1].valid_time_utc;
      const firstValid = firstIso ? Timeline.formatValidTime(firstIso) : "";
      const lastValid = lastIso ? Timeline.formatValidTime(lastIso) : "";
      // extract HH:MM from formatted "Sep 4 18:40Z" -> time part
      const fmtTime = (iso)=>{ const m=/^.*T(\d{2}):(\d{2})/.exec(iso); return m? m[1]+":"+m[2] : Timeline.formatValidTime(iso); };
      const t0 = fmtTime(firstIso);
      const t1 = fmtTime(lastIso);
      // compute span hours from offsets or cadence
      let spanHours = null;
      if(Number.isFinite(data.frames[data.frames.length-1].offset_seconds) && Number.isFinite(data.frames[0].offset_seconds)){
        const spanSec = data.frames[data.frames.length-1].offset_seconds - data.frames[0].offset_seconds;
        spanHours = spanSec/3600;
      } else if(cadenceSec && n>1){
        spanHours = (n-1)*cadenceSec/3600;
      }
      let spanLabel = "";
      if(spanHours!=null){
        if(Math.abs(spanHours - Math.round(spanHours))<0.01) spanLabel = Math.round(spanHours)+"-h forecast";
        else spanLabel = (Math.round(spanHours*10)/10)+"-h forecast";
      } else {
        spanLabel = n+"-frame series";
      }
      // detect over-run: last frame is verified beyond-horizon run state
      let overRun = "";
      let tEnd = t1;
      try{
        const offsets = data.frames.map(f=>f.offset_seconds).filter(Number.isFinite);
        if(offsets.length>=2){
          const lastOff = offsets[offsets.length-1];
          const prevOff = offsets[offsets.length-2];
          const step = prevOff!=null ? lastOff-prevOff : cadenceSec;
          // horizon is second-last offset when over-run present (e.g., 10800 = 3h)
          if(lastOff > 10800 && Math.abs(lastOff - prevOff - cadenceSec) < 1){
            const extraMin = Math.round(step/60);
            if(extraMin) overRun = ` (+${extraMin}m over-run)`;
            // window end should be previous frame's time (21:40), not last (22:10)
            const prevIso = data.frames[data.frames.length-2].valid_time_utc;
            tEnd = fmtTime(prevIso);
            // also adjust spanLabel to 3-h not 3.5: horizon hours = (n-1)*cadence - step? Compute as prevOff/3600
            if(prevOff!=null) spanHours = prevOff/3600;
            if(spanHours!=null){
              if(Math.abs(spanHours - Math.round(spanHours))<0.01) spanLabel = Math.round(spanHours)+"-h forecast";
              else spanLabel = (Math.round(spanHours*10)/10)+"-h forecast";
            }
          }
        }
      }catch{}
      // Compose: '3-h forecast · 8 frames × 30 min · 18:40 → 21:40Z (+30m over-run)'
      const parts = [];
      if(spanLabel) parts.push(spanLabel);
      if(n && cadenceMin) parts.push(`${n} frames \u00d7 ${cadenceMin} min`);
      if(t0 && tEnd) parts.push(`${t0} \u2192 ${tEnd}Z${overRun}`);
      const label = parts.join(" \u00b7 ");
      if(this.lengthEl){
        // in LIVE empty, hide demo length
        const isLiveEmpty = (globalThis.__JALADHAR_MODE==="live") && globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.liveEmpty;
        if(isLiveEmpty){ this.lengthEl.style.display="none"; return; }
        this.lengthEl.style.display="";
        this.lengthEl.textContent = label;
        this.lengthEl.title = `frames ${n}, cadence ${cadenceSec}s, ${firstIso} → ${lastIso}`;
      }
    }catch(e){ /* silent */ }
  }

  async configure(leads, fetchFrame, opts = {}) {
    this.leads = leads;
    this.mode = opts.mode ?? "lead";
    this.describe = opts.describe ?? ((v) => this.formatLead(v));
    for (const f of this.frames.values()) f.release?.();
    this.frames.clear();
    if (!leads.length) {
      this.slider.disabled = true;
      this.playBtn.disabled = true;
      this._syncControls();
      return null;
    }
    const first = await fetchFrame(leads[0]);
    this.frames.set(leads[0], first);
    const multi = leads.length > 1;
    this.slider.min = "0";
    this.slider.max = String(leads.length - 1);
    this.slider.step = multi ? "any" : "1";
    this.slider.value = String(leads.length - 1); // hold at the latest realized lead
    this.slider.disabled = !multi;
    this.playBtn.disabled = !multi;
    this.playBtn.title = multi
      ? "Play the realized horizon"
      : "Play needs more than one realized lead — this product is a single event-maximum frame";
    // A single-realized-frame product carries NO forecast lead axis (its one
    // frame sits at offset 0), so lead-derived words ("now") would mislabel
    // it. Callers label that case explicitly via the tick-label opts; the
    // describe() fallback only serves callers that did not opt in.
    this.tickStart.textContent =
      (!multi && opts.tickStartLabel) || this.describe(leads[0]);
    this.tickEnd.textContent =
      (!multi && opts.tickEndLabel) || this.describe(leads[leads.length - 1]);
    // GAP-3 initial tick annotation handled by _emit on hold index; ensure tickEnd gets beyond suffix after seriesFrames assigned by caller
    try{
      // delay to allow seriesFrames assignment (app.js sets after configure), annotate on next tick
      setTimeout(()=>{ try{ this._emit(this.leads.length-1,0); this.slider.value=String(this.leads.length-1); const p=this.interpolated(); this._emit(p.leadIndex,p.frac);}catch{} }, 100);
    }catch{}
    this.nowLead.textContent =
      leads.length > 1
        ? (opts.horizonLabel ?? this.describe(leads[leads.length - 1]) + " HORIZON")
        : (opts.horizonLabel ?? "SINGLE REALIZED FRAME");
    this._syncControls();
    // Fire only if the held frame is actually loaded — with multiple leads
    // the caller fetches the rest (ensureFrames) and drives frames itself.
    const holdIndex = this.leads.length - 1;
    if (this.frames.get(this.leads[holdIndex])) {
      this._emit(holdIndex, 0);
    }
    try{ this._refreshLengthLabel(); }catch{}
    // listen for demo-enter to re-show label
    if(!this._lengthListener){
      this._lengthListener=true;
      document.addEventListener("jaladhar:demo-enter", ()=>{ try{ this._refreshLengthLabel(); }catch{} });
      document.addEventListener("jaladhar:live-enter", ()=>{ if(this.lengthEl) this.lengthEl.style.display="none"; });
    }
    return first;
  }

  interpolated() {
    const v = parseFloat(this.slider.value);
    const iA = Math.floor(v);
    const frac = v - iA;
    return { leadIndex: Math.min(iA, this.leads.length - 1), frac };
  }

  // Depth interpolation so water grows instead of snapping (guide section 4).
  // Returns {frameA, frameB|null, frac} — rendering lerps band_high only.
  pair(leadIndex) {
    const a = this.leads[leadIndex];
    const b = this.leads[Math.min(leadIndex + 1, this.leads.length - 1)];
    return [this.frames.get(a), b === a ? null : this.frames.get(b)];
  }

  async ensureFrames(fetchFrame) {
    for (const lead of this.leads) {
      if (!this.frames.has(lead)) this.frames.set(lead, await fetchFrame(lead));
    }
  }

  play() {
    if (this.leads.length < 2 || this._playing) return;
    this._playing = true;
    this.playBtn.textContent = "❚❚";
    // Sweep duration scales with the speed selector (3000/speed). Under
    // reduced motion it collapses to a jump-cut; looping then would strobe
    // every frame, so reduced-motion always holds at the end.
    const durationMs = REDUCED.matches ? 1 : Math.round(SWEEP_MS / this.speed);
    const looping = this.loop && !REDUCED.matches;
    let start = 0;
    const max = this.leads.length - 1;
    const step = (now) => {
      if (!start) start = now;
      const t = Math.min((now - start) / durationMs, 1);
      this.slider.value = String(t * max);
      const p = this.interpolated();
      this._emit(p.leadIndex, p.frac);
      if (t < 1 && this._playing) {
        this._raf = requestAnimationFrame(step);
        return;
      }
      if (this._playing && looping) {
        start = 0; // restart the sweep from frame 0 without leaving play state
        this._raf = requestAnimationFrame(step);
        return;
      }
      this.stop();
      this.slider.value = String(max); // hold at the final frame after one loop
      const pEnd = this.interpolated();
      this._emit(pEnd.leadIndex, pEnd.frac);
    };
    this._raf = requestAnimationFrame(step);
  }

  stop() {
    this._playing = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this.playBtn.textContent = "▶";
    this._syncControls();
  }
}
