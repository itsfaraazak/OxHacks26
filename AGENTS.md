# Project instructions

## Shared project context

OxHacks26 is building **Fido**, a focus-companion robot dog for OxHacks 2026. The phone is carried by the rover; it is not the robot's camera. A dog-mounted webcam and/or laptop camera support focus detection. The intended demo escalation is focus → nudge → bark → escape; when the user catches the dog it returns to focus checking, with a 10-minute stop timeout. Treat movement as potentially hazardous: keep hardware actions simulated unless the user explicitly requests hardware control and safety is established.

Work across frontend, backend, and prototype threads has established these constraints:

- The Next.js/React/Tailwind frontend lives on the `frontend` branch. Its typed session state machine is in `src/lib/focus.ts`; use it as the frontend integration contract, not as a reason to dictate backend internals.
- The backend should reuse teammate Vayun's experimental prototype and the Presage SDK used there. Do not add a Node sidecar or firmware work unless explicitly requested. The source prototype should remain preserved in its archive as requested.
- Keep the MVP simple. Explain the need before introducing persistence or other infrastructure; TigerData is optional, not a default requirement. Evaluate REST and real-time transport against actual client needs.
- ElevenLabs voice is a later enhancement; prioritize essentials first.
- Preserve privacy: camera frames and biometric signals should be handled deliberately, with retention and transmission documented before implementation.

## Working rules

- Before changing a directory, check its applicable `AGENTS.md`/`AGENTS.md` ancestor instructions and follow more specific guidance.
- Inspect the existing implementation and tests before choosing an architecture. Reuse established project conventions and verify changes with the narrowest relevant checks.
- Keep API contracts, state-machine behavior, and hardware safety boundaries explicit. Document assumptions when the repository or project context does not settle them.
- You may use subagents when you judge that delegation would improve the work or make it faster; keep tasks independent, bounded, and review their results before relying on them.
- Treat `README.md`, source code, tests, package/config files, and archived prototype as the implementation sources of truth; update documentation when behavior changes.
