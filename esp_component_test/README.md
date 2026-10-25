# Standalone component test

This replaces the running firmware when uploaded; it does not use Wi-Fi or the
backend. The main sketch in `esp_firmware/` remains separate and unchanged.

**Before uploading:** lift wheels, detach the servo horn/linkage, confirm the
button pull-up is connected to 3.3V and Echo has its voltage divider.
Use suitable separate motor/servo power and common ground. GPIO12 is
boot-sensitive; verify boot with the servo connected and powered.

| Component | GPIO |
|---|---|
| MX1508 left IN1 / IN2 | 25 / 26 |
| MX1508 right IN3 / IN4 | 27 / 14 |
| Button to GND, external pull-up to 3.3V | 35 |
| HC-SR04 Trig / divided Echo | 33 / 34 |
| SSD1306 128x64 SDA / SCL | 13 / 32 |
| Servo signal | 12 |

Power HC-SR04 from regulated 5V; OLED from supported 3.3V.
For a regulated 5V Echo: 330 ohms from Echo to GPIO34, then two series
330-ohm resistors from GPIO34 to GND. Button can use the previously discussed
330-ohm pull-up (about 10mA while pressed).

```sh
pio run -d esp_component_test
pio run -d esp_component_test -t upload
pio device monitor -d esp_component_test
```

One debounced button press advances one step:

Idle → forward → reverse → left → right → distance → scrolling OLED
→ servo 30° → 90° → 150° → idle → repeat.

Motors run at 255/255 duty (full power) and stop after 3 seconds; the next click still
advances normally. If they buzz without turning, stop and check power/load
before adjusting `MOTOR_SPEED`. Polarity and turn commands preserve the
original tested prototype: both motors inverted, left turn uses logical
left-forward/right-reverse, right turn the opposite. Verify the labels on
your chassis with wheels lifted.

Servo pulses use a conservative 1000–2000us mapping, so nominal angle labels
are not guaranteed physical angles. It holds position until the next mode.
No movement starts on boot. OLED defaults to address 0x3C; change
`OLED_ADDRESS` if your module uses 0x3D. Missing OLED does not prevent other
tests. The loop is a bench test, not autonomous driving or obstacle avoidance.
