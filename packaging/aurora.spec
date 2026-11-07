# PyInstaller build for the AURORA Command Centre.
# Build with:  .venv\Scripts\python.exe packaging\build_windows.py
#
# One-folder build (fast start-up, easy to update). The executable doesn't
# contain AURORA's data: memory, models, logs, Documents and
# local_settings.py stay in the project folder, which build_windows.py
# records in aurora_home.txt next to the .exe.

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "aurora", "interface", "assets", "aurora.ico")
NAME = "AURORA Command Centre"

# Every AURORA module (many are imported lazily), except personal settings,
# and AURORA's own neural network (the Model manager runs it, and the
# Training monitor starts "<exe> --train …").
hidden = collect_submodules("aurora", filter=lambda name: name != "aurora.config.local_settings")
hidden += collect_submodules("aurora_core")
# Voice (Phase 12): speech recognition (Vosk), microphone (sounddevice) and
# Windows text-to-speech (SAPI through comtypes). The speech model itself
# stays in Models/ in the data folder.
hidden += collect_submodules("vosk") + ["sounddevice", "comtypes.client", "comtypes.stream"]

# OCR (RapidOCR) adds its own folder to sys.path and imports its parts by
# name as top-level modules ("ch_ppocr_v3_det"), which PyInstaller can't see.
# Without these the .exe can't read text (found by `--vision-check`).
import rapidocr_onnxruntime

RAPIDOCR = os.path.dirname(rapidocr_onnxruntime.__file__)
OCR_PARTS = ["ch_ppocr_v3_det", "ch_ppocr_v3_rec", "ch_ppocr_v2_cls"]
hidden += OCR_PARTS + [f"{part}.{module}" for part in OCR_PARTS for module in ("utils",)]
hidden += ["ch_ppocr_v3_det.text_detect", "ch_ppocr_v3_rec.text_recognize", "ch_ppocr_v2_cls.text_cls"]

datas = [(ICON, os.path.join("aurora", "interface", "assets"))]
datas += collect_data_files("rapidocr_onnxruntime")      # OCR models and config
datas += collect_data_files("vosk")
binaries = collect_dynamic_libs("vosk")                  # libvosk.dll and its runtime

a = Analysis(
    [os.path.join(ROOT, "command_centre.py")],
    pathex=[ROOT, RAPIDOCR],
    hiddenimports=hidden,
    datas=datas,
    binaries=binaries,
    excludes=[
        "aurora.config.local_settings",   # Personal details stay out of the .exe
        "pyttsx3",                        # replaced by direct SAPI (aurora/voice/speaker.py)
        # Development and unused GUI toolkits.
        "pytest", "_pytest", "tkinter", "matplotlib", "IPython",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    icon=ICON,
    console=False,          # A windowed app: no console window.
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
