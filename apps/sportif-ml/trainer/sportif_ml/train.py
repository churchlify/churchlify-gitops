import json
import os
import random
import shutil
import sys
from math import ceil
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
from .model import build_model, initialization_metadata


class YoloDetectionDataset(Dataset):
    def __init__(self, root, split, augment=False, seed=42, augmentation=None):
        self.image_dir = root / split / "images"
        self.label_dir = root / split / "labels"
        self.images = sorted(path for path in self.image_dir.glob("**/*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"})
        self.augment = augment
        self.seed = seed
        self.epoch = 0
        self.augmentation = augmentation or {}

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

    def __len__(self):
        return len(self.images)

    def __getitem__(self, index):
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
            image, box_tensor = augment_detection(
                image,
                box_tensor,
                random.Random(self.seed + self.epoch * max(len(self), 1) + index),
                self.augmentation,
            )
        tensor = torch.from_numpy(np.array(image, copy=True)).permute(2, 0, 1).float() / 255
        target = {
            "boxes": box_tensor,
            "labels": torch.tensor(labels, dtype=torch.int64),
            "image_id": torch.tensor([index]),
        }
        return tensor, target


def augment_detection(image, boxes, randomizer, config):
    width, height = image.size
    scale = randomizer.uniform(
        float(config.get("scaleMin", 1.0)),
        float(config.get("scaleMax", 1.0)),
    )
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
    return image, boxes


class BalancedBatchSampler(Sampler):
    def __init__(self, positive_indices, negative_indices, batch_size, seed, source_by_index=None):
        if not positive_indices or not negative_indices:
            raise ValueError("balanced training requires positive and negative frames")
        if batch_size < 2:
            raise ValueError("balanced training requires a batch size of at least two")
        self.positive_indices = list(positive_indices)
        self.negative_indices = list(negative_indices)
        self.positive_per_batch = max(1, batch_size // 2)
        self.negative_per_batch = batch_size - self.positive_per_batch
        self.seed = seed
        self.epoch = 0
        self.source_by_index = source_by_index or {}
        self.sources = sorted({
            self.source_by_index.get(index, "unknown")
            for index in self.positive_indices + self.negative_indices
        })
        self.source_balancing_active = len(self.sources) > 1
        self.batch_count = max(
            ceil(len(self.positive_indices) / self.positive_per_batch),
            ceil(len(self.negative_indices) / self.negative_per_batch),
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
            by_source[self.source_by_index.get(index, "unknown")].append(index)
        available = [source for source in self.sources if by_source[source]]
        for source in available:
            randomizer.shuffle(by_source[source])
        result = []
        positions = {source: 0 for source in available}
        while len(result) < len(indices):
            for source in available:
                if positions[source] < len(by_source[source]):
                    result.append(by_source[source][positions[source]])
                    positions[source] += 1
        return result


def collate(batch):
    return tuple(zip(*batch))


def is_better_checkpoint(metrics, best_metrics, minimum_improvement):
    if best_metrics is None:
        return True
    if metrics["mAP50"] > best_metrics["mAP50"] + minimum_improvement:
        return True
    return (
        abs(metrics["mAP50"] - best_metrics["mAP50"]) <= minimum_improvement
        and metrics["mAP50-95"] > best_metrics["mAP50-95"] + minimum_improvement
    )


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
    )
    loader = DataLoader(training_dataset, batch_sampler=sampler, collate_fn=collate)
    base_learning_rate = float(os.environ.get("TRAINING_LEARNING_RATE", "0.0002"))
    warmup_epochs = int(os.environ.get("TRAINING_WARMUP_EPOCHS", "5"))
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=base_learning_rate / max(warmup_epochs, 1),
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer,
        mode="max",
        factor=float(os.environ.get("TRAINING_LR_REDUCTION_FACTOR", "0.5")),
        patience=int(os.environ.get("TRAINING_LR_PATIENCE", "2")),
        threshold=float(os.environ.get("TRAINING_MIN_VALIDATION_IMPROVEMENT", "0.001")),
        min_lr=float(os.environ.get("TRAINING_MIN_LEARNING_RATE", "0.000001")),
    )
    epochs = int(os.environ.get("TRAINING_EPOCHS", "150"))
    validation_interval = int(os.environ.get("TRAINING_VALIDATION_INTERVAL", "5"))
    early_stopping_patience = int(os.environ.get("TRAINING_EARLY_STOPPING_PATIENCE", "5"))
    minimum_improvement = float(os.environ.get("TRAINING_MIN_VALIDATION_IMPROVEMENT", "0.001"))
    calibration_thresholds = threshold_grid(
        float(os.environ.get("TRAINING_THRESHOLD_MIN", "0.05")),
        float(os.environ.get("TRAINING_THRESHOLD_MAX", "0.95")),
        float(os.environ.get("TRAINING_THRESHOLD_STEP", "0.05")),
    )
    calibration_minimum_recall = float(os.environ.get("TRAINING_THRESHOLD_MIN_RECALL", "0.20"))
    gradient_clip_norm = float(os.environ.get("TRAINING_GRADIENT_CLIP_NORM", "5.0"))
    explosion_threshold = float(os.environ.get("TRAINING_LOSS_EXPLOSION_THRESHOLD", "50.0"))
    output = root / "artifacts"
    output.mkdir(parents=True, exist_ok=True)
    best_checkpoint = output / "best-model.pt"
    epoch_losses = []
    component_loss_history = []
    validation_history = []
    learning_rate_history = []
    gradient_norm_history = []
    maximum_batch_loss_history = []
    best_metrics = None
    best_epoch = None
    evaluations_without_improvement = 0
    stopped_early = False
    consecutive_explosions = 0
    for epoch in range(epochs):
        if epoch < warmup_epochs:
            warmup_learning_rate = base_learning_rate * (epoch + 1) / max(warmup_epochs, 1)
            for group in optimizer.param_groups:
                group["lr"] = warmup_learning_rate
        sampler.set_epoch(epoch)
        training_dataset.set_epoch(epoch)
        model.train()
        running_loss = 0.0
        running_components = {}
        running_gradient_norm = 0.0
        maximum_batch_loss = 0.0
        batches = 0
        for images, targets in loader:
            losses = model([image.to(device) for image in images], [{key: value.to(device) for key, value in target.items()} for target in targets])
            loss = sum(losses.values())
            loss_value, consecutive_explosions = require_stable_loss(
                loss,
                explosion_threshold,
                consecutive_explosions,
            )
            optimizer.zero_grad()
            loss.backward()
            gradient_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip_norm)
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
            if is_better_checkpoint(validation_metrics, best_metrics, minimum_improvement):
                best_metrics = validation_metrics.copy()
                best_epoch = epoch + 1
                evaluations_without_improvement = 0
                torch.save(model.state_dict(), best_checkpoint)
            else:
                evaluations_without_improvement += 1
                if evaluations_without_improvement >= early_stopping_patience:
                    stopped_early = True
            if epoch + 1 > warmup_epochs:
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
        "trainingSources": sampler.sources,
        "trainingSourceCount": len(sampler.sources),
        "augmentation": augmentation,
        "trainingPositiveImages": len(positive_indices),
        "trainingNegativeImages": len(negative_indices),
        "baseLearningRate": base_learning_rate,
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
            "thresholds": calibration_thresholds,
        },
        "validationHistory": validation_history,
        "bestValidationEpoch": best_epoch,
        "bestValidationMetrics": best_metrics,
        "selectedOperatingThreshold": best_metrics["scoreThreshold"],
        "earlyStoppingPatience": early_stopping_patience,
        "minimumValidationImprovement": minimum_improvement,
        "stoppedEarly": stopped_early,
        "publishedCheckpoint": "best-validation",
        "validation": validation_result,
        **initialization_metadata(),
    }
    (output / "training-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    train(sys.argv[1])