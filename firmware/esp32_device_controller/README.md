# AURORA Device Controller (ESP32)

Lets AURORA switch a lamp and a fan over your home Wi-Fi.

> **Status:** written for AURORA 0.15 but **not yet tested on real
> hardware**.

> ⚠️ **Mains electricity can kill.** Start with low-voltage loads (a USB
> lamp, a 12 V fan). If you switch mains devices, use a properly rated,
> enclosed relay module or a certified smart plug, and have a qualified
> person do the mains wiring.

## Parts

| Part | Purpose |
|---|---|
| ESP32 dev board | Controller |
| 2-channel relay module (3.3 V logic compatible) | Switches the lamp and the fan |
| Optional: MOSFET module or PWM fan driver | Fan speed (1–3) |

## Wiring

| From | To |
|---|---|
| Relay IN1 (desk light) | GPIO 26 |
| Relay IN2 (fan) | GPIO 25 |
| Fan speed signal (PWM) | GPIO 33 |
| Relay VCC / GND | 5V (VIN) / GND |

Most cheap relay modules switch **on when the input is LOW**; this is set
by `RELAY_ACTIVE_LOW` in the sketch. Everything starts **off** after
power-up.

## Setup

1. Copy `secrets.example.h` to `secrets.h` and fill in your Wi-Fi details
   and a long random `DEVICE_TOKEN` (16+ characters). `secrets.h` is
   ignored by git.
2. Upload `esp32_device_controller.ino` (board: **ESP32 Dev Module**). No
   extra libraries are needed.
3. In `aurora/config/local_settings.py`:

   ```python
   DEVICE_CONTROLLER_URL = "http://aurora-devices.local"   # or http://<IP>
   DEVICE_CONTROLLER_TOKEN = "the same secret as DEVICE_TOKEN"
   DEVICES = [
       {"id": "desk_light", "name": "desk light", "kind": "light",
        "aliases": ["light", "lamp"], "driver": "esp32"},
       {"id": "fan", "name": "fan", "kind": "fan", "driver": "esp32"},
   ]
   ```

   The `id`s must match `DEVICES[]` in the sketch.

## Safety design

- Requests without the right token are refused (401).
- Unknown devices are refused (404); out-of-range values are refused
  (400), never silently clamped.
- AURORA asks you to confirm every change, rate-limits each device
  (6 changes a minute) and never takes device commands from web pages,
  documents, OCR text or the language model's own replies.
