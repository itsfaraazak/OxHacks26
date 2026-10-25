// Robot modes sent by the backend (backend/fido/engine.py ROBOT_MODES).
#pragma once

#include <stdint.h>

namespace fido {

enum class Mode : uint8_t {
  Unknown,  // anything we do not recognise: treated as "do nothing, never move"
  Sleep,
  BeckonDock,
  Watch,
  Nudge,
  Bark,
  Run,
  Rest,
  Celebrate,
};

Mode parseMode(const char* name);
const char* modeName(Mode mode);

}  // namespace fido
