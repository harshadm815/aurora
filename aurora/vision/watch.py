"""
Watch mode: AURORA keeps an eye on the room and reports motion.

Started only with the "watch" command and stopped with Ctrl+C. The
terminal shows that the camera is on for the whole time. When motion is
seen, AURORA reports the time and which objects are in view (people are
counted, never identified). Nothing is recorded or saved.
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime

from aurora.config import settings
from aurora.events import topics
from aurora.kernel.permissions import Permission
from aurora.vision.camera import Camera, VisionUnavailable

logger = logging.getLogger(__name__)

COMPONENT = "Camera"


class Watcher:

    def __init__(self, engine, analyzer=None, motion=None,
                 camera: Callable[[], Camera] = Camera,
                 output: Callable[[str], None] = print,
                 clock: Callable[[], float] = time.monotonic,
                 frame_interval: float = 0.2):
        self.engine = engine
        self.analyzer = analyzer
        self.motion = motion
        self.camera = camera
        self.output = output
        self.clock = clock
        self.frame_interval = frame_interval

    def run(self, should_stop: Callable[[], bool] = lambda: False, max_frames: int | None = None) -> int:
        """Watches until Ctrl+C (or ``should_stop``). Returns how many alerts were raised."""
        if self.engine.permissions.missing(COMPONENT, (Permission.CAMERA,)):
            raise VisionUnavailable(
                "I'm not allowed to use the camera. Grant 'camera' to 'Camera' "
                "in PERMISSION_GRANTS in aurora/config/settings.py."
            )

        if self.motion is None:
            from aurora.vision.motion import MotionDetector
            self.motion = MotionDetector()

        alerts, last_alert, frames = 0, None, 0
        self.output("📷  Watching for motion. The camera stays on until you press Ctrl+C.")
        try:
            with self.camera() as camera:
                for frame in camera.frames(self.frame_interval):
                    frames += 1
                    if should_stop() or (max_frames is not None and frames > max_frames):
                        break
                    if not self.motion.motion(frame):
                        continue

                    now = self.clock()
                    if last_alert is not None and now - last_alert < settings.VISION_MOTION_COOLDOWN:
                        continue
                    last_alert = now
                    alerts += 1

                    seen = ""
                    if self.analyzer is not None:
                        try:
                            seen = " " + self.analyzer.objects(frame)[0]
                        except VisionUnavailable:
                            seen = ""
                    self.output(f"[{datetime.now():%H:%M:%S}] Motion detected.{seen}")
                    self.engine.events.emit(topics.MOTION_DETECTED, {"alert": alerts}, source="Watcher")
        except KeyboardInterrupt:
            pass
        finally:
            self.output(f"📷  Stopped watching. Camera off. ({alerts} alert{'s' if alerts != 1 else ''})\n")
        return alerts
