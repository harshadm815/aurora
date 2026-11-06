# PyInstaller build for the AURORA Command Centre.
# Build with:  .venv\Scripts\python.exe packaging\build_windows.py
#
# One-folder build (fast start-up, easy to update). The executable doesn't
# contain AURORA's data: memory, models, logs, Documents and
# local_settings.py stay in the project folder, which build_windows.py
# records in aurora_home.txt next to the .exe.

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "aurora", "interface", "assets", "aurora.ico")
NAME = "AURORA Command Centre"

# Every AURORA module (many are imported lazily), except personal settings.
hidden = collect_submodules("aurora", filter=lambda name: name != "aurora.config.local_settings")

datas = [(ICON, os.path.join("aurora", "interface", "assets"))]
datas += collect_data_files("rapidocr_onnxruntime")      # OCR models and config

a = Analysis(
    [os.path.join(ROOT, "command_centre.py")],
    pathex=[ROOT],
    hiddenimports=hidden,
    datas=datas,
    excludes=[
        "aurora.config.local_settings",   # Personal details stay out of the .exe
        # Voice runs from the terminal only (for now); not needed in the window.
        "vosk", "sounddevice", "pyttsx3", "comtypes",
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
