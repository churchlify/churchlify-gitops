# Training

The maximum configuration is 150 epochs, batch size 2, image size 1024, seed
42, and one GPU. The trainer verifies dataset validation, sets deterministic
seeds, requires CUDA, and constructs the model with both `weights=None` and
`weights_backbone=None`.

The detector uses FPN anchor sizes `[4, 8, 16]`, `[8, 16, 32]`,
`[16, 32, 64]`, `[32, 64, 128]`, and `[64, 128, 256]` to cover the approved
dataset's small ball annotations. Training batches deterministically contain
positive and negative frames. Each epoch records classifier, box-regression,
RPN-objectness, and RPN-box-regression losses separately.

Before full training, a GPU smoke stage must overfit four deterministic positive
frames within 150 steps, reaching at least IoU `0.75` and confidence `0.90` on
each frame. Full training evaluates only the validation split every five epochs,
selects the best checkpoint by mAP50 then mAP50-95, and stops after five
validation checks without sufficient improvement. The held-out test split is
not read until the separate final evaluation stage.

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
