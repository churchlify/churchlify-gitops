# Sportif ML Factory Architecture Assessment

## Repository findings

- Sportif workloads use Kustomize and one Argo CD `Application` per logical app.
- MinIO is already deployed in the `platform` namespace. In-cluster workloads
  use `http://minio-service.platform.svc.cluster.local:9000`; public ingress is
  not required for service-to-service traffic.
- Shared secrets are supplied by the `platform-secrets` ClusterSecretStore from
  `global-db-secrets`. ML storage therefore uses a dedicated remote secret entry,
  not credentials committed to Git and not MinIO root credentials.
- Longhorn is the established storage class. The repository contains only a
  preference for `accelerator=nvidia-gpu`; it does not prove that this label, a
  GPU taint/toleration, or the NVIDIA device plugin exists in the live cluster.
- Existing ingress uses nginx and cert-manager with the `letsencrypt-prod`
  ClusterIssuer. `annotate.churchlify.com` is active behind an nginx Basic Auth
  gate followed by CVAT's own human login. `mlflow.churchlify.com` remains
  disabled.

## Initial design

The ML Factory is isolated in `sportif-ml` and owned by a dedicated Argo CD
Application. Rollout is intentionally staged. The active foundation creates the
namespace, quotas, configuration, and the scoped MinIO ExternalSecret request.
MLflow and bucket creation are enabled only after that ExternalSecret is Ready.
CVAT is installed from its supported Helm chart only after shared database/cache
prerequisites exist. Argo Workflows `v3.6.10` is deployed as a controller-only,
namespace-scoped child Application; its server is disabled. The Sportif
`sportif-video-ingest` WorkflowTemplate is active with a digest-pinned CPU worker
for source validation and frame extraction. The GPU training WorkflowTemplate
remains staged until its image and complete dataset/artifact flow are ready.

Stage 4B transfers an approved source video and provenance metadata from MinIO,
validates them, extracts frames, and publishes frame/provenance artifacts back to
MinIO. Stage 4C deterministically selects representative frames and records every
decision without modifying the source JPEGs. The broader checked-in training
workflow remains an implementation scaffold: it does not yet import/export CVAT
tasks, generate and approve a dataset, compute real evaluation metrics, or
publish the complete release package. It must not be promoted as end-to-end
acceptance complete until those gaps are implemented and tested.

## Risks and prerequisites

1. The foundation requires External Secrets and Longhorn. Later stages require
   nginx ingress, cert-manager, Argo Workflows, and the NVIDIA device plugin.
2. `global-db-secrets` must contain scoped `SPORTIF_ML_S3_ACCESS_KEY` and
   `SPORTIF_ML_S3_SECRET_KEY` values limited to the six ML buckets/prefixes.
3. CVAT requires a dedicated database/user on the shared PostgreSQL service and
   the existing Redis host/password. The Helm values disable bundled
   PostgreSQL, Redis, ClickHouse, Grafana, Traefik, and Nuclio.
4. MLflow currently uses a single-replica SQLite backend on Longhorn and MinIO
   artifacts through `MLFLOW_S3_ENDPOINT_URL`. It is not pinned to a concrete
   node. Because the upstream MLflow 2.18.0 image omits the optional S3 client,
   a non-root init container installs a fully pinned, SHA-256-verified boto3
   dependency set into a shared read-only runtime volume. This is acceptable for
   the initial milestone; a digest-pinned derivative image and shared PostgreSQL
   should replace the init install and SQLite before scaling.
5. Argo Workflows installs cluster-scoped CRDs, but controller permissions are
   namespace-scoped to `sportif-ml`. The workflow executor identity can only
   create and patch `workflowtaskresults`; workflow pod lifecycle remains owned
   by the controller.

No commercial or legal clearance is inferred from successful deployment. The
provenance and release gates remain explicit engineering checks.
