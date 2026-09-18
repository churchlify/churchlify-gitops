import json
import os
import random
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

from .dataset import dataset_root, validate
from .model import build_model, initialization_metadata


class YoloDetectionDataset(Dataset):
    def __init__(self, root, split):
        self.image_dir = root / split / "images"
        self.label_dir = root / split / "labels"
        self.images = sorted(path for path in self.image_dir.glob("**/*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"})

    def __len__(self):
        return len(self.images)

    def __getitem__(self, index):
        image_path = self.images[index]
        image = Image.open(image_path).convert("RGB")
        width, height = image.size
        tensor = torch.from_numpy(np.asarray(image)).permute(2, 0, 1).float() / 255
        label_path = self.label_dir / image_path.relative_to(self.image_dir).with_suffix(".txt")
        boxes = []
        labels = []
        for line in label_path.read_text().splitlines():
            class_id, center_x, center_y, box_width, box_height = map(float, line.split())
            boxes.append([(center_x - box_width / 2) * width, (center_y - box_height / 2) * height, (center_x + box_width / 2) * width, (center_y + box_height / 2) * height])
            labels.append(int(class_id) + 1)
        target = {"boxes": torch.tensor(boxes, dtype=torch.float32), "labels": torch.tensor(labels, dtype=torch.int64), "image_id": torch.tensor([index])}
        return tensor, target


def collate(batch):
    return tuple(zip(*batch))


def train(dataset_id):
    seed = int(os.environ.get("DATASET_SEED", "42"))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    root = dataset_root(dataset_id)
    validation_result = validate(dataset_id)
    model = build_model()
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for the production training stage")
    device = torch.device("cuda")
    model.to(device)
    loader = DataLoader(YoloDetectionDataset(root, "train"), batch_size=int(os.environ.get("TRAINING_BATCH_SIZE", "2")), shuffle=True, collate_fn=collate)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(os.environ.get("TRAINING_LEARNING_RATE", "0.001")))
    epochs = int(os.environ.get("TRAINING_EPOCHS", "150"))
    model.train()
    for _ in range(epochs):
        for images, targets in loader:
            losses = model([image.to(device) for image in images], [{key: value.to(device) for key, value in target.items()} for target in targets])
            loss = sum(losses.values())
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
    output = root / "artifacts"
    output.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), output / "model.pt")
    metadata = {"datasetId": dataset_id, "device": str(device), "epochs": epochs, "validation": validation_result, **initialization_metadata()}
    (output / "training-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    train(sys.argv[1])