"""
Builds "dist\\AURORA Command Centre\\AURORA Command Centre.exe".

    .venv\\Scripts\\python.exe packaging\\build_windows.py

After building, it writes aurora_home.txt next to the .exe so the
executable uses this project's data (memory, models, logs, Documents,
local_settings.py). Move the data elsewhere? Edit that one line.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "AURORA Command Centre"


def main() -> int:
    icon = ROOT / "aurora" / "interface" / "assets" / "aurora.ico"
    if not icon.is_file():
        print("Icon missing; generate it with: python -m aurora.interface.windows.icon")
        return 1

    command = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "aurora.spec"),
        "--noconfirm", "--clean",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
    ]
    print("Running:", " ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    folder = ROOT / "dist" / NAME
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
