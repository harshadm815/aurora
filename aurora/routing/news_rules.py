"""
Routing rule for news requests (the News tool).

    find today's major achievements around the world
    what are the latest reported murder cases in India?
    show me major scientific discoveries this month
    compare today's international news from five publishers
    investigate the Pune bridge collapse story
    latest news about artificial intelligence from BBC, not from NPR

The whole request goes to the News tool, which reads topic, place, dates
and publishers from it (aurora/news/query.py).
"""

import re

from aurora.routing import decision as targets
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent
from aurora.news.query import is_news_request

_EXPLICIT = re.compile(r"\b(?:news|headlines?|top\s+stories|breaking|investigate|news\s+investigation)\b", re.I)


# "every morning show me the news", "monitor the news about X", "alert me when …":
# AURORA doesn't search the news on its own, so these get an explanation.
_BACKGROUND = re.compile(
    r"^(?:please\s+)?(?:every|each)\s+\w+|\b(?:daily|hourly|weekly|every\s+(?:day|morning|evening|hour|week)|"
    r"automatically|in\s+the\s+background|keep\s+(?:me\s+)?(?:updated|posted|informed)|keep\s+watching|"
    r"monitor(?:ing)?|watch\s+(?:for|the)|notify\s+me|alert\s+me|subscribe|news\s+briefings?)\b", re.I)
_NEWSY = re.compile(r"\b(?:news|headlines?|stories|developments|reports?)\b", re.I)


def news_rule(original: str, body: str) -> RouteDecision | None:
    text = (body or original).strip()
    if _NEWSY.search(text) and _BACKGROUND.search(text) and not re.search(r"\bremind", text, re.I):
        return RouteDecision(Intent.NEWS, 0.99, targets.NEWS, {"request": text, "background": True},
                             "The user asks for news searches on a schedule, which AURORA doesn't do.")
    if not is_news_request(text):
        return None
    from aurora.routing import router
    if router._PAPERS.match(router._clean(text)) and not _EXPLICIT.search(text):
        return None              # "recent discoveries about exoplanets" has always meant papers (arXiv)
    confidence = 0.97 if _EXPLICIT.search(text) else 0.93
    return RouteDecision(Intent.NEWS, confidence, targets.NEWS, {"request": text},
                         "The user asks for news (searched only now, because they asked).")
