import io
import json
import math
import random
import struct
import wave

import pytest

from fido.recording import BY_SLOT, TAKES
from recorder import analysis
from recorder.app import CONSENT_STATEMENT, create_app


def make_wav(samples, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def sine(seconds=1.0, amp=0.5, rate=16000, freq=220):
    return make_wav([int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(int(seconds * rate))], rate)


def noise(seconds=1.0, amp=0.001, rate=16000):
    rng = random.Random(1)
    return make_wav([int(amp * 32767 * rng.uniform(-1, 1)) for _ in range(int(seconds * rate))], rate)


# ---- analysis -----------------------------------------------------------------
def test_sine_levels():
    a = analysis.analyse(sine(1.0, 0.5))
    assert a.duration == pytest.approx(1.0)
    assert a.peak_dbfs == pytest.approx(-6.0, abs=0.1)
    assert a.rms_dbfs == pytest.approx(-9.0, abs=0.2)
    assert a.clipping_percent == 0


def test_clipping_detected():
    samples = [max(-32768, min(32767, int(1.5 * 32767 * math.sin(2 * math.pi * 220 * i / 16000)))) for i in range(16000)]
    a = analysis.analyse(make_wav(samples))
    assert a.clipping_percent > 0.5


def test_silence_and_rejects():
    assert analysis.analyse(make_wav([0] * 100)).peak_dbfs == -120.0
    for bad in (b"not a wav", b"", b"RIFF" + b"\0" * 40, make_wav([])):
        with pytest.raises(analysis.WavError):
            analysis.analyse(bad)


def test_hints():
    lo, hi = 1.0, 2.0
    quiet = analysis.hints(analysis.analyse(sine(1.5, 0.01)), (lo, hi))
    assert any("quiet" in h["text"] for h in quiet)
    short = analysis.hints(analysis.analyse(sine(0.3, 0.5)), (lo, hi))
    assert any("short" in h["text"] for h in short)
    long_ = analysis.hints(analysis.analyse(sine(3, 0.5)), (lo, hi))
    assert any("long" in h["text"] for h in long_)
    ok = analysis.hints(analysis.analyse(sine(1.5, 0.5)), (lo, hi), room_rms_dbfs=-60)
    assert [h["level"] for h in ok] == ["ok"]
    noisy = analysis.hints(analysis.analyse(sine(1.5, 0.5)), (lo, hi), room_rms_dbfs=-30)
    assert any("noisy" in h["text"] for h in noisy)
    room = analysis.analyse(noise(1, 0.0005))
    assert room.rms_dbfs < analysis.NOISY_ROOM_DBFS


# ---- server -------------------------------------------------------------------
@pytest.fixture
def rec(tmp_path):
    return tmp_path / "recordings"


@pytest.fixture
def client(rec):
    return create_app(rec).test_client()


def consent(client, name="Dheer"):
    return client.post("/api/consent", json={"name": name, "agreed": True})


def test_upload_requires_consent(client, rec):
    r = client.post("/api/takes/woof-1", data=sine())
    assert r.status_code == 403
    assert not (rec / "woof-1").exists()
    assert client.post("/api/keep", json={"slot": "woof-1", "take": 1}).status_code == 403
    assert client.post("/api/room-tone", data=sine()).status_code == 403


def test_consent_persists(client, rec):
    assert client.post("/api/consent", json={"name": "Dheer", "agreed": False}).status_code == 400
    assert client.post("/api/consent", json={"name": " ", "agreed": True}).status_code == 400
    assert consent(client).status_code == 200
    saved = json.loads((rec / "consent.json").read_text())
    assert saved["name"] == "Dheer" and saved["statement"] == CONSENT_STATEMENT
    assert saved["agreed_at"].endswith("+00:00")
    # a fresh app on the same folder still sees it
    plan = create_app(rec).test_client().get("/api/plan").get_json()
    assert plan["consent"]["name"] == "Dheer"
    client.delete("/api/consent")
    assert client.post("/api/takes/woof-1", data=sine()).status_code == 403


def test_corrupt_consent_is_not_consent(client, rec):
    rec.mkdir()
    (rec / "consent.json").write_text("{nope")
    assert client.post("/api/takes/woof-1", data=sine()).status_code == 403
    (rec / "consent.json").write_text(json.dumps({"name": "X", "agreed_at": "now", "statement": CONSENT_STATEMENT}))
    assert client.post("/api/takes/woof-1", data=sine()).status_code == 403


def test_plan_matches_takes(client):
    j = client.get("/api/plan").get_json()
    assert [t["slot"] for t in j["takes"]] == [t.slot for t in TAKES]
    for api, t in zip(j["takes"], TAKES):
        assert (api["text"], api["direction"], api["kind"], api["seconds"]) == (t.text, t.direction, t.kind, list(t.seconds))
        assert api["kept"] is False and api["takes"] == []
    assert j["consent"] is None


def test_unknown_slot_and_traversal(client, rec, tmp_path):
    consent(client)
    for slot in ("nope", "..", "%2e%2e", "woof-1/../x", "..%2Fetc", "_room-tone"):
        assert client.post(f"/api/takes/{slot}", data=sine()).status_code in (400, 404)
    assert client.post("/api/keep", json={"slot": "../x", "take": 1}).status_code == 404
    assert client.get("/api/audio/..%2F..%2Fconsent.json/1").status_code == 404
    assert client.get("/api/audio/woof-1/../../consent.json").status_code == 404
    assert client.get("/api/audio/woof-1/1").status_code == 404
    assert client.post("/api/keep", json={"slot": "woof-1", "take": "1"}).status_code == 400
    assert client.post("/api/keep", json={"slot": "woof-1", "take": 99}).status_code == 404
    assert not any(p.name not in ("consent.json",) for p in rec.iterdir())


def test_wav_validation(client, rec):
    consent(client)
    assert client.post("/api/takes/woof-1", data=b"hello world").status_code == 400
    assert client.post("/api/takes/woof-1", data=b"").status_code == 400
    assert client.post("/api/takes/woof-1", data=sine(0.2)[:60]).status_code == 400
    assert not (rec / "woof-1").exists()


def test_size_cap(client):
    consent(client)
    r = client.post("/api/takes/woof-1", data=b"RIFF" + b"\0" * (10 * 1024 * 1024 + 10))
    assert r.status_code == 413


def test_take_numbering_and_final(client, rec):
    consent(client)
    first, second = sine(1.0, 0.5), sine(1.2, 0.4)
    r1 = client.post("/api/takes/woof-1", data=first).get_json()
    r2 = client.post("/api/takes/woof-1", data=second).get_json()
    assert (r1["take"], r2["take"]) == (1, 2)
    assert r1["analysis"]["duration"] == pytest.approx(1.0)
    assert (rec / "woof-1" / "take-1.wav").read_bytes() == first
    assert client.post("/api/keep", json={"slot": "woof-1", "take": 1}).status_code == 200
    assert (rec / "woof-1" / "final.wav").read_bytes() == first
    client.post("/api/keep", json={"slot": "woof-1", "take": 2})
    assert (rec / "woof-1" / "final.wav").read_bytes() == second
    assert (rec / "woof-1" / "take-1.wav").read_bytes() == first  # never overwritten
    assert not [p for p in (rec / "woof-1").iterdir() if p.name.startswith(".tmp-")]
    slot = next(t for t in client.get("/api/plan").get_json()["takes"] if t["slot"] == "woof-1")
    assert slot["kept"] and slot["takes"] == [1, 2]
    assert client.get("/api/audio/woof-1/final").data == second
    assert client.get("/api/audio/woof-1/1").data == first


def test_room_tone_saved_and_used_in_hints(client, rec):
    consent(client)
    r = client.post("/api/room-tone", data=sine(1, 0.5)).get_json()
    assert r["noisy"] is True and (rec / "_room-tone.wav").is_file()
    hints = client.post("/api/takes/woof-1", data=sine(1.0, 0.5)).get_json()["hints"]
    assert any("noisy" in h["text"] for h in hints)


def test_cross_origin_and_host_rejected(client):
    assert client.get("/api/plan", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/plan", headers={"Host": "evil.example"}).status_code == 403
    ok = client.get("/api/plan", headers={"Origin": "http://localhost", "Host": "localhost"})
    assert ok.status_code == 200
    assert client.get("/api/plan", base_url="http://127.0.0.1:5100", headers={"Origin": "http://127.0.0.1:5100"}).status_code == 200
    assert client.get("/").status_code == 200
