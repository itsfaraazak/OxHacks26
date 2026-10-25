#include "display.h"

#include <Arduino.h>

void SerialDisplay::show(const char* line1, const char* line2) {
  Serial.printf("[display] %s%s%s\n", line1, line2[0] ? " | " : "", line2);
}

void OledDisplay::begin() {
  Wire.begin(13, 32);
  Wire.setTimeOut(25);
  Wire.beginTransmission(0x3C);
  ready_ = Wire.endTransmission() == 0 && oled_.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  Serial.println(ready_ ? "[oled] ready" : "[oled] missing at 0x3C; serial fallback");
}

void OledDisplay::show(const char* line1, const char* line2) {
  Serial.printf("[display] %s | %s\n", line1, line2);
  if (!ready_) return;
  oled_.clearDisplay();
  oled_.setTextColor(SSD1306_WHITE);
  oled_.setTextSize(2);
  oled_.setCursor(0, 0);
  oled_.println(line1);
  oled_.setTextSize(1);
  oled_.setCursor(0, 42);
  for (int i = 0; line2[i] && i < 42; ++i) oled_.write(line2[i]);
  oled_.display();
}
