// Fido firmware configuration.
//
//   cp include/config.example.h include/fido_config.h     # then edit fido_config.h
//
// include/fido_config.h is gitignored: it holds your Wi-Fi password and device token. Never commit it.
// (It is not called config.h because the ESP32-A2DP library has its own config.h, which a project
// file of that name would shadow.) If fido_config.h is missing, the build falls back to THIS file
// (placeholders) with a warning.
//
// Everything marked "PLACEHOLDER" is a guess: the motor driver, OLED and NFC models are not
// confirmed. Check each pin against your actual wiring before you power the motors.
#pragma once

// ---- Wi-Fi + backend ----------------------------------------------------------------------
#define WIFI_SSID "your-wifi-name"          // 2.4 GHz only: the ESP32 has no 5 GHz radio
#define WIFI_PASSWORD "your-wifi-password"

// Where the Flask backend runs (the laptop's LAN IP; `python -m fido` listens on 0.0.0.0).
// https:// works too, but the TLS handshake is slow while Bluetooth is streaming.
#define BACKEND_BASE_URL "http://192.168.1.50:5000"

// Same value as FIDO_DEVICE_TOKEN on the backend. Leave "" if the backend has no token.
#define DEVICE_TOKEN ""

// ---- Bluetooth speaker (the robot's voice) ------------------------------------------------
// Name as it appears in your phone's Bluetooth list. Matched as a prefix. Put the speaker in
// pairing mode the first time; afterwards the ESP32 reconnects to the last address on its own.
// "" disables Bluetooth audio entirely (the heartbeat then reports speaker "none").
#define SPEAKER_NAME "My Bluetooth Speaker"
#define AUDIO_VOLUME 90  // 0..127, A2DP absolute volume sent to the speaker

// ---- Motion: OFF unless you opt in --------------------------------------------------------
// 0 = the motors can never be driven; the firmware only logs what it would have done.
// 1 = motion allowed, still gated by the backend, watchdog and limits below.
// Read firmware/README.md ("Enabling motion") before setting this to 1.
#define ENABLE_MOTION 0

// A `run` (escape) is cut off this long after it starts, even if the backend still says run.
#define MAX_ESCAPE_MS 8000

// Slow speed cap, percent of full PWM. The build refuses values above 60.
#define MAX_SPEED_PERCENT 35
// Cap for the in-place `nudge` wiggle (never above MAX_SPEED_PERCENT).
#define WIGGLE_SPEED_PERCENT 25

// ---- Motor driver pins (PLACEHOLDER: L298N / TB6612-style PWM + two direction pins) -------
// Chosen from GPIOs that are safe to drive LOW at boot (not strapping or flash pins).
// L298N: PWM = ENA/ENB, IN1/IN2 = IN1/IN2 (left), IN3/IN4 (right).
// TB6612: PWM = PWMA/PWMB, IN1/IN2 = AIN1/AIN2, BIN1/BIN2; wire STBY high (or set MOTOR_STBY_PIN).
#define MOTOR_LEFT_PWM_PIN 25
#define MOTOR_LEFT_IN1_PIN 26
#define MOTOR_LEFT_IN2_PIN 27
#define MOTOR_RIGHT_PWM_PIN 33
#define MOTOR_RIGHT_IN1_PIN 32
#define MOTOR_RIGHT_IN2_PIN 13
#define MOTOR_STBY_PIN -1  // TB6612 standby pin, -1 if unused

// Flip a side if "forward" turns that wheel backwards. Check this on the bench, wheels off the ground.
#define MOTOR_LEFT_INVERT 0
#define MOTOR_RIGHT_INVERT 0

// ---- Battery (PLACEHOLDER: no sensing is confirmed) ---------------------------------------
// ADC1 pin (GPIO32-39; ADC2 does not work while Wi-Fi is on) behind a voltage divider, or -1
// to not report a battery level (the key is then left out of the heartbeat).
#define BATTERY_ADC_PIN -1
#define BATTERY_DIVIDER_RATIO 2.0f  // battery volts = pin volts * ratio
#define BATTERY_EMPTY_MV 3300       // 0 % (single Li-ion cell)
#define BATTERY_FULL_MV 4150        // 100 %
