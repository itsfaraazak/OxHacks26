"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, ArrowUpRight, AudioLines, BatteryFull, Check, CircleHelp, Clock3, Dog, Flame, Focus, LayoutGrid, Laptop, Leaf, Pause, Play, Settings2, ShieldCheck, Smartphone, Sparkles, Square, Target, Wifi, X, Camera } from "lucide-react";
import { createSession, transition, type FocusState } from "@/lib/focus";

type Tab = "Focus" | "Activity" | "Devices";
type Entry = { task: string; minutes: number; distractions: number; date: string };
const phaseCopy = {
  idle: ["Small paws. Big focus.", "Your phone takes a break. You make progress."],
  focusing: ["You do your thing.", "Fido’s keeping you company. One thing at a time."],
  nudge: ["Hey, eyes over here.", "A gentle nudge. Your goal is waiting for you."],
  barking: ["A little less scrolling.", "Woof! Let’s bring your attention back."],
  escaped: ["Your phone’s on a walk.", "Simulated escape. No robot movement is enabled."],
  paused: ["Take a little breather.", "Your session is paused. Fido’s not going anywhere."],
  completed: ["Look at you, focused.", "One small session. A meaningful step forward."],
};

function Robot({ phase }: { phase: FocusState["phase"] }) {
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
  const [session, setSession] = useState(() => createSession("Make something that matters", 25));
  const [modal, setModal] = useState<"settings" | "help" | null>(null);
  const [sound, setSound] = useState(false);
  const [camera, setCamera] = useState(false);
  const [notice, setNotice] = useState("");
  const [history, setHistory] = useState<Entry[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const stream = useRef<MediaStream | null>(null);
  const video = useRef<HTMLVideoElement | null>(null);
  const started = useRef(false);
  const idle = session.phase === "idle" || session.phase === "completed";
  const active = !idle && session.phase !== "paused";

  useEffect(() => {
    try { const saved = JSON.parse(localStorage.getItem("fido-history") || "[]"); if (Array.isArray(saved)) setHistory(saved.filter(e => typeof e.task === "string" && typeof e.minutes === "number" && typeof e.distractions === "number" && typeof e.date === "string")); } catch { /* Storage is optional. */ }
    return () => stream.current?.getTracks().forEach(track => track.stop());
  }, []);
  useEffect(() => {
    if (!active) return;
    let last = Date.now();
    const timer = setInterval(() => {
      const seconds = Math.floor((Date.now() - last) / 1000);
      if (seconds < 1) return;
      last += seconds * 1000;
      setSession(current => {
        let next = current;
        for (let i = 0; i < Math.min(seconds, current.remainingSeconds); i++) next = transition(next, { type: "tick" });
        return next;
      });
    }, 250);
    return () => clearInterval(timer);
  }, [active]);
  useEffect(() => {
    if (session.phase !== "completed" || !started.current) return;
    started.current = false;
    const entry = { task: session.task, minutes: session.durationMinutes, distractions: session.distractions, date: new Date().toISOString() };
    setHistory(current => {
      const next = [entry, ...current];
      try { localStorage.setItem("fido-history", JSON.stringify(next)); } catch { /* Storage is optional. */ }
      return next;
    });
  }, [session]);
  useEffect(() => { if (video.current && camera) video.current.srcObject = stream.current; }, [camera, tab]);
  useEffect(() => { if (!notice) return; const id = setTimeout(() => setNotice(""), 5000); return () => clearTimeout(id); }, [notice]);

  function start() {
    if (!task.trim()) { setNotice("Give this session a small, specific goal first."); return; }
    started.current = true;
    setSession(transition(createSession(task.trim(), duration), { type: "start" }));
    setEvents(["Session started · phone dock simulated"]);
  }
  function distract() {
    setSession(current => transition(current, { type: "distract" }));
    setEvents(current => [`Distraction simulated · ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`, ...current]);
    if (sound && session.phase === "nudge") {
      try { const audio = new AudioContext(); const osc = audio.createOscillator(); const gain = audio.createGain(); osc.connect(gain); gain.connect(audio.destination); osc.frequency.value = 330; gain.gain.value = .08; osc.start(); osc.stop(audio.currentTime + .2); osc.onended = () => void audio.close(); } catch { setNotice("Audio is unavailable in this browser."); }
    }
  }
  async function toggleCamera() {
    if (camera) { stream.current?.getTracks().forEach(t => t.stop()); stream.current = null; setCamera(false); return; }
    if (!navigator.mediaDevices?.getUserMedia) { setNotice("Camera access needs localhost or HTTPS. No camera was started."); return; }
    try { stream.current = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false }); setCamera(true); }
    catch { setNotice("Camera permission wasn’t granted. You can still use the full demo."); }
  }
  const minutes = String(Math.floor(session.remainingSeconds / 60)).padStart(2, "0");
  const seconds = String(session.remainingSeconds % 60).padStart(2, "0");
  const totalMinutes = history.reduce((sum, entry) => sum + entry.minutes, 0);
  const progress = 1 - session.remainingSeconds / (session.durationMinutes * 60);

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="Fido home"><span className="brand-mark"><Dog size={25}/></span> fido<span className="brand-dot">.</span></a>
      <div className="workspace-label">YOUR LITTLE FOCUS SPACE</div>
      <nav aria-label="Main navigation">{(["Focus", "Activity", "Devices"] as Tab[]).map((name, i) => { const Icon = [Focus, LayoutGrid, Smartphone][i]; return <button className={`nav-link ${tab === name ? "selected" : ""}`} key={name} onClick={() => setTab(name)}><Icon size={19}/>{name}{name === "Devices" && <span className="nav-count">3</span>}</button>; })}</nav>
      <div className="sidebar-bottom"><div className="little-note"><Leaf size={20}/><p>A little less scrolling.<br/><strong>A little more living.</strong></p></div><button className="nav-link" onClick={() => setModal("help")}><CircleHelp size={18}/> Meet your companion <ArrowUpRight size={15}/></button><div className="profile"><div className="avatar">Y</div><div><strong>Your workspace</strong><span>One thing at a time</span></div><button className="icon-button" aria-label="Open settings" onClick={() => setModal("settings")}><Settings2 size={18}/></button></div></div>
    </aside>
    <main>
      <header className="topbar"><div className="mobile-brand"><Dog size={23}/> fido.</div><div className="breadcrumb">Your workspace <span>/</span> <strong>{tab}</strong></div><div className="top-actions"><span className="demo-badge"><span/> DEMO MODE</span><button className="icon-button" aria-label="About this demo" onClick={() => setModal("help")}><CircleHelp size={19}/></button><div className="avatar small">Y</div></div></header>
      <div className="page-content">
        <div className="page-heading"><div><div className="eyebrow"><span className="sun-symbol">✳</span> A GOOD DAY TO MAKE PROGRESS</div><h1>{tab === "Focus" ? "Find your focus." : tab === "Activity" ? "Small steps add up." : "Better, connected."}</h1><p>{tab === "Focus" ? "Less distraction. More intention. A friend to keep you on track." : tab === "Activity" ? "A little reflection on the time you made for what matters." : "Your companion, your screens. One focused little ecosystem."}</p></div><button className="settings-button" onClick={() => setModal("settings")}><Settings2 size={16}/><span>Preferences</span></button></div>
        {tab === "Focus" && <>
          <div className="focus-grid">
            <section className="companion-card">
              <div className="card-top"><span className="section-label"><span className="status-dot"/> YOUR FOCUS COMPANION</span><span className="soft-badge">Meet Fido <Sparkles size={12}/></span></div>
              <div className="companion-scene"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="floating-note"><span className="status-dot"/>{session.phase === "idle" ? "Ready when you are" : session.phase === "escaped" ? "Catch me if you can" : session.phase === "paused" ? "Taking a breather" : "Here for your focus"}<span className="note-dot">·</span><Dog size={13}/></div><Robot phase={session.phase}/><span className="scene-spark spark-one">✧</span><span className="scene-spark spark-two">✳</span><div className="ground-label">YOUR PARTNER IN GETTING THINGS DONE</div></div>
              <div className="companion-copy" aria-live="polite"><h2>{phaseCopy[session.phase][0]}</h2><p>{phaseCopy[session.phase][1]}</p></div>
              <div className="companion-footer"><span><Wifi size={14}/> Virtual companion</span><span><BatteryFull size={16}/> Demo device</span><button aria-label="Companion settings" className="icon-button" onClick={() => setModal("settings")}><Settings2 size={16}/></button></div>
            </section>
            <section className="session-card"><div className="card-top"><span className="section-label">MAKE ROOM FOR DEEP WORK</span><Clock3 size={17}/></div><h2>{idle ? "What’s your one thing?" : session.phase === "paused" ? "A moment to reset." : "Stay with your one thing."}</h2><p className="muted">Big ideas start with a little undivided attention.</p>
              <label className="field-label" htmlFor="task">I WANT TO FOCUS ON</label><div className="task-input"><Target size={18}/><input id="task" value={task} maxLength={120} disabled={!idle} onChange={e => setTask(e.target.value)} placeholder="Give your session a goal…"/></div>
              <div className="duration-label"><span className="field-label">FOR A LITTLE WHILE</span><span>Find your flow</span></div>
              <div className="duration-options">{[15, 25, 45, 60].map(n => <button disabled={!idle} className={duration === n ? "chosen" : ""} onClick={() => { setDuration(n); setSession(createSession(task, n)); }} key={n}>{n}<span> min</span>{duration === n && <span className="duration-dot"/>}</button>)}</div>
              {!idle && <div className="timer" aria-label={`${minutes} minutes ${seconds} seconds remaining`}><span>{minutes}<span>:</span>{seconds}</span><div className="timer-track"><div style={{ width: `${progress * 100}%` }}/></div></div>}
              {idle ? <button className="primary-button" onClick={start}>Let’s focus <ArrowRight size={19}/></button> : <div className="session-actions"><button className="primary-button" onClick={() => setSession(s => transition(s, { type: s.phase === "paused" ? "resume" : "pause" }))}>{session.phase === "paused" ? <Play size={17}/> : <Pause size={17}/>} {session.phase === "paused" ? "Keep going" : "Take a breath"}</button><button className="stop-button" aria-label="End session" onClick={() => { started.current = false; setSession(s => transition(s, { type: "reset" })); setNotice("Session ended. Only completed sessions are saved."); }}><Square size={17}/></button></div>}
              <div className="session-footnote"><ShieldCheck size={14}/>{idle ? "Dock your phone. Set your intention. We’ve got you." : "Simulation only · your phone stays right where it is."}</div>
            </section>
          </div>
          <section className="stats-strip" aria-label="Focus statistics"><div><span className="stat-icon"><Clock3 size={19}/></span><div><span className="stat-label">FOCUS TIME</span><strong>{totalMinutes}<small> min</small></strong></div><span className="stat-note">All your small wins</span></div><div><span className="stat-icon"><Check size={20}/></span><div><span className="stat-label">SESSIONS FINISHED</span><strong>{history.length}<small> sessions</small></strong></div><span className="stat-note">Made time for you</span></div><div><span className="stat-icon"><Flame size={19}/></span><div><span className="stat-label">THIS SESSION</span><strong>{session.distractions}<small> distractions</small></strong></div><span className="stat-note">Progress, not perfection</span></div></section>
          <section className="how-section"><div className="section-heading"><h2>A friend. Not another notification.</h2><button onClick={() => setModal("help")}>How it works <ArrowUpRight size={15}/></button></div><div className="steps">{[{ icon: Smartphone, title: "Phone down. Head clear.", text: "Give your phone a home on Fido. Give your attention a little space." }, { icon: Focus, title: "A gentle nudge back.", text: "Lose your flow? Your companion helps bring you back to your one thing." }, { icon: Dog, title: "A little tough love.", text: "Keep getting distracted? Fido barks. Then your phone goes for a walk." }].map((step, i) => <div className="step" key={step.title}><div className="step-top"><step.icon size={21}/><span>0{i + 1}</span></div><h3>{step.title}</h3><p>{step.text}</p></div>)}</div></section>
          <div className="demo-controls"><div><span className="section-label"><Sparkles size={13}/> THE DEMO PLAYGROUND</span><p>Try a distraction to see Fido’s personality.</p></div><div className="demo-buttons"><button disabled={!active} onClick={distract}><AudioLines size={15}/> Simulate distraction</button><button disabled={!active || session.phase === "focusing"} onClick={() => { setSession(s => transition(s, { type: "refocus" })); setEvents(e => ["Back on track", ...e]); }}><Focus size={15}/> Refocus</button></div></div>
        </>}
        {tab === "Activity" && <div className="activity-layout"><section className="surface"><div className="section-heading"><h2>Your focus journal</h2><span className="soft-badge">{history.length} completed</span></div>{history.length ? history.map((entry, i) => <div className="history-row" key={`${entry.date}-${i}`}><span className="stat-icon"><Check size={18}/></span><div><strong>{entry.task}</strong><p>{new Date(entry.date).toLocaleDateString()} · {entry.distractions} distractions</p></div><span>{entry.minutes} min</span></div>) : <div className="empty-state"><Leaf size={38}/><h3>Your next small win starts here.</h3><p>Finish a focus session and it’ll find a home in your journal. No made-up streaks. Just your progress.</p><button className="primary-button" onClick={() => setTab("Focus")}>Make time for focus <ArrowRight size={17}/></button></div>}</section><section className="surface"><h2>In this session</h2><p className="muted">Simulated events, not camera analysis.</p>{events.length ? events.map((event, i) => <div className="event-row" key={i}><span className="status-dot"/>{event}</div>) : <p className="empty-small">A clean slate. Start a session to see your timeline.</p>}</section></div>}
        {tab === "Devices" && <><div className="device-grid">{[{ icon: Dog, title: "Fido", detail: "ESP32 · OLED · motion control", status: "Simulated" }, { icon: Laptop, title: "Your laptop", detail: "Focus dashboard · local browser", status: "This browser" }, { icon: Smartphone, title: "Your phone", detail: "Camera · companion dashboard", status: "Not paired" }].map(device => <section className="surface device-card" key={device.title}><span className="device-icon"><device.icon size={32}/></span><span className="soft-badge">{device.status}</span><h2>{device.title}</h2><p>{device.detail}</p><div className="device-bottom"><ShieldCheck size={15}/> {device.title === "Your laptop" ? "Frontend is running locally" : "Backend connection not configured"}</div></section>)}</div><section className="surface camera-panel"><div><span className="section-label">CAMERA PREVIEW</span><h2>Your privacy comes first.</h2><p>Opt into a local preview. No video is recorded, uploaded, or analyzed. Presage and OpenCV need a backend integration.</p><button className="secondary-button" onClick={toggleCamera}><Camera size={17}/>{camera ? "Turn camera off" : "Enable camera preview"}</button></div><div className="camera-preview">{camera ? <video ref={video} autoPlay playsInline muted aria-label="Local camera preview"/> : <><Camera size={30}/><span>Your camera is off</span></>}</div></section><div className="privacy-note"><ShieldCheck size={17}/><span>Physical movement is disabled. A hardware safety interlock and tabletop boundary detection must be implemented before connecting motors.</span></div></>}
        <footer><span><Dog size={15}/> A little company. A lot more focus.</span><span>Made for intentional days <span className="footer-flower">✳</span></span></footer>
      </div>
    </main>
    <nav className="mobile-nav" aria-label="Mobile navigation">{(["Focus", "Activity", "Devices"] as Tab[]).map((name, i) => { const Icon = [Focus, LayoutGrid, Smartphone][i]; return <button key={name} aria-current={tab === name ? "page" : undefined} className={tab === name ? "selected" : ""} onClick={() => setTab(name)}><Icon size={21}/><span>{name}</span></button>; })}<button onClick={() => setModal("settings")}><Settings2 size={21}/><span>Settings</span></button></nav>
    {notice && <div className="toast" role="status"><span>{notice}</span><button className="icon-button" aria-label="Dismiss message" onClick={() => setNotice("")}><X size={16}/></button></div>}
    {modal && <div className="modal-backdrop" onClick={() => setModal(null)}><section className="modal surface" role="dialog" aria-modal="true" aria-labelledby="modal-title" onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === "Escape") setModal(null); }}><button autoFocus className="modal-close icon-button" onClick={() => setModal(null)} aria-label="Close dialog"><X size={21}/></button><span className="modal-icon"><Dog size={28}/></span><h2 id="modal-title">{modal === "settings" ? "Make yourself at home." : "Meet your new focus friend."}</h2>{modal === "settings" ? <><p>A few little things to make focus feel like you.</p><label className="setting-row"><span><strong>Demo sound</strong><small>A short tone at the barking stage.</small></span><input type="checkbox" checked={sound} onChange={e => setSound(e.target.checked)}/></label><div className="setting-row"><span><strong>Camera preview</strong><small>Optional. Local to this browser.</small></span><button className="secondary-button" onClick={toggleCamera}>{camera ? "Disable" : "Enable"}</button></div><div className="privacy-note"><ShieldCheck size={19}/><span>Demo mode is always on. No ESP32, Presage, remote sync, or motor connection is active.</span></div></> : <><p>Fido gives your phone a place to rest so you can do your best work.</p><ol className="help-list"><li>Set one goal and choose your focus time.</li><li>Start a session. Phone docking is simulated.</li><li>Tap “Simulate distraction” to nudge, bark, then escape.</li><li>Refocus to return to calm. Complete a session to save it locally.</li></ol><p className="muted">Camera preview is optional and does not detect distraction. History stays in this browser. No biometrics or medical measurements are claimed.</p></>}<button className="primary-button" onClick={() => setModal(null)}>Sounds good <Check size={17}/></button></section></div>}
  </div>;
}
