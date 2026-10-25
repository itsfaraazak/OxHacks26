#include "nfc.h"

const char* nfcEventName(NfcEvent e) {
  switch (e) {
    case NfcEvent::Docked: return "docked";
    case NfcEvent::Undocked: return "undocked";
    case NfcEvent::Caught: return "caught";
    case NfcEvent::None: break;
  }
  return "";
}

void SerialNfc::inject(NfcEvent e) {
  if (e == NfcEvent::None || count_ == 4) return;
  queue_[(head_ + count_) % 4] = e;
  ++count_;
}

NfcEvent SerialNfc::poll() {
  if (count_ == 0) return NfcEvent::None;
  NfcEvent e = queue_[head_];
  head_ = (head_ + 1) % 4;
  --count_;
  return e;
}
