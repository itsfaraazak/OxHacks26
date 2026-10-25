import os

from audio.player import CLIP_NAME, CueTracker, Fido, Speaker, find_player, parse_sse


def snap(seq=None, clip="nudge-1", online=False, speaker=None):
    speech = None if seq is None else {"seq": seq, "cue": clip.split("-")[0], "clip": clip, "url": f"/api/audio/{clip}.mp3"}
    info = {} if speaker is None else {"speaker": speaker}
    return {"speech": speech, "robot": {"online": online, "info": info}}


def plays(tracker, snapshot):
    decision = tracker.decide(snapshot)
    return bool(decision and decision.play)


# --- exactly once ---


def test_first_snapshot_is_recorded_not_played():
    t = CueTracker()
    d = t.decide(snap(5))
    assert d is not None and not d.play
    assert t.seq == 5
    assert t.decide(snap(5)) is None


def test_plays_each_new_seq_once():
    t = CueTracker()
    assert t.decide(snap(None)) is None  # first snapshot, no cue yet
    assert plays(t, snap(1, "dock-1"))
    assert t.decide(snap(1, "dock-1")) is None  # same cue in a later state event
    assert plays(t, snap(2, "start-1"))
    assert t.decide(snap(1, "dock-1")) is None  # never goes backwards


def test_first_cue_after_empty_start_is_played():
    t = CueTracker()
    t.decide(snap(None))
    assert plays(t, snap(1))


def test_reconnect_does_not_replay_but_plays_what_is_new():
    t = CueTracker()
    t.decide(snap(1))
    assert plays(t, snap(2))
    t.reset()
    assert not plays(t, snap(2))  # first snapshot after reconnect
    assert plays(t, snap(3))


# --- silent while the robot speaks ---


def test_global_mute_consumes_sequence_without_replay():
    t = CueTracker()
    t.decide(snap(None))
    muted = {**snap(1), "settings": {"voice_enabled": False}}
    decision = t.decide(muted)
    assert decision is not None and not decision.play and "muted" in decision.reason
    assert t.seq == 1
    assert t.decide(snap(1)) is None
    assert plays(t, snap(2))


def test_muting_stops_current_audio_even_without_new_cue(tmp_path, monkeypatch):
    speaker = FakeSpeaker()
    stopped = []
    monkeypatch.setattr(speaker, "stop", lambda: stopped.append(True), raising=False)
    fido = Fido("http://x", None, speaker, str(tmp_path))
    fido.handle(snap(1))
    fido.handle({**snap(1), "settings": {"voice_enabled": False}})
    assert stopped == [True]
    assert speaker.played == []


def test_silent_when_robot_online_with_speaker_connected_but_seq_tracked():
    t = CueTracker()
    t.decide(snap(None))
    d = t.decide(snap(1, online=True, speaker="connected"))
    assert d is not None and not d.play and "robot" in d.reason
    # The robot's speaker drops: the old cue must not replay, the next one plays.
    assert t.decide(snap(1, online=False, speaker="connected")) is None
    assert plays(t, snap(2, online=True, speaker="disconnected"))


def test_plays_when_robot_offline_or_no_speaker():
    for online, speaker in [(False, "connected"), (True, "disconnected"), (True, "none"), (True, None)]:
        t = CueTracker()
        t.decide(snap(None))
        assert plays(t, snap(1, online=online, speaker=speaker)), (online, speaker)


def test_missing_robot_plays():
    t = CueTracker()
    t.decide({"speech": None})
    assert plays(t, {"speech": {"seq": 1, "cue": "dock", "clip": "dock-1"}})


# --- clip names ---


def test_clip_name_validation():
    for good in ["nudge-1", "dock-12"]:
        assert CLIP_NAME.fullmatch(good)
    for bad in ["", "../x-1", "nudge", "Nudge-1", "nudge-1/../a", "nudge-1.mp3", "a-1\n", "nudge_1"]:
        assert not CLIP_NAME.fullmatch(bad), bad


def test_unsafe_clip_is_skipped_but_tracked():
    t = CueTracker()
    t.decide(snap(None))
    d = t.decide(snap(1, clip="../../etc-1"))
    assert d is not None and not d.play and "unsafe" in d.reason
    assert t.seq == 1


# --- SSE parsing ---

STREAM = (
    b": keepalive\n\n"
    b"id: 1\nevent: state\ndata: {\"a\":1}\n\n"
    b": keepalive\r\n\r\n"
    b"event: state\r\ndata: {\"a\":\r\ndata: 2}\r\n\r\n"
    b"data:no-space\n\n"
    b"event: state\ndata: cut off by a dropped connection\n"
)


def test_parse_sse():
    events = list(parse_sse(STREAM.splitlines(keepends=True)))
    assert events == [("state", '{"a":1}'), ("state", '{"a":\n2}'), ("message", "no-space")]


# --- interrupting, caching, players ---


class FakeProcess:
    def __init__(self, command):
        self.command, self.terminated = command, False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True


def test_new_clip_interrupts_the_one_playing():
    started = []

    def popen(command, **kwargs):
        started.append(FakeProcess(command))
        return started[-1]

    speaker = Speaker(["afplay"], popen)
    speaker.play("/tmp/a.mp3")
    speaker.play("/tmp/b.mp3")
    assert started[0].terminated and not started[1].terminated
    assert started[1].command == ["afplay", "/tmp/b.mp3"]
    speaker.stop()
    assert started[1].terminated


def test_finished_clip_is_not_terminated():
    proc = FakeProcess(["x"])
    proc.poll = lambda: 0
    speaker = Speaker(["afplay"], lambda command, **kw: proc)
    speaker.play("a")
    speaker.stop()
    assert not proc.terminated


def test_find_player_prefers_afplay_then_ffplay_then_mpg123():
    assert find_player(lambda n: "/x" if n in {"afplay", "ffplay", "mpg123"} else None) == ["afplay"]
    assert find_player(lambda n: "/x" if n in {"ffplay", "mpg123"} else None)[0] == "ffplay"
    assert find_player(lambda n: "/x" if n == "mpg123" else None) == ["mpg123", "-q"]
    assert find_player(lambda n: None) is None


class FakeSpeaker:
    def __init__(self):
        self.played = []

    def play(self, path):
        self.played.append(path)


def test_handle_downloads_once_and_plays_and_interrupt_flow(tmp_path, monkeypatch):
    fido = Fido("http://x", None, FakeSpeaker(), str(tmp_path))
    downloads = []

    class Response:
        def __init__(self, url):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b"mp3"

    def urlopen(request, timeout):
        downloads.append(request.full_url)
        return Response(request.full_url)

    monkeypatch.setattr("audio.player.urllib.request.urlopen", urlopen)
    fido.handle(snap(None))
    fido.handle(snap(1, "nudge-1"))
    fido.handle(snap(2, "nudge-1"))  # a repeated clip comes from the cache
    fido.handle(snap(3, "bark-2", online=True, speaker="connected"))  # silent: no download, no play
    assert downloads == ["http://x/api/audio/nudge-1.mp3"]
    assert fido.speaker.played == [os.path.join(tmp_path, "nudge-1.mp3")] * 2


def test_token_sent_and_dry_run_does_not_play(tmp_path, monkeypatch, capsys):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b"mp3"

    monkeypatch.setattr("audio.player.urllib.request.urlopen", lambda r, timeout: seen.append(r) or Response())
    fido = Fido("http://x/", "secret", None, str(tmp_path))
    fido.handle(snap(None))
    fido.handle(snap(1, "dock-1"))
    assert seen[0].get_header("Authorization") == "Bearer secret"
    assert "dry-run cue=dock clip=dock-1" in capsys.readouterr().out
