"""
Global settings for Project AURORA.

Every configurable value lives here so other modules never hardcode
model names, paths or versions.
"""

import sys
from pathlib import Path

APP_NAME = "AURORA"
VERSION = "0.31.0"

USER_NAME = "Harry"

# AURORA's identity, given to the language model as its first instruction.
PERSONALITY_PROMPT = (
    "You are AURORA, an intelligent modular AI assistant created by Harry. "
    "Be helpful, honest, concise, and explain your reasoning clearly. "
    "If you don't know something, say so instead of inventing information. "
    "Understand the user's tone while replying."
)

# Language model
DEFAULT_MODEL = "tinyllama"
# Give up on a reply after this long, so a stalled model can't freeze AURORA.
MODEL_TIMEOUT_SECONDS = 120
# Longest reply the model may generate (stops runaway, looping output).
MODEL_MAX_REPLY_TOKENS = 400

# Paths are absolute so AURORA uses the same files no matter which
# folder it is started from.
#
# When AURORA runs as a packaged .exe, its data (memory, models, logs,
# Documents, personal settings) lives outside the bundle: in the folder
# named in "aurora_home.txt" next to the .exe, or else the .exe's folder.
def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        exe_folder = Path(sys.executable).resolve().parent
        pointer = exe_folder / "aurora_home.txt"
        if pointer.is_file():
            # utf-8-sig: Notepad (and PowerShell 5) may add a byte-order mark,
            # which would otherwise make the path not match any folder.
            home = pointer.read_text(encoding="utf-8-sig").strip()
            if home and Path(home).is_dir():
                return Path(home).resolve()
        return exe_folder
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _project_root()
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
# Keep the sentence each fact was learned from, so AURORA can say how it
# knows something ("how do you know where I live?"). Deleted with the fact.
MEMORY_KEEP_SOURCE_TEXT = True

# Episodic memory: one short summary per completed task ("Checked the
# weather in Pune"), never the text of your messages. Kept this many days.
# Say "stop recording activity" / "clear my activity history" to control it.
EPISODES_ENABLED = True
EPISODE_RETENTION_DAYS = 30

# Procedural memory: routines you teach ("when I say morning briefing, ...").
ROUTINE_MAX = 20

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
    # Your memory switches: "don't remember that", "stop learning", activity history.
    "MemoryControl": ["memory.read", "memory.write"],
    # Episodic memory: "what did we do today?"
    "Episodes": ["memory.read"],
    # Procedural memory: routines you teach ("when I say morning briefing, ...").
    "Routines": ["memory.read", "memory.write"],
    # Learns facts from conversation and gives relevant ones to the Brain.
    "AutoMemory": ["memory.read", "memory.write"],
    "Weather": ["network"],   # Sends place names to open-meteo.com.
    "Wikipedia": ["network"],  # Sends the topic to en.wikipedia.org.
    "arXiv": ["network"],      # Sends the topic to export.arxiv.org.
    # Reads .txt/.md/.pdf files, only inside DOCUMENTS_DIR.
    "Documents": ["filesystem.read"],
    # Questions, summaries and comparisons over the same documents, with citations.
    "Knowledge": ["filesystem.read"],
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

# Knowledge index: your documents split into passages for search and
# questions ("what do my notes say about …"). Rebuilt from the Documents
# folder whenever files change; delete the file to start afresh.
KNOWLEDGE_INDEX_PATH = PROJECT_ROOT / "knowledge_index.db"

# AURORA's own models (aurora_core): checkpoints and training runs live
# under MODELS_DIR, training text under DATA_DIR (neither is in git).
MODELS_DIR = PROJECT_ROOT / "Models"
DATA_DIR = PROJECT_ROOT / "Data"
# Mark language-model answers to factual questions as unverified
# ("from my language model's training, not checked against a source").
LABEL_MODEL_ANSWERS = True

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
VOICE_VOLUME = 1.0                 # Spoken replies: 0.0 (silent) to 1.0 (full).
# Voice activity detection (aurora/voice/vad.py): when you start and stop talking.
VOICE_NO_SPEECH_SECONDS = 5        # Stop listening if nobody starts speaking.
VOICE_VAD_RATIO = 3.0              # Speech must be this many times louder than the room.
VOICE_VAD_MIN_RMS = 300            # ...and at least this loud (16-bit scale), whatever the room.
# Stop a spoken reply when you start talking. Needs headphones: with
# speakers, the microphone hears AURORA itself and would cut it off.
VOICE_BARGE_IN = False
# Words the small speech model mishears, e.g. {"pony": "Pune"}.
VOICE_CORRECTIONS: dict[str, str] = {}

# Vision
#
# The camera is opened only when you ask ("what do you see?", "read this",
# "watch"). Pictures are analysed on this computer and never saved.
VISION_CAMERA_INDEX = 0            # 0 = the built-in webcam.
VISION_DETECTOR_MODEL = PROJECT_ROOT / "Models" / "object_detection_nanodet_2022nov.onnx"
VISION_MIN_CONFIDENCE = 0.4        # Below this an object isn't mentioned at all.
VISION_CERTAIN_CONFIDENCE = 0.5    # At or above this, and seen again in the mirrored picture,
                                   # an object is stated as fact; in between it's "might be".
VISION_MAX_IMAGE_MB = 25           # Larger image files are refused.
VISION_MAX_PIXELS = 50_000_000     # Larger pictures are refused before decoding.
VISION_ANALYSIS_MAX_SIDE = 1600    # Larger pictures are scaled down for analysis.
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
# A device may say how long ago it measured each value ("ages" in its JSON);
# values older than this are refused rather than shown as current.
SENSOR_MAX_SAMPLE_AGE_SECONDS = 300
# Other simple sensors, e.g. {"name": "soil_moisture", "label": "Soil moisture",
# "unit": "%", "minimum": 0, "maximum": 100} (see aurora/sensors/reading.py).
SENSOR_EXTRA_QUANTITIES: list[dict] = []
# Background watching (off unless you switch it on in the Sensor Centre or here):
# room sensors are read every SENSOR_MONITOR_SECONDS, readings go on the event
# bus, and a reading that becomes poor (e.g. CO2 above 1500 ppm) raises an alert,
# at most once per SENSOR_ALERT_COOLDOWN_MINUTES per quantity. Health sensors are
# never read in the background.
SENSOR_MONITOR = False
SENSOR_MONITOR_SECONDS = 60
SENSOR_ALERT_COOLDOWN_MINUTES = 30
SENSOR_NOTIFICATIONS = True        # Show alerts as Windows notifications (Command Centre).

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
    # The packaged .exe deliberately doesn't contain local_settings.py
    # (it holds personal details); read it from the data folder instead.
    _local = PROJECT_ROOT / "aurora" / "config" / "local_settings.py"
    if getattr(sys, "frozen", False) and _local.is_file():
        import runpy

        for _name, _value in runpy.run_path(str(_local)).items():
            if _name.isupper():
                globals()[_name] = _value
