import hashlib
import json
import os
import random
import sys
from pathlib import Path

from PIL import Image


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
SPLITS = ("train", "validation", "test")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dataset_root(dataset_id):
    return Path(os.environ.get("ML_WORK_DIR", "/work")) / dataset_id


def validate(dataset_id):
    root = dataset_root(dataset_id)
    errors = []
    image_count = 0
    annotation_count = 0
    videos = {split: set() for split in SPLITS}
    seen_hashes = {}

    for split in SPLITS:
        image_dir = root / split / "images"
        label_dir = root / split / "labels"
        for image_path in sorted(image_dir.glob("**/*")):
            if image_path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            image_count += 1
            relative = image_path.relative_to(image_dir)
            video_id = relative.parts[0] if len(relative.parts) > 1 else "unknown"
            videos[split].add(video_id)
            label_path = label_dir / relative.with_suffix(".txt")
            if not label_path.exists():
                errors.append(f"missing label: {label_path}")
                continue
            try:
                with Image.open(image_path) as image:
                    width, height = image.size
                image_hash = sha256_file(image_path)
                if image_hash in seen_hashes:
                    errors.append(f"duplicate frame: {image_path} and {seen_hashes[image_hash]}")
                seen_hashes[image_hash] = image_path
                for line_number, line in enumerate(label_path.read_text().splitlines(), 1):
                    fields = line.split()
                    if len(fields) != 5:
                        errors.append(f"malformed label: {label_path}:{line_number}")
                        continue
                    class_id, *box = fields
                    values = [float(value) for value in box]
                    if int(class_id) != 0 or any(value < 0 or value > 1 for value in values):
                        errors.append(f"invalid box: {label_path}:{line_number}")
                    if values[2] <= 0 or values[3] <= 0:
                        errors.append(f"empty box: {label_path}:{line_number}")
                    if values[2] < 1 / max(width, 1) or values[3] < 1 / max(height, 1):
                        errors.append(f"extreme annotation: {label_path}:{line_number}")
                    annotation_count += 1
            except (OSError, ValueError) as error:
                errors.append(f"invalid image or label {image_path}: {error}")

    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        overlap = videos[left] & videos[right]
        if overlap:
            errors.append(f"video leakage between {left} and {right}: {sorted(overlap)}")

    provenance = root / "provenance.json"
    if not provenance.exists():
        errors.append("missing source-video provenance")
    else:
        for item in json.loads(provenance.read_text()):
            if item.get("sourceRightsStatus") != "VERIFIED" or item.get("aiTrainingPermission") is not True:
                errors.append(f"rights metadata is not commercially eligible: {item.get('videoId')}")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "images": image_count,
        "annotations": annotation_count,
        "videos": len(set().union(*videos.values())),
        "leakageDetected": any("leakage" in error for error in errors),
        "invalidLabels": sum("label" in error or "box" in error for error in errors),
        "missingLabels": sum("missing label" in error for error in errors),
        "errors": errors,
    }
    (root / "dataset-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    if errors:
        raise SystemExit(f"dataset validation failed with {len(errors)} error(s)")
    return result


def prepare(dataset_id):
    root = dataset_root(dataset_id)
    source = root / "source-videos.json"
    if not source.exists():
        raise SystemExit(f"missing source manifest: {source}")
    videos = json.loads(source.read_text())
    if len(videos) < 3:
        raise SystemExit("at least three source videos are required for video-level train/validation/test")
    randomizer = random.Random(int(os.environ.get("DATASET_SEED", "42")))
    videos = sorted(videos, key=lambda item: item["videoId"])
    randomizer.shuffle(videos)
    counts = {"train": max(1, int(len(videos) * 0.70)), "validation": max(1, int(len(videos) * 0.15))}
    counts["test"] = len(videos) - counts["train"] - counts["validation"]
    if counts["test"] < 1:
        counts["test"] = 1
        counts["train"] -= 1
    offset = 0
    split_videos = {}
    for split in SPLITS:
        split_videos[split] = videos[offset : offset + counts[split]]
        offset += counts[split]
        (root / split / "images").mkdir(parents=True, exist_ok=True)
        (root / split / "labels").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schemaVersion": 1,
        "datasetId": dataset_id,
        "classes": [{"id": 0, "name": "ball"}],
        "sourceVideos": [item["videoId"] for item in videos],
        "trainVideos": [item["videoId"] for item in split_videos["train"]],
        "validationVideos": [item["videoId"] for item in split_videos["validation"]],
        "testVideos": [item["videoId"] for item in split_videos["test"]],
        "annotationFormat": "YOLO",
        "splitStrategy": "video-level",
        "randomSeed": int(os.environ.get("DATASET_SEED", "42")),
        "pretrainedWeightsUsed": False,
    }
    (root / "dataset-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    command, dataset_id = sys.argv[1:3]
    if command == "prepare":
        prepare(dataset_id)
    elif command == "validate":
        validate(dataset_id)
    else:
        raise SystemExit(f"unsupported command: {command}")