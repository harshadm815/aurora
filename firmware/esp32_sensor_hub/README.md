# AURORA Sensor Hub (ESP32)

An ESP32 that measures your room (and optionally your pulse) and serves
the readings to AURORA over your home Wi-Fi.

> **Status:** written for AURORA 0.14 (light and air sensors added in
> 0.31) but **not yet tested on real hardware**. Check the Serial Monitor
> output on first use.

## Parts

| Part | Measures | Required? |
|---|---|---|
| ESP32 dev board (e.g. ESP32-DevKitC) | – | Yes |
| BME280 breakout (3.3 V) | Temperature, humidity, air pressure | Optional |
| BH1750 breakout | Ambient light (lux) | Optional |
| SGP30 breakout | VOCs (ppb) and *estimated* CO₂ (ppm) | Optional |
| HC-SR501 PIR sensor | Motion | Optional |
| MAX30102 breakout | Heart rate, blood oxygen (SpO2) | Optional |

Any sensor can be left out; the firmware reports `null` for it.

## Wiring

All I2C sensors share the same two wires (addresses: BME280 0x76/0x77,
BH1750 0x23, MAX30102 0x57, SGP30 0x58, so they don't clash).

| Sensor pin | ESP32 pin |
|---|---|
| BME280 VIN | 3V3 |
| BME280 GND | GND |
| BME280 SDA | GPIO 21 |
| BME280 SCL | GPIO 22 |
| MAX30102 VIN | 3V3 |
| MAX30102 GND | GND |
| MAX30102 SDA | GPIO 21 |
| MAX30102 SCL | GPIO 22 |
| BH1750 VCC / GND / SDA / SCL | 3V3 / GND / GPIO 21 / GPIO 22 |
| SGP30 VIN / GND / SDA / SCL | 3V3 / GND / GPIO 21 / GPIO 22 |
| PIR VCC | 5V (VIN) |
| PIR GND | GND |
| PIR OUT | GPIO 27 |

The HC-SR501 needs 5 V power but its output is 3.3 V, which is safe for
the ESP32. Give it about a minute after power-up to settle.

## Flashing

1. Install the Arduino IDE and the **esp32** board package by Espressif.
2. Install the libraries **Adafruit BME280 Library** (with **Adafruit
   Unified Sensor**), **BH1750** (Christopher Laws), **Adafruit SGP30
   Sensor** and **SparkFun MAX3010x Pulse and Proximity Sensor Library**.
3. Copy `secrets.example.h` to `secrets.h` and enter your Wi-Fi name and
   password. `secrets.h` is ignored by git.
4. Open `esp32_sensor_hub.ino`, choose **ESP32 Dev Module** and the right
   COM port, and upload.
5. Open the Serial Monitor at 115200 baud. You should see which sensors
   were found and the board's IP address.
6. In a browser on the same Wi-Fi, open
   `http://aurora-sensors.local/readings` (or `http://<IP address>/readings`).

## Connecting AURORA

In `aurora/config/local_settings.py`:

```python
SENSOR_SOURCES = [
    {"name": "Room ESP32", "type": "http",
     "url": "http://aurora-sensors.local/readings", "location": "room"},
]
```

If `.local` names don't resolve on your PC, use the IP address shown in
the Serial Monitor (and reserve it in your router so it doesn't change).

Then ask AURORA: "what's the temperature in my room?", "is there any
motion in my room?", "check my sensors", "what's my heart rate?".

## Health readings

The MAX30102 gives **informational** readings only, not medical
measurements. Place a fingertip gently on the sensor and keep still for
about 10 seconds; until the signal is stable the firmware reports `null`.
AURORA stores health readings separately from room readings, deletes them
after 7 days, and they can be switched off by removing the `"Health"`
grant in `PERMISSION_GRANTS`.

## Air and light readings

- The **SGP30** needs about 15 seconds after power-up before its values
  mean anything (the firmware reports `null` until then), and about
  12 hours of running to settle its baseline. Its CO₂ is **estimated from
  VOCs**, not measured; AURORA labels it "CO₂ (estimated)". For a real
  CO₂ measurement use an NDIR or photoacoustic sensor (SCD40, MH-Z19) on
  any device that sends `"co2"` in the same JSON.
- The firmware tells AURORA how old each air value is (`"ages"`); AURORA
  refuses values older than 5 minutes rather than show them as current.
- Fine dust (`"pm2_5"`, `"pm10"`) is understood by AURORA too, e.g. from a
  PMS5003 on another device; this firmware doesn't read one.
- Any other simple sensor can be sent under its own name once it is
  declared in `SENSOR_EXTRA_QUANTITIES` (see `aurora/sensors/reading.py`).

To accept only some readings from a device, list them:

```python
SENSOR_SOURCES = [
    {"name": "Room ESP32", "type": "http", "url": "http://aurora-sensors.local/readings",
     "location": "room", "quantities": ["temperature", "humidity", "light"]},
]
```
