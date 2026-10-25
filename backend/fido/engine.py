"""Fido's focus session state machine: the single source of truth for every client.

The flow follows the prototype README (archive/oxhack26-prototype/README.md):

1. Lock-In: the user starts a session and docks the phone on the dog (NFC).
2. Sentry: the camera detector watches; the session clock runs.
3. Heist: if the user keeps slacking, the dog nudges, then barks, then runs
   away with the phone.
4. Stop: catching the dog ends the failed session. Only an explicit new Start
   returns it to watching with a fresh timer. An escape also stops by itself
   after `escape_timeout_seconds` (10 minutes by default).

The engine is plain Python with an injectable clock so every rule is unit-tested.
It is not thread-safe; the web app serialises calls with a lock.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Callable

from .speech import CAPTIONS, PLAYLIST


class Phase(str, Enum):
    IDLE = "idle"  # no session
    AWAITING_PHONE = "awaiting_phone"  # session created; the dog asks for the phone
    FOCUSING = "focusing"  # watching the user; all good
    NUDGE = "nudge"  # distracted: gentle beckon back to the task
    BARKING = "barking"  # still distracted
    ESCAPED = "escaped"  # running away with the phone until caught
    PAUSED = "paused"  # user-requested break; clock stopped
    COMPLETED = "completed"  # session time is up
    FAILED = "failed"  # escape stopped; a new Start is required


# Phases where the session clock runs and the dog is "on duty".
ACTIVE_PHASES = frozenset({Phase.FOCUSING, Phase.NUDGE, Phase.BARKING})

# What the robot should be doing in each phase. Robot hardware acts on this
# value only, so the hardware never needs to know the session rules.
ROBOT_MODES: dict[Phase, str] = {
    Phase.IDLE: "sleep",
    Phase.AWAITING_PHONE: "beckon_dock",
    Phase.FOCUSING: "watch",
    Phase.NUDGE: "nudge",
    Phase.BARKING: "bark",
    Phase.ESCAPED: "run",
    Phase.PAUSED: "rest",
    Phase.COMPLETED: "celebrate",
    Phase.FAILED: "rest",
}

MAX_TASK_LENGTH = 120
MAX_SOURCES = 8
SOURCE_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")
MAX_SESSION_EVENTS = 50
MAX_HISTORY = 100
DEFAULT_DURATION_MINUTES = 25
NUDGE_COOLDOWN_SECONDS = 15.0


class EngineError(ValueError):
    """A request that does not apply to the current state."""


class InvalidInput(EngineError):
    """A request whose values are malformed, whatever the state."""


@dataclass
class Settings:
    """Escalation timings, editable live from the app."""

    bark_after_seconds: float = 6.0  # nudge -> barking while still distracted
    escape_after_seconds: float = 8.0  # barking -> escaped while still distracted
    escape_timeout_seconds: float = 600.0  # an uncaught escape ends by itself
    recover_grace_seconds: float = 15.0  # no escalation right after resume
    source_stale_seconds: float = 3.0  # ignore cameras that stopped reporting
    robot_offline_seconds: float = 5.0  # robot shown offline after this silence
    require_dock: bool = True  # False: start without NFC docking (no hardware)
    motion_enabled: bool = True  # robot may drive; turn off to keep Fido still (e.g. on a crowded table)
    voice_enabled: bool = True  # Fido speaks and barks; off mutes every cue
    speech_repeat_seconds: float = 25.0  # how often the angry line repeats while Fido runs away

    def update(self, changes: dict[str, Any]) -> None:
        """Validate every change first, then apply all of them or none."""
        defaults = {f.name: f.default for f in fields(self)}
        unknown = set(changes) - set(defaults)
        if unknown:
            raise InvalidInput(f"Unknown settings: {', '.join(sorted(unknown))}")
        for name, value in changes.items():
            if isinstance(defaults[name], bool):
                if not isinstance(value, bool):
                    raise InvalidInput(f"{name} must be true or false")
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 86_400:
                raise InvalidInput(f"{name} must be a positive number of seconds")
        for name, value in changes.items():
            setattr(self, name, value if isinstance(value, bool) else float(value))


@dataclass
class SourceReading:
    """Latest report from one camera running the focus detector."""

    focused: bool
    calibrated: bool
    reason: str
    received_at: float
    fps: float | None = None
    timers: dict[str, float] = field(default_factory=dict)


@dataclass
class Session:
    id: str
    task: str
    duration_seconds: int
    phase: Phase
    created_at: float
    phase_since: float
    reason: str = ""
    distractions: int = 0
    escapes: int = 0
    grace_until: float = 0.0
    elapsed_seconds: float = 0.0  # active time banked before clock_started_at
    clock_started_at: float | None = None  # set while the phase is active
    events: list[dict[str, Any]] = field(default_factory=list)

    def active_seconds(self, now: float) -> float:
        running = now - self.clock_started_at if self.clock_started_at is not None else 0.0
        return self.elapsed_seconds + running

    def remaining_seconds(self, now: float) -> float:
        return max(0.0, self.duration_seconds - self.active_seconds(now))


class FocusEngine:
    def __init__(self, settings: Settings | None = None, clock: Callable[[], float] = time.time):
        self.settings = settings or Settings()
        self._clock = clock
        self.session: Session | None = None
        self.phone_docked = False
        self.robot_last_seen: float | None = None
        self.robot_info: dict[str, Any] = {}
        self.sources: dict[str, SourceReading] = {}
        self.vitals: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        # Incremented on every change; the web app broadcasts when it moves.
        self.version = 0
        self._last_liveness: tuple[bool, ...] = ()
        # The latest thing Fido said (see docs/audio.md). `seq` grows with each new cue.
        self.speech: dict[str, Any] | None = None
        self._speech_seq = 0
        self._cue_counts: dict[str, int] = {}
        self._last_nudge_at = float("-inf")
        self._nudge_voiced = False  # a spoken nudge is awaiting its "good human"

    # ----- commands from the app -------------------------------------------

    def start(self, task: str, duration_minutes: Any) -> None:
        task = task.strip() if isinstance(task, str) else ""
        if not task:
            raise InvalidInput("task must be a non-empty string")
        if len(task) > MAX_TASK_LENGTH:
            raise InvalidInput(f"task must be at most {MAX_TASK_LENGTH} characters")
        if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int) or not 1 <= duration_minutes <= 600:
            raise InvalidInput("duration_minutes must be a whole number from 1 to 600")
        if self.session is not None and self.session.phase not in (Phase.COMPLETED, Phase.FAILED):
            raise EngineError("A session is already running; end it first")

        now = self._clock()
        self.session = Session(
            id=uuid.uuid4().hex[:12],
            task=task,
            duration_seconds=duration_minutes * 60,
            phase=Phase.AWAITING_PHONE,
            created_at=now,
            phase_since=now,
        )
        self._last_nudge_at = float("-inf")
        self._nudge_voiced = False
        # The physical button acknowledges this session, not a persistent dock sensor.
        self.phone_docked = False
        self._log(now, "Session created")
        self.advance()  # only require_dock=False may skip the fresh acknowledgement
        if self.session.phase is Phase.AWAITING_PHONE:
            self._say("dock", now)
        self._changed()

    def apply(self, action: str) -> None:
        """pause | resume | caught | end"""
        now = self._clock()
        self.advance()
        s = self.session
        if action == "end":
            if s is None:
                raise EngineError("No session to end")
            if s.phase not in (Phase.COMPLETED, Phase.FAILED):
                self._archive(s, now, completed=False)
            self.session = None
            self._changed()
            return
        if s is None:
            raise EngineError("No session is running")
        if action == "pause":
            if s.phase not in (Phase.FOCUSING, Phase.NUDGE, Phase.BARKING):
                raise EngineError(f"Cannot pause while {s.phase.value}")
            self._enter(Phase.PAUSED, now, "Paused")
        elif action == "resume":
            if s.phase is not Phase.PAUSED:
                raise EngineError("Session is not paused")
            self._back_to_focusing(now, "Resumed")
        elif action == "caught":
            self._caught(now, "Caught (marked in app)")
            self._say("caught", now)
        else:
            raise InvalidInput(f"Unknown action: {action}")
        self._changed()

    def update_settings(self, changes: dict[str, Any]) -> None:
        self.settings.update(changes)
        self._changed()
        self.advance()

    # ----- reports from devices ---------------------------------------------

    def robot_event(self, event: str, info: dict[str, Any] | None = None) -> None:
        """heartbeat | docked | undocked | caught"""
        now = self._clock()
        if event not in {"heartbeat", "docked", "undocked", "caught"}:
            raise InvalidInput(f"Unknown robot event: {event}")
        self.robot_last_seen = now
        if info and any(self.robot_info.get(k) != v for k, v in info.items()):
            self.robot_info.update(info)
            self._changed()
        s = self.session
        if event in ("docked", "undocked") and self.phone_docked != (event == "docked"):
            self.phone_docked = event == "docked"
            if s is not None and s.phase is not Phase.AWAITING_PHONE:
                self._log(now, "Phone docked" if self.phone_docked else "Phone removed")
            self._changed()
        elif event == "caught":
            # The NFC "good boy" card only means something during an escape.
            if s is not None and s.phase is Phase.ESCAPED:
                self._caught(now, "Caught (robot stop button)")
                self._say("caught", now)
                self._changed()
        self.advance()

    def record_reading(self, source: Any, reading: Any, vitals: Any = None) -> None:
        """Latest YES / NO from a camera running detector/agent.py."""
        now = self._clock()
        if not isinstance(source, str) or not SOURCE_NAME.fullmatch(source):
            raise InvalidInput("source must be 1-32 letters, digits, '-' or '_'")
        if source not in self.sources and len(self.sources) >= MAX_SOURCES:
            raise InvalidInput(f"At most {MAX_SOURCES} cameras can report")
        if not isinstance(reading, dict):
            raise InvalidInput("reading must be an object")
        focused, calibrated, reason = reading.get("focused"), reading.get("calibrated"), reading.get("reason")
        if not isinstance(focused, bool) or not isinstance(calibrated, bool):
            raise InvalidInput("reading.focused and reading.calibrated must be true or false")
        if not isinstance(reason, str) or len(reason) > 200:
            raise InvalidInput("reading.reason must be a string of at most 200 characters")
        fps = reading.get("fps")
        timers = reading.get("timers") or {}
        if fps is not None and (isinstance(fps, bool) or not isinstance(fps, (int, float))):
            raise InvalidInput("reading.fps must be a number")
        if not isinstance(timers, dict) or not all(
            isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) for k, v in timers.items()
        ):
            raise InvalidInput("reading.timers must map names to numbers")
        if vitals is not None and not isinstance(vitals, dict):
            raise InvalidInput("vitals must be an object")

        self.sources[source] = SourceReading(focused, calibrated, reason, now, fps, dict(timers))
        if vitals is not None:
            self.vitals = vitals
        self._changed()
        self.advance()

    # ----- time ---------------------------------------------------------------

    def advance(self) -> None:
        """Apply every time-based rule. Call after each event and on a timer."""
        now = self._clock()
        s = self.session
        st = self.settings
        liveness = self._liveness(now)
        if liveness != self._last_liveness:
            self._last_liveness = liveness  # a device came online or went quiet
            self._changed()
        if s is not None and s.phase is Phase.AWAITING_PHONE and (self.phone_docked or not st.require_dock):
            self._enter(Phase.FOCUSING, now, "Phone docked" if self.phone_docked else "Started without docking")
            self._say("start", now)
            self._changed()
        if s is not None and s.phase is Phase.ESCAPED:
            if now - s.phase_since >= st.escape_timeout_seconds:
                self._stop_escape(now, "Escape timed out; Fido stopped")
                self._say("timeout", now)
                self._changed()
            elif self.speech and self.speech["cue"] == "run" and now - self.speech["at"] >= st.speech_repeat_seconds:
                if self._say("run", now):  # keep taunting while the user chases Fido
                    self._changed()
            return
        if s is None or s.phase not in ACTIVE_PHASES:
            return

        if s.remaining_seconds(now) <= 0:
            self._enter(Phase.COMPLETED, now, "Time's up. Well done!")
            self._say("done", now)
            self._archive(s, now, completed=True)
            self._changed()
            return

        distracted, reason = self._distraction(now)
        if not distracted or now < s.grace_until:
            if s.phase in (Phase.NUDGE, Phase.BARKING):
                self._enter(Phase.FOCUSING, now, "Back on track")
                if self._nudge_voiced:
                    self._say("praise", now)
                self._nudge_voiced = False
                self._changed()
            return

        in_phase = now - s.phase_since
        if s.phase is Phase.FOCUSING:
            s.distractions += 1
            self._enter(Phase.NUDGE, now, reason)
            # A flickering camera must not make Fido chatter: one spoken nudge per NUDGE_COOLDOWN_SECONDS.
            if now - self._last_nudge_at >= NUDGE_COOLDOWN_SECONDS and self._say("nudge", now):
                self._last_nudge_at = now
                self._nudge_voiced = True
        elif s.phase is Phase.NUDGE and in_phase >= st.bark_after_seconds:
            self._enter(Phase.BARKING, now, reason)
            self._say("bark", now)
        elif s.phase is Phase.BARKING and in_phase >= st.escape_after_seconds:
            s.escapes += 1
            self._enter(Phase.ESCAPED, now, reason)
            self._say("run", now)
        elif s.reason != reason:
            s.reason = reason  # same phase, newer explanation
        else:
            return
        self._changed()

    # ----- read model -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        s = self.session
        phase = s.phase if s else Phase.IDLE
        stale = self.settings.source_stale_seconds
        sources = {
            name: {**asdict(r), "fresh": now - r.received_at <= stale} for name, r in self.sources.items()
        }
        fresh = [r for r in sources.values() if r["fresh"] and r["calibrated"]]
        return {
            "version": self.version,
            "server_time": now,
            "phase": phase.value,
            "session": None
            if s is None
            else {
                "id": s.id,
                "task": s.task,
                "phase": s.phase.value,
                "phase_since": s.phase_since,
                "reason": s.reason,
                "duration_seconds": s.duration_seconds,
                "remaining_seconds": round(s.remaining_seconds(now), 1),
                "clock_running": s.clock_started_at is not None,
                "distractions": s.distractions,
                "escapes": s.escapes,
                "grace_until": s.grace_until,
                "created_at": s.created_at,
                "events": list(s.events),
            },
            "robot": {
                "mode": ROBOT_MODES[phase],
                "online": self._robot_online(now),
                "last_seen": self.robot_last_seen,
                "phone_docked": self.phone_docked,
                "info": dict(self.robot_info),
            },
            "detector": {
                # None until at least one calibrated camera is reporting.
                "focused": all(r["focused"] for r in fresh) if fresh else None,
                "sources": sources,
            },
            "vitals": self.vitals,
            "speech": self.speech,
            "settings": asdict(self.settings),
        }

    # ----- internals --------------------------------------------------------------

    def _distraction(self, now: float) -> tuple[bool, str]:
        """Distracted if the phone left the dock or any live camera says NO."""
        if self.settings.require_dock and not self.phone_docked:
            return True, "Phone removed from Fido"
        for name, r in sorted(self.sources.items()):
            if r.calibrated and not r.focused and now - r.received_at <= self.settings.source_stale_seconds:
                return True, f"{r.reason} [{name}]"
        return False, ""

    def _say(self, cue: str, now: float) -> bool:
        """Record a caption/audio event for `cue` (see PLAYLIST). Returns False
        when the cue has no clips. Global mute gates playback, not captions.

        A cue's clips rotate in order. Players speak each `seq` once; they decide how
        (robot speaker, laptop, phone).
        """
        clips = PLAYLIST.get(cue)
        if not clips:
            return False
        index = self._cue_counts.get(cue, 0) % len(clips)
        self._cue_counts[cue] = index + 1
        self._speech_seq += 1
        clip = clips[index]
        self.speech = {
            "seq": self._speech_seq,
            "cue": cue,
            "clip": clip,
            "text": CAPTIONS[clip],
            "at": now,
            "url": f"/api/audio/{clip}.mp3",
        }
        return True

    def _liveness(self, now: float) -> tuple[bool, ...]:
        stale = self.settings.source_stale_seconds
        sources = (now - r.received_at <= stale for _, r in sorted(self.sources.items()))
        return (self._robot_online(now), *sources)

    def _robot_online(self, now: float) -> bool:
        return self.robot_last_seen is not None and now - self.robot_last_seen <= self.settings.robot_offline_seconds

    def _caught(self, now: float, reason: str) -> None:
        s = self.session
        if s is None or s.phase is not Phase.ESCAPED:
            raise EngineError("Fido is not running away")
        self._stop_escape(now, reason)

    def _stop_escape(self, now: float, reason: str) -> None:
        assert self.session is not None
        self._enter(Phase.FAILED, now, f"{reason}. Session failed; press Start to begin again.")
        self._archive(self.session, now, completed=False)

    def _back_to_focusing(self, now: float, reason: str) -> None:
        assert self.session is not None
        self.session.grace_until = now + self.settings.recover_grace_seconds
        self._enter(Phase.FOCUSING, now, reason)

    def _enter(self, phase: Phase, now: float, reason: str) -> None:
        s = self.session
        assert s is not None
        was_active = s.phase in ACTIVE_PHASES
        is_active = phase in ACTIVE_PHASES
        if was_active and not is_active:
            s.elapsed_seconds = s.active_seconds(now)
            s.clock_started_at = None
        elif is_active and not was_active:
            s.clock_started_at = now
        s.phase = phase
        s.phase_since = now
        s.reason = reason
        self._log(now, reason, phase)

    def _log(self, now: float, message: str, phase: Phase | None = None) -> None:
        s = self.session
        assert s is not None
        s.events.append({"at": now, "phase": (phase or s.phase).value, "message": message})
        del s.events[:-MAX_SESSION_EVENTS]

    def _archive(self, s: Session, now: float, completed: bool) -> None:
        self.history.insert(
            0,
            {
                "id": s.id,
                "task": s.task,
                "duration_seconds": s.duration_seconds,
                "focused_seconds": round(min(s.active_seconds(now), s.duration_seconds)),
                "distractions": s.distractions,
                "escapes": s.escapes,
                "completed": completed,
                "started_at": s.created_at,
                "ended_at": now,
            },
        )
        del self.history[MAX_HISTORY:]

    def _changed(self) -> None:
        self.version += 1
