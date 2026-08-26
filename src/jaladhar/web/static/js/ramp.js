// Depth-band recalibration from realized product data (WF-6 build lane L1).
//
// Audit B5 measured the saturation failure: with the guide's typed boundaries
// (15/30/50 cm) 46.8% of flooded segments land in the top red band because
// the realized flooded distribution sits far above them (p50=47, p90=151 cm).
// The binding fix (C1) is to MOVE THE BAND BOUNDARIES onto the realized
// distribution — never to dim or attenuate the glow, which is the operational
// signature. C2 keeps the escalating-alarm hues cyan/yellow/orange/red.
//
// Boundaries are DATA, not magic constants: they are derived from the loaded
// product's flooded band_high_cm distribution and anchored at the frozen
// contract flood threshold served by /api/basemap/meta as depth_rule. The
// FIRST boundary IS the contract threshold; the upper boundaries are
// data-derived percentiles of the flooded subset.

// Minimum flooded samples before a percentile derivation is trusted; below
// this the ramp stays on the contract-anchored fallback bounds and says so
// via source:"contract".
const MIN_FLOODED_SAMPLES = 64;

// Fallback upper-bound ratios anchored at the contract threshold. At the
// frozen threshold of 15 cm these reproduce the guide's typed 30/50 cm
// exactly (x2 and x10/3); at any other served threshold they scale with it
// instead of embedding unrelated constants.
const FALLBACK_MID_RATIO = 2;
const FALLBACK_HIGH_RATIO = 10 / 3;

function isFinitePositive(v) {
  return typeof v === "number" && Number.isFinite(v) && v > 0;
}

// Type-7 quantile over an integer histogram {valueCm -> count}. Positions
// run 0..total-1; a target inside a value's run returns that value, a target
// between two distinct values interpolates linearly between them.
export function histogramQuantile(hist, total, q) {
  if (!(total > 0)) return NaN;
  const target = q * (total - 1);
  let seen = 0; // positions below the current value's run
  let prevValue = null;
  let prevEnd = -1; // last position consumed by a previous run
  for (const value of [...hist.keys()].sort((a, b) => a - b)) {
    const start = seen;
    const end = seen + hist.get(value) - 1;
    if (target <= end) {
      if (target >= start || prevValue === null) return value;
      const frac = (target - prevEnd) / (start - prevEnd);
      return prevValue + frac * (value - prevValue);
    }
    seen += hist.get(value);
    prevEnd = end;
    prevValue = value;
  }
  return prevValue ?? NaN;
}

// Derive the three lower band boundaries (cm): [threshold, p50, p90] of the
// flooded band_high_cm histogram, each clamped to >= the contract threshold
// and nudged strictly increasing (+1 cm steps — data resolution is integer
// cm). Returns the seam-contract object, or null when inputs are unusable
// (fail closed: the caller refuses to render water rather than guessing).
//
// Seam contract (V8 — asserted here, re-checked by the consumer):
//   { threshold_cm, bounds_cm:[...], source:'derived:flooded_p50_p90'|'contract' }
export function deriveDepthBands(thresholdCm, hist) {
  if (!isFinitePositive(thresholdCm) || !(hist instanceof Map)) return null;
  let samples = 0;
  for (const [value, count] of hist) {
    if (!Number.isInteger(value) || !Number.isInteger(count) || count < 0) return null;
    samples += count;
  }
  if (!Number.isFinite(samples) || samples < 0) return null;

  if (samples >= MIN_FLOODED_SAMPLES) {
    const p50 = histogramQuantile(hist, samples, 0.5);
    const p90 = histogramQuantile(hist, samples, 0.9);
    if (!isFinitePositive(p50) || !isFinitePositive(p90)) return null;
    const bounds = [
      Math.round(thresholdCm),
      Math.max(Math.round(p50), Math.round(thresholdCm)),
      Math.max(Math.round(p90), Math.round(thresholdCm)),
    ];
    // Strictly increasing: equal adjacent boundaries get the minimum +1 cm
    // nudge so every band is non-empty (integer cm is the data resolution).
    if (bounds[1] <= bounds[0]) bounds[1] = bounds[0] + 1;
    if (bounds[2] <= bounds[1]) bounds[2] = bounds[1] + 1;
    const bands = {
      threshold_cm: Math.round(thresholdCm),
      bounds_cm: bounds,
      source: "derived:flooded_p50_p90",
      n_flooded_samples: samples,
    };
    return assertRampShape(bands) ? bands : null;
  }

  const t = Math.round(thresholdCm);
  const bands = {
    threshold_cm: t,
    bounds_cm: [
      t,
      Math.max(Math.round(t * FALLBACK_MID_RATIO), t + 1),
      Math.max(Math.round(t * FALLBACK_HIGH_RATIO), t + 2),
    ],
    source: "contract",
    n_flooded_samples: samples,
  };
  return assertRampShape(bands) ? bands : null;
}

// V8 seam assertion for the ramp boundaries object. The first boundary must
// EQUAL the contract threshold (adapted honestly from the old
// assertDepthRule semantics: the first edge was always the contract rule;
// upper edges are now data-derived instead of guide-typed).
export function assertRampShape(bands) {
  if (!bands || typeof bands !== "object") return false;
  if (!isFinitePositive(bands.threshold_cm)) return false;
  if (!Array.isArray(bands.bounds_cm) || bands.bounds_cm.length !== 3) return false;
  if (!bands.bounds_cm.every(isFinitePositive)) return false;
  if (bands.bounds_cm[0] !== bands.threshold_cm) return false;
  if (!(bands.bounds_cm[0] < bands.bounds_cm[1] && bands.bounds_cm[1] < bands.bounds_cm[2])) {
    return false;
  }
  if (bands.source !== "derived:flooded_p50_p90" && bands.source !== "contract") return false;
  return true;
}
