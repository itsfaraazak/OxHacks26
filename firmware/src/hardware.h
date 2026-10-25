#pragma once
#include <Arduino.h>
#include "mode.h"

namespace hardware {
void begin();
bool buttonPressed();
void tickRange();
bool rangeFresh();
float rangeCm();
uint32_t rangeSampleMs();
// Attach after boot, then slew-limit mode-specific wags around centre.
void centreServo();
void tickServo(fido::Mode mode, bool enabled);
}
