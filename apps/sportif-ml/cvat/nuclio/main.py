import base64
import io
import json
import math
import os

import torch
from PIL import Image
from sportif_ml.model import build_model


LABEL = "ball"
CHECKPOINT = os.environ.get(
    "SPORTIF_MODEL_CHECKPOINT",
    "/models/epoch-0015.pt",
)
DEFAULT_THRESHOLD = float(os.environ.get("SPORTIF_SCORE_THRESHOLD", "0.25"))


def _threshold(value):
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError("threshold must be finite and within [0, 1]")
    return result


def _decode_image(encoded):
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("image must be a non-empty base64 string")
    try:
        payload = base64.b64decode(encoded, validate=True)
        return Image.open(io.BytesIO(payload)).convert("RGB")
    except Exception as error:
        raise ValueError("image is not valid base64-encoded image data") from error


def _tensor(image, device):
    tensor = torch.frombuffer(
        bytearray(image.tobytes()),
        dtype=torch.uint8,
    ).view(image.height, image.width, 3)
    return tensor.permute(2, 0, 1).float().div(255.0).to(device)


def _results(prediction, width, height, threshold):
    output = []
    boxes = prediction["boxes"].detach().cpu().tolist()
    labels = prediction["labels"].detach().cpu().tolist()
    scores = prediction["scores"].detach().cpu().tolist()
    for box, label, score in zip(boxes, labels, scores):
        if int(label) != 1 or float(score) < threshold:
            continue
        x1, y1, x2, y2 = (
            min(max(float(box[0]), 0.0), float(width)),
            min(max(float(box[1]), 0.0), float(height)),
            min(max(float(box[2]), 0.0), float(width)),
            min(max(float(box[3]), 0.0), float(height)),
        )
        if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        output.append({
            "confidence": str(float(score)),
            "label": LABEL,
            "points": [x1, y1, x2, y2],
            "type": "rectangle",
        })
    return output


def init_context(context):
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the Sportif CVAT detector")
    device = torch.device("cuda")
    model = build_model()
    model.load_state_dict(torch.load(CHECKPOINT, map_location="cpu", weights_only=True))
    model.to(device).eval()
    context.user_data.device = device
    context.user_data.model = model
    context.logger.info_with("Sportif detector initialized", checkpoint=CHECKPOINT)


def handler(context, event):
    try:
        data = event.body
        if isinstance(data, (bytes, str)):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        image = _decode_image(data.get("image"))
        threshold = _threshold(data.get("threshold", DEFAULT_THRESHOLD))
        tensor = _tensor(image, context.user_data.device)
        with torch.inference_mode():
            prediction = context.user_data.model([tensor])[0]
        result = _results(prediction, image.width, image.height, threshold)
        return context.Response(
            body=json.dumps(result),
            headers={},
            content_type="application/json",
            status_code=200,
        )
    except (ValueError, json.JSONDecodeError) as error:
        return context.Response(
            body=json.dumps({"error": str(error)}),
            headers={},
            content_type="application/json",
            status_code=400,
        )
