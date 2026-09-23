import json
import os
import random
import shutil
import sys
from math import ceil
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset, Sampler

from .dataset import dataset_root, validate
from .metrics import evaluate_dataset
from .model import build_model, initialization_metadata


class YoloDetectionDataset(Dataset):
    def __init__(self, root, split):
        self.image_dir = root / split / "images"
        self.label_dir = root / split / "labels"
        self.images = sorted(path for path in self.image_dir.glob("**/*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"})

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
        tensor = torch.from_numpy(np.array(image, copy=True)).permute(2, 0, 1).float() / 255
        label_path = self.label_path(index)
        boxes = []
        labels = []
        for line in label_path.read_text().splitlines():
            class_id, center_x, center_y, box_width, box_height = map(float, line.split())
            boxes.append([(center_x - box_width / 2) * width, (center_y - box_height / 2) * height, (center_x + box_width / 2) * width, (center_y + box_height / 2) * height])
            labels.append(int(class_id) + 1)
        target = {
            "boxes": torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4),
            "labels": torch.tensor(labels, dtype=torch.int64),
            "image_id": torch.tensor([index]),
        }
        return tensor, target


class BalancedBatchSampler(Sampler):
    def __init__(self, positive_indices, negative_indices, batch_size, seed):
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
        positive = self.positive_indices.copy()
        negative = self.negative_indices.copy()
        randomizer.shuffle(positive)
        randomizer.shuffle(negative)
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
    training_dataset = YoloDetectionDataset(root, "train")
    validation_dataset = YoloDetectionDataset(root, "validation")
    positive_indices = [index for index in range(len(training_dataset)) if training_dataset.has_annotations(index)]
    negative_indices = [index for index in range(len(training_dataset)) if not training_dataset.has_annotations(index)]
    batch_size = int(os.environ.get("TRAINING_BATCH_SIZE", "2"))
    sampler = BalancedBatchSampler(positive_indices, negative_indices, batch_size, seed)
    loader = DataLoader(training_dataset, batch_sampler=sampler, collate_fn=collate)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(os.environ.get("TRAINING_LEARNING_RATE", "0.001")))
    epochs = int(os.environ.get("TRAINING_EPOCHS", "150"))
    validation_interval = int(os.environ.get("TRAINING_VALIDATION_INTERVAL", "5"))
    early_stopping_patience = int(os.environ.get("TRAINING_EARLY_STOPPING_PATIENCE", "5"))
    minimum_improvement = float(os.environ.get("TRAINING_MIN_VALIDATION_IMPROVEMENT", "0.001"))
    validation_score_threshold = float(os.environ.get("TRAINING_VALIDATION_SCORE_THRESHOLD", "0.05"))
    output = root / "artifacts"
    output.mkdir(parents=True, exist_ok=True)
    best_checkpoint = output / "best-model.pt"
    epoch_losses = []
    component_loss_history = []
    validation_history = []
    best_metrics = None
    best_epoch = None
    evaluations_without_improvement = 0
    stopped_early = False
    for epoch in range(epochs):
        sampler.set_epoch(epoch)
        model.train()
        running_loss = 0.0
        running_components = {}
        batches = 0
        for images, targets in loader:
            losses = model([image.to(device) for image in images], [{key: value.to(device) for key, value in target.items()} for target in targets])
            loss = sum(losses.values())
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            running_loss += loss.detach().item()
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
        progress = {"epoch": epoch + 1, "epochs": epochs, "loss": epoch_loss, "componentLosses": component_losses}
        should_validate = (epoch + 1) % validation_interval == 0 or epoch + 1 == epochs
        if should_validate:
            validation_metrics = evaluate_dataset(
                model,
                validation_dataset,
                device,
                validation_score_threshold,
            )
            validation_metrics["epoch"] = epoch + 1
            validation_history.append(validation_metrics)
            progress["validation"] = validation_metrics
            if is_better_checkpoint(validation_metrics, best_metrics, minimum_improvement):
                best_metrics = validation_metrics.copy()
                best_epoch = epoch + 1
                evaluations_without_improvement = 0
                torch.save(model.state_dict(), best_checkpoint)
            else:
                evaluations_without_improvement += 1
                if evaluations_without_improvement >= early_stopping_patience:
                    stopped_early = True
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
        "trainingPositiveImages": len(positive_indices),
        "trainingNegativeImages": len(negative_indices),
        "learningRate": optimizer.param_groups[0]["lr"],
        "seed": seed,
        "epochLosses": epoch_losses,
        "componentLossHistory": component_loss_history,
        "finalLoss": epoch_losses[-1],
        "epochsCompleted": len(epoch_losses),
        "validationInterval": validation_interval,
        "validationScoreThreshold": validation_score_threshold,
        "validationHistory": validation_history,
        "bestValidationEpoch": best_epoch,
        "bestValidationMetrics": best_metrics,
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