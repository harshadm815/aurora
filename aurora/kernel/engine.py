from aurora.brain.brain import Brain
from aurora.conversation.conversation import Conversation
from aurora.kernel.service_manager import ServiceManager
from aurora.plugins.plugin_manager import PluginManager
from aurora.services.brain_service import BrainService


class AuroraEngine:
    """
    The central orchestrator of AURORA.

    The Engine coordinates all major systems but does not
    perform reasoning itself.
    """

    def __init__(self):

        # Core components
        self.conversation = Conversation()
        self.brain = Brain()

        # Kernel managers
        self.service_manager = ServiceManager()
        self.plugin_manager = PluginManager()

        # Register services
        self.service_manager.register(
            BrainService()
        )

        self.running = False

    def start(self):

        print("\n==============================")
        print(" AURORA KERNEL INITIALIZING ")
        print("==============================\n")

        self.service_manager.start_all()
        self.plugin_manager.initialize_all()

        self.running = True

        print("AURORA Kernel Online.\n")

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

    def shutdown(self):

        print("\nShutting down AURORA...\n")

        self.plugin_manager.shutdown_all()
        self.service_manager.stop_all()

        self.running = False

        print("AURORA Kernel Offline.")