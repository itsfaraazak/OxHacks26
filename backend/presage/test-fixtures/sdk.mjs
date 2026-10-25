export const PixelFormat = { kBGR: 1 };
export const MetricType = { TALKING: 13, BLINKING: 12, FACE_LANDMARKS: 11 };
export const decodeMetrics = buf => JSON.parse(buf.toString());
export class SmartSpectraSDK {
  callbacks = {};
  constructor(options) {
    if (options.enableTelemetry !== false) throw new Error('SDK telemetry must be disabled');
  }
  on(name, callback) { this.callbacks[name] = callback; }
  useCustomInput() {}
  start() {
    console.log('FIDO_TEST started');
    if (process.env.FIDO_FAKE_SLOW_START) {
      // Simulate blocking native model/auth initialization without real auth.
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 150);
      this.startedAt = Date.now() * 1000;
    }
    if (process.env.FIDO_FAKE_START_ERROR) {
      throw Object.assign(new Error('private startup message'), { code: 3 });
    }
    if (process.env.FIDO_FAKE_SDK_ERROR) this.callbacks.error(2, 'do not leak this message', false);
  }
  sendFrame(buf, width, height, stride, format, timestamp) {
    if (buf.length !== width * height * 3 || stride !== width * 3 || format !== 1) {
      throw new Error('Incorrect BGR frame');
    }
    if (this.startedAt && timestamp < this.startedAt) {
      throw Object.assign(new Error('stale startup frame'), { code: 11 });
    }
    this.callbacks.metrics(Buffer.from(JSON.stringify({
      face: { talking: [{ detected: true, stable: true, timestamp }] },
    })));
    return true;
  }
  async stopAsync() { console.log('FIDO_TEST stopped'); }
  async destroy() { console.log('FIDO_TEST destroyed'); }
}
