# Installation

Prerequisites are Argo CD, Argo Workflows, External Secrets, nginx ingress,
cert-manager with `letsencrypt-prod`, Longhorn, and the NVIDIA device plugin.
DNS records for `annotate.churchlify.com` and `mlflow.churchlify.com` must point
to the nginx ingress address.

The secret backend must contain `sportif-minio-secrets` with scoped
`SPORTIF_ML_S3_ACCESS_KEY` and `SPORTIF_ML_S3_SECRET_KEY` properties. It must
also expose the existing `PGSQL_HOST`, `PGSQL_USER`, `PGSQL_PASSWORD`, and
`REDIS_HOST` properties in `global-db-secrets`.

```bash
kubectl apply -f platform/namespaces/sportif-ml.yaml
kubectl apply -f platform/argocd/sportif-ml.yaml
kubectl -n sportif-ml get externalsecret sportif-ml-storage-sync
kubectl -n sportif-ml get workflowtemplate sportif-ball-pipeline
```

Build and publish `apps/sportif-ml/trainer/Dockerfile` as
`ghcr.io/agogos-llc/sportif-ball-trainer:0.1.0` before starting the workflow.
For production, replace that tag in `pipeline/workflows.yaml` with the
published digest. The MLflow image is pinned to `v2.18.0` and should also be
mirrored or digest-pinned according to the cluster image policy.
