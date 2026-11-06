"""
Starts the AURORA Command Centre.

    command_centre.py              open the window
    command_centre.py --minimized  start in the notification area (used by "start with Windows")

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


def run(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
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
