"""
Vision Centre.

Two ways to give AURORA a picture:

- **An image you choose**: Open image…, Paste (from the clipboard) or
  drag a file onto the page. It is checked, turned upright and analysed
  in memory; it isn't copied or saved anywhere.
- **The camera**: never started automatically. "Turn camera on" asks
  you first, checks the camera permission, and shows a red indicator for
  as long as it's on. Leaving this page or closing the window turns it
  off. Frames are only kept in memory; nothing is recorded or saved.

Describe gives a grounded description: every object it names has a
number, and the same number is drawn on the picture next to it, with how
sure AURORA is.
"""

import importlib.util
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QSizePolicy, QTextBrowser)

from aurora.config import settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Badge, Panel, StatusDot, StatusRow, label
from aurora.kernel.permissions import Permission

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def to_qimage(image) -> QImage:
    """A BGR uint8 array as a QImage (copied, so the array can be freed)."""
    height, width = image.shape[:2]
    return QImage(image.data, width, height, image.strides[0], QImage.Format_BGR888).copy()


def from_qimage(image: QImage):
    """A QImage (e.g. from the clipboard) as a BGR uint8 array."""
    import numpy as np

    converted = image.convertToFormat(QImage.Format_BGR888)
    width, height = converted.width(), converted.height()
    row = converted.bytesPerLine()
    buffer = np.frombuffer(converted.constBits(), dtype=np.uint8, count=row * height).reshape(height, row)
    return buffer[:, : width * 3].reshape(height, width, 3).copy()


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
                    self.frame_ready.emit(frame.copy(), to_qimage(frame))
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
    subtitle = "Open a picture or, if you ask, use the camera. Pictures are analysed on this laptop and never saved."
    nav_icon = "👁"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.thread: CameraThread | None = None
        self.last_frame = None
        self.picture = None          # LoadedImage chosen by the user (file or clipboard)
        self.report = None           # the last SceneReport
        self._confirmed = False
        self.setAcceptDrops(True)

        top = QHBoxLayout()

        # Preview
        preview = Panel("Picture")
        indicator = QHBoxLayout()
        self.live_dot = StatusDot("off")
        self.live_badge = Badge("CAMERA OFF", "off")
        self.source_badge = Badge("NO PICTURE", "off")
        indicator.addWidget(self.live_dot)
        indicator.addWidget(self.live_badge)
        indicator.addWidget(self.source_badge)
        indicator.addStretch(1)
        preview.add_layout(indicator)
        self.image = QLabel("Open or paste a picture, drag one here, or turn the camera on.")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setWordWrap(True)
        self.image.setMinimumSize(480, 340)
        self.image.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image.setStyleSheet("background: #04070d; border-radius: 10px; color: #5b6b85;")
        preview.add(self.image, 1)

        sources = QHBoxLayout()
        self.open_button = QPushButton("Open image…")
        self.open_button.clicked.connect(self.choose_file)
        self.paste_button = QPushButton("Paste")
        self.paste_button.setToolTip("Use a picture copied to the clipboard")
        self.paste_button.clicked.connect(self.paste)
        self.toggle = QPushButton("Turn camera on")
        self.toggle.setObjectName("Primary")
        self.toggle.clicked.connect(self._toggle)
        for button in (self.open_button, self.paste_button, self.toggle):
            sources.addWidget(button)
        preview.add_layout(sources)

        actions = QHBoxLayout()
        self.analyse = QPushButton("Describe")
        self.analyse.setToolTip("What's in the picture, with evidence for each thing named")
        self.analyse.clicked.connect(lambda: self._analyse(read_text=None))
        self.read = QPushButton("Read text")
        self.read.clicked.connect(lambda: self._analyse(read_text=True))
        for button in (self.analyse, self.read):
            actions.addWidget(button)
        preview.add_layout(actions)
        top.addWidget(preview, 3)

        # Status
        status = Panel("Vision system")
        self.camera_row = status.add(StatusRow("Camera", "Off", "off"))
        self.detector_row = status.add(StatusRow("Object detection", "", "off"))
        self.ocr_row = status.add(StatusRow("Text reading (OCR)", "", "off"))
        self.permission_row = status.add(StatusRow("Camera permission", "", "off"))
        status.add(label("Objects are named as certain only when AURORA finds them again in the mirrored "
                         "picture; otherwise it says it \"might\" see them. Each has a numbered box on the "
                         "picture.", "Faint", wrap=True))
        status.add(label("People are only counted (\"a person on the left\"); nobody is identified. "
                         "There is no face recognition.", "Faint", wrap=True))
        status.layout_.addStretch(1)
        top.addWidget(status, 2)
        self.body.addLayout(top, 1)

        results = Panel("Analysis results")
        self.results = QTextBrowser()
        self.results.setMinimumHeight(140)
        self.results.setPlainText("Choose a picture (or turn the camera on), then click Describe or Read text.")
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
        has_picture = on or self.picture is not None
        self.analyse.setEnabled(has_picture)
        self.read.setEnabled(has_picture)
        self.live_dot.set_state("error" if on else "off", pulse=on)
        self.live_badge.set("● CAMERA ON" if on else "CAMERA OFF", "error" if on else "off")
        self.camera_row.set("On (live preview)" if on else "Off", "error" if on else "off")
        if on:
            self.source_badge.set("LIVE CAMERA", "error")
        elif self.picture is not None:
            self.source_badge.set(f"IMAGE: {self.picture.name}"[:48], "info")
        else:
            self.source_badge.set("NO PICTURE", "off")

    def _show(self, image):
        self.image.setPixmap(QPixmap.fromImage(to_qimage(image)).scaled(
            self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # --- Pictures chosen by the user -----------------------------------------------------

    def choose_file(self):
        start = str(settings.DOCUMENTS_DIR) if settings.DOCUMENTS_DIR.is_dir() else ""
        from aurora.vision.image_input import FILE_FILTER

        path, _ = QFileDialog.getOpenFileName(self, "Open an image", start, FILE_FILTER)
        if path:
            self.open_file(Path(path))

    def open_file(self, path: Path):
        """Loads (checks, turns upright, normalises) an image off the GUI thread."""
        from aurora.vision.image_input import load_image

        self.results.setPlainText(f"Opening {path.name}…")
        self.backend.run_io(lambda: load_image(path), self.use_picture,
                            lambda error: self.results.setPlainText(str(error)))

    def paste(self):
        from aurora.vision.image_input import ImageError, from_array

        image = QApplication.clipboard().image()
        if image.isNull():
            self.results.setPlainText("There's no picture on the clipboard. Copy one first (for example "
                                      "with Win+Shift+S or Copy image in a browser).")
            return
        try:
            self.use_picture(from_array(from_qimage(image), "pasted picture"))
        except ImageError as error:
            self.results.setPlainText(str(error))

    def use_picture(self, picture):
        """Shows a chosen picture. The camera is turned off: one picture at a time."""
        self.stop_camera()
        self.picture = picture
        self.report = None
        self._show(picture.image)
        width, height = picture.original_size
        notes = [f"{picture.name}: {width}×{height} {picture.format}."]
        if picture.rotated:
            notes.append("Turned upright using the photo's orientation note.")
        if picture.scaled < 1:
            notes.append(f"Scaled to {picture.size[0]}×{picture.size[1]} for analysis.")
        self.results.setPlainText(" ".join(notes) + " Click Describe or Read text.")
        self._update_controls()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(url.toLocalFile().lower().endswith(IMAGE_SUFFIXES) for url in urls):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(IMAGE_SUFFIXES):
                self.open_file(Path(path))
                event.acceptProposedAction()
                return

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

        self.picture = None
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
        self.last_frame = None
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

    def _analyse(self, read_text: bool | None):
        """Describe (read_text=None: text is read for chosen pictures, not for the
        live camera, to keep it quick) or Read text (read_text=True)."""
        tool = self.backend.tool("Camera")
        if tool is None:
            return
        if self.thread is not None:
            frame, name = self.last_frame, "camera"
            if frame is None:
                return
            picture = frame.copy()
            read = bool(read_text)
        elif self.picture is not None:
            picture, name, read = self.picture, self.picture.name, True
        else:
            return

        self.results.setPlainText("Analysing…" + (" (reading text takes a few seconds the first time)"
                                                  if read else ""))
        analyzer = tool.analyzer
        only_text = read_text is True

        def work():
            report = analyzer.analyse(picture, read_text=read, name=name)
            image = picture.image if hasattr(picture, "image") else picture
            return report, report.annotate(image)

        self.backend.run_io(work, lambda result: self._show_report(*result, only_text=only_text),
                            lambda error: self.results.setPlainText(f"Analysis failed: {error}"))

    def _show_report(self, report, annotated, only_text: bool = False):
        self.report = report
        if self.thread is None:              # a chosen picture: show the evidence on it
            self._show(annotated)
        if only_text:
            lines = report.text or []
            body = ("The text says:\n" + "\n".join(f"  {line.text}  ({line.confidence:.0%} sure)" for line in lines)
                    if lines else "I can't find any readable text.")
            self.results.setPlainText(body)
        else:
            self.results.setPlainText(report.message())
