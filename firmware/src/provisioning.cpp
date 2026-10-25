#include "provisioning.h"
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include "settings.h"

namespace provisioning {
namespace {
Preferences prefs;
WebServer server(80);
String wifiSsid, wifiPassword, url, speaker, deviceToken;
bool reboot = false;
uint32_t savedAt = 0;

String escaped(String text) {
  text.replace("&", "&amp;");
  text.replace("\"", "&quot;");
  text.replace("<", "&lt;");
  text.replace(">", "&gt;");
  return text;
}

void page() {
  if (server.client().localIP() != WiFi.softAPIP()) {
    server.send(403, "text/plain", "Connect to Fido-Setup.");
    return;
  }
  if (server.method() == HTTP_POST) {
    String address = server.arg("server");
    address.trim();
    while (address.endsWith("/")) address.remove(address.length() - 1);
    // Local IPv4 and optional port only; no credentials, paths or arbitrary schemes.
    String host = address;
    if (host.startsWith("http://")) host.remove(0, 7);
    int colon = host.indexOf(':');
    String portText = colon < 0 ? "5000" : host.substring(colon + 1);
    bool digits = !portText.isEmpty();
    for (size_t i = 0; i < portText.length(); ++i)
      if (!isDigit(portText[i])) digits = false;
    long port = portText.toInt();
    IPAddress ip;
    String name = server.arg("ssid"), pass = server.arg("password");
    String apPass = server.arg("ap_password"), bt = server.arg("speaker");
    if (!ip.fromString(colon < 0 ? host : host.substring(0, colon)) ||
        ip == IPAddress(0, 0, 0, 0) || ip == IPAddress(255, 255, 255, 255) ||
        !digits || port < 1 || port > 65535 ||
        name.length() > 32 ||
        (!name.isEmpty() && (pass.length() < 8 || pass.length() > 63)) ||
        (wifiSsid.isEmpty() && name.isEmpty()) ||
        (!apPass.isEmpty() && (apPass.length() < 8 || apPass.length() > 63)) ||
        (!prefs.isKey("ap_password") && apPass.isEmpty()) ||
        bt.length() > 64 || server.arg("token").length() > 256) {
      server.send(400, "text/plain", "Invalid settings. Use laptop IPv4:port. First setup needs Wi-Fi "
                  "and a setup password (8-63 characters). Go back to retry.");
      return;
    }
    String normalized = "http://" + ip.toString() + ":" + String(port);
    bool ok = prefs.putString("server", normalized) > 0;
    if (!name.isEmpty()) {
      ok = (prefs.putString("ssid", name) > 0) && ok;
      ok = (prefs.putString("password", pass) > 0) && ok;
    }
    if (!apPass.isEmpty()) ok = (prefs.putString("ap_password", apPass) > 0) && ok;
    if (!bt.isEmpty()) ok = (prefs.putString("speaker", bt) > 0) && ok;
    if (!server.arg("token").isEmpty())
      ok = (prefs.putString("token", server.arg("token")) > 0) && ok;
    if (!ok) { server.send(500, "text/plain", "Save failed. Retry."); return; }
    // Post/Redirect/Get avoids repeating the previous save on refresh.
    server.sendHeader("Location", "/?saved=1");
    server.send(303, "text/plain", "Saved; restarting shortly.");
    reboot = true;
    savedAt = millis();
    return;
  }
  String html = F("<!doctype html><html lang=en><meta charset=utf-8>"
      "<meta name=viewport content='width=device-width,initial-scale=1'><title>Fido setup</title>"
      "<style>body{font:16px system-ui;max-width:28rem;margin:2rem auto;padding:1rem}"
      "label{display:block;margin-top:1rem}input,button{font:inherit;box-sizing:border-box;width:100%;padding:.7rem}"
      "button{margin-top:1rem}</style><h1>Fido setup</h1>");
  if (server.hasArg("saved")) html += "<p>Settings saved. Reconnect after restart.</p>";
  html += "<form method=post action=/><label for=server>Server IPv4:port</label>"
          "<input id=server name=server required value=\"" + escaped(url) + "\">"
          "<label for=speaker>Bluetooth speaker name</label><input id=speaker name=speaker value=\"" +
          escaped(speaker) + "\"><details><summary>Wi-Fi / setup password / token</summary>"
          "<p>Leave blank to keep saved values.</p>"
          "<label for=ssid>Wi-Fi name</label><input id=ssid name=ssid maxlength=32>"
          "<label for=password>Wi-Fi password</label><input id=password name=password type=password minlength=8 maxlength=63>"
          "<label for=ap_password>Fido-Setup password</label>"
          "<input id=ap_password name=ap_password type=password minlength=8 maxlength=63>"
          "<label for=token>Backend device token</label><input id=token name=token type=password>"
          "</details><button>Save and restart</button></form>"
          "<p>Motors stop when saving. First setup network is open; configure somewhere trusted.</p></html>";
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "text/html", html);
}

String stored(const char* key, const char* fallback = "") {
  return prefs.isKey(key) ? prefs.getString(key) : String(fallback);
}
}
void begin() {
  prefs.begin("fido", false); // Same namespace/keys as the original setup sketch.
  wifiSsid = stored("ssid");
  wifiPassword = stored("password");
  url = stored("server", "http://10.56.237.26:5000");
  speaker = stored("speaker", SPEAKER_NAME);
  deviceToken = stored("token", DEVICE_TOKEN);
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAPConfig(IPAddress(192,168,4,1), IPAddress(192,168,4,1), IPAddress(255,255,255,0));
  String apPass = stored("ap_password");
  WiFi.softAP("Fido-Setup", apPass.isEmpty() ? nullptr : apPass.c_str());
  server.on("/", HTTP_GET, page);
  server.on("/", HTTP_POST, page);
  server.onNotFound([] { server.send(404, "text/plain", "Open http://192.168.4.1/"); });
  server.begin();
  Serial.println("[setup] Fido-Setup: http://192.168.4.1");
}
void poll() {
  server.handleClient();
  if (reboot && millis() - savedAt >= 2000) ESP.restart();
}
bool restarting() { return reboot; }
const String& ssid() { return wifiSsid; }
const String& password() { return wifiPassword; }
const String& backendUrl() { return url; }
const String& speakerName() { return speaker; }
const String& token() { return deviceToken; }
}
