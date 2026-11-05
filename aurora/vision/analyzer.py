"""
Turns a picture into words: which objects are visible, and any text.

The detector and OCR models are loaded on first use (OCR takes a few
seconds), then kept for later requests.
"""

import logging
from collections import Counter

from aurora.config import settings
from aurora.tools.untrusted import one_line
from aurora.vision.camera import VisionUnavailable
from aurora.vision.detector import Detection, ObjectDetector

logger = logging.getLogger(__name__)

_PLURALS = {
    "person": "people", "mouse": "mice", "knife": "knives", "sheep": "sheep",
    "skis": "pairs of skis", "scissors": "pairs of scissors", "couch": "couches",
    "bus": "buses", "wine glass": "wine glasses", "sandwich": "sandwiches",
    "hot dog": "hot dogs", "toothbrush": "toothbrushes", "bench": "benches",
    "cell phone": "phones", "tv": "TVs",
}
_NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _phrase(label: str, count: int) -> str:
    name = "phone" if label == "cell phone" else ("TV" if label == "tv" else label)
    if count == 1:
        article = "an" if name[0].lower() in "aeiou" else "a"
        return f"{article} {name}"
    number = _NUMBERS[count] if count < len(_NUMBERS) else str(count)
    return f"{number} {_PLURALS.get(label, label + 's')}"


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _position(detection: Detection, width: int) -> str:
    third = width / 3
    if detection.centre_x < third:
        return "on the left"
    if detection.centre_x > 2 * third:
        return "on the right"
    return "in the middle"


def describe(detections: list[Detection], width: int) -> str:
    """'I can see a person in the middle, a laptop and two cups.'"""
    if not detections:
        return "I don't recognise any objects in view."

    counts = Counter(d.label for d in detections)
    # Most common / most confident first, people first of all.
    order = sorted(counts, key=lambda label: (label != "person", -counts[label]))

    parts = []
    for label in order:
        phrase = _phrase(label, counts[label])
        if label == "person" and counts[label] == 1:
            person = next(d for d in detections if d.label == "person")
            phrase += f" {_position(person, width)}"
        parts.append(phrase)

    return f"I can see {_join(parts)}."


class TextReader:
    """Reads printed text in a picture with RapidOCR (offline)."""

    def __init__(self, min_confidence: float | None = None):
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as error:
            raise VisionUnavailable("The 'rapidocr-onnxruntime' package isn't installed.") from error

        self.engine = RapidOCR()
        self.min_confidence = settings.VISION_OCR_MIN_CONFIDENCE if min_confidence is None else min_confidence

    def read(self, image) -> list[str]:
        """Lines of text, top to bottom."""
        result, _ = self.engine(image)
        lines = []
        for box, text, confidence in result or []:
            if float(confidence) >= self.min_confidence and text.strip():
                top = min(point[1] for point in box)
                left = min(point[0] for point in box)
                lines.append((round(top / 20), left, text.strip()))
        return [text for _, _, text in sorted(lines)]


class VisionAnalyzer:

    def __init__(self, detector: ObjectDetector | None = None, reader: TextReader | None = None):
        self._detector = detector
        self._reader = reader

    @property
    def detector(self) -> ObjectDetector:
        if self._detector is None:
            self._detector = ObjectDetector()
        return self._detector

    @property
    def reader(self) -> TextReader:
        if self._reader is None:
            self._reader = TextReader()
        return self._reader

    def objects(self, image) -> tuple[str, list[Detection]]:
        detections = self.detector.detect(image)
        logger.info("Detected %d objects", len(detections))   # Counts only, never what.
        return describe(detections, image.shape[1]), detections

    def text(self, image) -> tuple[str, list[str]]:
        # Text in a picture is untrusted: clean each line (control characters etc.).
        lines = [line for line in (one_line(text) for text in self.reader.read(image)) if line]
        if not lines:
            return "I can't find any readable text.", []
        return "The text says:\n" + "\n".join(f"  {line}" for line in lines), lines
