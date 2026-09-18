# Sportif ML Factory Architecture Assessment

## Repository findings

- Sportif workloads use Kustomize and one Argo CD `Application` per logical app.
- MinIO is already deployed in the `platform` namespace and is reachable through
  `https://s3.churchlify.com`; the ML Factory must reuse it.
- Shared secrets are supplied by the `platform-secrets` ClusterSecretStore from
  `global-db-secrets`. ML storage therefore uses a dedicated remote secret entry,
  not credentials committed to Git and not MinIO root credentials.
- Longhorn is the established storage class. The existing GPU scheduling label is
  `accelerator=nvidia-gpu`; the repository does not declare a GPU taint or a
  device-plugin manifest, so those remain cluster prerequisites to verify.
- Existing ingress uses nginx and cert-manager with the `letsencrypt-prod`
  ClusterIssuer. The ML endpoints are therefore `annotate.churchlify.com` and
  `mlflow.churchlify.com`.

## Initial design

The ML Factory is isolated in `sportif-ml` and owned by a dedicated Argo CD
Application. CPU pipeline jobs use the shared MinIO S3 endpoint and a Longhorn
working PVC. Training jobs request `nvidia.com/gpu: 1` and select the existing
GPU label without assuming a node name. CVAT and MLflow are exposed only through
the requested ingress hosts; authentication and DNS remain cluster/application
prerequisites and are documented rather than invented here.

The first implementation keeps the workflow small: ingest metadata and video
objects in MinIO, extract and validate a video-level dataset, pause on an explicit
approval ConfigMap, train from random initialization, evaluate on held-out source
videos, export ONNX, and publish immutable manifests and hashes. The existing
MinIO deployment is not modified; bucket/policy provisioning is represented as a
separate operator-controlled bootstrap manifest so root credentials never enter
the ML namespace.

## Risks and prerequisites

1. The cluster must provide External Secrets, nginx ingress, cert-manager,
   Longhorn, Argo Workflows, and the NVIDIA device plugin.
2. The `sportif-minio-secrets` backend object must contain a scoped ML access key
   and secret with access limited to the six ML buckets/prefixes.
3. CVAT requires its supported database/Redis components. The manifests use the
   official CVAT deployment boundary and must be smoke-tested against the chosen
   CVAT release before production annotation work.
4. MLflow needs a durable backend store and artifact bucket. The initial release
   uses the existing PostgreSQL service through an ExternalSecret reference and
   MinIO for artifacts; exact database host properties must be supplied by the
   cluster secret backend.

No commercial or legal clearance is inferred from successful deployment. The
provenance and release gates remain explicit engineering checks.
