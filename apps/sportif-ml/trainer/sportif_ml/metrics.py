import numpy as np
import torch


def box_iou(boxes1, boxes2):
    if boxes1.numel() == 0 or boxes2.numel() == 0:
        return torch.zeros((len(boxes1), len(boxes2)))
    upper_left = torch.maximum(boxes1[:, None, :2], boxes2[None, :, :2])
    lower_right = torch.minimum(boxes1[:, None, 2:], boxes2[None, :, 2:])
    size = (lower_right - upper_left).clamp(min=0)
    intersection = size[:, :, 0] * size[:, :, 1]
    area1 = (boxes1[:, 2] - boxes1[:, 0]) * (boxes1[:, 3] - boxes1[:, 1])
    area2 = (boxes2[:, 2] - boxes2[:, 0]) * (boxes2[:, 3] - boxes2[:, 1])
    return intersection / (area1[:, None] + area2[None, :] - intersection).clamp(min=1e-9)


def average_precision(predictions, ground_truth, iou_threshold):
    total_ground_truth = sum(len(boxes) for boxes in ground_truth)
    matched = [set() for _ in ground_truth]
    ranked = sorted(predictions, key=lambda item: item[0], reverse=True)
    true_positives = []
    false_positives = []
    for _, image_index, predicted_box in ranked:
        boxes = ground_truth[image_index]
        if len(boxes) == 0:
            true_positives.append(0)
            false_positives.append(1)
            continue
        overlaps = box_iou(predicted_box.unsqueeze(0), boxes).squeeze(0)
        best_iou, best_index = overlaps.max(dim=0)
        if best_iou.item() >= iou_threshold and best_index.item() not in matched[image_index]:
            matched[image_index].add(best_index.item())
            true_positives.append(1)
            false_positives.append(0)
        else:
            true_positives.append(0)
            false_positives.append(1)
    if total_ground_truth == 0:
        return 0.0, 0, len(ranked), 0
    cumulative_tp = np.cumsum(true_positives)
    cumulative_fp = np.cumsum(false_positives)
    recall = cumulative_tp / total_ground_truth
    precision = cumulative_tp / np.maximum(cumulative_tp + cumulative_fp, 1)
    recall_points = np.concatenate(([0.0], recall, [1.0]))
    precision_points = np.concatenate(([0.0], precision, [0.0]))
    precision_points = np.maximum.accumulate(precision_points[::-1])[::-1]
    changes = np.where(recall_points[1:] != recall_points[:-1])[0]
    ap = np.sum((recall_points[changes + 1] - recall_points[changes]) * precision_points[changes + 1])
    true_positive_count = int(cumulative_tp[-1]) if len(cumulative_tp) else 0
    return float(ap), true_positive_count, len(ranked) - true_positive_count, total_ground_truth - true_positive_count


def collect_detections(model, dataset, device):
    outputs = []
    ground_truth = []
    model.eval()
    with torch.inference_mode():
        for image_index in range(len(dataset)):
            image, target = dataset[image_index]
            output = model([image.to(device)])[0]
            selected = output["labels"] == 1
            outputs.append({
                "scores": output["scores"][selected].cpu(),
                "boxes": output["boxes"][selected].cpu(),
            })
            ground_truth.append(target["boxes"])
    return outputs, ground_truth


def evaluate_detections(outputs, ground_truth, score_threshold):
    predictions = []
    detections_on_positive_frames = 0
    detections_on_negative_frames = 0
    for image_index, (output, truth) in enumerate(zip(outputs, ground_truth)):
        selected = output["scores"] >= score_threshold
        detection_count = int(selected.sum())
        if len(truth):
            detections_on_positive_frames += detection_count
        else:
            detections_on_negative_frames += detection_count
        for score, box in zip(output["scores"][selected], output["boxes"][selected]):
            predictions.append((float(score), image_index, box))

    thresholds = [value / 100 for value in range(50, 100, 5)]
    evaluations = {
        threshold: average_precision(predictions, ground_truth, threshold)
        for threshold in thresholds
    }
    ap50, true_positives, false_positives, false_negatives = evaluations[0.5]
    precision = true_positives / max(true_positives + false_positives, 1)
    recall = true_positives / max(true_positives + false_negatives, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    return {
        "images": len(ground_truth),
        "annotations": sum(len(boxes) for boxes in ground_truth),
        "scoreThreshold": score_threshold,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mAP50": ap50,
        "mAP50-95": float(np.mean([value[0] for value in evaluations.values()])),
        "falsePositives": false_positives,
        "falseNegatives": false_negatives,
        "detectionsOnPositiveFrames": detections_on_positive_frames,
        "detectionsOnNegativeFrames": detections_on_negative_frames,
    }


def evaluate_dataset(model, dataset, device, score_threshold):
    outputs, ground_truth = collect_detections(model, dataset, device)
    return evaluate_detections(outputs, ground_truth, score_threshold)


def per_source_metrics(outputs, ground_truth, dataset, score_threshold):
    grouped = {}
    for index in range(len(ground_truth)):
        grouped.setdefault(dataset.source_id(index), []).append(index)
    return {
        source: evaluate_detections(
            [outputs[index] for index in indices],
            [ground_truth[index] for index in indices],
            score_threshold,
        )
        for source, indices in sorted(grouped.items())
    }


def object_size_recall(outputs, ground_truth, score_threshold, iou_threshold=0.5):
    bands = {
        "tiny": {"annotations": 0, "truePositives": 0},
        "small": {"annotations": 0, "truePositives": 0},
        "larger": {"annotations": 0, "truePositives": 0},
    }
    for output, truth in zip(outputs, ground_truth):
        selected_boxes = output["boxes"][output["scores"] >= score_threshold]
        matched_predictions = set()
        if len(truth):
            areas = (truth[:, 2] - truth[:, 0]) * (truth[:, 3] - truth[:, 1])
            for truth_index in torch.argsort(areas).tolist():
                box = truth[truth_index]
                maximum_side = float(torch.max(box[2:] - box[:2]))
                band = "tiny" if maximum_side < 12 else "small" if maximum_side < 24 else "larger"
                bands[band]["annotations"] += 1
                if len(selected_boxes):
                    overlaps = box_iou(box.unsqueeze(0), selected_boxes).squeeze(0)
                    for prediction_index in torch.argsort(overlaps, descending=True).tolist():
                        if prediction_index not in matched_predictions and float(overlaps[prediction_index]) >= iou_threshold:
                            matched_predictions.add(prediction_index)
                            bands[band]["truePositives"] += 1
                            break
    for values in bands.values():
        values["falseNegatives"] = values["annotations"] - values["truePositives"]
        values["recall"] = values["truePositives"] / max(values["annotations"], 1)
    return bands


def calibrate_score_threshold(outputs, ground_truth, thresholds, minimum_recall):
    sweep = [
        evaluate_detections(outputs, ground_truth, threshold)
        for threshold in thresholds
    ]
    eligible = [metrics for metrics in sweep if metrics["recall"] >= minimum_recall]
    candidates = eligible or sweep
    selected = max(
        candidates,
        key=lambda metrics: (
            metrics["f1"],
            metrics["precision"],
            metrics["recall"],
            metrics["scoreThreshold"],
        ),
    )
    selected["minimumRecallSatisfied"] = bool(eligible)
    selected["calibrationMinimumRecall"] = minimum_recall
    return selected.copy(), sweep