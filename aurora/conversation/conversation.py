from aurora.personality.personality import Personality


class Conversation:

    def __init__(self):
        personality = Personality()

        self.messages = [
            personality.system_message()
        ]

    def add_user(self, message):
        self.messages.append(
            {
                "role": "user",
                "content": message
            }
        )

    def add_assistant(self, message):
        self.messages.append(
            {
                "role": "assistant",
                "content": message
            }
        )

    def history(self):
        return self.messages

    def clear(self):
        """
        Clears the current conversation while preserving
        AURORA's personality.
        """
        personality = Personality()
        self.messages = [
            personality.system_message()
        ]