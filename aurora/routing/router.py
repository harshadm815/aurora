from aurora.routing.intent import Intent


class IntentRouter:

    def route(
        self,
        message: str
    ) -> Intent:

        text = message.lower()

        if any(
            op in text
            for op in [
                "+",
                "-",
                "*",
                "/"
            ]
        ):
            return Intent.CALCULATOR

        if "weather" in text:
            return Intent.WEATHER

        if any(
            word in text
            for word in [
                "remember",
                "memory",
                "my favourite",
                "my favorite"
            ]
        ):
            return Intent.MEMORY

        return Intent.REASONING