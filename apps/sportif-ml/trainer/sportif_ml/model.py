import hashlib
import os
from pathlib import Path

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


def image_resize_policy(environ=None):
    environ = os.environ if environ is None else environ
    minimum = int(environ.get("TRAINING_MIN_IMAGE_SIZE", "720"))
    maximum = int(environ.get("TRAINING_MAX_IMAGE_SIZE", "1280"))
    if minimum < 1 or maximum < minimum:
        raise ValueError("model image resize bounds must satisfy 1 <= minimum <= maximum")
    return minimum, maximum


INITIALIZATION_MODES = {"random", "pretrained-backbone", "pretrained-detector"}


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def initialization_configuration(environ=None):
    environ = os.environ if environ is None else environ
    mode = environ.get("TRAINING_INITIALIZATION", "random")
    if mode not in INITIALIZATION_MODES:
        raise ValueError(f"unsupported training initialization mode: {mode}")
    if mode == "random":
        return {"mode": mode, "pretrainedWeightsUsed": False}
    if environ.get("TRAINING_PRETRAINED_WEIGHTS_APPROVED", "").lower() != "true":
        raise SystemExit("pretrained initialization requires explicit weight approval")
    required = {
        "path": "TRAINING_PRETRAINED_WEIGHTS_PATH",
        "sha256": "TRAINING_PRETRAINED_WEIGHTS_SHA256",
        "source": "TRAINING_PRETRAINED_WEIGHTS_SOURCE",
        "license": "TRAINING_PRETRAINED_WEIGHTS_LICENSE",
        "identifier": "TRAINING_PRETRAINED_WEIGHTS_IDENTIFIER",
    }
    values = {key: environ.get(name, "").strip() for key, name in required.items()}
    missing = [required[key] for key, value in values.items() if not value]
    if missing:
        raise SystemExit(f"pretrained initialization metadata is incomplete: {missing}")
    path = Path(values["path"])
    if not path.is_absolute() or not path.is_file():
        raise SystemExit("pretrained weights must be an existing absolute local file")
    expected = values["sha256"].lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise SystemExit("pretrained weights SHA-256 is invalid")
    actual = _sha256(path)
    if actual != expected:
        raise SystemExit("pretrained weights SHA-256 does not match the approved digest")
    return {
        "mode": mode,
        "pretrainedWeightsUsed": True,
        "weightsPath": str(path),
        "weightsSha256": actual,
        "weightsSource": values["source"],
        "weightsLicense": values["license"],
        "weightsIdentifier": values["identifier"],
        "weightsApproved": True,
        "offlineReproducible": True,
    }


def _checkpoint_state(path):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(checkpoint, dict) and isinstance(checkpoint.get("state_dict"), dict):
        checkpoint = checkpoint["state_dict"]
    if not isinstance(checkpoint, dict):
        raise SystemExit("pretrained weights must contain a state dictionary")
    return {key.removeprefix("module."): value for key, value in checkpoint.items()}


def _load_initialization(model, configuration):
    if configuration["mode"] == "random":
        return
    state = _checkpoint_state(configuration["weightsPath"])
    if configuration["mode"] == "pretrained-backbone":
        incompatible = model.backbone.body.load_state_dict(state, strict=False)
        loaded = set(state) - set(incompatible.unexpected_keys)
    else:
        current = model.state_dict()
        compatible = {
            key: value for key, value in state.items()
            if key in current and current[key].shape == value.shape
        }
        model.load_state_dict(compatible, strict=False)
        loaded = set(compatible)
    if not loaded:
        raise SystemExit("pretrained checkpoint has no compatible model parameters")
    configuration["loadedParameterTensors"] = len(loaded)


def build_model(environ=None):
    environ = os.environ if environ is None else environ
    minimum_image_size, maximum_image_size = image_resize_policy(environ)
    configuration = initialization_configuration(environ)
    model = fasterrcnn_resnet50_fpn(
        weights=None,
        weights_backbone=None,
        num_classes=2,
        min_size=minimum_image_size,
        max_size=maximum_image_size,
    )
    model.rpn.anchor_generator = AnchorGenerator(
        sizes=SMALL_OBJECT_ANCHOR_SIZES,
        aspect_ratios=ANCHOR_ASPECT_RATIOS,
    )
    model.rpn.head = RPNHead(
        model.backbone.out_channels,
        model.rpn.anchor_generator.num_anchors_per_location()[0],
    )
    _load_initialization(model, configuration)
    model.sportif_initialization = configuration
    return model


def initialization_metadata(model=None, environ=None):
    environ = os.environ if environ is None else environ
    minimum_image_size, maximum_image_size = image_resize_policy(environ)
    configuration = getattr(model, "sportif_initialization", None)
    if configuration is None:
        configuration = initialization_configuration(environ)
    return {
        "initialization": configuration["mode"],
        "pretrainedWeightsUsed": configuration["pretrainedWeightsUsed"],
        "pretrainedWeights": {
            key: value for key, value in configuration.items()
            if key not in {"mode", "pretrainedWeightsUsed", "weightsPath"}
        } if configuration["pretrainedWeightsUsed"] else None,
        "architectureLicense": "BSD-3-Clause",
        "sourceRepository": "https://github.com/pytorch/vision",
        "sourceRevision": "v0.20.1",
        "anchorSizes": SMALL_OBJECT_ANCHOR_SIZES,
        "anchorAspectRatios": ANCHOR_ASPECT_RATIOS,
        "minimumImageSize": minimum_image_size,
        "maximumImageSize": maximum_image_size,
    }