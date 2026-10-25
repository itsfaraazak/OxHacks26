#include "clip_store.h"

#include <string.h>

namespace fido {

namespace {
uint32_t rd32(const uint8_t* p) {
  return static_cast<uint32_t>(p[0]) | static_cast<uint32_t>(p[1]) << 8 | static_cast<uint32_t>(p[2]) << 16 |
         static_cast<uint32_t>(p[3]) << 24;
}
uint16_t rd16(const uint8_t* p) { return static_cast<uint16_t>(p[0] | p[1] << 8); }
}  // namespace

bool ClipStore::open(const uint8_t* base, size_t size) {
  base_ = nullptr;
  size_ = count_ = rate_ = 0;
  if (base == nullptr || size < kHeaderSize || memcmp(base, "FIDOCLP1", 8) != 0) return false;
  uint32_t rate = rd32(base + 8);
  size_t count = rd16(base + 12);
  if (rate < 4000 || rate > 48000) return false;
  if (kHeaderSize + count * kEntrySize > size) return false;
  for (size_t i = 0; i < count; ++i) {  // every clip must lie inside the blob
    const uint8_t* e = base + kHeaderSize + i * kEntrySize;
    uint64_t offset = rd32(e + kNameSize);
    uint64_t bytes = (static_cast<uint64_t>(rd32(e + kNameSize + 4)) + 1) / 2;
    if (offset < kHeaderSize + count * kEntrySize || offset + bytes > size) return false;
  }
  base_ = base;
  size_ = size;
  count_ = count;
  rate_ = rate;
  return true;
}

bool ClipStore::find(const char* name, ClipRef& out) const {
  if (base_ == nullptr || name == nullptr || name[0] == '\0') return false;
  for (size_t i = 0; i < count_; ++i) {
    const uint8_t* e = base_ + kHeaderSize + i * kEntrySize;
    if (strncmp(reinterpret_cast<const char*>(e), name, kNameSize) == 0 && strlen(name) < kNameSize) {
      out.adpcm = base_ + rd32(e + kNameSize);
      out.samples = rd32(e + kNameSize + 4);
      out.sample_rate = rate_;
      return true;
    }
  }
  return false;
}

bool ClipStore::nameAt(size_t index, char* out, size_t cap) const {
  if (index >= count_ || cap == 0) return false;
  const uint8_t* e = base_ + kHeaderSize + index * kEntrySize;
  size_t n = strnlen(reinterpret_cast<const char*>(e), kNameSize);
  if (n >= cap) n = cap - 1;
  memcpy(out, e, n);
  out[n] = '\0';
  return true;
}

}  // namespace fido
