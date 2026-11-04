"""
Global settings for Project AURORA.

Every configurable value lives here so other modules never hardcode
model names, paths or versions.
"""

from pathlib import Path

APP_NAME = "AURORA"
VERSION = "0.5.0"

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
