#include "console.h"

#include <Arduino.h>
#include <WiFi.h>
#include <esp_system.h>

#include "settings.h"

void Console::help() {
  Serial.println(
      "\n[console] d = phone docked   u = phone undocked   c = caught (good boy card)\n"
      "          x = EMERGENCY STOP (latched)   r = release stop   s = status\n"
      "          p <clip> + Enter = play a clip now (e.g. p nudge-1)   l = list clips   h = help");
}

void Console::poll() {
  while (Serial.available() > 0) {
    char c = static_cast<char>(Serial.read());
    if (lineMode_) {
      if (c == '\n' || c == '\r') {
        line();
        lineMode_ = false;
        len_ = 0;
      } else if (len_ < sizeof buf_ - 1) {
        buf_[len_++] = c;
        buf_[len_] = '\0';
      }
    } else {
      handle(c);
    }
  }
}

void Console::handle(char c) {
  switch (c) {
    case 'd': Serial.println("[console] simulate: docked"); nfc_.inject(NfcEvent::Docked); break;
    case 'u': Serial.println("[console] simulate: undocked"); nfc_.inject(NfcEvent::Undocked); break;
    case 'c': Serial.println("[console] simulate: caught"); nfc_.inject(NfcEvent::Caught); break;
    case 'x': safety_.setEstop(true); Serial.println("[console] EMERGENCY STOP latched (r releases)"); break;
    case 'r': safety_.setEstop(false); Serial.println("[console] stop released"); break;
    case 's': printStatus(backend_, safety_); break;
    case 'l': audio::listClips(); break;
    case 'h': case '?': help(); break;
    case 'p': lineMode_ = true; len_ = 0; buf_[0] = '\0'; Serial.print("[console] play clip: "); break;
    default: break;  // ignore CR/LF and anything else
  }
}

void Console::line() {
  Serial.println();
  char* name = buf_;
  while (*name == ' ') ++name;
  if (*name != '\0') audio::request(name);
}

void printStatus(BackendClient& backend, SharedSafety& safety) {
  LinkStats l = backend.stats();
  audio::Stats a = audio::stats();
  bool valid;
  fido::Command cmd = backend.latest(valid);
  Serial.printf(
      "[status] up %lus | wifi %s rssi %d | backend ok %u dropped %u last %dms avg %ums max %ums code %d | "
      "mode %s motion_enabled %d | motion %s%s | speaker %s%s | clips %u played %u dropped %u unknown %u | "
      "heap %u min %u\n",
      static_cast<unsigned long>(millis() / 1000), l.wifi ? "up" : "DOWN", l.wifi ? WiFi.RSSI() : 0,
      static_cast<unsigned>(l.ok), static_cast<unsigned>(l.dropped), static_cast<int>(l.last_ms),
      static_cast<unsigned>(l.avg_ms), static_cast<unsigned>(l.max_ms), l.last_code,
      valid ? fido::modeName(cmd.mode) : "(none yet)", cmd.motion_enabled ? 1 : 0,
      ENABLE_MOTION ? "COMPILED-IN" : "compiled-out", safety.estop() ? " ESTOP" : "",
      audio::speakerName(audio::speaker()), audio::playing() ? " (playing)" : "",
      static_cast<unsigned>(audio::clipCount()), static_cast<unsigned>(a.clips_played),
      static_cast<unsigned>(a.clips_dropped), static_cast<unsigned>(a.clips_unknown),
      static_cast<unsigned>(ESP.getFreeHeap()), static_cast<unsigned>(ESP.getMinFreeHeap()));
}
