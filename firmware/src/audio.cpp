#include "audio.h"

#include <Arduino.h>
#include <BluetoothA2DPSource.h>
#include <esp_partition.h>
#include <esp_spi_flash.h>

#include "backoff.h"
#include "clip_player.h"
#include "clip_store.h"
#include "settings.h"

namespace audio {
namespace {

// The library retries a lost speaker every 10 s forever (its connection timer). Each attempt pages the
// speaker and takes radio time away from Wi-Fi, so after repeated failures we stretch the interval:
// 10 s, 20 s, 40 s, 80 s, then every 120 s. Resets as soon as the speaker connects.
fido::Backoff reconnectBackoff(8000, 120000);

class Source : public BluetoothA2DPSource {
 protected:
  void bt_app_av_state_unconnected_hdlr(uint16_t event, void* param) override {
    constexpr uint16_t kHeartBeat = 0xff00;  // BT_APP_HEART_BEAT_EVT in BluetoothA2DPSource.cpp
    if (event == kHeartBeat && is_target_status_active) {
      uint32_t now = millis();
      if (!reconnectBackoff.due(now)) return;  // not yet: skip this retry
      reconnectBackoff.failed(now);
      Serial.printf("[a2dp] reconnect attempt (next in >= %u s)\n",
                    static_cast<unsigned>(reconnectBackoff.currentDelayMs() / 1000));
    }
    BluetoothA2DPSource::bt_app_av_state_unconnected_hdlr(event, param);
  }
};

Source a2dp;
fido::ClipStore store;
fido::ClipPlayer player;  // touched only from the A2DP data callback
Stats st;

// Hand-over between request() (backend task) and the data callback (Bluetooth task).
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
fido::ClipRef pending;
bool havePending = false;
volatile bool isPlaying = false;

bool btStarted = false;
bool wasConnected = false;

int32_t dataCallback(uint8_t* data, int32_t len) {
  if (len < 4 || data == nullptr) return 0;
  fido::ClipRef next;
  bool take = false;
  portENTER_CRITICAL(&mux);
  if (havePending) {
    next = pending;
    havePending = false;
    take = true;
  }
  portEXIT_CRITICAL(&mux);
  if (take) player.begin(next.adpcm, next.samples, next.sample_rate);
  if (!player.active()) {
    isPlaying = false;
    return 0;  // nothing to say: send no audio (keeps the radio free for Wi-Fi)
  }
  isPlaying = true;
  uint32_t frames = static_cast<uint32_t>(len) / 4;
  uint32_t n = player.read(reinterpret_cast<int16_t*>(data), frames);
  if (!player.active()) isPlaying = false;
  return static_cast<int32_t>(n * 4);
}

void mapClips() {
  const esp_partition_t* part =
      esp_partition_find_first(ESP_PARTITION_TYPE_DATA, static_cast<esp_partition_subtype_t>(0x40), "audio");
  if (part == nullptr) {
    Serial.println("[audio] no `audio` partition: is partitions.csv in use? clips disabled");
    return;
  }
  const void* ptr = nullptr;
  spi_flash_mmap_handle_t handle;
  if (esp_partition_mmap(part, 0, part->size, SPI_FLASH_MMAP_DATA, &ptr, &handle) != ESP_OK) {
    Serial.println("[audio] could not map the audio partition; clips disabled");
    return;
  }
  if (!store.open(static_cast<const uint8_t*>(ptr), part->size)) {
    Serial.println("[audio] audio partition is empty or invalid. Run tools/prepare_clips.py, "
                   "then `pio run -t uploadaudio`. Clips disabled.");
    return;
  }
  Serial.printf("[audio] %u clips in flash\n", static_cast<unsigned>(store.count()));
  if (!fido::runtimeClipsReady(store)) {
    Serial.println("[audio] required runtime clips missing or empty; speaker reports \"none\", "
                   "laptop fallback stays enabled");
  }
}

}  // namespace

const char* speakerName(Speaker s) {
  switch (s) {
    case Speaker::Connected: return "connected";
    case Speaker::Disconnected: return "disconnected";
    case Speaker::None: break;
  }
  return "none";
}

void begin() {
  mapClips();
  if (SPEAKER_NAME[0] == '\0') {
    Serial.println("[audio] SPEAKER_NAME is empty: Bluetooth audio off, speaker reports \"none\"");
    return;
  }
  a2dp.set_auto_reconnect(true);
  a2dp.set_data_callback(dataCallback);
  a2dp.set_task_core(1);  // Bluetooth app task on core 1; the backend task runs on core 0
  a2dp.set_volume(AUDIO_VOLUME);
  Serial.printf("[audio] starting A2DP source, looking for \"%s\" (connects ~10 s after boot)\n", SPEAKER_NAME);
  a2dp.start(SPEAKER_NAME);
  btStarted = true;
}

void tick(uint32_t now_ms) {
  if (!btStarted) return;
  bool connected = a2dp.is_connected();
  if (connected != wasConnected) {
    wasConnected = connected;
    if (connected) {
      ++st.connects;
      reconnectBackoff.reset(now_ms);
      Serial.printf("[a2dp] speaker connected (%s)\n", a2dp.get_name());
    } else {
      ++st.disconnects;
      Serial.println("[a2dp] speaker disconnected");
    }
  }
}

Speaker speaker() {
  return fido::speakerStatus(store, btStarted, btStarted && a2dp.is_connected());
}

bool request(const char* clip) {
  if (speaker() != Speaker::Connected) {
    ++st.clips_dropped;
    Serial.printf("[audio] \"%s\" not played: speaker %s\n", clip, speakerName(speaker()));
    return false;
  }
  fido::ClipRef ref;
  if (!store.find(clip, ref)) {
    ++st.clips_unknown;
    Serial.printf("[audio] \"%s\" is not in flash, ignored\n", clip);
    return false;
  }
  portENTER_CRITICAL(&mux);
  pending = ref;
  havePending = true;
  portEXIT_CRITICAL(&mux);
  ++st.clips_played;
  Serial.printf("[audio] playing \"%s\" (%.1f s)\n", clip, static_cast<double>(ref.samples) / ref.sample_rate);
  return true;
}

bool playing() { return isPlaying; }
size_t clipCount() { return store.count(); }
Stats stats() { return st; }

void listClips() {
  char name[fido::ClipStore::kNameSize + 1];
  Serial.printf("[audio] %u clips:", static_cast<unsigned>(store.count()));
  for (size_t i = 0; i < store.count(); ++i) {
    if (store.nameAt(i, name, sizeof name)) Serial.printf(" %s", name);
  }
  Serial.println();
}

}  // namespace audio
