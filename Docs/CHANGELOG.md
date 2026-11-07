# Changelog

## 0.30.0 — Vision system (2026-11-07)

Blueprint Phase 13. See "Vision" in Docs/ARCHITECTURE.md.

### Added
- **Image input and preprocessing** (`aurora/vision/image_input.py`):
  format checked from the file's content, size and pixel limits checked
  before decoding (decompression bombs refused), EXIF orientation
  applied, grey/palette/16-bit/transparent images normalised, large
  pictures scaled for analysis; brightness, contrast, sharpness and main
  colours measured.
- **Grounded descriptions** (`aurora/vision/scene.py`): every object
  named has a number, a confidence and a box drawn on the picture; "I
  might also see …" for uncertain objects; colours, text and quality
  warnings from measurements; an evidence list under each description.
- **Looking twice**: objects must be found again in the mirrored picture.
  On 136 hand-labelled local photos, photos without objects where
  something was wrongly named fell from 44 % to 6 %, and named objects
  that are right rose from 69 % to 87 % (objects found: 72 % → 63 %).
- **Vision Centre**: Open image…, Paste and drag-and-drop; Describe
  shows the annotated picture; choosing a picture turns the camera off.
- **Chat**: 🖼 Image… describes a picture you choose.
- "describe desk.jpg", "what's in photo.png?", "look at scan.webp" for
  pictures in `Documents/`.
- **Evaluation** (`python -m aurora.vision.evaluate`): a self-check with
  pictures made in memory (also `--vision-check` in the .exe) and a
  benchmark for any labelled folder of photos.
- Settings `VISION_CERTAIN_CONFIDENCE`, `VISION_MAX_IMAGE_MB`,
  `VISION_MAX_PIXELS`, `VISION_ANALYSIS_MAX_SIDE`.
- Pillow is now a direct requirement (it was already installed for OCR).
- 74 new tests.

### Changed
- Camera and image answers are grounded reports with evidence instead of
  a single sentence. Spoken replies leave out the evidence and numbers.
- Image sources in Chat read "your documents: desk.png (image, analysed
  on this computer)".

### Fixed
- The detector named objects in abstract pictures (e.g. "airplane" in
  Windows' grey-waves wallpaper, 62 % sure), which AURORA stated as fact.
- The packaged .exe couldn't read text in pictures: RapidOCR imports its
  parts by name from its own folder, which the build didn't include.
  Found by the new `--vision-check`.
- "Sources:" captions for documents with a location other than a page.

## 0.29.0 — Voice system (2026-11-07)

Blueprint Phase 12. See "Voice" in Docs/ARCHITECTURE.md.

### Added
- **Voice activity detection** (`aurora/voice/vad.py`): adaptive,
  energy-based, standard library only. Listening ends when you stop
  speaking and gives up after 5 s of nobody speaking.
- **Interrupting AURORA**: any key in the terminal, ■ Stop in the window,
  and optional barge-in (`VOICE_BARGE_IN`, off by default).
- **Voice in the Command Centre**: 🎙 Ask by voice, ■ Stop and Speak
  replies on the Chat page; voice, speed, volume, microphone, barge-in,
  Test the voice and Test the microphone on the Settings page. One worker
  thread (`VoiceController`) owns Vosk and the Windows voice.
- **Loop-back self-test** (`python -m aurora.voice.selftest`, `--voice-check`
  in the .exe): 0.9 % word error rate, 100 % correct commands over 20
  trials, with no microphone or speakers.
- Device names that are close to a real one get a suggestion ("desk life"
  → "Did you mean the desk light?"), always with confirmation.
- The .exe bundles voice.
- Settings `VOICE_VOLUME`, `VOICE_NO_SPEECH_SECONDS`, `VOICE_VAD_RATIO`,
  `VOICE_VAD_MIN_RMS`, `VOICE_BARGE_IN`; preferences `voice_volume`,
  `voice_barge_in`, `voice_input_device`, `speak_replies`.
- 42 new tests.

### Changed
- Text-to-speech calls Windows SAPI directly through `comtypes`
  (`SapiSpeaker`) instead of pyttsx3. pyttsx3 2.99 only spoke the first
  reply, hung saving a second file and never fired its callbacks.
- Spoken replies also drop `Sources:` blocks and `[1]` citation marks.

### Fixed
- Spoken lists of numbers ("average of four five six") were sent to
  Wikipedia; they are now read as numbers.
- Tests that saved preferences changed the global settings for the tests
  after them; every test now starts with the original settings.

## 0.28.0 — Command Centre: Model manager and Training monitor (2026-11-07)

Blueprint Phase 11. The Command Centre now has all eleven sections and
talks to both the AURORA backend and AURORA's own runtime.

### Added
- **Model manager**:
  - Ollama's installed models (parameters, quantization, size, in
    memory), choose the chat model, unload from memory.
  - Every AURORA checkpoint under `Models/`, read from its header only:
    load it into the AURORA Core Runtime, try it with streamed, stoppable
    generation and speed figures, and unload it.
- **Training monitor**:
  - every training run's loss curves against the unigram and bigram
    baselines, with test loss and perplexity;
  - start a new AURORA-T0 run on a text in `Data/`, with live progress,
    time left and Stop.
- Training runs as a separate process (`command_centre.py --train …`;
  the packaged .exe supports it too), so the window stays responsive.
  Stopping AURORA or exiting ends it.
- Pausing AURORA also unloads AURORA's own model.
- `OllamaProvider.model_overview()`; `unload()` takes a model name.
- `settings.MODELS_DIR`, `settings.DATA_DIR`.
- The .exe bundles `aurora_core`.
- Tools page descriptions for the Numbers, MemoryControl, Episodes,
  Routines and Knowledge tools (a test checks every tool has one).
- 20 new tests.

### Fixed
- `aurora_home.txt` saved with a byte-order mark (as Notepad or
  PowerShell 5 can do) made the packaged app silently use its own folder
  for data. The mark is now ignored.

## 0.27.0 — Knowledge and research system (2026-11-07)

Blueprint Phase 10. See "Knowledge and sources" in Docs/ARCHITECTURE.md.

### Added
- **Knowledge index** (`aurora/knowledge/index.py`):
  - your documents split into passages with page numbers, in SQLite
    FTS5 with Porter stemming and BM25 ranking (`knowledge_index.db`,
    not in git);
  - it updates itself, re-reading only files that changed;
  - a damaged file is reported and skipped.
- **Answers with citations**: "according to my notes, …?", "what do my
  notes say about …?", "in thesis.pdf, …?".
  - AURORA quotes the sentences that answer the question, numbers each
    one, and lists its file and page.
  - If the documents don't answer it, it says so and doesn't guess.
- **Disagreements**: when two documents give different figures for the
  same thing, the answer says so.
- **Summaries** of a document or of a topic across your documents, and
  **comparisons** of two documents, or of your documents and Wikipedia.
  All quote and cite their sources.
- **Source labels on every reply** (`aurora/knowledge/sources.py`):
  - kinds: language model (training), memory, your documents, live
    source, calculated, sensor;
  - the Command Centre shows them under each reply;
  - language-model answers to factual questions say they're unverified.
- Knowledge Centre: an "Ask your documents" tab (Ask, Summarize topic,
  Re-index, index status).
- `ToolResult.sources`; the executor fills in sources for tools that
  don't set them.
- 66 new tests.

### Changed
- "what do my notes say about X" is now answered by the Knowledge tool
  (it used to list matching passages). "search my documents for X"
  still lists passages.

## 0.26.0 — Advanced memory system (2026-11-07)

Blueprint Phase 9. See Docs/MEMORY_DESIGN.md (version 3).

### Added
- **Provenance**: every memory records how it was learned (automatic,
  explicit, edited), when, the sentence it came from, and a history of
  changes. "how do you know where I live?" answers from it.
- **Confidence tracking**: repeating a fact raises its confidence; told
  explicitly or edited, it is certain.
- **Importance scoring** to choose what the Brain sees.
- **Duplicate detection** that ignores case, articles and punctuation.
- **Retention controls**:
  - "always remember …" (pinned);
  - "remember for today / 3 days …" (expires);
  - "don't remember that" (undo and block);
  - "stop learning" / "start learning again";
  - a block list.
- **Episodic memory**: one line per completed tool task, kept 30 days,
  never message text. "what did we do today?", "when did I last check
  the weather?", "stop recording activity", "clear my activity history".
- **Procedural memory**: routines ("when I say morning briefing, …"),
  run through the normal routing and permissions; routines needing
  confirmation are refused.
- **Associative memory**: facts about people the user mentions ("my
  sister Priya lives in Delhi"), followed by recall ("where does my
  sister live?").
- Recall synonyms ("hometown" finds From).
- Memory Centre:
  - columns for about, source and kept;
  - Pin, "How do you know?" and "Delete & don't learn again" buttons;
  - switches for learning and the activity history;
  - lists of the activity history, routines and blocked memories.
- New tools: MemoryControl, Episodes, Routines (permission-checked).
- 92 new tests.

### Changed
- Memory database schema version 1. The upgrade only adds columns and
  tables and backs up the file first (`*.db.*.bak`, ignored by git).
- Memory database connections are now always closed.

### Fixed
- Recall kept "does" (stemmed to "doe") as a keyword, so "where does …"
  questions could miss.

## 0.25.0 — Workflows: structured multi-step tasks (2026-11-07)

Blueprint Phase 8. See "Workflows" in Docs/ARCHITECTURE.md.

### Added
- **Numbers tool**:
  - average, median, mode, sum, count, minimum, maximum, range,
    standard deviation;
  - "p % of x", "what percent x is of y", increase / decrease by p %.
  - It computes exactly (Python's `statistics`, no `eval`) and explains
    its working from templates, so the language model never does
    arithmetic.
- **Routing for number questions**: "average of 4, 8 and 15" and
  "sum of 3, 5 and 9" went to Wikipedia; "15% of 80" and "median of …"
  went to the language model. A stated count ("these 6 numbers") is
  checked against the numbers found.
- **Workflows** (`aurora/workflows/`): typed values passed between steps
  ("what's the weather in Pune and double the temperature", "what is
  12 * 7, then add 5 and halve the result", "… convert the temperature
  to Fahrenheit"), with a structured trace of every step.
- **Response validation**: text AURORA composes may only contain numbers
  it can account for; tool values are checked for plausibility.
- **Error recovery**:
  - temporary failures (`ToolResult.retryable`, set by the weather tool
    for timeouts, connection and server errors) are retried once;
  - steps that need a failed step's result are skipped and explained;
  - independent steps still run.
- `plan <message>` in the terminal shows the workflow if one applies.
- Event `workflow.completed` (metadata only).
- 122 new tests.

## 0.24.0 — Optimisation (2026-11-06)

Blueprint Phase 7. See the Phase 7 section of Docs/AURORA_CORE.md.

### Changed
- `TextStream` decodes each token once (incremental UTF-8 decoding)
  instead of re-decoding the whole text for every token. Before, the
  cost grew with the square of the output length; at 2,000 tokens it
  took 73 % of the time.
- The runtime uses an optimised engine by default
  (`RuntimeLimits(engine="fast")`):
  - `FastInferenceSession`: weights unpacked once, fewer and larger NumPy
    operations;
  - `FastSampler`: the same draws as `rng.choice`, with less work.

  Both produce the same tokens as the Phase 6 code, which stays
  available as `engine="reference"`.

### Added
- Lower-precision checkpoints: `save_checkpoint(..., precision="float16" | "int8")`
  (int8: symmetric, one scale per output channel). Old checkpoints still
  load.
- `python -m aurora_core.runtime.precision`: file size, loss and greedy
  agreement per precision.
- `python -m aurora_core.runtime.benchmark`: no cache, reference and fast
  engines on T0 and two larger random models, checking that the tokens
  are identical.

### Results (against the Phase 6 code, same machine, alternating runs)
- AURORA-T0, 500 tokens: 1,788–1,871 → 5,383–5,743 tokens/s (3.0×).
- AURORA-T0, 2,000 tokens: 1,039–1,055 → 5,667–5,735 tokens/s (5.4×).
- 0.9M-parameter model: 2.0× (500 tokens), 2.5–2.9× (2,000 tokens).
- Same tokens as Phase 6 in every case.
- float16 halves the weights on disk with no measurable loss change;
  int8 saves 55 % of the file for +0.0007 validation loss.

## 0.23.0 — AURORA Core Runtime (2026-11-06)

Blueprint Phase 6. See Docs/AURORA_CORE.md.

### Added
- `aurora_core.runtime`, which runs AURORA's own models with no Ollama or
  other model runner:
  - Model loader: size limit, SHA-256 fingerprint, the tokenizer is
    required, and weights are deserialised without pickle.
  - `InferenceSession` with a KV cache: the prompt is processed once,
    then one position per new token. A configurable context-window
    policy applies when the cache is full.
  - `AuroraRuntime` controller:
    - streaming text;
    - limits on model size, tokens and time per request;
    - cancellation;
    - one request at a time;
    - memory report and unload.
- `python -m aurora_core.runtime --model ... [--prompt | --interactive | --benchmark]`.
- Baseline measurements for Phase 7: 45 ms load, 126 KB memory, 1,746
  tokens/s with the cache vs 1,210 without (500 tokens, sampling).
- `TextStream` (shared by generation and the runtime).

### Fixed
- A Phase 5 test checked that structural tokens are never generated, but
  its setup didn't actually make them likely, so it couldn't fail. It now
  confirms they are the model's favourites before checking.

## 0.22.0 — aurora_core: text generation and experiments (2026-11-06)

Blueprint Phase 5. See Docs/AURORA_CORE.md and Docs/AURORA_T0_MODEL_CARD.md.

### Added
- `aurora_core.generation`:
  - Sampling: greedy, temperature, top-k, top-p.
  - Generation with a sliding context window.
  - Streaming text that never shows half a character.
  - Structural tokens can't be generated.
- `python -m aurora_core.generation --checkpoint ... --prompt ...`.
- Generated-text evaluation (`python -m aurora_core.generation.evaluate`),
  compared with the bigram baseline and the real text:
  - real-word rate;
  - distinct word pairs;
  - repeated 4-grams;
  - 6-grams copied from the training text.
- Experiment runner (`python -m aurora_core.training.experiments`):
  - 14 controlled variants: context length, width, depth, heads,
    initialisation, learning rate, clipping, weight decay, plus 2 extra
    baseline seeds.
  - Equal tokens per step for every variant.
  - Results are saved after every variant and resumed after an
    interruption.
- AURORA-T0 retrained with the better learning rate: test loss 3.354
  (perplexity 28.6). It generates text from its own weights.
- `Docs/AURORA_T0_MODEL_CARD.md`.

### Changed
- `python -m aurora_core.training` uses a peak learning rate of 1e-2 by
  default (was 3e-3).

## 0.21.0 — aurora_core: training pipeline (2026-11-06)

Blueprint Phase 4. See Docs/AURORA_CORE.md.

### Added
- Backpropagation through AURORA-T0 (`transformer/backprop.py`), checked
  against numerical gradients for every parameter of a 2-layer model.
- `aurora_core.training`:
  - Reading text, with Project Gutenberg headers removed.
  - Contiguous 90 / 5 / 5 train / validation / test splits.
  - Batches of (input, next-token target) windows.
  - Uniform, unigram and bigram baselines.
  - AdamW, global gradient-norm clipping, warm-up + cosine learning-rate
    schedule.
  - Checkpoints (.npz with the model, optimizer state, step and
    tokenizer; loaded without pickle).
  - The training loop with periodic validation; the best model is kept.
- `python -m aurora_core.training --data <file>`.
- First trained model, on *The Adventures of Sherlock Holmes* (Project
  Gutenberg, public domain): test loss 3.388 (perplexity 29.6), better
  than the bigram baseline (3.699), in 3.6 minutes on the laptop.
- `Data/` (training text) is ignored by git. The model is in
  `Models/aurora_t0/`, also ignored.
- Tokenizers: `to_json()` / `Tokenizer.from_json()`.
- 32 new tests (1,389 in total).

## 0.20.0 — aurora_core: tokenizers (2026-11-06)

Blueprint Phase 3. See Docs/AURORA_CORE.md.

### Added
- `aurora_core.tokenizer`:
  - Normalisation (NFC and line endings, or none).
  - Special tokens `<pad>`, `<unk>`, `<bos>`, `<eos>` with fixed ids 0–3.
    Text can never produce them; only `add_bos` / `add_eos` add them.
  - `CharTokenizer`: one token per character; unknown characters become
    `<unk>`.
  - `ByteBPETokenizer`: byte-level BPE (GPT-2 style), trained from text.
    It has no unknown tokens and round-trips any valid text exactly.
  - Token ↔ id conversion and readable token names.
  - Saving and loading as checked JSON files (never pickle).
- `python -m aurora_core.tokenizer` trains on AURORA's docs and shows the result.
- Tokenizer tests, including 300+ random multilingual strings for the
  lossless round trip, and a reference implementation that encoding is
  checked against (745 test cases).

## 0.19.0 — aurora_core: tensor maths and AURORA-T0 (2026-11-06)

Blueprint Phases 1–2. A new package, `aurora_core/`, separate from the
assistant and not yet used by it. See Docs/AURORA_CORE.md.

### Added
- `aurora_core.tensors`: linear, softmax, log-softmax, cross-entropy
  (with `ignore_index`), GELU and layer norm, each with a backward pass;
  numerical gradient checking. Every backward pass is verified against
  central differences.
- `aurora_core.transformer`: AURORA-T0, a decoder-only Transformer in
  NumPy (1 layer, 2 heads, 32 dimensions, context 32, feed-forward 128,
  21,984 parameters, random initialisation, forward pass only).
- `python -m aurora_core` builds T0 and runs one forward pass.
- NumPy pinned in Requirements.txt (it was only installed through OpenCV).
- 68 new tests (612 in total).

## 0.18.0 — Windows desktop integration (2026-11-06)

### Added
- AURORA application icon (`aurora.ico`, 16–256 px) for the window,
  taskbar, tray, shortcut and executable.
- Own Windows app identity (AppUserModelID), so AURORA has its own
  taskbar button and can be pinned.
- Notification-area icon with Open, Start, Pause, Settings and Exit;
  minimise to tray (default on) and close to tray (default off).
- Pause / Start: stops services and plugins and unloads the model from
  RAM; Start brings it back. Also on the Dashboard.
- Settings → Windows: start with Windows (HKCU Run key, starts minimised
  to the tray, off by default) and Create desktop shortcut.
- Single instance per user: launching again brings the running AURORA to
  the front.
- Clean exit: camera off, reminders cancelled, engine shut down, worker
  pools drained, tray removed, lock released.
- PyInstaller build (`packaging/aurora.spec`, `packaging/build_windows.py`):
  one-folder `AURORA Command Centre.exe` that uses the project's data via
  `aurora_home.txt` and never contains `local_settings.py`.
- `settings.PROJECT_ROOT` resolves the data folder when packaged.
- 544 tests (1 skipped on Windows).

## 0.17.0 — AURORA Command Centre (2026-11-06)

### Added
- Desktop interface (`command_centre.py`, `aurora/interface/`, PySide6):
  Dashboard, Chat, Memory Centre, Knowledge Centre, Tools & capabilities,
  Vision Centre, Sensor Centre, System monitor and Settings, in a dark
  theme with cyan, blue or purple accents. See Docs/COMMAND_CENTRE.md.
- `Backend` bridge: one engine on one worker thread, results delivered on
  the GUI thread, activity feed and log view with metadata only.
- Real CPU / RAM / process memory readings via Windows APIs (no new
  dependency beyond PySide6).
- Streaming replies: `process_message(..., on_token=...)`,
  `Brain.ask_stream`, `OllamaProvider.chat_stream`.
- Preferences saved from the Settings page (`aurora_preferences.json`,
  ignored by git), applied by both interfaces; limited to a fixed,
  type-checked list of keys.
- `settings.PERSONALITY_PROMPT`.
- 530 tests (1 skipped on Windows), including offscreen interface tests.

## 0.16.0 — Milestone 12: Multi-agent intelligence (2026-11-05)

### Added
- Agent framework (`aurora/agents/base.py`): agents with one job each, a
  shared blackboard, and a coordinator that shows progress and survives a
  failing agent. Agents use tools through the ToolManager, so permissions
  still apply.
- Research team: memory, documents, research, analyst and reviewer agents.
  "help me continue my research on <topic>" produces a sourced report:
  your notes and open questions, Wikipedia background, newest matching
  arXiv papers marked [NEW] since your last session, papers related to
  your notes, concrete next steps, and a review of which sources answered.
- Research log: sessions are saved only after you say yes;
  "list my research projects".
- `research` intent, `Research` permission component.
- 505 tests (1 skipped on Windows).

### Found in a live run, fixed
- Notes were only used where a line contained the topic's words; a note
  that mentions the topic is now used whole.
- Wikipedia's closest article can be off-topic; it is now left out with
  a note instead of shown as background.
- Common words ("across") and stems ("acros") were shown as matches;
  matches now ignore everyday words and are shown as written.

## 0.15.0 — Milestone 11: Automation (2026-11-05)

### Added
- Devices tool: turn on/off, toggle, set brightness or speed, status and
  list, for devices listed in `settings.DEVICES` (simulated desk light and
  fan by default; every answer says when a device is simulated).
- Safety: only configured devices and allowed actions, values checked
  against limits, a yes/no confirmation for every change, a rate limit of
  6 changes per device per minute, and an audit trail. Device commands
  are only accepted on their own, never inside a combined request, and
  never from fetched text or the model's replies (tested).
- ESP32 relay/fan controller firmware with token authentication and
  safety notes (`firmware/esp32_device_controller/`; untested on hardware).
- Reminders and timers for the current session ("remind me in 10 minutes
  to stretch", "set a timer for 3 minutes", list, cancel).
- `devices` and `reminder` intents; device and reminder settings; grant
  `Devices: hardware_control`.
- 492 tests (1 skipped on Windows).

## 0.14.0 — Milestone 10: Sensor Hub (2026-11-05)

### Added
- Sensor Hub (`aurora/sensors/`): ESP32-over-Wi-Fi source, simulator,
  plausibility checks, fallback to recent readings, SQLite history.
- Sensors tool: room temperature, humidity, air pressure and motion
  ("what's the temperature in my room?", "check my sensors").
- Health tool: heart rate and SpO2 with an "informational only" note;
  separate `health_data` permission, separate table, 7-day retention.
  Sleep questions explain that no tracker is connected.
- Simulated values are always labelled as simulated.
- ESP32 firmware for BME280, PIR and MAX30102 with wiring and flashing
  guide (`firmware/esp32_sensor_hub/`; not yet tested on hardware).
  Wi-Fi credentials go in an ignored `secrets.h`.
- `sensors` and `health` intents and routing; sensor settings and grants.
- 433 tests (1 skipped on Windows).

### Fixed
- "what is the temperature in Pune" asked for clarification (weather vs
  Wikipedia); weather questions are no longer treated as encyclopedia topics.

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
