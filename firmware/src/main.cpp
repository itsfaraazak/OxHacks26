// Fido robot firmware (ESP32-DevKit-V1 / ESP32-WROOM-32).
//
//   core 0: backend task  - Wi-Fi + HTTP heartbeat (backend_client.cpp), next to the radio stack
//   core 1: control loop  - this file: NFC/console, mode behaviour, motor gate (10 ms tick)
//           A2DP app task - Bluetooth audio, data callback decodes clips (audio.cpp)
//
// Motor rule: the ONLY path from a backend reply to a motor pin is
//   BackendClient -> SharedSafety (MotorSafety::gate) -> ModeController -> Motors.
// See lib/fido_core/src/motor_safety.h for the rules; they have host-side unit tests.
#include <Arduino.h>
#include <esp_task_wdt.h>

#include "audio.h"
#include "backend_client.h"
#include "console.h"
#include "display.h"
#include "mode_controller.h"
#include "motors.h"
#include "nfc.h"
#include "settings.h"
#include "shared_safety.h"

namespace {

fido::SafetyConfig makeSafetyConfig() {
  fido::SafetyConfig c;
  c.motion_compiled = ENABLE_MOTION != 0;
  c.watchdog_ms = kWatchdogMs;
  c.max_escape_ms = MAX_ESCAPE_MS;
  c.error_streak_limit = kErrorStreakLimit;
  c.max_speed = MAX_SPEED_PERCENT / 100.0f;
  c.wiggle_speed = WIGGLE_SPEED_PERCENT / 100.0f;
  return c;
}

SharedSafety safety(makeSafetyConfig());
Motors motors;
SerialDisplay display;
SerialNfc nfc;  // swap for a real reader implementing Nfc
BackendClient backend(safety);
ModeController controller(safety, motors, display);
Console console(nfc, safety, backend);

uint32_t lastStatus = 0;

}  // namespace

void setup() {
  motors.begin();  // first: every motor pin to its stopped state
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== Fido firmware ===");
  Serial.printf("[boot] board esp32-devkit-v1, motion %s, max speed %d%%, max escape %d ms\n",
                ENABLE_MOTION ? "COMPILED IN (ENABLE_MOTION=1)" : "compiled out (ENABLE_MOTION=0)",
                MAX_SPEED_PERCENT, MAX_ESCAPE_MS);

  display.begin();
  display.show("Fido booting", "");
  nfc.begin();
  backend.begin();  // Wi-Fi first so it gets the radio before Bluetooth starts
  audio::begin();
  Console::help();

  // Hardware watchdog on the control task, armed after the slow Bluetooth start-up: if the loop ever
  // hangs the chip resets. After a reset the GPIOs are inputs, so fit pull-down resistors on the
  // motor driver's PWM/direction inputs (see README) so a reset also means "stopped".
  esp_task_wdt_init(5, true);
  esp_task_wdt_add(nullptr);
}

void loop() {
  esp_task_wdt_reset();
  uint32_t now = millis();

  console.poll();
  for (NfcEvent e = nfc.poll(); e != NfcEvent::None; e = nfc.poll()) backend.sendEvent(e);

  bool valid;
  fido::Command cmd = backend.latest(valid);
  controller.update(now, cmd.task);
  audio::tick(now);

  if (now - lastStatus >= 5000) {
    lastStatus = now;
    printStatus(backend, safety);
  }
  delay(10);
}
