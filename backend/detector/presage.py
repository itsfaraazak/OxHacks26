"""Local SmartSpectra bridge and timestamp-aware face-metric aggregation.

Only detector.agent sends camera frames, through an anonymous child-process
pipe. No frame/landmark files, HTTP listener, SDK key, or paid auto-restart.
"""

from __future__ import annotations

import json
import math
import queue
import shutil
import struct
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BRIDGE_SCRIPT = Path(__file__).resolve().parents[1] / "presage" / "bridge.mjs"
PREFIX = "FIDO_PRESAGE "
FRESH_SECONDS = 1.0
TALK_WINDOW_SECONDS = 6.0
SDK_ERRORS = {
    1: "invalid state", 2: "authentication failed", 3: "configuration failed",
    4: "credits exhausted", 5: "network failure", 6: "server failure",
    7: "input unavailable", 8: "processing failed", 9: "frame conversion failed",
    10: "non-monotonic timestamp", 11: "timestamp gap",
}
ERROR_STAGES = {"sdk", "start", "frame", "decode", "key", "protocol"}


def error_status(message: dict[str, Any]) -> str:
    """Render only allowlisted codes/stages, never native messages or secrets."""
    stage = message.get("stage")
    stage = stage if isinstance(stage, str) and stage in ERROR_STAGES else "sdk"
    code = message.get("code")
    if type(code) is int and code in SDK_ERRORS:
        return f"error: {SDK_ERRORS[code]} [{code}/{stage}]"
    return f"error: {stage}"


class CaptureClock:
    """Epoch microseconds for the SDK, advanced by a monotonic clock only."""

    def __init__(self) -> None:
        self._epoch_us = time.time_ns() // 1000
        self._monotonic_ns = time.monotonic_ns()

    def now_us(self) -> int:
        return self._epoch_us + (time.monotonic_ns() - self._monotonic_ns) // 1000


@dataclass(frozen=True)
class PresageReading:
    status: str
    talking: bool | None = None
    blinking: bool | None = None
    eyes_closed: bool | None = None
    talking_seconds: float = 0.0
    eyes_closed_seconds: float = 0.0
    blinks: int = 0


class PresageMetrics:
    """Accept only new, stable SDK samples; silence/missing data is unknown.

    Duration comes from consecutive SDK sample timestamps, never repeated
    camera polls. A frozen true value therefore cannot become a sleep event.
    """

    def __init__(self) -> None:
        self.channels: dict[str, tuple[float, bool | None]] = {}
        self.talking_intervals: deque[tuple[float, float]] = deque()
        self.eyes_since: float | None = None
        self.blinks = 0
        self.cutoff = 0.0

    def reset(self, now: float) -> None:
        self.channels.clear()
        self.talking_intervals.clear()
        self.eyes_since = None
        self.blinks = 0
        self.cutoff = now

    def ingest(self, message: dict[str, Any], now: float) -> None:
        for name in ("talking", "blinking", "eyes"):
            sample = message.get(name)
            if not isinstance(sample, dict):
                continue
            timestamp = sample.get("timestamp")
            detected = sample.get("detected")
            if (
                isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp) or not isinstance(detected, bool)
            ):
                continue
            at = timestamp / 1_000_000
            previous_at, previous = self.channels.get(name, (0.0, None))
            if at <= max(previous_at, self.cutoff) or not 0 <= now - at <= FRESH_SECONDS:
                continue
            value = detected if sample.get("stable") is True else None
            continuous = previous is True and 0 < at - previous_at <= FRESH_SECONDS
            if name == "talking" and continuous and value is not None:
                self.talking_intervals.append((previous_at, at))
            if name == "eyes":
                if value is True:
                    if not continuous:
                        self.eyes_since = at
                else:
                    self.eyes_since = None
            if name == "blinking" and value is True and previous is not True:
                self.blinks += 1
            self.channels[name] = (at, value)

    def read(self, now: float, status: str = "running") -> PresageReading:
        def current(name: str) -> bool | None:
            at, value = self.channels.get(name, (0.0, None))
            return value if 0 <= now - at <= FRESH_SECONDS else None

        while self.talking_intervals and self.talking_intervals[0][1] <= now - TALK_WINDOW_SECONDS:
            self.talking_intervals.popleft()
        talking = current("talking")
        eyes = current("eyes")
        seconds = sum(end - max(start, now - TALK_WINDOW_SECONDS) for start, end in self.talking_intervals)
        return PresageReading(
            status=status,
            talking=talking,
            blinking=current("blinking"),
            eyes_closed=eyes,
            talking_seconds=max(0.0, seconds) if talking is not None else 0.0,
            eyes_closed_seconds=(
                max(0.0, self.channels["eyes"][0] - self.eyes_since)
                if eyes is True and self.eyes_since is not None else 0.0
            ),
            blinks=self.blinks,
        )


class SessionPresage:
    """Own a bounded scan for each active period; never restart on SDK failure/budget."""

    def __init__(self, seconds: float, factory=None):
        self._seconds = seconds
        self._factory = factory or PresageBridge
        self._session = None
        self._bridge = None
        self._status = "idle (no active session)"

    def reconcile(self, session_id: str | None) -> None:
        if session_id == self._session:
            return
        self.close()
        self._session = session_id
        if session_id is not None:
            try:
                self._bridge = self._factory(self._seconds)
                self._status = "starting"
            except (RuntimeError, OSError):
                self._status = "unavailable (restart session to retry)"

    def send(self, *args) -> None:
        if self._bridge:
            self._bridge.send(*args)

    def read(self, now: float) -> PresageReading:
        return self._bridge.read(now) if self._bridge else PresageReading(status=self._status)

    def reset(self, now: float) -> None:
        if self._bridge:
            self._bridge.reset(now)

    def close(self) -> None:
        if self._bridge:
            self._bridge.close()
        self._bridge = None
        self._session = None
        self._status = "idle (no active session)"


class PresageBridge:
    """One bounded child; latest-frame queue keeps SDK stalls off the camera loop."""

    def __init__(self, seconds: float = 25):
        if not math.isfinite(seconds) or not 0 < seconds <= 1500:
            raise ValueError("Presage scan seconds must be > 0 and <= 1500")
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js is required for Presage; install Node.js 20.9+")
        self._lock = threading.Lock()
        self._metrics = PresageMetrics()
        self._status = "starting"
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._seconds = seconds
        self._watchdog: threading.Timer | None = None
        self._frames: queue.Queue[bytes | None] = queue.Queue(maxsize=1)
        # SDK diagnostics are not forwarded: they may contain sensitive context.
        self._process = subprocess.Popen(
            [node, str(BRIDGE_SCRIPT), str(seconds)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=self._read, daemon=True, name="presage-results").start()
        threading.Thread(target=self._write, daemon=True, name="presage-frames").start()
        if not self._ready.wait(10) or self._status != "ready":
            self.close()
            raise RuntimeError(
                f"Presage bridge not ready ({self._status}). Run npm ci in backend/presage "
                "and set SMARTSPECTRA_API_KEY in backend/presage/.env."
            )

    def _read(self) -> None:
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                if not line.startswith(PREFIX.encode()):
                    continue
                try:
                    message = json.loads(line[len(PREFIX):])
                except (ValueError, UnicodeDecodeError):
                    continue
                kind = message.get("type")
                with self._lock:
                    if kind == "ready":
                        self._status = "ready"
                        self._ready.set()
                    elif kind == "started":
                        self._status = "waiting for stable metrics"
                    elif kind == "metrics":
                        if not self._status.startswith("error"):
                            self._status = "running"
                            self._metrics.ingest(message, time.time())
                    elif kind in ("error", "stopped"):
                        if kind == "error" and not self._status.startswith("error"):
                            self._status = error_status(message)
                        elif kind == "stopped" and not self._status.startswith("error"):
                            self._status = "stopped (budget or shutdown)"
                        self._metrics.reset(time.time())
                        self._ready.set()
        finally:
            with self._lock:
                if not self._status.startswith("error") and self._status != "stopped (budget or shutdown)":
                    self._status = "unavailable"
                self._metrics.reset(time.time())
            self._ready.set()
            self._process.stdout.close()

    def _write(self) -> None:
        assert self._process.stdin is not None
        try:
            while not self._closed.is_set():
                packet = self._frames.get()
                if packet is None:
                    return
                self._process.stdin.write(packet)
                self._process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                self._process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    def send(self, width: int, height: int, bgr: bytes, timestamp_us: float) -> None:
        if self._closed.is_set() or self._process.poll() is not None:
            return
        if not 0 < width <= 1920 or not 0 < height <= 1080 or len(bgr) != width * height * 3:
            raise ValueError("Invalid BGR frame dimensions")
        if self._watchdog is None:
            # Start the independent bound only with the first frame, not while
            # the user is still entering their task before pressing Start.
            self._watchdog = threading.Timer(self._seconds + 10, self.close)
            self._watchdog.daemon = True
            self._watchdog.start()
        packet = struct.pack(">IId", width, height, timestamp_us) + bgr
        try:
            self._frames.get_nowait()
        except queue.Empty:
            pass
        try:
            self._frames.put_nowait(packet)
        except queue.Full:
            pass  # shutdown can race the camera thread; never block capture

    def read(self, now: float) -> PresageReading:
        with self._lock:
            return self._metrics.read(now, self._status)

    def reset(self, now: float) -> None:
        with self._lock:
            self._metrics.reset(now)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with self._lock:
            self._metrics.reset(time.time())
            if not self._status.startswith("error"):
                self._status = "stopped (budget or shutdown)"
        watchdog = getattr(self, "_watchdog", None)
        if watchdog:
            watchdog.cancel()
        try:
            self._frames.get_nowait()
        except queue.Empty:
            pass
        try:
            self._frames.put_nowait(None)
        except queue.Full:
            pass
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
