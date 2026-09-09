// WF-6 L8 — location search over wards and streets (N5). Floating box at the
// top-left of the map area; sources merged from
// GET /api/context/search_index.json.
//
// Rules that bind this module:
//   1/2 — suggestions come only from the served index; if it is missing the
//         box says so and offers nothing. No local name table is invented.
//   3   — no PIN codes anywhere: the index holds none and none are rendered,
//         whatever fields arrive.
//
// V8 integration seam (documented for the integrator):
//   emits on document:
//     wf6:select-ward   {name}
//     wf6:select-street {segment_id, street_name, world_hint}  — segment_id is
//                       the entry's first realized segment
//   The box repositions itself right of the watchlist panel while that panel
//   is expanded (listening for its wf6:watchlist-toggle event), so the two
//   modules stay decoupled.

const SEARCH_WIDTH = 340;
const MAX_RESULTS = 12;
const DEBOUNCE_MS = 120;
const INDEX_URL = "/api/context/search_index.json";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function emit(type, detail) {
  document.dispatchEvent(new CustomEvent(type, { detail }));
}

// Accepts both the contract shape ({name_lc, ward}) and the realized
// producer shape ({key, ward_no, ward_name}) — V8: assert nothing about which
// one is live tonight; read defensively, render only what exists.
function normalizeEntry(entry) {
  const name = typeof entry?.name === "string" ? entry.name : null;
  if (!name) return null;
  const lc =
    (typeof entry.name_lc === "string" && entry.name_lc) ||
    (typeof entry.key === "string" && entry.key) ||
    name.toLowerCase();
  const ward =
    (typeof entry.ward === "string" && entry.ward) ||
    (Number.isFinite(entry.ward_no) ? `Ward ${entry.ward_no}` : null) ||
    (typeof entry.ward_name === "string" ? entry.ward_name : null);
  const segmentIds = Array.isArray(entry.segment_ids)
    ? entry.segment_ids.filter((id) => Number.isFinite(id))
    : [];
  return { kind: entry.kind === "ward" ? "ward" : "street", name, lc, ward, segmentIds };
}

// ------------------------------------------------------------------ styles

const SEARCH_CSS = `
.wf6-search{
  position:absolute; top:calc(var(--header-h,56px) + 14px); z-index:50;
  width:min(${SEARCH_WIDTH}px, calc(100% - 24px));
  font-size:13px; transition:left var(--base,240ms) var(--ease,ease);
}
.wf6-search input{
  width:100%; padding:10px 14px 10px 38px; border-radius:var(--radius-ctl,10px);
  border:1px solid var(--glass-border, rgba(255,255,255,0.16));
  background:var(--glass, rgba(255,255,255,0.72))
    no-repeat left 14px center / 15px 15px
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='rgba(0,0,0,0.42)' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='M21 21l-4.3-4.3'/%3E%3C/svg%3E");
  -webkit-backdrop-filter:var(--glass-refract, blur(32px) saturate(180%)); backdrop-filter:var(--glass-refract, blur(32px) saturate(180%));
  box-shadow:var(--shadow-hud, 0 12px 40px rgba(0,0,0,0.4)), var(--glass-highlight, inset 0 1px 0 rgba(255,255,255,0.3));
  color:var(--ink); font-size:13px; outline:none;
  transition:border-color var(--fast,160ms) var(--ease,ease), box-shadow var(--fast,160ms) var(--ease,ease);
}
.wf6-search input::placeholder{ color:var(--ink-faint); }
.wf6-search input:focus{ border-color:var(--accent); box-shadow:var(--shadow-hud, 0 12px 40px rgba(0,0,0,0.4)), 0 0 0 3px rgba(10,132,255,0.30); }
.wf6-search input:focus-visible{ outline:1px solid var(--accent); outline-offset:2px; }
.wf6-search-list{
  position:absolute; left:0; right:0; top:calc(100% + 4px); margin:0; padding:4px;
  list-style:none; background:var(--glass, rgba(255,255,255,0.9)); border:1px solid var(--line);
  border-radius:10px; max-height:320px; overflow-y:auto; box-sizing:border-box;
}
.wf6-search-list[hidden]{ display:none; }
.wf6-search-opt{
  display:flex; align-items:center; gap:8px; padding:6px 9px; border-radius:6px;
  cursor:pointer; color:var(--ink); transition:background var(--fast) var(--ease);
}
.wf6-search-opt:hover{ background:var(--panel-hi); }
.wf6-search-opt.active{ background:var(--panel-hi); }
.wf6-search-kind{
  flex:none; min-width:42px; text-align:center; border:1px solid var(--line);
  border-radius:999px; padding:0 7px; font-size:10px; letter-spacing:0.06em;
  text-transform:uppercase; color:var(--ink-dim); background:var(--panel);
}
.wf6-search-name{ flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.wf6-search-ward{ flex:none; font-size:11px; color:var(--ink-faint); }
.wf6-search-note{ padding:8px 10px; font-size:11px; line-height:1.5; color:var(--ink-faint); }
`;

function ensureStyles() {
  if (document.getElementById("wf6-search-style")) return;
  const style = el("style");
  style.id = "wf6-search-style";
  style.textContent = SEARCH_CSS;
  document.head.appendChild(style);
}

// ------------------------------------------------------------------- search

export class LocationSearch {
  constructor() {
    this.entries = null; // normalized index once loaded
    this.indexPromise = null; // in-flight or failed load (failed -> retried)
    this.unavailable = null;
    this.options = [];
    this.active = -1;
    this.debouncer = 0;
    this.warnedIndex = false;
    this.root = null;
    this.listbox = null;
    this.input = null;
  }

  mount() {
    ensureStyles();
    const host = document.querySelector(".app") ?? document.getElementById("map-area") ?? document.body;
    const root = el("div", "wf6-search");
    root.setAttribute("role", "search");

    this.input = el("input");
    this.input.type = "text";
    this.input.placeholder = "Search wards or streets…";
    this.input.setAttribute("aria-label", "Search wards or streets");
    this.input.autocomplete = "off";
    this.input.spellcheck = false;

    this.listbox = el("ul", "wf6-search-list");
    this.listbox.hidden = true;
    this.listbox.setAttribute("role", "listbox");

    root.append(this.input, this.listbox);
    host.appendChild(root);
    this.root = root;

    this.input.addEventListener("input", () => {
      clearTimeout(this.debouncer);
      this.debouncer = setTimeout(() => this.runQuery(this.input.value), DEBOUNCE_MS);
    });
    this.input.addEventListener("focus", () => {
      this.reposition();
      if (this.input.value.trim()) this.runQuery(this.input.value);
    });
    this.input.addEventListener("keydown", (event) => this.onKeydown(event));
    // Click on an option must not steal focus before the click lands.
    this.listbox.addEventListener("mousedown", (event) => event.preventDefault());
    this.root.addEventListener("focusout", (event) => {
      if (!this.root.contains(event.relatedTarget)) this.close();
    });

    document.addEventListener("wf6:watchlist-toggle", () => this.reposition());
    // The panel may mount after us depending on import order — re-check twice
    // before settling, then live off the toggle event alone.
    requestAnimationFrame(() => this.reposition());
    setTimeout(() => this.reposition(), 300);
    return this;
  }

  // Left edge sits beside the watchlist overlay while it is open. The width
  // mirrors watchlist.js PANEL_WIDTH (duplicated constant, not an import, so
  // either module can load standalone).
  reposition() {
    const panel = document.getElementById("watchlist");
    // The red traffic light (body.hide-left) removes the panel entirely — the
    // search returns to the map's left margin. When the panel is collapsed to
    // its 40 px rail instead, the search must sit CLEAR of it: at 12 px it
    // would cover the rail's expand chevron and the panel could never be
    // reopened (owner bug report 2026-09-09).
    const hidden =
      document.body.classList.contains("hide-left") ||
      !panel ||
      getComputedStyle(panel).display === "none";
    if (hidden) {
      this.root.style.left = "12px";
      return;
    }
    const open = !panel.classList.contains("wf6-collapsed");
    this.root.style.left = open ? `${12 + 320 + 12}px` : `${12 + 40 + 12}px`;
  }

  // ------------------------------------------------------------------ data

  async ensureIndex() {
    if (this.entries) return this.entries;
    if (!this.indexPromise) {
      this.indexPromise = this.loadIndex();
    }
    return this.indexPromise;
  }

  async loadIndex() {
    let response;
    try {
      const override = globalThis.__wf6_FETCH_OVERRIDE;
      response =
        typeof override === "function"
          ? await override(INDEX_URL)
          : await fetch(INDEX_URL, { cache: "no-store" });
    } catch (err) {
      this.indexPromise = null; // allow a retry on next focus/keystroke
      throw new Error(err instanceof Error ? err.message : String(err));
    }
    if (!response.ok) {
      this.indexPromise = null;
      let message = `search index failed (${response.status})`;
      try {
        const payload = await response.json();
        if (payload && (payload.error || payload.message)) message = payload.error || payload.message;
      } catch {
        // non-JSON error body
      }
      throw new Error(message);
    }
    const payload = await response.json();
    const raw = Array.isArray(payload?.entries) ? payload.entries : null;
    if (!raw) throw new Error("search index payload carries no entries[]");
    const entries = raw.map(normalizeEntry).filter(Boolean);
    this.entries = entries;
    this.unavailable = null;
    return entries;
  }

  // ---------------------------------------------------------------- render

  async runQuery(text) {
    const query = String(text ?? "").trim().toLowerCase();
    if (!query) {
      this.close();
      return;
    }
    try {
      const entries = await this.ensureIndex();
      // A stale keystroke resolving after a cleared input must not reopen.
      if (!this.input.value.trim()) return;
      this.showResults(this.rank(entries, query));
    } catch (err) {
      if (!this.warnedIndex) {
        this.warnedIndex = true;
        console.warn("[wf6] search index unavailable:", err instanceof Error ? err.message : err);
      }
      this.showNote(`search index unavailable — ${err instanceof Error ? err.message : err}`);
    }
  }

  // Prefix matches first, then earliest substring hit, then shorter names,
  // then alphabetical — all decided by the query itself, nothing hardcoded.
  rank(entries, query) {
    const scored = [];
    for (const entry of entries) {
      const idx = entry.lc.indexOf(query);
      if (idx < 0) continue;
      scored.push({ entry, idx, prefix: entry.lc.startsWith(query) });
    }
    scored.sort((a, b) => {
      if (a.prefix !== b.prefix) return a.prefix ? -1 : 1;
      if (a.idx !== b.idx) return a.idx - b.idx;
      if (a.entry.lc.length !== b.entry.lc.length) return a.entry.lc.length - b.entry.lc.length;
      return a.entry.lc.localeCompare(b.entry.lc);
    });
    return scored.slice(0, MAX_RESULTS).map((item) => item.entry);
  }

  showResults(results) {
    this.listbox.replaceChildren();
    this.options = results;
    this.active = results.length ? 0 : -1;
    if (!results.length) {
      this.showNote("no matching ward or street");
      return;
    }
    for (const entry of results) {
      const li = el("li", "wf6-search-opt");
      li.setAttribute("role", "option");
      li.appendChild(el("span", "wf6-search-kind", entry.kind));
      li.appendChild(el("span", "wf6-search-name", entry.name));
      if (entry.ward) li.appendChild(el("span", "wf6-search-ward", entry.ward));
      li.addEventListener("click", () => this.pick(entry));
      this.listbox.appendChild(li);
    }
    this.syncActive();
    this.listbox.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  }

  showNote(message) {
    this.listbox.replaceChildren(el("li", "wf6-search-note", message));
    this.options = [];
    this.active = -1;
    this.listbox.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  }

  close() {
    this.listbox.hidden = true;
    this.listbox.replaceChildren();
    this.options = [];
    this.active = -1;
    this.input.setAttribute("aria-expanded", "false");
  }

  syncActive() {
    [...this.listbox.children].forEach((node, i) => {
      const on = i === this.active;
      node.classList.toggle("active", on);
      if (node.hasAttribute("role")) node.setAttribute("aria-selected", String(on));
      if (on) node.scrollIntoView({ block: "nearest" });
    });
  }

  // ------------------------------------------------------------ interaction

  onKeydown(event) {
    if (event.key === "Escape") {
      if (!this.listbox.hidden) {
        event.stopPropagation();
        this.close();
      }
      return;
    }
    if (this.listbox.hidden) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (this.options.length) {
        this.active = Math.min(this.active + 1, this.options.length - 1);
        this.syncActive();
      }
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (this.options.length) {
        this.active = Math.max(this.active - 1, 0);
        this.syncActive();
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      const pick = this.options[this.active] ?? this.options[0];
      if (pick) this.pick(pick);
    }
  }

  pick(entry) {
    if (entry.kind === "ward") {
      emit("wf6:select-ward", { name: entry.name });
    } else {
      emit("wf6:select-street", {
        segment_id: entry.segmentIds.length ? entry.segmentIds[0] : null,
        street_name: entry.name,
        world_hint: null,
      });
    }
    this.close();
  }
}

// -------------------------------------------------------------------- boot

export function bootLocationSearch() {
  if (globalThis.__WF6_SEARCH) return globalThis.__WF6_SEARCH;
  const search = new LocationSearch();
  search.mount();
  globalThis.__WF6_SEARCH = search;
  return search;
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => bootLocationSearch(), { once: true });
} else {
  bootLocationSearch();
}
