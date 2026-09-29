import json
import os
import random
import sys
from collections import OrderedDict

import numpy as np
import torch

from .dataset import dataset_root
from .metrics import box_iou
from .model import (
    build_model,
    detection_loss_weights,
    initialization_metadata,
    weighted_detection_loss,
)
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


def object_size_diagnostics(target, transformed_target, difficult_side=8.0, core_side=12.0):
    if not 0 < difficult_side < core_side:
        raise ValueError("object-size bands must satisfy 0 < difficult < core")
    boxes = target["boxes"]
    transformed_boxes = transformed_target["boxes"]
    if not len(boxes):
        return None
    sizes = boxes[:, 2:] - boxes[:, :2]
    transformed_sizes = transformed_boxes[:, 2:] - transformed_boxes[:, :2]
    minimum_side = float(sizes.min())
    maximum_side = float(sizes.max())
    transformed_minimum_side = float(transformed_sizes.min())
    transformed_maximum_side = float(transformed_sizes.max())
    if minimum_side < difficult_side:
        band = "difficult"
    elif minimum_side < core_side:
        band = "tiny"
    else:
        band = "core"
    return {
        "objectSizeBand": band,
        "minimumBoxSidePixels": minimum_side,
        "maximumBoxSidePixels": maximum_side,
        "transformedMinimumBoxSidePixels": transformed_minimum_side,
        "transformedMaximumBoxSidePixels": transformed_maximum_side,
    }


def best_overlap(boxes, scores, targets):
    overlaps = box_iou(boxes, targets)
    if not overlaps.numel():
        return 0.0, 0.0
    flat_index = int(overlaps.argmax())
    prediction_index = flat_index // overlaps.shape[1]
    return float(overlaps.flatten()[flat_index]), float(scores[prediction_index])


def diagnostic_inference(model, image, target):
    original_size = tuple(image.shape[-2:])
    images, transformed_targets = model.transform([image], [target])
    features = model.backbone(images.tensors)
    if isinstance(features, torch.Tensor):
        features = OrderedDict([("0", features)])
    proposals, _ = model.rpn(images, features, None)
    detections, _ = model.roi_heads(features, proposals, images.image_sizes, None)
    detections = model.transform.postprocess(detections, images.image_sizes, [original_size])
    return detections[0], proposals[0], transformed_targets[0], images.image_sizes[0]


def positive_band_summary(outcomes, minimum_iou, minimum_score):
    summary = {}
    for band in ("difficult", "tiny", "core"):
        selected = [
            outcome for outcome in outcomes
            if outcome.get("labelStatus") == "positive" and outcome.get("objectSizeBand") == band
        ]
        passes = sum(
            outcome["bestIoU"] >= minimum_iou and outcome["scoreAtBestIoU"] >= minimum_score
            for outcome in selected
        )
        summary[band] = {
            "frames": len(selected),
            "passes": passes,
            "passRate": passes / len(selected) if selected else None,
        }
    return summary


def diagnostic_score_sweep(outcomes, minimum_iou, thresholds):
    positive_outcomes = [item for item in outcomes if item["labelStatus"] == "positive"]
    negative_outcomes = [item for item in outcomes if item["labelStatus"] == "negative"]
    sweep = []
    for threshold in thresholds:
        positive_passes = sum(
            item["bestIoUBeforeGateThreshold"] >= minimum_iou
            and item["scoreAtBestIoUBeforeGateThreshold"] >= threshold
            for item in positive_outcomes
        )
        negative_detections = sum(
            item["maximumBallScoreBeforeGateThreshold"] >= threshold
            for item in negative_outcomes
        )
        sweep.append({
            "minimumScore": threshold,
            "positivePasses": positive_passes,
            "positiveFrames": len(positive_outcomes),
            "positivePassRate": positive_passes / len(positive_outcomes),
            "negativeDetections": negative_detections,
            "negativeFrames": len(negative_outcomes),
            "negativeDetectionRate": negative_detections / len(negative_outcomes),
        })
    return sweep


def persist_diagnostic_result(root, result):
    output = root / "artifacts" / "trainability-diagnostic.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if result["diagnosticOnly"]:
        marker = {
            "publishable": False,
            "exportEligible": False,
            "promotionEligible": False,
            "reason": "pretrained trainability initialization experiment",
        }
        (output.parent / "NON_PUBLISHABLE.json").write_text(
            json.dumps(marker, indent=2, sort_keys=True) + "\n"
        )
    return output


def verify(dataset_id):
    seed = int(os.environ.get("DATASET_SEED", "42"))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for the trainability smoke test")
    device = torch.device("cuda")
    root = dataset_root(dataset_id)
    dataset = YoloDetectionDataset(root, "train")
    image_count = int(os.environ.get("TRAINABILITY_IMAGE_COUNT", "40"))
    steps = int(os.environ.get("TRAINABILITY_STEPS", "600"))
    minimum_iou = float(os.environ.get("TRAINABILITY_MIN_IOU", "0.75"))
    minimum_score = float(os.environ.get("TRAINABILITY_MIN_SCORE", "0.90"))
    minimum_positive_pass_rate = float(os.environ.get("TRAINABILITY_MIN_POSITIVE_PASS_RATE", "0.90"))
    maximum_negative_detection_rate = float(os.environ.get("TRAINABILITY_MAX_NEGATIVE_DETECTION_RATE", "0.10"))
    evaluation_score_threshold = float(os.environ.get("TRAINABILITY_EVALUATION_SCORE_THRESHOLD", "0.50"))
    difficult_side = float(os.environ.get("TRAINABILITY_DIFFICULT_MIN_SIDE", "8.0"))
    core_side = float(os.environ.get("TRAINABILITY_CORE_MIN_SIDE", "12.0"))
    batch_size = int(os.environ.get("TRAINABILITY_BATCH_SIZE", "2"))
    loss_weights = detection_loss_weights(prefix="TRAINABILITY")
    diagnostic_only = os.environ.get("TRAINABILITY_DIAGNOSTIC_ONLY", "false").lower() == "true"
    if os.environ.get("TRAINING_INITIALIZATION", "random") != "random" and not diagnostic_only:
        raise SystemExit("pretrained trainability runs must be explicitly diagnostic-only")
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
        loss = weighted_detection_loss(losses, loss_weights)
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
            device_image = image.to(device)
            device_target = {key: value.to(device) for key, value in target.items()}
            output, proposals, transformed_target, transformed_size = diagnostic_inference(
                model, device_image, device_target
            )
            selected_predictions = output["labels"] == 1
            all_boxes = output["boxes"][selected_predictions].cpu()
            all_scores = output["scores"][selected_predictions].cpu()
            boxes = all_boxes
            scores = all_scores
            confident = scores >= evaluation_score_threshold
            boxes = boxes[confident]
            scores = scores[confident]
            relative_path = str(dataset.images[index].relative_to(dataset.image_dir))
            common = {
                "datasetIndex": index,
                "imagePath": relative_path,
                "source": dataset.source_id(index),
                "originalImageSize": list(image.shape[-2:]),
                "transformedImageSize": list(transformed_size),
                "ballDetectionsBeforeGateThreshold": len(all_boxes),
                "maximumBallScoreBeforeGateThreshold": float(all_scores.max()) if len(all_scores) else 0.0,
                "ballDetectionsAtGateThreshold": len(boxes),
                "rpnProposals": len(proposals),
            }
            if not len(target["boxes"]):
                outcomes.append({
                    **common,
                    "labelStatus": "negative",
                    "detections": len(boxes),
                })
                continue
            best_iou, score = best_overlap(boxes, scores, target["boxes"])
            pre_threshold_iou, pre_threshold_score = best_overlap(
                all_boxes, all_scores, target["boxes"]
            )
            proposal_overlaps = box_iou(proposals.cpu(), transformed_target["boxes"].cpu())
            best_proposal_iou = float(proposal_overlaps.max()) if proposal_overlaps.numel() else 0.0
            outcomes.append({
                **common,
                "labelStatus": "positive",
                "bestIoU": best_iou,
                "scoreAtBestIoU": score,
                "bestIoUBeforeGateThreshold": pre_threshold_iou,
                "scoreAtBestIoUBeforeGateThreshold": pre_threshold_score,
                "bestRpnProposalIoU": best_proposal_iou,
                **object_size_diagnostics(
                    target, {key: value.cpu() for key, value in transformed_target.items()},
                    difficult_side, core_side,
                ),
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
        "scoreGateSweep": diagnostic_score_sweep(
            outcomes, minimum_iou, (0.80, 0.825, 0.85, 0.875, 0.90)
        ),
        "lossWeights": loss_weights,
        "diagnosticOnly": diagnostic_only,
        "publishable": False if diagnostic_only else None,
        "initialization": initialization_metadata(model),
        "objectSizeBands": {
            "difficult": f"minimum side < {difficult_side:g} raw pixels",
            "tiny": f"{difficult_side:g} <= minimum side < {core_side:g} raw pixels",
            "core": f"minimum side >= {core_side:g} raw pixels",
        },
        "sources": sorted({dataset.source_id(index) for index in selected}),
        "positiveFrames": len(positive_outcomes),
        "negativeFrames": len(negative_outcomes),
        "positivePassRate": positive_pass_rate,
        "negativeDetectionRate": negative_detection_rate,
        "positiveFramesByObjectSize": positive_band_summary(
            outcomes, minimum_iou, minimum_score
        ),
        "history": history,
        "outcomes": outcomes,
    }
    persist_diagnostic_result(root, result)
    print(json.dumps(result), flush=True)
    if failures:
        raise SystemExit(f"mixed-source overfit gate failed: {failures}")


if __name__ == "__main__":
    verify(sys.argv[1])