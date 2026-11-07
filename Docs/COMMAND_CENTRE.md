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
Voice isn't bundled yet (the window doesn't use it); use `main.py` for
voice.

## Pages

| Page | What it shows | Where the data comes from |
|---|---|---|
| Dashboard | Greeting, clock, AI status, model, memory count, CPU, RAM, services, plugins, tools, recent activity, quick access | Engine status, Ollama check, MemoryManager, Windows APIs, event bus |
| Chat | Conversation with timestamps, Markdown, streaming replies, a thinking indicator, Yes/No buttons when AURORA asks for confirmation, Clear | `engine.process_message()` (the same engine as the terminal) |
| Memory Centre | All memories with category, priority, confidence, dates and use; search, category filter, add note, edit value, delete with confirmation | `MemoryManager` |
| Knowledge Centre | Ask your documents (answers quoted with citations, topic summaries, re-index, index status); research projects and sessions; documents (list, search, read); notes | Research log; Documents tool (through the ToolManager, so its folder limit and permission apply); MemoryManager |
| Tools & capabilities | Every tool as Available, Disabled (with the missing permission) or Error; calculator trial | `engine.tool_status()`, `tool.failed` events |
| Vision Centre | Camera status, live preview, "What do you see?", "Read text", detector/OCR/permission status | Camera tool's analyser |
| Sensor Centre | Temperature, humidity, pressure, motion, air quality (not supported yet); health on request | Sensor Hub |
| System monitor | CPU, system RAM, AURORA's own memory, uptime, Ollama, active and installed models, services, plugins, tools, logs | Windows APIs, Ollama, engine, AURORA's log |
| Settings | Model, personality, streaming, memory announcements, memories given to the model, accent, start page, maximised start, animations, voice name and speed | `aurora_preferences.json` |

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
  indicator. Leaving the page or closing the window turns it off.
  Frames stay in memory; nothing is recorded or saved.
- **Health readings** are only fetched when you click "Show health
  readings", and need the Health permission.
- **Permissions, devices and paths can't be changed from the interface.**
  They stay in `settings.py` / `local_settings.py`.

## Architecture

```
command_centre.py ── aurora/interface/app.py
                       │
                       ├── MainWindow ── sidebar + QStackedWidget of views
                       │                  (each view: BaseView with on_shown / on_hidden)
                       │
                       └── Backend (QObject)
                             ├── AuroraEngine  (the one engine; created in the background)
                             ├── engine thread pool, 1 thread: every engine call, in order
                             ├── io thread pool, 2 threads: Ollama checks, memory queries, image analysis
                             ├── Dispatcher: delivers results on the GUI thread
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

## Not done yet

- Voice conversations inside the window (the terminal's `listen` /
  `voice on` work).
- Device control and reminders have no page of their own yet; use Chat
  ("turn on the desk light", "remind me in 10 minutes to stretch").
- Light theme (only dark accents: cyan, blue, purple).
