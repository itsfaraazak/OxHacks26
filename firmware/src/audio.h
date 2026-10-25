// Bluetooth A2DP speaker + clip playback.
//
// The A2DP source library pulls PCM through a data callback (44.1 kHz, 16-bit, stereo). The callback
// runs in the Bluetooth stack's task: it must not block, so it only decodes ADPCM from flash into
// the buffer it is handed. request() is called from the backend task and just hands over a pointer.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "speaker_status.h"

namespace audio {

using Speaker = fido::Speaker;
const char* speakerName(Speaker s);  // "none" | "disconnected" | "connected": the heartbeat strings

struct Stats {
  uint32_t clips_played = 0;
  uint32_t clips_dropped = 0;  // asked for while playback was unavailable
  uint32_t clips_unknown = 0;  // name not in flash
  uint32_t underruns = 0;      // callback had a clip but the stack asked for more than was left
  uint32_t connects = 0;
  uint32_t disconnects = 0;
};

// Starts Bluetooth (unless SPEAKER_NAME is empty) and maps the clip partition. Takes ~1 s;
// the library then waits ~10 s before it first connects.
void begin();
// Call from the control loop: logs state changes and retries the connection with backoff.
void tick(uint32_t now_ms);
// Playback-ready status: connected requires the complete runtime clip set AND the Bluetooth link.
// None means Bluetooth is disabled or clips are unavailable; tick() still logs the actual link.
Speaker speaker();
// Plays a clip by name (latest request wins). False if it was ignored, with the reason logged.
bool request(const char* clip, uint8_t gainPercent = 100, bool interrupt = true);
bool playing();
size_t clipCount();
void listClips();
Stats stats();

}  // namespace audio
