// Face of the robot. The OLED model is not confirmed, so the firmware depends on this interface;
// SerialDisplay just logs. Replace it with an SSD1306/SH1106 implementation when the part is known.
#pragma once

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
