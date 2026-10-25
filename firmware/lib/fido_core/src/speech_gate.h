// "Play each speech.seq at most once" (docs/audio.md).
#pragma once

#include "command.h"

namespace fido {

class SpeechGate {
 public:
  // True if the clip in `speech` should be played now.
  //  * first reply after boot / after resync(): record seq, do not play (no replay of old speech)
  //  * seq unchanged: do not play
  //  * seq went DOWN: the backend restarted; record it, do not play
  bool shouldPlay(const Speech& speech) {
    uint32_t seq = speech.present ? speech.seq : 0;
    if (!synced_) {
      synced_ = true;
      last_ = seq;
      return false;
    }
    if (seq <= last_) {
      last_ = seq;
      return false;
    }
    last_ = seq;
    return speech.clip[0] != '\0';
  }

  // Call after the backend was unreachable: the next reply is "first" again.
  void resync() { synced_ = false; }

 private:
  bool synced_ = false;
  uint32_t last_ = 0;
};

}  // namespace fido
