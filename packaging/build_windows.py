"""
Builds "dist\\AURORA Command Centre\\AURORA Command Centre.exe".

    .venv\\Scripts\\python.exe packaging\\build_windows.py [--into dist-new]

After building, it writes aurora_home.txt next to the .exe so the
executable uses this project's data (memory, models, logs, Documents,
local_settings.py). Move the data elsewhere? Edit that one line.

Before replacing the old build it asks the Wake Engine helper to quit
(politely: forcing AURORA's processes closed has twice left the old
folder locked until Windows restarted) and stops if any program is still
running from that folder. If the old folder can't be removed anyway,
build into another one with ``--into`` and point the shortcut there.
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "AURORA Command Centre"


def running_from(folder: Path) -> list[int]:
    """Process ids of programs started from ``folder`` (Windows only; [] elsewhere)."""
    if sys.platform != "win32":
        return []
    script = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.ExecutablePath -like '{folder}\\*' }} | "
              "ForEach-Object { $_.ProcessId }")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def stop_wake_helper() -> None:
    try:
        sys.path.insert(0, str(ROOT))
        from PySide6.QtCore import QCoreApplication
        _app = QCoreApplication.instance() or QCoreApplication([])
        from aurora.wake import launcher
        if launcher.stop_helper():
            print("Asked the Wake Engine helper to quit.")
    except Exception as error:          # no PySide6, no helper: nothing to stop
        print(f"(Couldn't ask the Wake Engine helper to quit: {error})")


def prepare(folder: Path) -> bool:
    stop_wake_helper()
    for _ in range(40):                 # up to 20 s for programs to close by themselves
        if not running_from(folder):
            break
        time.sleep(0.5)
    still = running_from(folder)
    if still:
        print(f"AURORA is still running from {folder} (process {', '.join(map(str, still))}). "
              "Exit it from the tray icon (and switch off the Wake Engine) first. Nothing was changed.")
        return False
    if folder.exists():
        try:
            shutil.rmtree(folder)
        except OSError as error:
            print(f"The old build in {folder} is locked ({error.__class__.__name__}). Restart Windows to free it, "
                  "or build into another folder: packaging\\build_windows.py --into dist-new")
            return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--into", default="dist", help="output folder inside the project (default: dist)")
    args = parser.parse_args(argv)

    icon = ROOT / "aurora" / "interface" / "assets" / "aurora.ico"
    if not icon.is_file():
        print("Icon missing; generate it with: python -m aurora.interface.windows.icon")
        return 1

    dist = ROOT / args.into
    folder = dist / NAME
    if not prepare(folder):
        return 1

    command = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "aurora.spec"),
        "--noconfirm", "--clean",
        "--distpath", str(dist),
        "--workpath", str(ROOT / "build"),
    ]
    print("Running:", " ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    exe = folder / f"{NAME}.exe"
    if not exe.is_file():
        print("Build finished but the executable is missing.")
        return 1

    (folder / "aurora_home.txt").write_text(str(ROOT), encoding="utf-8")
    bundled = [p for p in folder.rglob("local_settings*")]
    if bundled:
        print("WARNING: personal settings were bundled:", bundled)
        return 1

    size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    print(f"\nBuilt {exe}")
    print(f"Folder size: {size / 1_000_000:.0f} MB")
    print(f"Data folder: {ROOT} (aurora_home.txt)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
