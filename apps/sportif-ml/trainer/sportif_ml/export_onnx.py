import hashlib
import json
import os
import sys

import numpy as np
import onnx
import onnxruntime
import torch

from .dataset import dataset_root
from .model import build_model


class DetectionExport(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, image):
        output = self.model([image[0]])[0]
        return output["boxes"], output["labels"], output["scores"]


def export(dataset_id):
    root = dataset_root(dataset_id)
    checkpoint = root / "artifacts" / "model.pt"
    if not checkpoint.exists():
        raise SystemExit("model checkpoint is missing")
    image_size = int(os.environ.get("TRAINING_IMAGE_SIZE", "1024"))
    model = build_model()
    model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True))
    wrapper = DetectionExport(model.eval())
    output_path = root / "artifacts" / "model.onnx"
    example = torch.zeros(1, 3, image_size, image_size)
    torch.onnx.export(
        wrapper,
        example,
        output_path,
        input_names=["images"],
        output_names=["boxes", "labels", "scores"],
        dynamic_axes={
            "images": {2: "height", 3: "width"},
            "boxes": {0: "detections"},
            "labels": {0: "detections"},
            "scores": {0: "detections"},
        },
        opset_version=17,
        dynamo=False,
    )
    onnx_model = onnx.load(output_path)
    onnx.checker.check_model(onnx_model)
    session = onnxruntime.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    outputs = session.run(None, {"images": np.zeros((1, 3, image_size, image_size), dtype=np.float32)})
    if len(outputs) != 3 or outputs[0].ndim != 2 or outputs[0].shape[-1] != 4:
        raise SystemExit("ONNX Runtime returned an invalid detection output signature")
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    result = {
        "onnxValid": True,
        "runtimeValidation": "PASS",
        "opset": 17,
        "inputSize": f"{image_size}x{image_size}",
        "outputNames": [output.name for output in session.get_outputs()],
        "modelBytes": output_path.stat().st_size,
        "sha256": digest,
    }
    (root / "artifacts" / "onnx-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    export(sys.argv[1])