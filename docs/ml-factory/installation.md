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

Stage 2 is active after the successful Stage 1 sync at revision `a31e086`.
The six ML buckets are pre-provisioned, so the application deploys the MLflow
Service, dependency ConfigMap, PVC, and Deployment. Bucket administration is
deliberately kept out of the Argo application; the scoped identity needs object
access to the existing buckets, not bucket-creation privileges.

MLflow uses `MLFLOW_S3_ENDPOINT_URL` to reach the existing in-cluster MinIO
Service and is deliberately not pinned to a specific Kubernetes node.
The upstream `ghcr.io/mlflow/mlflow:v2.18.0` image is pinned by its verified
multi-architecture digest, but it does not contain the optional `boto3` S3
client. `mlflow-dependencies.yaml` therefore supplies a complete,
version-pinned and SHA-256-verified dependency set that a non-root init container
installs into an ephemeral shared volume. Cluster egress to PyPI is required on
pod initialization. Replace this bootstrap with a digest-pinned derivative image
once an image build/publish workflow is available.

The single-replica Deployment uses the `Recreate` strategy because its SQLite
backend is stored on a `ReadWriteOnce` Longhorn PVC. This intentionally causes a
short MLflow outage during upgrades, but prevents the old and new pods from
contending for the same single-writer volume.

Verify after Argo CD syncs the Stage 2 commit:

```bash
kubectl -n sportif-ml rollout status deployment/mlflow --timeout=180s
kubectl -n sportif-ml get service/mlflow pvc/mlflow-backend
MLFLOW_POD="$(kubectl -n sportif-ml get pods -l app=mlflow \
  --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{.items[-1:].metadata.name}')"
kubectl -n sportif-ml logs "$MLFLOW_POD" -c install-s3-dependencies
kubectl -n sportif-ml exec "$MLFLOW_POD" -c mlflow -- \
  python -c 'import boto3, os; print(boto3.__version__); print(os.environ["MLFLOW_S3_ENDPOINT_URL"])'
kubectl -n sportif-ml port-forward service/mlflow 5000:5000
curl --fail http://127.0.0.1:5000/
```

MLflow remains cluster-internal. Do not expose it publicly until the existing
ingress authentication pattern is selected.

## Stage 3: CVAT

Use the CVAT v2.45.0 Helm chart at immutable Git revision
`125dd1e7006e7aadd8249c1256ec3a8945fcb191` (chart version `0.17.0`) through
`platform/argocd/sportif-ml/cvat.yaml`, with
`apps/sportif-ml/cvat/values.yaml`. The configured shared services are
`pgsql-postgresql.platform.svc.cluster.local` and
`redis-master.platform.svc.cluster.local`. Before activation:

1. Create a dedicated `cvat` database and least-privilege user on shared
   PostgreSQL.
2. Create `cvat-postgres-secret` with keys `database`, `username`, `password`.
3. Create `cvat-redis-secret` with the password key expected by the pinned chart.
4. The staged `apps/sportif-ml/cvat/externalsecrets.yaml` defines those two
   Secrets from the existing `global-db-secrets` fields. Keep it out of the
   active Kustomization until the PostgreSQL `cvat` database and a
   least-privilege user exist; reusing the shared application database user is
   not a production substitute.
5. Replace `SET_FROM_PLATFORM_SECRET` in an environment overlay with the actual
   existing Redis hostname from the `REDIS_HOST` property; do not commit
   credentials.
6. Confirm Longhorn supports the requested RWX CVAT volume.

After those prerequisites are complete, activate the CVAT ExternalSecrets and
Argo Application:

```bash
# Run these from the GitOps repository checkout. On k8s-master-01, use the
# administrator kubeconfig; apps/sportif-ml/k8s.conf is a repository-local
# client config and is not present under the remote user's home directory.
export KUBECONFIG=/etc/kubernetes/admin.conf
kubectl apply -f apps/sportif-ml/cvat/externalsecrets.yaml
kubectl apply -f platform/argocd/sportif-ml/cvat.yaml
kubectl -n argocd get applications.argoproj.io sportif-ml-cvat
kubectl -n sportif-ml get externalsecrets,secret
```

If the repository is not checked out on the master, apply the two manifests
from a workstation that has the checkout and uses a valid kubeconfig. Do not
set `KUBECONFIG=apps/sportif-ml/k8s.conf` from `~` unless that relative path
actually exists there; otherwise kubectl silently falls back to localhost.

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
