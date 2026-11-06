from aurora.config import settings


class Personality:
    """
    Defines AURORA's identity. The prompt comes from
    ``settings.PERSONALITY_PROMPT`` (editable in the Command Centre).
    """

    def system_message(self):

        return {
            "role": "system",
            "content": settings.PERSONALITY_PROMPT,
        }
