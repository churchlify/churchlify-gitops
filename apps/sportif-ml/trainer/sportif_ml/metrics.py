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


def evaluate_dataset(model, dataset, device, score_threshold):
    predictions = []
    ground_truth = []
    model.eval()
    with torch.inference_mode():
        for image_index in range(len(dataset)):
            image, target = dataset[image_index]
            output = model([image.to(device)])[0]
            selected = (output["labels"] == 1) & (output["scores"] >= score_threshold)
            for score, box in zip(output["scores"][selected].cpu(), output["boxes"][selected].cpu()):
                predictions.append((float(score), image_index, box))
            ground_truth.append(target["boxes"])

    thresholds = [value / 100 for value in range(50, 100, 5)]
    evaluations = {
        threshold: average_precision(predictions, ground_truth, threshold)
        for threshold in thresholds
    }
    ap50, true_positives, false_positives, false_negatives = evaluations[0.5]
    precision = true_positives / max(true_positives + false_positives, 1)
    recall = true_positives / max(true_positives + false_negatives, 1)
    return {
        "images": len(dataset),
        "annotations": sum(len(boxes) for boxes in ground_truth),
        "scoreThreshold": score_threshold,
        "precision": precision,
        "recall": recall,
        "mAP50": ap50,
        "mAP50-95": float(np.mean([value[0] for value in evaluations.values()])),
        "falsePositives": false_positives,
        "falseNegatives": false_negatives,
    }