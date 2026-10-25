#pragma once
#include "motor_safety.h"

namespace fido {
// Paired in-place gestures, never forward wandering. Timed from mode entry so
// transitions cancel the previous gesture. No motion in unknown/rest/celebrate.
inline MotorCommand presenceMotion(Mode mode, uint32_t elapsed) {
  uint32_t period;
  uint32_t start;
  uint32_t pulse;
  if (mode == Mode::Watch) {
    period = 25000; start = 20000; pulse = 60;
  } else if (mode == Mode::Sleep || mode == Mode::BeckonDock) {
    // Alternate 8 and 12 second cycles without a random dependency.
    uint32_t cycle = elapsed % 20000;
    elapsed = cycle < 8000 ? cycle : cycle - 8000;
    period = cycle < 8000 ? 8000 : 12000;
    start = period - 1000;
    pulse = 120;
  } else {
    return {};
  }
  uint32_t t = elapsed % period;
  if (t < start) return {};
  t -= start;
  if (t < pulse) return {1, -1};
  if (t < pulse + 80) return {};
  if (t < 2 * pulse + 80) return {-1, 1};
  return {};
}
}  // namespace fido
