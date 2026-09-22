# Training

The initial configuration is 150 epochs, batch size 2, image size 1024, seed
42, and one GPU. These are defaults, not claims of optimality. The trainer
verifies dataset validation, sets deterministic seeds, requires CUDA for the
production training path, and constructs the model with both `weights=None` and
`weights_backbone=None`.

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

The serial workflow validates the local copy, trains on the GPU, evaluates the
held-out test video, exports and runtime-validates ONNX, creates candidate
provenance, and uploads immutable run-specific objects to the model and
provenance buckets. Workflow completion creates a `CANDIDATE`; it does not grant
commercial release approval.

GPU stages request one `nvidia.com/gpu`, select `accelerator=nvidia-v100`, and
use the cluster's `nvidia` RuntimeClass. Failed-workflow pods are retained for
diagnosis; pods are garbage-collected only after a successful workflow.
