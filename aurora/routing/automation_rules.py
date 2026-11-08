"""
Routing for automation: scheduled tasks, programs, tidying folders and undo.

The schedule rule runs before every other rule: "every weekday at 18:30
turn on the desk light" contains a device command, and it must become a
*proposed task* (which asks first), never an immediate switch.
"""

import re

from aurora.config import settings
from aurora.routing import decision as targets
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent

# --- Scheduled tasks ----------------------------------------------------------------------

_DAY_WORDS = {
    "day": "daily", "daily": "daily", "morning": "daily", "afternoon": "daily", "evening": "daily",
    "night": "daily", "weekday": "weekdays", "weekend": "weekends",
    "monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu", "friday": "fri",
    "saturday": "sat", "sunday": "sun",
}
_DAYS = re.compile(
    r"\b(?:(?:every|each)\s+(?P<every>day|morning|afternoon|evening|night|weekday|weekend|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?|(?P<daily>daily)|"
    r"on\s+(?P<on>weekdays|weekends|mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays))\b", re.I)
_AT = re.compile(r"\bat\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)(?=\s|,|$)", re.I)
_SCHEDULE_LIST = re.compile(
    r"^(?:list|show(?:\s+me)?|what\s+are)\s+(?:my\s+|the\s+)?(?:scheduled\s+tasks|schedule|tasks)$"
    r"|^what(?:'s|\s+is|\s+tasks\s+are|\s+are)\s+(?:my\s+)?scheduled(?:\s+tasks)?$|^(?:my\s+)?scheduled\s+tasks$", re.I)
_SCHEDULE_CHANGE = re.compile(
    r"^(?P<verb>pause|resume|restart|delete|remove|cancel|stop)\s+(?:the\s+)?(?:scheduled\s+)?task\s+"
    r"(?:number\s+)?(?P<n>\d+)$", re.I)
_SCHEDULE_DELETE_ALL = re.compile(r"^(?:delete|remove|cancel|clear)\s+all\s+(?:my\s+|the\s+)?scheduled\s+tasks$", re.I)
_REMIND_ACTION = re.compile(r"^remind\s+me\s+(?:to\s+)?(?P<text>.+)$", re.I)
_QUESTION = re.compile(r"^(?:what|how|why|when|where|who|is|are|does|do|did|can|could)\b", re.I)
_ROUTINE_ACTION = re.compile(r"^(?:run|do|start)\s+(?:my\s+|the\s+)?(?:routine\s+)?(?P<name>.+?)(?:\s+routine)?$", re.I)


def _days(match: re.Match) -> str:
    word = (match.group("every") or match.group("daily") or match.group("on") or "").lower()
    if match.group("on"):
        word = word[:-1]                          # "weekdays" -> "weekday", "mondays" -> "monday"
    return _DAY_WORDS[word]


def schedule_rule(original: str, body: str) -> RouteDecision | None:
    from aurora.routing import router

    text = router._clean(body)
    if _SCHEDULE_LIST.match(text):
        return RouteDecision(Intent.SCHEDULE, 0.95, targets.SCHEDULE, {"action": "list"}, "List scheduled tasks.")
    match = _SCHEDULE_CHANGE.match(text)
    if match:
        verb = match.group("verb").lower()
        action = {"restart": "resume", "remove": "delete", "cancel": "delete", "stop": "pause"}.get(verb, verb)
        return RouteDecision(Intent.SCHEDULE, 0.95, targets.SCHEDULE, {"action": action, "number": int(match.group("n"))},
                             "Change a scheduled task.")
    if _SCHEDULE_DELETE_ALL.match(text):
        return RouteDecision(Intent.SCHEDULE, 0.95, targets.SCHEDULE, {"action": "delete_all"},
                             "Delete all scheduled tasks.")

    days_match = _DAYS.search(text)
    if not days_match:
        return None
    days = _days(days_match)
    rest = (text[:days_match.start()] + " " + text[days_match.end():]).strip()
    at = ""
    time_match = _AT.search(rest)
    if time_match:
        at = router._clock(time_match.group("time")) or ""
        word = (days_match.group("every") or "").lower()
        has_ampm = re.search(r"[ap]\.?m", time_match.group("time"), re.I)
        if at and word in ("evening", "night", "afternoon") and not has_ampm and int(at[:2]) < 12:
            at = f"{int(at[:2]) + 12:02d}{at[2:]}"      # "every evening at 9" is 21:00
        rest = (rest[:time_match.start()] + " " + rest[time_match.end():]).strip()
    action = re.sub(r"\s+", " ", rest).strip(" ,.;").removeprefix("and ").removeprefix("then ").strip()

    if _QUESTION.match(action):
        return None
    arguments: dict = {"action": "add", "days": days, "at": at}
    remind = _REMIND_ACTION.match(action)
    if remind:
        arguments.update(kind="reminder", text=router._clean(remind.group("text")))
    else:
        device = router.devices_rule(action, action)
        sensors = router.sensors_rule(action, action)
        routine = _ROUTINE_ACTION.match(action)
        if device is not None and device.arguments.get("action") not in ("list", "status"):
            arguments.update(kind="device", device=device.arguments["device"],
                             device_action=device.arguments["action"], value=device.arguments.get("value"))
        elif sensors is not None and sensors.arguments.get("view", "now") == "now":
            arguments.update(kind="sensors", quantity=sensors.arguments["quantity"])
        elif routine:
            arguments.update(kind="routine", routine=routine.group("name"))
        elif not at or _QUESTION.match(action):
            return None                       # "what is the temperature every day?" isn't a request to schedule
        else:
            arguments.update(kind="unknown")
    return RouteDecision(Intent.SCHEDULE, 0.95, targets.SCHEDULE, arguments, "Add a scheduled task.")


# --- Programs -------------------------------------------------------------------------------

_APP = re.compile(r"^(?:please\s+)?(?P<verb>open|launch|start|run|close|quit|exit)\s+(?P<app>.+?)(?:\s+for\s+me)?$", re.I)
_APP_LIST = re.compile(
    r"^(?:which|what)\s+(?:programs|apps|applications)\s+can\s+you\s+(?:open|launch|start)$"
    r"|^list\s+(?:my\s+|the\s+)?(?:programs|apps|applications)$", re.I)


def apps_rule(original: str, body: str) -> RouteDecision | None:
    from aurora.automation.apps import AppError, Launcher, load_apps
    from aurora.routing import router

    text = router._clean(body)
    if _APP_LIST.match(text):
        return RouteDecision(Intent.APPS, 0.95, targets.APPS, {"action": "list"}, "List the programs AURORA can open.")
    match = _APP.match(text)
    if not match:
        return None
    try:
        app = Launcher(load_apps()).find(match.group("app"))
    except (AppError, ValueError, KeyError):
        return None                             # not a program on the list: something else
    verb = match.group("verb").lower()
    action = "close" if verb in ("close", "quit", "exit") else "open"
    return RouteDecision(Intent.APPS, 0.95, targets.APPS, {"action": action, "app": app.name},
                         "Open or close a program.")


# --- Tidying folders ------------------------------------------------------------------------

_FOLDER_WORDS = r"(?:documents?|docs|files|folder|downloads|desktop|pictures|photos|music|videos)"
_ORGANIZE = re.compile(
    rf"^(?:please\s+)?(?:organi[sz]e|tidy(?:\s+up)?|sort(?:\s+out)?|clean\s+up)\s+"
    rf"(?P<folder>(?:my\s+|the\s+)?[\w\s'-]*?{_FOLDER_WORDS}|(?:my\s+|the\s+)?[\w-]+\s+folder)"
    rf"(?:\s+by\s+(?P<by>type|kind|file\s+type|month|date))?$", re.I)
_FOLDERS = re.compile(r"^(?:which|what)\s+folders?\s+(?:can|may)\s+you\s+(?:tidy|organi[sz]e|sort|clean\s+up)$", re.I)
_UNDO_TIDY = re.compile(
    r"^undo\s+(?:my\s+|the\s+)?(?:last\s+)?(?:tidy[\s-]?up|tidying|organi[sz]ing|clean[\s-]?up)$", re.I)


def files_rule(original: str, body: str) -> RouteDecision | None:
    from aurora.routing import router

    text = router._clean(body)
    if _FOLDERS.match(text):
        return RouteDecision(Intent.FILES, 0.95, targets.FILES, {"action": "folders"}, "Which folders can be tidied.")
    if _UNDO_TIDY.match(text):
        return RouteDecision(Intent.FILES, 0.95, targets.FILES, {"action": "undo"}, "Undo the last tidy-up.")
    match = _ORGANIZE.match(text)
    if not match:
        return None
    by = (match.group("by") or "type").lower()
    return RouteDecision(Intent.FILES, 0.95, targets.FILES,
                         {"action": "organize", "folder": match.group("folder").strip(),
                          "by": "month" if by in ("month", "date") else "type"},
                         "Tidy a folder.")


# --- Undo -------------------------------------------------------------------------------------

_UNDO = re.compile(
    r"^(?:please\s+)?(?:undo|reverse|revert)(?:\s+(?:that|it|this|the\s+last\s+(?:action|change|thing)|"
    r"what\s+you\s+(?:just\s+)?did|your\s+last\s+(?:action|change)))?$|^put\s+it\s+back$", re.I)


def undo_rule(original: str, body: str) -> RouteDecision | None:
    from aurora.routing import router

    if _UNDO.match(router._clean(body)):
        return RouteDecision(Intent.UNDO, 0.95, targets.UNDO, {}, "Undo the last action.")
    return None
