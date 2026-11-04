import logging

from aurora.brain.brain import Brain
from aurora.conversation.conversation import Conversation
from aurora.kernel.service_manager import ServiceManager
from aurora.plugins.plugin_manager import PluginManager
from aurora.services.brain_service import BrainService

logger = logging.getLogger(__name__)


class AuroraEngine:
    """
    The central orchestrator of AURORA.

    The Engine coordinates all major systems but does not
    perform reasoning itself. It does not print; the user
    interface (main.py) owns all terminal output.
    """

    def __init__(self, brain: Brain | None = None):

        # Core components
        self.conversation = Conversation()

        # Kernel managers
        self.service_manager = ServiceManager()
        self.plugin_manager = PluginManager()

        # Register services. BrainService is the only owner of the Brain.
        self.service_manager.register(
            BrainService(brain)
        )

        self.running = False

    @property
    def brain(self) -> Brain:
        return self.service_manager.get("Brain").brain

    def start(self):

        logger.info("Kernel starting")

        self.service_manager.start_all()
        self.plugin_manager.initialize_all()

        self.running = True

        logger.info("Kernel online: %s", self.service_manager.status())

    def process_message(self, message: str) -> str:

        if not self.running:
            raise RuntimeError(
                "Kernel is not running."
            )

        self.conversation.add_user(message)

        response = self.brain.ask(
            self.conversation.history()
        )

        self.conversation.add_assistant(
            response
        )

        return response

    def clear_conversation(self):

        self.conversation.clear()
        logger.info("Conversation cleared")

    def shutdown(self):

        logger.info("Kernel shutting down")

        self.plugin_manager.shutdown_all()
        self.service_manager.stop_all()

        self.running = False

        logger.info("Kernel offline")
