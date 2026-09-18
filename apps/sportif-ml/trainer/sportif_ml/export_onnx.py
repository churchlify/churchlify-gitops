import hashlib
import json
import sys

import torch

from .dataset import dataset_root
from .model import build_model


def export(dataset_id):
    root = dataset_root(dataset_id)
    model = build_model()
    model.load_state_dict(torch.load(root / "artifacts" / "model.pt", map_location="cpu"))
    model.eval()
    output_path = root / "artifacts" / "model.onnx"
    example = [torch.zeros(3, 1024, 1024)]
    torch.onnx.export(model, (example,), output_path, opset_version=17, dynamo=False)
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    result = {"onnxValid": output_path.stat().st_size > 0, "opset": 17, "inputSize": "1024x1024", "modelBytes": output_path.stat().st_size, "sha256": digest}
    (root / "artifacts" / "onnx-validation.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    export(sys.argv[1])