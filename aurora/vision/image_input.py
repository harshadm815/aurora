"""
Image input and preprocessing: from a file, bytes or the clipboard to a
picture the vision models can use, checked and normalised.

    picture = load_image("Documents/desk.jpg")      # -> LoadedImage
    picture.image                                     # BGR uint8 array, upright
    quality(picture.image).notes                      # ["The picture is very dark."]

What happens to every image:

- **Checked before decoding.** The format is recognised from the file's
  first bytes (not its name), files over `VISION_MAX_IMAGE_MB` are
  refused, and the size in pixels is read from the header first, so a
  small file that would expand to gigabytes (a "decompression bomb") is
  refused before it uses any memory.
- **Upright.** Phone photos are often stored sideways with an EXIF note
  saying how to turn them; that note is applied.
- **Normalised.** Grey, palette, 16-bit and transparent images become
  ordinary 8-bit colour (transparency on white). Very large pictures are
  scaled down for analysis to `VISION_ANALYSIS_MAX_SIDE`, which keeps
  OCR fast and doesn't change what the detector sees (it works at 416 px).

Images stay in memory; nothing is copied or saved.
"""

import io
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from aurora.config import settings
from aurora.vision.camera import VisionUnavailable

# First bytes of each format AURORA accepts.
_SIGNATURES = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"BM", "BMP"),
)
FORMATS = ("JPEG", "PNG", "BMP", "WEBP")
FILE_FILTER = "Images (*.jpg *.jpeg *.png *.bmp *.webp)"


class ImageError(VisionUnavailable):
    """The image can't be used (not an image, damaged, too large)."""


def image_format(data: bytes) -> str | None:
    """'JPEG', 'PNG', 'BMP' or 'WEBP' from the first bytes, else None."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    for signature, name in _SIGNATURES:
        if data.startswith(signature):
            return name
    return None


@dataclass
class LoadedImage:
    image: object                     # BGR uint8 array (H, W, 3), upright
    name: str
    format: str
    original_size: tuple[int, int]    # (width, height) as stored
    rotated: bool = False             # an EXIF orientation was applied
    scaled: float = 1.0               # factor applied for analysis (<= 1)

    @property
    def size(self) -> tuple[int, int]:
        return self.image.shape[1], self.image.shape[0]


def _limit_mb() -> float:
    return settings.VISION_MAX_IMAGE_MB


def decode_image(data: bytes, name: str = "image") -> LoadedImage:
    """Check, decode, turn upright and normalise an image held in memory."""
    import numpy as np
    from PIL import Image, ImageOps

    if not data:
        raise ImageError(f"{name} is empty.")
    if len(data) > _limit_mb() * 1024 * 1024:
        raise ImageError(f"{name} is larger than {_limit_mb():g} MB.")
    kind = image_format(data)
    if kind is None:
        raise ImageError(f"{name} isn't a JPEG, PNG, BMP or WebP image.")

    try:
        with warnings.catch_warnings():
            # Pillow warns about (and above twice its limit refuses) huge pictures
            # itself; AURORA's own, lower limit is checked just below.
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            opened = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as error:
        raise ImageError(f"{name} claims to be far more than the "
                         f"{settings.VISION_MAX_PIXELS / 1e6:g} million pixels AURORA analyses.") from error
    except Exception as error:
        raise ImageError(f"I can't open {name} as an image ({type(error).__name__}).") from error

    try:
        with opened:
            width, height = opened.size
            if width < 1 or height < 1:
                raise ImageError(f"{name} has no pixels.")
            if width * height > settings.VISION_MAX_PIXELS:
                raise ImageError(
                    f"{name} is {width}×{height} pixels, more than the "
                    f"{settings.VISION_MAX_PIXELS / 1e6:g} million AURORA analyses.")
            orientation = opened.getexif().get(0x0112, 1)      # EXIF "Orientation"
            rotated = orientation not in (None, 1)
            picture = _to_rgb(ImageOps.exif_transpose(opened) if rotated else opened)
    except ImageError:
        raise
    except Exception as error:      # PIL raises many kinds for damaged files
        raise ImageError(f"I can't open {name} as an image ({type(error).__name__}).") from error

    rgb = np.asarray(picture, dtype=np.uint8)
    loaded = LoadedImage(np.ascontiguousarray(rgb[:, :, ::-1]), name, kind, (width, height), rotated)
    return _fit(loaded)


def load_image(path: str | Path) -> LoadedImage:
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ImageError(f"I can't find {path.name}.") from error
    if size > _limit_mb() * 1024 * 1024:            # refuse before reading it
        raise ImageError(f"{path.name} is larger than {_limit_mb():g} MB.")
    return decode_image(path.read_bytes(), path.name)


def from_array(image, name: str = "camera") -> LoadedImage:
    """A frame already in memory (camera, clipboard): only normalised."""
    import numpy as np

    if image is None or getattr(image, "ndim", 0) not in (2, 3) or image.size == 0:
        raise ImageError(f"The {name} picture is empty.")
    if image.ndim == 2:
        image = np.repeat(image[:, :, None], 3, axis=2)
    elif image.shape[2] == 4:
        image = _composite_on_white(image)
    elif image.shape[2] != 3:
        raise ImageError(f"The {name} picture has an unusual format.")
    if image.dtype != np.uint8:
        image = (image / 257).astype(np.uint8) if image.dtype == np.uint16 else image.astype(np.uint8)
    height, width = image.shape[:2]
    return _fit(LoadedImage(np.ascontiguousarray(image), name, "frame", (width, height)))


def _to_rgb(picture):
    from PIL import Image

    if picture.mode in ("I;16", "I;16B", "I;16L", "I"):
        picture = picture.point(lambda value: value / 256).convert("L")
    if picture.mode == "P":
        picture = picture.convert("RGBA")
    if picture.mode in ("RGBA", "LA"):
        background = Image.new("RGB", picture.size, (255, 255, 255))
        background.paste(picture, mask=picture.getchannel("A"))
        return background
    return picture.convert("RGB")


def _composite_on_white(bgra):
    import numpy as np

    alpha = bgra[:, :, 3:4].astype(np.float32) / 255
    return (bgra[:, :, :3] * alpha + 255 * (1 - alpha)).astype(np.uint8)


def _fit(loaded: LoadedImage) -> LoadedImage:
    """Scale very large pictures down for analysis."""
    import cv2

    width, height = loaded.size
    longest = max(width, height)
    limit = settings.VISION_ANALYSIS_MAX_SIDE
    if longest > limit:
        factor = limit / longest
        loaded.image = cv2.resize(loaded.image, (max(1, round(width * factor)), max(1, round(height * factor))),
                                  interpolation=cv2.INTER_AREA)
        loaded.scaled = factor
    return loaded


# --- What the picture itself says (measured, not guessed) -------------------------

@dataclass
class ImageQuality:
    brightness: float      # 0 (black) .. 1 (white), mean of the grey picture
    contrast: float        # standard deviation of grey, 0 .. ~0.5
    sharpness: float       # variance of the Laplacian (higher = sharper)
    notes: list[str] = field(default_factory=list)

    @property
    def poor(self) -> bool:
        return bool(self.notes)


def quality(image) -> ImageQuality:
    """Brightness, contrast and sharpness, with plain-language warnings."""
    import cv2

    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = grey if max(grey.shape) <= 800 else cv2.resize(
        grey, None, fx=800 / max(grey.shape), fy=800 / max(grey.shape), interpolation=cv2.INTER_AREA)
    brightness = float(small.mean()) / 255
    contrast = float(small.std()) / 255
    sharpness = float(cv2.Laplacian(small, cv2.CV_64F).var())

    notes = []
    if min(image.shape[:2]) < 120:
        notes.append("The picture is very small.")
    if brightness < 0.12:
        notes.append("The picture is very dark.")
    elif brightness > 0.92 and contrast < 0.1:    # a white page with text is fine
        notes.append("The picture is very bright (overexposed).")
    if contrast < 0.04:
        notes.append("The picture has almost no contrast (nearly one flat colour).")
    elif sharpness < 15:
        notes.append("The picture looks blurry.")
    return ImageQuality(brightness, contrast, sharpness, notes)


# Hue ranges (OpenCV hue is 0..179) and their names.
_HUES = ((0, 8, "red"), (8, 20, "orange"), (20, 33, "yellow"), (33, 78, "green"),
         (78, 100, "cyan"), (100, 130, "blue"), (130, 150, "purple"), (150, 170, "pink"), (170, 180, "red"))


def colours(image, limit: int = 2, minimum_share: float = 0.2) -> list[tuple[str, float]]:
    """The main colours by share of the picture: [('blue', 0.61), ('black', 0.22)]."""
    import cv2
    import numpy as np

    small = cv2.resize(image, (96, 96), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.int32)
    hue, saturation, value = hsv[:, 0], hsv[:, 1], hsv[:, 2]

    names = np.empty(len(hsv), dtype=object)
    dark = value < 50
    grey = (saturation < 40) & ~dark
    names[dark] = "black"
    names[grey & (value >= 200)] = "white"
    names[grey & (value < 200)] = "grey"
    coloured = ~dark & ~grey
    for low, high, name in _HUES:
        names[coloured & (hue >= low) & (hue < high)] = name
    # Dark, unsaturated oranges and reds read as brown.
    names[coloured & (hue < 20) & (value < 150) & (saturation >= 40)] = "brown"

    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    ranked = sorted(((count / len(names), name) for name, count in counts.items()), reverse=True)
    return [(name, round(share, 2)) for share, name in ranked[:limit] if share >= minimum_share]
