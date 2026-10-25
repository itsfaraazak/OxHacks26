import pytest

from fido.engine import EngineError, FocusEngine, Phase, Settings


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


NOT_FOCUSED = {"focused": False, "calibrated": True, "reason": "Looking Down at Phone/Lap"}
FOCUSED = {"focused": True, "calibrated": True, "reason": "Looking at Screen"}


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def engine(clock: Clock) -> FocusEngine:
    return FocusEngine(Settings(bark_after_seconds=5, escape_after_seconds=5, recover_grace_seconds=10), clock)


def docked_session(engine: FocusEngine, minutes: int = 25) -> None:
    engine.start("Write the report", minutes)
    engine.robot_event("docked")


def phase(engine: FocusEngine) -> Phase:
    assert engine.session is not None
    return engine.session.phase


def report(engine: FocusEngine, clock: Clock, reading: dict, seconds: float) -> None:
    """Report the same reading once per second, like a live camera."""
    for _ in range(int(seconds)):
        clock.tick(1)
        engine.record_reading("laptop", reading, None)


def test_session_waits_for_phone_dock_then_focuses(engine: FocusEngine) -> None:
    engine.start("  Write the report  ", 25)
    assert phase(engine) is Phase.AWAITING_PHONE
    assert engine.session.task == "Write the report"
    assert engine.snapshot()["robot"]["mode"] == "beckon_dock"

    engine.robot_event("docked")
    assert phase(engine) is Phase.FOCUSING


def test_already_docked_phone_starts_focusing_immediately(engine: FocusEngine) -> None:
    engine.robot_event("docked")
    engine.start("Write", 25)
    assert phase(engine) is Phase.FOCUSING


def test_dock_can_be_skipped_without_hardware(engine: FocusEngine) -> None:
    engine.update_settings({"require_dock": False})
    engine.start("Write", 25)
    assert phase(engine) is Phase.FOCUSING


def test_clock_does_not_run_until_phone_is_docked(engine: FocusEngine, clock: Clock) -> None:
    engine.start("Write", 1)
    clock.tick(30)
    engine.robot_event("docked")
    assert engine.snapshot()["session"]["remaining_seconds"] == 60


def test_continued_distraction_escalates_nudge_bark_escape(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine)
    report(engine, clock, NOT_FOCUSED, 1)
    assert phase(engine) is Phase.NUDGE
    assert "Looking Down" in engine.session.reason

    report(engine, clock, NOT_FOCUSED, 5)
    assert phase(engine) is Phase.BARKING

    report(engine, clock, NOT_FOCUSED, 5)
    assert phase(engine) is Phase.ESCAPED
    snap = engine.snapshot()
    assert snap["robot"]["mode"] == "run"
    assert snap["session"]["distractions"] == 1
    assert snap["session"]["escapes"] == 1


def test_refocusing_before_escape_calms_fido_down(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine)
    report(engine, clock, NOT_FOCUSED, 6)
    assert phase(engine) is Phase.BARKING

    report(engine, clock, FOCUSED, 1)
    assert phase(engine) is Phase.FOCUSING

    report(engine, clock, NOT_FOCUSED, 1)
    assert phase(engine) is Phase.NUDGE
    assert engine.session.distractions == 2


def test_escape_continues_until_caught_even_if_user_refocuses(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine)
    report(engine, clock, NOT_FOCUSED, 11)
    report(engine, clock, FOCUSED, 30)
    assert phase(engine) is Phase.ESCAPED


def test_robot_stop_ends_failed_session_until_explicit_restart(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine)
    report(engine, clock, NOT_FOCUSED, 11)
    old_id = engine.session.id
    engine.robot_event("caught")
    assert phase(engine) is Phase.FAILED
    assert engine.snapshot()["robot"]["mode"] == "rest"
    assert engine.history[0]["completed"] is False

    report(engine, clock, NOT_FOCUSED, 30)
    assert phase(engine) is Phase.FAILED
    report(engine, clock, FOCUSED, 1)
    engine.start("Write the report", 25)
    assert phase(engine) is Phase.FOCUSING
    assert engine.session.id != old_id
    assert engine.snapshot()["session"]["remaining_seconds"] == 1500
    assert engine.session.distractions == engine.session.escapes == 0
    assert len(engine.history) == 1


def test_caught_from_app_and_outside_escape(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine)
    with pytest.raises(EngineError):
        engine.apply("caught")
    engine.robot_event("caught")  # stray card tap is ignored
    assert phase(engine) is Phase.FOCUSING

    report(engine, clock, NOT_FOCUSED, 11)
    engine.apply("caught")
    assert phase(engine) is Phase.FAILED
    engine.robot_event("caught")  # repeated button press must not archive twice
    engine.apply("end")
    assert len(engine.history) == 1


def test_escape_stops_by_itself_after_ten_minutes(clock: Clock) -> None:
    engine = FocusEngine(Settings(bark_after_seconds=5, escape_after_seconds=5), clock)
    assert engine.settings.escape_timeout_seconds == 600
    docked_session(engine, minutes=60)
    report(engine, clock, NOT_FOCUSED, 11)
    clock.tick(599)
    engine.advance()
    assert phase(engine) is Phase.ESCAPED
    clock.tick(1)
    engine.advance()
    assert phase(engine) is Phase.FAILED
    assert "timed out" in engine.session.reason
    assert engine.history[0]["completed"] is False


def test_escape_freezes_timer_and_cannot_complete_or_restart(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine, minutes=1)
    report(engine, clock, NOT_FOCUSED, 11)
    remaining = engine.snapshot()["session"]["remaining_seconds"]
    assert engine.snapshot()["session"]["clock_running"] is False
    clock.tick(90)
    engine.advance()
    assert phase(engine) is Phase.ESCAPED
    assert engine.snapshot()["session"]["remaining_seconds"] == remaining
    with pytest.raises(EngineError):
        engine.start("Try again", 1)
    engine.apply("caught")
    assert phase(engine) is Phase.FAILED
    assert engine.history[0]["completed"] is False


def test_removing_phone_is_a_distraction(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine)
    clock.tick(1)
    engine.robot_event("undocked")
    assert phase(engine) is Phase.NUDGE
    assert engine.session.reason == "Phone removed from Fido"
    engine.robot_event("docked")
    assert phase(engine) is Phase.FOCUSING


def test_stale_or_uncalibrated_cameras_are_ignored(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine)
    engine.record_reading("laptop", {**NOT_FOCUSED, "calibrated": False}, None)
    assert phase(engine) is Phase.FOCUSING

    engine.record_reading("laptop", NOT_FOCUSED, None)
    assert phase(engine) is Phase.NUDGE
    clock.tick(engine.settings.source_stale_seconds + 1)  # camera stopped reporting
    engine.advance()
    assert phase(engine) is Phase.FOCUSING
    assert engine.snapshot()["detector"]["focused"] is None


def test_any_live_camera_saying_no_counts(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine)
    engine.record_reading("laptop", FOCUSED, None)
    engine.record_reading("dog", {**NOT_FOCUSED, "reason": "Talking / Chatting (2.1s)"}, None)
    assert phase(engine) is Phase.NUDGE
    assert engine.session.reason == "Talking / Chatting (2.1s) [dog]"
    assert engine.snapshot()["detector"]["focused"] is False


def test_pause_stops_clock_and_escalation(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine, minutes=1)
    clock.tick(10)
    engine.apply("pause")
    report(engine, clock, NOT_FOCUSED, 30)
    assert phase(engine) is Phase.PAUSED
    assert engine.snapshot()["session"]["remaining_seconds"] == 50

    engine.apply("resume")
    assert phase(engine) is Phase.FOCUSING


def test_cannot_pause_to_escape_the_escape(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine)
    report(engine, clock, NOT_FOCUSED, 11)
    with pytest.raises(EngineError):
        engine.apply("pause")


def test_session_completes_and_is_archived(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine, minutes=1)
    clock.tick(60)
    engine.advance()
    assert phase(engine) is Phase.COMPLETED
    assert engine.snapshot()["robot"]["mode"] == "celebrate"
    assert engine.history[0]["completed"] is True
    assert engine.history[0]["focused_seconds"] == 60

    engine.apply("end")
    assert engine.session is None
    assert len(engine.history) == 1
    engine.start("Next thing", 5)  # a new session can start


def test_ending_early_is_archived_as_incomplete(engine: FocusEngine, clock: Clock) -> None:
    docked_session(engine)
    clock.tick(90)
    engine.apply("end")
    assert engine.history[0]["completed"] is False
    assert engine.history[0]["focused_seconds"] == 90


@pytest.mark.parametrize(
    ("task", "minutes"),
    [("", 25), ("   ", 25), ("x" * 121, 25), ("ok", 0), ("ok", 2.5), ("ok", True), ("ok", "25"), (None, 25)],
)
def test_start_validation(engine: FocusEngine, task, minutes) -> None:
    with pytest.raises(EngineError):
        engine.start(task, minutes)
    assert engine.session is None


def test_only_one_session_at_a_time(engine: FocusEngine) -> None:
    engine.start("One", 25)
    with pytest.raises(EngineError):
        engine.start("Two", 25)


def test_settings_are_validated_atomically(engine: FocusEngine) -> None:
    with pytest.raises(EngineError):
        engine.update_settings({"bark_after_seconds": 3, "escape_after_seconds": -1})
    with pytest.raises(EngineError):
        engine.update_settings({"motion_enabled": "no"})
    with pytest.raises(EngineError):
        engine.update_settings({"warp_speed": 9})
    assert engine.settings.bark_after_seconds == 5
    assert engine.settings.motion_enabled is True


def test_robot_online_status_and_version_changes(engine: FocusEngine, clock: Clock) -> None:
    assert engine.snapshot()["robot"]["online"] is False
    v0 = engine.version
    engine.robot_event("heartbeat", {"battery": 90})
    assert engine.snapshot()["robot"]["online"] is True
    assert engine.snapshot()["robot"]["info"] == {"battery": 90}
    v1 = engine.version
    assert v1 > v0

    engine.robot_event("heartbeat", {"battery": 90})  # nothing new
    assert engine.version == v1

    clock.tick(engine.settings.robot_offline_seconds + 1)
    engine.advance()
    assert engine.snapshot()["robot"]["online"] is False
    assert engine.version > v1
