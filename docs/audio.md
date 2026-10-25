# Fido audio: shared contract

Fido talks at fixed moments of a session. Its voice is **six recordings of our
friend Dheer** (two spoken lines and four dog sounds), not live speech and not
AI-generated: no generated voice, no voice clone. The backend decides *when* to
speak; devices decide *how* to play.

Players, in priority order:

1. **The robot's Bluetooth speaker**, driven by the ESP32 (`firmware/`).
2. **The laptop** (`backend/audio/player.py`), as a fallback while the robot
   reports no connected speaker.
3. **A judge's phone**, opt-in only (the web app).

Which clip plays at which cue is one table, `PLAYLIST` in
`backend/fido/speech.py` (see "Clips" below). **Nothing is generated while a
session runs, and no task text or other user input is used for audio.**

## Cue in the state

The state snapshot (`GET /api/state`, SSE `/api/events`) and the robot command
(`POST /api/robot/events`, `GET /api/robot/command`) gain `speech`:

```json
"speech": {"seq": 7, "cue": "nudge", "clip": "nudge-1",
           "text": "Psst... your task is waiting for you. I believe in you.",
           "at": 1792881318.1, "url": "/api/audio/nudge-1.mp3"}
```

`speech` is `null` until the first cue. `seq` increases by one for every new
cue. Players attempt each new sequence at most once and ignore duplicates.
On the first snapshot after starting or reconnecting they just record `seq`
without playing, so a reload never replays old speech. This is not guaranteed
delivery: a download failure or Bluetooth disconnect can lose a cue. There is
no playback acknowledgement or retry of a consumed sequence.

Cues are emitted by the engine on transitions: `dock` (session created, waiting
for the phone), `start` (phone docked, watching begins), `nudge`, `bark`,
`run` (escape begins, then repeated every `speech_repeat_seconds` = 25 s while
escaped), `caught`, `timeout` (escape ended by the 10 minute timer), `praise`
(refocused before escaping), `done`. When a cue has several clips they rotate
in order. Setting `voice_enabled` to `false` (default `true`) mutes playback,
not caption events: the full state still emits cues and advances `seq`.
The frontend shows captions independently of both global mute and its local
audio opt-in. Frontend and laptop players consume muted sequences without
playing them, so unmuting does not replay them. Robot command responses retain
the current `seq` but send empty `clip` and `url` while muted. The firmware's
sequence gate records these silent events, preventing replay on unmute.

## Clips

- `GET /api/audio` -> `{"clips": {"nudge-1": {"cue": "nudge", "caption": "...", "available": true}, ...}}`
- `GET /api/audio/<clip>.mp3` -> the MP3 (404 if unknown or not generated).
  Public (phones need it), cacheable.
  `/api/audio` lists only clips that are in the playlist.
- Files live in `backend/clips/*.mp3` (committed): `dock-1`, `nudge-1`,
  `bark-4`, `bark-5`, `run-5`, `woof-1`. They are Dheer's recordings, processed
  by `backend/scripts/process_recordings.py` (MP3, mono, 22.05 kHz, 32 kbps).
  How they were made is in `backend/README.md` ("Fido's voice (recorded
  clips)").

| Clip | Says (caption) |
| --- | --- |
| `dock-1` | Ooh, a new goal! Pop your phone on my back and let's get started! |
| `nudge-1` | Psst... your task is waiting for you. I believe in you. |
| `bark-4` | Woof! Woof! |
| `bark-5` | Woof woof woof! |
| `run-5` | Grrr... WOOF! |
| `woof-1` | Woof! |

### Which clip plays when

`PLAYLIST` maps a cue to a list of clip names. A clip may appear under several
cues; a cue that is missing or empty is silent; a cue's clips rotate in order.

| Cue | Clips |
| --- | --- |
| `dock` | `dock-1` |
| `start` | `woof-1` |
| `nudge` | `nudge-1` |
| `bark` | `bark-4`, `bark-5` |
| `run` | `run-5`, `bark-5` (repeats every `speech_repeat_seconds`) |
| `caught` | `bark-4` |
| `timeout` | `nudge-1` |
| `praise` | `woof-1` |
| `done` | `bark-5` |

To reuse clips differently, change the table. Captions shown in the app come
from `CAPTIONS` (the words of each clip) in the same file; a caption must match
what the clip actually says.

### Privacy

The clips are Dheer's voice. Committing and pushing them publishes his voice,
which he consented to for this hackathon project only. The consent record is
`backend/recordings/consent.json`; the raw takes in `backend/recordings/` are
gitignored and never committed.

## Who plays now: the robot reports its speaker

The robot's heartbeat `info` includes `"speaker"`: `"connected"`,
`"disconnected"` or `"none"`. Shown at `state.robot.info.speaker`.
The audio firmware reports `connected` only when Bluetooth is connected and
its flash image contains all six required, nonempty clips. A missing or
incomplete image reports `none`, keeping laptop fallback available.

**The laptop player stays silent while `robot.online` is true and
`robot.info.speaker == "connected"`**; otherwise it plays. Phones never
auto-play; they play only after the user taps "Hear Fido on this phone".

## Robot-side playback

There are separate PlatformIO projects. `esp_firmware/` on main retains its
Wi-Fi setup webpage and saved configuration; `esp_component_test/` retains
its standalone component test. Neither is modified by this audio addition.
The audio-capable application is `firmware/`, configured through its gitignored
`include/fido_config.h`. Flashing it replaces the running ESP32 application;
it does not include the other project's setup webpage. Do not flash it over
a teammate's working setup without coordinating. Motion is compiled out by
default, and its motor pins are placeholders.

The ESP32 stores the clips in flash (the DevKit V1 has 4 MB shared with the
program; these six clips are a few seconds and fit easily) and plays the clip named in `speech.clip` over
Bluetooth A2DP. Unknown clip names are ignored. Wi-Fi and A2DP share one radio,
so the heartbeat must keep working during playback, and the motors must obey
the existing safety rules regardless of audio state (stop if no reply for 3 s or
`motion_enabled` is false).

## Secrets

There are none for audio: the running server and the players need no key. The
ElevenLabs generator (`scripts/generate_audio.py`, key `ELEVENLABS_API_KEY` in
the gitignored `backend/.env`) and the voice clone are **not used**; they stay
in the repository for a possible future AI voice.
