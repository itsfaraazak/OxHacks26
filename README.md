# OxHacks26: Fido, the focus robot dog

Dock your phone on Fido to commit to a task. If a camera sees you slacking, Fido
nudges you, then barks, then runs off with your phone until you catch it.

| Directory | Contents |
| --- | --- |
| [`backend/`](backend/README.md) | Flask backend, focus detector agent, Presage harness |
| [`archive/oxhack26-prototype/`](archive/oxhack26-prototype) | Vayun's detection prototype ([vayunk/Oxhack26](https://github.com/vayunk/Oxhack26) @ `9e49425`), kept verbatim |

The Next.js frontend is at the repository root (below). Robot code is on hold
until the board is chosen.

## Frontend: Fido, your little focus companion

A mobile-first Next.js, React, TypeScript, and Tailwind CSS frontend for the
OxHacks 2026 robot dog. Warm neutrals, a lime accent, and a custom illustrated
robot keep the interface calm and playful. No external image or font services.

The frontend only displays backend state and sends commands. The Flask backend
in [`backend/`](backend/README.md) owns the session, its clock, and the
escalation rules; see [`docs/frontend-integration.md`](docs/frontend-integration.md).

## Run it

Requires Node.js 20.9+, npm, and Python 3.11+. From **the directory containing
this file**:

```sh
sh run.sh
```

This starts the backend on `http://localhost:5000` (creating `backend/.venv` on
the first run) and the frontend on `http://localhost:3000`. Stop both with Ctrl+C.

```sh
sh run.sh backend       # backend only
sh run.sh dev           # frontend only (backend already running)
PORT=3001 sh run.sh     # choose a different frontend port
sh run.sh check         # TypeScript + backend unit tests
sh run.sh test          # Playwright end-to-end tests against the real backend
sh run.sh build         # production build
sh run.sh start         # serve the production build
```

Without a robot, open Settings and turn on **Start without docking**. To get
real distraction detection, run the detector agent on the laptop (see
[backend/README.md](backend/README.md#run-it-locally)):

```sh
cd backend && .venv/bin/pip install -r requirements-detector.txt
.venv/bin/python -m detector.agent
```

`run.sh` is the editable source for setup and launch commands; `package.json`
contains the underlying npm scripts.

### Where the frontend finds the backend

`NEXT_PUBLIC_FIDO_API` sets the backend URL (for example a hosted backend, at
build time). Without it, the page uses port 5000 on the host that served it, so
a phone on the same trusted Wi-Fi can open the network URL printed by Next.js
(`http://<laptop-ip>:3000`) and talk to `http://<laptop-ip>:5000` with no
configuration. If the backend can't be reached, the page shows an offline banner
and retries.

## What works

- Live state from `GET /api/events` (Server-Sent Events); every phone and laptop
  shows the same session. A status badge shows Live / Reconnecting / Offline.
- Start, pause, resume, end, and "I caught Fido" go to the backend; on a 409 the
  page applies the state the backend returns, and errors show in a toast.
- The countdown shows the backend's `remaining_seconds`, interpolated locally
  between events; there is no frontend timer or state machine.
- Every new session is 25 minutes; custom session durations are not supported.
- Phases: awaiting phone, focusing, nudge, barking, escaped, paused, completed, failed,
  with the backend's `reason` shown during nudge, barking, and escaped.
- Activity: the journal comes from `GET /api/history`; the session timeline from
  the session's `events`.
- Devices: Fido's robot (online, docked, mode, info such as battery), one card per
  reporting camera, and Presage vitals when a scan has been forwarded.
- Settings (`PATCH /api/settings`): escalation timings, Start without docking,
  and Fido can move (also an emergency stop).
- Responsive desktop sidebar and phone bottom navigation.

## Testing

`npx playwright test` (or `sh run.sh test`) starts the backend on :5000 and the
frontend on :3000 unless they are already running, then drives the real journey:
it shortens escalation through the API, fakes detector readings with
`POST /api/readings` to go nudge → barking → escaped, clicks "Stop Fido",
checks the failed state, explicitly starts a fresh timer, and checks the
journal. Tests share one backend, so they run
serially and reset the session and settings around each test. Install the
browser once with `npx playwright install chromium`.

**Hardware safety:** turning off "Fido can move" tells the robot to stop its
motors, but the robot must also stop on its own when it loses contact (see
[backend/README.md](backend/README.md#devices)). Do not rely on the frontend as
a safety system.

## Presentation references

The supplied Opening Ceremony Slides emphasize snappy UX (30%), technical
implementation (25%), innovation (25%), and polish/usability (20%), with a
Presage challenge for camera-based sensing. Vitals shown in the app come from a
real Presage scan forwarded by the detector agent, and are not medical
measurements.
