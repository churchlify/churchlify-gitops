# Troubleshooting

- `ExternalSecret` not Ready: verify `platform-secrets` and the exact remote
  properties documented in `installation.md`.
- Workflow stops at approval: resubmit with `dataset-approved=true` only after
  CVAT review and dataset validation.
- GPU Pending: verify the NVIDIA device plugin, node label
  `accelerator=nvidia-gpu`, available `nvidia.com/gpu`, and any cluster taint
  toleration required by the existing GPU node.
- Dataset validation fails: inspect `dataset-validation.json`; fix missing
  labels, malformed YOLO rows, rights metadata, duplicate frames, or leakage.
- ONNX export fails: inspect the trainer image's torch/torchvision/onnx versions
  and rerun export against the saved checkpoint.
