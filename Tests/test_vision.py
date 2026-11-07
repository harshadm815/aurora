import importlib.util

import pytest

from aurora.brain.brain import Brain
from aurora.config import settings
from aurora.events import topics
from aurora.kernel.engine import AuroraEngine
from aurora.kernel.permissions import PermissionManager
from aurora.tool_plugins.camera import CameraTool
from aurora.vision.analyzer import describe
from aurora.vision.camera import Camera, VisionUnavailable
from aurora.vision.detector import Detection
from aurora.vision.image_input import ImageQuality
from aurora.vision.scene import ObjectEvidence, SceneReport
from aurora.vision.watch import Watcher

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def picture(width=640, height=480, colour=0):
    return np.full((height, width, 3), colour, dtype=np.uint8)


def text_picture(text="AURORA VISION TEST 42"):
    image = picture(900, 200, 255)
    cv2.putText(image, text, (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    return image


def detection(label, x=320, confidence=0.9):
    return Detection(label, confidence, (x - 20, 100, x + 20, 200))


# --- Describing a scene -------------------------------------------------------

@pytest.mark.parametrize(
    "found, sentence",
    [
        ([], "I don't recognise any objects in view."),
        ([detection("laptop")], "I can see a laptop."),
        ([detection("person", x=100)], "I can see a person on the left."),
        ([detection("person", x=320), detection("laptop"), detection("cup"), detection("cup")],
         "I can see a person in the middle, two cups and a laptop."),
        ([detection("person"), detection("person"), detection("cell phone")],
         "I can see two people and a phone."),
        ([detection("apple")], "I can see an apple."),
    ],
)
def test_describe(found, sentence):
    assert describe(found, width=640) == sentence


# --- Object detector (real model, decoded from crafted outputs) ---------------

def _detector_available():
    return settings.VISION_DETECTOR_MODEL.is_file()


@pytest.fixture(scope="module")
def detector():
    if not _detector_available():
        pytest.skip("NanoDet model not downloaded")
    from aurora.vision.detector import ObjectDetector

    return ObjectDetector()


def test_decode_places_a_box_around_the_anchor(detector):
    # Three feature maps (strides 8, 16, 32); one confident "cup" on the 13x13 map.
    outputs = []
    for side in (52, 26, 13):
        outputs.append(np.zeros((1, side * side, 80), np.float32))
    for side in (52, 26, 13):
        outputs.append(np.zeros((1, side * side, 32), np.float32))

    cell = 6 * 13 + 6                      # Row 6, column 6 of the 13x13 grid.
    outputs[2][0, cell, 41] = 0.95         # Class 41 = cup.
    distribution = np.full(32, -10.0, np.float32)
    distribution[[2, 10, 18, 26]] = 10.0   # Distance of 2 bins (x 32 px) on every side.
    outputs[5][0, cell] = distribution

    boxes, scores, classes = detector.decode(outputs)
    best = int(scores.argmax())

    assert classes[best] == 41
    centre = 6 * 32 + 0.5 * 31
    assert np.allclose(boxes[best], [centre - 64, centre - 64, centre + 64, centre + 64], atol=0.5)


def test_blank_picture_has_no_objects(detector):
    assert detector.detect(picture()) == []


# --- Motion -------------------------------------------------------------------

def test_motion_detector():
    from aurora.vision.motion import MotionDetector

    motion = MotionDetector(threshold=0.02)
    still = picture()
    moved = picture()
    cv2.rectangle(moved, (200, 150), (440, 330), (255, 255, 255), -1)

    assert motion.motion(still) is False      # First frame sets the background.
    assert motion.motion(still) is False
    assert motion.motion(moved) is True


# --- Camera tool with a fake camera -------------------------------------------

class FakeCamera:
    opened = 0
    is_open = False

    def __init__(self, image=None, frames=()):
        self.image = image if image is not None else picture()
        self.frame_list = list(frames)
        FakeCamera.opened += 1

    def __enter__(self):
        FakeCamera.is_open = True
        return self

    def __exit__(self, *exc):
        FakeCamera.is_open = False

    def frame(self):
        return self.image

    def frames(self, interval=0.0):
        yield from self.frame_list


class FakeAnalyzer:
    def __init__(self, objects="I can see a cup.", lines=("HELLO",)):
        self._objects, self._lines = objects, list(lines)
        self.seen = []

    def objects(self, image):
        assert not FakeCamera.is_open or True
        self.seen.append(image)
        return self._objects, [detection("cup")]

    def analyse(self, image, read_text=False, name="camera"):
        self.seen.append(image)
        cup = ObjectEvidence("cup", 0.9, (300, 200, 340, 280), certain=True)
        return SceneReport(name, 640, 480, [cup], ImageQuality(0.5, 0.2, 100.0))

    def text(self, image):
        if not self._lines:
            return "I can't find any readable text.", []
        return "The text says:\n" + "\n".join(f"  {l}" for l in self._lines), self._lines


def test_look(monkeypatch):
    notes = []
    tool = CameraTool(FakeAnalyzer(), camera=FakeCamera, notify=notes.append)

    result = tool.execute(action="look")

    assert result.message.startswith("I can see a cup in the centre [1].\n\nEvidence:\n  [1] cup: 90% sure")
    assert result.data.objects[0].label == "cup"
    assert notes == ["📷  Camera on…", "📷  Camera off."]
    assert FakeCamera.is_open is False


def test_read_text():
    result = CameraTool(FakeAnalyzer(lines=["Chapter 2", "Entropy"]), camera=FakeCamera).execute(action="read")

    assert result.message == "The text says:\n  Chapter 2\n  Entropy"


def test_camera_unavailable_is_explained():
    def broken():
        raise VisionUnavailable("I can't open the camera.")

    notes = []
    result = CameraTool(FakeAnalyzer(), camera=broken, notify=notes.append).execute(action="look")

    assert result.success is False
    assert result.message == "I can't open the camera."
    assert notes[-1] == "📷  Camera off."


def test_real_camera_is_blocked_in_tests():
    with pytest.raises(VisionUnavailable, match="must not open the real camera"):
        Camera()


# --- Engine -------------------------------------------------------------------

@pytest.fixture
def engine(fake_provider, memory):
    def make(permissions=None):
        engine = AuroraEngine(Brain(fake_provider), permissions=permissions, memory=memory,
                              tools=[CameraTool(FakeAnalyzer(), camera=FakeCamera)])
        engine.start()
        return engine
    return make


def test_what_do_you_see(engine, fake_provider):
    assert engine().process_message("Aurora, what do you see?").startswith("I can see a cup in the centre [1].")
    assert fake_provider.calls == []


def test_camera_needs_permission(engine):
    FakeCamera.opened = 0

    reply = engine(PermissionManager()).process_message("what do you see?")

    assert "not allowed to use the Camera tool" in reply
    assert FakeCamera.opened == 0


# --- Watch mode ---------------------------------------------------------------

class ScriptedMotion:
    def __init__(self, pattern):
        self.pattern = list(pattern)

    def motion(self, frame):
        return self.pattern.pop(0) if self.pattern else False


def test_watch_reports_motion_with_cooldown(engine, monkeypatch):
    monkeypatch.setattr(settings, "VISION_MOTION_COOLDOWN", 10)
    times = iter([0, 5, 20])           # Second motion is within the cooldown.
    lines, events = [], []
    aurora = engine()
    aurora.events.subscribe(topics.MOTION_DETECTED, events.append)

    watcher = Watcher(aurora, analyzer=FakeAnalyzer(objects="I can see a person in the middle."),
                      motion=ScriptedMotion([True, True, True]),
                      camera=lambda: FakeCamera(frames=[picture()] * 3),
                      output=lines.append, clock=lambda: next(times))

    alerts = watcher.run()

    assert alerts == 2
    assert [l.split("] ", 1)[1] for l in lines if l.startswith("[")] == [
        "Motion detected. I can see a person in the middle.",
        "Motion detected. I can see a person in the middle.",
    ]
    assert lines[-1].startswith("📷  Stopped watching. Camera off. (2 alerts)")
    assert len(events) == 2


def test_watch_needs_permission(engine):
    with pytest.raises(VisionUnavailable, match="not allowed to use the camera"):
        Watcher(engine(PermissionManager()), camera=FakeCamera).run()


def test_watch_stops_on_ctrl_c(engine):
    class Interrupting(FakeCamera):
        def frames(self, interval=0.0):
            yield picture()
            raise KeyboardInterrupt

    lines = []
    alerts = Watcher(engine(), motion=ScriptedMotion([False]), camera=Interrupting,
                     output=lines.append).run()

    assert alerts == 0
    assert lines[-1].startswith("📷  Stopped watching. Camera off.")


# --- Real OCR (RapidOCR, offline) ---------------------------------------------

@pytest.fixture(scope="module")
def reader():
    if importlib.util.find_spec("rapidocr_onnxruntime") is None:
        pytest.skip("RapidOCR not installed")
    from aurora.vision.analyzer import TextReader

    return TextReader()


def test_real_ocr_reads_printed_text(reader):
    assert reader.read(text_picture()) == ["AURORA VISION TEST 42"]


def test_real_ocr_on_blank_picture(reader):
    assert reader.read(picture(400, 200, 255)) == []


def test_reading_an_image_from_documents(tmp_path, reader, detector):
    from aurora.tool_plugins.documents import DocumentsTool
    from aurora.vision.analyzer import VisionAnalyzer

    folder = tmp_path / "Documents"
    folder.mkdir()
    cv2.imwrite(str(folder / "note.png"), text_picture("BLACK HOLE NOTES"))

    tool = DocumentsTool(folder, analyzer=VisionAnalyzer(detector, reader))
    result = tool.execute(action="read", name="note.png")

    assert result.message.startswith(
        "note.png: I don't see any everyday objects I can recognise. The picture is mostly white. "
        "It contains text: “BLACK HOLE NOTES”.\n\nEvidence:\n  Text: “BLACK HOLE NOTES”"
    )
    assert [line.text for line in result.data.text] == ["BLACK HOLE NOTES"]
