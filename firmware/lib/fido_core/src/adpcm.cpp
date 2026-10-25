#include "adpcm.h"

namespace fido {

namespace {
const int8_t kIndexTable[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};
const int16_t kStepTable[89] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,
    25,    28,    31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,
    88,    97,    107,   118,   130,   143,   157,   173,   190,   209,   230,   253,   279,
    307,   337,   371,   408,   449,   494,   544,   598,   658,   724,   796,   876,   963,
    1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,  2272,  2499,  2749,  3024,  3327,
    3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845,  8630,  9493,  10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};
}  // namespace

int16_t AdpcmDecoder::decode(uint8_t nibble) {
  nibble &= 0x0F;
  int32_t step = kStepTable[index_];
  int32_t diff = step >> 3;
  if (nibble & 1) diff += step >> 2;
  if (nibble & 2) diff += step >> 1;
  if (nibble & 4) diff += step;
  if (nibble & 8) diff = -diff;
  predictor_ += diff;
  if (predictor_ > 32767) predictor_ = 32767;
  if (predictor_ < -32768) predictor_ = -32768;
  index_ += kIndexTable[nibble];
  if (index_ < 0) index_ = 0;
  if (index_ > 88) index_ = 88;
  return static_cast<int16_t>(predictor_);
}

}  // namespace fido
