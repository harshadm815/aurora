from aurora.knowledge.fact import Fact


class FactExtractor:
    """
    Converts natural language into structured facts.
    """

    def extract(self, text: str):

        text_lower = text.lower()

        if "my name is" in text_lower:

            value = text.split("is", 1)[1].strip()

            return Fact(
                category="Personal",
                entity="User",
                attribute="Name",
                value=value,
                priority=5,
            )

        if "i like" in text_lower:

            value = text.split("like", 1)[1].strip()

            return Fact(
                category="Preference",
                entity="User",
                attribute="Likes",
                value=value,
                priority=4,
            )

        return None