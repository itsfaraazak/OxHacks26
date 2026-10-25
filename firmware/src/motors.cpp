#include "motors.h"

#include <Arduino.h>

#include "settings.h"

namespace {
constexpr int kLeftChannel = 0, kRightChannel = 1;
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
  setPin(MOTOR_LEFT_PWM_PIN, LOW);
  setPin(MOTOR_RIGHT_PWM_PIN, LOW);
  setPin(MOTOR_STBY_PIN, LOW);  // TB6612: LOW = standby (outputs off)
#if ENABLE_MOTION
  ledcSetup(kLeftChannel, kPwmHz, kPwmBits);
  ledcSetup(kRightChannel, kPwmHz, kPwmBits);
  ledcAttachPin(MOTOR_LEFT_PWM_PIN, kLeftChannel);
  ledcAttachPin(MOTOR_RIGHT_PWM_PIN, kRightChannel);
  ledcWrite(kLeftChannel, 0);
  ledcWrite(kRightChannel, 0);
#endif
}

void Motors::setSide(int pwmChannel, int in1, int in2, bool invert, float speed) {
  if (invert) speed = -speed;
  uint32_t duty = static_cast<uint32_t>((speed < 0 ? -speed : speed) * 255.0f + 0.5f);
  if (duty > 255) duty = 255;
  digitalWrite(in1, speed > 0 ? HIGH : LOW);
  digitalWrite(in2, speed < 0 ? HIGH : LOW);
  ledcWrite(pwmChannel, duty);
}

void Motors::stop() {
#if ENABLE_MOTION
  ledcWrite(kLeftChannel, 0);  // PWM off first, then direction pins
  ledcWrite(kRightChannel, 0);
  digitalWrite(MOTOR_LEFT_IN1_PIN, LOW);
  digitalWrite(MOTOR_LEFT_IN2_PIN, LOW);
  digitalWrite(MOTOR_RIGHT_IN1_PIN, LOW);
  digitalWrite(MOTOR_RIGHT_IN2_PIN, LOW);
  setPin(MOTOR_STBY_PIN, LOW);
#endif
  moving_ = false;
}

void Motors::drive(const fido::MotorCommand& cmd) {
#if ENABLE_MOTION
  if (cmd.left == 0 && cmd.right == 0) {
    if (moving_) stop();
    return;
  }
  setPin(MOTOR_STBY_PIN, HIGH);
  setSide(kLeftChannel, MOTOR_LEFT_IN1_PIN, MOTOR_LEFT_IN2_PIN, MOTOR_LEFT_INVERT, cmd.left);
  setSide(kRightChannel, MOTOR_RIGHT_IN1_PIN, MOTOR_RIGHT_IN2_PIN, MOTOR_RIGHT_INVERT, cmd.right);
  moving_ = true;
#else
  (void)cmd;  // motion compiled out: nothing can drive the pins
#endif
}
