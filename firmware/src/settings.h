// Pulls in config.h (yours) or config.example.h (placeholders), fills in optional defaults,
// and refuses to build with unsafe values.
#pragma once

// The real file is NOT called config.h: the ESP32-A2DP library has its own "config.h", and a
// project config.h on the include path would shadow it and break that library's build.
#if __has_include("fido_config.h")
#include "fido_config.h"
#else
#include "config.example.h"
#warning "include/fido_config.h not found: using config.example.h placeholders. Copy it to fido_config.h."
#endif

#include <stdint.h>

#include "sdkconfig.h"

// Wi-Fi and Bluetooth share one radio: the SDK's software coexistence arbiter must be on.
// The prebuilt Arduino-ESP32 SDK has it on; this stops the build if that ever changes.
#if !defined(CONFIG_SW_COEXIST_ENABLE)
#error "Wi-Fi/Bluetooth software coexistence is disabled in this SDK build"
#endif

#ifndef ENABLE_MOTION
#define ENABLE_MOTION 0
#endif
#ifndef MAX_ESCAPE_MS
#define MAX_ESCAPE_MS 8000
#endif
#ifndef MAX_SPEED_PERCENT
#define MAX_SPEED_PERCENT 35
#endif
#ifndef WIGGLE_SPEED_PERCENT
#define WIGGLE_SPEED_PERCENT 25
#endif
#ifndef MOTOR_STBY_PIN
#define MOTOR_STBY_PIN -1
#endif
#ifndef MOTOR_LEFT_INVERT
#define MOTOR_LEFT_INVERT 0
#endif
#ifndef MOTOR_RIGHT_INVERT
#define MOTOR_RIGHT_INVERT 0
#endif
#ifndef BATTERY_ADC_PIN
#define BATTERY_ADC_PIN -1
#endif
#ifndef BATTERY_DIVIDER_RATIO
#define BATTERY_DIVIDER_RATIO 2.0f
#endif
#ifndef BATTERY_EMPTY_MV
#define BATTERY_EMPTY_MV 3300
#endif
#ifndef BATTERY_FULL_MV
#define BATTERY_FULL_MV 4150
#endif
#ifndef AUDIO_VOLUME
#define AUDIO_VOLUME 90
#endif
#ifndef DEVICE_TOKEN
#define DEVICE_TOKEN ""
#endif

#ifndef WIFI_POWER_SAVE
#define WIFI_POWER_SAVE 1  // modem sleep: recommended while A2DP streams
#endif

static_assert(ENABLE_MOTION == 0 || ENABLE_MOTION == 1, "ENABLE_MOTION must be 0 or 1");
static_assert(MAX_SPEED_PERCENT >= 1 && MAX_SPEED_PERCENT <= 60, "MAX_SPEED_PERCENT must be 1..60 (slow on purpose)");
static_assert(WIGGLE_SPEED_PERCENT >= 1 && WIGGLE_SPEED_PERCENT <= MAX_SPEED_PERCENT,
              "WIGGLE_SPEED_PERCENT must be 1..MAX_SPEED_PERCENT");
static_assert(MAX_ESCAPE_MS >= 500 && MAX_ESCAPE_MS <= 60000, "MAX_ESCAPE_MS must be 500..60000");

// Timing that is part of the safety story, kept in one place.
constexpr uint32_t kHeartbeatPeriodMs = 1000;  // backend poll interval
constexpr uint32_t kWatchdogMs = 3000;         // no good reply for this long => motors stop
constexpr uint8_t kErrorStreakLimit = 2;       // consecutive failed requests => motors stop
constexpr uint32_t kHttpConnectTimeoutMs = 800;
constexpr uint32_t kHttpTimeoutMs = 1200;
