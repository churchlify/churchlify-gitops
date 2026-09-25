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


def select_mixed_source_indices(dataset, image_count, seed):
    if image_count < 2:
        raise ValueError("mixed-source overfit suite requires at least two frames")
    by_source = {}
    for index in range(len(dataset)):
        by_source.setdefault(dataset.source_id(index), {True: [], False: []})[
            dataset.has_annotations(index)
        ].append(index)
    eligible_sources = sorted(
        source for source, pools in by_source.items() if pools[True] or pools[False]
    )
    if len(eligible_sources) < 2:
        raise SystemExit("mixed-source overfit suite requires at least two training sources")
    randomizer = random.Random(seed)
    for pools in by_source.values():
        randomizer.shuffle(pools[True])
        randomizer.shuffle(pools[False])
    selected = []
    used = set()
    label_cycle = (True, False)
    while len(selected) < image_count:
        added = False
        for label_status in label_cycle:
            for source in eligible_sources:
                candidates = by_source[source][label_status]
                while candidates and candidates[0] in used:
                    candidates.pop(0)
                if candidates and len(selected) < image_count:
                    index = candidates.pop(0)
                    selected.append(index)
                    used.add(index)
                    added = True
        if not added:
            break
    if len(selected) < image_count:
        raise SystemExit(f"mixed-source overfit suite requires {image_count} available frames")
    if not any(dataset.has_annotations(index) for index in selected) or all(
        dataset.has_annotations(index) for index in selected
    ):
        raise SystemExit("mixed-source overfit suite requires positive and negative frames")
    return selected


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
    image_count = int(os.environ.get("TRAINABILITY_IMAGE_COUNT", "40"))
    steps = int(os.environ.get("TRAINABILITY_STEPS", "600"))
    minimum_iou = float(os.environ.get("TRAINABILITY_MIN_IOU", "0.75"))
    minimum_score = float(os.environ.get("TRAINABILITY_MIN_SCORE", "0.90"))
    minimum_positive_pass_rate = float(os.environ.get("TRAINABILITY_MIN_POSITIVE_PASS_RATE", "0.90"))
    maximum_negative_detection_rate = float(os.environ.get("TRAINABILITY_MAX_NEGATIVE_DETECTION_RATE", "0.10"))
    evaluation_score_threshold = float(os.environ.get("TRAINABILITY_EVALUATION_SCORE_THRESHOLD", "0.50"))
    batch_size = int(os.environ.get("TRAINABILITY_BATCH_SIZE", "2"))
    selected = select_mixed_source_indices(dataset, image_count, seed)
    model = build_model().to(device).train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(os.environ.get("TRAINABILITY_LEARNING_RATE", "0.0005")))
    history = []
    suite = [dataset[index] for index in selected]
    for step in range(steps):
        offset = (step * batch_size) % len(suite)
        batch = [suite[(offset + index) % len(suite)] for index in range(batch_size)]
        images = [image.to(device) for image, _ in batch]
        targets = [{key: value.to(device) for key, value in target.items()} for _, target in batch]
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
        for index, (image, target) in zip(selected, suite):
            output = model([image.to(device)])[0]
            selected_predictions = output["labels"] == 1
            boxes = output["boxes"][selected_predictions].cpu()
            scores = output["scores"][selected_predictions].cpu()
            confident = scores >= evaluation_score_threshold
            boxes = boxes[confident]
            scores = scores[confident]
            if not len(target["boxes"]):
                outcomes.append({
                    "source": dataset.source_id(index),
                    "labelStatus": "negative",
                    "detections": len(boxes),
                })
                continue
            overlaps = box_iou(boxes, target["boxes"])
            if overlaps.numel():
                flat_index = int(overlaps.argmax())
                prediction_index = flat_index // overlaps.shape[1]
                best_iou = float(overlaps.flatten()[flat_index])
                score = float(scores[prediction_index])
            else:
                best_iou = 0.0
                score = 0.0
            outcomes.append({
                "source": dataset.source_id(index),
                "labelStatus": "positive",
                "bestIoU": best_iou,
                "scoreAtBestIoU": score,
            })
    positive_outcomes = [outcome for outcome in outcomes if outcome["labelStatus"] == "positive"]
    negative_outcomes = [outcome for outcome in outcomes if outcome["labelStatus"] == "negative"]
    positive_passes = sum(
        outcome["bestIoU"] >= minimum_iou and outcome["scoreAtBestIoU"] >= minimum_score
        for outcome in positive_outcomes
    )
    negative_detections = sum(outcome["detections"] > 0 for outcome in negative_outcomes)
    positive_pass_rate = positive_passes / len(positive_outcomes)
    negative_detection_rate = negative_detections / len(negative_outcomes)
    failures = []
    if positive_pass_rate < minimum_positive_pass_rate:
        failures.append("positive-pass-rate")
    if negative_detection_rate > maximum_negative_detection_rate:
        failures.append("negative-detection-rate")
    result = {
        "status": "PASS" if not failures else "FAIL",
        "datasetId": dataset_id,
        "images": image_count,
        "steps": steps,
        "minimumIoU": minimum_iou,
        "minimumScore": minimum_score,
        "minimumPositivePassRate": minimum_positive_pass_rate,
        "maximumNegativeDetectionRate": maximum_negative_detection_rate,
        "evaluationScoreThreshold": evaluation_score_threshold,
        "sources": sorted({dataset.source_id(index) for index in selected}),
        "positiveFrames": len(positive_outcomes),
        "negativeFrames": len(negative_outcomes),
        "positivePassRate": positive_pass_rate,
        "negativeDetectionRate": negative_detection_rate,
        "history": history,
        "outcomes": outcomes,
    }
    print(json.dumps(result), flush=True)
    if failures:
        raise SystemExit(f"mixed-source overfit gate failed: {failures}")


if __name__ == "__main__":
    verify(sys.argv[1])