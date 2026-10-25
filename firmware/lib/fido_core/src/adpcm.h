// IMA ADPCM (4 bits/sample) decoder. Headerless stream: low nibble first, predictor 0 and
// step index 0 at the start of every clip. tools/prepare_clips.py is the matching encoder.
#pragma once

#include <stdint.h>

namespace fido {

class AdpcmDecoder {
 public:
  void reset() {
    predictor_ = 0;
    index_ = 0;
  }
  int16_t decode(uint8_t nibble);

 private:
  int32_t predictor_ = 0;
  int32_t index_ = 0;
};

// Sample `i` of a packed stream (two samples per byte, low nibble first).
inline uint8_t adpcmNibble(const uint8_t* data, uint32_t i) {
  uint8_t b = data[i >> 1];
  return (i & 1) ? (b >> 4) : (b & 0x0F);
}

}  // namespace fido
