"""Tests for scripts/process_recordings.py. The ffmpeg tests run the real pipeline on synthetic WAVs."""

from __future__ import annotations

import array
import importlib.util
import json
import math
import random
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from fido.recording import BY_SLOT

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "process_recordings.py"
spec = importlib.util.spec_from_file_location("process_recordings", SCRIPT_PATH)
pr = importlib.util.module_from_spec(spec)
sys.modules["process_recordings"] = pr
spec.loader.exec_module(pr)

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")

RATE = 16000


def tone(seconds: float, amp: float = 0.3, freq: float = 220.0, noise: float = 0.0) -> list[float]:
    rng = random.Random(1)
    n = int(seconds * RATE)
    return [
        amp * math.sin(2 * math.pi * freq * i / RATE) * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * i / RATE))
        + noise * rng.uniform(-1, 1)
        for i in range(n)
    ]


def silence(seconds: float, noise: float = 0.0) -> list[float]:
    rng = random.Random(2)
    return [noise * rng.uniform(-1, 1) for _ in range(int(seconds * RATE))]


def write_wav(path: Path, samples: list[float], rate: int = RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = array.array("h", (max(-32768, min(32767, round(x * 32767))) for x in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def voice(seconds: float, **kw) -> list[float]:
    return silence(0.5, kw.get("noise", 0.0)) + tone(seconds, **kw) + silence(0.7, kw.get("noise", 0.0))


@pytest.fixture
def dirs(tmp_path):
    rec, clips = tmp_path / "recordings", tmp_path / "clips"
    rec.mkdir()
    (rec / "consent.json").write_text(json.dumps({"agreed_at": "2026-01-01T00:00:00Z"}))
    return rec, clips


def run(dirs, *extra) -> int:
    rec, clips = dirs
    return pr.main(["--recordings-dir", str(rec), "--clips-dir", str(clips), *extra])


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=channels,sample_rate:format=duration", "-of", "json", str(path)],
        capture_output=True, check=True,
    )  # fmt: skip
    data = json.loads(out.stdout)
    return {"channels": data["streams"][0]["channels"], "rate": int(data["streams"][0]["sample_rate"]),
            "duration": float(data["format"]["duration"])}  # fmt: skip


# --- consent / no ffmpeg needed ---------------------------------------------


def test_consent_missing_or_unsigned_refuses(tmp_path, capsys):
    rec, clips = tmp_path / "recordings", tmp_path / "clips"
    write_wav(rec / "woof-1" / "final.wav", voice(0.8))
    assert run((rec, clips)) == 1
    assert "consent" in capsys.readouterr().err
    (rec / "consent.json").write_text("{}")
    assert run((rec, clips)) == 1
    assert not clips.exists()


def test_missing_ffmpeg_message(dirs, monkeypatch, capsys):
    monkeypatch.setattr(pr.shutil, "which", lambda name: None)
    assert run(dirs) == 1
    assert "brew install ffmpeg" in capsys.readouterr().err


def test_unknown_only_slot_is_an_error(dirs, capsys):
    if not HAVE_FFMPEG:
        pytest.skip("needs ffmpeg")
    assert run(dirs, "--only", "nope") == 1


# --- verdicts ---------------------------------------------------------------


def analyse(tmp_path, slot, samples, rate=RATE):
    wav = tmp_path / "x" / "final.wav"
    write_wav(wav, samples, rate)
    return pr.analyse_take(BY_SLOT[slot], wav, shutil.which("ffmpeg"), False, tmp_path)


@needs_ffmpeg
def test_verdict_ok(tmp_path):
    a = analyse(tmp_path, "woof-1", voice(1.0, amp=0.5, noise=0.002))
    assert a.verdict == "ok", a.reasons


@needs_ffmpeg
def test_verdict_silent(tmp_path):
    a = analyse(tmp_path, "woof-1", silence(3.0, noise=0.001))
    assert a.verdict == "redo"
    assert "no speech detected" in a.reasons[0]


@needs_ffmpeg
def test_verdict_too_quiet(tmp_path):
    a = analyse(tmp_path, "woof-1", voice(1.0, amp=0.012))
    assert a.verdict == "redo"
    assert any("too quiet" in r for r in a.reasons)


@needs_ffmpeg
def test_verdict_clipping(tmp_path):
    loud = [max(-1.0, min(1.0, x * 6)) for x in voice(1.0, amp=0.5)]
    a = analyse(tmp_path, "woof-1", loud)
    assert a.verdict == "redo"
    assert any("clipping" in r for r in a.reasons)


@needs_ffmpeg
def test_verdict_noisy_room_and_length(tmp_path):
    a = analyse(tmp_path, "woof-1", voice(1.0, amp=0.3, noise=0.06))
    assert a.verdict in ("warn", "redo")
    assert any("noisy room" in r for r in a.reasons)
    b = analyse(tmp_path, "woof-1", voice(0.4, amp=0.5, noise=0.001) )  # trimmed ~0.7s: in range
    assert b.verdict == "ok", b.reasons
    c = analyse(tmp_path, "dock-1", voice(1.2, amp=0.5))  # expects 2-9 s
    assert c.verdict == "warn" and any("short" in r for r in c.reasons)


# --- pipeline ---------------------------------------------------------------


@needs_ffmpeg
def test_pipeline_outputs_and_manifest(dirs):
    rec, clips = dirs
    write_wav(rec / "woof-2" / "final.wav", voice(1.5, amp=0.3, noise=0.002))
    write_wav(rec / "dock-1" / "final.wav", [x for x in voice(3.0, amp=0.2, noise=0.002) for _ in (0, 1)], rate=2 * RATE)
    write_wav(rec / "nudge-1" / "final.wav", voice(12.0, amp=0.3, noise=0.002))
    assert run(dirs) == 0

    # known slots map to the right paths (woof-2 is shipped as the clip bark-4)
    assert (clips / "bark-4.mp3").is_file()
    assert (clips / "ingredients" / "woof-2.mp3").is_file()
    assert (clips / "dock-1.mp3").is_file()
    assert sorted(p.name for p in clips.glob("*.mp3")) == ["bark-4.mp3", "dock-1.mp3", "nudge-1.mp3"]
    assert not (clips / "ingredients" / "nudge-1.mp3").exists()  # spoken lines have no ingredient
    assert (rec / "clone-samples" / "nudge-1.wav").is_file()
    assert (rec / "clone-samples" / "dock-1.wav").is_file()
    assert not (rec / "clone-samples" / "woof-2.wav").exists()  # woofs are not for the clone

    mp3 = clips / "dock-1.mp3"
    assert not mp3.read_bytes().startswith(b"ID3")
    info = probe(mp3)
    assert (info["channels"], info["rate"]) == (1, 22050)
    assert 3.0 < info["duration"] < 3.0 + 0.08 + 0.2 + 0.4  # tone + kept head/tail, with some slack

    # loudness near target (speech -16, dog -14)
    ff = shutil.which("ffmpeg")
    assert abs(pr.measure_loudness(ff, mp3)["lufs"] - pr.LUFS_SPEECH) < 1.5
    assert abs(pr.measure_loudness(ff, clips / "bark-4.mp3", pr.LUFS_DOG)["lufs"] - pr.LUFS_DOG) < 1.5
    assert (clips / "bark-4.mp3").read_bytes() == (clips / "ingredients" / "woof-2.mp3").read_bytes()

    manifest = json.loads((clips / "manifest.json").read_text())
    by_name = {c["name"]: c for c in manifest["clips"]}
    assert set(by_name) == {"bark-4", "dock-1", "nudge-1"}
    assert by_name["dock-1"]["source"] == "recorded"
    assert by_name["dock-1"]["bytes"] == mp3.stat().st_size
    assert len(by_name["dock-1"]["sha256"]) == 64 and by_name["dock-1"]["caption"]

    index = json.loads((rec / "clone-samples" / "index.json").read_text())
    assert index["total_seconds"] > 12 and index["enough"] is False

    with wave.open(str(rec / "clone-samples" / "nudge-1.wav")) as w:
        assert (w.getnchannels(), w.getframerate()) == (1, 44100)
        samples = array.array("h", w.readframes(w.getnframes()))
    assert abs(20 * math.log10(max(samples) / 32768) - pr.CLONE_PEAK_DB) < 0.2


@needs_ffmpeg
def test_check_and_dry_run_write_nothing(dirs, capsys):
    rec, clips = dirs
    write_wav(rec / "woof-1" / "final.wav", voice(1.0, amp=0.4))
    before = sorted(p for p in rec.rglob("*"))
    assert run(dirs, "--check") == 0
    assert run(dirs, "--dry-run") == 0
    assert not clips.exists()
    assert sorted(p for p in rec.rglob("*")) == before
    assert "OK" in capsys.readouterr().out


@needs_ffmpeg
def test_only_force_and_never_touch_missing_or_delete(dirs):
    rec, clips = dirs
    write_wav(rec / "woof-1" / "final.wav", voice(1.0, amp=0.4))
    write_wav(rec / "dock-1" / "final.wav", voice(3.0, amp=0.3))
    keep = clips / "unrelated.mp3"
    clips.mkdir()
    keep.write_bytes(b"keep")
    assert run(dirs, "--only", "woof-1") == 0
    assert (clips / "ingredients" / "woof-1.mp3").is_file()
    assert not (clips / "dock-1.mp3").exists()
    assert not (clips / "bark-4.mp3").exists()  # no final.wav for woof-2
    assert keep.read_bytes() == b"keep"

    target = clips / "ingredients" / "woof-1.mp3"
    target.write_bytes(b"sentinel")
    import os
    os.utime(target, (target.stat().st_atime, (rec / "woof-1" / "final.wav").stat().st_mtime + 100))
    assert run(dirs, "--only", "woof-1") == 0
    assert target.read_bytes() == b"sentinel"  # up to date: skipped without --force
    assert run(dirs, "--only", "woof-1", "--force") == 0
    assert target.read_bytes() != b"sentinel"


@needs_ffmpeg
def test_redo_take_is_not_written_and_existing_manifest_entries_kept(dirs):
    rec, clips = dirs
    clips.mkdir()
    (clips / "nudge-1.mp3").write_bytes(b"\xff\xf3" + b"x" * 100)
    (clips / "manifest.json").write_text(json.dumps({"output_format": "mp3_22050_32", "clips": [
        {"name": "nudge-1", "cue": "x", "caption": "x", "bytes": 102, "sha256": "0", "source": "generated"}]}))
    write_wav(rec / "dock-1" / "final.wav", silence(4.0, noise=0.001))
    assert run(dirs) == 0  # warnings/redo are advice, not errors
    assert not (clips / "dock-1.mp3").exists()
    write_wav(rec / "woof-1" / "final.wav", voice(1.0, amp=0.4))
    assert run(dirs) == 0
    by_name = {c["name"]: c for c in json.loads((clips / "manifest.json").read_text())["clips"]}
    assert by_name["nudge-1"]["source"] == "generated"
