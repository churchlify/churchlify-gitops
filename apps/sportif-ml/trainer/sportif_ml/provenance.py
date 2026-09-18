import hashlib
import json
import os
import sys
from pathlib import Path

from .dataset import dataset_root


def validate_input(video_key):
    root = Path(os.environ.get("ML_WORK_DIR", "/work"))
    manifest_path = root / "raw" / "video-provenance.json"
    if not manifest_path.exists():
        raise SystemExit(f"missing provenance manifest: {manifest_path}")
    entries = json.loads(manifest_path.read_text())
    entry = next((item for item in entries if item.get("objectKey") == video_key), None)
    if entry is None:
        raise SystemExit(f"video is absent from provenance manifest: {video_key}")
    video_path = root / "raw" / video_key
    if not video_path.exists():
        raise SystemExit(f"source video is absent: {video_path}")
    digest = hashlib.sha256(video_path.read_bytes()).hexdigest()
    if digest != entry.get("sha256"):
        raise SystemExit("source video SHA-256 does not match provenance")
    print(json.dumps({"status": "PASS", "videoId": entry["videoId"], "sha256": digest}))


def release(dataset_id):
    root = dataset_root(dataset_id)
    manifest_path = root / "artifacts" / "model-manifest.json"
    if not (root / "artifacts" / "model.pt").exists() or not (root / "artifacts" / "model.onnx").exists():
        raise SystemExit("both model.pt and model.onnx are required")
    dataset_manifest = json.loads((root / "dataset-manifest.json").read_text())
    if dataset_manifest.get("commercialGate", {}).get("datasetRightsVerified") is not True:
        raise SystemExit("dataset rights are not verified")
    manifest = {
        "schemaVersion": 1,
        "modelId": os.environ.get("MODEL_ID", "sportif-ball-detector-v001"),
        "status": "CANDIDATE",
        "architecture": {"name": "Faster R-CNN ResNet-50 FPN", "sourceRepository": "https://github.com/pytorch/vision", "sourceRevision": "v0.20.1", "license": "BSD-3-Clause"},
        "training": {"initialization": "random", "pretrainedWeightsUsed": False, "datasetId": dataset_id, "seed": int(os.environ.get("DATASET_SEED", "42"))},
        "artifacts": {name: {"sha256": hashlib.sha256((root / "artifacts" / name).read_bytes()).hexdigest()} for name in ("model.pt", "model.onnx")},
        "commercialGate": {"datasetRightsVerified": False, "trainingCodeLicenseVerified": True, "dependencyLicensesVerified": False, "pretrainedWeightsUsed": False, "provenanceComplete": False},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "validate-input":
        validate_input(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "release":
        release(sys.argv[2])
    else:
        raise SystemExit("usage: python -m sportif_ml.provenance validate-input OBJECT_KEY | release DATASET_ID")