// What each mode wants the wheels to do, as intent in [-1, 1]. The safety gate scales
// and may zero it. Differential drive: two rear wheels, passive omni wheel in front.
#pragma once

#include <stdint.h>

#include "motor_safety.h"
#include "mode.h"

namespace fido {

// `ms_in_mode` is the time since the backend switched to `mode`.
//   nudge: a tiny in-place wiggle (100 ms left, 200 ms right, 100 ms left, then rest),
//          net heading change zero, no translation, repeating every 1.5 s.
//   run:   straight ahead with a gentle weave. Not a "drive off the table" routine.
//   other: stand still.
MotorCommand planMotion(Mode mode, uint32_t ms_in_mode);

}  // namespace fido
