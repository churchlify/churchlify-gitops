# Operations

Upload supported `.mp4`, `.mov`, or `.mkv` files to the `videos/` prefix in
`sportif-ml-raw`. Create a provenance entry with the video SHA-256, source
rights status, jurisdiction, and explicit AI-training permission.

The active `sportif-video-ingest` WorkflowTemplate validates one approved source
video and extracts representative frames. It requires explicit MinIO object keys
for both the video and its provenance manifest. The full training workflow remains
staged and must not be submitted.

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
kubectl -n sportif-ml get workflowtemplate sportif-video-ingest
kubectl -n sportif-ml port-forward service/cvat-frontend-service 8081:80
kubectl -n sportif-ml port-forward service/cvat-backend-service 8080:8080
```

Use `http://127.0.0.1:8081/` for a frontend component check and
`http://127.0.0.1:8080/api/server/about` for the backend health check. These
separate forwards do not provide a complete browser session because `/api`
routing is normally supplied by the disabled ingress.

Argo Workflows is controller-only. There is no Argo Server Service or UI. Submit
an approved ingest run with a generated Workflow resource:

```bash
VIDEO_KEY='videos/<approved-video>.mp4'
PROVENANCE_KEY='videos/<video-id>/source-provenance.json'

cat <<EOF | kubectl -n sportif-ml create -f -
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  generateName: sportif-video-ingest-
spec:
  workflowTemplateRef:
    name: sportif-video-ingest
  arguments:
    parameters:
      - name: video-key
        value: ${VIDEO_KEY}
      - name: provenance-key
        value: ${PROVENANCE_KEY}
EOF
```

Watch with `kubectl -n sportif-ml get workflows,pods --watch`. A successful run
writes `input-validation.json` and `frame-extraction.json` to the provenance
bucket and JPEG frames plus `frames-manifest.json` to the frames bucket.
