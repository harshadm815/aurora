"""
Opening programs: "open notepad", "launch the calculator", "close notepad".

- Only programs listed in ``settings.APPS`` can be opened. Each has a
  fixed command; nothing from what you say ever becomes part of it, and
  no shell is involved (so "open notepad & del *" can't do anything but
  fail to find a program called that).
- The program must exist on this PC: a bare name is looked up on the
  system PATH, a full path must exist.
- AURORA only closes programs it started in this session, and asks
  first (unsaved work would be lost). Some Windows programs (Calculator,
  newer Notepad) hand over to another process at once; AURORA says so
  rather than claiming it can close them.
"""

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from aurora.config import settings

logger = logging.getLogger(__name__)

HANDOVER_WAIT_SECONDS = 0.5


class AppError(Exception):
    """Shown to the user as it is."""


@dataclass(frozen=True)
class App:
    name: str
    aliases: tuple[str, ...]
    command: tuple[str, ...]
    confirm: bool = False

    def names(self) -> set[str]:
        return {self.name.lower(), *(alias.lower() for alias in self.aliases)}


def load_apps(config: list[dict] | None = None) -> list[App]:
    apps = []
    for entry in settings.APPS if config is None else config:
        command = entry.get("command")
        if not isinstance(command, (list, tuple)) or not command or not all(isinstance(c, str) and c for c in command):
            raise ValueError(f"APPS entry {entry.get('name')!r} needs a command: a list of strings.")
        apps.append(App(str(entry["name"]), tuple(entry.get("aliases", [])), tuple(command),
                        bool(entry.get("confirm", False))))
    return apps


class Launcher:

    def __init__(self, apps: list[App] | None = None, popen=subprocess.Popen, which=shutil.which,
                 wait: float = HANDOVER_WAIT_SECONDS):
        self._apps = apps
        self.popen = popen
        self.which = which
        self.wait = wait
        self.started: dict[str, list] = {}         # app name -> processes AURORA started

    @property
    def apps(self) -> list[App]:
        if self._apps is None:
            self._apps = load_apps()
        return self._apps

    def find(self, name: str) -> App:
        wanted = (name or "").strip().lower()
        for prefix in ("the ", "my ", "a "):
            wanted = wanted.removeprefix(prefix)
        wanted = wanted.removesuffix(" app").removesuffix(" program").strip()
        for app in self.apps:
            if wanted in app.names():
                return app
        known = ", ".join(app.name for app in self.apps) or "none are set up"
        raise AppError(f"I can only open programs on my list ({known}). You can add others to APPS in settings.")

    def command(self, app: App) -> list[str]:
        """The exact command, checked: the program exists, placeholders filled in."""
        program = app.command[0]
        if Path(program).is_absolute():
            if not Path(program).is_file():
                raise AppError(f"I can't find {app.name} at {program}.")
            resolved = program
        else:
            resolved = self.which(program)
            if not resolved:
                raise AppError(f"I can't find {app.name} ({program}) on this PC.")
        arguments = [part.replace("{documents}", str(settings.DOCUMENTS_DIR)) for part in app.command[1:]]
        return [resolved, *arguments]

    def open(self, app: App) -> bool:
        """Starts the program. Returns True if AURORA can close it later."""
        process = self.popen(self.command(app), shell=False, close_fds=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Opened %s", app.name)
        if self.wait:
            time.sleep(self.wait)
        if process.poll() is not None:          # handed over to another process (or closed at once)
            return False
        self.started.setdefault(app.name, []).append(process)
        return True

    def running(self, app: App) -> list:
        processes = [p for p in self.started.get(app.name, []) if p.poll() is None]
        self.started[app.name] = processes
        return processes

    def close(self, app: App) -> int:
        closed = 0
        for process in self.running(app):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            closed += 1
        self.started[app.name] = []
        logger.info("Closed %s (%d window%s)", app.name, closed, "" if closed == 1 else "s")
        return closed
