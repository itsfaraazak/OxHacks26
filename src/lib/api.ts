/**
 * Thin typed client for the Fido backend (see backend/README.md#api).
 * The backend owns the session; the page renders the latest snapshot.
 */

export type Phase =
  | "idle" | "awaiting_phone" | "focusing" | "nudge"
  | "barking" | "escaped" | "paused" | "completed";

export type RobotMode =
  | "sleep" | "beckon_dock" | "watch" | "nudge"
  | "bark" | "run" | "rest" | "celebrate";

export interface SessionEvent { at: number; phase: Phase; message: string }

export interface Session {
  id: string; task: string; phase: Phase; reason: string;
  phase_since: number; created_at: number; grace_until: number;
  duration_seconds: number; remaining_seconds: number; clock_running: boolean;
  distractions: number; escapes: number;
  events: SessionEvent[];
}

export interface CameraSource {
  focused: boolean; calibrated: boolean; reason: string; fresh: boolean;
  fps: number | null; received_at: number; timers: Record<string, number>;
}

export interface Settings {
  bark_after_seconds: number; escape_after_seconds: number;
  escape_timeout_seconds: number; recover_grace_seconds: number;
  source_stale_seconds: number; robot_offline_seconds: number;
  require_dock: boolean; motion_enabled: boolean;
}

export interface FidoState {
  version: number;
  server_time: number; // unix seconds
  phase: Phase;
  session: Session | null;
  robot: {
    mode: RobotMode; online: boolean; phone_docked: boolean;
    last_seen: number | null; info: Record<string, unknown>;
  };
  detector: {
    focused: boolean | null; // null until a calibrated camera reports
    sources: Record<string, CameraSource>;
  };
  vitals: null | { pulse?: number; breathing?: number; hrvRmssd?: number; [k: string]: unknown };
  settings: Settings;
}

export interface HistoryEntry {
  id: string; task: string; duration_seconds: number; focused_seconds: number;
  distractions: number; escapes: number; completed: boolean;
  started_at: number; ended_at: number;
}

export type SessionAction = "pause" | "resume" | "caught" | "end";

/** `NEXT_PUBLIC_FIDO_API`, else port 5000 on whichever host served the page (so LAN phones work). */
export function apiBase(): string {
  const configured = process.env.NEXT_PUBLIC_FIDO_API;
  if (configured) return configured.replace(/\/+$/, "");
  const host = typeof window === "undefined" ? "localhost" : window.location.hostname;
  return `http://${host.includes(":") ? `[${host}]` : host}:5000`;
}

/** A response with an error status. `state` is present on 409, so the caller can resync. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly state?: FidoState) {
    super(message);
  }
}

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`${apiBase()}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(data?.error ?? `Request failed (${response.status})`, response.status, data?.state);
  }
  return data as T;
}

export const api = {
  startSession: (task: string, durationMinutes: number) =>
    request<FidoState>("/api/session", "POST", { task, duration_minutes: durationMinutes }),
  sessionAction: (action: SessionAction) => request<FidoState>(`/api/session/${action}`, "POST"),
  updateSettings: (changes: Partial<Settings>) => request<Settings>("/api/settings", "PATCH", changes),
  history: () => request<HistoryEntry[]>("/api/history"),
};

/** live: stream open. connecting: (re)connecting. offline: unreachable for a few seconds. */
export type Connection = "connecting" | "live" | "offline";
const OFFLINE_AFTER_MS = 3000;
const RETRY_MS = 2000;

/**
 * Subscribes to `/api/events`. `onState` receives every snapshot; `fresh` is true for the
 * first snapshot after each (re)connect, which replaces local state even if its version is
 * lower (the backend may have restarted). Returns an unsubscribe function.
 */
export function subscribe(
  onState: (state: FidoState, fresh: boolean) => void,
  onConnection: (connection: Connection) => void,
): () => void {
  let source: EventSource | undefined;
  let current: Connection = "connecting";
  let fresh = true;
  let offlineTimer: ReturnType<typeof setTimeout> | undefined;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;

  const set = (next: Connection) => {
    if (next !== current) onConnection((current = next));
  };
  const startOfflineTimer = () => {
    offlineTimer ??= setTimeout(() => set("offline"), OFFLINE_AFTER_MS);
  };
  const connect = () => {
    source = new EventSource(`${apiBase()}/api/events`);
    source.onopen = () => {
      clearTimeout(offlineTimer);
      offlineTimer = undefined;
      fresh = true;
      set("live");
    };
    source.addEventListener("state", event => {
      onState(JSON.parse((event as MessageEvent<string>).data) as FidoState, fresh);
      fresh = false;
    });
    source.onerror = () => {
      if (current === "live") set("connecting");
      startOfflineTimer();
      // The browser retries network errors itself; it gives up (CLOSED) on e.g. CORS or HTTP errors.
      if (source?.readyState === EventSource.CLOSED) {
        source.close();
        retryTimer = setTimeout(connect, RETRY_MS);
      }
    };
  };

  startOfflineTimer();
  connect();
  return () => {
    clearTimeout(offlineTimer);
    clearTimeout(retryTimer);
    source?.close();
  };
}
