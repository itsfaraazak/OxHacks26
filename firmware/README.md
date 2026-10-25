# Integrated Fido ESP32 firmware

`src/main.ino` is the Arduino entry point. Existing backend/audio modules now
use the hardware verified in `esp_component_test/`. The legacy `esp_firmware/`
and standalone component test are unchanged.

**Motion remains compiled out (`ENABLE_MOTION=0`). Integrated hardware is not
yet verified.** Use a clear floor, supervision and a physical power cutoff.
Ultrasonic sensing cannot detect cliffs or reliably detect every obstacle.

## Wiring

| Function | GPIO |
|---|---|
| MX1508 left IN1 / IN2 | 25 / 26 |
| MX1508 right IN3 / IN4 | 27 / 14 |
| Button to GND, external pull-up to 3.3V | 35 |
| HC-SR04 Trig / divided Echo | 33 / 34 |
| SSD1306 128x64 at 0x3C, SDA / SCL | 13 / 32 |
| Servo signal | 12 |

Both motor inversions and calibrated turn commands retain the tested prototype.
The driver uses four PWM inputs, not six-wire PWM/enable wiring.
Use appropriate motor/servo supplies and common ground. HC-SR04 Echo needs
its voltage divider. OLED I2C levels and button pull-up must be 3.3V-safe.

**GPIO12 workaround:** disconnect servo signal for reset/upload, reconnect after
boot. Centre pulses start after 10 seconds (or later if Bluetooth startup is
slow). It stays at 90°/1500us; no random tail behavior is defined. Keep the horn
detached initially. This is not a reliable unattended boot design.

## Build and flash

```sh
pio test -d firmware -e native
pio run -d firmware
python firmware/tools/prepare_clips.py
pio run -d firmware -t upload
pio run -d firmware -t uploadaudio
pio device monitor -d firmware
```

Clip conversion requires **ffmpeg on PATH**. Upload both application and audio
image. The 4MB partition map reserves 1536KB for audio, with no OTA. Ordinary
uploads preserve NVS; full flash erase loses settings.

Bluetooth requires original ESP32, not S3/C3. Default speaker:
**Acoustic Solutions WJ-201**. Put it in pairing mode; disconnect competing
phones/laptops. Actual pairing/audio quality are not yet bench-tested.

## Runtime setup

Connect a phone to **Fido-Setup**, open **http://192.168.4.1** manually.
Keep laptop connected to the main Wi-Fi.

- Server accepts `http://<IPv4>:<port>` or `<IPv4>:<port>`; omitted port = 5000.
- Speaker name is editable.
- First setup: expand Wi-Fi details; enter SSID/password and choose a setup
  password (8–63 characters). Blank credential fields retain saved settings later.
- Optional device token must match backend `FIDO_DEVICE_TOKEN`.
- Save stops motors, redirects to the form, then restarts.

The same `fido` NVS keys preserve old Wi-Fi/server/setup-password settings
across ordinary uploads and restarts. NVS is not encrypted by this sketch.
First setup AP is open until secured; provision somewhere trusted.
Page is restricted to AP connections; anyone with setup Wi-Fi access can edit.
Forgotten setup password requires clearing board NVS. No captive portal.
Connecting a setup client inhibits motor movement; disconnect before motion tests.

## Behavior

| Backend mode | Hardware |
|---|---|
| sleep, rest, watch, bark, celebrate | Stopped; OLED state/task |
| beckon_dock | OLED docking prompt; button sends `docked` |
| nudge | Once: forward 300ms, pause 50ms, reverse 300ms |
| run | Forward; calibrated right turn below 20cm, forward again at 25cm |

Fresh valid range is required for movement. Missing, invalid or stale readings
stop motion; no echo is not clear space. Rear obstacles are not sensed.
Catch button stops locally immediately and reports `caught`, holding stopped
until the backend leaves run. Other presses only log. Failed device events
retry from the queue. No automatic phone-removal/NFC detection.
The backend clears the docking acknowledgement on every new session, so each
start needs a fresh press unless "Start without docking" is enabled.

Safety gates retain backend permission, 3-second watchdog, two HTTP errors,
serial e-stop, compile-time enable and unknown/still modes. Local escape cap is
10 minutes even if server remains in run. All obstacle turns pass the same gate.
Direction reversals have an all-off interval. Run/nudge speed caps remain
35%/25%, maximum 60%: not full-power component-test settings.

Backend requests run on core 0; control and Bluetooth app run on core 1.
Echo uses interrupts, not blocking pulseIn. OLED refreshes on mode changes.
Serial status includes range/freshness. Button uses 30ms debounce.

Audio uses backend `speech.clip` and `speech.seq`, not just mode changes.
Boot/reconnect does not replay old cues. Mute consumes silent sequences.
The six recorded clips are converted to ADPCM and played through A2DP.
Only a complete audio image plus BT connection reports speaker `connected`;
otherwise laptop fallback is retained. See `../docs/audio.md`.

## Acceptance checks before enabling movement

Serial 115200: `s` status, `x/r` latch/release e-stop, `d/u/c` simulate device
events, `l` list clips, `p` + name + Enter play clip, `h` help.

1. Native tests/build pass; application and audio images uploaded.
2. Wheels lifted, motor supply disconnected, servo linkage detached.
3. Verify boot workaround, setup persistence, OLED and real button events.
4. Pair speaker, play all six clips; heartbeats must continue during audio.
5. Verify simulated obstacle/stale sensor stop, catch stop, mute and fallback.
6. Explicitly enable motion in local hardware override, repeat wheels-lifted
   tests with physical cutoff ready, including backend/sensor loss and e-stop.
7. Floor testing only away from edges, people/animals and loose wiring.

Host tests do not establish wiring, braking distance, obstacle reliability,
Bluetooth quality or unattended boot safety. No drive-off-table routine exists.
