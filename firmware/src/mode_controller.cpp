#include "mode_controller.h"

#include <Arduino.h>

void ModeController::showMode(fido::Mode mode, const char* task) {
  using fido::Mode;
  switch (mode) {
    case Mode::Sleep: display_.show("SLEEPING", "No session"); break;
    case Mode::BeckonDock: display_.show("DOCK\nPHONE", task); break;
    case Mode::Watch: display_.show("STAY\nFOCUSED", task); break;
    case Mode::Nudge: display_.show("HEY!\nFOCUS", task); break;
    case Mode::Bark: display_.show("LAST\nWARNING", "WOOF!"); break;
    case Mode::Run: display_.show("CATCH ME!", "Press button to stop"); break;
    case Mode::Rest: display_.show("RESTING", "Stopped / paused"); break;
    case Mode::Celebrate: display_.show("GOOD JOB!", "Session complete"); break;
    case Mode::Unknown: display_.show("WAITING\nFOR APP", ""); break;
  }
}

void ModeController::update(uint32_t now_ms, const char* task, bool rangeFresh, float rangeCm, uint32_t sampleMs) {
  SharedSafety::Tick t = safety_.tick(now_ms, rangeFresh, rangeCm, sampleMs);
  why_ = t.why;

  fido::Mode faceMode = t.why == fido::StopReason::Watchdog ||
                        t.why == fido::StopReason::NoReply ||
                        t.why == fido::StopReason::HttpErrors ? fido::Mode::Unknown : t.mode;
  if (!haveShown_ || faceMode != shown_) {
    haveShown_ = true;
    shown_ = faceMode;
    Serial.printf("[mode] %s\n", fido::modeName(t.mode));
    showMode(faceMode, task);
  }

  // The only place motor outputs are written. t.output is already zero unless every rule allows it.
  motors_.drive(t.output);

  bool moving = t.output.left != 0 || t.output.right != 0;
  if (moving != wasMoving_) {
    wasMoving_ = moving;
    const char* reason = !moving && t.why == fido::StopReason::None
                             ? t.inhibition : fido::stopReasonName(t.why);
    Serial.printf("[motor] %s (%s), range %.1f cm fresh=%d\n",
                  moving ? "MOVING" : "stopped", reason, rangeCm, rangeFresh);
  }
  // With ENABLE_MOTION=0 the gate's last rule fires: say once what would have happened.
  bool would = t.why == fido::StopReason::NotCompiled && (t.intent.left != 0 || t.intent.right != 0);
  if (would != wouldMove_) {
    wouldMove_ = would;
    if (would) Serial.printf("[motor] (ENABLE_MOTION=0) would be moving in mode %s\n", fido::modeName(t.mode));
  }
}
