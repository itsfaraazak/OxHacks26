#include "command.h"

#include <ArduinoJson.h>
#include <string.h>

namespace fido {

namespace {
void copyString(char* dst, size_t cap, const char* src) {
  if (src == nullptr) src = "";
  strncpy(dst, src, cap - 1);
  dst[cap - 1] = '\0';
}
}  // namespace

bool parseCommand(const char* json, size_t length, Command& out) {
  JsonDocument doc;
  if (deserializeJson(doc, json, length)) return false;
  const char* mode = doc["mode"] | static_cast<const char*>(nullptr);
  if (mode == nullptr) return false;

  Command cmd;
  cmd.mode = parseMode(mode);
  // Only a literal JSON `true` enables motion; a string, number or missing key is "off".
  cmd.motion_enabled = doc["motion_enabled"].is<bool>() && doc["motion_enabled"].as<bool>();
  copyString(cmd.task, sizeof cmd.task, doc["task"] | static_cast<const char*>(nullptr));

  JsonObjectConst speech = doc["speech"];
  if (!speech.isNull()) {
    cmd.speech.present = true;
    cmd.speech.seq = speech["seq"] | 0u;
    copyString(cmd.speech.clip, sizeof cmd.speech.clip, speech["clip"] | static_cast<const char*>(nullptr));
  }
  out = cmd;
  return true;
}

}  // namespace fido
