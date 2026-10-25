"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { speechUrl, type FidoState } from "@/lib/api";

/** How long a caption stays fully visible, then how long it takes to fade out (see `.speech-bubble`). */
const CAPTION_MS = 6000;
const FADE_MS = 500;
/** The user's explicit choice to hear Fido on this device. Absent means off. */
const STORAGE_KEY = "fido-hear-on-this-device";
/** A few milliseconds of silence, played muted during a tap to unlock the audio element on phones. */
const SILENCE = "data:audio/wav;base64,UklGRsQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";

export type Caption = { seq: number; text: string; fading: boolean };

/**
 * Fido's cues (docs/audio.md): a caption on every device, plus clips on this device only if the
 * user opted in. A cue is shown/played once: when its `seq` is greater than the last one seen. The
 * first snapshot after loading or reconnecting (`fresh`) just records `seq`, so old speech never
 * replays. Everything here fails silently apart from `notify`; nothing throws into the page.
 */
export function useSpeech(snap: { state: FidoState; fresh: boolean } | null, notify: (message: string) => void) {
  const [caption, setCaption] = useState<Caption | null>(null);
  const [hearHere, setHearHere] = useState(false);
  const lastSeq = useRef<number | null>(null);
  const hearRef = useRef(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const primed = useRef(false);
  const warned = useRef(false);

  /** The one `Audio` element. Created lazily (never during render), and reused so a tap unlocks it for good. */
  const element = useCallback(() => {
    if (audio.current) return audio.current;
    if (typeof Audio === "undefined") return null;
    const el = new Audio();
    el.preload = "auto";
    el.addEventListener("playing", () => { warned.current = false; });
    el.addEventListener("error", () => {
      // Our own priming clip and the pause()/src swaps never get here; a 404 or bad file does.
      if (el.getAttribute("src") === SILENCE || !el.getAttribute("src")) return;
      if (!warned.current) { warned.current = true; notify("Fido’s voice clip couldn’t be played on this device."); }
    });
    return (audio.current = el);
  }, [notify]);

  /** Play a silent clip muted. Must run inside a user gesture; afterwards programmatic plays are allowed. */
  const prime = useCallback(() => {
    const el = element();
    if (!el || primed.current) return;
    primed.current = true;
    el.muted = true;
    el.src = SILENCE;
    el.play().catch(() => { primed.current = false; }).finally(() => { if (el.getAttribute("src") === SILENCE) el.muted = false; });
  }, [element]);

  const setHear = useCallback((on: boolean) => {
    hearRef.current = on;
    setHearHere(on);
    try { if (on) localStorage.setItem(STORAGE_KEY, "1"); else localStorage.removeItem(STORAGE_KEY); } catch { /* storage may be blocked */ }
    if (on) prime(); // called from the click handler, so this is the user gesture
    else audio.current?.pause();
  }, [prime]);

  // A remembered choice still needs one tap on this page before a phone lets us play: unlock on the first.
  useEffect(() => {
    let remembered = false;
    try { remembered = localStorage.getItem(STORAGE_KEY) === "1"; } catch { /* ignore */ }
    if (!remembered) return;
    hearRef.current = true;
    setHearHere(true);
    const unlock = () => prime();
    window.addEventListener("click", unlock, { once: true, capture: true }); // touch counts as a gesture on click
    return () => window.removeEventListener("click", unlock, { capture: true });
  }, [prime]);
  useEffect(() => () => { audio.current?.pause(); }, []);

  useEffect(() => {
    if (!snap) return;
    const voiceEnabled = snap.state.settings.voice_enabled !== false;
    if (!voiceEnabled) audio.current?.pause();
    const speech = snap.state.speech ?? null;
    const last = lastSeq.current;
    if (snap.fresh || last === null) { lastSeq.current = speech?.seq ?? 0; return; }
    if (!speech || speech.seq <= last) return;
    lastSeq.current = speech.seq;
    setCaption({ seq: speech.seq, text: speech.text, fading: false });
    if (!hearRef.current || !voiceEnabled) return;
    const el = element();
    if (!el) return;
    try {
      el.pause(); // a new cue interrupts the previous clip
      el.muted = false;
      el.src = speechUrl(speech);
      el.play().catch(error => {
        // A newer cue swapping `src` aborts this play; that is not a failure.
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!warned.current) { warned.current = true; notify("Fido’s voice is blocked on this device. Tap “Hear Fido on this device” to allow it."); }
      });
    } catch { /* never throw into the page */ }
  }, [snap, element, notify]);

  const seq = caption?.seq;
  useEffect(() => {
    if (seq === undefined) return;
    const fade = setTimeout(() => setCaption(c => c && c.seq === seq ? { ...c, fading: true } : c), CAPTION_MS);
    const clear = setTimeout(() => setCaption(c => c && c.seq === seq ? null : c), CAPTION_MS + FADE_MS);
    return () => { clearTimeout(fade); clearTimeout(clear); };
  }, [seq]);

  return { caption, hearHere, setHear };
}
