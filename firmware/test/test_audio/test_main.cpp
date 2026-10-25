#include <string.h>
#include <unity.h>

#include <vector>

#include "adpcm.h"
#include "clip_player.h"
#include "clip_store.h"
#include "speaker_status.h"

using namespace fido;

void setUp() {}
void tearDown() {}

// Produced by tools/prepare_clips.py: 40 samples of 8000*sin(i/3), encoded and decoded in Python.
static const uint8_t kGolden[20] = {112, 119, 119, 119, 226, 188, 170, 8,   66,  68,
                                    35,  19,  152, 219, 204, 170, 138, 33,  83,  52};
static const int16_t kGoldenPcm[40] = {
    0,     11,    41,    104,   240,   533,   1164,  2521,  3491,  1199,  -1612, -4258, -5975, -7536,
    -7820, -7562, -6389, -4469, -2145, 666,   3312,  5029,  7214,  8066,  7808,  7105,  5613,  3479,
    923,   -2169, -4247, -6137, -7854, -8166, -7314, -6023, -4381, -2035, 776,   3422};

void test_decoder_matches_python_encoder() {
  AdpcmDecoder d;
  for (uint32_t i = 0; i < 40; ++i) {
    TEST_ASSERT_EQUAL_INT16(kGoldenPcm[i], d.decode(adpcmNibble(kGolden, i)));
  }
}

// ---- clip store ------------------------------------------------------------------------
static std::vector<uint8_t> makeBlob(uint32_t rate = 16000) {
  std::vector<uint8_t> b(16 + 2 * 32, 0);
  memcpy(b.data(), "FIDOCLP1", 8);
  b[8] = rate & 255;
  b[9] = rate >> 8;
  b[12] = 2;  // two clips, both the golden data
  uint32_t offset = static_cast<uint32_t>(b.size());
  const char* names[] = {"run-1", "nudge-2"};
  for (int i = 0; i < 2; ++i) {
    uint8_t* e = b.data() + 16 + 32 * i;
    memcpy(e, names[i], strlen(names[i]));
    uint32_t off = offset + 20 * i;
    memcpy(e + 24, &off, 4);  // host is little endian, like the ESP32
    uint32_t n = 40;
    memcpy(e + 28, &n, 4);
  }
  b.insert(b.end(), kGolden, kGolden + 20);
  b.insert(b.end(), kGolden, kGolden + 20);
  return b;
}

void test_store_finds_clips_and_ignores_unknown() {
  auto b = makeBlob();
  ClipStore s;
  TEST_ASSERT_TRUE(s.open(b.data(), b.size()));
  TEST_ASSERT_EQUAL(2, s.count());
  ClipRef r;
  TEST_ASSERT_TRUE(s.find("nudge-2", r));
  TEST_ASSERT_EQUAL_UINT32(40, r.samples);
  TEST_ASSERT_EQUAL_UINT32(16000, r.sample_rate);
  TEST_ASSERT_EQUAL_UINT8(kGolden[0], r.adpcm[0]);
  TEST_ASSERT_FALSE(s.find("nudge-3", r));
  TEST_ASSERT_FALSE(s.find("nudge", r));  // prefix of a name is not a match
  TEST_ASSERT_FALSE(s.find("", r));
  TEST_ASSERT_FALSE(s.find(nullptr, r));
}

void test_store_rejects_erased_or_truncated_flash() {
  std::vector<uint8_t> erased(256, 0xFF);
  ClipStore s;
  TEST_ASSERT_FALSE(s.open(erased.data(), erased.size()));
  ClipRef r;
  TEST_ASSERT_FALSE(s.find("run-1", r));
  auto b = makeBlob();
  TEST_ASSERT_FALSE(s.open(b.data(), b.size() - 1));  // last clip runs past the end
  TEST_ASSERT_FALSE(s.open(b.data(), 10));
  TEST_ASSERT_FALSE(s.open(nullptr, 0));
}

// ---- advertised playback availability -------------------------------------------------
static const char* kRuntimeClips[] = {"dock-1", "woof-1", "nudge-1", "bark-4", "bark-5", "run-5"};

static std::vector<uint8_t> runtimeBlob(int omit = -1, bool emptyClip = false) {
  const size_t count = omit < 0 ? 6 : 5;
  std::vector<uint8_t> b(16 + count * 32, 0);
  memcpy(b.data(), "FIDOCLP1", 8);
  b[8] = 0x80;  // 16000 Hz, little endian
  b[9] = 0x3e;
  b[12] = static_cast<uint8_t>(count);
  size_t entry = 0;
  for (int i = 0; i < 6; ++i) {
    if (i == omit) continue;
    uint8_t* e = b.data() + 16 + 32 * entry++;
    memcpy(e, kRuntimeClips[i], strlen(kRuntimeClips[i]));
    uint32_t offset = static_cast<uint32_t>(16 + count * 32);
    uint32_t samples = emptyClip && i == 0 ? 0 : 40;
    memcpy(e + 24, &offset, 4);
    memcpy(e + 28, &samples, 4);
  }
  b.insert(b.end(), kGolden, kGolden + 20);
  return b;
}

void test_speaker_not_ready_without_valid_image_even_if_bluetooth_connected() {
  ClipStore s;
  TEST_ASSERT_TRUE(speakerStatus(s, true, true) == Speaker::None);
  std::vector<uint8_t> erased(256, 0xff);
  TEST_ASSERT_FALSE(s.open(erased.data(), erased.size()));
  TEST_ASSERT_TRUE(speakerStatus(s, true, true) == Speaker::None);
  auto b = runtimeBlob();
  TEST_ASSERT_FALSE(s.open(b.data(), b.size() - 1));
  TEST_ASSERT_TRUE(speakerStatus(s, true, true) == Speaker::None);
}

void test_speaker_requires_every_runtime_clip_not_just_a_valid_store() {
  for (int omit = 0; omit < 6; ++omit) {
    auto b = runtimeBlob(omit);
    ClipStore s;
    TEST_ASSERT_TRUE(s.open(b.data(), b.size()));
    TEST_ASSERT_TRUE(speakerStatus(s, true, true) == Speaker::None);
  }
  auto wrongNames = runtimeBlob();
  memcpy(wrongNames.data() + 16, "dock-2", 6);  // six entries is not enough: IDs must match
  ClipStore wrong;
  TEST_ASSERT_TRUE(wrong.open(wrongNames.data(), wrongNames.size()));
  TEST_ASSERT_TRUE(speakerStatus(wrong, true, true) == Speaker::None);
  auto b = makeBlob();  // valid older image with unrelated names
  ClipStore s;
  TEST_ASSERT_TRUE(s.open(b.data(), b.size()));
  TEST_ASSERT_TRUE(speakerStatus(s, true, true) == Speaker::None);
  b = runtimeBlob(-1, true);
  TEST_ASSERT_TRUE(s.open(b.data(), b.size()));
  TEST_ASSERT_TRUE(speakerStatus(s, true, true) == Speaker::None);
}

void test_complete_image_requires_bluetooth_and_failed_reopen_clears_readiness() {
  auto b = runtimeBlob();
  ClipStore s;
  TEST_ASSERT_TRUE(s.open(b.data(), b.size()));
  TEST_ASSERT_TRUE(speakerStatus(s, false, false) == Speaker::None);
  TEST_ASSERT_TRUE(speakerStatus(s, false, true) == Speaker::None);
  TEST_ASSERT_TRUE(speakerStatus(s, true, false) == Speaker::Disconnected);
  TEST_ASSERT_TRUE(speakerStatus(s, true, true) == Speaker::Connected);
  TEST_ASSERT_FALSE(s.open(nullptr, 0));
  TEST_ASSERT_TRUE(speakerStatus(s, true, true) == Speaker::None);
}

// ---- player ----------------------------------------------------------------------------
void test_player_outputs_stereo_44k_with_silence_padding() {
  ClipPlayer p;
  p.begin(kGolden, 40, 16000, 44100, 10, 10);  // 441 frames preroll, 441 tail
  std::vector<int16_t> out(2 * 4000);
  uint32_t n = p.read(out.data(), 4000);
  TEST_ASSERT_FALSE(p.active());
  // 40 source samples at 16 kHz = 110 frames at 44.1 kHz, plus ramp-down, plus padding.
  TEST_ASSERT_TRUE(n >= 441 + 110 + 441 && n <= 441 + 125 + 441);
  for (uint32_t i = 0; i < 441; ++i) TEST_ASSERT_EQUAL_INT16(0, out[2 * i]);
  bool loud = false;
  for (uint32_t i = 441; i < n - 441; ++i) {
    TEST_ASSERT_EQUAL_INT16(out[2 * i], out[2 * i + 1]);  // mono duplicated to both channels
    if (out[2 * i] > 2000 || out[2 * i] < -2000) loud = true;
  }
  TEST_ASSERT_TRUE(loud);
  for (uint32_t i = n - 400; i < n; ++i) TEST_ASSERT_EQUAL_INT16(0, out[2 * i]);
}

void test_player_chunking_does_not_change_the_output() {
  std::vector<int16_t> a(2 * 20000), b(2 * 20000);
  ClipPlayer p, q;
  p.begin(kGolden, 40, 16000);
  uint32_t na = p.read(a.data(), 20000);
  TEST_ASSERT_FALSE(p.active());
  q.begin(kGolden, 40, 16000);
  uint32_t nb = 0;
  while (q.active()) nb += q.read(b.data() + 2 * nb, 7);  // odd chunk size, like the BT stack
  TEST_ASSERT_EQUAL_UINT32(na, nb);
  TEST_ASSERT_EQUAL_INT16_ARRAY(a.data(), b.data(), 2 * na);
}

void test_player_stop_and_bad_input() {
  ClipPlayer p;
  p.begin(nullptr, 40, 16000);
  TEST_ASSERT_FALSE(p.active());
  p.begin(kGolden, 0, 16000);
  TEST_ASSERT_FALSE(p.active());
  p.begin(kGolden, 40, 16000);
  TEST_ASSERT_TRUE(p.active());
  p.stop();
  int16_t out[8];
  TEST_ASSERT_EQUAL_UINT32(0, p.read(out, 4));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_decoder_matches_python_encoder);
  RUN_TEST(test_store_finds_clips_and_ignores_unknown);
  RUN_TEST(test_store_rejects_erased_or_truncated_flash);
  RUN_TEST(test_speaker_not_ready_without_valid_image_even_if_bluetooth_connected);
  RUN_TEST(test_speaker_requires_every_runtime_clip_not_just_a_valid_store);
  RUN_TEST(test_complete_image_requires_bluetooth_and_failed_reopen_clears_readiness);
  RUN_TEST(test_player_outputs_stereo_44k_with_silence_padding);
  RUN_TEST(test_player_chunking_does_not_change_the_output);
  RUN_TEST(test_player_stop_and_bad_input);
  return UNITY_END();
}
