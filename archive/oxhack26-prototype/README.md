# Repo Hound 🐶📱
> **The Anti-Procrastination Robot Dog that holds your phone hostage.**
> Built at **OxHack '26** (Theme: **TIME**).

---

## 🎯 The Core Concept
Human willpower is weak. Traditional focus apps fail because on-screen notifications carry zero physical consequence. **Repo Hound** physically enforces your focus:

1. **The Lock-In**: You dock your personal smartphone into a cushioned saddle on Repo Hound's back to commit to a focus session.
2. **The Sentry**: The laptop webcam monitors your attention, posture, and biometrics in real time using **Presage SmartSpectra** (rPPG vitals & face telemetry).
3. **The Heist**: The moment you slack off (looking away, doomscrolling on a second screen, slumping, or abandoning your desk for > 5 seconds), Repo Hound triggers:
   - Its animated face screen flashes angry red.
   - High-volume alarm barks sound.
   - The robot car **drives off the table (landing safely on our shock-absorbing skid / cord release) and speeds across the room carrying your phone**.
4. **The Rescue**: You physically have to get up, chase down the robot dog, and tap an NFC "good boy" card on its collar to disarm the alarm and reclaim your device.

---

## 🏗️ Architecture

```
                       +-----------------------------------+
                       |          LAPTOP (THE BRAIN)       |
                       |  Webcam running Focus Detection   |
                       |  (Presage rPPG + Face Telemetry)  |
                       +-----------------+-----------------+
                                         |
                                         | Local Wi-Fi / WebSockets
                         +---------------+---------------+
                         |                               |
                         v                               v
             +-----------------------+       +-----------------------+
             |    PHONE ON DOG FACE  |       |   ESP32 MOTOR BASE    |
             |   Animated Dog Eyes   |       |   2x DC Gear Motors   |
             |  Loud Barks & Voices  |       |   L298N Motor Driver  |
             +-----------------------+       +-----------------------+
```

---

## 🛠️ Hardware & Drop Survival (75cm Desk Drop)
* **Chassis**: Rigid double-layer corrugated baseplate with zip-tied DC motors.
* **Axle Protection (Belly Skid)**: Dense foam impact block mounted underneath (~3–5mm off the floor) that absorbs 90% of landing shock, shielding the motor shafts.
* **Pitch Control (Snowboard Nose + Tail Drag)**: Upward-curved front bumper prevents nose-dive cartwheels; rear drag tail ensures flat landing.
* **Controlled Descent (Optional)**: Slack-release bungee tether that gently lowers the bot and disconnects the moment wheels touch the floor.
* **Phone Nest**: Floating bubble-wrap sleeve with top retention strap.

---

## 🏆 Tracks & Prize Targets
* **OxHack Main Track (Theme: TIME)**: First Place / Runner Up — A visceral, physical embodiment of stolen time and forced productivity.
* **Citadel Best Handmade Project**: Fully custom handmade robot chassis, shock-absorption mechanics, and dog-body fabrication.
* **Best Use of Presage**: Contactless facial vitals, engagement, and attention telemetry via the SmartSpectra SDK.
* **Best Use of ElevenLabs**: Dynamic synthesized voice lines and reactive dog audio.
