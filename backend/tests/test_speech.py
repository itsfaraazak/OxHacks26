import pytest

from fido.app import create_app
from fido.engine import FocusEngine, Settings
from fido import speech
from fido.speech import CAPTIONS, PLAYABLE, PLAYLIST

NOT_FOCUSED = {"focused": False, "calibrated": True, "reason": "Looking Down at Phone/Lap"}
FOCUSED = {"focused": True, "calibrated": True, "reason": "Looking at Screen"}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def engine(clock: Clock) -> FocusEngine:
    return FocusEngine(Settings(bark_after_seconds=5, escape_after_seconds=5, recover_grace_seconds=10), clock)


def cue(engine: FocusEngine) -> str | None:
    return engine.snapshot()["speech"] and engine.snapshot()["speech"]["cue"]


def report(engine: FocusEngine, clock: Clock, reading: dict, seconds: int) -> None:
    for _ in range(seconds):
        clock.tick(1)
        engine.record_reading("laptop", reading, None)


def cues_heard(engine: FocusEngine, clock: Clock, step) -> list[str]:
    """Run `step`, collecting every cue emitted, the way a player watching `seq` would."""
    heard: list[str] = []
    last = engine.snapshot()["speech"]["seq"] if engine.speech else 0
    original = engine.record_reading

    def spy(*args, **kwargs):
        nonlocal last
        original(*args, **kwargs)
        speech = engine.snapshot()["speech"]
        if speech and speech["seq"] > last:
            heard.append(speech["cue"])
            last = speech["seq"]

    engine.record_reading = spy  # type: ignore[method-assign]
    step()
    return heard


def test_playlist_only_uses_clips_that_have_captions() -> None:
    assert set(PLAYABLE) == {c for clips in PLAYLIST.values() for c in clips}
    for name in PLAYABLE:
        assert CAPTIONS[name] and "[" not in CAPTIONS[name]  # audio tags never shown to users
    # every moment of a session can be handled (a cue with no clips would just be silent)
    assert set(PLAYLIST) >= {"dock", "start", "nudge", "bark", "run", "caught", "timeout", "praise", "done"}


def test_every_recorded_clip_in_the_playlist_exists_on_disk() -> None:
    from pathlib import Path

    clips = Path(__file__).resolve().parents[1] / "clips"
    missing = [c for c in PLAYABLE if not (clips / f"{c}.mp3").is_file()]
    assert not missing, f"playlist clips missing from backend/clips/: {missing}"


def test_clips_are_reused_across_moments() -> None:
    reused = {c for c in PLAYABLE if sum(c in clips for clips in PLAYLIST.values()) > 1}
    assert reused  # the team deliberately repurposed some recordings


def test_no_speech_before_anything_happens(engine: FocusEngine) -> None:
    assert engine.snapshot()["speech"] is None


def test_dock_cue_when_waiting_for_the_phone(engine: FocusEngine) -> None:
    engine.start("Write", 25)
    assert cue(engine) == "dock"


def test_start_cue_when_phone_is_docked(engine: FocusEngine) -> None:
    engine.start("Write", 25)
    engine.robot_event("docked")
    assert cue(engine) == "start"
    assert engine.snapshot()["speech"]["seq"] == 2


def test_old_dock_acknowledgement_still_emits_new_dock_cue(engine: FocusEngine) -> None:
    engine.robot_event("docked")
    engine.start("Write", 25)
    assert cue(engine) == "dock"
    engine.robot_event("docked")
    assert cue(engine) == "start"
    assert engine.snapshot()["speech"]["seq"] == 2


def test_escalation_speaks_nudge_bark_run(engine: FocusEngine, clock: Clock) -> None:
    engine.start("Write", 25)
    engine.robot_event("docked")
    heard = cues_heard(engine, clock, lambda: report(engine, clock, NOT_FOCUSED, 12))
    assert heard == ["nudge", "bark", "run"]


def test_seq_only_ever_increases_and_a_cues_clips_rotate(engine: FocusEngine, clock: Clock, monkeypatch) -> None:
    import fido.engine as engine_module

    monkeypatch.setitem(PLAYLIST, "nudge", ["nudge-1", "woof-1", "bark-4"])
    monkeypatch.setattr(engine_module, "PLAYLIST", PLAYLIST)
    engine.start("Write", 25)
    engine.robot_event("docked")
    seen, seqs = [], []
    for _ in range(4):
        report(engine, clock, NOT_FOCUSED, 1)
        speech_now = engine.snapshot()["speech"]
        seen.append(speech_now["clip"])
        seqs.append(speech_now["seq"])
        report(engine, clock, FOCUSED, 1)
        clock.tick(20)  # past the nudge cooldown
    assert seen == ["nudge-1", "woof-1", "bark-4", "nudge-1"]  # in order, then around again
    assert seqs == sorted(set(seqs))  # strictly increasing


def test_a_cue_with_no_clips_is_silent(engine: FocusEngine, clock: Clock, monkeypatch) -> None:
    import fido.engine as engine_module

    monkeypatch.setitem(PLAYLIST, "bark", [])
    monkeypatch.setattr(engine_module, "PLAYLIST", PLAYLIST)
    engine.start("Write", 25)
    engine.robot_event("docked")
    report(engine, clock, NOT_FOCUSED, 1)
    assert cue(engine) == "nudge"
    version = engine.version
    report(engine, clock, NOT_FOCUSED, 6)  # reaches barking: no clip, so no new speech
    assert engine.snapshot()["phase"] in ("barking", "escaped")
    assert cue(engine) in ("nudge", "run")  # never "bark"


def test_flickering_camera_does_not_make_fido_chatter(engine: FocusEngine, clock: Clock) -> None:
    engine.start("Write", 25)
    engine.robot_event("docked")
    seq_before = engine.snapshot()["speech"]["seq"]
    for _ in range(5):  # distracted for 1 s, refocused for 1 s, repeatedly
        report(engine, clock, NOT_FOCUSED, 1)
        report(engine, clock, FOCUSED, 1)
    # One nudge and its single "good human"; the other four flickers stay silent.
    assert engine.snapshot()["speech"]["seq"] == seq_before + 2
    assert cue(engine) == "praise"


def test_praise_only_after_a_spoken_nudge(engine: FocusEngine, clock: Clock) -> None:
    engine.start("Write", 25)
    engine.robot_event("docked")
    report(engine, clock, NOT_FOCUSED, 1)
    assert cue(engine) == "nudge"
    report(engine, clock, FOCUSED, 1)
    assert cue(engine) == "praise"


def test_angry_line_repeats_while_escaped_then_caught(engine: FocusEngine, clock: Clock) -> None:
    engine.start("Write", 60)
    engine.robot_event("docked")
    report(engine, clock, NOT_FOCUSED, 12)
    assert cue(engine) == "run"
    first = engine.snapshot()["speech"]
    clock.tick(first["at"] + 3 - clock.now)
    engine.advance()
    assert engine.snapshot()["speech"]["seq"] == first["seq"]  # no early repeat
    clock.tick(1)
    engine.advance()
    assert engine.snapshot()["speech"]["seq"] == first["seq"] + 1  # exactly four seconds
    assert engine.snapshot()["speech"]["clip"] != first["clip"]

    engine.robot_event("caught")
    assert cue(engine) == "caught"


def test_run_barks_use_random_pool_without_repeating_and_stop_when_caught(engine, clock, monkeypatch):
    selections = []

    def choose(options):
        selections.append(list(options))
        return options[-1]

    monkeypatch.setattr("fido.engine.random.choice", choose)
    engine.start("Write", 60)
    engine.robot_event("docked")
    report(engine, clock, NOT_FOCUSED, 12)
    assert set(selections[0]) == {"run-5", "bark-4", "bark-5", "woof-1"}
    previous = engine.snapshot()["speech"]
    for _ in range(5):
        clock.tick(4)
        engine.advance()
        current = engine.snapshot()["speech"]
        assert previous["clip"] not in selections[-1]
        assert current["clip"] != previous["clip"]
        assert current["seq"] == previous["seq"] + 1
        previous = current
    engine.robot_event("caught")
    caught = engine.snapshot()["speech"]
    clock.tick(8)
    engine.advance()
    assert engine.snapshot()["speech"] == caught


def test_caught_from_the_app_also_speaks(engine: FocusEngine, clock: Clock) -> None:
    engine.start("Write", 60)
    engine.robot_event("docked")
    report(engine, clock, NOT_FOCUSED, 12)
    engine.apply("caught")
    assert cue(engine) == "caught"


def test_escape_timeout_and_completion_speak(clock: Clock) -> None:
    engine = FocusEngine(Settings(bark_after_seconds=5, escape_after_seconds=5), clock)
    engine.start("Write", 60)
    engine.robot_event("docked")
    report(engine, clock, NOT_FOCUSED, 12)
    clock.tick(600)
    engine.advance()
    assert cue(engine) == "timeout"

    short = FocusEngine(Settings(), clock)
    short.start("Write", 1)
    short.robot_event("docked")
    clock.tick(60)
    short.advance()
    assert cue(short) == "done"


def test_muted_voice_still_emits_caption_events(engine: FocusEngine, clock: Clock) -> None:
    engine.update_settings({"voice_enabled": False})
    engine.start("Write", 25)
    engine.robot_event("docked")
    report(engine, clock, NOT_FOCUSED, 12)
    assert cue(engine) == "run"
    assert engine.snapshot()["speech"]["seq"] == 5
    assert engine.snapshot()["speech"]["text"] == CAPTIONS[engine.speech["clip"]]

    assert engine.snapshot()["phase"] == "escaped"
    clock.tick(60)
    engine.advance()  # lets devices going stale settle
    seq = engine.snapshot()["speech"]["seq"]
    clock.tick(60)
    engine.advance()
    assert engine.snapshot()["speech"]["seq"] == seq + 1  # captions keep repeating


def test_muting_mid_escape_preserves_caption_sequence(engine: FocusEngine, clock: Clock) -> None:
    engine.start("Write", 60)
    engine.robot_event("docked")
    report(engine, clock, NOT_FOCUSED, 12)
    engine.update_settings({"voice_enabled": False})
    clock.tick(60)
    engine.advance()  # lets devices going stale settle
    version = engine.version
    seq = engine.snapshot()["speech"]["seq"]
    clock.tick(60)
    engine.advance()
    assert engine.snapshot()["speech"]["seq"] == seq + 1
    engine.update_settings({"voice_enabled": True})
    assert engine.snapshot()["speech"]["seq"] == seq + 1  # no new event on unmute


# ----- HTTP -----------------------------------------------------------------


@pytest.fixture
def client(clock: Clock, tmp_path, monkeypatch):
    import fido.app as app_module

    (tmp_path / "nudge-1.mp3").write_bytes(b"ID3fakemp3")
    monkeypatch.setattr(app_module, "CLIPS_DIR", str(tmp_path))
    return create_app(FocusEngine(clock=clock), start_ticker=False).test_client()


def test_audio_index_lists_only_playlist_clips_and_availability(client) -> None:
    clips = client.get("/api/audio").json["clips"]
    assert set(clips) == set(PLAYABLE)  # nothing from the unused generated script
    assert clips["nudge-1"]["available"] is True  # the fixture wrote this file
    assert clips["bark-4"]["available"] is False
    assert "bark" in clips["bark-5"]["cues"] and "run" in clips["bark-5"]["cues"]  # reused
    assert clips["nudge-1"]["caption"] == CAPTIONS["nudge-1"]


def test_audio_clip_is_served_with_cache_headers(client) -> None:
    res = client.get("/api/audio/nudge-1.mp3")
    assert res.status_code == 200
    assert res.mimetype == "audio/mpeg"
    assert res.data == b"ID3fakemp3"
    assert "max-age" in res.headers["Cache-Control"]


@pytest.mark.parametrize("name", ["nope-1", "..%2Fsecret", "nudge-99", "nudge-2", "praise-1", "bark-1"])
def test_unknown_or_missing_clip_is_404(client, name) -> None:
    assert client.get(f"/api/audio/{name}.mp3").status_code == 404


def test_speech_reaches_state_and_robot_command(client) -> None:
    response = client.post("/api/session", json={"task": "x", "duration_minutes": 25})
    assert response.status_code == 201
    speech = client.get("/api/state").json["speech"]
    assert speech["cue"] == "dock" and speech["url"].startswith("/api/audio/dock-")
    assert client.get("/api/robot/command").json["speech"] == speech


def test_voice_setting_over_http(client) -> None:
    assert client.patch("/api/settings", json={"voice_enabled": False}).json["voice_enabled"] is False
    assert client.patch("/api/settings", json={"speech_repeat_seconds": 0}).status_code == 400


def test_muted_robot_commands_consume_sequence_but_state_keeps_captions(client) -> None:
    client.post("/api/session", json={"task": "x", "duration_minutes": 25})
    client.patch("/api/settings", json={"voice_enabled": False})
    before = client.get("/api/state").json["speech"]
    muted = client.get("/api/robot/command").json["speech"]
    assert muted["clip"] == "" and muted["seq"] == before["seq"]
    response = client.post("/api/robot/events", json={"event": "docked"})
    assert response.status_code == 200
    assert response.json["speech"]["clip"] == ""
    after = client.get("/api/state").json["speech"]
    assert after["seq"] == before["seq"] + 1
    assert after["text"] == CAPTIONS[after["clip"]]
    assert response.json["speech"]["seq"] == after["seq"]
    client.patch("/api/settings", json={"voice_enabled": True})
    assert client.get("/api/robot/command").json["speech"]["seq"] == after["seq"]
