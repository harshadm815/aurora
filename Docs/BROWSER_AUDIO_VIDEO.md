# Blueprint: AURORA Browser, Audio Lab and Video Studio

Three subsystems, added to the blueprint on 2026-11-11. **The Browser's
first milestone is built** (0.37.0); the Audio Lab and Video Studio are
not started. This file holds their requirements, what already
exists in AURORA that each builds on, and what this laptop (i3-8130U,
4 GB RAM, no GPU) can and can't do, so that each feature is planned as
something that will really work.

Order: **Browser first, then Audio Lab, then Video Studio.**

| Subsystem | Package (planned) | Builds on | Status |
|---|---|---|---|
| AURORA Browser | `aurora/browser/` | Command Centre (PySide6), network gate, Knowledge (documents and citations), News | **First milestone done** |
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
| Tabs, address bar, search, back/forward/reload | Qt WebEngine (Chromium 140) in PySide6; up to `BROWSER_MAX_TABS` (6) tabs; searches go to `BROWSER_SEARCH_URL` (DuckDuckGo by default) | **Done** |
| Bookmarks and history | `aurora_browser.db` on this PC (not in git); history search, delete, clear, switch off; older than 90 days deleted | **Done** |
| Download manager | Asked first (file name, site, size), then saved where you choose; a downloads list in the sidebar (on this PC; clearing it keeps the files) | **Done** |
| Reader mode | Article text without menus, adverts, cookie banners, share buttons and hidden text (`aurora/browser/reader.py`) | **Done** (a heuristic, like every reader mode) |
| PDF viewing | Qt WebEngine's built-in PDF viewer | **Done** (reader mode and Ask don't work on PDFs yet) |
| Find in page | Qt WebEngine | **Done** |
| Extract information | Published date, author, site, dates and figures mentioned, sections, tables, links (`aurora/browser/extract.py`) | **Done** |
| Save pages to the knowledge base | Reader-mode text saved as Markdown in `Documents/Web pages/`, with the address and date; Knowledge indexes it with your documents and cites it | **Done** |
| Questions about the page you're viewing | Sentences quoted from the open page, with the page as the source; "doesn't answer that" when no sentence does (`aurora/browser/answers.py`) | **Done** (in the Browser page; not yet from the chat) |
| Compare sources ("compare these three papers") | Compare open tabs: site, title, published date, length, sections, key sentences, and figures that differ between pages | **Done** (side by side and quoted; a written comparison needs a capable model) |
| Website summaries | Key sentences quoted from the page in its own order (the Knowledge summary method); a written summary needs a capable local model | **Done** (quoted) |
| Translation | Needs a translation model (several hundred MB per language pair) or an external service through the gate | Later |
| Voice-controlled browsing | Voice commands routed to the browser ("open …", "next tab", "read this page") | Planned (after the basics) |

**Privacy (built):**
- Chromium makes its own connections, not through AURORA's httpx gate. A
  request interceptor checks every request (the page and everything it
  loads) with the network gate: internet on / ask / off and the
  local-network switch. With "ask", the first page each session asks you.
  `file:` addresses are never opened, and only http/https from the
  address bar.
- Each host goes into System → Network activity the first time it is
  contacted in a session (host only, never the path or search), and the
  Sites tab lists every site the open page contacted, marking other sites
  (often adverts and trackers) and blocked ones.
- Sites can't use the camera, microphone, location, notifications or
  screen capture: every such request is refused. DNS prefetching and the
  spell-check dictionary download are off.
- Cookies, cache and site data stay in AURORA's own profile
  (`Data/browser/`, not in git or the OneDrive backup), separate from your
  other browsers, and can be cleared from the Sites tab.
- AURORA reads a page only when you press Ask, Reader or Save, and keeps
  its text only when you save it.
- Chromium starts only when you first open the Browser page.
- Limit: the interceptor sees requests, not every connection. A page that
  is allowed to load can ask Chromium to look up or open a connection to
  another site in advance ("preconnect"), and that can happen even if the
  site's requests are then blocked. No page data is requested from it,
  but the site can see the connection. DNS prefetching is off.

**On this laptop:**
- It needs `PySide6-Addons` and `PySide6-WebEngine` (about 165 MB of
  downloads, more installed, and a larger `.exe`).
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

## Browser milestones

**1. Basic browsing: done (0.37.0).** Tabs, address bar and search;
history and bookmarks (local and clearable); every host checked and
recorded through the interceptor; reader mode; Save to Knowledge;
questions answered by quoting the open page. Tested with a fake web
engine (`Tests/test_browser.py`, no Chromium, no network).

**2. Browser redesign: done (0.39.0).** Desktop-browser layout: navigation
bar with padlock, home, zoom, downloads and menu; tabs that can be pinned,
duplicated, restored and searched; sidebar (bookmarks, history, downloads,
reading list, saved pages); reader mode in the page area; full screen;
the AURORA panel hidden until opened (summary, information, compare
tabs, explain the selection in Chat). Not done: tab previews (tooltips
show title and address instead) and tab groups.

**3. Next:** questions about the open page from the chat ("what does this
page say about …"); comparing several open pages side by side (titles,
dates, headings, quoted sentences); tables, links and dates extracted;
reader mode and Ask for PDFs; a downloads list; voice commands for the
browser.
