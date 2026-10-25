// Pure adapter for the decoded SmartSpectra payload; no SDK, camera, or key.
// Presage documents 478 pixel-coordinate landmarks with MediaPipe numbering:
// https://smartspectra.presagetech.com/docs/model-cards-and-limitations
const EYES = [[33, 160, 158, 133, 153, 144], [362, 385, 387, 263, 373, 380]];

function detection(sample) {
  if (!sample || typeof sample.detected !== 'boolean') return null;
  const timestamp = Number(sample.timestamp);
  if (!Number.isFinite(timestamp) || timestamp <= 0) return null;
  return { detected: sample.detected, stable: sample.stable === true, timestamp };
}

function eyeRatio(points, indices) {
  const [a, b, c, d, e, f] = indices.map(i => points[i]);
  const distance = (p, q) => Math.hypot(p.x - q.x, p.y - q.y);
  const width = distance(a, d);
  return width > 0 ? (distance(b, f) + distance(c, e)) / (2 * width) : null;
}

export function eyeClosure(sample) {
  const points = sample?.value;
  if (!Array.isArray(points) || points.length !== 478 ||
      !points.every(p => Number.isFinite(p.x) && Number.isFinite(p.y))) return null;
  const ratios = EYES.map(eye => eyeRatio(points, eye));
  if (ratios.some(ratio => ratio === null)) return null;
  // Derived bilateral eye-aspect-ratio heuristic, NOT a native sleep metric.
  return detection({
    detected: ratios.every(ratio => ratio < 0.18),
    stable: sample.stable === true && sample.reset !== true,
    timestamp: sample.timestamp,
  });
}

export function normalizeMetrics(metrics) {
  const result = {
    type: 'metrics',
    talking: detection(metrics.face?.talking?.at(-1)),
    blinking: detection(metrics.face?.blinking?.at(-1)),
    eyes: eyeClosure(metrics.face?.landmarks?.at(-1)),
  };
  return result;
}
