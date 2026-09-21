# Sportif ML Factory

The factory is an isolated `sportif-ml` namespace managed by
`platform/argocd/sportif-ml.yaml`. It reuses the platform MinIO service at
`http://minio-service.platform.svc.cluster.local:9000`, External Secrets, and
Longhorn. Components with additional prerequisites are activated in stages.

The reserved ingress endpoints are `https://annotate.churchlify.com` for CVAT
and `https://mlflow.churchlify.com` for MLflow. Both remain cluster-internal
until an existing platform authentication pattern is selected. CVAT is a pinned
child Argo CD Application discovered recursively by `platform-root`; it follows
the foundation Application by sync wave. Argo Workflows runs the active
validation, extraction, and deterministic frame-selection pipeline without a
public server or UI.
GPU label/taint conventions must be read from the live cluster before activation.

The production clean-room candidate uses torchvision Faster R-CNN initialized
with no pretrained model or backbone weights. Benchmark candidates listed in
the implementation prompt remain research-only and are not promoted by this
configuration.

The trainer and evaluator are not yet an end-to-end production implementation.
CVAT task automation, annotation export, dataset generation and approval, real
metric calculation, MLflow publication, and release packaging remain required
work.
