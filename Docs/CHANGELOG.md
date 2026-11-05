# Changelog

## 0.13.0 — Milestone 9: Vision (2026-11-05)

### Added
- Camera tool: "what do you see?" (objects, with a person's position) and
  "read this" (printed text). One picture per request, never saved;
  the terminal shows when the camera is on.
- Object detection with NanoDet-Plus (OpenCV model zoo, Apache-2.0) on the
  CPU; 80 everyday object classes. People are counted, never identified.
- OCR with RapidOCR (offline).
- `watch` command: motion alerts with time and objects in view, 10 s
  cooldown, Ctrl+C to stop; `vision.motion_detected` event.
- Images (.jpg, .png, .bmp, .webp) in `Documents/`: "read photo.jpg"
  reports objects and text.
- `vision` intent and routing; `Camera` component with the `camera`
  permission; vision settings; `opencv-python` and `rapidocr-onnxruntime`
  in Requirements.txt.
- Tests can never open the real camera or microphone (fixture).
- 390 tests (1 skipped on Windows).

## 0.12.0 — Milestone 8: Voice (2026-11-05)

### Added
- Offline voice (`aurora/voice/`): Vosk speech recognition (small English
  model) and Windows text-to-speech (pyttsx3).
- `listen` (one request by voice), `voice on` (hands-free with the wake
  word "Aurora"; ends on "stop listening" or Ctrl+C), `speak on/off`.
- Wake word via a grammar-restricted recogniser; requests after the wake
  word are followed until the speaker pauses.
- Transcript clean-up: wake-word removal, spoken numbers to digits for
  arithmetic, user corrections (`VOICE_CORRECTIONS`).
- Speech-friendly replies: no links, units spelled out, long replies cut
  with "The rest is on screen."
- `Voice` component with the `microphone` permission; the microphone is
  open only while listening and closed while AURORA speaks. Audio is
  never saved; transcripts are never logged.
- Voice settings; `vosk`, `sounddevice`, `pyttsx3` in Requirements.txt.
- Tests: fakes for the session logic, plus real-speech tests that have
  Windows read phrases into Vosk (skipped where unavailable).
- Tests can no longer open network connections (`no_network` fixture).
- 368 tests (1 skipped on Windows).

## 0.11.1 — Personal settings (2026-11-05)

### Added
- `aurora/config/local_settings.py` (ignored by git) overrides `settings.py`,
  for personal details such as `RESEARCH_CONTACT`. Wikipedia lookups are
  now enabled on this machine.

## 0.11.0 — Milestone 7: Knowledge and research (2026-11-05)

### Added
- Wikipedia tool: encyclopedia summaries with the article link
  ("tell me about black holes", "who is Stephen Hawking?",
  "what is a neutron star?"). Needs `RESEARCH_CONTACT` (Wikimedia refuses
  requests without contact details).
- arXiv tool: up to 5 papers with authors, dates and links; newest first
  for "recent / latest" ("Find recent discoveries about exoplanets").
- Documents tool: list, search and read .txt, .md and .pdf files in
  `Documents/` only, with file and PDF page citations.
- New intents `knowledge`, `papers`, `documents` and routing rules that
  don't take over calculator, weather, memory or small-talk questions.
- `aurora/tools/untrusted.py`: cleaning and shortening of outside text.
- Settings: `RESEARCH_CONTACT`, `RESEARCH_TIMEOUT_SECONDS`,
  `RESEARCH_MAX_CHARS`, `ARXIV_MAX_RESULTS`, `DOCUMENTS_DIR`,
  `DOCUMENT_MAX_BYTES`, `DOCUMENT_READ_CHARS`; grants for the new tools.
- `pypdf` dependency. 339 tests (1 skipped on Windows: symlinks need admin).

### Changed
- Tool results given to the Brain are labelled as data, never instructions.
- `Documents/` is in `.gitignore`.

### Fixed
- `ToolManager.execute()` crashed when a tool argument was called `name`.

## 0.10.0 — Milestone 6: Planning engine (2026-11-05)

### Added
- `Planner` and `Plan`/`Step` (`aurora/planning/`): a message can hold
  several requests. "weather in Pune and Delhi, then calculate 12 * 7"
  runs three steps.
- Tool results from a plan are given to the Brain for the conversational
  parts ("weather in Pune tomorrow and what should I wear?").
- Weather requests for several places, and follow-ups that inherit the
  place and day ("… and should I carry an umbrella?").
- Deletions are never combined: the other steps run and AURORA asks for
  the deletion separately.
- Step limit (`PLAN_MAX_STEPS`, 5) with a message when reached.
- `plan <message>` terminal command; `planning.*` events.
- 284 tests.

### Changed
- The router only sets a weather `when` if the user says one (the
  Weather tool still treats a missing `when` as "now").

## 0.9.1 — Log privacy (2026-11-05)

### Fixed
- `Logs/aurora.log` recorded user content in plain text: every message
  (router debug line), tool arguments such as notes and memory queries,
  remembered values, and weather place names. Logs now contain only
  metadata: intent, confidence, target, argument names, attribute names,
  memory ids and error types.

### Added
- `Tests/test_log_privacy.py`: sends messages through every path
  (conversation, memory, forget + confirm, calculator, weather success
  and failure) and fails if any user or model text reaches the log.

## 0.9.0 — Milestone 5: Intelligent memory (2026-11-05)

### Added
- Automatic memory for ordinary conversation (`aurora/memory/auto.py`):
  facts are extracted, evaluated, stored with conflict resolution, and
  each save or update is announced in the reply.
- `FactExtractor`: names, location, origin, birthday, likes, dislikes,
  favourites, study, institution, occupation, employer and relationships,
  with multi-clause messages and lists. Ignores questions, hypotheticals,
  reported speech, and every message mentioning sensitive data.
- `MemoryEvaluator`: confidence threshold, vague/negated/overlong values,
  priority levels from MEMORY_DESIGN.md.
- `MemoryManager.learn()`: duplicates, single-valued attributes
  (a new name replaces the old one), likes vs dislikes.
- Relevant memories are given to the Brain as a system message (not
  saved in the conversation); `last_used` / `times_used` are tracked.
- Recall stems words, so "where do I live?" finds "Lives in: Pune".
- Router: "where do I live?", "what do I study?", "who am I?" etc. go to Recall.
- `AutoMemory` permission component; `memory.learned` event.
- Settings: `MEMORY_MIN_CONFIDENCE`, `MEMORY_CONTEXT_LIMIT`,
  `MEMORY_ANNOUNCE`, `MODEL_TIMEOUT_SECONDS`, `MODEL_MAX_REPLY_TOKENS`.
- 251 tests.

### Changed
- "remember that my name is Harry" stores a structured fact (so it can
  be updated later) instead of a free-text note when the statement is
  recognised.
- Recall replies start with "Here's what I remember:".
- `Docs/MEMORY_DESIGN.md` rewritten to describe the implemented system.

### Fixed
- A stalled or endlessly generating model could freeze AURORA. Ollama
  calls now time out (120 s) and replies are capped (400 tokens), with
  a clear message instead of a hang.

### Removed
- `aurora/knowledge/extractor.py` (replaced by `aurora/memory/extractor.py`).

## 0.8.0 — Milestone 4: Tool execution (2026-11-04)

### Added
- Route decisions are now acted on: tool intents run the matching tool
  and the tool's reply is returned without involving the language model.
- `ToolExecutor` (intent to tool) and `ToolResult` (success, message,
  data, optional confirmation).
- Memory tools: Remember, Recall (keyword matching with "related"
  results), Forget (asks for confirmation before deleting).
- Weather tool using Open-Meteo (no API key): current conditions, today,
  tomorrow, this week, this weekend. Reports failures instead of guessing.
- Confirmation flow for consequential actions: yes runs it, no cancels,
  anything else cancels and is handled normally; `clear` cancels too.
- Default least-privilege grants for the built-in tools; `status` shows
  each tool as Ready or Blocked.
- `DEFAULT_LOCATION` and `WEATHER_TIMEOUT_SECONDS` settings.
- Events: `tool.executed`, `tool.failed`, `tool.confirmation_requested`,
  `tool.confirmation_answered`; `permission.denied` is now published.
- Tests use an isolated database automatically. 176 tests.

### Changed
- `BaseTool.execute(**arguments)` returns a `ToolResult`. The calculator
  replies like `12 * (7 + 3) = 120`.
- `MemoryStore` resolves its database path when created, not at import.
- Terminal output is forced to UTF-8 so ° and – display correctly.
- `httpx` added to `Requirements.txt` (already installed as an Ollama
  dependency).

### Removed
- `WeatherPlugin` placeholder (replaced by the Weather tool).

## 0.7.0 — Milestone 3: RouteDecision (2026-11-04)

### Added
- `RouteDecision`: intent, confidence, target, read-only arguments,
  reason and ranked alternatives.
- New intents: `remember`, `recall`, `forget`, `clarify` (replacing the
  vague `memory`, `reasoning`, `unknown`).
- Router extracts arguments: calculator expression (including "plus",
  "times", "x", "^"), weather location and time, memory content/query.
- Clarification when two tool candidates are nearly tied.
- Engine routes every message and publishes `routing.decided`;
  `route <message>` command in the terminal.
- Routing thresholds in `settings.py`.
- 56 routing tests.

### Fixed
- Messages containing `-` or `/` (hyphenated words, URLs, dates, phone
  numbers) no longer route to the calculator.
- "I can't remember…" and statements like "my favourite colour is blue"
  are no longer treated as memory commands.
- Indoor/device temperatures and non-questions ("Purple Rain is my
  favourite song") are not routed to weather.

### Not changed
- The Brain still answers every message; decisions are observed only
  until Milestone 4.


## 0.6.0 — Milestone 2: Complete kernel (2026-11-04)

### Added
- `PermissionManager`: deny-by-default, per-component permissions for
  memory, filesystem, network, camera, microphone, sensors, health data
  and hardware control. Grants come from `settings.PERMISSION_GRANTS`.
- Plugins and tools declare `required_permissions`. Plugins missing a
  grant are blocked; tools missing a grant raise `PermissionDeniedError`.
- `KernelContext`: plugins receive the event bus and permissions (not
  the whole engine) and can `publish()` events and `require()` permissions.
- Kernel publishes lifecycle, service, plugin and conversation events
  (`aurora/events/topics.py`).
- `status` command in the terminal; `engine.status()`.
- `engine.register_plugin()`.
- Provider health check: the Brain service reports `Error` if Ollama is
  unreachable or the model isn't installed.

### Changed
- `EventBus` isolates failing subscribers, validates events, supports
  `"*"` wildcard subscriptions and `unsubscribe()`.
- `Event` gains `source` and is immutable.
- Engine `start()` / `shutdown()` are idempotent; plugins shut down in
  reverse order.
- `ToolManager` rejects duplicate tool names.


## 0.5.0 — Milestone 1: Architecture cleanup (2026-11-04)

### Changed
- `BrainService` is now the single owner of the `Brain`; the engine no
  longer creates a second one.
- `ServiceManager` and `PluginManager` isolate failures: a broken
  service is marked `ERROR`, a broken plugin is disabled, and the rest
  keep running. Duplicate names are rejected.
- Model name, version and file paths come from `settings.py`. Paths are
  absolute, so starting AURORA from another folder no longer creates a
  new empty database.
- Diagnostics use Python's `logging` module and go to `Logs/aurora.log`.
  The engine no longer prints; `main.py` owns terminal output.
- `main.py` shuts down cleanly on Ctrl+C / end of input.
- `Brain`, `AuroraEngine`, `MemoryManager` accept injected dependencies.

### Security
- The calculator no longer uses `eval()`. It parses expressions with
  `ast` and accepts only numbers and arithmetic operators, with limits
  on length, exponent and result size.

### Removed
- `kernel/kernel.py` (old duplicate of `AuroraEngine`).
- `kernel/plugins/test_plugins.py` (broken import) and
  `plugins/test_plugins.py` (manual script).
- `memory/models.py` (unused duplicate of `Fact`), `conversation/message.py`
  (unused), `utils/logger.py` (replaced by `logging`).
- Empty placeholders `memory/evaluator.py` and `pipeline/chat_pipeline.py`.

### Added
- pytest suite in `Tests/` (45 tests) using a fake model provider and a
  temporary database.
- `Requirements.txt`, `pyproject.toml`, `README.md`, `Docs/ARCHITECTURE.md`.
- Git repository.
