# Troubleshooting

- Argo CD says `Skipping auto-sync`: the previous attempt for that exact Git SHA
  failed. Push a corrected commit or manually sync after fixing the cause. The
  failed `b29d5b8` revision contained `minio/mc:REPLACE`.
- `ExternalSecret` not Ready: verify `platform-secrets` and the exact remote
  properties documented in `installation.md`.
- MLflow cannot access artifacts: verify the six pre-provisioned ML buckets,
  their versioning settings, and the scoped identity's object permissions.
- MLflow is not Ready: inspect the Deployment logs, PVC binding, Longhorn volume
  events, and the generated `sportif-ml-storage` Secret. MLflow is intentionally
  available only through its ClusterIP Service in Stage 2.
- CVAT OPA reports `cvat-backend-service:8080 connection refused`: OPA is a
  downstream symptom when the backend has no ready endpoints. Check the backend
  and KVrocks PVC events first. If Longhorn reports
  `node.longhorn.io <node> not found`, the Kubernetes node is not registered in
  Longhorn and its volumes cannot attach.
- Longhorn manager logs request CRDs such as `shards`, `snapshotgroups`, or
  `instancemanagerupgrades` that are absent from the API: the Longhorn manager,
  CRDs, and RBAC are from mismatched releases. Reconcile Longhorn as one pinned,
  supported release, verify every storage node has a `nodes.longhorn.io` object,
  and wait for all Longhorn managers to become Ready before restarting CVAT.
  Do not delete CVAT PVCs or weaken OPA probes to mask this storage failure.
- A GPU node does not need to be a Longhorn node. CVAT and MLflow are configured
  to use the registered worker nodes, while GPU training remains free to target
  the GPU node.
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
