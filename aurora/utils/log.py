"""
Logging setup for AURORA.

Modules get a logger with ``logging.getLogger(__name__)``.
``setup_logging()`` is called once, by the entry point.

Everything goes to Logs/aurora.log. Only warnings and errors are shown
in the terminal, so diagnostics don't clutter the conversation.
"""

import logging

from aurora.config import settings

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: str = settings.LOG_LEVEL) -> None:
    root = logging.getLogger("aurora")

    if root.handlers:
        return  # Already configured.

    root.setLevel(level)

    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(settings.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root.addHandler(console_handler)
