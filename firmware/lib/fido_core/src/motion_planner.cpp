#include "motion_planner.h"

namespace fido {

MotorCommand planMotion(Mode mode, uint32_t ms_in_mode) {
  switch (mode) {
    case Mode::Nudge: {
      uint32_t t = ms_in_mode % 1500;
      if (t < 100) return {-1.0f, 1.0f};
      if (t < 300) return {1.0f, -1.0f};
      if (t < 400) return {-1.0f, 1.0f};
      return {};
    }
    case Mode::Run:
      return ((ms_in_mode / 600) % 2 == 0) ? MotorCommand{1.0f, 0.6f} : MotorCommand{0.6f, 1.0f};
    default:
      return {};
  }
}

}  // namespace fido
