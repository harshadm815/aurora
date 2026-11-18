# AURORA communications: Gmail Assistant and call screening — Phase 1 audit

Written 2026-11-17 from your brief ("Gmail Assistant & Android AI Call
Screening Integration"). This is the architecture audit and plan the brief
asks for before any implementation. Nothing here is built yet.

## 1. What AURORA already has (reused, not duplicated)

| Need | Existing part | How it's reused |
|---|---|---|
| Permissions, deny by default | `aurora/kernel/permissions.py` (per component, granted only in settings) | New permissions `email.read`, `email.send`, `calls.screen`; the Gmail and call tools get only what they need |
| Internet access control | `aurora/network/gate.py` (every request through the gate; "ask / on / off") | Gmail is called through the gate with plain HTTPS (httpx), not Google's client library (which would bypass it) |
| Confirm before acting | the engine's pending confirmations (`engine.pending`, Yes/No in Chat and on the phone) | Every email send is one confirmation showing the exact recipient, subject and text; changing anything asks again |
| Untrusted content | `aurora/tools/untrusted.py` | Email bodies and callers' words are quoted as data, never followed as instructions |
| Scheduling | `aurora/automation/schedule.py`, the Schedule tool (asks before adding) | "every day at 21:00 prepare my email report" |
| Briefing | `tool_plugins/briefing.py` | The daily email report reuses its structure and is readable on the PC and the phone |
| Phone link | `aurora/mobile` (paired phone, signed requests, TLS, event feed, notifications, Approve-actions permission) | Email summaries, drafts and approvals, reports, call-screening notices all go over it; nothing new is exposed |
| Voice | Vosk (hearing) and Piper (voice) on the PC; Vosk, the phone model and Android's voice on the phone | Reused for any call conversation |
| AI | TinyLlama on the PC, Qwen3 0.6B on the phone | Used for summaries; see limits below |
| Audit | the mobile access log, logging that never stores content | A separate audit log for sends and approvals (who, when, recipient, subject hash; never the body) |

Missing: secret storage (AURORA stores no passwords or tokens today).
Plan: encrypt OAuth tokens with Windows' own per-user encryption (DPAPI,
built in, no new library), in a file outside the repository.

## 2. Gmail Assistant — feasible, free, with one setup step only you can do

**What you need to set up (once, about 15 minutes, free):** a Google Cloud
project in your Google account, the Gmail API switched on, an OAuth
consent screen and a "Desktop app" OAuth client. I can walk you through
it; creating the project and accepting Google's terms are yours to do.
The Gmail API itself costs nothing at this size.

**The 7-day catch.** A personal project left in "Testing" mode gives sign-ins
that expire after 7 days (then AURORA asks you to sign in again: one click
in the browser). Publishing it ("In production") removes that, but Gmail
read access is a *restricted* permission: Google's verification and a paid
yearly security assessment apply to apps for other people; apps for your
own personal use are exempt but show an "unverified app" warning when you
sign in. Recommended: start in Testing, move to production (personal use)
once it works.

**How it would work:**

- Sign-in in your own browser (AURORA never sees your password), with
  PKCE and a one-time local redirect on 127.0.0.1; tokens encrypted with
  DPAPI; "Disconnect Gmail" revokes them at Google and deletes them.
- Read-only first (`gmail.readonly`). Sending is a separate, later
  permission (`gmail.send`), asked for only when you switch replies on.
- Sorting uses Gmail's own labels first (Important, Promotions, Social,
  Updates, Forums, Spam) plus simple rules (calendar invitations →
  Meetings; your contacts and replies → Personal; school/work domains you
  name → School/Work; unsubscribe headers → Newsletters). Suspicious:
  failed SPF/DKIM/DMARC in Gmail's own authentication results, look-alike
  sender domains, links whose text and target differ, urgent-payment
  wording; always "possibly", never certain.
- Deadlines, meetings and requests: dates and request phrases found in
  the text, shown as "found in the email", not as facts.
- Unanswered: threads where the last message is to you and you haven't
  replied.

**Honest limit:** TinyLlama on this PC writes weak summaries of long
emails. Plan: short extractive summaries (the sentences that matter, the
dates, the asks) always, plus an AI paragraph clearly labelled
"AI summary, may be wrong". A better model would need more memory than this
PC has free.

**Replies (Phase 3):** you ask; AURORA finds the thread and drafts; it shows
recipient, subject and the full text; it sends only after your "yes" to
that exact message (in Chat or the phone's Approve), never on silence, never
attachments unless you add them, and every send and approval is in the
audit log. A draft changed after approval needs a new approval.

**Daily report (Phase 4):** at the time you choose, when AURORA is running
(it can run in the notification area: switch on "close to tray" and "start
with Windows"). If the PC is asleep at that time, it's prepared when it
wakes. Readable on the PC and the phone, notification on either.

## 3. Call screening — what's actually possible on your phone in India

**Approach A (Android itself): screening yes, an AI conversation no.**
Android lets an app be your "call screening" app (Android 10+, no need to
replace OPPO's dialer): it sees each incoming call's number before it rings
and can let it ring, silence it, or reject it, and AURORA can notify you
("Unknown number, not in your contacts, called 3 times today"). But no
ordinary app can hear or speak into a mobile (SIM) call: that needs
system-only permissions (CAPTURE_AUDIO_OUTPUT, CALL_AUDIO_INTERCEPTION)
that only phone makers' own apps get. Google's Call Screen works only on
Pixels for that reason. So AURORA cannot answer a SIM call and talk to the
caller through your phone, and the brief rules out workarounds (rooting,
accessibility tricks), as do I.

**Approach B (forwarding to a call gateway): technically possible,
practically hard for one person in India.** Your carrier forwards calls
(for example when you don't answer) to a number at a telephony provider;
the provider streams the call to AURORA (Twilio Media Streams or similar),
AURORA talks to the caller with Vosk and Piper, notifies you, and on
"Connect" the provider calls you and joins the calls. Problems:

- An Indian number at such a provider needs KYC and documents (sources
  conflict on whether Twilio offers Indian numbers at all); Indian
  providers (Exotel, Ozonetel) mostly sign up businesses.
- Forwarding to a foreign number costs international rates per minute on
  your plan and some carriers block it.
- Costs: the number (monthly), per-minute incoming and outgoing legs, plus
  your carrier's forwarding charges.
- The provider must reach AURORA from the internet: a public HTTPS
  address (Tailscale Funnel or a tunnel) protected by the provider's
  request signatures; that's the one place AURORA would face the internet.
- Bridging back to you: if you forward all calls, the gateway's call to you
  would be forwarded too; it has to be "forward when unanswered/busy" or a
  second number, or the AURORA app as the receiver (in-app calling).

**Approach C (fallback): in-app only.** Callers would need the AURORA app,
which ordinary callers don't have, so this doesn't screen real calls.

**Recommendation:** build Approach A's screening now (free, works on your
phone: who's calling, rules for unknown numbers, AURORA's notification and
a summary from your contacts and emails), and decide on B only after you
check with your carrier and a provider (I can draft the questions). Nothing
about B will be claimed working until tested with a test number.

## 4. Phases (your brief's order, adjusted to the findings)

1. This audit. ✔
2. ✔ (0.56.0) Gmail read-only: setup guide, OAuth (PKCE, DPAPI), inbox, sorting,
   search, summaries, the Gmail page on the PC, commands in Chat, tests with
   recorded API responses (no real mail in tests).
3. ✔ (0.58.0) Replies with per-message approval and the audit log.
4. ✔ (0.57.0, built before 3 at your request) Daily email report (PC and phone),
   tried on your real mail: notifications arrived on the PC and the phone.
5. Call screening, Approach A: the phone's screening role, rules,
   notifications, call log in Control, tests.
6. Approach B only if you choose it after the carrier/provider checks.

## 5. Decisions (2026-11-17)

1. **Phase 2, Gmail read-only: go.** You create the Google Cloud project and
   OAuth client with my guidance (Testing mode first).
2. **Call screening: Approach A only** (screening on the phone; no AI
   conversation with callers, no forwarding). Carrier: Airtel, noted in case
   forwarding is reconsidered later.
