import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np
import time
import json
import os

# ==========================================================
# SUPER ACCURATE BINARY FOCUS DETECTOR: YES or NO
# Includes:
#   1. Phone / Lap looking down (Pitch)
#   2. Ceiling daydreaming (Pitch Up)
#   3. Looking away sideways (Yaw)
#   4. Micro-sleep detection (Eyes closed ~4s)
#   5. Talking / Chatting detection (Mouth active ~2.5s)
#   6. Presage Vitals live badge integration
# ==========================================================

DIR_PATH = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(DIR_PATH, "focus_log.json")
MODEL_PATH = os.path.join(DIR_PATH, "face_landmarker.task")
PRESAGE_VITALS_FILE = os.path.join(DIR_PATH, "presage_vitals.json")

# Timing Configurations
SLACK_TRIGGER_SECONDS = 8.0     # 8s continuous slacking before NO
MICRO_SLEEP_SECONDS = 4.0       # 4s continuous eyes closed
TALKING_TRIGGER_SECONDS = 2.0   # 2.0s continuous talking

# Thresholds
PHONE_PITCH_THRESHOLD = -22.0
LOOKING_AWAY_YAW = 25.0
EYE_CLOSED_THRESHOLD = 0.55
JAW_OPEN_THRESHOLD = 0.065        # Increased sensitivity: catches natural speech easily
SILENCE_GRACE_SECONDS = 0.8       # Silence required to confirm speech has fully ended

# Initialize MediaPipe Task FaceLandmarker
base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
options = vision.FaceLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.IMAGE,
    output_face_blendshapes=True,
    num_faces=1
)
detector = vision.FaceLandmarker.create_from_options(options)

def compute_face_geometry(landmarks, blendshapes):
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
    eye_blink = 0.0
    jaw_open = 0.0

    if blendshapes:
        shapes = {s.category_name: s.score for s in blendshapes[0]}
        eye_down = (shapes.get('eyeLookDownLeft', 0.0) + shapes.get('eyeLookDownRight', 0.0)) / 2.0
        eye_blink = (shapes.get('eyeBlinkLeft', 0.0) + shapes.get('eyeBlinkRight', 0.0)) / 2.0
        jaw_open = shapes.get('jawOpen', 0.0)

    return pitch_ratio, yaw_ratio, eye_down, eye_blink, jaw_open

def load_presage_vitals():
    if os.path.exists(PRESAGE_VITALS_FILE):
        try:
            with open(PRESAGE_VITALS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ ERROR: Could not open webcam.")
        return

    print("\n==========================================================")
    print("   🎯 BINARY FOCUS DETECTOR: FOCUS = YES / NO              ")
    print(f"   ⏱️  Slacking Threshold: {SLACK_TRIGGER_SECONDS}s before NO")
    print(f"   😴 Micro-Sleep: {MICRO_SLEEP_SECONDS}s eyes closed       ")
    print(f"   🗣️  Talking: {TALKING_TRIGGER_SECONDS}s mouth active     ")
    print(f"   💾 Logging live to: detector/focus_log.json            ")
    print("   Press 'q' in the window to quit                        ")
    print("==========================================================\n")

    distracted_seconds = 0.0
    eyes_closed_seconds = 0.0
    talking_seconds = 0.0
    silence_seconds = 0.0

    last_time = time.time()
    last_log_time = 0.0
    current_state = "YES"

    # Auto-Calibration
    calibrated = False
    calibration_samples = []
    base_pitch = 0.40
    base_yaw = 0.0

    logs = []

    def write_log(event_type, reason, p_ratio, y_ratio, vitals):
        entry = {
            "timestamp": time.strftime("%H:%M:%S"),
            "focus": current_state,
            "event": event_type,
            "reason": reason,
            "pitch_ratio": round(p_ratio, 2),
            "yaw_ratio": round(y_ratio, 2),
            "slacking_timer_sec": round(distracted_seconds, 1),
            "presageVitals": vitals
        }
        logs.append(entry)
        if len(logs) > 300:
            logs.pop(0)
        try:
            with open(LOG_FILE, "w") as f:
                json.dump({"currentState": current_state, "slackingTimer": round(distracted_seconds, 1), "vitals": vitals, "history": logs[-40:]}, f, indent=2)
        except Exception:
            pass

    write_log("START", "System initialized", 0, 0, None)

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        now = time.time()
        dt = now - last_time
        last_time = now

        frame = cv2.flip(frame, 1)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        detection_result = detector.detect(mp_image)

        is_slacking_frame = False
        reason = "Looking at Screen"
        pitch_ratio, yaw_ratio = 0.0, 0.0

        if not detection_result.face_landmarks:
            is_slacking_frame = True
            reason = "No Face (Desk Abandoned)"
            eyes_closed_seconds = 0.0
            talking_seconds = 0.0
        else:
            landmarks = detection_result.face_landmarks[0]
            blendshapes = detection_result.face_blendshapes
            pitch_ratio, yaw_ratio, eye_down, eye_blink, jaw_open = compute_face_geometry(landmarks, blendshapes)

            # Auto-calibration phase (first 30 frames)
            if not calibrated:
                calibration_samples.append((pitch_ratio, yaw_ratio))
                if len(calibration_samples) >= 30:
                    base_pitch = float(np.median([p[0] for p in calibration_samples]))
                    base_yaw = float(np.median([p[1] for p in calibration_samples]))
                    calibrated = True
                    print(f"✅ Calibrated: Baseline Pitch={base_pitch:.2f}, Yaw={base_yaw:.2f}")
                reason = "Calibrating posture..."
            else:
                pitch_diff = pitch_ratio - base_pitch
                yaw_diff = abs(yaw_ratio - base_yaw)

                # 1. Micro-Sleep Tracker (Eyes closed -> triggers NO at 4.0s)
                if eye_blink > EYE_CLOSED_THRESHOLD:
                    eyes_closed_seconds += dt
                else:
                    # Instant awake reset when eyes open
                    eyes_closed_seconds = 0.0

                # 2. Talking / Chatting Tracker (Conversation-aware)
                # Sensitive speech detection: keeps gaining continuously throughout speech until silence is detected
                if jaw_open > JAW_OPEN_THRESHOLD:
                    talking_seconds = min(TALKING_TRIGGER_SECONDS + 0.8, talking_seconds + dt)
                    silence_seconds = 0.0
                elif talking_seconds > 0.0:
                    # Inside active speech - do NOT stop gaining during syllable/word pauses!
                    if silence_seconds < SILENCE_GRACE_SECONDS:
                        talking_seconds = min(TALKING_TRIGGER_SECONDS + 0.8, talking_seconds + dt)
                        silence_seconds += dt
                    else:
                        # Full silence confirmed -> speech session ended, reset immediately
                        talking_seconds = 0.0
                        silence_seconds = 0.0
                else:
                    silence_seconds += dt

                # 3. Spatial Slacking (Phone / Looking away -> triggers NO at 8.0s)
                if pitch_diff > 0.28 or (pitch_diff > 0.18 and eye_down > 0.82):
                    is_slacking_frame = True
                    reason = f"Looking Down at Phone/Lap (ΔPitch: +{pitch_diff:.2f})"
                elif pitch_diff < -0.20:
                    is_slacking_frame = True
                    reason = f"Looking Up / Daydreaming (ΔPitch: {pitch_diff:.2f})"
                elif yaw_diff > 0.28:
                    is_slacking_frame = True
                    reason = f"Looking Away Sideways (ΔYaw: {yaw_diff:.2f})"
                else:
                    reason = "Looking at Screen"

        # Timer accumulation for general gaze slacking (capped at 9.0s max)
        if calibrated:
            if is_slacking_frame:
                distracted_seconds = min(SLACK_TRIGGER_SECONDS + 1.0, distracted_seconds + dt)
            else:
                # Fast recovery when looking at screen
                distracted_seconds = max(0.0, distracted_seconds - dt * 3.0)

        # Decide binary state (Direct triggers for Micro-sleep and Talking!)
        previous_state = current_state
        is_not_focused = False
        active_reason = reason

        if eyes_closed_seconds >= MICRO_SLEEP_SECONDS:
            is_not_focused = True
            active_reason = f"Micro-Sleep ({eyes_closed_seconds:.1f}s eyes closed)"
        elif talking_seconds >= TALKING_TRIGGER_SECONDS:
            is_not_focused = True
            active_reason = f"Talking / Chatting ({talking_seconds:.1f}s)"
        elif distracted_seconds >= SLACK_TRIGGER_SECONDS:
            is_not_focused = True
            active_reason = reason

        if is_not_focused:
            current_state = "NO"
            color = (0, 0, 220) # Red
        else:
            current_state = "YES"
            color = (0, 200, 0) # Green

        # Load Presage vitals if available
        vitals = load_presage_vitals()

        # Log state changes or periodic heartbeat
        if current_state != previous_state:
            write_log("STATE_CHANGE", f"Flipped from {previous_state} to {current_state} ({active_reason})", pitch_ratio, yaw_ratio, vitals)
            print(f"[{time.strftime('%H:%M:%S')}] 🚨 STATE CHANGED: FOCUS = {current_state} ({active_reason})")
        elif now - last_log_time >= 1.0:
            last_log_time = now
            write_log("PERIODIC", active_reason, pitch_ratio, yaw_ratio, vitals)

        # UI Overlay
        h, w = frame.shape[:2]

        if not calibrated:
            cv2.rectangle(frame, (0, 0), (w, 90), (200, 150, 0), -1)
            cv2.putText(frame, "CALIBRATING... LOOK AT SCREEN", (25, 60),
                        cv2.FONT_HERSHEY_DUPLEX, 1.2, (255, 255, 255), 2)
        else:
            # Top Banner: Giant YES or NO
            cv2.rectangle(frame, (0, 0), (w, 90), color, -1)
            cv2.putText(frame, f"FOCUS: {current_state}", (25, 65),
                        cv2.FONT_HERSHEY_DUPLEX, 1.8, (255, 255, 255), 3)

            # Timer Indicator (shows whichever timer is actively ticking)
            if eyes_closed_seconds > 0.8:
                timer_text = f"Sleep: {eyes_closed_seconds:.1f}s / {MICRO_SLEEP_SECONDS}s"
                fill_ratio = min(1.0, eyes_closed_seconds / MICRO_SLEEP_SECONDS)
            elif talking_seconds > 0.2:
                timer_text = f"Talking: {talking_seconds:.1f}s / {TALKING_TRIGGER_SECONDS}s"
                fill_ratio = min(1.0, talking_seconds / TALKING_TRIGGER_SECONDS)
            else:
                timer_text = f"Slacking: {distracted_seconds:.1f}s / {SLACK_TRIGGER_SECONDS}s"
                fill_ratio = min(1.0, distracted_seconds / SLACK_TRIGGER_SECONDS)

            cv2.putText(frame, timer_text, (w - 380, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2)

            # Progress bar under top banner
            bar_width = int(w * fill_ratio)
            if bar_width > 0:
                cv2.rectangle(frame, (0, 85), (bar_width, 92), (0, 0, 255), -1)

        # Presage Vitals Badge (Above Bottom Bar)
        if vitals:
            v_text = f"[PRESAGE BIOMETRICS] Pulse: {vitals.get('pulse', '--')} BPM | Breath: {vitals.get('breathing', '--')} | HRV: {vitals.get('hrvRmssd', '--')}"
            cv2.rectangle(frame, (0, h - 85), (w, h - 50), (40, 40, 40), -1)
            cv2.putText(frame, v_text, (20, h - 62),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 255), 2)

        # Bottom Bar: Diagnostic Reason
        cv2.rectangle(frame, (0, h - 50), (w, h), (25, 25, 25), -1)
        cv2.putText(frame, f"Diagnostic: {active_reason}", (20, h - 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)

        cv2.imshow("Binary Focus Detector (YES/NO)", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    write_log("STOP", "User closed detector", 0, 0, None)
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
