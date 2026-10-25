// Fido firmware configuration.
//
// Optional hardware overrides: copy to include/fido_config.h.
//
// Wi-Fi/server/token/speaker are provisioned through Fido-Setup and saved on the board.
// Do not put credentials in source files.
// (It is not called config.h because the ESP32-A2DP library has its own config.h, which a project
// file of that name would shadow.) If fido_config.h is missing, the build falls back to THIS file
// as the defaults.
//
// MX1508 / SSD1306 / HC-SR04 / button wiring was checked with esp_component_test.
#pragma once

// Initial token fallback only; leave empty and provision at runtime if needed.
#define DEVICE_TOKEN ""

// ---- Bluetooth speaker (the robot's voice) ------------------------------------------------
// Name as it appears in your phone's Bluetooth list. Matched as a prefix. Put the speaker in
// pairing mode the first time; afterwards the ESP32 reconnects to the last address on its own.
// "" disables Bluetooth audio entirely (the heartbeat then reports speaker "none").
#define SPEAKER_NAME "Acoustic Solutions WJ-201"
#define AUDIO_VOLUME 127  // Maximum A2DP absolute volume requested from the speaker

// ---- Motion: OFF unless you opt in --------------------------------------------------------
// 0 = the motors can never be driven; the firmware only logs what it would have done.
// 1 = motion allowed, still gated by the backend, watchdog and limits below.
// Read firmware/README.md ("Enabling motion") before setting this to 1.
#ifndef ENABLE_MOTION
#define ENABLE_MOTION 0
#endif

// Hard local cap even if the backend keeps saying run; matches the 10-minute stop timeout.
#define MAX_ESCAPE_MS 600000

// Default simulation/bench caps; the explicit motion target overrides these.
#ifndef MAX_SPEED_PERCENT
#define MAX_SPEED_PERCENT 35
#endif
// Cap for the in-place `nudge` wiggle (never above MAX_SPEED_PERCENT).
#ifndef WIGGLE_SPEED_PERCENT
#define WIGGLE_SPEED_PERCENT 25
#endif

// ---- MX1508: two PWM-capable inputs per wheel; no separate enable pins -------------------
#define MOTOR_LEFT_IN1_PIN 25
#define MOTOR_LEFT_IN2_PIN 26
#define MOTOR_RIGHT_IN1_PIN 27
#define MOTOR_RIGHT_IN2_PIN 14
#define MOTOR_STBY_PIN -1

// Flip a side if "forward" turns that wheel backwards. Check this on the bench, wheels off the ground.
#define MOTOR_LEFT_INVERT 1
#define MOTOR_RIGHT_INVERT 1

// ---- Battery (PLACEHOLDER: no sensing is confirmed) ---------------------------------------
// ADC1 pin (GPIO32-39; ADC2 does not work while Wi-Fi is on) behind a voltage divider, or -1
// to not report a battery level (the key is then left out of the heartbeat).
#define BATTERY_ADC_PIN -1
#define BATTERY_DIVIDER_RATIO 2.0f  // battery volts = pin volts * ratio
#define BATTERY_EMPTY_MV 3300       // 0 % (single Li-ion cell)
#define BATTERY_FULL_MV 4150        // 100 %
