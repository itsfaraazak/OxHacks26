#include <Arduino.h>

// ESP32 DOIT DevKit V1 -> motor driver inputs.
// Share GND with the driver; power motors separately, not from ESP32 pins.
constexpr int LEFT_IN1 = 25;
constexpr int LEFT_IN2 = 26;
constexpr int RIGHT_IN3 = 27;
constexpr int RIGHT_IN4 = 14;
constexpr int LED_PIN = 2;

void setup() {
    const int motorPins[] = {LEFT_IN1, LEFT_IN2, RIGHT_IN3, RIGHT_IN4};
    for (int pin : motorPins) {
        digitalWrite(pin, LOW);
        pinMode(pin, OUTPUT);
    }
    pinMode(LED_PIN, OUTPUT);
    Serial.begin(115200);
    Serial.println("ESP32 ready. Motor inputs held LOW.");
}

void loop() {
    digitalWrite(LED_PIN, HIGH);
    delay(500);
    digitalWrite(LED_PIN, LOW);
    delay(500);
}
