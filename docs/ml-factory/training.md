# Training

The maximum configuration is 150 epochs, batch size 2, image size 1024, seed
42, and one GPU. The trainer verifies dataset validation, sets deterministic
seeds, requires CUDA, and constructs the model with both `weights=None` and
`weights_backbone=None`.

The detector uses FPN anchor sizes `[4, 8, 16]`, `[8, 16, 32]`,
`[16, 32, 64]`, `[32, 64, 128]`, and `[64, 128, 256]` to cover the approved
dataset's small ball annotations. Training batches deterministically contain
positive and negative frames. Each epoch records classifier, box-regression,
RPN-objectness, and RPN-box-regression losses separately. The optimizer uses a
base learning rate of `0.0002`, a five-epoch linear warm-up, validation-plateau
learning-rate reduction, and gradient clipping at norm `5.0`. Non-finite loss or
two consecutive batch losses above `50.0` fail the run instead of allowing an
unstable checkpoint to continue training.

Training-only augmentation is deterministic for the configured seed, epoch, and
image index. It applies horizontal reflection, bounded brightness/contrast/color
variation, and whole-frame zoom-out from `1.00` to `0.60` on a neutral canvas.
Zoom-out translates every box without cropping and is intended to cover the
approved validation video's smaller ball distribution. Validation and test
images are never augmented.

Positive and negative sampling is source-aware. When an approved training split
contains multiple source videos, each class pool is round-robin ordered across
sources before batches are assembled. The current `sportif-ball-v001` training
split contains only `VID-20260922-001`, so metadata must report source balancing
as inactive; augmentation does not substitute for real source diversity.

Before full training, a GPU smoke stage must overfit four deterministic positive
frames within 150 steps, reaching at least IoU `0.75` and confidence `0.90` on
each frame. Full training evaluates only the validation split every five epochs,
selects the best checkpoint by mAP50 then mAP50-95, and stops after five
validation checks without sufficient improvement. The held-out test split is
not read until the separate final evaluation stage.

At each validation checkpoint, one inference pass is evaluated over score
thresholds `0.05` through `0.95`. The trainer selects the threshold with maximum
F1 among thresholds retaining at least `0.20` validation recall. That threshold
is stored with the best checkpoint and is the only operating threshold used by
the final test evaluator; the test split is never used for threshold tuning.
Each validation checkpoint also records metrics by source-video directory and
recall for objects whose maximum box side is under 12 pixels, 12–23 pixels, or
at least 24 pixels. These diagnostics do not alter the release thresholds.

The model source and license are recorded in `model-manifest.json`. Dependency
and license inventory must be completed before release; code license and model
weights are treated as separate provenance facts.

The active `sportif-ball-training` WorkflowTemplate starts from the approved
immutable dataset rather than repeating video ingest or CVAT export. It
materializes `s3://sportif-ml-datasets/<dataset-id>/` into a workflow-specific
directory on the `sportif-ml-work` PVC, verifies every entry in `SHA256SUMS`, and
requires the immutable approval document under
`s3://sportif-ml-provenance/datasets/<dataset-id>/dataset-approval.json` to agree
with the manifest, validation result, content SHA-256, rights state, and human
review decision.

The serial workflow validates the local copy, verifies trainability, trains on
the GPU, evaluates the held-out test video, exports and runtime-validates ONNX, creates candidate
provenance, and uploads immutable run-specific objects to the model and
provenance buckets. Workflow completion creates a `CANDIDATE`; it does not grant
commercial release approval.

GPU stages request one `nvidia.com/gpu`, select `accelerator=nvidia-v100`, and
use the cluster's `nvidia` RuntimeClass. Failed-workflow pods are retained for
diagnosis; pods are garbage-collected only after a successful workflow.
