import json
import os
import sys
from pathlib import Path

import torch

from .dataset import dataset_root
from .metrics import average_precision, box_iou, evaluate_dataset
from .model import build_model
from .train import YoloDetectionDataset


QUALITY_THRESHOLDS = {
    "mAP50": ("EVALUATION_MIN_MAP50", 0.50),
    "mAP50-95": ("EVALUATION_MIN_MAP50_95", 0.20),
    "precision": ("EVALUATION_MIN_PRECISION", 0.60),
    "recall": ("EVALUATION_MIN_RECALL", 0.60),
}


def evaluate_quality_gate(metrics, environ=None):
    environ = os.environ if environ is None else environ
    thresholds = {
        metric: float(environ.get(variable, default))
        for metric, (variable, default) in QUALITY_THRESHOLDS.items()
    }
    failures = [
        {
            "metric": metric,
            "actual": float(metrics[metric]),
            "minimum": minimum,
        }
        for metric, minimum in thresholds.items()
        if float(metrics[metric]) < minimum
    ]
    return {
        "qualityGateStatus": "PASS" if not failures else "FAIL",
        "qualityThresholds": thresholds,
        "qualityGateFailures": failures,
    }


def persist_evaluation_result(path, result):
    Path(path).write_text(json.dumps(result, indent=2) + "\n")
    if result["qualityGateStatus"] != "PASS":
        failed = ", ".join(
            f'{item["metric"]}={item["actual"]:.6f} < {item["minimum"]:.6f}'
            for item in result["qualityGateFailures"]
        )
        raise SystemExit(f"model quality gate failed: {failed}")


def evaluate(dataset_id):
    root = dataset_root(dataset_id)
    model_path = root / "artifacts" / "model.pt"
    if not model_path.exists():
        raise SystemExit("model checkpoint is missing")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_model()
    model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
    model.to(device).eval()
    dataset = YoloDetectionDataset(root, "test")
    metadata_path = root / "artifacts" / "training-metadata.json"
    if not metadata_path.exists():
        raise SystemExit("training metadata is missing")
    training_metadata = json.loads(metadata_path.read_text())
    score_threshold = float(training_metadata["selectedOperatingThreshold"])
    result = {
        "datasetId": dataset_id,
        "split": "test",
        "unseenSourceVideos": True,
        "scoreThresholdSelection": "validation",
        **evaluate_dataset(model, dataset, device, score_threshold),
        "executionStatus": "PASS",
    }
    result.update(evaluate_quality_gate(result))
    print(json.dumps(result))
    persist_evaluation_result(root / "artifacts" / "metrics.json", result)


if __name__ == "__main__":
    evaluate(sys.argv[1])