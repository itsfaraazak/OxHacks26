#pragma once

#include <stdint.h>

namespace fido {

// Battery percent, rounded to the nearest 5 so the backend's state does not change on every
// ADC wobble (each change is pushed to every browser). Clamped to 0..100.
inline int batteryPercent(int millivolts, int empty_mv, int full_mv) {
  if (full_mv <= empty_mv) return 0;
  long pct = (static_cast<long>(millivolts) - empty_mv) * 100 / (full_mv - empty_mv);
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  return static_cast<int>((pct + 2) / 5 * 5);
}

}  // namespace fido
