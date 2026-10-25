// Talks to the Flask backend on its own FreeRTOS task (core 0), so slow Wi-Fi never delays the
// control loop or the audio callback.
#pragma once

#include <stdint.h>

#include "command.h"
#include "nfc.h"
#include "shared_safety.h"

struct LinkStats {
  uint32_t sent = 0;       // requests attempted
  uint32_t ok = 0;
  uint32_t dropped = 0;    // failed requests (timeout, Wi-Fi down, bad status, bad body)
  uint32_t last_ms = 0;    // latency of the last successful request
  uint32_t max_ms = 0;
  uint32_t avg_ms = 0;     // running average of successful requests
  int last_code = 0;       // last HTTP status, or a negative HTTPClient error
  bool wifi = false;
};

class BackendClient {
 public:
  explicit BackendClient(SharedSafety& safety) : safety_(safety) {}
  // Starts Wi-Fi and the task. Returns immediately.
  void begin();
  // Thread-safe. Sent as soon as the task is free (before the next heartbeat). False if the queue is full.
  bool sendEvent(NfcEvent e);
  LinkStats stats() const;
  // Latest successfully parsed command (copy). `valid` false until the first reply.
  fido::Command latest(bool& valid) const;
  uint32_t lastOkMs() const { return lastOkMs_; }

 private:
  static void taskEntry(void* self);
  void run();
  bool post(const char* event);  // one POST /api/robot/events; updates safety + stats
  void wifiMaintain(uint32_t now);

  SharedSafety& safety_;
  void* queue_ = nullptr;   // QueueHandle_t of NfcEvent
  mutable portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  LinkStats stats_;
  fido::Command latest_;
  bool valid_ = false;
  volatile uint32_t lastOkMs_ = 0;
  uint8_t errorStreak_ = 0;
};
