from enum import Enum


class Intent(Enum):
    """What the user wants AURORA to do with a message."""

    CONVERSATION = "conversation"   # Answer with the Brain.
    CALCULATOR = "calculator"       # Evaluate arithmetic.
    NUMBERS = "numbers"             # Statistics and percentages on numbers in the message.
    WEATHER = "weather"             # Look up weather.
    REMEMBER = "remember"           # Store something the user explicitly asked to keep.
    RECALL = "recall"               # Retrieve something from long-term memory.
    FORGET = "forget"               # Delete something from long-term memory.
    MEMORY_CONTROL = "memory_control"  # Undo a memory, pause learning, episode switches.
    EPISODES = "episodes"           # What AURORA has done for the user (episodic memory).
    ROUTINES = "routines"           # Teach, list or delete routines (procedural memory).
    KNOWLEDGE = "knowledge"         # Look a topic up in an encyclopedia.
    PAPERS = "papers"               # Find research papers.
    KNOWLEDGE_BASE = "knowledge_base"  # Questions, summaries and comparisons over the documents.
    DOCUMENTS = "documents"         # List, search or read the user's documents.
    VISION = "vision"               # Look through the camera, or read text in front of it.
    SENSORS = "sensors"             # Room readings: temperature, humidity, pressure, motion.
    HEALTH = "health"               # The user's own heart rate / blood oxygen.
    DEVICES = "devices"             # Control or check a smart device (always confirmed).
    REMINDER = "reminder"           # Set, list or cancel reminders and timers.
    RESEARCH = "research"           # A research session run by the agent team.
    APPS = "apps"                   # Open or close a program from the APPS allow-list.
    FILES = "files"                 # Tidy an allowed folder (previewed, confirmed, undoable).
    SCHEDULE = "schedule"           # Add, list, pause or delete scheduled tasks.
    UNDO = "undo"                   # Reverse the last reversible action (asks first).
    CLARIFY = "clarify"             # Too ambiguous: ask the user what they meant.
