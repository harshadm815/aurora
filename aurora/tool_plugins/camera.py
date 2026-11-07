"""
Camera tool: "what do you see?" and "read this".

The camera is opened for one picture and released straight away. The
picture is analysed in memory and never saved. People are only counted
("a person on the left"); nobody is identified.
"""

import logging
from collections.abc import Callable

from aurora.kernel.permissions import Permission
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool
from aurora.vision.analyzer import VisionAnalyzer
from aurora.vision.camera import Camera, VisionUnavailable

logger = logging.getLogger(__name__)


class CameraTool(BaseTool):

    required_permissions = (Permission.CAMERA,)

    def __init__(self, analyzer: VisionAnalyzer | None = None,
                 camera: Callable[[], Camera] = Camera,
                 notify: Callable[[str], None] | None = None):
        super().__init__("Camera")
        self.analyzer = analyzer or VisionAnalyzer()
        self.camera = camera
        self.notify = notify   # Shows "camera on / off" in the terminal.

    def _say(self, text: str) -> None:
        if self.notify:
            self.notify(text)

    def _picture(self):
        self._say("📷  Camera on…")
        try:
            with self.camera() as camera:
                return camera.frame()
        finally:
            self._say("📷  Camera off.")

    def execute(self, action: str = "look", **_) -> ToolResult:
        try:
            image = self._picture()
            if action == "read":
                message, lines = self.analyzer.text(image)
                return ToolResult(bool(lines), message, lines)

            report = self.analyzer.analyse(image, name="camera")
            return ToolResult(True, report.message(), report)
        except VisionUnavailable as error:
            return ToolResult(False, str(error))
