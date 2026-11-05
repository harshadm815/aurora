"""
Rule-based intent router.

Each rule looks at a message and either returns a scored RouteDecision
or ``None``. The router collects every candidate, adds a default
"conversation" candidate, and picks the best one. If the two best tool
candidates are nearly tied, it asks for clarification instead of
guessing.

Rules are deliberately conservative: a message only goes to a tool
when it clearly asks for one. Everything else goes to the Brain.
"""

import logging
import re
from collections.abc import Callable

from aurora.config import settings
from aurora.routing import decision as targets
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent
from aurora.tool_plugins.calculator import CalculationError, evaluate

logger = logging.getLogger(__name__)

Rule = Callable[[str, str], RouteDecision | None]

# --- Shared text helpers ----------------------------------------------------

# Polite / wake-word prefixes that don't change what is being asked.
_PREFIX = re.compile(
    r"^(?:(?:hey|ok|okay|hi)\s+)?(?:aurora[\s,:]+)?"
    r"(?:please\s+)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?",
    re.IGNORECASE,
)

_TRAILING = re.compile(r"[\s?.!]+$")


def _strip_prefix(text: str) -> str:
    return _PREFIX.sub("", text, count=1).strip()


def _clean(value: str) -> str:
    return _TRAILING.sub("", value).strip()


# --- Calculator -------------------------------------------------------------

_WORD_OPERATORS = [
    (re.compile(r"\bto the power of\b", re.I), "**"),
    (re.compile(r"\bmultiplied by\b", re.I), "*"),
    (re.compile(r"\bdivided by\b", re.I), "/"),
    (re.compile(r"\btimes\b", re.I), "*"),
    (re.compile(r"\bplus\b", re.I), "+"),
    (re.compile(r"\bminus\b", re.I), "-"),
    (re.compile(r"\bmod(?:ulo)?\b", re.I), "%"),
    (re.compile(r"(?<=\d)\s*[x×]\s*(?=[\d(])", re.I), "*"),
    (re.compile(r"÷"), "/"),
    (re.compile(r"\^"), "**"),  # People write 2^3 for powers (Python's ^ is XOR).
]

_CALC_LEAD = re.compile(
    r"^(?:what(?:'s|\s+is)|calculate|compute|evaluate|solve|how\s+much\s+is)\s+",
    re.IGNORECASE,
)

# A run of characters that could be arithmetic.
_EXPRESSION = re.compile(r"[-+(]*[\d.][\d.\s+\-*/%()]*[\d)]")

# At least one binary operator between two operands.
_HAS_OPERATOR = re.compile(r"[\d)]\s*(?:\*\*|[+\-*/%])\s*[-+(]*[\d.(]")

# Things that look like arithmetic but aren't: dates, times, phone numbers.
_NOT_MATH = re.compile(
    r"^\d{1,4}([-/.])\d{1,2}\1\d{1,4}$"      # 2026-11-04, 04/11/2026
    r"|^\d{3,}-\d{3,}(?:-\d+)*$"              # 555-1234, 020-555-1234
)


def _normalize_math(text: str) -> str:
    for pattern, symbol in _WORD_OPERATORS:
        text = pattern.sub(f" {symbol} ", text)
    return text


def calculator_rule(original: str, body: str) -> RouteDecision | None:
    text = _normalize_math(body)
    lead = _CALC_LEAD.match(text)
    remainder = _clean(text[lead.end():] if lead else text)

    best = None
    for match in _EXPRESSION.finditer(text):
        span = match.group().strip()
        if not _HAS_OPERATOR.search(span) or _NOT_MATH.match(span):
            continue
        try:
            evaluate(span)
        except CalculationError:
            continue
        if best is None or len(span) > len(best):
            best = span

    if best is None:
        return None

    expression = " ".join(best.split())

    if remainder == best:
        return RouteDecision(
            Intent.CALCULATOR, 0.95, targets.CALCULATOR,
            {"expression": expression},
            "The message is an arithmetic expression.",
        )

    # Arithmetic inside a longer sentence ("I need 3/4 cup of flour") is
    # more likely conversation; keep it as a low-confidence alternative.
    return RouteDecision(
        Intent.CALCULATOR, 0.45, targets.CALCULATOR,
        {"expression": expression},
        "The message contains arithmetic inside other text.",
    )


# --- Weather ----------------------------------------------------------------

_WEATHER_STRONG = re.compile(r"\b(?:weather|forecast)\b", re.I)

_WEATHER_WEAK = re.compile(
    r"\b(?:rain(?:ing|y)?|snow(?:ing|y)?|sunny|cloudy|windy|humid(?:ity)?|"
    r"temperature|umbrella|how\s+(?:hot|cold|warm)\s+is\s+it)\b",
    re.I,
)

# Indoor / body / device temperatures are not weather.
_NOT_WEATHER = re.compile(
    r"\b(?:room|inside|indoors?|bedroom|house|home\s+sensor|sensor|cpu|gpu|"
    r"laptop|computer|body|fever|oven|fridge|water)\b",
    re.I,
)

_QUESTION = re.compile(
    r"\?\s*$|^(?:is|will|what|how|do|does|should|whats|what's|tell\s+me)\b", re.I
)

_TIME_WORDS = r"(?:today|tomorrow|tonight|this|next|now|right\s+now|on|the|later)"

_LOCATION = re.compile(
    rf"\b(?:in|for|at|near)\s+(?!{_TIME_WORDS}\b)"
    rf"(?P<location>[a-z][a-z .'-]*?)"
    rf"(?=\s+{_TIME_WORDS}\b|\s*[?.!,]|\s*$)",
    re.I,
)

_WHEN = re.compile(
    r"\b(today|tomorrow|tonight|this\s+week(?:end)?|next\s+week|right\s+now|now)\b", re.I
)


def weather_rule(original: str, body: str) -> RouteDecision | None:
    if _NOT_WEATHER.search(body):
        return None

    if _WEATHER_STRONG.search(body):
        confidence, reason = 0.9, "The message mentions the weather or a forecast."
    elif _WEATHER_WEAK.search(body) and _QUESTION.search(body):
        confidence, reason = 0.7, "The message asks about weather conditions."
    else:
        return None

    arguments = {}
    location = _LOCATION.search(body)
    if location:
        arguments["location"] = location.group("location").strip()
    when = _WHEN.search(body)
    if when:
        # Only when stated, so a follow-up ("will it rain?") can inherit it.
        # The Weather tool treats a missing "when" as "now".
        arguments["when"] = " ".join(when.group(1).lower().split())

    return RouteDecision(Intent.WEATHER, confidence, targets.WEATHER, arguments, reason)


# --- Memory -----------------------------------------------------------------

_REMEMBER = re.compile(
    r"^(?:remember|note|don'?t\s+forget|do\s+not\s+forget|keep\s+in\s+mind|make\s+a\s+note)"
    r"(?:\s+that)?[\s,:]+(?P<content>.+)$",
    re.I | re.S,
)

_QUESTION_WORD = re.compile(r"^(?:what|when|where|who|how|why|which|my)\b", re.I)

_FORGET = re.compile(
    r"^forget(?:\s+(?:about|that))?\s+(?P<query>.+)$", re.I | re.S
)

_DELETE_MEMORY = re.compile(
    r"^(?:delete|erase|remove)\s+(?:the\s+|my\s+|your\s+|that\s+)?"
    r"(?:memory|memories|note|notes|fact|facts)\s*(?:about|of|on|that|saying)?\s*(?P<query>.*)$",
    re.I | re.S,
)

# "forget it" / "forget that" mean "never mind", not "delete a memory".
_NEVER_MIND = re.compile(r"^(?:it|that|this|about\s+it|it\s+then)$", re.I)

_RECALL_PATTERNS = [
    (re.compile(r"\bwhat\s+do\s+you\s+(?:know|remember)\s+about\s+(?P<query>.+)$", re.I), 0.9),
    (re.compile(r"\bwhat\s+(?:i\s+told\s+you|have\s+i\s+told\s+you|did\s+i\s+(?:tell\s+you|say))\s+about\s+(?P<query>.+)$", re.I), 0.9),
    (re.compile(r"^(?:do|did)\s+you\s+remember\s+(?P<query>.+)$", re.I), 0.85),
    (re.compile(r"^(?:what|who|where|when|which)(?:'s|\s+is|\s+are|\s+was|\s+were)\s+my\s+(?P<query>.+)$", re.I), 0.75),
    # "where do I live?", "what do I study?", "who am I?"
    (re.compile(
        r"^(?:what|where|who|which)\s+(?:do|did|am)\s+i\s+"
        r"(?P<query>live|stay|study|studying|work|like|love|hate|dislike|come\s+from|go\s+to)\b.*$",
        re.I), 0.8),
    (re.compile(r"^who\s+am\s+i\s*\??$", re.I), 0.8),
]


def remember_rule(original: str, body: str) -> RouteDecision | None:
    match = _REMEMBER.match(body)
    if not match:
        return None

    content = _clean(match.group("content"))

    # "Remember what I told you about X?" is a question, not a command.
    if body.rstrip().endswith("?") and _QUESTION_WORD.match(content):
        recalled = recall_rule(original, content)
        query = recalled.arguments["query"] if recalled else content
        return RouteDecision(
            Intent.RECALL, 0.85, targets.MEMORY, {"query": query},
            "The user asks AURORA to recall something.",
        )

    return RouteDecision(
        Intent.REMEMBER, 0.95, targets.MEMORY, {"content": content},
        "The user explicitly asked AURORA to remember something.",
    )


def forget_rule(original: str, body: str) -> RouteDecision | None:
    match = _FORGET.match(body) or _DELETE_MEMORY.match(body)
    if not match:
        return None

    query = _clean(match.group("query"))
    if not query or _NEVER_MIND.match(query):
        return None

    return RouteDecision(
        Intent.FORGET, 0.95, targets.MEMORY, {"query": query},
        "The user explicitly asked AURORA to forget something.",
    )


def recall_rule(original: str, body: str) -> RouteDecision | None:
    for pattern, confidence in _RECALL_PATTERNS:
        match = pattern.search(body)
        if match:
            query = match.groupdict().get("query") or "me"   # "who am I?" -> everything
            return RouteDecision(
                Intent.RECALL, confidence, targets.MEMORY,
                {"query": _clean(query)},
                "The user asks about something they may have told AURORA.",
            )
    return None


# --- Research ---------------------------------------------------------------

# Topics that look like "what is X" but are about AURORA, the user, or small talk.
_NOT_A_TOPIC = re.compile(
    r"^(?:(?:the\s+)?(?:time|date|day|matter|plan|point|deal)|up|new|wrong|happening|going\s+on|"
    r"(?:you|your|yourself|i|i'm|me|my|we|us|our|he|she|his|her|they|them|their|"
    r"it|its|this|that|these|those|there|here)\b.*|"
    r"the\s+weather\b.*|weather\b.*|today|tomorrow)$",
    re.I,
)

_KNOWLEDGE_STRONG = re.compile(
    r"^(?:tell\s+me\s+about|look\s+up|search\s+(?:wikipedia|wiki)\s+for|wiki(?:pedia)?|"
    r"who\s+(?:is|was|were|are))\s+(?P<topic>.+)$",
    re.I,
)

_KNOWLEDGE_WEAK = re.compile(
    r"^(?:what(?:'s|\s+is|\s+are|\s+was|\s+were)|define|definition\s+of)\s+(?P<topic>.+)$",
    re.I,
)

_RESEARCH_WORDS = r"(?:research\s+)?(?:papers?|articles?|research|studies|preprints?|publications?|discoveries|findings)"

_PAPERS = re.compile(
    rf"^(?:(?:find|search(?:\s+for)?|show(?:\s+me)?|get|list|look\s+for|are\s+there(?:\s+any)?|any|"
    rf"what\s+are(?:\s+the|\s+some)?|tell\s+me\s+(?:about\s+)?(?:the\s+)?)\s+)?"
    rf"(?:(?P<recent>recent|latest|new)\s+)?(?:arxiv\s+)?{_RESEARCH_WORDS}\s+"
    rf"(?:on|about|regarding|into|in|for)\s+(?P<topic>.+)$",
    re.I,
)

_RECENT = re.compile(r"\b(?:recent|latest|new|this\s+year|newest)\b", re.I)

_ARTICLES = re.compile(r"^(?:an?|the)\s+", re.I)


def _topic(value: str) -> str:
    return _ARTICLES.sub("", _clean(value)).strip()


def papers_rule(original: str, body: str) -> RouteDecision | None:
    match = _PAPERS.match(_clean(body))
    if not match:
        return None
    topic = _topic(match.group("topic"))
    if not topic or _NOT_A_TOPIC.match(topic):
        return None
    return RouteDecision(
        Intent.PAPERS, 0.9, targets.ARXIV,
        {"topic": topic, "recent": bool(match.group("recent") or _RECENT.search(body))},
        "The user asks for research papers on a topic.",
    )


def knowledge_rule(original: str, body: str) -> RouteDecision | None:
    text = _clean(body)
    match = _KNOWLEDGE_STRONG.match(text)
    confidence = 0.8
    if not match:
        match = _KNOWLEDGE_WEAK.match(text)
        confidence = 0.6
    if not match:
        return None

    topic = _topic(match.group("topic"))
    if not topic or len(topic) < 3 or _NOT_A_TOPIC.match(topic) or len(topic.split()) > 8:
        return None
    if any(ch.isdigit() for ch in topic) and _HAS_OPERATOR.search(topic):
        return None  # "what is 2 + 2" belongs to the calculator.
    if _WEATHER_STRONG.search(topic) or _WEATHER_WEAK.search(topic):
        return None  # "what is the temperature in Pune" belongs to weather or sensors.

    return RouteDecision(
        Intent.KNOWLEDGE, confidence, targets.WIKIPEDIA, {"topic": topic},
        "The user asks what or who something is.",
    )


_DOC_WORDS = r"(?:documents?|docs|notes|files|papers|pdfs?)"

_DOC_LIST = re.compile(
    rf"^(?:list|show(?:\s+me)?|what\s+are|which\s+are)\s+(?:all\s+)?(?:of\s+)?my\s+{_DOC_WORDS}$"
    rf"|^what\s+{_DOC_WORDS}\s+do\s+i\s+have$",
    re.I,
)

_DOC_SEARCH = re.compile(
    rf"^(?:search|look\s+through|check)\s+(?:in\s+)?my\s+{_DOC_WORDS}\s+(?:for|about)\s+(?P<query>.+)$"
    rf"|^(?:find|search\s+for|look\s+for)\s+(?P<query2>.+?)\s+in\s+my\s+{_DOC_WORDS}$"
    rf"|^what\s+(?:do|does)\s+my\s+{_DOC_WORDS}\s+say\s+about\s+(?P<query3>.+)$",
    re.I,
)

_DOC_READ = re.compile(
    r"^(?:read|open|show(?:\s+me)?)\s+(?:my\s+)?(?:(?:document|file|notes?)\s+)?"
    r"(?P<name>[\w .()'-]+\.(?:txt|md|pdf|jpe?g|png|bmp|webp))$",
    re.I,
)


def documents_rule(original: str, body: str) -> RouteDecision | None:
    text = _clean(body)

    if _DOC_LIST.match(text):
        return RouteDecision(
            Intent.DOCUMENTS, 0.9, targets.DOCUMENTS, {"action": "list"},
            "The user asks which documents they have.",
        )

    match = _DOC_SEARCH.match(text)
    if match:
        query = match.group("query") or match.group("query2") or match.group("query3")
        return RouteDecision(
            Intent.DOCUMENTS, 0.95, targets.DOCUMENTS,
            {"action": "search", "query": _clean(query)},
            "The user asks to search their documents.",
        )

    # File names keep their case and extension, so match on the original text.
    match = _DOC_READ.match(_clean(_strip_prefix(original)))
    if match:
        return RouteDecision(
            Intent.DOCUMENTS, 0.95, targets.DOCUMENTS,
            {"action": "read", "name": match.group("name").strip()},
            "The user asks to read one of their documents.",
        )

    return None


# --- Sensors and health ---------------------------------------------------------

_INDOOR = re.compile(
    r"\b(?:in\s+(?:my|the|this|our)\s+(?:room|bedroom|house|home|office|flat|apartment)|"
    r"inside|indoors?|in\s+here|room\s+temperature|room)\b",
    re.I,
)

_ROOM_QUANTITY = [
    (re.compile(r"\b(?:temperature|how\s+(?:hot|warm|cold)\s+is\s+it|hot|warm|cold)\b", re.I), "temperature"),
    (re.compile(r"\b(?:humidity|humid)\b", re.I), "humidity"),
    (re.compile(r"\b(?:air\s+)?pressure\b", re.I), "pressure"),
]

_MOTION = re.compile(
    r"\b(?:is\s+there\s+(?:any\s+)?(?:motion|movement)|any\s+(?:motion|movement)|"
    r"(?:has|did)\s+(?:anyone|anybody|someone|somebody|something)\s+mov(?:e|ed)|motion\s+sensor)\b",
    re.I,
)

_ALL_SENSORS = re.compile(
    r"^(?:sensor\s+(?:readings?|status|data)|(?:check|read|show(?:\s+me)?)\s+(?:my\s+|the\s+|all\s+)?sensors?|"
    r"what\s+do\s+(?:my|the)\s+sensors\s+say|room\s+(?:conditions|status|readings)|"
    r"how\s+is\s+(?:my|the)\s+room)$",
    re.I,
)

_HEALTH_QUANTITY = [
    (re.compile(r"\b(?:heart\s*rate|pulse|bpm)\b", re.I), "heart_rate"),
    (re.compile(r"\b(?:spo2|sp\s*o\s*2|blood\s+oxygen|oxygen\s+(?:level|saturation))\b", re.I), "spo2"),
    (re.compile(r"\b(?:vitals|health\s+(?:readings?|sensors?|data))\b", re.I), "all"),
]

_ABOUT_ME = re.compile(r"\bmy\b|^(?:check|measure|read|take)\b", re.I)
_SLEEP = re.compile(r"\bhow\s+(?:did|have)\s+i\s+(?:sleep|slept)\b|\bmy\s+sleep\b", re.I)


def sensors_rule(original: str, body: str) -> RouteDecision | None:
    text = _clean(body)

    if _ALL_SENSORS.match(text):
        return RouteDecision(Intent.SENSORS, 0.9, targets.SENSORS, {"quantity": "all"},
                             "The user asks for all sensor readings.")
    if _MOTION.search(text):
        return RouteDecision(Intent.SENSORS, 0.85, targets.SENSORS, {"quantity": "motion"},
                             "The user asks whether the motion sensor saw anything.")
    if _INDOOR.search(text):
        for pattern, quantity in _ROOM_QUANTITY:
            if pattern.search(text):
                return RouteDecision(Intent.SENSORS, 0.9, targets.SENSORS, {"quantity": quantity},
                                     "The user asks about conditions in their room.")
    return None


def health_rule(original: str, body: str) -> RouteDecision | None:
    text = _clean(body)

    if _SLEEP.search(text):
        return RouteDecision(Intent.HEALTH, 0.9, targets.HEALTH, {"quantity": "sleep"},
                             "The user asks about their sleep.")
    if not _ABOUT_ME.search(text):
        return None   # "what is a heart rate?" is a general question.
    for pattern, quantity in _HEALTH_QUANTITY:
        if pattern.search(text):
            return RouteDecision(Intent.HEALTH, 0.9, targets.HEALTH, {"quantity": quantity},
                                 "The user asks for their own health readings.")
    return None


# --- Vision -------------------------------------------------------------------

_LOOK = re.compile(
    r"^(?:what\s+(?:do|can)\s+you\s+see|look(?:\s+around)?|take\s+a\s+look(?:\s+around)?|"
    r"what(?:'s|\s+is)\s+in\s+front\s+of\s+(?:you|me|the\s+camera)|"
    r"(?:is|are)\s+(?:there\s+)?any(?:one|body)\s+(?:there|here|in\s+the\s+room|in\s+front\s+of\s+you)|"
    r"who(?:'s|\s+is)\s+(?:there|here|in\s+the\s+room)|how\s+many\s+people\s+(?:are\s+)?(?:there|here|in\s+the\s+room)|"
    r"describe\s+(?:what\s+you\s+see|the\s+room|the\s+scene|your\s+surroundings)|use\s+(?:the|your)\s+camera)$",
    re.I,
)

_READ_TEXT = re.compile(
    r"^(?:read\s+(?:this|that|it|the\s+text|this\s+text|this\s+page|the\s+page|the\s+sign|"
    r"what\s+i'?m\s+holding|what\s+i\s+am\s+holding)|what\s+does\s+(?:this|that|it)\s+say|"
    r"can\s+you\s+read\s+(?:this|that|it))$",
    re.I,
)


def vision_rule(original: str, body: str) -> RouteDecision | None:
    text = _clean(body)
    if _LOOK.match(text):
        return RouteDecision(
            Intent.VISION, 0.95, targets.CAMERA, {"action": "look"},
            "The user asks what the camera sees.",
        )
    if _READ_TEXT.match(text):
        return RouteDecision(
            Intent.VISION, 0.95, targets.CAMERA, {"action": "read"},
            "The user asks AURORA to read text in front of the camera.",
        )
    return None


# Commands addressed to AURORA's memory. Anything else in the message
# ("remember that the weather in Pune was great") is the thing to store,
# not a competing request, so these never trigger clarification.
_EXPLICIT_COMMANDS = {Intent.REMEMBER, Intent.FORGET}

DEFAULT_RULES: tuple[Rule, ...] = (
    calculator_rule,
    weather_rule,
    remember_rule,
    forget_rule,
    recall_rule,
    papers_rule,
    knowledge_rule,
    documents_rule,
    vision_rule,
    sensors_rule,
    health_rule,
)


# --- Router -----------------------------------------------------------------

class IntentRouter:
    """
    Turns a message into a RouteDecision.

    The decision with the highest confidence wins. The default
    conversation candidate has ``settings.ROUTE_CONVERSATION_CONFIDENCE``,
    so a tool only wins when its rule is more confident than that.
    """

    def __init__(
        self,
        rules: tuple[Rule, ...] = DEFAULT_RULES,
        conversation_confidence: float = settings.ROUTE_CONVERSATION_CONFIDENCE,
        clarify_margin: float = settings.ROUTE_CLARIFY_MARGIN,
    ):
        self.rules = tuple(rules)
        self.conversation_confidence = conversation_confidence
        self.clarify_margin = clarify_margin

    def route(self, message: str) -> RouteDecision:

        original = (message or "").strip()

        if not original:
            return RouteDecision(Intent.CLARIFY, 1.0, None, {}, "The message is empty.")

        body = _strip_prefix(original) or original

        candidates = []
        for rule in self.rules:
            try:
                result = rule(original, body)
            except Exception:
                logger.exception("Routing rule %s failed", getattr(rule, "__name__", rule))
                continue
            if result is not None:
                candidates.append(result)

        candidates.append(
            RouteDecision(
                Intent.CONVERSATION, self.conversation_confidence, targets.BRAIN, {},
                "No tool clearly matched; the Brain should answer.",
            )
        )

        candidates.sort(key=lambda d: d.confidence, reverse=True)
        best, others = candidates[0], tuple(candidates[1:])

        tools = [c for c in candidates if c.uses_tool]
        if (
            len(tools) >= 2
            and best.uses_tool
            and best.intent not in _EXPLICIT_COMMANDS
            and tools[0].intent is not tools[1].intent
            and tools[0].confidence - tools[1].confidence < self.clarify_margin
        ):
            decision = RouteDecision(
                Intent.CLARIFY, best.confidence, None, {},
                f"Ambiguous between {tools[0].intent.value} and {tools[1].intent.value}.",
                tuple(candidates),
            )
        else:
            decision = RouteDecision(
                best.intent, best.confidence, best.target, best.arguments,
                best.reason, others,
            )

        # Never log the message or argument values: they are the user's words.
        logger.debug(
            "Routed message -> %s (%.2f), arguments: %s",
            decision.intent.value, decision.confidence,
            sorted(decision.arguments) or "none",
        )
        return decision
