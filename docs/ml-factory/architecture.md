# Sportif ML Factory

The factory is an isolated `sportif-ml` namespace managed by
`platform/argocd/sportif-ml.yaml`. It reuses the platform MinIO service at
`http://minio-service.platform.svc.cluster.local:9000`, External Secrets, and
Longhorn. Components with additional prerequisites are activated in stages.

The intended ingress endpoints are `https://annotate.churchlify.com` for CVAT
and `https://mlflow.churchlify.com` for MLflow. CVAT is staged as pinned Helm
values, while the workflow template is staged until Argo Workflows is installed.
GPU label/taint conventions must be read from the live cluster before activation.

The production clean-room candidate uses torchvision Faster R-CNN initialized
with no pretrained model or backbone weights. Benchmark candidates listed in
the implementation prompt remain research-only and are not promoted by this
configuration.

The trainer, evaluator, and workflow are not yet an end-to-end production
implementation. In particular, artifact movement, CVAT automation, real metric
calculation, MLflow publication, and release packaging remain required work.
