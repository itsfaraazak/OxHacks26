#include "motor_safety.h"

namespace fido {

const char* stopReasonName(StopReason reason) {
  switch (reason) {
    case StopReason::None: return "moving-allowed";
    case StopReason::NotCompiled: return "motion-not-compiled";
    case StopReason::Estop: return "estop";
    case StopReason::NoReply: return "no-reply-yet";
    case StopReason::Watchdog: return "watchdog";
    case StopReason::HttpErrors: return "http-errors";
    case StopReason::MotionDisabled: return "motion-disabled";
    case StopReason::ModeStill: return "mode-still";
    case StopReason::EscapeTimeout: return "escape-timeout";
  }
  return "?";
}

void MotorSafety::onReply(uint32_t now_ms, Mode mode, bool motion_enabled) {
  have_reply_ = true;
  last_ok_ms_ = now_ms;
  error_streak_ = 0;
  motion_enabled_ = motion_enabled;
  if (mode != mode_) {
    mode_ = mode;
    mode_since_ = now_ms;
  }
}

void MotorSafety::onHttpError(uint32_t /*now_ms*/) {
  if (error_streak_ < 255) ++error_streak_;
}

namespace {
float clampUnit(float v) {
  if (!(v == v)) return 0;  // NaN
  if (v > 1) return 1;
  if (v < -1) return -1;
  return v;
}
}  // namespace

MotorCommand MotorSafety::gate(uint32_t now_ms, MotorCommand intent, StopReason* why) const {
  StopReason reason = StopReason::None;
  if (estop_) {
    reason = StopReason::Estop;
  } else if (!have_reply_) {
    reason = StopReason::NoReply;
  } else if (elapsedMs(now_ms, last_ok_ms_) >= cfg_.watchdog_ms) {
    reason = StopReason::Watchdog;
  } else if (cfg_.error_streak_limit > 0 && error_streak_ >= cfg_.error_streak_limit) {
    reason = StopReason::HttpErrors;
  } else if (!motion_enabled_) {
    reason = StopReason::MotionDisabled;
  } else if (mode_ != Mode::Run && mode_ != Mode::Nudge && mode_ != Mode::Bark &&
             mode_ != Mode::Sleep && mode_ != Mode::BeckonDock && mode_ != Mode::Watch) {
    reason = StopReason::ModeStill;
  } else if (mode_ == Mode::Run && msInMode(now_ms) >= cfg_.max_escape_ms) {
    reason = StopReason::EscapeTimeout;
  } else if (!cfg_.motion_compiled) {
    // Checked last on purpose: with ENABLE_MOTION=0 the motors never move, but the reason then
    // shows what the rules above would have allowed ("would have moved"), useful on the bench.
    reason = StopReason::NotCompiled;
  }
  if (why != nullptr) *why = reason;
  if (reason != StopReason::None) return MotorCommand{};

  float cap = mode_ == Mode::Run ? cfg_.max_speed : cfg_.wiggle_speed;
  if (cap > cfg_.max_speed) cap = cfg_.max_speed;
  if (cap < 0) cap = 0;
  return MotorCommand{clampUnit(intent.left) * cap, clampUnit(intent.right) * cap};
}

}  // namespace fido
