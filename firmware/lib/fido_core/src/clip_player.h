// Turns one ADPCM clip into 44.1 kHz stereo 16-bit frames, which is what the A2DP source
// callback must supply. Mono source is duplicated to both channels; the sample rate is
// converted with linear interpolation (fine for speech through a small speaker).
// A little silence is added before the clip (so a speaker that is waking up does not eat the
// first word) and after it (so the last syllable is not cut off).
//
// Not thread-safe: use it from one task (the A2DP data callback).
#pragma once

#include <stdint.h>

#include "adpcm.h"

namespace fido {

class ClipPlayer {
 public:
  void begin(const uint8_t* adpcm, uint32_t samples, uint32_t src_rate, uint32_t out_rate = 44100,
             uint32_t preroll_ms = 150, uint32_t tail_ms = 100);
  void stop() { stage_ = Stage::Idle; }
  bool active() const { return stage_ != Stage::Idle; }

  // Writes up to `frames` stereo frames (L,R interleaved) to `out`; returns how many it wrote.
  // It writes fewer than asked only when the clip has ended (then active() is false).
  uint32_t read(int16_t* out, uint32_t frames);

 private:
  enum class Stage : uint8_t { Idle, Preroll, Playing, Tail };
  int16_t next();

  Stage stage_ = Stage::Idle;
  const uint8_t* data_ = nullptr;
  uint32_t samples_ = 0, pos_ = 0;
  AdpcmDecoder dec_;
  int32_t prev_ = 0, cur_ = 0;
  uint32_t frac_ = 0, step_ = 0;  // Q16 phase and per-output-frame increment
  bool src_done_ = false;
  uint32_t preroll_left_ = 0, tail_left_ = 0, tail_frames_ = 0;
};

}  // namespace fido
