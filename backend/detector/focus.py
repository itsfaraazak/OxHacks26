"""Binary focus detection (YES / NO), ported from the prototype.

Source: archive/oxhack26-prototype/detector/focus_detector.py (vayunk/Oxhack26).
Geometry and auto-calibration are preserved; talking now uses a rolling window.
The prototype's
single `main()` loop is split into three parts so the backend can reuse them:

- `compute_face_geometry`: MediaPipe landmarks -> `FaceSignals` (verbatim maths).
- `FocusTracker`: the per-frame timers that decide YES / NO. Pure Python, no
  camera or MediaPipe, so it is unit-tested directly.
- `FaceLandmarkSource`: wraps the MediaPipe FaceLandmarker for one camera frame.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_landmarker.task")


@dataclass(frozen=True)
class DetectorConfig:
    """Prototype thresholds with rolling-window talking detection."""

    slack_trigger_seconds: float = 8.0  # continuous slacking before NO
    micro_sleep_seconds: float = 4.0  # continuous eyes closed
    talking_trigger_seconds: float = 2.0  # mouth-open activity within the window
    talking_window_seconds: float = 6.0
    eye_closed_threshold: float = 0.55
    jaw_open_threshold: float = 0.065
    calibration_frames: int = 30
    # Deviation from the calibrated baseline that counts as slacking.
    phone_pitch_delta: float = 0.28
    phone_pitch_delta_with_eyes_down: float = 0.18
    eyes_down_threshold: float = 0.82
    look_up_pitch_delta: float = -0.20
    look_away_yaw_delta: float = 0.28


@dataclass(frozen=True)
class FaceSignals:
    pitch_ratio: float
    yaw_ratio: float
    eye_down: float
    eye_blink: float
    jaw_open: float


@dataclass(frozen=True)
class FocusReading:
    focused: bool  # the prototype's FOCUS = YES / NO
    reason: str
    calibrated: bool
    pitch_ratio: float
    yaw_ratio: float
    slacking_seconds: float
    eyes_closed_seconds: float
    talking_seconds: float


def compute_face_geometry(landmarks, blendshapes) -> FaceSignals:
    """Head pose ratios and blendshape scores, unchanged from the prototype."""
    nose = landmarks[1]
    eye_l = landmarks[33]
    eye_r = landmarks[263]
    chin = landmarks[152]

    eye_mid_x = (eye_l.x + eye_r.x) / 2.0
    eye_mid_y = (eye_l.y + eye_r.y) / 2.0

    eye_span = max(0.01, abs(eye_r.x - eye_l.x))
    yaw_ratio = (nose.x - eye_mid_x) / eye_span

    upper_face = max(0.01, nose.y - eye_mid_y)
    lower_face = max(0.01, chin.y - nose.y)
    pitch_ratio = upper_face / lower_face

    eye_down = eye_blink = jaw_open = 0.0
    if blendshapes:
        shapes = {s.category_name: s.score for s in blendshapes[0]}
        eye_down = (shapes.get("eyeLookDownLeft", 0.0) + shapes.get("eyeLookDownRight", 0.0)) / 2.0
        eye_blink = (shapes.get("eyeBlinkLeft", 0.0) + shapes.get("eyeBlinkRight", 0.0)) / 2.0
        jaw_open = shapes.get("jawOpen", 0.0)

    return FaceSignals(pitch_ratio, yaw_ratio, eye_down, eye_blink, jaw_open)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


@dataclass
class FocusTracker:
    """The prototype's per-frame decision loop. Feed one frame at a time.

    `signals` is None when no face was found. `dt` is the seconds since the
    previous frame, so the rules hold at any frame rate.
    """

    config: DetectorConfig = field(default_factory=DetectorConfig)
    calibrated: bool = False
    base_pitch: float = 0.40
    base_yaw: float = 0.0
    distracted_seconds: float = 0.0
    eyes_closed_seconds: float = 0.0
    talking_seconds: float = 0.0
    _talk_window: deque[tuple[float, bool]] = field(default_factory=deque)
    _talk_window_seconds: float = 0.0
    _calibration_samples: list[tuple[float, float]] = field(default_factory=list)

    def recalibrate(self) -> None:
        """Forget the baseline and all timers, e.g. when a new session starts."""
        self.calibrated = False
        self.base_pitch, self.base_yaw = 0.40, 0.0
        self.distracted_seconds = self.eyes_closed_seconds = 0.0
        self.talking_seconds = self._talk_window_seconds = 0.0
        self._talk_window.clear()
        self._calibration_samples.clear()

    def update(self, signals: FaceSignals | None, dt: float) -> FocusReading:
        c = self.config
        is_slacking_frame = False
        reason = "Looking at Screen"
        pitch_ratio = yaw_ratio = 0.0

        if signals is None:
            is_slacking_frame = True
            reason = "No Face (Desk Abandoned)"
            self.eyes_closed_seconds = 0.0
            self.talking_seconds = 0.0
            self._talk_window_seconds = 0.0
            self._talk_window.clear()
        else:
            pitch_ratio, yaw_ratio = signals.pitch_ratio, signals.yaw_ratio
            if not self.calibrated:
                self._calibration_samples.append((pitch_ratio, yaw_ratio))
                if len(self._calibration_samples) >= c.calibration_frames:
                    self.base_pitch = _median([p for p, _ in self._calibration_samples])
                    self.base_yaw = _median([y for _, y in self._calibration_samples])
                    self.calibrated = True
                reason = "Calibrating posture..."
            else:
                reason, is_slacking_frame = self._classify_face(signals, dt)

        # General gaze slacking timer, capped one second past the trigger.
        if self.calibrated:
            if is_slacking_frame:
                self.distracted_seconds = min(c.slack_trigger_seconds + 1.0, self.distracted_seconds + dt)
            else:
                self.distracted_seconds = max(0.0, self.distracted_seconds - dt * 3.0)  # fast recovery

        # Micro-sleep and talking trigger NO directly; gaze needs the full slack timer.
        focused = True
        if self.eyes_closed_seconds >= c.micro_sleep_seconds:
            focused = False
            reason = f"Micro-Sleep ({self.eyes_closed_seconds:.1f}s eyes closed)"
        elif self.talking_seconds >= c.talking_trigger_seconds:
            focused = False
            reason = f"Talking / Chatting ({self.talking_seconds:.1f}s in last {c.talking_window_seconds:g}s)"
        elif self.distracted_seconds >= c.slack_trigger_seconds:
            focused = False

        return FocusReading(
            focused=focused,
            reason=reason,
            calibrated=self.calibrated,
            pitch_ratio=pitch_ratio,
            yaw_ratio=yaw_ratio,
            slacking_seconds=self.distracted_seconds,
            eyes_closed_seconds=self.eyes_closed_seconds,
            talking_seconds=self.talking_seconds,
        )

    def _classify_face(self, s: FaceSignals, dt: float) -> tuple[str, bool]:
        c = self.config
        pitch_diff = s.pitch_ratio - self.base_pitch
        yaw_diff = abs(s.yaw_ratio - self.base_yaw)

        # 1. Micro-sleep: eyes must stay closed; any open frame resets.
        self.eyes_closed_seconds = self.eyes_closed_seconds + dt if s.eye_blink > c.eye_closed_threshold else 0.0

        # 2. Talking: accumulate actual mouth-open time across short bursts.
        # Silence ages old activity out of the window; it never adds speech.
        duration = max(0.0, dt)
        talking = s.jaw_open > c.jaw_open_threshold
        if duration:
            self._talk_window.append((duration, talking))
            self._talk_window_seconds += duration
            if talking:
                self.talking_seconds += duration
        excess = self._talk_window_seconds - c.talking_window_seconds
        while excess > 0 and self._talk_window:
            oldest_duration, was_talking = self._talk_window.popleft()
            removed = min(excess, oldest_duration)
            self._talk_window_seconds -= removed
            if was_talking:
                self.talking_seconds -= removed
            if removed < oldest_duration:
                self._talk_window.appendleft((oldest_duration - removed, was_talking))
            excess = self._talk_window_seconds - c.talking_window_seconds
        self.talking_seconds = max(0.0, self.talking_seconds)

        # 3. Spatial slacking: phone / lap, ceiling, or sideways.
        if pitch_diff > c.phone_pitch_delta or (
            pitch_diff > c.phone_pitch_delta_with_eyes_down and s.eye_down > c.eyes_down_threshold
        ):
            return f"Looking Down at Phone/Lap (ΔPitch: +{pitch_diff:.2f})", True
        if pitch_diff < c.look_up_pitch_delta:
            return f"Looking Up / Daydreaming (ΔPitch: {pitch_diff:.2f})", True
        if yaw_diff > c.look_away_yaw_delta:
            return f"Looking Away Sideways (ΔYaw: {yaw_diff:.2f})", True
        return "Looking at Screen", False


class FaceLandmarkSource:
    """MediaPipe FaceLandmarker in IMAGE mode, configured as in the prototype."""

    def __init__(self, model_path: str = MODEL_PATH):
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        self._mp = mp
        options = vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.IMAGE,
            output_face_blendshapes=True,
            num_faces=1,
        )
        self._detector = vision.FaceLandmarker.create_from_options(options)

    def signals(self, rgb_frame) -> FaceSignals | None:
        """Signals for the first face in an RGB numpy frame, or None if no face."""
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb_frame)
        result = self._detector.detect(image)
        if not result.face_landmarks:
            return None
        return compute_face_geometry(result.face_landmarks[0], result.face_blendshapes)

    def close(self) -> None:
        self._detector.close()
