"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowRight, ArrowUpRight, BatteryFull, Camera, Check, CircleHelp, Clock3, Dog, Flame, Focus, Hand, HeartPulse, LayoutGrid, Leaf, Pause, Play, Settings2, ShieldCheck, Smartphone, Sparkles, Square, Target, Wifi, WifiOff, X } from "lucide-react";
import { api, apiBase, ApiError, subscribe, type Connection, type FidoState, type HistoryEntry, type Phase, type SessionAction, type Settings } from "@/lib/api";
import CameraPreview from "./camera-preview";

type Tab = "Focus" | "Activity" | "Devices";
/** The latest snapshot plus the local time it arrived, for countdown interpolation. */
type Snapshot = { state: FidoState; receivedAt: number };

const phaseCopy: Record<Phase, [string, string]> = {
  idle: ["Small paws. Big focus.", "Your phone takes a break. You make progress."],
  awaiting_phone: ["Place your phone on Fido.", "Fido starts watching as soon as your phone is docked."],
  focusing: ["You do your thing.", "Fido’s keeping watch. One thing at a time."],
  nudge: ["Hey, eyes over here.", "A gentle nudge. Your goal is waiting for you."],
  barking: ["Woof! Back to your one thing.", "Fido’s getting impatient. Bring your attention back."],
  escaped: ["Fido ran off with your phone.", "Catch Fido and tap the good-boy card on its collar."],
  paused: ["Take a little breather.", "Your session is paused. Fido’s not going anywhere."],
  completed: ["Look at you, focused.", "One small session. A meaningful step forward."],
};
const floatingNote: Record<Phase, string> = {
  idle: "Ready when you are", awaiting_phone: "Waiting for your phone", focusing: "Keeping watch",
  nudge: "Psst, over here", barking: "Woof woof!", escaped: "Catch me if you can",
  paused: "Taking a breather", completed: "Good job!",
};
const PAUSABLE: Phase[] = ["focusing", "nudge", "barking"];
const SHOW_REASON: Phase[] = ["nudge", "barking", "escaped"];
const NUMBER_SETTINGS: { key: "bark_after_seconds" | "escape_after_seconds" | "escape_timeout_seconds"; label: string; hint: string }[] = [
  { key: "bark_after_seconds", label: "Bark after", hint: "Seconds of distraction in a nudge before Fido barks." },
  { key: "escape_after_seconds", label: "Run off after", hint: "Seconds of barking before Fido runs off with your phone." },
  { key: "escape_timeout_seconds", label: "Give up after", hint: "An uncaught escape stops by itself after this many seconds." },
];

const clock = (unixSeconds: number) => new Date(unixSeconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const mins = (seconds: number) => Math.round(seconds / 60);
const label = (key: string) => key.replace(/[_-]+/g, " ").replace(/^\w/, c => c.toUpperCase());
const infoValue = (key: string, value: unknown) => typeof value === "number" && /battery/i.test(key) ? `${value}%` : typeof value === "object" ? JSON.stringify(value) : String(value);

function Robot({ phase }: { phase: Phase }) {
  return <svg className={`robot robot-${phase}`} viewBox="0 0 440 290" role="img" aria-label="Fido, a friendly cream robot dog with a black OLED face">
    <defs>
      <linearGradient id="shell" x1="0" y1="0" x2="0.6" y2="1"><stop stopColor="#fffef6"/><stop offset="1" stopColor="#c8c9b6"/></linearGradient>
      <linearGradient id="side" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#dadccb"/><stop offset="1" stopColor="#a6aa95"/></linearGradient>
      <linearGradient id="face" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#323a33"/><stop offset="1" stopColor="#101812"/></linearGradient>
      <filter id="shadow"><feGaussianBlur stdDeviation="7"/></filter>
    </defs>
    <ellipse cx="227" cy="254" rx="139" ry="14" fill="#80866b" opacity=".18" filter="url(#shadow)"/>
    <path d="M309 140 Q365 104 349 86" fill="none" stroke="#b6bda4" strokeWidth="17" strokeLinecap="round"/>
    <path d="M279 172 L310 184 L304 243 Q291 255 277 244 L277 209" fill="url(#side)" stroke="#a6ad96" strokeWidth="1.5"/>
    <path d="M183 174 L207 190 L198 241 Q184 251 172 240 Z" fill="url(#side)"/>
    <path d="M160 116 Q185 92 228 102 L304 124 Q326 131 322 163 L309 198 Q289 215 259 204 L180 177 Z" fill="url(#shell)" stroke="#bcc2ab" strokeWidth="1.5"/>
    <path d="M238 181 L265 190 L258 251 Q244 261 230 250 Z" fill="url(#shell)" stroke="#b5bca4" strokeWidth="1.5"/>
    <path d="M150 171 L175 177 L168 238 Q153 248 141 236 Z" fill="url(#shell)" stroke="#b5bca4" strokeWidth="1.5"/>
    <ellipse cx="252" cy="196" rx="11" ry="12" fill="#a9af96"/><ellipse cx="252" cy="196" rx="5" ry="6" fill="#dce0cb"/>
    <path d="M217 107 L239 99 L285 110 L265 120 Z" fill="#a5ac96"/>
    <path d="M222 103 L239 96 L279 106 L263 114 Z" fill="#343d35"/>
    <path d="M231 99 L239 96 L279 106 L271 109 Z" fill="#596451"/>
    <path d="M119 80 Q143 63 188 72 L215 88 Q228 99 222 130 L211 165 Q206 180 180 182 L119 162 Q100 154 101 128 Z" fill="url(#shell)" stroke="#b6bea5" strokeWidth="1.5"/>
    <path d="M112 88 Q96 72 94 85 L87 118 Q88 131 105 135 Z" fill="url(#side)"/>
    <path d="M202 83 Q219 64 225 82 L231 112 Q232 126 217 132 Z" fill="url(#side)"/>
    <path d="M117 96 Q130 86 149 91 L197 104 Q207 108 205 128 L199 155 Q197 168 180 164 L122 148 Q109 145 111 128 Z" fill="url(#face)" stroke="#6f7867" strokeWidth="3"/>
    <g fill="#d4f78a">
      <rect x="131" y="112" width="10" height={phase === "paused" ? 5 : 19} rx="5" transform="rotate(12 131 112)"/>
      <rect x="172" y="122" width="10" height={phase === "paused" ? 5 : 19} rx="5" transform="rotate(12 172 122)"/>
      <path d="M146 140 Q153 149 162 144" fill="none" stroke="#d4f78a" strokeWidth="3" strokeLinecap="round"/>
    </g>
    <path d="M126 80 L178 92" stroke="white" strokeWidth="3" opacity=".65" strokeLinecap="round"/>
    <circle cx="300" cy="150" r="4" fill="#90ac63"/>
  </svg>;
}

export default function Home() {
  const [tab, setTab] = useState<Tab>("Focus");
  const [task, setTask] = useState("Make something that matters");
  const [duration, setDuration] = useState(25);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [modal, setModal] = useState<"settings" | "help" | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [now, setNow] = useState(() => Date.now());

  /** Keep the newest snapshot; `fresh` (first after a reconnect) wins even if older, e.g. after a backend restart. */
  const apply = useCallback((state: FidoState, fresh = false) => {
    setSnap(prev => !fresh && prev && state.version < prev.state.version ? prev : { state, receivedAt: Date.now() });
  }, []);
  useEffect(() => subscribe(apply, setConnection), [apply]);

  const state = snap?.state ?? null;
  const session = state?.session ?? null;
  const phase: Phase = state?.phase ?? "idle";
  const settings = state?.settings ?? null;
  const robot = state?.robot ?? null;
  const online = connection === "live" && state !== null;
  const canStart = phase === "idle" || phase === "completed";

  // History changes when a session completes or ends, and may have changed while disconnected.
  const historyKey = `${connection}|${session?.id ?? ""}|${phase === "completed"}`;
  useEffect(() => {
    if (connection !== "live") return;
    let current = true;
    api.history().then(entries => { if (current) setHistory(entries); }).catch(() => { /* The offline banner covers this. */ });
    return () => { current = false; };
  }, [historyKey, connection]);

  // The backend owns the clock. Between events, count down from its last remaining_seconds.
  const clockRunning = session?.clock_running ?? false;
  useEffect(() => {
    if (!clockRunning) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [clockRunning, snap]);
  useEffect(() => { if (!notice) return; const id = setTimeout(() => setNotice(""), 5000); return () => clearTimeout(id); }, [notice]);

  async function run(command: () => Promise<FidoState | unknown>, done?: string) {
    setBusy(true);
    try {
      const result = await command();
      if (result && typeof result === "object" && "version" in result) apply(result as FidoState);
      if (done) setNotice(done);
      return true;
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.state) apply(err.state);
        setNotice(err.message);
      } else {
        setNotice(`Can’t reach Fido’s backend at ${apiBase()}.`);
      }
      return false;
    } finally {
      setBusy(false);
    }
  }
  function start() {
    if (!task.trim()) { setNotice("Give this session a small, specific goal first."); return; }
    void run(() => api.startSession(task.trim(), duration));
  }
  const act = (action: SessionAction, done?: string) => void run(() => api.sessionAction(action), done);
  const updateSettings = (changes: Partial<Settings>) => run(() => api.updateSettings(changes));
  function openSettings() {
    if (settings) setDrafts(Object.fromEntries(NUMBER_SETTINGS.map(({ key }) => [key, String(settings[key])])));
    setModal("settings");
  }
  async function saveSettings() {
    if (!settings) { setModal(null); return; }
    const changes = Object.fromEntries(NUMBER_SETTINGS
      .filter(({ key }) => drafts[key] !== undefined && Number(drafts[key]) !== settings[key])
      .map(({ key }) => [key, Number(drafts[key])]));
    if (Object.keys(changes).length && !await updateSettings(changes)) return;
    setModal(null);
  }

  // Interpolate from the last snapshot. Elapsed time is measured on this device from when the
  // snapshot arrived (server_time anchored to local receipt), so clock skew between devices cancels out.
  const remaining = !session ? duration * 60
    : session.clock_running && snap ? Math.max(0, session.remaining_seconds - Math.max(0, now - snap.receivedAt) / 1000)
    : session.remaining_seconds;
  const shown = Math.ceil(remaining);
  const minutes = String(Math.floor(shown / 60)).padStart(2, "0");
  const seconds = String(shown % 60).padStart(2, "0");
  const progress = session ? 1 - remaining / session.duration_seconds : 0;
  const focusedMinutes = mins(history.reduce((sum, entry) => sum + entry.focused_seconds, 0));
  const completedCount = history.filter(entry => entry.completed).length;
  const sources = Object.entries(state?.detector.sources ?? {});
  const vitals = state?.vitals ?? null;
  const vitalRows = ([["pulse", "Pulse", "bpm"], ["breathing", "Breathing", "breaths/min"], ["hrvRmssd", "HRV (RMSSD)", "ms"]] as const)
    .filter(([key]) => typeof vitals?.[key] === "number");
  const onlineDevices = (robot?.online ? 1 : 0) + sources.filter(([, s]) => s.fresh).length;
  const watching = session !== null && !["awaiting_phone", "paused", "completed"].includes(phase);
  const footnote = !session || phase === "completed" ? "Dock your phone. Set your intention. We’ve got you."
    : phase === "awaiting_phone" ? "The clock starts once your phone is on Fido."
    : watching && state?.detector.focused === null ? "No camera is reporting yet. Run python -m detector.agent on your laptop."
    : "Only yes / no leaves the camera. No video is uploaded.";
  const badge = connection === "live" ? "LIVE" : connection === "offline" ? "OFFLINE" : "RECONNECTING…";

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="Fido home"><span className="brand-mark"><Dog size={25}/></span> fido<span className="brand-dot">.</span></a>
      <div className="workspace-label">YOUR LITTLE FOCUS SPACE</div>
      <nav aria-label="Main navigation">{(["Focus", "Activity", "Devices"] as Tab[]).map((name, i) => { const Icon = [Focus, LayoutGrid, Smartphone][i]; return <button className={`nav-link ${tab === name ? "selected" : ""}`} key={name} onClick={() => setTab(name)}><Icon size={19}/>{name}{name === "Devices" && online && <span className="nav-count" title="Devices online">{onlineDevices}</span>}</button>; })}</nav>
      <div className="sidebar-bottom"><div className="little-note"><Leaf size={20}/><p>A little less scrolling.<br/><strong>A little more living.</strong></p></div><button className="nav-link" onClick={() => setModal("help")}><CircleHelp size={18}/> Meet your companion <ArrowUpRight size={15}/></button><div className="profile"><div className="avatar">Y</div><div><strong>Your workspace</strong><span>One thing at a time</span></div><button className="icon-button" aria-label="Open settings" onClick={openSettings}><Settings2 size={18}/></button></div></div>
    </aside>
    <main>
      <header className="topbar"><div className="mobile-brand"><Dog size={23}/> fido.</div><div className="breadcrumb">Your workspace <span>/</span> <strong>{tab}</strong></div><div className="top-actions"><span className={`status-badge status-${connection}`} role="status"><span/> {badge}</span><button className="icon-button" aria-label="How Fido works" onClick={() => setModal("help")}><CircleHelp size={19}/></button><div className="avatar small">Y</div></div></header>
      <div className="page-content">
        {connection === "offline" && <div className="privacy-note offline-banner" role="alert"><WifiOff size={17}/><span><strong>Can’t reach Fido’s backend at {apiBase()}.</strong> Start it with <code>cd backend && .venv/bin/python -m fido</code>. Retrying automatically.</span></div>}
        <div className="page-heading"><div><div className="eyebrow"><span className="sun-symbol">✳</span> A GOOD DAY TO MAKE PROGRESS</div><h1>{tab === "Focus" ? "Find your focus." : tab === "Activity" ? "Small steps add up." : "Better, connected."}</h1><p>{tab === "Focus" ? "Less distraction. More intention. A friend to keep you on track." : tab === "Activity" ? "A little reflection on the time you made for what matters." : "Your companion, your screens. One focused little ecosystem."}</p></div><button className="settings-button" onClick={openSettings}><Settings2 size={16}/><span>Preferences</span></button></div>
        {tab === "Focus" && <>
          <div className="focus-grid">
            <section className="companion-card">
              <div className="card-top"><span className="section-label"><span className="status-dot"/> YOUR FOCUS COMPANION</span><span className="soft-badge">Meet Fido <Sparkles size={12}/></span></div>
              <div className="companion-scene"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="floating-note"><span className="status-dot"/>{floatingNote[phase]}<span className="note-dot">·</span><Dog size={13}/></div><Robot phase={phase}/><span className="scene-spark spark-one">✧</span><span className="scene-spark spark-two">✳</span><div className="ground-label">YOUR PARTNER IN GETTING THINGS DONE</div></div>
              <div className="companion-copy" aria-live="polite"><h2>{phaseCopy[phase][0]}</h2><p>{phaseCopy[phase][1]}</p>{session?.reason && SHOW_REASON.includes(phase) && <p className="companion-reason">{session.reason}</p>}</div>
              <div className="companion-footer"><span>{robot?.online ? <Wifi size={14}/> : <WifiOff size={14}/>} {robot?.online ? "Fido online" : "Fido offline"}</span><span><Smartphone size={14}/> {robot?.phone_docked ? "Phone docked" : "Phone not docked"}</span>{typeof robot?.info.battery === "number" && <span><BatteryFull size={16}/> {robot.info.battery}%</span>}<button aria-label="Companion settings" className="icon-button" onClick={openSettings}><Settings2 size={16}/></button></div>
            </section>
            <section className="session-card"><div className="card-top"><span className="section-label">MAKE ROOM FOR DEEP WORK</span><Clock3 size={17}/></div><h2>{canStart ? "What’s your one thing?" : phase === "awaiting_phone" ? "Place your phone on Fido." : phase === "paused" ? "A moment to reset." : phase === "escaped" ? "Go get your phone back." : "Stay with your one thing."}</h2><p className="muted">Big ideas start with a little undivided attention.</p>
              <label className="field-label" htmlFor="task">I WANT TO FOCUS ON</label><div className="task-input"><Target size={18}/><input id="task" value={canStart || !session ? task : session.task} maxLength={120} disabled={!canStart} onChange={e => setTask(e.target.value)} placeholder="Give your session a goal…"/></div>
              <div className="duration-label"><span className="field-label">FOR A LITTLE WHILE</span><span>Find your flow</span></div>
              <div className="duration-options">{[15, 25, 45, 60].map(n => { const chosen = canStart ? duration === n : session?.duration_seconds === n * 60; return <button disabled={!canStart} className={chosen ? "chosen" : ""} onClick={() => setDuration(n)} key={n}>{n}<span> min</span>{chosen && <span className="duration-dot"/>}</button>; })}</div>
              {session && phase !== "completed" && <div className="timer" aria-label={`${minutes} minutes ${seconds} seconds remaining`}><span>{minutes}<span>:</span>{seconds}</span><div className="timer-track"><div style={{ width: `${progress * 100}%` }}/></div></div>}
              {phase === "awaiting_phone" && settings?.require_dock && !robot?.online && <div className="privacy-note"><Dog size={17}/><span>Fido’s robot isn’t connected. No robot yet? Turn on <button className="link-button" onClick={openSettings}>Start without docking</button> in Settings.</span></div>}
              {!session ? <button className="primary-button" disabled={!online || busy} onClick={start}>Let’s focus <ArrowRight size={19}/></button>
                : <div className="session-actions">
                  {phase === "completed" ? <button className="primary-button" disabled={!online || busy} onClick={start}>Let’s focus <ArrowRight size={17}/></button>
                    : phase === "escaped" ? <button className="primary-button" disabled={busy} onClick={() => act("caught")}><Hand size={17}/> I caught Fido</button>
                    : phase === "paused" ? <button className="primary-button" disabled={busy} onClick={() => act("resume")}><Play size={17}/> Keep going</button>
                    : <button className="primary-button" disabled={busy || !PAUSABLE.includes(phase)} onClick={() => act("pause")}><Pause size={17}/> Take a breath</button>}
                  <button className="stop-button" aria-label="End session" disabled={busy} onClick={() => act("end", phase === "completed" ? "Session cleared." : "Session ended. It’s in your journal.")}><Square size={17}/></button>
                </div>}
              <div className="session-footnote"><ShieldCheck size={14}/>{footnote}</div>
            </section>
          </div>
          <CameraPreview/>
          <section className="stats-strip" aria-label="Focus statistics"><div><span className="stat-icon"><Clock3 size={19}/></span><div><span className="stat-label">FOCUS TIME</span><strong>{focusedMinutes}<small> min</small></strong></div><span className="stat-note">All your small wins</span></div><div><span className="stat-icon"><Check size={20}/></span><div><span className="stat-label">SESSIONS FINISHED</span><strong>{completedCount}<small> sessions</small></strong></div><span className="stat-note">Made time for you</span></div><div><span className="stat-icon"><Flame size={19}/></span><div><span className="stat-label">THIS SESSION</span><strong>{session?.distractions ?? 0}<small> distractions</small></strong></div><span className="stat-note">Progress, not perfection</span></div></section>
          <section className="how-section"><div className="section-heading"><h2>A friend. Not another notification.</h2><button onClick={() => setModal("help")}>How it works <ArrowUpRight size={15}/></button></div><div className="steps">{[{ icon: Smartphone, title: "Phone down. Head clear.", text: "Give your phone a home on Fido. Give your attention a little space." }, { icon: Focus, title: "A gentle nudge back.", text: "Lose your flow? Your companion helps bring you back to your one thing." }, { icon: Dog, title: "A little tough love.", text: "Keep getting distracted? Fido barks. Then your phone goes for a walk." }].map((step, i) => <div className="step" key={step.title}><div className="step-top"><step.icon size={21}/><span>0{i + 1}</span></div><h3>{step.title}</h3><p>{step.text}</p></div>)}</div></section>
        </>}
        {tab === "Activity" && <div className="activity-layout"><section className="surface"><div className="section-heading"><h2>Your focus journal</h2><span className="soft-badge">{completedCount} completed</span></div>{history.length ? history.map(entry => <div className="history-row" key={entry.id}><span className="stat-icon">{entry.completed ? <Check size={18}/> : <Square size={15}/>}</span><div><strong>{entry.task}</strong><p>{new Date(entry.started_at * 1000).toLocaleDateString()} · {entry.completed ? "completed" : "ended early"} · {entry.distractions} distractions{entry.escapes ? ` · ${entry.escapes} escapes` : ""}</p></div><span>{mins(entry.focused_seconds)} / {mins(entry.duration_seconds)} min</span></div>) : <div className="empty-state"><Leaf size={38}/><h3>Your next small win starts here.</h3><p>Finish a focus session and it’ll find a home in your journal. No made-up streaks. Just your progress.</p><button className="primary-button" onClick={() => setTab("Focus")}>Make time for focus <ArrowRight size={17}/></button></div>}</section><section className="surface"><h2>In this session</h2><p className="muted">{session ? session.task : "What Fido noticed, as it happens."}</p>{session?.events.length ? [...session.events].reverse().map((event, i) => <div className="event-row" key={`${event.at}-${i}`}><span className="status-dot"/><span>{event.message}</span><span className="event-time">{clock(event.at)}</span></div>) : <p className="empty-small">A clean slate. Start a session to see your timeline.</p>}</section></div>}
        {tab === "Devices" && <><div className="device-grid">
          <section className="surface device-card"><span className="device-icon"><Dog size={32}/></span><span className="soft-badge">{robot?.online ? "Online" : "Offline"}</span><h2>Fido</h2><p>Phone dock · OLED · motors</p><dl className="device-facts"><div><dt>Mode</dt><dd>{robot ? label(robot.mode) : "–"}</dd></div><div><dt>Phone</dt><dd>{robot?.phone_docked ? "Docked" : "Not docked"}</dd></div><div><dt>Can move</dt><dd>{settings ? settings.motion_enabled ? "Yes" : "No (stopped)" : "–"}</dd></div>{Object.entries(robot?.info ?? {}).map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{infoValue(key, value)}</dd></div>)}</dl><div className="device-bottom"><ShieldCheck size={15}/> {robot?.last_seen ? `Last heard from at ${clock(robot.last_seen)}` : "Not connected yet"}</div></section>
          {sources.map(([name, source]) => <section className="surface device-card" key={name}><span className="device-icon"><Camera size={32}/></span><span className="soft-badge">{!source.fresh ? "Stale" : !source.calibrated ? "Calibrating" : source.focused ? "Focused" : "Distracted"}</span><h2>{label(name)} camera</h2><p>{source.reason || "No reason reported"}</p><dl className="device-facts"><div><dt>Live</dt><dd>{source.fresh ? "Yes" : "No"}</dd></div><div><dt>Calibrated</dt><dd>{source.calibrated ? "Yes" : "No"}</dd></div><div><dt>Focused</dt><dd>{source.focused ? "Yes" : "No"}</dd></div><div><dt>Frame rate</dt><dd>{source.fps === null ? "–" : `${source.fps.toFixed(1)} fps`}</dd></div></dl><div className="device-bottom"><ShieldCheck size={15}/> Last reading at {clock(source.received_at)}</div></section>)}
          {!sources.length && <section className="surface device-card"><span className="device-icon"><Camera size={32}/></span><span className="soft-badge">None yet</span><h2>Cameras</h2><p>No camera is reporting. Run <code>python -m detector.agent</code> in <code>backend/</code> on the laptop or the dog’s camera.</p><div className="device-bottom"><ShieldCheck size={15}/> Frames never leave that machine</div></section>}
          <section className="surface device-card"><span className="device-icon"><HeartPulse size={32}/></span><span className="soft-badge">{vitalRows.length ? "Scanned" : "No scan"}</span><h2>Vitals</h2><p>From a 25-second Presage scan.</p>{vitalRows.length ? <dl className="device-facts">{vitalRows.map(([key, name, unit]) => <div key={key}><dt>{name}</dt><dd>{Math.round((vitals?.[key] as number) * 10) / 10} {unit}</dd></div>)}</dl> : <p>Run the Presage harness in <code>backend/presage</code>; the detector agent forwards the result.</p>}<div className="device-bottom"><ShieldCheck size={15}/> Not a medical measurement</div></section>
        </div><div className="privacy-note"><ShieldCheck size={17}/><span>Detection runs on the laptop or the dog’s camera via <code>python -m detector.agent</code>. Only the YES / NO decision and its reason are sent to Fido’s backend; no video leaves that machine. Turn off “Fido can move” in Settings to stop the motors at any time.</span></div></>}
        <footer><span><Dog size={15}/> A little company. A lot more focus.</span><span>Made for intentional days <span className="footer-flower">✳</span></span></footer>
      </div>
    </main>
    <nav className="mobile-nav" aria-label="Mobile navigation">{(["Focus", "Activity", "Devices"] as Tab[]).map((name, i) => { const Icon = [Focus, LayoutGrid, Smartphone][i]; return <button key={name} aria-current={tab === name ? "page" : undefined} className={tab === name ? "selected" : ""} onClick={() => setTab(name)}><Icon size={21}/><span>{name}</span></button>; })}<button onClick={openSettings}><Settings2 size={21}/><span>Settings</span></button></nav>
    {notice && <div className="toast" role="status"><span>{notice}</span><button className="icon-button" aria-label="Dismiss message" onClick={() => setNotice("")}><X size={16}/></button></div>}
    {modal && <div className="modal-backdrop" onClick={() => setModal(null)}><section className="modal surface" role="dialog" aria-modal="true" aria-labelledby="modal-title" onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === "Escape") setModal(null); }}><button autoFocus className="modal-close icon-button" onClick={() => setModal(null)} aria-label="Close dialog"><X size={21}/></button><span className="modal-icon"><Dog size={28}/></span><h2 id="modal-title">{modal === "settings" ? "Make yourself at home." : "Meet your new focus friend."}</h2>{modal === "settings" ? <><p>Changes apply to Fido right away, on every device.</p>{settings ? <>
      {NUMBER_SETTINGS.map(({ key, label: name, hint }) => <label className="setting-row" key={key}><span><strong>{name}</strong><small>{hint}</small></span><input className="number-input" type="number" min={1} max={86400} step={1} aria-label={name} value={drafts[key] ?? String(settings[key])} onChange={e => setDrafts(d => ({ ...d, [key]: e.target.value }))}/></label>)}
      <label className="setting-row"><span><strong>Start without docking</strong><small>Begin watching without the phone on Fido, e.g. with no robot connected.</small></span><input type="checkbox" checked={!settings.require_dock} disabled={busy} onChange={e => void updateSettings({ require_dock: !e.target.checked })}/></label>
      <label className="setting-row"><span><strong>Fido can move</strong><small>Turn off to keep Fido still. Also an emergency stop.</small></span><input type="checkbox" checked={settings.motion_enabled} disabled={busy} onChange={e => void updateSettings({ motion_enabled: e.target.checked })}/></label>
    </> : <div className="privacy-note"><WifiOff size={19}/><span>Settings live on Fido’s backend, which isn’t reachable right now.</span></div>}<button className="primary-button" disabled={busy} onClick={() => void saveSettings()}>Save <Check size={17}/></button></> : <><p>Fido gives your phone a place to rest so you can do your best work.</p><ol className="help-list"><li>Set one goal and choose your focus time.</li><li>Place your phone on Fido. The clock starts once it’s docked.</li><li>A camera watches for slacking. Fido nudges, barks, then runs off with your phone.</li><li>Catch Fido and tap the good-boy card on its collar to get back to work.</li></ol><p className="muted">Detection runs next to the camera and only sends yes / no. Vitals come from an optional Presage scan and are not medical measurements.</p><button className="primary-button" onClick={() => setModal(null)}>Sounds good <Check size={17}/></button></>}</section></div>}
  </div>;
}
