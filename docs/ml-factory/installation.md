# Installation

## Why revision `b29d5b8` failed

That revision committed `minio/mc:REPLACE` in the PostSync bucket bootstrap Job.
Argo CD attempts automated sync only once for a failed commit/parameter pair, so
it reports `Skipping auto-sync` until a new Git revision is available or an
operator manually retries. The same revision also tried to apply a
`WorkflowTemplate` without declaring an Argo Workflows installation and deployed
CVAT as one server container rather than its supported multi-component chart.

## Stage 1: foundation

Required: Argo CD and External Secrets with the `platform-secrets`
`ClusterSecretStore`. Longhorn is required beginning with Stage 2.

Add scoped MinIO credentials to `global-db-secrets` out-of-band:

- `SPORTIF_ML_S3_ACCESS_KEY`
- `SPORTIF_ML_S3_SECRET_KEY`

The policy for those credentials must be restricted to the six `sportif-ml-*`
buckets. Then validate and apply the Argo CD application:

```bash
./apps/sportif-ml/validate.sh
kubectl apply -f platform/argocd/sportif-ml.yaml
kubectl -n sportif-ml get externalsecret sportif-ml-storage-sync
kubectl -n sportif-ml wait --for=condition=Ready \
  externalsecret/sportif-ml-storage-sync --timeout=120s
```

The Namespace is part of the application and `CreateNamespace=true`; no separate
manual namespace manifest is required.

## Stage 2: MinIO buckets and MLflow

After the ExternalSecret is Ready, add `services.yaml`, `mlflow.yaml`, and
`storage-bootstrap.yaml` to `apps/sportif-ml/pipeline/kustomization.yaml`.
Commit that change so Argo CD creates the buckets and MLflow. Do not expose
MLflow publicly until the existing ingress authentication pattern is selected.

## Stage 3: CVAT

Use the CVAT v2.45.0 Helm chart at immutable Git revision
`125dd1e7006e7aadd8249c1256ec3a8945fcb191` (chart version `0.17.0`) with
`apps/sportif-ml/cvat/values.yaml`. Before activation:

1. Create a dedicated `cvat` database and least-privilege user on shared
   PostgreSQL.
2. Create `cvat-postgres-secret` with keys `database`, `username`, `password`.
3. Create `cvat-redis-secret` with the password key expected by the pinned chart.
4. Replace `SET_FROM_PLATFORM_SECRET` in an environment overlay with the actual
   existing Redis hostname; do not commit credentials.
5. Confirm Longhorn supports the requested RWX CVAT volume.

The staged values disable the chart's bundled PostgreSQL, Redis, analytics,
ClickHouse, Grafana, Traefik, and Nuclio to avoid duplicate platform services.

## Stage 4: workflow and GPU trainer

Install a pinned Argo Workflows release (Argo CD does not supply its CRDs), then
verify `workflowtemplates.argoproj.io` exists. Inspect live GPU labels, taints,
tolerations, and `nvidia.com/gpu` capacity. Update the staged workflow to those
observed conventions; do not assume `accelerator=nvidia-gpu`.

Build `apps/sportif-ml/trainer/Dockerfile` on an amd64-capable builder, publish it
to GHCR, and replace `:0.1.0` with an immutable digest. The Dockerfile uses a CUDA
runtime and training now fails if CUDA is unavailable instead of silently using
CPU. Only then add `rbac.yaml`, `storage.yaml`, and `pipeline/workflows.yaml` to
the active Kustomization.

The workflow still requires implementation of MinIO transfer, shared workspace
mounting, CVAT import/export, real evaluation, MLflow logging, and artifact
packaging before an end-to-end acceptance run.
