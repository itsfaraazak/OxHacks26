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
  if (timeout) valid = false;
  if (done) {
    cm = pulse * 0.0343f / 2;
    valid = cm >= 2 && cm <= 400;
    readingMs = now;
  }
  if (now - pingMs < 80) return;
  pingMs = now;
  // A new measurement invalidates the previous one on timeout rather than
  // treating "no echo" as clear space.
  valid = done ? valid : (now - readingMs < 150 && valid);
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

void centreServo() {
  ledcAttachPin(kServo, 4);
  ledcWrite(4, 1500UL * 65535UL / 20000UL);
}
}
