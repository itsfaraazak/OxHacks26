#include "motion_planner.h"

namespace fido {

MotorCommand planMotion(Mode mode, uint32_t ms_in_mode) {
  switch (mode) {
    case Mode::Nudge: {
      if (ms_in_mode < 300) return {1.0f, 1.0f};
      if (ms_in_mode < 350) return {};
      if (ms_in_mode < 650) return {-1.0f, -1.0f};
      return {};
    }
    case Mode::Run:
      return {1.0f, 1.0f};
    default:
      return {};
  }
}

}  // namespace fido
