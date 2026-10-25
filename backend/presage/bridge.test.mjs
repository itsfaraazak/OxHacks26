import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';

function child(seconds = '0.15', extraEnv = {}) {
  const processChild = spawn(process.execPath, [
    '--experimental-loader', fileURLToPath(new URL('./test-fixtures/loader.mjs', import.meta.url)),
    fileURLToPath(new URL('./bridge.mjs', import.meta.url)), seconds,
  ], {
    env: { ...process.env, SMARTSPECTRA_API_KEY: 'test-not-a-secret', ...extraEnv },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const output = { stdout: '', stderr: '' };
  processChild.stdout.on('data', chunk => { output.stdout += chunk; });
  processChild.stderr.on('data', chunk => { output.stderr += chunk; });
  return { processChild, output };
}

function frame(timestamp = Date.now() * 1000) {
  const header = Buffer.alloc(16);
  header.writeUInt32BE(1, 0); header.writeUInt32BE(1, 4);
  header.writeDoubleBE(timestamp, 8);
  return Buffer.concat([header, Buffer.from([1, 2, 3])]);
}

async function waitStarted(processChild, output) {
  const deadline = Date.now() + 1000;
  while (!output.stdout.includes('"type":"started"') && processChild.exitCode === null && Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.doesNotMatch(output.stdout, /"type":"error"/);
  assert.match(output.stdout, /"type":"started"/);
}

test('production bridge handles chunked frames and stops once at its budget', { timeout: 5000 }, async () => {
  const { processChild, output } = child();
  try {
    const exited = once(processChild, 'exit');
    const packet = frame();
    processChild.stdin.write(packet.subarray(0, 5));
    processChild.stdin.write(packet.subarray(5));
    await waitStarted(processChild, output);
    processChild.stdin.write(frame());
    const [code] = await exited;
    assert.equal(code, 0, output.stderr);
    assert.match(output.stdout, /"type":"ready"/);
    assert.match(output.stdout, /"talking":\{"detected":true,"stable":true,"timestamp":\d+\}/);
    assert.match(output.stdout, /scan budget reached/);
    assert.equal((output.stdout.match(/FIDO_TEST started/g) ?? []).length, 1);
    assert.equal((output.stdout.match(/FIDO_TEST destroyed/g) ?? []).length, 1);
  } finally {
    processChild.kill();
  }
});

test('EOF before the first frame never starts or authenticates an SDK session', { timeout: 5000 }, async () => {
  const { processChild, output } = child();
  try {
    const exited = once(processChild, 'exit');
    processChild.stdin.end();
    const [code] = await exited;
    assert.equal(code, 0);
    assert.doesNotMatch(output.stdout, /FIDO_TEST started/);
  } finally {
    processChild.kill();
  }
});

test('SDK errors stop processing without logging native authentication details', { timeout: 5000 }, async () => {
  const { processChild, output } = child('1', { FIDO_FAKE_SDK_ERROR: '1' });
  try {
    const exited = once(processChild, 'exit');
    processChild.stdin.write(frame());
    const [code] = await exited;
    assert.equal(code, 1);
    assert.match(output.stdout, /Presage SDK error 2/);
    assert.match(output.stdout, /"stage":"sdk","code":2/);
    assert.doesNotMatch(output.stdout + output.stderr, /do not leak this message/);
    assert.match(output.stdout, /FIDO_TEST destroyed/);
  } finally {
    processChild.kill();
  }
});

test('thrown startup errors retain their safe code and are not mislabelled as frame failures', { timeout: 5000 }, async () => {
  const { processChild, output } = child('1', { FIDO_FAKE_START_ERROR: '1' });
  try {
    const exited = once(processChild, 'exit');
    processChild.stdin.write(frame());
    const [code] = await exited;
    assert.equal(code, 1);
    assert.match(output.stdout, /"stage":"start","code":3/);
    assert.doesNotMatch(output.stdout + output.stderr, /private startup message/);
  } finally {
    processChild.kill();
  }
});

test('slow SDK startup drops pre-start frames and accepts fresh capture without timestamp-gap failure', { timeout: 5000 }, async () => {
  const { processChild, output } = child('1', { FIDO_FAKE_SLOW_START: '1' });
  try {
    const exited = once(processChild, 'close');
    // This frame and another buffered one predate native SDK readiness.
    const beforeStart = Date.now() * 1000;
    processChild.stdin.write(Buffer.concat([frame(beforeStart), frame(beforeStart + 1)]));
    // First verify startup did not fail, then submit a genuinely fresh capture.
    await waitStarted(processChild, output);
    processChild.stdin.write(frame(Date.now() * 1000));
    const [code] = await exited;
    assert.equal(code, 0);
    assert.match(output.stdout, /"type":"metrics"/);
    assert.doesNotMatch(output.stdout, /"type":"error"/);
    assert.equal((output.stdout.match(/"type":"metrics"/g) ?? []).length, 1);
  } finally {
    processChild.kill();
  }
});
