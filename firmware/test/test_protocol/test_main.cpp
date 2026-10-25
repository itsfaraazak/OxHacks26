#include <string.h>
#include <unity.h>

#include "backoff.h"
#include "command.h"
#include "speech_gate.h"

using namespace fido;

void setUp() {}
void tearDown() {}

static bool parse(const char* json, Command& c) { return parseCommand(json, strlen(json), c); }

void test_parse_full_reply() {
  Command c;
  TEST_ASSERT_TRUE(parse(
      "{\"mode\":\"bark\",\"motion_enabled\":true,\"task\":\"Ship it\",\"remaining_seconds\":12.5,"
      "\"reason\":\"x\",\"version\":3,\"speech\":{\"seq\":7,\"cue\":\"nudge\",\"clip\":\"nudge-2\","
      "\"text\":\"hi\",\"at\":1.5,\"url\":\"/api/audio/nudge-2.mp3\"}}",
      c));
  TEST_ASSERT_EQUAL(Mode::Bark, c.mode);
  TEST_ASSERT_TRUE(c.motion_enabled);
  TEST_ASSERT_EQUAL_STRING("Ship it", c.task);
  TEST_ASSERT_TRUE(c.speech.present);
  TEST_ASSERT_EQUAL_UINT32(7, c.speech.seq);
  TEST_ASSERT_EQUAL_STRING("nudge-2", c.speech.clip);
}

void test_null_speech_and_null_task() {
  Command c;
  TEST_ASSERT_TRUE(parse("{\"mode\":\"watch\",\"motion_enabled\":false,\"task\":null,\"speech\":null}", c));
  TEST_ASSERT_EQUAL(Mode::Watch, c.mode);
  TEST_ASSERT_FALSE(c.speech.present);
  TEST_ASSERT_EQUAL_STRING("", c.task);
}

void test_every_backend_mode_parses() {
  const char* names[] = {"sleep", "beckon_dock", "watch", "nudge", "bark", "run", "rest", "celebrate"};
  for (const char* n : names) {
    TEST_ASSERT_NOT_EQUAL_MESSAGE(Mode::Unknown, parseMode(n), n);
    TEST_ASSERT_EQUAL_STRING(n, modeName(parseMode(n)));
  }
  TEST_ASSERT_EQUAL(Mode::Unknown, parseMode("fly"));
  TEST_ASSERT_EQUAL(Mode::Unknown, parseMode(nullptr));
}

void test_motion_enabled_must_be_literal_true() {
  Command c;
  TEST_ASSERT_TRUE(parse("{\"mode\":\"run\"}", c));
  TEST_ASSERT_FALSE(c.motion_enabled);  // missing => off
  TEST_ASSERT_TRUE(parse("{\"mode\":\"run\",\"motion_enabled\":\"true\"}", c));
  TEST_ASSERT_FALSE(c.motion_enabled);
  TEST_ASSERT_TRUE(parse("{\"mode\":\"run\",\"motion_enabled\":1}", c));
  TEST_ASSERT_FALSE(c.motion_enabled);
}

void test_garbage_is_rejected_and_leaves_command_untouched() {
  Command c;
  c.mode = Mode::Watch;
  TEST_ASSERT_FALSE(parse("<html>502 Bad Gateway</html>", c));
  TEST_ASSERT_FALSE(parse("", c));
  TEST_ASSERT_FALSE(parse("{\"error\":\"nope\"}", c));
  TEST_ASSERT_FALSE(parse("{\"mode\":5}", c));
  TEST_ASSERT_EQUAL(Mode::Watch, c.mode);
}

void test_long_clip_name_is_truncated_not_overflowed() {
  Command c;
  TEST_ASSERT_TRUE(parse("{\"mode\":\"watch\",\"speech\":{\"seq\":1,\"clip\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}}", c));
  TEST_ASSERT_EQUAL(23, strlen(c.speech.clip));
}

static Speech sp(uint32_t seq, const char* clip) {
  Speech s;
  s.present = true;
  s.seq = seq;
  strncpy(s.clip, clip, sizeof s.clip - 1);
  return s;
}

void test_first_reply_is_recorded_not_played() {
  SpeechGate g;
  TEST_ASSERT_FALSE(g.shouldPlay(sp(7, "nudge-1")));
  TEST_ASSERT_FALSE(g.shouldPlay(sp(7, "nudge-1")));
  TEST_ASSERT_TRUE(g.shouldPlay(sp(8, "bark-1")));
}

void test_each_seq_plays_once() {
  SpeechGate g;
  Speech none;
  TEST_ASSERT_FALSE(g.shouldPlay(none));  // first reply: speech null
  TEST_ASSERT_TRUE(g.shouldPlay(sp(1, "dock-1")));
  TEST_ASSERT_FALSE(g.shouldPlay(sp(1, "dock-1")));
  TEST_ASSERT_TRUE(g.shouldPlay(sp(2, "start-1")));
  TEST_ASSERT_TRUE(g.shouldPlay(sp(5, "run-1")));  // gaps are fine; play the latest
  TEST_ASSERT_FALSE(g.shouldPlay(sp(4, "bark-1")));  // older is ignored
}

void test_backend_restart_does_not_replay_or_stick() {
  SpeechGate g;
  g.shouldPlay(sp(10, "nudge-1"));
  TEST_ASSERT_FALSE(g.shouldPlay(sp(1, "dock-1")));  // seq went down: record only
  TEST_ASSERT_TRUE(g.shouldPlay(sp(2, "start-1")));
}

void test_resync_after_outage_records_without_playing() {
  SpeechGate g;
  g.shouldPlay(sp(3, "a-1"));
  g.resync();
  TEST_ASSERT_FALSE(g.shouldPlay(sp(9, "run-1")));
  TEST_ASSERT_TRUE(g.shouldPlay(sp(10, "run-2")));
}

void test_empty_clip_name_is_not_played() {
  SpeechGate g;
  g.shouldPlay(sp(1, "a-1"));
  TEST_ASSERT_FALSE(g.shouldPlay(sp(2, "")));
}

void test_backoff_doubles_to_cap_and_resets() {
  Backoff b(1000, 8000);
  TEST_ASSERT_TRUE(b.due(0));
  b.failed(0);
  TEST_ASSERT_FALSE(b.due(999));
  TEST_ASSERT_TRUE(b.due(1000));
  b.failed(1000);
  TEST_ASSERT_FALSE(b.due(2999));
  TEST_ASSERT_TRUE(b.due(3000));
  b.failed(3000);  // 4000
  b.failed(7000);  // 8000
  b.failed(15000);
  TEST_ASSERT_EQUAL_UINT32(8000, b.currentDelayMs());
  b.reset(30000);
  TEST_ASSERT_EQUAL_UINT32(1000, b.currentDelayMs());
  TEST_ASSERT_TRUE(b.due(30000));
}

void test_backoff_rollover() {
  Backoff b(1000, 8000);
  b.failed(0xFFFFFF00u);
  TEST_ASSERT_FALSE(b.due(0xFFFFFF00u + 500));
  TEST_ASSERT_TRUE(b.due(0xFFFFFF00u + 1000));  // wrapped past zero
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_parse_full_reply);
  RUN_TEST(test_null_speech_and_null_task);
  RUN_TEST(test_every_backend_mode_parses);
  RUN_TEST(test_motion_enabled_must_be_literal_true);
  RUN_TEST(test_garbage_is_rejected_and_leaves_command_untouched);
  RUN_TEST(test_long_clip_name_is_truncated_not_overflowed);
  RUN_TEST(test_first_reply_is_recorded_not_played);
  RUN_TEST(test_each_seq_plays_once);
  RUN_TEST(test_backend_restart_does_not_replay_or_stick);
  RUN_TEST(test_resync_after_outage_records_without_playing);
  RUN_TEST(test_empty_clip_name_is_not_played);
  RUN_TEST(test_backoff_doubles_to_cap_and_resets);
  RUN_TEST(test_backoff_rollover);
  return UNITY_END();
}
