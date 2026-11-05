"""
Motion detection by comparing each frame with a slowly updated
background. Cheap enough to run continuously on a laptop CPU, and it
needs no model.
"""

from aurora.config import settings


class MotionDetector:

    def __init__(self, threshold: float | None = None, sensitivity: int = 25):
        import cv2  # Imported lazily: only needed for vision.

        self._cv2 = cv2
        # Fraction of the picture that must change to count as motion.
        self.threshold = settings.VISION_MOTION_THRESHOLD if threshold is None else threshold
        self.sensitivity = sensitivity
        self._background = None

    def _prepare(self, frame):
        cv2 = self._cv2
        small = cv2.resize(frame, (320, 240))
        grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(grey, (21, 21), 0).astype("float32")

    def changed_fraction(self, frame) -> float:
        """How much of the picture changed since the background (0.0–1.0)."""
        cv2 = self._cv2
        current = self._prepare(frame)
        if self._background is None:
            self._background = current
            return 0.0

        difference = cv2.absdiff(current, self._background)
        cv2.accumulateWeighted(current, self._background, 0.1)
        _, mask = cv2.threshold(difference, self.sensitivity, 255, cv2.THRESH_BINARY)
        return float((mask > 0).mean())

    def motion(self, frame) -> bool:
        return self.changed_fraction(frame) >= self.threshold
