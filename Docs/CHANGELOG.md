# Changelog

## 0.43.0 — Wake Engine (2026-11-13)

### Added
- **Wake Engine** (`aurora/wake/`, Settings → Wake Engine, off until you
  switch it on): hold Right Ctrl (or Space) and say "Wake up"; AURORA
  starts or comes forward, says "I'm awake. How can I help you?" and
  listens. A small helper watches for the key (measured: 28 MB, 0 % CPU
  while idle); the microphone opens only while the key is held, with a
  beep and an on-screen panel, and closes when the phrase is heard, the
  key is released or another key is pressed.
- The wake phrase can be changed; sound-alikes are recognised separately,
  which removed the false activations measured with synthetic voices.
- The Command Centre accepts `--wake` and a "wake" message from another
  launch; the helper can start with Windows (your choice).

### Changed
- Kokoro and the voice comparison samples were removed; Piper stays.

## 0.42.0 — Natural voice: Piper (2026-11-13)

### Added
- **Piper**, a natural-sounding neural voice that runs on this PC, is now
  AURORA's default voice (Settings → Voice → Voice engine). Measured here:
  about 5 times faster than real time, the first sentence ready in about
  0.4 s, ~255 MB of memory while speaking. The next sentence is made while
  one plays; Stop and barge-in cut it off within about 0.1 s.
- If Piper or its voice file is missing, AURORA says so and uses the
  Windows voice instead.
- The voice is loaded in the background at start-up when spoken replies
  are on, so the first spoken reply doesn't wait.

### Notes
- Piper (piper-tts 1.8.0) and its pronunciation library espeak-ng are
  GPL-3.0; the voice (LJSpeech) is public-domain data. Kokoro-82M was
  also measured and was too slow on this laptop (about 2 times slower
  than real time).

## 0.41.0 — Voice Engine: providers, words while you speak, pitch (2026-11-13)

### Added
- **Speech providers** (`aurora/voice/providers.py`): one interface for
  speech recognisers and voices. Vosk and Windows SAPI are the first two;
  the Command Centre and the terminal ask for "the recogniser" and "the
  voice" from settings (`VOICE_RECOGNISER`, `VOICE_SYNTHESISER`). A
  provider that works outside this PC is refused unless
  `VOICE_ALLOW_EXTERNAL` is on (off; none is built in).
- **Words while you speak**: Chat and the Dashboard show what AURORA has
  heard so far while you're talking.
- **Pitch** for the Windows voices (Settings → Voice, -10 to 10).
- Blueprint for the Voice Engine, Wake Engine, Mobile Companion and
  Calling Agent (Docs/VOICE_WAKE_MOBILE.md): what exists, what's missing,
  what this laptop can run, open decisions and the order of work.

## 0.40.0 — Performance mode, voice waveform, Connections (2026-11-12)

### Added
- **Performance mode** (Settings → Interface): lighter graphics while the
  PC is busy. The AI core is still while Ready and runs at 5 frames a
  second while busy; the waveform redraws at most 5 times a second; pages
  don't fade. Automatic (default) after 6 seconds of CPU above 85 % or
  RAM above 92 %, off again after 6 seconds below 65 % / 88 %; or Always
  on / Off. The Dashboard says when it's on and why.
- **Voice waveform** under the AI core: real microphone loudness while
  listening, a moving pattern while speaking, still otherwise.
- **Connections** on the Dashboard: internet access and the services
  allowed this session, the Browser's open tabs, research projects and
  the latest session.

### Changed
- The Dashboard's Coming up and Recent activity are two tabs of one
  panel, so everything fits a 1366 × 768 screen.

## 0.39.0 — Browser and Documents redesign (2026-11-12)

The Dashboard is unchanged. See the Browser and Documents rows in
Docs/COMMAND_CENTRE.md.

### Added
- **Documents page**, laid out like Windows File Explorer: Home (favourites,
  recent, places), Recent, Favourites and the places (AURORA Documents,
  your Windows Documents, Pictures, Videos, Music, Downloads, Desktop,
  OneDrive); toolbar (new folder, new document, cut, copy, paste, rename,
  delete, sort, view); breadcrumbs; Details, Large icons and List views;
  preview panel (pictures, start of text and PDFs, details, Open, Open
  with…, Show in Explorer, Favourite, Summarise with AURORA); context
  menus and keyboard shortcuts.
- **Smart search**: find documents by what they say (new Knowledge action
  `find`, so the Knowledge permission applies).
- File operations only inside the places, never overwriting (" (2)"),
  refusing names Windows can't use, deleting to the Recycle Bin
  (`aurora/explorer/`). AURORA still reads contents only in its own
  Documents folder.
- **Browser**: home, padlock (HTTPS / not secure / local, click for the
  sites contacted), zoom, downloads list, reading list, saved pages, full
  screen, pinned and duplicated tabs, close other tabs, restore closed
  tabs, search open tabs, reader mode in the page area.
- **AURORA panel** in the browser (hidden until opened): key sentences,
  important information (published date, author, dates, figures,
  sections, tables, links), compare open tabs, explain the selection in
  Chat.
- 28 new tests (Tests/test_documents.py, Tests/test_browser.py).

### Changed
- The browser's side panel became a left sidebar and a right AURORA
  panel; the sites list moved behind the padlock.
- Knowledge Centre keeps Research projects and Notes; its "Ask your
  documents" and "Documents" tabs are gone (the Documents page has the
  files, Smart search and summaries; Chat answers questions about your
  documents with quoted sources).
- Ctrl+1 … Ctrl+9 now include Documents (Training moved out of the nine).

### Fixed
- Renaming a file to the same name in different letter case did nothing
  (Windows paths compare equal ignoring case).

### Not done
- Tab previews and tab groups; drag and drop in Documents; page
  translation; cloud storage beyond the OneDrive folder.

## 0.38.0 — Command Centre design: AI core, top bar, new dashboard (2026-11-12)

See "Layout" in Docs/COMMAND_CENTRE.md.

### Added
- **The AI core**: an orb on the Dashboard that shows what AURORA is
  doing: ready (slow breathing), thinking (turning rings with linked
  nodes), responding and speaking (particles flowing out), listening
  (rings expanding with the voice's loudness), starting (progress arc),
  error and paused (still). Driven by a new `Backend.ai_state` that
  follows real events (message sent, first reply text, reply finished or
  failed, microphone open, speaking, paused, failed).
- **Top bar** on every page: AURORA's state, model, internet access,
  CPU/RAM, time; each opens the page behind it.
- **Round gauges** for CPU, RAM, AURORA's own memory and tools ready.
- **Coming up**: the next reminders and scheduled tasks on the Dashboard.
- **Ask bar** on the Dashboard: type, speak or choose a picture; the
  answer appears in Chat.
- Ctrl+1 … Ctrl+9 open the first nine pages.
- 10 new tests (Tests/test_command_centre_ui.py).

### Changed
- Dashboard redesigned (core in the centre, status and activity on the
  right, ask bar at the bottom); services are listed in the AI status
  card and its tooltip. The quick-access buttons are gone (the sidebar
  and Ctrl+number do it).
- Panels have a faint top-lit gradient and a lit top edge ("glass"
  without blur).
- The status bar is hidden; the top bar shows the same state.
- `settings.VERSION` is 0.38.0 (it had stayed at 0.33.0).

### Not done
- Light theme (many pages still use fixed dark colours), QML/Qt Quick
  (kept QWidgets: lower memory on 4 GB), 3D space visualisation, live
  resource graphs on the Dashboard (the System monitor has the numbers),
  a network diagram of services, a custom SVG icon set (emoji icons stay).

## 0.37.0 — AURORA Browser, first milestone (2026-11-11)

See "AURORA Browser" in Docs/BROWSER_AUDIO_VIDEO.md.

### Added
- **Browser page**: AURORA's own browser (Qt WebEngine, Chromium 140).
  Tabs (up to 6), address bar that opens addresses or searches
  (DuckDuckGo by default), suggestions from history and bookmarks,
  back/forward/reload, find in page, PDF viewer, bookmarks, and downloads
  that are asked for first and saved where you choose.
- **Ask about the page**: answers quoted from the open page's own
  sentences, with the page as the source; says so when the page doesn't
  answer.
- **Reader mode**: the article without menus, adverts, cookie banners,
  share buttons or hidden text.
- **Save to Knowledge**: the page's text as Markdown in
  `Documents/Web pages/`, with its address and date, so document
  questions can quote and cite it. Files are never overwritten.
- **History and bookmarks** on this PC only (`aurora_browser.db`): search,
  delete, clear, switch off; history older than 90 days is deleted.
- **Sites contacted**: every site the open page loaded something from,
  with other sites and blocked ones marked.
- 47 new tests, using a fake web engine (no Chromium, no network).

### Changed
- The network gate checks every browser request (internet on / ask /
  off, local network). New `gate.decide()` and `gate.record()`; services
  marked `any_host` (only the Browser) may contact any host.
- Sites can't use the camera, microphone, location or notifications.
- New requirements: `PySide6-Addons` and `PySide6-WebEngine` (6.12.0).
- Tests can't start Chromium (conftest), like the camera and microphone.

## 0.36.1 — Blueprint: Browser, Audio Lab, Video Studio (2026-11-11)

Documentation only; nothing new is built yet.

- Docs/BROWSER_AUDIO_VIDEO.md: requirements for three planned subsystems
  (AURORA Browser, Audio Lab, Video Studio), their priority order, what
  each builds on, what this laptop can and can't run, the privacy and
  consent rules, and the first milestone (basic browsing).
- README: Highlights section and MIT license.

## 0.36.0 — Universal News Intelligence (2026-11-10)

See Docs/NEWS.md.

### Added
- **News, on request only**, about any topic, place and dates, from four
  sources in parallel: GDELT (worldwide index, no key), Google News search
  (RSS), ten publisher feeds (searched on this PC, so publishers never see
  the search) and, with a key, Brave web news search.
- **Checks on every story**: publisher, publication and event dates,
  duplicates and agency copies, independent publishers, official and
  primary sources, allegations, corrections, conflicting numbers. Crime
  reports show the legal stage (investigation, allegation, arrest, charge,
  trial, conviction, acquittal), never present guilt before a conviction,
  and leave out graphic details.
- **Comparing publishers** and **Investigation Mode** (earliest report,
  timeline, official statements, legal process, corrections,
  disagreements).
- **News Centre** page: search, filters (category, place, dates, publishers),
  article cards with checks and links, saving into folders.
- Every report says what was searched, what wasn't and why, and that it
  isn't every report published; empty results are never filled in.
- 63 new tests, none using the network.

### Changed
- News requests on a schedule ("every morning show me the news", "monitor
  …") are refused with an explanation; routines with a news search can't
  be scheduled.

## 0.35.0 — Network gate (2026-11-09)

Deployment plan, stage 8.

### Added
- **One gate for every network request** (`aurora/network/gate.py`). Each
  service may contact only its own hosts (Weather → Open-Meteo, Wikipedia →
  en.wikipedia.org, arXiv → export.arxiv.org, sensors and devices → the
  address you configured); other hosts and redirects elsewhere are blocked.
- **Internet access: ask (default), on or off.** In "ask", the first
  request from each service in a session waits while AURORA asks in the
  chat, saying where it would go and what it would send.
- **Local network switch** for the ESP32 sensor hub and device controller.
- **Settings → Internet and network**, and **System → Network activity**:
  every request made or blocked this session, with time, service, host and
  outcome, never what you asked about.
- 25 new tests, including one that fails if any module creates an HTTP
  client outside the gate.

### Changed
- Weather, Wikipedia and arXiv now ask before their first request each
  session. Set Internet access to "Allowed without asking" for the old
  behaviour.

## 0.34.0 — Model interface (2026-11-09)

Deployment plan, stage 6.

### Added
- **AURORA's own models as the assistant's model.** The Brain now talks to
  `ModelSwitch`, which serves Ollama models or, for names like
  `aurora:aurora_t0_lr1e-2`, AURORA's own checkpoint through the Core
  Runtime, with no Ollama or network. AURORA's checkpoints are listed in
  Settings → Model. Loaded when first needed, unloaded on pause or when
  switching; replies stream and can be stopped. Ollama stays the default.
- Status displays (dashboard, status bar, System page, Model manager) say
  when AURORA's own model is in use instead of reporting Ollama.
- 19 new tests, including one where Ollama is unreachable and a real
  checkpoint answers.

### Changed
- `ModelUnavailableError` moved to `aurora/providers/base_provider.py`
  (still importable from `ollama_provider`).

## 0.33.1 — Learning-rate follow-up (2026-11-09)

### Added
- **Experiment architecture_lr1e-2** (20 runs): architecture_v1 repeated at
  learning rate 1e-2 → 1e-3, specification committed before running. The
  gated linear recurrence still beat attention, by 0.051 nats [95 % CI
  0.022–0.080] on all 5 seeds, confirmed on test; RMSNorm and SwiGLU again
  showed no detectable difference. The written hypothesis held. Two runs
  re-trained from scratch gave identical weights.

## 0.33.0 — Research platform (2026-11-09)

Blueprint Phase 16. See Docs/RESEARCH.md and Docs/research/CLAIMS.md.

### Added
- **Research variants of AURORA-T0** (`aurora_core/research/arch.py`):
  RMSNorm, SwiGLU, and an attention-free gated linear recurrence, each a
  single change, parameter-matched (within 0.3 %), with gradient-checked
  backward passes. The default reproduces T0 exactly.
- **Reproducible experiments** (`python -m aurora_core.research`): a
  specification written before running (question, hypothesis, data
  SHA-256, seeds, decision rule); paired runs over seeds with 95 %
  confidence intervals; validation decides, test confirms; data, code and
  weight fingerprints; resumable runs that never mix code versions;
  bit-for-bit `verify`; report and claims register.
- **First experiment, architecture_v1** (20 runs): the gated linear
  recurrence beat attention by 0.077 nats [95 % CI 0.053–0.102] on all 5
  seeds, confirmed on test, contrary to the written hypothesis; RMSNorm and
  SwiGLU showed no detectable difference. Three runs re-trained from
  scratch gave identical weights; T0 seed 0 matched Phase 5's result to
  every digit.
- A test that no variant can see future tokens.
- RMSNorm, SiLU and sigmoid operations.
- 37 new tests.

### Fixed
- A flaky precision test (random sampling could stop before 5 tokens).
- Reports for experiments with a single finished seed crashed.

## 0.32.0 — Automation and device control (2026-11-08)

Blueprint Phase 15. See "Devices and automation" in Docs/ARCHITECTURE.md.

### Added
- **Action catalogue** (`aurora/automation/actions.py`): every action
  with its permission, whether it asks first, how it is undone and
  whether a schedule may run it; shown on the Automation page.
- **Audit trail** (`aurora_automation.db`): every action asked,
  declined, cancelled, done, failed, undone or skipped, by you or a
  schedule; 90 days.
- **Undo** ("undo that"): device changes, opened programs, tidy-ups,
  reminders and scheduled tasks; asks first and is audited.
- **Programs** ("open notepad", "close notepad"): only from `APPS`,
  fixed commands, no shell; closes only what AURORA opened.
- **Tidying folders** ("organise my documents by type / by month"):
  allowed folders only, previewed and confirmed, nothing deleted or
  overwritten, stops at the first problem or when you press Stop,
  undoable even after a restart.
- **Scheduled tasks** ("every weekday at 18:30 turn on the fan",
  "every day at 7 run my morning briefing"): approved once; reminders,
  routines, sensor reports and devices marked `"schedulable"`;
  permissions checked at every run; missed runs skipped and reported;
  paused after 3 failures; every run reported and audited.
- Cancelling a single reminder ("cancel reminder 2", "cancel the
  reminder about stretching").
- **Automation page** in the Command Centre; Windows notifications for
  reminders, missed reminders and task runs.
- Permissions `apps.launch` and `automation`; components `Apps`,
  `Files`, `Schedule`.
- 82 new tests.

### Changed
- **Reminders are kept on disk**: closing AURORA no longer loses them;
  missed ones are reported at the next start.
- Requests and scheduled tasks share one engine lock.

### Fixed
- Reminders were never shown in the Command Centre (only the terminal
  displayed them).

## 0.31.0 — Sensor integration (2026-11-08)

Blueprint Phase 14. See "Sensor Hub" in Docs/ARCHITECTURE.md.

### Added
- **New quantities**: ambient light, CO₂, estimated CO₂ and VOCs, PM2.5
  and PM10; other simple sensors can be declared in
  `SENSOR_EXTRA_QUANTITIES`.
- **Timestamps**: each reading carries when it was measured (devices may
  send `"ages"`); values older than 5 minutes are refused. A device's
  `"last_motion_s"` is kept in history.
- **Permitted readings**: `"quantities"` in a `SENSOR_SOURCES` entry.
- **Interpretation** (`aurora/sensors/interpret.py`): comfort and air
  bands with their references, trends from history, 24-hour summaries,
  window advice. Health readings are never interpreted.
- **Sensor Service** (`aurora/sensors/service.py`): optional background
  watching (off by default), `sensor.readings` / `sensor.alert` /
  `sensor.monitoring` events, alerts with a cooldown.
- New questions: air quality, comfort, "should I open a window?", light,
  CO₂ / VOCs / dust, "how has the temperature changed today?", "when was
  the last motion?".
- **Sensor Centre**: eight cards with meanings, a 24-hour history chart,
  the background-watching switch, alerts. **Dashboard**: room card.
  Windows notifications for alerts; terminal shows them too.
- Preferences `sensor_monitoring`, `sensor_notifications`.
- **Firmware**: BH1750 (light) and SGP30 (VOCs, estimated CO₂, with
  humidity compensation), ages and last-motion time. Not yet tested on
  hardware; its JSON format is checked by a test, and a virtual device on
  127.0.0.1 was read end to end.
- 110 new tests.

### Changed
- Sensor answers include the measuring time and what the value means.
- With several devices, the most recently measured value is used (was:
  the first device's).
- The engine status lists a "Sensors" service.

### Fixed
- Test runs that create Command Centre pages could crash Python on exit
  (an access violation after every test had passed, about one run in
  six). The pages are now deleted while Qt is still running.

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
