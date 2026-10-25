"""Laptop audio player: plays Fido's spoken cues when the robot's speaker can't.

Follows the backend's state stream and plays each cue (`state.speech`, see
docs/audio.md) exactly once through a system MP3 player. It stays silent while
the robot is online with a connected Bluetooth speaker, so it is the fallback,
not a second voice.

    python -m audio.player --server http://localhost:5000
    python -m audio.player --dry-run      # print and download, but make no sound

Players tried in order: afplay (macOS), ffplay, mpg123.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator

CLIP_NAME = re.compile(r"[a-z]+-[0-9]+")  # what the backend serves; also keeps names path-safe
PLAYERS = (
    ("afplay", ["afplay"]),
    ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
    ("mpg123", ["mpg123", "-q"]),
)
STREAM_TIMEOUT_SECONDS = 45  # the backend sends a keepalive every 15 s
DOWNLOAD_TIMEOUT_SECONDS = 10
MAX_BACKOFF_SECONDS = 10


# --- Pure logic: no network, no audio device -------------------------------------------------


@dataclass(frozen=True)
class Decision:
    play: bool
    cue: str
    clip: str
    reason: str


def parse_sse(lines: Iterable[bytes]) -> Iterator[tuple[str, str]]:
    """Yield (event, data) for each complete Server-Sent Event in a stream of lines.

    Comments (`: keepalive`) are ignored, several `data:` lines are joined with
    newlines, and an event cut off by a dropped connection is discarded.
    """
    event, data = "message", []
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            continue
        else:
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)


class CueTracker:
    """Decides, for each state snapshot, whether its cue should be played.

    A cue is played once: only when `speech.seq` is higher than any seen. The
    first snapshot after a (re)connect only records `seq`, so a reload never
    replays old speech. Call `reset()` when the connection is lost.
    """

    def __init__(self) -> None:
        self.seq: int | None = None  # None until the first snapshot of this connection

    def reset(self) -> None:
        self.seq = None

    def decide(self, snapshot: dict[str, Any]) -> Decision | None:
        speech = snapshot.get("speech") or {}
        seq = speech.get("seq")
        if not isinstance(seq, int):
            seq = 0  # no cue yet
        first, previous = self.seq is None, self.seq or 0
        self.seq = max(previous, seq)  # tracked even when silent, so nothing replays later
        if seq <= previous and not (first and seq > 0):
            return None
        cue, clip = str(speech.get("cue")), str(speech.get("clip"))
        if first:
            return Decision(False, cue, clip, "already played before the player started")
        if not CLIP_NAME.fullmatch(clip):
            return Decision(False, cue, clip, "unsafe clip name")
        robot = snapshot.get("robot") or {}
        if robot.get("online") and (robot.get("info") or {}).get("speaker") == "connected":
            return Decision(False, cue, clip, "robot speaker is connected")
        return Decision(True, cue, clip, "playing")


# --- IO ------------------------------------------------------------------------------------


def find_player(which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    for name, command in PLAYERS:
        if which(name):
            return command
    return None


class Speaker:
    """Plays one MP3 at a time in a subprocess; a new clip cuts off the previous one."""

    def __init__(self, command: list[str], popen: Callable[..., Any] = subprocess.Popen):
        self.command = command
        self._popen = popen
        self._current: Any = None

    def play(self, path: str) -> None:
        self.stop()
        self._current = self._popen(
            [*self.command, path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def stop(self) -> None:
        if self._current is not None and self._current.poll() is None:
            self._current.terminate()
        self._current = None


class Fido:
    """Connects the tracker to the network and the speaker."""

    def __init__(self, server: str, token: str | None, speaker: Speaker | None, cache_dir: str):
        self.server = server.rstrip("/")
        self.token = token
        self.speaker = speaker  # None for --dry-run
        self.cache_dir = cache_dir
        self.tracker = CueTracker()

    def _request(self, path: str) -> urllib.request.Request:
        request = urllib.request.Request(self.server + path)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        return request

    def fetch(self, clip: str) -> str:
        """Local path of the clip, downloading it the first time it is needed."""
        path = os.path.join(self.cache_dir, f"{clip}.mp3")
        if not os.path.exists(path):
            with urllib.request.urlopen(self._request(f"/api/audio/{clip}.mp3"), timeout=DOWNLOAD_TIMEOUT_SECONDS) as r:
                body = r.read()
            with open(path + ".part", "wb") as f:
                f.write(body)
            os.replace(path + ".part", path)
        return path

    def handle(self, snapshot: dict[str, Any]) -> None:
        decision = self.tracker.decide(snapshot)
        if decision is None:
            return
        label = f"cue={decision.cue} clip={decision.clip}"
        if not decision.play:
            print(f"skip {label}: {decision.reason}")
            return
        try:
            path = self.fetch(decision.clip)
        except (urllib.error.URLError, OSError) as err:
            print(f"skip {label}: could not download ({err})")
            return
        if self.speaker is None:
            print(f"dry-run {label}: would play {path}")
        else:
            print(f"play {label}")
            self.speaker.play(path)

    def run_once(self) -> None:
        """Follow one SSE connection until it ends or fails."""
        with urllib.request.urlopen(self._request("/api/events"), timeout=STREAM_TIMEOUT_SECONDS) as response:
            self.tracker.reset()
            print(f"Connected to {self.server}")
            for event, data in parse_sse(response):
                if event == "state":
                    try:
                        self.handle(json.loads(data))
                    except ValueError:
                        print("ignoring a state event that is not valid JSON")

    def run_forever(self, sleep: Callable[[float], None] = time.sleep) -> None:
        backoff = 1.0
        while True:
            started = time.monotonic()
            try:
                self.run_once()
            except (urllib.error.URLError, OSError) as err:
                print(f"Backend unreachable ({err}); retrying in {backoff:.0f}s")
            if time.monotonic() - started > MAX_BACKOFF_SECONDS:
                backoff = 1.0  # it was connected for a while: start the backoff over
            sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Play Fido's spoken cues on this computer.")
    parser.add_argument("--server", default=os.environ.get("FIDO_SERVER", "http://localhost:5000"))
    parser.add_argument("--dry-run", action="store_true", help="print and download cues, but do not play them")
    args = parser.parse_args()

    speaker = None
    if not args.dry_run:
        command = find_player()
        if command is None:
            raise SystemExit("No MP3 player found. Install ffmpeg (ffplay) or mpg123; macOS has afplay built in.")
        speaker = Speaker(command)
    cache_dir = tempfile.mkdtemp(prefix="fido-audio-")
    fido = Fido(args.server, os.environ.get("FIDO_DEVICE_TOKEN"), speaker, cache_dir)
    print(f"Fido audio player: following {fido.server}; stays silent while the robot's speaker is connected. Ctrl+C to quit.")
    try:
        fido.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if speaker:
            speaker.stop()
        shutil.rmtree(cache_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
