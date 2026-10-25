import json
import threading

import pytest

from fido.app import create_app
from fido.engine import FocusEngine, Settings


class Clock:
    now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(clock: Clock):
    engine = FocusEngine(Settings(bark_after_seconds=2, escape_after_seconds=2), clock)
    return create_app(engine, start_ticker=False)


@pytest.fixture
def client(app):
    return app.test_client()


def reading(focused: bool) -> dict:
    return {
        "source": "laptop",
        "reading": {"focused": focused, "calibrated": True, "reason": "Looking Away Sideways", "fps": 29.5},
        "vitals": {"pulse": 72},
    }


def test_full_journey_over_http(client, clock: Clock) -> None:
    res = client.post("/api/session", json={"task": "Ship the backend", "duration_minutes": 25})
    assert res.status_code == 201
    assert res.json["phase"] == "awaiting_phone"

    cmd = client.post("/api/robot/events", json={"event": "docked", "info": {"battery": 80}}).json
    assert cmd["mode"] == "watch"
    assert cmd["task"] == "Ship the backend"
    assert cmd["motion_enabled"] is True

    res = client.post("/api/readings", json=reading(False))
    assert res.json["phase"] == "nudge"
    assert res.json["calibration_id"]

    for _ in range(5):
        clock.now += 1
        client.post("/api/readings", json=reading(False))
    assert client.get("/api/robot/command").json["mode"] == "run"

    client.post("/api/robot/events", json={"event": "caught"})
    state = client.get("/api/state").json
    assert state["phase"] == "failed"
    assert client.get("/api/robot/command").json["mode"] == "rest"
    assert state["vitals"] == {"pulse": 72}
    assert state["robot"]["info"] == {"battery": 80}
    assert state["detector"]["sources"]["laptop"]["fps"] == 29.5

    assert client.post("/api/session/end").status_code == 200
    assert client.get("/api/history").json[0]["escapes"] == 1


def test_each_session_waits_for_new_button_event(client, clock: Clock) -> None:
    for task in ("First", "Second"):
        response = client.post("/api/session", json={"task": task})
        assert response.status_code == 201
        assert response.json["phase"] == "awaiting_phone"
        assert response.json["robot"]["phone_docked"] is False
        assert response.json["speech"]["cue"] == "dock"
        clock.now += 30
        command = client.post("/api/robot/events", json={"event": "heartbeat"}).json
        assert command["mode"] == "beckon_dock"
        assert command["remaining_seconds"] == 1500
        command = client.post("/api/robot/events", json={"event": "docked"}).json
        assert command["mode"] == "watch"
        assert command["speech"]["cue"] == "start"
        assert client.post("/api/session/end").status_code == 200


def test_presage_permission_tracks_active_phases_only(client, clock):
    def permission():
        return client.post("/api/readings", json=reading(True)).json["calibration_id"]

    assert permission() is None
    client.post("/api/session", json={"task": "Lifecycle"})
    assert permission() is None
    client.post("/api/robot/events", json={"event": "docked"})
    session_id = permission()
    assert session_id
    client.post("/api/session/pause")
    assert permission() is None
    client.post("/api/session/resume")
    assert permission() == session_id
    clock.now += 16  # Resume grace period.
    for _ in range(6):
        clock.now += 1
        response = client.post("/api/readings", json=reading(False)).json
    assert response["phase"] == "escaped"
    assert response["calibration_id"] is None
    client.post("/api/robot/events", json={"event": "caught"})
    assert permission() is None
    client.post("/api/session/end")
    assert permission() is None


def test_robot_ambient_audio_obeys_global_mute(client):
    assert client.get("/api/robot/command").json["voice_enabled"] is True
    client.patch("/api/settings", json={"voice_enabled": False})
    assert client.post("/api/robot/events", json={"event": "heartbeat"}).json["voice_enabled"] is False


def test_errors_distinguish_bad_input_from_wrong_state(client) -> None:
    assert client.post("/api/session", json={"task": "", "duration_minutes": 25}).status_code == 400
    assert client.post("/api/session", data="not json").status_code == 400
    assert client.post("/api/session/dance").status_code == 409  # no session yet

    client.post("/api/session", json={"task": "x"})
    assert client.post("/api/session/dance").status_code == 400
    res = client.post("/api/session/resume")
    assert res.status_code == 409
    assert res.json["state"]["phase"] == "awaiting_phone"  # lets the client resync

    assert client.post("/api/readings", json={"source": "bad name!", "reading": {}}).status_code == 400
    assert client.post("/api/readings", json={"source": "x", "reading": {"focused": "no"}}).status_code == 400
    assert client.post("/api/robot/events", json={"event": "fly"}).status_code == 400


def test_settings_patch(client) -> None:
    res = client.patch("/api/settings", json={"motion_enabled": False, "escape_timeout_seconds": 120})
    assert res.status_code == 200
    assert res.json["motion_enabled"] is False
    assert client.get("/api/robot/command").json["motion_enabled"] is False
    assert client.patch("/api/settings", json={"bark_after_seconds": 0}).status_code == 400


def test_device_token_is_enforced_when_configured(monkeypatch, clock: Clock) -> None:
    monkeypatch.setenv("FIDO_DEVICE_TOKEN", "s3cret")
    client = create_app(FocusEngine(clock=clock), start_ticker=False).test_client()
    assert client.post("/api/robot/events", json={"event": "heartbeat"}).status_code == 401
    ok = client.post("/api/robot/events", json={"event": "heartbeat"}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert client.get("/api/state").status_code == 200  # the app itself stays open


def test_event_stream_pushes_each_change(app, client) -> None:
    res = client.get("/api/events", buffered=False)
    assert res.mimetype == "text/event-stream"
    stream = res.response

    def next_state() -> dict:
        chunk = next(stream).decode()
        assert chunk.startswith("id: ") and "event: state" in chunk
        return json.loads(chunk.split("data: ", 1)[1])

    assert next_state()["phase"] == "idle"

    threading.Timer(0.05, lambda: client.post("/api/session", json={"task": "x"})).start()
    assert next_state()["phase"] == "awaiting_phone"
    res.close()


@pytest.mark.parametrize("duration", [1, 15, 45, 60, 25.0, "25", True, None])
def test_custom_session_durations_are_rejected(client, duration) -> None:
    res = client.post("/api/session", json={"task": "Write", "duration_minutes": duration})
    assert res.status_code == 400
    assert client.get("/api/state").json["session"] is None


def test_session_defaults_to_25_minutes(client) -> None:
    res = client.post("/api/session", json={"task": "Write"})
    assert res.status_code == 201
    assert res.json["session"]["duration_seconds"] == 1500
    assert res.json["session"]["remaining_seconds"] == 1500
