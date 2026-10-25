import fs from 'fs';
import { config } from 'dotenv';
import {
  SmartSpectraSDK,
  CameraSelection,
  breathingMetrics,
  cardioMetrics,
  faceMetrics,
  decodeMetrics,
} from '@smartspectra/node-sdk';

// 1. Load API Key from presage/.env
config();
const apiKey = process.env.SMARTSPECTRA_API_KEY;

if (!apiKey || apiKey.includes('YOUR_API_KEY')) {
  console.error('\n❌ ERROR: No valid API key found!');
  console.error('👉 Please paste your API key inside presage/.env\n');
  process.exit(1);
}

console.log('====================================================');
console.log('   🛡️ PRESAGE SMARTSPECTRA 25-SECOND TEST HARNESS   ');
console.log('   Budget Safety: Strictly limited to 1 credit (<30s) ');
console.log('====================================================\n');

// 2. Discover cameras
const cameras = SmartSpectraSDK.availableCameras();
console.log(`📷 Discovered ${cameras.length} camera(s)`);
cameras.forEach((cam, i) => console.log(`   [${i}] ID: ${cam.id} | Name: ${cam.name || 'Default'}`));

// 3. Initialize SDK with requested metric bundles
const sdk = new SmartSpectraSDK({
  apiKey,
  requestedMetrics: [
    ...breathingMetrics, // Respiration rate, chest/abdominal traces
    ...cardioMetrics,    // Pulse (BPM), HRV (stress index, RMSSD)
    ...faceMetrics,      // Blinking, talking, facial landmarks, expressions
  ],
});

const logFilePath = new URL('test_output.json', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const eventLogs = [];

let sessionMetrics = {
  samplesCount: 0,
  latestPulse: null,
  latestBreathing: null,
  latestHrvRmssd: null,
  blinkEvents: 0,
  talkingEvents: 0,
  lastExpression: null,
  validationHints: [],
  rawMetricsSamples: [],
};

// 4. Attach event listeners
sdk.on('processingStatus', (status) => {
  const entry = { type: 'status', time: new Date().toISOString(), status };
  eventLogs.push(entry);
  console.log(`[STATUS] Processing: ${status}`);
});

sdk.on('validationStatus', (code, timestampUs, hint) => {
  const entry = { type: 'validation', time: new Date().toISOString(), code, timestampUs, hint };
  eventLogs.push(entry);
  if (hint) {
    sessionMetrics.validationHints.push({ time: new Date().toISOString(), code, hint });
    console.log(`\n[HINT] 👁️  Validation (${code}): ${hint}`);
  }
});

sdk.on('metrics', (buf, timestampUs) => {
  const metrics = decodeMetrics(buf);
  if (!metrics || Buffer.isBuffer(metrics)) return;

  sessionMetrics.samplesCount++;

  const pulse = metrics.cardio?.pulseRate?.at(-1)?.value;
  const hrv = metrics.cardio?.hrv?.at(-1)?.rmssd;
  const breathing = metrics.breathing?.rate?.at(-1)?.value;
  const blinking = metrics.face?.blinking?.at(-1)?.detected;
  const talking = metrics.face?.talking?.at(-1)?.detected;
  const expression = metrics.face?.expression?.at(-1);

  if (pulse !== undefined) sessionMetrics.latestPulse = Math.round(pulse);
  if (breathing !== undefined) sessionMetrics.latestBreathing = Math.round(breathing);
  if (hrv !== undefined) sessionMetrics.latestHrvRmssd = Math.round(hrv * 100) / 100;
  if (blinking) sessionMetrics.blinkEvents++;
  if (talking) sessionMetrics.talkingEvents++;
  if (expression) sessionMetrics.lastExpression = expression;

  // Save parsed sample to log array (every 5th sample to keep file concise)
  if (sessionMetrics.samplesCount % 5 === 0) {
    sessionMetrics.rawMetricsSamples.push({
      timestampUs,
      pulse: sessionMetrics.latestPulse,
      breathing: sessionMetrics.latestBreathing,
      hrv: sessionMetrics.latestHrvRmssd,
      blinking,
      talking,
      expression,
    });
  }

  // Live telemetry line
  process.stdout.write(
    `\r⏱️ ${remaining}s left | ` +
    `💓 Pulse: ${sessionMetrics.latestPulse ?? '--'} | ` +
    `🫁 Breath: ${sessionMetrics.latestBreathing ?? '--'} | ` +
    `⚡ HRV: ${sessionMetrics.latestHrvRmssd ?? '--'} | ` +
    `👀 Blinks: ${sessionMetrics.blinkEvents} | ` +
    `🗣️ Talk: ${talking ? 'YES' : 'no '} `
  );
});

sdk.on('error', (code, message, retryable) => {
  const entry = { type: 'error', time: new Date().toISOString(), code, message, retryable };
  eventLogs.push(entry);
  console.error(`\n[ERROR] Code: ${code} | Message: ${message} (retryable: ${retryable})`);
});

// 5. Select camera & start
if (cameras.length > 0) {
  sdk.useCamera(CameraSelection.byId(cameras[0].id));
} else {
  sdk.useCamera(CameraSelection.default);
}

console.log('\n🚀 Starting capture from camera... (Look directly at the camera)\n');
await sdk.start();

// 6. Hard 25-Second Budget Countdown (guarantees strictly 1 credit)
const TEST_DURATION_SECONDS = 25;
let remaining = TEST_DURATION_SECONDS;

const countdown = setInterval(async () => {
  remaining--;
  if (remaining <= 0) {
    clearInterval(countdown);
    console.log('\n\n⏱️  25-SECOND LIMIT REACHED — STOPPING IMMEDIATELY TO PRESERVE CREDITS...');
    await shutdown();
  }
}, 1000);

async function shutdown() {
  try {
    await sdk.stopAsync();
    await sdk.destroy();
  } catch (err) {
    // Ignore teardown errors
  }

  // Write full log to JSON file
  const fullLog = {
    testDate: new Date().toISOString(),
    durationSeconds: TEST_DURATION_SECONDS - remaining,
    summary: {
      totalSamples: sessionMetrics.samplesCount,
      pulse: sessionMetrics.latestPulse,
      breathing: sessionMetrics.latestBreathing,
      hrvRmssd: sessionMetrics.latestHrvRmssd,
      blinks: sessionMetrics.blinkEvents,
      talkingEvents: sessionMetrics.talkingEvents,
      lastExpression: sessionMetrics.lastExpression,
    },
    validationHints: sessionMetrics.validationHints,
    allEvents: eventLogs,
    metricsTimeline: sessionMetrics.rawMetricsSamples,
  };

  try {
    fs.writeFileSync(logFilePath, JSON.stringify(fullLog, null, 2));
    console.log(`\n💾 Full session log saved to: presage/test_output.json`);
  } catch (e) {
    console.error('Failed to write log file:', e);
  }

  // Export vitals for focus detector overlay
  const vitalsPath = new URL('../detector/presage_vitals.json', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
  try {
    fs.writeFileSync(vitalsPath, JSON.stringify({
      timestamp: new Date().toISOString(),
      pulse: sessionMetrics.latestPulse,
      breathing: sessionMetrics.latestBreathing,
      hrvRmssd: sessionMetrics.latestHrvRmssd,
      blinks: sessionMetrics.blinkEvents,
      status: "Calibrated"
    }, null, 2));
    console.log(`💾 Live vitals exported to: detector/presage_vitals.json`);
  } catch (e) {
    console.error('Failed to export vitals to detector:', e);
  }

  console.log('\n====================================================');
  console.log('              📊 TEST SUMMARY REPORT                ');
  console.log('====================================================');
  console.log(`• Total Metric Frames:   ${sessionMetrics.samplesCount}`);
  console.log(`• Final Pulse (BPM):     ${sessionMetrics.latestPulse ?? 'Calibrating...'}`);
  console.log(`• Final Respiration:     ${sessionMetrics.latestBreathing ?? 'Calibrating...'}`);
  console.log(`• Heart Rate Variability:${sessionMetrics.latestHrvRmssd ?? 'Calibrating...'}`);
  console.log(`• Total Blinks Detected: ${sessionMetrics.blinkEvents}`);
  console.log(`• Talking Detected:      ${sessionMetrics.talkingEvents > 0 ? 'Yes' : 'No'}`);
  console.log(`• Validation Hints Seen: ${sessionMetrics.validationHints.length}`);
  console.log('• Estimated Credits Used: Exactly 1 credit (~25 seconds)');
  console.log('====================================================\n');
  process.exit(0);
}

process.on('SIGINT', async () => {
  console.log('\n\n[ABORT] User triggered Ctrl+C — stopping SDK cleanly...');
  clearInterval(countdown);
  await shutdown();
});
