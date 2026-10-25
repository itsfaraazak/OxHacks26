#include "hardware.h"

namespace hardware {
namespace {
constexpr int kButton = 35, kTrig = 33, kEcho = 34, kServo = 12;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
volatile uint32_t riseUs = 0, pulseUs = 0;
volatile bool waiting = false, risen = false, complete = false;
uint32_t pingMs = 0, readingMs = 0;
float cm = 0;
bool valid = false;
void IRAM_ATTR echoEdge() {
  uint32_t now = micros();
  portENTER_CRITICAL_ISR(&mux);
  if (waiting) {
    if (digitalRead(kEcho)) {
      riseUs = now;
      risen = true;
    } else if (risen) {
      pulseUs = now - riseUs;
      complete = true;
      waiting = false;
    }
  }
  portEXIT_CRITICAL_ISR(&mux);
}
}

void begin() {
  pinMode(kButton, INPUT); // External 3.3V pull-up; GPIO35 has none internally.
  digitalWrite(kTrig, LOW);
  pinMode(kTrig, OUTPUT);
  pinMode(kEcho, INPUT); // Mandatory Echo voltage divider.
  attachInterrupt(digitalPinToInterrupt(kEcho), echoEdge, CHANGE);
  digitalWrite(kServo, LOW);
  pinMode(kServo, OUTPUT);
  // Channels 0..3 are motors; channel 4 uses a different PWM timer.
  ledcSetup(4, 50, 16);
}

bool buttonPressed() {
  static bool last = HIGH, stable = HIGH;
  static uint32_t changed = 0;
  bool reading = digitalRead(kButton);
  if (reading != last) { last = reading; changed = millis(); }
  if (millis() - changed < 30 || stable == reading) return false;
  stable = reading;
  return stable == LOW;
}

void tickRange() {
  uint32_t now = millis();
  portENTER_CRITICAL(&mux);
  bool done = complete;
  uint32_t pulse = pulseUs;
  complete = false;
  bool timeout = waiting && now - pingMs >= 35;
  if (timeout) waiting = false;
  portEXIT_CRITICAL(&mux);
  // Retain a valid echo through isolated dropouts; expiry below still stops motion.
  if (done) {
    float measured = pulse * 0.0343f / 2;
    if (measured >= 2 && measured <= 400) {
      cm = measured;
      valid = true;
      readingMs = now;
    }
  }
  if (now - pingMs < 80) return;
  pingMs = now;
  portENTER_CRITICAL(&mux);
  waiting = digitalRead(kEcho) == LOW;
  risen = false;
  portEXIT_CRITICAL(&mux);
  digitalWrite(kTrig, HIGH);
  delayMicroseconds(12);
  digitalWrite(kTrig, LOW);
}
bool rangeFresh() { return valid && millis() - readingMs < 250; }
float rangeCm() { return cm; }
uint32_t rangeSampleMs() { return readingMs; }

void centreServo() {
  ledcAttachPin(kServo, 4);
  ledcWrite(4, 1500UL * 65535UL / 20000UL);
}

void tickServo(fido::Mode mode, bool enabled) {
  static uint32_t lastTick = 0;
  static int position = 90;
  uint32_t now = millis();
  if (now - lastTick < 20) return;
  lastTick = now;
  int amplitude = 0, period = 1100;
  if (enabled) {
    switch (mode) {
      case fido::Mode::Sleep:
        amplitude = now % 7000 < 4200 ? 28 : 0; period = 1000; break;
      case fido::Mode::BeckonDock: amplitude = 35; period = 900; break;
      case fido::Mode::Watch:
        amplitude = now % 8000 < 2200 ? 18 : 0;
        break;
      case fido::Mode::Nudge: amplitude = 35; period = 700; break;
      case fido::Mode::Bark: amplitude = 35; period = 600; break;
      case fido::Mode::Run: amplitude = 30; period = 800; break;
      case fido::Mode::Celebrate: amplitude = 45; period = 700; break;
      default: break;
    }
  }
  int phase = now % period;
  int triangle = phase < period / 2 ? phase * 200 / period - 50 :
                                                150 - phase * 200 / period;
  int target = 90 + triangle * amplitude / 50;
  // Three degrees per 20ms; still bounded to 45..135 degrees.
  position += constrain(target - position, -3, 3);
  unsigned long pulse = map(position, 0, 180, 1000, 2000);
  ledcWrite(4, pulse * 65535UL / 20000UL);
}
}
