#include "clip_player.h"

namespace fido {

void ClipPlayer::begin(const uint8_t* adpcm, uint32_t samples, uint32_t src_rate, uint32_t out_rate,
                       uint32_t preroll_ms, uint32_t tail_ms) {
  if (adpcm == nullptr || samples == 0 || src_rate == 0 || out_rate == 0) {
    stage_ = Stage::Idle;
    return;
  }
  data_ = adpcm;
  samples_ = samples;
  pos_ = 0;
  dec_.reset();
  prev_ = 0;
  cur_ = dec_.decode(adpcmNibble(data_, pos_++));
  frac_ = 0;
  step_ = static_cast<uint32_t>((static_cast<uint64_t>(src_rate) << 16) / out_rate);
  src_done_ = false;
  preroll_left_ = static_cast<uint32_t>(static_cast<uint64_t>(out_rate) * preroll_ms / 1000);
  tail_frames_ = static_cast<uint32_t>(static_cast<uint64_t>(out_rate) * tail_ms / 1000);
  tail_left_ = tail_frames_;
  stage_ = preroll_left_ ? Stage::Preroll : Stage::Playing;
}

// One output frame of the resampled clip; moves to Tail after the last source sample.
int16_t ClipPlayer::next() {
  int32_t v = prev_ + static_cast<int32_t>((static_cast<int64_t>(cur_ - prev_) * frac_) >> 16);
  frac_ += step_;
  while (frac_ >= 65536u && stage_ == Stage::Playing) {
    frac_ -= 65536u;
    prev_ = cur_;
    if (pos_ < samples_) {
      cur_ = dec_.decode(adpcmNibble(data_, pos_++));
    } else if (!src_done_) {
      src_done_ = true;  // one more step so the output ramps down to 0
      cur_ = 0;
    } else {
      stage_ = Stage::Tail;
    }
  }
  return static_cast<int16_t>(v);
}

uint32_t ClipPlayer::read(int16_t* out, uint32_t frames) {
  uint32_t n = 0;
  while (n < frames && stage_ != Stage::Idle) {
    int16_t s = 0;
    if (stage_ == Stage::Preroll) {
      if (--preroll_left_ == 0) stage_ = Stage::Playing;
    } else if (stage_ == Stage::Playing) {
      s = next();
    } else {  // Tail
      if (tail_left_ == 0) {
        stage_ = Stage::Idle;
        break;
      }
      --tail_left_;
    }
    out[2 * n] = s;
    out[2 * n + 1] = s;
    ++n;
  }
  return n;
}

}  // namespace fido
