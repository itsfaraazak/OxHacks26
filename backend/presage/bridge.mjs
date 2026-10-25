// Child of detector.agent, not an HTTP service or a second camera owner.
// stdin: 16-byte header (uint32 BE width/height, float64 BE timestamp in us),
// followed by width * height * 3 BGR bytes. stdout: prefixed JSON aggregates.
import { fileURLToPath } from 'node:url';
import { config } from 'dotenv';
import { SmartSpectraSDK, PixelFormat, MetricType, decodeMetrics } from '@smartspectra/node-sdk';
import { normalizeMetrics } from './metrics.mjs';

config({ path: fileURLToPath(new URL('.env', import.meta.url)) });
const emit = message => process.stdout.write(`FIDO_PRESAGE ${JSON.stringify(message)}\n`);
const seconds = Number(process.argv[2] ?? 25);
if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 1500) {
  throw new Error('Presage budget must be > 0 and <= 1500 seconds');
}
if (!process.env.SMARTSPECTRA_API_KEY || process.env.SMARTSPECTRA_API_KEY.includes('YOUR_API_KEY')) {
  emit({ type: 'error', stage: 'key', message: 'Set SMARTSPECTRA_API_KEY in backend/presage/.env' });
  process.exit(1);
}

let sdk;
let deadline;
let stopping = false;
let exitCode = 0;
let minimumCaptureTimestamp = 0;
async function shutdown(reason, code = 0) {
  if (stopping) return;
  stopping = true;
  exitCode = code;
  clearTimeout(deadline);
  process.stdin.pause();
  emit({ type: 'stopped', message: reason });
  // Bound teardown too; an SDK hang must not silently extend a paid scan.
  const force = setTimeout(() => process.exit(exitCode), 3000);
  try {
    if (sdk) {
      await sdk.stopAsync();
      await sdk.destroy();
    }
  } finally {
    clearTimeout(force);
    process.exit(exitCode);
  }
}

function start() {
  deadline = setTimeout(() => void shutdown('Presage scan budget reached'), seconds * 1000);
  sdk = new SmartSpectraSDK({
    apiKey: process.env.SMARTSPECTRA_API_KEY,
    requestedMetrics: [MetricType.TALKING, MetricType.BLINKING, MetricType.FACE_LANDMARKS],
    enableTelemetry: false,
  });
  sdk.on('metrics', buf => {
    try {
      const metrics = decodeMetrics(buf);
      if (!metrics || Buffer.isBuffer(metrics)) {
        emit({ type: 'error', stage: 'decode', message: 'Presage metric decoding unavailable' });
        void shutdown('Metric decoding failed', 1);
      } else {
        emit(normalizeMetrics(metrics));
      }
    } catch {
      emit({ type: 'error', stage: 'decode', message: 'Invalid Presage metric payload' });
      void shutdown('Metric decoding failed', 1);
    }
  });
  sdk.on('error', code => {
    // Do not echo SDK messages that might contain authentication details.
    emit({ type: 'error', stage: 'sdk', code, message: `Presage SDK error ${code}` });
    void shutdown('SDK stopped after an error', 1);
  });
  sdk.useCustomInput();
  sdk.start();
  if (!stopping) {
    // start() may block on native model/auth initialization. The frame that
    // triggered it, plus buffered captures, must never enter the running graph.
    minimumCaptureTimestamp = Date.now() * 1000;
    emit({ type: 'started' });
  }
}

let pending = Buffer.alloc(0);
process.stdin.on('data', chunk => {
  if (stopping) return;
  pending = Buffer.concat([pending, chunk]);
  let stage = 'protocol';
  try {
    while (!stopping && pending.length >= 16) {
      const width = pending.readUInt32BE(0);
      const height = pending.readUInt32BE(4);
      const timestamp = pending.readDoubleBE(8);
      if (!width || !height || width > 1920 || height > 1080 ||
          !Number.isFinite(timestamp) || timestamp <= 0) throw new Error('Invalid frame');
      const size = width * height * 3;
      if (pending.length < 16 + size) break;
      const frame = pending.subarray(16, 16 + size);
      pending = pending.subarray(16 + size);
      if (!sdk) {
        stage = 'start';
        start(); // No authentication/paid scan until the first frame.
      }
      if (!stopping) {
        if (timestamp < minimumCaptureTimestamp) continue;
        stage = 'frame';
        sdk.sendFrame(frame, width, height, width * 3, PixelFormat.kBGR, timestamp);
      }
      stage = 'protocol';
    }
  } catch (error) {
    if (!stopping) emit({
      type: 'error', stage,
      code: Number.isInteger(error?.code) ? error.code : null,
    });
    void shutdown('Frame submission stopped', 1);
  }
});
process.stdin.on('end', () => void shutdown('Camera agent closed'));
process.on('SIGTERM', () => void shutdown('Camera agent stopped'));
process.on('SIGINT', () => void shutdown('Interrupted'));
emit({ type: 'ready' });
