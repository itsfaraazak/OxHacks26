#pragma once
#include <stdint.h>
#include "motor_safety.h"

namespace fido {
// Forward-facing sensor only. No edge detection and no protection behind the dog.
class ObstacleGate {
 public:
  MotorCommand apply(Mode mode, MotorCommand intent, bool fresh, float cm) {
    if (!fresh || !(cm > 0)) return {};
    if (mode != Mode::Run) {
      turning_ = false;
      if (cm < 20 && (intent.left > 0 || intent.right > 0)) return {};
      return intent;
    }
    if (cm < 20) turning_ = true;
    if (cm >= 25) turning_ = false;
    // Calibrated right turn: prototype logical left reverse, right forward.
    return turning_ ? MotorCommand{-1, 1} : intent;
  }
 private:
  bool turning_ = false;
};
}  // namespace fido
