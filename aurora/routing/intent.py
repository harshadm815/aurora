from enum import Enum


class Intent(Enum):
    """What the user wants AURORA to do with a message."""

    CONVERSATION = "conversation"   # Answer with the Brain.
    CALCULATOR = "calculator"       # Evaluate arithmetic.
    WEATHER = "weather"             # Look up weather.
    REMEMBER = "remember"           # Store something the user explicitly asked to keep.
    RECALL = "recall"               # Retrieve something from long-term memory.
    FORGET = "forget"               # Delete something from long-term memory.
    CLARIFY = "clarify"             # Too ambiguous: ask the user what they meant.
