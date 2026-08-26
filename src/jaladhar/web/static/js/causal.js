// WF-6 requirement A5 — causal-link panel for the selected street segment.
//
// Rule 2 binds every string here: this panel renders exactly the status the
// payload declares. A PREDICTED overcapacity (design-storm rational demand vs
// bounded-section conveyance) is never worded or coloured as a MEASURED
// surcharge, and the empty state says plainly that no coupled run exists.
// Absent/failed payload degrades to the designed empty state — nothing is
// invented client-side, no number is typed anywhere in this module (rule 3).
//
// Integration (routes/wiring belong to the integrator):
//   import { CausalPanel } from "./js/causal.js";
//   const causal = new CausalPanel();
//   ...after rail.selectSegment(segmentId, ...): await causal.show(segmentId);
// The panel renders into <div id="causal-body">, created on demand INSIDE the
// "Selected segment" panel section (sibling of #selected-body, so rail.js's
// replaceChildren() cannot wipe it mid-selection) — no index.html edit needed.

const CAUSAL_HOST_ID = "causal-body";
export const CAUSAL_FETCH_PATH = "/api/drains/causal?segment_id=";
export const CAUSAL_NONE_STATUS = "none_measured";
export const CAUSAL_NONE_STATEMENT =
  "No measured surcharge for this street — coupled run pending.";

const STATUS_LABELS = {
  measured: "MEASURED",
  predicted: "PREDICTED",
  none_measured: "NONE",
};

const STATUS_TITLES = {
  measured: "Surcharge was recorded here by a coupled drain-network run.",
  predicted:
    "Design-storm calculation says this drain is over capacity — a prediction, not an observation.",
  none_measured: "No coupled drain run exists yet, so nothing is claimed for this street.",
};

const WHY_THIS_MATTERS =
  "Why this matters: a PREDICTED overcapacity comes from the cited design storm and cited " +
  "drain capacities; only a MEASURED surcharge comes from a coupled hydraulic run. " +
  "Confusing the two would overstate what the system knows.";

function ensureCausalStyles(documentRef) {
  // styles.css is owned by another lane; the pulse keyframes are injected once
  // from here instead. The red pulse animation attaches ONLY to
  // [data-status="measured"] so a predicted/none pill can never animate.
  if (documentRef.getElementById("causal-style")) return;
  const style = documentRef.createElement("style");
  style.id = "causal-style";
  style.textContent =
    "@keyframes causal-pulse{0%,100%{box-shadow:0 0 0 0 rgba(220,38,38,.45)}" +
    "50%{box-shadow:0 0 0 6px rgba(220,38,38,0)}}" +
    "#" +
    CAUSAL_HOST_ID +
    ' .causal-pill[data-status="measured"]{animation:causal-pulse 1.6s ease-in-out infinite}';
  documentRef.head.appendChild(style);
}

function pillFor(status) {
  const pill = document.createElement("span");
  pill.className = "causal-pill";
  pill.dataset.status = status;
  pill.textContent = STATUS_LABELS[status] ?? STATUS_LABELS.none_measured;
  pill.title = STATUS_TITLES[status] ?? STATUS_TITLES.none_measured;
  if (status === "measured") {
    // red pulse ONLY when measured
    pill.style.background = "#dc2626";
    pill.style.color = "#fff";
    pill.style.border = "1px solid #dc2626";
  } else if (status === "predicted") {
    // hollow amber: outline only, transparent fill
    pill.style.background = "transparent";
    pill.style.color = "#b45309";
    pill.style.border = "1px solid #d97706";
  } else {
    // faint ink
    pill.style.background = "transparent";
    pill.style.color = "#6b7280";
    pill.style.border = "1px solid var(--ink-faint, #cbd5e1)";
  }
  pill.style.fontSize = "10px";
  pill.style.fontWeight = "600";
  pill.style.letterSpacing = "0.08em";
  pill.style.padding = "2px 8px";
  pill.style.borderRadius = "999px";
  return pill;
}

function statementEl(text) {
  const p = document.createElement("p");
  p.className = "causal-statement";
  p.textContent = String(text);
  p.title = WHY_THIS_MATTERS;
  p.style.margin = "6px 0 0";
  p.style.fontSize = "12px";
  p.style.lineHeight = "1.45";
  return p;
}

function provenanceEl(predictedSet) {
  if (!predictedSet || typeof predictedSet !== "object") return null;
  const p = document.createElement("p");
  p.className = "provenance-line";
  const sha = typeof predictedSet.sha256 === "string" ? predictedSet.sha256.slice(0, 12) : "?";
  p.textContent =
    "predicted set " + String(predictedSet.size ?? "?") + " edges · source " +
    String(predictedSet.source ?? "?") + " · sha256 " + sha + "…";
  p.title = "Traceability: the predicted-overcapacity edge count and the manifest bytes " +
    "it was read from (rule 3). This set is modelled, not observed.";
  return p;
}

// Sparkline of per-node capacity vs demand. WIRED BUT INERT today (rule 2):
// the payload builder emits capacity_basis as provenance TEXT only and never
// numeric capacity/demand pairs, so this branch draws nothing until a future
// payload actually carries measured/modelled numbers. It plots only finite,
// paired values it was handed — it never derives or estimates one.
function sparklineIfMeasuredNumbers(nodes) {
  const pairs = (Array.isArray(nodes) ? nodes : [])
    .map((n) => [Number(n?.capacity_m3s), Number(n?.demand_m3s)])
    .filter(([c, d]) => Number.isFinite(c) && Number.isFinite(d));
  if (!pairs.length) return null;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  const w = 120;
  const h = 28;
  svg.setAttribute("viewBox", "0 0 " + w + " " + h);
  svg.setAttribute("width", String(w));
  svg.setAttribute("height", String(h));
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "per-node capacity versus demand");
  const max = Math.max(...pairs.flat());
  pairs.forEach(([cap, dem], i) => {
    const slot = (w / pairs.length) | 0;
    const x0 = i * slot;
    const barW = Math.max(2, slot * 0.28);
    const capH = (cap / max) * (h - 4);
    const demH = (dem / max) * (h - 4);
    const capBar = document.createElementNS(NS, "rect");
    capBar.setAttribute("x", String(x0 + slot * 0.15));
    capBar.setAttribute("y", String(h - capH));
    capBar.setAttribute("width", String(barW));
    capBar.setAttribute("height", String(capH));
    capBar.setAttribute("fill", "#64748b");
    const demBar = document.createElementNS(NS, "rect");
    demBar.setAttribute("x", String(x0 + slot * 0.55));
    demBar.setAttribute("y", String(h - demH));
    demBar.setAttribute("width", String(barW));
    demBar.setAttribute("height", String(demH));
    demBar.setAttribute("fill", "#dc2626");
    svg.append(capBar, demBar);
  });
  const wrap = document.createElement("div");
  wrap.className = "causal-sparkline";
  wrap.appendChild(svg);
  const key = document.createElement("span");
  key.textContent = "grey capacity · red demand";
  key.style.fontSize = "10px";
  key.style.color = "#6b7280";
  wrap.append(key);
  return wrap;
}

function hollowNodeLegend(nodes) {
  if (!Array.isArray(nodes) || !nodes.length) return null;
  const wrap = document.createElement("div");
  wrap.className = "causal-nodes";
  wrap.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;margin-top:6px;";
  for (const n of nodes) {
    if (!n || !Array.isArray(n.world_xy)) continue;
    const chip = document.createElement("span");
    chip.className = "causal-node";
    chip.dataset.flagged = n.demand_exceeds_capacity ? "true" : "false";
    chip.textContent = "node " + String(n.node_id);
    chip.title =
      "Drain node near this street; edges " + (n.edge_ids ?? []).join(", ") +
      ". Draw hollow on the map: predicted overcapacity, not a measured event.";
    chip.style.cssText =
      "font-size:10px;color:#b45309;border:1px dashed #d97706;border-radius:999px;" +
      "padding:1px 7px;background:transparent;";
    wrap.appendChild(chip);
  }
  return wrap.childNodes.length ? wrap : null;
}

function emptyState(host, note) {
  host.replaceChildren();
  host.appendChild(pillFor(CAUSAL_NONE_STATUS));
  host.appendChild(statementEl(CAUSAL_NONE_STATEMENT));
  if (note) {
    const d = document.createElement("p");
    d.className = "causal-detail";
    d.textContent = String(note);
    d.style.cssText = "margin:4px 0 0;font-size:11px;color:#9ca3af;";
    host.appendChild(d);
  }
}

export class CausalPanel {
  constructor({ fetchImpl = fetch, documentRef = document } = {}) {
    this.fetchImpl = fetchImpl;
    this.document = documentRef;
    this.lastSegmentId = null;
  }

  ensureHost() {
    let host = this.document.getElementById(CAUSAL_HOST_ID);
    if (host) return host;
    host = this.document.createElement("div");
    host.id = CAUSAL_HOST_ID;
    host.className = "causal-body";
    const panel =
      this.document.getElementById("selected-body")?.closest("section.panel") ??
      this.document.body;
    panel.appendChild(host);
    return host;
  }

  async show(segmentId) {
    this.lastSegmentId = String(segmentId);
    const host = this.ensureHost();
    ensureCausalStyles(this.document);
    emptyState(host, "resolving causal link…");
    let payload = null;
    try {
      const res = await this.fetchImpl(CAUSAL_FETCH_PATH + encodeURIComponent(segmentId), {
        cache: "no-store",
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      payload = await res.json();
    } catch (_err) {
      // absent payload => designed empty state; never invent (rule 2)
      emptyState(host);
      return;
    }
    // Ignore stale responses after a rapid re-selection.
    if (this.lastSegmentId !== String(segmentId)) return;
    this.render(payload, segmentId);
  }

  render(payload, segmentId) {
    const host = this.ensureHost();
    ensureCausalStyles(this.document);
    const status =
      payload && typeof payload.status === "string" && payload.status in STATUS_LABELS
        ? payload.status
        : CAUSAL_NONE_STATUS;
    const statement =
      payload && typeof payload.statement === "string"
        ? payload.statement
        : CAUSAL_NONE_STATEMENT;
    host.replaceChildren();
    host.appendChild(pillFor(status));
    host.appendChild(statementEl(statement));
    const prov = provenanceEl(payload?.predicted_set);
    if (prov) host.appendChild(prov);
    const legend = hollowNodeLegend(payload?.nodes);
    if (legend) host.appendChild(legend);
    const spark = sparklineIfMeasuredNumbers(payload?.nodes);
    if (spark) host.appendChild(spark);
    void segmentId;
  }
}
