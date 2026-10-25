// Motor-safety rules. These are the non-negotiable ones; if one of these fails, do not enable motion.
#include <unity.h>

#include "motion_planner.h"
#include "motor_safety.h"
#include "obstacle.h"

using namespace fido;

void setUp() {}
void tearDown() {}

namespace {
SafetyConfig cfg() {
  SafetyConfig c;
  c.motion_compiled = true;
  c.watchdog_ms = 3000;
  c.max_escape_ms = 8000;
  c.error_streak_limit = 2;
  c.max_speed = 0.35f;
  c.wiggle_speed = 0.25f;
  return c;
}
const MotorCommand kFull{1.0f, 1.0f};

bool stopped(const MotorCommand& m) { return m.left == 0 && m.right == 0; }

StopReason reasonAt(const MotorSafety& s, uint32_t now, MotorCommand intent = kFull) {
  StopReason r;
  s.gate(now, intent, &r);
  return r;
}
}  // namespace

void test_stopped_before_any_reply() {
  MotorSafety s(cfg());
  TEST_ASSERT_TRUE(stopped(s.gate(0, kFull)));
  TEST_ASSERT_EQUAL(StopReason::NoReply, reasonAt(s, 100));
}

void test_not_compiled_never_moves() {
  SafetyConfig c = cfg();
  c.motion_compiled = false;
  MotorSafety s(c);
  s.onReply(0, Mode::Run, true);
  TEST_ASSERT_TRUE(stopped(s.gate(10, kFull)));
  TEST_ASSERT_EQUAL(StopReason::NotCompiled, reasonAt(s, 10));
}

void test_default_config_does_not_move() {
  SafetyConfig c;  // defaults: motion_compiled = false
  MotorSafety s(c);
  s.onReply(0, Mode::Run, true);
  TEST_ASSERT_TRUE(stopped(s.gate(10, kFull)));
}

void test_run_moves_at_capped_speed() {
  MotorSafety s(cfg());
  s.onReply(1000, Mode::Run, true);
  MotorCommand m = s.gate(1100, kFull);
  TEST_ASSERT_EQUAL_FLOAT(0.35f, m.left);
  TEST_ASSERT_EQUAL_FLOAT(0.35f, m.right);
  m = s.gate(1100, {-1.0f, 0.5f});
  TEST_ASSERT_EQUAL_FLOAT(-0.35f, m.left);
  TEST_ASSERT_EQUAL_FLOAT(0.175f, m.right);
}

void test_intent_is_clamped_and_nan_is_zero() {
  MotorSafety s(cfg());
  s.onReply(0, Mode::Run, true);
  MotorCommand m = s.gate(10, {5.0f, -9.0f});
  TEST_ASSERT_EQUAL_FLOAT(0.35f, m.left);
  TEST_ASSERT_EQUAL_FLOAT(-0.35f, m.right);
  float nan = 0.0f / 0.0f;
  m = s.gate(10, {nan, nan});
  TEST_ASSERT_TRUE(stopped(m));
}

void test_speed_cap_cannot_be_exceeded_by_wiggle_config() {
  SafetyConfig c = cfg();
  c.wiggle_speed = 0.9f;  // misconfigured above max_speed
  MotorSafety s(c);
  s.onReply(0, Mode::Nudge, true);
  TEST_ASSERT_EQUAL_FLOAT(0.35f, s.gate(10, kFull).left);
}

void test_motion_disabled_stops_immediately() {
  MotorSafety s(cfg());
  s.onReply(1000, Mode::Run, true);
  TEST_ASSERT_FALSE(stopped(s.gate(1100, kFull)));
  s.onReply(1200, Mode::Run, false);  // e-stop pressed in the app
  TEST_ASSERT_TRUE(stopped(s.gate(1200, kFull)));
  TEST_ASSERT_EQUAL(StopReason::MotionDisabled, reasonAt(s, 1200));
}

void test_watchdog_stops_after_3s_without_reply() {
  MotorSafety s(cfg());
  s.onReply(1000, Mode::Run, true);
  TEST_ASSERT_FALSE(stopped(s.gate(3999, kFull)));  // 2999 ms old
  TEST_ASSERT_TRUE(stopped(s.gate(4000, kFull)));   // exactly 3000 ms old
  TEST_ASSERT_EQUAL(StopReason::Watchdog, reasonAt(s, 4000));
  s.onReply(4500, Mode::Run, true);  // link is back
  TEST_ASSERT_FALSE(stopped(s.gate(4600, kFull)));
}

void test_watchdog_survives_millis_rollover() {
  MotorSafety s(cfg());
  uint32_t t0 = 0xFFFFFF00u;
  s.onReply(t0, Mode::Run, true);
  TEST_ASSERT_FALSE(stopped(s.gate(t0 + 1000, kFull)));  // wrapped past zero, 1 s old
  TEST_ASSERT_TRUE(stopped(s.gate(t0 + 3000, kFull)));
}

void test_http_error_streak_stops_before_watchdog() {
  MotorSafety s(cfg());
  s.onReply(1000, Mode::Run, true);
  s.onHttpError(1200);
  TEST_ASSERT_FALSE(stopped(s.gate(1250, kFull)));  // one error is tolerated
  s.onHttpError(1500);
  TEST_ASSERT_TRUE(stopped(s.gate(1500, kFull)));
  TEST_ASSERT_EQUAL(StopReason::HttpErrors, reasonAt(s, 1500));
  s.onReply(2000, Mode::Run, true);  // success clears the streak
  TEST_ASSERT_FALSE(stopped(s.gate(2000, kFull)));
}

void test_non_run_modes_do_not_drive() {
  const Mode still[] = {Mode::Unknown, Mode::Rest, Mode::Celebrate};
  for (Mode m : still) {
    MotorSafety s(cfg());
    s.onReply(0, m, true);
    TEST_ASSERT_TRUE_MESSAGE(stopped(s.gate(10, kFull)), modeName(m));
  }
}

void test_leaving_run_stops_immediately() {
  MotorSafety s(cfg());
  s.onReply(0, Mode::Run, true);
  TEST_ASSERT_FALSE(stopped(s.gate(100, kFull)));
  s.onReply(1000, Mode::Watch, true);  // user caught the dog / refocused
  TEST_ASSERT_TRUE(stopped(s.gate(1000, planMotion(Mode::Watch, 0))));
}

void test_escape_cut_off_after_max_escape_ms() {
  MotorSafety s(cfg());
  // Replies keep arriving every second, all `run`, so only the escape timer can stop it.
  for (uint32_t t = 0; t <= 7000; t += 1000) s.onReply(t, Mode::Run, true);
  TEST_ASSERT_FALSE(stopped(s.gate(7999, kFull)));
  s.onReply(8000, Mode::Run, true);
  TEST_ASSERT_TRUE(stopped(s.gate(8000, kFull)));
  TEST_ASSERT_EQUAL(StopReason::EscapeTimeout, reasonAt(s, 8000));
  for (uint32_t t = 9000; t < 60000; t += 1000) {
    s.onReply(t, Mode::Run, true);
    TEST_ASSERT_TRUE(stopped(s.gate(t, kFull)));  // stays stopped for the rest of this run
  }
}

void test_new_run_gets_a_fresh_escape_timer() {
  MotorSafety s(cfg());
  for (uint32_t t = 0; t <= 9000; t += 1000) s.onReply(t, Mode::Run, true);
  TEST_ASSERT_TRUE(stopped(s.gate(9000, kFull)));
  s.onReply(10000, Mode::Watch, true);  // caught
  s.onReply(20000, Mode::Run, true);    // distracted again
  TEST_ASSERT_FALSE(stopped(s.gate(20100, kFull)));
  s.onReply(28000, Mode::Run, true);
  TEST_ASSERT_TRUE(stopped(s.gate(28000, kFull)));
}

void test_estop_latches() {
  MotorSafety s(cfg());
  s.onReply(0, Mode::Run, true);
  s.setEstop(true);
  TEST_ASSERT_TRUE(stopped(s.gate(10, kFull)));
  s.onReply(500, Mode::Run, true);
  TEST_ASSERT_TRUE(stopped(s.gate(500, kFull)));
  s.setEstop(false);
  TEST_ASSERT_FALSE(stopped(s.gate(500, kFull)));
}

void test_nudge_wiggle_is_tiny_and_balanced() {
  MotorSafety s(cfg());
  s.onReply(0, Mode::Nudge, true);
  float net_left = 0, net_right = 0, peak = 0;
  uint32_t moving_ms = 0;
  for (uint32_t t = 0; t < 1500; ++t) {
    MotorCommand m = s.gate(t, planMotion(Mode::Nudge, t));
    net_left += m.left;
    net_right += m.right;
    float a = m.left < 0 ? -m.left : m.left;
    if (a > peak) peak = a;
    if (!stopped(m)) ++moving_ms;
  }
  TEST_ASSERT_TRUE(peak <= 0.25f + 1e-6f);
  TEST_ASSERT_EQUAL(600, moving_ms);
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, 0.0f, net_left);
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, 0.0f, net_right);
  TEST_ASSERT_TRUE(stopped(planMotion(Mode::Nudge, 1500))); // Not repeated.
  TEST_ASSERT_TRUE(stopped(planMotion(Mode::Watch, 0)));
  TEST_ASSERT_TRUE(stopped(planMotion(Mode::Bark, 350)));
}

void test_obstacle_turn_hysteresis_and_missing_sensor() {
  ObstacleGate o;
  TEST_ASSERT_TRUE(stopped(o.apply(Mode::Run, kFull, false, 100)));
  TEST_ASSERT_TRUE(stopped(o.apply(Mode::Run, kFull, true, 0)));
  TEST_ASSERT_EQUAL_FLOAT(1, o.apply(Mode::Run, kFull, true, 35, 0, 0).left);
  MotorCommand m = o.apply(Mode::Run, kFull, true, 34, 1, 1);
  TEST_ASSERT_EQUAL_FLOAT(-1, m.left);
  TEST_ASSERT_EQUAL_FLOAT(1, m.right);
  TEST_ASSERT_EQUAL_FLOAT(-1, o.apply(Mode::Run, kFull, true, 23, 2, 2).left);
  TEST_ASSERT_EQUAL_FLOAT(-1, o.apply(Mode::Run, kFull, true, 45, 3, 3).left);
  // Re-reading one sample cannot count as three confirmations.
  TEST_ASSERT_EQUAL_FLOAT(-1, o.apply(Mode::Run, kFull, true, 45, 3, 4).left);
  TEST_ASSERT_EQUAL_FLOAT(-1, o.apply(Mode::Run, kFull, true, 45, 4, 5).left);
  TEST_ASSERT_EQUAL_FLOAT(1, o.apply(Mode::Run, kFull, true, 45, 5, 6).left);
  TEST_ASSERT_TRUE(stopped(o.apply(Mode::Nudge, kFull, true, 19)));
  TEST_ASSERT_EQUAL_FLOAT(-1, o.apply(Mode::Nudge, {-1, -1}, true, 19).left);
}

void test_obstacle_cannot_bypass_backend_stop() {
  ObstacleGate o;
  MotorSafety s(cfg());
  s.onReply(0, Mode::Run, false);
  TEST_ASSERT_TRUE(stopped(s.gate(10, o.apply(Mode::Run, kFull, true, 10))));
  s.onReply(20, Mode::Run, true);
  TEST_ASSERT_TRUE(stopped(s.gate(3020, o.apply(Mode::Run, kFull, true, 10))));
}

void test_turn_timeout_latches_until_leaving_run() {
  ObstacleGate o;
  TEST_ASSERT_EQUAL_FLOAT(-1, o.apply(Mode::Run, kFull, true, 10, 100, 100).left);
  TEST_ASSERT_EQUAL_FLOAT(-1, o.apply(Mode::Run, kFull, true, 10, 5000, 5000).left);
  TEST_ASSERT_TRUE(stopped(o.apply(Mode::Run, kFull, true, 10, 5100, 5100)));
  TEST_ASSERT_TRUE(stopped(o.apply(Mode::Run, kFull, true, 100, 5200, 5200)));
  o.apply(Mode::Watch, {}, false, 0, 0, 5300);
  TEST_ASSERT_EQUAL_FLOAT(1, o.apply(Mode::Run, kFull, true, 100, 5400, 5400).left);
}

void test_bark_shake_is_once_and_gated() {
  SafetyConfig c = cfg();
  c.max_speed = c.wiggle_speed = 1;
  MotorSafety s(c);
  s.onReply(0, Mode::Bark, true);
  TEST_ASSERT_EQUAL_FLOAT(1, s.gate(100, planMotion(Mode::Bark, 100)).left);
  TEST_ASSERT_EQUAL_FLOAT(-1, s.gate(250, planMotion(Mode::Bark, 250)).left);
  TEST_ASSERT_TRUE(stopped(s.gate(180, planMotion(Mode::Bark, 180))));
  TEST_ASSERT_TRUE(stopped(s.gate(500, planMotion(Mode::Bark, 500))));
  s.setEstop(true);
  TEST_ASSERT_TRUE(stopped(s.gate(250, planMotion(Mode::Bark, 250))));
}

void test_time_based_http_grace_and_physical_stop() {
  SafetyConfig c;
  c.motion_compiled = true;
  c.max_speed = 1;
  MotorSafety s(c);
  s.onReply(1000, Mode::Run, true);
  for (uint32_t t = 1100; t < 6000; t += 100) {
    s.onHttpError(t);
    TEST_ASSERT_FALSE(stopped(s.gate(t, kFull)));
  }
  TEST_ASSERT_TRUE(stopped(s.gate(6000, kFull)));
  TEST_ASSERT_EQUAL(StopReason::Watchdog, reasonAt(s, 6000));
  s.onReply(6100, Mode::Run, true);
  TEST_ASSERT_FALSE(stopped(s.gate(6100, kFull)));
  s.setEstop(true);
  TEST_ASSERT_TRUE(stopped(s.gate(6101, kFull)));
}

void test_presence_is_short_balanced_and_cancellable() {
  const Mode modes[] = {Mode::Sleep, Mode::BeckonDock, Mode::Watch};
  for (Mode mode : modes) {
    float netLeft = 0, netRight = 0;
    uint32_t moving = 0;
    for (uint32_t t = 0; t < 50000; ++t) {
      MotorCommand m = planMotion(mode, t);
      netLeft += m.left;
      netRight += m.right;
      if (!stopped(m)) ++moving;
    }
    TEST_ASSERT_TRUE(moving > 0 && moving < 1500);
    TEST_ASSERT_EQUAL_FLOAT(0, netLeft);
    TEST_ASSERT_EQUAL_FLOAT(0, netRight);
    MotorSafety s(cfg());
    s.onReply(0, mode, false);
    TEST_ASSERT_TRUE(stopped(s.gate(10, {1, -1})));
    s.onReply(0, mode, true);
    TEST_ASSERT_TRUE(stopped(s.gate(3000, {1, -1})));
    s.onReply(4000, Mode::Rest, true);
    TEST_ASSERT_TRUE(stopped(s.gate(4001, {1, -1})));
  }
}

void test_reply_newer_than_control_snapshot_does_not_fake_an_outage() {
  MotorSafety s(cfg());
  s.onReply(1001, Mode::Run, true); // Backend task updates after loop captured 1000.
  TEST_ASSERT_FALSE(stopped(s.gate(1000, kFull)));
  TEST_ASSERT_EQUAL_UINT32(0, s.msInMode(1000));
  TEST_ASSERT_TRUE(stopped(s.gate(4001, kFull))); // Real expiry still works.
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_stopped_before_any_reply);
  RUN_TEST(test_not_compiled_never_moves);
  RUN_TEST(test_default_config_does_not_move);
  RUN_TEST(test_run_moves_at_capped_speed);
  RUN_TEST(test_intent_is_clamped_and_nan_is_zero);
  RUN_TEST(test_speed_cap_cannot_be_exceeded_by_wiggle_config);
  RUN_TEST(test_motion_disabled_stops_immediately);
  RUN_TEST(test_watchdog_stops_after_3s_without_reply);
  RUN_TEST(test_watchdog_survives_millis_rollover);
  RUN_TEST(test_http_error_streak_stops_before_watchdog);
  RUN_TEST(test_non_run_modes_do_not_drive);
  RUN_TEST(test_leaving_run_stops_immediately);
  RUN_TEST(test_escape_cut_off_after_max_escape_ms);
  RUN_TEST(test_new_run_gets_a_fresh_escape_timer);
  RUN_TEST(test_estop_latches);
  RUN_TEST(test_nudge_wiggle_is_tiny_and_balanced);
  RUN_TEST(test_obstacle_turn_hysteresis_and_missing_sensor);
  RUN_TEST(test_obstacle_cannot_bypass_backend_stop);
  RUN_TEST(test_turn_timeout_latches_until_leaving_run);
  RUN_TEST(test_bark_shake_is_once_and_gated);
  RUN_TEST(test_time_based_http_grace_and_physical_stop);
  RUN_TEST(test_presence_is_short_balanced_and_cancellable);
  RUN_TEST(test_reply_newer_than_control_snapshot_does_not_fake_an_outage);
  return UNITY_END();
}
