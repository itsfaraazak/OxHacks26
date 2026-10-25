// Face of the robot. The OLED model is not confirmed, so the firmware depends on this interface;
// SerialDisplay just logs. Replace it with an SSD1306/SH1106 implementation when the part is known.
#pragma once
#include <Adafruit_SSD1306.h>

class Display {
 public:
  virtual ~Display() = default;
  virtual void begin() = 0;
  // `line1` is the big text, `line2` the small one (may be "").
  virtual void show(const char* line1, const char* line2) = 0;
};

class SerialDisplay : public Display {
 public:
  void begin() override {}
  void show(const char* line1, const char* line2) override;
};

class OledDisplay : public Display {
 public:
  void begin() override;
  void show(const char* line1, const char* line2) override;
 private:
  Adafruit_SSD1306 oled_{128, 64, &Wire, -1};
  bool ready_ = false;
};
