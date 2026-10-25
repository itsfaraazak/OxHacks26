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
#include <WiFi.h>

#include "audio.h"
#include "backend_client.h"
#include "console.h"
#include "display.h"
#include "mode_controller.h"
#include "motors.h"
#include "nfc.h"
#include "settings.h"
#include "shared_safety.h"
#include "hardware.h"
#include "provisioning.h"

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
OledDisplay display;
SerialNfc nfc;  // swap for a real reader implementing Nfc
BackendClient backend(safety);
ModeController controller(safety, motors, display);
Console console(nfc, safety, backend);

uint32_t lastStatus = 0;
bool caughtPending = false;
bool dockPending = false;
bool centreRequested = false;
uint32_t lastPresenceSound = 0;
uint32_t lastSpeechSeq = 0;

}  // namespace

void fidoSetup() {
  motors.begin();  // first: every motor pin to its stopped state
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== Fido firmware ===");
  Serial.printf("[boot] board esp32-devkit-v1, motion %s, max speed %d%%, max escape %d ms\n",
                ENABLE_MOTION ? "COMPILED IN (ENABLE_MOTION=1)" : "compiled out (ENABLE_MOTION=0)",
                MAX_SPEED_PERCENT, MAX_ESCAPE_MS);

  display.begin();
  display.show("WAITING\nFOR APP", "");
  nfc.begin();
  hardware::begin();
  provisioning::begin();
  backend.begin();  // Wi-Fi first so it gets the radio before Bluetooth starts
  audio::begin();
  Console::help();

  // Hardware watchdog on the control task, armed after the slow Bluetooth start-up: if the loop ever
  // hangs the chip resets. After a reset the GPIOs are inputs, so fit pull-down resistors on the
  // motor driver's PWM/direction inputs (see README) so a reset also means "stopped".
  esp_task_wdt_init(5, true);
  esp_task_wdt_add(nullptr);
}

void fidoLoop() {
  esp_task_wdt_reset();
  uint32_t now = millis();

  console.poll();
  // Stop before servicing setup HTTP, which may wait briefly on a slow client.
  if (WiFi.softAPgetStationNum() > 0) motors.stop();
  provisioning::poll();
  if (provisioning::restarting()) {
    safety.setEstop(true);
    motors.stop();
    return;
  }
  hardware::tickRange();
  for (NfcEvent e = nfc.poll(); e != NfcEvent::None; e = nfc.poll()) backend.sendEvent(e);

  bool valid;
  fido::Command cmd = backend.latest(valid);
  if (caughtPending && valid && cmd.mode != fido::Mode::Run) caughtPending = false;
  if (dockPending && valid && cmd.mode != fido::Mode::BeckonDock) dockPending = false;
  if (hardware::buttonPressed()) {
    Serial.println("[button] clicked");
    motors.stop(); // Physical stop works in every mode, even with a stale backend.
    if (valid && (cmd.mode == fido::Mode::Run || millis() - backend.lastOkMs() < kWatchdogMs)) {
      NfcEvent event = cmd.mode == fido::Mode::BeckonDock ? NfcEvent::Docked :
                       cmd.mode == fido::Mode::Run ? NfcEvent::Caught : NfcEvent::None;
      if (event == NfcEvent::Caught) {
        caughtPending = true;
        motors.stop(); // Immediate local stop, even before server acknowledges the catch.
      }
      if (event == NfcEvent::Docked) dockPending = true;
      if (event != NfcEvent::None && !backend.sendEvent(event))
        Serial.println("[button] event queue full; click again");
      if (event == NfcEvent::None) {
        safety.setEstop(true);
        Serial.println("[button] stop latched; serial r releases");
      }
    } else {
      safety.setEstop(true);
      Serial.println("[button] backend stale; stop latched (r releases)");
    }
  }
  if (!centreRequested && now > 10000) {
    // Gives time to reconnect GPIO12 after boot, before bounded gestures start.
    hardware::centreServo();
    centreRequested = true;
    Serial.println("[servo] centre signal active on GPIO12");
  }
  if (centreRequested)
    hardware::tickServo(cmd.mode, valid && !caughtPending && !dockPending && !safety.estop() &&
                        millis() - backend.lastOkMs() < kWatchdogMs &&
                        WiFi.softAPgetStationNum() == 0);
  if (caughtPending || dockPending || WiFi.softAPgetStationNum() > 0) {
    motors.stop();
  } else {
    controller.update(now, cmd.task, hardware::rangeFresh(), hardware::rangeCm(),
                      hardware::rangeSampleMs());
  }
  audio::tick(now);
  if (cmd.speech.present && cmd.speech.seq != lastSpeechSeq) {
    lastSpeechSeq = cmd.speech.seq;
    lastPresenceSound = now; // Give backend cues priority and room to finish.
  }
  uint32_t soundInterval = cmd.mode == fido::Mode::Watch ? 60000 : 22000;
  bool ambientMode = cmd.mode == fido::Mode::Sleep || cmd.mode == fido::Mode::BeckonDock ||
                     cmd.mode == fido::Mode::Watch || cmd.mode == fido::Mode::Celebrate;
  if (ambientMode && valid && cmd.voice_enabled && !safety.estop() &&
      !caughtPending && !dockPending && millis() - backend.lastOkMs() < kWatchdogMs &&
      WiFi.softAPgetStationNum() == 0 && now - lastPresenceSound >= soundInterval) {
    lastPresenceSound = now;
    if (audio::speaker() == audio::Speaker::Connected)
      audio::request("woof-1", 100, false); // Full PCM amplitude; never interrupt another clip.
  }

  if (now - lastStatus >= 5000) {
    lastStatus = now;
    printStatus(backend, safety);
    Serial.printf("[range] %.1f cm (%s) | catch %s | setup clients %u\n",
                  hardware::rangeCm(), hardware::rangeFresh() ? "fresh" : "STOP: stale/invalid",
                  caughtPending ? "pending" : "no", WiFi.softAPgetStationNum());
  }
  delay(10);
}
