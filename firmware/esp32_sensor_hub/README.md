# AURORA Sensor Hub (ESP32)

An ESP32 that measures your room (and optionally your pulse) and serves
the readings to AURORA over your home Wi-Fi.

> **Status:** written for AURORA 0.14 but **not yet tested on real
> hardware**. Check the Serial Monitor output on first use.

## Parts

| Part | Measures | Required? |
|---|---|---|
| ESP32 dev board (e.g. ESP32-DevKitC) | – | Yes |
| BME280 breakout (3.3 V) | Temperature, humidity, air pressure | Optional |
| HC-SR501 PIR sensor | Motion | Optional |
| MAX30102 breakout | Heart rate, blood oxygen (SpO2) | Optional |

Any sensor can be left out; the firmware reports `null` for it.

## Wiring

Both I2C sensors share the same two wires.

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
| PIR VCC | 5V (VIN) |
| PIR GND | GND |
| PIR OUT | GPIO 27 |

The HC-SR501 needs 5 V power but its output is 3.3 V, which is safe for
the ESP32. Give it about a minute after power-up to settle.

## Flashing

1. Install the Arduino IDE and the **esp32** board package by Espressif.
2. Install the libraries **Adafruit BME280 Library** (with **Adafruit
   Unified Sensor**) and **SparkFun MAX3010x Pulse and Proximity Sensor
   Library**.
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
