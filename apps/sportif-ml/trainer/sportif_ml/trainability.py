import json
import os
import random
import sys

import numpy as np
import torch

from .dataset import dataset_root
from .metrics import box_iou
from .model import build_model
from .train import YoloDetectionDataset


def verify(dataset_id):
    seed = int(os.environ.get("DATASET_SEED", "42"))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for the trainability smoke test")
    device = torch.device("cuda")
    dataset = YoloDetectionDataset(dataset_root(dataset_id), "train")
    positive_indices = [index for index in range(len(dataset)) if dataset.has_annotations(index)]
    image_count = int(os.environ.get("TRAINABILITY_IMAGE_COUNT", "4"))
    steps = int(os.environ.get("TRAINABILITY_STEPS", "150"))
    minimum_iou = float(os.environ.get("TRAINABILITY_MIN_IOU", "0.75"))
    minimum_score = float(os.environ.get("TRAINABILITY_MIN_SCORE", "0.90"))
    if len(positive_indices) < image_count:
        raise SystemExit(f"trainability smoke test requires at least {image_count} positive frames")
    selected = positive_indices[:image_count]
    model = build_model().to(device).train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(os.environ.get("TRAINABILITY_LEARNING_RATE", "0.0005")))
    history = []
    batch = [dataset[index] for index in selected]
    images = [image.to(device) for image, _ in batch]
    targets = [{key: value.to(device) for key, value in target.items()} for _, target in batch]
    for step in range(steps):
        losses = model(images, targets)
        loss = sum(losses.values())
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if step in {0, steps - 1}:
            history.append({
                "step": step + 1,
                "total": float(loss.detach()),
                "components": {name: float(value.detach()) for name, value in losses.items()},
            })

    model.eval()
    outcomes = []
    with torch.inference_mode():
        for image, target in batch:
            output = model([image.to(device)])[0]
            selected_predictions = output["labels"] == 1
            boxes = output["boxes"][selected_predictions].cpu()
            scores = output["scores"][selected_predictions].cpu()
            overlaps = box_iou(boxes, target["boxes"])
            if overlaps.numel():
                flat_index = int(overlaps.argmax())
                prediction_index = flat_index // overlaps.shape[1]
                best_iou = float(overlaps.flatten()[flat_index])
                score = float(scores[prediction_index])
            else:
                best_iou = 0.0
                score = 0.0
            outcomes.append({"bestIoU": best_iou, "scoreAtBestIoU": score})
    failures = [
        outcome for outcome in outcomes
        if outcome["bestIoU"] < minimum_iou or outcome["scoreAtBestIoU"] < minimum_score
    ]
    result = {
        "status": "PASS" if not failures else "FAIL",
        "datasetId": dataset_id,
        "images": image_count,
        "steps": steps,
        "minimumIoU": minimum_iou,
        "minimumScore": minimum_score,
        "history": history,
        "outcomes": outcomes,
    }
    print(json.dumps(result), flush=True)
    if failures:
        raise SystemExit(f"trainability smoke test failed for {len(failures)} of {image_count} images")


if __name__ == "__main__":
    verify(sys.argv[1])