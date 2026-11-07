"""
Routing rules for memory control, episodes, routines and provenance.

    don't remember that · stop learning · start learning again
    stop recording activity · start recording activity · clear my activity history
    what did we do today? · when did I last check the weather? · show my activity
    when I say morning briefing, check the weather in Pune and list my reminders
    create a routine called focus: set a timer for 25 minutes
    what routines do you know? · delete the routine morning briefing
    how do you know where I live? · why do you think I like tea?
"""

import re

from aurora.routing import decision as targets
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent

_TRAIL = re.compile(r"[\s?.!]+$")


def _clean(value: str) -> str:
    return _TRAIL.sub("", value or "").strip()


# --- Memory control ---------------------------------------------------------

_CONTROL = [
    ("undo", re.compile(
        r"^(?:please\s+)?(?:don'?t|do\s+not)\s+(?:remember|save|store|keep)\s+(?:that|this|it)\b"
        r"|^(?:undo|take\s+back|cancel)\s+(?:that|the\s+last|your\s+last)\s+memor(?:y|ies)\b"
        r"|^you\s+(?:shouldn'?t|should\s+not)\s+(?:remember|save)\s+that\b"
        r"|^(?:that's|that\s+is)\s+(?:wrong|not\s+true|not\s+right)\s*,?\s*(?:forget|don'?t\s+remember)\s+it\b", re.I)),
    ("pause", re.compile(
        r"^(?:please\s+)?(?:stop|pause)\s+(?:learning|remembering\s+things|saving\s+memories|"
        r"(?:automatic|auto)\s+memory|learning\s+about\s+me)\b"
        r"|^(?:don'?t|do\s+not)\s+(?:remember|learn)\s+anything\s+(?:new|else|more|from\s+now\s+on)\b", re.I)),
    ("resume", re.compile(
        r"^(?:please\s+)?(?:start|resume|restart)\s+(?:learning|remembering\s+things|(?:automatic|auto)\s+memory)"
        r"(?:\s+again)?\b|^you\s+can\s+(?:start\s+)?(?:remember(?:ing)?|learn(?:ing)?)\s+(?:things\s+)?again\b", re.I)),
    ("episodes_off", re.compile(
        r"^(?:please\s+)?(?:stop|pause)\s+(?:recording|keeping|saving|tracking)\s+(?:my\s+|our\s+)?"
        r"(?:activity|activities|episodes|history)\b", re.I)),
    ("episodes_on", re.compile(
        r"^(?:please\s+)?(?:start|resume)\s+(?:recording|keeping|saving|tracking)\s+(?:my\s+|our\s+)?"
        r"(?:activity|activities|episodes|history)(?:\s+again)?\b", re.I)),
    ("clear_episodes", re.compile(
        r"^(?:please\s+)?(?:clear|delete|erase|wipe|forget)\s+(?:all\s+)?(?:of\s+)?(?:my\s+|our\s+)?"
        r"(?:activity(?:\s+history)?|activities|episodes|episode\s+history)\b", re.I)),
]


def memory_control_rule(original: str, body: str) -> RouteDecision | None:
    text = _clean(body)
    for action, pattern in _CONTROL:
        if pattern.match(text):
            return RouteDecision(Intent.MEMORY_CONTROL, 0.97, targets.MEMORY_CONTROL, {"action": action},
                                 "The user is controlling what AURORA remembers.")
    return None


# --- Provenance ("how do you know ...") -------------------------------------

_PROVENANCE = re.compile(
    r"^(?:how|why)\s+(?:do|did)\s+you\s+(?:know|think|believe|remember)\s+(?:that\s+)?(?P<query>.+)$"
    r"|^where\s+did\s+you\s+(?:learn|get|hear)\s+(?:that\s+)?(?P<q2>.+)$"
    r"|^who\s+told\s+you\s+(?:that\s+)?(?P<q3>.+)$", re.I)


def provenance_rule(original: str, body: str) -> RouteDecision | None:
    match = _PROVENANCE.match(_clean(body))
    if not match:
        return None
    query = match.group("query") or match.group("q2") or match.group("q3")
    return RouteDecision(Intent.RECALL, 0.9, targets.MEMORY, {"query": _clean(query), "mode": "provenance"},
                         "The user asks how AURORA knows something.")


# --- Episodes ---------------------------------------------------------------

_PERIOD = r"(?P<period>today|yesterday|this\s+week|last\s+week|recently|so\s+far(?:\s+today)?|lately)"
_EPISODES = [
    re.compile(rf"^what\s+(?:did|have)\s+(?:we|i|you)\s+(?:do|done|been\s+doing|ask(?:ed)?(?:\s+you)?|"
               rf"talk(?:ed)?\s+about|work(?:ed)?\s+on)(?:\s+together)?(?:\s+{_PERIOD})?$", re.I),
    re.compile(rf"^(?:show|list|tell\s+me)\s+(?:me\s+)?(?:my\s+|our\s+)?(?:recent\s+)?"
               rf"(?:activity|activities|activity\s+history|history|episodes)(?:\s+(?:for\s+)?{_PERIOD})?$", re.I),
    re.compile(r"^when\s+did\s+(?:i|we)\s+last\s+(?P<what>.+)$", re.I),
    re.compile(rf"^(?:have|did)\s+(?:i|we)\s+(?:already\s+)?(?:ask(?:ed)?\s+(?:you\s+)?(?:about\s+|for\s+)?|"
               rf"check(?:ed)?|look(?:ed)?\s+up|calculate[d]?)\s+(?P<what>.+?)(?:\s+{_PERIOD})?$", re.I),
]


def episodes_rule(original: str, body: str) -> RouteDecision | None:
    text = _clean(body)
    for number, pattern in enumerate(_EPISODES):
        match = pattern.match(text)
        if not match:
            continue
        groups = match.groupdict()
        arguments = {}
        if groups.get("period"):
            arguments["period"] = " ".join(groups["period"].lower().split())
        if groups.get("what"):
            arguments["what"] = groups["what"]
            arguments["latest"] = number == 2
        return RouteDecision(Intent.EPISODES, 0.95, targets.EPISODES, arguments,
                             "The user asks what AURORA has done for them.")
    return None


# --- Routines ---------------------------------------------------------------

_Q = r"[\"“”'‘’]?"
_CREATE = [
    re.compile(rf"^when(?:ever)?\s+i\s+say\s+{_Q}(?P<name>[^\"“”,:]{{2,40}}?){_Q}\s*[,:]\s*"
               rf"(?:please\s+)?(?:you\s+should\s+|do\s+this\s*:?\s*|i\s+want\s+you\s+to\s+)?(?P<steps>.+)$", re.I),
    re.compile(rf"^(?:create|make|add|save|set\s+up|teach\s+you)\s+(?:a\s+|the\s+)?(?:new\s+)?routine\s+"
               rf"(?:called|named)\s+{_Q}(?P<name>[^\"“”,:]{{2,40}}?){_Q}\s*[,:-]\s*(?P<steps>.+)$", re.I),
]
_LIST = re.compile(r"^(?:what|which)\s+routines\s+(?:do\s+you\s+know|have\s+i\s+(?:taught\s+you|made|set\s+up)|"
                   r"are\s+there|do\s+i\s+have)|^(?:list|show)\s+(?:me\s+)?(?:my\s+|all\s+)?routines$", re.I)
_DELETE = re.compile(rf"^(?:delete|remove|forget)\s+(?:the\s+|my\s+)?routine\s+(?:called\s+|named\s+)?"
                     rf"{_Q}(?P<name>.+?){_Q}$", re.I)


def routines_rule(original: str, body: str) -> RouteDecision | None:
    text = _clean(body)
    for pattern in _CREATE:
        match = pattern.match(text)
        if match:
            return RouteDecision(Intent.ROUTINES, 0.97, targets.ROUTINES,
                                 {"action": "create", "name": match.group("name").strip(),
                                  "steps": match.group("steps").strip()},
                                 "The user is teaching AURORA a routine.")
    match = _DELETE.match(text)
    if match:
        return RouteDecision(Intent.ROUTINES, 0.97, targets.ROUTINES,
                             {"action": "delete", "name": match.group("name").strip()},
                             "The user wants a routine deleted.")
    if _LIST.match(text):
        return RouteDecision(Intent.ROUTINES, 0.95, targets.ROUTINES, {"action": "list"},
                             "The user asks which routines AURORA knows.")
    return None


# --- Helpers for the existing remember / forget rules -----------------------

# "forget my activity history" and "forget the routine X" belong to the rules above.
NOT_A_MEMORY_QUERY = re.compile(
    r"^(?:the\s+|my\s+)?routine\b|^(?:all\s+)?(?:of\s+)?(?:my\s+|our\s+)?(?:activity|activities|episodes)\b", re.I)

# "remember for today that ...", "... for 3 days"
KEEP_FOR = re.compile(
    r"^(?:for|until)\s+(?P<span>today|tonight|tomorrow|this\s+week|the\s+end\s+of\s+the\s+week|"
    r"(?:\d+|an?|one|two|three|four|five|six|seven)\s+(?:hours?|days?|weeks?))\s*(?:,\s*)?(?:that\s+)?"
    r"|\s+(?:for|until)\s+(?P<span2>today|tonight|tomorrow|this\s+week|the\s+end\s+of\s+the\s+week|"
    r"(?:\d+|an?|one|two|three|four|five|six|seven)\s+(?:hours?|days?|weeks?))\s*$", re.I)

ALWAYS = re.compile(r"^(?:always|permanently|never\s+forget(?:\s+that)?\s*:?|keep\s+forever)\s*"
                    r"(?:remember\s+)?(?:that\s+)?(?P<content>.+)$", re.I)
