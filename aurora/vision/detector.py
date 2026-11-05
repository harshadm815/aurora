"""
Object detection with NanoDet-Plus (OpenCV model zoo, Apache-2.0),
run on the CPU through OpenCV's DNN module.

It recognises the 80 everyday object classes of the COCO dataset.
People are only ever reported as "person": there is no face
recognition or identification of individuals.
"""

from dataclasses import dataclass
from pathlib import Path

from aurora.config import settings
from aurora.vision.camera import VisionUnavailable

COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]

INPUT_SIZE = 416
REG_MAX = 7


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: tuple[float, float, float, float]   # x1, y1, x2, y2 in image pixels

    @property
    def centre_x(self) -> float:
        return (self.box[0] + self.box[2]) / 2


def _softmax(values, axis):
    import numpy as np

    exp = np.exp(values - values.max(axis=axis, keepdims=True))
    return exp / exp.sum(axis=axis, keepdims=True)


class ObjectDetector:

    def __init__(self, model_path: str | Path | None = None,
                 min_confidence: float | None = None, iou_threshold: float = 0.6):
        try:
            import cv2
            import numpy as np
        except ImportError as error:
            raise VisionUnavailable("The 'opencv-python' package isn't installed.") from error

        path = Path(model_path or settings.VISION_DETECTOR_MODEL)
        if not path.is_file():
            raise VisionUnavailable(
                f"The object detection model isn't at {path}. Download "
                "object_detection_nanodet_2022nov.onnx from OpenCV's opencv_zoo repository."
            )

        self._cv2, self._np = cv2, np
        self.net = cv2.dnn.readNet(str(path))
        self.min_confidence = settings.VISION_MIN_CONFIDENCE if min_confidence is None else min_confidence
        self.iou_threshold = iou_threshold
        self.mean = np.array([103.53, 116.28, 123.675], dtype=np.float32)
        self.std = np.array([57.375, 57.12, 58.395], dtype=np.float32)
        self._anchors: dict[int, object] = {}

    def _anchor_points(self, count: int):
        """Centres of the grid cells for a feature map with ``count`` cells."""
        if count not in self._anchors:
            np = self._np
            side = int(round(count ** 0.5))
            stride = INPUT_SIZE / side
            xs, ys = np.meshgrid(np.arange(side) * stride, np.arange(side) * stride)
            centres = np.column_stack((xs.ravel(), ys.ravel())) + 0.5 * (stride - 1)
            self._anchors[count] = (centres, stride)
        return self._anchors[count]

    def _letterbox(self, image):
        """Resize keeping the aspect ratio, padded to INPUT_SIZE x INPUT_SIZE."""
        cv2, np = self._cv2, self._np
        height, width = image.shape[:2]
        scale = INPUT_SIZE / max(height, width)
        resized = cv2.resize(image, (int(round(width * scale)), int(round(height * scale))))
        canvas = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8)
        top = (INPUT_SIZE - resized.shape[0]) // 2
        left = (INPUT_SIZE - resized.shape[1]) // 2
        canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized
        return canvas, scale, left, top

    def decode(self, outputs) -> tuple[list, list, list]:
        """Turns raw network outputs into boxes (model pixels), scores and class ids."""
        np = self._np
        outputs = [o.reshape(o.shape[-2], o.shape[-1]) for o in outputs]
        scores_by_count = {o.shape[0]: o for o in outputs if o.shape[1] == len(COCO_LABELS)}
        boxes_by_count = {o.shape[0]: o for o in outputs if o.shape[1] == 4 * (REG_MAX + 1)}

        boxes, scores, classes = [], [], []
        for count, class_scores in scores_by_count.items():
            distribution = boxes_by_count[count].reshape(-1, REG_MAX + 1)
            centres, stride = self._anchor_points(count)
            distances = (_softmax(distribution, axis=1) @ np.arange(REG_MAX + 1)).reshape(-1, 4) * stride

            x1 = np.clip(centres[:, 0] - distances[:, 0], 0, INPUT_SIZE)
            y1 = np.clip(centres[:, 1] - distances[:, 1], 0, INPUT_SIZE)
            x2 = np.clip(centres[:, 0] + distances[:, 2], 0, INPUT_SIZE)
            y2 = np.clip(centres[:, 1] + distances[:, 3], 0, INPUT_SIZE)

            boxes.append(np.column_stack((x1, y1, x2, y2)))
            scores.append(class_scores.max(axis=1))
            classes.append(class_scores.argmax(axis=1))

        if not boxes:
            return [], [], []
        return np.concatenate(boxes), np.concatenate(scores), np.concatenate(classes)

    def detect(self, image) -> list[Detection]:
        cv2, np = self._cv2, self._np
        if image is None or image.ndim != 3:
            return []

        padded, scale, left, top = self._letterbox(image)
        blob = cv2.dnn.blobFromImage((padded.astype(np.float32) - self.mean) / self.std)
        self.net.setInput(blob)
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())

        boxes, scores, classes = self.decode(outputs)
        if len(boxes) == 0:
            return []

        widths_heights = boxes.copy()
        widths_heights[:, 2:] -= widths_heights[:, :2]
        keep = cv2.dnn.NMSBoxes(widths_heights.tolist(), scores.tolist(),
                                self.min_confidence, self.iou_threshold)

        detections = []
        for index in np.array(keep).flatten():
            x1, y1, x2, y2 = boxes[index]
            # Back from the padded 416x416 image to the original picture,
            # kept inside its edges.
            height, width = image.shape[:2]
            box = (
                float(min(max((x1 - left) / scale, 0), width)),
                float(min(max((y1 - top) / scale, 0), height)),
                float(min(max((x2 - left) / scale, 0), width)),
                float(min(max((y2 - top) / scale, 0), height)),
            )
            detections.append(Detection(COCO_LABELS[int(classes[index])], float(scores[index]), box))

        return sorted(detections, key=lambda d: d.confidence, reverse=True)
