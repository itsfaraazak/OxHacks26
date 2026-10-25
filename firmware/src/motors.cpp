#include "motors.h"

#include <Arduino.h>

#include "settings.h"

namespace {
constexpr int kLeftChannel = 0, kRightChannel = 2;
constexpr uint32_t kPwmHz = 5000;
constexpr uint8_t kPwmBits = 8;

void setPin(int pin, int level) {
  if (pin >= 0) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, level);
  }
}
}  // namespace

void Motors::begin() {
  // Stopped state first, before anything else can fail: direction pins low, standby asserted.
  setPin(MOTOR_LEFT_IN1_PIN, LOW);
  setPin(MOTOR_LEFT_IN2_PIN, LOW);
  setPin(MOTOR_RIGHT_IN1_PIN, LOW);
  setPin(MOTOR_RIGHT_IN2_PIN, LOW);
  setPin(MOTOR_STBY_PIN, LOW);  // TB6612: LOW = standby (outputs off)
#if ENABLE_MOTION
  const int pins[] = {MOTOR_LEFT_IN1_PIN, MOTOR_LEFT_IN2_PIN,
                      MOTOR_RIGHT_IN1_PIN, MOTOR_RIGHT_IN2_PIN};
  for (int i = 0; i < 4; ++i) {
    ledcSetup(i, kPwmHz, kPwmBits);
    ledcAttachPin(pins[i], i);
    ledcWrite(i, 0);
  }
#endif
}

void Motors::setSide(int pwmChannel, bool invert, float speed) {
  if (invert) speed = -speed;
  uint32_t duty = static_cast<uint32_t>((speed < 0 ? -speed : speed) * 255.0f + 0.5f);
  if (duty > 255) duty = 255;
  // MX1508: PWM on the active input, opposite input LOW.
  ledcWrite(pwmChannel, speed > 0 ? duty : 0);
  ledcWrite(pwmChannel + 1, speed < 0 ? duty : 0);
}

void Motors::stop() {
#if ENABLE_MOTION
  for (int i = 0; i < 4; ++i) ledcWrite(i, 0);
  setPin(MOTOR_STBY_PIN, LOW);
#endif
  moving_ = false;
  lastLeft_ = lastRight_ = 0;
}

void Motors::drive(const fido::MotorCommand& cmd) {
#if ENABLE_MOTION
  if (cmd.left == 0 && cmd.right == 0) {
    if (moving_) stop();
    return;
  }
  setPin(MOTOR_STBY_PIN, HIGH);
  if (cmd.left * lastLeft_ < 0 || cmd.right * lastRight_ < 0) {
    stop();
    reverseAt_ = micros();
    reversing_ = true;
    return;
  }
  if (reversing_ && micros() - reverseAt_ < 2000) return;
  reversing_ = false;
  setSide(kLeftChannel, MOTOR_LEFT_INVERT, cmd.left);
  setSide(kRightChannel, MOTOR_RIGHT_INVERT, cmd.right);
  moving_ = true;
  lastLeft_ = cmd.left;
  lastRight_ = cmd.right;
#else
  (void)cmd;  // motion compiled out: nothing can drive the pins
#endif
}
