# Troubleshooting

- Argo CD says `Skipping auto-sync`: the previous attempt for that exact Git SHA
  failed. Push a corrected commit or manually sync after fixing the cause. The
  failed `b29d5b8` revision contained `minio/mc:REPLACE`.
- `ExternalSecret` not Ready: verify `platform-secrets` and the exact remote
  properties documented in `installation.md`.
- `WorkflowTemplate` is unknown: install pinned Argo Workflows CRDs/controller;
  Argo CD and Argo Workflows are separate products.
- CVAT is missing: it is intentionally staged. Complete the shared PostgreSQL,
  Redis, RWX storage, and secret prerequisites before installing its Helm chart.
- Workflow stops at approval: resubmit with `dataset-approved=true` only after
  CVAT review and dataset validation.
- GPU Pending: inspect the live NVIDIA device plugin, labels, allocatable
  `nvidia.com/gpu`, taints, and required tolerations. Do not infer them solely
  from the repository.
- Dataset validation fails: inspect `dataset-validation.json`; fix missing
  labels, malformed YOLO rows, rights metadata, duplicate frames, or leakage.
- ONNX export fails: inspect the trainer image's torch/torchvision/onnx versions
  and rerun export against the saved checkpoint.
