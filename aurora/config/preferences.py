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
    "voice_volume": ("VOICE_VOLUME", float, lambda v: 0.0 <= v <= 1.0),
    "voice_pitch": ("VOICE_PITCH", int, lambda v: -10 <= v <= 10),
    "voice_synthesiser": ("VOICE_SYNTHESISER", str, lambda v: v in ("piper", "sapi")),
    # Wake Engine (aurora/wake/).
    "wake_key": ("WAKE_KEY", str, lambda v: v in ("right_ctrl", "space")),
    "wake_phrase": ("WAKE_PHRASE", str, lambda v: 0 < len(v.split()) <= 4 and len(v) <= 40
                    and all(c.isalpha() or c in " '" for c in v)),
    "wake_sound": ("WAKE_SOUND", bool, lambda v: True),
    "voice_barge_in": ("VOICE_BARGE_IN", bool, lambda v: True),
    # "" = the Windows default microphone (settings.VOICE_INPUT_DEVICE = None)
    "voice_input_device": ("VOICE_INPUT_DEVICE", str, lambda v: len(v) <= 200),
    # Background sensor watching (room sensors only, never health) and its notifications.
    "sensor_monitoring": ("SENSOR_MONITOR", bool, lambda v: True),
    "sensor_notifications": ("SENSOR_NOTIFICATIONS", bool, lambda v: True),
    # The network gate (aurora/network/gate.py).
    "internet_access": ("INTERNET_ACCESS", str, lambda v: v in ("on", "ask", "off")),
    "local_network_access": ("LOCAL_NETWORK_ACCESS", bool, lambda v: True),
    # AURORA Browser history on/off.
    "browser_history": ("BROWSER_HISTORY", bool, lambda v: True),
}

# Preferences used only by the interface: key -> (type, default, check)
INTERFACE = {
    "accent": (str, "cyan", lambda v: v in ("cyan", "blue", "purple")),
    "start_page": (str, "Dashboard", lambda v: 0 < len(v) <= 40),
    "start_maximized": (bool, False, lambda v: True),
    "animations": (bool, True, lambda v: True),
    # Lighter graphics while the PC is busy (aurora/interface/performance.py).
    "performance_mode": (str, "auto", lambda v: v in ("auto", "on", "off")),
    "chat_streaming": (bool, True, lambda v: True),
    "minimize_to_tray": (bool, True, lambda v: True),
    "close_to_tray": (bool, False, lambda v: True),
    "speak_replies": (bool, False, lambda v: True),
    "speak_reminders": (bool, False, lambda v: True),     # say reminders aloud when they're due
    "wake_enabled": (bool, False, lambda v: True),        # run the Wake Engine's helper
    "mobile_enabled": (bool, False, lambda v: True),      # serve the phone app (this PC only; see aurora/mobile)
}


def _valid(key: str, value) -> bool:
    if key in BACKEND:
        _, kind, check = BACKEND[key]
    elif key in INTERFACE:
        kind, _, check = INTERFACE[key]
    else:
        return False
    if kind in (int, float) and isinstance(value, bool):
        return False
    if kind is float and isinstance(value, int):
        value = float(value)                 # JSON writes 1.0 as 1
    return isinstance(value, kind) and check(value)


def defaults() -> dict:
    values = {key: getattr(settings, attribute) for key, (attribute, _, _) in BACKEND.items()}
    values["voice_input_device"] = settings.VOICE_INPUT_DEVICE if isinstance(settings.VOICE_INPUT_DEVICE, str) else ""
    values["voice_volume"] = float(values["voice_volume"])
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
    for key, (attribute, kind, _) in BACKEND.items():
        if key in values and _valid(key, values[key]):
            value = float(values[key]) if kind is float else values[key]
            if key == "voice_input_device":
                value = value or None          # "" means the Windows default microphone
            setattr(settings, attribute, value)


def save(values: dict, path: Path | None = None) -> None:
    path = Path(path or PATH)
    clean = {key: value for key, value in values.items() if _valid(key, value)}
    path.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
