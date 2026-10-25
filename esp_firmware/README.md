# ESP32 local API test

One sketch, no motor movement. GPIO 25/26 (left) and 27/14 (right) stay LOW.
GPIO 2 lights while Wi-Fi is connected.

After flashing:

1. Keep the laptop on your Fido Wi-Fi network.
2. Connect your phone to `Fido-Setup`. On first boot it is open; do first
   setup somewhere trusted. Stay connected despite the "no internet" message.
3. Open `http://192.168.4.1` manually (no captive portal).
4. Enter the laptop IPv4 address, e.g. `10.56.237.125`.
5. Expand "First setup / change Wi-Fi": enter the Wi-Fi name and password,
   and choose a password for `Fido-Setup` (8-63 characters).
6. Save. The board restarts, protects `Fido-Setup` with your chosen password,
   joins the main Wi-Fi and polls the saved server.

For later IP changes, reconnect to `Fido-Setup`, edit only the laptop IP,
and save. Wi-Fi fields can stay blank. No USB setup or reflashing required.
Saved settings are loaded from ESP32 flash (NVS) on every boot. They are not
encrypted by this sketch; treat a provisioned board as containing credentials.
Save success means settings were stored, not that Flask was reachable.
If you forget the setup password, erasing board flash clears settings.

The ESP32 uses AP+STA mode: setup Wi-Fi and the main connection share one
radio/channel. The setup page rejects requests arriving on the main Wi-Fi
interface. Anyone with the setup Wi-Fi password can change settings.
HTTP setup is local and unencrypted; don't expose or forward it.

The laptop's default IP above may change with DHCP. There is no automatic
discovery; use the form again, or reserve its IP in your router if supported.

The local Flask backend must listen on `0.0.0.0:5000`, with firewall access.
This basic local test assumes `FIDO_DEVICE_TOKEN` is unset; do not expose it
to the internet. It sends only a heartbeat and simulated board status, not
camera frames or credentials.

```sh
pio run -d esp_firmware
```

The sketch POSTs `/api/robot/events` once a second and prints the requested
action. Only `run` with `motion_enabled: true` prints a movement request.
This is HTTP over TCP: the ESP32 initiates requests and the server replies,
not a server-initiated connection to the ESP32.
Errors, disconnection, and replies older than 3 seconds mean STOP.
HTTP calls can block briefly; this is a simulation, not a motor watchdog.
Actual movement needs a separate nonblocking safety implementation first.
