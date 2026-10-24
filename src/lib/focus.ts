/** Fido's local, in-memory demo; no backend or persistence is connected. */
export const DEMO_ONLY_LABEL = "Demo only — progress is not saved.";

export type FocusPhase =
  | "idle"
  | "focusing"
  | "nudge"
  | "barking"
  | "escaped"
  | "paused"
  | "completed";

export type ActiveFocusPhase = Exclude<
  FocusPhase,
  "idle" | "paused" | "completed"
>;

export interface FocusState {
  readonly task: string;
  readonly durationMinutes: number;
  readonly remainingSeconds: number;
  readonly distractions: number;
  readonly phase: FocusPhase;
  readonly previousPhase?: ActiveFocusPhase;
}

export type FocusAction =
  | { readonly type: "start" }
  | { readonly type: "tick" }
  | { readonly type: "distract" }
  | { readonly type: "refocus" }
  | { readonly type: "pause" }
  | { readonly type: "resume" }
  | { readonly type: "reset" };

/** Contract for a future asynchronous backend; this demo does not implement it. */
export interface FocusGateway {
  createSession(task: string, durationMinutes: number): Promise<FocusState>;
  transition(state: FocusState, action: FocusAction): Promise<FocusState>;
}

function isActive(phase: FocusPhase): phase is ActiveFocusPhase {
  return (
    phase === "focusing" ||
    phase === "nudge" ||
    phase === "barking" ||
    phase === "escaped"
  );
}

/**
 * Creates an idle session. Task text is preserved verbatim.
 * Duration must be a positive whole number of minutes with a safe seconds total.
 */
export function createSession(
  task: string,
  durationMinutes: number,
): FocusState {
  if (
    !Number.isSafeInteger(durationMinutes) ||
    durationMinutes <= 0 ||
    !Number.isSafeInteger(durationMinutes * 60)
  ) {
    throw new RangeError(
      "durationMinutes must be a positive whole number with a safe seconds total",
    );
  }

  return {
    task,
    durationMinutes,
    remainingSeconds: durationMinutes * 60,
    distractions: 0,
    phase: "idle",
  };
}

/**
 * Pure reducer for states created by this module.
 * Each tick consumes one second in any active phase, including escaped.
 * Inapplicable actions return the original state. Inputs are never mutated.
 */
export function transition(
  state: FocusState,
  action: FocusAction,
): FocusState {
  switch (action.type) {
    case "start":
      return state.phase === "idle" ? { ...state, phase: "focusing" } : state;

    case "tick": {
      if (!isActive(state.phase)) return state;
      const remainingSeconds = Math.max(0, state.remainingSeconds - 1);
      return {
        ...state,
        remainingSeconds,
        phase: remainingSeconds === 0 ? "completed" : state.phase,
      };
    }

    case "distract": {
      if (!isActive(state.phase)) return state;
      const nextPhase: Record<ActiveFocusPhase, ActiveFocusPhase> = {
        focusing: "nudge",
        nudge: "barking",
        barking: "escaped",
        escaped: "escaped",
      };
      return {
        ...state,
        distractions: state.distractions + 1,
        phase: nextPhase[state.phase],
      };
    }

    case "refocus":
      return isActive(state.phase) && state.phase !== "focusing"
        ? { ...state, phase: "focusing" }
        : state;

    case "pause":
      return isActive(state.phase)
        ? { ...state, phase: "paused", previousPhase: state.phase }
        : state;

    case "resume": {
      if (state.phase !== "paused" || state.previousPhase === undefined) {
        return state;
      }
      const { previousPhase, ...session } = state;
      return { ...session, phase: previousPhase };
    }

    case "reset":
      return createSession(state.task, state.durationMinutes);

    default: {
      const exhaustive: never = action;
      return exhaustive;
    }
  }
}
