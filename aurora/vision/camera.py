"""
Webcam access. The camera is opened only for an explicit request
("look", "read this", "watch") and released straight afterwards.
Frames stay in memory and are never saved.
"""

import logging
import time
from collections.abc import Iterator

from aurora.config import settings

logger = logging.getLogger(__name__)


class VisionUnavailable(RuntimeError):
    """Vision can't be used (package or model missing, no camera)."""


class Camera:

    def __init__(self, index: int | None = None):
        try:
            import cv2
        except ImportError as error:
            raise VisionUnavailable("The 'opencv-python' package isn't installed.") from error

        self._cv2 = cv2
        index = settings.VISION_CAMERA_INDEX if index is None else index
        # DirectShow opens Windows webcams much faster than the default backend.
        backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
        self._capture = cv2.VideoCapture(index, backend)
        if not self._capture.isOpened():
            self._capture.release()
            raise VisionUnavailable("I can't open the camera. Is it connected, and not in use by another app?")

    def frame(self, warmup: int = 5):
        """One frame. The first few are skipped so exposure can settle."""
        frame = None
        for _ in range(max(1, warmup)):
            ok, frame = self._capture.read()
            if not ok:
                frame = None
        if frame is None:
            raise VisionUnavailable("The camera didn't return a picture.")
        return frame

    def frames(self, interval: float = 0.0) -> Iterator:
        while True:
            ok, frame = self._capture.read()
            if not ok:
                raise VisionUnavailable("The camera stopped returning pictures.")
            yield frame
            if interval:
                time.sleep(interval)

    def close(self) -> None:
        self._capture.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
