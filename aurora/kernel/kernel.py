from aurora.brain.brain import Brain
from aurora.conversation.conversation import Conversation


class AuroraEngine:
    """
    Central orchestrator for AURORA.
    """

    def __init__(self):
        self.brain = Brain()
        self.conversation = Conversation()
        self.running = False

    def start(self):
        self.running = True
        print("AURORA Core Engine: ONLINE")

    def process_message(self, message: str) -> str:
        if not self.running:
            raise RuntimeError("AURORA Engine is not running.")

        # Add the user's message to working memory
        self.conversation.add_user(message)

        # Send the conversation history to the AI
        response = self.brain.ask(
            self.conversation.history()
        )

        # Remember the AI's response in working memory
        self.conversation.add_assistant(response)

        return response

    def clear_conversation(self):
        self.conversation.clear()

    def shutdown(self):
        self.running = False
        print("AURORA Core Engine: OFFLINE")