import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from aurora.automation.actions import AUDITED_TOOLS, BY_NAME, action_name
from aurora.automation.audit import AuditLog
from aurora.events import topics
from aurora.events.bus import EventBus
from aurora.kernel.permissions import PermissionDeniedError
from aurora.network import NetworkBlocked, gate as network_gate
from aurora.knowledge.sources import COMPUTED, DOCUMENT, LIVE, MEMORY, SENSOR, Source
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent
from aurora.tools.result import ToolResult
from aurora.tools.tool_manager import ToolManager

logger = logging.getLogger(__name__)

UNDO_LIMIT = 20


@dataclass
class UndoEntry:
    action: str
    label: str                      # "turn the desk light back off"
    undo: Callable[[], ToolResult]
    simulated: bool
    initiated: str

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
    Intent.NEWS: "News",
    Intent.DOCUMENTS: "Documents",
    Intent.KNOWLEDGE_BASE: "Knowledge",
    Intent.VISION: "Camera",
    Intent.SENSORS: "Sensors",
    Intent.HEALTH: "Health",
    Intent.DEVICES: "Devices",
    Intent.REMINDER: "Reminders",
    Intent.RESEARCH: "Research",
    Intent.APPS: "Apps",
    Intent.FILES: "Files",
    Intent.SCHEDULE: "Schedule",
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

    def __init__(self, tools: ToolManager, events: EventBus | None = None, audit: AuditLog | None = None,
                 network=None):
        self.tools = tools
        self.network = network or network_gate
        self.events = events or EventBus()
        # Actions (aurora/automation/actions.py) are written to the audit trail,
        # and reversible ones remembered for "undo that" (newest last).
        self.audit = audit or AuditLog()
        self.undo_stack: list[UndoEntry] = []

    # --- Audit trail and undo --------------------------------------------------------

    def _audited(self, name: str, arguments: dict) -> str | None:
        if name not in AUDITED_TOOLS:
            return None
        action = action_name(name, arguments)
        return action if action in BY_NAME else None

    def audit_result(self, action: str | None, result: ToolResult, initiated: str = "you") -> None:
        """Records what happened; remembers how to reverse it."""
        if action is None:
            return
        outcome = "asked" if result.needs_confirmation else ("done" if result.success else "failed")
        self.audit.record(action, outcome, result.message.splitlines()[0] if result.message else "",
                          initiated, result.simulated)
        if outcome == "done" and result.undo is not None:
            self.undo_stack.append(UndoEntry(action, result.undo_label or "undo that", result.undo,
                                             result.simulated, initiated))
            del self.undo_stack[:-UNDO_LIMIT]

    def declined(self, name: str, pending: ToolResult, cancelled: bool = False) -> None:
        action = getattr(pending, "audit_action", None)
        if action:
            self.audit.record(action, "cancelled" if cancelled else "declined",
                              pending.message.splitlines()[0] if pending.message else "", "you", pending.simulated)

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

        self.network.clear_block()
        raised = None
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
        except NetworkBlocked as error:
            # Raised by tools that search in several threads (the News tool):
            # the gate's per-thread record can't see those.
            result, raised = ToolResult(False, ""), error
        except Exception as error:
            logger.exception("Tool %s crashed", name)
            result = ToolResult(False, f"The {name} tool failed unexpectedly ({error}).")
            self.events.emit(topics.TOOL_FAILED, {"tool": name, "error": str(error)}, source=SOURCE)
            return result

        block = self.network.take_block() or raised
        if block is not None and not result.success:
            result = self.network_result(name, decision.arguments, block)
            self._report(name, result)
            return result

        if result.success and not result.sources:
            result.sources = default_sources(name, result)
        action = self._audited(name, decision.arguments)
        if result.needs_confirmation:
            result.audit_action = action          # for the yes / no that follows
        self.audit_result(action, result)
        self._report(name, result)
        return result

    def network_result(self, name: str, arguments: dict, block: NetworkBlocked) -> ToolResult:
        """Why a tool couldn't go online, or a question asking whether it may."""
        service = self.network.service(block.component)
        label = service.get("name", block.component)
        if block.can_ask:
            sends = service.get("sends", "your request")
            purpose = service.get("purpose", "this")

            def approved() -> ToolResult:
                self.network.approve(block.component)
                self.network.clear_block()
                try:
                    result = self.tools.execute(name, **arguments)
                except NetworkBlocked as error:
                    return self.network_result(name, arguments, error)
                again = self.network.take_block()
                if again is not None and not result.success:
                    return self.network_result(name, arguments, again)
                if result.success and not result.sources:
                    result.sources = default_sources(name, result)
                return result

            return ToolResult(
                False,
                f"{label} needs the internet for {purpose}: it would send {sends} to {block.host}. "
                f"Allow {label} to go online until AURORA restarts?",
                confirm=approved,
            )
        if block.reason == "internet access is switched off":
            how = "Internet access is off. You can turn it on in Settings → Internet and network."
        elif block.kind != "internet" and "local network" in block.reason:
            how = "Access to your local network is off. You can turn it on in Settings → Internet and network."
        else:
            how = f"AURORA blocked the request: {block.reason}."
        return ToolResult(False, f"{label} can't reach {block.host}. {how}")

    def confirm(self, name: str, pending: ToolResult) -> ToolResult:
        """Runs a pending action the user has approved."""
        action = getattr(pending, "audit_action", None)
        try:
            result = pending.confirm()
        except Exception as error:
            logger.exception("Confirmed action from %s crashed", name)
            result = ToolResult(False, f"That didn't work ({error}).")
            self.events.emit(topics.TOOL_FAILED, {"tool": name, "error": str(error)}, source=SOURCE)
            self.audit_result(action, result)
            return result

        self.audit_result(action, result)
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
