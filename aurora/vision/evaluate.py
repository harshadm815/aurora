"""
Measuring the vision system.

Self-check (no camera, no outside images; also `"AURORA Command Centre.exe" --vision-check`):

    python -m aurora.vision.evaluate

    makes test pictures in memory (printed text, a photo stored sideways
    with an EXIF orientation note, transparent, 16-bit and grey images, a
    dark picture, a featureless picture and a "decompression bomb") and
    checks each is handled correctly.

Benchmark on your own labelled photos:

    python -m aurora.vision.evaluate --labels labels.json [--out results.json]

    labels.json is a list of {"path": "...", "required": ["person"], "also": ["cup"]}:
    "required" objects should be named, "also" objects may be (present or
    ambiguous), anything else named is a mistake. An empty "required" list
    means the photo has none of the 80 everyday object kinds.

    Reports, for AURORA's grounded rule and for the plain "anything above
    VISION_MIN_CONFIDENCE" rule it replaced:
      - recall: share of required objects named as certain;
      - precision: share of certain names that are right (photos with objects);
      - false-alarm rate: share of photos with no objects where something is
        named as certain.
"""

import argparse
import io
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from aurora.config import settings


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _encode(image, kind=".png") -> bytes:
    import cv2

    ok, data = cv2.imencode(kind, image)
    assert ok
    return data.tobytes()


def text_picture(text="AURORA VISION 42", width=900, height=200):
    import cv2
    import numpy as np

    image = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.putText(image, text, (20, height // 2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    return image


def sideways_jpeg(image) -> bytes:
    """A JPEG stored turned 90° with an EXIF note (Orientation = 6) saying how to turn it back,
    as phones save portrait photos."""
    import numpy as np
    from PIL import Image

    stored = np.rot90(image[:, :, ::-1], k=1)          # turned 90° anticlockwise, RGB
    picture = Image.fromarray(np.ascontiguousarray(stored))
    exif = Image.Exif()
    exif[0x0112] = 6                                   # "rotate 90° clockwise to view"
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG", quality=95, exif=exif)
    return buffer.getvalue()


def bomb_png(width=60000, height=60000) -> bytes:
    """A tiny PNG whose header claims a huge picture (decoding it would need ~10 GB)."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b"")


def self_check(analyzer=None) -> list[Check]:
    import numpy as np
    from PIL import Image

    from aurora.vision.analyzer import VisionAnalyzer
    from aurora.vision.image_input import ImageError, decode_image

    analyzer = analyzer or VisionAnalyzer()
    checks: list[Check] = []

    def check(name, function):
        try:
            passed, detail = function()
        except Exception as error:      # a crash is a failed check, with its type
            passed, detail = False, f"{type(error).__name__}: {error}"
        checks.append(Check(name, bool(passed), detail))

    def ocr_plain():
        report = analyzer.analyse(decode_image(_encode(text_picture()), "text.png"), read_text=True)
        got = " ".join(line.text for line in report.text or [])
        return got == "AURORA VISION 42", f"read {got!r}"

    def ocr_sideways():
        loaded = decode_image(sideways_jpeg(text_picture()), "portrait.jpg")
        report = analyzer.analyse(loaded, read_text=True)
        got = " ".join(line.text for line in report.text or [])
        return loaded.rotated and loaded.size == (900, 200) and got == "AURORA VISION 42", \
            f"rotated={loaded.rotated}, size={loaded.size}, read {got!r}"

    def transparent():
        rgba = np.zeros((80, 120, 4), dtype=np.uint8)              # fully transparent
        buffer = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buffer, "PNG")
        loaded = decode_image(buffer.getvalue(), "clear.png")
        return int(loaded.image.min()) == 255, f"transparent pixels became {int(loaded.image.min())} (want 255, white)"

    def sixteen_bit():
        grey = np.full((60, 90), 40000, dtype=np.uint16)
        buffer = io.BytesIO()
        Image.fromarray(grey).save(buffer, "PNG")
        loaded = decode_image(buffer.getvalue(), "deep.png")
        value = int(loaded.image[0, 0, 0])
        return loaded.image.dtype == np.uint8 and loaded.image.shape == (60, 90, 3) and abs(value - 156) <= 1, \
            f"{loaded.image.dtype}, {loaded.image.shape}, value {value} (want ~156)"

    def dark():
        report = analyzer.analyse(np.full((240, 320, 3), 8, dtype=np.uint8), name="dark")
        return "The picture is very dark." in report.quality.notes, f"notes: {report.quality.notes}"

    def featureless():
        gradient = np.tile(np.linspace(0, 255, 640, dtype=np.uint8), (480, 1))
        image = np.dstack([gradient, gradient // 2, 255 - gradient])
        report = analyzer.analyse(image, name="gradient")
        return not report.certain, f"named as certain: {[o.label for o in report.certain]}"

    def bomb():
        try:
            decode_image(bomb_png(), "bomb.png")
        except ImageError as error:
            return "million" in str(error), str(error)
        return False, "was decoded"

    def not_an_image():
        try:
            decode_image(b"MZ\x90\x00 this is a program, not a picture", "fake.jpg")
        except ImageError as error:
            return True, str(error)
        return False, "was accepted"

    check("Reads printed text", ocr_plain)
    check("Turns a sideways phone photo upright (EXIF)", ocr_sideways)
    check("Transparent image on white", transparent)
    check("16-bit image to 8-bit colour", sixteen_bit)
    check("Warns about a very dark picture", dark)
    check("Names nothing in a featureless picture", featureless)
    check("Refuses a decompression bomb before decoding", bomb)
    check("Refuses a file that isn't an image", not_an_image)
    return checks


# --- Benchmark on labelled photos ----------------------------------------------------

def benchmark(items: list[dict], analyzer=None, on_item=None) -> dict:
    from aurora.vision.analyzer import VisionAnalyzer
    from aurora.vision.image_input import load_image

    analyzer = analyzer or VisionAnalyzer()
    rules = {"grounded": lambda report, raw: {o.label for o in report.certain},
             "plain": lambda report, raw: {d.label for d in raw}}
    totals = {name: {"found": 0, "needed": 0, "right": 0, "said": 0, "alarms": 0, "negatives": 0, "mistakes": []}
              for name in rules}
    seconds = 0.0
    for item in items:
        loaded = load_image(item["path"])
        started = time.perf_counter()
        report = analyzer.analyse(loaded)
        seconds += time.perf_counter() - started
        raw = analyzer.detector.detect(loaded.image)
        required, also = set(item.get("required", [])), set(item.get("also", []))
        for name, rule in rules.items():
            said = rule(report, raw)
            total = totals[name]
            if not required:
                total["negatives"] += 1
                if said:
                    total["alarms"] += 1
                    total["mistakes"].append([item.get("id", item["path"]), sorted(said)])
                continue
            total["needed"] += len(required)
            total["found"] += len(required & said)
            total["said"] += len(said)
            total["right"] += len(said & (required | also))
            wrong = said - required - also
            if wrong:
                total["mistakes"].append([item.get("id", item["path"]), sorted(wrong)])
        if on_item:
            on_item(item, report)

    results = {"photos": len(items), "seconds_per_photo": round(seconds / max(len(items), 1), 2)}
    for name, total in totals.items():
        results[name] = {
            "recall": round(total["found"] / total["needed"], 3) if total["needed"] else None,
            "precision": round(total["right"] / total["said"], 3) if total["said"] else None,
            "false_alarm_rate": round(total["alarms"] / total["negatives"], 3) if total["negatives"] else None,
            "photos_with_objects": len(items) - total["negatives"],
            "photos_without": total["negatives"],
            "mistakes": total["mistakes"],
        }
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora.vision.evaluate", description=__doc__.split("\n\n")[0])
    parser.add_argument("--labels", type=Path, help="JSON list of labelled photos (see the module notes)")
    parser.add_argument("--out", type=Path, help="write the results as JSON")
    options = parser.parse_args(argv)

    if options.labels:
        items = json.loads(options.labels.read_text(encoding="utf-8"))
        results = benchmark(items)
        for name in ("grounded", "plain"):
            r = results[name]
            print(f"{name:9} recall {r['recall']}  precision {r['precision']}  false alarms {r['false_alarm_rate']}")
        print(f"{results['photos']} photos, {results['seconds_per_photo']} s each "
              f"(threshold {settings.VISION_MIN_CONFIDENCE}, certain at {settings.VISION_CERTAIN_CONFIDENCE})")
        ok = True
    else:
        checks = self_check()
        for item in checks:
            print(f"{'PASS' if item.passed else 'FAIL'}  {item.name}: {item.detail}")
        results = {"checks": [item.__dict__ for item in checks]}
        ok = all(item.passed for item in checks)
        print(f"{sum(item.passed for item in checks)} of {len(checks)} checks passed")

    if options.out:
        options.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
