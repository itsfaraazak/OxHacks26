#pragma once
#include <Arduino.h>

namespace provisioning {
void begin();
void poll();
const String& ssid();
const String& password();
const String& backendUrl();
const String& speakerName();
const String& token();
bool restarting();
}
