// Motor safety gate. Pure logic (no Arduino calls) so it can be unit-tested on a laptop.
//
// Every motor command goes through MotorSafety::gate(); nothing else may drive the motors.
// The output is zero unless ALL of these hold:
//   * the software e-stop is not latched
//   * a successful backend reply has arrived, and the latest one is younger than watchdog_ms
//   * the HTTP error streak is below error_streak_limit
//   * the latest reply says motion_enabled == true
//   * the mode is run, nudge or bark (bounded planner pulses), and for `run`
//     it has lasted less than max_escape_ms
//   * motion was compiled in (ENABLE_MOTION=1). Evaluated last so that, with motion compiled
//     out, StopReason::NotCompiled means "every other rule would have allowed movement".
#pragma once

#include <stdint.h>

#include "mode.h"

namespace fido {

struct SafetyConfig {
  bool motion_compiled = false;        // ENABLE_MOTION; false means gate() always returns 0
  uint32_t watchdog_ms = 5000;         // no successful reply for this long => stop
  uint32_t max_escape_ms = 8000;       // `run` is cut off this long after it started
  uint8_t error_streak_limit = 0;      // 0 disables count-based stopping; watchdog still applies
  float max_speed = 0.35f;             // slow speed cap in `run`, fraction of full PWM
  float wiggle_speed = 0.25f;          // cap in `nudge` (in-place wiggle), <= max_speed
};

// Wheel intent or command in [-1, 1] (forward positive). Planner output is an intent;
// the safety gate scales it by the mode's cap.
struct MotorCommand {
  float left = 0;
  float right = 0;
};

enum class StopReason : uint8_t {
  None,  // motors are allowed to move
  Estop,
  NoReply,   // no successful reply since boot
  Watchdog,  // last successful reply too old
  HttpErrors,
  MotionDisabled,
  ModeStill,       // mode is not run/nudge/bark
  EscapeTimeout,   // run lasted max_escape_ms
  NotCompiled,     // ENABLE_MOTION=0; output is zero (see above)
};

const char* stopReasonName(StopReason reason);

class MotorSafety {
 public:
  explicit MotorSafety(const SafetyConfig& config) : cfg_(config) {}

  // A successful, parsed backend reply.
  void onReply(uint32_t now_ms, Mode mode, bool motion_enabled);
  // A failed request (timeout, Wi-Fi down, HTTP != 2xx, unparseable body).
  void onHttpError(uint32_t now_ms);
  // Latched software e-stop (serial console), released only by setEstop(false).
  void setEstop(bool on) { estop_ = on; }
  bool estop() const { return estop_; }

  // Scale `intent` to the allowed speed, or return zeros. Call every control tick.
  MotorCommand gate(uint32_t now_ms, MotorCommand intent, StopReason* why = nullptr) const;

  Mode mode() const { return mode_; }
  uint32_t msInMode(uint32_t now_ms) const { return now_ms - mode_since_; }

 private:
  SafetyConfig cfg_;
  bool have_reply_ = false;
  uint32_t last_ok_ms_ = 0;
  uint8_t error_streak_ = 0;
  bool motion_enabled_ = false;
  Mode mode_ = Mode::Unknown;
  uint32_t mode_since_ = 0;
  bool estop_ = false;
};

}  // namespace fido
