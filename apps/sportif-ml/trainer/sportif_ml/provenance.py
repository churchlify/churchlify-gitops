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
    artifacts = root / "artifacts"
    required = ("model.pt", "model.onnx", "metrics.json", "onnx-validation.json", "training-metadata.json")
    missing = [name for name in required if not (artifacts / name).is_file()]
    if missing:
        raise SystemExit(f"candidate provenance requires all artifacts: {missing}")
    dataset_manifest = json.loads((root / "dataset-manifest.json").read_text())
    dataset_validation = json.loads((root / "dataset-validation.json").read_text())
    dataset_approval = json.loads((root / "dataset-approval.json").read_text())
    if dataset_manifest.get("commercialGate", {}).get("datasetRightsVerified") is not True or dataset_approval.get("rightsVerified") is not True:
        raise SystemExit("dataset rights are not verified")
    if dataset_validation.get("status") != "PASS" or dataset_approval.get("datasetApprovalGranted") is not True:
        raise SystemExit("dataset validation and human approval are required")
    metrics = json.loads((artifacts / "metrics.json").read_text())
    onnx_validation = json.loads((artifacts / "onnx-validation.json").read_text())
    if metrics.get("status") != "PASS" or onnx_validation.get("runtimeValidation") != "PASS":
        raise SystemExit("evaluation and ONNX runtime validation must pass")
    manifest = {
        "schemaVersion": 1,
        "modelId": os.environ.get("MODEL_ID", "sportif-ball-detector-v001"),
        "status": "CANDIDATE",
        "architecture": {"name": "Faster R-CNN ResNet-50 FPN", "sourceRepository": "https://github.com/pytorch/vision", "sourceRevision": "v0.20.1", "license": "BSD-3-Clause"},
        "training": {"initialization": "random", "pretrainedWeightsUsed": False, "datasetId": dataset_id, "seed": int(os.environ.get("DATASET_SEED", "42"))},
        "datasetContentSha256": dataset_manifest["contentSha256"],
        "evaluation": metrics,
        "artifacts": {
            name: {
                "sha256": hashlib.sha256((artifacts / name).read_bytes()).hexdigest(),
                "bytes": (artifacts / name).stat().st_size,
            }
            for name in required
        },
        "commercialGate": {
            "datasetRightsVerified": True,
            "datasetApprovalGranted": True,
            "trainingCodeLicenseVerified": True,
            "dependencyLicensesVerified": False,
            "pretrainedWeightsUsed": False,
            "provenanceComplete": True,
            "releaseApproved": False,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    checksum_names = (*required, "model-manifest.json")
    checksum_lines = [
        f"{hashlib.sha256((artifacts / name).read_bytes()).hexdigest()}  {name}"
        for name in sorted(checksum_names)
    ]
    (artifacts / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n")
    print(json.dumps({"status": "CANDIDATE", "modelId": manifest["modelId"], "datasetId": dataset_id}))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "validate-input":
        validate_input(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "release":
        release(sys.argv[2])
    else:
        raise SystemExit("usage: python -m sportif_ml.provenance validate-input OBJECT_KEY | release DATASET_ID")