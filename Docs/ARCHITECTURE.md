# AURORA Architecture

Version: 0.13.0
Status: Describes what exists in the code today. Planned components are listed separately at the end.

---

## Overview

AURORA is a modular personal AI assistant. A small application-level
kernel (not an operating-system kernel) starts, coordinates and stops
independent components. Each component has one responsibility and can be
tested on its own.

```
main.py  (terminal UI: all printing and input; voice commands)
   │  └── VoiceSession ── Listener (Vosk, offline) + Speaker (Windows voices)
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
   │      └── Camera ── webcam, one picture per request (camera)
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
| Remember | `remember` | `memory.write` | Stores an explicit note ("remember that ...") as a `Note` fact |
| Recall | `recall` | `memory.read` | Keyword lookup. Only memories containing **every** keyword count as an answer; partial matches are listed separately as "related" |
| Forget | `forget` | `memory.read`, `memory.write` | Lists matching memories and **asks for confirmation** before deleting (including exact duplicates) |
| Weather | `weather` | `network` | Open-Meteo: current conditions and today, tomorrow, this week, this weekend |
| Wikipedia | `knowledge` | `network` | Encyclopedia summary ("tell me about black holes", "who is …", "what is a neutron star") with a link to the article |
| arXiv | `papers` | `network` | Up to 5 papers with authors, dates and links; newest first for "recent / latest", otherwise most relevant |
| Documents | `documents` | `filesystem.read` | List, search (with file and PDF page citations) and read .txt, .md and .pdf files in `Documents/` only |

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
speech recognition, and the voices built into Windows (SAPI5, via pyttsx3)
for speech.

| Command | What happens |
|---|---|
| `listen` | The microphone opens for one request (up to 10 s, ends when you pause), AURORA answers on screen and aloud, the microphone closes |
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
- **Spoken replies** drop links and source lines, spell out units
  ("27°C" → "27 degrees Celsius") and stop after about 500 characters with
  "The rest is on screen."
- **Privacy and control.** The microphone (permission `microphone` for the
  component `Voice`) opens only after `listen` or `voice on`, the terminal
  shows when it is on and off, and it is closed while AURORA speaks so it
  never hears itself. Audio is processed in memory, never saved, and
  transcripts are never logged.
- **Voice is optional.** The packages and model load only on the first
  voice command; without them AURORA works as before and explains what's
  missing.

Settings: `VOICE_MODEL_DIR`, `VOICE_INPUT_DEVICE`, `VOICE_LISTEN_SECONDS`,
`VOICE_END_SILENCE`, `VOICE_RATE`, `VOICE_NAME` ("Zira" or "David"),
`VOICE_MAX_SPOKEN_CHARS`, `VOICE_CORRECTIONS`.

## Vision

`aurora/vision/` adds the camera, all processed on this computer:

| You say / type | What happens |
|---|---|
| what do you see? · is anyone in the room? · describe the room | One picture → objects: "I can see a person in the middle, a laptop and two cups." |
| read this · what does this say? | One picture → printed text (OCR) |
| read photo.jpg | Objects and text in an image from `Documents/` (no camera) |
| `watch` | Motion alerts with the time and what's in view, until Ctrl+C |

Components:

- **Camera** (`camera.py`): OpenCV, DirectShow backend. Opened for one
  picture and released, or for the duration of `watch`. The terminal
  shows "📷 Camera on… / Camera off." Pictures are never saved.
- **Object detection** (`detector.py`): NanoDet-Plus from OpenCV's model
  zoo (Apache-2.0, 3.8 MB, `Models/object_detection_nanodet_2022nov.onnx`),
  80 COCO classes, CPU only via `cv2.dnn`. Outputs at strides 8/16/32 are
  paired by shape and decoded (distribution focal loss → box distances),
  then non-maximum suppression.
- **Text** (`analyzer.TextReader`): RapidOCR (ONNX, offline). Loads on
  first use (~4 s); each recognised line is cleaned as untrusted text.
- **Scene description** (`analyzer.describe`): built from the detections,
  with positions for a single person (left / middle / right). No
  captioning model: one that describes scenes freely needs more than
  1.5 GB of RAM.
- **Motion** (`motion.py`): difference from a slowly updated background;
  2 % of the picture changing counts. `watch` waits
  `VISION_MOTION_COOLDOWN` (10 s) between alerts and publishes
  `vision.motion_detected` (no image data).

**People are only counted, never identified.** There is no face
recognition. The camera needs the `camera` permission for the `Camera`
component; logs record only how many objects were detected.

Settings: `VISION_CAMERA_INDEX`, `VISION_DETECTOR_MODEL`,
`VISION_MIN_CONFIDENCE`, `VISION_OCR_MIN_CONFIDENCE`,
`VISION_MOTION_THRESHOLD`, `VISION_MOTION_COOLDOWN`.

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
| `aurora.memory` | `store.py`, `manager.py`, `extractor.py`, `evaluator.py`, `auto.py` | Long-term memory: extraction, evaluation, conflict resolution, keyword recall with stemming, context for the Brain | Yes |
| `aurora.knowledge` | `fact.py` | The `Fact` data model | Yes |
| `aurora.routing` | `intent.py`, `decision.py`, `router.py` | Scored rule-based `IntentRouter` returning a structured `RouteDecision` | Yes |
| `aurora.planning` | `plan.py`, `planner.py` | Splits a message into steps; the engine runs them | Yes |
| `aurora.vision` | `camera.py`, `detector.py`, `analyzer.py`, `motion.py`, `watch.py` | Webcam, object detection, OCR, scene description, motion watch | Yes (Camera tool, `watch`) |
| `aurora.voice` | `listener.py`, `speaker.py`, `session.py` | Offline speech recognition with wake word, text-to-speech, voice conversations | Yes (from `main.py`, on demand) |
| `aurora.tools` | `tool.py`, `tool_manager.py`, `result.py`, `executor.py`, `untrusted.py` | Tool interface, permission-checked registry, `ToolResult`, intent-to-tool execution, cleaning of outside text | Yes |
| `aurora.tool_plugins` | `calculator.py`, `memory_tools.py`, `weather.py`, `research.py`, `documents.py`, `registry.py` | The built-in tools and `default_tools()` | Yes |
| `aurora.events` | `event.py`, `bus.py`, `topics.py` | Publish/subscribe with error isolation; standard event names | Yes |

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
| Vision recognises 80 everyday object classes only; no free-form scene descriptions, no recording or face recognition (by design) | Larger models need more RAM |
| The small speech model misses uncommon names and places ("Pune" → "pony"); no voice profiles or barge-in (interrupting AURORA while it speaks) | Larger model / later voice work |
| Document search matches words, not meaning; scanned PDFs (images) have no readable text | Semantic search / OCR later |
| Recall matches words, not meaning ("hometown" won't find "From: Nagpur") | Semantic search, when it fits in 4 GB RAM |
| Only semantic memory; no episodic, procedural or associative (knowledge graph) memory | Later memory work |
| TinyLlama often ignores or garbles the memory context it is given | A stronger model (hardware-dependent) |
| Plans are fixed splits: a step can't use another step's result as input ("double the temperature in Pune") | Later planning work, needs a stronger model |
| Routing is rule-based English only; no LLM fallback for unusual phrasing | Later, once a model good at structured output is available |
| Permission grants are static config; no prompt to ask the user at runtime | Later, with physical-action tools (11) |
| Event bus is synchronous; a slow subscriber delays the publisher | When voice/sensors need it (8, 10) |
