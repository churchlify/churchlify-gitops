# Sportif ML Factory

The factory is an isolated `sportif-ml` namespace managed by
`platform/argocd/sportif-ml.yaml`. It reuses the platform MinIO service at
`http://minio-service.platform.svc.cluster.local:9000`, External Secrets, and
Longhorn. Components with additional prerequisites are activated in stages.

The CVAT endpoint is `https://annotate.churchlify.com`. Nginx requires a
dedicated Basic Authentication credential before presenting CVAT's own human
login, and TLS is issued by `letsencrypt-prod`. The bcrypt verifier is
synchronized from `global-db-secrets`; the plaintext ingress password is not
stored in Git or Kubernetes. `https://mlflow.churchlify.com` remains disabled.
CVAT is a pinned child Argo CD Application discovered recursively by
`platform-root`; it follows the foundation Application by sync wave. Argo
Workflows runs the active
validation, extraction, and deterministic frame-selection pipeline without a
public server or UI. The CVAT handoff is a separate, explicitly submitted
workflow so successful ingest cannot create annotation tasks implicitly.
GPU label/taint conventions must be read from the live cluster before activation.

The production clean-room candidate uses torchvision Faster R-CNN initialized
with no pretrained model or backbone weights. Benchmark candidates listed in
the implementation prompt remain research-only and are not promoted by this
configuration.

The trainer and evaluator are not yet an end-to-end production implementation.
The handoff design maps every video to one deterministic task named
`sportif-ball-<videoId>` in the `Sportif Soccer Ball Annotation` project. That
project must contain only one rectangle label, `ball`. The handoff imports only
SHA-256-verified objects listed in `selected-frames-manifest.json`, fails closed
on ambiguous or mismatched existing resources, and writes `cvat-handoff.json`.
Annotation export, dataset generation and approval, real metric calculation,
MLflow publication, and release packaging remain required work.
