# Blueprint: AURORA Browser, Audio Lab and Video Studio

Three planned subsystems, added to the blueprint on 2026-11-11. **None of
them is built yet.** This file holds their requirements, what already
exists in AURORA that each builds on, and what this laptop (i3-8130U,
4 GB RAM, no GPU) can and can't do, so that each feature is planned as
something that will really work.

Order: **Browser first, then Audio Lab, then Video Studio.**

| Subsystem | Package (planned) | Builds on | Status |
|---|---|---|---|
| AURORA Browser | `aurora/browser/` | Command Centre (PySide6), network gate, Knowledge (documents and citations), News | Planned: priority 1 |
| AURORA Audio Lab | `aurora/audio/` | Voice (Vosk speech recognition, Windows SAPI speech), NumPy | Planned: priority 2 |
| AURORA Video Studio | `aurora/video/` | Vision (OpenCV), Audio Lab, NumPy | Planned: priority 3 |

The packages sit inside `aurora/` like `news/`, `voice/` and `vision/`, so
they share the engine, router, permissions and network gate. Only
`aurora_core/` (AURORA's own model) is a separate top-level package. The
packages will be created when their first working feature is, not before:
empty modules would show progress that doesn't exist.

## Rules for all three

- **Local first.** Everything that can run on this PC runs here. Your
  pages, recordings and videos stay on this PC.
- **External computing is optional and asked for.** Some generation (video
  in particular) is beyond this laptop. If an external service is ever
  added, it goes through the network gate like every other connection: off
  until you turn it on, asked for each session, recorded in Network
  activity, and sent only what the job needs (a prompt, never your files or
  history unless you choose a file to send).
- **Honest output.** Summaries, comparisons and explanations written by a
  model are labelled as such, with links to their sources. Nothing is
  claimed that a test hasn't checked. The model limits in NEWS.md apply
  here too: TinyLlama and AURORA-T0 can't yet write reliable summaries, so
  those features wait for a capable local model, or show extracted text
  and quotes instead.
- **Consent for voices.** Voice cloning only of a voice whose owner has
  recorded their consent in AURORA. Never of public figures. Generated
  speech can be marked as generated.
- **Tests never use the network, camera, microphone or speakers.**

## 1. AURORA Browser

A browser page inside the Command Centre.

| Requirement | How | Status |
|---|---|---|
| Tabs, address bar, search, back/forward/reload | Qt WebEngine (Chromium) in PySide6 | Planned |
| Bookmarks and history | Local SQLite database, not in git; history can be paused and cleared, like activity history | Planned |
| Download manager | Qt WebEngine downloads, saved only after you confirm, to a folder you choose | Planned |
| Reader mode | Extract the article text from the page (main content, without menus and adverts) | Planned |
| PDF viewing | Qt WebEngine's built-in PDF viewer | Planned |
| Find in page | Qt WebEngine | Planned |
| Extract information | Tables, links, headings, dates and numbers from the page text, rule-based like News | Planned |
| Save pages to the knowledge base | Reader-mode text saved into Knowledge, with the address and date, so answers can quote and cite it | Planned |
| Questions about the page you're viewing | Knowledge's quoting with citations over the page text; model-written answers when a capable local model exists | Planned (quoting first) |
| Compare sources ("compare these three papers") | Side by side: titles, authors, dates, methods and results sections, figures quoted with links; a written comparison needs a capable model | Planned (extraction first) |
| Website summaries | Needs a capable local model; until then, the page's own description and its headings | Waits for a model |
| Translation | Needs a translation model (several hundred MB per language pair) or an external service through the gate | Later |
| Voice-controlled browsing | Voice commands routed to the browser ("open …", "next tab", "read this page") | Planned (after the basics) |

**Privacy:**
- Chromium makes its own connections, not through AURORA's httpx gate. A
  request interceptor (`QWebEngineUrlRequestInterceptor`) will record and
  check every host it contacts. Because you are browsing on purpose, page
  loads you start count as your request. Pages never load by themselves
  in the background.
- Cookies and site data stay in AURORA's own browser profile, separate
  from your other browsers, and can be cleared.
- AURORA reads a page only when you ask about it, and keeps the page text
  only when you save it.

**On this laptop:**
- It needs `PySide6-Addons` (about 200–300 MB more, and a larger `.exe`).
- Each Chromium tab uses about 100–300 MB of RAM. With Ollama loaded,
  expect a few tabs at a time, not dozens. The browser should limit the
  number of open tabs and warn when memory is low.

## 2. AURORA Audio Lab

| Requirement | How | Status |
|---|---|---|
| Text-to-speech, narration | Windows SAPI voices (already in Voice); saving to WAV files | Speaking: **done** (Voice). Saving narration to files: planned |
| Voice choice, speed, volume | Already in Voice settings | **Done** |
| Transcription of audio files | Vosk (already used for the microphone), run on files | Planned |
| Voice activity detection | Energy-based detection exists for barge-in; a proper detector for files | Partly done |
| Audio editing: cut, join, fade, normalise, change speed | NumPy on WAV files | Planned |
| Noise reduction | Spectral subtraction in NumPy | Planned |
| Sound effects and ambient sound | Procedural synthesis in NumPy (noise, tones, envelopes, filters: rain, wind, hums, chimes) | Planned |
| Background music | Rule-based and procedural composition (scales, chords, rhythm) in NumPy | Planned (simple music) |
| Podcasts from documents ("turn my paper into a podcast") | Script from the document's own text, read by SAPI voices, joined with music | Planned, with quoted script; a written script needs a capable model |
| Audio summaries | Transcription, then the same summary limits as text | Waits for a model |
| Speech translation | Needs translation models | Later |
| Customisable and cloned voices | Neural speech models (e.g. Piper-sized, 20–60 MB per voice) run on a CPU; cloning needs larger models and consent | Later; cloning only with consent |
| Neural music and sound generation | Models of that kind need a GPU and several GB of memory | Not on this laptop; optional external computing |

## 3. AURORA Video Studio

Starts with editing, animation and scientific visualisation. Generative
video comes last, and not locally.

| Requirement | How | Status |
|---|---|---|
| Format and resolution conversion | FFmpeg (needs installing, about 100 MB; free and open source) | Planned |
| Editing: cut, join, transitions, titles | FFmpeg, with frames drawn in NumPy/OpenCV | Planned |
| Subtitles | From the script, or from transcription (Audio Lab), as SRT files or burned in | Planned |
| Video transcription | Audio track to Vosk | Planned |
| Scientific and space visualisations | Animations drawn frame by frame from real formulas and data (orbits, light curves, gravitational lensing, black hole formation as diagrams) | Planned |
| Educational videos ("a 30-second video on how a black hole forms") | Script, scenes, diagrams and animations, SAPI narration, subtitles, combined with FFmpeg | Planned; a written script needs a capable model, so first from a script you give or approve |
| Automatic compilation | Joining clips, pictures and narration to a plan | Planned |
| Background removal | OpenCV (GrabCut, or a small segmentation model) | Later |
| Video summaries | Transcription and scene changes; written summaries wait for a model | Later |
| Text-to-video and image-to-video generation | Needs GPU-class computing far beyond 4 GB of RAM | Not on this laptop; optional external computing |

## First milestone

The AURORA Browser, basic browsing:
1. tabs, address bar and search;
2. history and bookmarks (local and clearable);
3. every host recorded through the interceptor;
4. reader mode;
5. "save this page" into Knowledge;
6. questions answered by quoting the open page.

Done means: it works in the Command Centre on this laptop, with tests for
everything except the real Chromium rendering.
