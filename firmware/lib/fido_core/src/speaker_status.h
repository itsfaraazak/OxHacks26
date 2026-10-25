// Playback availability advertised to the backend, not raw Bluetooth link state.
#pragma once

#include <stdint.h>

#include "clip_store.h"

namespace fido {

enum class Speaker : uint8_t { None, Disconnected, Connected };

inline bool runtimeClipsReady(const ClipStore& store) {
  // Keep in sync with backend/fido/speech.py PLAYABLE (the runtime playlist, not CLIPS).
  const char* required[] = {"dock-1", "woof-1", "nudge-1", "bark-4", "bark-5", "run-5"};
  for (const char* name : required) {
    ClipRef clip;
    if (!store.find(name, clip) || clip.samples == 0) return false;
  }
  return true;
}

inline Speaker speakerStatus(const ClipStore& store, bool btStarted, bool btConnected) {
  if (!btStarted || !runtimeClipsReady(store)) return Speaker::None;
  return btConnected ? Speaker::Connected : Speaker::Disconnected;
}

}  // namespace fido
