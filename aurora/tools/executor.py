import logging
from datetime import datetime

from aurora.events import topics
from aurora.events.bus import EventBus
from aurora.kernel.permissions import PermissionDeniedError
from aurora.knowledge.sources import COMPUTED, DOCUMENT, LIVE, MEMORY, SENSOR, Source
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent
from aurora.tools.result import ToolResult
from aurora.tools.tool_manager import ToolManager

logger = logging.getLogger(__name__)

# Which tool handles which intent.
INTENT_TOOLS = {
    Intent.CALCULATOR: "Calculator",
    Intent.NUMBERS: "Numbers",
    Intent.WEATHER: "Weather",
    Intent.REMEMBER: "Remember",
    Intent.RECALL: "Recall",
    Intent.FORGET: "Forget",
    Intent.MEMORY_CONTROL: "MemoryControl",
    Intent.EPISODES: "Episodes",
    Intent.ROUTINES: "Routines",
    Intent.KNOWLEDGE: "Wikipedia",
    Intent.PAPERS: "arXiv",
    Intent.DOCUMENTS: "Documents",
    Intent.KNOWLEDGE_BASE: "Knowledge",
    Intent.VISION: "Camera",
    Intent.SENSORS: "Sensors",
    Intent.HEALTH: "Health",
    Intent.DEVICES: "Devices",
    Intent.REMINDER: "Reminders",
    Intent.RESEARCH: "Research",
}

SOURCE = "ToolExecutor"


def default_sources(name: str, result: ToolResult) -> list[Source]:
    """Where a tool's answer came from, for tools that don't say themselves."""
    now = datetime.now()
    data = result.data
    if name in ("Calculator", "Numbers"):
        return [Source(COMPUTED, name)]
    if name == "Weather":
        return [Source(LIVE, "Open-Meteo", "https://open-meteo.com", now)]
    if name == "Wikipedia" and isinstance(data, dict):
        title = data.get("title") or "Wikipedia"
        url = (data.get("content_urls") or {}).get("desktop", {}).get("page", "")
        return [Source(LIVE, f"Wikipedia, \"{title}\"", url, now)]
    if name == "arXiv":
        return [Source(LIVE, "arXiv", "https://arxiv.org", now)]
    if name == "Documents":
        if isinstance(data, list) and data and hasattr(data[0], "citation"):
            seen = []
            for passage in data:
                source = Source(DOCUMENT, passage.file, f"page {passage.page}" if passage.page else "")
                if source not in seen:
                    seen.append(source)
            return seen
        if hasattr(data, "evidence") and getattr(data, "name", ""):     # a picture's SceneReport
            return [Source(DOCUMENT, data.name, "image, analysed on this computer")]
        return [Source(DOCUMENT, "your documents")]
    if name in ("Recall", "Remember"):
        return [Source(MEMORY)]
    if name == "Episodes":
        return [Source(MEMORY, "activity history")]
    if name in ("Sensors", "Health"):
        return [Source(SENSOR)]
    if name == "Camera":
        return [Source(SENSOR, "camera")]
    return []


class ToolExecutor:
    """
    Runs the tool chosen by a RouteDecision.

    Permission checks happen in the ToolManager. Every failure, whether
    a missing permission or a crash inside the tool, becomes a
    ToolResult explaining what went wrong; nothing is passed on to the
    language model to guess at.
    """

    def __init__(self, tools: ToolManager, events: EventBus | None = None):
        self.tools = tools
        self.events = events or EventBus()

    def tool_for(self, decision: RouteDecision) -> str | None:
        name = INTENT_TOOLS.get(decision.intent)
        if name and self.tools.get(name) is not None:
            return name
        return None

    def can_handle(self, decision: RouteDecision) -> bool:
        return self.tool_for(decision) is not None

    def execute(self, decision: RouteDecision) -> ToolResult:
        name = self.tool_for(decision)
        if name is None:
            raise ValueError(f"No tool handles intent '{decision.intent.value}'.")

        try:
            result = self.tools.execute(name, **decision.arguments)
        except PermissionDeniedError as error:
            result = ToolResult(
                False,
                f"I'm not allowed to use the {name} tool: it needs the "
                f"'{error.permission.value}' permission. You can grant it in "
                "PERMISSION_GRANTS in aurora/config/settings.py.",
            )
            self.events.emit(
                topics.PERMISSION_DENIED,
                {"component": name, "permission": error.permission.value},
                source=SOURCE,
            )
            return result
        except Exception as error:
            logger.exception("Tool %s crashed", name)
            result = ToolResult(False, f"The {name} tool failed unexpectedly ({error}).")
            self.events.emit(topics.TOOL_FAILED, {"tool": name, "error": str(error)}, source=SOURCE)
            return result

        if result.success and not result.sources:
            result.sources = default_sources(name, result)
        self._report(name, result)
        return result

    def confirm(self, name: str, pending: ToolResult) -> ToolResult:
        """Runs a pending action the user has approved."""
        try:
            result = pending.confirm()
        except Exception as error:
            logger.exception("Confirmed action from %s crashed", name)
            result = ToolResult(False, f"That didn't work ({error}).")
            self.events.emit(topics.TOOL_FAILED, {"tool": name, "error": str(error)}, source=SOURCE)
            return result

        self._report(name, result)
        return result

    def _report(self, name: str, result: ToolResult) -> None:
        logger.info("Tool %s: success=%s", name, result.success)
        self.events.emit(
            topics.TOOL_EXECUTED,
            {"tool": name, "success": result.success,
             "needs_confirmation": result.needs_confirmation},
            source=SOURCE,
        )
