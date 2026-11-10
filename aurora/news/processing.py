"""
From raw articles to checked stories.

For every article: a canonical link, a validated date, the event date when
the text names one, a category, what a crime report says about the legal
process, whether it reports allegations, whether it was corrected, whether
it is an agency copy, and whether the source is official.

Then duplicates are merged (the same link from several services), and
articles about the same event are grouped into a Story, so AURORA can say
how many *independent* publishers reported it and where their accounts
differ (different numbers of people killed, injured, arrested…).

All of this is pattern-based and shown as such. It never decides that a
report is true; it shows what each source says and who said it.
"""

import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aurora.news.article import LEGAL_STAGES, Article, Story
from aurora.news.query import CATEGORIES, NewsQuery

_STOP = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "is", "are", "was", "were", "be",
         "by", "with", "as", "from", "after", "over", "into", "its", "it", "this", "that", "has", "have", "had",
         "will", "says", "said", "say", "new", "how", "why", "what", "who", "live", "updates", "update", "news"}
_TRACKING = re.compile(r"^(?:utm_\w+|fbclid|gclid|ref|cmpid|ito|at_\w+|ocid|mc_\w+|s_cid|src)$", re.I)

OFFICIAL_SITES = re.compile(
    r"(?:\.gov|\.gov\.\w\w|\.gouv\.\w\w|\.gc\.ca|\.nic\.in|\.mil|\.int|\.edu|\.ac\.\w\w|\.edu\.\w\w)$|"
    r"^(?:who\.int|un\.org|news\.un\.org|nasa\.gov|isro\.gov\.in|esa\.int|pib\.gov\.in|sci\.gov\.in|"
    r"supremecourt\.gov|nature\.com|science\.org|thelancet\.com|nejm\.org|arxiv\.org|cell\.com|"
    r"europa\.eu|ec\.europa\.eu|imf\.org|worldbank\.org|wto\.org|icj-cij\.org|interpol\.int)$")

AGENCIES = {
    "PTI": r"\bPTI\b|Press Trust of India", "ANI": r"\bANI\b", "IANS": r"\bIANS\b", "Reuters": r"\bReuters\b",
    "AP": r"\(AP\)|\bAssociated Press\b", "AFP": r"\bAFP\b|Agence France-Presse", "UNI": r"\bUNI\b",
}

# Furthest stage wins within one article; acquittal and conviction are checked first.
_LEGAL = [
    ("acquitted", r"\bacquit(?:ted|tal)\b|\bfound not guilty\b|\bcleared of (?:all )?charges\b|\bexonerated\b"),
    ("convicted", r"\bconvicted\b|\bfound guilty\b|\bsentenced\b|\bpleaded guilty\b|\bpled guilty\b|"
                  r"\bawarded (?:life|death) sentence\b|\bjailed for\b"),
    ("on trial", r"\bon trial\b|\btrial (?:begins|began|opens|opened|resumes)\b|\bstands trial\b|"
                 r"\btestified\b|\bwitness(?:es)? (?:told|testif)"),
    ("charged", r"\bcharged\b|\bcharge-?sheet(?:ed)?\b|\bindicted\b|\bbooked (?:for|under)\b|"
                r"\bcharges (?:filed|framed)\b|\bFIR (?:filed|registered|lodged)\b"),
    ("arrested", r"\barrest(?:ed|s)?\b|\bdetained\b|\bnabbed\b|\bheld for\b|\bin custody\b|\btaken into custody\b"),
    ("alleged", r"\balleged(?:ly)?\b|\baccused\b|\bsuspect(?:ed|s)?\b|\bclaims?\b|\bclaimed\b"),
    ("under investigation", r"\binvestigat(?:ion|ing|ors?)\b|\bprobe\b|\bprobing\b|\binquiry\b|\bpolice said\b"),
]
_CRIMINAL = re.compile(r"\b(?:murder|kill(?:ed|ing)?|homicide|crime|criminal|police|court|arrest|rape|assault|"
                       r"fraud|scam|theft|robbery|kidnap|abduct|smuggl|accused|suspect|trial|verdict|"
                       r"charge|jail|prison|custody|FIR|probe)", re.I)
_ALLEGATION = re.compile(r"\b(?:alleged(?:ly)?|accused|accuses|accusation|claims?|claimed|reportedly|purportedly|"
                         r"suspected|according to (?:police|sources|officials))\b", re.I)
_CORRECTION = re.compile(r"\b(?:correction|corrected|clarification|editor'?s note|this (?:article|story) "
                         r"(?:has been|was) (?:updated|amended|corrected)|updated:)", re.I)

# Sentences with these are left out of crime summaries: the facts stay, the details don't.
_GRAPHIC = re.compile(r"\b(?:dismember\w*|mutilat\w*|decapitat\w*|behead\w*|gory|gruesome|grisly|"
                      r"blood-?soaked|pool of blood|body parts|chopped|hacked to death|burnt alive|"
                      r"burned alive|stabbed \d+ times|disembowel\w*|charred bod\w*|severed)\b", re.I)

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_MONTH = r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|" \
         r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY_MONTH = re.compile(rf"\b(?:on\s+)?(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH}\b", re.I)
_MONTH_DAY = re.compile(rf"\b(?:on\s+)?{_MONTH}\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
_ON_WEEKDAY = re.compile(r"\bon\s+(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)

_COUNTS = re.compile(r"\b(?P<n>\d{1,6}|one|two|three|four|five|six|seven|eight|nine|ten|dozens|hundreds)\s+"
                     r"(?:people\s+|persons\s+|men\s+|women\s+|children\s+|workers\s+|passengers\s+)?"
                     r"(?:were\s+|have\s+been\s+|are\s+)?"
                     r"(?P<what>killed|dead|died|injured|hurt|missing|arrested|rescued|evacuated)\b", re.I)
_WORD_NUMBERS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                 "eight": "8", "nine": "9", "ten": "10"}
_WHAT = {"dead": "killed", "died": "killed", "hurt": "injured"}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP and len(w) > 1}


def _event_date(text: str, published: datetime | None) -> datetime | None:
    if published is None:
        return None
    base = published.date()
    for pattern in (_DAY_MONTH, _MONTH_DAY):
        match = pattern.search(text)
        if match:
            month = next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(match.group("month").lower()[:3]))
            day = int(match.group("day"))
            try:
                found = date(base.year, month, day)
            except ValueError:
                continue
            if found > base:
                try:
                    found = date(base.year - 1, month, day)
                except ValueError:
                    continue
            if base - found <= timedelta(days=366):
                return datetime.combine(found, datetime.min.time())
    match = _ON_WEEKDAY.search(text)
    if match:
        back = (base.weekday() - _WEEKDAYS.index(match.group("weekday").lower())) % 7
        return datetime.combine(base - timedelta(days=back), datetime.min.time())
    return None


def category_of(text: str) -> str:
    lowered = (text or "").lower()
    scores = {}
    for name, (keywords, _) in CATEGORIES.items():
        score = sum(1 for word in keywords if re.search(rf"\b{re.escape(word)}\b", lowered))
        if score:
            scores[name] = score
    return max(scores, key=scores.get) if scores else ""


def legal_stage(text: str) -> str:
    if not _CRIMINAL.search(text or ""):
        return ""
    found = [stage for stage, pattern in _LEGAL if re.search(pattern, text, re.I)]
    if not found:
        return ""
    if "acquitted" in found:
        return "acquitted"
    return max(found, key=LEGAL_STAGES.index)


def non_graphic(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text or "")
    kept = [s for s in sentences if not _GRAPHIC.search(s)]
    return " ".join(kept).strip()


def annotate(article: Article, now: datetime | None = None) -> Article:
    now = now or datetime.now()
    text = f"{article.title}. {article.summary}"
    if article.published and article.published > now + timedelta(days=1):
        article.date_note = "the date given is in the future, so it isn't used"
        article.published = None
    article.domain = article.domain or (urlsplit(article.url).hostname or "")
    if article.domain.startswith("www."):
        article.domain = article.domain[4:]
    article.official = article.official or bool(OFFICIAL_SITES.search(article.domain))
    article.category = article.category or category_of(text)
    article.legal_stage = legal_stage(text)
    article.allegation = bool(_ALLEGATION.search(text))
    article.correction = bool(_CORRECTION.search(text))
    for agency, pattern in AGENCIES.items():
        if re.search(pattern, article.summary or "") or re.search(rf"^{pattern}\b|\({pattern}\)", article.title):
            article.agency = agency
            break
    article.event_date = _event_date(text, article.published)
    if article.legal_stage or article.category == "crime and law" or _GRAPHIC.search(text):
        article.summary = non_graphic(article.summary)
        if _GRAPHIC.search(article.title):
            article.title = non_graphic(article.title) or "(headline withheld: graphic details)"
    return article


def _matches_publisher(article: Article, names) -> bool:
    for name in names:
        lowered = name.lower().removeprefix("the ")
        if lowered in article.publisher.lower() or lowered.replace(" ", "") in article.domain.replace(".", ""):
            return True
    return False


def matches(article: Article, query: NewsQuery) -> bool:
    """For publisher feeds, which aren't searched by the publisher: does the text match?"""
    text = f"{article.title} {article.summary}".lower()
    terms = query.terms()
    if not terms:
        return True
    groups = re.findall(r"\(([^)]*)\)", terms)
    rest = re.sub(r"\([^)]*\)", " ", terms)
    for group in groups:                    # (a OR b OR c): any of them
        options = [w.strip().lower() for w in group.split(" OR ") if w.strip()]
        if not any(_has(text, option) for option in options):
            return False
    for word in rest.split():
        if word.upper() != "OR" and not _has(text, word.lower()):
            return False
    return True


def _has(text: str, word: str) -> bool:
    word = word.strip('"')
    if len(word) <= 3:
        return bool(re.search(rf"\b{re.escape(word)}\b", text))
    stem = word[:-1] if word.endswith("s") else word
    return stem[: max(4, len(stem) - 2)] in text


def select(articles: list[Article], query: NewsQuery) -> list[Article]:
    """Publisher choices from the request."""
    kept = articles
    if query.publishers:
        kept = [a for a in kept if _matches_publisher(a, query.publishers)]
    if query.exclude:
        kept = [a for a in kept if not _matches_publisher(a, query.exclude)]
    return kept


def merge_duplicates(articles: list[Article]) -> tuple[list[Article], int]:
    """One article per link; a link returned by several services remembers them."""
    by_url: dict[str, Article] = {}
    for article in articles:
        key = canonical_url(article.url)
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = article
            continue
        if article.method not in existing.also_via and article.method != existing.method:
            existing.also_via.append(article.method)
        existing.summary = existing.summary or article.summary
        existing.author = existing.author or article.author
        if existing.published is None:
            existing.published, existing.date_note = article.published, article.date_note
    return list(by_url.values()), len(articles) - len(by_url)


def _similar(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    shared = len(a & b)
    # At least three distinctive words in common, and most of the shorter headline.
    return shared >= 3 and (shared / len(a | b) >= 0.4 or shared / min(len(a), len(b)) >= 0.75)


def group_stories(articles: list[Article]) -> list[Story]:
    """Articles about the same event (similar headlines, within a few days) become one Story."""
    stories: list[tuple[set[str], Story]] = []
    for article in sorted(articles, key=lambda a: a.published or datetime.min, reverse=True):
        tokens = words(article.title)
        home = None
        for story_tokens, story in stories:
            close = (article.published is None or story.latest is None
                     or abs((story.latest - article.published).days) <= 4)
            if close and _similar(tokens, story_tokens):
                home = story
                story_tokens |= tokens
                break
        if home is None:
            stories.append((set(tokens), Story([article])))
        else:
            home.articles.append(article)
    for _, story in stories:
        story.differences = differences(story)
    return [story for _, story in stories]


def differences(story: Story) -> list[str]:
    """Where publishers give different numbers for the same thing."""
    reported: dict[str, dict[str, list[str]]] = {}
    for article in story.articles:
        for match in _COUNTS.finditer(f"{article.title}. {article.summary}"):
            number = _WORD_NUMBERS.get(match.group("n").lower(), match.group("n").lower())
            what = _WHAT.get(match.group("what").lower(), match.group("what").lower())
            reported.setdefault(what, {}).setdefault(number, [])
            if article.publisher not in reported[what][number]:
                reported[what][number].append(article.publisher)
    notes = []
    for what, numbers in reported.items():
        if len(numbers) > 1:
            parts = [f"{n} ({', '.join(p[:2])})" for n, p in numbers.items()]
            notes.append(f"number {what}: " + " vs ".join(parts))
    return notes


def rank(stories: list[Story]) -> list[Story]:
    """Most independently reported first, then newest."""
    return sorted(stories, key=lambda s: (len(s.independent) + (1 if s.official else 0),
                                          s.latest or datetime.min), reverse=True)
