# Frontend integration with the Fido backend

The Flask backend in [`backend/`](../backend/README.md) is the single source of
truth for the focus session. The frontend (Next.js, `frontend` branch) should
**display backend state and send commands**; it must not run its own session
state machine or timer. Interface and visual design stay with the frontend.

API reference: [backend/README.md#api](../backend/README.md#api).

## Required changes

1. **Connect to the backend.**
   - Base URL from `NEXT_PUBLIC_FIDO_API` (default `http://localhost:5000`).
     When the page is opened from another device on the LAN, default to
     `http://<page hostname>:5000` so phones work without configuration.
   - Subscribe with `new EventSource(`${API}/api/events`)`; each `state`
     event carries a full snapshot. Keep the latest snapshot in React state and
     render from it. `EventSource` reconnects automatically; show a quiet
     "Reconnecting…" indicator while `readyState !== OPEN`.
2. **Replace the local state machine.**
   - Delete or retire `src/lib/focus.ts` transitions and their tests; replace
     them with a thin typed client (`src/lib/api.ts`) mirroring the snapshot
     shape below.
   - Commands: `POST /api/session {task, duration_minutes}`, `POST
     /api/session/pause|resume|caught|end`, `PATCH /api/settings`. On HTTP 409
     the body contains `state`; apply it. On 400, show `error` in the toast.
   - The countdown: display `session.remaining_seconds`, and while
     `session.clock_running` interpolate locally between events using
     `server_time` (no authoritative local timer).
3. **New phase `awaiting_phone`.** Shown after starting until the phone is
   docked: "Place your phone on Fido". Include a hint when
   `settings.require_dock` is true and the robot is offline, and a way to turn
   `require_dock` off in Settings ("Start without docking").
4. **Escape fails the timer and requires a stop, then an explicit restart.**
   In `escaped`, the clock is frozen and the primary action is "Stop Fido"
   (`POST /api/session/caught`), simulating the robot's stop button.
   Catching or the 10-minute timeout enters `failed`, never `focusing`.
   Render `failed` with "Start again", posting a new `/api/session` to get a
   fresh timer and counters. Pause is only allowed in `focusing`, `nudge`,
   `barking`; no resume is allowed after escape.
5. **Show why.** `session.reason` explains the current phase (e.g.
   "Looking Down at Phone/Lap (ΔPitch: +0.31) [laptop]"). Show it under the
   companion copy during `nudge`, `barking`, `escaped`, `failed`. `session.events` is the
   session timeline for the Activity tab.
6. **History from the backend.** Activity uses `GET /api/history` (refetch when
   a session completes, fails, or ends) instead of localStorage. Totals: sum of
   `focused_seconds`, count of `completed`.
7. **Devices tab reflects reality.**
   - Fido: `robot.online`, `robot.phone_docked`, `robot.mode`, `robot.info`
     (e.g. battery when the robot reports it).
   - Cameras: one card per `detector.sources` entry (name, `fresh`,
     `calibrated`, `focused`, `fps`, `reason`). Explain that detection runs on
     the laptop / dog camera via `python -m detector.agent` and only the YES /
     NO decision is sent. Remove the in-browser camera preview (it did not
     analyse anything and the phone is not a camera in this design).
   - Vitals (Presage): `vitals.pulse`, `vitals.breathing`, `vitals.hrvRmssd`
     when present, with the note "from a 25-second Presage scan".
8. **Settings modal** edits backend settings: `bark_after_seconds`,
   `escape_after_seconds`, `escape_timeout_seconds`, `require_dock`,
   `motion_enabled` (label "Fido can move"; doubles as an emergency stop).
9. **Remove "DEMO MODE" / simulation copy** where it is no longer true; show a
   clear offline banner when the backend is unreachable instead.

## Snapshot shape (TypeScript)

```ts
export type Phase =
  | "idle" | "awaiting_phone" | "focusing" | "nudge"
  | "barking" | "escaped" | "paused" | "completed" | "failed";

export type RobotMode =
  | "sleep" | "beckon_dock" | "watch" | "nudge"
  | "bark" | "run" | "rest" | "celebrate";

export interface FidoState {
  version: number;
  server_time: number; // unix seconds
  phase: Phase;
  session: null | {
    id: string; task: string; phase: Phase; reason: string;
    phase_since: number; created_at: number; grace_until: number;
    duration_seconds: number; remaining_seconds: number; clock_running: boolean;
    distractions: number; escapes: number;
    events: { at: number; phase: Phase; message: string }[];
  };
  robot: {
    mode: RobotMode; online: boolean; phone_docked: boolean;
    last_seen: number | null; info: Record<string, unknown>;
  };
  detector: {
    focused: boolean | null; // null until a calibrated camera reports
    sources: Record<string, {
      focused: boolean; calibrated: boolean; reason: string; fresh: boolean;
      fps: number | null; received_at: number; timers: Record<string, number>;
    }>;
  };
  vitals: null | { pulse?: number; breathing?: number; hrvRmssd?: number; [k: string]: unknown };
  settings: {
    bark_after_seconds: number; escape_after_seconds: number;
    escape_timeout_seconds: number; recover_grace_seconds: number;
    source_stale_seconds: number; robot_offline_seconds: number;
    require_dock: boolean; motion_enabled: boolean;
  };
}

export interface HistoryEntry {
  id: string; task: string; duration_seconds: number; focused_seconds: number;
  distractions: number; escapes: number; completed: boolean;
  started_at: number; ended_at: number;
}
```

## Phase copy suggestions

| Phase | Robot | Suggested copy |
| --- | --- | --- |
| `awaiting_phone` | OLED beckons | "Place your phone on Fido to begin." |
| `focusing` | watching | "You do your thing. Fido's keeping watch." |
| `nudge` | gentle beckon | "Hey, eyes over here." + reason |
| `barking` | barking | "Woof! Back to your one thing." + reason |
| `escaped` | running with phone | "Session failed. Catch Fido, then press Stop Fido." |
| `failed` | resting | "Fido stopped. Press Start again when you're ready for a fresh session." |
| `paused` | resting | "Take a little breather." |
| `completed` | celebrating | "Look at you, focused." |

## Testing without hardware

```sh
cd backend && .venv/bin/python -m fido                    # backend :5000
curl -X PATCH localhost:5000/api/settings -H 'Content-Type: application/json' -d '{"require_dock": false}'
.venv/bin/python -m detector.agent                        # real webcam detection
# or fake devices:
curl -X POST localhost:5000/api/robot/events -H 'Content-Type: application/json' -d '{"event":"docked"}'
curl -X POST localhost:5000/api/readings -H 'Content-Type: application/json' \
  -d '{"source":"laptop","reading":{"focused":false,"calibrated":true,"reason":"Looking Away Sideways"}}'
curl -X POST localhost:5000/api/robot/events -H 'Content-Type: application/json' -d '{"event":"caught"}'
```
