import { test } from 'node:test';
import assert from 'node:assert/strict';
import { eyeClosure, normalizeMetrics } from './metrics.mjs';

const sample = (detected, timestamp = 1_000_000) => ({ detected, stable: true, timestamp });
function landmarks(leftRatio, rightRatio = leftRatio) {
  const value = Array.from({ length: 478 }, () => ({ x: 0, y: 0 }));
  for (const [indices, ratio] of [
    [[33, 160, 158, 133, 153, 144], leftRatio],
    [[362, 385, 387, 263, 373, 380], rightRatio],
  ]) {
    const [a, b, c, d, e, f] = indices;
    value[a] = { x: 0, y: 0 }; value[d] = { x: 100, y: 0 };
    value[b] = { x: 30, y: -50 * ratio }; value[f] = { x: 30, y: 50 * ratio };
    value[c] = { x: 70, y: -50 * ratio }; value[e] = { x: 70, y: 50 * ratio };
  }
  return { value, timestamp: 1_000_000, stable: true, reset: false };
}

test('native Presage talking/blinking retain false, stability and sample timestamp', () => {
  const result = normalizeMetrics({ face: {
    talking: [sample(true), sample(false, 2_000_000)],
    blinking: [sample(true)],
  } });
  assert.deepEqual(result.talking, sample(false, 2_000_000));
  assert.deepEqual(result.blinking, sample(true));
  assert.equal(result.eyes, null); // blink is not sustained closure
  assert.equal(normalizeMetrics({}).talking, null);
});

test('eye closure uses both Presage eyes, not a wink or native blink', () => {
  assert.equal(eyeClosure(landmarks(0.1)).detected, true);
  assert.equal(eyeClosure(landmarks(0.3)).detected, false);
  assert.equal(eyeClosure(landmarks(0.1, 0.3)).detected, false);
});

test('unsupported, unstable, reset and degenerate landmark data cannot indicate reliable closure', () => {
  assert.equal(eyeClosure({ ...landmarks(0.1), value: [] }), null);
  assert.equal(eyeClosure({ ...landmarks(0.1), stable: false }).stable, false);
  assert.equal(eyeClosure({ ...landmarks(0.1), reset: true }).stable, false);
  const invalid = landmarks(0.1);
  invalid.value[33].x = NaN;
  assert.equal(eyeClosure(invalid), null);
  const flat = { ...landmarks(0.1), value: Array(478).fill({ x: 0, y: 0 }) };
  assert.equal(eyeClosure(flat), null);
});
