"""
Preferences changed from the Command Centre's Settings page.

They are stored in ``aurora_preferences.json`` (ignored by git) and
applied on top of settings.py and local_settings.py at start-up. Only the
keys below are accepted, each with a type check, so the file can't be
used to change anything else (permissions, paths, devices…).
"""

import json
import logging
from pathlib import Path

from aurora.config import settings

logger = logging.getLogger(__name__)

PATH = settings.PROJECT_ROOT / "aurora_preferences.json"

# Preferences that change a setting: key -> (settings attribute, type, check)
BACKEND = {
    "model": ("DEFAULT_MODEL", str, lambda v: 0 < len(v) <= 100),
    "personality": ("PERSONALITY_PROMPT", str, lambda v: 0 < len(v) <= 4000),
    "memory_announce": ("MEMORY_ANNOUNCE", bool, lambda v: True),
    "memory_context_limit": ("MEMORY_CONTEXT_LIMIT", int, lambda v: 0 <= v <= 20),
    "voice_name": ("VOICE_NAME", str, lambda v: len(v) <= 100),
    "voice_rate": ("VOICE_RATE", int, lambda v: 80 <= v <= 300),
}

# Preferences used only by the interface: key -> (type, default, check)
INTERFACE = {
    "accent": (str, "cyan", lambda v: v in ("cyan", "blue", "purple")),
    "start_page": (str, "Dashboard", lambda v: 0 < len(v) <= 40),
    "start_maximized": (bool, False, lambda v: True),
    "animations": (bool, True, lambda v: True),
    "chat_streaming": (bool, True, lambda v: True),
    "minimize_to_tray": (bool, True, lambda v: True),
    "close_to_tray": (bool, False, lambda v: True),
}


def _valid(key: str, value) -> bool:
    if key in BACKEND:
        _, kind, check = BACKEND[key]
    elif key in INTERFACE:
        kind, _, check = INTERFACE[key]
    else:
        return False
    if kind is int and isinstance(value, bool):
        return False
    return isinstance(value, kind) and check(value)


def defaults() -> dict:
    values = {key: getattr(settings, attribute) for key, (attribute, _, _) in BACKEND.items()}
    values.update({key: default for key, (_, default, _) in INTERFACE.items()})
    return values


def load(path: Path | None = None) -> dict:
    """Saved preferences merged over the defaults; invalid entries are ignored."""
    path = Path(path or PATH)
    values = defaults()
    if not path.is_file():
        return values
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Preferences file is unreadable; using defaults")
        return values
    if not isinstance(saved, dict):
        return values
    for key, value in saved.items():
        if _valid(key, value):
            values[key] = value
        else:
            logger.warning("Ignoring invalid preference: %s", key)
    return values


def apply(values: dict) -> None:
    """Puts the backend preferences into settings (call before creating the engine)."""
    for key, (attribute, _, _) in BACKEND.items():
        if key in values and _valid(key, values[key]):
            setattr(settings, attribute, values[key])


def save(values: dict, path: Path | None = None) -> None:
    path = Path(path or PATH)
    clean = {key: value for key, value in values.items() if _valid(key, value)}
    path.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
