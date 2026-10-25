// Serial monitor commands, for testing without hardware. Single letters act immediately (the
// PlatformIO monitor sends each key as you type it); `p <clip>` needs Enter.
#pragma once

#include "audio.h"
#include "backend_client.h"
#include "nfc.h"
#include "shared_safety.h"

class Console {
 public:
  Console(SerialNfc& nfc, SharedSafety& safety, BackendClient& backend) : nfc_(nfc), safety_(safety), backend_(backend) {}
  void poll();
  static void help();

 private:
  void handle(char c);
  void line();
  SerialNfc& nfc_;
  SharedSafety& safety_;
  BackendClient& backend_;
  char buf_[32] = {0};
  uint8_t len_ = 0;
  bool lineMode_ = false;
};

void printStatus(BackendClient& backend, SharedSafety& safety);
