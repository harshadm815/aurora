# AURORA Mobile redesign: Call, and a complete app

Written 2026-11-15 from your brief ("Replace Voice Chat with a Dedicated
Calling System" and "Complete Mobile UI/UX Development"). Builds on
Docs/ANDROID_APP.md (milestones 0-6, all built).

## What's there now (inspected)

| Part | Where | Fate |
|---|---|---|
| Hold-to-talk 🎤 in Chat (the "voice chat") | `MainActivity.chatScreen`, `startTalking`, `stopTalking`, `sendChat(spoken)` | **Removed** |
| `Recorder` (records while the 🎤 is held) | `Sound.kt` | **Removed** (used only by hold-to-talk) |
| `Speech` (plays AURORA's voice, Android voice fallback) | `Sound.kt` | Kept: reminders use it |
| Calls: Android's call system, ringing, lock screen, earpiece/speaker/Bluetooth, echo cancellation | `Calls.kt` | Kept, extended (Bluetooth route, levels, duration, transcript, reconnect) |
| Call sound: signed stream to the PC, interruption | `CallAudio.kt`, PC `aurora/mobile/call.py` | Kept, extended (sound levels for the animation, reconnect) |
| Call screen (text and plain buttons) | `CallActivity.kt` | **Rebuilt** |
| Tabs Chat · Today · PC · Memories | `MainActivity.kt` | **Rebuilt** as Home · Chat · Call · Memory · Control |
| PC speech: Vosk recognition, Piper voice | `aurora/mobile/speech.py` | Kept unchanged (calls use it; `/api/voice` stays) |
| PC memory edit/forget | `aurora/memory/manager.py` | Reused by new, permission-checked phone endpoints |
| PC chat streaming (`on_token`) | `Backend.send_message` | Reused to stream replies to the phone |

## Decisions (yours to change)

1. **No WebRTC.** Investigated: it would add a native library of about
   10-20 MB to an 0.9 MB app, and its strengths (NAT traversal, TURN relays,
   jitter buffers for the open internet) aren't needed here: the phone
   always talks to *your* PC over the cable, home Wi-Fi (TLS, pinned) or
   Tailscale (WireGuard), and each call is already signed by the phone's
   key. Measured answer time is about 1 s. The existing call stream stays
   and gets reconnection and sound levels instead.
2. **Transcripts stay on the phone** during the call and are thrown away
   when you close the after-call screen, unless you press **Save
   transcript** (it goes to Documents › From phone as a text file).
   Nothing from a call becomes a memory unless you pick it.
3. **Memory on the phone** (view, search, edit, delete, add) uses the
   existing "Memories" permission on the PC's Phone page; deleting asks
   first. Without the permission the Memory tab says so and shows nothing.
4. **Remote commands are few and harmless**: pause or start AURORA, stop
   AURORA speaking on the PC. No shell, no opening programs; anything
   AURORA would *do* still goes through Chat's confirmations ("Approve
   actions" permission).
5. **Saved conversations**: AURORA doesn't store old conversations (by
   design); Chat shows the current shared conversation and can clear it.
6. **Theme**: dark (AURORA's look) by default; a light theme you can pick
   in Control.

## Navigation

Bottom bar, one-handed: **Home · Chat · Call · Memory · Control**.
Call opens the call screen (it's a call, not a page); the others are pages.

## Milestones

R1. **Design system and pages**: colours (dark and light), type sizes,
    cards, buttons, status pills, the animated AURORA core (one light
    Canvas view, animating only while visible), waveform; the bottom bar;
    Home (core, greeting, connection, model, coming up, recent, quick
    actions, big Call button), Chat (bubbles, copy, code blocks, file
    attachments, clear; no 🎤), Memory (list, search, categories), Control
    (status, services, tasks, notifications, this phone, settings,
    commands). Hold-to-talk removed.
R2. **AURORA Call**: full-screen call with the core showing the real state
    (connecting rings, calm, listening reacts to your voice, thinking
    pulses, speaking waveform, muted, reconnecting, disconnected), duration,
    mute, speaker, Bluetooth, live transcript (optional), end; reconnects
    once if the link drops; landscape layout; after-call screen (summary,
    AURORA's points, reminders set, transcript, save to memory, save
    transcript).
R3. **PC side**: `/api/memories` edit, delete, add; `/api/control` (pause,
    start, stop speaking); streamed chat replies through the event feed;
    clear conversation; reminders created during a call; tests for each,
    with permissions and the access log.
R4. **Checks**: unit tests for the app's logic (states, transcript,
    summary, search, formatting), PC tests, a build on the phone, every
    screen tried on the phone (I'll ask before taking screenshots of the
    AURORA app), battery after a call.

## Files

New: `Ui.kt` (palette, components), `CoreView.kt` (core, waveform, sound
levels), `CallSummary.kt` (transcript and after-call logic), `Format.kt`
(code blocks, search, greetings, due times); all logic unit-tested in
`RedesignTest.kt`. Rebuilt: `MainActivity.kt` (the five sections),
`CallActivity.kt`.
Changed: `Calls.kt`, `CallAudio.kt`, `Sound.kt` (Recorder out),
`Protocol.kt`; PC `aurora/mobile/server.py`, `mobile_bridge.py`, `call.py`.
