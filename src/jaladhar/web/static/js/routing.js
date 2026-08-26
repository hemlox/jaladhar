// WF-6 / M2 — route-around affordance: vehicle-class selector + per-street
// route requests against the CPU-only routing API (jaladhar/routing/api.py).
//
// ── Scientific-readiness story (stated plainly, no code change) ─────────────
// If the product-bound scientific gate has not passed (G1 FAIL, or its report
// is absent/invalid), the API reports operational_routing_ready=false on
// /health and EVERY POST /route is refused 503 scientific_gate_not_accepted,
// regardless of how many wading classes or snap configurations are unblocked.
// The demo narrative under G1 FAIL is exactly: "wading + snap gates visibly
// unblocked; routing execution awaits the scientific gate."
//
// ── Bound contracts (read from api.py/graph.py before writing this) ────────
// POST {base}/v1/route body — ALL five fields required (api.py validation):
//   origin, destination      [x,y] pairs in the graph CRS EPSG:32643. A bare
//                            2-element array is treated as graph-CRS coords by
//                            RoadNetwork._to_graph_crs (verified).
//   vehicle_class            string; resolved against the cited policy catalog.
//   forecast_lead_minutes    integer within 0..180.
//   max_snap_distance_m      positive number ≤ server cap.
// Origin/destination resolution ("graph-neighbour offset"): the road graph's
// nodes ARE segment endpoints (RoadNetwork builds nodes from geometry
// endpoints), so this module resolves the target flooded segment's OWN two
// endpoint coordinates from already-served dashboard metadata
// (/api/segments GeoJSON). Snapping distance is then exactly 0 ≤ any server
// cap, and the baseline path between them IS the blocked segment itself — the
// safe route must genuinely route around it or honestly refuse.
//
// ── Honesty rules (repo rules 1–3 bind this module) ─────────────────────────
// * max_snap_distance_m is ECHOED from GET {base}/health
//   snap_policy.server_max_snap_distance_m. Never invented; if absent, the
//   request is refused locally with a designed message.
// * forecast_lead_minutes is sent ONLY when the displayed frame payload's
//   source declares lead_kind === "forecast_lead" with a finite lead_minutes
//   value. A hindcast offset is NEVER sent as a forecast lead; when no
//   realized forecast lead exists the field is omitted and the API's own
//   "missing required field" refusal is surfaced as-is.
// * Every failure renders a designed state; no fabricated route exists here.
//   Refusal codes map 1:1 to banner text (see REFUSAL_TEXT below).
//
// ── V8 integration seams (documented for the integrator) ───────────────────
//   listens for (document):
//     wf6:route-request {segment_id, street_name, vehicle_class}
//       — emitted by watchlist.js per-row action buttons.
//     wf6:frame-changed {frame} — tracks the displayed frame tag (display only).
//   emits (document):
//     wf6:route-polyline {ok, status?, vehicle_class, segment_ids?,
//                         origin_xy?, destination_xy?, error_code?}
//       — integrator-style consumers draw/clear the map polyline from this.
//         segment_ids are in route order when ok; empty/absent on failure.
//   Existing events are neither re-emitted nor consumed elsewhere.
//
// Base URL: window.__WF6_ROUTING_BASE ?? "http://127.0.0.1:8502" (the demo
// launcher's routing port). When the API is unreachable the selector degrades
// to a designed absence (all classes disabled with an explanatory tooltip);
// nothing is fabricated.

const ROUTING_BASE =
  globalThis.__WF6_ROUTING_BASE ?? "http://127.0.0.1:8502";

const CLASSES = [
  { key: "passenger_car", label: "Passenger car" },
  { key: "emergency_heavy", label: "Emergency/heavy" },
  { key: "bus_truck", label: "Bus/truck" },
];
const BUS_TRUCK_KEY = "bus_truck";
const BUS_TRUCK_FALLBACK_REASON =
  "no published stationary-depth wading limit exists";

// Refusal-code → operator banner text, mapped 1:1 from api.py codes.
const REFUSAL_TEXT = {
  scientific_gate_not_accepted:
    "routing refused: scientific gate not accepted (G1 FAIL)",
  vehicle_policy_unavailable: "routing refused: no cited vehicle policy for this class",
  snap_limit_exceeds_server_policy:
    "routing refused: requested snap limit exceeds server policy",
  depth_product_unavailable: "routing refused: realized depth product unavailable",
};

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

async function fetchJson(url, options) {
  let response;
  try {
    const override = globalThis.__wf6_FETCH_OVERRIDE;
    response =
      typeof override === "function"
        ? await override(url, options)
        : await fetch(url, { cache: "no-store", ...options });
  } catch (err) {
    throw new Error(`${url}: ${err instanceof Error ? err.message : String(err)}`);
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // non-JSON body falls through to the generic status message
  }
  if (!response.ok) {
    const err = new Error(
      (payload && payload.error && (payload.error.detail || payload.error.code || payload.error)) ||
        payload?.message ||
        `${url} failed (${response.status})`
    );
    err.payload = payload;
    err.status = response.status;
    throw err;
  }
  return payload;
}

function firstLineCoords(geometry) {
  // First part's coordinate list of a GeoJSON LineString/MultiLineString.
  if (!geometry || typeof geometry !== "object") return null;
  if (geometry.type === "LineString" && Array.isArray(geometry.coordinates)) {
    return geometry.coordinates;
  }
  if (
    geometry.type === "MultiLineString" &&
    Array.isArray(geometry.coordinates) &&
    Array.isArray(geometry.coordinates[0])
  ) {
    return geometry.coordinates[0];
  }
  return null;
}

function formatBand(band) {
  return Array.isArray(band) && band.length === 2 ? `${band[0]}–${band[1]} cm` : "—";
}

// ------------------------------------------------------------------- styles

const ROUTING_CSS = `
.wf6-routing{ display:flex; align-items:center; gap:8px; padding:7px 12px;
  border-bottom:1px solid var(--line); flex-wrap:wrap; }
.wf6-routing-label{ font-size:11px; color:var(--ink-faint); letter-spacing:0.08em; text-transform:uppercase; }
.wf6-routing .wf6-seg{ display:inline-flex; border:1px solid var(--line); border-radius:999px; overflow:hidden; }
.wf6-routing .wf6-seg button{
  border:none; background:transparent; color:var(--ink-dim); font-size:11px;
  padding:3px 10px; cursor:pointer;
}
.wf6-routing .wf6-seg button:hover:not(:disabled){ color:var(--ink); }
.wf6-routing .wf6-seg button.on{ background:var(--panel-hi); color:var(--ink); }
.wf6-routing .wf6-seg button:disabled{ opacity:0.45; cursor:not-allowed; }
.wf6-route-result{ margin:0; padding:6px 12px; font-size:11px; line-height:1.55;
  color:var(--ink-dim); border-bottom:1px solid var(--line); max-height:180px; overflow-y:auto; white-space:normal; }
.wf6-route-result.is-error{ color:var(--surcharge); }
.wf6-route-result strong{ color:var(--ink); font-weight:600; }
.wf6-route-result ul{ margin:4px 0 0; padding-left:16px; }
.wf6-route-float{ position:absolute; top:var(--header-h,56px); right:8px; z-index:40;
  background:rgba(10,14,23,0.97); border:1px solid var(--line); border-radius:10px;
  padding:8px; width:300px; color:var(--ink-dim); font-size:12px; }
`;

function ensureStyles() {
  if (document.getElementById("wf6-routing-style")) return;
  const style = el("style");
  style.id = "wf6-routing-style";
  style.textContent = ROUTING_CSS;
  document.head.appendChild(style);
}

// ------------------------------------------------------------------ module

export class RouteAroundPanel {
  constructor({ base = ROUTING_BASE } = {}) {
    this.base = base.replace(/\/+$/, "");
    this.vehicleClass = null; // selected class key; null until a policy-backed one is chosen
    this.policyState = null; // realized /policies payload, or null when unreachable
    this.frameTag = null; // display-only tracking of wf6:frame-changed
    this.busy = false;
    this.root = null;
  }

  mount() {
    ensureStyles();
    if (globalThis.__WF6_ROUTING_PANEL) return globalThis.__WF6_ROUTING_PANEL;
    const strip = el("div", "wf6-routing");
    strip.setAttribute("aria-label", "Route around street");
    strip.appendChild(el("span", "wf6-routing-label", "route"));

    const seg = el("div", "wf6-seg");
    this.buttons = {};
    for (const definition of CLASSES) {
      const btn = el(
        "button",
        definition.key === this.vehicleClass ? "on" : "",
        definition.label
      );
      btn.type = "button";
      btn.dataset.wf6VehicleClass = definition.key;
      btn.disabled = true; // enabled only by the realized /policies payload
      btn.title = "loading policy…";
      btn.addEventListener("click", () => this.setClass(definition.key));
      this.buttons[definition.key] = btn;
      seg.appendChild(btn);
    }
    strip.appendChild(seg);

    this.resultBox = el("div", "wf6-route-result");
    this.resultBox.hidden = true;

    // Insert into the watchlist panel's sortbar area when present; otherwise a
    // floating fallback keeps the affordance reachable (designed, not silent).
    const panel = document.getElementById("watchlist");
    if (panel) {
      const sortbar = panel.querySelector(".wf6-sortbar");
      if (sortbar) sortbar.after(strip);
      else panel.prepend(strip);
      const statusline = panel.querySelector(".wf6-statusline");
      if (statusline) statusline.after(this.resultBox);
      else panel.appendChild(this.resultBox);
    } else {
      const float_ = el("div", "wf6-route-float");
      float_.appendChild(strip);
      float_.appendChild(this.resultBox);
      document.body.appendChild(float_);
    }
    this.root = strip;

    document.addEventListener("wf6:route-request", (event) => {
      this.handleRouteRequest(event.detail ?? {});
    });
    document.addEventListener("wf6:frame-changed", (event) => {
      const frame = event.detail && event.detail.frame;
      if (typeof frame === "string") this.frameTag = frame;
    });

    this.refreshPolicyState();
    globalThis.__WF6_ROUTING_PANEL = this;
    globalThis.__WF6_ROUTING_VEHICLE_CLASS = this.vehicleClass;
    return this;
  }

  // ------------------------------------------------- policy selector state

  async refreshPolicyState() {
    let payload = null;
    try {
      payload = await fetchJson(`${this.base}/policies`);
    } catch {
      payload = null; // designed absence below
    }
    this.policyState = payload && typeof payload === "object" ? payload : null;
    this.renderSelector();
  }

  renderSelector() {
    const policies = this.policyState?.policies;
    const availableClasses = new Set(
      Array.isArray(policies)
        ? policies
            .filter((entry) => entry && typeof entry.vehicle_class === "string")
            .map((entry) => entry.vehicle_class)
        : []
    );
    const blockedReason =
      this.policyState &&
      this.policyState.blocked_classes &&
      typeof this.policyState.blocked_classes[BUS_TRUCK_KEY] === "object"
        ? String(this.policyState.blocked_classes[BUS_TRUCK_KEY].reason ?? "")
        : null;
    for (const definition of CLASSES) {
      const btn = this.buttons[definition.key];
      const available = availableClasses.has(definition.key);
      if (definition.key === BUS_TRUCK_KEY) {
        // Blocked class: disabled with the policy's own reason as tooltip.
        // Negative-evidence wording is reserved strictly for SERVED
        // blocked_classes content; an unreachable policy degrades to the SAME
        // designed-absence wording the sibling classes use.
        btn.disabled = true;
        btn.title =
          blockedReason ??
          (this.policyState
            ? `${definition.label}: ${BUS_TRUCK_FALLBACK_REASON}`
            : `routing policy unavailable at ${this.base} — designed absence`);
        continue;
      }
      btn.disabled = !available;
      btn.title = available
        ? `route around using the ${definition.label.toLowerCase()} wading limit`
        : this.policyState
          ? "no cited policy configured for this class"
          : `routing policy unavailable at ${this.base} — designed absence`;
      if (!available && this.vehicleClass === definition.key) {
        this.vehicleClass = null;
        globalThis.__WF6_ROUTING_VEHICLE_CLASS = null;
      }
      btn.classList.toggle("on", this.vehicleClass === definition.key);
    }
  }

  setClass(key) {
    const btn = this.buttons[key];
    if (!btn || btn.disabled) return;
    this.vehicleClass = key;
    globalThis.__WF6_ROUTING_VEHICLE_CLASS = key;
    for (const [value, element] of Object.entries(this.buttons)) {
      element.classList.toggle("on", value === key);
    }
    this.showInfo(`vehicle class: ${btn.textContent} — click ⚡ on a street row`);
  }

  // ----------------------------------------------------------- result views

  show(kind, html) {
    this.resultBox.hidden = false;
    this.resultBox.classList.toggle("is-error", kind === "error");
    this.resultBox.replaceChildren(el("span", null, ""));
    this.resultBox.innerHTML = html;
  }

  escape(text) {
    const div = document.createElement("div");
    div.textContent = String(text);
    return div.innerHTML;
  }

  showInfo(text) {
    this.show("info", this.escape(text));
  }

  showError(text) {
    this.show("error", `⚠ ${this.escape(text)}`);
  }

  // ------------------------------------------------------- data resolution

  async currentWatchlist() {
    const frame = this.frameTag ?? "held";
    return fetchJson(`/api/watchlist?frame=${encodeURIComponent(frame)}`);
  }

  async segmentEndpoints(segmentId, sourceBlock) {
    // Already-served dashboard metadata only: series payloads need index+id,
    // flat payloads take segment_id alone (store selects its single snapshot).
    const url =
      sourceBlock && sourceBlock.kind === "frame" && Number.isFinite(sourceBlock.frame_index)
        ? `/api/segments?index=${Number(sourceBlock.frame_index)}&segment_id=${encodeURIComponent(segmentId)}`
        : `/api/segments?segment_id=${encodeURIComponent(segmentId)}`;
    const payload = await fetchJson(url);
    const feature = Array.isArray(payload?.features) ? payload.features[0] : null;
    const coords = firstLineCoords(feature?.geometry);
    if (!coords || coords.length < 2) {
      throw new Error("no served geometry for this segment");
    }
    const first = coords[0];
    const last = coords[coords.length - 1];
    return {
      origin: [Number(first[0]), Number(first[1])],
      destination: [Number(last[0]), Number(last[1])],
    };
  }

  async resolveSnapCap() {
    // Echoed from the server's realized health payload — never invented.
    const health = await fetchJson(`${this.base}/health`);
    const snap = health?.snap_policy;
    const cap = Number(snap?.server_max_snap_distance_m);
    if (snap?.status !== "available" || !Number.isFinite(cap) || cap <= 0) {
      throw new Error(
        "server snap policy unavailable — refusing to invent max_snap_distance_m"
      );
    }
    return cap;
  }

  buildForecastLead(sourceBlock, rows) {
    // A hindcast offset is NOT a forecast lead and must never be sent as one.
    // The forecast lead is read from the CURRENT PAYLOAD'S OWN rows (the
    // product contract guarantees a single lead per file; the source block
    // itself carries none on flat runs). Absent => omit => the API's own
    // refusal surfaces honestly.
    if (!sourceBlock || sourceBlock.lead_kind !== "forecast_lead") return null;
    const withLead = (Array.isArray(rows) ? rows : []).find((row) =>
      Number.isFinite(row?.lead_minutes)
    );
    return withLead ? Math.trunc(withLead.lead_minutes) : null;
  }

  // ------------------------------------------------------------ route flow

  async handleRouteRequest(detail) {
    const segmentId = Number(detail.segment_id);
    if (this.busy) {
      this.showError("a route request is already in flight");
      return;
    }
    if (!this.vehicleClass) {
      this.showError("select a vehicle class above before requesting a route");
      emit("wf6:route-polyline", { ok: false, error_code: "no_vehicle_class_selected" });
      return;
    }
    if (!Number.isFinite(segmentId)) {
      this.showError("row carries no usable segment id");
      emit("wf6:route-polyline", { ok: false, error_code: "invalid_segment" });
      return;
    }
    this.busy = true;
    try {
      this.showInfo(`resolving route around segment ${segmentId}…`);

      const watchPayload = await this.currentWatchlist();
      const rows = Array.isArray(watchPayload?.rows) ? watchPayload.rows : [];
      const sourceBlock =
        watchPayload?.source && typeof watchPayload.source === "object"
          ? watchPayload.source
          : null;
      const streetNameBySegment = new Map();
      for (const row of rows) {
        const name = typeof row.street_name === "string" ? row.street_name : null;
        if (!name) continue;
        for (const id of Array.isArray(row.segment_ids) ? row.segment_ids : []) {
          streetNameBySegment.set(Number(id), name);
        }
      }

      const cap = await this.resolveSnapCap();
      const { origin, destination } = await this.segmentEndpoints(segmentId, sourceBlock);
      const body = {
        origin,
        destination,
        vehicle_class: this.vehicleClass,
        max_snap_distance_m: cap,
      };
      const lead = this.buildForecastLead(sourceBlock, rows);
      if (lead !== null) body.forecast_lead_minutes = lead;

      let payload;
      try {
        payload = await fetchJson(`${this.base}/v1/route`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } catch (err) {
        const code = err.payload?.error?.code;
        if (code && REFUSAL_TEXT[code]) {
          this.showError(REFUSAL_TEXT[code]);
          emit("wf6:route-polyline", { ok: false, error_code: code, vehicle_class: this.vehicleClass });
          return;
        }
        // Network/CORS failure or unmapped code: designed error, never a fake
        // route; unmapped API codes surface with their own detail text.
        const detail = err.payload?.error?.detail;
        this.showError(
          code
            ? `routing refused: ${code}${detail ? ` — ${detail}` : ""}`
            : `routing API unreachable at ${this.base} — no route fabricated`
        );
        emit("wf6:route-polyline", {
          ok: false,
          error_code: code ?? "network_unreachable",
          vehicle_class: this.vehicleClass,
        });
        return;
      }

      if (payload?.status === "ok" && payload.route && Array.isArray(payload.route.segments)) {
        const segments = payload.route.segments;
        const avoided = Array.isArray(payload.avoided_segments) ? payload.avoided_segments : [];
        const items = segments.map((segment) => {
          const name = typeof segment.name === "string" && segment.name
            ? segment.name
            : streetNameBySegment.get(Number(segment.segment_id));
          return `<li>#${this.escape(segment.segment_id)} ${this.escape(name ?? "unnamed")} · ${this.escape(formatBand(segment.depth_band_cm))} · ${this.escape(String(segment.flood_status ?? "?"))}</li>`;
        });
        const avoidedItems = avoided.map((item) => {
          const joined = streetNameBySegment.get(Number(item.segment_id));
          const suffix = joined ? ` (${this.escape(joined)})` : ""; // join only from current rows
          return `<li>avoided #${this.escape(item.segment_id)}${suffix} · ${this.escape(formatBand(item.depth_band_cm))} · ${this.escape(String(item.reason ?? ""))}</li>`;
        });
        this.show(
          "info",
          `<strong>flood-safe route</strong> · ${this.escape(String(Math.round(payload.route.distance_m)))} m · ${segments.length} segment(s)` +
            `<ul>${items.join("")}</ul>` +
            (avoidedItems.length ? `<ul>${avoidedItems.join("")}</ul>` : "")
        );
        emit("wf6:route-polyline", {
          ok: true,
          status: "ok",
          vehicle_class: payload.vehicle_class ?? this.vehicleClass,
          segment_ids: segments.map((segment) => Number(segment.segment_id)),
          origin_xy: payload.origin ? [payload.origin.x_m, payload.origin.y_m] : origin,
          destination_xy: payload.destination
            ? [payload.destination.x_m, payload.destination.y_m]
            : destination,
        });
        return;
      }

      if (payload?.status === "disconnected") {
        const text =
          payload.reason === "source_graph_disconnected"
            ? "origin/destination graphs disconnected — no route exists"
            : "no flood-safe route around this street at the selected limit";
        this.showError(text);
        emit("wf6:route-polyline", {
          ok: false,
          status: "disconnected",
          reason: payload.reason,
          vehicle_class: this.vehicleClass,
        });
        return;
      }
      this.showError(`unexpected route payload status: ${String(payload?.status)}`);
      emit("wf6:route-polyline", { ok: false, error_code: "unexpected_payload" });
    } catch (err) {
      this.showError(err instanceof Error ? err.message : String(err));
      emit("wf6:route-polyline", { ok: false, error_code: "resolution_failed" });
    } finally {
      this.busy = false;
    }
  }
}

export function bootRouting(opts) {
  if (globalThis.__WF6_ROUTING_PANEL) return globalThis.__WF6_ROUTING_PANEL;
  return new RouteAroundPanel(opts).mount();
}

// Auto-mount on import — same pattern as watchlist.js; the integrator wires
// the dashboard by adding this module to the import graph.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => bootRouting(), { once: true });
} else {
  bootRouting();
}
