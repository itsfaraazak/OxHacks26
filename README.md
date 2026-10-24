# Fido — your little focus companion

A mobile-first Next.js, React, TypeScript, and Tailwind CSS frontend for the
OxHacks 2026 robot dog. Warm neutrals, a lime accent, and a custom illustrated
robot keep the interface calm and playful. No external image or font services.

## Run it

Requires Node.js 20.9+ and npm. From **the directory containing this file**:

```sh
sh run.sh
```

The launcher installs dependencies on its first run and prints the local URL
(default `http://localhost:3000`). Stop with Ctrl+C.
Next.js is a project-local dependency: `npm ci` installs the exact locked
Next.js version into `node_modules/.bin/next`; do not install Next.js globally.
If an earlier install was interrupted and `node_modules` exists without the
Next.js executable, `sh run.sh` detects this and reinstalls the locked
dependencies. If npm reports a network error, verify the host has access to the
npm registry, then run `npm ci` from the project root and retry.

```sh
PORT=3001 sh run.sh dev  # choose a different port
sh run.sh check         # TypeScript + 12 state-machine tests
sh run.sh build         # production build
sh run.sh start         # serve the production build
npx playwright test    # phone + desktop browser smoke tests
```

## Deployment

Deploy with a Node.js hosting service (or a Node.js server/container). The
runtime must provide Node.js 20.9+ and npm during the dependency-install/build
steps. From the project root:

```sh
npm ci
npm run build
npm start
```

The Next.js production server listens on `0.0.0.0` and uses `PORT` if the
platform supplies it (otherwise port 3000). Set the platform's start command
to `npm start`, and build command to `npm run build`. Include the committed
`package-lock.json` in deployments so `npm ci` installs a reproducible set of
dependencies. For container builds, install dependencies and build inside the
container/platform environment; do not copy a macOS `node_modules` directory
into a Linux deployment. A static export is not configured. The optional
browser camera preview requires HTTPS in production; the frontend itself does
not require camera access to run.

`run.sh` is the editable source for setup and launch commands; `package.json`
contains the underlying npm scripts.

For a phone on the same trusted Wi-Fi network, open the network URL printed by
Next.js. A camera preview requires HTTPS or localhost, so it normally will not
work on a plain HTTP LAN URL. The rest of the demo works without a camera.

## What works

- Focus task and 15/25/45/60-minute duration selection.
- Timer, pause/resume, end session, and completion.
- Simulated distraction escalation: focusing → nudge → barking → escaped.
- Refocus resets escalation without erasing the distraction count.
- Optional short demo sound at the barking stage.
- Completed-session journal saved in this browser's local storage.
- Devices view and opt-in local camera preview; tracks stop when disabled.
- Responsive desktop sidebar and phone bottom navigation.

**This is a frontend simulation.** No phone docking, remote device pairing,
biometric sensing, distraction detection, or motor commands are implemented.
Stats start at zero rather than presenting invented measurements.

## Backend handoff

`src/lib/focus.ts` owns the pure, immutable session transitions and exports
`FocusState`, `FocusAction`, and the asynchronous `FocusGateway` interface.
The page currently calls the pure transitions directly. Implement that gateway
with `fetch` for either Next.js route handlers or a separate Flask/Django API,
then route UI actions through it. The backend should own authoritative session
time and state; publish updated state over WebSocket/SSE for phone/laptop sync.

Suggested routes (not implemented):

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/sessions` | Create session from task and duration |
| POST | `/api/sessions/:id/actions` | Validate and apply a typed action |
| GET | `/api/sessions/:id` | Read authoritative state |
| GET | `/api/devices` | Read ESP32, laptop, phone connectivity |
| WS | `/api/events` | Docking, consented detection, device state |

Keep ESP32 credentials and Presage keys server-side. Treat detection as uncertain
signals, not proof that a person is unproductive. Obtain explicit camera consent;
do not transmit raw camera frames without a deliberate privacy design. The current
preview never records, uploads, or analyzes frames.

**Hardware safety:** the escape state is visual only. Before enabling physical
motion, implement emergency stop, tabletop edge detection, and a hardware safety
interlock. Do not use the frontend timer or a “controlled fall” as a safety system.

## Presentation references

The supplied Opening Ceremony Slides emphasize snappy UX (30%), technical
implementation (25%), innovation (25%), and polish/usability (20%), with a
Presage challenge for camera-based sensing. This frontend demonstrates the user
journey without claiming a Presage integration or fabricated biometrics.
