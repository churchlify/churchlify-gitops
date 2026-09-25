# Model Release

Every candidate must contain `model.pt`, `model.onnx`, `metrics.json`, the
training configuration, dataset and model manifests, dependency/license
notices, and `SHA256SUMS`. The engineering gate requires verified dataset
rights, verified training-code and dependency licenses, no pretrained weights,
and complete provenance before a model can be marked `RELEASED`.

Successful pipeline execution is not legal or commercial clearance. Pretrained
initialization modes are diagnostic-only under the current clean-room policy;
candidate creation and release verification require random initialization.

## Promotion boundary

Training publishes only an immutable run-scoped `CANDIDATE`. Promotion is a
separate operator action and must never run automatically at workflow completion.
The release command verifies the candidate inventory and every candidate
checksum, repeats the evaluation/ONNX/random-initialization/provenance gates, and
requires an immutable approval document bound to both the candidate
`model-manifest.json` SHA-256 and candidate `SHA256SUMS` SHA-256.

The approval document must identify the candidate run and model, name the
approver and organizational role, record a timezone-aware approval timestamp,
and explicitly confirm dependency-license review, organizational commercial
approval, and production-promotion authorization. Package metadata is review
evidence and is not a legal conclusion.

Approved artifacts are written append-only under
`s3://sportif-ml-models/releases/<model-id>/<release-id>/`. Promotion refuses to
overwrite a different object and does not create a mutable `latest` pointer.
The release package adds `training-config.yaml`, `dependency-lock.txt`, declared
dependency-license inventory, third-party notices, `model-card.md`, the approval
record, a `RELEASED` model manifest, and checksums covering every release file.

Example approval shape (values must match the actual candidate):

```json
{
  "schemaVersion": 1,
  "modelId": "sportif-ball-detector-v001",
  "candidateRunId": "sportif-ball-training-v002-20260924",
  "candidateManifestSha256": "<64 lowercase hex characters>",
  "candidateChecksumSetSha256": "<64 lowercase hex characters>",
  "pythonInventorySha256": "<inventory-hashes.json value>",
  "systemInventorySha256": "<inventory-hashes.json value>",
  "status": "APPROVED",
  "decision": "APPROVE",
  "dependencyLicensesVerified": true,
  "organizationalCommercialApprovalGranted": true,
  "productionPromotionAuthorized": true,
  "approvedBy": "<named reviewer>",
  "approverRole": "<organizational role>",
  "approvedAtUtc": "<timezone-aware ISO-8601 timestamp>"
}
```

After that document is stored under a new immutable provenance key, run:

```bash
python -m sportif_ml.release promote \
  sportif-ball-training-v002-20260924 \
  sportif-ball-detector-v001-r001 \
  releases/sportif-ball-detector-v001/sportif-ball-training-v002-20260924/release-approval.json
```

Generate the environment inventory from the exact digest-pinned release-tool
image before approval:

```bash
python -m sportif_ml.release inventory /review/environment
```

The reviewer must inspect the complete Python and Debian package inventories and
bind the approval to both hashes in `inventory-hashes.json`.

After a training workflow has published a passing candidate, generate the full
review bundle without granting approval:

```bash
python -m sportif_ml.release review \
  sportif-ball-training-v002-20260924 \
  /review/sportif-ball-training-v002-20260924
```

This command refuses failed quality/ONNX/provenance gates and writes
`release-review.json`, both complete package inventories, and
`release-approval.template.json`. Every approval field in the template remains
pending or false until a named organizational reviewer changes it after review.

Creating a release package does not by itself deploy a runtime consumer. A
production deployment must reference the exact immutable release ID and retain a
documented rollback target.

## Verified release-tool image

The release-tool image built from Git revision
`011c5f91dd9db127fd5ad67cc0f547300b1c7a09` was published and independently
pulled on September 24, 2026 as:

```text
ghcr.io/bjelugbo/sportif-ball-trainer@sha256:e56c201cfc81f76b54ff5dff18011aeb803afb13e0c92003e99ef5f129b27838
```

The pulled digest passed all 42 trainer tests. Its complete environment inventory
contains 171 Python distributions and 300 Debian packages, bound to these hashes:

```text
pythonInventorySha256: 685122946bcecd694f0e74bc33a0e5ad14c06c3c4daad602ba5824dd1736f18b
systemInventorySha256: 425d21136d335f5801359927fb12a5098d59e999d346e7bd5b4bc48a77f2ca30
```

Installed Python metadata does not declare a license for
`conda-package-handling`, `conda_index`, `libmambapy`, or `mamba`; `archspec`
reports an `Other/Proprietary License` classifier. These are review findings, not
legal conclusions. Keep `dependencyLicensesVerified` false until a named
organizational reviewer resolves those findings and reviews the complete Python
and Debian inventories from the exact image digest above.
