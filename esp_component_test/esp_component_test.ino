#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_SSD1306.h>

constexpr int LEFT_IN1 = 25, LEFT_IN2 = 26;
constexpr int RIGHT_IN3 = 27, RIGHT_IN4 = 14;
constexpr int BUTTON = 35, TRIG = 33, ECHO = 34;
constexpr int SDA_PIN = 13, SCL_PIN = 32, SERVO = 12;
constexpr int MOTOR_SPEED = 255; // Full power, matching the original prototype.
constexpr unsigned long MOTOR_LIMIT_MS = 3000;
constexpr int SERVO_CHANNEL = 4;
constexpr uint8_t OLED_ADDRESS = 0x3C;

// Preserve the original prototype's calibrated polarity and turn commands.
constexpr bool INVERT_LEFT = true, INVERT_RIGHT = true;
enum Mode { IDLE, FORWARD, REVERSE, LEFT, RIGHT, DISTANCE, OLED,
            SERVO_30, SERVO_90, SERVO_150, MODE_COUNT };
const char *const modeNames[] = {
    "Idle", "Both motors forward", "Both motors reverse",
    "Left turn (prototype calibration)", "Right turn (prototype calibration)",
    "HC-SR04 distance", "OLED scrolling Hello world",
    "Servo 30 degrees", "Servo 90 degrees", "Servo 150 degrees"
};
Mode mode = IDLE;
unsigned long modeSince = 0;
bool motorsRunning = false;
bool servoAttached = false;
Adafruit_SSD1306 display(128, 64, &Wire, -1);
bool oledReady = false;
int textX = 128;
unsigned long lastFrame = 0;

// Ultrasonic edge sampling: no pulseIn() blocking the button.
enum EchoState { QUIET, WAIT_RISE, WAIT_FALL };
EchoState echoState = QUIET;
unsigned long lastPing = 0, pingStarted = 0, echoRose = 0;

void stopMotors() {
    for (int channel = 0; channel < 4; ++channel) ledcWrite(channel, 0);
    motorsRunning = false;
}

void drive(bool leftForward, bool rightForward) {
    if (INVERT_LEFT) leftForward = !leftForward;
    if (INVERT_RIGHT) rightForward = !rightForward;
    ledcWrite(0, leftForward ? MOTOR_SPEED : 0);
    ledcWrite(1, leftForward ? 0 : MOTOR_SPEED);
    ledcWrite(2, rightForward ? MOTOR_SPEED : 0);
    ledcWrite(3, rightForward ? 0 : MOTOR_SPEED);
    motorsRunning = true;
}

void setServo(int degrees) {
    if (!servoAttached) {
        ledcAttachPin(SERVO, SERVO_CHANNEL);
        servoAttached = true;
    }
    // Conservative 1000..2000 us pulse range, 20 ms period.
    unsigned long pulseUs = map(degrees, 0, 180, 1000, 2000);
    ledcWrite(SERVO_CHANNEL, pulseUs * 65535UL / 20000UL);
}

void selectMode(Mode next) {
    stopMotors();
    delay(10); // All inputs off before changing motor direction.
    if (servoAttached) {
        ledcDetachPin(SERVO);
        digitalWrite(SERVO, LOW);
        servoAttached = false;
    }
    digitalWrite(TRIG, LOW);
    echoState = QUIET;
    if (oledReady) {
        display.clearDisplay();
        display.display();
    }
    mode = next;
    modeSince = millis();
    Serial.printf("Mode: %s\n", modeNames[mode]);
    switch (mode) {
        case FORWARD: drive(true, true); break;
        case REVERSE: drive(false, false); break;
        case LEFT: drive(true, false); break;
        case RIGHT: drive(false, true); break;
        case DISTANCE: lastPing = millis() - 150; break;
        case OLED:
            textX = 128;
            if (!oledReady) Serial.println("OLED unavailable; check wiring/address.");
            break;
        case SERVO_30: setServo(30); break;
        case SERVO_90: setServo(90); break;
        case SERVO_150: setServo(150); break;
        default: break;
    }
}

void checkButton() {
    static bool lastReading = HIGH, stable = HIGH;
    static unsigned long changedAt = 0;
    bool reading = digitalRead(BUTTON);
    if (reading != lastReading) {
        lastReading = reading;
        changedAt = millis();
    }
    if (millis() - changedAt >= 30 && reading != stable) {
        stable = reading;
        if (stable == LOW) {
            Serial.println("Button clicked");
            selectMode(static_cast<Mode>((mode + 1) % MODE_COUNT));
        }
    }
}

void readDistance() {
    unsigned long now = micros();
    if (echoState == QUIET) {
        if (millis() - lastPing < 150) return;
        lastPing = millis();
        if (digitalRead(ECHO) == HIGH) {
            Serial.println("Distance: Echo stuck HIGH; check wiring");
            return;
        }
        digitalWrite(TRIG, HIGH);
        delayMicroseconds(12);
        digitalWrite(TRIG, LOW);
        pingStarted = micros();
        echoState = WAIT_RISE;
    } else if (now - pingStarted >= 30000) {
        Serial.println("Distance: no echo / out of range");
        echoState = QUIET;
    } else if (echoState == WAIT_RISE && digitalRead(ECHO) == HIGH) {
        echoRose = now;
        echoState = WAIT_FALL;
    } else if (echoState == WAIT_FALL && digitalRead(ECHO) == LOW) {
        Serial.printf("Distance: %.1f cm\n", (now - echoRose) * 0.0343f / 2);
        echoState = QUIET;
    }
}

void setup() {
    Serial.begin(115200);
    const int pins[] = {LEFT_IN1, LEFT_IN2, RIGHT_IN3, RIGHT_IN4};
    for (int i = 0; i < 4; ++i) {
        digitalWrite(pins[i], LOW);
        pinMode(pins[i], OUTPUT);
        ledcSetup(i, 15000, 8);
        ledcAttachPin(pins[i], i);
    }
    stopMotors();
    pinMode(BUTTON, INPUT); // GPIO35 needs the external pull-up to 3.3V.
    digitalWrite(TRIG, LOW);
    pinMode(TRIG, OUTPUT);
    pinMode(ECHO, INPUT); // Echo MUST pass through its voltage divider.
    digitalWrite(SERVO, LOW);
    pinMode(SERVO, OUTPUT);
    ledcSetup(SERVO_CHANNEL, 50, 16);
    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setTimeOut(25);
    Wire.beginTransmission(OLED_ADDRESS);
    bool oledPresent = Wire.endTransmission() == 0;
    oledReady = oledPresent && display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS);
    if (oledReady) {
        display.clearDisplay();
        display.setTextSize(2);
        display.setTextColor(SSD1306_WHITE);
        display.setTextWrap(false);
        display.display();
    }
    Serial.println("Component test ready. Lift wheels; detach servo linkage.");
    Serial.println(oledReady ? "OLED found at 0x3C" : "OLED not found at 0x3C");
    selectMode(IDLE);
}

void loop() {
    checkButton();
    if (motorsRunning && millis() - modeSince >= MOTOR_LIMIT_MS) {
        stopMotors();
        Serial.println("Motor safety timeout: stopped. Click for next mode.");
    }
    if (mode == DISTANCE) readDistance();
    if (mode == OLED && oledReady && millis() - lastFrame >= 40) {
        lastFrame = millis();
        display.clearDisplay();
        display.setCursor(textX, 24);
        display.print("Hello world");
        display.display();
        if (--textX < -132) textX = 128;
    }
}
