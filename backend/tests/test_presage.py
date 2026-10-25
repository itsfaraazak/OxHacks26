import shutil
import time

import pytest

from detector import presage
from detector.focus import FaceSignals, FocusTracker
from detector.presage import CaptureClock, PresageBridge, PresageMetrics
from fido.engine import FocusEngine, Phase, Settings


def sample(at, detected=True, stable=True):
    return {"timestamp": at * 1_000_000, "detected": detected, "stable": stable}


def test_capture_clock_is_epoch_anchored_but_ignores_wall_clock_jumps(monkeypatch):
    monotonic_ns = 100_000_000
    wall_ns = 1_000_000_000_000
    monkeypatch.setattr(presage.time, "time_ns", lambda: wall_ns)
    monkeypatch.setattr(presage.time, "monotonic_ns", lambda: monotonic_ns)
    clock = CaptureClock()
    first = clock.now_us()
    assert first == wall_ns // 1000
    wall_ns -= 10_000_000_000
    monotonic_ns += 33_000_000
    assert clock.now_us() - first == 33_000
    wall_ns += 50_000_000_000
    monotonic_ns += 33_000_000
    assert clock.now_us() - first == 66_000


def test_sdk_talking_accumulates_across_short_pauses_and_expires():
    metrics = PresageMetrics()
    for i in range(20):
        now = 1000 + i * 0.1
        metrics.ingest({"talking": sample(now, i % 4 < 2)}, now)
    reading = metrics.read(now)
    assert reading.talking_seconds >= 0.6
    assert reading.talking is False  # pauses are not counted as active speech
    assert metrics.read(now + 6.1).talking_seconds == 0


def test_repeated_or_out_of_order_samples_do_not_extend_eye_closure():
    metrics = PresageMetrics()
    for i in range(40):
        now = 1000 + i * 0.1
        metrics.ingest({"eyes": sample(now)}, now)
    assert metrics.read(now).eyes_closed_seconds == pytest.approx(3.9)
    for _ in range(100):
        metrics.ingest({"eyes": sample(now)}, now + 0.5)
        metrics.ingest({"eyes": sample(now - 0.1)}, now + 0.5)
    assert metrics.read(now + 0.5).eyes_closed_seconds == pytest.approx(3.9)
    assert metrics.read(now + 1.1).eyes_closed_seconds == 0


def test_continuous_closure_uses_sdk_time_and_open_eyes_reset():
    metrics = PresageMetrics()
    for i in range(43):
        now = 1000 + i * 0.1
        metrics.ingest({"eyes": sample(now)}, now)
    assert metrics.read(now).eyes_closed_seconds > 4
    metrics.ingest({"eyes": sample(now + 0.1, False)}, now + 0.1)
    assert metrics.read(now + 0.1).eyes_closed_seconds == 0
    # A long gap must not count as continuous closed eyes.
    metrics.ingest({"eyes": sample(now + 5)}, now + 5)
    assert metrics.read(now + 5).eyes_closed_seconds == 0


def test_missing_unstable_stale_future_and_invalid_samples_are_unknown():
    metrics = PresageMetrics()
    for item in (sample(998), sample(1001), sample(1000, stable=False),
                 sample(float("nan")), {"timestamp": "bad", "detected": True}):
        metrics.ingest({"eyes": item, "talking": item}, 1000)
    reading = metrics.read(1000)
    assert reading.eyes_closed is None and reading.talking is None
    assert reading.eyes_closed_seconds == reading.talking_seconds == 0
    metrics.ingest({"eyes": sample(1000.1)}, 1000.1)
    metrics.ingest({"blinking": sample(1000.2)}, 1000.2)  # sparse packets don't refresh eyes
    assert metrics.read(1001.2).eyes_closed is None


def test_blinks_count_rising_edges_and_do_not_imply_eye_closure():
    metrics = PresageMetrics()
    for i, value in enumerate((False, True, True, False, True)):
        now = 1000 + i * 0.1
        metrics.ingest({"blinking": sample(now, value)}, now)
    reading = metrics.read(now)
    assert reading.blinks == 2 and reading.blinking is True
    assert reading.eyes_closed is None and reading.eyes_closed_seconds == 0


def test_reset_discards_old_session_metrics():
    metrics = PresageMetrics()
    metrics.ingest({"eyes": sample(1000), "blinking": sample(1000)}, 1000)
    metrics.reset(1000.5)
    metrics.ingest({"eyes": sample(1000.4)}, 1000.6)
    assert metrics.read(1000.6).eyes_closed is None
    assert metrics.read(1000.6).blinks == 0


def test_presage_talking_drives_backend_escalation_without_jaw_signal():
    now = 1000.0
    engine = FocusEngine(Settings(
        require_dock=False, motion_enabled=False,
        bark_after_seconds=1, escape_after_seconds=1,
    ), clock=lambda: now)
    engine.start("Focus", 25)
    tracker = FocusTracker(calibrated=True)
    metrics = PresageMetrics()
    looking = FaceSignals(0.4, 0, 0)
    phases = set()
    for i in range(40):
        now = 1000 + i * 0.1
        metrics.ingest({"talking": sample(now)}, now)
        reading = tracker.update(looking, 0.1, metrics.read(now))
        engine.record_reading("laptop", {
            "focused": reading.focused, "calibrated": reading.calibrated,
            "reason": reading.reason,
        })
        phases.add(engine.session.phase)
    assert {Phase.NUDGE, Phase.BARKING, Phase.ESCAPED} <= phases
    assert "Presage" in engine.session.reason
    assert engine.settings.motion_enabled is False


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_real_child_pipe_protocol_and_cleanup_without_sdk_or_key(tmp_path, monkeypatch):
    fake = tmp_path / "fake.mjs"
    fake.write_text("""
console.log('FIDO_PRESAGE {"type":"ready"}');
let pending = Buffer.alloc(0);
process.stdin.on('data', chunk => {
  pending = Buffer.concat([pending, chunk]);
  if (pending.length >= 19) {
    const timestamp = pending.readDoubleBE(8);
    console.log('FIDO_PRESAGE ' + JSON.stringify({
      type: 'metrics', talking: {timestamp, stable: true, detected: true}
    }));
    pending = pending.subarray(19);
  }
});
process.on('SIGTERM', () => process.exit(0));
""")
    monkeypatch.setattr(presage, "BRIDGE_SCRIPT", fake)
    bridge = PresageBridge(seconds=1)
    try:
        bridge.send(1, 1, b"\x01\x02\x03", time.time() * 1_000_000)
        # Bounded wait for a deterministic child-message assertion, not a paid scan.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            reading = bridge.read(time.time())
            if reading.talking is True:
                break
            time.sleep(0.01)
        assert reading.talking is True
        assert bridge._frames.maxsize == 1
    finally:
        bridge.close()
        bridge.close()  # idempotent
    assert bridge._process.poll() is not None


@pytest.mark.parametrize("seconds", [0, -1, 1501, float("nan"), float("inf")])
def test_invalid_budget_never_starts_child(seconds):
    with pytest.raises(ValueError):
        PresageBridge(seconds)


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_sdk_error_reason_survives_child_shutdown_without_echoing_raw_message(tmp_path, monkeypatch):
    fake = tmp_path / "error.mjs"
    fake.write_text("""
console.log('FIDO_PRESAGE {"type":"ready"}');
process.stdin.once('data', () => {
  console.log('FIDO_PRESAGE ' + JSON.stringify({
    type: 'error', code: 2, stage: 'sdk', message: 'sensitive native message'
  }));
  console.log('FIDO_PRESAGE {"type":"stopped"}');
  process.exit(1);
});
""")
    monkeypatch.setattr(presage, "BRIDGE_SCRIPT", fake)
    bridge = PresageBridge(seconds=1)
    try:
        bridge.send(1, 1, b"\x01\x02\x03", time.time() * 1_000_000)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = bridge.read(time.time()).status
            if status.startswith("error"):
                break
            time.sleep(0.01)
        assert "authentication" in status and "2" in status
        assert "sensitive native message" not in status
    finally:
        bridge.close()
