import torch
from torchvision.models.detection import fasterrcnn_resnet50_fpn


def build_model():
    model = fasterrcnn_resnet50_fpn(
        weights=None,
        weights_backbone=None,
        num_classes=2,
        min_size=1024,
        max_size=1024,
    )
    return model


def initialization_metadata():
    return {
        "initialization": "random",
        "pretrainedWeightsUsed": False,
        "architectureLicense": "BSD-3-Clause",
        "sourceRepository": "https://github.com/pytorch/vision",
        "sourceRevision": "v0.20.1",
    }