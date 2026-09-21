# Troubleshooting

- Argo CD says `Skipping auto-sync`: the previous attempt for that exact Git SHA
  failed. Push a corrected commit or manually sync after fixing the cause. The
  failed `b29d5b8` revision contained `minio/mc:REPLACE`.
- `ExternalSecret` not Ready: verify `platform-secrets` and the exact remote
  properties documented in `installation.md`.
- MLflow cannot access artifacts: verify the six pre-provisioned ML buckets,
  their versioning settings, the scoped identity's object permissions, and that
  `MLFLOW_S3_ENDPOINT_URL` resolves to the platform MinIO Service.
- MLflow is not Ready: inspect the Deployment logs, PVC binding, Longhorn volume
  events, and the generated `sportif-ml-storage` Secret. MLflow is intentionally
  available only through its ClusterIP Service in Stage 2.
- MLflow rollout reports `old replicas are pending termination`: the SQLite
  backend uses a `ReadWriteOnce` Longhorn PVC, so MLflow must use the `Recreate`
  Deployment strategy. With two pods present, commands such as `kubectl logs
  deployment/mlflow` or `kubectl exec deployment/mlflow` may select the old pod,
  which has neither the new init container nor `boto3`. List the pods by creation
  time and target the newest pod explicitly after the old pod terminates.
- MLflow init container cannot download dependencies: verify cluster HTTPS egress
  to PyPI and inspect the new pod explicitly with `kubectl -n sportif-ml logs
  <new-mlflow-pod> -c install-s3-dependencies`. Every wheel is version-pinned and
  hash-verified; do not remove `--require-hashes` to work around an integrity
  failure. Inspect `.status.initContainerStatuses` and pod events before trying
  `kubectl exec`: the `mlflow` container does not exist as a running process while
  the pod is `Init:0/1`. The init download uses three retries and a 15-second
  network timeout; repeated connection failures indicate DNS, proxy, CA, or HTTPS
  egress policy problems that must be fixed or avoided with a prebuilt image.
- CVAT OPA reports `cvat-backend-service:8080 connection refused`: OPA is a
  downstream symptom when the backend has no ready endpoints. Check the backend
  and KVrocks PVC events first. If Longhorn reports
  `node.longhorn.io <node> not found`, the Kubernetes node is not registered in
  Longhorn and its volumes cannot attach.
- Argo CD reports that `cvat-backend-initializer-r1` is missing: this is expected
  after the `PreSync` hook succeeds because `HookSucceeded` deletes it. A failed
  hook is removed by `BeforeHookCreation` on the next full sync. Do not use
  `Replace=true`, manually rename initializer Jobs, or delete PostgreSQL/PVC data.
- The initializer waits for ClickHouse while analytics is disabled: verify the
  pod mounts `/etc/cvat/init.d/10-no-analytics.sh` from
  `cvat-initializer-config`. The override must contain only PostgreSQL and Redis
  migration commands.
- Argo CD cannot update `cvat-kvrocks` claim templates: the existing StatefulSet
  owns an immutable 100 GiB claim. Confirm the child Application ignores only
  `/spec/volumeClaimTemplates` and has `RespectIgnoreDifferences=true`; never
  delete the KVrocks PVC merely to resolve this diff.
- Longhorn manager logs request CRDs such as `shards`, `snapshotgroups`, or
  `instancemanagerupgrades` that are absent from the API: the Longhorn manager,
  CRDs, and RBAC are from mismatched releases. Reconcile Longhorn as one pinned,
  supported release, verify every storage node has a `nodes.longhorn.io` object,
  and wait for all Longhorn managers to become Ready before restarting CVAT.
  Do not delete CVAT PVCs or weaken OPA probes to mask this storage failure.
- A GPU node does not need to be a Longhorn node. CVAT and MLflow are configured
  to use the registered worker nodes, while GPU training remains free to target
  the GPU node.
- `WorkflowTemplate` is unknown: inspect the `sportif-ml-argo-workflows` Argo CD
  Application and verify `workflowtemplates.argoproj.io` exists. Argo CD and
  Argo Workflows are separate products.
- Argo Workflows controller is Pending: it must schedule on a node labelled
  `node-role.kubernetes.io/worker=worker`; inspect node labels and namespace
  quota. It must not be moved to the GPU node.
- Argo Workflows controller logs RBAC denial: verify the chart rendered a
  namespaced `Role`/`RoleBinding`, the controller has `--namespaced`, and no
  ClusterWorkflowTemplate is being submitted. Do not broaden it to cluster-admin.
- Workflow pods remain in `Init:CreateContainerConfigError` with
  `runAsNonRoot=true, but image will run as root`: verify the controller ConfigMap
  executor security context contains `runAsUser: 8737` and `runAsGroup: 8737`.
  The upstream `argoexec` image declares user `0`; do not weaken the Sportif
  worker container's non-root security context.
- Video ingest reports `ImagePullBackOff`: verify the public worker digest remains
  available at `ghcr.io/agogos-llc/sportif-ml-worker` and that cluster nodes can
  reach GHCR. Do not replace the digest with a mutable tag.
- Video ingest fails validation: inspect the step logs and confirm the explicit
  video/provenance keys, supported video suffix, source SHA-256,
  `sourceRightsStatus: VERIFIED`, and `aiTrainingPermission: true`.
- Frame extraction produces no images: inspect FFprobe/FFmpeg errors, confirm the
  source has a readable video stream, and verify the namespace has enough
  ephemeral storage for the downloaded video and generated JPEGs.
- Frame selection rejects a manifest: verify its video ID and source SHA-256
  match provenance, frame IDs and object keys use the expected video prefix,
  timestamps are strictly increasing, and every downloaded JPEG matches its
  manifest SHA-256. Do not bypass these checks.
- Frame selection retains too many or too few images: inspect the recorded
  Hamming distances before changing `FRAME_PHASH_THRESHOLD`. Lower values retain
  more frames; higher values retain fewer. Re-run the workflow after any change.
- CVAT components are missing or unhealthy: inspect the `sportif-ml-cvat` child
  Application, shared PostgreSQL and Redis connectivity, RWX storage, External
  Secrets, and the initializer hook. Do not install a second standalone CVAT.
- `sportif-ml-cvat-automation-sync` is not Ready: confirm the operator completed
  the non-admin account bootstrap and that `global-db-secrets` contains the exact
  `CVAT_API_TOKEN` property. Do not commit or echo the token.
- CVAT handoff returns HTTP 401 or 403: verify the token belongs to the active
  `sportif-ml-automation` user and has not been deleted. Do not make the account
  staff or superuser to bypass an authorization failure.
- CVAT handoff rejects duplicate projects/tasks: inspect CVAT as a human operator
  and resolve the duplicate ownership/name conflict. Never let automation choose
  one arbitrarily or delete annotation data automatically.
- CVAT handoff finds a task with the wrong frame count: preserve the task for
  investigation, compare it with `selected-frames-manifest.json`, and correct the
  naming/ownership conflict manually. CVAT cannot replace data attached to an
  existing task.
- CVAT UI opens but API calls fail: the frontend Service does not proxy `/api`.
  Confirm the chart-managed `cvat` Ingress routes `/api`, `/admin`, `/static`,
  `/django-rq`, and `/profiler` to `cvat-backend-service:8080`, with `/` routed
  to `cvat-frontend-service:8000`.
- `annotate.churchlify.com` returns nginx `404`: the `cvat` Ingress is missing or
  not reconciled. Check the `sportif-ml-cvat` Argo CD Application.
- `annotate.churchlify.com` returns `503`: verify
  `sportif-ml-cvat-ingress-auth-sync` is Ready, the generated Secret contains an
  `auth` key, and both CVAT Services have endpoints.
- Browser ingress credentials are unavailable: retrieve them from the operator
  Mac with `security find-generic-password -s annotate.churchlify.com -a babs -w`.
  Do not replace the bcrypt verifier with a plaintext password.
- Workflow stops at approval: resubmit with `dataset-approved=true` only after
  CVAT review and dataset validation.
- GPU Pending: inspect the live NVIDIA device plugin, labels, allocatable
  `nvidia.com/gpu`, taints, and required tolerations. Do not infer them solely
  from the repository.
- Dataset validation fails: inspect `dataset-validation.json`; fix missing
  labels, malformed YOLO rows, rights metadata, duplicate frames, or leakage.
- ONNX export fails: inspect the trainer image's torch/torchvision/onnx versions
  and rerun export against the saved checkpoint.
