from enum import Enum


class Intent(Enum):

    REASONING = "reasoning"

    MEMORY = "memory"

    CALCULATOR = "calculator"

    WEATHER = "weather"

    UNKNOWN = "unknown"