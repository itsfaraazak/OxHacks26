#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WebServer.h>

Preferences config;
String serverUrl;
WebServer setupServer(80);
bool restartPending = false;
unsigned long savedAt = 0;

void setupPage() {
    // Setup is accessible only through the ESP32's own access point.
    if (setupServer.client().localIP() != WiFi.softAPIP()) {
        setupServer.send(403, "text/plain", "Connect to Fido-Setup first.");
        return;
    }
    if (setupServer.method() == HTTP_POST) {
        IPAddress address;
        String ip = setupServer.arg("ip");
        ip.trim();
        String ssid = setupServer.arg("ssid");
        String password = setupServer.arg("password");
        String apPassword = setupServer.arg("ap_password");
        String oldApPassword = config.getString("ap_password", "");
        if (!address.fromString(ip) || address == IPAddress(0, 0, 0, 0) ||
            address == IPAddress(255, 255, 255, 255) ||
            ssid.length() > 32 ||
            (ssid.length() > 0 && (password.length() < 8 || password.length() > 63)) ||
            (apPassword.length() > 0 && (apPassword.length() < 8 || apPassword.length() > 63)) ||
            (oldApPassword.isEmpty() && apPassword.isEmpty()) ||
            (config.getString("ssid", "").isEmpty() && ssid.isEmpty())) {
            setupServer.send(400, "text/plain",
                "Enter a valid laptop IPv4 address. First setup also requires Wi-Fi name, "
                "Wi-Fi password and setup password (8-63 characters). Go back to retry.");
            return;
        }
        String url = "http://" + address.toString() + ":5000";
        bool saved = config.putString("server", url) > 0;
        if (!ssid.isEmpty()) {
            saved = (config.putString("ssid", ssid) > 0) && saved;
            saved = (config.putString("password", password) > 0) && saved;
        }
        if (!apPassword.isEmpty()) saved = (config.putString("ap_password", apPassword) > 0) && saved;
        if (!saved) {
            setupServer.send(500, "text/plain", "Could not save all settings. Please retry.");
            return;
        }
        setupServer.send(200, "text/plain",
            "Saved. Fido will restart and use this address. Reconnect to Fido-Setup "
            "with your setup password if you need to change it again.");
        restartPending = true;
        savedAt = millis();
        return;
    }
    String ip = serverUrl.substring(7);
    ip = ip.substring(0, ip.indexOf(':'));
    // Only a parsed address is inserted into HTML.
    IPAddress address;
    if (!address.fromString(ip)) ip = "";
    String page = R"HTML(<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fido setup</title><style>
body{font:16px system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem;color:#222;background:#fafafa}
label{display:block;margin-top:1.2rem}input,button{box-sizing:border-box;width:100%;font:inherit;padding:.75rem;margin-top:.4rem}
button{margin-top:1.5rem;cursor:pointer}small{display:block;margin-top:.4rem}
</style><main><h1>Fido setup</h1><p>Motors are disabled. Settings survive restarts.</p>
<form method="post" action="/"><label for="ip">Laptop IP address</label>
<input id="ip" name="ip" required maxlength="15" inputmode="decimal" value=")HTML";
    page += ip;
    page += R"HTML("><small>Flask server uses port 5000.</small>
<details><summary>First setup / change Wi-Fi</summary>
<label for="ssid">Wi-Fi name</label><input id="ssid" name="ssid" maxlength="32" placeholder="Fido">
<label for="password">Wi-Fi password</label>
<input id="password" name="password" type="password" minlength="8" maxlength="63" autocomplete="new-password">
<label for="ap_password">Fido-Setup password</label>
<input id="ap_password" name="ap_password" type="password" minlength="8" maxlength="63" autocomplete="new-password">
<small>Required on first setup. Leave blank later to keep saved settings.</small>
</details><button type="submit">Save and restart</button></form>
<p>Keep your laptop on the Fido network. Use your phone here.</p></main></html>)HTML";
    setupServer.sendHeader("Cache-Control", "no-store");
    setupServer.send(200, "text/html", page);
}

unsigned long lastPoll = 0;
unsigned long lastReply = 0;
bool commandFresh = false;

void pollBackend() {
    HTTPClient http;
    http.setConnectTimeout(500);
    http.setTimeout(500);
    http.useHTTP10(true); // Plain JSON stream, no HTTP chunk framing.
    http.begin(serverUrl + "/api/robot/events");
    http.addHeader("Content-Type", "application/json");
    int status = http.POST("{\"event\":\"heartbeat\",\"info\":{\"board\":\"esp32\",\"simulated\":true}}");
    JsonDocument reply;
    bool valid = status == 200 &&
        deserializeJson(reply, http.getStream()) == DeserializationError::Ok &&
        reply["mode"].is<const char *>() && reply["motion_enabled"].is<bool>();
    if (valid) {
        const char *mode = reply["mode"];
        bool wouldMove = reply["motion_enabled"].as<bool>() && strcmp(mode, "run") == 0;
        Serial.printf("mode=%s -> %s (simulation; motors OFF)\n",
                      mode, wouldMove ? "request movement" : "stop");
        lastReply = millis();
        commandFresh = true;
    } else {
        Serial.printf("Invalid reply / HTTP error (%d) -> STOP\n", status);
        commandFresh = false;
    }
    http.end();
}

// ESP32 DOIT DevKit V1 -> motor driver inputs.
// Share GND with the driver; power motors separately, not from ESP32 pins.
constexpr int LEFT_IN1 = 25;
constexpr int LEFT_IN2 = 26;
constexpr int RIGHT_IN3 = 27;
constexpr int RIGHT_IN4 = 14;
constexpr int LED_PIN = 2;

void setup() {
    const int motorPins[] = {LEFT_IN1, LEFT_IN2, RIGHT_IN3, RIGHT_IN4};
    for (int pin : motorPins) {
        digitalWrite(pin, LOW);
        pinMode(pin, OUTPUT);
    }
    pinMode(LED_PIN, OUTPUT);
    Serial.begin(115200);
    Serial.println("ESP32 ready. Motor inputs held LOW.");
    WiFi.mode(WIFI_AP_STA);
    WiFi.setAutoReconnect(true);
    config.begin("fido", false);
    String apPassword = config.getString("ap_password", "");
    WiFi.softAPConfig(IPAddress(192, 168, 4, 1), IPAddress(192, 168, 4, 1),
                      IPAddress(255, 255, 255, 0));
    WiFi.softAP("Fido-Setup", apPassword.isEmpty() ? nullptr : apPassword.c_str());
    setupServer.on("/", HTTP_GET, setupPage);
    setupServer.on("/", HTTP_POST, setupPage);
    setupServer.begin();
    serverUrl = config.getString("server", "http://10.56.237.125:5000");
    String ssid = config.getString("ssid", "");
    String password = config.getString("password", "");
    if (ssid.length() > 0) {
        WiFi.begin(ssid.c_str(), password.c_str());
    } else {
        Serial.println("Configure Wi-Fi through Fido-Setup.");
    }
    Serial.println("Server: " + serverUrl);
    Serial.println("Setup: connect to Fido-Setup, open http://192.168.4.1");
}

void loop() {
    setupServer.handleClient();
    if (restartPending) {
        if (millis() - savedAt >= 1000) ESP.restart();
        return;
    }
    digitalWrite(LED_PIN, WiFi.status() == WL_CONNECTED ? HIGH : LOW);
    if (WiFi.status() != WL_CONNECTED || millis() - lastReply >= 3000) {
        if (commandFresh) Serial.println("Connection stale -> STOP");
        commandFresh = false;
    }
    if (WiFi.status() == WL_CONNECTED && millis() - lastPoll >= 1000) {
        lastPoll = millis();
        pollBackend();
    }
    delay(10);
}
