"""
Turns a picture into words: which objects are visible, and any text.

The detector and OCR models are loaded on first use (OCR takes a few
seconds), then kept for later requests.
"""

import logging
import threading
from collections import Counter

from aurora.config import settings
from aurora.tools.untrusted import one_line
from aurora.vision.camera import VisionUnavailable
from aurora.vision.detector import Detection, ObjectDetector
from aurora.vision.image_input import LoadedImage, colours, from_array, quality
from aurora.vision.scene import ObjectEvidence, SceneReport, TextEvidence

logger = logging.getLogger(__name__)

MIRROR_OVERLAP = 0.5     # how much the two views' boxes must overlap (intersection over union)


def overlap(a, b) -> float:
    """Intersection over union of two (x1, y1, x2, y2) boxes."""
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = width * height
    area = lambda box: max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0

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

    def read_detailed(self, image) -> list[TextEvidence]:
        """Lines of text in reading order, each with its confidence and box."""
        result, _ = self.engine(image)
        lines = []
        for box, text, confidence in result or []:
            if float(confidence) >= self.min_confidence and text.strip():
                xs = [float(point[0]) for point in box]
                ys = [float(point[1]) for point in box]
                lines.append((round(min(ys) / 20), min(xs),
                              TextEvidence(text.strip(), float(confidence), (min(xs), min(ys), max(xs), max(ys)))))
        return [evidence for _, _, evidence in sorted(lines, key=lambda line: line[:2])]

    def read(self, image) -> list[str]:
        """Lines of text, top to bottom."""
        return [line.text for line in self.read_detailed(image)]


class VisionAnalyzer:

    def __init__(self, detector: ObjectDetector | None = None, reader: TextReader | None = None):
        self._detector = detector
        self._reader = reader
        # The models aren't safe to run from two threads at once (e.g. Chat and
        # the Vision Centre analysing at the same moment); take turns.
        self._lock = threading.RLock()

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

    # --- Grounded analysis -----------------------------------------------------------

    def grounded_objects(self, image) -> list[ObjectEvidence]:
        """Objects confirmed by looking twice: at the picture and its mirror image.

        Detections of textures and shapes that only resemble an object tend
        to change when the picture is mirrored; real objects don't. An
        object found in only one view is dropped; one found in both is
        certain if both views are at least VISION_CERTAIN_CONFIDENCE sure."""
        import cv2

        found = self.detector.detect(image)
        if not found:
            return []
        width = image.shape[1]
        mirrored = [Detection(d.label, d.confidence, (width - d.box[2], d.box[1], width - d.box[0], d.box[3]))
                    for d in self.detector.detect(cv2.flip(image, 1))]

        evidence = []
        for detection in found:
            matches = [m.confidence for m in mirrored
                       if m.label == detection.label and overlap(detection.box, m.box) >= MIRROR_OVERLAP]
            if not matches:
                continue
            confidence = min(detection.confidence, max(matches))
            evidence.append(ObjectEvidence(detection.label, confidence, detection.box,
                                           certain=confidence >= settings.VISION_CERTAIN_CONFIDENCE))
        return evidence

    def analyse(self, picture, read_text: bool = False, name: str = "camera") -> SceneReport:
        """A grounded report on a picture (a LoadedImage or a BGR array)."""
        loaded = picture if isinstance(picture, LoadedImage) else from_array(picture, name)
        image = loaded.image
        with self._lock:
            objects = self.grounded_objects(image)
            text = None
            if read_text:
                # Text in a picture is untrusted: clean each line (control characters etc.).
                text = [TextEvidence(cleaned, line.confidence, line.box)
                        for line in self.reader.read_detailed(image) if (cleaned := one_line(line.text))]
        logger.info("Analysed a picture: %d objects, %s text lines",   # counts only, never what
                    len(objects), "no" if text is None else len(text))
        return SceneReport(loaded.name, image.shape[1], image.shape[0], objects, quality(image),
                           colours(image), text, loaded.rotated, loaded.scaled)

    def objects(self, image) -> tuple[str, list[ObjectEvidence]]:
        report = self.analyse(image)
        return report.description(), report.objects

    def text(self, image) -> tuple[str, list[str]]:
        # Text in a picture is untrusted: clean each line (control characters etc.).
        with self._lock:
            read = self.reader.read(image)
        lines = [line for line in (one_line(text) for text in read) if line]
        if not lines:
            return "I can't find any readable text.", []
        return "The text says:\n" + "\n".join(f"  {line}" for line in lines), lines
