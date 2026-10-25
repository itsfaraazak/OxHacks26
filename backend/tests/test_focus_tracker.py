"""The ported tracker must make the same decisions as the prototype loop."""

from detector.focus import FaceSignals, FocusTracker

DT = 1 / 30  # 30 fps, a typical webcam
LOOKING = FaceSignals(pitch_ratio=0.40, yaw_ratio=0.0, eye_down=0.0, eye_blink=0.0, jaw_open=0.0)


def calibrated() -> FocusTracker:
    tracker = FocusTracker()
    for _ in range(30):
        reading = tracker.update(LOOKING, DT)
    assert reading.calibrated
    return tracker


def run(tracker: FocusTracker, signals: FaceSignals | None, seconds: float):
    reading = None
    for _ in range(round(seconds / DT)):
        reading = tracker.update(signals, DT)
    return reading


def test_calibrates_on_first_30_frames_with_median_baseline() -> None:
    tracker = FocusTracker()
    for i in range(29):
        assert tracker.update(FaceSignals(0.5 + i / 1000, 0.1, 0, 0, 0), DT).reason == "Calibrating posture..."
    reading = tracker.update(FaceSignals(0.5, 0.1, 0, 0, 0), DT)
    assert reading.calibrated and reading.focused
    assert abs(tracker.base_pitch - 0.514) < 0.001
    assert tracker.base_yaw == 0.1


def test_looking_at_screen_stays_focused() -> None:
    assert run(calibrated(), LOOKING, 20).focused


def test_looking_down_at_phone_flips_to_no_after_8_seconds() -> None:
    tracker = calibrated()
    phone = FaceSignals(0.40 + 0.30, 0.0, 0.0, 0.0, 0.0)
    almost = run(tracker, phone, 7.9)
    assert almost.focused and almost.reason.startswith("Looking Down at Phone/Lap")
    assert not run(tracker, phone, 0.2).focused


def test_eyes_down_lowers_the_phone_threshold() -> None:
    tracker = calibrated()
    reading = run(tracker, FaceSignals(0.40 + 0.20, 0.0, 0.9, 0.0, 0.0), 8.1)
    assert not reading.focused


def test_looking_up_and_sideways_are_slacking() -> None:
    assert run(calibrated(), FaceSignals(0.40 - 0.25, 0.0, 0, 0, 0), 8.1).reason.startswith("Looking Up")
    assert run(calibrated(), FaceSignals(0.40, 0.3, 0, 0, 0), 8.1).reason.startswith("Looking Away Sideways")


def test_slack_timer_recovers_three_times_faster() -> None:
    tracker = calibrated()
    run(tracker, FaceSignals(0.40, 0.3, 0, 0, 0), 6)
    run(tracker, LOOKING, 1)
    assert abs(tracker.distracted_seconds - 3.0) < 0.05


def test_no_face_counts_as_desk_abandoned() -> None:
    reading = run(calibrated(), None, 8.1)
    assert not reading.focused
    assert reading.reason == "No Face (Desk Abandoned)"


def test_micro_sleep_after_4_seconds_of_closed_eyes() -> None:
    tracker = calibrated()
    asleep = FaceSignals(0.40, 0.0, 0.0, 0.9, 0.0)
    assert run(tracker, asleep, 3.9).focused
    reading = run(tracker, asleep, 0.2)
    assert not reading.focused and reading.reason.startswith("Micro-Sleep")
    assert run(tracker, LOOKING, DT).focused  # opening the eyes resets instantly


def test_talking_survives_short_pauses_and_triggers_after_2_seconds() -> None:
    tracker = calibrated()
    talking = FaceSignals(0.40, 0.0, 0.0, 0.0, 0.2)
    run(tracker, talking, 1.0)
    run(tracker, LOOKING, 0.5)  # pause between words keeps counting
    reading = run(tracker, talking, 0.6)
    assert not reading.focused and reading.reason.startswith("Talking / Chatting")


def test_talking_resets_after_silence_grace() -> None:
    tracker = calibrated()
    run(tracker, FaceSignals(0.40, 0.0, 0.0, 0.0, 0.2), 1.5)
    run(tracker, LOOKING, 1.0)
    assert tracker.talking_seconds == 0.0


def test_recalibrate_forgets_baseline_and_timers() -> None:
    tracker = calibrated()
    run(tracker, None, 5)
    tracker.recalibrate()
    assert not tracker.calibrated and tracker.distracted_seconds == 0.0
    assert tracker.update(LOOKING, DT).reason == "Calibrating posture..."
