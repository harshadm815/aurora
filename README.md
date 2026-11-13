# AURORA

A local-first personal AI assistant for Windows, with its own language
model written from scratch in NumPy. Everything runs on a 4 GB laptop, and
nothing leaves it unless you ask.

## Highlights

- **Its own model, from scratch.** `aurora_core/` implements the tensor
  maths, hand-written backpropagation, a Transformer (AURORA-T0), BPE
  tokenizers, training and text generation in NumPy only: no PyTorch, no
  TensorFlow. AURORA's own runtime runs the trained model in the
  assistant ([model card](Docs/AURORA_T0_MODEL_CARD.md)).
- **Research done properly.** Architecture experiments are specified
  before they run, repeated over five seeds and reported with confidence
  intervals. Results are checked by re-running them bit for bit
  ([Docs/RESEARCH.md](Docs/RESEARCH.md)). For example, a recurrent variant
  beat the T0 baseline by 0.051 validation loss (95% CI 0.022 to 0.080),
  on all 5 seeds.
- **Privacy by design.** A network gate lets each component reach only
  its own hosts. Internet access can be on, off or ask-first, and every
  request is listed. Memories, documents and sensor data stay on the PC.
  The camera and microphone open only when you ask.
- **Verified tools, not guesses.** Requests are routed to tools that
  compute or look up the answer (calculator, statistics, weather,
  Wikipedia, arXiv, news, your documents). Every answer says where it
  came from.
- **Its own web browser.** Tabs, history, bookmarks, reader mode, and
  questions about the open page answered by quoting it. Every site a page
  contacts is listed and checked by the network gate.
- **Documents like File Explorer.** Browse your folders, preview files,
  and find documents by what they say (Smart search); deletes go to the
  Recycle Bin and nothing is ever overwritten.
- **News intelligence on request.** Searches GDELT, Google News, publisher
  feeds and web search in parallel. It cross-checks publishers, flags
  allegations and corrections, tracks the legal stages of crime reports,
  and has an Investigation Mode ([Docs/NEWS.md](Docs/NEWS.md)).
- **Tested.** About 2,200 automated tests, none of which use the network,
  camera or microphone.

Current stage: working prototype of all twelve roadmap milestones.
AURORA chats with a local model, remembers facts about you, routes
requests to verified tools (calculator, statistics, weather, Wikipedia,
arXiv, your documents), plans multi-step requests and runs structured
workflows that pass results between steps, remembers facts with their
source (and forgets on request), answers questions from your documents
by quoting them with citations, labels where every answer came from, keeps a short activity history, learns
routines you teach it, listens and speaks offline, sees
through the webcam, reads room and health sensors (simulated until the
ESP32 is built), switches devices only after you confirm, sets reminders,
and runs a team of research agents. Everything runs on a 4 GB laptop.

AURORA's own language model is being built from scratch in `aurora_core/`
(NumPy only). So far: the tensor maths, the AURORA-T0 Transformer, the
tokenizers, the training pipeline and text generation. A first model,
trained on *The Adventures of Sherlock Holmes*, writes text from its own
weights, run by AURORA's own runtime with no Ollama involved (see
[its model card](Docs/AURORA_T0_MODEL_CARD.md)). It can be chosen as the
assistant's model in Settings (`aurora:aurora_t0_lr1e-2`), but it's far too
small to hold a conversation, so Ollama stays the default; see
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

4. Optional, for voice (`listen`, `voice on`, `speak on`, and Ask by voice in the Command Centre):
   download `vosk-model-small-en-us-0.15.zip` from
   https://alphacephei.com/vosk/models and unzip it into `Models/`, so
   that `Models/vosk-model-small-en-us-0.15/am` exists.

   For the natural voice (Piper, the default; without it AURORA uses
   Windows' voice), download `en_US-ljspeech-medium.onnx` and
   `en_US-ljspeech-medium.onnx.json` from
   https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/ljspeech/medium
   into `Models/tts/piper/`. Piper (piper-tts) is GPL-3.0: fine for use on
   your own PC; see Docs/VOICE_WAKE_MOBILE.md before giving the packaged
   program to anyone.

5. Optional, for vision (`describe photo.jpg`, `what do you see?`, `read this`, `watch`, and the Vision Centre):
   download `object_detection_nanodet_2022nov.onnx` from OpenCV's
   opencv_zoo repository (models/object_detection_nanodet) into `Models/`.
   OCR models come with the `rapidocr-onnxruntime` package.

6. Optional, sensors (temperature, humidity, light, motion, CO₂/VOCs,
   dust): AURORA starts with a **simulator** (answers are labelled as
   simulated). To use real sensors, build the ESP32 in
   `firmware/esp32_sensor_hub/` (wiring and flashing in its README) and
   set `SENSOR_SOURCES` in `aurora/config/local_settings.py`.

7. Optional, devices and automation: AURORA starts with a simulated desk
   light and fan. Add `"schedulable": True` to a device to let scheduled
   tasks switch it; programs AURORA may open are listed in `APPS`, folders
   it may tidy in `ORGANIZE_FOLDERS` (default: Documents).
   For real switching, build `firmware/esp32_device_controller/` (read
   its safety notes first) and set `DEVICES`, `DEVICE_CONTROLLER_URL` and
   `DEVICE_CONTROLLER_TOKEN` in `aurora/config/local_settings.py`.

## Research

AURORA's own model is also a research platform: every claimed improvement
needs a reproducible experiment. See [docs/RESEARCH.md](docs/RESEARCH.md) and the
[claims register](docs/research/CLAIMS.md).

## News

Ask for news about anything, anywhere ("latest reported murder cases in
India", "scientific discoveries this month", "investigate the Pune bridge
collapse"), in the chat or the News Centre. AURORA searches only when you
ask, sends only the search terms, and shows every story's publishers, dates,
links and how well it is confirmed. See [Docs/NEWS.md](Docs/NEWS.md).

## Browser

The Browser page is AURORA's own web browser (Qt WebEngine): tabs, an
address bar that also searches, history and bookmarks kept on this PC,
find in page, PDF viewing and downloads you confirm. Reader mode shows a
page's article without menus and adverts; "Ask" answers questions about
the open page by quoting it; "Save to Knowledge" adds it to your documents.
Every site a page contacts is listed, and the network gate applies. See
[Docs/BROWSER_AUDIO_VIDEO.md](Docs/BROWSER_AUDIO_VIDEO.md).

## Privacy and the network

Conversations, memories, documents and the language model stay on this PC.
Only Weather, Wikipedia, arXiv, News and the Browser use the internet (each
service only to its own hosts; the Browser to the sites you open), and by
default AURORA asks before each one first goes online in a session. Settings → Internet and network switches internet access on, off
or back to asking; System → Network activity lists every request. See
"Network gate" in [Docs/ARCHITECTURE.md](Docs/ARCHITECTURE.md).

## Run

Desktop interface (AURORA Command Centre):

```
.venv\Scripts\python.exe command_centre.py
```

Windows executable (after building it, see [Docs/COMMAND_CENTRE.md](Docs/COMMAND_CENTRE.md)):

```
packaging\build_windows.py   →   dist\AURORA Command Centre\AURORA Command Centre.exe
```

Wake Engine (hold Right Ctrl, say "Wake up"): switch it on in Settings →
Wake Engine. A small helper (about 35 MB, no CPU while idle) watches for the
key; the microphone opens only while you hold it.

Terminal interface:

```
.venv\Scripts\python.exe main.py
```

The Command Centre's **Model manager** loads and tries AURORA's own models
(and manages Ollama's), and its **Training monitor** shows loss curves and
starts new training runs.

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
- [Planned: Browser, Audio Lab, Video Studio](Docs/BROWSER_AUDIO_VIDEO.md)
- [Planned: Voice Engine, Wake Engine, Mobile Companion, Calling Agent](Docs/VOICE_WAKE_MOBILE.md)

## License

MIT; see [LICENSE](LICENSE). Trained model weights (`Models/`) and personal
data are not part of this repository.
