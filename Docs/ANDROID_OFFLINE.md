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

## C. AURORA's mind on the phone (needs your go-ahead for downloads)

A small language model on the phone (llama.cpp, 0.5-1.5 B parameters,
0.4-1 GB) for offline chat and calls, Vosk on the phone for hearing, Android's
voice for speaking. Needs Android's native build tools (NDK, about 1 GB) on
the PC. Offline answers are clearly marked and much simpler than the PC's.
I'll ask before downloading anything.
