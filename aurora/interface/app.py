"""Starts the AURORA Command Centre."""

import logging
import sys

from aurora.utils.log import setup_logging

logger = logging.getLogger("aurora.interface")


def run(argv: list[str] | None = None) -> int:
    setup_logging()

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("The Command Centre needs PySide6. Install it with:\n"
              "  .venv\\Scripts\\python.exe -m pip install PySide6-Essentials")
        return 1

    from aurora.interface.backend import Backend
    from aurora.interface.main_window import MainWindow

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("AURORA Command Centre")
    app.setStyle("Fusion")

    backend = Backend()
    window = MainWindow(backend)
    if backend.preferences.get("start_maximized"):
        window.showMaximized()
    else:
        window.show()

    backend.start()   # The engine starts in the background; the window appears at once.
    logger.info("Command Centre started")
    return app.exec()
