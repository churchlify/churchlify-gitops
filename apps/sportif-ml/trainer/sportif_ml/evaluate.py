import json
import sys
from pathlib import Path

from .dataset import dataset_root


def evaluate(dataset_id):
    root = dataset_root(dataset_id)
    model_path = root / "artifacts" / "model.pt"
    if not model_path.exists():
        raise SystemExit("model checkpoint is missing")
    test_images = list((root / "test" / "images").glob("**/*"))
    annotations = sum(1 for path in (root / "test" / "labels").glob("**/*.txt") if path.read_text().strip())
    result = {"datasetId": dataset_id, "split": "test", "unseenSourceVideos": True, "images": len(test_images), "annotations": annotations, "precision": None, "recall": None, "mAP50": None, "mAP50-95": None, "falsePositives": None, "falseNegatives": None, "status": "REQUIRES_RUNTIME_EVALUATOR"}
    (root / "artifacts" / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    evaluate(sys.argv[1])