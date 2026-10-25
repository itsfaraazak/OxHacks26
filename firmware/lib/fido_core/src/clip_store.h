// Read-only view of the audio partition written by tools/prepare_clips.py.
//
// Layout, little endian:
//   header   16 bytes: magic "FIDOCLP1", u32 sample_rate_hz, u16 clip_count, u16 reserved
//   entries  32 bytes each: char name[24] (NUL padded), u32 offset (from blob start), u32 samples
//   data     IMA ADPCM, mono, `sample_rate_hz`, 4 bits per sample
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace fido {

struct ClipRef {
  const uint8_t* adpcm = nullptr;
  uint32_t samples = 0;
  uint32_t sample_rate = 0;
};

class ClipStore {
 public:
  // False (and an empty store) if the blob is erased flash, truncated or inconsistent.
  bool open(const uint8_t* base, size_t size);
  size_t count() const { return count_; }
  // Unknown names (or an unopened store) return false.
  bool find(const char* name, ClipRef& out) const;
  bool nameAt(size_t index, char* out, size_t cap) const;

  static constexpr size_t kHeaderSize = 16;
  static constexpr size_t kEntrySize = 32;
  static constexpr size_t kNameSize = 24;

 private:
  const uint8_t* base_ = nullptr;
  size_t size_ = 0;
  size_t count_ = 0;
  uint32_t rate_ = 0;
};

}  // namespace fido
