// NFC layer: reports the phone docking / leaving and the "good boy" card. The reader model is
// not confirmed, so the firmware only depends on this interface. SerialNfc simulates it from the
// serial monitor; a real reader (e.g. PN532 over I2C) implements Nfc::poll() the same way.
#pragma once

#include <stdint.h>

enum class NfcEvent : uint8_t { None, Docked, Undocked, Caught };

const char* nfcEventName(NfcEvent e);  // the backend's event string, "" for None

class Nfc {
 public:
  virtual ~Nfc() = default;
  virtual void begin() = 0;
  // Returns the next pending event, or None. Called every few milliseconds from the control loop.
  virtual NfcEvent poll() = 0;
};

// Serial console stand-in: the console calls inject() when you type d / u / c.
class SerialNfc : public Nfc {
 public:
  void begin() override {}
  NfcEvent poll() override;
  void inject(NfcEvent e);

 private:
  NfcEvent queue_[4] = {};
  uint8_t head_ = 0, count_ = 0;
};
