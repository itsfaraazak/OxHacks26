"""Prototype gaze rules, with Presage as the only face-activity source."""

from detector.focus import FaceSignals, FocusTracker
from detector.presage import PresageReading

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


def test_mediapipe_jaw_and_blink_do_not_trigger_face_activity() -> None:
    reading = run(calibrated(), FaceSignals(0.40, 0.0, 0.0, 1.0, 1.0), 10)
    assert reading.focused
    assert reading.talking_seconds == reading.eyes_closed_seconds == 0.0
    assert reading.presage_status == "disabled"


def test_presage_talking_drives_decision_even_with_closed_jaw() -> None:
    tracker = calibrated()
    assert tracker.update(LOOKING, DT, PresageReading(
        status="running", talking=True, talking_seconds=0.5,
    )).focused
    reading = tracker.update(LOOKING, DT, PresageReading(
        status="running", talking=True, talking_seconds=0.7,
    ))
    assert not reading.focused and "Talking / Chatting (Presage" in reading.reason


def test_presage_blink_is_telemetry_not_sleep_or_distraction() -> None:
    reading = calibrated().update(LOOKING, DT, PresageReading(
        status="running", blinking=True, blinks=1,
    ))
    assert reading.focused and reading.blinking is True and reading.blinks == 1
    assert reading.eyes_closed_seconds == 0


def test_prolonged_presage_landmark_closure_requires_four_seconds() -> None:
    tracker = calibrated()
    assert tracker.update(LOOKING, DT, PresageReading(
        status="running", eyes_closed=True, eyes_closed_seconds=3.9,
    )).focused
    reading = tracker.update(LOOKING, DT, PresageReading(
        status="running", eyes_closed=True, eyes_closed_seconds=4.1,
    ))
    assert not reading.focused and "Presage landmarks" in reading.reason
    assert not tracker.update(LOOKING, DT, PresageReading(
        status="running", eyes_closed=False,
    )).focused
    assert run(tracker, LOOKING, 2.1).focused


def test_unknown_presage_does_not_reuse_old_face_activity() -> None:
    tracker = calibrated()
    tracker.update(LOOKING, DT, PresageReading(status="running", talking_seconds=1))
    reading = tracker.update(LOOKING, DT, PresageReading(status="stopped"))
    assert not reading.focused and reading.talking_seconds == 0
    assert "face activity unknown" in reading.reason
    assert tracker.update(None, DT, PresageReading(
        status="running", eyes_closed=True, eyes_closed_seconds=5,
    )).eyes_closed_seconds == 0


def test_recalibrate_forgets_baseline_and_timers() -> None:
    tracker = calibrated()
    run(tracker, None, 5)
    tracker.recalibrate()
    assert not tracker.calibrated and tracker.distracted_seconds == 0.0
    assert tracker.update(LOOKING, DT).reason == "Calibrating posture..."


def test_no_requires_two_full_seconds_of_clear_readings_before_yes() -> None:
    tracker = calibrated()
    assert not tracker.update(LOOKING, 0.1, PresageReading(
        status="running", talking=True, talking_seconds=0.7,
    )).focused
    assert not tracker.update(LOOKING, 0.1).focused  # first clear frame starts clock
    for _ in range(19):
        reading = tracker.update(LOOKING, 0.1)
        assert not reading.focused
        assert "recovering" in reading.reason
    assert tracker.update(LOOKING, 0.1).focused


def test_new_no_resets_recovery_instead_of_flickering() -> None:
    tracker = calibrated()
    no = PresageReading(status="running", eyes_closed=True, eyes_closed_seconds=4.1)
    for _ in range(3):
        assert not tracker.update(LOOKING, 0.1, no).focused
        assert not tracker.update(LOOKING, 0.1).focused
        for _ in range(19):
            assert not tracker.update(LOOKING, 0.1).focused
    assert tracker.update(LOOKING, 0.1).focused


def test_same_recovery_rule_applies_to_gaze_distraction() -> None:
    tracker = calibrated()
    assert not run(tracker, FaceSignals(0.7, 0, 0), 8.1).focused
    assert not run(tracker, LOOKING, 1.9).focused
    assert run(tracker, LOOKING, 0.3).focused


def test_capture_stall_cannot_satisfy_recovery_and_recalibration_clears_latch() -> None:
    tracker = calibrated()
    no = PresageReading(status="running", talking_seconds=0.7)
    tracker.update(LOOKING, 0.1, no)
    tracker.update(LOOKING, 0.1)
    for _ in range(10):
        tracker.update(LOOKING, 0.1)
    assert not tracker.update(LOOKING, 3.0).focused
    assert not tracker.update(LOOKING, 0.1).focused
    tracker.recalibrate()
    assert tracker.update(LOOKING, DT).focused
