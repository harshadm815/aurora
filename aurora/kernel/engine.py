import logging

from aurora.brain.brain import Brain
from aurora.config import settings
from aurora.conversation.conversation import Conversation
from aurora.events import topics
from aurora.events.bus import EventBus
from aurora.kernel.context import KernelContext
from aurora.kernel.permissions import PermissionManager
from aurora.kernel.service_manager import ServiceManager
from aurora.plugins.plugin import BasePlugin
from aurora.plugins.plugin_manager import PluginManager
from aurora.routing.decision import RouteDecision
from aurora.routing.router import IntentRouter
from aurora.services.brain_service import BrainService

logger = logging.getLogger(__name__)

SOURCE = "Kernel"


class AuroraEngine:
    """
    The central orchestrator of AURORA.

    The Engine coordinates all major systems but does not
    perform reasoning itself. It does not print; the user
    interface (main.py) owns all terminal output.

    Startup order:   services → plugins → ``kernel.started``
    Shutdown order:  ``kernel.stopping`` → plugins → services → ``kernel.stopped``
    """

    def __init__(
        self,
        brain: Brain | None = None,
        permissions: PermissionManager | None = None,
    ):
        # Shared kernel facilities
        self.events = EventBus()
        self.permissions = permissions or PermissionManager(settings.PERMISSION_GRANTS)
        self.context = KernelContext(self.events, self.permissions)

        # Core components
        self.conversation = Conversation()
        self.router = IntentRouter()
        self.last_route: RouteDecision | None = None

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

    def process_message(self, message: str) -> str:

        if not self.running:
            raise RuntimeError(
                "Kernel is not running."
            )

        self.conversation.add_user(message)
        self.events.emit(topics.MESSAGE_RECEIVED, message, source=SOURCE)

        # Routing decides what *should* handle the message. Until tool
        # execution exists (Milestone 4) the Brain still answers everything;
        # the decision is logged and published so it can be observed.
        self.last_route = self.route(message)

        response = self.brain.ask(
            self.conversation.history()
        )

        self.conversation.add_assistant(
            response
        )
        self.events.emit(topics.RESPONSE_GENERATED, response, source=SOURCE)

        return response

    def route(self, message: str) -> RouteDecision:
        """Returns (and publishes) the routing decision for a message."""
        decision = self.router.route(message)
        logger.info(
            "Route: %s (%.2f) -> %s %s",
            decision.intent.value, decision.confidence,
            decision.target, dict(decision.arguments),
        )
        self.events.emit(topics.ROUTE_DECIDED, decision.to_dict(), source=SOURCE)
        return decision

    def clear_conversation(self):

        self.conversation.clear()
        logger.info("Conversation cleared")
        self.events.emit(topics.CONVERSATION_CLEARED, source=SOURCE)

    def status(self) -> dict:
        return {
            "running": self.running,
            "services": self.service_manager.status(),
            "plugins": self.plugin_manager.status(),
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
