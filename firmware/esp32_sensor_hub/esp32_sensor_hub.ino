/*
  AURORA Sensor Hub — ESP32 firmware

  Serves sensor readings as JSON on your local Wi-Fi:

      GET http://aurora-sensors.local/readings
      {"device":"aurora-sensors","uptime_s":123,
       "readings":{"temperature":23.4,"humidity":55.0,"pressure":1008.2,"light":312.5,
                   "motion":false,"eco2":640,"tvoc":85,"heart_rate":null,"spo2":null},
       "ages":{"eco2":1,"tvoc":1},
       "last_motion_s":42}

  null means "no reading": the sensor isn't connected, is still warming up,
  or (for heart rate and SpO2) no finger is on the sensor or the signal
  isn't stable yet. "ages" says how many seconds ago a value was measured
  (for values measured in the background); "last_motion_s" how long ago the
  PIR last saw motion (left out if it hasn't since start-up).

  Sensors (any can be left out; it is detected at start-up):
    - BME280  (I2C, address 0x76 or 0x77): temperature, humidity, pressure
    - BH1750  (I2C, address 0x23): ambient light in lux
    - SGP30   (I2C, address 0x58): VOCs (ppb) and estimated CO2 (ppm);
              null for the first 15 s while it warms up
    - HC-SR501 PIR motion sensor: digital output on GPIO 27
    - MAX30102 (I2C, address 0x57): heart rate and SpO2 (informational only)

  Wiring: see README.md in this folder.

  Libraries (Arduino IDE → Tools → Manage Libraries):
    - "Adafruit BME280 Library" (and its dependency "Adafruit Unified Sensor")
    - "BH1750" by Christopher Laws
    - "Adafruit SGP30 Sensor"
    - "SparkFun MAX3010x Pulse and Proximity Sensor Library"
  Board: "ESP32 Dev Module" (Espressif ESP32 board package).

  Wi-Fi name and password go in secrets.h (copy secrets.example.h).
  This firmware has not yet been tested on real hardware.
*/

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Wire.h>
#include <Adafruit_BME280.h>
#include <BH1750.h>
#include <Adafruit_SGP30.h>
#include <MAX30105.h>
#include <heartRate.h>
#include <spo2_algorithm.h>

#include "secrets.h"   // Defines WIFI_SSID and WIFI_PASSWORD.

const char* HOSTNAME = "aurora-sensors";   // -> http://aurora-sensors.local
const int PIR_PIN = 27;
const long FINGER_IR_THRESHOLD = 50000;     // Below this, no finger is on the MAX30102.
const unsigned long READING_MAX_AGE_MS = 15000;

const unsigned long SGP30_WARMUP_MS = 15000;    // eCO2/TVOC read 400/0 until then.

WebServer server(80);
Adafruit_BME280 bme;
BH1750 lightMeter;
Adafruit_SGP30 sgp;
MAX30105 pulseSensor;

bool hasBME = false;
bool hasLight = false;
bool hasSGP = false;
bool hasPulse = false;

// --- SGP30: measured once a second in the background ------------------------
unsigned long sgpStarted = 0;
unsigned long sgpTime = 0;      // when the last good measurement was taken
unsigned long sgpLastTry = 0;
uint16_t eco2Value = 0;
uint16_t tvocValue = 0;

// --- PIR: when motion was last seen ------------------------------------------
bool motionSeen = false;
unsigned long lastMotionMs = 0;

// --- Heart rate: average of the last few beats -----------------------------
const byte RATE_SIZE = 4;
byte rates[RATE_SIZE];
byte rateSpot = 0;
byte ratesStored = 0;
long lastBeat = 0;
float beatAverage = 0;
unsigned long beatAverageTime = 0;

// --- SpO2: Maxim's algorithm over 100 samples (~4 s at 25 samples/s) -------
const int SPO2_SAMPLES = 100;
uint32_t irBuffer[SPO2_SAMPLES];
uint32_t redBuffer[SPO2_SAMPLES];
int sampleCount = 0;
int32_t spo2Value = 0;
int8_t spo2Valid = 0;
unsigned long spo2Time = 0;
bool fingerPresent = false;

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
    Serial.println("Reachable at http://aurora-sensors.local/readings");
  }
}

void addNumber(String& json, const char* key, bool available, float value, int decimals) {
  json += "\"";
  json += key;
  json += "\":";
  if (available && !isnan(value)) {
    json += String(value, decimals);
  } else {
    json += "null";
  }
}

void handleReadings() {
  unsigned long now = millis();
  float temperature = NAN, humidity = NAN, pressure = NAN;
  if (hasBME) {
    temperature = bme.readTemperature();
    humidity = bme.readHumidity();
    pressure = bme.readPressure() / 100.0F;   // Pa -> hPa
  }

  float light = NAN;
  if (hasLight) {
    light = lightMeter.readLightLevel();   // negative on error
    if (light < 0) light = NAN;
  }
  bool airFresh = hasSGP && sgpTime > 0 && now - sgpStarted > SGP30_WARMUP_MS &&
                  now - sgpTime < READING_MAX_AGE_MS;

  bool heartFresh = hasPulse && fingerPresent && ratesStored == RATE_SIZE &&
                    now - beatAverageTime < READING_MAX_AGE_MS;
  bool spo2Fresh = hasPulse && fingerPresent && spo2Valid &&
                   now - spo2Time < READING_MAX_AGE_MS;

  String json = "{\"device\":\"";
  json += HOSTNAME;
  json += "\",\"uptime_s\":";
  json += String(now / 1000);
  json += ",\"readings\":{";
  addNumber(json, "temperature", hasBME, temperature, 1); json += ",";
  addNumber(json, "humidity", hasBME, humidity, 1);       json += ",";
  addNumber(json, "pressure", hasBME, pressure, 1);       json += ",";
  addNumber(json, "light", hasLight, light, 1);           json += ",";
  json += "\"motion\":";
  json += digitalRead(PIR_PIN) == HIGH ? "true" : "false";
  json += ",";
  addNumber(json, "eco2", airFresh, (float)eco2Value, 0);  json += ",";
  addNumber(json, "tvoc", airFresh, (float)tvocValue, 0);  json += ",";
  addNumber(json, "heart_rate", heartFresh, beatAverage, 0); json += ",";
  addNumber(json, "spo2", spo2Fresh, (float)spo2Value, 0);
  json += "}";
  if (airFresh) {
    unsigned long age = (now - sgpTime) / 1000;
    json += ",\"ages\":{\"eco2\":" + String(age) + ",\"tvoc\":" + String(age) + "}";
  }
  if (motionSeen) {
    json += ",\"last_motion_s\":" + String((now - lastMotionMs) / 1000);
  }
  json += "}";

  server.send(200, "application/json", json);
}

void readPulseSensor() {
  if (!hasPulse) return;

  pulseSensor.check();   // Reads new samples into the sensor library's buffer.
  while (pulseSensor.available()) {
    uint32_t red = pulseSensor.getFIFORed();
    uint32_t ir = pulseSensor.getFIFOIR();
    pulseSensor.nextSample();

    fingerPresent = ir > FINGER_IR_THRESHOLD;
    if (!fingerPresent) {
      sampleCount = 0;
      ratesStored = 0;
      continue;
    }

    // Heart rate from the time between beats.
    if (checkForBeat(ir)) {
      long delta = millis() - lastBeat;
      lastBeat = millis();
      float bpm = 60 / (delta / 1000.0);
      if (bpm > 30 && bpm < 220) {
        rates[rateSpot++] = (byte)bpm;
        rateSpot %= RATE_SIZE;
        if (ratesStored < RATE_SIZE) ratesStored++;
        int total = 0;
        for (byte i = 0; i < ratesStored; i++) total += rates[i];
        beatAverage = (float)total / ratesStored;
        beatAverageTime = millis();
      }
    }

    // SpO2 every 100 samples.
    irBuffer[sampleCount] = ir;
    redBuffer[sampleCount] = red;
    sampleCount++;
    if (sampleCount == SPO2_SAMPLES) {
      int32_t heartRateUnused;
      int8_t heartRateValidUnused;
      maxim_heart_rate_and_oxygen_saturation(irBuffer, SPO2_SAMPLES, redBuffer,
                                             &spo2Value, &spo2Valid,
                                             &heartRateUnused, &heartRateValidUnused);
      if (spo2Valid && (spo2Value < 70 || spo2Value > 100)) spo2Valid = 0;
      spo2Time = millis();
      sampleCount = 0;
    }
  }
}

void readAirSensor() {
  // The SGP30's baseline algorithm expects one measurement per second.
  if (!hasSGP || millis() - sgpLastTry < 1000) return;
  sgpLastTry = millis();
  if (hasBME) {
    // Humidity compensation: absolute humidity in mg/m^3 from temperature and relative humidity.
    float t = bme.readTemperature();
    float rh = bme.readHumidity();
    if (!isnan(t) && !isnan(rh)) {
      float absolute = 216.7f * ((rh / 100.0f) * 6.112f * exp((17.62f * t) / (243.12f + t)) / (273.15f + t));
      sgp.setHumidity((uint32_t)(absolute * 1000.0f));
    }
  }
  if (sgp.IAQmeasure()) {
    eco2Value = sgp.eCO2;
    tvocValue = sgp.TVOC;
    sgpTime = millis();
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIR_PIN, INPUT);
  Wire.begin();

  hasBME = bme.begin(0x76) || bme.begin(0x77);
  Serial.println(hasBME ? "BME280 found." : "BME280 not found (temperature/humidity/pressure will be null).");

  hasLight = lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);
  Serial.println(hasLight ? "BH1750 found." : "BH1750 not found (light will be null).");

  hasSGP = sgp.begin();
  sgpStarted = millis();
  Serial.println(hasSGP ? "SGP30 found (air readings after a 15 s warm-up)."
                        : "SGP30 not found (VOCs/eCO2 will be null).");

  hasPulse = pulseSensor.begin(Wire, I2C_SPEED_FAST);
  if (hasPulse) {
    // brightness, sample average, LED mode (2 = red + IR), sample rate, pulse width, ADC range
    pulseSensor.setup(60, 4, 2, 100, 411, 4096);
    Serial.println("MAX30102 found.");
  } else {
    Serial.println("MAX30102 not found (heart rate/SpO2 will be null).");
  }

  connectWiFi();
  server.on("/readings", handleReadings);
  server.onNotFound([]() { server.send(404, "text/plain", "Try /readings"); });
  server.begin();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }
  server.handleClient();
  readPulseSensor();
  readAirSensor();
  if (digitalRead(PIR_PIN) == HIGH) {
    motionSeen = true;
    lastMotionMs = millis();
  }
}
