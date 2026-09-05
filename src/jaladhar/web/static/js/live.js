// Live-refresh: REAL simulation tile trigger + realized polling.
// Every displayed value is fetched from /api/forecast/* — no literal metrics.
// Spec: tile HARDCODED server-side '1200,2055,1200,2078' — client never sends scope.

const TILE_LABEL = "1200,2055,1200,2078"; // display only, never sent
const POLL_MS = 2000;
const CLIENT_TIMEOUT_MS = 300_000;

async function getJson(url) {
  const r = await fetch(url, { cache: "no-store" });
  // try json even on error for honest error field
  let payload = null;
  try { payload = await r.json(); } catch { payload = null; }
  if (!r.ok) {
    const msg = payload && (payload.error || payload.message) ? (payload.error || payload.message) : `${url} failed (${r.status})`;
    const err = new Error(msg);
    err.status = r.status;
    err.payload = payload;
    err.headers = r.headers;
    throw err;
  }
  return payload;
}

function fmtTime(iso) {
  return iso ? String(iso) : "—";
}

function ensureHost() {
  let host = document.getElementById("live-panel");
  if (host) return host;
  // Create host as first rail section if rail exists, else under topbar
  const rail = document.querySelector("aside.rail");
  const topbar = document.querySelector(".topbar");
  host = document.createElement("section");
  host.id = "live-panel";
  host.className = "panel live-panel";
  host.innerHTML = `
    <h3 class="panel-heading">LIVE FORECAST <span class="live-tile-hint" title="Server-locked tile (G4 measured)">tile ${TILE_LABEL}</span></h3>
    <div id="live-status" class="live-status" aria-live="polite"></div>
  `;
  if (rail) rail.insertBefore(host, rail.firstChild);
  else if (topbar) topbar.after(host);
  else document.body.appendChild(host);
  return host;
}

function renderIdle(statusEl) {
  // One honest line (owner 2026-08-27): the previous idle card repeated the
  // same sentence twice. No live rain feed exists tonight — say it once.
  statusEl.innerHTML = `
    <p class="live-idle">No live run yet. RUN FRESH SIMULATION triggers a real 3-hour tile nowcast; no live rain feed is wired tonight.</p>
  `;
}

function renderQueued(job) {
  const started = fmtTime(job.started_utc);
  return `
    <div class="live-card live-queued">
      <p class="live-state-label"><span class="live-dot" aria-hidden="true"></span>queued</p>
      <p class="live-meta">job <code>${job.job_id}</code> · started ${started} · tile ${job.tile ?? TILE_LABEL}</p>
      <p class="live-note">Waiting for solver… polling <code>/api/forecast/status</code> every 2 s.</p>
    </div>
  `;
}

function renderRunning(job) {
  const started = fmtTime(job.started_utc);
  // REAL status text tied to manifest_status/status, never fake percent
  const stateText = job.manifest_status ? `${job.status} — manifest ${job.manifest_status}` : `${job.status} — solver timestepping…`;
  const pid = job.pid ? ` · pid ${job.pid}` : "";
  return `
    <div class="live-card live-running">
      <p class="live-state-label"><span class="live-dot" aria-hidden="true"></span>${stateText}</p>
      <p class="live-meta">job <code>${job.job_id}</code> · started ${started}${pid} · tile ${job.tile ?? TILE_LABEL}</p>
      <p class="live-note">Running — polling <code>/api/forecast/status?job_id=${job.job_id}</code> every 2 s (timeout ${CLIENT_TIMEOUT_MS/1000}s).</p>
    </div>
  `;
}

function renderCompleted(job) {
  const completed = fmtTime(job.completed_utc || job.finished_utc);
  const valid = fmtTime(job.valid_time_utc);
  const wall = job.wall_clock_s != null ? `${job.wall_clock_s}s` : (job.wall_clock_min != null ? `${job.wall_clock_min} min` : "—");
  const steps = job.steps != null ? String(job.steps) : "—";
  const manifestPath = job.manifest_path ?? "—";
  const outDir = job.out_dir ?? "—";
  const listing = Array.isArray(job.out_dir_listing_hint) ? job.out_dir_listing_hint.join(", ") : (job.out_dir_exists ? "artifacts present" : "");
  const g4 = job.g4_passes != null ? (job.g4_passes ? "G4 PASS (≤10 min)" : "G4 FAIL") : "";
  return `
    <div class="live-card live-completed">
      <p class="live-state-label">completed</p>
      <p class="live-meta">job <code>${job.job_id}</code> · completed ${completed} · valid ${valid}</p>
      <dl class="kv">
        <dt>wall clock</dt><dd>${wall}</dd>
        <dt>steps</dt><dd>${steps}</dd>
        <dt>manifest</dt><dd><code>${manifestPath}</code></dd>
        <dt>out dir</dt><dd><code>${outDir}</code></dd>
      </dl>
      ${listing ? `<p class="live-listing">out_dir: ${listing}</p>` : ""}
      ${g4 ? `<p class="live-g4">${g4}</p>` : ""}
      <p class="live-scope-note scope-emphasis">map continues to serve frames_v2 series; fresh artifacts at <code>${outDir}</code> — raster-only, not hot-reloaded into the map.</p>
      <p class="provenance-line">source ${manifestPath} · tile ${TILE_LABEL}</p>
    </div>
  `;
}

function renderFailed(job) {
  const snippetRaw = job.error ?? job.error_message ?? job.reason ?? job.manifest_error ?? job.manifest?.error ?? null;
  const snippet = snippetRaw != null ? String(snippetRaw).slice(0,600) : "unknown error";
  const manifestPath = job.manifest_path ?? job.manifestPath ?? "—";
  const outDir = job.out_dir ?? "—";
  return `
    <div class="live-card live-failed">
      <p class="live-state-label">failed</p>
      <p class="live-meta">job <code>${job.job_id}</code></p>
      <p class="live-error"><code>${snippet.replace(/</g,"&lt;")}</code></p>
      <p class="live-meta">manifest <code>${manifestPath}</code> · out <code>${outDir}</code></p>
    </div>
  `;
}

function renderTimeout(job) {
  return `
    <div class="live-card live-timeout">
      <p class="live-state-label">still running</p>
      <p class="live-meta">job <code>${job.job_id}</code> exceeded client ${CLIENT_TIMEOUT_MS/1000}s guard — server job continues. Check <code>/api/forecast/status?job_id=${job.job_id}</code>.</p>
      <p class="live-note">This is honest timeout, not a failure — solver may still be timestepping.</p>
    </div>
  `;
}

function setButtonState(btn, state, retryAfter) {
  // states: idle, pending, disabled
  if (!btn) return;
  if (state === "pending") {
    btn.disabled = true;
    btn.textContent = "RUNNING…";
    btn.title = "A forecast tile is running — polling…";
  } else if (state === "cooldown" || state === "singleflight") {
    btn.disabled = true;
    const secs = retryAfter != null ? `${retryAfter}s` : "";
    btn.textContent = secs ? `WAIT ${secs}` : "WAIT";
    btn.title = state === "cooldown" ? `Cooldown ${secs} remaining` : `Single-flight: another job running`;
  } else {
    btn.disabled = false;
    btn.textContent = "RUN FRESH SIMULATION";
    btn.title = "RE-RUN the 3 h forecast tile NOW — fresh compute of the demo forecast (tile locked server-side)";
  }
}

export function mountLiveController() {
  const btn = document.getElementById("btn-live-run");
  const host = ensureHost();
  const statusEl = host.querySelector("#live-status");
  if (!btn || !statusEl) return;

  let pollTimer = null;
  let timeoutTimer = null;
  let activeJobId = null;

  function clearPoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    if (timeoutTimer) clearTimeout(timeoutTimer);
    timeoutTimer = null;
  }

  async function poll(jobId) {
    try {
      const job = await getJson(`/api/forecast/status?job_id=${encodeURIComponent(jobId)}`);
      // authoritative manifest status already applied server-side
      if (job.status === "queued") {
        statusEl.innerHTML = renderQueued(job);
        setButtonState(btn, "pending");
      } else if (job.status === "running") {
        statusEl.innerHTML = renderRunning(job);
        setButtonState(btn, "pending");
      } else if (job.status === "completed") {
        clearPoll();
        statusEl.innerHTML = renderCompleted(job);
        setButtonState(btn, "idle");
      } else if (job.status === "failed") {
        clearPoll();
        statusEl.innerHTML = renderFailed(job);
        setButtonState(btn, "idle");
      } else {
        statusEl.innerHTML = `<p class="live-note">status: ${job.status} — polling…</p><pre class="live-raw">${JSON.stringify(job,null,2)}</pre>`;
      }
    } catch (err) {
      // honest error: show status endpoint remainder
      const code = err.status ?? "?";
      const msg = err.message ?? String(err);
      statusEl.innerHTML = `<p class="live-error">status poll failed (${code}): ${msg}</p><p class="live-note">poll <code>/api/forecast/status?job_id=${jobId}</code> — will retry.</p>`;
    }
  }

  async function trigger() {
    // debounce double-clicks and respect cpu_forced debounce window
    if(btn._cpuDebounce && Date.now() < btn._cpuDebounce) return;
    if(btn._lastTrigger && Date.now() - btn._lastTrigger < 800) return;
    btn._lastTrigger = Date.now();
    if (activeJobId && pollTimer) return; // single-flight client guard
    setButtonState(btn, "pending");
    statusEl.innerHTML = `<p class="live-note">triggering… POST /api/forecast/trigger (tile ${TILE_LABEL} server-locked)</p>`;
    try {
      const res = await fetch("/api/forecast/trigger", { method: "POST", cache: "no-store", headers: {"Content-Type":"application/json"}, body: JSON.stringify({}) });
      let payload = null;
      try { payload = await res.json(); } catch { payload = {}; }
      if (res.status === 202) {
        const jobId = payload.job_id;
        activeJobId = jobId;
        statusEl.innerHTML = renderQueued({ job_id: jobId, status: payload.status ?? "queued", started_utc: new Date().toISOString(), tile: TILE_LABEL });
        // start polling
        clearPoll();
        pollTimer = setInterval(() => poll(jobId), POLL_MS);
        poll(jobId);
        timeoutTimer = setTimeout(() => {
          clearPoll();
          // honest still-running note + remainder
          statusEl.innerHTML = renderTimeout({ job_id: jobId });
          // one final status fetch for remainder
          getJson(`/api/forecast/status?job_id=${encodeURIComponent(jobId)}`).then(j=>{
            const extra = document.createElement("pre");
            extra.className = "live-raw";
            extra.textContent = JSON.stringify(j,null,2);
            statusEl.appendChild(extra);
          }).catch(()=>{});
        }, CLIENT_TIMEOUT_MS);
        return;
      }
      // handle 409/429 with Retry-After
      const retryAfter = res.headers.get("Retry-After") || payload?.retry_after_seconds;
      if (res.status === 409) {
        const reasonCode = payload?.reason_code ?? payload?.reasonCode ?? "";
        const errText = payload?.error ?? payload?.message ?? JSON.stringify(payload);
        if(reasonCode === "cpu_forced_session"){
          // Honest refusal in CPU-forced session — keep button enabled (state could change after relaunch) but debounce double-clicks
          statusEl.innerHTML = `<p class="live-error">${String(errText).replace(/</g,"&lt;")}</p><p class="live-note">No new run started — honest refusal, not a queued job.</p>`;
          setButtonState(btn, "idle");
          // debounce double-clicks: brief 900ms guard without staying disabled for 60s
          let _debounce = true;
          btn.disabled = true;
          setTimeout(()=>{ btn.disabled = false; _debounce = false; }, 900);
          // ensure global guard respects debounce
          btn._cpuDebounce = Date.now() + 900;
          return;
        }
        statusEl.innerHTML = `<p class="live-error">409 single-flight — another forecast is running. Retry-After ${retryAfter ?? 60}s</p><pre class="live-raw">${JSON.stringify(payload,null,2)}</pre>`;
        setButtonState(btn, "singleflight", retryAfter ?? 60);
        setTimeout(()=> setButtonState(btn, "idle"), (Number(retryAfter)||60)*1000);
        return;
      }
      if (res.status === 429) {
        statusEl.innerHTML = `<p class="live-error">429 cooldown — ${payload?.error ?? "wait"}. Retry-After ${retryAfter ?? 120}s</p><pre class="live-raw">${JSON.stringify(payload,null,2)}</pre>`;
        setButtonState(btn, "cooldown", retryAfter ?? 120);
        setTimeout(()=> setButtonState(btn, "idle"), (Number(retryAfter)||120)*1000);
        return;
      }
      // other error
      statusEl.innerHTML = `<p class="live-error">trigger failed (${res.status}): ${payload?.error ?? res.statusText}</p><pre class="live-raw">${JSON.stringify(payload,null,2)}</pre>`;
      setButtonState(btn, "idle");
    } catch (err) {
      statusEl.innerHTML = `<p class="live-error">trigger transport failed: ${err.message ?? err}</p>`;
      setButtonState(btn, "idle");
    }
  }

  btn.addEventListener("click", trigger);

  // Idle copy first: a fresh server has no jobs (404 below) and the card
  // must never sit blank (owner incident 2026-08-27).
  renderIdle(statusEl);

  // On load, show latest terminal job if any (UI uses after reload)
  getJson("/api/forecast/latest").then(job=>{
    if (job && job.status) {
      if (job.status === "completed") statusEl.innerHTML = renderCompleted(job);
      else if (job.status === "failed") statusEl.innerHTML = renderFailed(job);
      else if (job.status === "running" || job.status === "queued") {
        activeJobId = job.job_id;
        statusEl.innerHTML = job.status === "running" ? renderRunning(job) : renderQueued(job);
        clearPoll();
        pollTimer = setInterval(()=> poll(job.job_id), POLL_MS);
        timeoutTimer = setTimeout(()=>{
          clearPoll();
          statusEl.innerHTML = renderTimeout(job);
        }, CLIENT_TIMEOUT_MS);
        setButtonState(btn, "pending");
      }
    } else {
      renderIdle(statusEl);
    }
  }).catch(()=>{ /* no jobs yet — stay idle */ });
}

// Auto-mount
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountLiveController, { once: true });
} else {
  mountLiveController();
}
