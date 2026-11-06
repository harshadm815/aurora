"""
Vision Centre.

The camera is never started automatically. "Turn camera on" asks you
first, checks the camera permission, and shows a red indicator for as
long as it's on. Leaving this page or closing the window turns it off.
Frames are only kept in memory; nothing is recorded or saved.
"""

import importlib.util
import logging

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy, QTextBrowser

from aurora.config import settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Badge, Panel, StatusDot, StatusRow, label
from aurora.kernel.permissions import Permission

logger = logging.getLogger(__name__)


class CameraThread(QThread):
    """Reads frames until stopped. Emits each frame (BGR array) and a display image."""

    frame_ready = Signal(object, QImage)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False

    def run(self):
        from aurora.vision.camera import Camera, VisionUnavailable

        self._running = True
        try:
            with Camera() as camera:
                for frame in camera.frames(interval=0.06):
                    if not self._running:
                        break
                    height, width = frame.shape[:2]
                    image = QImage(frame.data, width, height, frame.strides[0], QImage.Format_BGR888).copy()
                    self.frame_ready.emit(frame.copy(), image)
        except VisionUnavailable as error:
            self.failed.emit(str(error))
        except Exception as error:
            logger.exception("Camera thread failed")
            self.failed.emit(f"The camera stopped ({type(error).__name__}).")

    def stop(self):
        self._running = False
        self.wait(3000)


class VisionView(BaseView):
    title = "Vision Centre"
    subtitle = "The camera only turns on when you ask. Pictures are analysed on this laptop and never saved."
    nav_icon = "👁"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.thread: CameraThread | None = None
        self.last_frame = None
        self._confirmed = False

        top = QHBoxLayout()

        # Preview
        preview = Panel("Camera")
        indicator = QHBoxLayout()
        self.live_dot = StatusDot("off")
        self.live_badge = Badge("CAMERA OFF", "off")
        indicator.addWidget(self.live_dot)
        indicator.addWidget(self.live_badge)
        indicator.addStretch(1)
        preview.add_layout(indicator)
        self.image = QLabel("The camera is off.")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(480, 360)
        self.image.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image.setStyleSheet("background: #04070d; border-radius: 10px; color: #5b6b85;")
        preview.add(self.image, 1)
        buttons = QHBoxLayout()
        self.toggle = QPushButton("Turn camera on")
        self.toggle.setObjectName("Primary")
        self.toggle.clicked.connect(self._toggle)
        self.analyse = QPushButton("What do you see?")
        self.analyse.clicked.connect(lambda: self._analyse("objects"))
        self.read = QPushButton("Read text")
        self.read.clicked.connect(lambda: self._analyse("text"))
        for button in (self.toggle, self.analyse, self.read):
            buttons.addWidget(button)
        preview.add_layout(buttons)
        top.addWidget(preview, 3)

        # Status
        status = Panel("Vision system")
        self.camera_row = status.add(StatusRow("Camera", "Off", "off"))
        self.detector_row = status.add(StatusRow("Object detection", "", "off"))
        self.ocr_row = status.add(StatusRow("Text reading (OCR)", "", "off"))
        self.permission_row = status.add(StatusRow("Camera permission", "", "off"))
        status.add(label("People are only counted (\"a person on the left\"); nobody is identified. "
                         "There is no face recognition.", "Faint", wrap=True))
        status.layout_.addStretch(1)
        top.addWidget(status, 2)
        self.body.addLayout(top, 1)

        results = Panel("Analysis results")
        self.results = QTextBrowser()
        self.results.setMinimumHeight(120)
        self.results.setPlainText("Turn the camera on, then ask what it sees or read text in front of it.")
        results.add(self.results)
        self.body.addWidget(results)

        self._update_controls()

    # --- State ----------------------------------------------------------------------

    def _permission_ok(self) -> bool:
        return not self.backend.missing_permissions("Camera", (Permission.CAMERA,))

    def on_shown(self):
        detector = settings.VISION_DETECTOR_MODEL.is_file() and importlib.util.find_spec("cv2") is not None
        self.detector_row.set("Ready" if detector else "Model or OpenCV missing", "ok" if detector else "error")
        ocr = importlib.util.find_spec("rapidocr_onnxruntime") is not None
        self.ocr_row.set("Ready" if ocr else "RapidOCR not installed", "ok" if ocr else "error")
        allowed = self._permission_ok()
        self.permission_row.set("Granted" if allowed else "Not granted", "ok" if allowed else "warning")
        self._update_controls()

    def on_hidden(self):
        self.stop_camera()

    def _update_controls(self):
        on = self.thread is not None
        self.toggle.setText("Turn camera off" if on else "Turn camera on")
        self.toggle.setObjectName("Danger" if on else "Primary")
        self.toggle.style().unpolish(self.toggle)
        self.toggle.style().polish(self.toggle)
        self.analyse.setEnabled(on)
        self.read.setEnabled(on)
        self.live_dot.set_state("error" if on else "off", pulse=on)
        self.live_badge.set("● CAMERA ON" if on else "CAMERA OFF", "error" if on else "off")
        self.camera_row.set("On (live preview)" if on else "Off", "error" if on else "off")

    # --- Camera ---------------------------------------------------------------------

    def _toggle(self):
        if self.thread is not None:
            self.stop_camera()
            return
        if not self._permission_ok():
            QMessageBox.warning(self, "Camera", "AURORA isn't allowed to use the camera. Grant 'camera' "
                                "to 'Camera' in PERMISSION_GRANTS to enable it.")
            return
        if not self._confirmed:
            answer = QMessageBox.question(
                self, "Turn the camera on?",
                "AURORA will show a live preview from your webcam until you turn it off or leave this page.\n\n"
                "Nothing is recorded or saved.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            self._confirmed = True

        self.thread = CameraThread(self)
        self.thread.frame_ready.connect(self._show_frame)
        self.thread.failed.connect(self._camera_failed)
        self.thread.finished.connect(self._thread_finished)
        self.thread.start()
        self._update_controls()

    def stop_camera(self):
        if self.thread is not None:
            thread, self.thread = self.thread, None
            thread.stop()
        self.last_frame = None
        self.image.setPixmap(QPixmap())
        self.image.setText("The camera is off.")
        self._update_controls()

    def _thread_finished(self):
        if self.thread is not None and not self.thread.isRunning():
            self.thread = None
            self._update_controls()

    def _show_frame(self, frame, image: QImage):
        if self.thread is None:
            return
        self.last_frame = frame
        self.image.setPixmap(QPixmap.fromImage(image).scaled(
            self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _camera_failed(self, message: str):
        self.results.setPlainText(message)
        self.stop_camera()

    # --- Analysis -------------------------------------------------------------------

    def _analyse(self, what: str):
        frame = self.last_frame
        tool = self.backend.tool("Camera")
        if frame is None or tool is None:
            return
        self.results.setPlainText("Analysing…" + (" (reading text takes a few seconds the first time)"
                                                  if what == "text" else ""))
        analyzer = tool.analyzer
        work = (lambda: analyzer.objects(frame)[0]) if what == "objects" else (lambda: analyzer.text(frame)[0])
        self.backend.run_io(work, self.results.setPlainText,
                            lambda error: self.results.setPlainText(f"Analysis failed: {error}"))
