# AURORA

A modular personal AI assistant that runs a local language model through
[Ollama](https://ollama.com).

Current stage: working prototype of all twelve roadmap milestones.
AURORA chats with a local model, remembers facts about you, routes
requests to verified tools (calculator, weather, Wikipedia, arXiv, your
documents), plans multi-step requests, listens and speaks offline, sees
through the webcam, reads room and health sensors (simulated until the
ESP32 is built), switches devices only after you confirm, sets reminders,
and runs a team of research agents. Everything runs on a 4 GB laptop.

AURORA's own language model is being built from scratch in `aurora_core/`
(NumPy only). So far: the tensor maths, the AURORA-T0 Transformer, the
tokenizers, the training pipeline and text generation. A first model,
trained on *The Adventures of Sherlock Holmes*, writes text from its own
weights (see [its model card](Docs/AURORA_T0_MODEL_CARD.md)). It isn't connected to the assistant; see
[Docs/AURORA_CORE.md](Docs/AURORA_CORE.md).

See [Docs/ARCHITECTURE.md](Docs/ARCHITECTURE.md) for how it works and
its "Known gaps" table for what's not done yet.

## Setup (Windows)

1. Install Ollama and pull the model:

   ```
   ollama pull tinyllama
   ```

2. Create the environment and install dependencies:

   ```
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r Requirements.txt
   ```

3. Optional:
   - Put .txt, .md or .pdf files in `Documents/` for AURORA to list,
     search and read. It can't read files anywhere else.
   - To use Wikipedia, create `aurora/config/local_settings.py` with
     `RESEARCH_CONTACT = "you@example.com"` (an email address or web page).
     That file isn't tracked by git and overrides `settings.py`.
     Wikimedia requires this, and it is sent with each lookup.

4. Optional, for voice (`listen`, `voice on`, `speak on`):
   download `vosk-model-small-en-us-0.15.zip` from
   https://alphacephei.com/vosk/models and unzip it into `Models/`, so
   that `Models/vosk-model-small-en-us-0.15/am` exists.

5. Optional, for vision (`what do you see?`, `read this`, `watch`):
   download `object_detection_nanodet_2022nov.onnx` from OpenCV's
   opencv_zoo repository (models/object_detection_nanodet) into `Models/`.
   OCR models come with the `rapidocr-onnxruntime` package.

6. Optional, sensors: AURORA starts with a **simulator** (answers are
   labelled as simulated). To use real sensors, build the ESP32 in
   `firmware/esp32_sensor_hub/` (wiring and flashing in its README) and
   set `SENSOR_SOURCES` in `aurora/config/local_settings.py`.

7. Optional, devices: AURORA starts with a simulated desk light and fan.
   For real switching, build `firmware/esp32_device_controller/` (read
   its safety notes first) and set `DEVICES`, `DEVICE_CONTROLLER_URL` and
   `DEVICE_CONTROLLER_TOKEN` in `aurora/config/local_settings.py`.

## Run

Desktop interface (AURORA Command Centre):

```
.venv\Scripts\python.exe command_centre.py
```

Windows executable (after building it, see [Docs/COMMAND_CENTRE.md](Docs/COMMAND_CENTRE.md)):

```
packaging\build_windows.py   →   dist\AURORA Command Centre\AURORA Command Centre.exe
```

Terminal interface:

```
.venv\Scripts\python.exe main.py
```

Commands: `status` shows system status, `route <message>` shows how a message would be routed, `plan <message>` shows how it would be split into steps, `clear` resets the conversation, `exit` shuts down.
Logs are written to `Logs/aurora.log` (metadata only; never your messages).

## Test

```
.venv\Scripts\python.exe -m pytest
```

## Documentation

- [Command Centre](Docs/COMMAND_CENTRE.md)
- [aurora_core: AURORA's own neural network](Docs/AURORA_CORE.md)
- [Architecture](Docs/ARCHITECTURE.md)
- [Memory design](Docs/MEMORY_DESIGN.md)
- [Changelog](Docs/CHANGELOG.md)
