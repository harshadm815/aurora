import logging
import re

from aurora.brain.brain import Brain
from aurora.config import settings
from aurora.conversation.conversation import Conversation
from aurora.events import topics
from aurora.events.bus import EventBus
from aurora.kernel.context import KernelContext
from aurora.kernel.permissions import Permission, PermissionManager
from aurora.kernel.service_manager import ServiceManager
from aurora.memory.auto import AutoMemory
from aurora.memory.manager import MemoryManager
from aurora.plugins.plugin import BasePlugin
from aurora.plugins.plugin_manager import PluginManager
from aurora.routing.decision import RouteDecision
from aurora.routing.router import IntentRouter
from aurora.services.brain_service import BrainService
from aurora.tool_plugins.registry import default_tools
from aurora.tools.executor import ToolExecutor
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool
from aurora.tools.tool_manager import ToolManager

logger = logging.getLogger(__name__)

SOURCE = "Kernel"

AUTO_MEMORY = "AutoMemory"
AUTO_MEMORY_PERMISSIONS = (Permission.MEMORY_READ, Permission.MEMORY_WRITE)

_YES = re.compile(r"^(?:y|yes|yeah|yep|sure|ok|okay|confirm|do it|go ahead)[.!]*$", re.I)
_NO = re.compile(r"^(?:n|no|nope|cancel|stop|don'?t|never ?mind)[.!]*$", re.I)


class AuroraEngine:
    """
    The central orchestrator of AURORA.

    The Engine coordinates all major systems but does not
    perform reasoning itself. It does not print; the user
    interface (main.py) owns all terminal output.

    Message flow:
        pending confirmation? → yes/no handled
        route → clarify | tool | Brain

    Startup order:   services → plugins → ``kernel.started``
    Shutdown order:  ``kernel.stopping`` → plugins → services → ``kernel.stopped``
    """

    def __init__(
        self,
        brain: Brain | None = None,
        permissions: PermissionManager | None = None,
        memory: MemoryManager | None = None,
        tools: list[BaseTool] | None = None,
    ):
        # Shared kernel facilities
        self.events = EventBus()
        self.permissions = permissions or PermissionManager(settings.PERMISSION_GRANTS)
        self.context = KernelContext(self.events, self.permissions)

        # Core components
        self.conversation = Conversation()
        self.router = IntentRouter()
        self.last_route: RouteDecision | None = None
        self.memory = memory or MemoryManager()
        self.auto_memory = AutoMemory(self.memory)

        # Tools
        self.tool_manager = ToolManager(self.permissions)
        for tool in (default_tools(self.memory) if tools is None else tools):
            self.tool_manager.register(tool)
        self.executor = ToolExecutor(self.tool_manager, self.events)

        # An action waiting for the user's yes/no: (tool name, result).
        self.pending: tuple[str, ToolResult] | None = None

        # Kernel managers
        self.service_manager = ServiceManager(self.events)
        self.plugin_manager = PluginManager(self.context)

        # Register services. BrainService is the only owner of the Brain.
        self.service_manager.register(
            BrainService(brain)
        )

        self.running = False

    @property
    def brain(self) -> Brain:
        return self.service_manager.get("Brain").brain

    def register_plugin(self, plugin: BasePlugin) -> None:
        """Registers a plugin. Call before ``start()``."""
        if self.running:
            raise RuntimeError("Register plugins before starting the kernel.")
        self.plugin_manager.register(plugin)

    def start(self):

        if self.running:
            return

        logger.info("Kernel starting")

        self.service_manager.start_all()
        self.plugin_manager.initialize_all()

        self.running = True

        logger.info("Kernel online: %s", self.status())
        self.events.emit(topics.KERNEL_STARTED, self.status(), source=SOURCE)

    # --- Messages -----------------------------------------------------------

    def process_message(self, message: str) -> str:

        if not self.running:
            raise RuntimeError(
                "Kernel is not running."
            )

        self.conversation.add_user(message)
        self.events.emit(topics.MESSAGE_RECEIVED, message, source=SOURCE)

        response = self._answer_pending(message)

        if response is None:
            self.last_route = self.route(message)
            response = self._handle(self.last_route, message)

        self.conversation.add_assistant(
            response
        )
        self.events.emit(topics.RESPONSE_GENERATED, response, source=SOURCE)

        return response

    def _answer_pending(self, message: str) -> str | None:
        """Handles a yes/no to a pending action. Returns None if there wasn't one."""
        if self.pending is None:
            return None

        name, pending = self.pending
        self.pending = None
        text = message.strip()

        if _YES.match(text):
            self.events.emit(topics.CONFIRMATION_ANSWERED, {"tool": name, "approved": True}, source=SOURCE)
            return self.executor.confirm(name, pending).message

        self.events.emit(topics.CONFIRMATION_ANSWERED, {"tool": name, "approved": False}, source=SOURCE)

        if _NO.match(text):
            return "Okay, I won't do that."

        # Anything else cancels the pending action and is handled normally.
        logger.info("Pending %s action cancelled by a new message", name)
        return None

    def _handle(self, decision: RouteDecision, message: str) -> str:

        if decision.needs_clarification:
            return self._clarifying_question(decision)

        if self.executor.can_handle(decision):
            result = self.executor.execute(decision)
            if result.needs_confirmation:
                name = self.executor.tool_for(decision)
                self.pending = (name, result)
                self.events.emit(topics.CONFIRMATION_REQUESTED, {"tool": name}, source=SOURCE)
            return result.message

        return self._converse(message)

    # --- Conversation with memory ---------------------------------------------

    @property
    def auto_memory_enabled(self) -> bool:
        return not self.permissions.missing(AUTO_MEMORY, AUTO_MEMORY_PERMISSIONS)

    def _converse(self, message: str) -> str:
        """
        Ordinary conversation: learn from the message, give the Brain the
        relevant memories, and tell the user if anything was saved.
        """
        learned = []
        context = None

        if self.auto_memory_enabled:
            try:
                learned = self.auto_memory.learn_from(message)
                context = self.auto_memory.context_message(message)
            except Exception:
                # Memory problems must never stop AURORA from answering.
                logger.exception("Automatic memory failed")

        for result in learned:
            self.events.emit(
                topics.MEMORY_LEARNED,
                {"action": result.action, "attribute": result.fact.attribute,
                 "value": result.fact.value,
                 "previous": result.previous.value if result.previous else None},
                source=SOURCE,
            )

        history = self.conversation.history()
        if context is not None:
            # Right after the personality prompt; not stored in the conversation.
            history = history[:1] + [context] + history[1:]

        response = self.brain.ask(history)

        note = AutoMemory.summary(learned) if settings.MEMORY_ANNOUNCE else None
        return f"{response}\n\n{note}" if note else response

    @staticmethod
    def _clarifying_question(decision: RouteDecision) -> str:
        options = [alt.intent.value for alt in decision.alternatives if alt.uses_tool][:2]
        if len(options) == 2:
            return f"Did you want me to use {options[0]} or {options[1]}? Could you rephrase?"
        return "I didn't catch that. Could you say it another way?"

    def route(self, message: str) -> RouteDecision:
        """Returns (and publishes) the routing decision for a message."""
        decision = self.router.route(message)
        # Argument names only; their values are the user's words.
        logger.info(
            "Route: %s (%.2f) -> %s, arguments: %s",
            decision.intent.value, decision.confidence,
            decision.target, sorted(decision.arguments) or "none",
        )
        self.events.emit(topics.ROUTE_DECIDED, decision.to_dict(), source=SOURCE)
        return decision

    def clear_conversation(self):

        self.conversation.clear()
        self.pending = None
        logger.info("Conversation cleared")
        self.events.emit(topics.CONVERSATION_CLEARED, source=SOURCE)

    # --- Status ---------------------------------------------------------------

    def tool_status(self) -> dict:
        status = {}
        status["AutoMemory"] = (
            "Ready" if self.auto_memory_enabled
            else "Blocked (needs memory.read, memory.write)"
        )
        for tool in self.tool_manager.tools.values():
            missing = self.permissions.missing(tool.name, tool.required_permissions)
            status[tool.name] = (
                "Ready" if not missing
                else "Blocked (needs " + ", ".join(p.value for p in missing) + ")"
            )
        return status

    def status(self) -> dict:
        return {
            "running": self.running,
            "services": self.service_manager.status(),
            "plugins": self.plugin_manager.status(),
            "tools": self.tool_status(),
        }

    def shutdown(self):

        if not self.running:
            return

        logger.info("Kernel shutting down")
        self.events.emit(topics.KERNEL_STOPPING, source=SOURCE)

        self.plugin_manager.shutdown_all()
        self.service_manager.stop_all()

        self.running = False

        logger.info("Kernel offline")
        self.events.emit(topics.KERNEL_STOPPED, source=SOURCE)
