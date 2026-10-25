"""Tests for scripts/generate_audio.py. No test makes a real network call."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from fido.speech import CLIPS

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "generate_audio.py"
spec = importlib.util.spec_from_file_location("generate_audio", SCRIPT_PATH)
ga = importlib.util.module_from_spec(spec)
sys.modules["generate_audio"] = ga  # dataclasses needs the module registered
spec.loader.exec_module(ga)

SECRET = "sk_test_SUPERSECRETVALUE123"
FAKE_MP3 = b"ID3\x04fake-mp3-bytes" * 10
BARK_MP3 = b"\xff\xf3bark-bytes" * 10
JOINED_MP3 = b"\xff\xf3joined-mp3-bytes" * 50


def no_network(*_a, **_k):
    raise AssertionError("network call attempted")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeApi:
    """Stands in for urllib.request.urlopen and records the calls."""

    def __init__(self, fail_first: int | None = None):
        self.calls: list[tuple[str, dict, dict]] = []
        self.fail_first = fail_first  # fail this many first calls with HTTP 429

    def __call__(self, request, timeout=None):
        if self.fail_first:
            self.fail_first -= 1
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, io.BytesIO(b'{"detail":"slow down"}'))
        body = json.loads(request.data)
        self.calls.append((request.full_url, body, dict(request.header_items())))
        url = request.full_url
        if "/v1/text-to-voice/design" in url:
            return FakeResponse(json.dumps({"previews": [{"generated_voice_id": "gen-1"}]}).encode())
        if url.endswith("/v1/text-to-voice"):
            return FakeResponse(json.dumps({"voice_id": "voice-abc"}).encode())
        return FakeResponse(FAKE_MP3)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(ga.time, "sleep", lambda _s: None)


class FakeFfmpeg:
    """Stands in for the ffmpeg subprocess: records the command, writes JOINED_MP3 to its output."""

    def __init__(self, returncode: int = 0):
        self.calls: list[list[str]] = []
        self.inputs: list[list[bytes]] = []
        self.returncode = returncode

    def __call__(self, cmd, **_kwargs):
        self.calls.append(list(cmd))
        self.inputs.append([Path(cmd[i + 1]).read_bytes() for i, a in enumerate(cmd) if a == "-i"])
        if self.returncode == 0:
            Path(cmd[-1]).write_bytes(JOINED_MP3)
        return subprocess.CompletedProcess(cmd, self.returncode, b"", b"Invalid data found" if self.returncode else b"")


@pytest.fixture(autouse=True)
def ffmpeg(monkeypatch):
    """ffmpeg is installed and fake by default; tests never need the real one."""
    fake = FakeFfmpeg()
    monkeypatch.setattr(ga.shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(ga.subprocess, "run", fake)
    monkeypatch.setattr(ga, "probe_duration", lambda _path: None)
    return fake


def run_cli(tmp_path, *argv, environ=None):
    clips = tmp_path / "clips"
    args = ["--clips-dir", str(clips), "--env-file", str(tmp_path / ".env"), *argv]
    return ga.main(args, environ={} if environ is None else environ), clips


def test_dry_run_makes_no_network_calls_and_needs_no_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    code, clips = run_cli(tmp_path, "--dry-run")
    assert code == 0
    assert "Dry run" in capsys.readouterr().out
    assert not clips.exists()


def test_cost_estimate_matches_the_real_script(tmp_path):
    _, todo = ga.build_plan(tmp_path, None, False, None)
    chars, credits = ga.totals(todo)
    assert len(todo) == len(CLIPS) == 31
    # Hand-computed from fido/speech.py: 1,694 characters of speech; 7 spoken lines
    # carry a bark and 3 lines are pure sound effects, each 2.5 s x 40 = 100 credits.
    assert chars == 1694
    assert sum(1 for i in todo if i.has_bark) == 7 and sum(1 for i in todo if i.kind == "sfx") == 3
    assert credits == 1694 + (7 + 3) * 100 == 2694
    assert chars == sum(len(line.voice) for _, line in CLIPS.values() if line.voice is not None)


def test_barked_line_costs_speech_plus_bark_and_dry_run_says_so(tmp_path, capsys):
    item = {i.name: i for i in ga.build_plan(tmp_path, None, False, None)[0]}["bark-1"]
    assert item.has_bark and item.label == "speech + bark"
    assert item.credits == len(item.line.voice) + ga.SFX_SECONDS * ga.SFX_CREDITS_PER_SECOND
    plain = {i.name: i for i in ga.build_plan(tmp_path, None, False, None)[0]}["praise-1"]
    assert not plain.has_bark and plain.label == "speech" and plain.credits == len(plain.line.voice)
    run_cli(tmp_path, "--dry-run")
    out = capsys.readouterr().out
    assert "speech + bark" in out and "2.5s bark" in out
    design = len(ga.VOICE_PREVIEW_TEXT)  # the preview text is billed per character
    assert f"~{2694 + design:,} credits (2,694 clips + {design} voice design)" in out


def test_dry_run_summary_includes_voice_design_on_first_run(tmp_path, capsys):
    _, todo = ga.build_plan(tmp_path, None, False, None)
    chars, credits = ga.totals(todo)
    run_cli(tmp_path, "--dry-run")
    out = capsys.readouterr().out
    assert f"Total: {chars} characters, ~{credits + len(ga.VOICE_PREVIEW_TEXT):,.0f} credits" in out


def test_env_parsing_and_no_leak(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(f'# comment\nOTHER=1\nexport ELEVENLABS_API_KEY="{SECRET}"\n')
    assert ga.parse_env_file(env)["ELEVENLABS_API_KEY"] == SECRET
    assert ga.load_api_key({}, env) == SECRET
    assert ga.load_api_key({"ELEVENLABS_API_KEY": "from-env"}, env) == "from-env"
    assert ga.parse_env_file(tmp_path / "missing.env") == {}


def test_missing_key_exits_with_clear_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    code, _ = run_cli(tmp_path)
    assert code == 1
    assert "ELEVENLABS_API_KEY is not set" in capsys.readouterr().err


def test_api_errors_never_include_the_key_or_headers(tmp_path, monkeypatch, capsys):
    def reject(request, timeout=None):
        body = json.dumps({"detail": {"status": "invalid", "message": f"bad key {SECRET}"}}).encode()
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {"xi-api-key": SECRET}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", reject)
    code, _ = run_cli(tmp_path, "--only", "praise", environ={"ELEVENLABS_API_KEY": SECRET})
    captured = capsys.readouterr()
    assert code == 1
    assert SECRET not in captured.out + captured.err
    assert "HTTP 401" in captured.err


def test_skip_existing_only_force_and_limit(tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "nudge-1.mp3").write_bytes(b"x")
    _, todo = ga.build_plan(clips, ["nudge", "run"], False, None)
    names = [i.name for i in todo]
    assert "nudge-1" not in names
    assert {i.cue for i in todo} == {"nudge", "run"}
    _, forced = ga.build_plan(clips, ["nudge"], True, None)
    assert [i.name for i in forced][0] == "nudge-1"
    _, limited = ga.build_plan(clips, None, False, 3)
    assert len(limited) == 3
    with pytest.raises(ga.GenerateError):
        ga.build_plan(clips, ["nope"], False, None)


def test_end_to_end_with_fake_api(tmp_path, monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(urllib.request, "urlopen", api)
    code, clips = run_cli(tmp_path, "--only", "bark,praise", environ={"ELEVENLABS_API_KEY": SECRET})
    assert code == 0
    expected = [n for n, (cue, _) in CLIPS.items() if cue in ("bark", "praise")]
    assert sorted(p.stem for p in clips.glob("*.mp3")) == sorted(expected)
    assert not list(clips.glob(".*.tmp"))
    assert json.loads((clips / "voice.json").read_text())["voice_id"] == "voice-abc"

    manifest = json.loads((clips / "manifest.json").read_text())
    by_name = {c["name"]: c for c in manifest["clips"]}
    assert set(by_name) == set(expected)
    entry = by_name["praise-1"]
    assert entry["cue"] == "praise" and entry["caption"] == CLIPS["praise-1"][1].caption
    assert entry["bytes"] == len(FAKE_MP3)
    assert entry["sha256"] == hashlib.sha256(FAKE_MP3).hexdigest()
    assert entry["duration_seconds"] > 0

    # Voice design, voice creation, 6 speech clips; 2 sound-effect clips plus the 3
    # barks that are joined in front of bark-1..3.
    urls = [c[0] for c in api.calls]
    assert urls[0].endswith("/v1/text-to-voice/design") and urls[1].endswith("/v1/text-to-voice")
    speech = [c for c in api.calls if "/v1/text-to-speech/voice-abc" in c[0]]
    sfx = [c for c in api.calls if "/v1/sound-generation" in c[0]]
    assert len(speech) == 6 and len(sfx) == 5
    assert speech[0][1]["model_id"] == "eleven_v3" and "output_format=mp3_22050_32" in speech[0][0]
    assert sfx[0][1]["duration_seconds"] == ga.SFX_SECONDS and sfx[0][1]["prompt_influence"] == 0.5
    # Only fixed script text is sent.
    sent = {c[1].get("text") for c in speech} | {c[1].get("text") for c in sfx}
    script_text = {t for _, line in CLIPS.values() for t in (line.voice, line.sfx, line.bark) if t}
    assert sent <= script_text
    assert all(h.get("Xi-api-key") == SECRET for _, _, h in api.calls)

    # Second run: everything exists, voice reused, no new calls.
    calls_before = len(api.calls)
    code, _ = run_cli(tmp_path, "--only", "bark,praise", environ={"ELEVENLABS_API_KEY": SECRET})
    assert code == 0 and len(api.calls) == calls_before


def test_saved_voice_and_override_skip_design(tmp_path, monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(urllib.request, "urlopen", api)
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "voice.json").write_text(json.dumps({"voice_id": "saved-id"}))
    run_cli(tmp_path, "--only", "praise", "--limit", "1", environ={"ELEVENLABS_API_KEY": SECRET})
    assert "/v1/text-to-speech/saved-id" in api.calls[0][0] and len(api.calls) == 1
    run_cli(tmp_path, "--only", "praise", "--limit", "1", "--force", "--voice-id", "other", environ={"ELEVENLABS_API_KEY": SECRET})
    assert "/v1/text-to-speech/other" in api.calls[1][0]


def test_retries_once_on_429_then_succeeds_and_gives_up_after(monkeypatch):
    api = FakeApi(fail_first=1)
    monkeypatch.setattr(urllib.request, "urlopen", api)
    assert ga.Client(SECRET).post("/v1/sound-generation", {"text": "x"}) == FAKE_MP3
    api = FakeApi(fail_first=2)
    monkeypatch.setattr(urllib.request, "urlopen", api)
    with pytest.raises(ga.GenerateError, match="429"):
        ga.Client(SECRET).post("/v1/sound-generation", {"text": "x"})


def test_failed_clip_leaves_no_partial_file_and_keeps_earlier_clips(tmp_path, monkeypatch, capsys):
    api = FakeApi()
    state = {"n": 0}

    def flaky(request, timeout=None):
        if "/v1/text-to-speech/" in request.full_url:
            state["n"] += 1
            if state["n"] == 2:
                raise urllib.error.HTTPError(request.full_url, 400, "Bad", {}, io.BytesIO(b'{"detail":"nope"}'))
        return api(request, timeout)

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    code, clips = run_cli(tmp_path, "--only", "praise", environ={"ELEVENLABS_API_KEY": SECRET})
    assert code == 1
    assert (clips / "praise-1.mp3").exists() and not (clips / "praise-2.mp3").exists()
    assert not list(clips.glob(".*.tmp"))
    assert [c["name"] for c in json.loads((clips / "manifest.json").read_text())["clips"]] == ["praise-1"]


# --- barks joined to speech -----------------------------------------------------


def urls_of(api):
    return [("sfx" if "/v1/sound-generation" in c[0] else "tts") for c in api.calls]


def test_barked_line_requests_bark_then_speech_then_joins_once(tmp_path, monkeypatch, ffmpeg):
    api = FakeApi()

    def urlopen(request, timeout=None):
        response = api(request, timeout)
        return FakeResponse(BARK_MP3) if "/v1/sound-generation" in request.full_url else response

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    code, clips = run_cli(tmp_path, "--only", "bark", "--limit", "1", "--voice-id", "v1", environ={"ELEVENLABS_API_KEY": SECRET})
    assert code == 0
    line = CLIPS["bark-1"][1]
    assert urls_of(api) == ["sfx", "tts"]
    (sfx_url, sfx_body, _), (tts_url, tts_body, _) = api.calls
    assert "output_format=mp3_22050_32" in sfx_url and "output_format=mp3_22050_32" in tts_url
    assert sfx_body == {"text": line.bark, "duration_seconds": ga.SFX_SECONDS, "prompt_influence": ga.SFX_PROMPT_INFLUENCE}
    assert tts_body["text"] == line.voice and tts_body["model_id"] == "eleven_v3"

    assert len(ffmpeg.calls) == 1
    assert ffmpeg.inputs[0] == [BARK_MP3, FAKE_MP3]  # bark first, then speech
    cmd = " ".join(ffmpeg.calls[0])
    assert f"apad=pad_dur={ga.BARK_GAP_SECONDS}" in cmd and "concat=n=2:v=0:a=1" in cmd
    assert "-b:a 32k" in cmd and "-ar 22050" in cmd and "-ac 1" in cmd and "libmp3lame" in cmd
    assert (clips / "bark-1.mp3").read_bytes() == JOINED_MP3
    assert not list(clips.glob(".*.tmp"))


def test_bark_is_the_first_input_and_gets_the_gap(tmp_path, monkeypatch, ffmpeg):
    api = FakeApi()
    monkeypatch.setattr(urllib.request, "urlopen", api)
    item = ga.build_plan(tmp_path, ["bark"], False, 1)[1][0]
    ga.generate_clip(ga.Client(SECRET), item, "v1", "mp3_22050_32")
    graph = ffmpeg.calls[0][ffmpeg.calls[0].index("-filter_complex") + 1]
    assert graph.startswith("[0:a]") and "apad" in graph.split(";")[0] and "apad" not in graph.split(";")[1]


def test_plain_speech_and_sfx_lines_do_not_join(tmp_path, monkeypatch, ffmpeg):
    api = FakeApi()
    monkeypatch.setattr(urllib.request, "urlopen", api)
    plain = [i for i in ga.build_plan(tmp_path, None, False, None)[0] if not i.has_bark]
    assert {i.kind for i in plain} == {"speech", "sfx"}
    for item in plain:
        assert ga.generate_clip(ga.Client(SECRET), item, "v1", "mp3_22050_32") == FAKE_MP3
    assert ffmpeg.calls == []


def test_joined_clip_duration_in_manifest_uses_the_size_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", FakeApi())
    code, clips = run_cli(tmp_path, "--only", "bark", "--voice-id", "v1", environ={"ELEVENLABS_API_KEY": SECRET})
    assert code == 0
    by_name = {c["name"]: c for c in json.loads((clips / "manifest.json").read_text())["clips"]}
    # joined clips are re-encoded at the constant 32 kbps, so bytes / 4000 = seconds
    assert by_name["bark-1"]["bytes"] == len(JOINED_MP3)
    assert by_name["bark-1"]["duration_seconds"] == round(len(JOINED_MP3) * 8 / 32000, 2)
    assert by_name["bark-4"]["duration_seconds"] == round(len(FAKE_MP3) * 8 / 32000, 2)


def test_manifest_prefers_ffprobe_duration_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(ga, "probe_duration", lambda _path: 3.213)
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "praise-1.mp3").write_bytes(FAKE_MP3)
    ga.write_manifest(clips, "mp3_22050_32")
    assert json.loads((clips / "manifest.json").read_text())["clips"][0]["duration_seconds"] == 3.21


def test_probe_duration_falls_back_when_ffprobe_is_missing_or_fails(tmp_path, monkeypatch):
    monkeypatch.undo()  # drop the autouse fakes; this test installs its own
    monkeypatch.setattr(ga.shutil, "which", lambda _n: None)
    assert ga.probe_duration(tmp_path / "x.mp3") is None
    monkeypatch.setattr(ga.shutil, "which", lambda n: "/fake/ffprobe")
    monkeypatch.setattr(ga.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, b"", b"bad"))
    assert ga.probe_duration(tmp_path / "x.mp3") is None
    monkeypatch.setattr(ga.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, b"3.25\n", b""))
    assert ga.probe_duration(tmp_path / "x.mp3") == 3.25


def test_missing_ffmpeg_fails_clearly_before_any_api_call_when_a_join_is_needed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ga.shutil, "which", lambda _n: None)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    code, clips = run_cli(tmp_path, "--only", "bark", environ={"ELEVENLABS_API_KEY": SECRET})
    captured = capsys.readouterr()
    assert code == 1
    assert "brew install ffmpeg" in captured.err
    assert SECRET not in captured.out + captured.err
    assert not clips.exists() or not list(clips.glob("*"))


def test_missing_ffmpeg_does_not_matter_without_barked_clips(tmp_path, monkeypatch):
    monkeypatch.setattr(ga.shutil, "which", lambda _n: None)
    monkeypatch.setattr(urllib.request, "urlopen", FakeApi())
    # praise has no barks; bark-4/bark-5 are pure sound effects
    code, clips = run_cli(tmp_path, "--only", "praise", "--voice-id", "v1", environ={"ELEVENLABS_API_KEY": SECRET})
    assert code == 0 and len(list(clips.glob("praise-*.mp3"))) == 3
    item = {i.name: i for i in ga.build_plan(tmp_path, None, False, None)[0]}["bark-4"]
    assert ga.generate_clip(ga.Client(SECRET), item, "v1", "mp3_22050_32") == FAKE_MP3
    assert ga.main(["--clips-dir", str(clips), "--env-file", str(tmp_path / ".env"), "--only", "praise", "--dry-run"], {}) == 0


def test_dry_run_needs_no_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(ga.shutil, "which", lambda _n: None)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    code, _ = run_cli(tmp_path, "--dry-run")
    assert code == 0


def test_ffmpeg_failure_leaves_no_partial_files_and_keeps_earlier_clips(tmp_path, monkeypatch, ffmpeg, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", FakeApi())
    state = {"n": 0}

    def flaky(cmd, **kwargs):
        state["n"] += 1
        return FakeFfmpeg(returncode=1 if state["n"] == 2 else 0)(cmd, **kwargs)

    monkeypatch.setattr(ga.subprocess, "run", flaky)
    code, clips = run_cli(tmp_path, "--only", "bark", "--voice-id", "v1", environ={"ELEVENLABS_API_KEY": SECRET})
    captured = capsys.readouterr()
    assert code == 1
    assert (clips / "bark-1.mp3").exists() and not (clips / "bark-2.mp3").exists()
    assert not list(clips.glob(".*.tmp")) and not list(clips.glob("*.part"))
    assert "ffmpeg could not join a clip: Invalid data found" in captured.err
    assert SECRET not in captured.out + captured.err
    assert [c["name"] for c in json.loads((clips / "manifest.json").read_text())["clips"]] == ["bark-1"]


def test_join_temp_files_are_cleaned_up(tmp_path, monkeypatch, ffmpeg):
    seen = []
    orig = ffmpeg.__call__

    def spy(cmd, **kw):
        seen.append(Path(cmd[-1]).parent)
        return orig(cmd, **kw)

    monkeypatch.setattr(ga.subprocess, "run", spy)
    assert ga.join_mp3([FAKE_MP3, FAKE_MP3], "mp3_22050_32") == JOINED_MP3
    assert seen and not seen[0].exists()


def test_join_rejects_formats_without_a_known_rate(monkeypatch):
    with pytest.raises(ga.GenerateError, match="mp3_<rate>_<kbps>"):
        ga.join_mp3([FAKE_MP3, FAKE_MP3], "pcm_16000")


def test_key_never_appears_in_ffmpeg_command_or_output(tmp_path, monkeypatch, ffmpeg, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", FakeApi())
    run_cli(tmp_path, "--only", "bark", "--limit", "1", "--voice-id", "v1", environ={"ELEVENLABS_API_KEY": SECRET})
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert all(SECRET not in part for cmd in ffmpeg.calls for part in cmd)




# --- --audition / --choose ---------------------------------------------------


class AuditionApi:
    """Voice Design returns `previews_per_design` previews with unique ids and base64 audio."""

    def __init__(self, previews_per_design: int = 3):
        self.calls: list[tuple[str, dict]] = []
        self.n = previews_per_design
        self.count = 0

    def __call__(self, request, timeout=None):
        body = json.loads(request.data)
        self.calls.append((request.full_url, body))
        if request.full_url.endswith("/v1/text-to-voice/design"):
            previews = []
            for _ in range(self.n):
                self.count += 1
                audio = FAKE_MP3 + str(self.count).encode()
                previews.append(
                    {
                        "generated_voice_id": f"gen-{self.count}",
                        "audio_base_64": base64.b64encode(audio).decode(),
                        "media_type": "audio/mpeg",
                        "duration_secs": 1.0,
                    }
                )
            return FakeResponse(json.dumps({"previews": previews, "text": body["text"]}).encode())
        if request.full_url.endswith("/v1/text-to-voice"):
            return FakeResponse(json.dumps({"voice_id": "voice-new"}).encode())
        raise AssertionError(f"unexpected endpoint {request.full_url}")


ENV = {"ELEVENLABS_API_KEY": SECRET}


def test_audition_saves_every_preview_and_an_index_without_saving_a_voice(tmp_path, monkeypatch, capsys):
    api = AuditionApi(previews_per_design=3)
    monkeypatch.setattr(urllib.request, "urlopen", api)
    code, clips = run_cli(tmp_path, "--audition", environ=ENV)
    out = capsys.readouterr().out
    assert code == 0

    candidates = list(ga.AUDITION_DESCRIPTIONS)
    assert len(candidates) == 3 and len(set(ga.AUDITION_DESCRIPTIONS.values())) == 3
    assert all(d.endswith("Perfect audio quality.") for d in ga.AUDITION_DESCRIPTIONS.values())

    # One design call per candidate, with the shared preview text; nothing else is called.
    assert [c[0].rsplit("/v1", 1)[1] for c in api.calls] == ["/text-to-voice/design"] * 3
    assert [c[1]["voice_description"] for c in api.calls] == list(ga.AUDITION_DESCRIPTIONS.values())
    assert all(c[1]["text"] == ga.VOICE_PREVIEW_TEXT for c in api.calls)

    auditions = clips / "auditions"
    expected = [f"{c}-{n}.mp3" for c in candidates for n in (1, 2, 3)]
    assert sorted(p.name for p in auditions.glob("*.mp3")) == sorted(expected)
    index = json.loads((auditions / "index.json").read_text())
    assert [e["file"] for e in index["previews"]] == expected
    for i, entry in enumerate(index["previews"], 1):
        assert entry["generated_voice_id"] == f"gen-{i}"
        assert entry["description"] == ga.AUDITION_DESCRIPTIONS[entry["candidate"]]
        assert (auditions / entry["file"]).read_bytes() == FAKE_MP3 + str(i).encode()  # base64 decoded
    assert not list(auditions.glob(".*.tmp"))

    # No voice saved and voice.json untouched.
    assert not (clips / "voice.json").exists()
    assert not any(c[0].endswith("/v1/text-to-voice") for c in api.calls)

    assert f"~{3 * len(ga.VOICE_PREVIEW_TEXT)} credits" in out.replace(",", "")
    assert "afplay" in out and "bulldog-1.mp3" in out
    assert SECRET not in out


def test_audition_leaves_an_existing_voice_json_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", AuditionApi())
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "voice.json").write_text('{"voice_id": "keep-me"}')
    run_cli(tmp_path, "--audition", environ=ENV)
    assert json.loads((clips / "voice.json").read_text()) == {"voice_id": "keep-me"}


def test_audition_keeps_what_was_paid_for_when_a_later_design_fails(tmp_path, monkeypatch, capsys):
    api = AuditionApi(previews_per_design=2)

    def flaky(request, timeout=None):
        if len(api.calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 400, "Bad", {}, io.BytesIO(b'{"detail":"nope"}'))
        return api(request, timeout)

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    code, clips = run_cli(tmp_path, "--audition", environ=ENV)
    captured = capsys.readouterr()
    assert code == 1 and "HTTP 400" in captured.err
    first = next(iter(ga.AUDITION_DESCRIPTIONS))
    index = json.loads((clips / "auditions" / "index.json").read_text())
    assert [e["file"] for e in index["previews"]] == [f"{first}-1.mp3", f"{first}-2.mp3"]
    # Only the one design request that succeeded is counted in the cost line.
    assert f"~{len(ga.VOICE_PREVIEW_TEXT)} credits (estimate: {len(ga.VOICE_PREVIEW_TEXT)} chars per design request, 1 request(s)" in captured.out


def test_audition_rejects_a_malformed_preview(tmp_path, monkeypatch, capsys):
    def bad(request, timeout=None):
        return FakeResponse(json.dumps({"previews": [{"generated_voice_id": "g", "audio_base_64": "!!!"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", bad)
    code, clips = run_cli(tmp_path, "--audition", environ=ENV)
    assert code == 1 and "no usable" in capsys.readouterr().err
    assert not (clips / "auditions").exists() or not list((clips / "auditions").glob("*.mp3"))


def test_audition_needs_a_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    code, _ = run_cli(tmp_path, "--audition")
    assert code == 1 and "ELEVENLABS_API_KEY is not set" in capsys.readouterr().err


def audition_first(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", AuditionApi())
    return run_cli(tmp_path, "--audition", environ=ENV)[1]


def test_choose_saves_the_voice_and_writes_voice_json(tmp_path, monkeypatch, capsys):
    clips = audition_first(tmp_path, monkeypatch)
    api = AuditionApi()
    monkeypatch.setattr(urllib.request, "urlopen", api)
    code, _ = run_cli(tmp_path, "--choose", "gen-4", environ=ENV)  # first preview of the second candidate
    out = capsys.readouterr().out
    assert code == 0 and len(api.calls) == 1
    url, body = api.calls[0]
    assert url.endswith("/v1/text-to-voice")
    chosen = list(ga.AUDITION_DESCRIPTIONS.values())[1]
    assert body == {"voice_name": ga.VOICE_NAME, "voice_description": chosen, "generated_voice_id": "gen-4"}
    assert json.loads((clips / "voice.json").read_text()) == {
        "voice_id": "voice-new",
        "voice_name": ga.VOICE_NAME,
        "description": chosen,
    }
    assert "voice-new" in out and SECRET not in out


def test_choose_rejects_unknown_ids_and_a_missing_index(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    code, _ = run_cli(tmp_path, "--choose", "gen-1", environ=ENV)
    assert code == 1 and "Run --audition first" in capsys.readouterr().err

    clips = audition_first(tmp_path, monkeypatch)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    code, _ = run_cli(tmp_path, "--choose", "not-an-id", environ=ENV)
    assert code == 1 and "is not in" in capsys.readouterr().err
    assert not (clips / "voice.json").exists()

    (clips / "auditions" / "index.json").write_text("not json")
    code, _ = run_cli(tmp_path, "--choose", "gen-1", environ=ENV)
    assert code == 1 and "not a valid audition index" in capsys.readouterr().err


def test_audition_and_choose_dry_runs_make_no_network_calls_and_need_no_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    code, clips = run_cli(tmp_path, "--audition", "--dry-run")
    out = capsys.readouterr().out
    assert code == 0 and "Dry run" in out and f"~{3 * len(ga.VOICE_PREVIEW_TEXT)} credits" in out.replace(",", "")
    assert not clips.exists()

    audition_first_dir = clips / "auditions"
    audition_first_dir.mkdir(parents=True)
    ga.write_json(
        audition_first_dir / "index.json",
        {"previews": [{"file": "x-1.mp3", "candidate": "x", "description": "d", "generated_voice_id": "gen-9"}]},
    )
    code, _ = run_cli(tmp_path, "--choose", "gen-9", "--dry-run")
    assert code == 0 and "would save x-1.mp3" in capsys.readouterr().out
    assert not (clips / "voice.json").exists()
    code, _ = run_cli(tmp_path, "--choose", "nope", "--dry-run")
    assert code == 1  # ids are validated even in a dry run


def test_audition_and_choose_together_is_an_error(tmp_path, capsys):
    code, _ = run_cli(tmp_path, "--audition", "--choose", "gen-1", "--dry-run")
    assert code == 1 and "only one of" in capsys.readouterr().err


def test_audition_errors_never_include_the_key(tmp_path, monkeypatch, capsys):
    def reject(request, timeout=None):
        body = json.dumps({"detail": {"message": f"bad key {SECRET}"}}).encode()
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", reject)
    code, _ = run_cli(tmp_path, "--audition", environ=ENV)
    captured = capsys.readouterr()
    assert code == 1 and SECRET not in captured.out + captured.err


# --- voice clone, recorded barks, provenance ---------------------------------


def wav_bytes(seconds: float, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with ga.wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def clone_setup(tmp_path, consent=True, seconds=(30, 30, 10)):
    rec = tmp_path / "recordings"
    (rec / "clone-samples").mkdir(parents=True)
    for n, sec in enumerate(seconds):
        (rec / "clone-samples" / f"s{n}.wav").write_bytes(wav_bytes(sec))
    if consent:
        (rec / "consent.json").write_text(json.dumps({"name": "Dheer", "agreed_at": "2026-05-01T10:00:00Z"}))
    return rec


def run_clone_cli(tmp_path, *argv, environ=None):
    return run_cli(tmp_path, "--recordings-dir", str(tmp_path / "recordings"), "--clone", *argv, environ=environ)


def test_clone_builds_multipart_request_and_saves_voice_json(tmp_path, monkeypatch, capsys):
    clone_setup(tmp_path)
    seen = []

    def fake(request, timeout=None):
        seen.append(request)
        return FakeResponse(json.dumps({"voice_id": "clone-1", "requires_verification": False}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    code, clips = run_clone_cli(tmp_path, environ={"ELEVENLABS_API_KEY": SECRET})
    out = capsys.readouterr()
    assert code == 0 and len(seen) == 1
    req = seen[0]
    assert req.full_url == "https://api.elevenlabs.io/v1/voices/add"
    assert req.get_header("Content-type").startswith("multipart/form-data; boundary=")
    body = req.data
    assert body.count(b'name="files[]"') == 3
    for n in range(3):
        assert f'filename="s{n}.wav"'.encode() in body
    assert b'name="name"\r\n\r\nFido (Dheer)' in body
    assert b'name="remove_background_noise"\r\n\r\nfalse' in body
    assert SECRET.encode() not in body and SECRET not in out.out + out.err
    voice = json.loads((clips / "voice.json").read_text())
    assert voice == {
        "voice_id": "clone-1", "voice_name": "Fido (Dheer)", "source": "instant-voice-clone",
        "cloned_from": ["s0.wav", "s1.wav", "s2.wav"], "consent_name": "Dheer",
        "consent_agreed_at": "2026-05-01T10:00:00Z",
    }
    assert "clone-1" in out.out and "--limit 3" in out.out


def test_clone_remove_background_noise_and_name_options(tmp_path, monkeypatch):
    clone_setup(tmp_path)
    seen = []
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda r, timeout=None: seen.append(r) or FakeResponse(b'{"voice_id": "v"}')
    )
    run_clone_cli(tmp_path, "--remove-background-noise", "--clone-name", "Rex", environ={"ELEVENLABS_API_KEY": SECRET})
    assert b'name="remove_background_noise"\r\n\r\ntrue' in seen[0].data and b"\r\n\r\nRex\r\n" in seen[0].data


def test_clone_requires_consent(tmp_path, monkeypatch, capsys):
    clone_setup(tmp_path, consent=False)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    code, clips = run_clone_cli(tmp_path, environ={"ELEVENLABS_API_KEY": SECRET})
    assert code == 1 and "consent" in capsys.readouterr().err
    assert not (clips / "voice.json").exists()
    (tmp_path / "recordings" / "consent.json").write_text(json.dumps({"name": "Dheer"}))
    code, _ = run_clone_cli(tmp_path, environ={"ELEVENLABS_API_KEY": SECRET})
    assert code == 1 and "agreed_at" in capsys.readouterr().err


def test_clone_dry_run_needs_no_key_or_network_and_warns_when_short(tmp_path, monkeypatch, capsys):
    clone_setup(tmp_path, seconds=(10, 12))
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    code, clips = run_clone_cli(tmp_path, "--dry-run")
    out = capsys.readouterr().out
    assert code == 0 and "s0.wav" in out and "s1.wav" in out and "22 s total" in out
    assert "under the recommended 60 s" in out and not clips.exists()


def test_clone_errors_on_empty_folder_and_falls_back_to_final_wavs(tmp_path, capsys):
    rec = clone_setup(tmp_path, seconds=())
    code, _ = run_clone_cli(tmp_path, "--dry-run")
    assert code == 1 and "no clone samples" in capsys.readouterr().err
    (rec / "clone-samples").rmdir()
    for slot in ("nudge-1", "woof-1"):  # only the for_clone take is used
        (rec / slot).mkdir()
        (rec / slot / "final.wav").write_bytes(wav_bytes(6))
    code, _ = run_clone_cli(tmp_path, "--dry-run")
    out = capsys.readouterr().out
    assert code == 0 and "1 clone sample(s)" in out


def ingredient(tmp_path, name="woof-1"):
    path = tmp_path / "clips" / "ingredients" / f"{name}.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(BARK_MP3)


def test_recorded_ingredient_replaces_sfx_call_and_cost(tmp_path, monkeypatch, ffmpeg, capsys):
    ingredient(tmp_path, "woof-2")  # bark-1 uses the two-woof prompt
    plan = {i.name: i for i in ga.build_plan(tmp_path / "clips", None, False, None)[0]}
    item = plan["bark-1"]
    assert item.bark_file is not None and item.label == "speech + recorded bark"
    assert item.credits == len(item.line.voice)  # no 100-credit bark
    api = FakeApi()
    monkeypatch.setattr(urllib.request, "urlopen", api)
    audio = ga.generate_clip(ga.Client(SECRET), item, "v", ga.DEFAULT_FORMAT)
    assert audio == JOINED_MP3
    assert [u for u, _, _ in api.calls if "sound-generation" in u] == []
    assert ffmpeg.inputs[-1][0] == BARK_MP3
    run_cli(tmp_path, "--dry-run")
    assert "speech + recorded bark" in capsys.readouterr().out


def test_recorded_barks_cover_their_lines_only(tmp_path):
    ingredient(tmp_path, "woof-1")  # only the lines using the single-woof prompt
    _, todo = ga.build_plan(tmp_path / "clips", None, False, None)
    assert sum(1 for i in todo if i.has_bark and i.bark_file) < sum(1 for i in todo if i.has_bark)
    assert all(i.credits >= len(i.line.voice or "") for i in todo)


def test_recorded_clips_are_protected_from_force(tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    for name in ("bark-4", "bark-5", "run-5"):
        (clips / f"{name}.mp3").write_bytes(b"x")
    (clips / "manifest.json").write_text(
        json.dumps({"clips": [{"name": "bark-4", "source": "recorded"}, {"name": "bark-5", "source": "recorded"}]})
    )
    _, todo = ga.build_plan(clips, None, True, None)
    names = {i.name for i in todo}
    assert "bark-4" not in names and "bark-5" not in names and "run-5" in names  # run-5 is not recorded here
    _, todo = ga.build_plan(clips, None, False, None)
    assert not {"bark-4", "bark-5", "run-5"} & {i.name for i in todo}  # existing: skipped without --force
    _, todo = ga.build_plan(clips, None, True, None, overwrite_recorded=True)
    assert {"bark-4", "bark-5"} <= {i.name for i in todo}


def test_manifest_sets_source_and_preserves_unknown_keys(tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "bark-4.mp3").write_bytes(b"recorded")
    (clips / "nudge-1.mp3").write_bytes(b"generated")
    (clips / "manifest.json").write_text(json.dumps({
        "output_format": "mp3_22050_32", "extra": 1,
        "clips": [
            {"name": "bark-4", "source": "recorded", "take": "woof-2"},
            {"name": "not-in-script", "source": "recorded"},
        ],
    }))
    ga.write_manifest(clips, "mp3_22050_32", frozenset({"nudge-1"}))
    manifest = json.loads((clips / "manifest.json").read_text())
    by = {e["name"]: e for e in manifest["clips"]}
    assert manifest["extra"] == 1
    assert by["bark-4"]["source"] == "recorded" and by["bark-4"]["take"] == "woof-2" and by["bark-4"]["bytes"] == 8
    assert by["nudge-1"]["source"] == "generated"
    assert by["not-in-script"] == {"name": "not-in-script", "source": "recorded"}


def test_voice_settings_and_model_follow_voice_json_source(tmp_path, monkeypatch):
    assert ga.voice_settings_for(None) == ga.VOICE_SETTINGS and ga.VOICE_SETTINGS["stability"] == 0.0
    clone = ga.voice_settings_for("instant-voice-clone")
    assert clone == ga.CLONE_VOICE_SETTINGS and 0.35 <= clone["stability"] <= 0.5 and clone["style"] == 0

    def generate(extra=()):
        api = FakeApi()
        monkeypatch.setattr(urllib.request, "urlopen", api)
        code, _ = run_cli(tmp_path, "--only", "praise", "--limit", "1", *extra, environ={"ELEVENLABS_API_KEY": SECRET})
        assert code == 0
        return api.calls[0][1]

    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "voice.json").write_text(json.dumps({"voice_id": "v1"}))
    body = generate()
    assert body["model_id"] == "eleven_v3" and body["voice_settings"] == ga.VOICE_SETTINGS
    (clips / "voice.json").write_text(json.dumps({"voice_id": "v1", "source": "instant-voice-clone"}))
    body = generate()
    assert body["model_id"] == ga.CLONE_TTS_MODEL and body["voice_settings"] == ga.CLONE_VOICE_SETTINGS
    assert generate(("--model", "eleven_v3"))["model_id"] == "eleven_v3"
