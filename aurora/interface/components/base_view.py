from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from aurora.interface.components.widgets import page_header


class BaseView(QWidget):
    """
    A page in the Command Centre. Subclasses fill ``self.body`` and may
    override ``on_shown`` (called each time the page is opened) and
    ``on_engine_ready``.
    """

    title = "Page"
    subtitle = ""
    nav_icon = "•"
    scrollable = True

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        content.setObjectName("Root")
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(28, 24, 28, 24)
        self.body.setSpacing(16)
        self.body.addWidget(page_header(self.title, self.subtitle))

        if self.scrollable:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(content)
            outer.addWidget(scroll)
        else:
            outer.addWidget(content)

        backend.engine_ready.connect(self.on_engine_ready)

    def on_shown(self) -> None:
        pass

    def on_hidden(self) -> None:
        """Called when the user leaves the page (e.g. to turn the camera off)."""

    def on_engine_ready(self) -> None:
        pass
