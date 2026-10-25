#include "motion_planner.h"
#include "presence.h"

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
    case Mode::Bark:
      if (ms_in_mode < 150) return {1.0f, -1.0f};
      if (ms_in_mode < 200) return {};
      if (ms_in_mode < 350) return {-1.0f, 1.0f};
      return {};
    default:
      return presenceMotion(mode, ms_in_mode);
  }
}

}  // namespace fido
