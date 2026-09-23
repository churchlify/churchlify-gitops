import torch
from torchvision.models.detection.anchor_utils import AnchorGenerator
from torchvision.models.detection import fasterrcnn_resnet50_fpn
from torchvision.models.detection.rpn import RPNHead


SMALL_OBJECT_ANCHOR_SIZES = (
    (4, 8, 16),
    (8, 16, 32),
    (16, 32, 64),
    (32, 64, 128),
    (64, 128, 256),
)
ANCHOR_ASPECT_RATIOS = ((0.5, 1.0, 2.0),) * len(SMALL_OBJECT_ANCHOR_SIZES)


def build_model():
    model = fasterrcnn_resnet50_fpn(
        weights=None,
        weights_backbone=None,
        num_classes=2,
        min_size=1024,
        max_size=1024,
    )
    model.rpn.anchor_generator = AnchorGenerator(
        sizes=SMALL_OBJECT_ANCHOR_SIZES,
        aspect_ratios=ANCHOR_ASPECT_RATIOS,
    )
    model.rpn.head = RPNHead(
        model.backbone.out_channels,
        model.rpn.anchor_generator.num_anchors_per_location()[0],
    )
    return model


def initialization_metadata():
    return {
        "initialization": "random",
        "pretrainedWeightsUsed": False,
        "architectureLicense": "BSD-3-Clause",
        "sourceRepository": "https://github.com/pytorch/vision",
        "sourceRevision": "v0.20.1",
        "anchorSizes": SMALL_OBJECT_ANCHOR_SIZES,
        "anchorAspectRatios": ANCHOR_ASPECT_RATIOS,
    }