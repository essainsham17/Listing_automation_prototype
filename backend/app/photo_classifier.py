"""Classifies car photos as exterior or interior with a bundled ONNX YOLOv8 model."""

import ast
import io
import logging

import numpy as np
from PIL import Image

from app import config

logger = logging.getLogger(__name__)

_session = None
_names: dict[int, str] = {}
_imgsz: tuple[int, int] = (224, 224)


def _get_session():
    """Loads and caches the ONNX inference session plus its class names and input size from metadata."""
    global _session, _names, _imgsz
    if _session is None:
        import onnxruntime as ort

        _session = ort.InferenceSession(
            config.PHOTO_CLASSIFIER_MODEL_PATH, providers=["CPUExecutionProvider"]
        )
        meta = _session.get_modelmeta().custom_metadata_map
        _names = ast.literal_eval(meta["names"])
        height, width = ast.literal_eval(meta.get("imgsz", "[224, 224]"))
        _imgsz = (height, width)
        logger.info("photo_classifier: loaded model, classes=%s", _names)
    return _session


def _preprocess(image: Image.Image, size: tuple[int, int]) -> np.ndarray:
    """Resizes the shortest edge, centre crops and converts a PIL image into a (1, 3, H, W) float32 batch."""
    crop_h, crop_w = size
    target = min(size)

    img_w, img_h = image.size
    if img_w <= img_h:
        new_w, new_h = target, int(target * img_h / img_w)
    else:
        new_h, new_w = target, int(target * img_w / img_h)
    resized = image.resize((new_w, new_h), Image.BILINEAR)

    left = int(round((new_w - crop_w) / 2.0))
    top = int(round((new_h - crop_h) / 2.0))
    cropped = resized.crop((left, top, left + crop_w, top + crop_h))

    chw = np.transpose(np.asarray(cropped, dtype=np.float32) / 255.0, (2, 0, 1))
    return np.ascontiguousarray(chw[None])


def classify(image_bytes: bytes) -> tuple[str | None, float]:
    """Returns (category, confidence) for image bytes, with category None when below the confidence minimum."""
    session = _get_session()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    batch = _preprocess(image, _imgsz)
    probs = session.run(None, {session.get_inputs()[0].name: batch})[0][0]

    top = int(probs.argmax())
    confidence = float(probs[top])
    if confidence < config.PHOTO_CLASSIFIER_MIN_CONFIDENCE:
        return None, confidence
    return _names[top].lower(), confidence
