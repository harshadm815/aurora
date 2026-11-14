# Plan: the AURORA Android app

Written 2026-11-14. Replaces "Mobile milestone 2" in VOICE_WAKE_MOBILE.md
and is the base for the Calling Agent (stage 5). Nothing here is built yet.

## The idea: AURORA works like an account

Your **PC is the account**. It holds the conversation, memories,
reminders, research and settings, and does all the thinking (the model,
speech recognition, the voice). The **phone app is a device signed in to
it**, like a phone signed in to an email account:

- **Sign in once**: scan a QR code shown on the PC (or type the code). The
  phone gets its own key, kept in Android's hardware-backed Keystore.
- **Stay signed in** and reconnect by itself, by whatever route is
  available (below).
- **Same everything**: one conversation, one set of memories, one list of
  reminders; the PC is the only copy, so there's nothing to keep in step.
- **Sign out** from the phone or from Settings → Phone on the PC.
- **What a phone may do** is set per phone (memories, approving actions,
  files, calls), as today.

## Where it runs

| | |
|---|---|
| Phone | OPPO CPH1931, Android 10 (API 29), arm64, 6 GB RAM, ColorOS |
| PC | i3-8130U, 3.4 GB usable RAM (about 0.5 GB free with AURORA and TinyLlama running) |

- The app is small: it shows, records and plays. No model runs on the
  phone, so it needs little memory and battery.
- **ColorOS closes background apps aggressively.** Notifications and
  incoming calls while the app is closed need a foreground service (a
  permanent "AURORA connected" notification) and AURORA allowed to run in
  the background in the phone's battery settings. The app will show you
  where that setting is.

## How it's built

**Kotlin, a native Android app**, built on this PC with Google's
command-line tools (no Android Studio), installed on the phone over the
cable with `adb` (no Play Store; signed with a key kept on this PC).

Why native: the Calling Agent needs Android's call system
(`ConnectionService`: a real ringing screen, answering from the lock
screen, Bluetooth headsets, echo cancellation), a background service and
notifications. A web page can't do these; wrappers (Capacitor, Kivy,
BeeWare) still need the same build tools and then make those parts harder.

Tools needed (one-time, about 1.5 GB download, 2.5 GB on disk; put on D:):

- Eclipse Temurin JDK 17 (open source)
- Android SDK command-line tools, platform 34, build-tools (Google)
- Gradle (fetched by the project's wrapper)

**Builds on 4 GB RAM**: Gradle needs about 1-1.5 GB, so AURORA's model
(Ollama) must be closed while I build; the first build may take 5-10
minutes, later ones about a minute. I'll measure this in milestone 0 and
always close AURORA properly, never force it.

Code lives in `android/` in this repository; the PC side stays in
`aurora/mobile/`. Tests: the Kotlin parts (pairing, connection choice,
message format) get unit tests run on the PC; the PC side gets Python
tests as now (no network, no microphone). Things only the phone can show
(ringing, notifications, sound) are checked by hand, on your phone, with
you.

## Connecting: three routes, chosen automatically

The app tries them in this order and switches when one appears or drops:

1. **USB cable**: AURORA notices the phone plugged in and sets up the
   `adb reverse` link itself (what I did by hand today), so it survives
   unplugging and restarts. Uses adb from Platform Tools on this PC.
2. **Home Wi-Fi** (needs your choice, see below): AURORA would also listen
   on the home network, with TLS encryption. The PC's certificate
   fingerprint is part of the QR code, so the app trusts only *your* PC
   (no warning screens, nothing a stranger's device can impersonate),
   and every request still needs the phone's key.
3. **Away from home: Tailscale**, as planned before (you install it). The
   app needs no change; it just has one more address to try.

The PC side gains a **live connection** (WebSocket) next to today's
requests, so the PC can push things to the phone: replies, reminders,
"AURORA is calling", call audio.

### Milestone 0, measured (2026-11-14)

- Tools in `D:\Android`: Temurin JDK 17.0.21, Gradle 8.7 (checksums
  verified), Android SDK platform 34, build-tools 34.0.0, platform-tools;
  licences accepted by you. Android Gradle Plugin 8.5.2, Kotlin 1.9.24.
- First build (including downloading the plugin and compiler): **6.3
  minutes**, Gradle at most **1.5 GB**, with only 0.5 GB of RAM free
  (AURORA closed), so Windows used the page file.
- The app: **784 KB**, no libraries, Android 8+; checks whether AURORA
  answers over the cable. 4 of 4 unit tests pass. Installed on the phone
  with `adb install` and started.
- Building from the command line (AURORA closed first):
  `gradle --no-daemon assembleDebug testDebugUnitTest` in `android/`, with
  `JAVA_HOME=D:\Android\jdk`, `ANDROID_HOME=D:\Android\sdk`,
  `GRADLE_USER_HOME=D:\Android\gradle-home`.

### Milestone 1: how "only my phone" is enforced

- **One phone.** While a phone is paired, the PC makes no pairing codes
  and refuses pairing (`MOBILE_MAX_PHONES = 1`). Disconnect it in
  Settings → Phone to pair another.
- **Pairing only over the cable.** `/api/pair` is accepted only on the
  127.0.0.1 listener, from 127.0.0.1, with Host `localhost`: that is the
  `adb reverse` link. Not over Wi-Fi, not through Tailscale. And only with
  the one-time code shown on the PC.
- **A key that can't leave the phone.** At pairing the app makes an
  ECDSA P-256 key in the Android Keystore and sends only its public half.
  Every request is signed (method, path, time, a one-time nonce and the
  body's SHA-256); the PC refuses requests that aren't signed by that key,
  are more than two minutes old, or reuse a nonce. A copied device key or a
  recorded request is useless elsewhere.
- **Home Wi-Fi only on home networks.** The HTTPS listener (port 8766) and
  the discovery answer (UDP 8767) run only while the PC is connected to a
  Wi-Fi network you marked as home in Settings → Phone (checked every 20
  seconds). The phone accepts only the PC's own certificate, whose
  fingerprint it got over the cable.
- The 0.45 web app is retired: a browser can't hold such a key.

Windows Firewall asks the first time AURORA listens on Wi-Fi. Allow it for
the kind of network your home network is (Windows calls a phone hotspot
"Public" unless you change it).

## Milestones

Each ends with something you can use on the phone, tested.

0. **Tools and a first app**: install the tools (with your OK), build an
   empty AURORA app, install it on your phone, measure build time and RAM.
1. **Sign in and chat** (**built, 0.46.0 / app 0.2.0**): pairing over the
   USB cable with a code (no QR: the cable is the proof the phone is
   yours), key in the Keystore, chat, Today, PC status, memories,
   approvals (same permissions as before), sign out. PC: automatic USB
   link; home Wi-Fi listening with TLS; one phone only. Details below.
2. **Live connection and notifications** (**built, 0.47.0 / app 0.3.0**):
   a background service keeps one request to `/api/events` waiting (long
   polling over the same signed, encrypted routes; no new port), so due
   reminders arrive as notifications and AURORA's replies too while the
   app is closed. Chats held on the PC appear in the phone's Chat. The
   service shows a quiet "AURORA · connected by …" notification (Android
   requires one), reconnects by itself (5 s up to 1 minute apart), starts
   again after the phone restarts, and can be switched off ("Stay
   connected" on the PC tab). Notifications show no content on the lock
   screen. The app asks Android to leave it running when saving battery;
   ColorOS also needs AURORA allowed under App battery management.
3. **Voice from the phone** (**built, 0.48.0 / app 0.4.0**): hold 🎤 in
   Chat; the phone records 16 kHz mono (only while held, up to 30 s, never
   saved) and sends it to `POST /api/voice`; the PC recognises it with Vosk
   (`aurora/mobile/speech.py`) and the text goes through Chat as usual. If
   you spoke, the reply is spoken: `POST /api/speak` returns a WAV in the
   Piper voice, played on the phone. No cloud speech service (Android's own
   recogniser may send audio to Google, so it isn't used; Android's own
   voice is used only if the PC can't speak). Measured on this PC:
   speaking a sentence 0.15 s and recognising one about 1 s once loaded;
   the first use loads the models (about 10 s and 8 s), freed after 10
   minutes unused. Words recognised exactly in a Piper-to-Vosk loop test.
   **Reminders** also got an alarm: sound and vibration (alarm volume), a
   full-screen reminder screen over the lock screen (Done / Remind me in
   10 minutes), and AURORA saying the reminder; each can be switched off
   on the app's PC tab.
4. **Calling Agent** (**built, 0.49.0 / app 0.5.0**): call AURORA with
   📞 in the app, or AURORA calls you ("Call my phone" in Settings → Phone
   on the PC, or reminders set to "ring as a call" in the app). The call
   goes through Android's own call system as a self-managed calling app
   (ConnectionService): a real ringing screen over the lock screen, the
   earpiece by default, speaker and Bluetooth, the phone's echo
   cancellation; it never uses the phone network. On the PC
   (`aurora/mobile/call.py`) the signed `GET /api/call` is upgraded to a
   two-way stream of small frames on the same connection: microphone up,
   AURORA's voice down at real-time pace, short JSON events. The voice
   activity detector ends your turn after 1 s of quiet, Vosk recognises
   it, it goes through Chat (same tools, permissions; approving actions
   still needs that permission), and the answer is spoken sentence by
   sentence; talking over AURORA stops it at once. "Goodbye" or "that's
   all", 20 s of silence, hanging up or 30 minutes end it; one call at a
   time. Measured with the real models (the 'phone' speaking Piper audio
   resampled to 16 kHz): each turn answered about 1 s after you stop
   talking; the first call after a quiet spell waits about 10 s while
   the voice and recogniser load (when AURORA calls you they load while
   it rings).
5. **Files**: share anything to AURORA from the phone's share menu (saved
   in a "From phone" folder, never overwriting) and send files from the PC.
6. **Away from home and audit**: Tailscale route, poor-network behaviour,
   battery use over a day, a security review of everything the phone can
   reach.

## What the Calling Agent can't do

Calls are **between you and AURORA**, over the app's own connection.
AURORA can't answer or make ordinary phone-network calls for you (talking
to another person in a SIM call): Android doesn't let apps hear or speak
into those calls; only the phone's maker can. A rough workaround (speaker
and microphone during a call) would be poor and unreliable, so it isn't
planned.

## Security, as for the rest of AURORA

- Sign-in only with a code from the PC; keys stored hashed on the PC and in
  the Keystore on the phone; lost phone → Disconnect on the PC.
- Encrypted on every network route (TLS with a pinned certificate at home,
  Tailscale away; the cable never leaves the two devices).
- The phone's microphone is on only while you hold to talk or are in a
  call, shown on screen and in Android's privacy indicator; no recording
  to disk, no transcripts in logs.
- Every request goes through the same tools, permissions and
  confirmations as Chat, and is in the access log (which phone, what part,
  never what was said).
- The PC never accepts commands that skip the engine: no remote shell, no
  unrestricted control.

## Decisions for you

1. **Download the build tools** (about 1.5 GB, to D:) to start milestone 0.
2. **Home Wi-Fi**: should AURORA also listen on your home network (TLS,
   pinned certificate, phone key required), or only by cable and
   Tailscale? Recommended: yes, it's what makes the phone work around the
   house without installing Tailscale.
