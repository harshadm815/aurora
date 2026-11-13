# AURORA Command Centre

The desktop interface for AURORA, built with PySide6 (Qt 6).

```
.venv\Scripts\python.exe command_centre.py
```

(or `python -m aurora.interface`). The terminal interface, `main.py`,
still works and shares the same memory, settings and preferences.

## Windows integration

| Feature | How |
|---|---|
| Icon | `aurora/interface/assets/aurora.ico`, 9 sizes (16–256 px), drawn from the emblem (`python -m aurora.interface.windows.icon` regenerates it). Used for the window, taskbar, tray, shortcut and .exe |
| Own taskbar button | The process sets the AppUserModelID `Aurora.CommandCentre`, so Windows shows AURORA's icon instead of Python's |
| Pin to taskbar | Right-click AURORA's taskbar button → **Pin to taskbar**. (Windows doesn't let apps pin themselves.) The shortcut carries the same app ID, so pinned and running buttons are one app |
| Desktop shortcut | Settings → Windows → **Create desktop shortcut** (`AURORA Command Centre.lnk`, uses the .exe when packaged, otherwise `pythonw command_centre.py`) |
| Notification area | Tray icon with **Open AURORA · Start AURORA · Pause AURORA · Settings · Exit**; click to open; tooltip shows the state |
| Minimise / close to tray | Minimising hides the window to the tray (on by default); closing can too (off by default). Settings → Windows |
| Start with Windows | Settings → Windows. Adds a value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (your user only, no admin) that starts AURORA minimised to the tray. Off until you tick it |
| One instance | Launching again (shortcut, startup, double-click) just brings the running AURORA to the front: no second process, no duplicate workers (a per-user local socket) |

**Pause / Start.** Pause stops AURORA's services and plugins and asks
Ollama to unload the model, freeing its memory (≈ 0.7 GB for TinyLlama).
The window and tray stay; Start brings everything back.

**Exit.** Exit from the tray (or closing the window when "close to tray"
is off) turns the camera off, cancels reminders, shuts the engine down
(services and plugins), waits for both worker pools to finish, removes
the tray icon and releases the single-instance lock.

## The .exe

```
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe packaging\build_windows.py
```

produces `dist\AURORA Command Centre\AURORA Command Centre.exe` (a
one-folder build: start-up is fast and nothing is unpacked to a temporary
folder). The executable contains the code, Qt, OpenCV and the OCR models,
but **not your data**: `aurora_home.txt` next to the .exe points to the
project folder, where the memory database, models, logs, Documents and
`local_settings.py` stay. `local_settings.py` (with your contact email) is
deliberately left out of the build, and the build script checks that.
AURORA's own neural network (`aurora_core`) is bundled: the Model
manager runs checkpoints with it, and the Training monitor starts
training as `AURORA Command Centre.exe --train …` (no window opens).
Voice is bundled too (Vosk, sounddevice, Windows SAPI); the speech
model stays in `Models/`. `AURORA Command Centre.exe --voice-check` runs
the voice loop-back test without a window, and `--vision-check` the
vision self-check.

## Layout

```
┌──────────┬───────────────────────────────────────────────────────────────┐
│          │ PAGE TITLE    ● AURORA · Ready  ● model  ● Internet  ● CPU/RAM │  top bar
│ sidebar  ├───────────────────────────────────────────────────────────────┤
│ (pages,  │                                                               │
│ Ctrl+1-9)│   the page                                                    │
│          │                                                               │
└──────────┴───────────────────────────────────────────────────────────────┘
```

**Top bar** (every page): what AURORA is doing, the language model and
whether it's reachable, internet access (on / asks first / off, how many
services are allowed this session, and the local-network switch in the
tooltip), CPU and RAM, and the time. Each item opens the page that
explains or changes it.

**The AI core** (Dashboard) shows `Backend.ai_state`, which follows real
events:

| State | When | Looks like |
|---|---|---|
| Ready | running, nothing happening | the core breathes slowly (10 frames/s, only the core redrawn) |
| Thinking | a message was sent, no reply text yet | the three rings turn, with linked nodes |
| Responding | the reply is being written | particles flow out from the core |
| Speaking | a reply is spoken aloud | as Responding |
| Listening | the microphone is open | rings expand with your voice's loudness |
| Starting | the kernel is starting | a progress arc sweeps |
| Something went wrong | a reply failed (5 seconds), or AURORA couldn't start | red core with a warning mark, still |
| Paused | paused or stopped | grey and still |

Drawn with QPainter only (no images, blur, particle systems or OpenGL),
15 frames a second while busy, and only while visible; with
**Animations** off in Settings each state is a still picture. The glow and
the still rings are drawn once and reused. Measured on this laptop's
i3-8130U (420 px orb): Ready about 1-3 % of one core, Listening about
14 %, Thinking and Responding about 25-30 % of one core (6-8 % of the
whole processor) for as long as the reply takes.

**Voice waveform** (under the core): while listening, bars follow the
microphone's real loudness; while speaking, a moving pattern shows *that*
AURORA is speaking (Windows' speech engine doesn't report loudness, so it
isn't a measurement). Otherwise it's a flat line and isn't redrawn.

**Performance mode** (Settings → Interface, `aurora/interface/performance.py`)
keeps the layout and makes the graphics cheaper: the core is still while
Ready and runs at 5 frames a second while busy, the waveform redraws at
most 5 times a second, and pages don't fade in. *Automatic* (the default)
turns it on after 6 seconds of CPU above 85 % or RAM above 92 %, and off
after 6 seconds below 65 % and 88 %, so it doesn't flicker; *Always on*
and *Off* are the other choices. The Dashboard says when it's on and why.
The browser engine (Qt WebEngine) starts only when the Browser page is
first opened, so it uses no memory until then.

**Look**: deep navy, one accent (cyan, electric blue or violet), panels
with a faint top-lit gradient and a hairline edge (glass without blur,
which is expensive), round gauges that turn amber above 75 % and red above
90 % (tools: amber when any is unavailable).

**Keyboard**: Ctrl+1 … Ctrl+9 open the first nine pages (Dashboard, Chat,
Memory, Knowledge, Documents, News, Browser, Tools, Models).
Browser: Ctrl+T new tab, Ctrl+W close, Ctrl+Shift+T restore, Ctrl+Shift+A
search tabs, Ctrl+L address, Ctrl+D bookmark, Ctrl+F find, Ctrl+B sidebar,
Ctrl+J AURORA panel, Ctrl+= / Ctrl+- / Ctrl+0 zoom, F5 reload, F11 full
screen, Alt+Left / Alt+Right back and forward.
Documents: Enter open, F2 rename, Del delete, Ctrl+C / X / V, Ctrl+Shift+N
new folder, Backspace or Alt+Up up, Ctrl+F search, F5 refresh.

## Pages

| Page | What it shows | Where the data comes from |
|---|---|---|
| Dashboard | Greeting and date; **the AI core** (what AURORA is doing now, in motion and in words); round gauges for CPU, RAM, AURORA's own memory and tools ready; the voice waveform; performance mode when it's on; AI status (with the services), model, memory count and the room; **Connections** (internet access and the services allowed this session, the Browser's open tabs, research projects and the latest session; each opens its page); **Coming up** (next reminders and scheduled tasks) and recent activity, as two tabs of one panel; Pause / Start; an **ask bar** (question, microphone, picture) that answers in Chat | `Backend.ai_state`, engine status, system sampler, voice level, network gate, Browser tabs, research log, Reminders and Schedule tools, activity feed |
| Chat | Conversation with timestamps, Markdown, streaming replies, a thinking indicator, Yes/No buttons when AURORA asks for confirmation, Clear. **🖼 Image…** (describe a picture you choose, with its evidence), **🎙 Ask by voice** (one request; a level meter shows the microphone is on), **■ Stop** (stops listening or speaking), **Speak replies** | `engine.process_message()` (the same engine as the terminal); `VoiceController` |
| Memory Centre | All memories with category, priority, confidence, dates and use; search, category filter, add note, edit value, delete with confirmation | `MemoryManager` |
| Knowledge Centre | Research projects and sessions; notes ("remember that …"). Files are on the Documents page; questions about your documents are answered in Chat | Research log; MemoryManager |
| Browser | Laid out like a desktop browser. **Navigation bar**: sidebar, back, forward, reload, home, padlock (HTTPS or not secure; click for every site the page contacted), address and search, bookmark, reader mode, zoom, downloads, ✦ AURORA panel, ⋯ menu (history on/off, clear history, cookies and cache, full screen). **Tabs** (up to 6): pin, duplicate, close others, restore closed (Ctrl+Shift+T), search tabs (Ctrl+Shift+A). **Sidebar**: bookmarks, history, downloads, reading list, saved pages. **AURORA panel** (hidden until opened, Ctrl+J): ask about the page, key sentences, important information, compare open tabs, explain the selection (in Chat), save to Knowledge, read later | Qt WebEngine (started on first opening); `aurora/browser/`; network gate |
| Documents | Laid out like File Explorer. **Left**: Home, Recent, Favourites, then the places (AURORA Documents, your Windows Documents, Pictures, Videos, Music, Downloads, Desktop, OneDrive). **Toolbar**: new folder, new document, cut, copy, paste, rename, delete (Recycle Bin), sort, view (Details, Large icons, List). **Breadcrumbs** and Up; filter by name, search names, or **Smart search** by content (AURORA's Documents folder). **Preview**: pictures, the start of text and PDFs, type, size, dates, location; Open, Open with…, Show in Explorer, Favourite, Summarise with AURORA | Qt's file system model; `aurora/explorer/`; Knowledge tool (find, summarize) |
| Tools & capabilities | Every tool as Available, Disabled (with the missing permission) or Error; calculator trial | `engine.tool_status()`, `tool.failed` events |
| Model manager | Conversation model: Ollama's installed models (parameters, quantization, size, in memory), use for chat, unload. AURORA's own models: every checkpoint in `Models/` (parameters, precision, size, steps, test loss); load into the AURORA Core Runtime, try it (streamed, stoppable, tokens/s), unload | Ollama (`model_overview`), checkpoint headers (no weights loaded until you load one), `aurora_core.runtime` |
| Training monitor | Every training run: loss curves (training and validation) against the unigram and bigram baselines, test loss, perplexity; start a new AURORA-T0 run on a text in `Data/` with live progress, time left and Stop | `metrics.jsonl` / `summary.json` in `Models/`; training runs as a separate process (`command_centre.py --train …`) |
| Vision Centre | A picture you choose (Open image…, Paste, or drag a file in) or the live camera; **Describe** (grounded description with numbered boxes drawn on the picture and an evidence list), **Read text**; detector/OCR/permission status | The shared `VisionAnalyzer` (`backend.vision_analyzer()`) |
| Sensor Centre | Temperature, humidity, pressure, ambient light, motion, CO₂, VOCs, fine dust (and extra quantities), each LIVE / SIMULATED / LAST KNOWN / NO SENSOR with its measuring time and what it means; 24-hour history chart with lowest, highest, average and trend; **Watch my room in the background** switch; alerts; connections; health on request | Sensor Hub, Sensor Service, `sensor.readings` / `sensor.alert` events |
| System monitor | CPU, system RAM, AURORA's own memory, uptime, Ollama, active and installed models, services, plugins, tools, logs | Windows APIs, Ollama, engine, AURORA's log |
| Automation | Undo (the last reversible action and what undoing it does); scheduled tasks (pause, resume, delete) and reminders (cancel), missed reminders; tidy a folder (preview, organise with confirmation, stop, undo); the audit trail with filters; "What AURORA may do": every action, its permission and whether it is granted, whether it asks first, how it is undone | The same checked and audited path as Chat (`engine.execute_action`), audit trail, Schedule / Reminders / Files tools |
| Settings | Model, personality, streaming, memory announcements, memories given to the model, accent, start page, maximised start, animations, performance mode, voice engine (natural Piper voice or Windows voices), Windows voice, speed, volume and pitch, microphone, barge-in, Test the voice, Test the microphone | `aurora_preferences.json` |

## Honest by design

- **No invented readings.** CPU and RAM come from Windows
  (`GetSystemTimes`, `GlobalMemoryStatusEx`, `K32GetProcessMemoryInfo`
  through ctypes; no extra package). Anything that can't be read shows
  "unavailable". Simulated sensor values carry a **SIMULATED** badge; a
  fallback value is **LAST KNOWN**; unsupported sensors say **NOT
  SUPPORTED**. A tool is only "Available" if its permissions are granted,
  and a crash shows as "Error".
- **The camera never starts by itself.** "Turn camera on" asks first,
  checks the `camera` permission and shows a red **● CAMERA ON**
  indicator. Leaving the page, closing the window or choosing a picture
  turns it off. A picture you choose is checked before it is decoded,
  analysed in memory and not copied anywhere.
  Frames stay in memory; nothing is recorded or saved.
- **The microphone never opens by itself.** Only a click on Ask by voice
  or Test the microphone opens it, for one request, and only with the
  `microphone` permission. The status line and level meter show it is on.
  Audio stays in memory and transcripts aren't logged.
- **Health readings** are only fetched when you click "Show health
  readings", and need the Health permission. They are never interpreted
  and never read in the background.
- **Sensor watching is off until you switch it on.** Alerts appear in
  the Sensor Centre and as Windows notifications (switch off with
  `SENSOR_NOTIFICATIONS`); simulated values say so in every alert.
- **Reminders, missed reminders and scheduled-task runs** appear as
  Windows notifications (they used to show only in the terminal).
- **Permissions, devices and paths can't be changed from the interface.**
  They stay in `settings.py` / `local_settings.py`.

## Architecture

```
command_centre.py ── aurora/interface/app.py
                       │
                       ├── MainWindow ── sidebar + top bar + QStackedWidget of views
                       │                  (each view: BaseView with on_shown / on_hidden)
                       │
                       └── Backend (QObject)
                             ├── AuroraEngine  (the one engine; created in the background)
                             ├── engine thread pool, 1 thread: every engine call, in order
                             ├── io thread pool, 2 threads: Ollama checks, memory queries, image analysis
                             ├── Dispatcher: delivers results on the GUI thread
                             ├── VoiceController: one worker thread for Vosk and SAPI
                             ├── activity feed (event bus → metadata only)
                             ├── log view (AURORA's log → metadata only)
                             └── system sampler (every 2 s, shared by all views)
```

- Views never call the engine directly from the GUI thread for anything
  slow, so the window doesn't freeze while TinyLlama is thinking.
- Streaming: `engine.process_message(text, on_token=…)` passes the model's
  reply piece by piece (`Brain.ask_stream` → `OllamaProvider.chat_stream`).
  Tool answers arrive whole.
- Chat Markdown is rendered with HTML escaped, so text from web pages or
  files is shown as text, never rendered as HTML. Links are not opened
  automatically.

## Backend changes made for the interface

All additive; the terminal behaves as before.

- `BaseProvider.chat_stream()`, `OllamaProvider.chat_stream()` and
  `installed_models()`, `Brain.ask_stream()`.
- `AuroraEngine.process_message(message, on_token=None)`.
- The personality prompt moved to `settings.PERSONALITY_PROMPT`.
- `aurora/config/preferences.py`: preferences from the Settings page,
  type-checked, limited to a fixed list of keys, saved in
  `aurora_preferences.json` (ignored by git), applied by both interfaces.

## Tests

`Tests/test_interface.py` runs Qt offscreen (no window appears) with the
real engine and a fake model: streaming, confirmation, preferences
(including that they can't change permissions or paths), real Windows
readings, every page opening, chat round-trips, HTML escaping, memory
filtering, tool states, simulated-sensor badges, and that the camera
never starts by itself.

`Tests/test_command_centre_models.py` covers the Model manager and the
Training monitor:
- finding checkpoints, including damaged and foreign files;
- training runs with half-written metrics;
- loading, generating, stopping and unloading through the real runtime;
- pausing unloads the model;
- real training processes started, followed, stopped, and stopped at
  shutdown;
- the `--train` entry point;
- Ollama model details;
- that every tool has a description.

## Not done yet

- Hands-free voice (wake word) inside the window; use the terminal's
  `voice on`.
- Device control has no page of its own yet; use Chat ("turn on the desk
  light"). Scheduled tasks are added in Chat and managed on the Automation
  page.
- Light theme (only dark accents: cyan, blue, purple).
