# AURORA when the PC is closed or off

Written 2026-11-15 from your request: start a call from the phone even when
AURORA isn't open on the PC; keep the phone useful while the PC is off, and
sync when they meet again; and later, AURORA's mind on the phone itself.

## A. The phone starts AURORA (and Call just works) — built in 0.54.0

A small **phone helper** on the PC (`AURORA Command Centre.exe
--phone-helper`, like the Wake Engine's helper: no window, about 45 MB) runs
while AURORA is closed:

- at Windows sign-in, and whenever AURORA quits, if you switch on **"Let my
  phone start AURORA"** on the PC's Phone page (off until you do);
- it listens where AURORA would (cable, home Wi-Fi with the same certificate,
  Tailscale) with the **same checks**: only your paired phone, every request
  signed;
- it answers only three things: "is AURORA running?" (no), the connection
  details, and **"start AURORA"**. Everything else says AURORA is closed;
- on "start", it lets go of the ports, starts AURORA in the notification area
  (no window pops up on the PC) and quits; AURORA takes over.

On the phone, **Call** first checks whether AURORA is open; if it's closed it
asks the helper to start it, shows "Starting AURORA on your PC…", and calls
as soon as AURORA answers (about 20-40 s on this PC). Home and Control show
"AURORA is closed on your PC" with a **Start** button.

Not possible: starting a PC that is asleep or off (Wi-Fi can't wake a laptop).

## B. The phone on its own, then syncing — built in 0.54.0 (app 0.9.0)

Without a model on the phone (no downloads):

- **Reminders ring from the phone**: the phone keeps a copy of upcoming
  reminders and schedules them with Android's alarms, so they ring even if
  the PC is off. When the PC also announces one, it isn't shown twice.
- **Memories**: a copy on the phone, readable offline; memories you add
  offline are kept and sent when the PC is back.
- **Notes and requests**: anything you type in Chat while the PC is away is
  kept ("will send when your PC is back") and sent, in order, when it is;
  replies arrive as usual. Actions still ask for confirmation then.
- **Sync** runs whenever the phone reaches AURORA: phone → PC first (queued
  messages, new memories, memory edits and deletions made offline), then PC
  → phone (memories, upcoming reminders). Each change carries an id, so a
  retry never adds anything twice; when both sides changed the same memory,
  the newer change wins.

## C. AURORA's mind on the phone — built in 0.55.0 (app 0.10.0)

A small language model on the phone (llama.cpp, 0.5-1.5 B parameters,
0.4-1 GB) for offline chat and calls, Vosk on the phone for hearing, Android's
voice for speaking. Needs Android's native build tools (NDK, about 1 GB) on
the PC. Offline answers are clearly marked and much simpler than the PC's.
I'll ask before downloading anything.

**Built (2026-11-16)**, with your approval of the downloads:

| What | Where | Checked |
|---|---|---|
| Android NDK r27d LTS (27.3.13750724) | D:\Android\sdk\ndk | Google's published SHA-1 |
| CMake 3.22.1 | D:\Android\sdk\cmake | sdkmanager |
| llama.cpp release b12414 (commit 278a89cb), MIT | D:\Android\src (not in this repo; `llama.dir` in android/local.properties) | git |
| Qwen3 0.6B Q4_K_M (bartowski's GGUF of Qwen/Qwen3-0.6B, Apache 2.0), 484 MB | D:\Android\models, copied to the phone | published SHA-256, on the PC and on the phone |
| Vosk Android 0.3.75 and JNA 5.18.1 (Apache 2.0) | Gradle | Maven Central |
| Vosk's small English model | copied from Models\ on the PC (no download) | — |

llama.cpp is built without tools, servers or network code and without
anything fetched during the build (KleidiAI off), always optimised even in
test builds (an unoptimised runner is several times slower). The app grew
from 0.9 MB to 18 MB; the models live in the app's own folder on the phone
(about 550 MB).

Measured on your phone (Snapdragon 665, 4 threads): model load 2.5 s, first
words about 4.3 s (mostly reading its instructions), then 12.6 pieces a
second; a two-sentence answer in about 6 s. Possible later: keep the
instructions ready between messages (first words in about 1 s).

Offline chat: requests only the PC can do (reminders, devices, files, the
web, memories; `Format.needsPc`) wait for the PC as before; the rest is
answered on the phone and the exchange is added to the PC's conversation
when it's back (`/api/history/import`, applied once). Offline calls: Vosk
hears, the phone's model answers, Android's voice speaks; what's heard while
AURORA itself speaks is ignored unless you talked over it (echo).

OPPO's ColorOS began refusing calls started by apps ("Call not sent", no
reason logged), so calls you start now open AURORA's own call screen and
audio (Android's in-call audio mode) instead of Android's call service;
AURORA calling you still rings through the call service.
