# Sportif ML Factory

The factory is an isolated `sportif-ml` namespace managed by
`platform/argocd/sportif-ml.yaml`. It reuses the platform MinIO deployment at
`https://s3.churchlify.com`, PostgreSQL/Redis properties exposed through
`platform-secrets`, nginx ingress, cert-manager, and Longhorn.

Ingress endpoints are `https://annotate.churchlify.com` for CVAT and
`https://mlflow.churchlify.com` for MLflow. The pipeline is an Argo Workflow
Template with CPU stages for validation/extraction and a GPU training stage
requesting `nvidia.com/gpu: 1` on `accelerator=nvidia-gpu`.

The production clean-room candidate uses torchvision Faster R-CNN initialized
with no pretrained model or backbone weights. Benchmark candidates listed in
the implementation prompt remain research-only and are not promoted by this
configuration.
