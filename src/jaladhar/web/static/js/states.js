// Designed states — guide section 7 item 8. The empty state WILL be seen if
// anything fails in the room, so it is composed, centred, and names what is
// absent. None of these states substitute synthetic data (rule 2).

export function showOverlay({ title, message, error = false }) {
  const card = document.getElementById("overlay-card");
  card.classList.toggle("error", error);
  document.getElementById("overlay-title").textContent = title;
  document.getElementById("overlay-message").textContent = message;
  card.classList.remove("hidden");
}

export function hideOverlay() {
  document.getElementById("overlay-card").classList.add("hidden");
}

export function loading() {
  showOverlay({
    title: "Loading",
    message: "Reading the realized run state, basemap bundles and drain snapshot.",
  });
}

// --------------------------------------------------------------------------
// Non-blocking notice variant (ADDENDUM 3 L3/L4). Unlike showOverlay — which
// centres over the map for boot-fatal states — a notice floats top-center,
// never blanks the basemap, and its host is pointer-transparent so map
// interaction continues underneath. Only the card itself takes pointer
// events. One notice at a time; re-calling replaces its content.
//
// Exported signatures of the overlay functions above are unchanged: boot
// error paths depend on them.
// --------------------------------------------------------------------------

const NOTICE_HOST_ID = "jaladhar-notice-host";

export function showNotice({ title = "", message = "", tone = "info" }) {
  let host = document.getElementById(NOTICE_HOST_ID);
  if (!host) {
    const mapArea = document.querySelector(".map-area") ?? document.body;
    host = document.createElement("div");
    host.id = NOTICE_HOST_ID;
    // Styles ship from mode.js's injected sheet; the structural rules here
    // keep this module correct even if that import never ran.
    host.style.cssText =
      "position:absolute;top:12px;left:50%;transform:translateX(-50%);" +
      "z-index:40;pointer-events:none;";
    mapArea.appendChild(host);
  }
  let card = host.querySelector(".jal-notice-card");
  if (!card) {
    card = document.createElement("div");
    card.className = "jal-notice-card";
    const heading = document.createElement("div");
    heading.className = "jal-notice-title";
    const body = document.createElement("p");
    body.className = "jal-notice-message";
    card.append(heading, body);
    host.appendChild(card);
  }
  card.className = "jal-notice-card tone-" + tone;
  if (tone === "alert") card.style.borderColor = "rgba(244,63,94,.45)";
  else if (tone === "calm") card.style.borderColor = "rgba(56,189,248,.4)";
  else card.style.borderColor = "";
  card.querySelector(".jal-notice-title").textContent = title;
  card.querySelector(".jal-notice-message").textContent = message;
  return card;
}

export function hideNotice() {
  document.getElementById(NOTICE_HOST_ID)?.remove();
}

// --------------------------------------------------------------------------
// Mode-architecture bootstrap. app.js already imports this module, and ES
// module scripts execute after the DOM is parsed, so this is the one wire
// available inside this lane's ownership to bring the header badge up. The
// dynamic import keeps states.js loadable without it (tests, future lanes)
// and avoids a static cycle; mountModeController is idempotent, so a later
// direct wiring from app.js would not double-mount. Failure here is
// non-fatal by design — the dashboard must still boot with rail's label.
// --------------------------------------------------------------------------

// B7: mode badge now owned by app.js chip (LIVE vs DEMO · Sept 2022 event).
// The legacy ModeController still exists for unit tests but is NOT auto-mounted
// here — app.js drives the header explicitly so stored preference vs default
// LIVE semantics are single-sourced and the 2022 payload never paints in LIVE.
void 0;
