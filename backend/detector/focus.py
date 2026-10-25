"""Binary focus detection (YES / NO), ported from the prototype.

Source: archive/oxhack26-prototype/detector/focus_detector.py (vayunk/Oxhack26).
Geometry and auto-calibration are preserved. Talking/blinking come from Presage;
prolonged eye closure is derived from its documented face landmarks, not from
MediaPipe blendshapes. The prototype's main loop is split for direct unit tests:

- `compute_face_geometry`: MediaPipe landmarks -> `FaceSignals` (verbatim maths).
- `FocusTracker`: the per-frame timers that decide YES / NO. Pure Python, no
  camera or MediaPipe, so it is unit-tested directly.
- `FaceLandmarkSource`: wraps the MediaPipe FaceLandmarker for one camera frame.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .presage import PresageReading

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_landmarker.task")


@dataclass(frozen=True)
class DetectorConfig:
    """Prototype gaze thresholds and Presage decision thresholds."""

    slack_trigger_seconds: float = 8.0  # continuous slacking before NO
    micro_sleep_seconds: float = 4.0  # continuous eyes closed
    talking_trigger_seconds: float = 0.6  # stable Presage talking within the last 6 s
    focus_recovery_seconds: float = 2.0  # continuous YES required to release NO
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
    # Legacy prototype inputs retained for callers; never used for decisions.
    eye_blink: float = 0.0
    jaw_open: float = 0.0


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
    talking: bool | None
    blinking: bool | None
    blinks: int
    eyes_closed: bool | None
    presage_status: str


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

    eye_down = 0.0
    if blendshapes:
        shapes = {s.category_name: s.score for s in blendshapes[0]}
        eye_down = (shapes.get("eyeLookDownLeft", 0.0) + shapes.get("eyeLookDownRight", 0.0)) / 2.0

    return FaceSignals(pitch_ratio, yaw_ratio, eye_down)


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
    _unfocused_reason: str = ""
    _recovery_seconds: float | None = None
    _calibration_samples: list[tuple[float, float]] = field(default_factory=list)

    def recalibrate(self) -> None:
        """Forget the baseline and all timers, e.g. when a new session starts."""
        self.calibrated = False
        self.base_pitch, self.base_yaw = 0.40, 0.0
        self.distracted_seconds = self.eyes_closed_seconds = 0.0
        self.talking_seconds = 0.0
        self._unfocused_reason = ""
        self._recovery_seconds = None
        self._calibration_samples.clear()

    def update(
        self, signals: FaceSignals | None, dt: float, presage: PresageReading | None = None
    ) -> FocusReading:
        c = self.config
        is_slacking_frame = False
        reason = "Looking at Screen"
        pitch_ratio = yaw_ratio = 0.0
        self.eyes_closed_seconds = self.talking_seconds = 0.0

        if signals is None:
            is_slacking_frame = True
            reason = "No Face (Desk Abandoned)"
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
                reason, is_slacking_frame = self._classify_face(signals)
                if presage is not None:
                    self.eyes_closed_seconds = presage.eyes_closed_seconds
                    self.talking_seconds = presage.talking_seconds

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
            reason = f"Prolonged eye closure ({self.eyes_closed_seconds:.1f}s; Presage landmarks)"
        elif self.talking_seconds >= c.talking_trigger_seconds:
            focused = False
            reason = (
                f"Talking / Chatting (Presage: {self.talking_seconds:.1f}s detected in last 6s)"
            )
        elif self.distracted_seconds >= c.slack_trigger_seconds:
            focused = False

        # NO takes effect immediately. Release it only after a continuous run
        # of clear frames, so preview and backend share one anti-flicker rule.
        if not focused:
            self._unfocused_reason = reason
            self._recovery_seconds = None
        elif self._unfocused_reason:
            # The first clear frame starts the clock. A capture stall is not
            # evidence of sustained focus and must not satisfy the cooldown.
            if self._recovery_seconds is None or not 0 <= dt <= 1.0:
                self._recovery_seconds = 0.0
            else:
                self._recovery_seconds += dt
            if self._recovery_seconds + 1e-9 < c.focus_recovery_seconds:
                focused = False
                reason = (
                    f"{self._unfocused_reason} "
                    f"(recovering {self._recovery_seconds:.1f}/{c.focus_recovery_seconds:g}s)"
                )
            else:
                self._unfocused_reason = ""
                self._recovery_seconds = None

        if presage:
            unknown = [name for name, value in (
                ("talking", presage.talking), ("eye closure", presage.eyes_closed),
            ) if value is None]
            if unknown:
                reason += f" | Presage {presage.status}; face activity unknown: {', '.join(unknown)}"

        return FocusReading(
            focused=focused,
            reason=reason,
            calibrated=self.calibrated,
            pitch_ratio=pitch_ratio,
            yaw_ratio=yaw_ratio,
            slacking_seconds=self.distracted_seconds,
            eyes_closed_seconds=self.eyes_closed_seconds,
            talking_seconds=self.talking_seconds,
            talking=presage.talking if presage and signals is not None else None,
            blinking=presage.blinking if presage and signals is not None else None,
            blinks=presage.blinks if presage else 0,
            eyes_closed=presage.eyes_closed if presage and signals is not None else None,
            presage_status=presage.status if presage else "disabled",
        )

    def _classify_face(self, s: FaceSignals) -> tuple[str, bool]:
        c = self.config
        pitch_diff = s.pitch_ratio - self.base_pitch
        yaw_diff = abs(s.yaw_ratio - self.base_yaw)

        # Spatial slacking stays local; face activity is exclusively Presage.
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
