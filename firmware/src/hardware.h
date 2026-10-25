#pragma once
#include <Arduino.h>

namespace hardware {
void begin();
bool buttonPressed();
void tickRange();
bool rangeFresh();
float rangeCm();
// No tail routine is defined yet: centre once, without random movement.
void centreServo();
}
