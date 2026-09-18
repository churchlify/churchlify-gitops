# Operations

Upload supported `.mp4`, `.mov`, or `.mkv` files to the `videos/` prefix in
`sportif-ml-raw`. Create a provenance entry with the video SHA-256, source
rights status, jurisdiction, and explicit AI-training permission before running
the workflow. The workflow is started from the Argo Workflows UI or CLI using
the `sportif-ball-pipeline` template.

The dataset approval parameter must be explicitly set to `true` after CVAT
review. A false or omitted value stops the DAG before training. Training writes
checkpoints and manifests under the dataset work directory and publishes them
to the model/provenance buckets in the release packaging step.

```bash
kubectl -n sportif-ml get pods,jobs,pvc
kubectl -n sportif-ml logs job/sportif-ml-bucket-bootstrap
kubectl -n sportif-ml get events --sort-by=.lastTimestamp
```
