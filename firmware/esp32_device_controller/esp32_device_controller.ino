/*
  AURORA Device Controller — ESP32 firmware

  Lets AURORA switch a lamp (via a relay module) and a fan (on/off relay
  plus a PWM speed signal) on your local Wi-Fi.

      GET  /devices/<id>                -> {"id":"fan","on":true,"level":2}
      POST /devices/<id>  {"command":"on"|"off"|"toggle"|"set","value":2}

  Every request must carry the header  X-Aurora-Token: <DEVICE_TOKEN>
  (from secrets.h), otherwise it is refused with 401. This stops other
  devices on your Wi-Fi from switching anything.

  Safety:
    - Everything starts OFF after power-up or reset.
    - Only the devices in DEVICES[] exist; unknown ids get 404.
    - Values outside a device's range are refused with 400, never clamped.
    - Mains wiring is dangerous. Use a relay module rated for the load,
      or better, a low-voltage lamp/fan, and let a qualified person do any
      mains work. See README.md.

  Board: "ESP32 Dev Module". No extra libraries needed.
  This firmware has not yet been tested on real hardware.
*/

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>

#include "secrets.h"   // WIFI_SSID, WIFI_PASSWORD, DEVICE_TOKEN

const char* HOSTNAME = "aurora-devices";   // -> http://aurora-devices.local

struct DeviceConfig {
  const char* id;
  int relayPin;      // HIGH = on (set RELAY_ACTIVE_LOW for low-trigger relay boards)
  int pwmPin;        // -1 = no speed/brightness control
  int minLevel;
  int maxLevel;
  bool on;
  int level;
};

// Must match DEVICES in AURORA's settings (same ids, kinds and ranges).
DeviceConfig DEVICES[] = {
  {"desk_light", 26, -1, 1, 100, false, 100},
  {"fan",        25, 33, 1, 3,   false, 3},
};
const int DEVICE_COUNT = sizeof(DEVICES) / sizeof(DEVICES[0]);
const bool RELAY_ACTIVE_LOW = true;   // Most cheap relay modules switch on LOW.

WebServer server(80);

void writeOutputs(DeviceConfig& d) {
  bool level = d.on ? !RELAY_ACTIVE_LOW : RELAY_ACTIVE_LOW;
  digitalWrite(d.relayPin, level ? HIGH : LOW);
  if (d.pwmPin >= 0) {
    int duty = d.on ? map(d.level, d.minLevel, d.maxLevel, 85, 255) : 0;
    analogWrite(d.pwmPin, duty);
  }
}

DeviceConfig* findDevice(const String& id) {
  for (int i = 0; i < DEVICE_COUNT; i++) {
    if (id == DEVICES[i].id) return &DEVICES[i];
  }
  return nullptr;
}

String stateJson(const DeviceConfig& d) {
  String json = "{\"id\":\"";
  json += d.id;
  json += "\",\"on\":";
  json += d.on ? "true" : "false";
  json += ",\"level\":";
  json += (d.pwmPin >= 0 || d.maxLevel > 1) ? String(d.level) : "null";
  json += "}";
  return json;
}

bool authorised() {
  if (server.header("X-Aurora-Token") == DEVICE_TOKEN && strlen(DEVICE_TOKEN) >= 16) {
    return true;
  }
  server.send(401, "application/json", "{\"error\":\"missing or wrong token\"}");
  return false;
}

// Very small JSON field readers: the body is tiny and fixed in shape.
String jsonString(const String& body, const char* key) {
  String pattern = String("\"") + key + "\"";
  int k = body.indexOf(pattern);
  if (k < 0) return "";
  int start = body.indexOf('"', body.indexOf(':', k) + 1);
  int end = body.indexOf('"', start + 1);
  if (start < 0 || end < 0) return "";
  return body.substring(start + 1, end);
}

bool jsonInt(const String& body, const char* key, int& out) {
  String pattern = String("\"") + key + "\"";
  int k = body.indexOf(pattern);
  if (k < 0) return false;
  int colon = body.indexOf(':', k);
  if (colon < 0) return false;
  String rest = body.substring(colon + 1);
  rest.trim();
  if (rest.length() == 0 || !(isDigit(rest[0]) || rest[0] == '-')) return false;
  out = rest.toInt();
  return true;
}

void handleDevice() {
  if (!authorised()) return;

  String uri = server.uri();                       // /devices/<id>
  DeviceConfig* d = findDevice(uri.substring(strlen("/devices/")));
  if (d == nullptr) {
    server.send(404, "application/json", "{\"error\":\"unknown device\"}");
    return;
  }

  if (server.method() == HTTP_POST) {
    String body = server.arg("plain");
    String command = jsonString(body, "command");
    if (command == "on") {
      d->on = true;
    } else if (command == "off") {
      d->on = false;
    } else if (command == "toggle") {
      d->on = !d->on;
    } else if (command == "set") {
      int value;
      if (!jsonInt(body, "value", value) || value < d->minLevel || value > d->maxLevel) {
        server.send(400, "application/json", "{\"error\":\"value out of range\"}");
        return;
      }
      d->level = value;
      d->on = true;
    } else {
      server.send(400, "application/json", "{\"error\":\"unknown command\"}");
      return;
    }
    writeOutputs(*d);
    Serial.printf("%s -> %s\n", d->id, command.c_str());
  }

  server.send(200, "application/json", stateJson(*d));
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.print("\nConnected. IP address: ");
  Serial.println(WiFi.localIP());
  if (MDNS.begin(HOSTNAME)) {
    MDNS.addService("http", "tcp", 80);
  }
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < DEVICE_COUNT; i++) {
    pinMode(DEVICES[i].relayPin, OUTPUT);
    if (DEVICES[i].pwmPin >= 0) pinMode(DEVICES[i].pwmPin, OUTPUT);
    DEVICES[i].on = false;          // Always start off.
    writeOutputs(DEVICES[i]);
  }

  if (strlen(DEVICE_TOKEN) < 16) {
    Serial.println("DEVICE_TOKEN in secrets.h must be at least 16 characters. Refusing all requests.");
  }

  connectWiFi();
  const char* headers[] = {"X-Aurora-Token"};
  server.collectHeaders(headers, 1);
  server.onNotFound([]() {
    if (server.uri().startsWith("/devices/")) {
      handleDevice();
    } else {
      server.send(404, "text/plain", "Not found");
    }
  });
  server.begin();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }
  server.handleClient();
}
