// The only code that writes motor pins. It takes already-gated commands from ModeController.
#pragma once

#include "motor_safety.h"

class Motors {
 public:
  // Always drives every motor pin to the stopped state first. With ENABLE_MOTION=0 that is all
  // it ever does: drive() then ignores every command.
  void begin();
  void drive(const fido::MotorCommand& cmd);  // wheel speeds in [-1, 1]
  void stop();
  bool moving() const { return moving_; }

 private:
  void setSide(int pwmChannel, bool invert, float speed);
  bool moving_ = false;
  float lastLeft_ = 0, lastRight_ = 0;
  uint32_t reverseAt_ = 0;
  bool reversing_ = false;
};
