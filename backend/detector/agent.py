"""Camera agent: the prototype's focus detector, reporting to the Fido backend.

Runs next to a camera (the laptop webcam, or the dog's own webcam) and keeps the
prototype's behaviour: MediaPipe + OpenCV, auto-calibration, the YES / NO
banner, and the Presage vitals badge read from presage_vitals.json. Instead of
only writing focus_log.json it posts each decision to the backend, which owns
the session and the dog.

    python -m detector.agent --server http://localhost:5000 --source laptop
    python -m detector.agent --camera 1 --source dog
    python -m detector.agent --camera http://dog.local:81/stream --source dog
    python -m detector.agent --camera recording.mp4 --source replay   # demo without a webcam

Frames never leave this machine; only the decision and reason are sent.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import cv2

from .focus import DetectorConfig, FaceLandmarkSource, FocusReading, FocusTracker
from .preview import PreviewServer

DIR_PATH = os.path.dirname(os.path.abspath(__file__))
# Written by presage/test_presage.mjs, exactly as in the prototype.
PRESAGE_VITALS_FILE = os.path.join(DIR_PATH, "presage_vitals.json")
REPORT_INTERVAL_SECONDS = 0.2  # also reports immediately whenever YES / NO flips


def load_presage_vitals() -> dict[str, Any] | None:
    """Unchanged from the prototype: latest vitals exported by the Presage harness."""
    if os.path.exists(PRESAGE_VITALS_FILE):
        try:
            with open(PRESAGE_VITALS_FILE) as f:
                return json.load(f)
        except Exception:
            return None
    return None


class Reporter:
    """Posts readings on a background thread so slow Wi-Fi never stalls the camera loop.

    Only the newest reading matters, so the queue holds one item and older
    unsent readings are replaced.
    """

    def __init__(self, server: str, token: str | None):
        self.url = server.rstrip("/") + "/api/readings"
        self.token = token
        self.calibration_id: str | None = None
        self.connected = False
        self._warned = False
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        threading.Thread(target=self._run, name="fido-reporter", daemon=True).start()

    def send(self, payload: dict[str, Any]) -> None:
        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass
        self._queue.put_nowait(payload)

    def _run(self) -> None:
        while True:
            payload = self._queue.get()
            request = urllib.request.Request(
                self.url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            if self.token:
                request.add_header("Authorization", f"Bearer {self.token}")
            try:
                with urllib.request.urlopen(request, timeout=2) as response:
                    self.calibration_id = json.load(response).get("calibration_id")
                if not self.connected:
                    print(f"Connected to {self.url}")
                self.connected = True
            except (urllib.error.URLError, OSError, ValueError) as err:
                if self.connected or not self._warned:
                    print(f"Backend unreachable ({err}); retrying")
                self.connected, self._warned = False, True
                time.sleep(1.0)


def draw_overlay(frame, reading: FocusReading, config: DetectorConfig, vitals, fps: float, online: bool) -> None:
    """The prototype's on-screen UI, plus fps and backend status."""
    h, w = frame.shape[:2]
    if not reading.calibrated:
        cv2.rectangle(frame, (0, 0), (w, 90), (200, 150, 0), -1)
        cv2.putText(frame, "CALIBRATING... LOOK AT SCREEN", (25, 60), cv2.FONT_HERSHEY_DUPLEX, 1.2, (255, 255, 255), 2)
    else:
        color = (0, 200, 0) if reading.focused else (0, 0, 220)
        cv2.rectangle(frame, (0, 0), (w, 90), color, -1)
        state = "YES" if reading.focused else "NO"
        cv2.putText(frame, f"FOCUS: {state}", (25, 65), cv2.FONT_HERSHEY_DUPLEX, 1.8, (255, 255, 255), 3)

        if reading.eyes_closed_seconds > 0.8:
            text = f"Sleep: {reading.eyes_closed_seconds:.1f}s / {config.micro_sleep_seconds}s"
            fill = reading.eyes_closed_seconds / config.micro_sleep_seconds
        elif reading.talking_seconds > 0.2:
            text = f"Talking: {reading.talking_seconds:.1f}s / {config.talking_trigger_seconds}s"
            fill = reading.talking_seconds / config.talking_trigger_seconds
        else:
            text = f"Slacking: {reading.slacking_seconds:.1f}s / {config.slack_trigger_seconds}s"
            fill = reading.slacking_seconds / config.slack_trigger_seconds
        cv2.putText(frame, text, (w - 380, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2)
        bar = int(w * min(1.0, fill))
        if bar > 0:
            cv2.rectangle(frame, (0, 85), (bar, 92), (0, 0, 255), -1)

    if vitals:

        def value(key: str) -> Any:
            v = vitals.get(key)
            return "--" if v is None else v

        v_text = (
            f"[PRESAGE BIOMETRICS] Pulse: {value('pulse')} BPM | "
            f"Breath: {value('breathing')} | HRV: {value('hrvRmssd')}"
        )
        cv2.rectangle(frame, (0, h - 85), (w, h - 50), (40, 40, 40), -1)
        cv2.putText(frame, v_text, (20, h - 62), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 255), 2)

    cv2.rectangle(frame, (0, h - 50), (w, h), (25, 25, 25), -1)
    cv2.putText(frame, f"Diagnostic: {reading.reason}", (20, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
    # Small status line in the top-right corner, clear of the prototype's text.
    status = f"{fps:.0f} fps | backend {'online' if online else 'OFFLINE'}"
    (tw, _), _ = cv2.getTextSize(status, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    org = (w - tw - 12, 108)
    cv2.putText(frame, status, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)  # outline: readable on any background
    cv2.putText(frame, status, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)


def open_camera(camera: str, width: int | None, height: int | None, fps: int | None) -> cv2.VideoCapture:
    """A camera index ("0") or any OpenCV source such as an MJPEG/RTSP URL."""
    cap = cv2.VideoCapture(int(camera) if camera.isdigit() else camera)
    if width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if fps:
        cap.set(cv2.CAP_PROP_FPS, fps)
    return cap


def main() -> None:
    parser = argparse.ArgumentParser(description="Report focus YES / NO from a camera to the Fido backend.")
    parser.add_argument("--server", default=os.environ.get("FIDO_SERVER", "http://localhost:5000"))
    parser.add_argument("--source", default="laptop", help="name shown in the app, e.g. laptop or dog")
    parser.add_argument("--camera", default="0", help="camera index or stream URL")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--fps", type=int, help="request a capture rate; detection runs on every frame it can")
    parser.add_argument("--headless", action="store_true", help="disable all previews")
    parser.add_argument("--window", action="store_true", help="also open the legacy OpenCV window")
    parser.add_argument("--preview-port", type=int, default=5002, help="local website camera preview port")
    parser.add_argument("--no-flip", action="store_true", help="do not mirror the image like a selfie view")
    args = parser.parse_args()

    preview = None
    if not args.headless:
        try:
            preview = PreviewServer(args.preview_port)
        except OSError as err:
            raise SystemExit(f"Cannot start preview on port {args.preview_port}: {err}. Stop the other agent or use --preview-port.") from err
    cap = open_camera(args.camera, args.width, args.height, args.fps)
    if not cap.isOpened():
        if preview:
            preview.close()
        raise SystemExit(f"Could not open camera {args.camera!r}")
    # A video file plays at its recorded speed and loops, so it behaves like a live camera.
    is_file = os.path.isfile(args.camera)
    file_frame_seconds = 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 30.0) if is_file else 0.0
    blank_frames = 0

    config = DetectorConfig()
    tracker = FocusTracker(config)
    faces = FaceLandmarkSource()
    reporter = Reporter(args.server, os.environ.get("FIDO_DEVICE_TOKEN"))
    print(f"Watching camera {args.camera!r} as {args.source!r}; reporting to {args.server}. Ctrl+C to quit.")
    if preview:
        print(f"Website preview ready at http://localhost:{preview.port}/preview.mjpg (this computer only).")

    last_time = time.time()
    last_report = 0.0
    last_focused: bool | None = None
    calibration_id: str | None = None
    fps = 0.0
    vitals: dict[str, Any] | None = None
    last_vitals_read = 0.0

    try:
        while True:
            ok, frame = cap.read()
            if not ok and is_file:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
            if not ok:
                print("Camera stopped delivering frames")
                break
            if is_file:
                time.sleep(max(0.0, file_frame_seconds - (time.time() - last_time)))
            blank_frames = blank_frames + 1 if frame.max() < 10 else 0
            if blank_frames == 60:
                print("Camera frames are completely black: check the privacy shutter / camera kill switch")
            now = time.time()
            dt, last_time = now - last_time, now
            fps = 0.9 * fps + 0.1 * (1.0 / dt) if dt > 0 else fps

            # Start each session from a fresh posture baseline, like restarting the prototype.
            if reporter.calibration_id and reporter.calibration_id != calibration_id:
                calibration_id = reporter.calibration_id
                tracker.recalibrate()

            if not args.no_flip:
                frame = cv2.flip(frame, 1)
            reading = tracker.update(faces.signals(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), dt)

            if now - last_vitals_read >= 1.0:  # the prototype re-read this file every frame
                vitals, last_vitals_read = load_presage_vitals(), now

            if reading.focused != last_focused or now - last_report >= REPORT_INTERVAL_SECONDS:
                last_focused, last_report = reading.focused, now
                reporter.send(
                    {
                        "source": args.source,
                        "reading": {
                            "focused": reading.focused,
                            "calibrated": reading.calibrated,
                            "reason": reading.reason,
                            "fps": round(fps, 1),
                            "timers": {
                                "slacking": round(reading.slacking_seconds, 2),
                                "eyes_closed": round(reading.eyes_closed_seconds, 2),
                                "talking": round(reading.talking_seconds, 2),
                            },
                        },
                        "vitals": vitals,
                    }
                )

            if not args.headless:
                draw_overlay(frame, reading, config, vitals, fps, bool(reporter.connected))
                if preview:
                    preview.publish(frame)
                if args.window:
                    cv2.imshow(f"Fido focus detector ({args.source})", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        faces.close()
        if preview:
            preview.close()
        if args.window:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
