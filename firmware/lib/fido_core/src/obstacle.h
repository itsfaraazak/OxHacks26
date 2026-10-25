#pragma once
#include <stdint.h>
#include "motor_safety.h"

namespace fido {
// Forward-facing sensor only. No edge detection and no protection behind the dog.
class ObstacleGate {
 public:
  MotorCommand apply(Mode mode, MotorCommand intent, bool fresh, float cm,
                     uint32_t sampleMs = 0, uint32_t nowMs = 0) {
    reason_ = "gesture-pause";
    if (mode != Mode::Run) {
      turning_ = false;
      blocked_ = false;
      clearCount_ = 0;
      if (!fresh || !(cm > 0)) { reason_ = "range-stale"; return {}; }
      if (cm < 35 && (intent.left > 0 || intent.right > 0)) {
        reason_ = "obstacle-close"; return {};
      }
      return intent;
    }
    if (turning_ && nowMs - turnSince_ >= 5000) blocked_ = true;
    if (blocked_) { reason_ = "turn-timeout"; return {}; }
    if (!fresh || !(cm > 0)) { reason_ = "range-stale"; return {}; }
    if (!turning_ && cm < 35) {
      turning_ = true;
      turnSince_ = nowMs;
      clearCount_ = 0;
      lastSample_ = sampleMs;
    }
    if (turning_ && sampleMs != lastSample_) {
      lastSample_ = sampleMs;
      clearCount_ = cm >= 45 ? clearCount_ + 1 : 0;
      if (clearCount_ >= 3) turning_ = false;
    }
    // Calibrated right turn: prototype logical left reverse, right forward.
    return turning_ ? MotorCommand{-1, 1} : intent;
  }
  const char* reason() const { return reason_; }
 private:
  const char* reason_ = "gesture-pause";
  bool turning_ = false;
  bool blocked_ = false;
  uint8_t clearCount_ = 0;
  uint32_t turnSince_ = 0, lastSample_ = 0;
};
}  // namespace fido
