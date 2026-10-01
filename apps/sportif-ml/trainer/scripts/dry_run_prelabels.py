import hashlib
import io
import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import torch
from PIL import Image
from sportif_ml.model import build_model
from sportif_ml.prelabel import map_crop_box_to_frame, normalized_zone, zone_aware_nms


def integer_list(name, default):
    value = os.environ.get(name, default)
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


TASK_IDS = integer_list("PRELABEL_TASK_IDS", "2,3,5,6,7")
HELD_OUT_TASK_IDS = integer_list("PRELABEL_HELD_OUT_TASK_IDS", "1")
LABEL_ID = 1
SCORE_FLOOR = float(os.environ.get("PRELABEL_SCORE_FLOOR", "0.05"))
NMS_IOU = float(os.environ.get("PRELABEL_NMS_IOU", "0.40"))
SMALL_ZONE_NMS_IOU = float(os.environ.get("PRELABEL_SMALL_ZONE_NMS_IOU", "0.70"))
DETECTOR_NMS_IOU = float(os.environ.get("PRELABEL_DETECTOR_NMS_IOU", "0.85"))
MAXIMUM_SMALL_SIDE_FRACTION = float(
    os.environ.get("PRELABEL_MAXIMUM_SMALL_SIDE_FRACTION", "0.04")
)
# Overlapping upper-field/player-band crops apply to every selected development
# task by default. Task-specific keys can override the wildcard configuration.
DEFAULT_PLAYER_BENCH_ZONES = {
    "*": [[0.0, 0.0, 0.4, 0.65], [0.3, 0.0, 0.7, 0.65], [0.6, 0.0, 1.0, 0.65]],
}
PLAYER_BENCH_ZONES = json.loads(
    os.environ.get("PRELABEL_PLAYER_BENCH_ZONES", json.dumps(DEFAULT_PLAYER_BENCH_ZONES))
)
HUMAN_IOU = 0.0
CHECKPOINT = Path(os.environ.get(
    "PRELABEL_CHECKPOINT",
    "/work/ed2c491b-5b21-4529-a7b7-ec016e82e7b9/"
    "sportif-ball-v004/artifacts/diagnostic-checkpoints/epoch-0015.pt",
))
OUTPUT = Path(os.environ.get(
    "PRELABEL_OUTPUT",
    "/work/prelabel-revision/epoch-0015-dry-run.json",
))
TRAINER_IMAGE = os.environ.get(
    "PRELABEL_TRAINER_IMAGE",
    "ghcr.io/bjelugbo/sportif-ball-trainer@sha256:"
    "87bc1b8d51584e465920cda193c078b32e6ecf1d1961453d1b787aac83338317",
)


def api_request(path, *, method="GET", body=None):
    base = os.environ["CVAT_API_URL"].rstrip("/")
    token = os.environ["CVAT_API_TOKEN"]
    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/vnd.cvat+json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = Request(base + path, data=data, headers=headers, method=method)
    with urlopen(request, timeout=120) as response:
        payload = response.read()
        content_type = response.headers.get("Content-Type", "")
    if "json" in content_type:
        return json.loads(payload)
    return payload


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def box_iou(left, right):
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def human_boxes_by_frame(annotations):
    result = defaultdict(list)
    for shape in annotations.get("shapes", []):
        if (
            shape.get("label_id") == LABEL_ID
            and shape.get("type") == "rectangle"
            and not shape.get("outside", False)
        ):
            result[int(shape["frame"])].append([float(value) for value in shape["points"]])
    for track in annotations.get("tracks", []):
        if track.get("label_id") != LABEL_ID:
            continue
        for shape in track.get("shapes", []):
            if shape.get("type") == "rectangle" and not shape.get("outside", False):
                result[int(shape["frame"])].append([float(value) for value in shape["points"]])
    return result


def task_zones(task_id):
    zones = PLAYER_BENCH_ZONES.get(str(task_id), PLAYER_BENCH_ZONES.get("*", []))
    return tuple(normalized_zone(zone) for zone in zones)


def zone_crop(zone, width, height):
    x1, y1, x2, y2 = zone
    return (
        math.floor(x1 * width),
        math.floor(y1 * height),
        math.ceil(x2 * width),
        math.ceil(y2 * height),
    )


def prediction_candidates(model, tensor, device, width, height, zones):
    predictions = [(model([tensor.to(device)])[0], None)]
    for zone in zones:
        crop = zone_crop(zone, width, height)
        x1, y1, x2, y2 = crop
        if x2 > x1 and y2 > y1:
            predictions.append((model([tensor[:, y1:y2, x1:x2].to(device)])[0], crop))
    candidates = []
    for prediction, crop in predictions:
        boxes = prediction["boxes"].detach().cpu()
        scores = prediction["scores"].detach().cpu()
        labels = prediction["labels"].detach().cpu()
        keep = (labels == 1) & (scores >= SCORE_FLOOR)
        for box_tensor, score_tensor in zip(boxes[keep], scores[keep]):
            box = tuple(float(value) for value in box_tensor)
            if crop is not None:
                box = map_crop_box_to_frame(box, crop)
            candidates.append((box, float(score_tensor), crop is not None))
    return candidates


def tensor_from_jpeg(payload):
    image = Image.open(io.BytesIO(payload)).convert("RGB")
    array = np.array(image, copy=True)
    return image.size, torch.from_numpy(array).permute(2, 0, 1).float().div_(255)


def quantiles(values):
    if not values:
        return {}
    ordered = sorted(values)
    def at(fraction):
        return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]
    return {
        "min": ordered[0],
        "p25": at(0.25),
        "median": at(0.50),
        "p75": at(0.75),
        "p90": at(0.90),
        "p95": at(0.95),
        "max": ordered[-1],
        "mean": statistics.fmean(ordered),
    }


def main():
    held_out_overlap = set(TASK_IDS) & set(HELD_OUT_TASK_IDS)
    if held_out_overlap:
        raise RuntimeError(
            f"held-out tasks are present in inference task list: {sorted(held_out_overlap)}"
        )
    if not CHECKPOINT.is_file():
        raise RuntimeError(f"checkpoint missing: {CHECKPOINT}")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")

    device = torch.device("cuda")
    model = build_model()
    model.load_state_dict(torch.load(CHECKPOINT, map_location="cpu", weights_only=True))
    model.to(device).eval()
    model.roi_heads.nms_thresh = DETECTOR_NMS_IOU

    report = {
        "schemaVersion": 1,
        "mode": "dry-run",
        "checkpoint": str(CHECKPOINT),
        "checkpointSha256": sha256(CHECKPOINT),
        "image": TRAINER_IMAGE,
        "device": torch.cuda.get_device_name(0),
        "taskIds": list(TASK_IDS),
        "heldOutTaskIds": list(HELD_OUT_TASK_IDS),
        "scoreFloor": SCORE_FLOOR,
        "predictionNmsIou": NMS_IOU,
        "smallPlayerBenchNmsIou": SMALL_ZONE_NMS_IOU,
        "detectorNmsIou": DETECTOR_NMS_IOU,
        "maximumSmallSideFraction": MAXIMUM_SMALL_SIDE_FRACTION,
        "playerBenchZones": PLAYER_BENCH_ZONES,
        "humanSuppressionIou": HUMAN_IOU,
        "tasks": {},
    }
    started = time.time()

    with torch.inference_mode():
        for task_id in TASK_IDS:
            task = api_request(f"/api/tasks/{task_id}")
            metadata = api_request(f"/api/tasks/{task_id}/data/meta")
            annotations = api_request(f"/api/tasks/{task_id}/annotations")
            existing = human_boxes_by_frame(annotations)
            zones = task_zones(task_id)
            frames = []
            proposed_scores = []
            suppressed_human = 0
            suppressed_nms = 0
            raw_above_floor = 0

            for frame_number, frame_meta in enumerate(metadata["frames"]):
                payload = api_request(
                    f"/api/tasks/{task_id}/data?type=frame&number={frame_number}&quality=original"
                )
                (width, height), tensor = tensor_from_jpeg(payload)
                if width != frame_meta["width"] or height != frame_meta["height"]:
                    raise RuntimeError(f"frame dimension mismatch: task={task_id} frame={frame_number}")
                candidates = prediction_candidates(
                    model, tensor, device, width, height, zones
                )
                boxes = [candidate[0] for candidate in candidates]
                scores = [candidate[1] for candidate in candidates]
                raw_above_floor += len(scores)
                kept_indices = zone_aware_nms(
                    boxes,
                    scores,
                    width,
                    height,
                    zones=zones,
                    default_iou=NMS_IOU,
                    small_zone_iou=SMALL_ZONE_NMS_IOU,
                    maximum_small_side_fraction=MAXIMUM_SMALL_SIDE_FRACTION,
                )
                suppressed_nms += len(boxes) - len(kept_indices)

                proposals = []
                for index in kept_indices:
                    box_tensor, score_tensor, from_zone_crop = candidates[index]
                    x1, y1, x2, y2 = box_tensor
                    box = [
                        min(max(x1, 0.0), float(width)),
                        min(max(y1, 0.0), float(height)),
                        min(max(x2, 0.0), float(width)),
                        min(max(y2, 0.0), float(height)),
                    ]
                    if not all(math.isfinite(value) for value in box) or box[2] <= box[0] or box[3] <= box[1]:
                        continue
                    duplicate = any(
                        box_iou(box, human) > HUMAN_IOU
                        for human in existing.get(frame_number, [])
                    )
                    if duplicate:
                        suppressed_human += 1
                        continue
                    score = float(score_tensor)
                    proposed_scores.append(score)
                    proposals.append({
                        "score": score,
                        "points": box,
                        "fromPlayerBenchCrop": from_zone_crop,
                    })

                frames.append({
                    "frame": frame_number,
                    "name": frame_meta["name"],
                    "width": width,
                    "height": height,
                    "existingBoxes": len(existing.get(frame_number, [])),
                    "proposals": proposals,
                })
                if (frame_number + 1) % 25 == 0 or frame_number + 1 == metadata["size"]:
                    print(
                        json.dumps({
                            "task": task_id,
                            "frame": frame_number + 1,
                            "totalFrames": metadata["size"],
                            "proposals": len(proposed_scores),
                        }),
                        flush=True,
                    )

            task_report = {
                "name": task["name"],
                "status": task["status"],
                "frameCount": metadata["size"],
                "existingShapeCount": len(annotations.get("shapes", [])),
                "existingTrackCount": len(annotations.get("tracks", [])),
                "existingVisibleBoxes": sum(len(value) for value in existing.values()),
                "rawPredictionsAboveFloor": raw_above_floor,
                "suppressedByPredictionNms": suppressed_nms,
                "suppressedAgainstExisting": suppressed_human,
                "proposedCount": len(proposed_scores),
                "proposedFrames": sum(bool(frame["proposals"]) for frame in frames),
                "scoreCounts": {
                    str(threshold): sum(score >= threshold for score in proposed_scores)
                    for threshold in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90)
                },
                "proposalsPerFrame": dict(sorted(Counter(
                    len(frame["proposals"]) for frame in frames
                ).items())),
                "scoreQuantiles": quantiles(proposed_scores),
                "frames": frames,
            }
            report["tasks"][str(task_id)] = task_report
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT.write_text(json.dumps(report, indent=2) + "\n")

    report["elapsedSeconds"] = time.time() - started
    report["totalFrames"] = sum(item["frameCount"] for item in report["tasks"].values())
    report["totalProposals"] = sum(item["proposedCount"] for item in report["tasks"].values())
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "output": str(OUTPUT),
        "totalFrames": report["totalFrames"],
        "totalProposals": report["totalProposals"],
        "elapsedSeconds": report["elapsedSeconds"],
    }))


if __name__ == "__main__":
    main()
