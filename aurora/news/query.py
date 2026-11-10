"""
Turning a news request into a search: topic, place, dates, publishers, mode.

    find today's major achievements around the world
    what are the latest reported murder cases in India?
    find the latest developments in the investigation into the Pune bridge collapse
    show me major scientific discoveries this month
    compare today's international news from five publishers
    news about artificial intelligence from BBC and The Hindu, not from NPR
    investigate the story of the Chandrayaan landing since August 2026

Only the topic and place become search terms; nothing else in the message
(and nothing from the conversation) is sent anywhere.
"""

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

SEARCH, COMPARE, INVESTIGATE = "search", "compare", "investigate"

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}
MONTHS.update({name[:3]: number for name, number in list(MONTHS.items())})
MONTHS["sept"] = 9

NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

# Categories AURORA labels articles with. They also turn a request that
# names only a kind of news ("major achievements") into search terms.
# They never limit what can be searched: any topic works.
CATEGORIES = {
    "achievements": (("achievement", "achievements", "award", "awards", "record", "records", "prize",
                      "accomplishment", "accomplishments", "milestone", "honoured", "honored", "wins", "won"),
                     "award OR record OR prize OR milestone"),
    "world affairs": (("international", "diplomacy", "diplomatic", "election", "elections", "government",
                       "minister", "president", "parliament", "summit", "treaty", "sanctions", "politics",
                       "political", "world affairs", "foreign"), "government OR election OR summit"),
    "crime and law": (("crime", "crimes", "murder", "murders", "killing", "homicide", "arrest", "arrested",
                       "police", "court", "trial", "verdict", "convicted", "accused", "investigation",
                       "fraud", "robbery", "theft", "kidnapping", "chargesheet", "charged", "sentenced",
                       "acquitted", "case", "cases"), "police OR court OR arrested"),
    "science and space": (("science", "scientific", "research", "researchers", "study", "discovery",
                           "discoveries", "astronomy", "space", "nasa", "isro", "telescope", "planet",
                           "mission", "physics", "biology", "fossil"), "scientists OR discovery OR study"),
    "technology": (("technology", "tech", "ai", "artificial intelligence", "robot", "robotics", "computing",
                    "software", "cybersecurity", "cyberattack", "chip", "semiconductor", "startup",
                    "smartphone", "internet"), "technology OR AI OR software"),
    "sports": (("sport", "sports", "cricket", "football", "tennis", "olympics", "match", "tournament",
                "championship", "league", "cup", "athlete", "medal"), "match OR tournament OR championship"),
    "business and economy": (("business", "economy", "economic", "market", "markets", "stocks", "shares",
                              "company", "companies", "inflation", "jobs", "employment", "gdp", "trade",
                              "bank", "rbi", "earnings"), "economy OR market OR company"),
    "environment": (("environment", "climate", "wildlife", "conservation", "pollution", "flood", "floods",
                     "earthquake", "cyclone", "drought", "wildfire", "heatwave", "emissions", "forest"),
                    "climate OR wildlife OR flood"),
    "health": (("health", "medicine", "medical", "disease", "hospital", "vaccine", "outbreak", "virus",
                "cancer", "doctors", "who"), "health OR disease OR hospital"),
    "education": (("education", "school", "schools", "university", "universities", "students", "exam",
                   "exams", "college"), "education OR university OR students"),
    "entertainment and culture": (("entertainment", "film", "films", "movie", "music", "culture", "art",
                                   "festival", "celebrity", "book", "books", "bollywood"),
                                  "film OR music OR festival"),
    "accidents and public safety": (("accident", "accidents", "crash", "collapse", "fire", "explosion",
                                     "derailment", "stampede", "safety"), "accident OR crash OR fire"),
    "disasters and emergencies": (("disaster", "disasters", "emergency", "emergencies", "evacuation",
                                   "tsunami", "landslide", "rescue"), "disaster OR emergency OR rescue"),
    "human interest": (("human interest", "human-interest", "heartwarming", "unusual", "odd", "quirky",
                        "inspiring", "positive"), "rescued OR reunited OR inspiring"),
}

# Words that name a whole category rather than a topic within it.
GENERIC = {
    "achievements": {"achievement", "achievements", "accomplishments", "awards", "records", "milestones"},
    "world affairs": {"world affairs", "politics", "foreign affairs"},
    "crime and law": {"crime", "crimes", "legal", "court cases"},
    "science and space": {"science", "space", "scientific discoveries", "discoveries", "research"},
    "technology": {"technology", "tech"},
    "sports": {"sport", "sports"},
    "business and economy": {"business", "economy", "economic", "markets"},
    "environment": {"environment", "environmental"},
    "health": {"health", "medical", "medicine"},
    "education": {"education"},
    "entertainment and culture": {"entertainment", "culture"},
    "accidents and public safety": {"accidents", "public safety"},
    "disasters and emergencies": {"disasters", "emergencies"},
    "human interest": {"human interest", "human-interest", "positive", "unusual", "inspiring"},
}
# World affairs as a kind of news means the day's top international stories.
NO_TERMS = {"world affairs"}

PLACES = {
    # Regions
    "world", "europe", "asia", "africa", "south asia", "southeast asia", "middle east", "latin america",
    "north america", "south america", "oceania", "the caribbean", "central asia", "east asia",
}

_REQUEST = re.compile(
    r"\b(?:news|headlines?|top\s+stories|breaking|newsroom|press\s+reports?)\b|"
    r"\b(?:latest|recent|new|today'?s|this\s+week'?s|this\s+month'?s|major|ongoing)\s+"
    r"(?:\w+\s+){0,3}(?:developments?|updates?|reports?|cases|stories|achievements|discoveries|events)\b|"
    r"\b(?:reported|reporting\s+on|coverage\s+of)\b|"
    r"\bwhat'?s\s+(?:happening|going\s+on)\s+(?:in|with|around)\b|"
    r"\binvestigat(?:e|ion\s+mode)\b.*\b(?:story|case|news|report|developments?)\b",
    re.I)
_NOT_NEWS = re.compile(r"\b(?:papers?|arxiv|my\s+(?:notes|documents|files|thesis)|weather|forecast|"
                       r"remind|reminder|timer)\b", re.I)
_INVESTIGATE = re.compile(r"^(?:please\s+)?(?:investigate|news\s+investigation(?:\s+mode)?(?:\s+(?:for|on))?|"
                          r"build\s+a\s+timeline\s+(?:of|for)|track\s+the\s+story\s+of|"
                          r"timeline\s+of)\b", re.I)
_COMPARE = re.compile(r"\bcompare\b", re.I)

_FILLER = re.compile(
    r"\b(?:please|can\s+you|could\s+you|would\s+you|tell\s+me|show\s+me|find(?:\s+me)?|get(?:\s+me)?|"
    r"search(?:\s+for)?|look\s+up|give\s+me|i\s+want|what\s+(?:are|is|were)|what'?s|whats|"
    r"are\s+there\s+any|any|the|a|an|latest|recent|newest|new|major|top|important|reported|reporting|"
    r"news|headlines?|stories|story|updates?|developments?|reports?|articles?|coverage|breaking|"
    r"happening|going\s+on|investigate|investigation\s+mode|news\s+investigation|timeline\s+of|"
    r"build\s+a\s+timeline\s+(?:of|for)|track|compare|ongoing|about|on|regarding|related\s+to|"
    r"concerning|of|in|into|around|across|for|from|me|some|today'?s|this\s+(?:week|month|year)'?s|"
    r"world'?s|international|global|worldwide|everywhere|and)\b",
    re.I)


@dataclass(frozen=True)
class NewsQuery:
    topic: str = ""                     # search terms ("" = top stories)
    place: str = ""                     # "" = anywhere
    since: date | None = None
    until: date | None = None           # inclusive
    category: str = ""                  # a CATEGORIES key, if the request named one
    publishers: tuple[str, ...] = ()    # only these (names or sites)
    exclude: tuple[str, ...] = ()       # never these
    mode: str = SEARCH
    compare_count: int = 0              # "from five publishers"
    period: str = ""                    # how the dates were asked for ("today", "this month")
    extra: dict = field(default_factory=dict, compare=False)

    def terms(self) -> str:
        """The words sent to search services: topic and place, nothing else."""
        topic = self.topic or (f"({CATEGORIES[self.category][1]})"
                               if self.category and self.category not in NO_TERMS else "")
        return " ".join(part for part in (topic, self.place) if part).strip()

    def describe(self) -> str:
        what = self.topic or (self.category if self.category else "top stories")
        where = f" in {self.place}" if self.place else " worldwide"
        return f"{what}{where}, {self.period or self.dates()}"

    def dates(self) -> str:
        if self.since and self.until and self.since == self.until:
            return f"{self.since:%d %b %Y}"
        if self.since and self.until:
            return f"{self.since:%d %b %Y} to {self.until:%d %b %Y}"
        if self.since:
            return f"since {self.since:%d %b %Y}"
        return "any date"

    def with_dates(self, since, until, period="") -> "NewsQuery":
        return replace(self, since=since, until=until, period=period)


_COMMAND = re.compile(r"^\s*(?:please\s+)?(?:remember|forget|don'?t\s+remember|note\s+that|remind|"
                      r"stop|undo|every\s+\w+|schedule|when\s+i\s+say)\b", re.I)


def is_news_request(text: str) -> bool:
    text = text or ""
    if _NOT_NEWS.search(text) or _COMMAND.search(text):
        return False
    return bool(_REQUEST.search(text) or _INVESTIGATE.search(text.strip()))


# --- Dates ---------------------------------------------------------------------

def _date_phrase(text: str, today: date):
    """Returns (since, until, period, matched span) for the first date phrase, or None."""
    lowered = text.lower()
    simple = [
        (r"\btoday'?s?\b|\bthis\s+morning\b|\btonight\b", today, today, "today"),
        (r"\byesterday'?s?\b", today - timedelta(days=1), today - timedelta(days=1), "yesterday"),
        (r"\bthis\s+week'?s?\b", today - timedelta(days=today.weekday()), today, "this week"),
        (r"\b(?:last|past)\s+week'?s?\b", today - timedelta(days=7), today, "the past week"),
        (r"\bthis\s+month'?s?\b", today.replace(day=1), today, "this month"),
        (r"\bthis\s+year'?s?\b", today.replace(month=1, day=1), today, "this year"),
    ]
    found = []
    for pattern, since, until, period in simple:
        match = re.search(pattern, lowered)
        if match:
            found.append((match.start(), (since, until, period, match.span())))
    match = re.search(r"\blast\s+month'?s?\b", lowered)
    if match:
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        found.append((match.start(), (end.replace(day=1), end, "last month", match.span())))
    match = re.search(r"\b(?:last|past)\s+(\d{1,3}|" + "|".join(NUMBERS) + r")\s+(day|week|month|year)s?\b", lowered)
    if match:
        count = int(match.group(1)) if match.group(1).isdigit() else NUMBERS[match.group(1)]
        days = {"day": 1, "week": 7, "month": 30, "year": 365}[match.group(2)] * count
        found.append((match.start(), (today - timedelta(days=days), today,
                                      f"the past {count} {match.group(2)}{'s' if count > 1 else ''}",
                                      match.span())))
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    match = re.search(rf"\bsince\s+(?:(\d{{1,2}})\s+)?({month_names})\.?\s+(\d{{4}})\b", lowered)
    if match:
        since = date(int(match.group(3)), MONTHS[match.group(2)], int(match.group(1) or 1))
        found.append((match.start(), (since, today, f"since {since:%d %b %Y}", match.span())))
    else:
        match = re.search(r"\bsince\s+(\d{4})\b", lowered)
        if match:
            since = date(int(match.group(1)), 1, 1)
            found.append((match.start(), (since, today, f"since {match.group(1)}", match.span())))
    match = re.search(rf"\b(?:in|during|from)\s+({month_names})\.?\s+(\d{{4}})\b", lowered)
    if match:
        year, month = int(match.group(2)), MONTHS[match.group(1)]
        first = date(year, month, 1)
        last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
        found.append((match.start(), (first, min(last, today), f"{first:%B %Y}", match.span())))
    elif (match := re.search(r"\b(?:in|during)\s+((?:19|20)\d{2})\b", lowered)):
        year = int(match.group(1))
        found.append((match.start(), (date(year, 1, 1), min(date(year, 12, 31), today), str(year),
                                      match.span())))
    match = re.search(rf"\bon\s+(\d{{1,2}})\s+({month_names})\.?(?:\s+(\d{{4}}))?\b", lowered)
    if match:
        day = date(int(match.group(3) or today.year), MONTHS[match.group(2)], int(match.group(1)))
        found.append((match.start(), (day, day, f"{day:%d %b %Y}", match.span())))
    if not found:
        return None
    return sorted(found)[0][1]


# --- Publishers ----------------------------------------------------------------

_NAME = r"(?:the\s+)?[A-Z][\w.&'-]*(?:\s+(?:of\s+)?[A-Z][\w.&'-]*)*"
_EXCLUDE = re.compile(rf"\b(?:not\s+from|excluding|except(?:\s+for)?|but\s+not|without)\s+"
                      rf"(?P<names>{_NAME}(?:\s*(?:,|and|or)\s*{_NAME})*)")
_ONLY = re.compile(rf"\b(?:only\s+from|from|by|according\s+to|in|on)\s+"
                   rf"(?P<names>{_NAME}(?:\s*(?:,|and|or)\s*{_NAME})*)")
_COUNT = re.compile(r"\bfrom\s+(\d{1,2}|" + "|".join(NUMBERS) + r")\s+(?:different\s+)?(?:publishers|sources|outlets|newspapers)\b", re.I)


def _names(text: str) -> list[str]:
    parts = re.split(r"\s*(?:,|\band\b|\bor\b)\s*", text)
    return [re.sub(r"^the\s+", "", p.strip(), flags=re.I) for p in parts if p.strip()]


def known_publisher(name: str, feeds) -> bool:
    lowered = name.lower()
    known = {"bbc", "reuters", "ap", "associated press", "afp", "pti", "ani", "ndtv", "cnn", "guardian",
             "the guardian", "hindu", "the hindu", "times of india", "indian express", "the indian express",
             "al jazeera", "npr", "new york times", "washington post", "hindustan times", "economic times",
             "mint", "livemint", "scroll", "the wire", "bloomberg", "financial times", "sky news",
             "deutsche welle", "dw", "france 24", "abc news", "cbs news", "nbc news", "fox news", "nature",
             "science", "un news", "who", "nasa"}
    names = {re.sub(r"^the\s+", "", f.get("name", "").lower()) for f in feeds}
    return lowered in known or re.sub(r"^the\s+", "", lowered) in names


# --- Places --------------------------------------------------------------------

_PLACE = re.compile(r"\b(?:in|across|around|from|within|at)\s+(?P<place>(?:the\s+)?[A-Z][a-zA-Z.'-]+"
                    r"(?:\s+[A-Z][a-zA-Z.'-]+){0,3})")
_WORLD = re.compile(r"\b(?:around\s+the\s+world|worldwide|world-?wide|globally|across\s+the\s+(?:world|globe)|"
                    r"international|global|world'?s|everywhere)\b", re.I)


def _place(text: str, publishers: list[str]) -> tuple[str, tuple[int, int] | None]:
    for match in _PLACE.finditer(text):
        place = match.group("place").strip()
        bare = re.sub(r"^the\s+", "", place, flags=re.I)
        if bare in publishers or bare.lower() in ("world", "globe"):
            continue
        if bare.isupper() and len(bare) <= 4 and bare.lower() not in ("uk", "us", "usa", "uae"):
            continue          # "in AI", "in NASA": a topic, not a place
        if bare.lower() in MONTHS:
            continue
        return bare, match.span()
    for region in sorted(PLACES, key=len, reverse=True):
        match = re.search(rf"\bin\s+{region}\b", text, re.I)
        if match and region != "world":
            return region.title(), match.span()
    return "", None


# --- The parser ------------------------------------------------------------------

def parse(text: str, today: date | None = None, feeds=()) -> NewsQuery:
    """Reads a news request. Doesn't decide whether it *is* one (is_news_request)."""
    today = today or datetime.now().date()
    original = (text or "").strip()
    working = re.sub(r"[?!.]+$", "", original)
    mode = INVESTIGATE if _INVESTIGATE.search(working) else (COMPARE if _COMPARE.search(working) else SEARCH)

    count = 0
    match = _COUNT.search(working)
    if match:
        value = match.group(1).lower()
        count = int(value) if value.isdigit() else NUMBERS[value]
        working = working[:match.start()] + " " + working[match.end():]
        mode = COMPARE

    exclude = []
    for match in list(_EXCLUDE.finditer(working)):
        exclude += _names(match.group("names"))
    working = _EXCLUDE.sub(" ", working)

    publishers = []
    for match in list(_ONLY.finditer(working)):
        names = [n for n in _names(match.group("names")) if known_publisher(n, feeds)]
        if names and len(names) == len(_names(match.group("names"))):
            publishers += names
            working = working.replace(match.group(0), " ")

    dates = _date_phrase(working, today)
    since = until = None
    period = ""
    if dates:
        since, until, period, span = dates
        working = working[:span[0]] + " " + working[span[1]:]
        # "since August 2026": the matched phrase may be preceded by its preposition
    working = re.sub(r"\b(?:since|during|on)\s*$", " ", working)

    world = bool(_WORLD.search(working))
    working = _WORLD.sub(" ", working)
    place, span = ("", None) if world else _place(working, publishers)
    if span:
        working = working[:span[0]] + " " + working[span[1]:]

    lowered = working.lower()
    category = ""
    for name, (words, _) in CATEGORIES.items():
        if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words):
            category = name
            break

    topic = _FILLER.sub(" ", working)
    topic = re.sub(r"[^\w\s'&-]", " ", topic)
    topic = " ".join(w for w in topic.split() if w.lower() not in ("s", "'s"))
    # A request naming only a kind of news ("major achievements", "crime news")
    # searches for that kind; a specific topic ("artificial intelligence") is kept.
    if category and topic.lower() in GENERIC.get(category, set()) | {category, ""}:
        topic = ""
    if mode == INVESTIGATE and since is None:
        since, until, period = today - timedelta(days=90), today, "the past 3 months"
    if since is None:
        since, until, period = today - timedelta(days=7), today, "the past week"
    return NewsQuery(topic=topic, place=place, since=since, until=until, category=category,
                     publishers=tuple(publishers), exclude=tuple(exclude), mode=mode,
                     compare_count=count or (5 if mode == COMPARE else 0), period=period)
