"""
Global settings for Project AURORA.

Every configurable value lives here so other modules never hardcode
model names, paths or versions.
"""

from pathlib import Path

APP_NAME = "AURORA"
VERSION = "0.11.1"

USER_NAME = "Harry"

# Language model
DEFAULT_MODEL = "tinyllama"
# Give up on a reply after this long, so a stalled model can't freeze AURORA.
MODEL_TIMEOUT_SECONDS = 120
# Longest reply the model may generate (stops runaway, looping output).
MODEL_MAX_REPLY_TOKENS = 400

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

# Planning
#
# Most steps one message may be split into.
PLAN_MAX_STEPS = 5

# Memory
#
# Facts extracted from conversation need at least this confidence to be
# saved automatically.
MEMORY_MIN_CONFIDENCE = 0.7
# Most memories given to the Brain as context for one reply.
MEMORY_CONTEXT_LIMIT = 5
# Tell the user when AURORA saves or updates a memory on its own.
MEMORY_ANNOUNCE = True

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
    # Learns facts from conversation and gives relevant ones to the Brain.
    "AutoMemory": ["memory.read", "memory.write"],
    "Weather": ["network"],   # Sends place names to open-meteo.com.
    "Wikipedia": ["network"],  # Sends the topic to en.wikipedia.org.
    "arXiv": ["network"],      # Sends the topic to export.arxiv.org.
    # Reads .txt/.md/.pdf files, only inside DOCUMENTS_DIR.
    "Documents": ["filesystem.read"],
}

# Research
#
# Wikipedia refuses requests that don't include contact details (an email
# address or a web page URL). Whatever you put here is sent to Wikipedia
# and arXiv with each lookup. Leave it empty to keep Wikipedia switched off.
RESEARCH_CONTACT: str = ""
RESEARCH_TIMEOUT_SECONDS = 15
# Longest encyclopedia summary shown.
RESEARCH_MAX_CHARS = 700
ARXIV_MAX_RESULTS = 5

# Documents: the only folder AURORA may read files from.
DOCUMENTS_DIR = PROJECT_ROOT / "Documents"
DOCUMENT_MAX_BYTES = 20_000_000
# How much of a document "read <file>" shows.
DOCUMENT_READ_CHARS = 1500

# Weather
#
# Used when you ask "what's the weather?" without naming a place.
# None means AURORA will ask which place you mean.
DEFAULT_LOCATION: str | None = None
WEATHER_TIMEOUT_SECONDS = 10

# Personal overrides
#
# Values in aurora/config/local_settings.py (not tracked by git) replace
# the ones above. Use it for personal details such as RESEARCH_CONTACT,
# so they never end up in the project's history.
try:
    from aurora.config.local_settings import *  # noqa: F401,F403
except ImportError:
    pass
