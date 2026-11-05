"""
Global settings for Project AURORA.

Every configurable value lives here so other modules never hardcode
model names, paths or versions.
"""

from pathlib import Path

APP_NAME = "AURORA"
VERSION = "0.16.0"

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
    # Microphone, used only while you've asked AURORA to listen.
    "Voice": ["microphone"],
    # Camera, used only for "what do you see?", "read this" and "watch".
    "Camera": ["camera"],
    # Room sensors (temperature, humidity, pressure, motion).
    "Sensors": ["sensors"],
    # Heart rate / blood oxygen. Remove this line to switch health readings off.
    "Health": ["sensors", "health_data"],
    # Switching devices. Every change still asks you to confirm.
    "Devices": ["hardware_control"],
    # The research team: reads your research log and saves sessions you approve.
    # (The tools it uses keep their own permissions.)
    "Research": ["memory.read", "memory.write"],
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

# Voice
#
# Listening only happens when you type "listen" or "voice on". Audio is
# processed offline and never saved.
VOICE_MODEL_DIR = PROJECT_ROOT / "Models" / "vosk-model-small-en-us-0.15"
VOICE_INPUT_DEVICE = None          # None = the Windows default microphone.
VOICE_LISTEN_SECONDS = 10          # Longest single request.
VOICE_END_SILENCE = 1.2            # Seconds without new words that end a request.
VOICE_RATE = 185                   # Speaking speed (words per minute).
VOICE_NAME = "Zira"                # Part of a Windows voice name ("David", "Zira"); "" = default.
VOICE_MAX_SPOKEN_CHARS = 500       # Longer replies end with "the rest is on screen".
# Words the small speech model mishears, e.g. {"pony": "Pune"}.
VOICE_CORRECTIONS: dict[str, str] = {}

# Vision
#
# The camera is opened only when you ask ("what do you see?", "read this",
# "watch"). Pictures are analysed on this computer and never saved.
VISION_CAMERA_INDEX = 0            # 0 = the built-in webcam.
VISION_DETECTOR_MODEL = PROJECT_ROOT / "Models" / "object_detection_nanodet_2022nov.onnx"
VISION_MIN_CONFIDENCE = 0.4        # How sure the detector must be to mention an object.
VISION_OCR_MIN_CONFIDENCE = 0.5
VISION_MOTION_THRESHOLD = 0.02     # Fraction of the picture that must change (2 %).
VISION_MOTION_COOLDOWN = 10        # Seconds between motion alerts in "watch" mode.

# Sensors
#
# Where readings come from. Until real hardware is connected, a simulator
# provides plausible values, and every answer says they are simulated.
# For the ESP32 firmware in firmware/esp32_sensor_hub, replace the list with:
#     {"name": "Room ESP32", "type": "http",
#      "url": "http://aurora-sensors.local/readings", "location": "room"},
SENSOR_SOURCES: list[dict] = [
    {"name": "Simulator", "type": "simulated", "location": "room"},
]
SENSOR_DATABASE_PATH = PROJECT_ROOT / "sensor_data.db"
SENSOR_TIMEOUT_SECONDS = 5
# If a device doesn't answer, a stored reading up to this old is used (and labelled).
SENSOR_MAX_AGE_MINUTES = 10
SENSOR_RETENTION_DAYS = 30         # Room readings are deleted after this.
HEALTH_RETENTION_DAYS = 7          # Health readings are deleted sooner.

# Devices
#
# Only devices listed here can be controlled, and only with the actions
# their kind allows ("light": on/off/toggle/brightness 1-100 %,
# "fan": on/off/toggle/speed 1-3, "plug": on/off/toggle). Every change
# asks "yes / no" first unless you add "confirm": False to a device.
# "driver": "simulated" until real hardware exists; "esp32" uses the
# controller in firmware/esp32_device_controller.
DEVICES: list[dict] = [
    {"id": "desk_light", "name": "desk light", "kind": "light",
     "aliases": ["light", "lamp", "desk lamp"], "driver": "simulated"},
    {"id": "fan", "name": "fan", "kind": "fan",
     "aliases": ["ceiling fan"], "driver": "simulated"},
]
DEVICE_CONTROLLER_URL = "http://aurora-devices.local"
DEVICE_CONTROLLER_TOKEN = ""       # Set in local_settings.py; must match the firmware.
DEVICE_MAX_CHANGES_PER_MINUTE = 6  # Per device, to stop rapid on/off switching.

# Reminders (this session only)
REMINDER_MAX_ACTIVE = 20
REMINDER_MAX_HOURS = 24

# Personal overrides
#
# Values in aurora/config/local_settings.py (not tracked by git) replace
# the ones above. Use it for personal details such as RESEARCH_CONTACT,
# so they never end up in the project's history.
try:
    from aurora.config.local_settings import *  # noqa: F401,F403
except ImportError:
    pass
