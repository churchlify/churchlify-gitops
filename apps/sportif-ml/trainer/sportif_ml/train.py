import json
import os
import random
import shutil
import sys
from math import ceil, cos, pi
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageEnhance
from torch.utils.data import DataLoader, Dataset, Sampler

from .dataset import dataset_root, validate
from .metrics import (
    calibrate_score_threshold,
    collect_detections,
    object_size_recall,
    per_source_metrics,
)
from .model import (
    build_model,
    detection_loss_weights,
    initialization_metadata,
    weighted_detection_loss,
)


class YoloDetectionDataset(Dataset):
    def __init__(self, root, split, augment=False, seed=42, augmentation=None):
        self.image_dir = root / split / "images"
        self.label_dir = root / split / "labels"
        self.images = sorted(path for path in self.image_dir.glob("**/*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"})
        self.augment = augment
        self.seed = seed
        self.epoch = 0
        self.augmentation = augmentation or {}
        self.augmentation_scale_records = []

    def set_epoch(self, epoch):
        self.epoch = epoch

    def source_id(self, index):
        relative = self.images[index].relative_to(self.image_dir)
        return relative.parts[0] if len(relative.parts) > 1 else "unknown"

    def label_path(self, index):
        image_path = self.images[index]
        return self.label_dir / image_path.relative_to(self.image_dir).with_suffix(".txt")

    def has_annotations(self, index):
        return bool(self.label_path(index).read_text().strip())

    def has_tiny_annotations(self, index, maximum_side=12.0):
        image_path = self.images[index]
        with Image.open(image_path) as image:
            width, height = image.size
        for line in self.label_path(index).read_text().splitlines():
            _, _, _, box_width, box_height = map(float, line.split())
            if max(box_width * width, box_height * height) < maximum_side:
                return True
        return False

    def __len__(self):
        return len(self.images)

    def __getitem__(self, index):
        replay_slot = 0
        if isinstance(index, tuple):
            index, replay_slot = index
        image_path = self.images[index]
        image = Image.open(image_path).convert("RGB")
        width, height = image.size
        label_path = self.label_path(index)
        boxes = []
        labels = []
        for line in label_path.read_text().splitlines():
            class_id, center_x, center_y, box_width, box_height = map(float, line.split())
            boxes.append([(center_x - box_width / 2) * width, (center_y - box_height / 2) * height, (center_x + box_width / 2) * width, (center_y + box_height / 2) * height])
            labels.append(int(class_id) + 1)
        box_tensor = torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4)
        if self.augment:
            image, box_tensor, scale_record = augment_detection(
                image,
                box_tensor,
                random.Random(
                    self.seed
                    + self.epoch * max(len(self), 1)
                    + index
                    + replay_slot * max(len(self), 1) * 1000003
                ),
                self.augmentation,
                return_metadata=True,
            )
            self.augmentation_scale_records.append(scale_record)
        tensor = torch.from_numpy(np.array(image, copy=True)).permute(2, 0, 1).float() / 255
        target = {
            "boxes": box_tensor,
            "labels": torch.tensor(labels, dtype=torch.int64),
            "image_id": torch.tensor([index]),
        }
        return tensor, target


def augment_detection(image, boxes, randomizer, config, return_metadata=False):
    width, height = image.size
    requested_scale = randomizer.uniform(
        float(config.get("scaleMin", 1.0)),
        float(config.get("scaleMax", 1.0)),
    )
    scale = requested_scale
    minimum_object_side = float(config.get("minimumObjectSide", 0.0))
    if len(boxes) and minimum_object_side > 0 and scale < 1.0:
        box_sizes = boxes[:, 2:] - boxes[:, :2]
        smallest_side = float(box_sizes.min())
        scale = min(1.0, max(scale, minimum_object_side / max(smallest_side, 1e-12)))
    if scale < 1.0:
        resized_width = max(1, round(width * scale))
        resized_height = max(1, round(height * scale))
        offset_x = randomizer.randint(0, width - resized_width)
        offset_y = randomizer.randint(0, height - resized_height)
        resized = image.resize((resized_width, resized_height), Image.Resampling.BILINEAR)
        canvas_value = int(config.get("canvasValue", 114))
        canvas = Image.new("RGB", (width, height), (canvas_value,) * 3)
        canvas.paste(resized, (offset_x, offset_y))
        image = canvas
        if len(boxes):
            scale_x = resized_width / width
            scale_y = resized_height / height
            boxes = boxes * torch.tensor([scale_x, scale_y, scale_x, scale_y])
            boxes = boxes + torch.tensor([offset_x, offset_y, offset_x, offset_y])
    elif scale > 1.0:
        crop_width = max(1, round(width / scale))
        crop_height = max(1, round(height / scale))
        if len(boxes):
            extent_width = float(boxes[:, 2].max() - boxes[:, 0].min())
            extent_height = float(boxes[:, 3].max() - boxes[:, 1].min())
            maximum_safe_scale = min(
                width / max(extent_width, 1.0),
                height / max(extent_height, 1.0),
            )
            scale = min(scale, maximum_safe_scale)
            crop_width = max(1, min(width, round(width / scale)))
            crop_height = max(1, min(height, round(height / scale)))
            minimum_x = max(0, ceil(float(boxes[:, 2].max()) - crop_width))
            maximum_x = min(int(float(boxes[:, 0].min())), width - crop_width)
            minimum_y = max(0, ceil(float(boxes[:, 3].max()) - crop_height))
            maximum_y = min(int(float(boxes[:, 1].min())), height - crop_height)
        else:
            minimum_x, maximum_x = 0, width - crop_width
            minimum_y, maximum_y = 0, height - crop_height
        offset_x = randomizer.randint(minimum_x, max(minimum_x, maximum_x))
        offset_y = randomizer.randint(minimum_y, max(minimum_y, maximum_y))
        image = image.crop((offset_x, offset_y, offset_x + crop_width, offset_y + crop_height))
        image = image.resize((width, height), Image.Resampling.BILINEAR)
        if len(boxes):
            scale_x = width / crop_width
            scale_y = height / crop_height
            boxes = boxes - torch.tensor([offset_x, offset_y, offset_x, offset_y])
            boxes = boxes * torch.tensor([scale_x, scale_y, scale_x, scale_y])

    if randomizer.random() < float(config.get("horizontalFlipProbability", 0.0)):
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if len(boxes):
            left = width - boxes[:, 2]
            right = width - boxes[:, 0]
            boxes = torch.stack((left, boxes[:, 1], right, boxes[:, 3]), dim=1)

    brightness = float(config.get("brightness", 0.0))
    contrast = float(config.get("contrast", 0.0))
    saturation = float(config.get("saturation", 0.0))
    if brightness:
        image = ImageEnhance.Brightness(image).enhance(randomizer.uniform(1 - brightness, 1 + brightness))
    if contrast:
        image = ImageEnhance.Contrast(image).enhance(randomizer.uniform(1 - contrast, 1 + contrast))
    if saturation:
        image = ImageEnhance.Color(image).enhance(randomizer.uniform(1 - saturation, 1 + saturation))
    metadata = {
        "requestedScale": requested_scale,
        "appliedScale": scale,
        "minimumObjectSide": minimum_object_side,
        "scaleClamped": scale > requested_scale + 1e-12,
        "safeCropScaleReduced": scale < requested_scale - 1e-12,
        "spatialOperation": "zoom-out" if scale < 1.0 else "safe-crop" if scale > 1.0 else "identity",
    }
    return (image, boxes, metadata) if return_metadata else (image, boxes)


class BalancedBatchSampler(Sampler):
    def __init__(
        self,
        positive_indices,
        negative_indices,
        batch_size,
        seed,
        source_by_index=None,
        replay_positive_indices=None,
        replay_factor=1,
    ):
        if not positive_indices or not negative_indices:
            raise ValueError("balanced training requires positive and negative frames")
        if batch_size < 2:
            raise ValueError("balanced training requires a batch size of at least two")
        if replay_factor < 1:
            raise ValueError("positive replay factor must be at least one")
        replay_positive_indices = set(replay_positive_indices or [])
        if not replay_positive_indices.issubset(positive_indices):
            raise ValueError("replayed indices must be positive frames")
        self.positive_indices = list(positive_indices)
        for replay_slot in range(1, replay_factor):
            self.positive_indices.extend(
                (index, replay_slot)
                for index in positive_indices
                if index in replay_positive_indices
            )
        self.negative_indices = list(negative_indices)
        self.replay_positive_indices = sorted(replay_positive_indices)
        self.replay_factor = replay_factor
        self.positive_per_batch = max(1, batch_size // 2)
        self.negative_per_batch = batch_size - self.positive_per_batch
        self.seed = seed
        self.epoch = 0
        self.source_by_index = source_by_index or {}
        self.sources = sorted({
            self._source(index)
            for index in self.positive_indices + self.negative_indices
        })
        self.source_balancing_active = len(self.sources) > 1
        self.batch_count = max(
            ceil(self._balanced_pool_size(self.positive_indices) / self.positive_per_batch),
            ceil(self._balanced_pool_size(self.negative_indices) / self.negative_per_batch),
        )

    def __len__(self):
        return self.batch_count

    def set_epoch(self, epoch):
        self.epoch = epoch

    def __iter__(self):
        randomizer = random.Random(self.seed + self.epoch)
        positive = self._ordered_indices(self.positive_indices, randomizer)
        negative = self._ordered_indices(self.negative_indices, randomizer)
        for batch_index in range(self.batch_count):
            batch = [
                positive[(batch_index * self.positive_per_batch + offset) % len(positive)]
                for offset in range(self.positive_per_batch)
            ]
            batch.extend(
                negative[(batch_index * self.negative_per_batch + offset) % len(negative)]
                for offset in range(self.negative_per_batch)
            )
            randomizer.shuffle(batch)
            yield batch

    def _ordered_indices(self, indices, randomizer):
        if not self.source_balancing_active:
            result = indices.copy()
            randomizer.shuffle(result)
            return result
        by_source = {source: [] for source in self.sources}
        for index in indices:
            by_source[self._source(index)].append(index)
        available = [source for source in self.sources if by_source[source]]
        for source in available:
            randomizer.shuffle(by_source[source])
        result = []
        positions = {source: 0 for source in available}
        target_per_source = max(len(by_source[source]) for source in available)
        while any(positions[source] < target_per_source for source in available):
            for source in available:
                position = positions[source]
                if position >= target_per_source:
                    continue
                item = by_source[source][position % len(by_source[source])]
                if position >= len(by_source[source]):
                    base_index = item[0] if isinstance(item, tuple) else item
                    item = (base_index, 2000000 + position)
                result.append(item)
                positions[source] += 1
        return result

    def _balanced_pool_size(self, indices):
        if not self.source_balancing_active:
            return len(indices)
        counts = {}
        for index in indices:
            source = self._source(index)
            counts[source] = counts.get(source, 0) + 1
        return max(counts.values()) * len(counts)

    def _source(self, index):
        base_index = index[0] if isinstance(index, tuple) else index
        return self.source_by_index.get(base_index, "unknown")


def collate(batch):
    return tuple(zip(*batch))


def is_better_checkpoint(
    metrics,
    best_metrics,
    minimum_improvement,
    minimum_tiny_recall=0.0,
    minimum_tiny_annotations=0,
):
    if metrics.get("operatingPointConstraintsSatisfied") is not True:
        return False
    tiny_metrics = metrics.get("objectSizeRecall", {}).get("tiny", {})
    tiny_recall = tiny_metrics.get("recall", 0.0)
    if tiny_metrics.get("annotations", 0) < minimum_tiny_annotations:
        return False
    if tiny_recall < minimum_tiny_recall:
        return False
    if best_metrics is None:
        return True
    if metrics["mAP50"] > best_metrics["mAP50"] + minimum_improvement:
        return True
    return (
        abs(metrics["mAP50"] - best_metrics["mAP50"]) <= minimum_improvement
        and (
            tiny_recall
            > best_metrics.get("objectSizeRecall", {}).get("tiny", {}).get("recall", 0.0)
            + minimum_improvement
            or (
                abs(
                    tiny_recall
                    - best_metrics.get("objectSizeRecall", {}).get("tiny", {}).get("recall", 0.0)
                )
                <= minimum_improvement
                and metrics["mAP50-95"]
                > best_metrics["mAP50-95"] + minimum_improvement
            )
        )
    )


def is_better_diagnostic_checkpoint(metrics, best_metrics, minimum_improvement):
    if best_metrics is None:
        return True
    tiny_recall = metrics.get("objectSizeRecall", {}).get("tiny", {}).get("recall", 0.0)
    best_tiny_recall = best_metrics.get("objectSizeRecall", {}).get("tiny", {}).get("recall", 0.0)
    if metrics["mAP50"] > best_metrics["mAP50"] + minimum_improvement:
        return True
    return (
        abs(metrics["mAP50"] - best_metrics["mAP50"]) <= minimum_improvement
        and (
            tiny_recall > best_tiny_recall + minimum_improvement
            or (
                abs(tiny_recall - best_tiny_recall) <= minimum_improvement
                and metrics["mAP50-95"] > best_metrics["mAP50-95"] + minimum_improvement
            )
        )
    )


def dataset_distribution(dataset):
    result = {"images": len(dataset), "annotations": 0, "positiveFrames": 0, "negativeFrames": 0, "tinyPositiveFrames": 0, "sources": {}}
    for index in range(len(dataset)):
        source = dataset.source_id(index)
        source_result = result["sources"].setdefault(source, {
            "images": 0,
            "annotations": 0,
            "positiveFrames": 0,
            "negativeFrames": 0,
            "tinyPositiveFrames": 0,
            "tinyAnnotations": 0,
            "smallAnnotations": 0,
            "largerAnnotations": 0,
        })
        source_result["images"] += 1
        image_path = dataset.images[index]
        with Image.open(image_path) as image:
            width, height = image.size
        annotation_sides = []
        for line in dataset.label_path(index).read_text().splitlines():
            _, _, _, box_width, box_height = map(float, line.split())
            annotation_sides.append(max(box_width * width, box_height * height))
        annotation_count = len(annotation_sides)
        result["annotations"] += annotation_count
        source_result["annotations"] += annotation_count
        if annotation_count:
            result["positiveFrames"] += 1
            source_result["positiveFrames"] += 1
        else:
            result["negativeFrames"] += 1
            source_result["negativeFrames"] += 1
        if any(side < 12.0 for side in annotation_sides):
            result["tinyPositiveFrames"] += 1
            source_result["tinyPositiveFrames"] += 1
        for side in annotation_sides:
            band = "tinyAnnotations" if side < 12.0 else "smallAnnotations" if side < 24.0 else "largerAnnotations"
            source_result[band] += 1
    return result


def summarize_augmentation_scales(records):
    if not records:
        return {
            "samples": 0,
            "clampedSamples": 0,
            "minimumRequestedScale": None,
            "minimumAppliedScale": None,
            "maximumRequestedScale": None,
            "maximumAppliedScale": None,
            "safeCropScaleReductions": 0,
            "spatialOperations": {},
        }
    operations = {}
    for record in records:
        operation = record.get("spatialOperation", "unknown")
        operations[operation] = operations.get(operation, 0) + 1
    return {
        "samples": len(records),
        "clampedSamples": sum(record["scaleClamped"] for record in records),
        "minimumRequestedScale": min(record["requestedScale"] for record in records),
        "minimumAppliedScale": min(record["appliedScale"] for record in records),
        "maximumRequestedScale": max(record["requestedScale"] for record in records),
        "maximumAppliedScale": max(record["appliedScale"] for record in records),
        "safeCropScaleReductions": sum(record.get("safeCropScaleReduced", False) for record in records),
        "spatialOperations": operations,
    }


def consume_early_stopping_patience(best_metrics):
    return best_metrics is not None


def threshold_grid(start, stop, step):
    count = int(round((stop - start) / step))
    return [round(start + index * step, 10) for index in range(count + 1)]


def require_stable_loss(loss, explosion_threshold, consecutive_explosions):
    value = float(loss.detach())
    if not torch.isfinite(loss):
        raise SystemExit("training produced a non-finite loss")
    if value > explosion_threshold:
        consecutive_explosions += 1
        if consecutive_explosions >= 2:
            raise SystemExit(
                f"training produced repeated explosive losses above {explosion_threshold}"
            )
    else:
        consecutive_explosions = 0
    return value, consecutive_explosions


BACKBONE_FREEZE_GROUPS = {
    "stem": ("conv1", "bn1"),
    "layer1": ("layer1",),
    "layer2": ("layer2",),
    "layer3": ("layer3",),
    "layer4": ("layer4",),
}


def freeze_backbone_stages(model, value):
    requested = [stage.strip() for stage in value.split(",") if stage.strip()]
    unknown = sorted(set(requested) - set(BACKBONE_FREEZE_GROUPS))
    if unknown:
        raise ValueError(f"unknown backbone freeze stages: {', '.join(unknown)}")
    frozen_modules = []
    for stage in requested:
        for module_name in BACKBONE_FREEZE_GROUPS[stage]:
            module = getattr(model.backbone.body, module_name)
            module.requires_grad_(False)
            frozen_modules.append(module)
    return requested, frozen_modules


def keep_frozen_modules_in_eval_mode(modules):
    for module in modules:
        module.eval()


def cosine_learning_rate(epoch, epochs, warmup_epochs, maximum, minimum):
    if minimum <= 0 or maximum <= minimum:
        raise ValueError("cosine learning rates must satisfy 0 < minimum < maximum")
    if epoch < warmup_epochs:
        return maximum * (epoch + 1) / max(warmup_epochs, 1)
    decay_epochs = max(epochs - warmup_epochs, 1)
    progress = min(max((epoch - warmup_epochs + 1) / decay_epochs, 0.0), 1.0)
    return minimum + 0.5 * (maximum - minimum) * (1.0 + cos(pi * progress))


def retain_diagnostic_checkpoint(model, directory, epoch, metrics, maximum_checkpoints):
    if maximum_checkpoints < 1:
        raise ValueError("diagnostic checkpoint retention must be at least one")
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"epoch-{epoch:04d}"
    torch.save(model.state_dict(), directory / f"{stem}.pt")
    record = {
        "epoch": epoch,
        "publishable": False,
        "promotionEligible": False,
        "exportEligible": False,
        "purpose": "training-diagnostics-only",
        "metrics": metrics,
    }
    (directory / f"{stem}.json").write_text(json.dumps(record, indent=2) + "\n")
    checkpoints = sorted(directory.glob("epoch-*.pt"))
    for checkpoint in checkpoints[:-maximum_checkpoints]:
        checkpoint.unlink()
        checkpoint.with_suffix(".json").unlink(missing_ok=True)
    (directory / "NON_PUBLISHABLE.json").write_text(json.dumps({
        "publishable": False,
        "promotionEligible": False,
        "exportEligible": False,
        "reason": "diagnostic checkpoints are not validation-selected candidate artifacts",
    }, indent=2) + "\n")


def train(dataset_id):
    seed = int(os.environ.get("DATASET_SEED", "42"))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    root = dataset_root(dataset_id)
    validation_result = validate(dataset_id)
    model = build_model()
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for the production training stage")
    device = torch.device("cuda")
    model.to(device)
    augmentation = {
        "scaleMin": float(os.environ.get("TRAINING_AUGMENT_SCALE_MIN", "0.60")),
        "scaleMax": float(os.environ.get("TRAINING_AUGMENT_SCALE_MAX", "1.00")),
        "horizontalFlipProbability": float(os.environ.get("TRAINING_AUGMENT_HORIZONTAL_FLIP", "0.50")),
        "brightness": float(os.environ.get("TRAINING_AUGMENT_BRIGHTNESS", "0.15")),
        "contrast": float(os.environ.get("TRAINING_AUGMENT_CONTRAST", "0.15")),
        "saturation": float(os.environ.get("TRAINING_AUGMENT_SATURATION", "0.10")),
        "canvasValue": int(os.environ.get("TRAINING_AUGMENT_CANVAS_VALUE", "114")),
        "minimumObjectSide": float(os.environ.get("TRAINING_AUGMENT_MIN_OBJECT_SIDE", "8.0")),
    }
    training_dataset = YoloDetectionDataset(
        root,
        "train",
        augment=True,
        seed=seed,
        augmentation=augmentation,
    )
    validation_dataset = YoloDetectionDataset(root, "validation")
    positive_indices = [index for index in range(len(training_dataset)) if training_dataset.has_annotations(index)]
    negative_indices = [index for index in range(len(training_dataset)) if not training_dataset.has_annotations(index)]
    tiny_positive_indices = [
        index for index in positive_indices
        if training_dataset.has_tiny_annotations(index)
    ]
    tiny_positive_replay_factor = int(
        os.environ.get("TRAINING_TINY_POSITIVE_REPLAY_FACTOR", "4")
    )
    batch_size = int(os.environ.get("TRAINING_BATCH_SIZE", "2"))
    source_by_index = {
        index: training_dataset.source_id(index)
        for index in range(len(training_dataset))
    }
    sampler = BalancedBatchSampler(
        positive_indices,
        negative_indices,
        batch_size,
        seed,
        source_by_index,
        replay_positive_indices=tiny_positive_indices,
        replay_factor=tiny_positive_replay_factor,
    )
    loader = DataLoader(training_dataset, batch_sampler=sampler, collate_fn=collate)
    base_learning_rate = float(os.environ.get("TRAINING_LEARNING_RATE", "0.0002"))
    loss_weights = detection_loss_weights()
    warmup_epochs = int(os.environ.get("TRAINING_WARMUP_EPOCHS", "5"))
    epochs = int(os.environ.get("TRAINING_EPOCHS", "150"))
    minimum_learning_rate = float(os.environ.get("TRAINING_MIN_LEARNING_RATE", "0.000001"))
    weight_decay = float(os.environ.get("TRAINING_WEIGHT_DECAY", "0.01"))
    if weight_decay < 0:
        raise ValueError("training weight decay must be non-negative")
    frozen_backbone_stages, frozen_backbone_modules = freeze_backbone_stages(
        model, os.environ.get("TRAINING_FREEZE_BACKBONE_STAGES", "")
    )
    trainable_parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    if not trainable_parameters:
        raise ValueError("training requires at least one trainable parameter")
    optimizer = torch.optim.AdamW(
        trainable_parameters,
        lr=base_learning_rate / max(warmup_epochs, 1),
        weight_decay=weight_decay,
    )
    scheduler_name = os.environ.get("TRAINING_LR_SCHEDULER", "reduce-on-validation-plateau")
    if scheduler_name == "reduce-on-validation-plateau":
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer,
            mode="max",
            factor=float(os.environ.get("TRAINING_LR_REDUCTION_FACTOR", "0.5")),
            patience=int(os.environ.get("TRAINING_LR_PATIENCE", "2")),
            threshold=float(os.environ.get("TRAINING_MIN_VALIDATION_IMPROVEMENT", "0.001")),
            min_lr=minimum_learning_rate,
        )
    elif scheduler_name == "cosine":
        scheduler = None
    else:
        raise ValueError(f"unsupported training learning-rate scheduler: {scheduler_name}")
    validation_interval = int(os.environ.get("TRAINING_VALIDATION_INTERVAL", "5"))
    early_stopping_patience = int(os.environ.get("TRAINING_EARLY_STOPPING_PATIENCE", "5"))
    diagnostic_early_stopping_patience = int(
        os.environ.get("TRAINING_DIAGNOSTIC_EARLY_STOPPING_PATIENCE", "0")
    )
    if early_stopping_patience < 1:
        raise ValueError("eligible-checkpoint early stopping patience must be at least one")
    if diagnostic_early_stopping_patience < 0:
        raise ValueError("diagnostic early stopping patience must be non-negative")
    minimum_improvement = float(os.environ.get("TRAINING_MIN_VALIDATION_IMPROVEMENT", "0.001"))
    minimum_tiny_recall = float(os.environ.get("TRAINING_CHECKPOINT_MIN_TINY_RECALL", "0.20"))
    minimum_tiny_annotations = int(
        os.environ.get("TRAINING_CHECKPOINT_MIN_TINY_ANNOTATIONS", "10")
    )
    calibration_thresholds = threshold_grid(
        float(os.environ.get("TRAINING_THRESHOLD_MIN", "0.05")),
        float(os.environ.get("TRAINING_THRESHOLD_MAX", "0.95")),
        float(os.environ.get("TRAINING_THRESHOLD_STEP", "0.05")),
    )
    calibration_minimum_recall = float(os.environ.get("TRAINING_THRESHOLD_MIN_RECALL", "0.60"))
    calibration_minimum_precision = float(
        os.environ.get("TRAINING_THRESHOLD_MIN_PRECISION", "0.60")
    )
    calibration_maximum_negative_detection_rate = float(
        os.environ.get("TRAINING_THRESHOLD_MAX_DETECTIONS_PER_NEGATIVE_FRAME", "0.10")
    )
    gradient_clip_norm = float(os.environ.get("TRAINING_GRADIENT_CLIP_NORM", "5.0"))
    explosion_threshold = float(os.environ.get("TRAINING_LOSS_EXPLOSION_THRESHOLD", "50.0"))
    output = root / "artifacts"
    output.mkdir(parents=True, exist_ok=True)
    best_checkpoint = output / "best-model.pt"
    diagnostic_directory = output / "diagnostic-checkpoints"
    retain_diagnostics = os.environ.get("TRAINING_RETAIN_DIAGNOSTIC_CHECKPOINTS", "true").lower() == "true"
    maximum_diagnostic_checkpoints = int(os.environ.get("TRAINING_MAX_DIAGNOSTIC_CHECKPOINTS", "5"))
    distribution_audit = {
        "schemaVersion": 1,
        "datasetId": dataset_id,
        "train": dataset_distribution(training_dataset),
        "validation": dataset_distribution(validation_dataset),
        "splitLeakageValidation": validation_result.get("leakageDetected"),
        "datasetValidationStatus": validation_result.get("status"),
    }
    (output / "dataset-distribution-audit.json").write_text(
        json.dumps(distribution_audit, indent=2) + "\n"
    )
    epoch_losses = []
    component_loss_history = []
    validation_history = []
    learning_rate_history = []
    gradient_norm_history = []
    maximum_batch_loss_history = []
    best_metrics = None
    best_epoch = None
    best_diagnostic_metrics = None
    best_diagnostic_epoch = None
    evaluations_without_improvement = 0
    diagnostic_evaluations_without_improvement = 0
    stopped_early = False
    early_stopping_reason = None
    consecutive_explosions = 0
    for epoch in range(epochs):
        if scheduler_name == "cosine":
            scheduled_learning_rate = cosine_learning_rate(
                epoch, epochs, warmup_epochs, base_learning_rate, minimum_learning_rate
            )
            for group in optimizer.param_groups:
                group["lr"] = scheduled_learning_rate
        elif epoch < warmup_epochs:
            warmup_learning_rate = base_learning_rate * (epoch + 1) / max(warmup_epochs, 1)
            for group in optimizer.param_groups:
                group["lr"] = warmup_learning_rate
        sampler.set_epoch(epoch)
        training_dataset.set_epoch(epoch)
        model.train()
        keep_frozen_modules_in_eval_mode(frozen_backbone_modules)
        running_loss = 0.0
        running_components = {}
        running_gradient_norm = 0.0
        maximum_batch_loss = 0.0
        batches = 0
        for images, targets in loader:
            losses = model([image.to(device) for image in images], [{key: value.to(device) for key, value in target.items()} for target in targets])
            loss = weighted_detection_loss(losses, loss_weights)
            loss_value, consecutive_explosions = require_stable_loss(
                loss,
                explosion_threshold,
                consecutive_explosions,
            )
            optimizer.zero_grad()
            loss.backward()
            gradient_norm = torch.nn.utils.clip_grad_norm_(trainable_parameters, gradient_clip_norm)
            if not torch.isfinite(gradient_norm):
                raise SystemExit("training produced a non-finite gradient norm")
            optimizer.step()
            running_loss += loss_value
            running_gradient_norm += float(gradient_norm)
            maximum_batch_loss = max(maximum_batch_loss, loss_value)
            for name, value in losses.items():
                running_components[name] = running_components.get(name, 0.0) + value.detach().item()
            batches += 1
        epoch_loss = running_loss / max(batches, 1)
        epoch_losses.append(epoch_loss)
        component_losses = {
            name: value / max(batches, 1)
            for name, value in sorted(running_components.items())
        }
        component_loss_history.append(component_losses)
        average_gradient_norm = running_gradient_norm / max(batches, 1)
        current_learning_rate = optimizer.param_groups[0]["lr"]
        gradient_norm_history.append(average_gradient_norm)
        maximum_batch_loss_history.append(maximum_batch_loss)
        learning_rate_history.append(current_learning_rate)
        progress = {
            "epoch": epoch + 1,
            "epochs": epochs,
            "loss": epoch_loss,
            "maximumBatchLoss": maximum_batch_loss,
            "averageGradientNorm": average_gradient_norm,
            "learningRate": current_learning_rate,
            "componentLosses": component_losses,
        }
        should_validate = (epoch + 1) % validation_interval == 0 or epoch + 1 == epochs
        if should_validate:
            validation_outputs, validation_truth = collect_detections(
                model, validation_dataset, device
            )
            validation_metrics, threshold_sweep = calibrate_score_threshold(
                validation_outputs,
                validation_truth,
                calibration_thresholds,
                calibration_minimum_recall,
                calibration_minimum_precision,
                calibration_maximum_negative_detection_rate,
            )
            validation_metrics["epoch"] = epoch + 1
            validation_metrics["perSource"] = per_source_metrics(
                validation_outputs,
                validation_truth,
                validation_dataset,
                validation_metrics["scoreThreshold"],
            )
            validation_metrics["objectSizeRecall"] = object_size_recall(
                validation_outputs,
                validation_truth,
                validation_metrics["scoreThreshold"],
            )
            validation_record = {
                **validation_metrics,
                "thresholdSweep": threshold_sweep,
            }
            validation_history.append(validation_record)
            progress["validation"] = validation_record
            if retain_diagnostics:
                retain_diagnostic_checkpoint(
                    model,
                    diagnostic_directory,
                    epoch + 1,
                    validation_record,
                    maximum_diagnostic_checkpoints,
                )
            if is_better_diagnostic_checkpoint(
                validation_metrics,
                best_diagnostic_metrics,
                minimum_improvement,
            ):
                best_diagnostic_metrics = validation_metrics.copy()
                best_diagnostic_epoch = epoch + 1
                diagnostic_evaluations_without_improvement = 0
                if retain_diagnostics:
                    diagnostic_directory.mkdir(parents=True, exist_ok=True)
                    torch.save(model.state_dict(), diagnostic_directory / "best-diagnostic.pt")
                    (diagnostic_directory / "best-diagnostic.json").write_text(json.dumps({
                        "epoch": best_diagnostic_epoch,
                        "publishable": False,
                        "promotionEligible": False,
                        "exportEligible": False,
                        "purpose": "best-broad-validation-diagnostic-only",
                        "metrics": validation_record,
                    }, indent=2) + "\n")
            else:
                diagnostic_evaluations_without_improvement += 1
            if is_better_checkpoint(
                validation_metrics,
                best_metrics,
                minimum_improvement,
                minimum_tiny_recall,
                minimum_tiny_annotations,
            ):
                best_metrics = validation_metrics.copy()
                best_epoch = epoch + 1
                evaluations_without_improvement = 0
                torch.save(model.state_dict(), best_checkpoint)
            elif consume_early_stopping_patience(best_metrics):
                evaluations_without_improvement += 1
                if evaluations_without_improvement >= early_stopping_patience:
                    stopped_early = True
                    early_stopping_reason = "eligible-checkpoint-patience"
            if (
                not stopped_early
                and diagnostic_early_stopping_patience > 0
                and diagnostic_evaluations_without_improvement >= diagnostic_early_stopping_patience
            ):
                stopped_early = True
                early_stopping_reason = "diagnostic-validation-patience"
            if scheduler is not None and epoch + 1 > warmup_epochs:
                scheduler.step(validation_metrics["mAP50"])
        print(json.dumps(progress), flush=True)
        if stopped_early:
            break
    if not best_checkpoint.exists():
        raise SystemExit("training did not produce a validation-selected checkpoint")
    shutil.copyfile(best_checkpoint, output / "model.pt")
    metadata = {
        "datasetId": dataset_id,
        "device": str(device),
        "epochs": epochs,
        "batchSize": batch_size,
        "balancedBatches": True,
        "sourceBalancedBatches": sampler.source_balancing_active,
        "sourceBalancingPolicy": "equal-exposure-within-label-pools",
        "trainingSources": sampler.sources,
        "trainingSourceCount": len(sampler.sources),
        "augmentation": augmentation,
        "augmentationScaleSummary": summarize_augmentation_scales(
            training_dataset.augmentation_scale_records
        ),
        "trainingPositiveImages": len(positive_indices),
        "trainingNegativeImages": len(negative_indices),
        "trainingTinyPositiveImages": len(tiny_positive_indices),
        "tinyPositiveReplayFactor": tiny_positive_replay_factor,
        "effectivePositiveSamplesPerEpoch": len(sampler.positive_indices),
        "baseLearningRate": base_learning_rate,
        "minimumLearningRate": minimum_learning_rate,
        "learningRateScheduler": scheduler_name,
        "weightDecay": weight_decay,
        "frozenBackboneStages": frozen_backbone_stages,
        "trainableParameters": sum(parameter.numel() for parameter in trainable_parameters),
        "totalParameters": sum(parameter.numel() for parameter in model.parameters()),
        "lossWeights": loss_weights,
        "finalLearningRate": optimizer.param_groups[0]["lr"],
        "warmupEpochs": warmup_epochs,
        "learningRateHistory": learning_rate_history,
        "gradientClipNorm": gradient_clip_norm,
        "averageGradientNormHistory": gradient_norm_history,
        "lossExplosionThreshold": explosion_threshold,
        "maximumBatchLossHistory": maximum_batch_loss_history,
        "seed": seed,
        "epochLosses": epoch_losses,
        "componentLossHistory": component_loss_history,
        "finalLoss": epoch_losses[-1],
        "epochsCompleted": len(epoch_losses),
        "validationInterval": validation_interval,
        "thresholdCalibration": {
            "sourceSplit": "validation",
            "minimumRecall": calibration_minimum_recall,
            "minimumPrecision": calibration_minimum_precision,
            "maximumDetectionsPerNegativeFrame": calibration_maximum_negative_detection_rate,
            "thresholds": calibration_thresholds,
        },
        "validationHistory": validation_history,
        "bestValidationEpoch": best_epoch,
        "bestValidationMetrics": best_metrics,
        "selectedOperatingThreshold": best_metrics["scoreThreshold"],
        "earlyStoppingPatience": early_stopping_patience,
        "diagnosticEarlyStoppingPatience": diagnostic_early_stopping_patience,
        "earlyStoppingReason": early_stopping_reason,
        "bestDiagnosticValidationEpoch": best_diagnostic_epoch,
        "bestDiagnosticValidationMetrics": best_diagnostic_metrics,
        "minimumValidationImprovement": minimum_improvement,
        "minimumCheckpointTinyRecall": minimum_tiny_recall,
        "minimumCheckpointTinyAnnotations": minimum_tiny_annotations,
        "stoppedEarly": stopped_early,
        "publishedCheckpoint": "best-validation",
        "validation": validation_result,
        "datasetDistributionAudit": distribution_audit,
        "diagnosticCheckpointRetention": {
            "enabled": retain_diagnostics,
            "maximumCheckpoints": maximum_diagnostic_checkpoints,
            "publishable": False,
            "exportEligible": False,
        },
        **initialization_metadata(model),
    }
    (output / "training-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    train(sys.argv[1])