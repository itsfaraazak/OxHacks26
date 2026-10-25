#include "mode.h"

#include <string.h>

namespace fido {

namespace {
struct Entry {
  const char* name;
  Mode mode;
};
const Entry kModes[] = {
    {"sleep", Mode::Sleep},   {"beckon_dock", Mode::BeckonDock}, {"watch", Mode::Watch},
    {"nudge", Mode::Nudge},   {"bark", Mode::Bark},              {"run", Mode::Run},
    {"rest", Mode::Rest},     {"celebrate", Mode::Celebrate},
};
}  // namespace

Mode parseMode(const char* name) {
  if (name == nullptr) return Mode::Unknown;
  for (const Entry& e : kModes) {
    if (strcmp(name, e.name) == 0) return e.mode;
  }
  return Mode::Unknown;
}

const char* modeName(Mode mode) {
  for (const Entry& e : kModes) {
    if (e.mode == mode) return e.name;
  }
  return "unknown";
}

}  // namespace fido
