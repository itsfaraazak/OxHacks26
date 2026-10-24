import assert from "node:assert/strict";
import test from "node:test";
import {
  createSession,
  DEMO_ONLY_LABEL,
  transition,
  type ActiveFocusPhase,
  type FocusAction,
  type FocusGateway,
  type FocusState,
} from "./focus";

const activePhases: readonly ActiveFocusPhase[] = [
  "focusing",
  "nudge",
  "barking",
  "escaped",
];

const actions: readonly FocusAction[] = [
  { type: "start" },
  { type: "tick" },
  { type: "distract" },
  { type: "refocus" },
  { type: "pause" },
  { type: "resume" },
  { type: "reset" },
];

function focusing(): FocusState {
  return transition(createSession("Write a draft", 1), { type: "start" });
}

test("creates an idle session and preserves task text verbatim", () => {
  assert.deepEqual(createSession("  Write a draft  ", 25), {
    task: "  Write a draft  ",
    durationMinutes: 25,
    remainingSeconds: 1500,
    distractions: 0,
    phase: "idle",
  });
  assert.equal(createSession("", 1).task, "");
  assert.match(DEMO_ONLY_LABEL, /demo only/i);
});

test("rejects invalid durations", () => {
  for (const duration of [
    0,
    -1,
    0.5,
    NaN,
    Infinity,
    -Infinity,
    Number.MAX_SAFE_INTEGER,
  ]) {
    assert.throws(() => createSession("Task", duration), RangeError);
  }
});

test("start only applies to idle sessions", () => {
  const idle = createSession("Task", 1);
  assert.equal(transition(idle, { type: "start" }).phase, "focusing");
  for (const phase of [...activePhases, "paused", "completed"] as const) {
    const state: FocusState = { ...idle, phase };
    assert.equal(transition(state, { type: "start" }), state);
  }
});

test("distractions escalate, saturate at escaped, and retain the count on refocus", () => {
  let state = focusing();
  for (const [index, phase] of [
    "nudge",
    "barking",
    "escaped",
    "escaped",
  ].entries()) {
    state = transition(state, { type: "distract" });
    assert.equal(state.phase, phase);
    assert.equal(state.distractions, index + 1);
    assert.equal(state.remainingSeconds, 60);
  }
  state = transition(state, { type: "refocus" });
  assert.equal(state.phase, "focusing");
  assert.equal(state.distractions, 4);
  assert.equal(transition(state, { type: "distract" }).phase, "nudge");
});

test("refocus works from every escalated phase without changing time or count", () => {
  for (const phase of activePhases) {
    const state: FocusState = { ...focusing(), phase, distractions: 3 };
    assert.deepEqual(transition(state, { type: "refocus" }), {
      ...state,
      phase: "focusing",
    });
  }
});

test("every active phase ticks and completes exactly at zero", () => {
  for (const phase of activePhases) {
    const state: FocusState = { ...focusing(), phase, remainingSeconds: 2 };
    const ticked = transition(state, { type: "tick" });
    assert.equal(ticked.remainingSeconds, 1);
    assert.equal(ticked.phase, phase);
    const completed = transition(ticked, { type: "tick" });
    assert.equal(completed.remainingSeconds, 0);
    assert.equal(completed.phase, "completed");
    assert.equal(transition(completed, { type: "tick" }), completed);
  }
});

test("pause freezes every active phase and resume restores it without stale metadata", () => {
  for (const phase of activePhases) {
    const state: FocusState = { ...focusing(), phase, distractions: 2 };
    const paused = transition(state, { type: "pause" });
    assert.deepEqual(paused, {
      ...state,
      phase: "paused",
      previousPhase: phase,
    });
    for (const type of ["tick", "distract", "refocus", "pause", "start"] as const) {
      assert.equal(transition(paused, { type }), paused);
    }
    const resumed = transition(paused, { type: "resume" });
    assert.deepEqual(resumed, state);
    assert.equal("previousPhase" in resumed, false);
    assert.equal(transition(resumed, { type: "tick" }).remainingSeconds, 59);
  }
});

test("idle and completed ignore all actions except start (idle) and reset", () => {
  const idle = createSession("Task", 1);
  const completed: FocusState = {
    ...idle,
    remainingSeconds: 0,
    phase: "completed",
  };
  for (const state of [idle, completed]) {
    for (const action of actions) {
      if (action.type === "reset") continue;
      if (state.phase === "idle" && action.type === "start") continue;
      assert.equal(transition(state, action), state);
    }
  }
});

test("resume is a no-op outside a valid paused session", () => {
  for (const phase of [...activePhases, "idle", "completed", "paused"] as const) {
    const state: FocusState = { ...focusing(), phase };
    assert.equal(transition(state, { type: "resume" }), state);
  }
});

test("reset restores the configured idle session from every phase", () => {
  const initial = createSession("Keep this task", 25);
  for (const phase of [
    "idle",
    ...activePhases,
    "paused",
    "completed",
  ] as const) {
    const state: FocusState = {
      ...initial,
      remainingSeconds: 7,
      distractions: 9,
      phase,
      ...(phase === "paused" ? { previousPhase: "barking" as const } : {}),
    };
    assert.deepEqual(transition(state, { type: "reset" }), initial);
  }
});

test("transitions never mutate their inputs", () => {
  for (const phase of ["idle", ...activePhases, "paused", "completed"] as const) {
    const state = Object.freeze({
      ...focusing(),
      phase,
      ...(phase === "paused" ? { previousPhase: "nudge" as const } : {}),
    });
    const before = { ...state };
    for (const action of actions) {
      transition(state, Object.freeze(action));
      assert.deepEqual(state, before);
    }
  }
});

test("the future gateway contract can wrap the pure demo asynchronously", async () => {
  const gateway: FocusGateway = {
    createSession: async (task, durationMinutes) =>
      createSession(task, durationMinutes),
    transition: async (state, action) => transition(state, action),
  };
  const state = await gateway.createSession("Task", 1);
  assert.deepEqual(
    await gateway.transition(state, { type: "start" }),
    focusingWithTask("Task"),
  );
});

function focusingWithTask(task: string): FocusState {
  return { ...focusing(), task };
}
