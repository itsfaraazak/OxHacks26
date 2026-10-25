// Maps the backend's mode to behaviour: what the display shows and, via SharedSafety, what the
// wheels do. The controller never touches motor pins directly; every command goes through the
// safety gate, then Motors.
#pragma once

#include "display.h"
#include "motors.h"
#include "shared_safety.h"

class ModeController {
 public:
  ModeController(SharedSafety& safety, Motors& motors, Display& display)
      : safety_(safety), motors_(motors), display_(display) {}

  // Call every ~10 ms. `task` is the current task text from the backend (may be "").
  void update(uint32_t now_ms, const char* task, bool rangeFresh, float rangeCm, uint32_t sampleMs);
  const char* lastStopReason() const { return fido::stopReasonName(why_); }

 private:
  void showMode(fido::Mode mode, const char* task);

  SharedSafety& safety_;
  Motors& motors_;
  Display& display_;
  fido::Mode shown_ = fido::Mode::Unknown;
  bool haveShown_ = false;
  fido::StopReason why_ = fido::StopReason::NoReply;
  bool wouldMove_ = false;
  bool wasMoving_ = false;
};
