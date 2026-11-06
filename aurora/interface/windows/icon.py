"""
AURORA's application icon.

The emblem is drawn with QPainter at each standard Windows icon size and
written as a multi-size .ico (PNG-compressed entries, supported since
Windows Vista), so it stays sharp in the taskbar, title bar, tray,
Alt+Tab and desktop shortcuts.

    python -m aurora.interface.windows.icon      # regenerates assets/aurora.ico
"""

import struct
from pathlib import Path

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ICON_PATH = Path(__file__).resolve().parents[1] / "assets" / "aurora.ico"
BACKGROUND = "#070b14"
ACCENT = "#22d3ee"


def render_png(size: int, accent: str = ACCENT) -> bytes:
    """The emblem on a rounded dark tile, as PNG bytes."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    from aurora.interface.components.widgets import draw_emblem

    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(BACKGROUND))
    radius = size * 0.22
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # Small sizes need a bigger, simpler emblem to stay readable.
    inset = size * (0.06 if size <= 24 else 0.12)
    painter.translate(inset, inset)
    draw_emblem(painter, int(size - 2 * inset), QColor(accent))
    painter.end()

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def build_ico(images: dict[int, bytes]) -> bytes:
    """An .ico file from {size: png bytes}."""
    sizes = sorted(images)
    header = struct.pack("<HHH", 0, 1, len(sizes))            # reserved, type 1 = icon, count
    directory = b""
    offset = 6 + 16 * len(sizes)
    payload = b""
    for size in sizes:
        png = images[size]
        side = 0 if size >= 256 else size                     # 0 means 256 in the directory.
        directory += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        payload += png
        offset += len(png)
    return header + directory + payload


def write_icon(path: Path = ICON_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_ico({size: render_png(size) for size in SIZES}))
    return path


if __name__ == "__main__":
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication([])
    print(f"Wrote {write_icon()}")
