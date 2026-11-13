# Blueprint: Voice Engine, Wake Engine, Mobile Companion and Calling Agent

Four capabilities, added to the blueprint on 2026-11-13. This file holds
their requirements, **what already exists in AURORA** (a good part of the
voice work does), what this laptop (i3-8130U, 4 GB RAM, UHD 620, no GPU)
can realistically run, the design decisions still open, and the order of
work. Nothing here is marked done until it has been tested on this PC.

| Capability | Package | Builds on | Status |
|---|---|---|---|
| Voice Engine | `aurora/voice/` (exists) | Vosk, VAD, Windows SAPI, Piper, `VoiceSession` | **Built**: provider interface, words while you speak, pitch (0.41.0), natural Piper voice (0.42.0); multilingual and Whisper optional, later |
| Wake Engine | `aurora/wake/` | `aurora/voice/` (Vosk grammar recogniser), the packaged .exe | **Built (0.43.0)**: hold Right Ctrl (or Space), say "Wake up"; measured below |
| Voice conversation | `aurora/voice/session.py` (exists) | Engine, Chat, barge-in, Stop | **Mostly built**; a continuous "call-like" session is missing |
| Mobile Companion | `aurora/mobile/` (planned) | Engine, memory, conversation, network gate | Not started |
| Calling Agent | `aurora/calling/` (planned) | Voice Engine, Mobile Companion transport | Not started |

The proposal's top-level folders (`aurora_voice/`, `aurora_wake/`, …) map
onto packages inside `aurora/`, like `voice/`, `browser/` and `news/`, so
they share the engine, router, permissions and network gate. `aurora_network/`
is not a new package: `aurora/network/` (the network gate) already exists
and the mobile transport is added there. Packages are created with their
first working feature, not as empty folders.

## Rules for all four

- **The microphone opens only when you ask**, is visibly on while it is,
  and closes when the request or session ends. No continuous listening by
  default, no recording to disk, transcripts never logged (all already
  true of `aurora/voice/`).
- **Local first.** Speech recognition and speech run on this PC. An
  external speech service may be added later as an option: off by default,
  asked for per session through the network gate, shown while in use, and
  sent only the audio of the current request.
- **Independent of the model.** Voice, wake, mobile and calling talk to the
  engine (`AuroraEngine.handle`), never to Ollama or AURORA-T0 directly, so
  the language model can change without touching them. How *good* the
  conversation is still depends on the model: TinyLlama is weak and
  AURORA-T0 is far too small to converse; that is a model problem, tracked
  in AURORA_CORE.md, not something these subsystems can hide.
- **Nothing reachable from the internet without pairing and encryption.**
  Remote access never gives unrestricted control: every remote request goes
  through the same tool permissions as Chat, and actions with side effects
  (devices, files, apps, deleting) still need confirmation, which a phone
  can give only after it has been paired. Every remote action is logged.

## What exists today (tested)

- **Speech recognition**: Vosk small English model (about 70 MB on disk,
  offline, streaming), adaptive voice activity detection (end of speech
  after 1.2 s of silence, gives up after 5 s without speech), wake-word
  grammar ("Aurora", "Hey Aurora"), number and correction clean-up.
- **Speech**: Windows SAPI voices (Zira, David) with speed, volume and voice
  choice in Settings, replies cleaned for speaking, long replies cut short.
- **Interruption**: Stop button, key press in the terminal, and barge-in
  (headphones) stop speech within a fraction of a second.
- **Conversation**: `listen` (one request), `voice on` (hands-free with the
  wake word, until "stop listening"), the Command Centre's Ask by voice
  and spoken replies; the AI core shows Listening and Speaking.
- **Loop-back test**: 0.9 % word error rate and 100 % correct commands on
  20 synthetic trials (real rooms are noisier).

## 1. Voice Engine: what's missing

| Requirement | Plan | Fits this laptop? |
|---|---|---|
| Common interface for local and external providers | **Done (0.41.0)**: `aurora/voice/providers.py`; Vosk and SAPI are the first providers; external ones refused unless `VOICE_ALLOW_EXTERNAL` | Yes (no new cost) |
| Natural, human-like speech | **Done (0.42.0): Piper**, chosen after measuring (below). Plan was: add a local neural voice as a second synthesiser. Candidates: Piper voices (ONNX, onnxruntime is already installed for OCR) and Kokoro-82M. **Licences must be checked first**: recent Piper releases are GPL-3.0 and both rely on a phonemiser (espeak-ng, GPL) for pronunciation; AURORA is MIT. Speed and RAM to be measured here before choosing | Probably, for a small voice (tens of MB); to be measured |
| Pitch, tone, style, accent | **Pitch done (0.41.0)** (SAPI XML, -10 to 10, in Settings); neural voices choose accent by voice model; "response length" is the existing `VOICE_MAX_SPOKEN_CHARS` | Yes |
| Streaming transcription | **Done (0.41.0)**: the words recognised so far are shown in Chat and on the Dashboard while you speak | Yes |
| Noisy rooms | Better VAD thresholds per microphone (the Test the microphone button), optionally a noise gate in `aurora/voice/` | Yes |
| More languages | Vosk small models exist for many languages (about 40-50 MB each); one loaded at a time | Yes, one at a time |
| More accurate recognition | Optional Whisper (tiny/base, int8) for one-shot requests where accuracy matters more than speed; Vosk stays the default because it streams | Tiny/base only; slower than real time on this CPU is possible, to be measured |

### A natural local voice: research (2026-11-13)

Three small neural voices can run on a CPU. None has been measured on this
laptop yet; published speed figures come from other machines.

| | Kokoro-82M (int8 ONNX) | Piper (medium voice) | Kitten TTS 0.8 (nano/mini) |
|---|---|---|---|
| Quality | The most natural of the three (best-rated small model) | Clear, a little flatter | Good for its size |
| Download | ~92 MB model + voices file | ~60 MB per voice | ~25-80 MB |
| Speed (reported elsewhere) | About real time on a mid-range laptop CPU (RTF ~1.1 in one browser test); may be slower here | Faster than real time even on a Raspberry Pi | Fast on CPU |
| Engine licence | kokoro-onnx: Apache-2.0 | piper-tts (OHF-Voice/piper1-gpl): **GPL-3.0** since the project moved in 2025 (the old rhasspy/piper, MIT, is archived) | Apache-2.0 (the newer 1.7B "KittenTTS 2" weights have a different, restrictive licence) |
| Voice/weights licence | Apache-2.0 | **Per voice**, in each MODEL_CARD: e.g. Ryan is CC BY-NC-SA 4.0 (non-commercial); LJSpeech-based voices use public-domain data | Apache-2.0 (0.8 models) |
| Pronunciation (phonemiser) | misaki (English dictionary; ~85 % of words without help) + **espeak-ng** fallback | **espeak-ng** built in | **espeak-ng** |

**Licences in practice.** espeak-ng, used by all three for pronunciation,
is GPL-3.0. For AURORA as it is (used on this PC, source code on GitHub
under MIT, the .exe not given to anyone), that restricts nothing: the GPL
only sets conditions on *distributing* the software. It would matter if
the packaged .exe were ever shared publicly (the GPL parts' source would
then have to be offered). So: the neural voice is an **optional
provider**, installed separately into `Models/` and not committed, and
Windows SAPI stays the built-in default. Kokoro can also run without
espeak-ng (misaki only), at the cost of some unusual words.

**Speed matters more than the table.** A voice that is slower than real
time makes AURORA pause between sentences. The plan copes with that by
speaking sentence by sentence (sentence 1 plays while sentence 2 is made),
but the first sentence's delay can't be hidden.

**Next step: measure, then choose.** Download Kokoro (int8) and one Piper
voice with permissive data into `Models/`, and on this laptop measure:
real-time factor, delay before the first word, RAM while speaking, and
CPU; then listen to the same sentences in each voice (and Windows' Zira
for comparison). The winner becomes the `neural` provider in
`providers.py`, with SAPI kept as the fallback when the model is missing.

### Measured on this laptop (2026-11-13)

i3-8130U, models on the USB drive, onnxruntime 1.32, five sentences
(1.7 to 11.7 s of speech each), after one warm-up sentence. "Speed" is
time to make the audio ÷ length of the audio: below 1 is faster than
real time.

| | Piper LJSpeech (medium) | Kokoro Heart (int8) | Kokoro Michael (int8) | Windows Zira |
|---|---|---|---|---|
| Speed | **0.16-0.21** (5× faster than real time) | 1.87-2.26 (2× slower) | 1.68-2.46 | (instant) |
| "I'm awake. How can I help you?" ready after | **0.37 s** | 3.8 s | 4.1 s | instant |
| A 10-second answer ready after | 2.1 s | 21 s | 20 s | instant |
| Load (from the USB drive) | 9.8 s | 9.4 s | 7.3 s | instant |
| RAM: model loaded / peak while speaking | 118 MB / 255 MB | 189 MB / 399 MB | 190 MB / 451 MB | small |
| CPU while making speech | ~2 cores, briefly | ~1.7 cores, for twice the speech's length | same | small |
| Word error rate (Vosk transcribing the samples) | 8.9 % | 5.1 % | 5.1 % | 6.3 % |

All four are clear enough to understand (the error rates are within
Vosk's own error). **Kokoro is too slow on this CPU**: every sentence
would wait about twice its own length before playing, so it can't be the
conversational voice here (it could make speech ahead of time, e.g. a
briefing saved to a file). **Piper is fast enough for conversation**, with
sentence-by-sentence speaking hiding all but the first ~0.4 s. Which
sounds better than Zira is for you to decide by listening: the samples are
in `Models/tts/samples/`.

## 2. Wake Engine

**Built in 0.43.0** (`aurora/wake/`; switched on in Settings → Wake Engine).
What was decided and measured on this laptop:

- **Key**: Right Ctrl by default (it never affects typing); Space is an
  option. With Space, a space is typed on release instead of on press,
  holding Space no longer repeats, and Space with Ctrl/Alt/Shift/Win is
  never intercepted; keys typed while Space is down keep their order.
- **Helper**: `AURORA Command Centre.exe --wake-helper` (the same program,
  no second build). Idle: **28 MB of memory, 0.00 % CPU** (20 s sample),
  no microphone, no speech model. Only one runs per user; the Command
  Centre starts, reloads and stops it, and can add it to Windows' startup.
- **Microphone**: opens after the key has been held 0.45 s, with a beep
  (optional) and a panel above the taskbar showing the level; closes the
  moment the phrase is heard, on release, on another key (cancel), or
  after 8 s. Needs the Voice component's 'microphone' permission.
- **Speech model**: loaded only when first needed (7 s cold from the USB
  drive, 1.5 s cached, +135 MB) while the audio is kept in memory, so
  nothing said meanwhile is lost; kept 10 minutes, then freed.
- **Wake phrase**: "wake up" (changeable; words the model doesn't know are
  reported). The recogniser's grammar also lists sound-alikes ("what's up",
  "wait up", "make up"…), which took false activations on 10 other
  sentences from 1/10 to **0/10** for each of Zira, David and Piper; the
  phrase itself is recognised in all three voices. Real voices in real
  rooms still need testing by you.
- **Waking**: the helper sends "wake" to the running Command Centre (its
  single-instance socket) or starts it with `--wake`; it comes forward,
  opens Chat, says "I'm awake. How can I help you?" (once AURORA has
  finished starting) and listens for one request.

The original design notes follow.

The requested sequence: AURORA closed, a small helper running, you hold a
key, the microphone opens (with a visible indicator), you say "Wake up",
AURORA starts and greets you, a voice session begins. Releasing the key
before the phrase is recognised cancels it.

Design:

- **Helper** (`aurora/wake/`): a separate small process (`AURORA Wake.exe`,
  started with Windows if you choose). It watches the keyboard with a
  Windows low-level keyboard hook (ctypes, no new dependency) and does
  nothing else while idle: no microphone, no model loaded. Target: under
  30 MB of RAM and ~0 % CPU idle (to be measured).
- **While the key is held**: open the microphone, show a small "Listening
  for 'Wake up'" overlay near the taskbar, run a Vosk recogniser restricted
  to the wake phrase (and a few rejection words, so other speech doesn't
  match). Release = microphone closed at once.
- **On the phrase**: start the Command Centre (or bring it forward if it's
  running) through a local pipe, which starts a voice session that says
  "I'm awake. How can I help you?".
- **False activations** need two things at once (key held *and* phrase
  heard), so they should be rare; the rate is measured with recorded and
  synthetic non-wake speech as part of the tests.
- **Phrase and key** are settings; manual activation from the Command Centre
  and later from the phone call the same start-up path.

**Open decision: the key.** Holding **Space** has a real cost: Space is the
most-typed key, so a global hook on it either delays every space you type
(it must wait to see whether you're holding it) or lets the held space
type repeated spaces into whatever window has focus. Alternatives without
that cost: hold **Right Ctrl**, hold **Ctrl+Space**, or a key you choose.
Recommendation: Right Ctrl by default, Space available as an option that
only counts a hold longer than about 0.6 s and passes short taps through.

Cold start matters on this PC: the project is on a USB drive and the
packaged app is about 850 MB, so "AURORA launches" may take several
seconds. The helper can say "Starting AURORA…" immediately, and the
greeting comes when the engine is ready. Start-up time will be measured.

## 3. Voice conversation (call-like sessions)

Already there: listen, answer, speak, interrupt. To add:

- A **conversation session** without the wake word after each reply: after
  AURORA speaks, it listens again for a few seconds; silence, "that's all"
  or the End button ends the session and closes the microphone.
- **Briefings**: "You have two reminders and one research update. Would you
  like a briefing?", built from Reminders, Schedule and the research log
  (real data only).
- Spoken reminders when they fall due (opt-in).

## 4. Mobile Companion

**Recommendation: a web app served by AURORA itself (a PWA), not a native
app.** Android Studio needs more memory than this laptop has, and a native
app would duplicate the interface in another language. A PWA is built with
the same tools AURORA already uses, installs to the phone's home screen,
works on Android and iPhone, and can use the phone's microphone.

- **Connection**: the phone's browser needs HTTPS to use the microphone.
  Recommended: **Tailscale** (a private, encrypted WireGuard network between
  your own devices, free for personal use). It gives the PC a private
  address and a valid HTTPS certificate, works on the same Wi-Fi and from
  anywhere, and nothing is opened to the public internet. You would install
  Tailscale on the PC and the phone and sign in yourself. Without it, local
  Wi-Fi only, with a self-signed certificate the phone has to trust once.
- **Pairing**: the Command Centre shows a QR code with a one-time code; the
  phone scans it, gets its own device key; Settings lists paired devices
  with Disconnect.
- **API** (`aurora/mobile/`): a small HTTPS + WebSocket server inside the
  Command Centre, off until you turn it on, listening only on the Tailscale
  (or local) address. Every request is authenticated per device; remote
  actions use the same permissions and confirmations as Chat, and are logged.
- **Features in order**: chat (the same conversation as the desktop),
  status (the top bar's information), reminders and tasks, memories
  (read, and edit if you allow it), research documents (read), file
  transfer into a "From phone" folder (never overwriting), notifications
  while the app is open.
- **Synchronisation**: the PC is the single source of truth, so there's no
  conflicting copy to merge: the phone shows and changes the PC's data
  through the API. You choose what the phone may see (conversation,
  memories, documents, files).

## 5. Calling Agent

- **First version**: a call is a voice session whose audio travels over the
  paired WebSocket: the phone streams microphone audio (16 kHz mono) to the
  PC, the PC recognises it, answers and streams speech back. On Tailscale
  or Wi-Fi that's about 256 kbit/s each way, well within reach, and it
  reuses everything in sections 1 and 3, including interruption.
- **WebRTC** (aiortc in Python) adds echo cancellation and better handling
  of poor networks, but brings large dependencies (FFmpeg libraries). It's
  worth adding only if the WebSocket version proves too slow or echoey;
  the call manager keeps the transport replaceable.
- Incoming calls: AURORA can ring the open app for a due reminder or a
  briefing you scheduled. Phone-network calls are out of scope.

## Memory budget on this laptop

| Piece | RAM (approx.) |
|---|---|
| Wake helper, idle | target < 30 MB |
| Vosk small model, while listening | ~ 60-100 MB |
| Windows SAPI voice | small (part of Windows) |
| A small neural voice (if chosen) | tens of MB; to be measured |
| Command Centre with the engine | ~ 170-300 MB |
| TinyLlama through Ollama | ~ 700 MB-1 GB |
| Browser engine, when opened | 100-300 MB per tab |

Only one speech recogniser and one voice are loaded at a time, and only
while needed.

## Order of work

1. **Voice Engine gaps**: provider interface; partial words while you
   speak; choose and measure a natural local voice (licence first).
2. **Wake Engine**: helper, key hold, wake-phrase detection, launch and
   greeting, cancellation; measured RAM, CPU, false activations, start-up.
3. **Voice sessions**: continuous conversation, end of session, briefings.
4. **Mobile Companion**: server, pairing, chat, status, reminders,
   memories, documents, file transfer.
5. **Calling Agent**: audio over the paired connection, interruption,
   incoming calls for reminders and briefings.
6. **Measure and audit**: CPU/RAM, network failures and reconnection,
   offline behaviour, microphone permissions, a security review of the
   mobile server.

## Tests each stage must pass

Automated (no real microphone, network or camera, as for all AURORA tests):
recognition and wake-phrase detection on WAV files, false activations on
non-wake speech, key-hold and cancellation with a simulated keyboard,
helper start/stop, interruption, pairing and authentication (rejected
without a valid device key), synchronisation, clean shutdown. Measured by
hand on this PC: real-microphone accuracy, voice quality, launch time,
latency of a call, CPU and RAM.
