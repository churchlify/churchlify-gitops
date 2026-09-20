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
once an image build/publish workflow is available. Pip network attempts are
bounded to three retries with a 15-second timeout so unavailable egress fails
visibly instead of leaving initialization apparently stuck for an extended time.

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
4. Add `CVAT_PGSQL_USER` and `CVAT_PGSQL_PASSWORD` to `global-db-secrets`.
   They must identify the dedicated CVAT role; the general Sportif application
   database account is not used by this deployment.
5. Confirm the existing `REDIS_PASSWORD` property is valid for
   `redis-master.platform.svc.cluster.local`.
6. Confirm Longhorn supports the requested RWX CVAT volume and the RWO KVrocks
   volume. CVAT CPU workloads are constrained to nodes carrying
   `node-role.kubernetes.io/worker=worker`; the GPU node is not used.

`platform-root` recursively discovers `platform/argocd/sportif-ml/cvat.yaml`.
The child Application has sync wave `1`, while the foundation Application owns
the namespace and CVAT ExternalSecrets. Do not manually apply those resources.
The chart-generated initializer is an Argo CD `PreSync` hook. It mounts a
foundation-owned script that runs PostgreSQL and Redis migrations without the
stock CVAT 2.45 ClickHouse initialization, because analytics is disabled.
Argo CD deletes a successful hook, so `cvat-backend-initializer-r1` being absent
after a successful sync is expected. `BeforeHookCreation` removes a failed or
stale hook before the next attempt.

The existing KVrocks StatefulSet owns a 100 GiB PVC. Its immutable claim template
is preserved, and the child Application ignores only
`/spec/volumeClaimTemplates` while continuing to reconcile its pod template.
After creating the dedicated database role and adding the secret properties,
verify GitOps reconciliation:

```bash
kubectl -n sportif-ml get externalsecrets \
  cvat-postgres-sync cvat-redis-sync
kubectl -n sportif-ml wait --for=condition=Ready \
  externalsecret/cvat-postgres-sync \
  externalsecret/cvat-redis-sync \
  --timeout=180s
kubectl -n sportif-ml get secret \
  cvat-postgres-secret cvat-redis-secret
kubectl -n argocd get application sportif-ml-cvat
kubectl -n sportif-ml get pods,pvc,service \
  -l app.kubernetes.io/instance=cvat
```

CVAT remains cluster-internal for the initial milestone. After all CVAT
workloads are Ready, test the frontend and backend Services independently:

```bash
kubectl -n sportif-ml port-forward service/cvat-frontend-service 8081:80
# In a second terminal:
curl --fail http://127.0.0.1:8081/

kubectl -n sportif-ml port-forward service/cvat-backend-service 8080:8080
# In a second terminal:
curl --fail http://127.0.0.1:8080/api/server/about
```

The frontend Service does not proxy `/api` to the backend, so a frontend-only
port-forward is a component smoke test rather than a complete browser session.
Enable `ingress.enabled` only after selecting an existing platform
authentication mechanism; TLS alone is not an authentication control.

The Stage 3 values disable the chart's bundled PostgreSQL, Redis, analytics,
ClickHouse, Grafana, Traefik, and Nuclio to avoid duplicate platform services.

## Stage 4A: Argo Workflows control plane

The repository deploys Argo Workflows `v3.6.10` from official chart `0.45.20`.
That application version supports Kubernetes 1.29. The published chart package
digest verified on September 20, 2026 is:

```text
sha256:80e9ffdfc4a8f7ec9e50d2bfcece2bae6ba76dc5d4af06638dddb717c0b8bad7
```

The child Application is `sportif-ml-argo-workflows` at sync wave `2`. It installs
the eight Argo Workflows CRDs and a single controller restricted to the
`sportif-ml` namespace. Argo Server, aggregate ClusterRoles, and
ClusterWorkflowTemplate access are disabled. The controller runs only on normal
workers and both controller and executor images are pinned by digest.
The injected Argo `init` and `wait` containers explicitly run as UID/GID `8737`.
The upstream `argoexec` image declares user `0`, so `runAsNonRoot: true` without
a numeric identity causes workflow pods to fail before the worker starts.

After Argo CD syncs the Stage 4A commit, verify:

```bash
kubectl -n argocd get application sportif-ml-argo-workflows
kubectl get crd workflows.argoproj.io workflowtemplates.argoproj.io \
  workflowtaskresults.argoproj.io
kubectl -n sportif-ml rollout status \
  deployment/sportif-ml-argo-workflows-workflow-controller \
  --timeout=300s
kubectl -n sportif-ml get serviceaccount,role,rolebinding | grep -E \
  'sportif-ml-pipeline|sportif-ml-argo-workflows'
```

No Argo Server or public ingress is installed. Operate workflows through
Kubernetes resources until an authenticated access design is approved.

## Stage 4B: validated video ingest and frame extraction

Stage 4B publishes the CPU worker image as a public, immutable GHCR artifact:

```text
ghcr.io/agogos-llc/sportif-ml-worker@sha256:7e23a3084ec2ababa26556fdb3a232f27a6ed5c43a999462a27b151c4fb2367e
```

The active `sportif-video-ingest` WorkflowTemplate performs only:

```text
validate-input → extract-frames
```

Both steps independently read the source video and provenance manifest from
MinIO, verify rights metadata and SHA-256, probe the video, and write validation,
frames, frame manifest, and extraction provenance back to MinIO. Submit it only
with operator-provided object keys; the template contains no default footage.

The live acceptance run on September 20, 2026 completed successfully as Workflow
`sportif-video-ingest-h46bk`. Both DAG tasks succeeded, and artifact verification
confirmed 4,724 JPEG objects, matching manifest/provenance frame counts, matching
source SHA-256 linkage, and the existence of the first and last referenced
frames. This verifies Stage 4B only; it does not constitute dataset, training, or
model-release acceptance.

The live cluster has one allocatable GPU on the node labelled
`accelerator=nvidia-v100`. The full training workflow remains in
`pipeline/workflows-training-staged.yaml` and is not active.

Later stages still require CVAT export, frame deduplication, dataset generation,
approval, GPU training, real evaluation, MLflow logging, artifact packaging, and
model provenance before an end-to-end acceptance run.
