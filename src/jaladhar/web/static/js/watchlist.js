// WF-6 L8 — "STREETS AT RISK" watchlist panel: the primary operator surface
// (A1 ranked street list, A2 depth bands, A3 lead-kind honesty, A8
// intersections). The map becomes this panel's context.
//
// Rules that bind this module:
//   1/2 — rows come only from GET /api/watchlist and GET /api/intersections;
//         any failure renders a designed state. A fabricated row is worse
//         than an empty panel, so there is no fallback synthesis anywhere.
//   3   — every displayed number is read from the payload in hand; the only
//         literals here are presentation constants (colours, cap sizes).
//
// Mounting: index.html/styles.css/app.js are owned by other lanes, so this
// module creates its own mount point — an absolutely-positioned 320 px
// overlay inserted as the first child of .app (before #map-area), collapsible,
// styled by a stylesheet injected once under the wf6-* namespace.
//
// V8 integration seam (documented for the integrator):
//   emits on document:
//     wf6:select-street       {segment_id, street_name, world_hint}
//     wf6:select-intersection {node_id, world_xy}
//     wf6:watchlist-toggle    {open}
//   listens for:
//     wf6:frame-changed       {frame} — refetches for that frame tag/index;
//     until the integrator forwards frame changes the panel pins to
//     frame=held, which is the run's held frame and nothing more.

import { Timeline } from "./timeline.js";

const PANEL_ID = "watchlist";
const PANEL_WIDTH = 320;
const ROW_RENDER_CAP = 200;
const DEFAULT_FRAME = "held";

// ---------------------------------------------------------------- utilities

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function emit(type, detail) {
  document.dispatchEvent(new CustomEvent(type, { detail }));
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

// Console.warn exactly once per endpoint — repeated failures are the same
// fact, and an operator console flooded with duplicates hides real signals.
const warnedEndpoints = new Set();
function warnOnce(key, ...args) {
  if (warnedEndpoints.has(key)) return;
  warnedEndpoints.add(key);
  console.warn("[wf6]", ...args);
}

// Fetch through window.__wf6_FETCH_OVERRIDE when a test harness defines one
// (see WF-6 L8 verification plan), else the network. Never fabricates: a
// non-ok response becomes a thrown Error carrying the server's own message.
async function fetchJson(url) {
  let response;
  try {
    const override = globalThis.__wf6_FETCH_OVERRIDE;
    response =
      typeof override === "function"
        ? await override(url)
        : await fetch(url, { cache: "no-store" });
  } catch (err) {
    throw new Error(`${url}: ${err instanceof Error ? err.message : String(err)}`);
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // non-JSON body — fall through to the generic status message
  }
  if (!response.ok) {
    throw new Error(
      (payload && (payload.error || payload.message)) ||
        `${url} failed (${response.status})`
    );
  }
  return payload;
}

// ------------------------------------------------------- payload formatting

// N9: depths above 100 cm are metres at 2 dp; below stay integer cm.
export function formatDepthBand(band) {
  if (!band || !isFiniteNumber(+band.low) || !isFiniteNumber(+band.high)) {
    return "—";
  }
  const low = +band.low;
  const high = +band.high;
  if (high > 100) {
    return `${(low / 100).toFixed(2)}–${(high / 100).toFixed(2)} m`;
  }
  return `${Math.round(low)}–${Math.round(high)} cm`;
}

function wardLabel(row) {
  if (isFiniteNumber(row.ward_number)) return `Ward ${row.ward_number}`;
  if (typeof row.ward_name === "string" && row.ward_name) return row.ward_name;
  return null;
}

function dotClass(status) {
  return status === "flooded" || status === "not_flooded" ? status : "unknown";
}

// A3 lead-kind honesty: the status line names what a "lead" actually is.
// Hindcast offsets are where water was relative to a past instant — they are
// not forecast leads and must never read as one.
export function statusLineText(source, rows) {
  const kind = source && source.lead_kind;
  if (kind === "hindcast_offset") return "hindcast offsets — not forecast leads";
  if (kind === "forecast_lead") {
    const withLead = rows.find((row) => isFiniteNumber(row.lead_minutes));
    return isFiniteNumber(withLead?.lead_minutes)
      ? `leads from issued run +${withLead.lead_minutes} min`
      : "leads from issued run";
  }
  return kind ? `lead kind ${String(kind)}` : "run state absent from payload";
}

// ------------------------------------------------------------------ styles

const PANEL_CSS = `
.wf6-watchlist{
  position:absolute; top:var(--header-h,56px); left:0; bottom:var(--bottom-h,96px);
  width:${PANEL_WIDTH}px; z-index:30;
  display:flex; flex-direction:column;
  background:rgba(10,14,23,0.97);
  border-right:1px solid var(--line);
  color:var(--ink); font-size:13px; overflow:hidden;
}
.wf6-watchlist *{ box-sizing:border-box; }

.wf6-titlebar{ display:flex; align-items:center; gap:8px; padding:9px 12px; border-bottom:1px solid var(--line); }
.wf6-collapse{
  width:22px; height:22px; flex:none; border:1px solid var(--line); border-radius:6px;
  background:transparent; color:var(--ink-dim); cursor:pointer; font-size:13px; line-height:1;
  padding:0;
}
.wf6-collapse:hover{ background:var(--panel-hi); color:var(--ink); }
.wf6-heading{
  margin:0; flex:1; font-size:11px; font-weight:600; letter-spacing:0.14em;
  text-transform:uppercase; color:var(--ink-dim); white-space:nowrap; overflow:hidden;
}
.wf6-chip{
  flex:none; border-radius:999px; padding:1px 9px; font-size:11px; font-weight:600;
  background:rgba(239,68,68,0.16); color:var(--d4); font-variant-numeric:tabular-nums;
}

.wf6-sortbar{ display:flex; align-items:center; gap:8px; padding:7px 12px; border-bottom:1px solid var(--line); }
.wf6-sortlabel{ font-size:11px; color:var(--ink-faint); letter-spacing:0.08em; text-transform:uppercase; }
.wf6-seg{ display:inline-flex; border:1px solid var(--line); border-radius:999px; overflow:hidden; }
.wf6-seg button{
  border:none; background:transparent; color:var(--ink-dim); font-size:11px;
  padding:3px 12px; cursor:pointer;
}
.wf6-seg button:hover{ color:var(--ink); }
.wf6-seg button.on{ background:var(--panel-hi); color:var(--ink); }

.wf6-statusline{
  margin:0; padding:6px 12px; font-size:11px; color:var(--ink-faint);
  border-bottom:1px solid var(--line); min-height:24px;
}

.wf6-body{ flex:1; overflow-y:auto; overscroll-behavior:contain; }
.wf6-rows{ list-style:none; margin:0; padding:0; }
.wf6-rowitem + .wf6-rowitem{ border-top:1px solid var(--line); }
/* M2 route-affordance hook: the li gains a right-hand action rail (routing.js
   listens for wf6:route-request); the row button's own grid is untouched. */
.wf6-rowitem{ display:flex; align-items:stretch; }
.wf6-rowitem .wf6-row{ flex:1; min-width:0; }
.wf6-go{
  flex:none; width:36px; align-self:stretch;
  transition:background var(--fast) var(--ease), color var(--fast) var(--ease), opacity var(--fast) var(--ease);
  border:none; border-left:1px solid var(--line);
  background:transparent
    no-repeat center / 15px 15px
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%235A6579' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='9 6 15 12 9 18'/%3E%3C/svg%3E");
  color:var(--ink-faint); font-size:0; cursor:pointer; opacity:0.5;
}
.wf6-rowitem:hover .wf6-go{ opacity:1; }
.wf6-go:hover{
  background-color:var(--panel-hi);
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2338BDF8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='9 6 15 12 9 18'/%3E%3C/svg%3E");
}
.wf6-row{
  display:grid; transition:background var(--fast) var(--ease);
  grid-template-columns:20px 1fr auto 8px;
  grid-template-areas:"rank name depth dot" "rank meta meta dot";
  align-items:center; column-gap:9px; row-gap:2px;
  width:100%; padding:8px 12px 9px; background:transparent; border:none;
  color:inherit; font:inherit; text-align:left; cursor:pointer;
}
.wf6-row:hover, .wf6-row:focus-visible{ background:var(--panel-hi); }
.wf6-rank{ grid-area:rank; align-self:start; padding-top:1px; color:var(--ink-faint); font-size:11px; font-variant-numeric:tabular-nums; }
.wf6-name{ grid-area:name; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-weight:500; min-width:0; color:var(--ink); }
.wf6-depth{ grid-area:depth; justify-self:end; font-size:12px; color:var(--ink); white-space:nowrap; font-variant-numeric:tabular-nums; }
.wf6-meta{ grid-area:meta; display:flex; align-items:center; gap:7px; min-width:0; }
.wf6-ward{
  flex:none; border:1px solid var(--line); border-radius:5px;
  padding:0 6px; font-size:10px; line-height:1.5; color:var(--ink-dim);
  white-space:nowrap; background:rgba(255,255,255,0.02);
}
.wf6-lead{ min-width:0; overflow:hidden; text-overflow:ellipsis; font-size:11px; color:var(--ink-faint); white-space:nowrap; font-variant-numeric:tabular-nums; }
.wf6-lead.tone-0{ color:var(--d1); }
.wf6-lead.tone-1{ color:var(--d2); }
.wf6-lead.tone-2{ color:var(--d3); }
.wf6-lead.tone-3{ color:var(--d4); }
.wf6-dot{ width:7px; height:7px; border-radius:999px; background:var(--ink-faint); }
.wf6-dot.dot-flooded{ background:var(--d4); }
.wf6-dot.dot-not_flooded{ background:var(--ink-faint); }
.wf6-dot.dot-unknown{ background:var(--ink-faint); opacity:0.5; }

.wf6-expand{
  display:block; margin:8px 12px; padding:4px 14px; border-radius:999px;
  border:1px solid var(--line); background:var(--panel); color:var(--ink-dim);
  font-size:11px; cursor:pointer;
}
.wf6-expand:hover{ background:var(--panel-hi); color:var(--ink); }

.wf6-empty{ padding:22px 16px; font-size:12px; line-height:1.6; color:var(--ink-dim); }
.wf6-error{ padding:16px; font-size:12px; line-height:1.6; color:var(--ink-dim); }
.wf6-error-title{
  display:block; margin-bottom:6px; font-size:11px; font-weight:600;
  letter-spacing:0.08em; text-transform:uppercase; color:var(--surcharge);
}
.wf6-retry{
  margin-top:10px; padding:3px 14px; border-radius:999px; border:1px solid var(--line);
  background:var(--panel); color:var(--ink-dim); font-size:11px; cursor:pointer;
}
.wf6-retry:hover{ background:var(--panel-hi); color:var(--ink); }

.wf6-skel{
  height:36px; margin:8px 12px; border-radius:10px;
  background:linear-gradient(100deg, var(--panel) 40%, var(--panel-hi) 50%, var(--panel) 60%);
  background-size:200% 100%;
  animation:wf6-shimmer 1.4s linear infinite;
}
@keyframes wf6-shimmer{ from{ background-position:180% 0; } to{ background-position:-80% 0; } }

.wf6-int{ margin:10px 12px 12px; border:1px solid var(--line); border-radius:10px; background:var(--panel); }
.wf6-int summary{
  cursor:pointer; padding:8px 12px; font-size:11px; font-weight:600;
  letter-spacing:0.14em; text-transform:uppercase; color:var(--ink-dim);
  display:flex; align-items:center; gap:8px; list-style:none; user-select:none;
}
.wf6-int summary::-webkit-details-marker{ display:none; }
.wf6-int-count{
  border-radius:999px; background:var(--panel-hi); border:1px solid var(--line);
  padding:0 8px; font-size:10px; color:var(--ink-dim); font-variant-numeric:tabular-nums;
}
.wf6-int-list{ list-style:none; margin:0; padding:0 0 6px; }
.wf6-int-row{
  display:block; width:calc(100% - 16px); margin:0 8px; padding:6px 8px;
  background:transparent; border:none; border-top:1px solid var(--line);
  color:inherit; font:inherit; text-align:left; cursor:pointer; border-radius:6px;
}
.wf6-int-row:first-child{ border-top:none; }
.wf6-int-row:hover, .wf6-int-row:focus-visible{ background:var(--panel-hi); }
.wf6-int-name{ display:block; font-weight:500; overflow-wrap:anywhere; }
.wf6-blocks{ color:var(--surcharge); font-weight:600; font-variant-numeric:tabular-nums; }
.wf6-int-meta{ font-size:11px; color:var(--ink-dim); font-variant-numeric:tabular-nums; }
.wf6-int-note{ padding:4px 12px 10px; font-size:11px; color:var(--ink-faint); }

/* collapsed — slim rail, chevron flips, content hidden */
.wf6-watchlist.wf6-collapsed{ width:40px !important; }
.wf6-watchlist.wf6-collapsed .wf6-heading,
.wf6-watchlist.wf6-collapsed .wf6-chip,
.wf6-watchlist.wf6-collapsed .wf6-sortbar,
.wf6-watchlist.wf6-collapsed .wf6-statusline,
.wf6-watchlist.wf6-collapsed .wf6-body{ display:none; }
.wf6-watchlist.wf6-collapsed .wf6-titlebar{ justify-content:center; border-bottom:none; }
.wf6-watchlist.wf6-collapsed .wf6-collapse{ transform:rotate(180deg); }
`;

function ensureStyles() {
  if (document.getElementById("wf6-panel-style")) return;
  const style = el("style");
  style.id = "wf6-panel-style";
  style.textContent = PANEL_CSS;
  document.head.appendChild(style);
}

// ------------------------------------------------------------------- panel

export class WatchlistPanel {
  constructor({ frame = DEFAULT_FRAME } = {}) {
    this.frame = frame;
    this.sort = "severity"; // 'severity' = payload order; 'soonest' = local lead sort
    this.expanded = false; // ROW_RENDER_CAP virtualization expander
    this.collapsed = false;
    this.source = null;
    this.counts = null;
    this.rows = [];
    this.maxLead = 0;
    this.loadSeq = 0; // guards stale responses against newer refreshes
    this.root = null;
  }

  // ------------------------------------------------------------- mounting

  mount() {
    ensureStyles();
    // D1 live-empty enforcement: watch for any demo rows leaking into LIVE empty and replace
    const _liveEnforce = () => {
      const lm = (globalThis.__JALADHAR_MODE === "live") || (globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.mode === "live");
      const le = !!(globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.liveEmpty);
      if(lm && le){
        // if body contains demo rows, force empty
        const wl=document.getElementById("watchlist");
        const body=wl?.querySelector(".wf6-body");
        if(body && body.querySelector(".wf6-row")){
          body.replaceChildren();
          const empty=document.createElement("div");
          empty.className="wf6-empty";
          empty.textContent="no live run — streets at risk appear after the first live forecast";
          body.appendChild(empty);
          const intEl=document.querySelector("#watchlist .wf6-int"); if(intEl) intEl.remove();
          const chip=document.querySelector("#watchlist .wf6-chip");
          if(chip) chip.textContent="—";
          const sl=document.querySelector("#watchlist .wf6-statusline");
          if(sl) sl.style.display="none";
        }
      }
    };
    setInterval(_liveEnforce, 800);
    document.addEventListener("jaladhar:live-enter", _liveEnforce);
    // also observe DOM mutations on watchlist body
    setTimeout(()=>{
      const wl=document.getElementById("watchlist");
      const b=wl?.querySelector(".wf6-body");
      if(b){
        new MutationObserver(_liveEnforce).observe(b, {childList:true, subtree:true});
      }
    }, 1000);
    if (document.getElementById(PANEL_ID)) {
      throw new Error(`#${PANEL_ID} already exists — refusing double mount`);
    }
    const app = document.querySelector(".app") ?? document.body;
    const anchor = document.getElementById("map-area") ?? app.firstElementChild;
    const root = el("aside", "wf6-watchlist");
    root.id = PANEL_ID;
    root.setAttribute("aria-label", "Streets at risk");
    app.insertBefore(root, anchor);

    // ---- header
    const titlebar = el("div", "wf6-titlebar");
    this.collapseBtn = el("button", "wf6-collapse", "‹");
    this.collapseBtn.type = "button";
    this.collapseBtn.setAttribute("aria-expanded", "true");
    this.collapseBtn.setAttribute("aria-label", "Collapse streets panel");
    this.collapseBtn.addEventListener("click", () => this.toggleCollapsed());
    titlebar.append(
      this.collapseBtn,
      el("h2", "wf6-heading", "Streets at risk"),
      (this.countChip = el("span", "wf6-chip", "…"))
    );

    const sortbar = el("div", "wf6-sortbar");
    sortbar.append(el("span", "wf6-sortlabel", "sort"));
    const seg = el("div", "wf6-seg");
    this.sortButtons = {};
    for (const [value, label] of [
      ["severity", "Severity"],
      ["soonest", "Soonest"],
    ]) {
      const btn = el("button", value === this.sort ? "on" : "", label);
      btn.type = "button";
      btn.dataset.wf6Sort = value;
      btn.setAttribute("aria-pressed", String(value === this.sort));
      btn.addEventListener("click", () => this.setSort(value));
      this.sortButtons[value] = btn;
      seg.appendChild(btn);
    }
    sortbar.appendChild(seg);

    this.statusLine = el("p", "wf6-statusline");

    // ---- body
    this.body = el("section", "wf6-body");

    root.append(titlebar, sortbar, this.statusLine, this.body);
    this.root = root;

    document.addEventListener("wf6:frame-changed", (event) => {
      // suppress demo frame changes while in LIVE empty
      const isLiveEmpty = (globalThis.__JALADHAR_MODE === "live") && globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.liveEmpty;
      if(isLiveEmpty) return;
      const frame = event.detail && event.detail.frame;
      if (typeof frame === "string" && frame) this.setFrame(frame);
    });
    document.addEventListener("jaladhar:live-enter", () => { this.refresh(); });
    document.addEventListener("jaladhar:demo-enter", () => { this.refresh(); });
    return this;
  }

  toggleCollapsed() {
    this.collapsed = !this.collapsed;
    this.root.classList.toggle("wf6-collapsed", this.collapsed);
    this.collapseBtn.textContent = this.collapsed ? "›" : "‹";
    this.collapseBtn.setAttribute("aria-expanded", String(!this.collapsed));
    this.collapseBtn.setAttribute(
      "aria-label",
      this.collapsed ? "Expand streets panel" : "Collapse streets panel"
    );
    emit("wf6:watchlist-toggle", { open: !this.collapsed });
  }

  setSort(sort) {
    if (sort !== "severity" && sort !== "soonest") return;
    this.sort = sort;
    for (const [value, btn] of Object.entries(this.sortButtons)) {
      btn.classList.toggle("on", value === sort);
      btn.setAttribute("aria-pressed", String(value === sort));
    }
    this.renderRows();
  }

  setFrame(frame) {
    this.frame = frame;
    return this.refresh();
  }

  // -------------------------------------------------------------- fetching

  async refresh() {
    // D1 LIVE honesty: zero demo-derived numbers in LIVE with no live run
    const liveMode = (globalThis.__JALADHAR_MODE === "live") || (globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.mode === "live");
    const liveEmpty = !!(globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.liveEmpty);
    if(liveMode && liveEmpty){
      // honest live-empty, never fetch demo watchlist
      this.countChip.textContent = "—";
      this.countChip.title = "no live run yet";
      if(this.statusLine) this.statusLine.style.display="none";
      this.body.replaceChildren();
      const empty=document.createElement("div");
      empty.className="wf6-empty";
      empty.textContent="no live run — streets at risk appear after the first live forecast";
      this.body.appendChild(empty);
      this.source=null; this.counts=null; this.rows=[]; this.everLoaded=false;
      return;
    } else {
      if(this.statusLine) this.statusLine.style.display="";
    }
    const seq = ++this.loadSeq;
    // Stale-while-revalidate (V-trace friction finding): once real data has
    // been shown, a frame change keeps the previous rows visible with an
    // updating chip instead of blanking to skeletons. First load still
    // gets the full skeleton -> data / error sequence.
    const soft = this.everLoaded === true;
    if (soft) {
      this.countChip.textContent = "…";
      const prev = statusLineText(this.source, this.rows);
      this.statusLine.textContent = (prev ? prev + " · " : "") + "updating…";
    } else {
      this.renderLoading();
    }
    const [watchRes, intRes] = await Promise.allSettled([
      fetchJson(`/api/watchlist?frame=${encodeURIComponent(this.frame)}`),
      fetchJson(`/api/intersections?frame=${encodeURIComponent(this.frame)}`),
    ]);
    if (seq !== this.loadSeq) return; // superseded by a newer refresh

    if (watchRes.status === "fulfilled") {
      const payload = watchRes.value;
      const rows = Array.isArray(payload?.rows)
        ? payload.rows.filter((row) => row && typeof row === "object")
        : null;
      if (!rows) {
        if (soft) {
          // Keep the previous frame on screen; never fake new data.
          this.statusLine.textContent +=
            " (refresh failed — showing previous frame)";
        } else {
          this.renderError(new Error("payload carries no rows[]"), payload);
        }
        return;
      }
      this.source =
        payload.source && typeof payload.source === "object" ? payload.source : null;
      this.counts =
        payload.counts && typeof payload.counts === "object" ? payload.counts : null;
      this.rows = rows;
      this.expanded = false;
      this.maxLead = rows.reduce(
        (max, row) =>
          isFiniteNumber(row.lead_minutes) ? Math.max(max, row.lead_minutes) : max,
        0
      );
      this.everLoaded = true;
      this.renderData();
    } else {
      const err = watchRes.reason instanceof Error ? watchRes.reason : new Error(String(watchRes.reason));
      warnOnce("watchlist", "/api/watchlist unavailable:", err.message);
      if (soft) {
        this.statusLine.textContent =
          this.statusLine.textContent.replace(" · updating…", "") +
          " (refresh failed — showing previous frame)";
      } else {
        this.renderError(err);
      }
    }

    // Intersections share the panel but stand on their own endpoint: their
    // failure degrades to a note under the street list, never a fake node.
    if (intRes.status === "fulfilled") {
      const nodes = Array.isArray(intRes.value?.nodes)
        ? intRes.value.nodes.filter((node) => node && typeof node === "object")
        : null;
      this.renderIntersections(nodes);
    } else {
      const err = intRes.reason instanceof Error ? intRes.reason : new Error(String(intRes.reason));
      warnOnce("intersections", "/api/intersections unavailable:", err.message);
      this.renderIntersections(null, err);
    }
  }

  // --------------------------------------------------------------- states

  renderLoading() {
    this.countChip.textContent = "…";
    this.statusLine.textContent = "";
    const skeletons = el("div");
    for (let i = 0; i < 7; i++) skeletons.appendChild(el("div", "wf6-skel"));
    this.body.replaceChildren(skeletons);
  }

  renderError(err, payload) {
    this.countChip.textContent = "—";
    this.statusLine.textContent = "";
    const box = el("div", "wf6-error");
    box.appendChild(el("strong", "wf6-error-title", "watchlist unavailable"));
    const message =
      (payload && (payload.error || payload.message)) ||
      (err instanceof Error ? err.message : String(err));
    box.appendChild(el("span", null, `— ${message}`));
    const retry = el("button", "wf6-retry", "retry");
    retry.type = "button";
    retry.addEventListener("click", () => this.refresh());
    box.appendChild(retry);
    this.body.replaceChildren(box);
  }

  renderData() {
    const liveMode = (globalThis.__JALADHAR_MODE === "live") || (globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.mode === "live");
    const liveEmpty = !!(globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.liveEmpty);
    if(liveMode && liveEmpty){
      this.countChip.textContent = "—";
      this.countChip.title = "no live run yet";
      if(this.statusLine) this.statusLine.style.display="none";
      this.body.replaceChildren();
      const empty=document.createElement("div");
      empty.className="wf6-empty";
      empty.textContent="no live run — streets at risk appear after the first live forecast";
      this.body.appendChild(empty);
      this.source=null; this.counts=null; this.rows=[]; this.everLoaded=false;
      return;
    }
    const flooded = isFiniteNumber(this.counts?.flooded_streets)
      ? this.counts.flooded_streets
      : this.rows.length;
    this.countChip.textContent = String(flooded);
    this.countChip.title = isFiniteNumber(this.counts?.streets_total)
      ? `${this.counts.streets_total} classified streets in frame`
      : "streets classified in frame";
    this.statusLine.textContent = statusLineText(this.source, this.rows);

    if (!this.rows.length) {
      const empty = el("div", "wf6-empty");
      empty.textContent = "No streets forecast to flood in this frame";
      this.body.replaceChildren(empty);
      return;
    }
    const wrap = el("div");
    this.rowsHost = el("ol", "wf6-rows");
    wrap.appendChild(this.rowsHost);
    this.body.replaceChildren(wrap);
    this.renderRows();
  }

  // ----------------------------------------------------------------- rows

  sortedRows() {
    if (this.sort !== "soonest") return this.rows;
    // Local re-sort by lead_minutes; rows with no realized lead sink rather
    // than inventing a position; ties fall back to the payload's rank.
    return [...this.rows].sort((a, b) => {
      const la = a.lead_minutes;
      const lb = b.lead_minutes;
      const fa = isFiniteNumber(la);
      const fb = isFiniteNumber(lb);
      if (fa && fb && la !== lb) return la - lb;
      if (fa !== fb) return fa ? -1 : 1;
      return (a.rank ?? 0) - (b.rank ?? 0);
    });
  }

  renderRows() {
    const host = this.rowsHost;
    if (!host) return;
    host.replaceChildren();
    const rows = this.sortedRows();
    const shown = this.expanded ? rows.length : Math.min(rows.length, ROW_RENDER_CAP);
    for (let i = 0; i < shown; i++) host.appendChild(this.rowElement(rows[i], i + 1));

    if (rows.length > shown) {
      // The expander lives outside the <ol> (which is fully rebuilt), so drop
      // any previous one first or a sort-toggle would stack duplicates.
      this.body.querySelector(".wf6-expand")?.remove();
      const expand = el("button", "wf6-expand", `show all ${rows.length} rows`);
      expand.type = "button";
      expand.addEventListener("click", () => {
        this.expanded = true;
        this.renderRows();
      });
      host.parentNode.appendChild(expand);
    }
  }

  rowElement(row, rank) {
    const item = el("li", "wf6-rowitem");
    const btn = el("button", "wf6-row");
    btn.type = "button";

    btn.appendChild(el("span", "wf6-rank", String(rank)));

    // primary line: name + depth band + status dot
    btn.appendChild(el("span", "wf6-name", row.street_name ?? "unnamed street"));
    btn.appendChild(el("span", "wf6-depth", formatDepthBand(row.depth_band_cm)));
    btn.appendChild(el("span", `wf6-dot dot-${dotClass(row.status)}`));

    // secondary meta line spans the row width: ward + valid time / lead.
    // (app.js appends the beyond-horizon hint into .wf6-lead, so it stays a
    //  distinct element carrying the valid-time text.)
    const meta = el("span", "wf6-meta");
    const ward = wardLabel(row);
    if (ward) meta.appendChild(el("span", "wf6-ward", ward));
    const lead = el("span", "wf6-lead", this.leadCellText(row));
    const tone = this.leadToneClass(row);
    if (tone) lead.classList.add(tone); // empty token throws on DOMTokenList
    meta.appendChild(lead);
    btn.appendChild(meta);

    const segments = Array.isArray(row.segment_ids) ? row.segment_ids.length : null;
    btn.title =
      isFiniteNumber(row.worst_depth_cm) && segments != null
        ? `worst cell ${row.worst_depth_cm} cm · ${segments} segment${segments === 1 ? "" : "s"}`
        : "";

    btn.addEventListener("click", () => {
      const segmentId = isFiniteNumber(row.worst_segment_id)
        ? row.worst_segment_id
        : Array.isArray(row.segment_ids)
          ? row.segment_ids[0] ?? null
          : null;
      // Decoupled by design: the integrator wires fly-to/highlight from this
      // event. This panel never reaches into the map's pick machinery.
      emit("wf6:select-street", {
        segment_id: segmentId,
        street_name: row.street_name ?? null,
        world_hint: null,
      });
    });

    item.appendChild(btn);

    // M2 route-around hook (minimal edit): a per-row action that emits the
    // request event consumed by routing.js. The selected vehicle class lives
    // in routing.js's global (null when that module is absent — its handler
    // then answers with a designed message). This panel invents nothing.
    const go = el("button", "wf6-go");
    go.type = "button";
    go.setAttribute("aria-label", `route around ${row.street_name ?? "this street"}`);
    go.title = "route around this street";
    go.addEventListener("click", (event) => {
      event.stopPropagation();
      emit("wf6:route-request", {
        segment_id:
          isFiniteNumber(row.worst_segment_id)
            ? row.worst_segment_id
            : Array.isArray(row.segment_ids)
              ? row.segment_ids[0] ?? null
              : null,
        street_name: row.street_name ?? null,
        vehicle_class: globalThis.__WF6_ROUTING_VEHICLE_CLASS ?? null,
      });
    });
    item.appendChild(go);
    return item;
  }

  leadCellText(row) {
    // A hindcast offset is where water stood relative to a past instant —
    // never typeset as a "+N min" lead, even when a numeric offset exists.
    if (this.source?.lead_kind === "hindcast_offset") {
      return Timeline.formatValidTime(row.valid_time_utc);
    }
    if (isFiniteNumber(row.lead_minutes)) return `+${row.lead_minutes} min`;
    return Timeline.formatValidTime(row.valid_time_utc);
  }

  leadToneClass(row) {
    if (
      this.source?.lead_kind !== "forecast_lead" ||
      !isFiniteNumber(row.lead_minutes) ||
      !(this.maxLead > 0)
    ) {
      return "";
    }
    const t = row.lead_minutes / this.maxLead; // normalised against realized max, no hardcoded horizon
    return t < 0.25 ? "tone-0" : t < 0.5 ? "tone-1" : t < 0.75 ? "tone-2" : "tone-3";
  }

  // --------------------------------------------------------- intersections

  renderIntersections(nodes, error) {
    const liveMode = (globalThis.__JALADHAR_MODE === "live") || (globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.mode === "live");
    const liveEmpty = !!(globalThis.__JALADHAR_STATE && globalThis.__JALADHAR_STATE.liveEmpty);
    if(liveMode && liveEmpty){
      this.root.querySelector(".wf6-int")?.remove();
      return;
    }
    // Remove any previous section; the whole panel re-renders on refresh.
    this.root.querySelector(".wf6-int")?.remove();
    if (this.body.querySelector(".wf6-empty") && !nodes) return;

    const details = el("details", "wf6-int");
    details.open = true;
    const summary = el("summary", null, "Intersections");
    summary.appendChild(el("span", "wf6-int-count", nodes ? String(nodes.length) : "—"));
    details.appendChild(summary);

    if (!nodes) {
      const message = error
        ? `intersections unavailable — ${error.message}`
        : "intersections payload carries no nodes[]";
      details.appendChild(el("p", "wf6-int-note", message));
      this.body.appendChild(details);
      return;
    }
    if (!nodes.length) {
      details.appendChild(
        el("p", "wf6-int-note", "No junction meets the blocking threshold in this frame")
      );
      this.body.appendChild(details);
      return;
    }

    const list = el("ul", "wf6-int-list");
    for (const node of nodes) {
      const btn = el("button", "wf6-int-row");
      btn.type = "button";
      const streets = Array.isArray(node.streets) ? node.streets.filter(Boolean) : [];
      btn.appendChild(
        el("span", "wf6-int-name", streets.length ? streets.join(" × ") : "unnamed junction")
      );
      const meta = el("span", "wf6-int-meta");
      const nAp = node.approaches_blocked;
      meta.appendChild(
        el(
          "span",
          "wf6-blocks",
          isFiniteNumber(nAp)
            ? `blocks ${nAp} approach${nAp === 1 ? "" : "es"}`
            : "blocks ? approaches"
        )
      );
      const bits = [formatDepthBand(node.depth_band_cm)];
      if (isFiniteNumber(node.lead_minutes)) bits.push(`+${node.lead_minutes} min`);
      meta.appendChild(document.createTextNode(" · " + bits.join(" · ")));
      btn.appendChild(meta);

      btn.addEventListener("click", () => {
        emit("wf6:select-intersection", {
          node_id: node.node_id ?? null,
          world_xy: Array.isArray(node.world_xy) ? node.world_xy.slice() : null,
        });
      });
      list.appendChild(el("li", null)).appendChild(btn);
    }
    details.appendChild(list);
    this.body.appendChild(details);
  }
}

// -------------------------------------------------------------------- boot

export function bootWatchlist(opts) {
  if (globalThis.__WF6_WATCHLIST) return globalThis.__WF6_WATCHLIST;
  const panel = new WatchlistPanel(opts);
  panel.mount();
  panel.refresh();
  globalThis.__WF6_WATCHLIST = panel;
  return panel;
}

// Auto-mount on import — the integrator wires the dashboard by adding these
// modules to the import graph, not by editing HTML.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => bootWatchlist(), { once: true });
} else {
  bootWatchlist();
}
