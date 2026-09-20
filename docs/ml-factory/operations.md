# Operations

Upload supported `.mp4`, `.mov`, or `.mkv` files to the `videos/` prefix in
`sportif-ml-raw`. Create a provenance entry with the video SHA-256, source
rights status, jurisdiction, and explicit AI-training permission.

The checked-in workflow is staged and is not operational yet. Once its missing
storage/CVAT/metrics/publishing stages are implemented and it is activated, the
dataset approval parameter must be explicitly set to `true` after CVAT review.

```bash
kubectl -n sportif-ml get pods,jobs,pvc
kubectl -n sportif-ml get externalsecret sportif-ml-storage-sync
kubectl -n sportif-ml rollout status deployment/mlflow
kubectl -n sportif-ml get pods -l app=mlflow \
  --sort-by=.metadata.creationTimestamp
kubectl -n sportif-ml get events --sort-by=.lastTimestamp
kubectl -n argocd get application sportif-ml-cvat
kubectl -n argocd get application sportif-ml-argo-workflows
kubectl -n sportif-ml get pods,pvc,service -l app.kubernetes.io/instance=cvat
kubectl -n sportif-ml rollout status \
  deployment/sportif-ml-argo-workflows-workflow-controller
kubectl get crd workflows.argoproj.io workflowtemplates.argoproj.io
kubectl -n sportif-ml port-forward service/cvat-frontend-service 8081:80
kubectl -n sportif-ml port-forward service/cvat-backend-service 8080:8080
```

Use `http://127.0.0.1:8081/` for a frontend component check and
`http://127.0.0.1:8080/api/server/about` for the backend health check. These
separate forwards do not provide a complete browser session because `/api`
routing is normally supplied by the disabled ingress.

Argo Workflows is controller-only. There is no Argo Server Service or UI. The
checked-in `WorkflowTemplate` remains inactive; do not submit it until the
trainer image is digest-pinned and the documented data-flow gaps are closed.
