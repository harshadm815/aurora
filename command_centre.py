"""
AURORA Command Centre: the desktop interface.

    .venv\\Scripts\\python.exe command_centre.py

(The terminal interface is still main.py.)
"""

import sys

from aurora.interface.app import run

if __name__ == "__main__":
    sys.exit(run())
