#include "backend_client.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include "audio.h"
#include "backoff.h"
#include "battery.h"
#include "settings.h"
#include "speech_gate.h"

namespace {
fido::SpeechGate speechGate;
}

void BackendClient::begin() {
  queue_ = xQueueCreate(8, sizeof(NfcEvent));
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  // Modem power save lets the Bluetooth controller have air time between Wi-Fi beacons; with it
  // off Wi-Fi hogs the radio and A2DP audio stutters. Costs up to one DTIM interval (~100 ms) of
  // latency per request, far inside the 3 s watchdog.
  WiFi.setSleep(WIFI_POWER_SAVE ? WIFI_PS_MIN_MODEM : WIFI_PS_NONE);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[wifi] connecting to \"%s\"\n", WIFI_SSID);
  xTaskCreatePinnedToCore(taskEntry, "backend", 8192, this, 2, nullptr, 0);
}

bool BackendClient::sendEvent(NfcEvent e) {
  return xQueueSend(static_cast<QueueHandle_t>(queue_), &e, 0) == pdTRUE;
}

LinkStats BackendClient::stats() const {
  portENTER_CRITICAL(&mux_);
  LinkStats s = stats_;
  portEXIT_CRITICAL(&mux_);
  s.wifi = WiFi.status() == WL_CONNECTED;
  return s;
}

fido::Command BackendClient::latest(bool& valid) const {
  portENTER_CRITICAL(&mux_);
  fido::Command c = latest_;
  valid = valid_;
  portEXIT_CRITICAL(&mux_);
  return c;
}

void BackendClient::taskEntry(void* self) { static_cast<BackendClient*>(self)->run(); }

void BackendClient::run() {
  uint32_t nextBeat = millis();
  for (;;) {
    uint32_t now = millis();
    wifiMaintain(now);

    // NFC events go out immediately, on their own request.
    NfcEvent e;
    while (xQueueReceive(static_cast<QueueHandle_t>(queue_), &e, 0) == pdTRUE) {
      Serial.printf("[backend] event %s\n", nfcEventName(e));
      post(nfcEventName(e));
    }

    now = millis();
    if (static_cast<int32_t>(now - nextBeat) >= 0) {
      post("heartbeat");
      nextBeat += kHeartbeatPeriodMs;
      now = millis();
      if (static_cast<int32_t>(now - nextBeat) > 0) nextBeat = now + 100;  // do not burst to catch up
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

// Reconnect Wi-Fi on our own schedule if the SDK's auto-reconnect has not recovered.
void BackendClient::wifiMaintain(uint32_t now) {
  static fido::Backoff backoff(2000, 15000);
  if (WiFi.status() == WL_CONNECTED) {
    backoff.reset(now);
    return;
  }
  if (backoff.due(now)) {
    Serial.println("[wifi] not connected, reconnecting");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    backoff.failed(now);
  }
}

static int batteryPercentNow() {
  if (BATTERY_ADC_PIN < 0) return -1;
  uint32_t mv = 0;
  for (int i = 0; i < 8; ++i) mv += analogReadMilliVolts(BATTERY_ADC_PIN);
  mv /= 8;
  return fido::batteryPercent(static_cast<int>(mv * BATTERY_DIVIDER_RATIO), BATTERY_EMPTY_MV, BATTERY_FULL_MV);
}

bool BackendClient::post(const char* event) {
  uint32_t t0 = millis();
  bool ok = false;
  int code = 0;

  if (WiFi.status() == WL_CONNECTED) {
    JsonDocument doc;
    doc["event"] = event;
    JsonObject info = doc["info"].to<JsonObject>();
    info["board"] = "esp32-devkit-v1";
    int battery = batteryPercentNow();
    if (battery >= 0) info["battery"] = battery;
    info["speaker"] = audio::speakerName(audio::speaker());
    char body[200];
    size_t n = serializeJson(doc, body, sizeof body);

    WiFiClient client;
    HTTPClient http;
    http.setConnectTimeout(kHttpConnectTimeoutMs);
    http.setTimeout(kHttpTimeoutMs);
    http.setReuse(false);
    String url = String(BACKEND_BASE_URL) + "/api/robot/events";
    if (http.begin(client, url)) {
      http.addHeader("Content-Type", "application/json");
      if (DEVICE_TOKEN[0] != '\0') http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);
      code = http.POST(reinterpret_cast<uint8_t*>(body), n);
      if (code == 200 && http.getSize() < 4096) {
        String payload = http.getString();
        fido::Command cmd;
        if (fido::parseCommand(payload.c_str(), payload.length(), cmd)) {
          uint32_t now = millis();
          safety_.onReply(now, cmd.mode, cmd.motion_enabled);
          lastOkMs_ = now;
          if (errorStreak_ >= kErrorStreakLimit) speechGate.resync();  // outage: do not replay old cues
          errorStreak_ = 0;
          if (speechGate.shouldPlay(cmd.speech)) audio::request(cmd.speech.clip);
          portENTER_CRITICAL(&mux_);
          latest_ = cmd;
          valid_ = true;
          portEXIT_CRITICAL(&mux_);
          ok = true;
        } else {
          code = -100;  // 200 but not a robot command
        }
      }
      http.end();
    } else {
      code = -101;
    }
  } else {
    code = -102;  // Wi-Fi down
  }

  uint32_t ms = millis() - t0;
  portENTER_CRITICAL(&mux_);
  ++stats_.sent;
  stats_.last_code = code;
  if (ok) {
    ++stats_.ok;
    stats_.last_ms = ms;
    if (ms > stats_.max_ms) stats_.max_ms = ms;
    stats_.avg_ms = stats_.ok == 1 ? ms : (stats_.avg_ms * 7 + ms) / 8;
  } else {
    ++stats_.dropped;
  }
  portEXIT_CRITICAL(&mux_);

  if (!ok) {
    safety_.onHttpError(millis());
    if (errorStreak_ < 255) ++errorStreak_;
    Serial.printf("[backend] %s failed (code %d, %u ms, streak %u)\n", event, code, static_cast<unsigned>(ms),
                  errorStreak_);
  }
  return ok;
}
