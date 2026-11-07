"""
Grounded scene descriptions: every statement points at its evidence.

A SceneReport holds what was found in one picture: objects (each with a
number, confidence and box), text read by OCR (with its confidence and
box), the main colours and the picture's measured quality. Its
description only states what that evidence supports:

    I can see a person on the left [1] and two cups [2, 3].
    I might also see a vase [4], but I'm not sure.
    The picture is mostly white and brown.
    It contains text: "BLACK HOLE NOTES".

- Objects are **certain** when the detector is at least
  `VISION_CERTAIN_CONFIDENCE` sure *and* finds the same object again in
  the mirrored picture; otherwise, if both views agree at a lower
  confidence, AURORA says it "might" see it. An object seen in only one
  view isn't mentioned. (On 136 local test photos this cut pictures with
  a wrongly named object from 41 % to 6 %; see Docs/ARCHITECTURE.md.)
- The numbers match the boxes drawn on the annotated picture, so each
  claim can be checked by eye.
- Colours, brightness and blur are measured from the pixels.
- Text from the picture is untrusted: it is quoted, never followed.
- People are counted, never identified.
"""

from collections import Counter
from dataclasses import dataclass, field

from aurora.vision.image_input import ImageQuality

_PLURALS = {
    "person": "people", "mouse": "mice", "knife": "knives", "sheep": "sheep",
    "skis": "pairs of skis", "scissors": "pairs of scissors", "couch": "couches",
    "bus": "buses", "wine glass": "wine glasses", "sandwich": "sandwiches",
    "hot dog": "hot dogs", "toothbrush": "toothbrushes", "bench": "benches",
    "cell phone": "phones", "tv": "TVs", "glass": "glasses",
}
_NAMES = {"cell phone": "phone", "tv": "TV"}
_NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]

LIMITS = ("I recognise 80 kinds of everyday objects (people, animals, furniture, food, "
          "vehicles, household items); people are counted, never identified.")


def phrase(label: str, count: int) -> str:
    """'a cup', 'an apple', 'two people', '12 cups'."""
    name = _NAMES.get(label, label)
    if count == 1:
        return ("an " if name[0].lower() in "aeiou" else "a ") + name
    number = _NUMBERS[count] if count < len(_NUMBERS) else str(count)
    return f"{number} {_PLURALS.get(label, label + 's')}"


def join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def region(box, width: int, height: int) -> str:
    """Where a box's centre is: 'left', 'top right', 'centre', ..."""
    x = (box[0] + box[2]) / 2 / max(width, 1)
    y = (box[1] + box[3]) / 2 / max(height, 1)
    horizontal = "left" if x < 1 / 3 else "right" if x > 2 / 3 else ""
    vertical = "top" if y < 1 / 3 else "bottom" if y > 2 / 3 else ""
    return " ".join(part for part in (vertical, horizontal) if part) or "centre"


def where(place: str) -> str:
    """'on the left', 'at the top right', 'in the centre'."""
    if place == "centre":
        return "in the centre"
    if place in ("left", "right"):
        return f"on the {place}"
    return f"at the {place}"


def _numbers(items) -> str:
    return "[" + ", ".join(str(item.number) for item in items) + "]"


@dataclass
class ObjectEvidence:
    label: str
    confidence: float                       # the lower of the two views' confidence
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in analysed-picture pixels
    certain: bool
    number: int = 0                         # set by the report; matches the drawn box

    def share(self, width: int, height: int) -> float:
        area = max(0.0, self.box[2] - self.box[0]) * max(0.0, self.box[3] - self.box[1])
        return area / max(width * height, 1)


@dataclass
class TextEvidence:
    text: str
    confidence: float
    box: tuple[float, float, float, float]


@dataclass
class SceneReport:
    name: str                                   # "camera" or the file name
    width: int
    height: int
    objects: list[ObjectEvidence]
    quality: ImageQuality
    colours: list[tuple[str, float]] = field(default_factory=list)
    text: list[TextEvidence] | None = None      # None: text wasn't read
    rotated: bool = False
    scaled: float = 1.0

    def __post_init__(self):
        self._groups = self._group_and_number()

    # --- Building the description -----------------------------------------------

    def _group_and_number(self):
        """Objects grouped by label, certain first, people first; numbered in that order."""
        groups = []
        for certain in (True, False):
            chosen = [o for o in self.objects if o.certain == certain]
            counts = Counter(o.label for o in chosen)
            best = {label: max(o.confidence for o in chosen if o.label == label) for label in counts}
            order = sorted(counts, key=lambda label: (label != "person", -counts[label], -best[label]))
            for label in order:
                members = sorted((o for o in chosen if o.label == label), key=lambda o: (o.box[0], o.box[1]))
                groups.append((certain, label, members))
        number = 0
        for _, _, members in groups:
            for item in members:
                number += 1
                item.number = number
        self.objects = sorted(self.objects, key=lambda o: o.number)
        return groups

    def _group_phrase(self, label: str, members: list[ObjectEvidence], more: bool = False) -> str:
        if more:     # the same kind of object was already named as certain
            text = ("another " + _NAMES.get(label, label) if len(members) == 1
                    else phrase(label, len(members)).replace(" ", " more ", 1))
        else:
            text = phrase(label, len(members))
        if len(members) == 1:
            item = members[0]
            if item.share(self.width, self.height) >= 0.5:
                text += " filling most of the picture"
            else:
                text += " " + where(region(item.box, self.width, self.height))
        elif len(members) <= 3:
            places = [region(m.box, self.width, self.height) for m in members]
            if len(set(places)) == len(places):
                text += f" ({join(places)})"
        return f"{text} {_numbers(members)}"

    @property
    def certain(self) -> list[ObjectEvidence]:
        return [o for o in self.objects if o.certain]

    @property
    def possible(self) -> list[ObjectEvidence]:
        return [o for o in self.objects if not o.certain]

    def description(self) -> str:
        sentences = []
        sure_labels = {label for certain, label, _ in self._groups if certain}
        sure = [self._group_phrase(label, m) for certain, label, m in self._groups if certain]
        unsure = [self._group_phrase(label, m, more=label in sure_labels)
                  for certain, label, m in self._groups if not certain]
        if sure:
            sentences.append(f"I can see {join(sure)}.")
            if unsure:
                sentences.append(f"I might also see {join(unsure)}, but I'm not sure.")
        elif unsure:
            sentences.append(f"I'm not sure, but there might be {join(unsure)}.")
        else:
            sentences.append("I don't see any everyday objects I can recognise.")

        if self.colours:
            names = [name for name, _ in self.colours]
            if self.colours[0][1] >= 0.5:
                sentences.append(f"The picture is mostly {names[0]}.")
            else:
                sentences.append(f"The main colour{'s are' if len(names) > 1 else ' is'} {join(names)}.")

        if self.text:
            if len(self.text) <= 2:
                quoted = " / ".join(f"“{line.text}”" for line in self.text)
                sentences.append(f"It contains text: {quoted}.")
            else:
                sentences.append(f"It contains {len(self.text)} lines of text, starting “{self.text[0].text}”.")

        for note in self.quality.notes:
            sentences.append(f"{note[:-1]}, so I may have missed things.")
        return " ".join(sentences)

    def evidence(self) -> list[str]:
        lines = []
        for item in self.objects:
            place = region(item.box, self.width, self.height)
            share = item.share(self.width, self.height)
            how = "seen in the mirrored picture too" if item.certain else "not certain"
            lines.append(f"[{item.number}] {_NAMES.get(item.label, item.label)}: {item.confidence:.0%} sure, "
                         f"{place}, {share:.0%} of the picture ({how})")
        for line in self.text or []:
            lines.append(f"Text: “{line.text}” ({line.confidence:.0%} sure)")
        if self.text == []:
            lines.append("Text: none found")
        if self.colours:
            lines.append("Colours: " + ", ".join(f"{name} {share:.0%}" for name, share in self.colours))
        measured = (f"Measured: brightness {self.quality.brightness:.0%}, contrast {self.quality.contrast:.0%}, "
                    f"sharpness {self.quality.sharpness:.0f}")
        lines.append(measured)
        notes = []
        if self.rotated:
            notes.append("turned upright using the photo's orientation note")
        if self.scaled < 1:
            notes.append(f"scaled to {self.width}×{self.height} for analysis")
        if notes:
            lines.append("Picture: " + "; ".join(notes))
        return lines

    def message(self, heading: str | None = None) -> str:
        parts = [f"{heading}: {self.description()}" if heading else self.description()]
        parts.append("Evidence:\n" + "\n".join(f"  {line}" for line in self.evidence()))
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "width": self.width, "height": self.height,
            "description": self.description(),
            "objects": [{"number": o.number, "label": o.label, "confidence": round(o.confidence, 3),
                         "box": [round(v, 1) for v in o.box], "certain": o.certain} for o in self.objects],
            "text": None if self.text is None else [
                {"text": t.text, "confidence": round(t.confidence, 3), "box": [round(v, 1) for v in t.box]}
                for t in self.text],
            "colours": self.colours,
            "quality": {"brightness": round(self.quality.brightness, 3), "contrast": round(self.quality.contrast, 3),
                        "sharpness": round(self.quality.sharpness, 1), "notes": self.quality.notes},
        }

    # --- The picture with its evidence drawn on it ----------------------------------

    def annotate(self, image):
        """A copy of the analysed picture with numbered boxes: solid cyan for
        certain objects, thin amber for uncertain ones, thin green for text."""
        import cv2

        canvas = image.copy()
        scale = max(canvas.shape[:2]) / 800
        thickness = max(1, round(2 * scale))
        font = 0.5 * max(scale, 0.6)
        for line in self.text or []:
            x1, y1, x2, y2 = (int(v) for v in line.box)
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (90, 200, 90), max(1, thickness // 2))
        for item in self.objects:
            colour = (255, 210, 0) if item.certain else (0, 170, 255)
            x1, y1, x2, y2 = (int(v) for v in item.box)
            cv2.rectangle(canvas, (x1, y1), (x2, y2), colour, thickness if item.certain else max(1, thickness // 2))
            tag = f"{item.number} {_NAMES.get(item.label, item.label)} {item.confidence:.0%}"
            (w, h), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, font, max(1, thickness // 2))
            top = max(y1, h + 6)
            left = max(0, min(x1, canvas.shape[1] - w - 6))      # keep the label inside the picture
            cv2.rectangle(canvas, (left, top - h - 6), (left + w + 6, top), colour, -1)
            cv2.putText(canvas, tag, (left + 3, top - 4), cv2.FONT_HERSHEY_SIMPLEX, font, (0, 0, 0),
                        max(1, thickness // 2), cv2.LINE_AA)
        return canvas
