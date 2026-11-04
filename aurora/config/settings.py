"""
Global settings for Project AURORA.

Every configurable value lives here so other modules never hardcode
model names, paths or versions.
"""

from pathlib import Path

APP_NAME = "AURORA"
VERSION = "0.8.0"

USER_NAME = "Harry"

# Language model
DEFAULT_MODEL = "tinyllama"

# Paths are absolute so AURORA uses the same files no matter which
# folder it is started from.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = PROJECT_ROOT / "aurora_memory.db"
LOG_DIR = PROJECT_ROOT / "Logs"
LOG_FILE = LOG_DIR / "aurora.log"

# Logging
DEBUG = True
LOG_LEVEL = "DEBUG" if DEBUG else "INFO"

# Routing
#
# A tool rule must be more confident than this to beat a normal
# conversational answer from the Brain.
ROUTE_CONVERSATION_CONFIDENCE = 0.5
# If the two best tool candidates are closer than this, AURORA asks
# what was meant instead of guessing.
ROUTE_CLARIFY_MARGIN = 0.1

# Permissions
#
# Every plugin and tool is denied everything unless it is listed here.
# Keys are component names, values are permission names from
# aurora/kernel/permissions.py, for example:
#
#     PERMISSION_GRANTS = {
#         "Weather": ["network"],
#     }
#
# The built-in tools are granted only what they need. Remove a line to
# switch that capability off.
PERMISSION_GRANTS: dict[str, list[str]] = {
    "Remember": ["memory.write"],
    "Recall": ["memory.read"],
    "Forget": ["memory.read", "memory.write"],
    "Weather": ["network"],   # Sends place names to open-meteo.com.
}

# Weather
#
# Used when you ask "what's the weather?" without naming a place.
# None means AURORA will ask which place you mean.
DEFAULT_LOCATION: str | None = None
WEATHER_TIMEOUT_SECONDS = 10
