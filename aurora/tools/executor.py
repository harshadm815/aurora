import logging

from aurora.events import topics
from aurora.events.bus import EventBus
from aurora.kernel.permissions import PermissionDeniedError
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent
from aurora.tools.result import ToolResult
from aurora.tools.tool_manager import ToolManager

logger = logging.getLogger(__name__)

# Which tool handles which intent.
INTENT_TOOLS = {
    Intent.CALCULATOR: "Calculator",
    Intent.WEATHER: "Weather",
    Intent.REMEMBER: "Remember",
    Intent.RECALL: "Recall",
    Intent.FORGET: "Forget",
    Intent.KNOWLEDGE: "Wikipedia",
    Intent.PAPERS: "arXiv",
    Intent.DOCUMENTS: "Documents",
    Intent.VISION: "Camera",
}

SOURCE = "ToolExecutor"


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
