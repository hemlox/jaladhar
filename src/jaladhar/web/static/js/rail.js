// Right rail: the two hero stats, the selected-segment panel and its
// provenance line, and the drain-network status note. Every value is read
// from realized payloads — no metric is typed anywhere in this module.
//
// WF-6 L6 additions (N9–N12, B11 assist):
//   N9  — depths above 100 cm render as metres with two decimals; conversion
//         is presentation only, values still come from the realized product.
//   N10 — context line under the selected name: nearest landmark + ward.
//   N11 — status badge with impact tooltip; run-label expands bare "replay".
//   N12 — drain-panel diagnostics accordion; the per-value provenance line
//         stays OUTSIDE it and always visible (rule 3).
//   B11 — legend's fourth range renders an explicit bound instead of "any".

import { loadSegmentRow, loadSeriesSegmentRow } from "./data.js";
import { Timeline } from "./timeline.js";
// Same DEPTH ramp table render.js strokes with — imported (not copied) so the
// B11 bound tracks re-derived band boundaries live. No cycle: render.js
// imports data.js only.
import { DEPTH } from "./render.js";

// Ward join asset built by jaladhar.web.wards from realized BBMP boundaries;
// served by the integrator at this URL. Designed absence: any failure here
// (404 until the integrator lands it, network error, decode error) omits the
// ward component of the context line silently — never a placeholder ward.
const WARD_JOIN_URL = "/api/context/segment_ward_2022.csv.gz";

// N9 threshold: at or below this many centimetres the reading stays in cm;
// above it the inspector and hero render metres with two decimals. The raw
// centimetre value always remains available (title attribute / traceability).
const METRE_THRESHOLD_CM = 100;

// B11: the d3/d4 boundary in centimetres, derived LIVE from the renderer's
// DEPTH ramp (the same table app.assertDepthRule pins to the contract at
// load, and whose bounds render.applyRampBands re-derives from realized
// product data mid-session). Reading it through the shared module instance
// keeps the bound truthful both before and after a ramp re-derivation.
function d4FloorCm() {
  return Math.round(DEPTH[2].max * 100);
}

// N11 badge copy. The flood_status enum is frozen at {flooded, not_flooded,
// unknown}; per the product contract an "unknown" row is one whose modelled
// cells are absent (band_low_cm = band_high_cm = 0), so it maps to no_data.
const STATUS_BADGES = {
  flooded: {
    label: "Flooded — impassable risk for passenger vehicles",
    tooltip:
      "Impact thresholds differ for heavy vehicles and emergency response; street routing is pending.",
  },
  not_flooded: {
    label: "Not flooded at this instant",
    tooltip:
      "This segment's modelled cells show no flooding at the displayed valid time.",
  },
};
const NO_DATA_BADGE = {
  label: "No modeled cells on this segment",
  tooltip:
    "The realized product holds no modelled cells on this segment, so no depth claim is made.",
};

// N11 run-label expansion: bare "replay" carries no meaning to an operator;
// the expanded form names the data stream, the tooltip states what is known.
const REPLAY_LABEL = "Data Stream: Historical Replay";
const REPLAY_TOOLTIP =
  "Rainfall and state known for the whole window; times are hindcast valid times, not forecast leads.";

// N9 presentation formatter shared by the inspector depth row and the hero.
// Returns null when the value is absent so callers can fall back to "—".
function formatDepthCm(cm) {
  if (cm == null || cm === "") return null;
  const v = Number(cm);
  if (!Number.isFinite(v)) return null;
  // Unit conversion only — the number itself is untouched product output.
  return v > METRE_THRESHOLD_CM ? (v / 100).toFixed(2) + " m" : String(v) + " cm";
}

export class Rail {
  constructor(elements) {
    this.el = elements;
    this.state = null;      // last state payload seen (N12 schema version)
    this._wardJoin = null;  // lazy cached promise (N10)
    this._diag = null;      // diagnostics accordion refs (N12)
    this._selectSeq = 0;    // selection token guarding async DOM writes (N10)
  }

  setRunLabel(state) {
    this.state = state; // kept for the N12 diagnostics schema line
    const series = state.series;
    const snapshot = (state.snapshots ?? [])[0] ?? {};
    const label = series?.product_label ?? snapshot.product_label;
    const isReplay =
      series?.forcing_kind === "historical_replay" ||
      snapshot.forcing_kind === "historical_replay";
    const bits = [];
    const runId = series?.run_id ?? snapshot.run_id;
    if (label) bits.push(String(label));
    else if (runId) bits.push(String(runId)); // no producer label: the run id IS the honest provenance
    // N11: expand the bare "replay" token into a named data stream.
    if (isReplay) bits.push(REPLAY_LABEL);
    this.el.runLabel.textContent = bits.length ? bits.join(" · ") : "no run loaded";
    this.el.runLabel.title = isReplay ? REPLAY_TOOLTIP : "";
    const dot = this.el.statusDot;
    dot.className = "status-dot " + (state.status === "ready" ? "replay" : "error");
    dot.title = state.status === "ready" ? "realized product loaded — not a live feed" : state.status;
    const gate = state.scientific_gate;
    this.el.gateNote.textContent =
      gate && gate.g1_status ? `G1 ${gate.g1_status}` : "";
    // WF-6 M3: the flagged unexplained result leads the rail when the active
    // run carries one; runs without it render nothing (designed absence).
    this.renderOpenAnomaly(state);
    // N12: build once so the schema line exists even if drains never load.
    this.ensureDiagAccordion();
  }

  // WF-6 M3 OPEN ANOMALY card. A flagged-but-unexplained measured result is
  // worth showing: owner rule — a clean-looking screen must not hide it. The
  // card is the FIRST rail section, always visible, NEVER inside the N12
  // diagnostics accordion. Every value is read from payload.open_anomaly,
  // which the backend sources verbatim from the active run manifest; nothing
  // is typed here. Idempotent across repeated setRunLabel calls, and present
  // in BOTH boot paths because setRunLabel runs before the series/flat branch.
  renderOpenAnomaly(state) {
    // The rail <aside> holds every panel section; this module's element refs
    // all live in the topbar/panels, so resolve the container by its own
    // class (exactly one .rail exists in index.html).
    const railEl = document.querySelector("aside.rail");
    if (!railEl) return;
    const existing = railEl.querySelector(":scope > .wf6-anomaly");
    const anomaly = state?.open_anomaly;
    if (!anomaly || anomaly.status !== "OPEN") {
      if (existing) existing.remove(); // designed absence for older products
      return;
    }
    const heading = document.createElement("h3");
    heading.className = "panel-heading";
    heading.textContent = "OPEN ANOMALY";
    const body = document.createElement("p");
    body.className = "wf6-anomaly-body";
    body.textContent =
      Number(anomaly.n_segments_gt300cm) +
      " flooded segments read " + anomaly.range_cm[0] + "–" + anomaly.range_cm[1] +
      " cm even after storage-water exclusion — cause not yet identified.";
    const policy = document.createElement("p");
    policy.className = "wf6-anomaly-policy";
    policy.textContent = String(anomaly.policy ?? "");
    const prov = document.createElement("p");
    prov.className = "provenance-line";
    prov.textContent = "source " + (anomaly.source_manifest ?? "unavailable");
    const card = existing ?? document.createElement("section");
    card.className = "panel wf6-anomaly";
    card.replaceChildren(heading, body, policy, prov);
    if (!existing) railEl.insertBefore(card, railEl.firstChild);
  }

  // The peak claim, pulled out of the curve deliberately: measured count,
  // its valid time in UTC and IST (UTC+5:30 — a unit conversion), and the
  // manifest path it came from.
  setPeakClaim(series) {
    const host = document.getElementById("peak-claim");
    if (!host) return;
    host.replaceChildren();
    if (!series?.peak) return;
    const peak = series.peak;
    const istMatch = /T(\d{2}):(\d{2})/.exec(peak.valid_time_utc ?? "");
    let ist = "";
    if (istMatch) {
      const minutes = Number(istMatch[1]) * 60 + Number(istMatch[2]) + 330;
      const hh = String(((minutes / 60) | 0) % 24).padStart(2, "0");
      const mm = String(minutes % 60).padStart(2, "0");
      ist = ` (${hh}:${mm} IST)`;
    }
    const line = document.createElement("p");
    line.className = "hero-caption";
    line.textContent =
      "PEAK " + peak.n_flooded + " segments flooded · " +
      Timeline.formatValidTime(peak.valid_time_utc) + ist;
    const prov = document.createElement("p");
    prov.className = "provenance-line";
    prov.textContent = "source " + series.manifest_path;
    host.append(line, prov);
  }

  // Hero stats computed from a REALIZED frame only — never from interpolated
  // values. count = flood_status==='flooded'; deepest = max band_high_cm over
  // modelled segments, located via the road network's own name table.
  updateStats(frame, roads) {
    let flooded = 0;
    let deepestIdx = -1;
    let deepestCm = -1;
    for (let i = 0; i < frame.n; i++) {
      if (frame.codes[i] === 1) flooded++;
      if (frame.codes[i] !== 2 && frame.high[i] > deepestCm) {
        deepestCm = frame.high[i];
        deepestIdx = i;
      }
    }
    this.el.statFlooded.textContent = String(flooded);
    if (deepestIdx >= 0) {
      this.setDeepestStat(deepestCm); // N9 formatting
      const name = roads?.nameOfSegment(deepestIdx);
      const id = roads ? roads.segmentIds[deepestIdx] : null;
      this.el.statDeepestLoc.textContent = name
        ? "deepest, " + name
        : id != null
          ? "deepest, unnamed segment " + id
          : "deepest segment";
    } else {
      this.setDeepestStat(null);
      this.el.statDeepestLoc.textContent = "";
    }
  }

  // N9 hero rendering. Above the metre threshold the full formatted value
  // ("3.69 m") lives in #stat-deepest and the static cm unit span is emptied;
  // at or below it the plain centimetres return beside the unit span. The raw
  // centimetres stay on the title attribute either way (traceability). The
  // unit span belongs to index.html; adjusting its text here keeps the metre
  // rule readable as ONE value without touching markup outside my ownership.
  setDeepestStat(cm) {
    const el = this.el.statDeepest;
    const unit = el.parentElement?.querySelector(".hero-unit") ?? null;
    if (cm == null || !Number.isFinite(Number(cm))) {
      el.textContent = "—";
      el.removeAttribute("title");
      if (unit) unit.textContent = "cm";
      return;
    }
    if (Number(cm) > METRE_THRESHOLD_CM) {
      // Presentation conversion of the realized band_high_cm — see comment
      // on METRE_THRESHOLD_CM; nothing here edits the underlying value.
      el.textContent = (Number(cm) / 100).toFixed(2) + " m";
      if (unit) unit.textContent = "";
    } else {
      el.textContent = String(cm);
      if (unit) unit.textContent = "cm";
    }
    el.title = cm + " cm";
  }

  // N10 geometry: approximate the selected segment's midpoint from the road
  // network's own LOD buffers, reachable through rail's existing `roads`
  // argument (segPartRanges chain warmed by app.refreshLines). No accessor is
  // added to data.js; the first vertex of the segment's first part and the
  // last vertex of its last part average to a nearest-landmark-grade point.
  segmentMidpoint(roads, segmentId) {
    try {
      const index = roads?.indexOfSegment(segmentId);
      if (index == null || index < 0 || index >= (roads.nSegments ?? 0)) return null;
      for (const [lod, ranges] of roads.segPartCache ?? []) {
        const lines = roads.lods.get(lod);
        if (!lines || !ranges) continue;
        if (index + 1 >= ranges.segOffsets.length) continue;
        // Zero-part segments must skip this LOD: reading partIds otherwise
        // would return a NEIGHBOURING segment's part (silent wrong midpoint).
        if (ranges.segOffsets[index + 1] === ranges.segOffsets[index]) continue;
        const firstPart = ranges.partIds[ranges.segOffsets[index]];
        const lastPart = ranges.partIds[ranges.segOffsets[index + 1] - 1];
        const start = lines.offsets[firstPart];
        const end = lines.offsets[lastPart + 1] - 1;
        if (!(end >= start)) continue;
        const x0 = lines.coords[start * 2];
        const y0 = lines.coords[start * 2 + 1];
        const x1 = lines.coords[end * 2];
        const y1 = lines.coords[end * 2 + 1];
        if (![x0, y0, x1, y1].every(Number.isFinite)) continue;
        return [(x0 + x1) / 2, (y0 + y1) / 2];
      }
    } catch {
      // geometry unavailable — degrade to ward-only context
    }
    return null;
  }

  // N10: nearest landmark by Euclidean distance in EPSG:32643 world metres
  // (projected CRS, so the metric is meaningful). Landmarks ride along in the
  // basemap meta the RoadNetwork was built from — same realized payload as
  // /api/basemap/meta, no second fetch needed.
  nearestLandmark(roads, midpoint) {
    if (!midpoint) return null;
    let bestName = null;
    let bestD = Infinity;
    for (const lm of roads?.meta?.landmarks ?? []) {
      if (typeof lm?.x !== "number" || typeof lm?.y !== "number") continue;
      const d = (lm.x - midpoint[0]) ** 2 + (lm.y - midpoint[1]) ** 2;
      if (d < bestD) {
        bestD = d;
        bestName = lm.name ?? null;
      }
    }
    return bestName;
  }

  // N10: fetch + parse the gzipped segment→ward join exactly ONCE per page,
  // then answer lookups from the cached map. Any failure resolves to null and
  // the ward component is omitted silently (designed absence).
  wardJoin() {
    if (!this._wardJoin) {
      this._wardJoin = (async () => {
        if (typeof DecompressionStream === "undefined") return null;
        const res = await fetch(WARD_JOIN_URL, { cache: "no-store" });
        if (!res.ok || !res.body) return null;
        const text = await new Response(
          res.body.pipeThrough(new DecompressionStream("gzip"))
        ).text();
        const rows = text.split(/\r?\n/).filter((line) => line.trim() !== "");
        if (rows.length < 2) return null;
        const header = parseCsvLine(rows[0]).map((h) => h.trim());
        const colId = header.indexOf("segment_id");
        const colNo = header.indexOf("ward_no");
        const colName = header.indexOf("ward_name");
        if (colId < 0 || colNo < 0 || colName < 0) return null;
        const map = new Map();
        for (let i = 1; i < rows.length; i++) {
          const fields = parseCsvLine(rows[i]);
          const id = fields[colId]?.trim();
          const no = fields[colNo]?.trim();
          const name = fields[colName]?.trim();
          if (!id || !no || !name) continue;
          map.set(id, { number: no, name });
        }
        return map.size ? map : null;
      })().catch(() => null);
    }
    return this._wardJoin;
  }

  // N10 line format: "<name>, near <landmark> (Ward <number> <ward_name>)" —
  // components drop out individually when missing; an empty result removes
  // the element entirely rather than shipping an empty line.
  static contextText(parts) {
    let text = parts.name ?? "";
    if (parts.landmark) text += (text ? ", near " : "near ") + parts.landmark;
    if (parts.ward) {
      text +=
        (text ? " " : "") +
        "(Ward " + parts.ward.number + " " + parts.ward.name + ")";
    }
    return text.trim();
  }

  renderContextLine(el, parts) {
    const text = Rail.contextText(parts);
    if (text) {
      el.textContent = text;
    } else {
      el.remove();
    }
  }

  async selectSegment(segmentId, context, roads, drainsMeta) {
    const seq = ++this._selectSeq; // invalidates stale async continuations
    const body = this.el.selectedBody;
    body.replaceChildren();
    body.classList.remove("panel-open");
    // force reflow so the 240ms panel-open animation replays (guide §4)
    void body.offsetWidth;
    body.classList.add("panel-open");
    const placeholder = document.createElement("p");
    placeholder.className = "hero-caption";
    placeholder.textContent = "resolving segment " + segmentId + "…";
    body.appendChild(placeholder);
    try {
      const feature =
        context.mode === "series"
          ? await loadSeriesSegmentRow(context.index, segmentId)
          : await loadSegmentRow(context.lead, segmentId);
      if (seq !== this._selectSeq) return;
      body.replaceChildren();
      if (!feature || !feature.properties) {
        placeholder.textContent = "No realized product row for segment " + segmentId + ".";
        return;
      }
      const props = feature.properties;
      const index = roads.indexOfSegment(segmentId);
      const name = roads.nameOfSegment(index) ?? props.name ?? null;

      const heading = document.createElement("p");
      heading.className = "selected-name";
      heading.textContent = name ?? "Unnamed segment " + segmentId;
      body.appendChild(heading);

      // N10 context line sits directly under the name. The landmark half is
      // computable immediately (local basemap meta); the ward half fills in
      // when the one-time join lookup settles.
      const ctxLine = document.createElement("p");
      ctxLine.className = "context-line";
      body.appendChild(ctxLine);
      const ctxParts = {
        name: name ?? null,
        landmark: this.nearestLandmark(roads, this.segmentMidpoint(roads, segmentId)),
        ward: null,
      };
      this.renderContextLine(ctxLine, ctxParts);
      this.wardJoin().then((join) => {
        if (seq !== this._selectSeq) return;
        ctxParts.ward = join?.get(String(segmentId)) ?? null;
        this.renderContextLine(ctxLine, ctxParts);
      }).catch(() => {}); // join failure already resolves null inside

      const dl = document.createElement("dl");
      dl.className = "kv";
      const when =
        context.mode === "series"
          ? Timeline.formatValidTime(feature.valid_time_utc ?? props.valid_time_utc)
          : (props.forecast_lead_minutes ?? 0) + " min (forecast rainfall)";
      // N9: each depth bound is formatted independently (>100 cm → metres,
      // two decimals). Unit conversion is presentation only — band_low_cm /
      // band_high_cm are passed through from the realized row unmodified and
      // their raw pair stays on the dd's title attribute for traceability.
      const lowText = formatDepthCm(props.band_low_cm) ?? "—";
      const highText = formatDepthCm(props.band_high_cm) ?? "—";
      const rows = [
        ["depth", lowText + "–" + highText],
        ["status", props.flood_status ?? "unknown"],
        ["valid time", when],
        ["surcharge node",
          drainsMeta?.surcharge?.available
            ? "see measured events"
            : "none measured"],
      ];
      for (const [k, v] of rows) {
        const dt = document.createElement("dt");
        dt.textContent = k;
        const dd = document.createElement("dd");
        if (k === "depth") {
          dd.textContent = v;
          dd.title =
            (props.band_low_cm ?? "?") + "–" + (props.band_high_cm ?? "?") + " cm";
        } else if (k === "status") {
          // N11: labelled badge with impact tooltip replaces the bare pill.
          dd.appendChild(this.statusBadge(v));
        } else {
          dd.textContent = String(v);
        }
        dl.append(dt, dd);
      }
      body.appendChild(dl);

      // Rule 3: the per-value provenance line stays VISIBLE in the inspector —
      // appended LAST so it sits at panel bottom, styled small/faint via
      // .provenance-line, never inside the N12 details element, never hidden.
      const prov = document.createElement("p");
      prov.className = "provenance-line";
      prov.textContent = "source " + (props.source_manifest_path ?? "unavailable");
      body.appendChild(prov);
    } catch (err) {
      if (seq !== this._selectSeq) return;
      body.replaceChildren();
      const p = document.createElement("p");
      p.className = "hero-caption";
      p.textContent = String(err instanceof Error ? err.message : err);
      body.appendChild(p);
    }
  }

  // N11: the frozen enum maps to badge copy; "unknown" means the contract's
  // unmodelled row shape (band 0/0), i.e. genuinely no modelled cells.
  statusBadge(status) {
    const key = String(status ?? "unknown");
    const spec =
      key === "unknown"
        ? NO_DATA_BADGE
        : STATUS_BADGES[key] ?? {
            label: key.replaceAll("_", " "),
            tooltip: "",
          };
    const badge = document.createElement("span");
    badge.className =
      "status-badge " + (key === "unknown" ? "no_data" : key);
    badge.textContent = spec.label;
    if (spec.tooltip) badge.title = spec.tooltip;
    badge.dataset.status = key; // raw enum stays inspectable
    return badge;
  }

  clearSelection() {
    this._selectSeq++; // cancel any pending context-line continuation
    this.el.selectedBody.replaceChildren();
    const p = document.createElement("p");
    p.className = "hero-caption";
    p.textContent = "Click a street to inspect its realized product row.";
    this.el.selectedBody.appendChild(p);
  }

  setDrainStatus(meta) {
    // N12: wrap the drain panel content in the collapsed diagnostics
    // accordion before filling it (idempotent).
    this.ensureDiagAccordion();
    if (!meta || !meta.available) {
      this.el.drainStatus.textContent =
        "Drain geometry unavailable at snapshot time. The layer stays off rather than inventing a network.";
      this.el.drainFingerprint.textContent = "";
      return;
    }
    const counts = meta.edge_source_counts ?? {};
    this.el.drainStatus.textContent =
      meta.reason ??
      ("rendering " + meta.n_edges + " edges from the snapshot taken " + meta.snapshot_utc);
    this.el.drainStatus.textContent +=
      counts.synthesised != null
        ? " Observed reaches solid; synthesised connectors dashed (" +
          counts.observed + " observed / " + counts.synthesised + " synthesised)."
        : "";
    const fp = meta.graph_fingerprint_at_read;
    const status = meta.manifest_status_at_read;
    this.el.drainFingerprint.textContent =
      "graph " + (fp ? fp.slice(0, 16) + "…" : "<fingerprint absent at read>") +
      (status ? " · manifest status: " + status : "");
  }

  // N12: move the drain-status/fingerprint nodes plus a NEW product-schema
  // line into a collapsed-by-default <details class="wf6-diag"> inside the
  // Drain network panel (index.html is read-only; restructuring happens in
  // the DOM here). The inspector's provenance line is NOT part of this —
  // rule 3 keeps it visible at all times.
  ensureDiagAccordion() {
    if (this._diag) return this._diag;
    const host = this.el.drainStatus;
    const panel = host?.parentElement;
    if (!panel || !this.el.drainFingerprint) return null;
    const details = document.createElement("details");
    details.className = "wf6-diag"; // collapsed by default: no open attribute
    const summary = document.createElement("summary");
    summary.textContent = "Diagnostics";
    const schemaLine = document.createElement("p");
    schemaLine.className = "diag-schema";
    details.append(summary, schemaLine);
    panel.insertBefore(details, host);
    details.append(host, this.el.drainFingerprint); // moves both nodes inside
    this._diag = { details, schemaLine };
    this.updateSchemaLine();
    return this._diag;
  }

  // N12: schema version comes from the state payload served by the backend
  // (contract.version), stored at setRunLabel — never typed client-side.
  updateSchemaLine() {
    if (!this._diag) return;
    const version = this.state?.contract?.version;
    this._diag.schemaLine.textContent =
      version != null && version !== ""
        ? "product schema: " + version
        : "product schema: unavailable";
  }

  // B11 assist: the legend's fourth range arrives as the sentinel "any"
  // (DEPTH[3].max = Infinity). An upper bound must be a bound, so translate
  // it to the explicit open interval "> <d3/d4 floor> cm".
  //
  // CROSS-WRITER SEAM (V8), RETIRED BY THE INTEGRATOR: render.js
  // updateLegendRanges() still writes all four range spans directly when a
  // frame's realized band bounds land (render.js is outside lane ownership),
  // but app.js now re-emits every range through THIS method whenever the
  // ramp telemetry reports newly applied bounds — both writers converge
  // here. The interim MutationObserver sentinel guard is therefore removed:
  // there is exactly one translation path left, and no observer second-
  // guessing DOM writes.
  showLegendRange(id, text) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text === "any" ? "> " + d4FloorCm() + " cm" : text;
    this.ensureLegendFootnote();
  }

  // B11 footnote: appended via DOM (index.html is read-only) only when the
  // legend exists; idempotent across the four showLegendRange calls.
  ensureLegendFootnote() {
    const legend = document.getElementById("legend");
    if (!legend || legend.querySelector(".legend-footnote")) return;
    const note = document.createElement("p");
    note.className = "legend-note legend-footnote";
    note.textContent = "Bands from realized distribution; boundaries re-derive per product.";
    legend.appendChild(note);
  }
}

// Minimal quoted-field-aware CSV row splitter for the ward join (N10): ward
// names may contain commas, so naive split(",") would corrupt those rows.
function parseCsvLine(line) {
  const out = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}
