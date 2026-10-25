import pytest

from fido.engine import EngineError, FocusEngine, InvalidInput, Phase, Settings
from fido.speech import PLAYLIST


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def report(engine: FocusEngine, focused: bool) -> None:
    engine.record_reading(
        "laptop", {"focused": focused, "calibrated": True, "reason": "Test reading"}, None
    )


def finish_nudged_session(engine: FocusEngine, clock: Clock, ending: str) -> None:
    engine.start("First task", 1)
    clock.now += 58
    report(engine, False)
    assert engine.speech["cue"] == "nudge"
    assert engine._nudge_voiced
    if ending == "ended":
        engine.apply("end")
        assert engine.session is None
    elif ending == "completed":
        clock.now += 2
        engine.advance()
        assert engine.session.phase is Phase.COMPLETED
    else:
        clock.now += 0.5
        report(engine, False)
        clock.now += 0.5
        report(engine, False)
        assert engine.session.phase is Phase.ESCAPED
        engine.apply("caught")
        assert engine.session.phase is Phase.FAILED


@pytest.mark.parametrize("ending", ["failed", "ended", "completed"])
def test_new_session_nudges_immediately_and_preserves_speech_rotation(ending: str) -> None:
    clock = Clock()
    engine = FocusEngine(
        Settings(require_dock=False, bark_after_seconds=0.5, escape_after_seconds=0.5), clock
    )
    finish_nudged_session(engine, clock, ending)
    previous_seq = engine.speech["seq"]
    # Keep the new session focused until its first fresh distracted reading.
    report(engine, True)
    engine.start("Second task", 1)
    assert engine.speech["seq"] == previous_seq + 1
    assert not engine._nudge_voiced
    assert engine._last_nudge_at == float("-inf")
    report(engine, False)
    assert engine.speech["cue"] == "nudge"
    assert engine.speech["seq"] == previous_seq + 2
    assert engine.speech["clip"] == PLAYLIST["nudge"][1 % len(PLAYLIST["nudge"])]


@pytest.mark.parametrize("ending", ["failed", "ended", "completed"])
def test_new_session_does_not_praise_a_previous_sessions_nudge(ending: str, monkeypatch) -> None:
    clock = Clock()
    engine = FocusEngine(
        Settings(require_dock=False, bark_after_seconds=0.5, escape_after_seconds=0.5), clock
    )
    finish_nudged_session(engine, clock, ending)
    # No new nudge event: refocusing must not praise the old session's nudge.
    monkeypatch.setitem(PLAYLIST, "nudge", [])
    engine.start("Second task", 1)
    report(engine, False)
    assert engine.session.phase is Phase.NUDGE
    previous_seq = engine.speech["seq"]
    report(engine, True)
    assert engine.session.phase is Phase.FOCUSING
    assert engine.speech["seq"] == previous_seq


@pytest.mark.parametrize(
    ("task", "minutes", "error"),
    [
        ("", 1, InvalidInput),
        ("x" * 121, 1, InvalidInput),
        ("Valid task", 0, InvalidInput),
        ("Valid task", 1, EngineError),
    ],
)
def test_rejected_start_preserves_current_sessions_nudge(task, minutes, error) -> None:
    clock = Clock()
    engine = FocusEngine(Settings(require_dock=False), clock)
    engine.start("First task", 1)
    report(engine, False)
    session = engine.session
    last_nudge_at = engine._last_nudge_at
    previous_seq = engine.speech["seq"]
    with pytest.raises(error):
        engine.start(task, minutes)
    assert engine.session is session
    assert engine._last_nudge_at == last_nudge_at
    assert engine._nudge_voiced
    report(engine, True)
    assert engine.speech["cue"] == "praise"
    assert engine.speech["seq"] == previous_seq + 1
