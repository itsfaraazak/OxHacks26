"""Fido HTTP API.

One process owns one `FocusEngine` (one dog). Clients:

- Browsers (laptop / phones): REST commands + Server-Sent Events for live state.
- Detector agents (laptop / dog webcam): POST /api/readings a few times a second.
- Robot: POST /api/robot/events as its heartbeat; the response says what to do.

All engine access goes through `Hub`, which serialises it with one lock and
wakes SSE streams whenever `engine.version` moves.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
import time
from typing import Any, Callable, Iterator

from flask import Flask, Response, jsonify, request, send_from_directory

from .engine import DEFAULT_DURATION_MINUTES, EngineError, FocusEngine, InvalidInput
from .speech import CAPTIONS, PLAYABLE, PLAYLIST

CLIPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clips")

TICK_SECONDS = 0.25  # how often time-based rules (escalation, timers) run
SSE_KEEPALIVE_SECONDS = 15.0


class Hub:
    def __init__(self, engine: FocusEngine):
        self.engine = engine
        self._changed = threading.Condition()

    def run(self, fn: Callable[[FocusEngine], Any]) -> Any:
        """Run fn under the lock and notify streams if it changed anything."""
        with self._changed:
            before = self.engine.version
            try:
                return fn(self.engine)
            finally:
                if self.engine.version != before:
                    self._changed.notify_all()

    def snapshot(self) -> dict[str, Any]:
        return self.run(lambda e: e.snapshot())

    def wait_for_change(self, seen_version: int, timeout: float) -> dict[str, Any] | None:
        """Next snapshot after `seen_version`, or None on timeout."""
        with self._changed:
            changed = self._changed.wait_for(lambda: self.engine.version != seen_version, timeout)
            return self.engine.snapshot() if changed else None

    def start_ticker(self) -> None:
        def tick() -> None:
            while True:
                time.sleep(TICK_SECONDS)
                self.run(lambda e: e.advance())

        threading.Thread(target=tick, name="fido-ticker", daemon=True).start()


def create_app(engine: FocusEngine | None = None, *, start_ticker: bool = True) -> Flask:
    app = Flask(__name__)
    hub = Hub(engine or FocusEngine())
    app.extensions["fido_hub"] = hub
    device_token = os.environ.get("FIDO_DEVICE_TOKEN", "")
    cors_origin = os.environ.get("FIDO_CORS_ORIGIN", "*")
    if start_ticker:
        hub.start_ticker()

    def body() -> dict[str, Any]:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise InvalidInput("Request body must be a JSON object")
        return data

    def require_device() -> None:
        """Robots and detectors must present FIDO_DEVICE_TOKEN when one is configured."""
        if not device_token:
            return
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(supplied, device_token):
            raise PermissionError("Missing or wrong device token")

    @app.errorhandler(InvalidInput)
    def invalid(err: InvalidInput):
        return jsonify(error=str(err)), 400

    @app.errorhandler(EngineError)
    def conflict(err: EngineError):
        return jsonify(error=str(err), state=hub.snapshot()), 409

    @app.errorhandler(PermissionError)
    def forbidden(err: PermissionError):
        return jsonify(error=str(err)), 401

    @app.after_request
    def cors(response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = cors_origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
        return response

    # ----- app (browser) API -------------------------------------------------

    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    @app.get("/api/state")
    def state():
        return jsonify(hub.snapshot())

    @app.post("/api/session")
    def start_session():
        data = body()
        duration = data.get("duration_minutes", DEFAULT_DURATION_MINUTES)
        if type(duration) is not int or duration != DEFAULT_DURATION_MINUTES:
            raise InvalidInput("Sessions are always 25 minutes; custom durations are not supported")
        hub.run(lambda e: e.start(data.get("task"), DEFAULT_DURATION_MINUTES))
        return jsonify(hub.snapshot()), 201

    @app.post("/api/session/<action>")
    def session_action(action: str):
        hub.run(lambda e: e.apply(action))
        return jsonify(hub.snapshot())

    @app.get("/api/settings")
    def get_settings():
        return jsonify(hub.snapshot()["settings"])

    @app.patch("/api/settings")
    def patch_settings():
        data = body()
        hub.run(lambda e: e.update_settings(data))
        return jsonify(hub.snapshot()["settings"])

    @app.get("/api/history")
    def history():
        return jsonify(hub.run(lambda e: list(e.history)))

    @app.get("/api/audio")
    def audio_index():
        """Every clip Fido can play, the moments it plays at, and whether its MP3 exists."""
        return jsonify(
            clips={
                name: {
                    "cues": [cue for cue, clips in PLAYLIST.items() if name in clips],
                    "caption": CAPTIONS[name],
                    "available": os.path.isfile(os.path.join(CLIPS_DIR, f"{name}.mp3")),
                }
                for name in PLAYABLE
            }
        )

    @app.get("/api/audio/<name>.mp3")
    def audio_clip(name: str):
        """A pre-generated clip. Public on purpose: phones fetch these directly."""
        if name not in PLAYABLE:  # whitelist: also rules out path tricks
            return jsonify(error="Unknown clip"), 404
        response = send_from_directory(CLIPS_DIR, f"{name}.mp3", mimetype="audio/mpeg", max_age=3600)
        return response

    @app.get("/api/events")
    def events():
        """SSE: one `state` event now, then one per change, plus keepalives."""

        def stream() -> Iterator[str]:
            snap = hub.snapshot()
            yield sse(snap)
            seen = snap["version"]
            while True:
                snap = hub.wait_for_change(seen, SSE_KEEPALIVE_SECONDS)
                if snap is None:
                    yield ": keepalive\n\n"
                    continue
                seen = snap["version"]
                yield sse(snap)

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ----- device API ---------------------------------------------------------

    @app.post("/api/readings")
    def readings():
        """A detector agent reports one camera's latest YES / NO decision."""
        require_device()
        data = body()
        hub.run(lambda e: e.record_reading(data.get("source"), data.get("reading"), data.get("vitals")))
        snap = hub.snapshot()
        session = snap["session"]
        # Agents recalibrate their posture baseline once per session, when watching starts.
        watching = session is not None and session["phase"] not in ("awaiting_phone", "completed")
        return jsonify(phase=snap["phase"], calibration_id=session["id"] if watching else None)

    @app.post("/api/robot/events")
    def robot_events():
        """Robot heartbeat and sensor events; the reply is its current command."""
        require_device()
        data = body()
        info = data.get("info")
        if info is not None and not isinstance(info, dict):
            raise InvalidInput("info must be an object")
        hub.run(lambda e: e.robot_event(data.get("event"), info))
        return jsonify(robot_command(hub.snapshot()))

    @app.get("/api/robot/command")
    def robot_command_view():
        require_device()
        return jsonify(robot_command(hub.snapshot()))

    return app


def robot_command(snap: dict[str, Any]) -> dict[str, Any]:
    """Everything the robot needs, nothing it doesn't.

    The robot must stop its motors when `motion_enabled` is false, and also when
    it has not heard from the server for a few seconds. `speech` is the latest
    audio cue; play each `seq` once (docs/audio.md).
    """
    session = snap["session"]
    return {
        "mode": snap["robot"]["mode"],
        "motion_enabled": snap["settings"]["motion_enabled"],
        "speech": snap["speech"],
        "task": session["task"] if session else None,
        "remaining_seconds": session["remaining_seconds"] if session else None,
        "reason": session["reason"] if session else None,
        "version": snap["version"],
    }


def sse(snapshot: dict[str, Any]) -> str:
    return f"id: {snapshot['version']}\nevent: state\ndata: {json.dumps(snapshot, separators=(',', ':'))}\n\n"
