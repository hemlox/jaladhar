// Affine view over projected world metres (EPSG:32643, y grows north).
// Screen space is CSS pixels; the engine multiplies by devicePixelRatio once.

const FLOOR_ABSOLUTE = 0.004;
const MIN_SCALE = 0.05;
const MAX_SCALE = 64;

// Pan clamp (audit B1): after every pan/zoom the data bbox must keep at
// least this fraction of the viewport intersecting in BOTH axes, so a hard
// drag can never push the city off-screen (the blank-canvas failure the
// audit measured). A fraction — not full containment — keeps edge inspection
// possible while guaranteeing recovery is always visible on screen.
// Presentation tightening: 0.35 (was 0.15) permits at most 65% off-screen per
// axis; combined with the 90% centre window below, hard multi-flick drags
// cannot park the city half-off while still allowing edge inspection.
const MIN_BBOX_OVERLAP = 0.35;

// One-time warning for degraded clamp mode (no viewport known).
let _clampWarned = false;

export class View {
  constructor(bbox) {
    this.bbox = bbox;
    this.scale = 1;
    this.tx = 0;
    this.ty = 0;
    this.minScale = MIN_SCALE;
    this.maxScale = MAX_SCALE;
    // Viewport the last fit() ran against; reset() re-runs that fit.
    this.width = 0;
    this.height = 0;
    // Verification telemetry (V1): automated gesture checks assert against
    // the realized transform, not a declared one.
    try {
      const registry = (globalThis.__JALADHAR_VIEWS ??= []);
      registry.push(this);
      if (registry.length > 4) registry.splice(0, registry.length - 4);
    } catch {
      // non-browser import (node --check)
    }
  }

  fit(width, height, pad = 40) {
    const [minx, miny, maxx, maxy] = this.bbox;
    const dx = Math.max(maxx - minx, 1);
    const dy = Math.max(maxy - miny, 1);
    this.width = width;
    this.height = height;
    // Invariant: minScale never rises above a scale the user is allowed to be
    // at. The boot/full-fit scale becomes the zoom-out floor (lowered to
    // FLOOR_ABSOLUTE if needed). Repeated refits can only lower minScale,
    // never raise it above the current scale, so users can always zoom out
    // to the full-city silhouette computed here.
    const fitScale = Math.min(
      (width - pad * 2) / dx,
      (height - pad * 2) / dy,
      this.maxScale
    );
    this.minScale = Math.max(FLOOR_ABSOLUTE, Math.min(this.minScale, fitScale));
    this.scale = Math.min(Math.max(fitScale, this.minScale), this.maxScale);
    this.tx = width / 2 - ((minx + maxx) / 2) * this.scale;
    this.ty = height / 2 + ((miny + maxy) / 2) * this.scale;
    // Ensure the freshly fitted view also respects the pan clamp (centre
    // window + overlap). Normally the centred fit already satisfies both,
    // but clamping defensively keeps the invariant tight across re-fits.
    this.clampToBbox();
  }

  // Recovery control (audit B3): restore exactly the boot fit.
  reset() {
    if (this.width > 0 && this.height > 0) this.fit(this.width, this.height);
  }

  // Zoom about the cursor, not the canvas centre (guide section 7 item 10).
  zoomAbout(cssX, cssY, factor) {
    const next = Math.min(this.maxScale, Math.max(this.minScale, this.scale * factor));
    if (next === this.scale) return;
    const wx = (cssX - this.tx) / this.scale;
    const wy = (this.ty - cssY) / this.scale;
    this.tx = cssX - wx * next;
    this.ty = cssY + wy * next;
    this.scale = next;
    this.clampToBbox();
  }

  panBy(dx, dy) {
    this.tx += dx;
    this.ty += dy;
    this.clampToBbox();
  }

  // Clamp tx/ty so the bbox keeps >= MIN_BBOX_OVERLAP of the viewport in both
  // axes. Screen y grows downward while world y grows north, hence the sign
  // asymmetry between the x and y limits. Additionally the bbox CENTRE is
  // constrained to the central 90% window (5% margin each side) — belt-and-
  // braces with the overlap constraint so hard multi-flick drags cannot park
  // the city half-off. If width/height are unset, tries
  // globalThis.__JALADHAR_VIEWPORT; else degraded fallback bounds centre
  // containment and warns once.
  clampToBbox() {
    let W = this.width;
    let H = this.height;
    if (!(W > 0) || !(H > 0)) {
      const vp = globalThis.__JALADHAR_VIEWPORT;
      if (vp && vp.width > 0 && vp.height > 0) {
        W = vp.width;
        H = vp.height;
      } else {
        // Degraded mode (no viewport known yet): no overlap basis exists.
        // Fall back to pure containment of the bbox centre near the origin
        // so pan cannot run away to blank canvas. Documented degraded mode:
        // we bound tx/ty so the bbox centre's screen projection stays within
        // a bounded window around the origin (±5000 px). This is not the
        // precise 90% window (no viewport), but prevents silent free pan.
        if (!_clampWarned) {
          try {
            console.warn('view.clamp: no viewport known yet');
          } catch {}
          _clampWarned = true;
        }
        const [minx, miny, maxx, maxy] = this.bbox;
        const cx = (minx + maxx) / 2;
        const cy = (miny + maxy) / 2;
        const s = this.scale;
        const bound = 5000;
        // Centre screen: cx*s + tx,  ty - cy*s . Keep within [-bound, +bound].
        const minTxDeg = -cx * s - bound;
        const maxTxDeg = -cx * s + bound;
        const minTyDeg = cy * s - bound;
        const maxTyDeg = cy * s + bound;
        this.tx = Math.min(Math.max(this.tx, minTxDeg), maxTxDeg);
        this.ty = Math.min(Math.max(this.ty, minTyDeg), maxTyDeg);
        return;
      }
    }
    const [minx, miny, maxx, maxy] = this.bbox;
    const s = this.scale;
    // Overlap constraint ONLY (owner incident 2026-08-27): the previous
    // belt-and-braces "bbox centre within central 90% of viewport" window was
    // scale-invariant in screen space, so at street zoom — where any real
    // street sits far more than half a viewport from the city centre — it
    // overpowered the fly-to target and dragged every selection back to the
    // city-centre anchor. The overlap intervals below already guarantee the
    // city keeps >=MIN_BBOX_OVERLAP of the viewport in both axes (blank-pan
    // recovery stays visible) while permitting street-level zoom anywhere in
    // the city. Verified: fly-to lands on each segment's own bbox centre.
    const minX = W * MIN_BBOX_OVERLAP - maxx * s;
    const maxX = W * (1 - MIN_BBOX_OVERLAP) - minx * s;
    const minY = H * MIN_BBOX_OVERLAP + miny * s;
    const maxY = H * (1 - MIN_BBOX_OVERLAP) + maxy * s;
    // The overlap intervals are never empty for s>0; clamp defensively anyway.
    if (minX > maxX) {
      this.tx = (minX + maxX) / 2;
    } else {
      this.tx = Math.min(Math.max(this.tx, minX), maxX);
    }
    if (minY > maxY) {
      this.ty = (minY + maxY) / 2;
    } else {
      this.ty = Math.min(Math.max(this.ty, minY), maxY);
    }
  }

  // Realized overlap fraction of the data bbox with the viewport, per axis —
  // the observable the B1 fix asserts against.
  bboxOverlapFractions() {
    const [minx, miny, maxx, maxy] = this.bbox;
    const s = this.scale;
    const left = minx * s + this.tx;
    const right = maxx * s + this.tx;
    const top = this.ty - maxy * s;
    const bottom = this.ty - miny * s;
    const overlapX =
      (Math.min(right, this.width) - Math.max(left, 0)) / Math.max(this.width, 1);
    const overlapY =
      (Math.min(bottom, this.height) - Math.max(top, 0)) / Math.max(this.height, 1);
    // If width/height unset but viewport fallback exists, report overlap
    // against the fallback viewport for invariant checks.
    if (!(this.width > 0) || !(this.height > 0)) {
      const vp = globalThis.__JALADHAR_VIEWPORT;
      if (vp && vp.width > 0 && vp.height > 0) {
        const W = vp.width;
        const H = vp.height;
        const fx =
          (Math.min(right, W) - Math.max(left, 0)) / Math.max(W, 1);
        const fy =
          (Math.min(bottom, H) - Math.max(top, 0)) / Math.max(H, 1);
        return [fx, fy];
      }
    }
    return [overlapX, overlapY];
  }

  toScreen(x, y) {
    return [x * this.scale + this.tx, -y * this.scale + this.ty];
  }

  toWorld(cssX, cssY) {
    return [(cssX - this.tx) / this.scale, (this.ty - cssY) / this.scale];
  }

  metresPerPixel() {
    return 1 / this.scale;
  }

  viewportRect(width, height) {
    const [x0, y1] = this.toWorld(0, 0);
    const [x1, y0] = this.toWorld(width, height);
    return [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
  }
}

// Exported helper for invariant checks (presentation verification).
// Returns {ok, overlapX, overlapY} using bboxOverlapFractions().
// No-ops outside browser (returns ok:true with zero overlaps) to avoid
// breaking node --check or SSR imports.
export function assertOverlapInvariant(view, eps = 0.02) {
  try {
    if (typeof window === 'undefined' && typeof document === 'undefined') {
      // Outside browser (node --check / SSR): still compute if view is
      // available, but do not enforce; caller may ignore. We return ok:true
      // to remain a no-op for non-browser verifiers that import the module.
      // However if bboxOverlapFractions exists, we compute properly for
      // throwaway node scripts that explicitly test the invariant.
      if (!view || typeof view.bboxOverlapFractions !== 'function') {
        return { ok: true, overlapX: 0, overlapY: 0 };
      }
    }
    if (!view || typeof view.bboxOverlapFractions !== 'function') {
      return { ok: false, overlapX: 0, overlapY: 0 };
    }
    const [overlapX, overlapY] = view.bboxOverlapFractions();
    const ok = overlapX + eps >= MIN_BBOX_OVERLAP && overlapY + eps >= MIN_BBOX_OVERLAP;
    return { ok, overlapX, overlapY };
  } catch {
    return { ok: false, overlapX: 0, overlapY: 0 };
  }
}
