# AURORA Architecture

Version: 0.20.0
Status: Describes what exists in the code today. Planned components are listed separately at the end.

---

## Overview

AURORA is a modular personal AI assistant. A small application-level
kernel (not an operating-system kernel) starts, coordinates and stops
independent components. Each component has one responsibility and can be
tested on its own.

```
command_centre.py  (desktop UI, PySide6) ── aurora/interface ── Backend (one engine, worker thread)
main.py  (terminal UI: all printing and input; voice commands)
   │  └── VoiceSession ── Listener (Vosk + VAD, offline) + SapiSpeaker (Windows voices)
   │
   ▼
AuroraEngine  (aurora/kernel/engine.py)
   ├── EventBus ─────────────┐
   ├── PermissionManager ────┤── KernelContext (what plugins may use)
   ├── Conversation ── Personality (system prompt)
   ├── IntentRouter ── RouteDecision (published as routing.decided)
   ├── Planner ── Plan of Steps (each step routed by the IntentRouter)
   ├── ToolExecutor ── ToolManager (permission-checked)
   │      ├── Calculator
   │      ├── Remember / Recall / Forget ── MemoryManager ── SQLite
   │      ├── Weather ── Open-Meteo (network)
   │      ├── Wikipedia ── en.wikipedia.org (network, needs RESEARCH_CONTACT)
   │      ├── arXiv ── export.arxiv.org (network)
   │      ├── Documents ── Documents/ folder only (filesystem.read); images analysed too
   │      ├── Camera ── webcam, one picture per request (camera)
   │      ├── Sensors ─┐
   │      ├── Health ──┴── SensorHub ── ESP32 over Wi-Fi / Simulator; sensor_data.db
   │      ├── Devices ── DeviceController ── simulated / ESP32 relay (token)
   │      ├── Reminders ── ReminderScheduler (this session)
   │      └── Research ── research team (agents) ── uses the tools above
   ├── ServiceManager ── publishes service.* events
   │      └── BrainService ── Brain ── OllamaProvider ── Ollama (tinyllama)
   │                                    (timeout + reply length cap)
   ├── AutoMemory ── FactExtractor → MemoryEvaluator → MemoryManager
   └── PluginManager ── receives KernelContext, publishes plugin.* events
          (no plugins registered by default)
```

## Kernel lifecycle

```
start():     services start → plugins initialize → kernel.started
shutdown():  kernel.stopping → plugins shut down → services stop → kernel.stopped
```

Both are idempotent. `BrainService.start()` runs a provider health check,
so if Ollama is down or the model is missing the Brain is marked `Error`
and `status` reports it instead of claiming all systems are operational.

## Events

`EventBus` (`aurora/events/bus.py`) is synchronous publish/subscribe.

- Event names are constants in `aurora/events/topics.py`
  (`kernel.started`, `service.failed`, `plugin.blocked`,
  `conversation.message_received`, …).
- Each `Event` has `name`, `data`, `source` and `timestamp`; empty names
  are rejected.
- Subscribing to `"*"` receives every event.
- A subscriber that raises is logged and skipped. Other subscribers
  still run and the publisher is never interrupted.

## Permissions

`PermissionManager` (`aurora/kernel/permissions.py`) is **deny by default**.

- Permissions: `memory.read`, `memory.write`, `filesystem.read`,
  `filesystem.write`, `network`, `camera`, `microphone`, `sensors`,
  `health_data`, `hardware_control`.
- Grants are per component name and come only from
  `settings.PERMISSION_GRANTS`. An unknown permission name there is an
  error at startup. By default only the built-in tools are granted, each
  with just what it needs (see the Tools table).
- `status` lists every tool as `Ready` or `Blocked (needs ...)`.
- Plugins and tools declare `required_permissions`. A plugin missing any
  of them is **blocked** (never initialized, `plugin.blocked` event,
  warning in the terminal). A tool missing any of them raises
  `PermissionDeniedError` when called.
- At runtime a plugin can re-check with `self.require(Permission.X)`.

## Writing a plugin

```python
from aurora.kernel.permissions import Permission
from aurora.plugins.plugin import BasePlugin


class RoomSensorPlugin(BasePlugin):
    required_permissions = (Permission.SENSORS,)

    def __init__(self):
        super().__init__("RoomSensor")

    def initialize(self):
        self.publish("sensor.ready")      # self.context.events is available

    def shutdown(self):
        pass
```

Grant it in `settings.py`:

```python
PERMISSION_GRANTS = {"RoomSensor": ["sensors"]}
```

and register it before `engine.start()`:

```python
engine.register_plugin(RoomSensorPlugin())
```

## Request flow (today)

1. `main.py` reads a line from the user.
2. `AuroraEngine.process_message()` appends it to the `Conversation`.
3. If an action is waiting for confirmation, "yes" runs it, "no" cancels
   it, and anything else cancels it and is handled as a new message.
4. Otherwise the `IntentRouter` produces a `RouteDecision`
   (published as `routing.decided`) and the `Planner` checks whether
   the message holds several requests. A **multi-step plan** runs its
   tool steps in order, then the Brain answers any conversational parts
   with the tool results as context (see Planning below). A
   single-step message is handled as follows:
   - `clarify`: AURORA asks the user to rephrase.
   - A tool intent with a registered tool: the `ToolExecutor` runs it
     and the tool's own message is the reply. **The language model is not
     involved**, so computed or retrieved facts reach the user unchanged.
   - Anything else is conversation. `AutoMemory` learns facts from the
     message (announced in the reply), picks the memories relevant to it,
     and the Brain receives: personality prompt, memory context, history.
     See [MEMORY_DESIGN.md](MEMORY_DESIGN.md).
5. The reply (from a tool or the Brain) is appended to the conversation,
   so the Brain sees tool answers as context in later turns.

## Tools

| Tool | Intent | Needs | What it does |
|---|---|---|---|
| Calculator | `calculator` | none | Safe arithmetic, e.g. `12 * (7 + 3) = 120` |
| Numbers | `numbers` | none | Statistics and percentages with explanations, e.g. "average of 4, 8 and 15", "15% of 80" |
| MemoryControl | `memory_control` | memory.read, memory.write | "don't remember that", "stop learning", activity history on/off/clear |
| Episodes | `episodes` | memory.read | "what did we do today?", "when did I last check the weather?" |
| Routines | `routines` | memory.read, memory.write | Teach, list and delete routines ("when I say morning briefing, …") |
| Remember | `remember` | `memory.write` | Stores an explicit note ("remember that ...") as a `Note` fact |
| Recall | `recall` | `memory.read` | Keyword lookup. Only memories containing **every** keyword count as an answer; partial matches are listed separately as "related" |
| Forget | `forget` | `memory.read`, `memory.write` | Lists matching memories and **asks for confirmation** before deleting (including exact duplicates) |
| Weather | `weather` | `network` | Open-Meteo: current conditions and today, tomorrow, this week, this weekend |
| Wikipedia | `knowledge` | `network` | Encyclopedia summary ("tell me about black holes", "who is …", "what is a neutron star") with a link to the article |
| arXiv | `papers` | `network` | Up to 5 papers with authors, dates and links; newest first for "recent / latest", otherwise most relevant |
| Documents | `documents` | `filesystem.read` | List, search (with file and PDF page citations) and read .txt, .md and .pdf files in `Documents/` only |
| Knowledge | `knowledge_base` | `filesystem.read` | Answers questions from your documents by quoting them with citations; summaries; comparisons (with Wikipedia through its own tool and permission) |

A tool returns a `ToolResult` (`aurora/tools/result.py`): `success`,
`message` (the reply), `data`, and optionally `confirm`, a function the
kernel only calls after the user says yes. This is the pattern for every
future consequential action (deleting, controlling hardware).

Failures are always reported, never papered over by the language model:

- Missing permission: explains which permission and where to grant it.
- Tool crash: "The X tool failed unexpectedly (...)" and a `tool.failed` event.
- Weather service down, slow, or place not found: says so, and
  "I won't guess the weather."

`WeatherPlugin` (a placeholder) was removed; weather is now a tool.

### Research and documents

- **Untrusted content.** Text from Wikipedia, arXiv and your files is
  cleaned (control and text-direction characters removed), shortened,
  shown with its source, and never obeyed. When it is passed to the Brain
  inside a plan, the context says to treat it as data, not instructions.
- **No summaries by the model.** AURORA shows Wikipedia's own summary,
  paper listings and the exact passages from your documents. TinyLlama
  is not asked to summarise them, because it distorts facts.
- **Documents folder only.** The Documents tool opens only .txt, .md and
  .pdf files that it found by listing `settings.DOCUMENTS_DIR`
  (`I:\Aurora\Documents`). It never opens a path you type, ignores links
  pointing outside the folder and skips files over 20 MB. The folder is
  in `.gitignore`.
- **Wikipedia needs contact details.** Wikimedia refuses requests that
  don't include an email address or web page. Set `RESEARCH_CONTACT` in
  `aurora/config/local_settings.py` (personal overrides, not tracked by
  git); until then the Wikipedia tool explains this instead of
  failing.
- **No general web search yet.** Every reliable provider needs an
  account and API key. A web search tool can be added the same way as
  the others when one is chosen.

### Privacy note

The Weather tool sends the place name (then its coordinates) to
open-meteo.com. The Wikipedia and arXiv tools send only the topic, plus
`RESEARCH_CONTACT` if you set it. Nothing else leaves the machine.
Remove a tool's line from `PERMISSION_GRANTS` to switch it off.

## Voice

`aurora/voice/` adds speaking and listening. Everything runs offline:
Vosk (`Models/vosk-model-small-en-us-0.15`, about 70 MB on disk) for
speech recognition, and the voices built into Windows (SAPI5, called
directly through `comtypes`) for speech. The language model never sees
audio: voice only turns speech into the same text a typed message would
be, and a reply back into speech.

| Command | What happens |
|---|---|
| `listen` | The microphone opens for one request (up to 10 s, ends when you stop speaking, gives up after 5 s of nobody speaking), AURORA answers on screen and aloud, the microphone closes |
| `voice on` | Hands-free: AURORA waits for "Aurora", answers, and waits again until you say "stop listening" (or "stop", "goodbye") or press Ctrl+C |
| `speak on` / `speak off` | Also read typed replies aloud |

How it works:

- **Wake word.** A second Vosk recogniser restricted to a tiny grammar
  ("aurora", "hey aurora", "okay aurora") listens continuously. It is
  cheap and doesn't fire on ordinary speech. When it fires, the same
  audio is transcribed in full and AURORA keeps listening until no new
  words arrive for `VOICE_END_SILENCE` (1.2 s), so "Aurora, what's twelve
  times seven?" in one breath works, and so does "Aurora" … "Yes?" … request.
- **Transcript clean-up.** The wake word (and how the model mishears it,
  e.g. "or aura") is removed; spoken numbers become digits when the
  sentence contains an arithmetic word ("twelve times seven" → `12 times 7`;
  "which one is it" is left alone); `VOICE_CORRECTIONS` fixes words the
  small model mishears, e.g. `{"pony": "Pune"}`.
  Spoken lists of numbers work too ("average of four five six" →
  `average of 4 5 6`; "twenty one" stays `21`).
- **Voice activity detection** (`vad.py`): each 30 ms frame's loudness is
  compared with the room's background level. The first 0.2 s only learn
  the room (so a noisy room isn't mistaken for speech), speech starts
  after ~90 ms of loud frames (a click or cough doesn't count) and ends
  after `VOICE_END_SILENCE` of quiet. Standard library only.
- **Spoken replies** drop links, `Sources:` blocks and `[1]` citation
  marks, spell out units ("27°C" → "27 degrees Celsius", °F likewise) and
  stop after about 500 characters with "The rest is on screen."
  `SapiSpeaker` speaks sentence by sentence and can be stopped mid-sentence.
- **Interrupting AURORA.** In the terminal, press any key; in the Command
  Centre, click **■ Stop**. Optional **barge-in** (`VOICE_BARGE_IN`, off by
  default): while AURORA speaks, a strict detector listens and stops it
  when you start talking. It is off by default because laptop speakers can
  trigger it.
- **Privacy and control.** The microphone (permission `microphone` for the
  component `Voice`) opens only after `listen`, `voice on` or a click on
  **🎙 Ask by voice**; the terminal and window show when it is on, with a
  level meter. It is closed while AURORA speaks (unless you turn on
  barge-in, which only measures loudness and never transcribes). Audio is processed in memory, never saved, and
  transcripts are never logged.
- **Voice is optional.** The packages and model load only on the first
  voice command; without them AURORA works as before and explains what's
  missing.

Settings: `VOICE_MODEL_DIR`, `VOICE_INPUT_DEVICE`, `VOICE_LISTEN_SECONDS`,
`VOICE_END_SILENCE`, `VOICE_NO_SPEECH_SECONDS`, `VOICE_VAD_RATIO`,
`VOICE_VAD_MIN_RMS`, `VOICE_RATE`, `VOICE_NAME` ("Zira" or "David"),
`VOICE_VOLUME`, `VOICE_BARGE_IN`, `VOICE_MAX_SPOKEN_CHARS`,
`VOICE_CORRECTIONS`. Name, speed, volume, microphone and barge-in can be
changed on the Command Centre's Settings page.

**Loop-back self-test** (`python -m aurora.voice.selftest`, or
`"AURORA Command Centre.exe" --voice-check`): Windows' voices speak ten
typical requests into WAV files, Vosk transcribes them and the engine's
router checks each is understood as the right command. No microphone or
speakers are used. Result on the development laptop (both voices, two
speeds, 20 trials): 0.9 % word error rate, 100 % correct commands, about
2.3 s per sentence. Real microphones and rooms are noisier than this.

pyttsx3 was dropped: with version 2.99 on this machine only its first
reply was spoken, saving to a file hung on the second call and its
callbacks never fired.

## Vision

`aurora/vision/` lets AURORA look at pictures you choose and, when you ask,
through the camera. Everything is processed on this computer.

| You say / do | What happens |
|---|---|
| describe desk.jpg · what's in photo.png? · read photo.jpg | A picture from `Documents/`: grounded description with evidence (no camera) |
| Vision Centre: Open image… / Paste / drag a file in, then Describe | The same, for any picture you choose; the boxes are drawn on it |
| Chat: 🖼 Image… | The same, as a chat reply |
| what do you see? · is anyone in the room? · describe the room | One camera picture → grounded description |
| read this · what does this say? | One camera picture → printed text (OCR) |
| `watch` | Motion alerts with the time and what's in view, until Ctrl+C |

A **grounded description** only says what its evidence supports, and
numbers each object so it can be checked against the picture:

```
desk.jpg: I can see a person on the left [1] and two cups (centre and right) [2, 3].
I might also see a vase at the top [4], but I'm not sure. The main colours are
white and brown. It contains text: "BLACK HOLE NOTES".

Evidence:
  [1] person: 82% sure, left, 19% of the picture (seen in the mirrored picture too)
  ...
  [4] vase: 45% sure, top, 3% of the picture (not certain)
  Text: "BLACK HOLE NOTES" (97% sure)
  Colours: white 41%, brown 23%
  Measured: brightness 63%, contrast 32%, sharpness 533
```

Components:

- **Image input and preprocessing** (`image_input.py`). The format is
  recognised from the file's first bytes, not its name (a program renamed
  `.jpg` is refused). Files over `VISION_MAX_IMAGE_MB` (25 MB) are refused
  before they are read, and pictures over `VISION_MAX_PIXELS` (50 million)
  are refused from their header before decoding, so a tiny file claiming
  to be enormous (a "decompression bomb") can't use up memory. Phone
  photos stored sideways are turned upright from their EXIF note; grey,
  palette, 16-bit and transparent images become ordinary colour
  (transparency on white); pictures larger than
  `VISION_ANALYSIS_MAX_SIDE` (1600 px) are scaled down for analysis.
  Brightness, contrast, sharpness and main colours are **measured** and
  turned into warnings ("very dark", "blurry") when they could hide things.
- **Object detection** (`detector.py`): NanoDet-Plus from OpenCV's model
  zoo (Apache-2.0, 3.8 MB, `Models/object_detection_nanodet_2022nov.onnx`),
  80 COCO classes, CPU only via `cv2.dnn`. Outputs at strides 8/16/32 are
  paired by shape and decoded (distribution focal loss → box distances),
  then non-maximum suppression.
- **Looking twice** (`analyzer.grounded_objects`). The detector runs on
  the picture and on its mirror image. Patterns that only resemble an
  object (waves, petals, ribbons) tend to change when mirrored; real
  objects don't. An object is **certain** when both views find it in the
  same place with at least `VISION_CERTAIN_CONFIDENCE` (0.5); if both find
  it less confidently (from `VISION_MIN_CONFIDENCE`, 0.4) AURORA says it
  "might" see it; if only one view finds it, it isn't mentioned.
- **Text** (`analyzer.TextReader`): RapidOCR (ONNX, offline). Loads on
  first use (~4 s); each line keeps its confidence and box, and is cleaned
  as untrusted text: words in a picture are quoted, never followed.
- **Scene report** (`scene.py`): `SceneReport` holds the evidence and
  writes the description, the evidence list, a JSON form and the
  annotated picture (numbered boxes: solid cyan for certain, thin amber
  for uncertain, thin green for text). The numbers in the text match the
  boxes. Spoken replies leave out the numbers and the evidence list.
- **Vision models**: the detector and OCR load on first use and are
  shared by the Camera tool, the Documents tool and the Command Centre;
  a lock lets one analysis run at a time. There is no captioning model:
  one that describes scenes freely needs more than 1.5 GB of RAM and,
  unlike the report above, can't show where each claim came from.
- **Camera** (`camera.py`): OpenCV, DirectShow backend. Opened for one
  picture and released, or for the duration of `watch`. The terminal
  shows "📷 Camera on… / Camera off." In the Command Centre it starts only
  after you confirm, shows **● CAMERA ON** while on, and turns off when
  you leave the page or choose a picture. Pictures are never saved.
- **Motion** (`motion.py`): difference from a slowly updated background;
  2 % of the picture changing counts. `watch` waits
  `VISION_MOTION_COOLDOWN` (10 s) between alerts and publishes
  `vision.motion_detected` (no image data).

**People are only counted, never identified.** There is no face
recognition. The camera needs the `camera` permission for the `Camera`
component; a picture you choose doesn't use the camera. Logs record only
how many objects and text lines were found, never what.

**How well it works** (`python -m aurora.vision.evaluate --labels …`), on
136 local photos labelled by hand: 32 with everyday objects (people,
cups, cakes, clocks, books, suitcases, chairs; Microsoft Office's sample
photos) and 104 without (flowers, landscapes, buildings, abstract
wallpapers):

| Rule | Objects found | Named objects correct | Photos without objects where something is named |
|---|---|---|---|
| Before: anything ≥ 0.4 | 72 % | 69 % | 44 % |
| Now: certain (both views ≥ 0.5) | 63 % | 87 % | 6 % |

About 0.17 s per photo for detection (both views) on the i3 laptop; OCR
adds about 1 s. Silhouettes, drawings and unusual angles are often
missed: that is the small detector's limit. Your own photos may differ;
the benchmark takes any labelled folder.

**Self-check** (`python -m aurora.vision.evaluate`, or
`"AURORA Command Centre.exe" --vision-check`): eight checks on pictures
made in memory: printed text, a sideways phone photo, transparency,
16-bit, a dark picture, a featureless picture, a decompression bomb and
a disguised file.

Settings: `VISION_CAMERA_INDEX`, `VISION_DETECTOR_MODEL`,
`VISION_MIN_CONFIDENCE`, `VISION_CERTAIN_CONFIDENCE`,
`VISION_OCR_MIN_CONFIDENCE`, `VISION_MAX_IMAGE_MB`, `VISION_MAX_PIXELS`,
`VISION_ANALYSIS_MAX_SIDE`, `VISION_MOTION_THRESHOLD`,
`VISION_MOTION_COOLDOWN`.

## Sensor Hub

`aurora/sensors/` connects AURORA to room sensors (and two optional,
informational health readings):

```
ESP32 / simulator ── device interface (sources.py: HTTP JSON, validation, timestamps, permitted list)
      ── SensorHub (hub.py: one snapshot at a time, newest value wins, last known value fallback)
      ── SensorStore (store.py: history; health kept separately)
      ── SensorService (service.py: optional background watching)
      ── event bus: sensor.readings, sensor.alert, sensor.monitoring
      ── Sensors tool (answers) · Sensor Centre and Dashboard (display) · Windows notifications
```

| You say | Answer |
|---|---|
| what's the temperature in my room? · how humid is it inside? | Value, where and when it was measured, what it means: "Temperature in your room: 23.4 °C (Room ESP32, 10:42, just now): comfortable." plus the guide it's based on and the trend |
| what's the CO₂ level? · VOCs · pm2.5 · how bright is it in here? | The same for air and light |
| how is the air in my room? · is it stuffy? | All air readings and a verdict ("Needs attention: CO₂. Open a window…") |
| is my room comfortable? | Temperature, humidity, light and air with a verdict |
| should I open a window? | Yes / no from CO₂, VOCs and humidity, with the readings behind it; "I can't tell" without an air sensor |
| how has the temperature in my room changed today? · co2 trend | Lowest, highest and average over 24 hours with times, and the trend |
| when was the last motion? · is there any motion in my room? | From the motion sensor and its history |
| check my sensors | Everything, and which quantities have no reading |
| what's my heart rate? · my blood oxygen | Health tool: reading plus "informational only, not a medical measurement"; never interpreted |

**Quantities** (`reading.py`): temperature, humidity, air pressure,
ambient light (lux), motion, CO₂ (NDIR sensors such as SCD40/MH-Z19),
estimated CO₂ and VOCs (SGP30, labelled "estimated"), fine dust PM2.5 and
PM10, heart rate and SpO2. Other simple sensors can be declared in
`SENSOR_EXTRA_QUANTITIES` (name, label, unit, plausible range): they are
read, stored and shown, but never interpreted and never health data.

**Nothing is invented:**

- Every value is checked against a plausible range; NaN, infinity, wrong
  types and impossible values are refused and reported as a note.
- Every reading carries the time it was **measured**: the moment it
  arrived, minus the age the device reports (`"ages"`). Values older than
  `SENSOR_MAX_SAMPLE_AGE_SECONDS` (5 minutes) are refused, not shown as
  current. If a device doesn't answer, a stored value up to
  `SENSOR_MAX_AGE_MINUTES` (10) old is used and labelled "last known value".
- A source can be limited to **permitted readings** (`"quantities"` in its
  `SENSOR_SOURCES` entry); anything else it sends is ignored.
- A quantity no device reports shows "no reading" / NO SENSOR, never a
  value. **Simulated values are labelled** everywhere, including alerts and
  trends; the simulator stays within realistic ranges (e.g. CO₂ never below
  outdoor air).
- If two devices report the same quantity, the most recently measured
  value is used.

**Interpretation** (`interpret.py`) uses fixed bands, each with its
reference shown to you: temperature 20–26 °C comfortable; humidity
30–60 % (US EPA); CO₂ 1000 / 1500 ppm (outdoor air is about 420 ppm);
VOCs 220 / 660 / 2200 ppb; PM2.5 and PM10 against the WHO 2021 24-hour
guidelines (15 and 45 µg/m³); light against desk lighting of about
300–500 lux (EN 12464-1). These are general indoor guidelines, not safety
limits. Trends need at least three readings from the same device over a
quarter of the window (1 hour; 3 hours for pressure) and use a
least-squares slope, so one noisy reading doesn't make a trend.
Window advice uses only CO₂, VOCs and humidity: dust isn't a reason to
open a window (outdoor air may be dustier).

**Background watching** (`service.py`, off by default; the switch is in
the Sensor Centre, or `SENSOR_MONITOR`): room sensors are read every
`SENSOR_MONITOR_SECONDS` (60) and each snapshot is published as
`sensor.readings`; a reading that becomes *poor* is published as
`sensor.alert` at most once per `SENSOR_ALERT_COOLDOWN_MINUTES` (30) per
quantity and shown in the Sensor Centre, the activity feed, a Windows
notification (`SENSOR_NOTIFICATIONS`) and, in the terminal, as a line like
a reminder. Last-known values never raise alerts. Watching needs the
`sensors` permission and stops if it is taken away; health sensors are
never read in the background.

- **Storage** (`store.py`, `sensor_data.db`, not in git): room and health
  readings in **separate tables**; room data kept 30 days, health data
  7 days. A value a device reports twice is stored once. Sensor data
  never enters long-term memory, and values are never logged (only counts).
- **Health** is a separate tool needing both `sensors` and `health_data`,
  so it can be switched off on its own by removing its grant.
- **Firmware** (`firmware/esp32_sensor_hub/`): BME280, BH1750, SGP30, PIR
  and MAX30102 on one ESP32, serving the JSON above. Not yet tested on
  real hardware; the same JSON from a virtual device on 127.0.0.1 was read
  end to end (readings, ages, last motion, background watching, an alert,
  and the device going offline).

Settings: `SENSOR_SOURCES`, `SENSOR_TIMEOUT_SECONDS`,
`SENSOR_MAX_AGE_MINUTES`, `SENSOR_MAX_SAMPLE_AGE_SECONDS`,
`SENSOR_EXTRA_QUANTITIES`, `SENSOR_MONITOR`, `SENSOR_MONITOR_SECONDS`,
`SENSOR_ALERT_COOLDOWN_MINUTES`, `SENSOR_NOTIFICATIONS`,
`SENSOR_RETENTION_DAYS`, `HEALTH_RETENTION_DAYS`.

## Devices and automation

**Rule: an AI suggestion never becomes a hardware command by itself.**

| You say | What happens |
|---|---|
| turn on my desk light · switch the fan off · set the fan speed to 2 · dim the lamp to 30% | "Shall I …? (yes / no)", then the change |
| is the fan on? · list my devices | Current state, no confirmation needed |
| remind me in 10 minutes to stretch · remind me at 5 pm to call home · set a timer for 3 minutes | A reminder shown in the terminal when due |
| what are my reminders · cancel my reminders | List / cancel |

Safety, in order:

1. **Only configured devices** (`settings.DEVICES`) exist, each with a
   kind (`light`, `fan`, `plug`), the actions it allows and value limits
   (brightness 1–100 %, fan speed 1–3). Anything else is refused with an
   explanation, never clamped.
2. **Every change asks "yes / no"** through the same confirmation flow
   as deleting memories. A device can skip this only if you add
   `"confirm": False` to it yourself.
3. **Commands come only from the user's own message.** Device intents are
   routed from what you type or say; text from Wikipedia, arXiv,
   documents, OCR, sensors or the language model is never routed, so a
   web page that says "turn on the desk light" can't switch anything
   (tested). Device commands are never part of a multi-step plan.
4. **Rate limit**: at most `DEVICE_MAX_CHANGES_PER_MINUTE` (6) changes per
   device per minute.
5. **Audit trail** of every change (device, change, simulated or real),
   kept in memory and logged.
6. Needs the `hardware_control` permission (component `Devices`).

Drivers (`aurora/devices/drivers.py`): `SimulatedDriver` (default; every
answer says "simulated device") and `HttpRelayDriver` for
`firmware/esp32_device_controller`, which refuses requests without the
shared secret token (`DEVICE_CONTROLLER_TOKEN`, kept in
`local_settings.py`) and starts with everything off.

**Reminders** (`aurora/automation/reminders.py`) run on background timers
for the current session only (AURORA says so when you set one), up to 20
at a time and 24 hours ahead. They are shown, not spoken: the Windows
speech engine must only be used from the main thread. Reminder text is
never logged.

## Multi-agent research

"AURORA, help me continue my research on black hole cosmogenesis" starts a
research session run by a team of specialist agents (`aurora/agents/`):

| Agent | Job | Uses |
|---|---|---|
| 🧠 Memory agent | Your research log for the topic (last session, papers already seen) and related memories | Research log, Recall tool |
| 📄 Documents agent | Notes that mention the topic, used whole; open questions ("…?", "Open question:", "TODO:") | Documents tool |
| 🔭 Research agent | Wikipedia background (left out, with a note, if the closest article is off-topic); newest matching arXiv papers, marked **[NEW]** if not seen in an earlier session | Wikipedia, arXiv tools |
| 🧩 Analyst agent | Which papers share meaningful words with your notes; up to four concrete next steps (which paper to read, a search for each open question) | Blackboard only |
| ✅ Reviewer agent | Which sources answered, what is missing, drops anything without a link, marks the report incomplete when a source failed | Blackboard only |

- A `Coordinator` runs them in order on a shared `Blackboard`, shows each
  agent's progress, and keeps going if one agent fails.
- Agents call tools **through the ToolManager**, so every tool keeps its
  own permission (e.g. no `network` → "Wikipedia is switched off").
- **Nothing in the report is written by the language model.** Everything
  is quoted from a named source, so it can be checked. The agents are
  deterministic because TinyLlama can't reason reliably and a second model
  wouldn't fit in 4 GB; the `Agent` interface allows a model-backed agent
  later.
- At the end AURORA asks "Save this session to your research log?"; only
  on yes is it recorded (tables `research_projects` / `research_sessions`
  in `aurora_memory.db`). "list my research projects" shows them.
- Research sessions are never part of a multi-step plan.

## Planning

`Planner.plan(message)` (`aurora/planning/planner.py`) returns a `Plan`
of `Step`s. Each step is a piece of the message plus its `RouteDecision`.

| Message | Steps |
|---|---|
| weather in Pune and Delhi, then calculate 12 * 7 | weather Pune · weather Delhi · calculator `12 * 7` |
| what is 2+2 and 3*5 | calculator · calculator (no language model involved) |
| weather in Pune tomorrow and what should I wear? | weather Pune tomorrow · conversation (Brain gets the forecast) |
| my name is Harry and what is 15 * 4? | conversation (name is learned) · calculator |
| what is 2+2 and remember that I like tea and coffee | calculator · remember "I like tea and coffee" |
| what is 2+2 and forget my exam | calculator, plus a note to ask for the deletion separately |
| I like tea and coffee · remember that … and … | single step |

Rules:

1. Always split on "then", "after that", "also", ";" and sentence ends.
2. Split on "and" / commas only if at least one part is a tool request
   and every other part is a real clause (3+ words).
3. "remember that …" owns everything after it.
4. A weather request for several places becomes one step per place.
5. A weather step without a place or day inherits them from the
   previous weather step; identical steps are merged.
6. **Deletions are never part of a plan.** The rest runs, and AURORA asks
   for the deletion in a separate message, so a confirmation can't be
   buried in a longer request.
7. At most `settings.PLAN_MAX_STEPS` (5) steps; AURORA says if it stopped early.

Running a plan: tool steps run in order and each reply is shown as-is.
Conversational parts go to the Brain once, with a system message listing
the tool results ("accurate: use them, don't contradict or recompute them")
in addition to the memory context. Statements inside a plan are still
learned by AutoMemory. Events: `planning.created`,
`planning.step_completed`, `planning.completed` (intents only, no user text).

Type `plan <message>` in the terminal to preview a plan without running it.

## Knowledge and sources

AURORA keeps four kinds of knowledge apart, and every reply says which
it used (`engine.last_sources`, shown under each reply in the Command
Centre):

| Kind | Meaning | Examples |
|---|---|---|
| language model | The model's training data: unverified, may be wrong or out of date | Ordinary conversation |
| memory | Something you told AURORA | "what's my name?", memories given to the model |
| your documents | A passage in a file in `Documents/`, with file and page | "according to my notes …" |
| live source | An external service, fetched just now, with a link and time | Wikipedia, arXiv, Open-Meteo |
| calculated | AURORA's own code | Calculator, Numbers, workflow operations |
| sensor | A sensor reading (or the simulator) | "what's the temperature in my room?" |

**Unverified answers are labelled.** When the language model answers a
factual question (not one about AURORA or you), the reply ends with:

> (This answer comes from my language model's training. It isn't checked
> against a source and may be wrong; say "look up …" to check Wikipedia,
> or "what do my notes say about …".)

Set `LABEL_MODEL_ANSWERS = False` to turn this off.

**External web access happens only through explicit tools:** Weather,
Wikipedia and arXiv. Each needs the `network` permission and names its
source in the reply. There is no general browsing or web search.

**Answering from documents** (`aurora/knowledge/`):

1. **Indexing:** `index.py` splits each document into passages of about
   120 words without crossing pages, and stores them in SQLite FTS5 (Porter
   stemming, BM25 ranking). It re-reads only files whose size, time or
   content changed. Ingestion goes through the Documents tool, so only
   files in `Documents/` are read.
2. **Answering:** `answers.answer()` searches, then scores every sentence
   of the best passages by how much of the question it covers, weighting
   rare words more.
   - Above 0.6: quote the best one to three sentences ("From your
     documents").
   - Above 0.34: show the closest passages and say it isn't a clear
     answer.
   - Otherwise: "I couldn't find anything about that in your documents."
3. **Citations:** each quote is numbered, with a source list such as
   `[1] thesis.pdf, page 2`. Quotes are exact sentences from the
   document; a test checks this.
4. **Disagreements:** if sentences from *different* documents give
   different figures for the same unit, the answer adds a note, for
   example "the sources give different figures: 27 percent
   (astronomy_notes.md) vs 25 percent (lecture.txt)".
5. **Summaries and comparisons:** these quote too.
   - A document summary picks its most representative sentences, in
     order.
   - A topic summary picks the sentences most about the topic.
   - A comparison shows each source's best sentences side by side, with
     differing figures noted. A Wikipedia side calls the Wikipedia tool
     through the tool manager, so its permission still applies.

Why quote instead of writing an answer: TinyLlama can't be trusted to
restate a source faithfully, and a quoted sentence can always be checked.
When a stronger model is available, it can phrase answers *from* these
quotes, with the quotes still shown.

## Workflows

`aurora/workflows/`. Some requests need more than a list of independent
steps: the answer must be computed exactly, or one step needs another
step's result. These become a **workflow**, a structured, testable
sequence of steps that runs without the language model:

| Message | Steps |
|---|---|
| Find the average of these numbers and explain the result: 4, 8, 15, 16, 23, 42 | Numbers (mean, explain) |
| what's the weather in Pune and double the temperature | Weather Pune · multiply 2 (uses the temperature from step 1) |
| what is 12 * 7, then add 5 and halve the result | Calculator · add 5 (step 1) · divide 2 (step 2) |
| weather in Pune and convert the temperature to Fahrenheit | Weather · convert to °F (step 1) |

How a message becomes a workflow (`builder.py`):
1. Follow-up operations at the end are split off: "and double it",
   "then add 5", "and convert the temperature to Fahrenheit".
2. The rest is planned by the Planner as usual.
3. Each operation depends on the step before it, so operations chain.

No workflow is built if a part needs the Brain, a confirmation (devices,
forgetting) or clarification. Those messages go through the normal plan.

**Typed values** (`values.py`): tool results become named values with
units. Calculator and Numbers give their result. Weather ("now") gives
temperature, feels like, humidity and wind, rounded exactly as the user
saw them. "it" / "the result" means the previous step's main value;
"the temperature" picks it by name.

**Running** (`runner.py`):
- **Tool steps** go through the ToolExecutor (same permission checks as
  everywhere). A failure marked `retryable` (timeout, no connection,
  server error) is retried once after a pause. "No such place" is not
  retried.
- **Plausibility:** values must be finite and plausible (outdoor
  temperature −90 to 60 °C, humidity 0–100 %). An implausible value
  fails its step instead of being used.
- **Dependent steps:** a step whose input failed is skipped, and the
  reply says why. Independent steps still run, and nothing is guessed.
- **Confirmations:** a step asking for confirmation is refused.
- **Response validation:** text AURORA composes (operation results,
  Numbers explanations) may only contain numbers from the message, the
  tools or the workflow's own computations. Otherwise it is replaced by
  a plain "Result: …" line, and the problem is recorded.

Every run gives a `WorkflowTrace`: for each step, its kind, target,
dependency, status (done / failed / skipped), attempts, values and
message. Its `to_dict()` (metadata only) is emitted as
`workflow.completed`. `engine.last_workflow` holds the last trace.

## Routing

`IntentRouter.route(message)` (`aurora/routing/router.py`) returns a
`RouteDecision` (`aurora/routing/decision.py`):

| Field | Meaning |
|---|---|
| `intent` | `conversation`, `calculator`, `weather`, `remember`, `recall`, `forget`, `clarify` |
| `confidence` | 0.0–1.0 |
| `target` | Component to handle it: `Brain`, `Calculator`, `Weather`, `Memory`, or `None` for clarify |
| `arguments` | Read-only, e.g. `{"expression": "12 * (7 + 3)"}`, `{"location": "Pune", "when": "tomorrow"}`, `{"content": "..."}`, `{"query": "..."}` |
| `reason` | Why, for logs |
| `alternatives` | The other candidates, best first |

How it decides:

1. Strip polite/wake-word prefixes ("Aurora, please can you …").
2. Every rule (calculator, weather, remember, forget, recall) returns a
   scored candidate or nothing. A rule that crashes is logged and skipped.
3. A default `conversation` candidate is added at
   `settings.ROUTE_CONVERSATION_CONFIDENCE` (0.5). A tool only wins if its
   rule is more confident than that.
4. If the two best tool candidates differ by less than
   `settings.ROUTE_CLARIFY_MARGIN` (0.1), the result is `clarify`.
   Explicit memory commands ("remember that …", "forget …") are exempt:
   whatever follows the command is content, not a competing request.

Rules are conservative on purpose. Examples:

| Message | Intent |
|---|---|
| `what is 5 plus 3?` | calculator `5 + 3` (0.95) |
| `I need 3/4 cup of flour` | conversation (calculator kept as a 0.45 alternative) |
| `this is well-known`, `2004-11-04`, `555-1234`, URLs | conversation |
| `what's the weather in Pune tomorrow?` | weather, Pune, tomorrow (0.9) |
| `what is the temperature in my room?` | conversation (indoor; a sensor question, not weather) |
| `remember that my exam is on Friday` | remember (0.95) |
| `I can't remember the formula` | conversation |
| `my favourite colour is blue` | conversation (automatic extraction is Milestone 5) |
| `what is my favourite colour?` | recall (0.75) |
| `forget it` | conversation ("never mind") |

Type `route <message>` in the terminal to see a decision without sending
the message to the Brain.

## Packages

| Package | Files | Responsibility | Used by the engine? |
|---|---|---|---|
| `aurora.config` | `settings.py` | App name, version, model, absolute paths, log level | Yes |
| `aurora.utils` | `log.py` | `setup_logging()`: file log at `Logs/aurora.log`, warnings to console | Yes (`main.py`) |
| `aurora.kernel` | `engine.py`, `service.py`, `service_manager.py`, `lifecycle.py`, `permissions.py`, `context.py` | Orchestration; service lifecycle (`STOPPED → STARTING → RUNNING → STOPPING`, or `ERROR`); permissions; the context handed to plugins | Yes |
| `aurora.services` | `brain_service.py` | `BrainService` is the **only** owner of the `Brain` | Yes |
| `aurora.brain` | `brain.py` | Reasoning/language, delegates to a provider | Yes |
| `aurora.providers` | `base_provider.py`, `ollama_provider.py` | Model backends behind one `chat(messages)` interface | Yes |
| `aurora.conversation` | `conversation.py` | Working memory: message history for the current session | Yes |
| `aurora.personality` | `personality.py` | AURORA's system prompt | Yes |
| `aurora.plugins` | `plugin.py`, `plugin_manager.py` | Plugin lifecycle with permission checks | Yes (none registered) |
| `aurora.memory` | `store.py`, `manager.py`, `extractor.py`, `evaluator.py`, `auto.py`, `episodes.py`, `routines.py` | Long-term memory (see MEMORY_DESIGN.md): semantic and associative facts with provenance, retention and recall; episodic activity history; routines | Yes |
| `aurora.knowledge` | `fact.py`, `sources.py`, `index.py`, `answers.py` | The `Fact` model; source kinds and citations; the document index; answers, summaries and comparisons | Yes |
| `aurora.routing` | `intent.py`, `decision.py`, `router.py` | Scored rule-based `IntentRouter` returning a structured `RouteDecision` | Yes |
| `aurora.planning` | `plan.py`, `planner.py` | Splits a message into steps; the engine runs them | Yes |
| `aurora.interface` | `app.py`, `backend.py`, `main_window.py`, nine views, `components/`, `styles/`, `system_info.py` | The AURORA Command Centre desktop interface (see COMMAND_CENTRE.md) | Separate front end |
| `aurora.agents` | `base.py`, `research_agents.py`, `research_session.py`, `research_log.py` | Agent framework, research team, coordinator, research log | Yes (Research tool) |
| `aurora.devices` | `device.py`, `drivers.py`, `controller.py` | Device definitions, limits, simulated / ESP32 drivers, checks, rate limit, audit | Yes (Devices tool) |
| `aurora.automation` | `reminders.py` | Session reminders and timers | Yes (Reminders tool) |
| `aurora.sensors` | `reading.py`, `sources.py`, `store.py`, `hub.py`, `interpret.py`, `service.py` | Sensor readings, validation and timestamps, ESP32 / simulated sources, history with separate health storage, interpretation and trends, background watching with events and alerts | Yes (Sensors and Health tools, Sensors service) |
| `aurora.vision` | `image_input.py`, `camera.py`, `detector.py`, `analyzer.py`, `scene.py`, `evaluate.py`, `motion.py`, `watch.py` | Image input and preprocessing, webcam, object detection checked in two views, OCR, grounded scene reports, evaluation, motion watch | Yes (Camera and Documents tools, Command Centre, `watch`) |
| `aurora.voice` | `listener.py`, `vad.py`, `speaker.py`, `session.py`, `selftest.py` | Offline speech recognition with wake word and voice activity detection, text-to-speech with interruption, voice conversations, loop-back test | Yes (from `main.py` and the Command Centre, on demand) |
| `aurora.tools` | `tool.py`, `tool_manager.py`, `result.py`, `executor.py`, `untrusted.py` | Tool interface, permission-checked registry, `ToolResult`, intent-to-tool execution, cleaning of outside text | Yes |
| `aurora.tool_plugins` | `calculator.py`, `numbers.py`, `memory_tools.py`, `weather.py`, `research.py`, `documents.py`, `registry.py` | The built-in tools and `default_tools()` | Yes |
| `aurora.workflows` | `values.py`, `operations.py`, `workflow.py`, `builder.py`, `runner.py`, `validation.py` | Structured multi-step tasks: typed values between steps, validation, retries, traces | Yes |
| `aurora.events` | `event.py`, `bus.py`, `topics.py` | Publish/subscribe with error isolation; standard event names | Yes |
| `aurora_core` (separate package) | `tensors/`, `transformer/`, `tokenizer/`, `training/`, `generation/`, `runtime/` | AURORA's own neural network: tensor maths with gradient checks, the AURORA-T0 Transformer with backpropagation, character and byte-level BPE tokenizers, the training pipeline and experiments, text generation, and the AURORA Core Runtime with its optimised engine (see AURORA_CORE.md) | No, not yet |

## Design rules

1. **One owner per component.** The Brain is created once and owned by
   `BrainService`; the engine reaches it through `engine.brain`.
2. **Dependency injection.** `Brain`, `BrainService`, `AuroraEngine`,
   `MemoryManager` and `MemoryStore` accept their dependencies as
   arguments, so tests can pass fakes (see `Tests/conftest.py`).
3. **Failures are isolated.** A service that fails to start is marked
   `ERROR`; a plugin that fails to initialize is disabled. Others continue.
4. **The engine never prints.** Terminal output belongs to `main.py`;
   everything else uses `logging.getLogger(__name__)`.
   **Logs never contain user content**: no messages, memory values,
   place names, tool arguments or model replies. Only metadata
   (intents, tool and attribute names, ids, counts, error types).
   `Tests/test_log_privacy.py` enforces this.
5. **No hardcoded configuration.** Model names, paths and versions come
   from `aurora/config/settings.py`. Paths are absolute, so running from any
   folder uses the same database and log.
6. **Untrusted input is never executed.** The calculator only accepts
   numbers and arithmetic operators, with limits on length, exponents and
   result size.

## Data

- `aurora_memory.db` (project root): SQLite table `facts`
  (`id, category, entity, attribute, value, priority, confidence,
  created, last_used, times_used`). Not committed to git.
- `Logs/aurora.log`: application log (metadata only, no user content). Not committed to git.

## Testing

```
.venv\Scripts\python.exe -m pytest
```

Tests live in `Tests/`, use a fake provider instead of Ollama and a
temporary database instead of `aurora_memory.db`.

## Known gaps (next milestones)

| Gap | Milestone |
|---|---|
| No general web search (needs a provider with an API key) | When a provider is chosen |
| No NASA / astronomy databases, no source cross-checking between results | Later research work |
| ESP32 firmware (sensors and devices) not yet tested on hardware; sensors only over Wi-Fi HTTP (no USB serial); interpretation bands are general guidelines and cheap sensors are uncalibrated; no wearable (sleep) support | Later sensor work |
| Research agents match words, not meaning; no free-form reasoning or summarising (needs a stronger model); no deleting research projects by voice/text yet | Later |
| No rule-based automations ("turn on the fan when it's above 28 °C"): they would act without a per-change confirmation, so they need their own safety design | Later automation work |
| Reminders are lost when AURORA closes and are not spoken | Later |
| Vision recognises 80 everyday object classes only and misses about a third of them (silhouettes, drawings, unusual angles); no free-form scene descriptions or relations between objects; no recording or face recognition (by design) | A larger detector or captioning model needs more RAM |
| The small speech model misses uncommon names and places ("Pune" → "pony"); no voice profiles; barge-in is energy-based (off by default) and can be set off by loud speakers | Larger model / later voice work |
| Document answers match words (with stemming), not meaning, and quote rather than explain; scanned PDFs (images) have no readable text; no general web search | Semantic search, a stronger model, OCR, a search provider |
| Recall matches words and a fixed synonym list, not meaning | Semantic search, when it fits in 4 GB RAM |
| Associative memory links people to facts only (no general concept graph); routines have no conditions | Later memory work |
| TinyLlama often ignores or garbles the memory context it is given | A stronger model (hardware-dependent) |
| Workflows pass only numbers between steps (from the calculator, Numbers and current weather), and follow-ups are a fixed set of operations; no text results or forecasts as inputs yet | Later workflow work |
| Routing is rule-based English only; no LLM fallback for unusual phrasing | Later, once a model good at structured output is available |
| Permission grants are static config; no prompt to ask the user at runtime | Later, with physical-action tools (11) |
| AURORA's own runtime (`aurora_core.runtime`) works, but AURORA-T0 is far too small to converse (see its model card), so the assistant still uses Ollama | Until a model good enough to converse exists |
| Event bus is synchronous; a slow subscriber delays the publisher | When voice/sensors need it (8, 10) |
