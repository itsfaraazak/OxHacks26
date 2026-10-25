// Thread-safe wrapper around MotorSafety. The backend task reports replies and errors; the
// control loop (a different task, on the other core) asks for the gated motor command.
// The critical sections are a few arithmetic operations, so a spinlock is enough, and the
// control loop never waits on network I/O: if the backend task hangs, time still passes and
// the watchdog rule in MotorSafety::gate() stops the motors on its own.
#pragma once

#include <Arduino.h>

#include "motion_planner.h"
#include "motor_safety.h"
#include "obstacle.h"

class SharedSafety {
 public:
  explicit SharedSafety(const fido::SafetyConfig& cfg) : safety_(cfg) {}

  void onReply(uint32_t now_ms, fido::Mode mode, bool motion_enabled) {
    portENTER_CRITICAL(&mux_);
    safety_.onReply(now_ms, mode, motion_enabled);
    portEXIT_CRITICAL(&mux_);
  }
  void onHttpError(uint32_t now_ms) {
    portENTER_CRITICAL(&mux_);
    safety_.onHttpError(now_ms);
    portEXIT_CRITICAL(&mux_);
  }
  void setEstop(bool on) {
    portENTER_CRITICAL(&mux_);
    safety_.setEstop(on);
    portEXIT_CRITICAL(&mux_);
  }
  bool estop() {
    portENTER_CRITICAL(&mux_);
    bool v = safety_.estop();
    portEXIT_CRITICAL(&mux_);
    return v;
  }

  struct Tick {
    fido::Mode mode;
    uint32_t ms_in_mode;
    fido::MotorCommand intent;  // what the mode wants
    fido::MotorCommand output;  // what the gate allows (zeros when stopped)
    fido::StopReason why;
  };

  Tick tick(uint32_t now_ms, bool rangeFresh = false, float rangeCm = 0, uint32_t sampleMs = 0) {
    Tick t;
    portENTER_CRITICAL(&mux_);
    t.mode = safety_.mode();
    t.ms_in_mode = safety_.msInMode(now_ms);
    t.intent = fido::planMotion(t.mode, t.ms_in_mode);
    t.intent = obstacle_.apply(t.mode, t.intent, rangeFresh, rangeCm, sampleMs, now_ms);
    t.output = safety_.gate(now_ms, t.intent, &t.why);
    portEXIT_CRITICAL(&mux_);
    return t;
  }

 private:
  portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  fido::MotorSafety safety_;
  fido::ObstacleGate obstacle_;
};
