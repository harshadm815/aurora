# AURORA Architecture

Version: 0.21.0
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
   │      └── BrainService ── Brain ── ModelSwitch ─┬─ OllamaProvider ── Ollama (tinyllama)
   │                                    (timeout +  └─ CoreRuntimeProvider ── AURORA Core Runtime
   │                                    reply cap)       ("aurora:<folder>" models, no Ollama)
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
so if Ollama is down or the model is missing (or, for an `aurora:` model,
its checkpoint is missing) the Brain is marked `Error`
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

## Network gate

The `network` permission says *whether* a tool may use the network at all.
The network gate (`aurora/network/gate.py`) controls *each request*. Every
HTTP client AURORA creates comes from `gate.client(component)` (a test checks
that no module creates any other), so every request, including every
redirect, passes it:

| Destination | Rule |
|---|---|
| Not one of that service's hosts (`NETWORK_SERVICES`, or the sensor / device URL you configured) | always blocked |
| The internet | `INTERNET_ACCESS`: `"ask"` (default), `"on"` or `"off"` |
| Your own network (private addresses, `.local` names) | `LOCAL_NETWORK_ACCESS` (default on) |
| This PC (localhost) | allowed |

- **Ask:** the first request from each service in a session is held back.
  In the chat AURORA says where it would go and what it would send ("Weather
  needs the internet for live weather: it would send the place name to
  geocoding-api.open-meteo.com. Allow Weather to go online until AURORA
  restarts?"). Yes repeats the request; no sends nothing.
- **Settings → Internet and network** changes both switches. **System →
  Network activity** lists every request made or blocked this session
  (time, service, host, outcome) and can forget the approvals.
- **Nothing you asked about is recorded:** only the host, never the path
  or query. The list is kept in memory and cleared when AURORA exits; the
  log file gets the same one-line entries.
- **What never goes online:** conversations, memories, documents and the
  language model. Ollama and AURORA's own model run on this PC (Ollama is
  reached by its own client on localhost, outside the gate).
- **Limits:** only requests from the chat can ask for approval. A blocked
  request from the research team, a workflow or a scheduled task fails
  with the service's ordinary "couldn't reach" message; approve the service
  by asking for it once in the chat, or set internet access to "on".

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
| News | `news` | `network` | News search, publisher comparison and Investigation Mode over GDELT, Google News, publisher feeds and (with a key) web search, only when asked; sources, dates, links and checks for every story (see NEWS.md) |
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
`VOICE_VOLUME`, `VOICE_PITCH`, `VOICE_BARGE_IN`, `VOICE_MAX_SPOKEN_CHARS`,
`VOICE_CORRECTIONS`, `VOICE_RECOGNISER`, `VOICE_SYNTHESISER`,
`VOICE_ALLOW_EXTERNAL`. Name, speed, volume, pitch, microphone and barge-in
can be changed on the Command Centre's Settings page.

**Providers** (`aurora/voice/providers.py`): the rest of AURORA asks for
"the recogniser" (`make_recogniser()`) and "the voice" (`make_synthesiser()`)
and never names Vosk or SAPI, so providers can be added or swapped without
changing the Command Centre, the terminal or voice sessions. Each provider
says whether it runs on this PC; one that doesn't is refused unless
`VOICE_ALLOW_EXTERNAL` is on (off by default; none is built in).

**Words while you speak**: `listen_once(..., on_partial=...)` reports the
words recognised so far; Chat and the Dashboard show them in quotes while
listening (never logged).

**Natural voice (Piper)** (`aurora/voice/piper_voice.py`, the default
`VOICE_SYNTHESISER = "piper"`): a neural voice run with onnxruntime,
`Models/tts/piper/<PIPER_VOICE>.onnx` (LJSpeech, public-domain data). On
the development laptop it makes speech about 5 times faster than real
time (first sentence ready in ~0.4 s) and uses about 120 MB loaded, 255 MB
while speaking. Sentences are made on a background thread while the
previous one plays; playback is written in 0.1 s blocks so Stop, barge-in
or a new request cut it off within about a block. If the package or the
voice file is missing, AURORA says why and uses the Windows voice
(`make_synthesiser_or_fallback`). The Command Centre loads it in the
background at start-up when spoken replies are on. Piper and espeak-ng
are GPL-3.0 (see Docs/VOICE_WAKE_MOBILE.md). Kokoro-82M was measured too
and was about 2 times slower than real time here, too slow to converse.

**Pitch**: SAPI's XML pitch tag (-10 to 10) wraps each sentence, with the
sentence's `&`, `<` and `>` escaped so nothing in a reply can act as markup.

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

## Voice conversations and briefings

Chat has three ways to talk: 🎙 Ask by voice (one request), 🗣
Conversation (answer aloud, listen again, until "that's all", silence or
Stop; `aurora/voice/conversation.py` has the end, yes and no phrases), and
waking AURORA, which starts a conversation. The `Briefing` tool
(`aurora/tool_plugins/briefing.py`, intent `briefing`, permission
`memory.read`) builds today's briefing from the Reminders, Schedule and
Research tools' records; `summary_line()` is the one sentence the wake
greeting uses to offer it. `speak_reminders` (Settings → Voice) says due
reminders aloud.

## Wake Engine

`aurora/wake/`: hold a key, say "Wake up", and AURORA starts (or comes
forward), greets you and listens. Details, decisions and measurements are
in Docs/VOICE_WAKE_MOBILE.md.

| Module | Role |
|---|---|
| `keys.py` | `HoldKey`: pure state machine for "is the key being held?" (Right Ctrl never blocks a key; Space is held back and re-typed when it was just a space) |
| `hook.py` | Windows low-level keyboard hook (ctypes) feeding it; re-types held-back keys with SendInput; never stores keys |
| `detector.py` | `WakeSession`: Vosk limited to the phrase, sound-alikes and `[unk]`; audio buffered while the model loads; `ModelCache` frees the model after 10 minutes unused |
| `launcher.py` | Wakes the running Command Centre ("wake" over its single-instance socket) or starts it with `--wake`; starts/stops the helper; startup entry |
| `helper.py` | The background helper (`--wake-helper`): overlay, microphone while held, permission check, one per user (own local socket: quit / reload / ping) |

Settings: `WAKE_KEY`, `WAKE_PHRASE`, `WAKE_HOLD_SECONDS`,
`WAKE_MAX_HOLD_SECONDS`, `WAKE_SOUND`, `WAKE_MODEL_KEEP_MINUTES`,
`WAKE_GREETING`; on the Settings page: on/off, key, phrase, beep, start with
Windows.

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

**Rules: every action AURORA can take is listed with its permission,
whether it asks first and how to undo it (`aurora/automation/actions.py`,
shown on the Command Centre's Automation page). Anything not listed, it
can't do. An AI suggestion never becomes an action by itself, and every
action is written to the audit trail.**

| You say | What happens |
|---|---|
| turn on my desk light · switch the fan off · set the fan speed to 2 | "Shall I …? (yes / no)", then the change |
| open notepad · launch the calculator · close notepad | Opens a program from the `APPS` list; closes only what AURORA opened (asks first) |
| organise my documents by type / by month · undo my last tidy-up | Shows every planned move, asks, moves; can be undone, even after a restart |
| remind me in 10 minutes to stretch · cancel reminder 2 · what are my reminders | Reminders kept on disk; one missed while AURORA was closed is reported at the next start |
| every weekday at 18:30 turn on the fan · every day at 7 run my morning briefing · every evening at 9 remind me to … · every morning at 8 check my sensors | A scheduled task, approved once |
| what tasks are scheduled · pause task 2 · resume task 2 · delete task 2 | Managing tasks |
| undo that | Offers to reverse the most recent reversible action (asks first) |

The catalogue (`ACTIONS`):

| Action | Needs | Asks first | Undo |
|---|---|---|---|
| Change a device | `hardware_control` | yes, unless you set `"confirm": False` for it | "undo that" restores the previous state |
| Open a program | `apps.launch` | if you set `"confirm": True` for it | "close …" if AURORA started it |
| Close a program | `apps.launch` | always | can't be undone (unsaved work is lost) |
| Tidy a folder / undo a tidy-up | `filesystem.read`, `filesystem.write` | always | "undo that" / "undo my last tidy-up" |
| Set / cancel a reminder | (none) | no | cancel / set again |
| Add a scheduled task | `automation` | always | delete or pause it |
| Pause, resume, delete a task | `automation` | only "delete all" | resume / add again |
| Run a scheduled task | `automation` plus the task's own | no (approved when added) | depends on the task |
| Delete memories / a routine | `memory.read`, `memory.write` | always | can't be undone / teach again |

Devices (`aurora/devices/`), safety in order:

1. **Only configured devices** (`settings.DEVICES`), each with a kind
   (`light`, `fan`, `plug`), the actions it allows and value limits
   (brightness 1–100 %, fan speed 1–3). Anything else is refused, never
   clamped.
2. **Every change asks "yes / no"**, unless you set `"confirm": False`.
3. **Commands come only from your own message**: text from web pages,
   documents, OCR, sensors or the language model is never routed, and
   device commands are never part of a multi-step plan (tested).
4. **Rate limit**: `DEVICE_MAX_CHANGES_PER_MINUTE` (6) per device.
5. **Undo**: the state before the change is read first; "undo that" puts
   it back (on, off, level), asking first. A change that changed nothing
   offers no undo.
6. Needs `hardware_control` (component `Devices`). Drivers:
   `SimulatedDriver` (default; every answer says "simulated device") and
   `HttpRelayDriver` for `firmware/esp32_device_controller` (shared
   secret token, starts with everything off).

**Programs** (`aurora/automation/apps.py`): only those in `APPS`
(default: Notepad, Calculator, Paint, File Explorer opening Documents),
each with a fixed command; nothing you say becomes part of it, there is
no shell, and the program must exist on this PC. AURORA closes only
programs it started this session. Some Windows programs (Calculator, the
newer Notepad) hand over to another process at once; AURORA says so
instead of claiming it can close them.

**Tidying files** (`aurora/automation/files.py`): only folders in
`ORGANIZE_FOLDERS` (default: Documents), only files directly in them,
only into subfolders of the same folder (by type: Images, PDFs,
Documents, Notes, Spreadsheets, Presentations, Audio, Video, Archives;
or by month). **Nothing is deleted or overwritten**: clashing names get
" (2)", a file that changed after the preview is left alone, a move onto
an existing file is refused. Programs, scripts, shortcuts, hidden and
system files, links, folders and unknown types stay put. Every move is
recorded in the automation database as it happens; the tidy-up stops at
the first problem (or when you press Stop) and whatever moved can be
undone. Undo never overwrites a file that has appeared since, and
removes only folders it created that are empty.

**Reminders** (`aurora/automation/reminders.py`) are kept in the
automation database: closing AURORA no longer loses them. One still
ahead is set again at the next start; one that fell due while AURORA was
closed is reported then (terminal line, Windows notification, Automation
page), never shown late as if on time. Up to 20, 24 hours ahead. They
now also appear in the Command Centre (before, only the terminal showed
them). Reminder text is never logged.

**Scheduled tasks** (`aurora/automation/schedule.py`), the safety
design that was missing for automations:

- You approve each task once, with its exact action and times spelled out.
- Only four kinds: a reminder, a routine you taught (routines can't
  contain device control, deletions, tidy-ups, schedules or undo), a
  sensor report, or a device change, and only for devices you marked
  `"schedulable": True` (checked when added and at every run).
- Permissions are checked at every run: take away `automation` (or the
  device's `hardware_control`) and the task stops running.
- A task runs once per scheduled time, within `SCHEDULE_GRACE_MINUTES`
  (5). If AURORA wasn't running, the run is skipped and reported, never
  done late. A new task waits for its next time.
- Every run is reported (notification / terminal line), audited
  ("schedule #3") and, for devices, can be undone.
- After `SCHEDULE_MAX_FAILURES` (3) failures in a row a task is paused.
- A scheduled run never replaces a question waiting for your yes / no.

**The audit trail** (`aurora/automation/audit.py`, table `audit` in
`aurora_automation.db`, not in git): every action asked, declined,
cancelled (you moved on), done, failed, undone or skipped, with who
started it ("you" or "schedule #3") and whether the device was
simulated. Kept 90 days (`AUDIT_RETENTION_DAYS`). AURORA's log file only
records the action and outcome.

The **Automation service** (`aurora/automation/service.py`) restores
reminders at start, runs the scheduler (checked every
`SCHEDULE_CHECK_SECONDS`, 20) and publishes `automation.reminder_due`,
`automation.reminders_missed` and `automation.task_ran`. Requests and
scheduled tasks share one engine lock, so they never run at once.

Settings: `DEVICES`, `DEVICE_CONTROLLER_URL`, `DEVICE_CONTROLLER_TOKEN`,
`DEVICE_MAX_CHANGES_PER_MINUTE`, `APPS`, `ORGANIZE_FOLDERS`,
`ORGANIZE_MAX_FILES`, `REMINDER_MAX_ACTIVE`, `REMINDER_MAX_HOURS`,
`AUTOMATION_DATABASE_PATH`, `AUDIT_RETENTION_DAYS`,
`SCHEDULE_MAX_TASKS`, `SCHEDULE_CHECK_SECONDS`, `SCHEDULE_GRACE_MINUTES`,
`SCHEDULE_MAX_FAILURES`.

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
| `aurora.automation` | `actions.py`, `audit.py`, `store.py`, `reminders.py`, `files.py`, `apps.py`, `schedule.py`, `service.py` | The action catalogue, audit trail, saved reminders, tidying folders with undo, opening programs, scheduled tasks, the Automation service | Yes (Reminders, Files, Apps, Schedule tools; Automation service) |
| `aurora.sensors` | `reading.py`, `sources.py`, `store.py`, `hub.py`, `interpret.py`, `service.py` | Sensor readings, validation and timestamps, ESP32 / simulated sources, history with separate health storage, interpretation and trends, background watching with events and alerts | Yes (Sensors and Health tools, Sensors service) |
| `aurora.vision` | `image_input.py`, `camera.py`, `detector.py`, `analyzer.py`, `scene.py`, `evaluate.py`, `motion.py`, `watch.py` | Image input and preprocessing, webcam, object detection checked in two views, OCR, grounded scene reports, evaluation, motion watch | Yes (Camera and Documents tools, Command Centre, `watch`) |
| `aurora.voice` | `listener.py`, `vad.py`, `speaker.py`, `session.py`, `selftest.py` | Offline speech recognition with wake word and voice activity detection, text-to-speech with interruption, voice conversations, loop-back test | Yes (from `main.py` and the Command Centre, on demand) |
| `aurora.tools` | `tool.py`, `tool_manager.py`, `result.py`, `executor.py`, `untrusted.py` | Tool interface, permission-checked registry, `ToolResult`, intent-to-tool execution, cleaning of outside text | Yes |
| `aurora.browser` | `addresses.py`, `store.py`, `reader.py`, `answers.py`, `saving.py`, `network.py` | The AURORA Browser without Qt: address bar, history and bookmarks, reader mode, page answers, saving to Knowledge, network decisions (the Qt WebEngine parts are `interface/browser_web.py` and `browser_view.py`; see BROWSER_AUDIO_VIDEO.md) | Yes |
| `aurora.explorer` | `places.py`, `operations.py`, `store.py` | AURORA Documents (the Documents page): the places shown, safe file operations (only inside the places, never overwriting, delete to the Recycle Bin), favourites and recent files | Yes |
| `aurora.news` | `query.py`, `sources.py`, `processing.py`, `service.py`, `report.py`, `saved.py` | Universal News Intelligence (see NEWS.md) | Yes |
| `aurora.network` | `gate.py` | The network gate: per-service destinations, internet and local-network switches, asking before going online, the activity record | Yes |
| `aurora.tool_plugins` | `calculator.py`, `numbers.py`, `memory_tools.py`, `weather.py`, `research.py`, `documents.py`, `registry.py` | The built-in tools and `default_tools()` | Yes |
| `aurora.workflows` | `values.py`, `operations.py`, `workflow.py`, `builder.py`, `runner.py`, `validation.py` | Structured multi-step tasks: typed values between steps, validation, retries, traces | Yes |
| `aurora.events` | `event.py`, `bus.py`, `topics.py` | Publish/subscribe with error isolation; standard event names | Yes |
| `aurora_core` (separate package) | `tensors/`, `transformer/`, `tokenizer/`, `training/`, `generation/`, `runtime/`, `research/` | AURORA's own neural network: tensor maths with gradient checks, the AURORA-T0 Transformer with backpropagation, character and byte-level BPE tokenizers, the training pipeline and experiments, text generation, the AURORA Core Runtime with its optimised engine (see AURORA_CORE.md), and reproducible research experiments (see RESEARCH.md) | No, not yet |

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
| Scheduled tasks run at times only; no conditions ("turn on the fan when it's above 28 °C"), no intervals ("every 5 minutes"); undo covers the current session (tidy-ups: any time) | Later automation work |
| Reminders aren't spoken, and only reach 24 hours ahead | Later |
| Vision recognises 80 everyday object classes only and misses about a third of them (silhouettes, drawings, unusual angles); no free-form scene descriptions or relations between objects; no recording or face recognition (by design) | A larger detector or captioning model needs more RAM |
| The small speech model misses uncommon names and places ("Pune" → "pony"); no voice profiles; barge-in is energy-based (off by default) and can be set off by loud speakers | Larger model / later voice work |
| Document answers match words (with stemming), not meaning, and quote rather than explain; scanned PDFs (images) have no readable text; no general web search | Semantic search, a stronger model, OCR, a search provider |
| Recall matches words and a fixed synonym list, not meaning | Semantic search, when it fits in 4 GB RAM |
| Associative memory links people to facts only (no general concept graph); routines have no conditions | Later memory work |
| TinyLlama often ignores or garbles the memory context it is given | A stronger model (hardware-dependent) |
| Workflows pass only numbers between steps (from the calculator, Numbers and current weather), and follow-ups are a fixed set of operations; no text results or forecasts as inputs yet | Later workflow work |
| Routing is rule-based English only; no LLM fallback for unusual phrasing | Later, once a model good at structured output is available |
| Permission grants are static config; only internet access is asked for at runtime (the network gate), and only from the chat | Later |
| AURORA-T0 can be chosen as the assistant's model (`aurora:` models, see AURORA_CORE.md), but it is far too small to converse, so Ollama stays the default | Until a model good enough to converse exists |
| Event bus is synchronous; a slow subscriber delays the publisher | When voice/sensors need it (8, 10) |
| Browser: no page summaries or written comparisons (needs a capable model), no translation, no voice control, no questions about the open page from the chat (only in the Browser page); reader mode is a heuristic | Browser milestone 2 (see BROWSER_AUDIO_VIDEO.md) |
| No audio editing/generation or video editing/generation | Planned: Audio Lab, then Video Studio (see BROWSER_AUDIO_VIDEO.md) |
