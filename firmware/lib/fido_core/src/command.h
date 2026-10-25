// The backend's reply to POST /api/robot/events (backend/fido/app.py robot_command).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "mode.h"

namespace fido {

struct Speech {
  bool present = false;  // false when the reply has `"speech": null`
  uint32_t seq = 0;
  char clip[24] = {0};
};

// Every default is the safe one: unknown mode, motion disabled.
struct Command {
  Mode mode = Mode::Unknown;
  bool motion_enabled = false;
  bool voice_enabled = false; // Missing permission never enables autonomous sounds.
  Speech speech;
  char task[48] = {0};
};

// Returns false if the body is not JSON or has no string `mode`; `out` is then untouched.
bool parseCommand(const char* json, size_t length, Command& out);

}  // namespace fido
