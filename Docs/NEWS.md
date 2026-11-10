# AURORA Universal News Intelligence

A general-purpose news research system: any topic, any place, any date
range, searched **only when you ask**, with every story's sources, dates
and links, and checks on how well it is confirmed.

No search service or aggregator can reach every article published:
private, paywalled, unindexed, region-restricted and API-less sources are
missing. AURORA searches as broadly as its sources allow and every report
says what was and wasn't searched.

## Asking for news

In the chat (or the terminal):

- "Find today's major achievements around the world."
- "What are the latest reported murder cases in India?"
- "Find the latest developments in the investigation of the Pune bridge collapse."
- "Show me major scientific discoveries this month."
- "Compare today's international news from five publishers."
- "Find recent developments in artificial intelligence."
- "News about AI from BBC and The Hindu, not from NPR."
- "Investigate the story of the Chandrayaan landing since August 2026." (Investigation Mode)

Or in the **News Centre** page: search bar; category, place and date
filters (including custom dates); "only from" and "not from" publishers;
Search, Compare publishers and Investigate story; article cards; Save into
folders; a Saved articles tab.

AURORA reads from a request: the topic, the place, the dates ("today",
"this week", "last 3 days", "this month", "in June 2026", "since August
2026", "on 3 November"; a week if none is given, three months for an
investigation), publishers to use or leave out, and the mode (search,
compare, investigate). Categories are labels and a way to search for a
*kind* of news ("major achievements"); they never limit what can be
searched.

## Where the news comes from

| Method | Service | Needs | Coverage and limits |
|---|---|---|---|
| News index (API) | GDELT DOC 2.0 (`api.gdeltproject.org`) | nothing | Worldwide news sites, about the last 3 months, one request every 5 seconds (AURORA waits). Dates are when GDELT first saw the article. |
| News search (RSS) | Google News search (`news.google.com`) | nothing | Broad; links go through Google News, so the original address isn't known (the publisher and its site are). |
| Publisher feeds (RSS/Atom) | `NEWS_FEEDS` in settings: BBC, The Guardian, Al Jazeera, NPR, The Hindu, The Indian Express, UN News, WHO, NASA, ScienceDaily | nothing | Each feed's recent items only. Downloaded whole and **searched on this PC**: the publishers never see your search. Add or remove feeds in settings. |
| Web news search | Brave Search API (`api.search.brave.com`) | `NEWS_WEB_SEARCH_KEY` in `local_settings.py` (free key at brave.com/search/api) | Reaches smaller publishers and specialist sites. Off until you add a key. |

All four run in parallel for each request. Article pages themselves are
never downloaded, so paywalls, logins and robots rules are never in
question: AURORA shows each publisher's own headline and description and
links to the original.

## Checks on every article and story

| Check | What AURORA does |
|---|---|
| Publisher | From the feed, service or site; agency copies (PTI, ANI, IANS, Reuters, AP, AFP, UNI) are recognised. |
| Publication date | Recorded; a date in the future isn't used; items without a date, or outside the dates asked for, are left out and counted. |
| Event date | Taken from the text when it names one ("on Monday", "on 3 November"). |
| Duplicates | The same link from several services is one article (tracking parameters ignored). |
| Same story | Articles with closely matching headlines within a few days are one story. |
| Independent reporting | Publishers that reported it, with all copies of one agency's story counted once. "Only one source found so far: not independently confirmed" when that's all there is. |
| Official and primary sources | Government, court, UN/WHO, space agency, university and journal sites are marked. |
| Allegations and the legal process | For crime and court reports: under investigation, alleged, arrested, charged, on trial, convicted, acquitted. Every stage before a conviction is shown with "Not proven in court"; an arrest is "not a finding of guilt". |
| Corrections | "Correction", "clarification", "this article has been updated" are flagged. |
| Conflicting accounts | Different numbers for the same thing (killed, injured, arrested, missing…) across publishers are listed. |
| Graphic details | Left out of crime summaries (sentences describing injuries or remains); the facts stay. |

These checks are pattern-based. They show what each source says and who
said it; they never decide that a report is true.

## Investigation Mode

"Investigate …", "news investigation …", "timeline of …", or the News
Centre's Investigate button. Over three months by default (or the dates
you give) it shows:

1. the earliest report found (and that earlier ones may exist);
2. a chronological timeline: each day the story had new reports, who reported first;
3. official and primary sources;
4. how the legal process was reported, stage by stage, with the date each was first reported;
5. corrections and updates;
6. where accounts differ;
7. coverage: what was and wasn't searched.

## Privacy and control

- **Only when you ask.** Nothing searches the news in the background, on a
  timer, or as a scheduled task. "Every morning show me the news",
  "monitor news about …", "alert me…" get an explanation instead. A routine
  that includes a news search can be run by saying its name, but can't be
  scheduled (and an older schedule won't run it).
- **Only the search terms are sent**, the topic and place, never the
  conversation or anything else in your message. No contact details are
  sent (unlike Wikipedia, which requires them).
- **The network gate applies**: News may contact only GDELT, Google News,
  Brave (with a key) and the feeds you list. With internet access on
  "ask" (the default), the first search each session asks you first.
  System → Network activity lists every request.
- **Episodes** record "Searched the news", not the topic.
- **Saved articles** stay in `aurora_news.db` on this PC (not in git).
- **API key**: in `aurora/config/local_settings.py`, which git ignores.

## Not done yet

| Requirement | Status |
|---|---|
| Summaries, explanations and comparisons written by AURORA's local model | Not done: the summaries are the publishers' own descriptions and the comparisons are side-by-side headlines. AURORA-T0 can't write summaries yet, and TinyLlama's would add errors to news. To revisit with a stronger local model. |
| Query understanding and query generation by the local model | Rule-based instead (`aurora/news/query.py`), so it is predictable and works offline. |
| Original link for Google News results | Not available from Google News RSS; the same story from GDELT, a feed or web search shows the original link when found. |
| Updates and corrections beyond what the text says | Only flagged when the publisher's headline or description says so; pages aren't re-fetched. |
| Paywall and access detection | Not needed for what is fetched (feeds and search results); article pages aren't opened. |
| Languages other than English | Searches work, but the checks (legal stages, allegations, numbers) understand English only. |
| More news APIs (NewsAPI, GNews, The Guardian's API…) | Not added; each needs a key. The sources module takes more. |

## Development requirements (blueprint)

The requirements AURORA's news system was built against, with status.

1. **Coverage.** All major categories (achievements and awards; crime,
   murder and criminal investigations; politics and international affairs;
   science and research; space and astronomy; technology and AI; sports;
   business and economics; health and medicine; education; environment and
   climate; accidents and public safety; disasters and emergencies;
   entertainment, culture and human interest), and arbitrary topics.
   **Done.**
2. **Retrieval.** News APIs, RSS feeds, and web search, several sources per
   topic; respect API terms, access restrictions, robots policies and rate
   limits; no paywall bypassing. **Done** (web search with a key; GDELT's
   rate limit enforced; article pages never fetched).
3. **Search behaviour.** Only when explicitly asked; no scheduled searches
   or monitoring; global, country, regional and local searches; date ranges
   and historical searches; topic-specific and unrestricted queries;
   preferred and excluded publishers. **Done** (historical searches are
   limited by each source's own coverage, and the report says so).
4. **Article processing.** Headline, publisher, original URL, publication
   date, event date when identifiable, author when available, summary,
   category, location when available; duplicates removed; syndicated copies
   identified. **Done** (location: the publisher's country when the source
   gives it, and the place you searched).
5. **Verification.** Compare independent sources; prefer primary sources;
   distinguish facts from allegations; preserve uncertainty and conflicting
   accounts; identify corrections; never invent sources, dates or
   citations; never claim complete coverage. Crime: distinguish suspects,
   accused, defendants and convicted people; factual, non-graphic.
   **Done**, within what pattern-based checks can tell (see above).
6. **Local AI integration.** The local model understands requests,
   generates queries, summarises, compares, explains context, finds
   disagreements, writes structured reports with links; only search terms
   sent out. **Partly done**: structured reports, links, disagreements and
   minimal data sent are done without a model; model-written summaries and
   explanations wait for a capable local model.
7. **News Centre.** Search bar, category, country/region, date-range and
   source filters, article cards, dates, links, verification indicators,
   saving and organising. **Done.**
8. **Privacy and reliability.** No retrieval without a request; network
   requests recorded; credentials stored locally; connectivity
   configurable; inaccessible and unavailable sources identified; nothing
   fabricated for empty results. **Done.**
9. **News Investigation Mode.** Earliest report, later developments,
   publisher comparison, official statements, corrections, chronological
   timeline, final sourced report. **Done.**

## Code

| File | What it does |
|---|---|
| `aurora/news/query.py` | Reads a request: topic, place, dates, publishers, mode, category |
| `aurora/news/sources.py` | GDELT, Google News, publisher feeds, web search |
| `aurora/news/processing.py` | Annotation, duplicates, story grouping, independence, differences |
| `aurora/news/service.py` | Runs the sources in parallel; reports, comparisons, investigations |
| `aurora/news/report.py` | Report text with checks and coverage |
| `aurora/news/saved.py` | Saved articles and folders |
| `aurora/tool_plugins/news.py` | The News tool (chat) |
| `aurora/routing/news_rules.py` | Routing, including the refusal of background news |
| `aurora/interface/news_view.py` | The News Centre page |
| `Tests/test_news.py` | 63 tests, no network |
