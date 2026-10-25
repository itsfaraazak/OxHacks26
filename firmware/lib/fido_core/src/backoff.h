// Retry timer with exponential backoff, e.g. for reconnecting the Bluetooth speaker.
#pragma once

#include <stdint.h>

namespace fido {

class Backoff {
 public:
  Backoff(uint32_t initial_ms, uint32_t max_ms) : initial_(initial_ms), max_(max_ms), delay_(initial_ms) {}

  // True when an attempt may be made now. Safe across millis() rollover.
  bool due(uint32_t now_ms) const { return static_cast<int32_t>(now_ms - next_) >= 0; }
  // Call after an attempt that did not succeed: wait twice as long next time, up to the cap.
  void failed(uint32_t now_ms) {
    next_ = now_ms + delay_;
    delay_ = delay_ * 2 > max_ ? max_ : delay_ * 2;
  }
  // Call on success: the next failure starts again from the initial delay.
  void reset(uint32_t now_ms) {
    delay_ = initial_;
    next_ = now_ms;
  }
  uint32_t currentDelayMs() const { return delay_; }

 private:
  uint32_t initial_, max_, delay_;
  uint32_t next_ = 0;
};

}  // namespace fido
