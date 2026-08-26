// Affine view over projected world metres (EPSG:32643, y grows north).
// Screen space is CSS pixels; the engine multiplies by devicePixelRatio once.

const MIN_SCALE = 0.05;
const MAX_SCALE = 64;

// Pan clamp (audit B1): after every pan/zoom the data bbox must keep at
// least this fraction of the viewport intersecting in BOTH axes, so a hard
// drag can never push the city off-screen (the blank-canvas failure the
// audit measured). A fraction — not full containment — keeps edge inspection
// possible while guaranteeing recovery is always visible on screen.
const MIN_BBOX_OVERLAP = 0.15;

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
    this.scale = Math.min(
      (width - pad * 2) / dx,
      (height - pad * 2) / dy,
      this.maxScale
    );
    this.scale = Math.max(this.scale, this.minScale);
    this.tx = width / 2 - ((minx + maxx) / 2) * this.scale;
    this.ty = height / 2 + ((miny + maxy) / 2) * this.scale;
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
  // asymmetry between the x and y limits.
  clampToBbox() {
    if (!(this.width > 0) || !(this.height > 0)) return;
    const [minx, miny, maxx, maxy] = this.bbox;
    const s = this.scale;
    const minX = this.width * MIN_BBOX_OVERLAP - maxx * s;
    const maxX = this.width * (1 - MIN_BBOX_OVERLAP) - minx * s;
    const minY = this.height * MIN_BBOX_OVERLAP + miny * s;
    const maxY = this.height * (1 - MIN_BBOX_OVERLAP) + maxy * s;
    // The intervals are never empty for s>0 (the constraint is overlap, not
    // containment), but clamp defensively against a degenerate bbox anyway.
    this.tx = Math.min(Math.max(this.tx, minX), maxX);
    this.ty = Math.min(Math.max(this.ty, minY), maxY);
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
