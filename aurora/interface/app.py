"""
Starts the AURORA Command Centre.

    command_centre.py              open the window
    command_centre.py --minimized  start in the notification area (used by "start with Windows")
    command_centre.py --train …    run a training job (used by the Training monitor; no window)
    command_centre.py --voice-check / --vision-check   self-tests (no window)

Only one Command Centre runs per Windows user: launching it again just
brings the running one to the front.
"""

import argparse
import logging
import sys

from aurora.utils.log import setup_logging

logger = logging.getLogger("aurora.interface")


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="AURORA Command Centre", add_help=False)
    parser.add_argument("--minimized", action="store_true")
    known, _ = parser.parse_known_args(argv[1:])
    return known


def train(arguments: list[str]) -> int:
    """`--train …`: run AURORA's trainer instead of opening the window.

    The Training monitor starts runs this way as a separate process, so the
    packaged .exe (which has no Python of its own to call) can train too.
    The arguments are those of `python -m aurora_core.training`."""
    import os

    setup_logging()
    if sys.stdout is None:                     # the windowed .exe has no console
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    from aurora_core.training.__main__ import main

    logger.info("Training run started from the Command Centre")
    return main(arguments)


def voice_check(arguments: list[str]) -> int:
    """`--voice-check [--out results.json]`: the voice loop-back test (speech
    synthesis -> file -> recognition), without a window, microphone or sound.
    Lets the packaged .exe prove its voice parts work."""
    import os

    setup_logging()
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    from aurora.voice.selftest import main

    return main(arguments)


def vision_check(arguments: list[str]) -> int:
    """`--vision-check [--labels labels.json] [--out results.json]`: the vision
    self-check (pictures made in memory; no camera, no window), or a benchmark
    on labelled photos. Lets the packaged .exe prove its vision parts work."""
    import os

    setup_logging()
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    from aurora.vision.evaluate import main

    return main(arguments)


def run(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    if argv[1:2] == ["--train"]:
        return train(argv[2:])
    if argv[1:2] == ["--voice-check"]:
        return voice_check(argv[2:])
    if argv[1:2] == ["--vision-check"]:
        return vision_check(argv[2:])
    options = parse(argv)
    setup_logging()

    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("The Command Centre needs PySide6. Install it with:\n"
              "  .venv\\Scripts\\python.exe -m pip install PySide6-Essentials")
        return 1

    from aurora.interface.windows import integration
    from aurora.interface.windows.single_instance import SingleInstance

    # Before any window exists: AURORA's own taskbar identity (not Python's).
    integration.set_app_id()

    app = QApplication(argv)
    app.setApplicationName("AURORA Command Centre")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(integration.icon_path())))

    guard = SingleInstance()
    if not guard.acquire():
        return 0   # Already running: it has been asked to show its window.

    from aurora.interface.backend import Backend
    from aurora.interface.main_window import MainWindow

    backend = Backend()
    window = MainWindow(backend)
    guard.activated.connect(window.show_from_tray)

    # With a tray icon, closing or hiding the window must not end the program;
    # only Exit (or closing with "close to tray" off) does.
    app.setQuitOnLastWindowClosed(window.tray is None)

    def finish():
        backend.shutdown()     # Safe if the window already did it.
        guard.release()
        logger.info("Command Centre stopped")

    app.aboutToQuit.connect(finish)

    if options.minimized and window.tray is not None:
        window.tray.tell_once()
    elif backend.preferences.get("start_maximized"):
        window.showMaximized()
    else:
        window.show()

    backend.start()   # The engine starts in the background; the window appears at once.
    logger.info("Command Centre started")
    return app.exec()
