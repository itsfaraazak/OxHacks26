#include "display.h"

#include <Arduino.h>

void SerialDisplay::show(const char* line1, const char* line2) {
  Serial.printf("[display] %s%s%s\n", line1, line2[0] ? " | " : "", line2);
}
